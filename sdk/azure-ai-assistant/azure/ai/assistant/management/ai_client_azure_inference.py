# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.base_ai_client import BaseAiClient

from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential

class AzureInferenceClient(BaseAiClient):
    def __init__(self, key, endpoint, **client_args) -> None:
        self._ai_client = ChatCompletionsClient(endpoint=endpoint, credential=AzureKeyCredential(key), headers={"api-key": key}, **client_args)
    
    def create_completions(self, **kwargs):
        return self._ai_client.complete(**kwargs)