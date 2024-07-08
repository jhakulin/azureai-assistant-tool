# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.base_ai_client import BaseAiClient

from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential

class AzureInferenceClient(BaseAiClient):
    """
    A class that manages Azure Inference Clients

    :param client_args: Additional keyword arguments for configuring the client.
    :type client_args: Dict
    """
    def __init__(self, **client_args) -> None:
        self._ai_client = ChatCompletionsClient(
            credential=AzureKeyCredential(client_args.get('key')), 
            headers={"api-key": client_args.get('key')}, 
            **client_args,
        )
    
    def create_completions(self, **kwargs):
        """
        Creates completions using the Azure inference service.

        :param kwargs: Keyword arguments for the completion request.
        :return: Completion results from the Azure inference service.
        """
        return self._ai_client.complete(**kwargs)
    
    @property
    def ai_client(self):
        """
        Returns the underlying AI client.

        :return: The AI client.
        :rtype: ChatCompletionsClient
        """
        return self._ai_client