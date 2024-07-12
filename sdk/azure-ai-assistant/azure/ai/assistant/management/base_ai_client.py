# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from abc import ABC, abstractmethod

class BaseAiClient(ABC):
    """
    A Base class for AI clients.

    :param ai_client: The underlying AI client.
    """
    def __init__(self, ai_client):
        self._ai_client = ai_client
    
    def __getattr__(self, attr):
        return getattr(self._ai_client, attr)
    
    @abstractmethod
    def create_completions(self, **kwargs):
        """
        Create completions for the AI client.

        :param kwargs: The keyword arguments for creating completions.
        :type kwargs: Dict
        :return: The completions for the AI client.
        :rtype: Any
        """
        pass
