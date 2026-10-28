# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Offline workflow tests using the published SDK and a stateful HTTP transport, never Azure."""

import copy
import json
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import urlsplit

from azure.core.exceptions import HttpResponseError
from azure.mgmt.horizondb import HorizonDBMgmtClient
from azure.cli.core.azclierror import (
    AzureResponseError, InvalidArgumentValueError, RequiredArgumentMissingError)
from azure.cli.core.mock import DummyCli
from azure.cli.command_modules.horizondb import _create

from .test_horizondb_sdk import _Credential, _Response, _Transport, SUBSCRIPTION, RESOURCE_GROUP, CLUSTER
from .test_horizondb_commands import _loader
from .test_horizondb_help import EXPECTED_COMMANDS, REFERENCE_COMMANDS


LOCATION = 'indonesiacentral'
OBJECT_ID = '11111111-1111-1111-1111-111111111111'
TENANT_ID = '22222222-2222-2222-2222-222222222222'
PREFIX = f'/subscriptions/{SUBSCRIPTION}/providers/Microsoft.HorizonDb/locations/{LOCATION}'
CLUSTER_PATH = (f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/'
                f'providers/Microsoft.HorizonDb/clusters/{CLUSTER}')
DEFAULT_ID = PREFIX + '/parameterGroups/default_pg17'


class _WorkflowTransport(_Transport):

    def __init__(self):
        super().__init__()
        self.resources = {
            DEFAULT_ID: {'id': DEFAULT_ID, 'name': 'default_pg17',
                         'properties': {'postgreSqlVersion': '17'}},
        }
        self.catalog = {
            'properties': {
                'clusterCapabilities': {'supportedPostgreSqlVersions': [{'name': '17'}]},
                'poolCapabilities': {'supportedProcessors': [{
                    'name': 'Intel', 'supportedGenerations': [{
                        'computeGeneration': 1, 'supportedComputeModels': [{
                            'type': 'Provisioned', 'supportedComputeSizes': [{'vCores': 8}, {'vCores': 2}],
                        }],
                    }],
                }]},
            },
        }
        self.fail_path = None
        self.fail_method = 'PUT'
        self.fail_property = None
        self.failure_status = 403
        self.endpoint_ready = True
        self.omit_patch_response_tags = False
        self.ignore_ai_updates = False
        self.forced_authentication_state = None

    def send(self, request, **_kwargs):
        self.requests.append(request)
        path = urlsplit(request.url).path
        if (path == self.fail_path and request.method == self.fail_method and
                (self.fail_property is None or
                 self.fail_property in json.loads(request.body).get('properties', {}))):
            return _Response(request, {'error': {'code': 'AuthorizationFailed', 'message': 'test-denied'}},
                             self.failure_status)
        if request.method == 'GET':
            if path == PREFIX + '/capabilities':
                return _Response(request, {'value': [self.catalog]})
            if path == CLUSTER_PATH + '/endpoints':
                return _Response(request, {'value': [{
                    'id': CLUSTER_PATH + '/endpoints/' + CLUSTER,
                    'properties': {
                        'fullyQualifiedDomainName': 'example.horizondb.azure.com',
                        'scope': 'Cluster', 'role': 'ReadWrite',
                    },
                }] if self.endpoint_ready else []})
            if path in self.resources:
                return _Response(request, self.resources[path])
            if path.endswith(('/pools', '/firewallRules', '/microsoftEntraAdministrators', '/parameterGroups')):
                return _Response(request, {'value': [
                    resource for resource_path, resource in self.resources.items()
                    if resource_path.rsplit('/', 1)[0] == path
                ]})
            return _Response(request, {'error': {'code': 'ResourceNotFound', 'message': 'test-absent'}}, 404)
        if request.method in ('PUT', 'PATCH'):
            body = json.loads(request.body)
            if self.forced_authentication_state:
                method, state = self.forced_authentication_state
                if path.endswith('/authentications/' + method):
                    body['properties']['state'] = state
            if self.ignore_ai_updates:
                body.get('properties', {}).pop('aiModelManagement', None)
            previous = copy.deepcopy(self.resources.get(path, {}))
            if request.method == 'PATCH':
                previous.setdefault('properties', {}).update(body.pop('properties', {}))
                previous.update(body)
                body = previous
            body.setdefault('id', path)
            body.setdefault('name', path.rsplit('/', 1)[-1])
            body.setdefault('properties', {})['provisioningState'] = 'Succeeded'
            self.resources[path] = body
            response = copy.deepcopy(body)
            if request.method == 'PATCH' and self.omit_patch_response_tags:
                response.pop('tags', None)
            return _Response(request, response)
        raise AssertionError('Unexpected HTTP operation: ' + request.method + ' ' + path)

    def writes(self):
        return [request for request in self.requests if request.method != 'GET']


