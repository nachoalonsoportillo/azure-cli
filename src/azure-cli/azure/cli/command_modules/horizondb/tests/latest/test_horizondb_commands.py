# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import shlex
import unittest
from types import SimpleNamespace
from unittest import mock

import yaml
from knack.help_files import helps
from knack import events

from azure.cli.core.mock import DummyCli
from azure.cli.core.parser import AzCliCommandParser
from azure.cli.core.commands.arm import register_ids_argument
from azure.cli.command_modules.horizondb import HorizonDBCommandsLoader
from .test_horizondb_help import EXPECTED_COMMANDS


LRO_COMMANDS = {
    'cluster': 'create update delete failover',
    'pool': 'create update delete start stop restart',
    'firewall-rule': 'create delete',
    'authentication password': 'create update',
    'authentication microsoft-entra': 'create update',
    'microsoft-entra-administrator': 'create delete',
    'log-capture-configuration': 'update',
    'change-data-capture': 'create update delete',
    'change-data-capture-destination': 'create update activate-identity delete',
    'change-data-capture-identity-assignment': 'create delete',
    'change-data-capture-configuration': 'create update delete',
    'private-endpoint-connection': 'approve reject delete',
    'parameter-group': 'create update delete',
}
EXPECTED_LRO_COMMANDS = {
    'horizondb ' + group + ' ' + verb
    for group, verbs in LRO_COMMANDS.items() for verb in verbs.split()
}


def _loader(command=None):
    cli = DummyCli(commands_loader_cls=HorizonDBCommandsLoader)
    loader = HorizonDBCommandsLoader(cli)
    cli.invocation = mock.MagicMock(commands_loader=loader, data={'command_string': command or 'horizondb'})
    loader.load_command_table(None)
    if command:
        loader.command_name = command
        loader.command_table[command].load_arguments()
        loader.load_arguments(command)
        loader._update_command_definitions()
    return loader


