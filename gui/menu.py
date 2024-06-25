# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

# This software uses the PySide6 library, which is licensed under the GNU Lesser General Public License (LGPL).
# For more details on PySide6's license, see <https://www.qt.io/licensing>

from PySide6.QtWidgets import QDialog, QMessageBox
from PySide6.QtGui import QAction

from azure.ai.assistant.management.assistant_client import AssistantClient
from azure.ai.assistant.management.ai_client_factory import AIClientType
from azure.ai.assistant.management.chat_assistant_client import ChatAssistantClient
from azure.ai.assistant.management.function_config_manager import FunctionConfigManager
from azure.ai.assistant.management.logger_module import logger, add_broadcaster_to_logger
from gui.debug_dialog import DebugViewDialog
from gui.assistant_dialogs import AssistantConfigDialog, ExportAssistantDialog
from gui.function_dialogs import CreateFunctionDialog, FunctionErrorsDialog
from gui.task_dialogs import CreateTaskDialog, ScheduleTaskDialog
from gui.settings_dialogs import ClientSettingsDialog, GeneralSettingsDialog
from gui.assistant_client_manager import AssistantClientManager
from gui.log_broadcaster import LogBroadcaster

from azure.core.credentials import AzureKeyCredential

class AssistantsMenu:
    def __init__(self, main_window):
        self.main_window = main_window
        self.assistants_menu = self.main_window.menuBar().addMenu("&Assistants")
        self.function_config_manager = FunctionConfigManager.get_instance()
        self.assistant_client_manager = AssistantClientManager.get_instance()
        self.create_assistants_menu()

    def create_assistants_menu(self):
        self.assistantActions = {}

        createAssistantAction = QAction('Create New / Edit OpenAI Assistant', self.main_window)
        createAssistantAction.triggered.connect(self.create_new_edit_assistant)
        self.assistants_menu.addAction(createAssistantAction)

        createChatAssistantAction = QAction('Create New / Edit Chat Assistant', self.main_window)
        createChatAssistantAction.triggered.connect(self.create_new_edit_chat_assistant)
        self.assistants_menu.addAction(createChatAssistantAction)

        # Add an action for exporting an assistant
        exportAction = QAction('Export', self.main_window)
        exportAction.triggered.connect(self.export_assistant)
        self.assistants_menu.addAction(exportAction)

    def create_new_edit_assistant(self):
        self.dialog = AssistantConfigDialog(parent=self.main_window, function_config_manager=self.function_config_manager)
        
        # Connect the custom signal to a method to process the submitted data
        self.dialog.assistantConfigSubmitted.connect(self.on_assistant_config_submitted)
        
        # Show the dialog non-modally
        self.dialog.show()

    def create_new_edit_chat_assistant(self):
        self.dialog = AssistantConfigDialog(parent=self.main_window, assistant_type="chat_assistant", function_config_manager=self.function_config_manager)
        
        # Connect the custom signal to a method to process the submitted data
        self.dialog.assistantConfigSubmitted.connect(self.on_assistant_config_submitted)
        
        # Show the dialog non-modally
        self.dialog.show()

    def on_assistant_config_submitted(self, assistant_config_json, ai_client_type, assistant_type, endpoint, key):
        try:
            if assistant_type == "chat_assistant":
                client_args = {}
                if ai_client_type == "AZURE_INFERENCE":
                    client_args = {
                        "endpoint": endpoint, 
                        "credential": AzureKeyCredential(key), 
                        "headers": {"api-key": key}}
                assistant_client = ChatAssistantClient.from_json(assistant_config_json, self.main_window, self.main_window.connection_timeout, **client_args)
            else:
                assistant_client = AssistantClient.from_json(assistant_config_json, self.main_window, self.main_window.connection_timeout)
            self.assistant_client_manager.register_client(assistant_client.name, assistant_client)
            client_type = AIClientType[ai_client_type]
            self.main_window.conversation_sidebar.load_assistant_list(client_type)
            self.dialog.update_assistant_combobox()
        except Exception as e:
            QMessageBox.warning(self.main_window, "Error", f"An error occurred while creating/updating the assistant: {e}")

    def export_assistant(self):
        dialog = ExportAssistantDialog()
        dialog.exec_()


