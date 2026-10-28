# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from azure.core.credentials import AccessToken
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.mgmt.horizondb import HorizonDBMgmtClient
from azure.mgmt.horizondb import models

from azure.cli.command_modules.horizondb._client_factory import cf_horizondb
from azure.cli.command_modules.horizondb._discovery import _list_regional_capabilities
from azure.cli.core.azclierror import InvalidArgumentValueError, RequiredArgumentMissingError
from azure.cli.core.util import todict


SUBSCRIPTION = '00000000-0000-0000-0000-000000000000'
RESOURCE_GROUP = 'example-rg'
CLUSTER = 'example-cluster'
CLUSTER_ID = (f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/'
              f'providers/Microsoft.HorizonDb/clusters/{CLUSTER}')
IDENTITY_ID = (f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/'
               'providers/Microsoft.ManagedIdentity/userAssignedIdentities/example-identity')
DEFAULT_PARAMETER_GROUP_ID = (
    f'/subscriptions/{SUBSCRIPTION}/providers/Microsoft.HorizonDb/locations/eastus2/'
    'parameterGroups/postgresql-17')
DESTINATION_ID = CLUSTER_ID + '/changeDataCaptureDestinations/fabric-main'
ASSIGNMENT_ID = DESTINATION_ID + '/identityAssignments/primary'


class _Credential:

    def get_token(self, *_scopes, **_kwargs):
        return AccessToken('offline-test-token', int(time.time()) + 3600)


class _Response(HttpResponse):

    def __init__(self, request, payload, status_code=200):
        super().__init__(request, None)
        self.status_code = status_code
        self.headers = {'content-type': 'application/json'}
        self.reason = {200: 'OK', 404: 'Not Found'}.get(status_code, 'Error')
        self.content_type = 'application/json'
        self._body = json.dumps(payload).encode('utf-8')

    def body(self):
        return self._body

    def json(self):
        return json.loads(self._body)

    def iter_bytes(self, **_kwargs):
        yield self._body

    def read(self):
        return self._body

    def close(self):
        pass


class _Transport(HttpTransport):
    """Capture actual SDK requests without sockets, login, polling, or Azure resources."""

    def __init__(self):
        self.requests = []
        self.responses = []
        self.payload = {
            'id': CLUSTER_ID,
            'name': CLUSTER,
            'location': 'eastus2',
            'properties': {'provisioningState': 'Succeeded'},
        }

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def send(self, request, **_kwargs):
        self.requests.append(request)
        payload, status_code = self.responses.pop(0) if self.responses else (self.payload, 200)
        return _Response(request, payload, status_code)