class HorizonDBCommandsTest(unittest.TestCase):

    def test_horizondb_removed_groups_are_not_loaded_or_documented(self):
        loader = _loader()
        for removed in ('horizondb capability', 'horizondb maintenance-event'):
            with self.subTest(group=removed):
                self.assertFalse(any(name == removed or name.startswith(removed + ' ')
                                     for name in loader.command_table))
                self.assertFalse(any(name == removed or name.startswith(removed + ' ') for name in helps))

    def test_horizondb_command_table_matches_supported_reference_surface(self):
        loader = _loader()
        self.assertEqual(EXPECTED_COMMANDS, set(loader.command_table))
        for name, command in loader.command_table.items():
            with self.subTest(command=name):
                self.assertEqual(
                    name in EXPECTED_LRO_COMMANDS,
                    bool(command.command_kwargs.get('supports_no_wait')))

    def test_horizondb_every_help_example_uses_registered_options(self):
        generic = {'--subscription', '--ids', '--yes', '-y', '--query', '--output', '-o'}
        for name in sorted(EXPECTED_COMMANDS):
            loader = _loader(name)
            command = loader.command_table[name]
            options = {
                option for argument in command.arguments.values()
                for option in argument.options_list
            } | generic
            document = yaml.safe_load(helps[name])
            for example in document['examples']:
                with self.subTest(command=name, example=example['name']):
                    tokens = shlex.split(example['text'])
                    used = {token for token in tokens if token.startswith('-')}
                    self.assertFalse(used - options, 'Unregistered example options: ' + str(used - options))
                    for argument_name, argument in command.arguments.items():
                        if argument.options.get('required') and argument_name not in ('cmd', 'client'):
                            if '--ids' in used and argument.type.settings.get('id_part'):
                                continue
                            self.assertTrue(set(argument.options_list) & used,
                                            'Example omits required ' + argument_name)

    def test_horizondb_password_and_singletons_hide_service_created_names(self):
        for name in ('horizondb authentication password create', 'horizondb authentication password update',
                     'horizondb authentication microsoft-entra show', 'horizondb change-data-capture show'):
            command = _loader(name).command_table[name]
            options = {option for argument in command.arguments.values() for option in argument.options_list}
            with self.subTest(command=name):
                self.assertNotIn('--name', options)
                self.assertNotIn('--authentication-name', options)
        command = _loader('horizondb authentication password update').command_table[
            'horizondb authentication password update']
        self.assertNotIn('administrator_login', command.arguments)

    def test_horizondb_resource_id_selector_parts(self):
        cases = {
            'horizondb cluster show': {'cluster_name': 'name'},
            'horizondb pool show': {'cluster_name': 'name', 'pool_name': 'child_name_1'},
            'horizondb node show': {
                'cluster_name': 'name', 'pool_name': 'child_name_1', 'node_name': 'child_name_2'},
            'horizondb microsoft-entra-administrator show': {
                'cluster_name': 'name', 'object_id': 'child_name_1'},
            'horizondb change-data-capture-identity-assignment show': {
                'cluster_name': 'name', 'destination_name': 'child_name_1', 'assignment_name': 'child_name_2'},
            'horizondb default-parameter-group show': {'location': 'name', 'name': 'child_name_1'},
        }
        for name, expected in cases.items():
            command = _loader(name).command_table[name]
            with self.subTest(command=name):
                for argument, part in expected.items():
                    self.assertEqual(part, command.arguments[argument].type.settings.get('id_part'))

    def test_horizondb_runtime_help_loads_and_reference_examples_parse(self):
        subscription = '00000000-0000-0000-0000-000000000000'
        prefix = f'/subscriptions/{subscription}/resourceGroups/example-rg/providers/'
        cluster = prefix + 'Microsoft.HorizonDb/clusters/example-cluster'
        identity = prefix + 'Microsoft.ManagedIdentity/userAssignedIdentities/example-identity'
        default_group = (f'/subscriptions/{subscription}/providers/Microsoft.HorizonDb/'
                         'locations/eastus2/parameterGroups/postgresql-17')
        variables = {
            '$SUBSCRIPTION_ID': subscription,
            '$CLUSTER_ID': cluster,
            '$SOURCE_CLUSTER_ID': cluster,
            '$IDENTITY_ID': identity,
            '$PRIMARY_ENCRYPTION_ID': identity,
            '$DEFAULT_PARAMETER_GROUP_ID': default_group,
            '$CUSTOM_PARAMETER_GROUP_ID': prefix + 'Microsoft.HorizonDb/parameterGroups/pg-transactional',
            '$DESTINATION_ID': cluster + '/changeDataCaptureDestinations/fabric-main',
            '$ASSIGNMENT_ID': cluster + '/changeDataCaptureDestinations/fabric-main/identityAssignments/primary',
            '$ETAG': '"test-etag"',
            '$LOG_FILE_NAME': '11111111-1111-1111-1111-111111111111',
            '$HORIZONDB_ADMIN_LOGIN': 'offline-test-admin',
            '$HORIZONDB_ADMIN_PASSWORD': 'offline-test-password',
            '$HORIZONDB_NEW_ADMIN_PASSWORD': 'offline-test-new-password',
        }
        for name in sorted(EXPECTED_COMMANDS):
            with self.subTest(command=name):
                loader = _loader(name)
                command = loader.command_table[name]
                # The invoker injects cmd, not the user. Exercise parser/help without invoking any handler.
                command.arguments.pop('cmd', None)
                loader.command_table = {name: command}
                loader.cmd_to_loader_map = {name: [loader]}
                help_ctx = loader.cli_ctx.help_cls(loader.cli_ctx)
                parser = AzCliCommandParser(cli_ctx=loader.cli_ctx, cli_help=help_ctx)
                parser.load_command_table(loader)
                command_parser = parser.subparser_map[name]
                help_ctx.update_loaders_with_help_file_contents(name.split())
                runtime_help = help_ctx.command_help_cls(help_ctx, name, command_parser)
                runtime_help.load(command_parser)
                document = yaml.safe_load(helps[name])
                self.assertEqual(document['short-summary'], runtime_help.short_summary)
                self.assertEqual(len(document['examples']), len(runtime_help.examples))
                for example in document['examples']:
                    tokens = [variables.get(token, token) for token in shlex.split(example['text'])]
                    if '--ids' in tokens:
                        # ID expansion belongs to the invoker and is covered by the selector metadata test.
                        continue
                    arguments = tokens[len(name.split()) + 1:]
                    _, unknown = command_parser.parse_known_args(arguments)
                    self.assertFalse([value for value in unknown
                                      if value.startswith('--') and value not in
                                      ('--subscription', '--query', '--output', '--yes')])

    def test_horizondb_ids_expand_nested_and_location_resource_selectors(self):
        subscription = '00000000-0000-0000-0000-000000000000'
        prefix = (f'/subscriptions/{subscription}/resourceGroups/example-rg/'
                  'providers/Microsoft.HorizonDb/clusters/example-cluster')
        cases = (
            ('horizondb node show', prefix + '/pools/primary/nodes/node-001',
             {'resource_group_name': 'example-rg', 'cluster_name': 'example-cluster',
              'pool_name': 'primary', 'node_name': 'node-001'}),
            ('horizondb change-data-capture-identity-assignment show',
             prefix + '/changeDataCaptureDestinations/fabric-main/identityAssignments/primary',
             {'cluster_name': 'example-cluster', 'destination_name': 'fabric-main', 'assignment_name': 'primary'}),
            ('horizondb default-parameter-group show',
             f'/subscriptions/{subscription}/providers/Microsoft.HorizonDb/'
             'locations/eastus2/parameterGroups/postgresql-17',
             {'location': 'eastus2', 'name': 'postgresql-17'}),
        )
        for name, resource_id, expected in cases:
            with self.subTest(command=name):
                loader = _loader(name)
                command = loader.command_table[name]
                command.arguments.pop('cmd', None)
                loader.command_table = {name: command}
                callbacks = {}
                register_ids_argument(SimpleNamespace(
                    register_event=lambda event, handler: callbacks.__setitem__(event, handler)))
                callbacks[events.EVENT_INVOKER_POST_CMD_TBL_CREATE](None, commands_loader=loader)
                parser = AzCliCommandParser(cli_ctx=loader.cli_ctx)
                parser.load_command_table(loader)
                namespace = parser.subparser_map[name].parse_args(['--ids', resource_id])
                callbacks[events.EVENT_INVOKER_POST_PARSE_ARGS](None, command=name, args=namespace)
                for argument, expected_value in expected.items():
                    self.assertEqual([expected_value], getattr(namespace, argument))


if __name__ == '__main__':
    unittest.main()
