# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.base_ai_client import BaseAiClient

from azure.ai.inference.aio import ChatCompletionsClient as AsyncChatCompletionsClient
from azure.core.credentials import AzureKeyCredential

class AsyncAzureInferenceClient(BaseAiClient):
    """
    A class that manages Azure Inference Clients

    :param key: The Azure API key.
    :type key: str
    :param endpoint: The Azure endpoint.
    :type endpoint: str
    :param client_args: Additional keyword arguments for configuring the client.
    :type client_args: Dict
    """
    def __init__(self, **client_args) -> None:
        self._ai_client = AsyncChatCompletionsClient(endpoint=client_args.get('endpoint'), 
                                                     credential=AzureKeyCredential(client_args.get('key')), 
                                                     headers={"api-key": client_args.get('key')}, 
                                                     **client_args)
    
    async def create_completions(self, **kwargs):
        """
        Creates completions using the Azure inference service.

        :param kwargs: Keyword arguments for the completion request.
        :return: Completion results from the Azure inference service.
        """
        return await self._ai_client.complete(**kwargs)
    
    @property
    def ai_client(self):
        """
        Returns the underlying AI client.

        :return: The AI client.
        :rtype: AsyncChatCompletionsClient
        """
        return self._ai_client