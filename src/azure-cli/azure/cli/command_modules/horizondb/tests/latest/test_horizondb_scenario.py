# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Replayable scenarios. Recording is opt-in; no missing cassette silently provisions resources.

Set AZURE_TEST_RUN_LIVE=true through azdev --live to record an explicitly selected scenario.
HORIZONDB_TEST_LOCATION chooses a region advertised by the subscription capability catalog.
Playback recovers region, Fabric capacity and restore target from the actual recorded requests.
Optional scenarios explain their additional prerequisites in PREREQUISITES below.
"""

import ast
import importlib
import json
import logging
import os
from pathlib import Path
import shlex
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from urllib.parse import unquote, urlsplit

from azure.cli.testsdk import ResourceGroupPreparer, ScenarioTest
from azure.cli.testsdk.scenario_tests import RecordingProcessor
from azure.cli.testsdk.scenario_tests.decorators import AllowLargeResponse
from knack.help_files import helps
import yaml

from .test_horizondb_help import EXPECTED_COMMANDS
from .create_recording import normalize_create_recording
from ..._client_factory import cf_horizondb
from ..._discovery import _list_regional_capabilities


LOGGER = logging.getLogger(__name__)
LOCATION = os.environ.get('HORIZONDB_TEST_LOCATION', 'indonesiacentral')
FABRIC_API = 'https://api.fabric.microsoft.com/v1'
PREREQUISITES = {
    'create-workflow': (
        'Set HORIZONDB_TEST_ENABLE_CREATE_WORKFLOW=true only after approval to provision a new minimal deployment. '
        'Entra directory discovery and the selected region must support HorizonDB provisioning. '
        'Only a disposable test resource group is used; no customer resources are adopted.'),
    'fabric': (
        'HORIZONDB_TEST_FABRIC_CAPACITY_ID must identify a Fabric capacity on which the caller may '
        'create a disposable workspace. The caller needs Workspace.ReadWrite.All and '
        'MirroredDatabase.ReadWrite.All (or Item.ReadWrite.All), workspace role assignment permission, '
        'and tenant permission for the test-created managed identity to use Fabric. '
        'No existing workspace, identity, or mirrored database is modified.'),
    'cmk': (
        'Set HORIZONDB_TEST_ENABLE_CMK=true only when subscription policy permits a disposable '
        'purge-protected access-policy Key Vault and the caller can create keys and grant get/wrapKey/unwrapKey '
        'to a disposable managed identity. Deleted vaults and keys cannot be purged until retention expires.'),
    'log-files': (
        'Set HORIZONDB_TEST_ENABLE_LOG_FILES=true when PostgreSQL server-log capture is supported '
        'and the service can produce captured files for the newly created test pool. '
        'The test polls for files; it does not substitute a persistent log file.'),
    'restore': (
        'Set HORIZONDB_TEST_ENABLE_RESTORE=true to wait for a newly created source cluster backup. '
        'Optional HORIZONDB_TEST_RESTORE_TARGET_LOCATION enables a cross-region restore and '
        'geo-redundancy on that test-created source. Both locations must support the subscription.'),
}


def _recorded_or_live(test_name, prerequisite=None):
    recording = Path(__file__).with_name('recordings') / (test_name + '.yaml')
    live = os.environ.get('AZURE_TEST_RUN_LIVE', '').lower() == 'true'
    message = 'No recording exists. Explicitly select this test with azdev test --live to record it.'
    if prerequisite:
        message += ' Prerequisite: ' + PREREQUISITES[prerequisite]
    gates = {
        'create-workflow': ('HORIZONDB_TEST_ENABLE_CREATE_WORKFLOW', 'true'),
        'fabric': ('HORIZONDB_TEST_FABRIC_CAPACITY_ID', None),
        'cmk': ('HORIZONDB_TEST_ENABLE_CMK', 'true'),
        'log-files': ('HORIZONDB_TEST_ENABLE_LOG_FILES', 'true'),
        'restore': ('HORIZONDB_TEST_ENABLE_RESTORE', 'true'),
    }
    allowed = recording.is_file() or live
    if live and prerequisite:
        variable, expected = gates[prerequisite]
        value = os.environ.get(variable, '')
        allowed = allowed and (value.lower() == expected if expected else bool(value))
    return unittest.skipUnless(allowed, message)


def _resource_group():
    preparer = ResourceGroupPreparer(
        name_prefix='clitest-hdb-', location=LOCATION,
        parameter_name_for_location='location', random_name_length=24)
    # Even development runs must own their resource group before destructive lifecycle assertions.
    preparer.dev_setting_name = None
    return preparer


_FABRIC_COMMANDS = {
    name for name in EXPECTED_COMMANDS
    if any(name.startswith('horizondb ' + group + ' ') for group in (
        'change-data-capture-destination', 'change-data-capture-identity-assignment',
        'change-data-capture-configuration'))
}
_PRIVATE_COMMANDS = {
    name for name in EXPECTED_COMMANDS if name.startswith('horizondb private-endpoint-connection ')
}
_OPERATIONAL_COMMANDS = {
    'horizondb log-file show',
    'horizondb log-file list-download-urls',
}
_CATALOG_COMMANDS = {
    'horizondb operation list',
    'horizondb default-parameter-group list', 'horizondb default-parameter-group show',
}
SCENARIO_COMMANDS = {
    'core': EXPECTED_COMMANDS - _FABRIC_COMMANDS - _PRIVATE_COMMANDS - _OPERATIONAL_COMMANDS -
    _CATALOG_COMMANDS - {'horizondb create'},
    'create-workflow': {'horizondb create'},
    'private': _PRIVATE_COMMANDS,
    'fabric': _FABRIC_COMMANDS,
    'log-files': {name for name in _OPERATIONAL_COMMANDS if 'log-file' in name},
    'catalog-operations': {'horizondb operation list'},
    'catalog-readonly': {
        'horizondb operation list',
        'horizondb default-parameter-group list', 'horizondb default-parameter-group show'},
    'catalog-default-parameters': {
        'horizondb default-parameter-group list', 'horizondb default-parameter-group show'},
}


def calc_coverage(_test_file):
    """Report authored scenario coverage, not evidence that a live recording has succeeded."""
    covered = set().union(*SCENARIO_COMMANDS.values())
    return {
        'covered': covered,
        'missing': EXPECTED_COMMANDS - covered,
        'fraction': len(covered & EXPECTED_COMMANDS) / len(EXPECTED_COMMANDS),
    }


def raise_if(report):
    if report['missing'] or report['fraction'] < 0.8:
        raise AssertionError('Missing scenario command coverage: ' + ', '.join(sorted(report['missing'])))


class HorizonDBSecretRecordingProcessor(RecordingProcessor):
    """Sanitize credentials and signed URLs in real HTTP recordings without fabricating traffic."""

    @staticmethod
    def _sanitize(value):
        replacements = {
            'administratorLogin': 'recorded-admin',
            'administratorLoginPassword': 'Recorded-Password!9',
            'downloadUri': 'https://redacted.invalid/log?sig=REDACTED',
        }
        if isinstance(value, dict):
            return {key: replacements[key] if key in replacements else
                    HorizonDBSecretRecordingProcessor._sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [HorizonDBSecretRecordingProcessor._sanitize(item) for item in value]
        return value

    def process_request(self, request):
        if request.body:
            try:
                request.body = json.dumps(self._sanitize(json.loads(request.body)))
            except (ValueError, TypeError):
                pass
        return request

    def process_response(self, response):
        body = response.get('body', {}).get('string')
        if body:
            try:
                response['body']['string'] = json.dumps(self._sanitize(json.loads(body)))
            except (ValueError, TypeError):
                pass
        return response


class HorizonDBScenarioTest(ScenarioTest):

    def __init__(self, method_name):
        super().__init__(method_name, recording_processors=[HorizonDBSecretRecordingProcessor()])

    @AllowLargeResponse(size_kb=1024)
    def setUp(self):
        super().setUp()
        self._covered = set()

    def _h(self, command, checks=None):
        full = 'horizondb ' + command
        name = next((item for item in sorted(EXPECTED_COMMANDS, key=len, reverse=True)
                     if full == item or full.startswith(item + ' ')), None)
        self.assertIsNotNone(name, 'Unknown scenario command: ' + full)
        result = self.cmd(full, checks=checks or [])
        self._covered.add(name)
        if name.split()[-1] in ('delete', 'wait') or '--no-wait' in command:
            return None
        return result.get_output_in_json()

    def _finish(self, scenario):
        missing = SCENARIO_COMMANDS[scenario] - self._covered
        self.assertFalse(missing, 'Scenario omitted commands: ' + ', '.join(sorted(missing)))
        report = calc_coverage(__file__)
        raise_if(report)
        LOGGER.info('Authored HorizonDB scenario command coverage: %s/%s; recording coverage is separate.',
                    len(report['covered']), len(EXPECTED_COMMANDS))

    def _poll(self, command, predicate, message, attempts=40):
        for _ in range(attempts):
            result = self._h(command)
            if predicate(result):
                return result
            time.sleep(30)
        self.fail(message)

    def _recorded_inputs(self):
        """Recover recording-time prerequisites without requiring developer environment settings in CI."""
        settings = {}
        if self.in_recording:
            return settings
        for request in self.cassette.requests:
            path = unquote(urlsplit(request.uri).path)
            parts = path.split('/')
            if 'locations' in parts and ('capabilities' in parts or 'parameterGroups' in parts):
                settings['location'] = parts[parts.index('locations') + 1]
            if request.method.upper() not in ('POST', 'PUT') or not request.body:
                continue
            try:
                body = json.loads(request.body)
            except (ValueError, TypeError):
                continue
            if request.uri.rstrip('/') == FABRIC_API + '/workspaces':
                settings['capacity'] = body.get('capacityId')
            if '/clusters/hdbcross' in path:
                settings['restore_target'] = body.get('location')
        return settings

    def _regional_catalog(self, location):
        with cf_horizondb(self.cli_ctx) as client:
            return _list_regional_capabilities(client, location)

    def _capability_properties(self, catalog):
        if isinstance(catalog, dict):
            catalog = catalog.get('value', [catalog])
        self.assertIsInstance(catalog, list, 'Expected an SDK discovery array or a collection value envelope.')
        for entry in catalog:
            if not isinstance(entry, dict):
                continue
            properties = entry.get('properties')
            if properties is None and 'clusterCapabilities' in entry and 'poolCapabilities' in entry:
                LOGGER.warning(
                    'The service returned a flat capability catalog instead of the published resource wrapper; '
                    'using its actual capability fields for test discovery.')
                properties = entry
            if (isinstance(properties, dict) and properties.get('clusterCapabilities') and
                    properties.get('poolCapabilities')):
                self.assertTrue(properties['clusterCapabilities'].get('supportedPostgreSqlVersions'),
                                'The capability catalog advertises no PostgreSQL versions.')
                self.assertTrue(properties['poolCapabilities'].get('supportedProcessors'),
                                'The capability catalog advertises no compute processors.')
                return properties
        self.fail('No capability catalog entry advertises both cluster and pool capabilities.')

    def _fixture(self, location, nodes=1, geo_redundancy='Disabled'):
        location = self._recorded_inputs().get('location', location)
        self.kwargs.update(
            loc=location, cluster=self.create_random_name('hdbcli', 20), pool='primary',
            pg=self.create_random_name('hdbpg', 20),
            admin=self.create_random_name('hdbadmin', 18),
            password=self.create_random_name('Hdb9!', 32),
            new_password=self.create_random_name('Hdb8!', 32))
        catalog = self._regional_catalog(location)
        capabilities = self._capability_properties(catalog)
        cluster_capabilities = capabilities['clusterCapabilities']
        versions = [entry['name'] for entry in cluster_capabilities['supportedPostgreSqlVersions']]
        self.assertTrue(versions, 'Subscription advertises no PostgreSQL versions in the selected location.')
        self.assertIn('17', versions, 'The selected location must advertise PostgreSQL 17 for these scenarios.')
        self.kwargs['version'] = '17'
        processors = capabilities['poolCapabilities']['supportedProcessors']
        models = [
            (processor['name'], generation['computeGeneration'], model, size['vCores'])
            for processor in processors for generation in processor['supportedGenerations']
            for model in generation['supportedComputeModels'] if model['type'] == 'Provisioned'
            for size in model['supportedComputeSizes']
        ]
        self.assertTrue(models, 'Subscription advertises no provisioned compute sizes in the selected location.')
        processor, generation, _, vcores = min(models, key=lambda item: item[3])
        self.kwargs.update(processor=processor, generation=generation, vcores=vcores,
                           nodes=nodes, ha='Disabled' if nodes == 1 else 'AnyZone', geo=geo_redundancy)
        defaults = self._h('default-parameter-group list --location {loc}')
        matching = [entry for entry in defaults if str(entry['properties']['postgreSqlVersion']) ==
                    str(self.kwargs['version'])]
        self.assertTrue(matching, 'No location default parameter group matches the advertised PostgreSQL version.')
        self.kwargs['default_pg'] = matching[0]['id']
        self._h('cluster create --resource-group {rg} --name {cluster} --location {loc} '
                '--postgresql-version {version} --geo-redundancy {geo} --backup-retention-days 7 '
                '--public-access Disabled --tags test=horizondb')
        self._h('cluster wait --resource-group {rg} --name {cluster} --created')
        self.kwargs['cluster_id'] = self._h(
            'cluster show --resource-group {rg} --name {cluster}')['id']
        self._h('pool create --resource-group {rg} --cluster-name {cluster} --name {pool} '
                '--compute-type Provisioned --compute-processor {processor} --compute-generation {generation} '
                '--v-cores {vcores} --node-count {nodes} --high-availability-mode {ha} '
                '--parameter-group "{default_pg}" --parameter-apply-method Immediate '
                '--maintenance-management-type System')
        self._h('pool wait --resource-group {rg} --cluster-name {cluster} --name {pool} --created')
        self.kwargs['pool_id'] = self._h(
            'pool show --resource-group {rg} --cluster-name {cluster} --name {pool}')['id']

    def _identity(self, key='identity'):
        self.kwargs[key + '_name'] = self.create_random_name('hdbidentity', 24)
        identity = self.cmd(
            'identity create --resource-group {rg} --name {' + key + '_name} --location {loc}'
        ).get_output_in_json()
        self.kwargs[key] = identity['id']
        self.kwargs[key + '_principal'] = identity['principalId']
        self.kwargs[key + '_tenant'] = identity['tenantId']
        return identity

    @_recorded_or_live('test_horizondb_catalog_operations')
    def test_horizondb_catalog_operations(self):
        operations = self._h('operation list')
        self.assertTrue(operations, 'The provider-global operations catalog is empty.')
        self._finish('catalog-operations')

    @_recorded_or_live('test_horizondb_catalog_readonly')
    def test_horizondb_catalog_readonly(self):
        """Record operations/default catalogs and a service-provided default without creating resources."""
        self.kwargs['loc'] = self._recorded_inputs().get('location', LOCATION)
        operations = self._h('operation list')
        defaults = self._h('default-parameter-group list --location {loc}')
        self.assertTrue(operations, 'The provider-global operations catalog is empty.')
        self.assertIsInstance(defaults, list, 'An empty defaults list is valid; a malformed response is not.')
        matching = [entry for entry in defaults
                    if str(entry.get('properties', entry).get('postgreSqlVersion')) == '17']
        self.assertTrue(matching, 'No service-provided PostgreSQL 17 default exists for the read-only show step.')
        self.kwargs['default_pg'] = matching[0]['id']
        self._h('default-parameter-group show --ids "{default_pg}"',
                [self.check('properties.postgreSqlVersion', '17')])
        self._finish('catalog-readonly')

    @_recorded_or_live('test_horizondb_catalog_default_parameters')
    def test_horizondb_catalog_default_parameters(self):
        self.kwargs['loc'] = self._recorded_inputs().get('location', LOCATION)
        defaults = self._h('default-parameter-group list --location {loc}')
        self.assertTrue(defaults, 'The selected region advertises no default parameter groups.')
        self.kwargs['default_pg'] = defaults[0]['id']
        self._h('default-parameter-group show --ids "{default_pg}"', [self.exists('properties.postgreSqlVersion')])
        self._finish('catalog-default-parameters')

    @_recorded_or_live('test_horizondb_create_workflow', 'create-workflow')
    @_resource_group()
    @normalize_create_recording
    def test_horizondb_create_workflow(self, resource_group, location):
        """Opt-in provisioning and a no-password-reset rerun; never recorded implicitly."""
        self.kwargs.update(
            loc=self._recorded_inputs().get('location', location),
            cluster=self.create_random_name('hdbquick', 20))
        result = self._h('create --resource-group {rg} --name {cluster} --location {loc} '
                         '--public-access 203.0.113.10')
        self.assertEqual('Entra', result['connection']['authentication'])
        self.assertEqual('Disabled', result['aiModelManagement'])
        self.assertEqual(1, result['pool']['nodeCount'])
        self.assertTrue(result['connection']['endpoints'])
        self.assertNotIn('administratorLoginPassword', json.dumps(result))
        resumed = self._h('create --resource-group {rg} --name {cluster} --location {loc} '
                          '--public-access 203.0.113.10')
        self.assertEqual(result, resumed)
        self._finish('create-workflow')

    @_recorded_or_live('test_horizondb_core_lifecycle')
    @_resource_group()
    def test_horizondb_core_lifecycle(self, resource_group, location):
        self._h('operation list')
        self._fixture(location)
        self._h('default-parameter-group show --ids "{default_pg}"',
                [self.check('properties.postgreSqlVersion', '{version}')])
        self._h('cluster list --resource-group {rg}', [self.check('length(@)', 1)])
        self._h('cluster list')
        self._h('cluster show --ids "{cluster_id}"', [
            self.check('properties.readWritePool.designatedPoolResourceId', '{pool_id}', case_sensitive=False)])
        self._h('cluster update --ids "{cluster_id}" --backup-retention-days 21 --no-wait')
        self._h('cluster wait --ids "{cluster_id}" --updated')
        self._h('cluster update --ids "{cluster_id}" --tags test=horizondb-updated')
        self._h('cluster update --ids "{cluster_id}" --public-access Enabled')
        self._h('cluster update --ids "{cluster_id}" --ai-model-management Enabled')
        self._h('pool list --resource-group {rg} --cluster-name {cluster}', [self.check('length(@)', 1)])
        nodes = self._h('node list --resource-group {rg} --cluster-name {cluster} --pool-name {pool}')
        self.assertTrue(nodes, 'The created pool has no allocated nodes.')
        self.kwargs['node'] = nodes[0]['name']
        self._h('node show --resource-group {rg} --cluster-name {cluster} --pool-name {pool} --name {node}')
        self._h('endpoint list --resource-group {rg} --cluster-name {cluster}')
        self._h('endpoint show --resource-group {rg} --cluster-name {cluster} --name {cluster}')
        self._h('endpoint show --resource-group {rg} --cluster-name {cluster} --name {pool}')
        self._h('private-link-resource list --resource-group {rg} --cluster-name {cluster}')
        self._h('private-link-resource show --resource-group {rg} --cluster-name {cluster} --name {cluster}')
        self._h('firewall-rule create --resource-group {rg} --cluster-name {cluster} --pool-name {pool} '
                '--name office --start-ip-address 203.0.113.10 --end-ip-address 203.0.113.10')
        self._h('firewall-rule wait --resource-group {rg} --cluster-name {cluster} --pool-name {pool} '
                '--name office --created')
        self._h('firewall-rule create --resource-group {rg} --cluster-name {cluster} --pool-name {pool} '
                '--name office --start-ip-address 203.0.113.10 --end-ip-address 203.0.113.20 '
                '--description "Disposable test rule"')
        self._h('firewall-rule show --resource-group {rg} --cluster-name {cluster} --pool-name {pool} '
                '--name office', [self.check('properties.endIpAddress', '203.0.113.20')])
        self._h('firewall-rule list --resource-group {rg} --cluster-name {cluster} --pool-name {pool}')
        self._h('authentication list --resource-group {rg} --cluster-name {cluster}',
                [self.check('length(@)', 2)])
        self._h('authentication password create --resource-group {rg} --cluster-name {cluster} --state Enabled '
                '--administrator-login "{admin}" --administrator-login-password "{password}"')
        self._h('authentication password update --resource-group {rg} --cluster-name {cluster} '
                '--administrator-login-password "{new_password}" --no-wait')
        self._h('authentication password wait --resource-group {rg} --cluster-name {cluster} --updated')
        self._h('authentication password show --resource-group {rg} --cluster-name {cluster}',
                [self.not_exists('properties.administratorLoginPassword')])
        self._h('authentication password update --resource-group {rg} --cluster-name {cluster} --state Disabled')
        self._h('authentication password update --resource-group {rg} --cluster-name {cluster} --state Enabled '
                '--administrator-login-password "{password}"')
        self._h('authentication microsoft-entra create --resource-group {rg} --cluster-name {cluster} --state Enabled')
        self._h('authentication microsoft-entra update --resource-group {rg} --cluster-name {cluster} --state Disabled')
        self._h('authentication microsoft-entra wait --resource-group {rg} --cluster-name {cluster} --updated')
        self._h('authentication microsoft-entra show --resource-group {rg} --cluster-name {cluster}',
                [self.check('properties.state', 'Disabled')])
        self._h('authentication microsoft-entra update --resource-group {rg} --cluster-name {cluster} --state Enabled')
        self._identity()
        self._h('microsoft-entra-administrator create --resource-group {rg} --cluster-name {cluster} '
                '--object-id {identity_principal} --principal-name {identity_name} '
                '--principal-type ServicePrincipal --tenant-id {identity_tenant}')
        self._h('microsoft-entra-administrator wait --resource-group {rg} --cluster-name {cluster} '
                '--object-id {identity_principal} --created')
        self._h('microsoft-entra-administrator show --resource-group {rg} --cluster-name {cluster} '
                '--object-id {identity_principal}')
        self._h('microsoft-entra-administrator list --resource-group {rg} --cluster-name {cluster}')
        self._h('microsoft-entra-administrator delete --resource-group {rg} --cluster-name {cluster} '
                '--object-id {identity_principal} --yes')
        self._h('cluster update --ids "{cluster_id}" --user-assigned-identities "{identity}"')
        self._h('cluster update --ids "{cluster_id}" --remove-user-assigned-identities "{identity}"')
        self._h('log-capture-configuration update --resource-group {rg} --cluster-name {cluster} '
                '--name PostgreSqlServer --state Enabled --retention-days 21 --no-wait')
        self._h('log-capture-configuration wait --resource-group {rg} --cluster-name {cluster} '
                '--name PostgreSqlServer --updated')
        self._h('log-capture-configuration show --resource-group {rg} --cluster-name {cluster} '
                '--name PostgreSqlServer', [self.check('properties.retentionDays', 21)])
        self._h('log-capture-configuration list --resource-group {rg} --cluster-name {cluster}')
        self._h('log-file list --resource-group {rg} --cluster-name {cluster}')
        self._h('change-data-capture create --resource-group {rg} --cluster-name {cluster} --desired-state Disabled')
        self._h('change-data-capture show --resource-group {rg} --cluster-name {cluster}')
        self._h('change-data-capture list --resource-group {rg} --cluster-name {cluster}')
        self._h('change-data-capture update --resource-group {rg} --cluster-name {cluster} '
                '--desired-state Disabled --auto-upgrade-extension false')
        self._h('change-data-capture wait --resource-group {rg} --cluster-name {cluster} --updated')
        self._h('change-data-capture delete --resource-group {rg} --cluster-name {cluster} --yes')
        self._h('parameter-group create --resource-group {rg} --name {pg} --location {loc} '
                '--postgresql-version {version} --parameters work_mem=64MB '
                '--description "Disposable scenario group" --tags test=horizondb')
        self.kwargs['custom_pg'] = self._h(
            'parameter-group show --resource-group {rg} --name {pg}')['id']
        self._h('parameter-group list --resource-group {rg}')
        self._h('parameter-group list')
        self._h('parameter-group update --resource-group {rg} --name {pg} --parameters work_mem=96MB')
        self._h('parameter-group wait --resource-group {rg} --name {pg} --updated')
        self._h('pool update --ids "{pool_id}" --parameter-group "{custom_pg}" '
                '--parameter-apply-method PendingReboot')
        self._h('pool restart --ids "{pool_id}"')
        self._h('parameter-group list-connections --resource-group {rg} --name {pg}')
        self._h('pool update --ids "{pool_id}" --parameter-group "{default_pg}" '
                '--parameter-apply-method Immediate')
        self._h('pool update --ids "{pool_id}" --v-cores {vcores}')
        self._h('parameter-group delete --resource-group {rg} --name {pg} --yes')
        # Failover needs HA; allocate the second node only for this phase, then return to one.
        self._h('pool update --ids "{pool_id}" --node-count 2 --high-availability-mode AnyZone')
        self._h('cluster failover --ids "{cluster_id}" --no-wait')
        self._h('cluster wait --ids "{cluster_id}" --updated')
        self._h('pool update --ids "{pool_id}" --node-count 1 --high-availability-mode Disabled')
        self._h('pool stop --ids "{pool_id}" --no-wait')
        self._h('pool wait --ids "{pool_id}" --custom "properties.state==\'Stopped\'"')
        self._h('pool start --ids "{pool_id}"')
        self._h('pool wait --ids "{pool_id}" --custom "properties.state==\'Ready\'"')
        self._h('firewall-rule delete --resource-group {rg} --cluster-name {cluster} --pool-name {pool} '
                '--name office --yes --no-wait')
        self._h('firewall-rule wait --resource-group {rg} --cluster-name {cluster} --pool-name {pool} '
                '--name office --deleted')
        self._h('pool delete --ids "{pool_id}" --yes --no-wait')
        self._h('pool wait --ids "{pool_id}" --deleted')
        self._h('cluster delete --ids "{cluster_id}" --yes --no-wait')
        self._h('cluster wait --ids "{cluster_id}" --deleted')
        self._finish('core')

    @_recorded_or_live('test_horizondb_private_connectivity')
    @_resource_group()
    def test_horizondb_private_connectivity(self, resource_group, location):
        self._fixture(location, nodes=1)
        self.kwargs.update(vnet=self.create_random_name('hdbvnet', 20),
                           pe=self.create_random_name('hdbpe', 20))
        self.cmd('network vnet create --resource-group {rg} --name {vnet} --location {loc} '
                 '--address-prefixes 10.254.0.0/16 --subnet-name private --subnet-prefixes 10.254.0.0/24')
        self.cmd('network vnet subnet update --resource-group {rg} --vnet-name {vnet} --name private '
                 '--disable-private-endpoint-network-policies true')
        self.cmd('network private-endpoint create --resource-group {rg} --name {pe} --location {loc} '
                 '--vnet-name {vnet} --subnet private --connection-name horizon '
                 '--private-connection-resource-id "{cluster_id}" --group-id {cluster} --manual-request true')
        connections = self._poll(
            'private-endpoint-connection list --resource-group {rg} --cluster-name {cluster}',
            bool, 'No connection appeared for the test-created private endpoint.')
        self.assertEqual(1, len(connections), 'Refusing to act on an unexpected connection set.')
        self.kwargs['connection'] = connections[0]['id']
        self._h('private-endpoint-connection show --ids "{connection}"')
        self._h('private-endpoint-connection approve --ids "{connection}" --description "Disposable test approval"')
        self._h('private-endpoint-connection wait --ids "{connection}" '
                '--custom "properties.privateLinkServiceConnectionState.status==\'Approved\'"')
        self._h('private-endpoint-connection reject --ids "{connection}" --description "Disposable test rejection"')
        self._h('private-endpoint-connection wait --ids "{connection}" '
                '--custom "properties.privateLinkServiceConnectionState.status==\'Rejected\'"')
        self._h('private-endpoint-connection approve --ids "{connection}"')
        self._h('pool delete --ids "{pool_id}" --yes')
        self._h('private-endpoint-connection show --ids "{connection}"', [
            self.check('properties.privateLinkServiceConnectionState.status', 'Rejected')])
        self._h('private-endpoint-connection delete --ids "{connection}" --yes --no-wait')
        self._h('private-endpoint-connection wait --ids "{connection}" --deleted')
        self._finish('private')

    @_recorded_or_live('test_horizondb_captured_log_download', 'log-files')
    @_resource_group()
    def test_horizondb_captured_log_download(self, resource_group, location):
        if self.is_live and os.environ.get('HORIZONDB_TEST_ENABLE_LOG_FILES', '').lower() != 'true':
            self.skipTest(PREREQUISITES['log-files'])
        self._fixture(location, nodes=1)
        self._h('log-capture-configuration update --resource-group {rg} --cluster-name {cluster} '
                '--name PostgreSqlServer --state Enabled --retention-days 1')
        self._h('pool restart --ids "{pool_id}"')
        files = self._poll(
            'log-file list --resource-group {rg} --cluster-name {cluster}', bool,
            'The test-created pool produced no captured logs. ' + PREREQUISITES['log-files'])
        self.kwargs['log_file'] = files[0]['name']
        self._h('log-file show --resource-group {rg} --cluster-name {cluster} --name {log_file}',
                [self.not_exists('downloadUri')])
        self._h('log-file list-download-urls --resource-group {rg} --cluster-name {cluster} --name {log_file}',
                [self.exists('downloadUri'), self.exists('expiresAtUtc')])
        self._finish('log-files')

    @_recorded_or_live('test_horizondb_point_in_time_restore', 'restore')
    @_resource_group()
    def test_horizondb_point_in_time_restore(self, resource_group, location):
        if self.is_live and os.environ.get('HORIZONDB_TEST_ENABLE_RESTORE', '').lower() != 'true':
            self.skipTest(PREREQUISITES['restore'])
        target = (os.environ.get('HORIZONDB_TEST_RESTORE_TARGET_LOCATION') if self.in_recording else
                  self._recorded_inputs().get('restore_target'))
        self._fixture(location, nodes=1, geo_redundancy='Enabled' if target else 'Disabled')
        source = self._poll(
            'cluster show --ids "{cluster_id}"',
            lambda item: bool(item['properties'].get('backup', {}).get('earliestRestorePointUtc')),
            'No restorable backup appeared for the test-created source. ' + PREREQUISITES['restore'])
        self.kwargs.update(restore=self.create_random_name('hdbrestore', 22),
                           restore_time=source['properties']['backup']['earliestRestorePointUtc'])
        self._h('cluster create --resource-group {rg} --name {restore} --location {loc} '
                '--creation-mode PointInTimeRestore --creation-source-cluster "{cluster_id}" '
                '--point-in-time "{restore_time}"')
        self._h('pool list --resource-group {rg} --cluster-name {restore}', [self.check('length(@)', 0)])
        self._h('endpoint list --resource-group {rg} --cluster-name {restore}', [self.check('length(@)', 1)])
        if target:
            self.kwargs.update(cross_restore=self.create_random_name('hdbcross', 20), target=target)
            self._h('cluster create --resource-group {rg} --name {cross_restore} --location {target} '
                    '--creation-mode PointInTimeRestore --creation-source-cluster "{cluster_id}" '
                    '--creation-source-cluster-location {loc} --point-in-time "{restore_time}"')
            self._h('cluster delete --resource-group {rg} --name {cross_restore} --yes')
        self._h('cluster delete --resource-group {rg} --name {restore} --yes')

    @_recorded_or_live('test_horizondb_customer_managed_encryption', 'cmk')
    @_resource_group()
    def test_horizondb_customer_managed_encryption(self, resource_group, location):
        if self.is_live and os.environ.get('HORIZONDB_TEST_ENABLE_CMK', '').lower() != 'true':
            self.skipTest(PREREQUISITES['cmk'])
        self._fixture(location, nodes=1)
        self._identity()
        self.kwargs.update(vault=self.create_random_name('hdbkv', 22),
                           encrypted=self.create_random_name('hdbcmk', 20))
        self.cmd('keyvault create --resource-group {rg} --name {vault} --location {loc} '
                 '--enable-rbac-authorization false --enable-purge-protection true')
        self.cmd('keyvault set-policy --name {vault} --object-id {identity_principal} '
                 '--key-permissions get wrapKey unwrapKey')
        first = self.cmd('keyvault key create --vault-name {vault} --name primary --kty RSA').get_output_in_json()
        second = self.cmd('keyvault key create --vault-name {vault} --name replacement --kty RSA').get_output_in_json()
        self.kwargs.update(key=first['key']['kid'].rsplit('/', 1)[0],
                           replacement=second['key']['kid'].rsplit('/', 1)[0])
        self._h('cluster create --resource-group {rg} --name {encrypted} --location {loc} '
                '--postgresql-version {version} --geo-redundancy Disabled '
                '--data-encryption-type AzureKeyVault --primary-encryption-key-uri "{key}" '
                '--primary-encryption-identity "{identity}" --user-assigned-identities "{identity}"')
        self._h('cluster update --resource-group {rg} --name {encrypted} '
                '--primary-encryption-key-uri "{replacement}"')
        self._h('cluster wait --resource-group {rg} --name {encrypted} --updated')
        self._h('cluster show --resource-group {rg} --name {encrypted}',
                [self.check('properties.dataEncryption.primaryKeyUri', '{replacement}')])
        self._h('cluster delete --resource-group {rg} --name {encrypted} --yes')

    def _fabric(self, path, method='get', body=None):
        command = (f'rest --method {method} --url {FABRIC_API}/{path} '
                   '--resource https://api.fabric.microsoft.com')
        if body is not None:
            self.kwargs['fabric_body'] = json.dumps(body)
            command += " --body '{fabric_body}'"
        result = self.cmd(command)
        return result.get_output_in_json() if result.output.strip() else None

    def _cleanup_fabric(self, workspace_id):
        try:
            self._h('cluster delete --ids "{cluster_id}" --yes')
        finally:
            self._fabric('workspaces/' + workspace_id, 'delete')

    @_recorded_or_live('test_horizondb_fabric_mirroring', 'fabric')
    @_resource_group()
    def test_horizondb_fabric_mirroring(self, resource_group, location):
        capacity = (os.environ.get('HORIZONDB_TEST_FABRIC_CAPACITY_ID') if self.in_recording else
                    self._recorded_inputs().get('capacity'))
        if self.is_live and not capacity:
            self.skipTest(PREREQUISITES['fabric'])
        self._fixture(location, nodes=1)
        workspace = self._fabric('workspaces', 'post', {
            'displayName': self.create_random_name('hdbworkspace', 24),
            **({'capacityId': capacity} if capacity else {}),
        })
        workspace_id = workspace['id']
        # Only this workspace was created by this test. Never delete a caller-provided workspace.
        self.addCleanup(self._cleanup_fabric, workspace_id)
        self.kwargs.update(workspace=workspace_id, destination='fabric-main',
                           configuration=self.create_random_name('hdbmirror', 20))
        self._identity()
        self._identity('candidate')
        for name in ('identity', 'candidate'):
            self._fabric('workspaces/' + workspace_id + '/roleAssignments', 'post', {
                'principal': {'id': self.kwargs[name + '_principal'], 'type': 'ServicePrincipal'},
                'role': 'Member',
            })
        self._h('cluster update --ids "{cluster_id}" --user-assigned-identities "{identity}" "{candidate}"')
        self._h('change-data-capture create --resource-group {rg} --cluster-name {cluster} '
                '--desired-state Enabled --auto-upgrade-extension true')
        self._h('change-data-capture-destination create --resource-group {rg} --cluster-name {cluster} '
                '--name {destination} --workspace-id {workspace} --desired-state Enabled')
        destination = self._h('change-data-capture-destination show --resource-group {rg} '
                              '--cluster-name {cluster} --name {destination}')
        self.kwargs['destination_id'] = destination['id']
        self._h('change-data-capture-destination list --resource-group {rg} --cluster-name {cluster}')
        for name, assignment in (('identity', 'primary'), ('candidate', 'retired')):
            self.kwargs['assignment'] = assignment
            self._h('change-data-capture-identity-assignment create --resource-group {rg} --cluster-name {cluster} '
                    '--destination-name {destination} --name {assignment} --identity "{' + name + '}"')
            self._h('change-data-capture-identity-assignment wait --resource-group {rg} --cluster-name {cluster} '
                    '--destination-name {destination} --name {assignment} --created')
        self._h('change-data-capture-identity-assignment list --resource-group {rg} --cluster-name {cluster} '
                '--destination-name {destination}')
        self.kwargs['assignment'] = 'primary'
        assignment = self._poll(
            'change-data-capture-identity-assignment show --resource-group {rg} --cluster-name {cluster} '
            '--destination-name {destination} --name primary',
            lambda item: item['properties'].get('validation', {}).get('state') == 'Succeeded',
            'The test identity did not validate. ' + PREREQUISITES['fabric'])
        self.kwargs.update(assignment_id=assignment['id'],
                           etag=destination.get('eTag') or destination.get('etag'))
        self.assertTrue(self.kwargs['etag'], 'Destination response did not return an ETag.')
        self._h('change-data-capture-destination activate-identity --resource-group {rg} --cluster-name {cluster} '
                '--name {destination} --identity-assignment "{assignment_id}" --if-match \'{etag}\' '
                '--drain-existing-sessions false')
        self._h('change-data-capture-configuration create --resource-group {rg} --cluster-name {cluster} '
                '--name {configuration} --database-name postgres --destination "{destination_id}" '
                '--desired-state Enabled')
        configuration = self._h('change-data-capture-configuration show --resource-group {rg} '
                                '--cluster-name {cluster} --name {configuration}')
        self._h('change-data-capture-configuration list --resource-group {rg} --cluster-name {cluster}')
        artifact = configuration['properties'].get('replication', {}).get('externalArtifactId')
        if not artifact:
            configuration = self._poll(
                'change-data-capture-configuration show --resource-group {rg} --cluster-name {cluster} '
                '--name {configuration}',
                lambda item: bool(item['properties'].get('replication', {}).get('externalArtifactId')),
                'The configuration has no Fabric mirrored-database artifact. ' + PREREQUISITES['fabric'])
            artifact = configuration['properties']['replication']['externalArtifactId']
        self._h('change-data-capture-configuration update --resource-group {rg} --cluster-name {cluster} '
                '--name {configuration} --desired-state Disabled')
        self._h('change-data-capture-configuration wait --resource-group {rg} --cluster-name {cluster} '
                '--name {configuration} --updated')
        self._fabric('workspaces/' + workspace_id + '/mirroredDatabases/' + artifact + '/stopMirroring', 'post')
        self._h('change-data-capture-configuration delete --resource-group {rg} --cluster-name {cluster} '
                '--name {configuration} --yes')
        self._h('change-data-capture-identity-assignment delete --resource-group {rg} --cluster-name {cluster} '
                '--destination-name {destination} --name retired --yes')
        self._h('change-data-capture-destination update --resource-group {rg} --cluster-name {cluster} '
                '--name {destination} --desired-state Disabled --no-wait')
        self._h('change-data-capture-destination wait --resource-group {rg} --cluster-name {cluster} '
                '--name {destination} --updated')
        # No deactivate action exists. Cluster teardown owns the final active assignment.
        # Verify successful deletion using a separate destination with no assignment/configuration references.
        self.kwargs['destination'] = 'unreferenced'
        self._h('change-data-capture-destination create --resource-group {rg} --cluster-name {cluster} '
                '--name {destination} --workspace-id {workspace} --desired-state Disabled')
        self._h('change-data-capture-destination delete --resource-group {rg} --cluster-name {cluster} '
                '--name {destination} --yes')
        self._finish('fabric')


class HorizonDBScenarioInventoryTest(unittest.TestCase):

    def test_horizondb_scenario_inventory_accounts_for_every_command_and_example(self):
        importlib.import_module('azure.cli.command_modules.horizondb._help')
        report = calc_coverage(__file__)
        raise_if(report)
        self.assertEqual(EXPECTED_COMMANDS, report['covered'])
        missing_examples = [name for name in report['covered'] if not yaml.safe_load(helps[name]).get('examples')]
        self.assertEqual([], missing_examples)
        self.assertEqual(1, report['fraction'])

    def test_horizondb_scenario_inventory_matches_authored_calls(self):
        tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
        authored = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ('_h', '_poll') or not node.args:
                continue
            expression = node.args[0]
            while isinstance(expression, ast.BinOp):
                expression = expression.left
            if not isinstance(expression, ast.Constant) or not isinstance(expression.value, str):
                continue
            command = 'horizondb ' + expression.value
            match = next((name for name in sorted(EXPECTED_COMMANDS, key=len, reverse=True)
                          if command == name or command.startswith(name + ' ')), None)
            if match:
                authored.add(match)
        self.assertEqual(EXPECTED_COMMANDS, authored)

    def test_horizondb_authored_scenario_options_are_registered(self):
        from .test_horizondb_commands import _loader
        tree = ast.parse(Path(__file__).read_text(encoding='utf-8'))
        cache = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ('_h', '_poll') or not node.args:
                continue
            expression = node.args[0]
            if not isinstance(expression, ast.Constant) or not isinstance(expression.value, str):
                continue
            command = 'horizondb ' + expression.value
            name = next((name for name in sorted(EXPECTED_COMMANDS, key=len, reverse=True)
                         if command == name or command.startswith(name + ' ')), None)
            if not name:
                continue
            if name not in cache:
                definition = _loader(name).command_table[name]
                cache[name] = {
                    option for argument in definition.arguments.values() for option in argument.options_list
                } | {'--ids', '--yes', '--subscription', '--query', '--output'}
            used = {token for token in shlex.split(command) if token.startswith('--')}
            with self.subTest(command=command):
                self.assertFalse(used - cache[name], 'Unregistered scenario options: ' + str(used - cache[name]))

    def test_horizondb_fabric_helper_handles_json_and_empty_action_responses(self):
        scenario = object.__new__(HorizonDBScenarioTest)
        scenario.kwargs = {}
        scenario.cmd = mock.Mock(return_value=mock.Mock(output=''))
        self.assertIsNone(scenario._fabric('workspaces/test/mirroredDatabases/test/stopMirroring', 'post'))
        scenario._fabric('workspaces', 'post', {'displayName': 'disposable-workspace'})
        command = scenario.cmd.call_args.args[0]
        self.assertIsInstance(command, str)
        expanded = command.format(**scenario.kwargs)
        self.assertIn('"displayName": "disposable-workspace"', expanded)
        self.assertNotIn('{fabric_body}', expanded)

    def test_horizondb_optional_prerequisites_skip_before_preparing_resources(self):
        called = mock.Mock()
        with mock.patch.dict(os.environ, {'AZURE_TEST_RUN_LIVE': 'true', 'HORIZONDB_TEST_ENABLE_CMK': ''}):
            decorated = _recorded_or_live('unrecorded_cmk_test', 'cmk')(called)
        with self.assertRaises(unittest.SkipTest):
            decorated()
        called.assert_not_called()

    def test_horizondb_playback_recovers_recorded_prerequisites(self):
        scenario = object.__new__(HorizonDBScenarioTest)
        scenario.in_recording = False
        scenario.cassette = SimpleNamespace(requests=[
            SimpleNamespace(uri='https://management.azure.com/subscriptions/test/providers/'
                            'Microsoft.HorizonDb/locations/indonesiacentral/capabilities',
                            method='GET', body=None),
            SimpleNamespace(uri=FABRIC_API + '/workspaces', method='POST',
                            body=json.dumps({'capacityId': 'recorded-capacity'})),
            SimpleNamespace(uri='https://management.azure.com/subscriptions/test/resourceGroups/test/'
                            'providers/Microsoft.HorizonDb/clusters/hdbcross000001?api-version=test',
                            method='PUT', body=json.dumps({'location': 'westus3'})),
        ])
        self.assertEqual({'location': 'indonesiacentral', 'capacity': 'recorded-capacity',
                          'restore_target': 'westus3'}, scenario._recorded_inputs())
        scenario.in_recording = True
        self.assertEqual({}, scenario._recorded_inputs())

    def test_horizondb_fixture_uses_smallest_advertised_compute_and_one_node(self):
        scenario = object.__new__(HorizonDBScenarioTest)
        scenario.in_recording = True
        scenario.kwargs = {'rg': 'disposable-test-group'}
        scenario.create_random_name = mock.Mock(side_effect=lambda prefix, _length: prefix + 'test')
        capabilities = {'properties': {
            'clusterCapabilities': {
                'recommendedPostgreSqlVersion': '18',
                'supportedPostgreSqlVersions': [{'name': '17'}, {'name': '18'}]},
            'poolCapabilities': {'supportedProcessors': [{
                'name': 'Intel', 'supportedGenerations': [{
                    'computeGeneration': 1, 'supportedComputeModels': [{
                        'type': 'Provisioned', 'supportedComputeSizes': [{'vCores': 8}, {'vCores': 2}]}]}]}]},
        }}

        def response(command):
            if command.startswith('default-parameter-group list'):
                return [{'id': 'discovered-default-group', 'properties': {'postgreSqlVersion': '17'}}]
            return {'id': 'test-owned-resource'}

        scenario._h = mock.Mock(side_effect=response)
        scenario._regional_catalog = mock.Mock(return_value=[capabilities])
        scenario._fixture('eastus')
        self.assertEqual('eastus', scenario.kwargs['loc'])
        self.assertEqual('17', scenario.kwargs['version'])
        self.assertEqual(1, scenario.kwargs['generation'])
        self.assertEqual(2, scenario.kwargs['vcores'])
        self.assertEqual(1, scenario.kwargs['nodes'])
        self.assertEqual('Disabled', scenario.kwargs['ha'])
        scenario._regional_catalog.assert_called_once_with('eastus')
        scenario._h.reset_mock()
        scenario._h.side_effect = lambda command: (
            [] if command.startswith('default-parameter-group list') else response(command))
        with self.assertRaisesRegex(AssertionError, 'No location default parameter group matches'):
            scenario._fixture('eastus')
        self.assertFalse(any(call.args[0].startswith('cluster create') for call in scenario._h.call_args_list),
                         'Missing service-provided default groups must block provisioning, not invent a resource ID.')

    def test_horizondb_fixture_discovery_uses_context_client_factory_and_closes_sdk_client(self):
        scenario = object.__new__(HorizonDBScenarioTest)
        scenario.cli_ctx = object()
        wire = {'location': 'eastus', 'clusterCapabilities': {}, 'poolCapabilities': {}}
        entry = SimpleNamespace(as_dict=lambda: wire)
        with mock.patch(__name__ + '.cf_horizondb') as factory:
            client = factory.return_value.__enter__.return_value
            client.capabilities.list.return_value = [entry]
            self.assertEqual([wire], scenario._regional_catalog('eastus'))
        factory.assert_called_once_with(scenario.cli_ctx)
        client.capabilities.list.assert_called_once_with('eastus')
        factory.return_value.__exit__.assert_called_once()

    def test_horizondb_internal_discovery_preserves_observed_fields_and_rejects_empty_models(self):
        scenario = object.__new__(HorizonDBScenarioTest)
        properties = {
            'clusterCapabilities': {'supportedPostgreSqlVersions': [{'name': '17'}]},
            'poolCapabilities': {'supportedProcessors': [{'name': 'Intel'}]},
        }
        self.assertIs(properties, scenario._capability_properties([{'properties': properties}]))
        flat = dict(properties, location='indonesiacentral')
        with self.assertLogs(LOGGER, level='WARNING') as logs:
            self.assertIs(flat, scenario._capability_properties([flat]))
        self.assertIn('instead of the published resource wrapper', logs.output[0])
        self.assertNotIn('properties', flat)
        self.assertIs(properties, scenario._capability_properties({'value': [{'properties': properties}]}))
        with self.assertLogs(LOGGER, level='WARNING'):
            self.assertIs(flat, scenario._capability_properties({'value': [flat]}))
        with self.assertRaisesRegex(AssertionError, 'No capability catalog entry'):
            scenario._capability_properties([{'properties': None}])

    def test_horizondb_recording_processor_redacts_nested_secrets(self):
        processor = HorizonDBSecretRecordingProcessor()
        response = {'body': {'string': json.dumps({
            'properties': {'administratorLogin': 'sensitive-login', 'administratorLoginPassword': 'sensitive-password'},
            'downloadUri': 'https://storage.example/log?sig=sensitive-signature',
            'expiresAtUtc': '2026-10-26T12:00:00Z',
        })}}
        body = processor.process_response(response)['body']['string']
        self.assertNotIn('sensitive-', body)
        self.assertEqual('2026-10-26T12:00:00Z', json.loads(body)['expiresAtUtc'])
