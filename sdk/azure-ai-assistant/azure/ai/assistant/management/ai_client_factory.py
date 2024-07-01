# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from enum import Enum, auto
from openai import AzureOpenAI, OpenAI, AsyncAzureOpenAI, AsyncOpenAI
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.aio import ChatCompletionsClient as AsyncChatCompletionsClient

import os
from typing import Union
from azure.ai.assistant.management.logger_module import logger
from azure.ai.assistant.management.exceptions import EngineError
from azure.ai.assistant.management.ai_client_azure_inference import AzureInferenceClient
from azure.ai.assistant.management.ai_client_azure_openai import AzureOpenAIClient
from azure.ai.assistant.management.ai_client_openai import OpenAIClient
from azure.ai.assistant.management.async_ai_client_azure_inference import AsyncAzureInferenceClient
from azure.ai.assistant.management.async_ai_client_azure_openai import AsyncAzureOpenAIClient
from azure.ai.assistant.management.async_ai_client_openai import AsyncOpenAIClient


class AIClientType(Enum):
    """
    An enum for the different types of AI clients.
    """
    AZURE_OPEN_AI = auto()
    """Azure OpenAI client"""
    OPEN_AI = auto()
    """OpenAI client"""
    AZURE_INFERENCE = auto()
    """Azure Inference client"""


class AsyncAIClientType(Enum):
    """
    An enum for the different types of AI clients.
    """
    AZURE_OPEN_AI = auto()
    """Azure OpenAI async client"""
    OPEN_AI = auto()
    """OpenAI async client"""
    AZURE_INFERENCE = auto()
    """Azure Inference async client"""


class AIClientFactory:
    _instance = None
    _clients = {}

    """
    A factory class for creating AI clients.
    """
    def __init__(self) -> None:
        if AIClientFactory._instance is not None:
            raise Exception("AIClientFactory is a singleton class")
        else:
            AIClientFactory._instance = self

    @classmethod
    def get_instance(cls) -> "AIClientFactory":
        """
        Get the singleton instance of the AI client factory.

        :return: The singleton instance of the AI client factory.
        :rtype: AIClientFactory
        """
        if cls._instance is None:
            cls._instance = AIClientFactory()
        return cls._instance

    def get_client(
            self, 
            client_type: Union[AIClientType, AsyncAIClientType],
            api_version: str = None,
            key: str = None,
            endpoint: str = None,
            **client_args
    ) -> Union[OpenAIClient, AzureOpenAIClient, AzureInferenceClient, AsyncOpenAIClient, AsyncAzureOpenAIClient, AsyncAzureInferenceClient]:
        """
        Get an AI client, synchronous or asynchronous, based on the given type and API version.

        :param client_type: The type of AI client to get.
        :type client_type: Union[AIClientType, AsyncAIClientType]
        :param api_version: The API version to use.
        :type api_version: str
        :param client_args: Additional keyword arguments for configuring the AI client.
        :type client_args: Dict
        :param key: The Azure API key to be used with the Azure Inference client.
        :type key: str
        :param endpoint: The Azure endpoint to be used with the Azure Inference client.
        :type endpoint: str
        
        :return: The AI client.
        :rtype: Union[OpenAIClient, AzureOpenAIClient, AzureInferenceClient, AsyncOpenAIClient, AsyncAzureOpenAIClient, AsyncAzureInferenceClient]
        """
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", api_version) or "2024-05-01-preview"
        client_key = (client_type, api_version)
        if client_key in self._clients:
            # if client is closed, recreate client
            if not client_type == AIClientType.AZURE_INFERENCE and self._clients[client_key].ai_client.is_closed():
                logger.info(f"Recreating client for {client_key}")
                del self._clients[client_key]
            else:
                return self._clients[client_key]

        if isinstance(client_type, AIClientType):
            if client_type == AIClientType.AZURE_OPEN_AI:
                self._clients[client_key] = AzureOpenAIClient(**client_args)
            elif client_type == AIClientType.OPEN_AI:
                self._clients[client_key] = OpenAIClient(**client_args)
            elif client_type == AIClientType.AZURE_INFERENCE:
                self._clients[client_key] = AzureInferenceClient(key=key, endpoint=endpoint, **client_args)
                
        elif isinstance(client_type, AsyncAIClientType):
            if client_type == AsyncAIClientType.AZURE_OPEN_AI:
                self._clients[client_key] = AsyncAzureOpenAIClient(**client_args)
            elif client_type == AsyncAIClientType.OPEN_AI:
                self._clients[client_key] = AsyncOpenAIClient(**client_args)
            elif client_type == AIClientType.AZURE_INFERENCE:
                self._clients[client_key] = AsyncAzureInferenceClient(key=key, endpoint=endpoint, **client_args)
        else:
            raise ValueError(f"Invalid client type: {client_type}")

        return self._clients[client_key]
