# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

# This software uses the PySide6 library, which is licensed under the GNU Lesser General Public License (LGPL).
# For more details on PySide6's license, see <https://www.qt.io/licensing>

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QTextOption

import json, os, shutil, threading

from azure.ai.assistant.management.assistant_config_manager import AssistantConfigManager
from azure.ai.assistant.management.assistant_config import AssistantType
from azure.ai.assistant.management.assistant_config import ToolResourcesConfig, VectorStoreConfig
from azure.ai.assistant.management.function_config import OpenAPIFunctionConfig
from azure.ai.assistant.management.function_config_manager import FunctionConfigManager
from azure.ai.assistant.management.ai_client_factory import AIClientType, AIClientFactory
from azure.ai.assistant.management.logger_module import logger
from gui.signals import ErrorSignal, StartStatusAnimationSignal, StopStatusAnimationSignal
from gui.status_bar import ActivityStatus, StatusBar
from gui.assistant_client_manager import AssistantClientManager


class CustomSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)

    def textFromValue(self, value):
        # Return an empty string when the spin box is disabled
        if not self.isEnabled():
            return ""
        return str(value)


class AssistantConfigDialog(QDialog):
    assistantConfigSubmitted = Signal(str, str, str, str)

    def __init__(
            self, 
            parent=None, 
            assistant_type : str = AssistantType.ASSISTANT.value,
            assistant_name : str = None,
            function_config_manager : FunctionConfigManager = None
    ):
        super().__init__(parent)
        self.main_window = parent
        if hasattr(self.main_window, 'instructions_reviewer'):
            self.instructions_reviewer = self.main_window.instructions_reviewer
        self.assistant_config_manager = self.main_window.assistant_config_manager
        self.assistant_type = assistant_type
        self.assistant_name = assistant_name
        self.function_config_manager = function_config_manager

        self.init_variables()
        self.init_ui()

    def init_variables(self):
        self.code_interpreter_files = {}
        self.file_search_files = {}
        self.vector_store_ids = []
        self.functions = []  # Store the functions
        self.code_interpreter = False  # Store the code interpreter setting
        self.file_search = False  # Store the file search setting
        self.checkBoxes = {}  # To keep track of all function checkboxes
        self.assistant_id = ''
        self.default_output_folder_path = os.path.join(os.getcwd(), 'output')
        self.default_keyword_model_file_path = os.path.join(os.getcwd(), 'assets', 'kws.table')
        self.default_voice_activity_detection_model_path = os.path.join(os.getcwd(), 'assets', 'silero_vad.onnx')
        # make sure the output folder path exists and create it if it doesn't
        if not os.path.exists(self.default_output_folder_path):
            os.makedirs(self.default_output_folder_path)

    def on_tab_changed(self, index):
        current_tab_text = self.tabWidget.tabText(index)
        
        # If the "Instructions Editor" tab is now active,
        # copy instructions from the "General" tab (if any).
        if current_tab_text == "Instructions Editor":
            if hasattr(self, 'instructionsEdit') and hasattr(self, 'newInstructionsEdit'):
                self.newInstructionsEdit.setPlainText(self.instructionsEdit.toPlainText())
        
        # If the "General" tab is now active, copy back whatever is in "Instructions Editor"
        # as long as there is some text in newInstructionsEdit.
        elif current_tab_text == "General":
            if hasattr(self, 'newInstructionsEdit') and self.newInstructionsEdit.toPlainText():
                self.instructionsEdit.setPlainText(self.newInstructionsEdit.toPlainText())

    def closeEvent(self, event):
        super(AssistantConfigDialog, self).closeEvent(event)

    def init_ui(self):
        self.error_signal = ErrorSignal()
        self.error_signal.error_signal.connect(lambda error_message: QMessageBox.warning(self, "Error", error_message))

        self.setWindowTitle("Assistant Configuration")
        self.tabWidget = QTabWidget(self)
        self.tabWidget.currentChanged.connect(self.on_tab_changed)

        # Create General Configuration tab
        configTab = self.create_config_tab()
        self.tabWidget.addTab(configTab, "General")

        # Create Actions tab (replaces part of Tools)
        actionsTab = self.create_actions_tab()
        self.tabWidget.addTab(actionsTab, "Actions")

        # Create Knowledge tab (replaces part of Tools)
        knowledgeTab = self.create_knowledge_tab()
        self.tabWidget.addTab(knowledgeTab, "Knowledge")

        # Create Completion tab
        completionTab = self.create_completion_tab()
        self.tabWidget.addTab(completionTab, "Completion")

        # Create Audio tab for real-time assistant
        if self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
            self.create_realtime_tab()
            self.tabWidget.addTab(self.create_realtime_tab(), "Realtime")

        # Create Instructions Editor tab
        instructionsEditorTab = self.create_instructions_tab()
        self.tabWidget.addTab(instructionsEditorTab, "Instructions Editor")

        # Set the main layout
        mainLayout = QVBoxLayout(self)
        mainLayout.addWidget(self.tabWidget)

        # Save Button
        self.saveButton = QPushButton('Save Configuration')
        self.saveButton.clicked.connect(self.save_configuration)
        mainLayout.addWidget(self.saveButton)

        # Setup status bar
        self.status_bar = StatusBar(self)
        mainLayout.addWidget(self.status_bar.get_widget())

        # Set the main layout
        self.setLayout(mainLayout)

        self.start_processing_signal = StartStatusAnimationSignal()
        self.stop_processing_signal = StopStatusAnimationSignal()
        self.start_processing_signal.start_signal.connect(self.start_processing)
        self.stop_processing_signal.stop_signal.connect(self.stop_processing)

        self.update_model_combobox()
        self.update_assistant_combobox()

        # Set the initial size of the dialog to make it wider
        if self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
            self.resize(800, 400)
        else:
            self.resize(600, 600)

    def create_config_tab(self):
        configTab = QWidget()  # Configuration tab
        configLayout = QVBoxLayout(configTab)

        # AI client selection
        self.aiClientLabel = QLabel('AI Client:')
        self.aiClientComboBox = QComboBox()
        if self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
            self.aiClientComboBox.addItem(AIClientType.OPEN_AI_REALTIME.name)
            self.aiClientComboBox.addItem(AIClientType.AZURE_OPEN_AI_REALTIME.name)
        elif self.assistant_type == AssistantType.CHAT_ASSISTANT.value or self.assistant_type == AssistantType.ASSISTANT.value:
            self.aiClientComboBox.addItem(AIClientType.OPEN_AI.name)
            self.aiClientComboBox.addItem(AIClientType.AZURE_OPEN_AI.name)
            self.aiClientComboBox.setEnabled(True)  # Allow user selection for non-realtime
        elif self.assistant_type == AssistantType.AGENT.value:
            self.aiClientComboBox.addItem(AIClientType.AZURE_AI_AGENT.name)
            self.aiClientComboBox.setEnabled(True)  # Allow user selection for non-realtime

        active_ai_client_type = self.main_window.active_ai_client_type
        self.aiClientComboBox.setCurrentIndex(self.aiClientComboBox.findText(active_ai_client_type.name))
        self.aiClientComboBox.currentIndexChanged.connect(self.ai_client_selection_changed)
        configLayout.addWidget(self.aiClientLabel)
        configLayout.addWidget(self.aiClientComboBox)

        # Assistant selection combo box
        self.assistantLabel = QLabel('Assistant:')
        self.assistantComboBox = QComboBox()
        self.assistantComboBox.currentIndexChanged.connect(self.assistant_selection_changed)
        configLayout.addWidget(self.assistantLabel)
        configLayout.addWidget(self.assistantComboBox)

        # Name input field
        self.nameLabel = QLabel('Name:')
        self.nameEdit = QLineEdit()
        self.nameEdit.setStyleSheet(
            "QLineEdit {"
            "  border-style: solid;"
            "  border-width: 1px;"
            "  border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;"
            "  padding: 1px;"
            "}"
        )
        configLayout.addWidget(self.nameLabel)
        configLayout.addWidget(self.nameEdit)

        # Instructions - using QTextEdit for multi-line input
        self.instructionsLabel = QLabel('Instructions:')
        self.instructionsEdit = QTextEdit()
        self.instructionsEdit.setStyleSheet(
            "QTextEdit {"
            "  border-style: solid;"
            "  border-width: 1px;"
            "  border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;"
            "  padding: 1px;"
            "}"
        )
        self.instructionsEdit.setAcceptRichText(False)
        self.instructionsEdit.setWordWrapMode(QTextOption.WordWrap)
        self.instructionsEdit.setMinimumHeight(100)
        configLayout.addWidget(self.instructionsLabel)
        configLayout.addWidget(self.instructionsEdit)

        # File references, Add File, and Remove File buttons
        if self.assistant_type != AssistantType.REALTIME_ASSISTANT.value:
            # File references, Add File, and Remove File buttons
            self.fileReferenceLabel = QLabel('File References for Instructions:')
            self.fileReferenceList = QListWidget()
            self.fileReferenceList.setMaximumHeight(100)
            self.fileReferenceList.setToolTip(
                "Select files to be used as references in the assistant instructions, "
                "example: {file_reference:0}, where 0 is the index of the file in the list"
            )
            self.fileReferenceList.setStyleSheet(
                "QListWidget {"
                "  border-style: solid;"
                "  border-width: 1px;"
                "  border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;"
                "}"
            )
            self.fileReferenceAddButton = QPushButton('Add File...')
            self.fileReferenceAddButton.clicked.connect(self.add_reference_file)
            self.fileReferenceRemoveButton = QPushButton('Remove File')
            self.fileReferenceRemoveButton.clicked.connect(self.remove_reference_file)

            fileButtonLayout = QHBoxLayout()
            fileButtonLayout.addWidget(self.fileReferenceAddButton)
            fileButtonLayout.addWidget(self.fileReferenceRemoveButton)

            configLayout.addWidget(self.fileReferenceLabel)
            configLayout.addWidget(self.fileReferenceList)
            configLayout.addLayout(fileButtonLayout)

        # Model selection
        self.modelLabel = QLabel('Model:')
        self.modelComboBox = QComboBox()
        self.modelComboBox.setEditable(True)
        self.modelComboBox.setStyleSheet(
            "QLineEdit {"
            "  border-style: solid;"
            "  border-width: 1px;"
            "  border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;"
            "  padding: 1px;"
            "}"
        )
        configLayout.addWidget(self.modelLabel)
        configLayout.addWidget(self.modelComboBox)

        # Create as new assistant checkbox
        self.createAsNewCheckBox = QCheckBox("Create as New Assistant")
        self.createAsNewCheckBox.stateChanged.connect(lambda state: setattr(self, 'is_create', state == Qt.CheckState.Checked.value))
        configLayout.addWidget(self.createAsNewCheckBox)

        # Output Folder Path
        self.outputFolderPathLabel = QLabel('Output Folder Path For Files')
        self.outputFolderPathEdit = QLineEdit()
        self.outputFolderPathEdit.setText(self.default_output_folder_path)
        self.outputFolderPathButton = QPushButton('Select Folder...')
        self.outputFolderPathButton.clicked.connect(self.select_output_folder_path)

        outputFolderPathLayout = QHBoxLayout()
        outputFolderPathLayout.addWidget(self.outputFolderPathEdit)
        outputFolderPathLayout.addWidget(self.outputFolderPathButton)

        configLayout.addWidget(self.outputFolderPathLabel)
        configLayout.addLayout(outputFolderPathLayout)

        return configTab

    def create_actions_tab(self):
        actionsTab = QWidget()
        actionsLayout = QVBoxLayout(actionsTab)

        # Splitter for system, user, and OpenAPI functions
        splitter = QSplitter(Qt.Vertical)
        actionsLayout.addWidget(splitter)

        # -----------------
        # System Functions
        # -----------------
        systemFunctionsGroup = QGroupBox("System Functions")
        systemFunctionsLayout = QVBoxLayout(systemFunctionsGroup)
        self.systemFunctionsList = QListWidget()
        systemFunctionsLayout.addWidget(self.systemFunctionsList)
        splitter.addWidget(systemFunctionsGroup)

        # ----------------
        # User Functions
        # ----------------
        userFunctionsGroup = QGroupBox("User Functions")
        userFunctionsLayout = QVBoxLayout(userFunctionsGroup)
        self.userFunctionsList = QListWidget()
        userFunctionsLayout.addWidget(self.userFunctionsList)
        splitter.addWidget(userFunctionsGroup)

        # Connect signals for function selection on all three lists
        self.systemFunctionsList.itemChanged.connect(self.handle_function_selection)
        self.userFunctionsList.itemChanged.connect(self.handle_function_selection)

        # -----------------------------
        # Add items: System/User funcs
        # -----------------------------
        if self.function_config_manager:
            function_configs = self.function_config_manager.get_function_configs()
            for function_type, funcs in function_configs.items():
                list_widget = (
                    self.systemFunctionsList
                    if function_type == 'system' else self.userFunctionsList
                )
                self.create_function_section(list_widget, function_type, funcs)

        # -------------------
        # OpenAPI Functions (only for AGENT)
        # -------------------
        if self.assistant_type == AssistantType.AGENT.value:
            openapiFunctionsGroup = QGroupBox("OpenAPI Functions")
            openapiFunctionsLayout = QVBoxLayout(openapiFunctionsGroup)
            self.openapiFunctionsList = QListWidget()
            openapiFunctionsLayout.addWidget(self.openapiFunctionsList)
            splitter.addWidget(openapiFunctionsGroup)
            self.openapiFunctionsList.itemChanged.connect(self.handle_function_selection)

            if self.function_config_manager:
                openapi_funcs = self.function_config_manager.get_all_openapi_functions()
            
                for openapi_func in openapi_funcs:
                    # For display name, we look at openapi_func["openapi"]["name"]
                    display_name = (openapi_func.get('openapi', {}).get('name') or "Unnamed OpenAPI Func")
                    listItem = QListWidgetItem(display_name)
                    listItem.setFlags(listItem.flags() | Qt.ItemIsUserCheckable)  # Allow item to be checkable
                    listItem.setCheckState(Qt.Unchecked)
                    
                    # Wrap the dict in our OpenAPIFunctionConfig (so handle_function_selection works)
                    func_config = OpenAPIFunctionConfig(openapi_func)
                    listItem.setData(Qt.UserRole, func_config)

                    self.openapiFunctionsList.addItem(listItem)

        # ---------
        # Code Interpreter
        # ---------
        if self.assistant_type in [AssistantType.ASSISTANT.value, AssistantType.AGENT.value]:
            codeInterpreterGroup = QGroupBox("Code Interpreter")
            codeInterpreterLayout = QVBoxLayout(codeInterpreterGroup)
            
            # Section for managing code interpreter files
            self.setup_code_interpreter_files(codeInterpreterLayout)

            # Checkbox to enable the Code Interpreter tool
            self.codeInterpreterCheckBox = QCheckBox("Enable Code Interpreter")
            self.codeInterpreterCheckBox.stateChanged.connect(
                lambda state: setattr(self, 'code_interpreter', state == Qt.CheckState.Checked.value)
            )
            codeInterpreterLayout.addWidget(self.codeInterpreterCheckBox)

            actionsLayout.addWidget(codeInterpreterGroup)

        return actionsTab

    def create_knowledge_tab(self):
        knowledgeTab = QWidget()
        knowledgeLayout = QVBoxLayout(knowledgeTab)

        # If assistant/agent, show file search
        if self.assistant_type in [AssistantType.ASSISTANT.value, AssistantType.AGENT.value]:
            
            fileSearchGroup = QGroupBox("File Search")
            fileSearchLayout = QVBoxLayout(fileSearchGroup)

            self.setup_file_search_vector_stores(fileSearchLayout)

            self.fileSearchCheckBox = QCheckBox("Enable File Search")
            self.fileSearchCheckBox.stateChanged.connect(
                lambda state: setattr(self, 'file_search', state == Qt.CheckState.Checked.value)
            )
            fileSearchLayout.addWidget(self.fileSearchCheckBox)
            knowledgeLayout.addWidget(fileSearchGroup)

        if self.assistant_type == AssistantType.AGENT.value:
            azureGroup = QGroupBox("Azure AI Search")
            azureLayout = QVBoxLayout(azureGroup)

            # Link to QuickStart
            azureLinkLabel = QLabel(
                '<a href="https://learn.microsoft.com/en-us/azure/ai-services/agents/how-to/tools/azure-ai-search?tabs=azurecli%2Cpython&pivots=overview-azure-ai-search">'
                'Azure AI Search QuickStart for Agents</a>'
            )
            azureLinkLabel.setOpenExternalLinks(True)
            azureLayout.addWidget(azureLinkLabel)

            # Connection ID dropdown
            azureLayout.addWidget(QLabel("Connection ID"))
            self.azureSearchConnectionComboBox = QComboBox()

            azure_connections = self.get_azure_search_connections()
            if azure_connections:
                for conn in azure_connections:
                    display_name = self._extract_display_name(conn)
                    # Add an item with short display text, and store the full path in userData
                    self.azureSearchConnectionComboBox.addItem(display_name, conn)
                    # Also set the tooltip to the full connection ID/path
                    idx = self.azureSearchConnectionComboBox.count() - 1
                    self.azureSearchConnectionComboBox.setItemData(idx, conn, Qt.ToolTipRole)

            azureLayout.addWidget(self.azureSearchConnectionComboBox)

            # Index Name
            azureLayout.addWidget(QLabel("Index Name"))
            self.azureSearchIndexLineEdit = QLineEdit()
            azureLayout.addWidget(self.azureSearchIndexLineEdit)

            self.azureSearchCheckBox = QCheckBox("Enable Azure AI Search")
            azureLayout.addWidget(self.azureSearchCheckBox)

            knowledgeLayout.addWidget(azureGroup)

            bingGroup = QGroupBox("Bing Search")
            bingLayout = QVBoxLayout(bingGroup)

            # Link to QuickStart
            bingLinkLabel = QLabel(
                '<a href="https://learn.microsoft.com/en-us/azure/ai-services/agents/how-to/tools/bing-grounding?tabs=python&pivots=overview">'
                'Bing Search QuickStart for Agents</a>'
            )
            bingLinkLabel.setOpenExternalLinks(True)
            bingLayout.addWidget(bingLinkLabel)

            # Connection ID dropdown
            bingLayout.addWidget(QLabel("Connection ID"))
            self.bingSearchConnectionComboBox = QComboBox()

            bing_connections = self.get_bing_search_connections()  # returns a list of connection objects
            if bing_connections:
                for conn in bing_connections:
                    display_name = self._extract_display_name(conn)
                    self.bingSearchConnectionComboBox.addItem(display_name, conn)
                    idx = self.bingSearchConnectionComboBox.count() - 1
                    self.bingSearchConnectionComboBox.setItemData(idx, conn, Qt.ToolTipRole)

            bingLayout.addWidget(self.bingSearchConnectionComboBox)

            self.bingSearchCheckBox = QCheckBox("Enable Bing Search")
            bingLayout.addWidget(self.bingSearchCheckBox)

            knowledgeLayout.addWidget(bingGroup)

        return knowledgeTab

    def get_azure_search_connections(self):
        """Obtain a list of Azure AI Search connections from your agent/project."""
        try:
            ai_client = AIClientFactory.get_instance().get_client(AIClientType.AZURE_AI_AGENT)
            conn_list = ai_client.connections.list()
            azure_search_ids = []
            for conn in conn_list:
                # Check your actual condition for Azure AI Search
                if conn.type == "CognitiveSearch":
                    azure_search_ids.append(conn.id)
            return azure_search_ids
        except Exception as e:
            self.error_signal.error_signal.emit(str(e))

    def get_bing_search_connections(self):
        """Obtain a list of Bing connections from your agent/project."""
        try:
            ai_client = AIClientFactory.get_instance().get_client(AIClientType.AZURE_AI_AGENT)
            conn_list = ai_client.connections.list()
            bing_ids = []
            for conn in conn_list:
                # Check your actual condition for Bing 
                if conn.target and conn.target.lower().startswith("https://api.bing.microsoft.com"):
                    bing_ids.append(conn.id)
            return bing_ids
        except Exception as e:
            self.error_signal.error_signal.emit(str(e))
    
    def _extract_display_name(self, connection_obj):
        """
        Defines how to show a short, user-friendly name in the combo box.
        If your connection objects have .name, use that; otherwise parse
        something from .id. Adjust as needed.
        """
        return connection_obj.rsplit('/', 1)[-1]
    
    def create_realtime_tab(self):
        audioTab = QWidget()

        # Top-level layout for the tab
        mainLayout = QVBoxLayout(audioTab)
        mainLayout.setContentsMargins(5, 5, 5, 5)
        mainLayout.setSpacing(5)

        # Form layout for label–widget rows
        formLayout = QFormLayout()
        formLayout.setRowWrapPolicy(QFormLayout.DontWrapRows)
        formLayout.setLabelAlignment(Qt.AlignRight)
        formLayout.setFormAlignment(Qt.AlignLeft)

        # -----------------------------------------------------------------------
        # Dictionaries and data
        self.voices_dict = {
            AIClientType.OPEN_AI_REALTIME.name: ['alloy', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse'],
            AIClientType.AZURE_OPEN_AI_REALTIME.name: [
                'amuch', 'dan', 'elan', 'marilyn', 'meadow',
                'breeze', 'cove', 'ember', 'jupiter',
                'alloy', 'echo', 'shimmer'
            ]
        }

        # Voice selection
        self.voiceLabel = QLabel('Voice:')
        self.voiceComboBox = QComboBox()
        self.update_voice_combo_box()
        formLayout.addRow(self.voiceLabel, self.voiceComboBox)

        # Modality selection
        self.modalityLabel = QLabel('Modalities:')
        self.modalityComboBox = QComboBox()
        self.modalityComboBox.addItems(['text_and_audio', 'text'])
        formLayout.addRow(self.modalityLabel, self.modalityComboBox)

        # Input Audio Format
        self.inputAudioFormatLabel = QLabel('Input Audio Format:')
        self.inputAudioFormatComboBox = QComboBox()
        self.inputAudioFormatComboBox.addItems(['pcm16'])
        formLayout.addRow(self.inputAudioFormatLabel, self.inputAudioFormatComboBox)

        # Output Audio Format
        self.outputAudioFormatLabel = QLabel('Output Audio Format:')
        self.outputAudioFormatComboBox = QComboBox()
        self.outputAudioFormatComboBox.addItems(['pcm16'])
        formLayout.addRow(self.outputAudioFormatLabel, self.outputAudioFormatComboBox)

        # Input Audio Transcription Model
        self.inputAudioTranscriptionModelLabel = QLabel('Input Audio Transcription Model:')
        self.inputAudioTranscriptionModelComboBox = QComboBox()
        self.inputAudioTranscriptionModelComboBox.addItems(['whisper-1'])
        formLayout.addRow(self.inputAudioTranscriptionModelLabel,
                        self.inputAudioTranscriptionModelComboBox)

        # Keyword Detection Model File Path
        self.keywordFilePathLabel = QLabel('Keyword Model File Path:')
        self.keywordFilePathEdit = QLineEdit()
        self.keywordFilePathEdit.setText(self.default_keyword_model_file_path)
        self.keywordFilePathEdit.setToolTip(
            "The path to the keyword model file. If left empty or invalid, the keyword detection will be disabled."
        )
        self.keywordFilePathButton = QPushButton('Select File...')
        self.keywordFilePathButton.clicked.connect(self.select_keyword_file_path)

        keywordFilePathLayout = QHBoxLayout()
        keywordFilePathLayout.addWidget(self.keywordFilePathEdit)
        keywordFilePathLayout.addWidget(self.keywordFilePathButton)
        formLayout.addRow(self.keywordFilePathLabel, keywordFilePathLayout)

        # Keyword Rearm silence timeout in seconds
        self.keywordRearmSilenceTimeoutLabel = QLabel('Keyword Rearm Silence Timeout (seconds):')
        self.keywordRearmSilenceTimeoutSpinBox = QSpinBox()
        self.keywordRearmSilenceTimeoutSpinBox.setRange(0, 60)
        self.keywordRearmSilenceTimeoutSpinBox.setValue(10)
        self.keywordRearmSilenceTimeoutSpinBox.setToolTip(
            "The time in seconds to wait after user is not speaking to rearm the keyword detection."
        )
        formLayout.addRow(self.keywordRearmSilenceTimeoutLabel,
                        self.keywordRearmSilenceTimeoutSpinBox)

        # add checkbox for enabling auto reconnect
        self.autoReconnectCheckBox = QCheckBox("Enable Automatic Reconnection to Server")
        self.autoReconnectCheckBox.setChecked(False)
        self.autoReconnectCheckBox.setToolTip(
            "Automatically reconnect to the server if the websocket connection is closed by server."
        )
        formLayout.addRow("", self.autoReconnectCheckBox)

        # Turn Detection
        self.turnDetectionLabel = QLabel('Turn Detection:')
        self.turnDetectionComboBox = QComboBox()
        self.turnDetectionComboBox.addItems(['local_vad'])  # 'server_vad' if/when needed
        self.turnDetectionComboBox.currentIndexChanged.connect(self.update_vad_settings)
        formLayout.addRow(self.turnDetectionLabel, self.turnDetectionComboBox)

        # Add the form layout to the main vertical layout
        mainLayout.addLayout(formLayout)

        # Reserve an area for VAD settings. We will add them dynamically in update_vad_settings().
        self.vadLayout = QVBoxLayout()
        self.vadLayout.setContentsMargins(0, 0, 0, 0)
        self.vadLayout.setSpacing(5)
        mainLayout.addLayout(self.vadLayout)

        # Initialize with default VAD settings
        self.currentVadSettings = None
        self.update_vad_settings()

        return audioTab

    def update_voice_combo_box(self):
        current_client_type = self.aiClientComboBox.currentText()
        voices = self.voices_dict.get(current_client_type, [])

        self.voiceComboBox.blockSignals(True)
        self.voiceComboBox.clear()
        self.voiceComboBox.addItems(voices)
        self.voiceComboBox.blockSignals(False)

    def select_keyword_file_path(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Keyword Model File", "", "Keyword Model Files (*.table)")
        if file_path:
            self.keywordFilePathEdit.setText(file_path)

    def select_vad_model_path(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select VAD Model File", "", "VAD Model Files (*.onnx)")
        if file_path:
            self.vadModelPathEdit.setText(file_path)

    def update_vad_settings(self):
        """ Updates the UI based on the selected VAD option (server or local). """

        # Clear old VAD widgets (if any) from self.vadLayout
        while self.vadLayout.count():
            item = self.vadLayout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        vad_selection = self.turnDetectionComboBox.currentText()
        if vad_selection == 'server_vad':
            self.setup_server_vad(self.vadLayout)
            self.currentVadSettings = self.serverVadSettings
        else:
            self.setup_local_vad(self.vadLayout)
            self.currentVadSettings = self.localVadSettings

    def clear_vad_layout(self, layout):
        """ Clears all widgets and sub-layouts from the specified layout. """
        # Recursively remove widgets from the layout
        for i in reversed(range(layout.count())):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                self.clear_vad_layout(item.layout())
        layout.update()

    def setup_server_vad(self, layout):
        self.serverVadSettings = QVBoxLayout()

        # Activation Threshold
        self.serverVadThresholdLabel = QLabel('Activation Threshold for VAD (0.0 to 1.0):')
        self.serverVadThresholdSlider = QSlider(Qt.Horizontal)
        self.serverVadThresholdSlider.setMinimum(0)
        self.serverVadThresholdSlider.setMaximum(100)
        self.serverVadThresholdSlider.setValue(50)
        self.serverVadThresholdValueLabel = QLabel('0.5')
        self.serverVadThresholdSlider.valueChanged.connect(
            lambda: self.serverVadThresholdValueLabel.setText(f"{self.serverVadThresholdSlider.value() / 100:.1f}"))
        self.serverVadSettings.addWidget(self.serverVadThresholdLabel)
        self.serverVadSettings.addWidget(self.serverVadThresholdSlider)
        self.serverVadSettings.addWidget(self.serverVadThresholdValueLabel)

        # Prefix Padding
        self.prefixPaddingMsLayout = QHBoxLayout()
        self.prefixPaddingMsLabel = QLabel('Prefix Padding (ms):')
        self.prefixPaddingMsSpinBox = QSpinBox()
        self.prefixPaddingMsSpinBox.setRange(0, 1000)
        self.prefixPaddingMsSpinBox.setValue(300)
        self.prefixPaddingMsLayout.addWidget(self.prefixPaddingMsLabel)
        self.prefixPaddingMsLayout.addWidget(self.prefixPaddingMsSpinBox)

        # Silence Duration
        self.silenceDurationMsLayout = QHBoxLayout()
        self.silenceDurationMsLabel = QLabel('Silence Duration (ms):')
        self.silenceDurationMsSpinBox = QSpinBox()
        self.silenceDurationMsSpinBox.setRange(0, 1000)
        self.silenceDurationMsSpinBox.setValue(500)
        self.silenceDurationMsLayout.addWidget(self.silenceDurationMsLabel)
        self.silenceDurationMsLayout.addWidget(self.silenceDurationMsSpinBox)

        self.serverVadSettings.addLayout(self.prefixPaddingMsLayout)
        self.serverVadSettings.addLayout(self.silenceDurationMsLayout)

        layout.addLayout(self.serverVadSettings)

    def setup_local_vad(self, layout):
        self.localVadSettings = QFormLayout()
        self.localVadSettings.setLabelAlignment(Qt.AlignRight)

        # Chunk Size
        self.chunkSizeLabel = QLabel('Chunk Size (samples):')
        self.chunkSizeSpinBox = QSpinBox()
        self.chunkSizeSpinBox.setRange(64, 65536)
        self.chunkSizeSpinBox.setValue(512)
        self.chunkSizeSpinBox.setToolTip(
            "Number of audio samples in each chunk that VAD processes. "
            "Recommended to keep this a power of two (e.g. 512)."
        )
        self.localVadSettings.addRow(self.chunkSizeLabel, self.chunkSizeSpinBox)

        # Window Size
        self.windowSizeLabel = QLabel('Window Size (samples):')
        self.windowSizeSpinBox = QSpinBox()
        self.windowSizeSpinBox.setRange(64, 65536)
        self.windowSizeSpinBox.setValue(512)
        self.windowSizeSpinBox.setToolTip(
            "Size of the analysis window (in samples) that VAD uses for speech detection."
        )
        self.localVadSettings.addRow(self.windowSizeLabel, self.windowSizeSpinBox)

        # Threshold
        self.thresholdLabel = QLabel('Threshold (0.0 - 1.0):')
        self.thresholdSpinBox = QDoubleSpinBox()
        self.thresholdSpinBox.setRange(0.0, 1.0)
        self.thresholdSpinBox.setSingleStep(0.05)
        self.thresholdSpinBox.setValue(0.5)
        self.thresholdSpinBox.setToolTip(
            "Detection threshold for VAD in the 0.0 to 1.0 range. "
            "Lower values make it more sensitive; higher values make it more selective."
        )
        self.localVadSettings.addRow(self.thresholdLabel, self.thresholdSpinBox)

        # Minimum Speech Duration
        self.minSpeechDurationLabel = QLabel('Minimum Speech Duration (ms):')
        self.minSpeechDurationSpinBox = QSpinBox()
        self.minSpeechDurationSpinBox.setRange(0, 10000)
        self.minSpeechDurationSpinBox.setValue(300)
        self.minSpeechDurationSpinBox.setToolTip(
            "Minimum duration (milliseconds) of continuous speech required for VAD to trigger."
        )
        self.localVadSettings.addRow(self.minSpeechDurationLabel, self.minSpeechDurationSpinBox)

        # Minimum Silence Duration
        self.minSilenceDurationLabel = QLabel('Minimum Silence Duration (ms):')
        self.minSilenceDurationSpinBox = QSpinBox()
        self.minSilenceDurationSpinBox.setRange(0, 10000)
        self.minSilenceDurationSpinBox.setValue(1000)
        self.minSilenceDurationSpinBox.setToolTip(
            "Minimum duration (milliseconds) of silence required for VAD to treat speech as ended."
        )
        self.localVadSettings.addRow(self.minSilenceDurationLabel, self.minSilenceDurationSpinBox)

        # VAD Model Path
        self.vadModelPathLabel = QLabel('VAD Model Path:')
        self.vadModelPathEdit = QLineEdit()
        self.vadModelPathEdit.setText(self.default_voice_activity_detection_model_path)
        self.vadModelPathEdit.setToolTip(
            "The path to the Silero VAD model file. If left empty or invalid, "
            "the default RMS-based VAD is used."
        )
        self.vadModelPathButton = QPushButton('Select File...')
        self.vadModelPathButton.clicked.connect(self.select_vad_model_path)

        vadFilePathLayout = QHBoxLayout()
        vadFilePathLayout.addWidget(self.vadModelPathEdit)
        vadFilePathLayout.addWidget(self.vadModelPathButton)
        self.localVadSettings.addRow(self.vadModelPathLabel, vadFilePathLayout)

        layout.addLayout(self.localVadSettings)

    def setup_code_interpreter_files(self, layout):
        codeFilesLabel = QLabel('Files:')
        self.codeFileList = QListWidget()
        self.codeFileList.setStyleSheet(
            "QListWidget {"
            "  border-style: solid;"
            "  border-width: 1px;"
            "  border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;"
            "}"
        )
        addCodeFileButton = QPushButton('Add File...')
        addCodeFileButton.clicked.connect(lambda: self.add_file(self.code_interpreter_files, self.codeFileList))
        removeCodeFileButton = QPushButton('Remove File')
        removeCodeFileButton.clicked.connect(lambda: self.remove_file(self.code_interpreter_files, self.codeFileList))

        codeFileButtonLayout = QHBoxLayout()
        codeFileButtonLayout.addWidget(addCodeFileButton)
        codeFileButtonLayout.addWidget(removeCodeFileButton)

        layout.addWidget(codeFilesLabel)
        layout.addWidget(self.codeFileList)
        layout.addLayout(codeFileButtonLayout)

    def setup_file_search_vector_stores(self, layout):
        fileSearchLabel = QLabel('Files:')
        self.fileSearchList = QListWidget()
        self.fileSearchList.setStyleSheet(
            "QListWidget {"
            "  border-style: solid;"
            "  border-width: 1px;"
            "  border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;"
            "}"
        )
        addFileSearchFileButton = QPushButton('Add File...')
        addFileSearchFileButton.clicked.connect(lambda: self.add_file(self.file_search_files, self.fileSearchList))
        removeFileSearchFileButton = QPushButton('Remove File')
        removeFileSearchFileButton.clicked.connect(lambda: self.remove_file(self.file_search_files, self.fileSearchList))

        fileSearchFileButtonLayout = QHBoxLayout()
        fileSearchFileButtonLayout.addWidget(addFileSearchFileButton)
        fileSearchFileButtonLayout.addWidget(removeFileSearchFileButton)

        layout.addWidget(fileSearchLabel)
        layout.addWidget(self.fileSearchList)
        layout.addLayout(fileSearchFileButtonLayout)

    def create_completion_tab(self):
        completionTab = QWidget()
        completionLayout = QVBoxLayout(completionTab)

        # Use Default Settings Checkbox
        self.useDefaultSettingsCheckBox = QCheckBox("Use Default Settings (Note: Reasoning models may not support custom settings)")
        self.useDefaultSettingsCheckBox.setChecked(True)
        self.useDefaultSettingsCheckBox.stateChanged.connect(self.toggleCompletionSettings)
        completionLayout.addWidget(self.useDefaultSettingsCheckBox)

        # If we have either a basic assistant, an agent, or a chat assistant
        # we add relevant completion settings. Realtime assistant is handled separately.
        if self.assistant_type == AssistantType.ASSISTANT.value or self.assistant_type == AssistantType.AGENT.value:
            self.init_assistant_completion_settings(completionLayout)
        elif self.assistant_type == AssistantType.CHAT_ASSISTANT.value:
            self.init_chat_assistant_completion_settings(completionLayout)
        elif self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
            self.init_realtime_assistant_completion_settings(completionLayout)

        self.toggleCompletionSettings()

        return completionTab

    def init_assistant_completion_settings(self, completionLayout):
        self.init_common_completion_settings(completionLayout)

        maxCompletionTokensLayout = QHBoxLayout()
        self.maxCompletionTokensLabel = QLabel('Max Completion Tokens (1-5000):')
        self.maxCompletionTokensEdit = QSpinBox()
        self.maxCompletionTokensEdit.setRange(1, 5000)
        self.maxCompletionTokensEdit.setValue(1000)
        self.maxCompletionTokensEdit.setToolTip(
            "The maximum number of tokens to generate. The model will stop once "
            "it has generated this many tokens."
        )
        maxCompletionTokensLayout.addWidget(self.maxCompletionTokensLabel)
        maxCompletionTokensLayout.addWidget(self.maxCompletionTokensEdit)
        completionLayout.addLayout(maxCompletionTokensLayout)

        maxPromptTokensLayout = QHBoxLayout()
        self.maxPromptTokensLabel = QLabel('Max Prompt Tokens (1-5000):')
        self.maxPromptTokensEdit = QSpinBox()
        self.maxPromptTokensEdit.setRange(1, 5000)
        self.maxPromptTokensEdit.setValue(1000)
        self.maxPromptTokensEdit.setToolTip(
            "The maximum number of tokens to include in the prompt. "
            "The model will use the prompt to generate the completion."
        )
        maxPromptTokensLayout.addWidget(self.maxPromptTokensLabel)
        maxPromptTokensLayout.addWidget(self.maxPromptTokensEdit)
        completionLayout.addLayout(maxPromptTokensLayout)

        truncation_strategy_layout = QVBoxLayout()
        self.truncationStrategyLabel = QLabel('Truncation Strategy:')
        truncation_strategy_layout.addWidget(self.truncationStrategyLabel)

        self.truncationTypeComboBox = QComboBox()
        self.truncationTypeComboBox.setToolTip(
            "Select the truncation strategy to use for the thread. The default is `auto`. "
            "If set to `last_messages`, the thread will be truncated to the n most recent "
            "messages in the thread."
        )
        self.truncationTypeComboBox.addItems(['auto', 'last_messages'])
        truncation_strategy_layout.addWidget(self.truncationTypeComboBox)

        self.lastMessagesSpinBox = CustomSpinBox()
        self.lastMessagesSpinBox.setRange(1, 100)
        self.lastMessagesSpinBox.setDisabled(True)
        # Hide the spin box by default
        self.lastMessagesSpinBox.setVisible(False)
        truncation_strategy_layout.addWidget(self.lastMessagesSpinBox)

        # Connect to handle toggling of lastMessagesSpinBox visibility
        self.truncationTypeComboBox.currentTextChanged.connect(self.on_truncation_type_changed)

        completionLayout.addLayout(truncation_strategy_layout)

        # Reasoning Effort (in a single horizontal layout)
        reasoningEffortLayout = QHBoxLayout()
        self.reasoningEffortLabel = QLabel("Reasoning Effort (o1 or o3-mini only):")
        self.reasoningEffortComboBox = QComboBox()
        self.reasoningEffortComboBox.addItem("")  # represents None
        self.reasoningEffortComboBox.addItems(["low", "medium", "high"])

        # Add a tooltip to the combo box (rather than the label)
        self.reasoningEffortComboBox.setToolTip(
            "When a reasoning effort (low, medium, or high) is set, only certain "
            "completion settings (max completion tokens & max prompt tokens) are used; "
            "temperature, top_p, etc. will be ignored."
        )

        reasoningEffortLayout.addWidget(self.reasoningEffortLabel)
        reasoningEffortLayout.addWidget(self.reasoningEffortComboBox)
        completionLayout.addLayout(reasoningEffortLayout)

    def on_truncation_type_changed(self, text):
        if text == 'last_messages':
            self.lastMessagesSpinBox.setVisible(True)
            self.lastMessagesSpinBox.setEnabled(True)
            self.lastMessagesSpinBox.setValue(10)
        else:
            self.lastMessagesSpinBox.setVisible(False)
            self.lastMessagesSpinBox.setDisabled(True)

    def init_chat_assistant_completion_settings(self, completionLayout):
        # Common settings
        self.init_common_completion_settings(completionLayout)

        # Frequency Penalty
        self.frequencyPenaltyLabel = QLabel('Frequency Penalty:')
        self.frequencyPenaltySlider = QSlider(Qt.Horizontal)
        self.frequencyPenaltySlider.setToolTip(
            "Positive values penalize new tokens based on their existing frequency in the text so far, "
            "decreasing the model's likelihood to repeat the same line verbatim."
        )
        self.frequencyPenaltySlider.setMinimum(-200)
        self.frequencyPenaltySlider.setMaximum(200)
        self.frequencyPenaltySlider.setValue(0)  # Default value
        self.frequencyPenaltyValueLabel = QLabel('0.0')
        self.frequencyPenaltySlider.valueChanged.connect(
            lambda: self.frequencyPenaltyValueLabel.setText(f"{self.frequencyPenaltySlider.value() / 100:.1f}")
        )
        completionLayout.addWidget(self.frequencyPenaltyLabel)
        completionLayout.addWidget(self.frequencyPenaltySlider)
        completionLayout.addWidget(self.frequencyPenaltyValueLabel)

        # Max Tokens
        maxTokensLayout = QHBoxLayout()
        self.maxTokensLabel = QLabel('Max Tokens (1-5000):')
        self.maxTokensEdit = QSpinBox()
        self.maxTokensEdit.setRange(1, 5000)
        self.maxTokensEdit.setValue(1000)
        self.maxTokensEdit.setToolTip(
            "The maximum number of tokens to generate. The model will stop once it has generated this many tokens."
        )
        maxTokensLayout.addWidget(self.maxTokensLabel)
        maxTokensLayout.addWidget(self.maxTokensEdit)
        completionLayout.addLayout(maxTokensLayout)

        # Presence Penalty
        self.presencePenaltyLabel = QLabel('Presence Penalty:')
        self.presencePenaltySlider = QSlider(Qt.Horizontal)
        self.presencePenaltySlider.setToolTip(
            "Positive values penalize new tokens based on whether they appear in the text so far, "
            "increasing the model's likelihood to talk about new topics."
        )
        self.presencePenaltySlider.setMinimum(-200)
        self.presencePenaltySlider.setMaximum(200)
        self.presencePenaltySlider.setValue(0)  # Default value
        self.presencePenaltyValueLabel = QLabel('0.0')
        self.presencePenaltySlider.valueChanged.connect(
            lambda: self.presencePenaltyValueLabel.setText(f"{self.presencePenaltySlider.value() / 100:.1f}")
        )
        completionLayout.addWidget(self.presencePenaltyLabel)
        completionLayout.addWidget(self.presencePenaltySlider)
        completionLayout.addWidget(self.presencePenaltyValueLabel)

        # Max Messages Edit
        self.init_max_messages_edit(completionLayout)

        # Reasoning Effort (single horizontal line, tooltip on combo box)
        reasoningEffortLayout = QHBoxLayout()
        self.reasoningEffortLabel = QLabel("Reasoning Effort (o1 or o3-mini only):")
        self.reasoningEffortComboBox = QComboBox()
        self.reasoningEffortComboBox.addItem("")  # Represents None
        self.reasoningEffortComboBox.addItems(["low", "medium", "high"])
        
        # Add a tooltip to the combo box
        self.reasoningEffortComboBox.setToolTip(
            "When a reasoning effort (low, medium, or high) is set, only certain completion settings "
            "(e.g. max tokens) are used; temperature, top_p, etc. will be ignored."
        )

        reasoningEffortLayout.addWidget(self.reasoningEffortLabel)
        reasoningEffortLayout.addWidget(self.reasoningEffortComboBox)
        completionLayout.addLayout(reasoningEffortLayout)

    def init_realtime_assistant_completion_settings(self, completionLayout):
        # For realtime assistant Sampling temperature for the model, limited to [0.6, 1.2]. Defaults to 0.8.
        self.init_temperature_slider(completionLayout, min_value=60, max_value=120, default_value=80)
        self.init_max_messages_edit(completionLayout)

        self.maxResponseOutputTokensLayout = QHBoxLayout()
        # if not set, it will be set to None (default) in the completion request
        self.maxResponseOutputTokensLabel = QLabel('Max Response Output Tokens (1-4096 or "inf"):')
        self.maxResponseOutputTokensEdit = QLineEdit()
        self.maxResponseOutputTokensEdit.setPlaceholderText("inf")
        self.maxResponseOutputTokensEdit.setToolTip("Maximum number of output tokens for a single assistant response, inclusive of tool calls. Provide a number between 1 and 4096 to limit output tokens, or 'inf' for the maximum available tokens.")
        self.maxResponseOutputTokensLayout.addWidget(self.maxResponseOutputTokensLabel)
        self.maxResponseOutputTokensLayout.addWidget(self.maxResponseOutputTokensEdit)
        completionLayout.addLayout(self.maxResponseOutputTokensLayout)

    def init_temperature_slider(self, completionLayout, min_value=0, max_value=200, default_value=100):
        self.temperatureLabel = QLabel('Temperature:')
        self.temperatureSlider = QSlider(Qt.Horizontal)
        self.temperatureSlider.setToolTip("Controls the randomness of the generated text. Lower values make the text more deterministic, while higher values make it more random.")
        self.temperatureSlider.setMinimum(min_value)
        self.temperatureSlider.setMaximum(max_value)
        self.temperatureSlider.setValue(default_value)
        self.temperatureValueLabel = QLabel(f"{default_value / 100:.1f}")
        self.temperatureSlider.valueChanged.connect(lambda: self.temperatureValueLabel.setText(f"{self.temperatureSlider.value() / 100:.1f}"))
        completionLayout.addWidget(self.temperatureLabel)
        completionLayout.addWidget(self.temperatureSlider)
        completionLayout.addWidget(self.temperatureValueLabel)

    def init_max_messages_edit(self, completionLayout):
        self.maxMessagesLayout = QHBoxLayout()
        self.maxMessagesLabel = QLabel('Max Number of Messages In Conversation Thread Context (1-100):')
        self.maxMessagesEdit = QSpinBox()
        self.maxMessagesEdit.setRange(1, 100)
        self.maxMessagesEdit.setValue(10)
        self.maxMessagesEdit.setToolTip("The maximum number of messages to include in the conversation thread context. If set to None, no limit will be applied.")
        self.maxMessagesLayout.addWidget(self.maxMessagesLabel)
        self.maxMessagesLayout.addWidget(self.maxMessagesEdit)
        completionLayout.addLayout(self.maxMessagesLayout)

    def init_common_completion_settings(self, completionLayout):
        self.init_temperature_slider(completionLayout)
        self.topPLabel = QLabel('Top P:')
        self.topPSlider = QSlider(Qt.Horizontal)
        self.topPSlider.setToolTip("Controls the diversity of the generated text. Lower values make the text more deterministic, while higher values make it more diverse.")
        self.topPSlider.setMinimum(0)
        self.topPSlider.setMaximum(100)
        self.topPSlider.setValue(100)
        self.topPValueLabel = QLabel('1.0')
        self.topPSlider.valueChanged.connect(lambda: self.topPValueLabel.setText(f"{self.topPSlider.value() / 100:.1f}"))
        completionLayout.addWidget(self.topPLabel)
        completionLayout.addWidget(self.topPSlider)
        completionLayout.addWidget(self.topPValueLabel)

        self.responseFormatLabel = QLabel('Response Format:')
        self.responseFormatComboBox = QComboBox()
        self.responseFormatComboBox.setToolTip("Select the format of the response from the AI model")
        self.responseFormatComboBox.addItems(["text", "json_object"])
        completionLayout.addWidget(self.responseFormatLabel)
        completionLayout.addWidget(self.responseFormatComboBox)

    def toggleCompletionSettings(self):
        # Determine if controls should be enabled based on the checkbox and assistant type
        isEnabled = not self.useDefaultSettingsCheckBox.isChecked()
        
        if self.assistant_type == AssistantType.ASSISTANT.value or self.assistant_type == AssistantType.AGENT.value:
            self.temperatureSlider.setEnabled(isEnabled)
            self.topPSlider.setEnabled(isEnabled)
            self.responseFormatComboBox.setEnabled(isEnabled)
            self.maxCompletionTokensEdit.setEnabled(isEnabled)
            self.maxPromptTokensEdit.setEnabled(isEnabled)
            self.truncationTypeComboBox.setEnabled(isEnabled)
            self.reasoningEffortComboBox.setEnabled(isEnabled)
        elif self.assistant_type == AssistantType.CHAT_ASSISTANT.value:
            self.frequencyPenaltySlider.setEnabled(isEnabled)
            self.maxTokensEdit.setEnabled(isEnabled)
            self.presencePenaltySlider.setEnabled(isEnabled)
            self.responseFormatComboBox.setEnabled(isEnabled)
            self.topPSlider.setEnabled(isEnabled)
            self.maxMessagesEdit.setEnabled(isEnabled)
            self.temperatureSlider.setEnabled(isEnabled)
            self.reasoningEffortComboBox.setEnabled(isEnabled)
        elif self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
            self.temperatureSlider.setEnabled(isEnabled)
            self.maxMessagesEdit.setEnabled(isEnabled)
            self.maxResponseOutputTokensEdit.setEnabled(isEnabled)

    def ai_client_selection_changed(self):
        self.ai_client_type = AIClientType[self.aiClientComboBox.currentText()]
        self.update_assistant_combobox()
        self.update_model_combobox()
        if self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
            self.update_voice_combo_box()

    def update_assistant_combobox(self):
        self.ai_client_type = AIClientType[self.aiClientComboBox.currentText()]
        assistant_config_manager = AssistantConfigManager.get_instance()
        assistant_names = assistant_config_manager.get_assistant_names_by_client_type(self.ai_client_type.name)

        self.assistantComboBox.clear()
        self.assistantComboBox.insertItem(0, "New Assistant")
        for assistant_name in assistant_names:
            assistant_config = assistant_config_manager.get_config(assistant_name)
            if assistant_config.assistant_type == self.assistant_type:
                self.assistantComboBox.addItem(assistant_name)
        self.set_initial_assistant_selection()

    def set_initial_assistant_selection(self):
        index = self.assistantComboBox.findText(self.assistant_name)
        if index >= 0:
            self.assistantComboBox.setCurrentIndex(index)
        else:
            self.assistantComboBox.setCurrentIndex(0)  # Set default to "New Assistant"

    def update_model_combobox(self):
        self.ai_client_type = AIClientType[self.aiClientComboBox.currentText()]
        self.modelComboBox.clear()
        try:
            ai_client = AIClientFactory.get_instance().get_client(self.ai_client_type)
            if self.ai_client_type == AIClientType.OPEN_AI:
                if ai_client:
                    models = ai_client.models.list().data
                    for model in models:
                        self.modelComboBox.addItem(model.id)
            elif self.ai_client_type == AIClientType.OPEN_AI_REALTIME:
                if ai_client:
                    models = ai_client.models.list().data
                    for model in models:
                        if "realtime" in model.id:
                            self.modelComboBox.addItem(model.id)
        except Exception as e:
            logger.error(f"Error getting models from AI client: {e}")
        finally:
            if self.ai_client_type == AIClientType.OPEN_AI or self.ai_client_type == AIClientType.OPEN_AI_REALTIME:
                self.modelComboBox.setToolTip("Select a model ID supported for assistant from the list")
            elif self.ai_client_type == AIClientType.AZURE_OPEN_AI or self.ai_client_type == AIClientType.AZURE_OPEN_AI_REALTIME:
                self.modelComboBox.setToolTip("Select a model deployment name from the Azure OpenAI resource")

    def assistant_selection_changed(self):
        selected_assistant = self.assistantComboBox.currentText()
        if selected_assistant == "New Assistant":
            self.is_create = True
            self.nameEdit.setEnabled(True)
            self.createAsNewCheckBox.setEnabled(False)
            self.outputFolderPathEdit.setText(self.default_output_folder_path)
        # if selected_assistant is not empty string, load the assistant config
        elif selected_assistant != "":
            self.reset_fields()
            self.is_create = False
            self.pre_load_assistant_config(selected_assistant)
            self.createAsNewCheckBox.setEnabled(True)
            # disable name edit
            self.nameEdit.setEnabled(False)

    def get_name(self):
        return self.nameEdit.text()

    def reset_fields(self):
        self.nameEdit.clear()
        self.instructionsEdit.clear()
        self.modelComboBox.setCurrentIndex(0)
        self.vector_store_ids = []
        self.file_search_files = {}
        self.code_interpreter_files = {}
        # Reset all checkboxes in the function sections
        for function_type, checkBoxes in self.checkBoxes.items():
            for checkBox in checkBoxes:
                checkBox.setChecked(False)
        self.functions = []
        self.file_search = False
        self.code_interpreter = False
        if self.assistant_type == AssistantType.ASSISTANT.value or self.assistant_type == AssistantType.AGENT.value:
            self.fileSearchCheckBox.setChecked(False)
            self.codeInterpreterCheckBox.setChecked(False)
        self.outputFolderPathEdit.clear()
        self.assistant_config = None
        if hasattr(self, 'fileSearchList'):            
            self.fileSearchList.clear()
        if hasattr(self, 'codeFileList'):
            self.codeFileList.clear()
        if self.assistant_type == AssistantType.AGENT.value:
            self.azureSearchCheckBox.setChecked(False)
            self.azureSearchConnectionComboBox.setCurrentIndex(0)
            self.azureSearchIndexLineEdit.clear()

            self.bingSearchCheckBox.setChecked(False)
            self.bingSearchConnectionComboBox.setCurrentIndex(0)

    def create_instructions_tab(self):
        instructionsEditorTab = QWidget()
        instructionsEditorLayout = QVBoxLayout(instructionsEditorTab)

        # QTextEdit for entering instructions
        self.newInstructionsEdit = QTextEdit()
        self.newInstructionsEdit.setText("")
        instructionsEditorLayout.addWidget(self.newInstructionsEdit)

        # 'Check Instructions' button
        checkInstructionsButton = QPushButton('Review Instructions with AI...')
        checkInstructionsButton.clicked.connect(self.check_instructions)
        instructionsEditorLayout.addWidget(checkInstructionsButton)

        return instructionsEditorTab

    def select_output_folder_path(self):
        options = QFileDialog.Options()
        folderPath = QFileDialog.getExistingDirectory(self, "Select Output Folder", "", options=options)
        if folderPath:
            self.outputFolderPathEdit.setText(folderPath)

    def check_instructions(self):
        threading.Thread(target=self._check_instructions, args=()).start()

    def _check_instructions(self):
        try:
            if not hasattr(self, 'instructions_reviewer'):
                raise Exception("Instruction reviewer is not available, check the system assistant settings")
            self.start_processing_signal.start_signal.emit(ActivityStatus.PROCESSING)
            instructions = self.newInstructionsEdit.toPlainText()
            self.reviewed_instructions = self.instructions_reviewer.process_messages(user_request=instructions, stream=False)
        except Exception as e:
            self.error_signal.error_signal.emit(str(e))
        finally:
            self.stop_processing_signal.stop_signal.emit(ActivityStatus.PROCESSING)

    def start_processing(self, status):
        self.status_bar.start_animation(status)

    def stop_processing(self, status):
        self.status_bar.stop_animation(status)
        try:
            # Open new dialog with the checked instructions
            if hasattr(self, 'reviewed_instructions') and self.reviewed_instructions:
                contentDialog = ContentDisplayDialog(self.reviewed_instructions, "AI Reviewed Instructions", self)
                contentDialog.show()
                self.reviewed_instructions = None
        except Exception as e:
            logger.error(f"Error displaying reviewed instructions: {e}")

    def pre_load_assistant_config(self, name):
        self.assistant_config = AssistantConfigManager.get_instance().get_config(name)
        if not self.assistant_config:
            return

        self.nameEdit.setText(self.assistant_config.name)
        self.assistant_id = self.assistant_config.assistant_id
        self.instructionsEdit.setText(self.assistant_config.instructions)

        index = self.modelComboBox.findText(self.assistant_config.model)
        if index >= 0:
            self.modelComboBox.setCurrentIndex(index)
        else:
            self.modelComboBox.addItem(self.assistant_config.model)
            self.modelComboBox.setCurrentIndex(self.modelComboBox.count() - 1)

        # Pre-select functions
        self.pre_select_functions()

        # Pre-fill reference files
        self.fileReferenceList.clear()
        for file_path in self.assistant_config.file_references:
            self.fileReferenceList.addItem(file_path)

        # Tool resources / code interpreter / file search
        if self.assistant_config.tool_resources:
            code_interpreter_files = self.assistant_config.tool_resources.code_interpreter_files
            if code_interpreter_files:
                for file_path, file_id in code_interpreter_files.items():
                    self.code_interpreter_files[file_path] = file_id
                    self.codeFileList.addItem(f"{file_path}")
            self.codeInterpreterCheckBox.setChecked(self.assistant_config.code_interpreter)

            for vector_store in self.assistant_config.tool_resources.file_search_vector_stores:
                self.vector_store_ids.append(vector_store.id)
                for file_path, file_id in vector_store.files.items():
                    item = QListWidgetItem(file_path)
                    item.setData(Qt.UserRole, file_id)
                    self.file_search_files[file_path] = file_id
                    self.fileSearchList.addItem(item)
            self.fileSearchCheckBox.setChecked(bool(self.assistant_config.file_search))

        # Load completion settings
        self.load_completion_settings(self.assistant_config.text_completion_config)

        # Load real-time settings
        if self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
            self.load_realtime_settings(self.assistant_config.realtime_config)

        # Output folder path
        output_folder_path = self.assistant_config.output_folder_path
        if output_folder_path:
            self.outputFolderPathEdit.setText(output_folder_path)

        # Load search settings for agent, azure search and bing search
        if self.assistant_type == AssistantType.AGENT.value:
            azure_search = getattr(self.assistant_config, "azure_ai_search", None)
            if azure_search:
                self.azureSearchCheckBox.setChecked(azure_search.get("enabled", False))
                saved_azure_conn_id = azure_search.get("connection_id", "")
                for i in range(self.azureSearchConnectionComboBox.count()):
                    data = self.azureSearchConnectionComboBox.itemData(i, Qt.UserRole)
                    if data == saved_azure_conn_id:
                        self.azureSearchConnectionComboBox.setCurrentIndex(i)
                        break
                self.azureSearchIndexLineEdit.setText(azure_search.get("index_name", ""))

            bing_search = getattr(self.assistant_config, "bing_search", None)
            if bing_search:
                self.bingSearchCheckBox.setChecked(bing_search.get("enabled", False))
                saved_bing_conn_id = bing_search.get("connection_id", "")
                for i in range(self.bingSearchConnectionComboBox.count()):
                    data = self.bingSearchConnectionComboBox.itemData(i, Qt.UserRole)
                    if data == saved_bing_conn_id:
                        self.bingSearchConnectionComboBox.setCurrentIndex(i)
                        break

    def load_completion_settings(self, text_completion_config):
        if text_completion_config:
            self.useDefaultSettingsCheckBox.setChecked(False)
            completion_settings = text_completion_config.to_dict()

            # Load settings into UI elements based on assistant type
            if self.assistant_type == AssistantType.ASSISTANT.value or self.assistant_type == AssistantType.AGENT.value:
                self.temperatureSlider.setValue(completion_settings.get('temperature', 1.0) * 100)
                self.topPSlider.setValue(completion_settings.get('top_p', 1.0) * 100)
                self.responseFormatComboBox.setCurrentText(completion_settings.get('response_format', 'text'))
                self.maxCompletionTokensEdit.setValue(completion_settings.get('max_completion_tokens', 1000))
                self.maxPromptTokensEdit.setValue(completion_settings.get('max_prompt_tokens', 1000))
                truncation_strategy = completion_settings.get('truncation_strategy', {'type': 'auto'})
                truncation_type = truncation_strategy.get('type', 'auto')
                self.truncationTypeComboBox.setCurrentText(truncation_type)
                if truncation_type == 'last_messages':
                    last_messages = truncation_strategy.get('last_messages')
                    if last_messages is not None:
                        self.lastMessagesSpinBox.setValue(last_messages)

                # Reasoning Effort
                reasoning_effort = completion_settings.get('reasoning_effort')
                if reasoning_effort:
                    self.reasoningEffortComboBox.setCurrentText(reasoning_effort)
                else:
                    self.reasoningEffortComboBox.setCurrentIndex(0)

            elif self.assistant_type == AssistantType.CHAT_ASSISTANT.value:
                self.frequencyPenaltySlider.setValue(completion_settings.get('frequency_penalty', 0) * 100)
                self.maxTokensEdit.setValue(completion_settings.get('max_tokens', 1000))
                self.presencePenaltySlider.setValue(completion_settings.get('presence_penalty', 0) * 100)
                self.responseFormatComboBox.setCurrentText(completion_settings.get('response_format', 'text'))
                self.temperatureSlider.setValue(completion_settings.get('temperature', 1.0) * 100)
                self.topPSlider.setValue(completion_settings.get('top_p', 1.0) * 100)
                self.maxMessagesEdit.setValue(completion_settings.get('max_text_messages', 50))

                # Reasoning Effort
                reasoning_effort = completion_settings.get('reasoning_effort')
                if reasoning_effort:
                    self.reasoningEffortComboBox.setCurrentText(reasoning_effort)
                else:
                    self.reasoningEffortComboBox.setCurrentIndex(0)

            elif self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
                self.temperatureSlider.setValue(completion_settings.get('temperature', 1.0) * 100)
                self.maxMessagesEdit.setValue(completion_settings.get('max_text_messages', 50))
                self.maxResponseOutputTokensEdit.setText(str(completion_settings.get('max_output_tokens', 'inf')))
        else:
            # Apply default settings if no config is found
            self.useDefaultSettingsCheckBox.setChecked(True)
            if self.assistant_type == AssistantType.ASSISTANT.value or self.assistant_type == AssistantType.AGENT.value:
                self.temperatureSlider.setValue(100)
                self.topPSlider.setValue(100)
                self.responseFormatComboBox.setCurrentText("text")
                self.maxCompletionTokensEdit.setValue(1000)
                self.maxPromptTokensEdit.setValue(1000)
                self.truncationTypeComboBox.setCurrentText("auto")
                self.reasoningEffortComboBox.setCurrentIndex(0)

            elif self.assistant_type == AssistantType.CHAT_ASSISTANT.value:
                self.frequencyPenaltySlider.setValue(0)
                self.maxTokensEdit.setValue(1000)
                self.presencePenaltySlider.setValue(0)
                self.responseFormatComboBox.setCurrentText("text")
                self.temperatureSlider.setValue(100)
                self.topPSlider.setValue(100)
                self.maxMessagesEdit.setValue(10)
                self.reasoningEffortComboBox.setCurrentIndex(0)

            elif self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
                self.temperatureSlider.setValue(100)
                self.maxMessagesEdit.setValue(10)
                self.maxResponseOutputTokensEdit.setText("inf")

    def load_realtime_settings(self, realtime_config):
        if realtime_config:
            self.voiceComboBox.setCurrentText(realtime_config.voice)
            if realtime_config.modalities == ["text"]:
                self.modalityComboBox.setCurrentText("text")
            elif realtime_config.modalities == ["text", "audio"]:
                self.modalityComboBox.setCurrentText("text_and_audio")
            self.inputAudioFormatComboBox.setCurrentText(realtime_config.input_audio_format)
            self.outputAudioFormatComboBox.setCurrentText(realtime_config.output_audio_format)
            self.inputAudioTranscriptionModelComboBox.setCurrentText(realtime_config.input_audio_transcription_model)
            self.keywordFilePathEdit.setText(realtime_config.keyword_detection_model)
            self.vadModelPathEdit.setText(realtime_config.voice_activity_detection_model)
            self.keywordRearmSilenceTimeoutSpinBox.setValue(realtime_config.keyword_rearm_silence_timeout)
            self.autoReconnectCheckBox.setChecked(realtime_config.auto_reconnect)

            turn_detection_type = realtime_config.turn_detection.get('type', 'local_vad')
            self.turnDetectionComboBox.setCurrentText(turn_detection_type)
            if turn_detection_type == "server_vad":
                self.serverVadThresholdSlider.setValue(
                    realtime_config.turn_detection.get('server_vad_threshold', 0.5) * 100
                )
                self.prefixPaddingMsSpinBox.setValue(
                    realtime_config.turn_detection.get('prefix_padding_ms', 300)
                )
                self.silenceDurationMsSpinBox.setValue(
                    realtime_config.turn_detection.get('silence_duration_ms', 500)
                )
            elif turn_detection_type == "local_vad":
                self.chunkSizeSpinBox.setValue(
                    realtime_config.turn_detection.get('chunk_size', 512)
                )
                self.windowSizeSpinBox.setValue(
                    realtime_config.turn_detection.get('window_size_samples', 512)
                )
                self.thresholdSpinBox.setValue(
                    realtime_config.turn_detection.get('threshold', 0.5)
                )
                self.minSpeechDurationSpinBox.setValue(
                    int(realtime_config.turn_detection.get('min_speech_duration', 0.3) * 1000)
                )
                self.minSilenceDurationSpinBox.setValue(
                    int(realtime_config.turn_detection.get('min_silence_duration', 1.0) * 1000)
                )

    def pre_select_functions(self):
        # Iterate over all selected functions from your assistant_config
        for func in self.assistant_config.functions:
            func_type = func.get('type', 'function')  # default to 'function' if missing

            if func_type == 'openapi' and hasattr(self, 'openapiFunctionsList'):
                func_name = func.get('openapi', {}).get('name')
                if not func_name:
                    continue
                if func not in self.functions:
                    self.functions.append(func)
                for i in range(self.openapiFunctionsList.count()):
                    listItem = self.openapiFunctionsList.item(i)
                    if listItem.text() == func_name:
                        listItem.setCheckState(Qt.Checked)
                        break
            else:
                if func_type == 'azure_function':
                    func_name = func.get('azure_function', {}).get('function', {}).get('name')
                else:
                    func_name = func.get('function', {}).get('name')

                if not func_name:
                    continue
                if func not in self.functions:
                    self.functions.append(func)

                function_configs = self.function_config_manager.get_function_configs()
                for category_type, funcs_in_category in function_configs.items():
                    for func_config in funcs_in_category:
                        if func_config.name == func_name:
                            if func_config.get_full_spec() not in self.functions:
                                self.functions.append(func_config.get_full_spec())
                            if category_type == 'system':
                                list_widget = self.systemFunctionsList
                            else:
                                list_widget = self.userFunctionsList

                            for i in range(list_widget.count()):
                                listItem = list_widget.item(i)
                                if listItem.text() == func_name or \
                                listItem.text() == f"{func_name} (Azure Function)":
                                    listItem.setCheckState(Qt.Checked)
                                    break

    def create_function_section(self, list_widget, function_type, funcs):
        for func_config in funcs:
            display_name = func_config.name
            if function_type == "azure":
                display_name += " (Azure Function)"

            listItem = QListWidgetItem(display_name)
            listItem.setFlags(listItem.flags() | Qt.ItemIsUserCheckable)  # checkable
            listItem.setCheckState(Qt.Unchecked)
            listItem.setData(Qt.UserRole, func_config)
            list_widget.addItem(listItem)

    def handle_function_selection(self, item):
        self.functions = []
        openAPIFunctionsList = QListWidget()
        if hasattr(self, 'openapiFunctionsList'):
            openAPIFunctionsList = self.openapiFunctionsList

        for listWidget in [self.systemFunctionsList, self.userFunctionsList, openAPIFunctionsList]:
            for i in range(listWidget.count()):
                listItem = listWidget.item(i)
                if listItem.checkState() == Qt.Checked:
                    functionConfig = listItem.data(Qt.UserRole)
                    if functionConfig.get_full_spec() not in self.functions:
                        self.functions.append(functionConfig.get_full_spec())

    def add_reference_file(self):
        self.fileReferenceList.addItem(QFileDialog.getOpenFileName(None, "Select File", "", "All Files (*)")[0])

    def remove_reference_file(self):
        selected_items = self.fileReferenceList.selectedItems()
        for item in selected_items:
            self.fileReferenceList.takeItem(self.fileReferenceList.row(item))

    def add_file(self, file_dict, list_widget):
        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getOpenFileName(None, "Select File", "", "All Files (*)", options=options)
        if filePath:
            if filePath in file_dict:
                QMessageBox.warning(None, "File Already Added", f"The file '{filePath}' is already in the list.")
            else:
                file_dict[filePath] = None
                list_widget.addItem(filePath)

    def remove_file(self, file_dict, list_widget):
        selected_items = list_widget.selectedItems()
        if not selected_items:
            return
        for item in selected_items:
            del file_dict[item.text()]
            list_widget.takeItem(list_widget.row(item))

    def get_realtime_settings(self):
        turn_detection = {}
        if self.turnDetectionComboBox.currentText() == "server_vad":
            turn_detection = {
                'type': 'server_vad',
                'threshold': self.serverVadThresholdSlider.value() / 100,
                'prefix_padding_ms': self.prefixPaddingMsSpinBox.value(),
                'silence_duration_ms': self.silenceDurationMsSpinBox.value()
            }
        elif self.turnDetectionComboBox.currentText() == "local_vad":
            turn_detection = {
                'type': 'local_vad',
                'chunk_size': self.chunkSizeSpinBox.value(),
                'window_size_samples': self.windowSizeSpinBox.value(),
                'threshold': self.thresholdSpinBox.value(),
                'min_speech_duration': self.minSpeechDurationSpinBox.value() / 1000.0,
                'min_silence_duration': self.minSilenceDurationSpinBox.value() / 1000.0
            }

        realtime_config = {
            'voice': self.voiceComboBox.currentText(),
            'modalities': self.modalityComboBox.currentText(),
            'input_audio_format': self.inputAudioFormatComboBox.currentText(),
            'output_audio_format': self.outputAudioFormatComboBox.currentText(),
            'input_audio_transcription_model': self.inputAudioTranscriptionModelComboBox.currentText(),
            'keyword_detection_model': self.keywordFilePathEdit.text(),
            'voice_activity_detection_model': self.vadModelPathEdit.text(),
            'keyword_rearm_silence_timeout': self.keywordRearmSilenceTimeoutSpinBox.value(),
            'auto_reconnect': self.autoReconnectCheckBox.isChecked(),
            'turn_detection': turn_detection
        }
        return realtime_config

    def save_configuration(self):
        current_tab_text = self.tabWidget.tabText(self.tabWidget.currentIndex())
        if current_tab_text == "Instructions Editor":
            self.instructionsEdit.setPlainText(self.newInstructionsEdit.toPlainText())

        self.assistant_name = self.get_name()

        # Prepare a tool_resources placeholder for assistant/agent
        tool_resources = None

        # Build completion_settings if not default
        completion_settings = None

        if self.assistant_type == AssistantType.CHAT_ASSISTANT.value:
            if not self.useDefaultSettingsCheckBox.isChecked():
                completion_settings = {
                    'frequency_penalty': self.frequencyPenaltySlider.value() / 100,
                    'max_tokens': self.maxTokensEdit.value(),
                    'presence_penalty': self.presencePenaltySlider.value() / 100,
                    'response_format': self.responseFormatComboBox.currentText(),
                    'temperature': self.temperatureSlider.value() / 100,
                    'top_p': self.topPSlider.value() / 100,
                    'max_text_messages': self.maxMessagesEdit.value(),
                    'reasoning_effort': self.reasoningEffortComboBox.currentText() or None
                }

        elif self.assistant_type == AssistantType.ASSISTANT.value or self.assistant_type == AssistantType.AGENT.value:
            if not self.useDefaultSettingsCheckBox.isChecked():
                truncation_strategy = {
                    'type': self.truncationTypeComboBox.currentText(),
                    'last_messages': self.lastMessagesSpinBox.value() if self.truncationTypeComboBox.currentText() == 'last_messages' else None
                }
                completion_settings = {
                    'temperature': self.temperatureSlider.value() / 100,
                    'max_completion_tokens': self.maxCompletionTokensEdit.value(),
                    'max_prompt_tokens': self.maxPromptTokensEdit.value(),
                    'top_p': self.topPSlider.value() / 100,
                    'response_format': self.responseFormatComboBox.currentText(),
                    'truncation_strategy': truncation_strategy,
                    'reasoning_effort': self.reasoningEffortComboBox.currentText() or None
                }

            code_interpreter_files = {}
            for i in range(self.codeFileList.count()):
                item = self.codeFileList.item(i)
                file_path = item.text()
                file_id = self.code_interpreter_files.get(file_path)
                code_interpreter_files[file_path] = file_id

            vector_stores = []
            vector_store_files = {}
            for i in range(self.fileSearchList.count()):
                item = self.fileSearchList.item(i)
                file_path = item.text()
                file_id = item.data(Qt.UserRole)
                vector_store_files[file_path] = file_id

            id = self.vector_store_ids[0] if self.vector_store_ids else None
            if id or vector_store_files:
                vector_store = VectorStoreConfig(name=f"Assistant {self.assistant_name} vector store",
                                                 id=id,
                                                 files=vector_store_files)
                vector_stores.append(vector_store)

            tool_resources = ToolResourcesConfig(
                code_interpreter_files=code_interpreter_files,
                file_search_vector_stores=vector_stores
            )

        elif self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
            if not self.useDefaultSettingsCheckBox.isChecked():
                max_output_tokens_input = self.maxResponseOutputTokensEdit.text().strip()
                if max_output_tokens_input.lower() != "inf":
                    try:
                        max_output_tokens = int(max_output_tokens_input)
                        if not (1 <= max_output_tokens <= 4096):
                            raise ValueError
                    except ValueError:
                        QMessageBox.information(
                            self,
                            "Invalid Input",
                            "max_output_tokens must be an integer between 1 and 4096 or 'inf'."
                        )
                        return
                else:
                    max_output_tokens = "inf"

                completion_settings = {
                    'temperature': self.temperatureSlider.value() / 100,
                    'max_text_messages': self.maxMessagesEdit.value(),
                    'max_output_tokens': max_output_tokens
                }

        config = {
            'name': self.assistant_name,
            'instructions': self.instructionsEdit.toPlainText(),
            'model': self.modelComboBox.currentText(),
            'assistant_id': self.assistant_id if not self.is_create else '',
            'file_references': [self.fileReferenceList.item(i).text() for i in range(self.fileReferenceList.count())] if self.assistant_type != AssistantType.REALTIME_ASSISTANT.value else [],
            'tool_resources': tool_resources.to_dict() if tool_resources else None,
            'functions': self.functions,
            'file_search': self.fileSearchCheckBox.isChecked() if (self.assistant_type == AssistantType.ASSISTANT.value or self.assistant_type == AssistantType.AGENT.value) else False,
            'code_interpreter': self.codeInterpreterCheckBox.isChecked() if (self.assistant_type == AssistantType.ASSISTANT.value or self.assistant_type == AssistantType.AGENT.value) else False,
            'output_folder_path': self.outputFolderPathEdit.text(),
            'ai_client_type': self.aiClientComboBox.currentText(),
            'assistant_type': self.assistant_type,
            'completion_settings': completion_settings,
            'realtime_settings': self.get_realtime_settings() if self.assistant_type == AssistantType.REALTIME_ASSISTANT.value else None
        }

        if self.assistant_type == AssistantType.AGENT.value:
            azure_conn_id = self.azureSearchConnectionComboBox.currentData(Qt.UserRole)
            bing_conn_id = self.bingSearchConnectionComboBox.currentData(Qt.UserRole)
            azure_ai_search = {
                'enabled': self.azureSearchCheckBox.isChecked(),
                'connection_id': azure_conn_id,
                'index_name': self.azureSearchIndexLineEdit.text()
            }
            bing_search = {
                'enabled': self.bingSearchCheckBox.isChecked(),
                'connection_id': bing_conn_id
            }
            config['azure_ai_search'] = azure_ai_search
            config['bing_search'] = bing_search

        if not config['name'] or not config['instructions'] or not config['model']:
            QMessageBox.information(self, "Missing Fields", "Name, Instructions, and Model are required fields.")
            return

        assistant_config_json = json.dumps(config, indent=4)
        self.assistantConfigSubmitted.emit(assistant_config_json, self.aiClientComboBox.currentText(), self.assistant_type, self.assistant_name)


class ExportAssistantDialog(QDialog):
    def __init__(self, main_window):
        """
        :param main_window: The main window instance (used to access active_ai_client_type).
        """
        super().__init__()
        self.main_window = main_window
        self.assistant_config_manager = AssistantConfigManager.get_instance()

        self.setWindowTitle("Export Assistant")
        self.setLayout(QVBoxLayout())

        self.assistant_label = QLabel("Select Assistant:")
        self.layout().addWidget(self.assistant_label)

        self.assistant_combo = QComboBox()
        self.assistant_combo.addItems(self.get_assistant_names())
        self.layout().addWidget(self.assistant_combo)

        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self.export_assistant)
        self.layout().addWidget(self.export_button)
        # Set the initial size of the dialog to make it wider
        self.resize(400, 100)

    def get_assistant_names(self):
        """
        Return assistant names matching the main_window's active AI client type.
        """
        client_type_name = self.main_window.active_ai_client_type.name
        return self.assistant_config_manager.get_assistant_names_by_client_type(
            client_type_name, include_system_assistants=False
        )

    def export_assistant(self):
        """
        Copy configuration and generate main.py using the appropriate template
        for real-time (text/audio) or standard exports, depending on the 
        assistant type and AI client type.
        """
        assistant_name = self.assistant_combo.currentText()
        assistant_config = self.assistant_config_manager.get_config(assistant_name)

        export_path = os.path.join("export", assistant_name)
        config_path = os.path.join(export_path, "config")
        functions_path = os.path.join(export_path, "functions")

        # Ensure directories exist
        os.makedirs(config_path, exist_ok=True)
        os.makedirs(functions_path, exist_ok=True)

        # Copy required config files
        try:
            shutil.copyfile(
                f"config/{assistant_name}_assistant_config.yaml",
                os.path.join(config_path, f"{assistant_name}_assistant_config.yaml")
            )
            shutil.copyfile(
                "config/function_error_specs.json",
                os.path.join(config_path, "function_error_specs.json")
            )
        except FileNotFoundError as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to copy configuration files: {e}")
            return

        # Copy user_functions.py if present
        user_functions_src = os.path.join("functions", "user_functions.py")
        if os.path.exists(user_functions_src):
            shutil.copyfile(user_functions_src, os.path.join(functions_path, "user_functions.py"))

        # Determine template path
        template_path = os.path.join("templates", "async_stream_template.py")

        # If the assistant/config is real-time and the client type is real-time, pick text/audio
        if (
            assistant_config.assistant_type == AssistantType.REALTIME_ASSISTANT.value
            and self.main_window.active_ai_client_type in [AIClientType.OPEN_AI_REALTIME, AIClientType.AZURE_OPEN_AI_REALTIME]
        ):
            selected_mode = assistant_config.realtime_config.modalities
            if selected_mode == ["text", "audio"]:
                template_path = os.path.join("templates", "realtime_audio_template.py")
            else:
                template_path = os.path.join("templates", "realtime_text_template.py")

        # Generate main.py from the determined template
        try:
            with open(template_path, "r") as template_file:
                template_content = template_file.read()

            main_content = template_content.replace("ASSISTANT_NAME", assistant_name)

            # Example: if chat assistant, rename references
            if assistant_config.assistant_type == AssistantType.CHAT_ASSISTANT.value:
                main_content = main_content.replace("assistant_client", "chat_assistant_client")
                main_content = main_content.replace("AssistantClient", "ChatAssistantClient")

            with open(os.path.join(export_path, "main.py"), "w") as main_file:
                main_file.write(main_content)

        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to create main.py: {e}")
            return

        QMessageBox.information(
            self,
            "Export Successful",
            f"Assistant '{assistant_name}' exported successfully to '{export_path}'."
        )
        self.accept()


class ContentDisplayDialog(QDialog):
    def __init__(self, content, title="Content Display", parent=None):
        super().__init__(parent)

        self.setWindowTitle(title)
        self.resize(400, 300)  # Set the size of the dialog

        layout = QVBoxLayout(self)

        self.contentEdit = QTextEdit()
        self.contentEdit.setReadOnly(True)  # Make it read-only
        self.contentEdit.setText(content)

        layout.addWidget(self.contentEdit)