class HorizonDBCreateTest(unittest.TestCase):

    def setUp(self):
        self.transport = _WorkflowTransport()
        self.client = HorizonDBMgmtClient(
            _Credential(), SUBSCRIPTION, api_version='2026-10-01-preview',
            transport=self.transport, retry_total=0, polling_interval=0)
        self.addCleanup(self.client.close)
        cli = DummyCli()
        cli.only_show_errors = True
        self.cmd = SimpleNamespace(cli_ctx=cli)
        self.profile = mock.patch(
            'azure.cli.core._profile.Profile.get_subscription',
            return_value={'tenantId': TENANT_ID, 'user': {'type': 'user', 'name': 'user@example.com'}}).start()
        self.addCleanup(mock.patch.stopall)
        self.resource_client = mock.patch(
            'azure.cli.core.commands.client_factory.get_mgmt_service_client').start()
        self.resource_client.return_value.resource_groups.get.return_value.location = LOCATION
        self.graph_factory = mock.patch('azure.cli.command_modules.role.graph_client_factory').start()
        self.graph_factory.return_value.signed_in_user_get.return_value = {
            'id': OBJECT_ID, 'userPrincipalName': 'user@example.com'}

    def create(self, **kwargs):
        kwargs.setdefault('public_access', ['203.0.113.10'])
        return _create.horizondb_create(self.cmd, self.client, RESOURCE_GROUP, CLUSTER, **kwargs)

    def body(self, suffix, method='PUT'):
        requests = [
            request for request in self.transport.requests
            if urlsplit(request.url).path.endswith(suffix) and request.method == method
        ]
        self.assertEqual(1, len(requests), suffix)
        return json.loads(requests[0].body)

    def test_horizondb_create_default_entra_complete_actual_sdk_workflow_and_safe_output(self):
        output = self.create()
        cluster = self.body('/clusters/' + CLUSTER)
        self.assertEqual(LOCATION, cluster['location'])
        self.assertEqual('17', cluster['properties']['postgreSqlVersion'])
        self.assertNotIn('aiModelManagement', cluster['properties'])
        self.assertEqual({'type': 'SystemManaged'}, cluster['properties']['dataEncryption'])
        self.assertEqual({'publicAccess': 'Enabled'}, cluster['properties']['network'])
        self.assertEqual('v1', cluster['tags']['horizondb-cli-create'])
        pool = self.body('/pools/' + CLUSTER + '-pool')['properties']
        self.assertEqual({'type': 'Provisioned', 'computeProcessor': 'Intel',
                          'computeGeneration': 1, 'vCores': 2}, pool['computeModel'])
        self.assertEqual(1, pool['nodeCount'])
        self.assertEqual({'mode': 'Disabled'}, pool['highAvailability'])
        self.assertEqual({'maintenanceManagementType': 'System'}, pool['scheduledMaintenance'])
        self.assertEqual({'parameterGroupResourceId': DEFAULT_ID, 'applyMethod': 'Immediate'}, pool['parameterGroup'])
        self.assertEqual({'state': 'Disabled'}, self.body('/authentications/passwordMethod')['properties'])
        self.assertEqual({'state': 'Enabled'}, self.body('/authentications/microsoftEntra')['properties'])
        self.assertEqual({'principalName': 'user@example.com', 'principalType': 'User', 'tenantId': TENANT_ID},
                         self.body('/microsoftEntraAdministrators/' + OBJECT_ID)['properties'])
        self.assertEqual({'startIpAddress': '203.0.113.10', 'endIpAddress': '203.0.113.10'},
                         self.body('/firewallRules/cli-create-1')['properties'])
        patches = [json.loads(request.body) for request in self.transport.requests if request.method == 'PATCH']
        self.assertIn({'properties': {'aiModelManagement': 'Disabled'}}, patches)
        self.assertEqual({'cluster', 'pool', 'connection', 'firewall', 'aiModelManagement'}, set(output))
        self.assertEqual('Entra', output['connection']['authentication'])
        self.assertEqual('example.horizondb.azure.com', output['connection']['endpoints'][0]['host'])
        self.assertEqual('Disabled', output['aiModelManagement'])
        self.assertNotIn('user@example.com', json.dumps(output))
        self.assertNotIn('password', json.dumps(output).lower())

    def test_horizondb_create_authentication_states_must_be_confirmed_before_checkpointing(self):
        initial = copy.deepcopy(self.transport.resources)
        for method, state in (('passwordMethod', 'Enabled'), ('microsoftEntra', 'Disabled')):
            with self.subTest(method=method):
                self.transport.resources = copy.deepcopy(initial)
                self.transport.requests.clear()
                self.transport.forced_authentication_state = (method, state)
                with self.assertRaisesRegex(AzureResponseError, 'authentication state'):
                    self.create()
                tags = self.transport.resources[CLUSTER_PATH]['tags']
                self.assertNotIn('horizondb-cli-create-auth', tags)
                self.assertNotIn('horizondb-cli-create-complete', tags)

    def test_horizondb_create_successful_rerun_is_read_only_and_uses_identical_metadata(self):
        first = self.create()
        self.transport.requests.clear()
        second = self.create()
        self.assertEqual(first, second)
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_password_and_both_modes_use_real_sdk_and_never_echo_credentials(self):
        for mode in ('Password', 'Both'):
            with self.subTest(mode=mode):
                transport = _WorkflowTransport()
                with HorizonDBMgmtClient(
                        _Credential(), SUBSCRIPTION, transport=transport, retry_total=0, polling_interval=0) as client:
                    output = _create.horizondb_create(
                        self.cmd, client, RESOURCE_GROUP, CLUSTER, authentication=mode,
                        public_access=['203.0.113.10'], administrator_login_password='Secret-Test!123')
                bodies = [json.loads(request.body) for request in transport.writes()]
                password = next(body for body in bodies
                                if body.get('properties', {}).get('administratorLoginPassword'))
                self.assertEqual('hdbadmin', password['properties']['administratorLogin'])
                self.assertEqual('Secret-Test!123', password['properties']['administratorLoginPassword'])
                self.assertNotIn('Secret-Test!123', json.dumps(output))
                entra = transport.resources[CLUSTER_PATH + '/authentications/microsoftEntra']
                self.assertEqual('Enabled' if mode == 'Both' else 'Disabled', entra['properties']['state'])

    def test_horizondb_create_resume_never_resets_an_existing_enabled_password_even_if_supplied(self):
        self.create(authentication='Password', administrator_login_password='Initial-Test!123')
        path = CLUSTER_PATH + '/authentications/passwordMethod'
        self.transport.resources[path]['properties']['administratorLoginPassword'] = 'Rotated-Out-Of-Band!123'
        self.transport.requests.clear()
        self.create(authentication='Password', administrator_login_password='Different-Test!123')
        self.assertEqual([], self.transport.writes())
        self.assertEqual('Rotated-Out-Of-Band!123',
                         self.transport.resources[path]['properties']['administratorLoginPassword'])

    def test_horizondb_create_partial_resume_preserves_a_password_rotated_after_a_later_stage_failed(self):
        self.transport.fail_path = CLUSTER_PATH + '/pools/' + CLUSTER + '-pool/firewallRules/cli-create-1'
        with self.assertRaises(HttpResponseError):
            self.create(authentication='Both', administrator_login_password='Initial-Test!123')
        path = CLUSTER_PATH + '/authentications/passwordMethod'
        self.transport.resources[path]['properties']['administratorLoginPassword'] = 'Rotated-Test!123'
        self.transport.fail_path = None
        self.transport.requests.clear()
        with mock.patch('knack.prompting.prompt_pass') as prompt:
            self.create(authentication='Both')
        prompt.assert_not_called()
        self.assertFalse(any('administratorLoginPassword' in (request.body or '')
                             for request in self.transport.writes()))
        self.assertEqual('Rotated-Test!123', self.transport.resources[path]['properties']['administratorLoginPassword'])

    def test_horizondb_create_partial_resume_rejects_changed_disabled_password_without_resetting_it(self):
        self.transport.fail_path = CLUSTER_PATH + '/pools/' + CLUSTER + '-pool/firewallRules/cli-create-1'
        with self.assertRaises(HttpResponseError):
            self.create(authentication='Both', administrator_login_password='Initial-Test!123')
        path = CLUSTER_PATH + '/authentications/passwordMethod'
        properties = self.transport.resources[path]['properties']
        properties.update(state='Disabled', administratorLoginPassword='Rotated-Test!123')
        properties.pop('administratorLogin', None)  # Read responses need not disclose a login.
        self.transport.fail_path = None
        self.transport.requests.clear()
        with mock.patch('knack.prompting.prompt_pass') as prompt:
            with self.assertRaisesRegex(InvalidArgumentValueError, 'password authentication differs'):
                self.create(authentication='Both')
        prompt.assert_not_called()
        self.assertEqual([], self.transport.writes())
        self.assertEqual('Rotated-Test!123', properties['administratorLoginPassword'])

    def test_horizondb_create_resume_after_pool_permission_failure_does_not_replace_cluster(self):
        self.transport.fail_path = CLUSTER_PATH + '/pools/' + CLUSTER + '-pool'
        with self.assertLogs(_create.logger, level='ERROR') as logs:
            with self.assertRaises(HttpResponseError) as caught:
                self.create()
        self.assertEqual(403, caught.exception.status_code)
        self.assertIn('pool creation', logs.output[-1])
        self.assertIn(CLUSTER_PATH, self.transport.resources)
        self.assertFalse(any(request.method == 'DELETE' for request in self.transport.requests))
        self.transport.fail_path = None
        self.transport.requests.clear()
        self.create()
        self.assertFalse(any(request.method == 'PUT' and urlsplit(request.url).path == CLUSTER_PATH
                             for request in self.transport.requests))

    def test_horizondb_create_checkpoint_preserves_customer_tags_when_sdk_patch_response_omits_tags(self):
        self.transport.fail_path = CLUSTER_PATH + '/pools/' + CLUSTER + '-pool/firewallRules/cli-create-1'
        with self.assertRaises(HttpResponseError):
            self.create()
        self.transport.resources[CLUSTER_PATH]['tags']['customer-tag'] = 'preserve-me'
        self.transport.omit_patch_response_tags = True
        self.transport.fail_path = None
        self.create()
        self.assertEqual('preserve-me', self.transport.resources[CLUSTER_PATH]['tags']['customer-tag'])
        self.assertEqual('true', self.transport.resources[CLUSTER_PATH]['tags']['horizondb-cli-create-complete'])

    def test_horizondb_create_resume_conflicts_are_rejected_before_any_writes(self):
        self.create()
        original = copy.deepcopy(self.transport.resources)
        cases = (
            ('changed intent', lambda: None, {'public_access': ['203.0.113.11']}),
            ('foreign owner', lambda: self.transport.resources[CLUSTER_PATH]['tags'].clear(), {}),
            ('version', lambda: self.transport.resources[CLUSTER_PATH]['properties'].update(
                postgreSqlVersion='18'), {}),
            ('network', lambda: self.transport.resources[CLUSTER_PATH]['properties'].update(
                network={'publicAccess': 'Disabled'}), {}),
            ('encryption', lambda: self.transport.resources[CLUSTER_PATH]['properties'].update(
                dataEncryption={'type': 'AzureKeyVault'}), {}),
            ('compute', lambda: self.transport.resources[CLUSTER_PATH + '/pools/' + CLUSTER + '-pool'][
                'properties']['computeModel'].update(vCores=8), {}),
            ('topology', lambda: self.transport.resources[CLUSTER_PATH + '/pools/' + CLUSTER + '-pool'][
                'properties'].update(nodeCount=2), {}),
            ('HA', lambda: self.transport.resources[CLUSTER_PATH + '/pools/' + CLUSTER + '-pool'][
                'properties']['highAvailability'].update(mode='AnyZone'), {}),
            ('maintenance', lambda: self.transport.resources[CLUSTER_PATH + '/pools/' + CLUSTER + '-pool'][
                'properties']['scheduledMaintenance'].update(maintenanceManagementType='Customer'), {}),
            ('parameter group', lambda: self.transport.resources[CLUSTER_PATH + '/pools/' + CLUSTER + '-pool'][
                'properties']['parameterGroup'].update(parameterGroupResourceId=DEFAULT_ID + '-changed'), {}),
            ('parameter apply', lambda: self.transport.resources[CLUSTER_PATH + '/pools/' + CLUSTER + '-pool'][
                'properties']['parameterGroup'].update(applyMethod='PendingRestart'), {}),
            ('pool stopped', lambda: self.transport.resources[CLUSTER_PATH + '/pools/' + CLUSTER + '-pool'][
                'properties'].update(state='Stopped'), {}),
            ('password', lambda: self.transport.resources[CLUSTER_PATH + '/authentications/passwordMethod'][
                'properties'].update(state='Enabled'), {}),
            ('entra', lambda: self.transport.resources[CLUSTER_PATH + '/authentications/microsoftEntra'][
                'properties'].update(state='Disabled'), {}),
            ('AI', lambda: self.transport.resources[CLUSTER_PATH]['properties'].update(
                aiModelManagement='Enabled'), {}),
            ('firewall', lambda: self.transport.resources[
                CLUSTER_PATH + '/pools/' + CLUSTER + '-pool/firewallRules/cli-create-1']['properties'].update(
                    endIpAddress='255.255.255.255'), {}),
        )
        for label, mutate, arguments in cases:
            with self.subTest(conflict=label):
                self.transport.resources = copy.deepcopy(original)
                self.transport.requests.clear()
                mutate()
                with self.assertRaisesRegex(InvalidArgumentValueError, 'conflicts'):
                    self.create(**arguments)
                self.assertEqual([], self.transport.writes())

    def test_horizondb_create_explicit_ranges_bypass_ip_discovery_and_are_exactly_serialized(self):
        with mock.patch(
                'azure.cli.command_modules.rdbms.flexible_server_virtual_network.prepare_public_network') as ip:
            self.create(public_access=['203.0.113.20-203.0.113.22', '198.51.100.1/30', '203.0.113.10'])
        ip.assert_not_called()
        rules = [resource['properties'] for path, resource in self.transport.resources.items()
                 if '/firewallRules/' in path]
        actual = {(rule['startIpAddress'], rule['endIpAddress']) for rule in rules}
        self.assertEqual({('203.0.113.20', '203.0.113.22'), ('198.51.100.0', '198.51.100.3'),
                          ('203.0.113.10', '203.0.113.10')}, actual)

    def test_horizondb_create_default_ip_helper_can_only_produce_single_caller_rule(self):
        with mock.patch(
                'azure.cli.command_modules.rdbms.flexible_server_virtual_network.prepare_public_network',
                return_value=('203.0.113.10', '203.0.113.10')) as ip:
            self.create(public_access=None)
        ip.assert_called_once_with(None, yes=True)
        body = self.body('/firewallRules/cli-create-1')
        self.assertEqual('203.0.113.10', body['properties']['startIpAddress'])
        self.assertEqual('203.0.113.10', body['properties']['endIpAddress'])

    def test_horizondb_create_ip_lookup_failure_or_unexpected_range_never_provisions(self):
        from knack.util import CLIError
        target = 'azure.cli.command_modules.rdbms.flexible_server_virtual_network.prepare_public_network'
        failure = CLIError('lookup unavailable')
        with mock.patch(target, side_effect=failure):
            with self.assertRaisesRegex(InvalidArgumentValueError, 'explicit --public-access') as caught:
                self.create(public_access=None)
        self.assertIs(failure, caught.exception.__cause__)
        with mock.patch(target, return_value=('0.0.0.0', '255.255.255.255')):
            with self.assertRaisesRegex(AzureResponseError, 'single IPv4'):
                self.create(public_access=None)
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_invalid_network_and_credentials_never_write(self):
        for rules in ([], ['::1'], ['all'], ['0.0.0.0'], ['203.0.113.22-203.0.113.20'], ['not-an-ip']):
            with self.subTest(rules=rules), self.assertRaises(InvalidArgumentValueError):
                self.create(public_access=rules)
        with self.assertRaises(InvalidArgumentValueError):
            self.create(administrator_login_password='Entra-Does-Not-Use-Passwords!123')
        with self.assertRaises(InvalidArgumentValueError):
            self.create(authentication='Password', administrator_login_password='short')
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_password_prompt_is_confirmed_before_first_write(self):
        def prompt(*_args, **kwargs):
            self.assertTrue(kwargs['confirm'])
            self.assertEqual([], self.transport.writes())
            return 'Prompted-Test!123'
        with mock.patch('knack.prompting.prompt_pass', side_effect=prompt) as prompted:
            self.create(authentication='Password')
        prompted.assert_called_once()
        self.assertEqual('Prompted-Test!123',
                         self.body('/authentications/passwordMethod')['properties']['administratorLoginPassword'])

    def test_horizondb_create_missing_noninteractive_password_fails_before_provisioning(self):
        from knack.prompting import NoTTYException
        with mock.patch('knack.prompting.prompt_pass', side_effect=NoTTYException):
            with self.assertRaises(RequiredArgumentMissingError):
                self.create(authentication='Password')
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_explicit_administrator_identifiers_avoid_graph_and_serialize_exactly(self):
        self.create(administrator_object_id=OBJECT_ID, administrator_principal_name='test-app',
                    administrator_principal_type='ServicePrincipal', tenant_id=TENANT_ID)
        self.graph_factory.assert_not_called()
        self.assertEqual({'principalName': 'test-app', 'principalType': 'ServicePrincipal', 'tenantId': TENANT_ID},
                         self.body('/microsoftEntraAdministrators/' + OBJECT_ID)['properties'])

    def test_horizondb_create_service_principal_resolution_uses_spn_not_me(self):
        self.profile.return_value['user'] = {'type': 'servicePrincipal', 'name': 'application-id'}
        self.graph_factory.return_value.service_principal_get.return_value = {'displayName': 'test-application'}
        with mock.patch('azure.cli.command_modules.role.util.get_object_id', return_value=OBJECT_ID) as resolve:
            self.create()
        resolve.assert_called_once_with(self.cmd.cli_ctx, spn='application-id')
        self.graph_factory.return_value.signed_in_user_get.assert_not_called()
        self.graph_factory.return_value.service_principal_get.assert_called_once_with(OBJECT_ID)
        self.assertEqual('test-application',
                         self.body('/microsoftEntraAdministrators/' + OBJECT_ID)['properties']['principalName'])

    def test_horizondb_create_unresolvable_service_connection_fails_actionably_before_writes(self):
        self.profile.return_value['user'] = {'type': 'servicePrincipal', 'name': 'application-id'}
        with mock.patch('azure.cli.command_modules.role.util.get_object_id', return_value=None):
            with self.assertRaisesRegex(RequiredArgumentMissingError, 'application ID is not an object ID'):
                self.create()
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_directory_permission_error_explains_explicit_identifiers(self):
        from azure.cli.command_modules.role import GraphError
        error = GraphError('directory read denied', response=None)
        self.graph_factory.return_value.signed_in_user_get.side_effect = error
        with self.assertRaisesRegex(AzureResponseError, '--administrator-object-id') as caught:
            self.create()
        self.assertIs(error, caught.exception.__cause__)
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_partial_or_invalid_administrator_inputs_fail_before_writes(self):
        cases = (
            {'administrator_object_id': OBJECT_ID},
            {'authentication': 'Password', 'administrator_object_id': OBJECT_ID},
            {'administrator_object_id': 'not-an-object-id', 'administrator_principal_name': 'test-app',
             'administrator_principal_type': 'ServicePrincipal'},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises((InvalidArgumentValueError, RequiredArgumentMissingError)):
                    self.create(**arguments)
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_regional_compute_version_and_default_prerequisites_precede_writes(self):
        for arguments in ({'postgresql_version': '18'}, {'v_cores': 4}):
            with self.subTest(arguments=arguments), self.assertRaises(InvalidArgumentValueError):
                self.create(**arguments)
        self.transport.resources.clear()
        with self.assertRaisesRegex(AzureResponseError, 'default parameter group'):
            self.create()
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_flat_live_catalog_fields_are_accepted_without_fabricating_wrapper(self):
        self.transport.catalog = self.transport.catalog['properties']
        self.transport.catalog['location'] = LOCATION
        with self.assertLogs(_create.logger, level='WARNING') as logs:
            output = self.create()
        self.assertEqual(2, output['pool']['vCores'])
        self.assertNotIn('properties', self.transport.catalog)
        self.assertTrue(any('published resource wrapper' in log for log in logs.output))

    def test_horizondb_create_generation_one_is_selected_even_when_generation_two_is_smaller(self):
        processor = self.transport.catalog['properties']['poolCapabilities']['supportedProcessors'][0]
        processor['supportedGenerations'].append({
            'computeGeneration': 2,
            'supportedComputeModels': [{'type': 'Provisioned', 'supportedComputeSizes': [{'vCores': 1}]}],
        })
        output = self.create()
        self.assertEqual(2, output['pool']['vCores'])
        self.assertEqual(1, self.body('/pools/' + CLUSTER + '-pool')['properties']['computeModel']['computeGeneration'])

    def test_horizondb_create_existing_additional_pool_administrator_or_firewall_is_not_replaced(self):
        self.create()
        original = copy.deepcopy(self.transport.resources)
        paths = (
            (CLUSTER_PATH + '/pools/unexpected', {'name': 'unexpected', 'properties': {}}),
            (CLUSTER_PATH + '/microsoftEntraAdministrators/' + TENANT_ID,
             {'name': TENANT_ID, 'properties': {}}),
            (CLUSTER_PATH + '/pools/' + CLUSTER + '-pool/firewallRules/extra',
             {'name': 'extra', 'properties': {'startIpAddress': '0.0.0.0', 'endIpAddress': '255.255.255.255'}}),
        )
        for path, resource in paths:
            with self.subTest(path=path):
                self.transport.resources = copy.deepcopy(original)
                self.transport.resources[path] = resource
                self.transport.requests.clear()
                with self.assertRaisesRegex(InvalidArgumentValueError, 'conflicts'):
                    self.create()
                self.assertEqual([], self.transport.writes())

    def test_horizondb_create_all_write_stage_permission_failures_retain_resources_and_original_error(self):
        cases = (
            ('authentication configuration', CLUSTER_PATH + '/authentications/passwordMethod', 'PUT', None),
            ('authentication configuration', CLUSTER_PATH + '/microsoftEntraAdministrators/' + OBJECT_ID, 'PUT', None),
            ('authentication checkpoint', CLUSTER_PATH, 'PATCH', None),
            ('firewall configuration', CLUSTER_PATH + '/pools/' + CLUSTER + '-pool/firewallRules/cli-create-1',
             'PUT', None),
            ('AI configuration', CLUSTER_PATH, 'PATCH', 'aiModelManagement'),
        )
        for stage, path, method, property_name in cases:
            with self.subTest(stage=stage, path=path):
                self.transport.resources = {DEFAULT_ID: {
                    'id': DEFAULT_ID, 'name': 'default_pg17', 'properties': {'postgreSqlVersion': '17'}}}
                self.transport.fail_path, self.transport.fail_method = path, method
                self.transport.fail_property = property_name
                self.transport.requests.clear()
                with self.assertLogs(_create.logger, level='ERROR') as logs:
                    with self.assertRaises(HttpResponseError) as caught:
                        self.create()
                self.assertEqual(403, caught.exception.status_code)
                self.assertIn('test-denied', str(caught.exception))
                self.assertTrue(any(stage in message for message in logs.output))
                self.assertIn(CLUSTER_PATH, self.transport.resources)
                self.assertFalse(any(request.method == 'DELETE' for request in self.transport.requests))

    def test_horizondb_create_ai_explicit_opt_in_uses_existing_patch_field(self):
        self.create(enable_ai=True, v_cores=8)
        patches = [json.loads(request.body) for request in self.transport.requests if request.method == 'PATCH']
        self.assertIn({'properties': {'aiModelManagement': 'Enabled'}}, patches)
        self.assertEqual(8, self.body('/pools/' + CLUSTER + '-pool')['properties']['computeModel']['vCores'])
        self.transport.requests.clear()
        self.create(enable_ai=True, v_cores=8)
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_success_requires_get_confirmation_of_explicit_ai_patch(self):
        self.transport.ignore_ai_updates = True
        with self.assertRaisesRegex(AzureResponseError, 'did not confirm.*after PATCH'):
            self.create()
        self.assertNotIn('aiModelManagement', self.transport.resources[CLUSTER_PATH]['properties'])
        self.assertNotIn('horizondb-cli-create-complete', self.transport.resources[CLUSTER_PATH]['tags'])
        self.assertIn({'properties': {'aiModelManagement': 'Disabled'}}, [
            json.loads(request.body) for request in self.transport.requests if request.method == 'PATCH'])
        self.assertFalse(any(request.method == 'DELETE' for request in self.transport.requests))

    def test_horizondb_create_read_errors_are_not_misinterpreted_as_absence(self):
        self.transport.fail_path, self.transport.fail_method = CLUSTER_PATH, 'GET'
        with self.assertRaises(HttpResponseError) as caught:
            self.create()
        self.assertEqual(403, caught.exception.status_code)
        self.assertEqual([], self.transport.writes())

    def test_horizondb_create_missing_connection_endpoint_fails_without_rollback(self):
        self.transport.endpoint_ready = False
        with self.assertRaisesRegex(AzureResponseError, 'read-write Cluster endpoint'):
            self.create()
        self.assertIn(CLUSTER_PATH, self.transport.resources)
        self.assertFalse(any(request.method == 'DELETE' for request in self.transport.requests))
        self.transport.endpoint_ready = True
        self.transport.requests.clear()
        self.create()
        self.assertFalse(any(request.method == 'PUT' for request in self.transport.requests))

    def test_horizondb_create_read_only_resume_waiter_rejects_failed_or_timed_out_resources(self):
        resource = {'properties': {'provisioningState': 'Creating'}}
        getter = mock.Mock(return_value={'properties': {'provisioningState': 'Succeeded'}})
        with mock.patch.object(_create.time, 'monotonic', return_value=0):
            waiter = _create._ResourceWaiter(getter, resource)
        with mock.patch.object(_create.time, 'monotonic', return_value=6):
            self.assertFalse(waiter.done())
        self.assertTrue(waiter.done())
        getter.assert_called_once()
        with self.assertRaises(AzureResponseError):
            _create._ResourceWaiter(getter, {'properties': {'provisioningState': 'Failed'}}).done()
        with mock.patch.object(_create.time, 'monotonic', return_value=1801):
            waiter.resource = resource
            with self.assertRaisesRegex(AzureResponseError, 'Timed out'):
                waiter.done()

    def test_horizondb_create_inventory_retains_88_public_reference_commands_plus_create(self):
        self.assertEqual(88, len(REFERENCE_COMMANDS))
        self.assertEqual(89, len(EXPECTED_COMMANDS))
        self.assertEqual(REFERENCE_COMMANDS | {'horizondb create'}, EXPECTED_COMMANDS)
        command = _loader('horizondb create').command_table['horizondb create']
        self.assertFalse(command.command_kwargs.get('supports_no_wait'))
        self.assertNotIn('no_wait', command.arguments)
        self.assertIn('-n', command.arguments['cluster_name'].options_list)
        self.assertFalse(command.arguments['location'].options.get('required'))
        self.assertIn('horizondb._create#horizondb_create', command.command_kwargs['custom_command_type'].settings[
            'operations_tmpl'].format('horizondb_create'))
