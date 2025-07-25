# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.assistant_config import AssistantConfig
from azure.ai.assistant.management.assistant_client_callbacks import AssistantClientCallbacks
from azure.ai.assistant.management.message import ConversationMessage
from azure.ai.assistant.management.text_message import TextMessage
from azure.ai.assistant.management.base_chat_assistant_client import BaseChatAssistantClient
from azure.ai.assistant.management.exceptions import EngineError, InvalidJSONError
from azure.ai.assistant.management.logger_module import logger

from typing import Optional
from datetime import datetime
import json, uuid, yaml


class ChatAssistantClient(BaseChatAssistantClient):
    """
    A class that manages an chat assistant client.

    :param config_json: The configuration data to use to create the chat client.
    :type config_json: str
    :param callbacks: The callbacks to use for the assistant client.
    :type callbacks: Optional[AssistantClientCallbacks]
    :param is_create: A flag to indicate if the assistant client is being created.
    :type is_create: bool
    :param timeout: The HTTP request timeout in seconds.
    :type timeout: Optional[float]
    :param client_args: Additional keyword arguments for configuring the AI client.
    :type client_args: Dict
    """
    def __init__(
            self, 
            config_json: str,
            callbacks: Optional[AssistantClientCallbacks] = None,
            is_create: bool = True,
            timeout: Optional[float] = None,
            **client_args
    ) -> None:
        super().__init__(config_json, callbacks, **client_args)
        self._init_chat_assistant_client(self._config_data, is_create, timeout=timeout)

    @classmethod
    def from_json(
        cls,
        config_json: str,
        callbacks: Optional[AssistantClientCallbacks] = None,
        timeout: Optional[float] = None,
        **client_args
    ) -> "ChatAssistantClient":
        """
        Creates a ChatAssistantClient instance from JSON configuration data.

        :param config_json: JSON string containing the configuration for the chat assistant.
        :type config_json: str
        :param callbacks: Optional callbacks for the chat assistant client.
        :type callbacks: Optional[AssistantClientCallbacks]
        :param timeout: Optional timeout for HTTP requests.
        :type timeout: Optional[float]
        :param client_args: Additional keyword arguments for configuring the AI client.
        :type client_args: Dict

        :return: An instance of ChatAssistantClient.
        :rtype: ChatAssistantClient
        """
        try:
            config_data = json.loads(config_json)
            is_create = not ("assistant_id" in config_data and config_data["assistant_id"])
            return cls(config_json=config_json, callbacks=callbacks, is_create=is_create, timeout=timeout, **client_args)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON format: {e}")
            raise InvalidJSONError(f"Invalid JSON format: {e}")

    @classmethod
    def from_yaml(
        cls,
        config_yaml: str,
        callbacks: Optional[AssistantClientCallbacks] = None,
        timeout: Optional[float] = None,
        **client_args
    ) -> "ChatAssistantClient":
        """
        Creates an ChatAssistantClient instance from YAML configuration data.

        :param config_yaml: YAML string containing the configuration for the chat assistant.
        :type config_yaml: str
        :param callbacks: Optional callbacks for the chat assistant client.
        :type callbacks: Optional[AssistantClientCallbacks]
        :param timeout: Optional timeout for HTTP requests.
        :type timeout: Optional[float]
        :param client_args: Additional keyword arguments for configuring the AI client.
        :type client_args: Dict

        :return: An instance of ChatAssistantClient.
        :rtype: ChatAssistantClient
        """
        try:
            config_data = yaml.safe_load(config_yaml)
            config_json = json.dumps(config_data)
            return cls.from_json(config_json, callbacks, timeout, **client_args)
        except yaml.YAMLError as e:
            logger.error(f"Invalid YAML format: {e}")
            raise EngineError(f"Invalid YAML format: {e}")

    @classmethod
    def from_config(
        cls,
        config: AssistantConfig,
        callbacks: Optional[AssistantClientCallbacks] = None,
        timeout: Optional[float] = None,
        **client_args
    ) -> "ChatAssistantClient":
        """
        Creates a ChatAssistantClient instance from an AssistantConfig object.

        :param config: AssistantConfig object containing the configuration for the chat assistant.
        :type config: AssistantConfig
        :param callbacks: Optional callbacks for the chat assistant client.
        :type callbacks: Optional[AssistantClientCallbacks]
        :param timeout: Optional timeout for HTTP requests.
        :type timeout: Optional[float]
        :param client_args: Additional keyword arguments for configuring the AI client.
        :type client_args: Dict

        :return: An instance of ChatAssistantClient.
        :rtype: ChatAssistantClient
        """
        try:
            config_json = config.to_json()
            return cls.from_json(config_json, callbacks, timeout, **client_args)
        except Exception as e:
            logger.error(f"Failed to create chat client from config: {e}")
            raise EngineError(f"Failed to create chat client from config: {e}")

    def purge(
            self,
            timeout: Optional[float] = None
    )-> None:
        """
        Purges the chat assistant from the local configuration.

        :param timeout: The HTTP request timeout in seconds.
        :type timeout: Optional[float]

        :return: None
        :rtype: None
        """
        self._purge(timeout)

    def process_messages(
            self, 
            thread_name: Optional[str] = None,
            user_request: Optional[str] = None,
            additional_instructions: Optional[str] = None,
            timeout: Optional[float] = None,
            stream: Optional[bool] = False
    ) -> Optional[str]:
        """
        Process the messages in a given thread.

        :param thread_name: The name of the thread to process.
        :type thread_name: Optional[str]
        :param user_request: The user request to process.
        :type user_request: Optional[str]
        :param additional_instructions: Additional instructions for the assistant.
        :type additional_instructions: Optional[str]
        :param timeout: The HTTP request timeout in seconds.
        :type timeout: Optional[float]
        :param stream: A flag indicating if the response should be streamed.
        :type stream: Optional[bool]
        :return: The response from the assistant.
        :rtype: Optional[str]
        """
        # Ensure at least one of thread_name or user_request is provided
        if thread_name is None and user_request is None:
            raise ValueError("Either thread_name or user_request must be provided.")

        try:
            logger.info("Process messages for chat assistant")

            if additional_instructions:
                self._messages.append({"role": "developer", "content": additional_instructions})

            if thread_name:
                max_text_messages = (
                    self._assistant_config.text_completion_config.max_text_messages
                    if self._assistant_config.text_completion_config
                    else None
                )
                conversation = self._conversation_thread_client.retrieve_conversation(
                    thread_name=thread_name,
                    max_text_messages=max_text_messages
                )
                self._parse_conversation_messages(conversation.messages)
            elif user_request:
                self._messages.append({"role": "user", "content": user_request})

            # call the start_run callback
            run_start_time = str(datetime.now())
            run_id = str(uuid.uuid4())
            if thread_name:
                conversation = self._conversation_thread_client.retrieve_conversation(thread_name)
                user_request = conversation.get_last_text_message("user").content
            self._callbacks.on_run_start(self._name, run_id, run_start_time, user_request)

            continue_processing = True
            if self._cancel_run_requested.is_set():
                self._cancel_run_requested.clear()

            response = None
            text_config = self._assistant_config.text_completion_config
            model = self._assistant_config.model

            temperature = text_config.temperature if text_config else None
            seed = text_config.seed if text_config else None
            frequency_penalty = text_config.frequency_penalty if text_config else None
            max_tokens = text_config.max_tokens if text_config else None
            presence_penalty = text_config.presence_penalty if text_config else None
            top_p = text_config.top_p if text_config else None
            response_format = {"type": text_config.response_format} if text_config else None
            reasoning_effort = text_config.reasoning_effort if text_config else None

            while continue_processing:
                if self._cancel_run_requested.is_set():
                    logger.info("User input processing cancellation requested.")
                    self._cancel_run_requested.clear()
                    break

                try:
                    if not (model.startswith("o1") or model.startswith("o3") or model.startswith("o4")):
                        response = self._ai_client.chat.completions.create(
                            model=model,
                            messages=self._messages,
                            tools=self._tools,
                            tool_choice=None if self._tools is None else "auto",
                            stream=stream,
                            temperature=temperature,
                            seed=seed,
                            frequency_penalty=frequency_penalty,
                            max_tokens=max_tokens,
                            presence_penalty=presence_penalty,
                            response_format=response_format,
                            top_p=top_p,
                            timeout=timeout
                        )
                    else:
                        stream = False
                        response = self._ai_client.chat.completions.create(
                            model=model,
                            messages=self._messages,
                            tools=self._tools,
                            tool_choice=None if self._tools is None else "auto",
                            response_format=response_format,
                            reasoning_effort=reasoning_effort if reasoning_effort else None,
                            timeout=timeout
                        )
                except Exception as e:
                    err_str = str(e).lower()
                    if "developer" in err_str and "invalid_request_error" in err_str:
                        logger.warning("Model does not support 'developer' role. Falling back to 'system' role.")
                        # Convert all 'developer' role messages to 'system' role, preserving their content
                        for i, msg in enumerate(self._messages):
                            if msg.get("role") == "developer":
                                self._messages[i]["role"] = "system"

                        # Retry creation call after removing the "developer" role
                        if not (model.startswith("o1") or model.startswith("o3") or model.startswith("o4")):
                            response = self._ai_client.chat.completions.create(
                                model=model,
                                messages=self._messages,
                                tools=self._tools,
                                tool_choice=None if self._tools is None else "auto",
                                stream=stream,
                                temperature=temperature,
                                seed=seed,
                                frequency_penalty=frequency_penalty,
                                max_tokens=max_tokens,
                                presence_penalty=presence_penalty,
                                response_format=response_format,
                                top_p=top_p,
                                timeout=timeout
                            )
                        else:
                            stream = False
                            response = self._ai_client.chat.completions.create(
                                model=model,
                                messages=self._messages,
                                tools=self._tools,
                                tool_choice=None if self._tools is None else "auto",
                                response_format=response_format,
                                reasoning_effort=reasoning_effort if reasoning_effort else None,
                                timeout=timeout
                            )
                    else:
                        raise e

                # Handle streaming or non-streaming responses
                if response and stream:
                    continue_processing = self._handle_streaming_response(response, thread_name, run_id)
                elif response:
                    continue_processing = self._handle_non_streaming_response(response, thread_name, run_id)
                else:
                    # If there's no response, stop the loop
                    continue_processing = False

            # Reset the system message
            self._reset_system_messages(self._assistant_config)

            self._callbacks.on_run_update(self._name, run_id, "completed", thread_name)

            run_end_time = str(datetime.now())
            # If there's no thread name and no streaming, return the response
            if not thread_name and not stream and response:
                response_message = response.choices[0].message.content
                self._callbacks.on_run_end(self._name, run_id, run_end_time, thread_name, response_message)
                return response_message

            self._callbacks.on_run_end(self._name, run_id, run_end_time, thread_name)

        except Exception as e:
            logger.error(f"Error occurred during processing run: {e}")
            raise EngineError(f"Error occurred during processing run: {e}")

    def _handle_non_streaming_response(self, response, thread_name, run_id):
        response_message = response.choices[0].message
        self._messages.append(response_message)

        if response_message.content:
            # extend conversation with assistant's reply
            if thread_name:
                self._conversation_thread_client.create_conversation_thread_message(
                    response_message.content,
                    thread_name,
                    metadata={"chat_assistant": self._name}
                )
            return False

        tool_calls = response_message.tool_calls
        if tool_calls != None:
            for tool_call in tool_calls:
                function_response = self._handle_function_call(tool_call.function.name, tool_call.function.arguments)
                self._callbacks.on_function_call_processed(self._name, run_id, tool_call.function.name, tool_call.function.arguments, str(function_response))
                self._messages.append(
                    {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": tool_call.function.name,
                        "content": function_response,
                    }
                )
            return True

    def _handle_streaming_response(self, response, thread_name, run_id):
        tool_calls, collected_messages = self._process_response_chunks(response, thread_name, run_id)
        self._process_tool_calls(tool_calls, run_id)
        self._update_conversation_with_messages(collected_messages, thread_name)
        return bool(tool_calls)  # Return True if there were tool calls processed, otherwise False

    def _process_response_chunks(self, response, thread_name, run_id):
        tool_calls = []
        collected_messages = []
        is_first_message = True

        for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                message : ConversationMessage = ConversationMessage(self.ai_client)
                message.text_message = TextMessage(delta.content)
                self._callbacks.on_run_update(self._name, run_id, "streaming", thread_name, is_first_message, message)
                collected_messages.append(delta.content)
                is_first_message = False
            if delta and delta.tool_calls:
                tool_calls = self._append_tool_calls(tool_calls, delta.tool_calls)

        return tool_calls, collected_messages

    def _process_tool_calls(self, tool_calls, run_id):
        if tool_calls:
            logger.info(f"Tool calls: {tool_calls}")
            self._messages.append({
                "tool_calls": tool_calls,
                "role": 'assistant',
            })
    
        for tool_call in tool_calls:
            function_response = self._handle_function_call(
                tool_call['function']['name'],
                tool_call['function']['arguments']
            )
            self._callbacks.on_function_call_processed(
                self._name, run_id, 
                tool_call['function']['name'], 
                tool_call['function']['arguments'], 
                str(function_response)
            )

            # Appending the processed tool call and its response to self._messages
            self._messages.append({
                "tool_call_id": tool_call['id'],
                "role": "tool",
                "name": tool_call['function']['name'],
                "content": str(function_response),  # Ensure content is stringified if necessary
            })

    def _update_conversation_with_messages(self, collected_messages, thread_name):
        full_response = ''.join(filter(None, collected_messages))
        if full_response and thread_name:
            self._conversation_thread_client.create_conversation_thread_message(
                message=full_response, 
                thread_name=thread_name, 
                metadata={"chat_assistant": self._name}
            )
            logger.info("Messages updated in conversation.")