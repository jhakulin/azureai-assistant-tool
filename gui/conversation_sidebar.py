# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

# This software uses the PySide6 library, which is licensed under the GNU Lesser General Public License (LGPL).
# For more details on PySide6's license, see <https://www.qt.io/licensing>

import uuid
import os
import time

from PySide6.QtWidgets import (
    QWidget, QDialog,
    QVBoxLayout, QHBoxLayout,
    QCheckBox, QLineEdit, QComboBox,
    QLabel, QListWidget, QListWidgetItem,
    QPushButton, QDialogButtonBox,
    QMenu,
    QFileDialog, QMessageBox,
    QSizePolicy, QAbstractItemView
)
from gui.thread_view_dialog import open_thread_view_dialog
from PySide6.QtCore import Qt, Signal, QThreadPool
from PySide6.QtGui import QFont, QIcon, QAction

from azure.ai.assistant.management.ai_client_type import AIClientType
from azure.ai.assistant.management.assistant_config_manager import AssistantConfigManager
from azure.ai.assistant.management.assistant_config import AssistantType
from azure.ai.assistant.management.conversation_thread_client import ConversationThreadClient
from azure.ai.assistant.management.logger_module import logger
from gui.assistant_client_manager import AssistantClientManager
from gui.assistant_gui_workers import open_assistant_config_dialog, LoadAssistantWorker, ProcessAssistantWorker, DeleteThreadsWorker, DeleteThreadsWorkerSignals
from gui.status_bar import ActivityStatus


class AssistantItemWidget(QWidget):
    checked_changed = Signal(str, bool)  # (assistant_name, is_checked)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.layout = QHBoxLayout(self)
        self.checkbox = QCheckBox(self)
        self.label = QLabel(name, self)
        self.name = name

        font = QFont("Arial", 11)
        self.checkbox.setFont(font)
        self.label.setFont(font)
        self.layout.addWidget(self.checkbox)
        self.layout.addWidget(self.label)
        self.layout.addStretch()
        self.setLayout(self.layout)
        # Connect the checkbox state change to emit the custom signal
        self.checkbox.stateChanged.connect(self.on_checkbox_state_changed)

    def on_checkbox_state_changed(self, state):
        is_checked = state == Qt.CheckState.Checked.value
        self.checked_changed.emit(self.name, is_checked)


