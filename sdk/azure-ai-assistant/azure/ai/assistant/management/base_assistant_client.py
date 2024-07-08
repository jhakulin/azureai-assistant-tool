# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.functions.system_function_mappings import system_functions
from azure.ai.assistant.management.assistant_config_manager import AssistantConfigManager
from azure.ai.assistant.management.assistant_config import AssistantConfig
from azure.ai.assistant.management.assistant_client_callbacks import AssistantClientCallbacks
from azure.ai.assistant.management.async_assistant_client_callbacks import AsyncAssistantClientCallbacks
from azure.ai.assistant.management.conversation_thread_client import ConversationThreadClient
from azure.ai.assistant.management.async_conversation_thread_client import AsyncConversationThreadClient
from azure.ai.assistant.management.ai_client_factory import AIClientType, AsyncAIClientType
from azure.ai.assistant.management.ai_client_factory import AIClientFactory
from azure.ai.assistant.management.exceptions import EngineError, InvalidJSONError
from azure.ai.assistant.management.logger_module import logger
from azure.ai.assistant.management.ai_client_azure_inference import AzureInferenceClient
from azure.ai.assistant.management.ai_client_azure_openai import AzureOpenAIClient
from azure.ai.assistant.management.ai_client_openai import OpenAIClient
from azure.ai.assistant.management.async_ai_client_azure_inference import AsyncAzureInferenceClient
from azure.ai.assistant.management.async_ai_client_azure_openai import AsyncAzureOpenAIClient
from azure.ai.assistant.management.async_ai_client_openai import AsyncOpenAIClient

from typing import Union

import re, yaml, copy
import json, importlib, sys, os
from typing import Optional
import threading