class FunctionsMenu:
    def __init__(self, main_window):
        self.main_window = main_window
        self.funtionsMenu = self.main_window.menuBar().addMenu('&Functions')
        self.setup_functions_menu()

    def setup_functions_menu(self):
        createFunctionAction = QAction('Create New/Edit', self.main_window)
        createFunctionAction.triggered.connect(lambda: self.create_function())
        self.funtionsMenu.addAction(createFunctionAction)
        editErrorMessagesAction = QAction('Error Categories', self.main_window)
        editErrorMessagesAction.triggered.connect(lambda: self.edit_error_messages())
        self.funtionsMenu.addAction(editErrorMessagesAction)

    def edit_error_messages(self):
        editor = FunctionErrorsDialog(self.main_window)
        editor.show()

    def create_function(self):
        dialog = CreateFunctionDialog(self.main_window)
        dialog.show()


class DiagnosticsMenu:
    def __init__(self, main_window):
        self.main_window = main_window
        self.diagnosticsMenu = self.main_window.menuBar().addMenu('&Diagnostics')
        self.debugViewDialog = None
        self.broadcaster = None
        self.setup_menu()

    def setup_menu(self):
        # Action for function diagnostics
        diagAction = QAction("Run View", self.main_window, checkable=True)
        diagAction.triggered.connect(self.toggle_diagnostics_sidebar)
        self.diagnosticsMenu.addAction(diagAction)

        debugViewAction = QAction("Debug View", self.main_window)
        debugViewAction.triggered.connect(self.show_debug_view)
        self.diagnosticsMenu.addAction(debugViewAction)

    def toggle_diagnostics_sidebar(self, state):
        self.main_window.diagnostics_sidebar.setVisible(not self.main_window.diagnostics_sidebar.isVisible())

    def show_debug_view(self):
        if not self.debugViewDialog:
            self.broadcaster = LogBroadcaster()
            self.debugViewDialog = DebugViewDialog(self.broadcaster, self.main_window)
            add_broadcaster_to_logger(self.broadcaster)
        self.debugViewDialog.show()
        self.debugViewDialog.raise_()
        self.debugViewDialog.activateWindow()


class SettingsMenu:
    def __init__(self, main_window):
        self.main_window = main_window
        self.settingsMenu = self.main_window.menuBar().addMenu('&Settings')
        self.debugViewDialog = None
        self.broadcaster = None
        self.setup_menu()

    def setup_menu(self):
        chatSettingsAction = QAction("System Assistants", self.main_window)
        chatSettingsAction.triggered.connect(lambda: self.show_client_settings())
        self.settingsMenu.addAction(chatSettingsAction)

        # General settings
        generalSettingsAction = QAction("General", self.main_window)
        generalSettingsAction.triggered.connect(lambda: self.show_general_settings())
        self.settingsMenu.addAction(generalSettingsAction)

    def show_client_settings(self):
        dialog = ClientSettingsDialog(self.main_window)
        if dialog.exec_() == QDialog.Accepted:
            try:
                self.main_window.init_system_assistant_settings()
                self.main_window.init_system_assistants()
            except Exception as e:
                QMessageBox.warning(self.main_window, "Error", f"An error occurred while updating the settings: {e}")

    def show_general_settings(self):
        dialog = GeneralSettingsDialog(self.main_window)
        dialog.show()


class TasksMenu:
    def __init__(self, main_window):
        self.main_window = main_window
        self.tasksMenu = self.main_window.menuBar().addMenu('&Tasks')
        self.setup_tasks_menu()

    def setup_tasks_menu(self):
        # Action for editing error messages
        createTaskAction = QAction('Create New/Edit', self.main_window)
        createTaskAction.triggered.connect(lambda: self.create_task())
        self.tasksMenu.addAction(createTaskAction)
        # Action for schedule task
        scheduleTaskAction = QAction('Schedule', self.main_window)
        scheduleTaskAction.triggered.connect(lambda: self.schedule_task())
        self.tasksMenu.addAction(scheduleTaskAction)
        # Action for Show Tasks
        showTasksAction = QAction('View', self.main_window)
        showTasksAction.triggered.connect(lambda: self.show_scheduled_tasks())
        self.tasksMenu.addAction(showTasksAction)

    def create_task(self):
        dialog = CreateTaskDialog(self.main_window, self.main_window.task_manager)
        dialog.show()

    def schedule_task(self):
        dialog = ScheduleTaskDialog(self.main_window, self.main_window.task_manager)
        dialog.show()

    def show_scheduled_tasks(self):
        # Show not implemented dialog
        QMessageBox.information(self.main_window, "Not Implemented", "This feature is not implemented yet.")
        #dialog = ShowScheduledTasksDialog(self.main_window)
        #dialog.show()