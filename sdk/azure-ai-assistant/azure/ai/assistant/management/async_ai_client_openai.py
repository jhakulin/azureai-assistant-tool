# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.base_ai_client import BaseAiClient

from openai import AsyncOpenAI

class AsyncOpenAIClient(BaseAiClient):
    def __init__(self, **client_args) -> None:
        """
        A class that manages OpenAI Clients
        
        :param client_args: Additional keyword arguments for configuring the client.
        :type client_args: Dict
        """
        super().__init__(AsyncOpenAI(**client_args))

    async def create_completions(self, **kwargs):
        """
        Creates completions using the OpenAI service.
        
        :param kwargs: Keyword arguments for the completion request.
        :return: Completion results from the OpenAI service.
        """
        return await self._ai_client.chat.completions.create(**kwargs)
    
    async def create_thread(self, **kwargs):
        """
        Creates a thread using the OpenAI service.

        :param kwargs: Keyword arguments for the thread.
        :return: Created thread.
        """
        return await self._ai_client.beta.threads.create(**kwargs)
    
    @property
    def ai_client(self):
        """
        Returns the underlying AI client.

        :return: The AI client.
        :rtype: AsyncOpenAI
        """
        return self._ai_client