# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.base_ai_client import BaseAiClient

import os
from openai import AzureOpenAI

class AzureOpenAIClient(BaseAiClient):
    def __init__(self, **client_args) -> None:
        api_version = os.getenv("AZURE_OPENAI_VERSION", "2024-05-01-preview")
        self._ai_client = AzureOpenAI(api_version=api_version, azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"), **client_args)
    
    def create_completions(self, **kwargs):
        return self._ai_client.chat.completions.create(**kwargs)