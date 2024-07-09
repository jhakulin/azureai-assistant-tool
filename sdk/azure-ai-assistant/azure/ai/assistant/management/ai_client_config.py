# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.ai_client_factory import AIClientType
from azure.ai.assistant.management.logger_module import logger

import json, os
from typing import Optional


class AIClientConfig:
    """
    A class to manage AI Clients.

    :param ai_client_type: The type of AI client to use.
    :type ai_client_type: AIClientType
    :param config_file: The path to the configuration file.
    :type config_file: str
    """
    def __init__(
            self, 
            ai_client_type: AIClientType,
            config_folder : Optional[str] = None,
    ) -> None:
        self._ai_client_type = ai_client_type.name
        if config_folder:
            self._config_file = os.path.join(config_folder, 'ai_clients.json')
        else:
            self._config_file = None
        self._config_data = {}
        self._ai_clients = []
        # Initialize the list of ai clients
        self.get_all_ai_clients()

    def add_ai_client(self, client_name: str, endpoint: str, key: str) -> None:
        """
        Add a new ai client.
        
        :param endpoint: The endpoint of the ai client.
        :type endpoint: str
        :param key: The key of the ai client.
        :type key: str
        """
        unique_client_name = self._generate_unique_client_name(client_name)
        if not any(ai_client['endpoint'] == endpoint for ai_client in self._ai_clients):
            self._ai_clients.append({'name': unique_client_name,'endpoint': endpoint, 'key': key})
    
    def _generate_unique_client_name(self, desired_name) -> str:
        if not any(ai_client['name'] == desired_name for ai_client in self._ai_clients):
            return desired_name

        i = 1
        while any(ai_client['name'] == f"{desired_name} {i}" for ai_client in self._ai_clients):
            i += 1
        return f"{desired_name} {i}"

    def remove_ai_client(self, endpoint: str) -> None:
        """
        Remove an ai client by its endpoint.
        
        :param endpoint: The endpoint of the ai client.
        :type endpoint: str
        """
        ai_client_to_remove = None
        for ai_client in self._ai_clients:
            if ai_client['endpoint'] == endpoint:
                ai_client_to_remove = ai_client['endpoint']
                break

        if ai_client_to_remove:
            self._ai_clients = [ai_client for ai_client in self._ai_clients if ai_client['endpoint'] != ai_client_to_remove]
        
    def get_ai_client_by_endpoint(self, endpoint: str) -> dict:
        """
        Get an ai client by its endpoint.
        
        :param endpoint: The endpoint of the ai client.
        :type endpoint: str

        :return: The ai client.
        :rtype: dict
        """
        for ai_client in self._ai_clients:
            if ai_client['endpoint'] == endpoint:
                return ai_client
        return None
    
    def get_ai_client_key_by_endpoint(self, endpoint: str) -> str:
        """
        Get the key of an ai client by its endpoint.
        
        :param endpoint: The endpoint of the ai client.
        :type endpoint: str

        :return: The key of the ai client.
        :rtype: str
        """
        ai_client = self.get_ai_client_by_endpoint(endpoint)
        return ai_client['key'] if ai_client else None
    
    def get_ai_client_by_name(self, name: str) -> dict:
        """
        Get an ai client by its name.
        
        :param name: The name of the ai client.
        :type name: str

        :return: The ai client.
        :rtype: dict
        """
        for ai_client in self._ai_clients:
            if ai_client['name'] == name:
                return ai_client
        return None
    
    def get_ai_client_endpoint_by_name(self, name: str) -> str:
        """
        Get the endpoint of an ai client by its name.
        
        :param name: The name of the ai client.
        :type name: str

        :return: The endpoint of the ai client.
        :rtype: str
        """
        ai_client = self.get_ai_client_by_name(name)
        return ai_client['endpoint'] if ai_client else None
    
    def get_ai_client_key_by_name(self, name: str) -> str:
        """
        Get the key of an ai client by its name.
        
        :param name: The name of the ai client.
        :type name: str

        :return: The key of the ai client.
        :rtype: str
        """
        ai_client = self.get_ai_client_by_name(name)
        return ai_client['key'] if ai_client else None
    
    def get_all_ai_client_endpoints(self) -> list:
        """
        Get a list of all ai client endpoints.
        
        :return: A list of all ai client endpoints.
        :rtype: list
        """
        return [ai_client['endpoint'] for ai_client in self._ai_clients]
    
    def get_all_ai_client_names(self) -> list:
        """
        Get a list of all ai client names.
        
        :return: A list of all ai client names.
        :rtype: list
        """
        return [ai_client['name'] for ai_client in self._ai_clients]
    
    def get_all_ai_clients(self) -> list:
        """
        Get a list of all ai clients.
        
        :return: A list of all ai clients.
        :rtype: list
        """
         # create config file if it doesn't exist
        if self._config_file is None:
            return []

        try:
            with open(self._config_file, 'r') as f:
                pass
        except FileNotFoundError:
            self.save_to_json()

        # Load ai clients from the config file
        with open(self._config_file, 'r') as f:
            self._config_data = json.load(f)

        # Fetching ai clients for the specific ai_client_type
        ai_client_type_data = self._config_data.get(self._ai_client_type, {})
        self._ai_clients = ai_client_type_data.get('ai_clients', [])

        return self._ai_clients

    def _get_config_data(self):
        # Initialize a structure for ai clients categorized by ai_client_type
        if self._ai_client_type not in self._config_data:
            self._config_data[self._ai_client_type] = {"ai_clients": []}

        # Add ai clients to the appropriate ai_client_type category
        self._config_data[self._ai_client_type]["ai_clients"] = self._ai_clients

        return self._config_data

    def save_to_json(self) -> None:
        """
        Save the configuration for the specific ai_client_type to a JSON file.
        """
        if self._config_file is None:
            return

        # Ensure the directory exists
        os.makedirs(os.path.dirname(self._config_file), exist_ok=True)
        
        # Read the existing configuration
        logger.info(f"Saving ai client configuration to {self._config_file}")
        try:
            with open(self._config_file, 'r') as f:
                existing_config = json.load(f)
        except FileNotFoundError:
            logger.info(f"Existing configuration file not found. Creating new file at {self._config_file}")
            existing_config = {}

        # Update the configuration data for the specific ai_client_type
        config_data = self._get_config_data()
        existing_config[self._ai_client_type] = config_data[self._ai_client_type]

        # Write the updated configuration back to the file
        with open(self._config_file, 'w') as f:
            json.dump(existing_config, f, indent=4)