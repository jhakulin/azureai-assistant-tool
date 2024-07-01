# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.base_ai_client import BaseAiClient

from openai import OpenAI

class OpenAIClient(BaseAiClient):
    def __init__(self, **client_args) -> None:
        """
        A class that manages OpenAI Clients
        
        :param client_args: Additional keyword arguments for configuring the client.
        :type client_args: Dict
        """
        self._ai_client = OpenAI(**client_args)

    def create_completions(self, **kwargs):
        """
        Creates completions using the OpenAI service.
        
        :param kwargs: Keyword arguments for the completion request.
        :return: Completion results from the OpenAI service.
        """
        return self._ai_client.chat.completions.create(**kwargs)
    
    @property
    def ai_client(self):
        """
        Returns the underlying AI client.

        :return: The AI client.
        :rtype: OpenAI
        """
        return self._ai_client