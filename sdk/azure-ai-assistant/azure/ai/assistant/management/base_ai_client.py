# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from abc import ABC, abstractmethod

class BaseAiClient(ABC):
    """
    A Base class for AI clients.
    """
    
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
