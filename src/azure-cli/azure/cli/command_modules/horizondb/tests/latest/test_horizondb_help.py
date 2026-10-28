# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import importlib
import shlex
import unittest

import yaml
from knack.help_files import helps


# Keep an independent inventory: a missing command must fail even if registration and help regress together.
COMMANDS = {
    'operation': 'list',
    'cluster': 'create show list update delete failover wait',
    'pool': 'create show list update delete start stop restart wait',
    'node': 'show list',
    'endpoint': 'show list',
    'firewall-rule': 'create show list delete wait',
    'authentication': 'list',
    'authentication password': 'show create update wait',
    'authentication microsoft-entra': 'show create update wait',
    'microsoft-entra-administrator': 'create show list delete wait',
    'log-capture-configuration': 'show list update wait',
    'log-file': 'show list list-download-urls',
    'change-data-capture': 'show list create update delete wait',
    'change-data-capture-destination': 'show list create update activate-identity delete wait',
    'change-data-capture-identity-assignment': 'show list create delete wait',
    'change-data-capture-configuration': 'show list create update delete wait',
    'private-endpoint-connection': 'show list approve reject delete wait',
    'private-link-resource': 'show list',
    'parameter-group': 'create show list update delete list-connections wait',
    'default-parameter-group': 'show list',
}
EXPECTED_COMMANDS = {
    'horizondb ' + group + ' ' + verb
    for group, verbs in COMMANDS.items() for verb in verbs.split()
}
# The supplied reference contains 94 commands. Two regional discovery entries remain SDK-only;
# four maintenance-event entries are excluded because the current API does not support them.
REFERENCE_COMMANDS = frozenset(EXPECTED_COMMANDS)
EXPECTED_COMMANDS.add('horizondb create')
EXPECTED_GROUPS = {'horizondb'} | {'horizondb ' + group for group in COMMANDS}


class HorizonDBHelpTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        importlib.import_module('azure.cli.command_modules.horizondb._help')
        cls.documents = {
            name: yaml.safe_load(value) for name, value in helps.items()
            if name == 'horizondb' or name.startswith('horizondb ')
        }

    def test_horizondb_help_covers_every_command_and_group(self):
        commands = {name for name, doc in self.documents.items() if doc['type'] == 'command'}
        groups = {name for name, doc in self.documents.items() if doc['type'] == 'group'}
        self.assertEqual(EXPECTED_COMMANDS, commands)
        self.assertEqual(EXPECTED_GROUPS, groups)
        for name, doc in self.documents.items():
            with self.subTest(command=name):
                self.assertTrue(doc['short-summary'])
                self.assertLessEqual(len(doc['short-summary']), 200)
                self.assertLessEqual(len(doc.get('long-summary', '')), 2000)

    def test_horizondb_help_every_command_has_scoped_executable_examples(self):
        covered = set()
        for name in EXPECTED_COMMANDS:
            examples = self.documents[name].get('examples', [])
            with self.subTest(command=name):
                self.assertTrue(examples, 'Missing example for ' + name)
                for example in examples:
                    self.assertTrue(example['name'])
                    tokens = shlex.split(example['text'])
                    self.assertEqual(['az'] + name.split(), tokens[:len(name.split()) + 1])
                    covered.add(name)
        # This offline inventory gate checks help coverage, not live service coverage.
        self.assertGreaterEqual(len(covered) / len(EXPECTED_COMMANDS), 0.8)
        self.assertEqual(EXPECTED_COMMANDS, covered)

    def test_horizondb_help_password_examples_do_not_embed_credentials(self):
        for name in ('horizondb authentication password create', 'horizondb authentication password update'):
            self.assertIn('--debug', self.documents[name]['long-summary'])
            self.assertIn('expanded command arguments', self.documents[name]['long-summary'])
            for example in self.documents[name]['examples']:
                tokens = shlex.split(example['text'])
                for option in ('--administrator-login', '--administrator-login-password', '-p'):
                    if option in tokens:
                        self.assertTrue(tokens[tokens.index(option) + 1].startswith('$HORIZONDB_'))
        self.assertIn('bearer credential', self.documents['horizondb log-file list-download-urls']['long-summary'])

    def test_horizondb_help_describes_non_obvious_service_constraints(self):
        required_prose = {
            'horizondb pool': ('at most one pool', 'must differ', 'restart'),
            'horizondb cluster create': ('does not clone pools', 'update-only'),
            'horizondb cluster update': ('cannot change together', 'empty dictionaries'),
            'horizondb pool delete': ('rejects all', 'not deleted'),
            'horizondb endpoint': ('no routing destination', 'read-write node'),
            'horizondb authentication password': ('create-only', 'write-only', 'Disabling'),
            'horizondb change-data-capture-identity-assignment': ('exactly match', 'RBAC'),
            'horizondb change-data-capture-destination activate-identity': ('ETAG', 'validated', 'idempotent'),
            'horizondb private-link-resource': ('not default', 'ReadWrite', 'ReadOnly'),
            'horizondb private-endpoint-connection approve': ('PrivateEndpointRequiresPool',),
            'horizondb parameter-group': ('Read-only parameters', 'customer-initiated', 'valueSource'),
        }
        for name, phrases in required_prose.items():
            for phrase in phrases:
                with self.subTest(command=name, phrase=phrase):
                    self.assertIn(phrase, self.documents[name]['long-summary'])


if __name__ == '__main__':
    unittest.main()
