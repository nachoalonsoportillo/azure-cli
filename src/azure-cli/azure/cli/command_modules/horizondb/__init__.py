# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from azure.cli.core import AzCommandsLoader
from azure.cli.core.commands import CliCommandType


class HorizonDBCommandsLoader(AzCommandsLoader):

    def __init__(self, cli_ctx=None):
        from ._client_factory import cf_horizondb
        custom = CliCommandType(
            operations_tmpl='azure.cli.command_modules.horizondb.custom#{}',
            client_factory=cf_horizondb)
        super().__init__(cli_ctx=cli_ctx, custom_command_type=custom)

    def load_command_table(self, args):
        from . import _help  # pylint: disable=unused-import
        from .commands import load_command_table
        load_command_table(self, args)
        return self.command_table

    def load_arguments(self, command):
        from ._params import load_arguments
        load_arguments(self, command)


COMMAND_LOADER_CLS = HorizonDBCommandsLoader
