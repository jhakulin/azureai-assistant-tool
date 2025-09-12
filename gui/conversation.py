# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

# This software uses the PySide6 library, which is licensed under the GNU Lesser General Public License (LGPL).
# For more details on PySide6's license, see <https://www.qt.io/licensing>

from azure.ai.assistant.management.assistant_config_manager import AssistantConfigManager
from azure.ai.assistant.management.message import ConversationMessage
from azure.ai.assistant.management.logger_module import logger

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit
from PySide6.QtGui import QFont, QTextCursor, QDesktopServices, QMouseEvent, QGuiApplication, QPalette, QImage
from PySide6.QtCore import Qt, QUrl, QMimeData, QIODevice, QBuffer
from bs4 import BeautifulSoup

import html, os, re, subprocess, sys, tempfile
import base64, random, time
from typing import List
from collections import defaultdict
import threading
from enum import Enum
import markdown


class AssistantStreamingState(Enum):
    NOT_STREAMING = 0
    STREAMING = 1


class ConversationInputView(QTextEdit):
    PLACEHOLDER_TEXT = "Message Assistant..."

    def __init__(self, parent, main_window):
        super().__init__(parent)
        self.main_window = main_window
        self.setInitialPlaceholderText()
        self.pasted_attachments = []
        self.pasted_images_html = {}

    def setInitialPlaceholderText(self):
        self.setText(self.PLACEHOLDER_TEXT)

    def focusInEvent(self, event):
        if self.toPlainText() == self.PLACEHOLDER_TEXT:
            self.clear()
        super().focusInEvent(event)

    def keyPressEvent(self, event):
        if self.toPlainText() == self.PLACEHOLDER_TEXT and not event.text().isspace():
            self.clear()

        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            html_before = self.toHtml()
            super().keyPressEvent(event)
            html_after = self.toHtml()
            self.check_for_deleted_images(html_before, html_after)
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            user_text = self.toPlainText()
            pasted_images = list(self.pasted_attachments)
            self.pasted_attachments.clear()
            self.main_window.on_user_input_complete(user_text, pasted_image_file_paths=pasted_images)
            self.clear()
        else:
            super().keyPressEvent(event)

    def get_and_clear_pasted_attachments(self):
        images = list(self.pasted_attachments)
        self.pasted_attachments.clear()
        return images

    def insertFromMimeData(self, mimeData: QMimeData):
        IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')

        if mimeData.hasImage():
            image = QImage(mimeData.imageData())
            if not image.isNull():
                logger.debug("Inserting image from clipboard...")
                temp_dir = tempfile.gettempdir()
                file_name = self.generate_unique_filename("pasted_image.png")
                temp_path = os.path.join(temp_dir, file_name)
                image.save(temp_path)
                self.add_image_thumbnail(image, temp_path)
            else:
                logger.warning("Pasted image data was null.")
        elif mimeData.hasUrls():
            for url in mimeData.urls():
                if url.isLocalFile():
                    local_path = url.toLocalFile()
                    ext = os.path.splitext(local_path)[1].lower()
                    if ext in IMAGE_EXTENSIONS:
                        image = QImage(local_path)
                        if not image.isNull():
                            temp_dir = tempfile.gettempdir()
                            file_name = self.generate_unique_filename(os.path.basename(local_path))
                            temp_path = os.path.join(temp_dir, file_name)
                            image.save(temp_path)
                            self.add_image_thumbnail(image, temp_path)
                        else:
                            logger.warning(f"Could not load image from {local_path}")
                    else:
                        logger.info(f"Unsupported file type pasted: {local_path}")
                        super().insertFromMimeData(mimeData)
                else:
                    super().insertFromMimeData(mimeData)
        elif mimeData.hasText():
            super().insertFromMimeData(mimeData)
        else:
            super().insertFromMimeData(mimeData)

    def generate_unique_filename(self, base_name):
        name, ext = os.path.splitext(base_name)
        unique_name = f"{name}_{int(time.time())}_{random.randint(1000, 9999)}{ext}"
        return unique_name

    def add_image_thumbnail(self, image: QImage, file_path: str):
        image_thumbnail = image.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        image_thumbnail.save(buffer, "PNG")
        base64_data = buffer.data().toBase64().data().decode()
        html_img = f'<img src="data:image/png;base64,{base64_data}" alt="{file_path}" />'
        cursor = self.textCursor()
        cursor.insertHtml(html_img)
        self.pasted_images_html[file_path] = html_img
        if file_path not in self.pasted_attachments:
            self.pasted_attachments.append(file_path)

    def check_for_deleted_images(self, html_before: str, html_after: str):
        soup_before = BeautifulSoup(html_before, 'html.parser')
        soup_after = BeautifulSoup(html_after, 'html.parser')

        file_paths_before = {img.get('alt', '') for img in soup_before.find_all('img')}
        file_paths_after = {img.get('alt', '') for img in soup_after.find_all('img')}

        missing_file_paths = file_paths_before - file_paths_after
        if missing_file_paths:
            logger.debug(f"User removed images: {missing_file_paths}")

        for file_path in missing_file_paths:
            if file_path in self.pasted_images_html:
                del self.pasted_images_html[file_path]
            if file_path in self.pasted_attachments:
                self.pasted_attachments.remove(file_path)

    def mouseReleaseEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        anchor = cursor.charFormat().anchorHref()
        if anchor:
            QDesktopServices.openUrl(QUrl(anchor))
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)


class ClickableTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_widget = parent
        self._toggle_provider = None  # ConversationView

    def set_toggle_provider(self, provider):
        self._toggle_provider = provider

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            cursor = self.cursorForPosition(event.pos())
            href = cursor.charFormat().anchorHref()
            if href:
                if href.startswith(("http://", "https://")):
                    QDesktopServices.openUrl(QUrl(href))
                else:
                    self.open_file(href)
                return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        if self._toggle_provider:
            menu.addSeparator()
            act = menu.addAction("Formatted (Markdown)")
            act.setCheckable(True)
            act.setChecked(self._toggle_provider.get_markdown_enabled())
            act.triggered.connect(lambda checked: self._toggle_provider.set_markdown_enabled(checked))
        menu.exec(event.globalPos())

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        cursor = self.cursorForPosition(event.pos())
        block = cursor.block()
        if block.isValid() and self._is_pre_block(block):
            first = block
            last = block
            b = block.previous()
            while b.isValid() and self._is_pre_block(b):
                first = b
                b = b.previous()
            b = block.next()
            while b.isValid() and self._is_pre_block(b):
                last = b
                b = b.next()
            sel = QTextCursor(first)
            start_pos = first.position()
            end_pos = last.position() + last.length() - 1
            sel.setPosition(start_pos, QTextCursor.MoveAnchor)
            sel.setPosition(end_pos, QTextCursor.KeepAnchor)
            self.setTextCursor(sel)
            return
        super().mouseDoubleClickEvent(event)

    def _is_pre_block(self, block):
        try:
            return block.blockFormat().nonBreakableLines()
        except Exception:
            return False

    def open_file(self, file_path):
        try:
            if sys.platform.startswith('linux'):
                subprocess.call(["xdg-open", file_path])
            elif sys.platform.startswith('win32'):
                os.startfile(file_path)
            elif sys.platform.startswith('darwin'):
                subprocess.call(["open", file_path])
        except Exception as e:
            logger.error(f"Failed to open file: {file_path} - {e}")