class BaseAssistantClient:
    """
    A base class for Assistant Clients.

    :param config_json: The configuration data to use to create the assistant client.
    :type config_json: str
    :param callbacks: The callbacks to use for the assistant client.
    :type callbacks: Optional[Union[AssistantClientCallbacks, AsyncAssistantClientCallbacks]]
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
            callbacks: Optional[Union[AssistantClientCallbacks, AsyncAssistantClientCallbacks]] = None,
            async_mode: bool = False,
            **client_args
        ) -> None:
        self._initialize_client(config_json, callbacks, async_mode, **client_args)

    def _initialize_client(
            self,
            config_json: str,
            callbacks: Optional[AssistantClientCallbacks],
            async_mode: Optional[bool] = False,
            **client_args
        ):
        try:
            self._config_data = json.loads(config_json)
            self._validate_config_data(self._config_data)
            self._name = self._config_data["name"]
            self._ai_client_type = self._get_ai_client_type(self._config_data["ai_client_type"], async_mode)
            self._ai_client : Union[OpenAIClient, AsyncOpenAIClient, AzureOpenAIClient, AsyncAzureOpenAIClient, AzureInferenceClient, AsyncAzureInferenceClient] = self._get_ai_client(self._ai_client_type, **self._config_data.get('ai_client_config'), **client_args) if self._ai_client_type == AIClientType.AZURE_INFERENCE else self._get_ai_client(self._ai_client_type, **client_args)
            config_folder = None
            if "config_folder" in self._config_data:
                config_folder = self._config_data["config_folder"]
            if async_mode:
                self._callbacks = callbacks if callbacks is not None else AsyncAssistantClientCallbacks()
                self._conversation_thread_client = AsyncConversationThreadClient.get_instance(AIClientType.OPEN_AI, config_folder=config_folder) if self._ai_client_type == AIClientType.AZURE_INFERENCE else AsyncConversationThreadClient.get_instance(self._ai_client_type, config_folder=config_folder)
            else:
                self._callbacks = callbacks if callbacks is not None else AssistantClientCallbacks()
                self._conversation_thread_client = ConversationThreadClient.get_instance(AIClientType.OPEN_AI, config_folder=config_folder) if self._ai_client_type == AIClientType.AZURE_INFERENCE else ConversationThreadClient.get_instance(self._ai_client_type, config_folder=config_folder)
            self._functions = {}
            self._assistant_config = AssistantConfig.from_dict(self._config_data)
            self._cancel_run_requested = threading.Event()
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON format: {e}")
            raise InvalidJSONError(f"Invalid JSON format: {e}")
        except Exception as e:
            logger.error(f"Failed to initialize assistant client: {e}")
            raise EngineError(f"Failed to initialize assistant client: {e}")

    def _validate_config_data(self, config_data: dict):
        if "name" not in config_data or not config_data["name"].strip():
            raise ValueError("The 'name' field in config_data cannot be empty")
        if "ai_client_type" not in config_data:
            raise ValueError("The 'ai_client_type' field is required in config_data")
        if "model" not in config_data:
            raise ValueError("The 'model' field is required in config_data")
        
        if config_data.get('ai_client_type') == 'AZURE_INFERENCE' and (('ai_client_config' not in config_data or config_data.get('ai_client_config') is None) or ('endpoint' not in config_data['ai_client_config'] or config_data['ai_client_config'].get('endpoint') is None) or('key' not in config_data['ai_client_config'] or config_data['ai_client_config'].get('key') is None)):
            raise ValueError("Azure Inference client requires 'endpoint' and 'key' in config_data")

    def _get_ai_client_type(self, ai_client_type_str: str, async_mode: bool = False):
        try:
            if async_mode:
                return AsyncAIClientType[ai_client_type_str]
            else:
                return AIClientType[ai_client_type_str]
        except KeyError:
            error_message = f"Invalid AI client type specified: '{ai_client_type_str}'. Must be one of {[e.name for e in AIClientType]}"
            logger.error(error_message)
            raise ValueError(error_message)

    def _get_ai_client(self, ai_client_type: Union[AIClientType, AsyncAIClientType], **client_args):
        client_factory = AIClientFactory.get_instance()
        return client_factory.get_client(ai_client_type, **client_args)

    def _clear_variables(self):
        # clear the local variables
        self._assistant_config = None
        self._functions = {}
        self._ai_client = None
        self._callbacks = None
        if self._cancel_run_requested:
            self._cancel_run_requested.clear()
        self._ai_client_type = None
        self._name = None

    def cancel_processing(self) -> None:
        """
        Cancels the processing of the user input.

        :return: None
        :rtype: None
        """
        logger.info("User processing run cancellation requested.")
        self._cancel_run_requested.set()

    def _update_arguments(self, args):
        """
        Updates the arguments if they contain '/mnt/data/'.
        """
        updated_args = {}
        for key, value in args.items():
            if isinstance(value, str) and '/mnt/data/' in value:
                assistant_config = AssistantConfigManager.get_instance().get_config(self._name)
                replacement_path = assistant_config.output_folder_path
                if not replacement_path.endswith('/'):
                    replacement_path += '/'
                updated_value = value.replace('/mnt/data/', replacement_path)
                updated_args[key] = updated_value
            else:
                updated_args[key] = value
        return updated_args

    def _handle_function_call(self, function_name, function_arguments):
        logger.info(f"Handling function call: {function_name} with arguments: {function_arguments}")

        function_to_call = self._functions.get(function_name)
        if function_to_call:
            try:
                function_args = json.loads(function_arguments)
            except json.JSONDecodeError:
                logger.error(f"Function {function_name} has invalid arguments.")
                return json.dumps({"function_error": function_name, "error": "Invalid JSON arguments."})
            
            # Update the arguments if necessary
            function_args = self._update_arguments(function_args)

            logger.info(f"Calling function: {function_name} with arguments: {function_args}")
            try:
                function_response = function_to_call(**function_args)
                return function_response
            except Exception as e:
                logger.error(f"Error in function call: {function_name}. Error: {str(e)}")
                return json.dumps({"function_error": function_name, "error": str(e)})
        else:
            logger.error(f"Function: {function_name} is not available.")
            return json.dumps({"function_error": function_name, "error": "Function is not available."})

    def _load_selected_functions(self, assistant_config: AssistantConfig):
        functions = {}

        try:
            for func_spec in assistant_config.functions:
                logger.info(f"Loading selected function: {func_spec['function']['name']}")
                function_name = func_spec["function"]["name"]
                module_name = func_spec["function"].get("module", "default.module.path")

                # Check if it is a system function
                if function_name in system_functions:
                    functions[function_name] = system_functions[function_name]
                elif module_name.startswith("functions"):
                    # Dynamic loading for user-defined functions
                    functions[function_name] = self._import_user_function_from_module(module_name, function_name)
                else:
                    logger.error(f"Invalid module name: {module_name}")
                    raise EngineError(f"Invalid module name: {module_name}")
                self._functions = functions
        except Exception as e:
            logger.error(f"Error loading selected functions for assistant: {e}")
            raise EngineError(f"Error loading selected functions for assistant: {e}")

    def _import_system_function_from_module(self, module_name, function_name):
        try:
            logger.info(f"Importing system function: {function_name} from module: {module_name}")
            module = importlib.import_module(module_name)
            # Retrieve the function from the imported module
            return getattr(module, function_name)
        except Exception as e:
            logger.error(f"Error importing system {function_name} from {module_name}: {e}")
            raise EngineError(f"Error importing system {function_name} from {module_name}: {e}")

    def _import_user_function_from_module(self, module_name, function_name):
        try:
            logger.info(f"Importing user function: {function_name} from module: {module_name}")
            module_path = self._get_module_path(module_name)
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if module_name in sys.modules:
                logger.info("Module is loaded, reloading...")
                # Reload the module if it's already loaded
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module  # Update sys.modules with the reloaded module
            else:
                logger.info("Module is not loaded, loading from scratch")
                # Import the module for the first time
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module  # Add it to sys.modules
            spec.loader.exec_module(module)
            return getattr(module, function_name)
        except Exception as e:
            logger.error(f"Error importing {function_name} from {module_name}: {e}")
            return None

    def _get_module_path(self, module_name):
        logger.info("Module_name: {}".format(module_name))
        if getattr(sys, 'frozen', False):
            # Path for PyInstaller bundle
            base_path = os.path.join(os.path.expanduser("~"), 'AssistantStudioData')
            module_path = os.path.join(base_path, module_name.replace('.', os.sep) + '.py')
            logger.info("Module path: {}".format(module_path))
        else:
            # Path for normal Python environment
            module_path = os.path.join(module_name.replace('.', os.sep) + '.py')
            logger.info("Module path: {}".format(module_path))
        return module_path

    def _replace_file_references_with_content(self, assistant_config: AssistantConfig) -> str:
        instructions = assistant_config.instructions
        file_references = assistant_config.file_references
        
        try:
            # Log the current working directory
            cwd = os.getcwd()
            logger.info(f"Current working directory: {cwd}")
            
            # Optionally, list files in the current directory
            files_in_cwd = os.listdir(cwd)
            logger.debug(f"Files in the current directory: {files_in_cwd}")

            # Regular expression to find all placeholders in the format {file_reference:X}
            pattern = re.compile(r'\{file_reference:(\d+)\}')
            
            # Function to replace each match with the corresponding file content
            def replacer(match):
                index = int(match.group(1))
                if 0 <= index < len(file_references):
                    file_path = file_references[index]
                    try:
                        with open(file_path, 'r') as file:
                            if file_path.endswith('.yaml') or file_path.endswith('.yml'):
                                try:
                                    # yaml.safe_load returns the full document, handle as needed
                                    yaml_content = yaml.safe_load(file)
                                    # Convert YAML content to a string if necessary
                                    return str(yaml_content)
                                except Exception as e:
                                    logger.warning(f"Failed to load YAML file '{file_path}': {e}")
                            else:
                                # Read and return content for all other file types
                                return file.read()
                    except Exception as e:
                        logger.warning(f"Failed to load file '{file_path}': {e}")
                return "File not found or error reading file"
            
            # Replace all placeholders in the instructions with file content
            updated_instructions = pattern.sub(replacer, instructions)
        except Exception as e:
            # If any error occurs, log the error and return the original instructions unmodified
            logger.warning(f"Error processing file references in instructions: {e}")
            return instructions

        return updated_instructions

    def _update_tools(self, assistant_config: AssistantConfig):
        tools = []
        logger.info(f"Updating tools for assistant: {assistant_config.name}")
        if assistant_config.file_search:
            tools.append({"type": "file_search"})
        if assistant_config.functions:
            modified_functions = []
            for function in assistant_config.functions:
                # Create a copy of the function spec to avoid modifying the original
                modified_function = copy.deepcopy(function)
                # Remove the module field from the function spec
                if "function" in modified_function and "module" in modified_function["function"]:
                    del modified_function["function"]["module"]
                modified_functions.append(modified_function)
            tools.extend(modified_functions)
        if assistant_config.code_interpreter:
            tools.append({"type": "code_interpreter"})
        return tools

    @property
    def name(self) -> str:
        """
        The name of the chat assistant.

        :return: The name of the chat assistant.
        :rtype: str
        """
        return self._name

    @property
    def assistant_config(self) -> AssistantConfig:
        """
        The chat assistant configuration.

        :return: The assistant configuration.
        :rtype: AssistantConfig
        """
        return self._assistant_config
    
    @property
    def ai_client(self) -> Union[OpenAIClient, AsyncOpenAIClient, AzureOpenAIClient, AsyncAzureOpenAIClient, AzureInferenceClient, AsyncAzureInferenceClient]:
        """
        The AI client used by the chat assistant.

        :return: The AI client.
        :rtype: Union[OpenAIClient, AsyncOpenAIClient, AzureOpenAIClient, AsyncAzureOpenAIClient, AzureInferenceClient, AsyncAzureInferenceClient]
        """
        return self._ai_client