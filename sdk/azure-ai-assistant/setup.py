# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from setuptools import setup, find_packages
import os
from io import open
import re


PACKAGE_NAME = "azure-ai-assistant"
PACKAGE_PPRINT_NAME = "AI Assistant"

# a-b-c => a/b/c
PACKAGE_FOLDER_PATH = PACKAGE_NAME.replace("-", "/")
# a-b-c => a.b.c
NAMESPACE_NAME = PACKAGE_NAME.replace("-", ".")

# Version extraction inspired from 'requests'
with open(os.path.join(PACKAGE_FOLDER_PATH, "_version.py"), "r") as fd:
    version = re.search(r'^VERSION\s*=\s*[\'"]([^\'"]*)[\'"]', fd.read(), re.MULTILINE).group(1)
if not version:
    raise RuntimeError("Cannot find version information")

with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name=PACKAGE_NAME,
    version=version,
    description="Microsoft Azure {} Client Library for Python".format(PACKAGE_PPRINT_NAME),
    # ensure that these are updated to reflect the package owners' information
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Azure/azure-sdk-for-python",
    keywords="azure, azure sdk, assistant",  # update with search keywords relevant to the azure service / product
    author="Microsoft Corporation",
    author_email="azuresdkengsysadmins@microsoft.com",
    license="MIT License",
    # ensure that the development status reflects the status of your package
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: MIT License",
    ],
    packages=find_packages(
        exclude=[
            # Exclude packages that will be covered by PEP420 or nspkg
            # This means any folder structure that only consists of a __init__.py.
            # For example, for storage, this would mean adding 'azure.storage'
            # in addition to the default 'azure' that is seen here.
            "azure",
            "azure.ai"
        ]
    ),
    package_data={
        'azure.ai.assistant': ['py.typed'],
    },
    install_requires=[
        "openai",
        "python-Levenshtein",
        "fuzzywuzzy",
        "Pillow",
        "PyYAML",
        "pyaudio",
        "numpy<2.2,>=1.24",  # Constrained to avoid conflict with realtime-ai
        "scipy",
        "onnxruntime=1.19.0",  # Constrained to avoid conflict with python 3.12
        "resampy",
        "azure-ai-projects",
        "azure-ai-agents>=1.1.0b3",
        "azure-identity",
        "azure-mgmt-logic",
        "azure-mgmt-web",
        "protobuf<6.0.0,>=3.20.2",  # Constrained to avoid conflicts with Google packages
    ],
    python_requires=">=3.8",
    project_urls={
        "Bug Reports": "https://github.com/Azure/azure-sdk-for-python/issues",
        "Source": "https://github.com/Azure/azure-sdk-python",
    },
)