class ConversationView(QWidget):
    def __init__(self, parent, main_window):
        super().__init__(parent)
        self.main_window = main_window
        self.assistant_config_manager = AssistantConfigManager.get_instance()

        # Output folder
        self.file_path = 'output'
        os.makedirs(self.file_path, exist_ok=True)

        # State
        self.text_to_url_map = {}
        self.streaming_buffer = defaultdict(list)  # sender -> [chunks]
        self.stream_snapshot = defaultdict(str)
        self.is_assistant_streaming = defaultdict(lambda: AssistantStreamingState.NOT_STREAMING)
        self.stream_regions = defaultdict(dict)    # sender -> {header_start, start, end}
        self._lock = threading.RLock()

        # Render mode and history for re-rendering
        self._markdown_enabled = True
        self._history = []  # items: {'kind':'text'|'image', ...}

        self._build_markdown_converter()
        self.init_ui()
        self.apply_document_theme_styles()

    def init_ui(self):
        self.layout = QVBoxLayout(self)

        self.conversationView = ClickableTextEdit(self)
        self.conversationView.setReadOnly(True)
        self.conversationView.setFont(QFont("Arial", 11))
        self.conversationView.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.conversationView.set_toggle_provider(self)

        base = QUrl.fromLocalFile(os.path.abspath(self.file_path) + os.sep)
        self.conversationView.document().setBaseUrl(base)

        self.inputField = ConversationInputView(self, self.main_window)
        self.inputField.setAcceptRichText(False)
        self.inputField.setFixedHeight(100)
        self.inputField.setToolTip("Type a message or paste an image here for the assistant.")
        self.inputField.setFont(QFont("Arial", 11))

        self.layout.addWidget(self.conversationView)
        self.layout.addWidget(self.inputField)

        self.conversationView.setStyleSheet("""
            QTextEdit {
                border: 1px solid #c0c0c0;
                border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;
                border-radius: 4px;
                padding: 1px;
            }
        """)
        self.inputField.setStyleSheet(
            "QTextEdit {"
            "  border-style: solid;"
            "  border-width: 1px;"
            "  border-color: #a0a0a0 #ffffff #ffffff #a0a0a0;"
            "  padding: 1px;"
            "}"
        )

    def get_text_to_url_map(self):
        return self.text_to_url_map

    def is_dark_mode(self):
        app = QGuiApplication.instance()
        if app is not None:
            windowBackgroundColor = app.palette().color(QPalette.ColorRole.Window)
            return windowBackgroundColor.lightness() < 127
        return False

    def apply_document_theme_styles(self) -> None:
        dark = self.is_dark_mode()
        text_fg        = "#dcdcdc" if dark else "#24292e"
        link_fg        = "#4ea1ff" if dark else "#0066cc"
        code_bg        = "#2d2d30" if dark else "#f6f8fa"
        code_border    = "#464647" if dark else "#d0d7de"
        code_fg        = "#ce9178" if dark else "#032f62"
        subtle_border  = "#2a2a2a" if dark else "#e6e6e6"
        tbl_header_bg  = "#2a2a2a" if dark else "#f2f2f2"
        quote_bg       = "#252525" if dark else "#f8f9fb"
        quote_border   = "#3c3c3c" if dark else "#d0d7de"

        inline_code_bg = "#3c3c3c" if dark else "#eff1f3"
        code_block_shadow = "rgba(0,0,0,0.3)" if dark else "rgba(0,0,0,0.1)"

        css = f"""
        /* message wrapper for spacing */
        .message {{
            margin-bottom: 12px;
        }}

        /* header: name + optional meta (no avatar) */
        .msg-header {{
            display: block;
            margin-bottom: 6px;
        }}

        /* sender label */
        .sender {{
            display: inline-block;
            font-weight: 700;
            font-size: 14.5px;
            line-height: 1;
            color: {text_fg};
            letter-spacing: 0.2px;
        }}

        /* Role specific classes */
        .sender.user {{ color: #1a73e8; }}
        .sender.assistant {{ color: {text_fg}; }}

        /* Scope all markdown output inside .md-root to stabilize font metrics */
        .md-root {{
            color: {text_fg};
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 13px;
            line-height: 1.45;
            margin: 0; /* body sits directly under header */
        }}
        .md-root a {{
            color: {link_fg};
            text-decoration: underline;
        }}

        /* headings */
        .md-root h1, .md-root h2, .md-root h3, .md-root h4, .md-root h5, .md-root h6 {{
            color: {text_fg};
            font-weight: 600;
            line-height: 1.25;
            margin: 0.6em 0 0.3em;
        }}
        .md-root h1 {{ font-size: 1.25em; }}
        .md-root h2 {{ font-size: 1.18em; }}
        .md-root h3 {{ font-size: 1.12em; }}
        .md-root h4 {{ font-size: 1.06em; }}
        .md-root h5 {{ font-size: 1.03em; }}
        .md-root h6 {{ font-size: 1.00em; }}

        /* tighter body spacing */
        .md-root p {{ margin: 0.3em 0; }}
        .md-root ul, .md-root ol {{ margin: 0.3em 0 0.3em 1.2em; }}

        /* Enhanced code blocks */
        .md-root pre {{
            background   : {code_bg};
            border       : 2px solid {code_border};
            border-radius: 8px;
            padding      : 12px 16px;
            white-space  : pre;
            font-family  : 'Cascadia Code', 'Consolas', 'Courier New', monospace;
            margin       : 0.5em 0;
            font-size    : 0.95em;
            overflow-x   : auto;
            box-shadow   : 0 2px 4px {code_block_shadow};
            color        : {code_fg};
            line-height  : 1.6;
            border-left: 4px solid {"#4ea1ff" if dark else "#0066cc"};
        }}

        .md-root code {{
            background   : {inline_code_bg};
            border       : 1px solid {code_border};
            border-radius: 4px;
            padding      : 2px 6px;
            font-family  : 'Cascadia Code', 'Consolas', 'Courier New', monospace;
            font-size    : 0.92em;
            color        : {code_fg};
            font-weight  : 500;
            white-space  : nowrap;
        }}

        .md-root pre code {{
            background   : transparent;
            border       : none;
            padding      : 0;
            font-size    : 1em;
            font-weight  : normal;
            white-space  : pre;
        }}

        .md-root .codehilite {{
            background   : {code_bg};
            border       : 2px solid {code_border};
            border-left  : 4px solid {"#4ea1ff" if dark else "#0066cc"};
            border-radius: 8px;
            padding      : 12px 16px;
            margin       : 0.5em 0;
            overflow-x   : auto;
            box-shadow   : 0 2px 4px {code_block_shadow};
        }}

        .md-root table {{ border-collapse: collapse; width: 100%; margin: 0.35em 0; }}
        .md-root th, .md-root td {{ border: 1px solid {subtle_border}; padding: 6px 8px; }}
        .md-root th {{ background: {tbl_header_bg}; }}

        .md-root blockquote {{
            margin      : 0.35em 0;
            padding     : 6px 10px;
            border-left : 4px solid {quote_border};
            background  : {quote_bg};
        }}
        .md-root hr {{
            border: none;
            border-top: 1px solid {subtle_border};
            margin: 8px 0;
        }}

        .md-root img {{ max-width: 100%; height: auto; }}
        """
        self.conversationView.document().setDefaultStyleSheet(css)

    def _build_markdown_converter(self) -> None:
        dark = self.is_dark_mode()
        # Use more contrasting syntax highlighting styles
        style_name = "monokai" if dark else "friendly"  # Changed from native/default
        self._md = markdown.Markdown(
            extensions=[
                "extra",          # fenced_code, tables, etc.
                "sane_lists",
                "smarty",
                "toc",
                "codehilite"
            ],
            extension_configs={
                "codehilite": {
                    "guess_lang":     True,  # Changed to True for better syntax detection
                    "pygments_style": style_name,
                    "linenums":       False,
                    "noclasses":      True   # inline styles so Qt renders nicely
                }
            },
            output_format="xhtml"
        )

    def render_markdown_to_html(self, text: str) -> str:
        text = self._normalize_markdown_lists(text)
        self._md.reset()
        html_out = self._md.convert(text or "")
        return self._post_process_links_and_images(html_out)

    def _normalize_markdown_lists(self, text: str) -> str:
        if not text:
            return text
        # Normalize newlines
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Ensure a blank line before any top-level list (ordered or unordered) if preceded by text
        text = re.sub(r'([^\n])\n(- |\d+\.)', r'\1\n\n\2', text)

        # Expand 2‑space indents before "-" to 4 spaces for nested lists (sane_lists prefers 4)
        text = re.sub(r'(?m)^( {2})(- )', r'    \2', text)

        # Likewise for numbered nested items written with 2 spaces
        text = re.sub(r'(?m)^( {2})(\d+\.) ', r'    \2 ', text)

        # Remove accidental double spaces after dash
        text = re.sub(r'(?m)^(-)  ([^\s])', r'\1 \2', text)

        # Guarantee a trailing newline so final list renders completely
        if not text.endswith("\n"):
            text += "\n"
        return text

    def _post_process_links_and_images(self, html_in: str) -> str:
        try:
            soup = BeautifulSoup(html_in, "html.parser")

            # Fix anchor hrefs
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href.startswith(("http://", "https://")):
                    continue
                href = re.sub(r'^sandbox:[/\\](?:mnt[/\\]data[/\\])?', '', href)
                local_path = os.path.normpath(os.path.join(self.file_path, href))
                a["href"] = local_path
                a["download"] = os.path.basename(local_path)

            # Fix images
            for img in soup.find_all("img", src=True):
                src = img["src"].strip()
                if src.startswith(("http://", "https://", "data:")):
                    continue
                src = re.sub(r'^sandbox:[/\\](?:mnt[/\\]data[/\\])?', '', src)
                local_img = os.path.normpath(os.path.join(self.file_path, src))
                img["src"] = QUrl.fromLocalFile(local_img).toString()

            return str(soup)
        except Exception as e:
            logger.warning(f"Link/image post-process failed: {e}")
            return html_in

    def reset_for_new_thread(self) -> None:
        """Clear all per-thread UI state so toggles/streams don't leak between threads."""
        with self._lock:
            self._history.clear()
            self.text_to_url_map = {}

            self.streaming_buffer.clear()
            self.stream_snapshot.clear()
            self.stream_regions.clear()
            self.is_assistant_streaming.clear()

            self.conversationView.clear()

    def append_conversation_messages(self, messages: List[ConversationMessage]):
        logger.info(f"Appending full conversation: {len(messages)} messages to the conversation view")
        # New thread load: fully reset prior UI/history to avoid leakage
        self.reset_for_new_thread()
        for message in reversed(messages):
            self.append_conversation_message(message, full_messages_append=True)

    def append_conversation_message(self, message: ConversationMessage, full_messages_append=False):
        if message.text_message:
            text_message = message.text_message
            color = 'blue' if message.sender == "user" else ('#D3D3D3' if self.is_dark_mode() else 'black')
            self._history.append({"kind": "text", "sender": message.sender, "color": color, "content": text_message.content})
            self._render_text_item(message.sender, color, text_message.content)

        if len(message.file_messages) > 0:
            for file_message in message.file_messages:
                file_path = file_message.retrieve_file(self.file_path)
                if file_path:
                    link_text = os.path.basename(file_path)
                    info = f"File saved: [{link_text}]({link_text})"
                    self._history.append({"kind": "text", "sender": message.sender, "color": "green", "content": info})
                    self._render_text_item(message.sender, "green", info)

        if len(message.image_messages) > 0:
            for image_message in message.image_messages:
                image_path = image_message.retrieve_image(self.file_path)
                if image_path:
                    self._history.append({"kind": "image", "path": image_path})
                    self._render_image_item(image_path)

        self.scroll_to_bottom()

    def convert_image_to_base64(self, image_path):
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode()
        return encoded_string

    def append_image(self, image_path):
        # public helper
        self._history.append({"kind": "image", "path": image_path})
        self._render_image_item(image_path)
        self.scroll_to_bottom()

    def append_message(self, sender, message, color='black', full_messages_append=False):
        # record
        self._history.append({"kind": "text", "sender": sender, "color": color, "content": message})

        with self._lock:
            if self.is_any_assistant_streaming() and sender == "user" and not full_messages_append:
                # Save and remove any in-progress assistant stream BEFORE showing the user message.
                # Do NOT restore here; new assistant answer will start a fresh stream.
                self.clear_and_save_assistant_streaming()
            elif self.is_any_assistant_streaming() and sender != "user":
                self.clear_assistant_streaming(sender)

            self._render_text_item(sender, color, message)
            self.scroll_to_bottom()

            # Intentionally no restore here to avoid flash.

    def _render_text_item(self, sender: str, color: str, message: str) -> None:
        role_class = "user" if sender and sender.lower() == "user" else "assistant"

        header_html = (
            f"<div class='msg-header'>"
            f"<span class='sender {role_class}' style='color:{color};'>{html.escape(sender)}</span>"
            f"</div>"
        )

        if self._markdown_enabled:
            body_html = f"<div class='md-root'>{self.render_markdown_to_html(message)}</div>"
        else:
            body_html = f"<div class='md-root'><pre>{html.escape(message)}</pre></div>"

        message_html = f"<div class='message'>{header_html}{body_html}</div>"

        self.conversationView.moveCursor(QTextCursor.End)
        # Insert the composed message block and one line break
        self.conversationView.insertHtml(message_html + "<br>")

    def _render_image_item(self, image_path: str) -> None:
        base64_image = self.convert_image_to_base64(image_path)
        self.conversationView.moveCursor(QTextCursor.End)
        image_html = f"<img src='data:image/png;base64,{base64_image}' alt='Image' style='width:100px; height:auto;'>"
        self.conversationView.insertHtml(image_html)
        # single break instead of two to reduce vertical spacing
        self.conversationView.insertHtml("<br>")

    def append_message_chunk(self, sender, message_chunk, is_start_of_message):
        with self._lock:
            # New stream: drop any stale snapshot to prevent a later restore/flash.
            if is_start_of_message:
                self.stream_snapshot.pop(sender, None)

            if is_start_of_message or self.is_assistant_streaming[sender] != AssistantStreamingState.STREAMING:
                # Start header and create an empty streaming region we will update
                self.conversationView.moveCursor(QTextCursor.End)
                header_color = "#D3D3D3" if self.is_dark_mode() else "black"
                role_class = "user" if sender and sender.lower() == "user" else "assistant"
                header_html = (
                    f"<div class='msg-header'>"
                    f"<span class='sender {role_class}' style='color:{header_color};'>{html.escape(sender)}</span>"
                    f"</div>"
                )

                cursor = self.conversationView.textCursor()
                header_start = cursor.position()
                # insert header (message wrapper will be completed by the streaming body)
                self.conversationView.insertHtml(header_html)
                # region starts after header
                start_cursor = self.conversationView.textCursor()
                start_pos = start_cursor.position()
                # insert empty placeholder to establish an end pos
                self.conversationView.insertHtml("")  # placeholder for streaming body
                end_pos = self.conversationView.textCursor().position()
                self.stream_regions[sender] = {"header_start": header_start, "start": start_pos, "end": end_pos}
                self.streaming_buffer[sender].clear()

            # Append the new chunk and (re)render region
            self.streaming_buffer[sender].append(message_chunk)
            self.is_assistant_streaming[sender] = AssistantStreamingState.STREAMING
            self._update_stream_region(sender)
            self.scroll_to_bottom()

    def _update_stream_region(self, sender: str) -> None:
        region = self.stream_regions.get(sender)
        if not region:
            return
        text = "".join(self.streaming_buffer[sender])
        if self._markdown_enabled:
            inner = self.render_markdown_to_html(text)
        else:
            inner = f"<pre>{html.escape(text)}</pre>"
        body_html = f"<div class='md-root'>{inner}</div>"

        cursor = QTextCursor(self.conversationView.document())
        cursor.setPosition(region["start"])
        cursor.setPosition(region["end"], QTextCursor.KeepAnchor)
        cursor.insertHtml(body_html)
        region["end"] = cursor.position()
        self.stream_regions[sender] = region

    def clear_assistant_streaming(self, assistant_name):
        with self._lock:
            self.is_assistant_streaming[assistant_name] = AssistantStreamingState.NOT_STREAMING
            self.stream_snapshot[assistant_name] = ""
            self.streaming_buffer[assistant_name].clear()
            self.stream_regions.pop(assistant_name, None)

    def is_any_assistant_streaming(self):
        with self._lock:
            return any(state == AssistantStreamingState.STREAMING for state in self.is_assistant_streaming.values())

    def restore_assistant_streaming(self):
        for assistant_name in list(self.is_assistant_streaming.keys()):
            if self.stream_snapshot[assistant_name]:
                # Recreate header and region, then render snapshot
                self.conversationView.moveCursor(QTextCursor.End)
                header_color = "#D3D3D3" if self.is_dark_mode() else "black"
                header_html = f"<span class='sender' style='color:{header_color};'>{html.escape(assistant_name)}:</span> "
                cursor = self.conversationView.textCursor()
                header_start = cursor.position()
                self.conversationView.insertHtml(header_html)
                start_pos = self.conversationView.textCursor().position()
                self.conversationView.insertHtml("")
                end_pos = self.conversationView.textCursor().position()
                self.stream_regions[assistant_name] = {"header_start": header_start, "start": start_pos, "end": end_pos}
                self.streaming_buffer[assistant_name] = [self.stream_snapshot[assistant_name]]
                self._update_stream_region(assistant_name)
                del self.stream_snapshot[assistant_name]
                self.is_assistant_streaming[assistant_name] = AssistantStreamingState.STREAMING

    def clear_and_save_assistant_streaming(self):
        # Save and remove the currently streaming region from the view
        for assistant_name, state in list(self.is_assistant_streaming.items()):
            if state == AssistantStreamingState.STREAMING:
                current_streamed_content = "".join(self.streaming_buffer[assistant_name])
                self.stream_snapshot[assistant_name] = current_streamed_content

                region = self.stream_regions.get(assistant_name)
                if region:
                    cursor = QTextCursor(self.conversationView.document())
                    cursor.setPosition(region["header_start"])
                    cursor.setPosition(region["end"], QTextCursor.KeepAnchor)
                    cursor.insertHtml("")  # remove header+streamed content
                    self.stream_regions.pop(assistant_name, None)

                self.streaming_buffer[assistant_name].clear()
                # Mark NOT_STREAMING so we won't restore automatically after user messages.
                self.is_assistant_streaming[assistant_name] = AssistantStreamingState.NOT_STREAMING

    def clear_selected_text_from_conversation(self, assistant_name, selected_text):
        # No longer used with region-based streaming; kept for compatibility.
        pass

    def scroll_to_bottom(self):
        scrollbar = self.conversationView.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.conversationView.update()

    def get_markdown_enabled(self) -> bool:
        return self._markdown_enabled

    def set_markdown_enabled(self, enabled: bool) -> None:
        if self._markdown_enabled == enabled:
            return
        with self._lock:
            had_streaming = self.is_any_assistant_streaming()
            if had_streaming:
                self.clear_and_save_assistant_streaming()

            self._markdown_enabled = enabled
            self._build_markdown_converter()
            self.apply_document_theme_styles()

            # Only re-render if this thread currently has content in the view or history.
            doc_has_content = bool(self.conversationView.document().toPlainText().strip())
            if self._history or doc_has_content:
                self._rerender_history()

            if had_streaming:
                self.restore_assistant_streaming()

    def toggle_markdown_rendering(self) -> None:
        self.set_markdown_enabled(not self._markdown_enabled)

    def _rerender_history(self) -> None:
        self.conversationView.clear()
        for item in self._history:
            self._render_item(item)
        self.scroll_to_bottom()

    def _render_item(self, item: dict) -> None:
        kind = item.get("kind")
        if kind == "text":
            self._render_text_item(item["sender"], item["color"], item["content"])
        elif kind == "image":
            self._render_image_item(item["path"])
