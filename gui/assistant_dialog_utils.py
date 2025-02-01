# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

# This software uses the PySide6 library, which is licensed under the GNU Lesser General Public License (LGPL).
# For more details on PySide6's license, see <https://www.qt.io/licensing>

from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import QRunnable, QThreadPool, QObject, Signal
from azure.ai.assistant.management.assistant_config import AssistantType
from azure.ai.assistant.management.assistant_config_manager import AssistantConfigManager
from azure.ai.assistant.management.ai_client_factory import AIClientType
from azure.ai.assistant.management.assistant_client import AssistantClient
from azure.ai.assistant.management.chat_assistant_client import ChatAssistantClient
from azure.ai.assistant.management.agent_client import AgentClient
from azure.ai.assistant.management.realtime_assistant_client import RealtimeAssistantClient
from azure.ai.assistant.audio.realtime_audio import RealtimeAudio
from gui.assistant_client_manager import AssistantClientManager


class ProcessAssistantWorkerSignals(QObject):
    """
    Defines the signals available from a running worker.
    """
    finished = Signal(object)
    error = Signal(str)


class ProcessAssistantWorker(QRunnable):
    """
    Worker thread for processing the assistant config submission.
    """
    def __init__(self, 
                 assistant_config_json : dict,
                 ai_client_type: str,
                 assistant_type : str,
                 assistant_name : str,
                 main_window,
                 assistant_client_manager : AssistantClientManager,
                 timeout : int
    ):
        super().__init__()
        self.assistant_config_json = assistant_config_json
        self.ai_client_type = ai_client_type
        self.assistant_type = assistant_type
        self.assistant_name = assistant_name
        self.main_window = main_window
        self.assistant_client_manager = assistant_client_manager
        self.timeout = timeout
        self.signals = ProcessAssistantWorkerSignals()

    def run(self):
        """
        Run the heavy processing in a separate thread.
        """
        try:
            realtime_audio = None
            if self.assistant_type == AssistantType.CHAT_ASSISTANT.value:
                assistant_client = ChatAssistantClient.from_json(
                    self.assistant_config_json, self.main_window, self.timeout
                )
            elif self.assistant_type == AssistantType.ASSISTANT.value:
                assistant_client = AssistantClient.from_json(
                    self.assistant_config_json, self.main_window, self.timeout
                )
            elif self.assistant_type == AssistantType.AGENT.value:
                assistant_client = AgentClient.from_json(
                    self.assistant_config_json, self.main_window, self.timeout
                )
            elif self.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
                assistant_client : RealtimeAssistantClient = self.assistant_client_manager.get_client(name=self.assistant_name)
                realtime_audio = self.assistant_client_manager.get_audio(name=self.assistant_name)
                if assistant_client and assistant_client.assistant_config.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
                    assistant_client.update(self.assistant_config_json, self.timeout)
                    realtime_audio.update(assistant_client.assistant_config)
                else:
                    assistant_client = RealtimeAssistantClient.from_json(
                        self.assistant_config_json, self.main_window, self.timeout
                    )
                    realtime_audio = RealtimeAudio(assistant_client)
            else:
                raise ValueError("Unsupported assistant type provided.")

            # Pass the client (and realtime_audio if any) as result
            self.signals.finished.emit((assistant_client, realtime_audio))
        except Exception as e:
            self.signals.error.emit(str(e))


def open_assistant_config_dialog(
        parent, 
        assistant_type=None, 
        assistant_name=None,
        function_config_manager=None, 
        callback=None
    ):
    """
    Create and show an AssistantConfigDialog.
    
    :param parent: The parent widget.
    :param assistant_type: Optional; type of assistant (e.g., AssistantType.ASSISTANT.value).
    :param assistant_name: Optional; the name of the assistant being edited.
    :param function_config_manager: A reference to the function config manager.
    :param callback: Function to connect to the assistantConfigSubmitted signal.
    :return: The opened dialog.
    """
    from gui.assistant_dialogs import AssistantConfigDialog  # Import here to avoid circular dependencies

    dialog = AssistantConfigDialog(
        parent=parent,
        assistant_type=assistant_type,
        assistant_name=assistant_name,
        function_config_manager=function_config_manager
    )
    if callback:
        dialog.assistantConfigSubmitted.connect(callback)

    dialog.show()
    return dialog


