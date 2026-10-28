# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import unittest
from types import SimpleNamespace
from unittest import mock

from azure.cli.core.azclierror import InvalidArgumentValueError, RequiredArgumentMissingError
from azure.cli.command_modules.horizondb import _validators as validators


class HorizonDBValidatorsTest(unittest.TestCase):

    def test_horizondb_tags_accept_standard_json_file_and_empty_clear(self):
        cases = (
            (None, None),
            ([], {}),
            (['flag', 'owner=cli', 'empty=', 'embedded=a=b'],
             {'flag': '', 'owner': 'cli', 'empty': '', 'embedded': 'a=b'}),
            (['{"owner":"cli","empty":""}'], {'owner': 'cli', 'empty': ''}),
            ({'owner': 'cli'}, {'owner': 'cli'}),
        )
        for supplied, expected in cases:
            with self.subTest(tags=supplied):
                namespace = SimpleNamespace(tags=supplied)
                validators.validate_tags(namespace)
                self.assertEqual(expected, namespace.tags)
        namespace = SimpleNamespace(tags=['@tags.json'])
        with mock.patch.object(validators, 'get_file_json', return_value={'owner': 'file'}) as read_file:
            validators.validate_tags(namespace)
            self.assertEqual({'owner': 'file'}, namespace.tags)
            read_file.assert_called_once_with('tags.json')

    def test_horizondb_tags_reject_non_string_json_entries_and_non_object_files(self):
        for supplied in ({'owner': 1}, {1: 'cli'}, ['{"owner":false}']):
            with self.subTest(tags=supplied):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.validate_tags(SimpleNamespace(tags=supplied))
        with mock.patch.object(validators, 'get_file_json', return_value=['owner=cli']):
            with self.assertRaises(InvalidArgumentValueError):
                validators.validate_tags(SimpleNamespace(tags=['@invalid-tags.json']))

    def test_horizondb_integer_boundaries(self):
        for minimum, maximum in ((1, 35), (1, 192), (1, 16)):
            parse = validators.bounded_int(minimum, maximum)
            for value in (minimum, maximum):
                self.assertEqual(value, parse(str(value)))
            for value in (minimum - 1, maximum + 1, 'not-an-integer'):
                with self.subTest(bounds=(minimum, maximum), value=value):
                    with self.assertRaises(InvalidArgumentValueError):
                        parse(value)

    def test_horizondb_dns_names_and_description_boundaries(self):
        for value in ('a', '1', '1-example-cluster', 'example-cluster', 'a' * 63):
            self.assertEqual(value, validators.dns_name(value))
        for value in ('', 'UpperCase', '-cluster', 'cluster-', 'a' * 64, 'cluster.name'):
            with self.subTest(name=value):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.dns_name(value)
        self.assertEqual('', validators.description(''))
        self.assertEqual('a' * 1024, validators.description('a' * 1024))
        with self.assertRaises(InvalidArgumentValueError):
            validators.description('a' * 1025)

    def test_horizondb_selector_name_contract_boundaries(self):
        for parse, maximum in ((validators.firewall_name, 128), (validators.parameter_group_name, 63)):
            for value in ('Ab', '1-Test-2', 'a' * maximum):
                self.assertEqual(value, parse(value))
            for value in ('', 'a', '-ab', 'ab-', 'ab.cd', 'ab_cd', 'a' * (maximum + 1)):
                with self.subTest(selector=parse.__name__, value=value):
                    with self.assertRaises(InvalidArgumentValueError):
                        parse(value)
        for value in ('00000000-0000-0000-0000-000000000000', 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE'):
            self.assertEqual(value, validators.guid(value))
        for value in ('not-a-guid', '00000000000000000000000000000000',
                      '{00000000-0000-0000-0000-000000000000}'):
            with self.subTest(log_file=value):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.guid(value)

    def test_horizondb_utc_datetime_requires_valid_utc_date(self):
        for value in ('2026-10-01T12:00:00Z', '2026-10-01T12:00:00+00:00',
                      '2026-10-01T12:00:00.123Z'):
            self.assertEqual(value, validators.utc_datetime(value))
        for value in ('2026-02-30T12:00:00Z', '2026-10-01', '2026-10-01T12:00:00-07:00',
                      '2026-10-01T12:00:00'):
            with self.subTest(timestamp=value):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.utc_datetime(value)

    def test_horizondb_key_reference_is_versionless_https(self):
        uri = 'https://example-vault.vault.azure.net/keys/example-key'
        self.assertEqual(uri, validators.key_uri(uri))
        for value in (uri + '/version', uri + '?secret=token', uri + '#fragment',
                      uri.replace('https:', 'http:'), 'https://user:password@vault/keys/key'):
            with self.subTest(uri=value):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.key_uri(value)

    def test_horizondb_identity_reference_requires_full_uami_id(self):
        identity = ('/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/example-rg/'
                    'providers/Microsoft.ManagedIdentity/userAssignedIdentities/example-identity')
        self.assertEqual(identity, validators.identity_reference(identity))
        for value in ('example-identity', identity.replace('Microsoft.ManagedIdentity', 'Microsoft.HorizonDb')):
            with self.subTest(identity=value):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.identity_reference(value)

    def test_horizondb_topology_tuples_and_sparse_values(self):
        for count, mode in ((1, 'Disabled'), (2, 'AnyZone'), (16, 'ZoneRedundant'),
                            (None, 'Disabled'), (2, None)):
            validators.validate_topology(count, mode)
        for count, mode in ((1, 'AnyZone'), (1, 'ZoneRedundant'), (2, 'Disabled')):
            with self.subTest(count=count, mode=mode):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.validate_topology(count, mode)

    def test_horizondb_firewall_requires_ordered_ipv4_range(self):
        validators.validate_ipv4_range('203.0.113.10', '203.0.113.10')
        validators.validate_ipv4_range('203.0.113.10', '203.0.113.20')
        for start, end in (('203.0.113.20', '203.0.113.10'),
                           ('::1', '::1'), ('not-an-ip', '203.0.113.10')):
            with self.subTest(start=start, end=end):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.validate_ipv4_range(start, end)

    def test_horizondb_sparse_update_counts_false_empty_and_zero_as_changes(self):
        for value in (False, '', 0, {}, []):
            validators.require_change(value=value)
        with self.assertRaises(RequiredArgumentMissingError):
            validators.require_change(value=None, other=None)

    def test_horizondb_parameter_json_rejects_read_only_metadata_and_duplicates(self):
        values = [{'name': 'work_mem', 'value': '64MB'}]
        self.assertEqual(values, validators.parameter_values(values))
        for value in ({'work_mem': '64MB'}, values + values,
                      [{'name': 'work_mem', 'value': '64MB', 'defaultValue': '4MB'}],
                      [{'name': 'work_mem', 'value': 64}]):
            with self.subTest(parameters=value):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.parameter_values(value)

    def test_horizondb_parameter_cli_entries_json_and_file_have_same_writable_shape(self):
        values = [{'name': 'work_mem', 'value': '64MB'}, {'name': 'custom_setting', 'value': 'a=b'}]
        self.assertEqual(values, validators.parameter_values(['work_mem=64MB', 'custom_setting=a=b']))
        self.assertEqual(values[:1], validators.parameter_values('work_mem=64MB'))
        self.assertEqual(values[:1], validators.parameter_values(['[{"name":"work_mem","value":"64MB"}]']))
        self.assertEqual([{'name': 'work_mem', 'value': ''}], validators.parameter_values(['work_mem=']))
        with mock.patch.object(validators, 'get_file_json', return_value=values) as read_file:
            self.assertEqual(values, validators.parameter_values(['@parameters.json']))
            read_file.assert_called_once_with('parameters.json')
        for value in (['work_mem'], ['=64MB'], ['work_mem=64MB', 'work_mem=96MB']):
            with self.subTest(parameters=value):
                with self.assertRaises(InvalidArgumentValueError):
                    validators.parameter_values(value)


if __name__ == '__main__':
    unittest.main()