class RenameDialog(QDialog):

    def __init__(self, current_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rename Thread")
        self.setModal(True)
        self.setFixedWidth(400)

        layout = QVBoxLayout(self)

        # Label
        label = QLabel("Enter new name for the thread:")
        layout.addWidget(label)

        # Input field
        self.name_input = QLineEdit(current_name)
        self.name_input.selectAll()
        layout.addWidget(self.name_input)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            parent=self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_new_name(self):
        return self.name_input.text().strip()


class CustomListWidget(QListWidget):
    # Signal emitted when user requests sorting from the context menu.
    # True = ascending, False = descending
    sortRequested = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        # Instead of item → attachments, store id_string → attachments
        self.itemIdToFileMap = {}  # {str: [attachment_dict, ...]}

    def clear_files(self):
        self.itemIdToFileMap.clear()

    def contextMenuEvent(self, event):
        context_menu = QMenu(self)
        current_item = self.currentItem()

        # Add rename action at the top if an item is selected
        if current_item:
            rename_action = context_menu.addAction("Rename Thread")
            rename_action.triggered.connect(lambda: self.rename_item(current_item))
            context_menu.addSeparator()

        # Keep all existing file attachment actions
        attach_file_search_action = context_menu.addAction("Attach File for File Search")
        attach_file_code_action = context_menu.addAction("Attach File for Code Interpreter")
        # Image attachment through context menu removed.
        # Paste images directly into the conversation input (ConversationInputView) instead.

        # Sorting submenu for threads
        sort_menu = context_menu.addMenu("Sort Threads")
        sort_asc_action = sort_menu.addAction("Sort A → Z")
        sort_desc_action = sort_menu.addAction("Sort Z → A")

        remove_file_menu = None

        if current_item:
            item_id = self._get_item_id(current_item)
            file_list = self.itemIdToFileMap.get(item_id, [])
            # Exclude image-only attachments from the "Remove File" submenu.
            # Image attachments are intended to be pasted/handled inline and should
            # not be removable via this context menu.
            non_image_files = []
            for file_info in file_list:
                try:
                    if isinstance(file_info, dict):
                        # If attachment_type explicitly says image_file, skip it
                        if file_info.get("attachment_type") == "image_file":
                            continue
                    # Otherwise include the file in the list
                    non_image_files.append(file_info)
                except Exception:
                    # On unexpected structure, include the item to be safe
                    non_image_files.append(file_info)

            if non_image_files:
                remove_file_menu = context_menu.addMenu("Remove File")
                for file_info in non_image_files:
                    # Safely derive a display name for the file
                    if isinstance(file_info, dict):
                        actual_file_path = file_info.get("file_path") or file_info.get("file_name") or ""
                        # Determine tool_type safely from tools list if present
                        if file_info.get("tools") and isinstance(file_info.get("tools"), list) and file_info["tools"]:
                            first_tool = file_info["tools"][0]
                            if isinstance(first_tool, dict):
                                tool_type = first_tool.get("type") or "File"
                            else:
                                tool_type = str(first_tool)
                        else:
                            tool_type = "File"
                    else:
                        # fallback for non-dict structures
                        actual_file_path = getattr(file_info, "file_path", None) or getattr(file_info, "file_name", None) or ""
                        try:
                            tools_attr = getattr(file_info, "tools", None)
                            if isinstance(tools_attr, list) and tools_attr:
                                first_tool = tools_attr[0]
                                if isinstance(first_tool, dict):
                                    tool_type = first_tool.get("type") or "File"
                                else:
                                    tool_type = str(first_tool)
                            else:
                                tool_type = "File"
                        except Exception:
                            tool_type = "File"

                    # Choose a safe base name for label
                    base_name = os.path.basename(actual_file_path) if actual_file_path else (file_info.get("file_name") if isinstance(file_info, dict) else (getattr(file_info, "file_name", "") or str(file_info)))
                    if not base_name:
                        base_name = "attachment"

                    file_label = f"{base_name} ({tool_type})"
                    action = remove_file_menu.addAction(file_label)
                    action.setData(file_info)

        selected_action = context_menu.exec_(self.mapToGlobal(event.pos()))

        if selected_action:
            if selected_action == attach_file_search_action:
                self.attach_file_to_selected_item("file_search")
            elif selected_action == attach_file_code_action:
                self.attach_file_to_selected_item("code_interpreter")
            # Image context-menu attach removed; use paste into conversation input instead.
            elif selected_action == sort_asc_action:
                # Emit a signal so the container (sidebar) can handle sorting + persistence
                self.sortRequested.emit(True)
            elif selected_action == sort_desc_action:
                # Emit a signal so the container (sidebar) can handle sorting + persistence
                self.sortRequested.emit(False)
            elif remove_file_menu and isinstance(selected_action, QAction) and selected_action.parent() == remove_file_menu:
                file_info = selected_action.data()
                self.remove_specific_file_from_selected_item(file_info, current_item)

    def rename_item(self, item):
        if not item:
            return

        current_name = item.text()
        dialog = RenameDialog(current_name, self)

        if dialog.exec() == QDialog.Accepted:
            new_name = dialog.get_new_name()

            if new_name and new_name != current_name:
                # Find the parent sidebar widget
                parent = self.parent()
                while parent and not hasattr(parent, 'update_thread_name'):
                    parent = parent.parent()

                if parent and hasattr(parent, 'update_thread_name'):
                    success = parent.update_thread_name(current_name, new_name)
                    if success:
                        item.setText(new_name)
                        logger.info(f"Thread renamed from '{current_name}' to '{new_name}'")
                    else:
                        QMessageBox.warning(
                            self,
                            "Rename Failed",
                            f"Could not rename thread. The name '{new_name}' may already exist or be invalid."
                        )
                else:
                    logger.error("Could not find parent with update_thread_name method")

    def attach_file_to_selected_item(self, mode, is_image=False):
        """Attaches a file to the selected item with a specified mode indicating its intended use."""
        file_dialog = QFileDialog(self)
        if is_image:
            file_path, _ = file_dialog.getOpenFileName(
                self,
                "Select Image File",
                filter="Images (*.png *.jpg *.jpeg *.gif *.webp)"
            )
        else:
            file_path, _ = file_dialog.getOpenFileName(self, "Select File")

        if file_path:
            current_item = self.currentItem()
            if current_item:
                item_id = self._get_item_id(current_item)
                if item_id not in self.itemIdToFileMap:
                    self.itemIdToFileMap[item_id] = []

                file_info = {
                    "file_id": None,  # This will be updated later
                    "file_path": file_path,
                    "attachment_type": "image_file" if is_image else "document_file",
                    "tools": [] if is_image else [{"type": mode}]
                }
                self.itemIdToFileMap[item_id].append(file_info)
                self.update_item_icon(current_item, self.itemIdToFileMap[item_id])

    def remove_specific_file_from_selected_item(self, file_info, item):
        """Removes a specific file from the selected item based on the file info provided."""
        if item:
            item_id = self._get_item_id(item)
            if item_id in self.itemIdToFileMap:
                file_path_to_remove = file_info["file_path"]
                self.itemIdToFileMap[item_id] = [
                    fi for fi in self.itemIdToFileMap[item_id]
                    if fi["file_path"] != file_path_to_remove
                ]
                if not self.itemIdToFileMap[item_id]:
                    item.setIcon(QIcon())
                else:
                    self.update_item_icon(item, self.itemIdToFileMap[item_id])

    def update_item_icon(self, item, files):
        """Updates the list item's icon based on whether there are attached files.
        If every attachment dictionary has attachment_type == 'image_file', do not show the paperclip.
        """
        if not files:
            item.setIcon(QIcon())
            return

        all_images = True
        for f in files:
            if isinstance(f, dict):
                if f.get('attachment_type') != 'image_file':
                    all_images = False
                    break
            else:
                # Unknown structure: conservatively treat as non-image so the paperclip is shown.
                all_images = False
                break

        if all_images:
            # Only image attachments -> do not show paperclip
            item.setIcon(QIcon())
        else:
            item.setIcon(QIcon("gui/images/paperclip_icon.png"))

    def get_attachments_for_selected_item(self):
        """Return the details of files attached to the currently selected item."""
        current_item = self.currentItem()
        if current_item:
            item_id = self._get_item_id(current_item)
            attached_files_info = self.itemIdToFileMap.get(item_id, [])
            attachments = []
            for file_info in attached_files_info:
                file_path = file_info["file_path"]
                file_name = os.path.basename(file_path)
                file_id = file_info.get("file_id", None)
                tools = file_info.get("tools", [])
                attachment_type = file_info.get("attachment_type", "document_file")
                attachments.append({
                    "file_name": file_name,
                    "file_id": file_id,
                    "file_path": file_path,
                    "attachment_type": attachment_type,
                    "tools": tools
                })
            return attachments
        return []

    def set_attachments_for_selected_item(self, attachments):
        """Set the attachments for the currently selected item."""
        current_item = self.currentItem()
        if current_item is not None:
            item_id = self._get_item_id(current_item)
            self.itemIdToFileMap[item_id] = attachments[:]
            self.update_item_icon(current_item, attachments)
        else:
            logger.warning("No item is currently selected.")

    def load_threads_with_attachments(self, threads):
        """Load threads into the list widget, adding icons for attached files only, based on attachments info."""
        self.clear_files()
        for thread in threads:
            item = QListWidgetItem(thread["thread_name"])
            self.addItem(item)
            # Provide a unique ID for the item so we can store attachments
            self._get_item_id(item)
            # Show a tooltip
            item.setToolTip("You can add/remove files by right-clicking this item.")
            attachments = thread.get("attachments", [])
            self.update_item_with_attachments(item, attachments)

    def update_item_with_attachments(self, item, attachments):
        """Update the given item with a paperclip icon if there are attachments.

        Use the centralized update_item_icon() so the UI logic for when to show
        the paperclip is consistent (e.g. hide for image-only attachments).
        """
        # Store complete attachment information in the mapping first
        item_id = self._get_item_id(item)
        self.itemIdToFileMap[item_id] = attachments[:] if attachments is not None else []

        # Update icon according to the attachments' types/paths
        self.update_item_icon(item, attachments)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F2:
            # F2 to rename selected item
            current = self.currentItem()
            if current:
                self.rename_item(current)
                return
        super().keyPressEvent(event)

    def delete_selected_item(self, item):
        if item:
            row = self.row(item)
            self.takeItem(row)
            item_id = self._get_item_id(item)
            if item_id in self.itemIdToFileMap:
               del self.itemIdToFileMap[item_id]

    def selectNewItem(self, previousRow):
        if previousRow < self.count():
            self.setCurrentRow(previousRow)
        elif self.count() > 0:
            self.setCurrentRow(self.count() - 1)

    def sort_threads_by_name(self, ascending: bool = True):
        """
        Delegate sorting request to the ConversationSidebar owner (if present).
        If the parent sidebar implements sorting, call it. Otherwise fallback
        to a simple alphabetical sort of the QListWidget items themselves.
        """
        # Try delegating to a parent that owns the thread list (ConversationSidebar)
        parent = self.parent()
        while parent and not hasattr(parent, 'sort_threads_by_name'):
            parent = parent.parent()
        if parent and hasattr(parent, 'sort_threads_by_name'):
            try:
                parent.sort_threads_by_name(ascending=ascending)
                return
            except Exception as e:
                logger.warning(f"Delegated sort failed: {e}")

        # Fallback: sort items locally within this QListWidget (no attachment preservation)
        try:
            names = [self.item(i).text() for i in range(self.count()) if self.item(i) is not None]
            names.sort(reverse=not ascending)
            self.clear()
            for name in names:
                self.addItem(QListWidgetItem(name))
        except Exception as e:
            logger.error(f"Failed to sort threads locally: {e}")
            QMessageBox.warning(self, "Error", f"Failed to sort threads: {e}")

    def get_current_text(self):
        """Return the text of the currently selected item."""
        current_item = self.currentItem()
        if current_item:
            return current_item.text()
        return ""

    def update_current_item(self, thread_title):
        """Update the name of the currently selected thread item."""
        current_item = self.currentItem()
        if current_item:
            current_item.setText(thread_title)

    def update_item_by_name(self, current_thread_name, new_thread_name):
        """Update the thread title from current_thread_name to new_thread_name."""
        for i in range(self.count()):
            item = self.item(i)
            if item.text() == current_thread_name:
                item.setText(new_thread_name)
                break

    def is_thread_selected(self, thread_name):
        """Check if the given thread name is the selected thread."""
        return self.get_current_text() == thread_name

    def get_last_thread_name(self):
        """Return the name of the last thread in the list."""
        if self.count() > 0:
            return self.item(self.count() - 1).text()
        return ""

    def _get_item_id(self, item):
        """
        Ensure the item has a unique ID stored in Qt.UserRole. Returns that ID.
        If it doesn't have one yet, generate it.
        """
        existing_id = item.data(Qt.UserRole)
        if existing_id is None:
            new_id = str(uuid.uuid4())
            item.setData(Qt.UserRole, new_id)
            return new_id
        return existing_id

    def mouseMoveEvent(self, event):
        # If the user is dragging with the left mouse button down,
        # ignore selection changes by not calling the parent implementation.
        if event.buttons() & Qt.LeftButton:
            # do nothing, effectively disabling drag-based selection
            return
        # Otherwise, preserve normal behavior (e.g., for other buttons)
        super().mouseMoveEvent(event)


class ConversationSidebar(QWidget):

    assistant_checkbox_toggled = Signal(str, bool)  # (assistant_name, is_checked)

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.setMinimumWidth(250)
        self.assistant_config_manager = AssistantConfigManager.get_instance()
        self.assistant_client_manager = AssistantClientManager.get_instance()

        self.addThreadButton = QPushButton("Add Thread", self)
        self.addThreadButton.setFixedHeight(23)
        self.addThreadButton.setFont(QFont("Arial", 11))

        self.cancelRunButton = QPushButton("Cancel Run", self)
        self.cancelRunButton.setFixedHeight(23)
        self.cancelRunButton.setFont(QFont("Arial", 11))

        buttonLayout = QHBoxLayout()
        buttonLayout.addWidget(self.addThreadButton)
        buttonLayout.addWidget(self.cancelRunButton)
        buttonLayout.setSpacing(10)

        self.threadList = CustomListWidget(self)
        self.threadList.setStyleSheet("""
            QListWidget {
                border-style: solid;
                border-width: 1px;
                border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;
                padding: 1px;
            }
        """)
        self.threadList.setFont(QFont("Arial", 11))
        # Connect the sortRequested signal so the sidebar performs sorting (and persistence)
        self.threadList.sortRequested.connect(self.sort_threads_by_name)

        self.addThreadButton.clicked.connect(self.on_add_thread_button_clicked)
        self.cancelRunButton.clicked.connect(self.main_window.on_cancel_run_button_clicked)
        self.threadList.itemClicked.connect(self.select_conversation_thread_by_item)
        self.threadList.itemDoubleClicked.connect(self.on_thread_double_clicked)
        self.threadList.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        self.assistantList = QListWidget(self)
        self.assistantList.setFont(QFont("Arial", 11))
        self.assistantList.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.assistantList.setStyleSheet("""
            QListWidget {
                border-style: solid;
                border-width: 1px;
                border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;
                padding: 1px;
            }
        """)
        self.assistantList.itemDoubleClicked.connect(self.on_assistant_double_clicked)
        self.assistantList.setToolTip("Select assistants to use in the conversation or double-click to edit the selected assistant.")

        self.aiClientComboBox = QComboBox()
        ai_client_type_names = [client_type.name for client_type in AIClientType]
        self.aiClientComboBox.addItems(ai_client_type_names)
        self.aiClientComboBox.currentIndexChanged.connect(self.on_ai_client_type_changed)

        layout = QVBoxLayout(self)
        layout.addWidget(self.aiClientComboBox)
        layout.addWidget(self.assistantList, 1)
        layout.addWidget(self.threadList, 2)
        layout.addLayout(buttonLayout)
        layout.setAlignment(Qt.AlignTop)

        self.setStyleSheet("""
            QWidget {
                border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;
                padding: 1px;
            }
        """)
        self.on_ai_client_type_changed(self.aiClientComboBox.currentIndex())
        self.assistant_checkbox_toggled.connect(self.main_window.handle_assistant_checkbox_toggled)

        # Keep strong references to background workers so they are not garbage-collected
        # while still running. This prevents occasional crashes where a worker is
        # collected and its signals or resources lead to invalid accesses on the main thread.
        self._active_workers = []

    def sort_threads_by_name(self, ascending: bool = True):
        """
        Sort the sidebar's threadList items by their visible text (thread name).
        Preserves the mapping of item IDs stored in Qt.UserRole so attachments
        remain associated with the same thread item after reordering.
        Also persists the sorted order to the ConversationThreadConfig JSON so
        subsequent reloads will respect the chosen order.
        """
        try:
            # Build a map of current thread name -> (item_id, tooltip)
            name_map = {}
            for i in range(self.threadList.count()):
                it = self.threadList.item(i)
                if it is None:
                    continue
                item_id = it.data(Qt.UserRole)
                tooltip = it.toolTip() if hasattr(it, "toolTip") else ""
                name_map[it.text()] = (item_id, tooltip)

            # Try to persist sorted order in the thread config, and fetch the canonical list of threads.
            try:
                threads_client = ConversationThreadClient.get_instance(self._ai_client_type)
                config = threads_client.get_config()
                if config is not None:
                    # This will reorder config._threads and persist to JSON
                    config.sort_threads_by_name(ascending=ascending, persist=True)
                    threads = config.get_all_threads()
                else:
                    # Fallback to sorting current names if config is not available
                    threads = [{"thread_name": name} for name in sorted(name_map.keys(), key=lambda n: n.lower(), reverse=not ascending)]
            except Exception as ex:
                logger.warning(f"Could not persist sort order to thread config: {ex}")
                # Fallback to sorting current names in memory
                threads = [{"thread_name": name} for name in sorted(name_map.keys(), key=lambda n: n.lower(), reverse=not ascending)]

            # Rebuild the threadList preserving item ids and tooltips when possible
            self.threadList.clear()
            for entry in threads:
                thread_name = entry.get("thread_name")
                if thread_name is None:
                    continue
                item_id, tooltip = name_map.get(thread_name, (None, None))
                new_item = QListWidgetItem(thread_name)
                if item_id is not None:
                    new_item.setData(Qt.UserRole, item_id)
                if tooltip:
                    new_item.setToolTip(tooltip)
                self.threadList.addItem(new_item)

            # Keep selection cleared after sort
            self.threadList.clearSelection()
        except Exception as e:
            logger.error(f"Failed to sort threads: {e}")
            QMessageBox.warning(self, "Error", f"Failed to sort threads: {e}")

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.assistantList.hasFocus():
                self.delete_selected_assistant()
            elif self.threadList.hasFocus():
                self.delete_selected_threads()
        else:
            super().keyPressEvent(event)

    def on_assistant_double_clicked(self, item):
        widget = self.assistantList.itemWidget(item)
        assistant_name = widget.label.text()
        assistant_config = self.assistant_config_manager.get_config(assistant_name)
        if assistant_config:
            self.dialog = open_assistant_config_dialog(
                parent=self.main_window,
                assistant_type=assistant_config.assistant_type,
                assistant_name=assistant_name,
                function_config_manager=self.main_window.function_config_manager,
                callback=self.on_assistant_config_submitted
            )

    def on_assistant_config_submitted(self, assistant_config_json, ai_client_type, assistant_type, assistant_name):
        worker = ProcessAssistantWorker(
            assistant_config_json=assistant_config_json,
            ai_client_type=ai_client_type,
            assistant_type=assistant_type,
            assistant_name=assistant_name,
            main_window=self.main_window,
            assistant_client_manager=self.assistant_client_manager
        )
        # Keep a strong reference to the worker to avoid it being garbage-collected
        self._active_workers.append(worker)

        # Connect finished and error signals and pass the worker so slots can clean up the reference
        worker.signals.finished.connect(lambda result, w=worker: self.on_assistant_config_submit_finished(result, w))
        worker.signals.error.connect(lambda error_msg, w=worker: self.on_assistant_config_submit_error(error_msg, w))

        self.dialog.start_processing_signal.start_signal.emit(ActivityStatus.PROCESSING)
        # Execute the worker in a separate thread using QThreadPool
        QThreadPool.globalInstance().start(worker)

    def on_assistant_config_submit_finished(self, result, worker=None):
        assistant_client, realtime_audio, assistant_name, ai_client_type = result
        self.assistant_client_manager.register_client(
            name=assistant_name,
            assistant_client=assistant_client,
            realtime_audio=realtime_audio
        )
        self.dialog.stop_processing_signal.stop_signal.emit(ActivityStatus.PROCESSING)
        client_type = AIClientType[ai_client_type]
        # UI update runs on the main thread.
        self.main_window.conversation_sidebar.load_assistant_list(client_type)
        self.dialog.update_assistant_combobox()

        # Remove strong reference to allow worker to be garbage-collected
        if worker in self._active_workers:
            try:
                self._active_workers.remove(worker)
            except ValueError:
                pass

    def on_assistant_config_submit_error(self, error_msg, worker=None):
        self.dialog.stop_processing_signal.stop_signal.emit(ActivityStatus.PROCESSING)

        # Remove strong reference to allow worker to be garbage-collected
        if worker in self._active_workers:
            try:
                self._active_workers.remove(worker)
            except ValueError:
                pass

        # Show error using a message box on the main thread.
        QMessageBox.warning(self.main_window, "Error",
                            f"An error occurred while creating/updating the assistant: {error_msg}")

    def delete_selected_assistant(self):
        current_item = self.assistantList.currentItem()
        if current_item:
            row = self.assistantList.row(current_item)
            item = self.assistantList.item(row)
            widget = self.assistantList.itemWidget(item)
            assistant_name = widget.label.text()
            reply = QMessageBox.question(self, 'Confirm Delete',
                                         f"Are you sure you want to delete '{assistant_name}'?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)

            if reply == QMessageBox.Yes:
                try:
                    assistant_client = self.assistant_client_manager.get_client(assistant_name)
                    if assistant_client:
                        assistant_client.purge(self.main_window.connection_timeout)
                    self.assistant_client_manager.remove_client(assistant_name)
                    self.main_window.conversation_view.conversationView.clear()
                    self.assistant_config_manager.load_configs()
                    self.load_assistant_list(self._ai_client_type)
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"An error occurred while deleting the assistant: {e}")

    def populate_assistants(self, assistant_names):
        """Populate the assistant list with given assistant names."""
        # Capture the currently selected assistant's name
        currently_selected_assistants = self.get_selected_assistants()

        # Clear and repopulate the list
        self.assistantList.clear()
        for name in assistant_names:
            item = QListWidgetItem(self.assistantList)
            widget = AssistantItemWidget(name)
            item.setSizeHint(widget.sizeHint())
            self.assistantList.addItem(item)
            self.assistantList.setItemWidget(item, widget)
            # Connect the widget's checked_changed signal to ConversationSidebar's signal
            widget.checked_changed.connect(self.assistant_checkbox_toggled.emit)

        # Restore selection if the assistant is still in the list
        for i in range(self.assistantList.count()):
            item = self.assistantList.item(i)
            widget : AssistantItemWidget = self.assistantList.itemWidget(item)
            if widget.label.text() in currently_selected_assistants:  # Assuming the label's text stores the assistant's name
                # self.assistantList.setCurrentItem(item)
                # check the checkbox
                widget.checkbox.setChecked(True)

    def get_selected_assistants(self):
        """Return a list of names of the selected assistants."""
        selected_assistants = []
        for i in range(self.assistantList.count()):
            item = self.assistantList.item(i)
            widget = self.assistantList.itemWidget(item)
            if isinstance(widget, AssistantItemWidget) and widget.checkbox.isChecked():
                selected_assistants.append(widget.label.text())
        return selected_assistants

    def is_assistant_selected(self, assistant_name):
        """Check if the given assistant name is selected."""
        for i in range(self.assistantList.count()):
            item = self.assistantList.item(i)
            widget = self.assistantList.itemWidget(item)
            if isinstance(widget, AssistantItemWidget) and widget.label.text() == assistant_name:
                return widget.checkbox.isChecked()
        return False

    def get_ai_client_type(self):
        """Return the AI client type selected in the combo box."""
        return self._ai_client_type

    def load_assistant_list(self, ai_client_type: AIClientType):
        """Populate the assistant list with the given assistant names."""
        worker = LoadAssistantWorker(
            ai_client_type=ai_client_type,
            assistant_config_manager=self.assistant_config_manager,
            assistant_client_manager=self.assistant_client_manager,
            main_window=self.main_window
        )
        worker.signals.finished.connect(self.on_load_assistant_list_finished)
        worker.signals.error.connect(self.on_load_assistant_list_error)
        QThreadPool.globalInstance().start(worker)

    def on_load_assistant_list_finished(self, assistant_names):
        """
        Callback on successful load; update the assistant list in the UI.
        """
        self.populate_assistants(assistant_names)

    def on_load_assistant_list_error(self, error_msg, assistant_names):
        """
        Callback on error; display a warning message.
        """
        self.populate_assistants(assistant_names)
        QMessageBox.warning(self, "Error", f"Error loading assistants: {error_msg}")

    def on_ai_client_type_changed(self, index):
        """Handle changes in the selected AI client type."""
        try:
            selected_ai_client = self.aiClientComboBox.itemText(index)
            self._ai_client_type = AIClientType[selected_ai_client]

            # Load the assistants for the selected AI client type
            self.load_assistant_list(self._ai_client_type)

            # Clear the existing items in the thread list
            self.threadList.clear()
            self.threadList.clear_files()

            # Get the threads for the selected AI client type
            threads_client = ConversationThreadClient.get_instance(self._ai_client_type, config_folder='config')
            threads = threads_client.get_conversation_threads()
            self.threadList.load_threads_with_attachments(threads)
        except Exception as e:
            logger.error(f"Error while changing AI client type: {e}")
        finally:
            self.main_window.set_active_ai_client_type(self._ai_client_type)

    def set_attachments_for_selected_thread(self, attachments):
        """Set the attachments for the currently selected item."""
        self.threadList.set_attachments_for_selected_item(attachments)

    def on_add_thread_button_clicked(self):
        """Handle clicks on the add thread button."""
        # Get the selected assistant
        selected_assistants = self.get_selected_assistants()
        # check if selected_assistants is empty list
        if not selected_assistants:
            QMessageBox.warning(self, "Error", "Please select an assistant first.")
            return
        try:
            threads_client = ConversationThreadClient.get_instance(self._ai_client_type)
            thread_name = self.create_conversation_thread(threads_client, timeout=self.main_window.connection_timeout)
            threads_client.save_conversation_threads()
            self._select_thread(thread_name)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"An error occurred while creating a new thread: {e}")

    def on_cancel_run_button_clicked(self):
        """Handle clicks on the cancel run button."""
        self.main_window.on_cancel_run_button_clicked()

    def create_conversation_thread(self, threads_client : ConversationThreadClient, is_scheduled_task=False, timeout: float=None):
        try:
            start_time = time.time()
            unique_thread_name = threads_client.create_conversation_thread(timeout=timeout)
            end_time = time.time()
            logger.debug(f"Total time taken to create a new conversation thread: {end_time - start_time} seconds")
            new_item = QListWidgetItem(unique_thread_name)
            self.threadList.addItem(new_item)
            thread_tooltip_text = f"You can add/remove files by right-clicking this item."
            new_item.setToolTip(thread_tooltip_text)

            if not is_scheduled_task:
                self.main_window.conversation_view.conversationView.clear()
            return unique_thread_name
        except Exception as e:
            QMessageBox.warning(self, "Error", f"An error occurred while creating a new thread: {e}")

    def select_conversation_thread_by_item(self, selected_item):
        selected_count = len(self.threadList.selectedItems())
        if selected_count == 1:
            # Only load the conversation if exactly one item is selected
            unique_thread_name = selected_item.text()
            self._select_thread(unique_thread_name)
        else:
            # Multiple items selected – do nothing
            # so the user can continue shift/ctrl selection, etc.
            pass

    def on_thread_double_clicked(self, item):
        """
        Open a separate dialog showing the thread messages in a tree view.
        """
        try:
            thread_name = item.text()
            ai_client_type = self._ai_client_type
            # Use the convenience function from gui.thread_view_dialog to open the dialog
            open_thread_view_dialog(parent=self.main_window, thread_name=thread_name, ai_client_type=ai_client_type, main_window=self.main_window)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to open thread view: {e}")

    def select_conversation_thread_by_name(self, unique_thread_name):
        self._select_thread(unique_thread_name)

    def _select_threadlist_item(self, unique_thread_name):
        # Clear any existing selection first
        self.threadList.clearSelection()
        for index in range(self.threadList.count()):
            if self.threadList.item(index).text() == unique_thread_name:
                self.threadList.setCurrentRow(index)
                break

    def _select_thread(self, unique_thread_name):
        # Select the thread item in the sidebar
        self._select_threadlist_item(unique_thread_name)
        try:
            threads_client = ConversationThreadClient.get_instance(self._ai_client_type)
            threads_client.set_current_conversation_thread(unique_thread_name)
            self.main_window.conversation_view.conversationView.clear()
            # Retrieve the messages for the selected thread
            conversation = threads_client.retrieve_conversation(unique_thread_name, timeout=self.main_window.connection_timeout)
            if conversation.messages is not None:
                self.main_window.conversation_view.append_conversation_messages(conversation.messages)
            selected_assistants = self.get_selected_assistants()
            for assistant_name in selected_assistants:
                assistant_client = self.assistant_client_manager.get_client(assistant_name)
                if assistant_client.assistant_config.assistant_type == AssistantType.REALTIME_ASSISTANT.value:
                    assistant_client.set_active_thread(unique_thread_name)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"An error occurred while selecting the thread: {e}")

    def on_delete_thread_status_update(self, thread_name: str):
        self.main_window.status_bar.start_animation(
            ActivityStatus.DELETING,
            interval=500,
            thread_name=thread_name
        )

    def on_delete_threads_finished(self, updated_threads, scroll_position, row):
        # Stop the status bar animation (or you can produce a different message if you like)
        self.main_window.status_bar.clear_all_statuses()

        # 1) Clear out the list
        self.threadList.clear()

        # 2) Reload updated threads with attachments
        self.threadList.load_threads_with_attachments(updated_threads)

        # 3) Restore scroll position, etc.
        self.threadList.verticalScrollBar().setValue(scroll_position)
        if row >= self.threadList.count():
            row = self.threadList.count() - 1
        self.threadList.setCurrentRow(row)
        self.threadList.clearSelection()

        # 4) Clear the conversation area for safety
        self.main_window.conversation_view.conversationView.clear()

    def on_delete_threads_error(self, error_msg):
        self.main_window.status_bar.stop_animation(ActivityStatus.DELETING)
        logger.error(f"Error deleting threads asynchronously: {error_msg}")
        QMessageBox.warning(
            self,
            "Error Deleting Threads",
            f"An error occurred: {error_msg}"
        )

    def delete_selected_threads(self):
        # Gather the items
        current_scroll_position = self.threadList.verticalScrollBar().value()
        current_row = self.threadList.currentRow()
        selected_items = self.threadList.selectedItems()
        if not selected_items:
            return  # Nothing selected, nothing to delete

        selected_count = len(selected_items)

        # If multiple items, confirm with user
        if selected_count > 1:
            all_names = ", ".join([item.text() for item in selected_items])
            prompt_title = f"Delete {selected_count} Threads"
            prompt_text = (
                f"Are you sure you want to delete these {selected_count} threads?"
            )
            reply = QMessageBox.question(
                self,
                prompt_title,
                prompt_text,
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return  # User canceled

        # Prepare thread names to delete
        thread_names = [item.text() for item in selected_items]

        # Create the worker and connect signals
        worker = DeleteThreadsWorker(
            ai_client_type=self._ai_client_type,
            thread_names=thread_names,
            main_window=self.main_window
        )

        # Keep a strong reference to the worker so it is not garbage-collected while running.
        # This prevents crashes that can occur if the worker object is collected and its
        # signal handlers or resources are accessed by the Qt main thread.
        if not hasattr(self, "_active_workers"):
            self._active_workers = []
        self._active_workers.append(worker)

        # When a thread is about to be deleted, show 'Deleting <thread_name>'
        worker.signals.status_update.connect(self.on_delete_thread_status_update)

        # When deletion is done, refresh the thread list and ensure worker is removed from active list.
        def _on_worker_finished(updated_threads, _scroll=current_scroll_position, _row=current_row, _worker=worker):
            try:
                self.on_delete_threads_finished(updated_threads, _scroll, _row)
            finally:
                try:
                    self._active_workers.remove(_worker)
                except Exception:
                    # Defensive: ignore if already removed
                    pass

        worker.signals.finished.connect(_on_worker_finished)

        # Handle errors and ensure cleanup
        def _on_worker_error(error_msg, _worker=worker):
            try:
                self.on_delete_threads_error(error_msg)
            finally:
                try:
                    self._active_workers.remove(_worker)
                except Exception:
                    pass

        worker.signals.error.connect(_on_worker_error)

        # Execute the worker in a separate thread via QThreadPool
        QThreadPool.globalInstance().start(worker)

    def update_thread_name(self, old_name: str, new_name: str) -> bool:
        try:
            # Validate new name
            if not new_name or new_name.isspace():
                logger.warning("Thread name cannot be empty")
                return False

            # Sanitize the name (remove any problematic characters)
            invalid_chars = ['"', '\\']  # Only escape characters that break JSON
            for char in invalid_chars:
                if char in new_name:
                    logger.warning(f"Thread name contains invalid character: {char}")
                    return False

            # Additional validation for edge cases
            if new_name.startswith(' ') or new_name.endswith(' '):
                # Trim whitespace automatically instead of rejecting
                new_name = new_name.strip()
                if not new_name:
                    logger.warning("Thread name cannot be only whitespace")
                    return False

            # Length validation (reasonable limit for UI display)
            if len(new_name) > 255:
                logger.warning("Thread name is too long (max 255 characters)")
                return False

            # Get the thread client
            threads_client = ConversationThreadClient.get_instance(self._ai_client_type)

            # Check if old thread exists
            all_threads = threads_client.get_conversation_threads()
            old_thread = next((t for t in all_threads if t['thread_name'] == old_name), None)
            if not old_thread:
                logger.error(f"Thread '{old_name}' not found")
                return False

            # Check if new name would be unique (excluding current thread)
            existing_names = [t['thread_name'] for t in all_threads if t['thread_name'] != old_name]
            if new_name in existing_names:
                logger.warning(f"Thread name '{new_name}' already exists")
                return False

            # Update the thread name using the client
            updated_name = threads_client.set_conversation_thread_name(new_name, old_name)

            # Save the configuration
            threads_client.save_conversation_threads()

            logger.info(f"Successfully renamed thread from '{old_name}' to '{updated_name}'")
            return True

        except Exception as e:
            logger.error(f"Failed to rename thread from '{old_name}' to '{new_name}': {e}")
            return False
