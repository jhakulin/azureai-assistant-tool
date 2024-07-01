# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.base_ai_client import BaseAiClient

from openai import OpenAI

class OpenAIClient(BaseAiClient):
    def __init__(self, **client_args) -> None:
        self._ai_client = OpenAI(**client_args)

    def create_completions(self, **kwargs):
        return self._ai_client.chat.completions.create(**kwargs)