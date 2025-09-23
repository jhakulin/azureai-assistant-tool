"""
ThreadViewDialog

A dialog that displays messages from a conversation thread in a tree view.
Top-level nodes are individual messages in time order. Each message can be
expanded to reveal:
 - the textual content (if any)
 - any file citations / file messages
 - any image messages or image URLs

Usage:
    from gui.thread_view_dialog import open_thread_view_dialog
    open_thread_view_dialog(parent, thread_name, ai_client_type, main_window)

Notes:
 - This dialog uses `ConversationThreadClient.get_instance(...).retrieve_conversation(...)`
   to fetch the conversation for `thread_name`. It attempts to extract timestamps
   where possible from the original message object.
 - Double-clicking a content node will open a plain read-only viewer for full text.
"""

import os
import traceback
from datetime import datetime


from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTreeWidget,
    QTreeWidgetItem, QFileDialog, QMessageBox, QTextEdit, QSplitter, QWidget
)
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QAction

from azure.ai.assistant.management.conversation_thread_client import ConversationThreadClient
from azure.ai.assistant.management.logger_module import logger
from gui.assistant_gui_workers import DeleteMessagesWorker
from gui.status_bar import ActivityStatus, StatusBar


class _ContentViewerDialog(QDialog):
    """Simple read-only dialog to display long text (message content)."""

    def __init__(self, parent=None, title: str = "Message Content", content: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(800, 600)

        layout = QVBoxLayout(self)
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(content or "")
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        self.close_btn = QPushButton("Close", self)
        self.copy_btn = QPushButton("Copy", self)
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(self.copy_btn)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

    def _copy_to_clipboard(self):
        self.text_edit.selectAll()
        self.text_edit.copy()


class ThreadViewDialog(QDialog):
    """
    Dialog showing a thread's conversation as a tree view.

    Top-level QTreeWidgetItems represent messages (in time order). Each message
    item contains children for textual content and attachments.
    """

    def __init__(self, parent, thread_name: str, ai_client_type, main_window):
        super().__init__(parent)
        self.thread_name = thread_name
        self.ai_client_type = ai_client_type
        self.main_window = main_window
        self.setWindowTitle(f"Thread: {thread_name}")
        self.resize(900, 700)

        self._init_ui()
        # Load messages on construction
        try:
            self.populate_messages()
        except Exception as e:
            logger.error(f"Failed to populate thread view: {e}\n{traceback.format_exc()}")
            QMessageBox.warning(self, "Error", f"Failed to load messages for thread '{thread_name}': {e}")

    def _init_ui(self):
        layout = QVBoxLayout(self)

        header_layout = QHBoxLayout()
        header_label = QLabel(f"<b>Thread:</b> {self.thread_name}", self)
        header_layout.addWidget(header_label)
        header_layout.addStretch()

        self.refresh_btn = QPushButton("Refresh", self)
        self.refresh_btn.clicked.connect(self.populate_messages)
        self.export_btn = QPushButton("Export...", self)
        self.export_btn.clicked.connect(self._export_thread_to_file)
        self.close_btn = QPushButton("Close", self)
        self.close_btn.clicked.connect(self.accept)

        header_layout.addWidget(self.refresh_btn)
        header_layout.addWidget(self.export_btn)
        header_layout.addWidget(self.close_btn)

        layout.addLayout(header_layout)

        # Splitter in case future detail panes are added
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setHandleWidth(1)

        # Tree view for messages
        # Tree view for messages (show only sender in column 0)
        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Sender", "Timestamp", "Summary"])
        self.tree.setRootIsDecorated(False)
        # Prevent double-click from expanding/collapsing nodes; we'll handle double-click to open content viewer.
        self.tree.setExpandsOnDoubleClick(False)
        # Allow multiple selection so users can select and delete multiple messages
        self.tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        # Provide a context menu (right-click) for operations like Delete message and Save attachment
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        splitter.addWidget(self.tree)

        # Footer / info label
        self.info_label = QLabel("", self)
        footer_widget = QWidget(self)
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.addWidget(self.info_label)
        footer_layout.addStretch()
        splitter.addWidget(footer_widget)

        splitter.setSizes([600, 40])
        layout.addWidget(splitter)

        # Add a local status bar for this dialog to display deletion progress
        self.status_bar = StatusBar(self)
        layout.addWidget(self.status_bar.get_widget())

    def _get_threads_client(self) -> ConversationThreadClient:
        return ConversationThreadClient.get_instance(self.ai_client_type)

    def _format_timestamp(self, original_message) -> str:
        """
        Try to extract a human readable timestamp from the original message object
        (which can be an OpenAI Message or an Azure ThreadMessage). This is best-effort.
        """
        if original_message is None:
            return ""
        # Common attributes attempted:
        for attr in ("created_at", "created", "creation_time", "created_at_timestamp", "timestamp"):
            ts = getattr(original_message, attr, None)
            if ts:
                # ts may be a string, int, or datetime
                try:
                    if isinstance(ts, (int, float)):
                        return datetime.fromtimestamp(ts).isoformat(sep=" ")
                    if isinstance(ts, str):
                        return ts
                    if hasattr(ts, "isoformat"):
                        return ts.isoformat(sep=" ")
                except Exception:
                    continue
        # Try metadata or dict-like created_at
        try:
            meta = getattr(original_message, "metadata", None)
            if meta and isinstance(meta, dict):
                for candidate in ("created_at", "timestamp", "creation_time"):
                    if candidate in meta:
                        return str(meta[candidate])
        except Exception:
            pass
        return ""

    def populate_messages(self):
        """
        Retrieve the conversation and populate the tree widget.
        Clears existing items and repopulates.
        """
        self.tree.clear()
        client = self._get_threads_client()
        timeout = getattr(self.main_window, "connection_timeout", None)
        conversation = client.retrieve_conversation(self.thread_name, timeout=timeout)
        msgs = conversation.messages or []
        self.info_label.setText(f"Messages: {len(msgs)}")

        # Build items in time order (assumed conversation.messages already ordered)
        for idx, msg in enumerate(msgs):
            # Show only sender (no role)
            sender = getattr(msg, "sender", None) or getattr(msg, "role", None) or "assistant"
            # Show only the sender in the main column
            title = sender
            timestamp = self._format_timestamp(getattr(msg, 'original_message', None))
            # Safely obtain text content (avoid direct attribute access that static analyzers complain about)
            text_msg = getattr(msg, 'text_message', None)
            text_content = getattr(text_msg, 'content', None) if text_msg is not None else ""
            summary = ""
            if text_content:
                # Short summary for the column
                summary = text_content.strip().splitlines()[0][:120]
            tree_item = QTreeWidgetItem(self.tree)
            tree_item.setText(0, title)
            tree_item.setText(1, timestamp)
            tree_item.setText(2, summary)
            # Attach a simple data reference to the item so handlers can show details.
            # Include the full text content in the top-level item's data so double-click opens it
            # without creating a separate 'Content' child node.
            # Deterministically extract the provider message id from the original message (.id)
            original_obj = getattr(msg, "original_message", None)
            msg_id = getattr(original_obj, "id", None) if original_obj is not None else None
            tree_item.setData(0, Qt.ItemDataRole.UserRole, {
                "message_obj": msg,
                "index": idx,
                "text_content": (text_content or ""),
                "message_id": (msg_id if msg_id is not None else "")
            })

            # Child: file messages
            file_msgs = getattr(msg, "file_messages", None) or []
            for fm in file_msgs:
                fm_node = QTreeWidgetItem(tree_item)
                fm_node.setText(0, "File")
                file_name = getattr(fm, "file_name", None) or getattr(fm, "file_name", "")
                fm_node.setText(2, file_name)
                fm_node.setData(0, 0x0100, {"kind": "file", "file_message": fm})

            # Child: image messages
            image_msgs = getattr(msg, "image_messages", None) or []
            for im in image_msgs:
                im_node = QTreeWidgetItem(tree_item)
                im_node.setText(0, "Image")
                im_name = getattr(im, "file_name", None) or getattr(im, "file_id", "") or "image"
                im_node.setText(2, im_name)
                im_node.setData(0, 0x0100, {"kind": "image", "image_message": im})

            # Child: image URLs (if any)
            image_urls = getattr(msg, "image_urls", None) or []
            for url in image_urls:
                url_node = QTreeWidgetItem(tree_item)
                url_node.setText(0, "Image URL")
                url_node.setText(2, url)
                url_node.setData(0, 0x0100, {"kind": "image_url", "url": url})

            tree_item.setExpanded(False)

        # Resize columns to content
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
        self.tree.setColumnWidth(2, max(200, int(self.width() * 0.5)))

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """
        If the node holds textual content, open a viewer. For files/images,
        provide quick actions (save).
        """
        data = item.data(0, 0x0100)
        if not data:
            return

        kind = data.get("kind", None)
        if kind == "text":
            content = data.get("content", "") or ""
            dlg = _ContentViewerDialog(self, title="Message Content", content=content)
            dlg.exec()
            return

        if kind == "file":
            file_msg = data.get("file_message", None)
            if file_msg:
                # Attempt to download / save file to chosen location
                self._save_file_message(file_msg)
            return

        if kind == "image":
            im = data.get("image_message", None)
            if im:
                self._save_image_message(im)
            return

        if kind == "image_url":
            url = data.get("url", "")
            # Show the URL in a viewer (or copy to clipboard)
            dlg = _ContentViewerDialog(self, title="Image URL", content=url)
            dlg.exec()
            return

        # Top-level message item double-click -> show entire message content viewer
        if "message_obj" in data:
            msg = data.get("message_obj")
            parts = []
            text_message = getattr(msg, 'text_message', None)
            if text_message and getattr(text_message, 'content', None):
                parts.append(text_message.content)
            # include file/image listings
            fparts = []
            for fm in getattr(msg, "file_messages", []) or []:
                fname = getattr(fm, "file_name", None) or getattr(fm, "file_id", "file")
                fparts.append(f"File: {fname}")
            for im in getattr(msg, "image_messages", []) or []:
                iname = getattr(im, "file_name", None) or getattr(im, "file_id", "image")
                fparts.append(f"Image: {iname}")
            if fparts:
                parts.append("\n\nAttachments:\n" + "\n".join(fparts))
            content_full = "\n\n".join(parts) or "<no content>"
            dlg = _ContentViewerDialog(self, title=f"Message: {getattr(msg, 'sender', '')}", content=content_full)
            dlg.exec()

    def _on_tree_context_menu(self, pos):
        """
        Show a context menu when the user right-clicks on the tree.
        Supports deleting top-level messages and saving attachments from child nodes.
        """
        try:
            item = self.tree.itemAt(pos)
            if item is None:
                return
            # Prefer UserRole for top-level message metadata, but fall back to child-role (0x0100)
            # so we correctly read data regardless of which role was used when creating the node.
            data = item.data(0, Qt.ItemDataRole.UserRole) or item.data(0, 0x0100) or {}
            menu = None

            # Build context menu depending on node kind
            menu = self.tree.createStandardContextMenu() if hasattr(self.tree, "createStandardContextMenu") else None
            if menu is None:
                from PySide6.QtWidgets import QMenu
                menu = QMenu(self.tree)

            # If the clicked item is a top-level message (parent is None) show a single
            # unified delete action that deletes the currently selected top-level message(s).
            if item.parent() is None:
                del_act = QAction("Delete selected message(s)", self.tree)
                del_act.triggered.connect(self._confirm_and_delete_selected_messages)
                menu.addAction(del_act)

            # If this is a file/image child, add Save action (reuse existing save handlers)
            kind = data.get("kind")
            if kind == "file":
                save_act = QAction("Save File...", self.tree)
                save_act.triggered.connect(lambda: self._save_file_message(data.get("file_message")))
                menu.addAction(save_act)
            elif kind == "image":
                save_act = QAction("Save Image...", self.tree)
                save_act.triggered.connect(lambda: self._save_image_message(data.get("image_message")))
                menu.addAction(save_act)
            elif kind == "image_url":
                open_act = QAction("Open Image URL...", self.tree)
                open_act.triggered.connect(lambda: _ContentViewerDialog(self, title="Image URL", content=data.get("url", "")).exec())
                menu.addAction(open_act)

            # Show the menu at the global position
            menu.exec(self.tree.mapToGlobal(pos))
        except Exception as e:
            logger.error(f"Error showing context menu: {e}")

    def _confirm_and_delete_selected_messages(self):
        """
        Delete all selected top-level messages in the tree. Prompts for confirmation.
        Uses an asynchronous worker so the UI remains responsive and so the main window
        status bar can show deletion progress similar to thread deletion in the sidebar.
        """
        try:
            selected_items = self.tree.selectedItems() or []
            if not selected_items:
                QMessageBox.information(self, "Delete Messages", "No messages selected for deletion.")
                return

            # Filter only top-level items (children represent attachments)
            top_level_items = []
            for it in selected_items:
                # ensure it's a top-level message (parent is None)
                if it.parent() is None:
                    top_level_items.append(it)

            if not top_level_items:
                QMessageBox.information(self, "Delete Messages", "No top-level messages selected for deletion.")
                return

            # Collect message ids
            message_ids = []
            for it in top_level_items:
                data = it.data(0, Qt.ItemDataRole.UserRole) or {}
                msg_obj = data.get("message_obj")
                if not msg_obj:
                    continue
                original = getattr(msg_obj, "original_message", None)
                mid = getattr(original, "id", None) if original is not None else None
                if mid:
                    message_ids.append(mid)

            if not message_ids:
                QMessageBox.warning(self, "Delete Messages", "Could not determine message ids for the selected messages.")
                return

            # Confirm deletion
            ids_preview = ", ".join([str(mid) for mid in message_ids][:10])
            more = "" if len(message_ids) <= 10 else f", and {len(message_ids)-10} more"
            reply = QMessageBox.question(self, "Confirm Delete",
                                         f"Delete {len(message_ids)} message(s) from thread '{self.thread_name}'?\nIDs: {ids_preview}{more}",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

            # Create and run the asynchronous delete worker so UI remains responsive.
            worker = DeleteMessagesWorker(
                ai_client_type=self.ai_client_type,
                thread_name=self.thread_name,
                message_ids=message_ids,
                main_window=self.main_window
            )

            # Keep a strong reference to the worker to prevent GC while running.
            if not hasattr(self, "_active_workers"):
                self._active_workers = []
            self._active_workers.append(worker)

            # When a message id is being deleted, show 'Deleting <message_id>' in status bar
            def _on_status_update(mid):
                try:
                    # Use the same ActivityStatus.DELETING to show consistent animation
                    # Display progress in the dialog's own status bar
                    self.status_bar.start_animation(
                        ActivityStatus.DELETING,
                        interval=500,
                        target_name=str(mid)
                    )
                except Exception:
                    pass

            worker.signals.status_update.connect(_on_status_update)

            # When deletion is complete, stop animation, refresh the view and cleanup worker ref
            def _on_finished(deleted_ids, _worker=worker):
                try:
                    try:
                        # Ensure the dialog-local status bar is fully cleared after deletion completes
                        self.status_bar.clear_all_statuses()
                    except Exception:
                        pass
                    # Refresh view after deletion
                    self.populate_messages()
                finally:
                    try:
                        self._active_workers.remove(_worker)
                    except Exception:
                        pass

            worker.signals.finished.connect(_on_finished)

            # Handle errors and stop animation
            def _on_error(error_msg, _worker=worker):
                try:
                    try:
                        self.status_bar.stop_animation(ActivityStatus.DELETING)
                    except Exception:
                        pass
                    logger.error(f"Error deleting messages asynchronously: {error_msg}")
                    QMessageBox.warning(self, "Error Deleting Messages", f"An error occurred: {error_msg}")
                finally:
                    try:
                        self._active_workers.remove(_worker)
                    except Exception:
                        pass

            worker.signals.error.connect(_on_error)

            # Execute the worker in a separate thread via QThreadPool
            QThreadPool.globalInstance().start(worker)

        except Exception as e:
            logger.error(f"Failed to delete selected messages: {e}\n{traceback.format_exc()}")
            QMessageBox.warning(self, "Error", f"Failed to delete selected messages: {e}")

    def _save_file_message(self, file_message):
        """
        Prompt user for a path and try to retrieve the file using the file_message
        object's `retrieve_file(output_folder_name)` method, if available.
        """
        suggested_name = getattr(file_message, "file_name", "") or "file.bin"
        target_path, _ = QFileDialog.getSaveFileName(self, "Save file as", suggested_name)
        if not target_path:
            return
        try:
            # If the FileMessage exposes a retrieve_file method (it does in this codebase),
            # it expects an output folder; try to use the directory chosen by the user.
            out_dir = os.path.dirname(target_path) or "."
            retrieved = file_message.retrieve_file(out_dir)
            if retrieved:
                # If retrieval produced a path, move/rename it to user-chosen filename
                try:
                    if os.path.abspath(retrieved) != os.path.abspath(target_path):
                        # move/rename
                        import shutil
                        shutil.move(retrieved, target_path)
                except Exception:
                    # fallback: try copy
                    try:
                        import shutil
                        shutil.copyfile(retrieved, target_path)
                    except Exception as ex2:
                        logger.error(f"Failed to place file at destination: {ex2}")
                QMessageBox.information(self, "Saved", f"File saved to: {target_path}")
            else:
                QMessageBox.warning(self, "Save failed", "Failed to retrieve the file from the server.")
        except Exception as e:
            logger.error(f"Exception saving file: {e}\n{traceback.format_exc()}")
            QMessageBox.warning(self, "Error", f"Failed to save file: {e}")

    def _save_image_message(self, image_message):
        suggested_name = getattr(image_message, "file_name", "") or (getattr(image_message, "file_id", "") + ".png")
        target_path, _ = QFileDialog.getSaveFileName(self, "Save image as", suggested_name, "PNG Files (*.png);;All Files (*)")
        if not target_path:
            return
        try:
            # ImageMessage may expose a retrieve_file or similar; reuse file_message flow if present
            if hasattr(image_message, "file_id") and hasattr(image_message, "file_name"):
                # Create a lightweight wrapper for reuse of retrieve_file convention if available
                if hasattr(image_message, "retrieve_file"):
                    out_dir = os.path.dirname(target_path) or "."
                    retrieved = image_message.retrieve_file(out_dir)
                    if retrieved:
                        import shutil
                        try:
                            if os.path.abspath(retrieved) != os.path.abspath(target_path):
                                shutil.move(retrieved, target_path)
                        except Exception:
                            shutil.copyfile(retrieved, target_path)
                        QMessageBox.information(self, "Saved", f"Image saved to: {target_path}")
                        return
            # As a fallback, try to save image data if attribute exists
            # Otherwise just notify user that saving isn't supported
            QMessageBox.information(self, "Not supported", "Saving images is not supported for this message type.")
        except Exception as e:
            logger.error(f"Exception saving image: {e}\n{traceback.format_exc()}")
            QMessageBox.warning(self, "Error", f"Failed to save image: {e}")

    def _export_thread_to_file(self):
        """
        Export the textual contents of the whole thread into a single text file.
        """
        fname, _ = QFileDialog.getSaveFileName(self, "Export thread to file", f"{self.thread_name}.md", "MD files (*.md);;All files (*.*)")
        if not fname:
            return
        try:
            client = self._get_threads_client()
            timeout = getattr(self.main_window, "connection_timeout", None)
            conversation = client.retrieve_conversation(self.thread_name, timeout=timeout)
            with open(fname, "w", encoding="utf-8") as fh:
                for msg in conversation.messages or []:
                    sender = getattr(msg, "sender", "") or ""
                    header = f"{sender}"
                    fh.write(header + "\n")
                    text_message = getattr(msg, "text_message", None)
                    if text_message and getattr(text_message, "content", None):
                        fh.write(text_message.content + "\n")
                    # attachments summary
                    for fm in getattr(msg, "file_messages", []) or []:
                        fname_out = getattr(fm, "file_name", None) or getattr(fm, "file_id", "")
                        fh.write(f"[File] {fname_out}\n")
                    for im in getattr(msg, "image_messages", []) or []:
                        iname = getattr(im, "file_name", None) or getattr(im, "file_id", "")
                        fh.write(f"[Image] {iname}\n")
                    fh.write("\n" + ("-" * 40) + "\n\n")
            QMessageBox.information(self, "Exported", f"Thread exported to {fname}")
        except Exception as e:
            logger.error(f"Failed to export thread: {e}\n{traceback.format_exc()}")
            QMessageBox.warning(self, "Error", f"Failed to export thread: {e}")


def open_thread_view_dialog(parent, thread_name: str, ai_client_type, main_window) -> ThreadViewDialog:
    """
    Convenience function to create and show the ThreadViewDialog.
    Returns the dialog instance for further interaction/testing if needed.
    """
    dlg = ThreadViewDialog(parent=parent, thread_name=thread_name, ai_client_type=ai_client_type, main_window=main_window)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