def process_assistant_config_submission(
    assistant_config_json,
    ai_client_type,
    assistant_type,
    assistant_name,
    main_window,
    assistant_client_manager,
    timeout,
    dialog
):
    """
    Offload the JSON parsing and client creation to a worker thread, and update
    the UI once ready.

    :param assistant_config_json: The JSON config submitted via the dialog.
    :param ai_client_type: The type of AI client as a string.
    :param assistant_type: The type of assistant (e.g., AssistantType.CHAT_ASSISTANT.value).
    :param assistant_name: The unique assistant name.
    :param main_window: The main window reference.
    :param assistant_client_manager: The manager responsible for keeping track of assistant clients.
    :param timeout: Timeout used for establishing connections.
    :param dialog: The dialog instance (to update UI, e.g., combobox).
    """

    def handle_finished(result):
        assistant_client, realtime_audio = result
        assistant_client_manager.register_client(
            name=assistant_name,
            assistant_client=assistant_client,
            realtime_audio=realtime_audio
        )
        client_type = AIClientType[ai_client_type]
        # UI update runs on the main thread.
        main_window.conversation_sidebar.load_assistant_list(client_type)
        dialog.update_assistant_combobox()

    def handle_error(error_msg):
        # Show error using a message box on the main thread.
        QMessageBox.warning(main_window, "Error",
                            f"An error occurred while creating/updating the assistant: {error_msg}")

    # Create the worker with parameters needed for heavy processing
    worker = ProcessAssistantWorker(
        assistant_config_json=assistant_config_json,
        ai_client_type=ai_client_type,
        assistant_type=assistant_type,
        assistant_name=assistant_name,
        main_window=main_window,
        assistant_client_manager=assistant_client_manager,
        timeout=timeout
    )
    worker.signals.finished.connect(handle_finished)
    worker.signals.error.connect(handle_error)

    # Execute the worker in a separate thread using QThreadPool
    QThreadPool.globalInstance().start(worker)


class LoadAssistantWorkerSignals(QObject):
    """Signals for LoadAssistantWorker."""
    finished = Signal(list)  # assistant_names list
    error = Signal(str, list)  # error message, assistant_names list


class LoadAssistantWorker(QRunnable):
    """
    Worker thread for loading assistants.
    
    This worker gets the list of assistant names by client type, creates/updates the assistant client objects,
    registers them, and then emits a signal with the resulting assistant name list.
    """
    def __init__(self, 
                 ai_client_type : str,
                 assistant_config_manager : AssistantConfigManager,
                 assistant_client_manager : AssistantClientManager,
                 main_window
        ):
        super().__init__()
        self.ai_client_type = ai_client_type
        self.assistant_config_manager = assistant_config_manager
        self.assistant_client_manager = assistant_client_manager
        self.main_window = main_window
        self.signals = LoadAssistantWorkerSignals()

    def run(self):
        try:
            # Get the list of assistant names for the given client type
            assistant_names = self.assistant_config_manager.get_assistant_names_by_client_type(self.ai_client_type.name)
            for name in assistant_names:
                if not self.assistant_client_manager.get_client(name):
                    assistant_config = self.assistant_config_manager.get_config(name)
                    assistant_config.config_folder = "config"
                    realtime_audio = None

                    if assistant_config.assistant_type == AssistantType.ASSISTANT.value:
                        assistant_client = AssistantClient.from_json(
                            assistant_config.to_json(),
                            self.main_window,
                            self.main_window.connection_timeout
                        )
                    elif assistant_config.assistant_type == AssistantType.AGENT.value:
                        assistant_client = AgentClient.from_json(
                            assistant_config.to_json(),
                            self.main_window,
                            self.main_window.connection_timeout
                        )
                    elif assistant_config.assistant_type == AssistantType.CHAT_ASSISTANT.value:
                        assistant_client = ChatAssistantClient.from_json(
                            assistant_config.to_json(),
                            self.main_window,
                            self.main_window.connection_timeout
                        )
                    elif assistant_config.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
                        assistant_client = RealtimeAssistantClient.from_json(
                            assistant_config.to_json(),
                            self.main_window,
                            self.main_window.connection_timeout
                        )
                        realtime_audio = RealtimeAudio(assistant_client)
                    else:
                        raise ValueError("Unsupported assistant type provided.")

                    # Register the assistant client with (optional) realtime audio
                    self.assistant_client_manager.register_client(
                        name=name,
                        assistant_client=assistant_client,
                        realtime_audio=realtime_audio
                    )
            # Emit the finished signal with the list of assistant names.
            self.signals.finished.emit(assistant_names)
        except Exception as e:
            self.signals.error.emit(str(e), assistant_names)