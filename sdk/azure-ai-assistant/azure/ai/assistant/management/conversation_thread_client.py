# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

import threading
from typing import Union, List, Optional, Tuple

from azure.ai.assistant.management.ai_client_factory import AIClientFactory, AIClient
from azure.ai.assistant.management.ai_client_type import AIClientType
from azure.ai.assistant.management.attachment import Attachment, AttachmentType
from azure.ai.assistant.management.conversation import Conversation
from azure.ai.assistant.management.conversation_thread_config import ConversationThreadConfig
from azure.ai.assistant.management.message_utils import _extract_image_urls
from azure.ai.assistant.management.assistant_config_manager import AssistantConfigManager
from azure.ai.assistant.management.exceptions import EngineError
from azure.ai.assistant.management.logger_module import logger

from azure.ai.agents.models import ThreadMessage
from openai.types.beta.threads import Message


class ConversationThreadClient:
    _instances = {}
    # Lock that protects the _instances dict itself:
    _instances_lock = threading.Lock()
    """
    A class to manage conversation threads.

    :param ai_client_type: The type of the AI client to use.
    :type ai_client_type: AIClientType
    :param config_folder: The folder to save the thread config to.
    :type config_folder: str, optional
    :param client_args: The arguments to pass to the AI client.
    :type client_args: dict
    """
    def __init_private(
        self,
        ai_client_type: AIClientType,
        config_folder: Optional[str] = None,
        **client_args
    ):
        self._ai_client_type = ai_client_type
        self._config_folder = config_folder

        # Underlying AI client from your factory
        self._ai_client: AIClient = AIClientFactory.get_instance().get_client(
            self._ai_client_type,
            **client_args
        )

        self._thread_config = ConversationThreadConfig(self._ai_client_type, self._config_folder)
        self._assistant_config_manager = AssistantConfigManager.get_instance()

        # Instance-level lock to protect reads/writes in this object
        self._lock = threading.RLock()

    @classmethod
    def get_instance(
        cls,
        ai_client_type: AIClientType,
        config_folder: Optional[str] = None,
        **client_args
    ) -> "ConversationThreadClient":
        """
        Get the singleton instance of ConversationThreadClient.

        :param ai_client_type: The type of the AI client to use.
        :type ai_client_type: AIClientType
        :param config_folder: The folder to save the thread config to.
        :type config_folder: str, optional
        :param client_args: The arguments to pass to the AI client.
        :type client_args: dict

        :return: The singleton instance of the ConversationThreadClient.
        :rtype: ConversationThreadClient
        """
        if ai_client_type not in cls._instances:
            with cls._instances_lock:
                if ai_client_type not in cls._instances:
                    instance = cls.__new__(cls)
                    instance.__init_private(ai_client_type, config_folder, **client_args)
                    cls._instances[ai_client_type] = instance
        return cls._instances[ai_client_type]

    def create_conversation_thread(
            self,
            timeout: Optional[float] = None
    ) -> str:
        """
        Creates a conversation thread.

        :param timeout: The HTTP request timeout in seconds.
        :type timeout: float, optional

        :return: The name of the created thread.
        :rtype: str
        """
        with self._lock:
            try:
                thread = self._create_thread_impl(timeout=timeout)
                # Record the new thread in our thread config
                self._thread_config.add_thread(thread.id, "New Thread")
                thread_name = self._thread_config.get_thread_name_by_id(thread.id)
                logger.info(f"Created thread Id: {thread.id} for thread name: {thread_name}")
                return thread_name
            except Exception as e:
                logger.error(f"Failed to create thread: {e}")
                raise EngineError(f"Failed to create thread: {e}")

    def set_current_conversation_thread(
            self,
            thread_name: str
    ) -> None:
        """
        Sets the current conversation thread.

        :param thread_name: The unique name of the thread to set as the current thread.
        :type thread_name: str
        """
        with self._lock:
            thread_id = self._thread_config.get_thread_id_by_name(thread_name)
            logger.info(f"Setting current thread name: {thread_name} to thread ID: {thread_id}")
            self._thread_config.set_current_thread_by_name(thread_name)

    def is_current_conversation_thread(
            self,
            thread_name: str
    ) -> bool:
        """
        Checks if the given thread name is the current thread for the given assistant name.

        :param thread_name: The unique name of the thread to check.
        :type thread_name: str

        :return: True if the given thread name is the current thread, False otherwise.
        :rtype: bool
        """
        with self._lock:
            thread_id = self._thread_config.get_thread_id_by_name(thread_name)
            return thread_id == self._thread_config.get_current_thread_id()

    def set_conversation_thread_name(
            self,
            new_thread_name: str,
            thread_name: str
    ) -> str:
        """
        Sets the current thread name.

        :param new_thread_name: The new name to set for the thread.
        :type new_thread_name: str
        :param thread_name: The unique name of the thread to set the new name for.
        :type thread_name: str

        :return: The updated thread name.
        :rtype: str
        """
        with self._lock:
            thread_id = self._thread_config.get_thread_id_by_name(thread_name)
            self._thread_config.update_thread_name(thread_id, new_thread_name)
            updated_thread_name = self._thread_config.get_thread_name_by_id(thread_id)
            return updated_thread_name

    def retrieve_conversation(
            self,
            thread_name: str,
            timeout: Optional[float] = None,
            max_text_messages: Optional[int] = None
    ) -> Conversation:
        """
        Retrieves the conversation from the given thread name.

        :param thread_name: The name of the thread to retrieve the conversation from.
        :type thread_name: str
        :param timeout: The HTTP request timeout in seconds.
        :type timeout: float, optional
        :param max_text_messages: Specifies the maximum number of the most recent text messages to retrieve. If None, all messages are retrieved.
        :type max_text_messages: int, optional

        :return: The conversation.
        :rtype: Conversation
        """
        with self._lock:
            try:
                messages = self._get_conversation_thread_messages(thread_name, timeout)
                logger.info(f"Retrieved messages content: {messages}")
                conversation = Conversation(self._ai_client, messages, max_text_messages)
                return conversation
            except Exception as e:
                error_message = f"Error retrieving messages: {e}"
                logger.error(error_message)
                raise EngineError(error_message)

    def create_conversation_thread_message(
            self,
            message: str,
            thread_name: str,
            role: Optional[str] = "user",
            attachments: Optional[List[Attachment]] = None,
            timeout: Optional[float] = None,
            metadata: Optional[dict] = None
    ) -> None:
        """
        Creates a new assistant thread message.

        :param message: The message to create.
        :type message: str
        :param thread_name: The unique name of the thread to create the message in.
        :type thread_name: str
        :param role: The role of the message sender. Default is "user".
        :type role: str, optional
        :param attachments: The list of attachments to add to the message.
        :type attachments: List[Attachment], optional
        :param timeout: The HTTP request timeout in seconds.
        :type timeout: float, optional
        """
        with self._lock:
            thread_id = None
            image_attachments = []
            try:
                thread_id = self._thread_config.get_thread_id_by_name(thread_name)

                # Handle attachments if any
                updated_attachments, image_attachments = (
                    self._update_message_attachments(thread_id, attachments)
                    if attachments is not None
                    else ([], [])
                )

                content = [{"type": "text", "text": message}]

                # Inline image URLs from message text
                image_urls = _extract_image_urls(message)
                for image_url in image_urls:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": "high"}
                    })

                # If we have image attachments
                for img_attachment in image_attachments:
                    content.append({
                        "type": "image_file",
                        "image_file": {
                            "file_id": img_attachment.file_id,
                            "detail": "high"
                        }
                    })

                self._create_message_impl(
                    thread_id=thread_id,
                    role=role,
                    content=content,
                    attachments=updated_attachments,
                    metadata=metadata,
                    timeout=timeout
                )
                logger.info(f"Created message: {message} in thread: {thread_name}")
            except Exception as e:
                logger.error(f"Failed to create message: {message} in thread: {thread_name}: {e}")
                raise EngineError(f"Failed to create message: {message} in thread: {thread_name}: {e}")

    def delete_conversation_thread(
            self,
            thread_name: str,
            timeout: Optional[float] = None
    ) -> None:
        """
        Deletes the conversation thread with the given thread name.

        :param thread_name: The unique name of the thread to delete.
        :type thread_name: str
        :param timeout: The HTTP request timeout in seconds.
        :type timeout: float, optional
        """
        with self._lock:
            try:
                thread_id = self._thread_config.get_thread_id_by_name(thread_name)
                logger.info(f"Deleting thread with ID: {thread_id}, thread name: {thread_name}")
                self._thread_config.remove_thread_by_id(thread_id)
                self._delete_thread_impl(thread_id, timeout=timeout)
                logger.info(f"Deleted thread with ID: {thread_id}, thread name: {thread_name}")
            except Exception as e:
                logger.error(f"Failed to delete thread with ID: {thread_id}, thread name: {thread_name}: {e}")
                raise EngineError(f"Failed to delete thread with ID: {thread_id} thread name: {thread_name}: {e}")

    def get_conversation_threads(self) -> list:
        """
        Retrieves all conversation threads.

        :return: The conversation threads.
        :rtype: list
        """
        with self._lock:
            try:
                threads = self._thread_config.get_all_threads()
                return threads
            except Exception as e:
                logger.error(f"Failed to retrieve threads: {e}")
                raise EngineError(f"Failed to retrieve threads: {e}")

    def get_config(self) -> ConversationThreadConfig:
        """
        Retrieves the thread config.

        :return: The threads config.
        :rtype: ConversationThreadConfig
        """
        with self._lock:
            try:
                return self._thread_config
            except Exception as e:
                logger.error(f"Failed to retrieve threads config: {e}")
                raise EngineError(f"Failed to retrieve threads config: {e}")

    def save_conversation_threads(self) -> None:
        """
        Saves the threads to json based on the AI client type.
        """
        with self._lock:
            logger.info(f"Save threads to json, ai_client_type: {self._ai_client_type}")
            self._thread_config.save_to_json()

    def delete_conversation_thread_message(
            self,
            thread_name: str,
            message_id: str,
            timeout: Optional[float] = None
    ) -> None:
        """
        Deletes a specific message from the conversation thread.

        :param thread_name: The name of the thread containing the message.
        :type thread_name: str
        :param message_id: The unique identifier of the message to delete.
        :type message_id: str
        :param timeout: Optional request timeout (for HTTP-based clients).
        :type timeout: Optional[float]

        This method locates the thread id using the thread config and delegates
        the actual delete operation to the provider-specific implementation.
        """
        with self._lock:
            try:
                thread_id = self._thread_config.get_thread_id_by_name(thread_name)
                logger.info(f"Deleting message id: {message_id} from thread id: {thread_id}, thread name: {thread_name}")
                # Delegate to provider-specific implementation
                self._delete_message_impl(thread_id=thread_id, message_id=message_id, timeout=timeout)
                logger.info(f"Deleted message id: {message_id} from thread id: {thread_id}, thread name: {thread_name}")
            except Exception as e:
                logger.error(f"Failed to delete message id: {message_id} from thread: {thread_name}: {e}")
                raise EngineError(f"Failed to delete message id: {message_id} from thread: {thread_name}: {e}")

    # -------------------------------------------------------------
    # Private / Internal methods:
    # -------------------------------------------------------------

    def _delete_message_impl(
        self,
        thread_id: str,
        message_id: str,
        timeout: Optional[float] = None
    ) -> None:
        """
        Provider-specific implementation to delete a message.

        For Azure AI Agents we attempt to use `agents.messages.delete`.
        For the OpenAI Beta Threads API we attempt to use
        `beta.threads.messages.delete(thread_id=..., message_id=...)`.

        This implementation is best-effort and will raise exceptions if the
        underlying client doesn't support message deletion.
        """
        if self._ai_client_type == AIClientType.AZURE_AI_AGENT:
            # Azure AI Agents: delete by message id (agents.messages.delete)
            # Note: exact SDK method name is assumed consistent with other methods used here.
            try:
                # Try the expected delete signature
                self._ai_client.agents.messages.delete(thread_id=thread_id, message_id=message_id)
            except Exception as ex:
                logger.error(f"Failed to delete message via beta threads API (fallback): {ex}")
                raise
        else:
            # OpenAI (beta threads): delete via beta.threads.messages.delete
            try:
                # Preferred: provide thread_id and message_id
                self._ai_client.beta.threads.messages.delete(thread_id=thread_id, message_id=message_id, timeout=timeout)
            except Exception as ex:
                logger.error(f"Failed to delete message via beta threads API (fallback): {ex}")
                raise

    def _create_thread_impl(self, timeout: Optional[float] = None):
        if self._ai_client_type == AIClientType.AZURE_AI_AGENT:
            return self._ai_client.agents.threads.create()
        else:
            return self._ai_client.beta.threads.create(timeout=timeout)

    def _delete_thread_impl(self, thread_id: str, timeout: Optional[float] = None):
        if self._ai_client_type == AIClientType.AZURE_AI_AGENT:
            self._ai_client.agents.threads.delete(thread_id=thread_id)
        else:
            self._ai_client.beta.threads.delete(thread_id=thread_id, timeout=timeout)

    def _get_conversation_thread_messages(
            self,
            thread_name: str,
            timeout: Optional[float] = None
    ) -> List[Union[ThreadMessage, Message]]:
        thread_id = self._thread_config.get_thread_id_by_name(thread_name)
        return self._list_messages_impl(thread_id, timeout)

    def _list_messages_impl(self, thread_id: str, timeout: Optional[float] = None) -> List[Union[ThreadMessage, Message]]:
        if self._ai_client_type == AIClientType.AZURE_AI_AGENT:
            resp = self._ai_client.agents.messages.list(thread_id=thread_id)
            return resp.data
        else:
            resp = self._ai_client.beta.threads.messages.list(thread_id=thread_id, timeout=timeout)
            return resp.data

    def _create_message_impl(
        self,
        thread_id: str,
        role: Optional[str],
        content: list,
        attachments: list,
        metadata: Optional[dict],
        timeout: Optional[float]
    ) -> None:
        if self._ai_client_type == AIClientType.AZURE_AI_AGENT:
            self._ai_client.agents.messages.create(
                thread_id=thread_id,
                role=role,
                content=content,
                attachments=attachments,
                metadata=metadata
            )
        else:
            self._ai_client.beta.threads.messages.create(
                thread_id=thread_id,
                role=role,
                content=content,
                attachments=attachments,
                metadata=metadata,
                timeout=timeout
            )

    # TODO: Change this to use the new Attachment class for Azure AI Agents
    def _update_message_attachments(
            self,
            thread_id: str,
            new_attachments: List[Attachment]
    ) -> Tuple[List[dict], List[Attachment]]:
        try:
            existing_attachments = self._thread_config.get_attachments_of_thread(thread_id)
            existing_attachments_by_id = {att.file_id: att for att in existing_attachments if att.file_id}

            all_updated_attachments = []
            image_attachments = []

            for attachment in new_attachments:
                file_id = attachment.file_id
                file_path = attachment.file_path
                attachment_type = attachment.attachment_type
                tool = attachment.tool

                if file_id is None:
                    # Need to upload
                    if attachment_type == AttachmentType.IMAGE_FILE and tool is None:
                        # Plain image — upload for immediate use but do NOT persist into the thread config.
                        # Images are intended to be processed inline (pasted into conversation),
                        # and we avoid persisting image-only attachments so they don't show paperclip later.
                        file_object = self._create_file_impl(file_path, purpose='vision')
                        attachment.file_id = file_object.id
                        image_attachments.append(attachment)
                    else:
                        # Probably a tool or other attachment (non-image): upload and persist to thread config.
                        file_object = self._create_file_impl(file_path, purpose='assistants')
                        attachment.file_id = file_object.id
                        all_updated_attachments.append(attachment)
                        self._thread_config.add_attachments_to_thread(thread_id, [attachment])
                else:
                    # We already have a file ID
                    current_attachment = existing_attachments_by_id.get(file_id)
                    if current_attachment and current_attachment != attachment:
                        self._thread_config.update_attachment_in_thread(thread_id, current_attachment)

                    if attachment_type == AttachmentType.IMAGE_FILE and tool is None:
                        image_attachments.append(current_attachment)
                    else:
                        all_updated_attachments.append(current_attachment)

            updated_attachments = [
                {
                    'file_id': att.file_id,
                    'tools': [att.tool.to_dict()] if att.tool else []
                }
                for att in all_updated_attachments
            ]
            return updated_attachments, image_attachments

        except Exception as e:
            logger.error(f"Failed to update attachments for thread {thread_id}: {str(e)}")
            raise

    def _create_file_impl(self, file_path: str, purpose: str, timeout: Optional[float] = None):
        if self._ai_client_type == AIClientType.AZURE_AI_AGENT:
            return self._ai_client.agents.files.upload(file=open(file_path, "rb"), purpose=purpose)
        else:
            return self._ai_client.files.create(file=open(file_path, "rb"), purpose=purpose, timeout=timeout)