class HorizonDBSDKTest(unittest.TestCase):

    def setUp(self):
        from azure.cli.command_modules.horizondb import custom
        self.custom = custom
        self.cmd = SimpleNamespace(get_models=lambda name: getattr(models, name))
        self.transport = _Transport()
        self.client = HorizonDBMgmtClient(
            _Credential(), SUBSCRIPTION, api_version='2026-10-01-preview',
            transport=self.transport, retry_total=0, polling_interval=0)
        self.addCleanup(self.client.close)

    def assert_request(self, method, suffix, body=None):
        request = self.transport.requests[-1]
        url = urlsplit(request.url)
        self.assertEqual(method, request.method)
        self.assertTrue(url.path.endswith(suffix), url.path)
        self.assertEqual(['2026-10-01-preview'], parse_qs(url.query).get('api-version'))
        if body is not None:
            self.assertEqual(body, json.loads(request.body))
        return request

    def test_horizondb_client_factory_pins_preview_api_version(self):
        context = mock.Mock()
        with mock.patch('azure.cli.command_modules.horizondb._client_factory.get_mgmt_service_client') as factory:
            self.assertIs(factory.return_value, cf_horizondb(context))
        factory.assert_called_once_with(context, HorizonDBMgmtClient, api_version='2026-10-01-preview')

    def test_horizondb_cluster_create_serializes_defaults(self):
        self.custom.cluster_create(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'eastus2', postgresql_version='17')
        self.assert_request('PUT', '/clusters/' + CLUSTER, {
            'location': 'eastus2',
            'properties': {'postgreSqlVersion': '17', 'creationMode': 'Create',
                           'network': {'publicAccess': 'Disabled'},
                           'dataEncryption': {'type': 'SystemManaged'}},
        })

    def test_horizondb_cdc_false_is_not_dropped_from_sparse_patch(self):
        self.custom.cdc_update(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, auto_upgrade_extension='false')
        self.assert_request('PATCH', '/changeDataCapture/default', {
            'properties': {'autoUpgradeExtension': False},
        })

    def test_horizondb_log_capture_patch_preserves_omitted_state(self):
        self.custom.log_capture_configuration_update(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'PostgreSqlServer', retention_days=1)
        self.assert_request('PATCH', '/logCaptureConfigurations/PostgreSqlServer', {
            'properties': {'retentionDays': 1},
        })

    def test_horizondb_fixed_service_resource_names(self):
        for handler, args, suffix in (
                ('password_authentication_show', (RESOURCE_GROUP, CLUSTER), '/authentications/passwordMethod'),
                ('microsoft_entra_authentication_show', (RESOURCE_GROUP, CLUSTER), '/authentications/microsoftEntra'),
                ('cdc_show', (RESOURCE_GROUP, CLUSTER), '/changeDataCapture/default')):
            with self.subTest(handler=handler):
                getattr(self.custom, handler)(self.client, *args)
                self.assert_request('GET', suffix)

    def test_horizondb_catalog_requests_use_exact_sdk_routes(self):
        self.transport.payload = {'value': []}
        self.assertEqual([], list(self.custom.operation_list(self.client)))
        request = self.assert_request('GET', '/providers/Microsoft.HorizonDb/operations')
        self.assertEqual('/providers/Microsoft.HorizonDb/operations', urlsplit(request.url).path)
        self.assertEqual([], list(self.custom.default_parameter_group_list(self.client, 'eastus')))
        self.assert_request('GET', '/locations/eastus/parameterGroups')
        self.custom.default_parameter_group_show(self.client, 'eastus', 'postgresql-17')
        self.assert_request('GET', '/locations/eastus/parameterGroups/postgresql-17')

    @staticmethod
    def _capability_payloads():
        properties = {
            'clusterCapabilities': {
                'recommendedPostgreSqlVersion': '17',
                'supportedPostgreSqlVersions': [{'name': '17', 'supportedVersionsToUpgrade': ['18']}],
                'supportedFeatures': [],
            },
            'poolCapabilities': {
                'recommendedComputeProcessor': 'Intel',
                'supportedProcessors': [],
                'supportedFeatures': [],
            },
        }
        resource_id = (f'/subscriptions/{SUBSCRIPTION}/providers/Microsoft.HorizonDb/'
                       'locations/indonesiacentral/capabilities/default')
        # Published SDK/API wrapper versus the flat shape observed in the real service response.
        return (
            {'id': resource_id, 'name': 'default', 'type': 'Microsoft.HorizonDb/locations/capabilities',
             'properties': properties},
            {'location': 'indonesiacentral', **properties},
        )

    def test_horizondb_internal_discovery_preserves_wrapper_and_flat_wire_fields(self):
        for wire in self._capability_payloads():
            with self.subTest(shape='wrapper' if 'properties' in wire else 'flat'):
                self.transport.payload = {'value': [wire]}
                result = _list_regional_capabilities(self.client, 'indonesiacentral')
                self.assertIsInstance(result, list)
                self.assertEqual([wire], todict(result))
                if 'properties' not in wire:
                    self.assertNotIn('properties', result[0])
                    self.assertNotIn('id', result[0])
                    self.assertEqual(wire['clusterCapabilities'], result[0]['clusterCapabilities'])
                    self.assertEqual(wire['poolCapabilities'], result[0]['poolCapabilities'])
                self.assert_request('GET', '/locations/indonesiacentral/capabilities')

    def test_horizondb_internal_discovery_preserves_sdk_paging(self):
        wrapped, flat = self._capability_payloads()
        next_link = (f'https://management.azure.com/subscriptions/{SUBSCRIPTION}/providers/Microsoft.HorizonDb/'
                     'locations/indonesiacentral/capabilities?api-version=2026-10-01-preview&$skiptoken=second')
        self.transport.responses = [
            ({'value': [wrapped], 'nextLink': next_link}, 200),
            ({'value': [flat]}, 200),
        ]
        result = _list_regional_capabilities(self.client, 'indonesiacentral')
        self.assertEqual([wrapped, flat], todict(result))
        self.assertEqual(2, len(self.transport.requests))
        self.assert_request('GET', '/locations/indonesiacentral/capabilities')
        self.assertEqual(['second'], parse_qs(urlsplit(self.transport.requests[-1].url).query)['$skiptoken'])
        self.assertFalse(self.transport.responses)

    def test_horizondb_internal_discovery_preserves_empty_collection_and_permission_errors(self):
        self.transport.payload = {'value': []}
        self.assertEqual([], _list_regional_capabilities(self.client, 'indonesiacentral'))
        self.assertEqual(1, len(self.transport.requests))
        self.assert_request('GET', '/locations/indonesiacentral/capabilities')
        self.transport.responses = [
            ({'error': {'code': 'AuthorizationFailed', 'message': 'Regional discovery denied.'}}, 403),
        ]
        with self.assertRaises(HttpResponseError) as caught:
            _list_regional_capabilities(self.client, 'indonesiacentral')
        self.assertEqual(403, caught.exception.status_code)
        self.assertIn('Regional discovery denied.', str(caught.exception))
        self.assertEqual(2, len(self.transport.requests))

    def test_horizondb_internal_discovery_propagates_sdk_page_errors(self):
        wrapped, _ = self._capability_payloads()
        next_link = (f'https://management.azure.com/subscriptions/{SUBSCRIPTION}/providers/Microsoft.HorizonDb/'
                     'locations/indonesiacentral/capabilities?api-version=2026-10-01-preview&$skiptoken=second')
        self.transport.responses = [
            ({'value': [wrapped], 'nextLink': next_link}, 200),
            ({'error': {'code': 'ResourceNotFound', 'message': 'Capability page was not found.'}}, 404),
        ]
        with self.assertRaises(ResourceNotFoundError) as caught:
            _list_regional_capabilities(self.client, 'indonesiacentral')
        self.assertEqual(404, caught.exception.status_code)
        self.assertIn('Capability page was not found.', str(caught.exception))
        self.assertEqual(2, len(self.transport.requests))

    def test_horizondb_destination_create_uses_fabric_native_mirroring(self):
        self.custom.cdc_destination_create(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'fabric-main',
            workspace_id='22222222-2222-2222-2222-222222222222', desired_state='Enabled')
        self.assert_request('PUT', '/changeDataCaptureDestinations/fabric-main', {
            'properties': {
                'destination': {'destinationType': 'Fabric', 'protocol': 'Native',
                                'workspaceId': '22222222-2222-2222-2222-222222222222'},
                'desiredState': 'Enabled',
            },
        })

    def test_horizondb_node_and_firewall_use_pool_child_paths(self):
        self.custom.node_show(self.client, RESOURCE_GROUP, CLUSTER, 'primary', 'node-001')
        self.assert_request('GET', '/pools/primary/nodes/node-001')
        self.custom.firewall_rule_create(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'primary', 'office-client',
            '203.0.113.10', '203.0.113.20', description='')
        self.assert_request('PUT', '/pools/primary/firewallRules/office-client', {
            'properties': {'startIpAddress': '203.0.113.10', 'endIpAddress': '203.0.113.20',
                           'description': ''},
        })

    def test_horizondb_pool_compute_patch_is_recursive_and_sparse(self):
        self.custom.pool_update(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'primary', v_cores=32)
        self.assert_request('PATCH', '/pools/primary', {
            'properties': {'computeModel': {'vCores': 32}},
        })

    def test_horizondb_cluster_identity_patch_removes_with_null_not_empty_object(self):
        self.transport.payload['identity'] = {
            'type': 'UserAssigned', 'userAssignedIdentities': {IDENTITY_ID: {}},
        }
        with mock.patch.object(self.client.change_data_capture_destinations, 'list', return_value=[]):
            self.custom.cluster_update(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER,
                remove_user_assigned_identities=[IDENTITY_ID])
        self.assert_request('PATCH', '/clusters/' + CLUSTER, {
            'identity': {'type': 'UserAssigned', 'userAssignedIdentities': {IDENTITY_ID: None}},
        })

    def test_horizondb_cluster_empty_tags_are_not_dropped(self):
        self.custom.cluster_update(self.cmd, self.client, RESOURCE_GROUP, CLUSTER, tags={})
        self.assert_request('PATCH', '/clusters/' + CLUSTER, {'tags': {}})

    def test_horizondb_cluster_customer_key_create_and_restore_bodies(self):
        key_uri = 'https://example-vault.vault.azure.net/keys/example-key'
        with mock.patch.object(self.client.clusters, 'get',
                               side_effect=ResourceNotFoundError('Not found')):
            self.custom.cluster_create(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'eastus2', postgresql_version='17',
                data_encryption_type='AzureKeyVault', primary_encryption_key_uri=key_uri,
                primary_encryption_identity=IDENTITY_ID, user_assigned_identities=[IDENTITY_ID],
                geo_redundancy='Disabled')
        self.assert_request('PUT', '/clusters/' + CLUSTER, {
            'location': 'eastus2',
            'identity': {'type': 'UserAssigned', 'userAssignedIdentities': {IDENTITY_ID: {}}},
            'properties': {
                'postgreSqlVersion': '17', 'creationMode': 'Create',
                'network': {'publicAccess': 'Disabled'},
                'backup': {'geoRedundancy': 'Disabled', 'retentionDays': 7},
                'dataEncryption': {'type': 'AzureKeyVault', 'primaryKeyUri': key_uri,
                                   'primaryUserAssignedIdentityId': IDENTITY_ID},
            },
        })
        self.custom.cluster_create(
            self.cmd, self.client, RESOURCE_GROUP, 'restored-cluster', 'westus2',
            creation_mode='PointInTimeRestore', creation_source_cluster=CLUSTER_ID,
            creation_source_cluster_location='eastus2', point_in_time='2026-10-01T12:00:00Z')
        self.assert_request('PUT', '/clusters/restored-cluster', {
            'location': 'westus2',
            'properties': {
                'creationMode': 'PointInTimeRestore', 'creationSourceClusterResourceId': CLUSTER_ID,
                'creationSourceClusterLocation': 'eastus2', 'pointInTimeUtc': '2026-10-01T12:00:00Z',
                'network': {'publicAccess': 'Disabled'}, 'dataEncryption': {'type': 'SystemManaged'},
            },
        })

    def test_horizondb_password_patch_is_sparse_and_disables_http_logging(self):
        password = 'offline-generated-password'
        operation = self.client.password_authentications.begin_update
        with mock.patch.object(self.client.password_authentications, 'begin_update', wraps=operation) as patched:
            self.custom.password_authentication_update(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, administrator_login_password=password)
        self.assertFalse(patched.call_args.kwargs['logging_enable'])
        self.assert_request('PATCH', '/authentications/passwordMethod', {
            'properties': {'administratorLoginPassword': password},
        })

    def test_horizondb_password_disable_rejects_credentials_before_sdk_call(self):
        with self.assertRaises(InvalidArgumentValueError):
            self.custom.password_authentication_update(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, state='Disabled',
                administrator_login_password='offline-generated-password')
        self.assertEqual([], self.transport.requests)

    def test_horizondb_password_responses_redact_server_echoed_credentials(self):
        self.transport.payload['properties'].update({
            'state': 'Enabled',
            'administratorLogin': 'server-echoed-login',
            'administratorLoginPassword': 'server-echoed-password',
            'administrator_login': 'server-echoed-snake-login',
            'administrator_login_password': 'server-echoed-snake-password',
        })
        cases = (
            ('password_authentication_show', (self.client, RESOURCE_GROUP, CLUSTER), {}),
            ('password_authentication_create', (self.cmd, self.client, RESOURCE_GROUP, CLUSTER), {
                'state': 'Enabled', 'administrator_login': 'request-login',
                'administrator_login_password': 'Request-Password!9'}),
            ('password_authentication_update', (self.cmd, self.client, RESOURCE_GROUP, CLUSTER), {
                'administrator_login_password': 'Replacement-Password!9'}),
        )
        for handler, args, kwargs in cases:
            with self.subTest(handler=handler):
                result = getattr(self.custom, handler)(*args, **kwargs)
                if hasattr(result, 'result'):
                    result = result.result()
                result = result.as_dict()
                self.assertEqual('Enabled', result['properties']['state'])
                self.assertNotIn('server-echoed-', json.dumps(result))
                for key in ('administratorLogin', 'administratorLoginPassword',
                            'administrator_login', 'administrator_login_password'):
                    self.assertNotIn(key, result['properties'])

    def test_horizondb_authentication_collection_redacts_server_echoed_credentials(self):
        self.transport.payload = {'value': [{
            'name': 'passwordMethod',
            'properties': {'state': 'Enabled', 'administratorLogin': 'server-echoed-login',
                           'administratorLoginPassword': 'server-echoed-password'},
        }]}
        result = list(self.custom.authentication_list(self.client, RESOURCE_GROUP, CLUSTER))
        self.assertEqual(1, len(result))
        body = result[0].as_dict()
        self.assertEqual('Enabled', body['properties']['state'])
        self.assertNotIn('server-echoed-', json.dumps(body))
        self.assertNotIn('administratorLogin', body['properties'])
        self.assertNotIn('administratorLoginPassword', body['properties'])

    def test_horizondb_signed_log_url_action_is_synchronous_and_not_http_logged(self):
        operation = self.client.log_files.list_download_urls
        with mock.patch.object(self.client.log_files, 'list_download_urls', wraps=operation) as patched:
            self.custom.log_file_list_download_urls(self.client, RESOURCE_GROUP, CLUSTER, 'captured-log')
        self.assertFalse(patched.call_args.kwargs['logging_enable'])
        self.assert_request('POST', '/logFiles/captured-log/listDownloadUrls')

    def test_horizondb_cdc_activation_requires_etag_and_preserves_false(self):
        self.transport.payload['properties']['validation'] = {'state': 'Succeeded'}
        self.custom.cdc_destination_activate_identity(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'fabric-main',
            ASSIGNMENT_ID, '"destination-etag"', drain_existing_sessions='false')
        request = self.assert_request('POST', '/changeDataCaptureDestinations/fabric-main/activateIdentity', {
            'identityAssignmentResourceId': ASSIGNMENT_ID, 'drainExistingSessions': False,
        })
        self.assertEqual('"destination-etag"', request.headers['If-Match'])
        with self.assertRaises(RequiredArgumentMissingError):
            self.custom.cdc_destination_activate_identity(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'fabric-main', ASSIGNMENT_ID, '')

    def test_horizondb_cdc_identity_requires_exact_cluster_attachment(self):
        with self.assertRaises(InvalidArgumentValueError):
            self.custom.cdc_identity_assignment_create(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'fabric-main', 'primary', IDENTITY_ID)
        self.assertEqual(['GET'], [request.method for request in self.transport.requests])
        self.transport.payload['identity'] = {
            'type': 'UserAssigned', 'userAssignedIdentities': {IDENTITY_ID: {}},
        }
        self.custom.cdc_identity_assignment_create(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'fabric-main', 'primary', IDENTITY_ID)
        self.assert_request('PUT', '/identityAssignments/primary', {
            'properties': {'identityResourceId': IDENTITY_ID},
        })

    def test_horizondb_cdc_configuration_uses_mirroring_and_arm_destination(self):
        self.custom.cdc_configuration_create(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'sales', 'sales', DESTINATION_ID, 'Enabled')
        self.assert_request('PUT', '/changeDataCaptureConfigurations/sales', {
            'properties': {'databaseName': 'sales', 'destinationResourceId': DESTINATION_ID,
                           'usageProfile': 'Mirroring', 'desiredState': 'Enabled'},
        })

    def test_horizondb_private_endpoint_approve_reject_put_omits_read_only_endpoint(self):
        endpoint_id = (f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/'
                       'providers/Microsoft.Network/privateEndpoints/example-endpoint')
        self.transport.payload['properties']['privateEndpoint'] = {'id': endpoint_id}
        for handler, state in (('private_endpoint_connection_approve', 'Approved'),
                               ('private_endpoint_connection_reject', 'Rejected')):
            with self.subTest(state=state):
                request_count = len(self.transport.requests)
                getattr(self.custom, handler)(
                    self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'example-pec', description='')
                self.assertEqual(request_count + 1, len(self.transport.requests),
                                 'Approval/rejection must not prefetch the read-only endpoint representation.')
                self.assert_request('PUT', '/privateEndpointConnections/example-pec', {
                    'properties': {'privateLinkServiceConnectionState': {'status': state, 'description': ''}},
                })

    def test_horizondb_parameter_group_patch_contains_only_writable_overrides(self):
        self.custom.parameter_group_update(
            self.cmd, self.client, RESOURCE_GROUP, 'pg-transactional',
            parameters=[{'name': 'work_mem', 'value': '96MB'}], description='', tags={})
        self.assert_request('PATCH', '/parameterGroups/pg-transactional', {
            'properties': {'parameters': [{'name': 'work_mem', 'value': '96MB'}], 'description': ''},
            'tags': {},
        })

    def test_horizondb_power_and_failover_no_wait_disable_polling(self):
        for group, method, handler, arguments, suffix in (
                ('clusters', 'begin_failover', 'cluster_failover', (), '/failover'),
                ('pools', 'begin_start', 'pool_start', ('primary',), '/start'),
                ('pools', 'begin_stop', 'pool_stop', ('primary',), '/stop'),
                ('pools', 'begin_restart', 'pool_restart', ('primary',), '/restart')):
            with self.subTest(handler=handler):
                operation = getattr(getattr(self.client, group), method)
                with mock.patch.object(getattr(self.client, group), method, wraps=operation) as patched:
                    getattr(self.custom, handler)(
                        self.client, RESOURCE_GROUP, CLUSTER, *arguments, no_wait=True)
                self.assertFalse(patched.call_args.kwargs['polling'])
                self.assert_request('POST', suffix)

    def test_horizondb_list_chooses_resource_group_or_subscription_operation(self):
        for handler, family in (('cluster_list', 'clusters'), ('parameter_group_list', 'parameter_groups')):
            client = mock.Mock()
            operation = getattr(client, family)
            self.assertIs(operation.list_by_subscription.return_value, getattr(self.custom, handler)(client))
            self.assertIs(operation.list_by_resource_group.return_value,
                          getattr(self.custom, handler)(client, RESOURCE_GROUP))
            operation.list_by_subscription.assert_called_once_with()
            operation.list_by_resource_group.assert_called_once_with(RESOURCE_GROUP)

    def test_horizondb_cluster_invalid_restore_and_encryption_do_not_send_requests(self):
        cases = (
            ({}, RequiredArgumentMissingError),
            ({'postgresql_version': '17', 'point_in_time': '2026-10-01T12:00:00Z'},
             InvalidArgumentValueError),
            ({'creation_mode': 'PointInTimeRestore', 'creation_source_cluster': CLUSTER_ID},
             RequiredArgumentMissingError),
            ({'postgresql_version': '17', 'backup_retention_days': 21}, RequiredArgumentMissingError),
            ({'postgresql_version': '17', 'data_encryption_type': 'AzureKeyVault'},
             RequiredArgumentMissingError),
            ({'postgresql_version': '17', 'data_encryption_type': 'AzureKeyVault',
              'primary_encryption_key_uri': 'https://example-vault.vault.azure.net/keys/example-key',
              'primary_encryption_identity': IDENTITY_ID, 'user_assigned_identities': [IDENTITY_ID],
              'geo_redundancy': 'Enabled'}, InvalidArgumentValueError),
        )
        for kwargs, error in cases:
            with self.subTest(arguments=kwargs):
                with mock.patch.object(self.client.clusters, 'get', side_effect=ResourceNotFoundError('Not found')):
                    with self.assertRaises(error):
                        self.custom.cluster_create(
                            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'eastus2', **kwargs)
        self.assertEqual([], self.transport.requests)

    def test_horizondb_cluster_update_cannot_switch_encryption_or_change_both_references(self):
        with self.assertRaises(InvalidArgumentValueError):
            self.custom.cluster_update(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, data_encryption_type='AzureKeyVault')
        with self.assertRaises(InvalidArgumentValueError):
            self.custom.cluster_update(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER,
                primary_encryption_key_uri='https://example-vault.vault.azure.net/keys/example-key',
                primary_encryption_identity=IDENTITY_ID)
        self.assertTrue(all(request.method == 'GET' for request in self.transport.requests))

    def test_horizondb_pool_update_topology_crossing_requires_both_fields(self):
        self.transport.payload['properties'].update({
            'nodeCount': 3, 'highAvailability': {'mode': 'AnyZone'},
        })
        with self.assertRaises(RequiredArgumentMissingError):
            self.custom.pool_update(self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'primary', node_count=1)
        self.assertTrue(all(request.method == 'GET' for request in self.transport.requests))
        self.custom.pool_update(
            self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'primary',
            node_count=1, high_availability_mode='Disabled')
        self.assert_request('PATCH', '/pools/primary', {
            'properties': {'nodeCount': 1, 'highAvailability': {'mode': 'Disabled'}},
        })
        with self.assertRaises(RequiredArgumentMissingError):
            self.custom.pool_update(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'primary',
                parameter_group=DEFAULT_PARAMETER_GROUP_ID)

    def test_horizondb_pool_creation_rejects_parent_name_and_initial_custom_group(self):
        arguments = {
            'compute_type': 'Provisioned', 'compute_processor': 'Intel', 'compute_generation': 3,
            'v_cores': 8, 'node_count': 1, 'high_availability_mode': 'Disabled',
            'parameter_group': DEFAULT_PARAMETER_GROUP_ID, 'parameter_apply_method': 'Immediate',
        }
        with self.assertRaises(InvalidArgumentValueError):
            self.custom.pool_create(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, CLUSTER, **arguments)
        custom_group = (f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}/'
                        'providers/Microsoft.HorizonDb/parameterGroups/pg-transactional')
        arguments['parameter_group'] = custom_group
        with mock.patch.object(self.client.pools, 'get', side_effect=ResourceNotFoundError('Not found')):
            with self.assertRaises(InvalidArgumentValueError):
                self.custom.pool_create(
                    self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'primary', **arguments)
        self.assertEqual([], self.transport.requests)

    def test_horizondb_active_assignment_and_foreign_destination_are_rejected(self):
        self.transport.payload['properties']['activationState'] = 'Active'
        with self.assertRaises(InvalidArgumentValueError):
            self.custom.cdc_identity_assignment_delete(
                self.client, RESOURCE_GROUP, CLUSTER, 'fabric-main', 'primary')
        with self.assertRaises(InvalidArgumentValueError):
            self.custom.cdc_configuration_create(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'sales', 'sales',
                DESTINATION_ID.replace('/example-cluster/', '/other-cluster/'), 'Enabled')
        with self.assertRaises(InvalidArgumentValueError):
            self.custom.cdc_destination_activate_identity(
                self.cmd, self.client, RESOURCE_GROUP, CLUSTER, 'fabric-main',
                ASSIGNMENT_ID.replace('/fabric-main/', '/other-destination/'), '"test-etag"')
        self.assertTrue(all(request.method == 'GET' for request in self.transport.requests))

    def test_horizondb_read_and_collection_handlers_target_correct_sdk_operations(self):
        cases = (
            ('operation_list', 'operations', 'list', ()),
            ('cluster_show', 'clusters', 'get', (RESOURCE_GROUP, CLUSTER)),
            ('pool_show', 'pools', 'get', (RESOURCE_GROUP, CLUSTER, 'primary')),
            ('pool_list', 'pools', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('node_list', 'nodes', 'list', (RESOURCE_GROUP, CLUSTER, 'primary')),
            ('endpoint_show', 'endpoints', 'get', (RESOURCE_GROUP, CLUSTER, CLUSTER)),
            ('endpoint_list', 'endpoints', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('firewall_rule_show', 'firewall_rules', 'get', (RESOURCE_GROUP, CLUSTER, 'primary', 'office')),
            ('firewall_rule_list', 'firewall_rules', 'list', (RESOURCE_GROUP, CLUSTER, 'primary')),
            ('entra_administrator_show', 'microsoft_entra_administrators', 'get',
             (RESOURCE_GROUP, CLUSTER, '11111111-1111-1111-1111-111111111111')),
            ('entra_administrator_list', 'microsoft_entra_administrators', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('log_capture_configuration_show', 'log_capture_configurations', 'get',
             (RESOURCE_GROUP, CLUSTER, 'PostgreSqlServer')),
            ('log_capture_configuration_list', 'log_capture_configurations', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('log_file_show', 'log_files', 'get', (RESOURCE_GROUP, CLUSTER, 'captured-log')),
            ('log_file_list', 'log_files', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('cdc_list', 'change_data_capture_capabilities', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('cdc_destination_show', 'change_data_capture_destinations', 'get',
             (RESOURCE_GROUP, CLUSTER, 'fabric-main')),
            ('cdc_destination_list', 'change_data_capture_destinations', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('cdc_identity_assignment_show', 'identity_assignments', 'get',
             (RESOURCE_GROUP, CLUSTER, 'fabric-main', 'primary')),
            ('cdc_identity_assignment_list', 'identity_assignments', 'list', (RESOURCE_GROUP, CLUSTER, 'fabric-main')),
            ('cdc_configuration_show', 'change_data_capture_configurations', 'get', (RESOURCE_GROUP, CLUSTER, 'sales')),
            ('cdc_configuration_list', 'change_data_capture_configurations', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('private_endpoint_connection_show', 'private_endpoint_connections', 'get',
             (RESOURCE_GROUP, CLUSTER, 'example-pec')),
            ('private_endpoint_connection_list', 'private_endpoint_connections', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('private_link_resource_show', 'private_link_resources', 'get', (RESOURCE_GROUP, CLUSTER, CLUSTER)),
            ('private_link_resource_list', 'private_link_resources', 'list', (RESOURCE_GROUP, CLUSTER)),
            ('parameter_group_show', 'parameter_groups', 'get', (RESOURCE_GROUP, 'pg-transactional')),
            ('parameter_group_list_connections', 'parameter_groups', 'list_connections',
             (RESOURCE_GROUP, 'pg-transactional')),
            ('default_parameter_group_show', 'default_parameter_groups', 'get', ('eastus2', 'postgresql-17')),
            ('default_parameter_group_list', 'default_parameter_groups', 'list_by_subscription_and_region',
             ('eastus2',)),
        )
        for handler, family, method, arguments in cases:
            with self.subTest(handler=handler):
                client = mock.Mock()
                operation = getattr(getattr(client, family), method)
                self.assertIs(operation.return_value, getattr(self.custom, handler)(client, *arguments))
                operation.assert_called_once_with(*arguments)


if __name__ == '__main__':
    unittest.main()
