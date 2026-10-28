# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from azure.cli.core.commands.client_factory import get_mgmt_service_client


def cf_horizondb(cli_ctx, *_):
    from azure.mgmt.horizondb import HorizonDBMgmtClient
    return get_mgmt_service_client(cli_ctx, HorizonDBMgmtClient, api_version='2026-10-01-preview')
