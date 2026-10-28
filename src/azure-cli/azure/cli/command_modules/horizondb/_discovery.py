# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------


def _list_regional_capabilities(client, location):
    """Keep actual SDK wire fields and paging/errors intact for internal configuration discovery."""
    return [entry.as_dict() for entry in client.capabilities.list(location)]
