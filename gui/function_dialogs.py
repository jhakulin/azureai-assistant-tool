# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

# This software uses the PySide6 library, which is licensed under the GNU Lesser General Public License (LGPL).
# For more details on PySide6's license, see <https://www.qt.io/licensing>

import json
import threading
import copy
from typing import List

from PySide6.QtWidgets import QDialog, QSplitter, QComboBox, QTabWidget, QHBoxLayout, QWidget, QListWidget, QLineEdit, QVBoxLayout, QPushButton, QLabel, QTextEdit, QMessageBox, QFrame, QFormLayout, QGridLayout
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextOption

from azure.ai.assistant.management.ai_client_type import AIClientType
from azure.ai.assistant.management.azure_logic_app_manager import AzureLogicAppManager
from azure.ai.assistant.management.azure_functions_manager import AzureFunctionManager
from azure.ai.assistant.management.function_config_manager import FunctionConfigManager
from azure.ai.assistant.management.logger_module import logger
from gui.signals import ErrorSignal, StartStatusAnimationSignal, StopStatusAnimationSignal
from gui.status_bar import ActivityStatus, StatusBar
from gui.utils import camel_to_snake, get_ai_client
from enum import Enum


azure_function_spec_template = {
   "type": "azure_function",
   "azure_function": {
      "function": {
         "name": "function_name, e.g. get_weather",
         "module": "azure_functions",
         "description": "Description of the function, e.g. Get the weather",
         "parameters": {
               "type": "object",
               "properties": {
                     "argument_1, e.g. location": {
                      "type": "argument_type, e.g. string",
                      "description": "The description, e.g. location of the weather"
                  }
               },
               "required": [
                  "argument_1 of function, e.g. location"
               ]
            }
      },
      "input_binding": {
         "type": "storage_queue",
         "storage_queue": {
            "queue_service_uri": "DEPLOYMENT_STORAGE_CONNECTION_STRING",
            "queue_name": "inputQueue"
         }
      },
      "output_binding": {
         "type": "storage_queue",
         "storage_queue": {
            "queue_service_uri": "DEPLOYMENT_STORAGE_CONNECTION_STRING",
            "queue_name": "outputQueue"
         }
      }
   }
}


class FunctionTab(Enum):
    SYSTEM = "System Functions"
    USER = "User Functions"
    AZURE_LOGIC_APP = "Azure Logic App Functions"
    OPENAPI = "OpenAPI Functions"
    AZURE_FUNCTIONS = "Azure Functions"


class CreateFunctionDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        if hasattr(main_window, 'function_spec_creator') and hasattr(main_window, 'function_impl_creator'):
            self.function_spec_creator = main_window.function_spec_creator
            self.function_impl_creator = main_window.function_impl_creator
        if hasattr(main_window, 'azure_logic_app_function_creator'):
            self.azure_logic_app_function_creator = main_window.azure_logic_app_function_creator
        self.function_config_manager: FunctionConfigManager = main_window.function_config_manager
        self.init_UI()
        self.previousSize = self.size()

        # Separate variables for each tab's results.
        self.user_spec_json = None
        self.user_code = None
        self.azure_spec_json = None
        self.azure_code = None

    def init_UI(self):
        self.setWindowTitle("Create/Edit Functions")
        self.resize(800, 900)

        mainLayout = QVBoxLayout(self)

        # Define necessary UI elements before connecting signals
        self.systemSpecEdit = self.create_text_edit()
        self.userSpecEdit = self.create_text_edit()
        self.userImplEdit = self.create_text_edit()

        # Tabs for System, User, Azure Logic Apps (optional), and OpenAPI (optional)
        self.tabs = QTabWidget(self)
        self.systemFunctionsTab = self.create_system_functions_tab()
        self.userFunctionsTab = self.create_user_functions_tab()

        self.tabs.addTab(self.systemFunctionsTab, FunctionTab.SYSTEM.value)
        self.tabs.addTab(self.userFunctionsTab, FunctionTab.USER.value)
        
        # Add Azure Logic Apps tab if the active AI client is AZURE_AI_AGENT and azure_logic_app_manager is set
        if (getattr(self.main_window, 'active_ai_client_type', None) == AIClientType.AZURE_AI_AGENT and
            hasattr(self.main_window, 'azure_logic_app_manager')):
            self.azureLogicAppsTab = self.create_azure_logic_apps_tab()
            self.tabs.addTab(self.azureLogicAppsTab, FunctionTab.AZURE_LOGIC_APP.value)

            self.azureFunctionSpecEdit = self.create_text_edit()
            self.azureFunctionsTab = self.create_azure_functions_tab()
            self.tabs.addTab(self.azureFunctionsTab, FunctionTab.AZURE_FUNCTIONS.value)

        # Add OpenAPI tab if we are in an agent mode 
        if getattr(self.main_window, 'active_ai_client_type', None) == AIClientType.AZURE_AI_AGENT:
            self.openapiTab = self.create_openapi_tab()
            self.tabs.addTab(self.openapiTab, FunctionTab.OPENAPI.value)

        mainLayout.addWidget(self.tabs)

        # Buttons layout at the bottom
        buttonLayout = QHBoxLayout()
        self.saveButton = QPushButton("Save Function", self)
        self.saveButton.clicked.connect(self.save_function)
        buttonLayout.addWidget(self.saveButton)

        self.removeButton = QPushButton("Remove Function", self)
        self.removeButton.clicked.connect(self.remove_function)
        self.removeButton.setEnabled(False)
        buttonLayout.addWidget(self.removeButton)
        mainLayout.addLayout(buttonLayout)

        self.tabs.currentChanged.connect(self.onTabChanged)

        self.status_bar = StatusBar(self)
        mainLayout.addWidget(self.status_bar.get_widget())

        self.start_processing_signal = StartStatusAnimationSignal()
        self.stop_processing_signal = StopStatusAnimationSignal()
        self.error_signal = ErrorSignal()

        self.start_processing_signal.start_signal.connect(self.start_processing)
        self.stop_processing_signal.stop_signal.connect(self.stop_processing)
        self.error_signal.error_signal.connect(lambda error_message: QMessageBox.warning(self, "Error", error_message))

        self.setLayout(mainLayout)

    def create_openapi_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        # Choose an existing OpenAPI function (if any) or create a new one
        select_label = QLabel("Select OpenAPI Function:")
        main_layout.addWidget(select_label)

        self.openapiSelector = QComboBox(self)
        self.load_openapi_functions()  # Populates self.openapiSelector
        self.openapiSelector.currentIndexChanged.connect(self.on_openapi_function_selected)
        main_layout.addWidget(self.openapiSelector)

        # Name
        name_label = QLabel("Name:")
        self.openapiNameEdit = QLineEdit()
        main_layout.addWidget(name_label)
        main_layout.addWidget(self.openapiNameEdit)

        # Description
        desc_label = QLabel("Description:")
        self.openapiDescriptionEdit = QLineEdit()
        main_layout.addWidget(desc_label)
        main_layout.addWidget(self.openapiDescriptionEdit)

        openapiDocLink = QLabel(
            '<a href="https://learn.microsoft.com/en-us/azure/ai-services/agents/how-to/tools/openapi-spec?tabs=python&pivots=overview">'
            'Learn about integrating OpenAPI in Azure AI Services Agents</a>'
        )
        openapiDocLink.setOpenExternalLinks(True)
        main_layout.addWidget(openapiDocLink)

        # Auth Type
        auth_label = QLabel("Auth Type:")
        self.openapiAuthSelector = QComboBox()
        self.openapiAuthSelector.addItems(["anonymous", "connection", "managed_identity"])
        main_layout.addWidget(auth_label)
        main_layout.addWidget(self.openapiAuthSelector)

        # Create a form area to hold authentication details (Connection ID / Audience)
        self.auth_details_frame = QFrame()
        self.auth_details_layout = QFormLayout(self.auth_details_frame)
        
        # Connection ID fields
        self.connection_id_label = QLabel("Connection ID:")
        self.connection_id_edit = QLineEdit()
        self.auth_details_layout.addRow(self.connection_id_label, self.connection_id_edit)
        
        # Audience fields (for managed identity)
        self.audience_label = QLabel("Audience:")
        self.audience_edit = QLineEdit()
        self.auth_details_layout.addRow(self.audience_label, self.audience_edit)
        
        # By default, hide the details fields
        self.connection_id_label.hide()
        self.connection_id_edit.hide()
        self.audience_label.hide()
        self.audience_edit.hide()

        main_layout.addWidget(self.auth_details_frame)

        # Connect the combo box signal to show/hide the relevant auth detail fields
        self.openapiAuthSelector.currentIndexChanged.connect(self.on_auth_type_changed)

        # Raw JSON text for the spec
        self.openapiSpecEdit = self.create_text_edit()
        self.openapiSpecEdit.setToolTip("")
        
        openapiSpecWidget = self.create_text_edit_labeled("OpenAPI Specification:", self.openapiSpecEdit)
        main_layout.addWidget(openapiSpecWidget)

        return tab

    def on_auth_type_changed(self, index):
        auth_type = self.openapiAuthSelector.currentText()
        
        if auth_type == "connection":
            # Show connection ID fields, hide audience
            self.connection_id_label.show()
            self.connection_id_edit.show()
            self.audience_label.hide()
            self.audience_edit.hide()
        elif auth_type == "managed_identity":
            # Show audience fields, hide connection
            self.connection_id_label.hide()
            self.connection_id_edit.hide()
            self.audience_label.show()
            self.audience_edit.show()
        else:  # "anonymous"
            # Hide both connection ID and audience fields
            self.connection_id_label.hide()
            self.connection_id_edit.hide()
            self.audience_label.hide()
            self.audience_edit.hide()
    
    def load_openapi_functions(self):
        self.openapiSelector.clear()
        self.openapiSelector.addItem("New OpenAPI Function", None)

        try:
            openapi_functions = self.function_config_manager.get_all_openapi_functions()
            for item in openapi_functions:
                # item is presumed to be a dict with the shape: {'type':'openapi','openapi': {...}}
                try:
                    name = item["openapi"]["name"]
                    self.openapiSelector.addItem(name, item)  # store the entire dict as data
                except Exception:
                    logger.warning("Malformed OpenAPI entry encountered while loading.")
        except Exception as e:
            logger.error(f"Error loading OpenAPI functions: {e}")

    def on_openapi_function_selected(self):
        data = self.openapiSelector.currentData()
        if data:
            try:
                openapi_data = data.get("openapi", {})
                auth_data = data.get("auth", {})
                
                # Name
                name_val = openapi_data.get("name", "")
                self.openapiNameEdit.setText(name_val)

                # Description
                desc_val = openapi_data.get("description", "")
                self.openapiDescriptionEdit.setText(desc_val)

                # Auth
                auth_type_val = auth_data.get("type", "anonymous")
                index = self.openapiAuthSelector.findText(auth_type_val)
                if index >= 0:
                    self.openapiAuthSelector.setCurrentIndex(index)

                security_scheme = auth_data.get("security_scheme", {})
                self.connection_id_edit.setText(security_scheme.get("connection_id", ""))
                self.audience_edit.setText(security_scheme.get("audience", ""))

                # Spec (convert dict to JSON text)
                spec_dict = openapi_data.get("spec", {})
                spec_text = json.dumps(spec_dict, indent=4)
                self.openapiSpecEdit.setText(spec_text)

            except Exception as ex:
                logger.warning(f"Malformed OpenAPI entry: {ex}")
                self.openapiNameEdit.clear()
                self.openapiDescriptionEdit.clear()
                self.openapiSpecEdit.clear()
                self.connection_id_edit.clear()
                self.audience_edit.clear()
        else:
            # "New OpenAPI Function" selected or no data
            self.openapiNameEdit.clear()
            self.openapiDescriptionEdit.clear()
            self.openapiSpecEdit.clear()
            self.connection_id_edit.clear()
            self.audience_edit.clear()
            self.openapiAuthSelector.setCurrentIndex(0)  # default to "anonymous"

    def save_openapi_function(self):
        # Gather fields
        name_val = self.openapiNameEdit.text().strip()
        desc_val = self.openapiDescriptionEdit.text().strip()
        auth_val = self.openapiAuthSelector.currentText()
        connection_id_val = self.connection_id_edit.text().strip()
        audience_val = self.audience_edit.text().strip()
        raw_spec = self.openapiSpecEdit.toPlainText().strip()

        if not name_val:
            QMessageBox.warning(self, "Error", "OpenAPI name is required.")
            return
        
        if not raw_spec:
            QMessageBox.warning(self, "Error", "OpenAPI spec is empty or invalid.")
            return

        try:
            spec_dict = json.loads(raw_spec)
        except json.JSONDecodeError as ex:
            QMessageBox.warning(self, "Error", f"Invalid JSON for OpenAPI spec:\n{ex}")
            return

        openapi_data = {
            "type": "openapi",
            "openapi": {
                "name": name_val,
                "description": desc_val,
                "spec": spec_dict
            },
            "auth": {
                "type": auth_val
            }
        }

        # If the user chose "connection" or "managed_identity", store the respective security_scheme details
        if auth_val == "connection":
            openapi_data["auth"]["security_scheme"] = {"connection_id": connection_id_val}
            if not connection_id_val:
                QMessageBox.warning(self, "Error", "Connection ID is required for 'connection' auth type.")
                return
        elif auth_val == "managed_identity":
            openapi_data["auth"]["security_scheme"] = {"audience": audience_val}
            if not audience_val:
                QMessageBox.warning(self, "Error", "Audience is required for 'managed_identity' auth type.")
                return

        # Pass this object to our manager to handle creation/update in openapi_functions.json
        try:
            self.function_config_manager.save_openapi_function(openapi_data)
            QMessageBox.information(self, "Success", "OpenAPI definition saved successfully.")
            self.load_openapi_functions()
        except Exception as e:
            logger.error(f"Error saving OpenAPI function: {e}")
            QMessageBox.warning(self, "Error", f"Could not save OpenAPI function: {e}")

    def create_azure_logic_apps_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        ai_client = get_ai_client(AIClientType.AZURE_AI_AGENT)
        resource_group_name = ai_client.scope["resource_group_name"]

        # Clear Section Label
        selector_label = QLabel(f"Select Azure Logic App in Resource Group [ {resource_group_name} ]")
        selector_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        main_layout.addWidget(selector_label)

        # Logic App Selector and Buttons Layout
        selector_layout = QHBoxLayout()

        self.azureLogicAppSelector = QComboBox(self)
        selector_layout.addWidget(self.azureLogicAppSelector, stretch=3)

        self.loadAzureLogicAppsButton = QPushButton("Load", self)
        self.loadAzureLogicAppsButton.clicked.connect(self.load_azure_logic_apps)
        self.loadAzureLogicAppsButton.setFixedWidth(100)
        selector_layout.addWidget(self.loadAzureLogicAppsButton, stretch=1)

        self.viewLogicAppDetailsButton = QPushButton("Details...", self)
        self.viewLogicAppDetailsButton.clicked.connect(self.open_logic_app_details_dialog)
        self.viewLogicAppDetailsButton.setFixedWidth(100)
        selector_layout.addWidget(self.viewLogicAppDetailsButton, stretch=1)

        main_layout.addLayout(selector_layout)

        # Action buttons layout
        buttons_layout = QHBoxLayout()
        self.generateUserFunctionFromLogicAppButton = QPushButton("Generate User Function from Logic App", self)
        self.generateUserFunctionFromLogicAppButton.clicked.connect(self.generate_user_function_for_logic_app)
        buttons_layout.addWidget(self.generateUserFunctionFromLogicAppButton)

        self.clearImplementationButton = QPushButton("Clear Implementation", self)
        self.clearImplementationButton.clicked.connect(self.clear_azure_user_function_impl)
        buttons_layout.addWidget(self.clearImplementationButton)
        main_layout.addLayout(buttons_layout)

        # Text Edit Layouts
        self.azureUserFunctionSpecEdit = self.create_text_edit()
        self.azureUserFunctionImplEdit = self.create_text_edit()

        splitter = QSplitter(Qt.Vertical, self)
        splitter.addWidget(self.create_text_edit_labeled("Function Specification:", self.azureUserFunctionSpecEdit))
        splitter.addWidget(self.create_text_edit_labeled("Function Implementation:", self.azureUserFunctionImplEdit))
        main_layout.addWidget(splitter)

        return tab

    def create_azure_functions_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout(tab)

        ai_client = get_ai_client(AIClientType.AZURE_AI_AGENT)
        resource_group_name = ai_client.scope["resource_group_name"]

        grid_layout = QGridLayout()
        main_layout.addLayout(grid_layout)

        # Row 0: Label for resource group, combo for Function App, Load button
        label_app = QLabel(f"Select Azure Function App in Resource Group [ {resource_group_name} ]")
        label_app.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.azureFunctionAppSelector = QComboBox(self)
        self.loadAzureFunctionAppsButton = QPushButton("Load", self)
        self.loadAzureFunctionAppsButton.clicked.connect(self.load_azure_function_apps)

        grid_layout.addWidget(label_app,                  0, 0)
        grid_layout.addWidget(self.azureFunctionAppSelector, 0, 1)
        grid_layout.addWidget(self.loadAzureFunctionAppsButton, 0, 2)

        # Row 1: Label for Function in App, combo for function, Details button
        label_func = QLabel("Select Function in App:")
        self.azureFunctionSelector = QComboBox(self)
        self.viewAzureFunctionDetailsButton = QPushButton("Details..", self)
        self.viewAzureFunctionDetailsButton.clicked.connect(self.open_azure_function_details_dialog)

        grid_layout.addWidget(label_func,                      1, 0)
        grid_layout.addWidget(self.azureFunctionSelector,      1, 1)
        grid_layout.addWidget(self.viewAzureFunctionDetailsButton, 1, 2)

        # Row 2: Label for local specs, local combo
        label_local = QLabel("Pick from local Azure Function specs:")
        self.azureFunctionSelectorLocal = self.create_function_selector("azure_function")
        grid_layout.addWidget(label_local,                  2, 0)
        grid_layout.addWidget(self.azureFunctionSelectorLocal, 2, 1)

        # Row 3+: The specification text edit
        code_widget = self.create_text_edit_labeled(
            "Azure Function Specification (from local config or loaded from Azure):",
            self.azureFunctionSpecEdit
        )
        # Span this widget across all grid columns for a full-width area
        grid_layout.addWidget(code_widget, 3, 0, 1, 3)

        # Setup signals to index changes (if not already connected earlier)
        self.azureFunctionAppSelector.currentIndexChanged.connect(self.on_azure_function_app_selected)
        self.azureFunctionSelector.currentIndexChanged.connect(self.on_azure_function_selected)

        return tab

    def parse_azure_storage_connection_string(self, conn_str):
        conn_dict = {}
        for segment in conn_str.strip().split(';'):
            if '=' in segment:
                key, value = segment.split('=', 1)
                conn_dict[key.strip()] = value.strip()
        return conn_dict

    def build_azure_function_spec(self, app_details: dict, function_details: dict) -> dict:

        # Make a deep copy so we don't modify the original template
        spec = copy.deepcopy(azure_function_spec_template)

        # Extract the short function name from e.g. "func-app/queue_trigger"
        full_function_name = function_details.get("name", "")
        short_function_name = full_function_name.split("/")[-1] if full_function_name else "unknown_function"
        spec["azure_function"]["function"]["name"] = short_function_name

        # Identify the input and output bindings from the function details
        bindings = function_details.get("bindings", [])
        input_binding_info = next((b for b in bindings if b.get("direction") == "IN"), None)
        output_binding_info = next((b for b in bindings if b.get("direction") == "OUT"), None)

        # Fill in the input binding info (if present)
        if input_binding_info:
            in_queue_name = input_binding_info.get("queueName", "input")
            spec["azure_function"]["input_binding"]["storage_queue"]["queue_name"] = in_queue_name

            # Extract the account name from the connection string to form the queue service URI
            queue_service_connection = app_details.get("AzureWebJobsStorage", "")
            if queue_service_connection:
                conn_dict = self.parse_azure_storage_connection_string(queue_service_connection)
                account_name = conn_dict.get("AccountName")
                if account_name:
                    storage_queue_uri = f"https://{account_name}.queue.core.windows.net"
                    spec["azure_function"]["input_binding"]["storage_queue"]["queue_service_uri"] = storage_queue_uri
                else:
                    raise ValueError("AccountName not found in storage connection string for input binding.")
            else:
                raise ValueError("AzureWebJobsStorage connection string is missing.")
        else:
            # No IN binding -> remove the input_binding section altogether
            spec["azure_function"].pop("input_binding", None)

        # Fill in the output binding info (if present)
        if output_binding_info:
            out_queue_name = output_binding_info.get("queueName", "output")
            queue_service_connection = app_details.get("AzureWebJobsStorage", "")
            spec["azure_function"]["output_binding"]["storage_queue"]["queue_name"] = out_queue_name

            queue_service_connection = app_details.get("AzureWebJobsStorage", "")
            if queue_service_connection:
                conn_dict = self.parse_azure_storage_connection_string(queue_service_connection)
                account_name = conn_dict.get("AccountName")
                if account_name:
                    storage_queue_uri = f"https://{account_name}.queue.core.windows.net"
                    spec["azure_function"]["output_binding"]["storage_queue"]["queue_service_uri"] = storage_queue_uri
                else:
                    raise ValueError("AccountName not found in storage connection string for output binding.")
            else:
                raise ValueError("AzureWebJobsStorage connection string is missing.")
        else:
            # No OUT binding -> remove the output_binding section
            spec["azure_function"].pop("output_binding", None)

        return spec

    def open_azure_function_details_dialog(self):
        try:
            if not hasattr(self.main_window, 'azure_function_manager'):
                QMessageBox.warning(self, "Warning", "Azure Function Manager is not available.")
                return

            manager: AzureFunctionManager = self.main_window.azure_function_manager
            function_app_name = self.azureFunctionAppSelector.currentText()
            func_data = self.azureFunctionSelector.currentData()

            if not function_app_name or not func_data:
                QMessageBox.warning(self, "Warning", "No Azure Function selected.")
                return

            # The function_data structure often has a 'name' key like "appName/functions/FunctionName"
            function_name = func_data.get("name", "")
            if not function_name:
                QMessageBox.warning(self, "Warning", "Invalid function name.")
                return

            app_details = manager.get_function_app_details(function_app_name)
            function_details = manager.get_function_details(function_app_name, function_name)
            if not function_details:
                QMessageBox.information(self, "Details", f"No details available for function '{function_name}'.")
                return

            # Display both the app_details and function_details in a simple dialog
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Details for {function_name}")
            layout = QVBoxLayout(dialog)

            text_edit = QTextEdit(dialog)
            text_edit.setReadOnly(True)

            # Enable line wrapping and character/word wrapping
            text_edit.setLineWrapMode(text_edit.LineWrapMode.WidgetWidth)
            text_edit.setWordWrapMode(QTextOption.WrapMode.WordWrap)

            # Combine both details into a single string, formatted with JSON:
            result_text = "App Details:\n"
            result_text += json.dumps(app_details, indent=4)
            result_text += "\n\nFunction Details:\n"
            result_text += json.dumps(function_details, indent=4)

            text_edit.setText(result_text)
            layout.addWidget(text_edit)

            dialog.resize(600, 400)
            dialog.setLayout(layout)
            dialog.exec()
        except Exception as e:
            logger.error(f"Error retrieving function details: {e}")
            QMessageBox.warning(self, "Error", "Error retrieving function details.")

    def load_azure_function_apps(self):
        if not hasattr(self.main_window, 'azure_function_manager'):
            QMessageBox.warning(self, "Error", "AzureFunctionManager not available.")
            return
        
        try:
            self.azure_function_manager : AzureFunctionManager = self.main_window.azure_function_manager

            # Initialize (this will discover function apps in the RG)
            self.azure_function_manager.initialize_function_apps()

            self.azureFunctionAppSelector.clear()
            apps = self.azure_function_manager.list_function_apps()
            if not apps:
                self.azureFunctionAppSelector.addItem("No Function Apps found", None)
                return
            for app_name in apps:
                self.azureFunctionAppSelector.addItem(app_name, app_name)
        except Exception as e:
            logger.error(f"Error loading Azure Function Apps: {e}")
            QMessageBox.warning(self, "Error", f"Error loading Azure Function Apps: {e}")

    def on_azure_function_app_selected(self):
        app_name = self.azureFunctionAppSelector.currentData()
        if not app_name:
            # Possibly "No Function Apps found" or similar
            self.azureFunctionSelector.clear()
            self.azureFunctionSelector.addItem("No Functions", None)
            return

        # Retrieve the functions in that app
        if not hasattr(self, 'azure_function_manager'):
            QMessageBox.warning(self, "Error", "AzureFunctionManager not available.")
            return

        try:
            functions = self.azure_function_manager.list_azure_functions_in_app(app_name)
            self.azureFunctionSelector.clear()
            if not functions:
                self.azureFunctionSelector.addItem("No Functions in this app", None)
                return
            
            for fn in functions:
                # The 'name' is typically "appName/functions/FunctionName". 
                # Possibly parse it for a friendly label, or you can show the raw name
                friendly_name = fn["name"]
                function_name = friendly_name.split("/")[-1]
                self.azureFunctionSelector.addItem(function_name, fn)
        except Exception as e:
            logger.error(f"Error loading functions in app '{app_name}': {e}")
            QMessageBox.warning(self, "Error", f"Error listing functions in '{app_name}': {e}")

    def on_azure_function_selected(self):
        func_data = self.azureFunctionSelector.currentData()
        if not func_data:
            self.azureFunctionSpecEdit.clear()
            return

        full_name = func_data.get("name", "")
        if not full_name:
            self.azureFunctionSpecEdit.clear()
            return

        # Make a copy so the global template isn't mutated
        app_details = self.azure_function_manager.get_function_app_details(self.azureFunctionAppSelector.currentText())
        function_details = self.azure_function_manager.get_function_details(self.azureFunctionAppSelector.currentText(), full_name)
        spec = self.build_azure_function_spec(app_details, function_details)

        # Convert the spec template to a JSON string
        template_json_str = json.dumps(spec, indent=4)

        # Display the JSON in the edit box
        self.azureFunctionSpecEdit.setPlainText(template_json_str)

    def clear_azure_user_function_impl(self):
        self.azureUserFunctionSpecEdit.clear()
        self.azureUserFunctionImplEdit.clear()

    def load_azure_logic_apps(self):
        self.azureLogicAppSelector.clear()
        self.azureLogicAppSelector.addItems(self.list_logic_app_names())

    def open_logic_app_details_dialog(self):
        from PySide6.QtWidgets import QMessageBox
        try:
            if hasattr(self.main_window, 'azure_logic_app_manager'):
                azure_manager: AzureLogicAppManager = self.main_window.azure_logic_app_manager
                logic_app_name = self.azureLogicAppSelector.currentText()
                if logic_app_name:
                    base_name = logic_app_name.split(" (HTTP Trigger)")[0]
                    logic_app_details = azure_manager.get_logic_app_details(base_name)
                    dialog = LogicAppDetailsDialog(details=logic_app_details, parent=self)
                    dialog.exec()
                else:
                    QMessageBox.warning(self, "Warning", "No Logic App selected.")
            else:
                QMessageBox.warning(self, "Warning", "Azure Logic App Manager is not available.")
        except Exception as e:
            logger.error(f"Error retrieving logic app details: {e}")
            QMessageBox.warning(self, "Error", "Error retrieving logic app details.")

    def list_logic_app_names(self) -> List[str]:
        names = []
        try:
            if hasattr(self.main_window, 'azure_logic_app_manager'):
                azure_manager: AzureLogicAppManager = self.main_window.azure_logic_app_manager
                azure_manager.initialize_logic_apps(trigger_name="When_a_HTTP_request_is_received")
                names = azure_manager.list_logic_apps()
        except Exception as e:
            logger.error(f"Error listing logic apps: {e}")
        return names

    def generate_user_function_for_logic_app(self):
        try:
            if not hasattr(self, 'azure_logic_app_function_creator'):
                raise Exception("Azure Logic App function creator not available, check the system assistant settings")
            logic_app_name = self.azureLogicAppSelector.currentText()
            base_name = logic_app_name.split(" (HTTP Trigger)")[0]
            azure_manager: AzureLogicAppManager = self.main_window.azure_logic_app_manager
            schema = azure_manager.get_http_trigger_schema(
                logic_app_name=base_name,
                trigger_name="When_a_HTTP_request_is_received"
            )
            schema_text = json.dumps(schema, indent=4)
            if not schema_text:
                raise ValueError("Schema is empty. Please ensure the schema is loaded correctly.")

            request_message = (
                f"Function name: {camel_to_snake(base_name)}\n"
                f"Logic App Name for the invoke method inside the function: {base_name}\n"
                f"JSON Schema: {schema_text}\n"
                "Please generate a Python function which name is given logic app name "
                "and that accepts input parameters based on the given JSON schema."
            )

            self.start_processing_signal.start_signal.emit(ActivityStatus.PROCESSING)
            # Generate the spec (goes into self.azure_spec_json on completion)
            threading.Thread(target=self._generate_function_spec, args=(request_message, FunctionTab.AZURE_LOGIC_APP.value)).start()
            # Generate the code (goes into self.azure_code on completion)
            threading.Thread(target=self._generate_logic_app_user_function_thread, args=(request_message,)).start()
        except Exception as e:
            self.error_signal.error_signal.emit(
                f"An error occurred while generating the user function for the Logic App: {e}"
            )

    def _generate_logic_app_user_function_thread(self, request_message):
        try:
            self.azure_code = self.azure_logic_app_function_creator.process_messages(
                user_request=request_message, 
                stream=False
            )
            logger.info("User function implementation generated successfully.")
        except Exception as e:
            self.error_signal.error_signal.emit(
                f"An error occurred while generating the user function for the Logic App: {e}"
            )
        finally:
            self.stop_processing_signal.stop_signal.emit(ActivityStatus.PROCESSING)

    def toggle_max_height(self):
        if not self.isMaximized():
            self.previousSize = self.size()
            self.showMaximized()
        else:
            self.showNormal()
            self.resize(self.previousSize) 

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F11:
            self.toggle_max_height()
        else:
            super().keyPressEvent(event)

    def onTabChanged(self, index):
        tab_text = self.tabs.tabText(index)
        if tab_text in ["User Functions", "OpenAPI"]:
            self.removeButton.setEnabled(True)
            self.removeButton.setDisabled(False)
        else:
            self.removeButton.setEnabled(False)
            self.removeButton.setDisabled(True)

    def create_system_functions_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.systemFunctionSelector = self.create_function_selector("system")
        layout.addWidget(QLabel("Select System Function:"))
        layout.addWidget(self.systemFunctionSelector)

        layout.addWidget(QLabel("Function Specification:"))
        layout.addWidget(self.systemSpecEdit)

        return tab

    def create_user_functions_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.userFunctionSelector = self.create_function_selector("user")
        layout.addWidget(QLabel("Select User Function:"))
        layout.addWidget(self.userFunctionSelector)

        self.userRequestLabel = QLabel("Function Requirements:")
        self.userRequest = QTextEdit(self)
        self.userRequest.setText("Create a function that...")
        self.userRequest.setMaximumHeight(50)
        self.userRequest.setStyleSheet(
            "QTextEdit {"
            "  border-style: solid;"
            "  border-width: 1px;"
            "  border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;"
            "  padding: 1px;"
            "}"
        )
        layout.addWidget(self.userRequestLabel)
        layout.addWidget(self.userRequest)

        self.generateSpecButton = QPushButton("Generate Specification with AI...", self)
        self.generateSpecButton.clicked.connect(self.generate_function_spec)
        layout.addWidget(self.generateSpecButton)

        splitter = QSplitter(Qt.Vertical, self)
        splitter.addWidget(self.create_text_edit_labeled("Function Specification:", self.userSpecEdit))
        splitter.addWidget(self.create_text_edit_labeled("Function Implementation:", self.userImplEdit))
        layout.addWidget(splitter)

        self.generateImplButton = QPushButton("Generate Implementation with AI...", self)
        self.generateImplButton.clicked.connect(self.generate_function_impl)
        layout.addWidget(self.generateImplButton)

        return tab

    def create_text_edit_labeled(self, label_text, text_edit_widget):
        widget = QWidget()
        widget.setStyleSheet("background-color: #2b2b2b;")
        layout = QVBoxLayout(widget)
        label = QLabel(label_text)
        label.setStyleSheet("""
            QLabel {
                color: #e0e0e0;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 10pt;
                background-color: #2b2b2b;
            }
        """)
        layout.addWidget(label)
        layout.addWidget(text_edit_widget)
        return widget

    def create_text_edit(self):
        textEdit = QTextEdit(self)
        textEdit.setStyleSheet("""
            QTextEdit {
                background-color: #2b2b2b;
                color: #e0e0e0;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 10pt;
            }
        """)
        textEdit.setAcceptRichText(False)
        return textEdit

    def create_function_selector(self, function_type):
        if function_type == "system":
            spec_edit = self.systemSpecEdit
            impl_edit = None
        elif function_type == "user":
            spec_edit = self.userSpecEdit
            impl_edit = self.userImplEdit
        elif function_type == "azure_function":
            spec_edit = self.azureFunctionSpecEdit
            impl_edit = None
        else:
            # Fallback: no connected text boxes
            spec_edit = None
            impl_edit = None

        function_selector = QComboBox(self)
        function_selector.currentIndexChanged.connect(
            lambda: self.on_function_selected(function_selector, spec_edit, impl_edit)
        )

        # Populate based on the given type
        self.load_functions(function_selector, function_type)
        return function_selector

    def get_user_function_names(self):
        functions_data = self.function_config_manager.get_all_functions_data()
        return [
            f_spec["function"]["name"]
            for f_type, f_spec, _ in functions_data
            # Must be in manager-level "user" category
            # AND actually a normal function spec ("type": "function")
            if f_type == "user" and f_spec.get("type") == "function"
        ]

    def load_functions(self, function_selector, function_type):
        functions_data = self.function_config_manager.get_all_functions_data()
        function_selector.clear()

        # If it's the user tab, we also add "New Function" at the top:
        if function_type == "user":
            function_selector.addItem("New Function", None)

        for f_type, function_spec, _ in functions_data:
            if function_type == "user":
                if f_type == "user" and function_spec.get("type") == "function":
                    try:
                        func_name = function_spec["function"]["name"]
                        function_selector.addItem(func_name, (f_type, function_spec))
                    except Exception as e:
                        logger.error(f"Error loading user functions: {e}")

            elif function_type == "azure_function":
                if function_spec.get("type") == "azure_function":
                    try:
                        azure_block = function_spec.get("azure_function", {})
                        azure_func = azure_block.get("function", {})
                        func_name = azure_func.get("name", "UnnamedAzureFunction")
                        function_selector.addItem(func_name, (f_type, function_spec))
                    except Exception as e:
                        logger.error(f"Error loading Azure functions: {e}")

            else:
                if f_type == function_type:
                    try:
                        func_name = function_spec["function"]["name"]
                        function_selector.addItem(func_name, (f_type, function_spec))
                    except Exception as e:
                        logger.error(f"Error loading functions: {e}")

    def on_function_selected(self, function_selector, spec_edit, impl_edit=None):
        function_data = function_selector.currentData()
        if function_data:
            function_type, function_spec = function_data
            spec_edit.setText(json.dumps(function_spec, indent=4))
            # If user-type function, also load its implementation
            if impl_edit and function_type == "user":
                impl_edit.setText(self.function_config_manager.get_user_function_code(function_spec['function']['name']))
            elif impl_edit:
                impl_edit.clear()
        else:
            # "New Function" selected or no data
            spec_edit.clear()
            if impl_edit:
                impl_edit.clear()

    def start_processing(self, status):
        self.status_bar.start_animation(status)

    def stop_processing(self, status):
        self.status_bar.stop_animation(status)
        # Based on the current active tab, update only that tab's controls if needed
        current_tab = self.tabs.tabText(self.tabs.currentIndex())
        if current_tab == FunctionTab.USER.value:
            if self.user_spec_json is not None:
                self.userSpecEdit.setText(self.user_spec_json)
            if self.user_code is not None:
                self.userImplEdit.setText(self.user_code)
        elif current_tab == FunctionTab.AZURE_LOGIC_APP.value:
            if self.azure_spec_json is not None:
                self.azureUserFunctionSpecEdit.setText(self.azure_spec_json)
            if self.azure_code is not None:
                self.azureUserFunctionImplEdit.setText(self.azure_code)

    def generate_function_spec(self):
        user_request = self.userRequest.toPlainText()
        # When generating for user functions, indicate target as "User Functions".
        threading.Thread(
            target=self._generate_function_spec, 
            args=(user_request, FunctionTab.USER.value)
        ).start()

    def _generate_function_spec(self, user_request, target_tab):
        try:
            if not hasattr(self, 'function_spec_creator'):
                raise Exception("Function spec creator not available, check the system assistant settings")
            self.start_processing_signal.start_signal.emit(ActivityStatus.PROCESSING)
            result = self.function_spec_creator.process_messages(user_request=user_request, stream=False)
            if target_tab == FunctionTab.USER.value:
                self.user_spec_json = result
            elif target_tab == FunctionTab.AZURE_LOGIC_APP.value:
                self.azure_spec_json = result
        except Exception as e:
            self.error_signal.error_signal.emit(f"An error occurred while generating the function spec: {e}")
        finally:
            self.stop_processing_signal.stop_signal.emit(ActivityStatus.PROCESSING)

    def generate_function_impl(self):
        user_request = self.userRequest.toPlainText()
        spec_json = self.userSpecEdit.toPlainText()
        threading.Thread(
            target=self._generate_function_impl, 
            args=(user_request, spec_json)
        ).start()

    def _generate_function_impl(self, user_request, spec_json):
        try:
            if not hasattr(self, 'function_impl_creator'):
                raise Exception("Function impl creator not available, check the system assistant settings")
            self.start_processing_signal.start_signal.emit(ActivityStatus.PROCESSING)
            request = user_request + " that follows the following spec: " + spec_json
            self.user_code = self.function_impl_creator.process_messages(user_request=request, stream=False)
        except Exception as e:
            self.error_signal.error_signal.emit(f"An error occurred while generating the function implementation: {e}")
        finally:
            self.stop_processing_signal.stop_signal.emit(ActivityStatus.PROCESSING)

    def save_function(self):
        current_tab_text = self.tabs.tabText(self.tabs.currentIndex())
        
        if current_tab_text == FunctionTab.SYSTEM.value:
            functionSpec = self.systemSpecEdit.toPlainText()
            functionImpl = None
            function_selector = self.systemFunctionSelector

        elif current_tab_text == FunctionTab.USER.value:
            functionSpec = self.userSpecEdit.toPlainText()
            functionImpl = self.userImplEdit.toPlainText()
            function_selector = self.userFunctionSelector

        elif current_tab_text == FunctionTab.AZURE_LOGIC_APP.value:
            functionSpec = self.azureUserFunctionSpecEdit.toPlainText()
            functionImpl = self.azureUserFunctionImplEdit.toPlainText()
            function_selector = None

        elif current_tab_text == FunctionTab.AZURE_FUNCTIONS.value:
            functionSpec = self.azureFunctionSpecEdit.toPlainText()
            functionImpl = None
            function_selector = self.azureFunctionSelector

        elif current_tab_text == FunctionTab.OPENAPI.value:
            try:
                self.save_openapi_function()
                return
            except Exception as e:
                QMessageBox.warning(self, "Error", f"An error occurred while saving the OpenAPI function: {e}")
                return
        else:
            QMessageBox.warning(self, "Error", "Invalid tab selected")
            return

        # Validate spec and impl
        try:
            is_valid, message = self.function_config_manager.validate_function(functionSpec, functionImpl)
            if not is_valid:
                QMessageBox.warning(self, "Error", f"Function is invalid: {message}")
                return
        except Exception as e:
            QMessageBox.warning(self, "Error", f"An error occurred while validating the function: {e}")
            return

        # Figure out the function name from the spec or the selector
        if current_tab_text == FunctionTab.AZURE_LOGIC_APP.value:
            try:
                spec_dict = json.loads(functionSpec)
                current_user_function_names = self.get_user_function_names()
                logic_app_function_name = spec_dict["function"]["name"]
                if logic_app_function_name in current_user_function_names:
                    QMessageBox.warning(self, "Error", f"Function '{logic_app_function_name}' already exists.")
                    return
                current_function_name = None
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Error extracting function name from the spec: {e}")
                return

        elif function_selector is not None:
            current_function_name = function_selector.currentText()
            if current_function_name == "New Function":
                current_function_name = None
        else:
            current_function_name = None

        # Save the spec
        try:
            _, new_function_name = self.function_config_manager.save_function_spec(functionSpec)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"An error occurred while saving the function spec: {e}")
            return

        # Save the impl if any
        file_path = None
        if functionImpl:
            try:
                file_path = self.function_config_manager.save_function_impl(
                    functionImpl, 
                    current_function_name, 
                    new_function_name
                )
            except Exception as e:
                QMessageBox.warning(self, "Error", f"An error occurred while saving the function implementation: {e}")
                return

        # Reload and refresh
        try:
            self.function_config_manager.load_function_configs()
            self.refresh_dropdown()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"An error occurred while reloading the function specs: {e}")
            return

        success_message = f"Function '{new_function_name or current_function_name}' saved successfully."
        if functionImpl and file_path:
            success_message += f" Impl file: {file_path}"
        QMessageBox.information(self, "Success", success_message)

    def remove_function(self):
        current_tab_text = self.tabs.tabText(self.tabs.currentIndex())

        if current_tab_text == FunctionTab.SYSTEM.value:
            QMessageBox.warning(self, "Not Supported", "Removing system functions is not supported.")
            return

        elif current_tab_text == FunctionTab.USER.value:
            function_name = self.userFunctionSelector.currentText().strip()
            if not function_name:
                QMessageBox.warning(self, "Error", "Please select a user function to remove.")
                return

            confirm = QMessageBox.question(
                self, "Confirm Remove", 
                f"Are you sure you want to remove the user function '{function_name}'?",
                QMessageBox.Yes | QMessageBox.No
            )
            if confirm == QMessageBox.No:
                return

            success = self.function_config_manager.delete_user_function(function_name)
            if success:
                QMessageBox.information(self, "Removed", f"User function '{function_name}' was removed successfully.")
                self.function_config_manager.load_function_configs()
                self.refresh_dropdown()
            else:
                QMessageBox.warning(self, "Error", f"User function '{function_name}' was not found.")

        elif current_tab_text == FunctionTab.AZURE_LOGIC_APP.value:
            QMessageBox.warning(self, "Not Supported", "Removing Azure Logic App functions is not yet supported.")
            return

        elif current_tab_text == FunctionTab.OPENAPI.value:
            openapi_name = self.openapiNameEdit.text().strip()
            if not openapi_name:
                QMessageBox.warning(self, "Error", "Cannot remove OpenAPI function: no name specified.")
                return

            confirm = QMessageBox.question(
                self, "Confirm Remove", 
                f"Are you sure you want to remove the OpenAPI definition '{openapi_name}'?",
                QMessageBox.Yes | QMessageBox.No
            )
            if confirm == QMessageBox.No:
                return

            success = self.function_config_manager.delete_openapi_function(openapi_name)
            if success:
                QMessageBox.information(self, "Removed", f"OpenAPI definition '{openapi_name}' was removed successfully.")
                self.load_openapi_functions()
            else:
                QMessageBox.warning(self, "Error", f"OpenAPI definition '{openapi_name}' was not found.")

        else:
            QMessageBox.warning(self, "Error", "Invalid tab selected for removal.")

    def refresh_dropdown(self):
        self.load_functions(self.userFunctionSelector, "user")
        self.load_functions(self.systemFunctionSelector, "system")
        # If an openapi tab is present, re-load that combobox too:
        if hasattr(self, 'openapiSelector'):
            self.load_openapi_functions()


class FunctionErrorsDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.function_config_manager = main_window.function_config_manager
        self.error_specs = {}
        self.loadErrorSpecs()
        self.initUI()

    def initUI(self):
        self.setWindowTitle("Function Errors Editor")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        # Error Category Header
        categoryHeader = QLabel("Error Categories")
        layout.addWidget(categoryHeader)

        self.errorList = QListWidget()
        self.errorList.currentItemChanged.connect(self.onCategorySelected)
        layout.addWidget(self.errorList)

        # Populate the list widget with keys from the error messages
        for key in self.error_specs.keys():
            self.errorList.addItem(key)

        # New Error Category Header
        newCategoryHeader = QLabel("New Error Category")
        layout.addWidget(newCategoryHeader)

        self.categoryEdit = QLineEdit()  # For adding/editing error categories
        layout.addWidget(self.categoryEdit)

        # Error Message Header
        messageHeader = QLabel("Error Specification")
        layout.addWidget(messageHeader)

        self.messageEdit = QTextEdit()  # For editing the error message
        layout.addWidget(self.messageEdit)

        # Buttons row
        buttonLayout = QHBoxLayout()
        addButton = QPushButton("Add")
        addButton.clicked.connect(self.addCategory)
        removeButton = QPushButton("Remove")
        removeButton.clicked.connect(self.removeCategory)
        saveButton = QPushButton("Save")
        saveButton.clicked.connect(self.saveErrors)
        buttonLayout.addWidget(addButton)
        buttonLayout.addWidget(removeButton)
        buttonLayout.addWidget(saveButton)
        layout.addLayout(buttonLayout)

        self.setLayout(layout)

        # If there's at least one error category, select the first by default
        if self.errorList.count() > 0:
            self.errorList.setCurrentRow(0)

    def loadErrorSpecs(self):
        try:
            self.error_specs = self.function_config_manager.get_function_error_specs()
        except Exception as e:
            logger.error(f"Error loading error specs: {e}")

    def onCategorySelected(self, current, previous):
        if current:
            category = current.text()
            self.messageEdit.setText(self.error_specs[category])

    def addCategory(self):
        new_category = self.categoryEdit.text().strip()
        new_message = self.messageEdit.toPlainText().strip()

        if new_category and new_category not in self.error_specs:
            self.error_specs[new_category] = new_message
            self.errorList.addItem(new_category)
            self.categoryEdit.clear()
            self.messageEdit.clear()
        elif new_category in self.error_specs:
            logger.warning(f"The category '{new_category}' already exists.")

    def removeCategory(self):
        selected = self.errorList.currentItem()
        if selected:
            category = selected.text()
            del self.error_specs[category]
            self.errorList.takeItem(self.errorList.row(selected))

    def saveErrors(self):
        selected = self.errorList.currentItem()
        if selected:
            category = selected.text()
            new_message = self.messageEdit.toPlainText()
            self.error_specs[category] = new_message
            self.saveErrorSpecsToFile()

    def saveErrorSpecsToFile(self):
        try:
            self.function_config_manager.save_function_error_specs(self.error_specs)
        except Exception as e:
            logger.error(f"Error saving error specs: {e}")


def format_logic_app_details(details: dict) -> str:
    from datetime import datetime

    class DateTimeEncoder(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, datetime):
                return o.isoformat()
            return super().default(o)

    return json.dumps(details, indent=4, cls=DateTimeEncoder)


class LogicAppDetailsDialog(QDialog):
    def __init__(self, details: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logic App Details")
        self.resize(600, 400)
        
        layout = QVBoxLayout(self)
        
        self.details_text = QTextEdit(self)
        self.details_text.setReadOnly(True)
        self.details_text.setStyleSheet("""
            QTextEdit {
                background-color: #f0f0f0;
                color: #000000;
                font-family: Consolas, Monaco, monospace;
                font-size: 10pt;
            }
        """)
        
        formatted_details = format_logic_app_details(details)
        self.details_text.setPlainText(formatted_details)
        layout.addWidget(self.details_text)
        
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        
        self.setLayout(layout)