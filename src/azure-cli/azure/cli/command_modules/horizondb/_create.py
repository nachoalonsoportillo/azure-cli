# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Awaited, resumable convenience workflow. Never replaces an existing deployment."""

from contextlib import contextmanager
import hashlib
import ipaddress
import json
import time

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError, ServiceRequestError
from azure.cli.core.azclierror import (
    AzureResponseError, InvalidArgumentValueError, RequiredArgumentMissingError)
from azure.cli.core.commands import LongRunningOperation
from knack.log import get_logger

from . import custom
from ._discovery import _list_regional_capabilities
from ._validators import bounded_int, dns_name, guid


logger = get_logger(__name__)
_OWNER = 'horizondb-cli-create'
_INTENT = 'horizondb-cli-create-intent'
_AUTH = 'horizondb-cli-create-auth'
_COMPLETE = 'horizondb-cli-create-complete'


def _plan_digest(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode('utf-8')).hexdigest()


def _wire(resource):
    return resource.as_dict() if hasattr(resource, 'as_dict') else resource


def _properties(resource):
    return (_wire(resource) or {}).get('properties') or {}


def _optional(getter, *args, **kwargs):
    # Only a real resource-not-found response means an absent stage.
    try:
        return getter(*args, **kwargs)
    except ResourceNotFoundError:
        return None


@contextmanager
def _stage(name):
    logger.info('HorizonDB create: %s.', name)
    try:
        yield
    except (HttpResponseError, ServiceRequestError):
        logger.error('HorizonDB create failed during %s. Existing resources have been retained; '
                     'resolve the original service error before resuming.', name)
        raise


class _ResourceWaiter:
    """Read-only polling when a previous CLI invocation left a resource provisioning."""

    def __init__(self, getter, resource):
        self.getter = getter
        self.resource = resource
        self.deadline = time.monotonic() + 1800
        self.next_read = time.monotonic() + 5

    def status(self):
        properties = _properties(self.resource)
        provisioning = properties.get('provisioningState', 'Succeeded')
        state = properties.get('state')
        if provisioning == 'Succeeded' and state in ('Provisioning', 'Starting', 'Restarting', 'Updating'):
            return state
        return provisioning

    def done(self):
        if self.status() in ('Failed', 'Canceled', 'Cancelled'):
            raise AzureResponseError(
                'An existing workflow resource is in a failed provisioning state. '
                'Inspect it with the resource show command; no overwrite or automatic retry was attempted.')
        if self.status() == 'Succeeded':
            return True
        now = time.monotonic()
        if now >= self.deadline:
            raise AzureResponseError(
                'Timed out waiting for an existing resource. Resources were retained; rerun later.')
        if now >= self.next_read:
            self.resource = self.getter()
            self.next_read = now + 5
        return False

    def result(self):
        return self.resource


def _settled(cmd, getter, resource):
    if resource is None:
        return None
    return LongRunningOperation(cmd.cli_ctx)(_ResourceWaiter(getter, resource))


def _public_ranges(public_access):
    if public_access is None:
        # yes=True returns only the detected single IP and never enters the helper's all-IPs prompt.
        from azure.cli.command_modules.rdbms.flexible_server_virtual_network import prepare_public_network
        from knack.util import CLIError
        try:
            start, end = prepare_public_network(None, yes=True)
        except CLIError as ex:
            raise InvalidArgumentValueError(
                'Caller IPv4 discovery failed. Supply explicit --public-access IPv4 rules. ' + str(ex)) from ex
        if start != end:
            raise AzureResponseError('Caller IP discovery did not return a single IPv4 address. '
                                     'Supply explicit --public-access IPv4 rules.')
        public_access = [start]
    ranges = set()
    for item in public_access:
        try:
            if '/' in item:
                network = ipaddress.IPv4Network(item, strict=False)
                start, end = network.network_address, network.broadcast_address
            elif '-' in item:
                first, last = item.split('-', 1)
                start, end = ipaddress.IPv4Address(first), ipaddress.IPv4Address(last)
            else:
                start = end = ipaddress.IPv4Address(item)
            if start > end:
                raise ValueError('reversed range')
            # 0.0.0.0 is the Azure-services special rule, not a caller IPv4 address.
            if start == end == ipaddress.IPv4Address('0.0.0.0'):
                raise ValueError('Azure-services wildcard is not supported')
        except (ValueError, TypeError) as ex:
            raise InvalidArgumentValueError(
                '--public-access requires IPv4 addresses, CIDRs, or start-end IPv4 ranges. '
                'IPv6 and the 0.0.0.0 Azure-services wildcard are not supported.') from ex
        ranges.add((str(start), str(end)))
    if not ranges:
        raise InvalidArgumentValueError('--public-access must contain at least one IPv4 rule.')
    return sorted(ranges)


def _administrator(cmd, authentication, object_id, principal_name, principal_type, tenant_id):
    if authentication == 'Password':
        if any((object_id, principal_name, principal_type, tenant_id)):
            raise InvalidArgumentValueError('Entra administrator options require Entra or Both authentication.')
        return None
    from azure.cli.core._profile import Profile
    subscription = Profile(cli_ctx=cmd.cli_ctx).get_subscription()
    if any((object_id, principal_name, principal_type)):
        if not all((object_id, principal_name, principal_type)):
            raise RequiredArgumentMissingError(
                'Supply --administrator-object-id, --administrator-principal-name, and '
                '--administrator-principal-type together.')
    else:
        from azure.cli.command_modules.role import graph_client_factory, GraphError
        from azure.cli.command_modules.role.util import get_object_id
        user = subscription['user']
        try:
            if user['type'] == 'user':
                principal = graph_client_factory(cmd.cli_ctx).signed_in_user_get()
                object_id = principal['id']
                principal_name = principal.get('userPrincipalName') or user['name']
                principal_type = 'User'
            elif user['type'] == 'servicePrincipal':
                object_id = get_object_id(cmd.cli_ctx, spn=user['name'])
                principal_type = 'ServicePrincipal'
                if object_id:
                    principal = graph_client_factory(cmd.cli_ctx).service_principal_get(object_id)
                    principal_name = principal.get('displayName')
            else:
                raise RequiredArgumentMissingError(
                    'This login type requires explicit Entra administrator identifiers.')
        except GraphError as ex:
            raise AzureResponseError(
                'Entra administrator discovery failed. Supply explicit --administrator-object-id, '
                '--administrator-principal-name, --administrator-principal-type and --tenant-id, '
                'or grant the signed-in identity directory read permission. ' + str(ex)) from ex
        if not object_id:
            raise RequiredArgumentMissingError(
                'Unable to resolve this service connection to a tenant object ID. '
                'Supply explicit Entra administrator identifiers; an application ID is not an object ID.')
    guid(object_id)
    tenant_id = tenant_id or subscription['tenantId']
    guid(tenant_id)
    if principal_type not in ('User', 'Group', 'ServicePrincipal'):
        raise InvalidArgumentValueError('Administrator principal type must be User, Group, or ServicePrincipal.')
    if not principal_name or len(principal_name) > 256:
        raise InvalidArgumentValueError('--administrator-principal-name must contain 1-256 characters.')
    return {'objectId': object_id.lower(), 'principalName': principal_name,
            'principalType': principal_type, 'tenantId': tenant_id.lower()}


def _provisioned_sizes(pool_capabilities, v_cores):
    for processor in pool_capabilities.get('supportedProcessors', []):
        for generation in processor.get('supportedGenerations', []):
            if generation.get('computeGeneration') != 1:
                continue
            for model in generation.get('supportedComputeModels', []):
                if model.get('type') != 'Provisioned':
                    continue
                for size in model.get('supportedComputeSizes', []):
                    cores = size.get('vCores')
                    if isinstance(cores, int) and (v_cores is None or cores == v_cores):
                        yield cores, processor['name']


def _regional_defaults(client, location, version, v_cores):
    catalog = _list_regional_capabilities(client, location)
    candidates = []
    for entry in catalog:
        properties = entry.get('properties')
        if properties is None:
            logger.warning('The capability service returned flat fields instead of its published resource wrapper; '
                           'using the actual advertised fields without changing their schema.')
            properties = entry
        versions = properties.get('clusterCapabilities', {}).get('supportedPostgreSqlVersions', [])
        if not any(str(item.get('name')) == version for item in versions):
            continue
        candidates.extend(_provisioned_sizes(properties.get('poolCapabilities', {}), v_cores))
    if not candidates:
        raise InvalidArgumentValueError(
            'The regional catalog does not advertise the requested PostgreSQL version and generation-1 '
            'Provisioned compute size. Select another --location, --postgresql-version, or --v-cores.')
    cores, processor = min(candidates)
    bounded_int(1, 192)(cores)
    defaults = [
        _wire(item) for item in client.default_parameter_groups.list_by_subscription_and_region(location)
        if str(_properties(item).get('postgreSqlVersion')) == version
    ]
    if len(defaults) != 1 or not defaults[0].get('id'):
        raise AzureResponseError('Expected one service-provided default parameter group for the selected version.')
    default = defaults[0]
    from azure.mgmt.core.tools import parse_resource_id
    parsed = parse_resource_id(default['id'])
    custom._pool_properties(  # pylint: disable=protected-access
        'Provisioned', processor, 1, cores, 1, 'Disabled', default['id'], 'Immediate', 'System')
    if parsed.get('name', '').lower() != location.lower():
        raise AzureResponseError('The advertised default parameter group belongs to a different region.')
    actual = client.default_parameter_groups.get(location, parsed['child_name_1'])
    if str(_properties(actual).get('postgreSqlVersion')) != version:
        raise AzureResponseError('The advertised default parameter group does not match its actual version.')
    return cores, processor, default['id']


def _conflict(detail):
    raise InvalidArgumentValueError(
        'Existing deployment conflicts with az horizondb create: ' + detail +
        '. Use matching options or a new name; inspect and manage existing resources with the individual commands.')


def _check(actual, expected, detail):
    if actual != expected:
        _conflict(detail)


def _checkpoint(cmd, client, resource_group_name, cluster_name, intent, checkpoint):
    # PATCH responses may omit tags. Preserve the current resource's unrelated tags.
    cluster = client.clusters.get(resource_group_name, cluster_name)
    tags = dict(_wire(cluster).get('tags') or {})
    _check(tags.get(_OWNER), 'v1', 'the cluster ownership changed while provisioning')
    _check(tags.get(_INTENT), intent, 'the workflow intent changed while provisioning')
    if tags.get(checkpoint) != 'true':
        tags[checkpoint] = 'true'
        cluster = LongRunningOperation(cmd.cli_ctx)(custom.cluster_update(
            cmd, client, resource_group_name, cluster_name, tags=tags))
    return cluster


def _check_pool(pool, plan):
    properties = _properties(pool)
    for field, expected in (
            ('computeModel', {'type': 'Provisioned', 'computeProcessor': plan['processor'],
                              'computeGeneration': 1, 'vCores': plan['cores']}),
            ('highAvailability', {'mode': 'Disabled'}),
            ('scheduledMaintenance', {'maintenanceManagementType': 'System'})):
        for key, value in expected.items():
            _check(properties.get(field, {}).get(key), value, f'pool {field}.{key} differs')
    _check(properties.get('nodeCount'), 1, 'pool node count differs')
    _check(properties.get('parameterGroup', {}).get('parameterGroupResourceId', '').lower(),
           plan['default'].lower(), 'pool parameter group differs')
    _check(properties.get('parameterGroup', {}).get('applyMethod'), 'Immediate',
           'pool parameter apply method differs')
    if properties.get('state') not in (None, 'Ready'):
        _conflict('pool is not Ready; inspect its state and use the pool start command if appropriate')


def _snapshot(cmd, client, rg, name, plan, tags):
    cluster = _optional(client.clusters.get, rg, name)
    if cluster is None:
        return {'cluster': None, 'pool': None, 'password': None, 'entra': None, 'administrators': [], 'rules': []}
    existing_tags = _wire(cluster).get('tags') or {}
    _check(existing_tags.get(_OWNER), 'v1', 'the cluster is not owned by this convenience workflow')
    _check(existing_tags.get(_INTENT), tags[_INTENT], 'location, compute, auth, firewall, or AI intent differs')
    cluster = _settled(cmd, lambda: client.clusters.get(rg, name), cluster)
    props = _properties(cluster)
    _check(_wire(cluster).get('location', '').lower().replace(' ', ''), plan['location'], 'location differs')
    _check(str(props.get('postgreSqlVersion')), plan['version'], 'PostgreSQL version differs')
    _check(props.get('network', {}).get('publicAccess'), 'Enabled', 'public network access differs')
    _check(props.get('dataEncryption', {}).get('type'), 'SystemManaged', 'encryption differs')
    pools = list(client.pools.list(rg, name))
    if any(_wire(pool).get('name') != plan['pool'] for pool in pools):
        _conflict('additional or differently named pools exist')
    pool = _optional(client.pools.get, rg, name, plan['pool'])
    if pool:
        pool = _settled(cmd, lambda: client.pools.get(rg, name, plan['pool']), pool)
        _check_pool(pool, plan)
    password = _optional(client.password_authentications.get, rg, name, logging_enable=False)
    entra = _optional(client.microsoft_entra_authentications.get, rg, name)
    password = _settled(
        cmd, lambda: client.password_authentications.get(rg, name, logging_enable=False), password)
    entra = _settled(cmd, lambda: client.microsoft_entra_authentications.get(rg, name), entra)
    password_properties = _properties(password)
    if password_properties.get('administratorLogin'):
        if plan['passwordState'] == 'Enabled':
            _check(password_properties['administratorLogin'], plan['login'], 'password administrator login differs')
            _check(password_properties.get('state'), 'Enabled', 'an initialized password administrator was disabled')
        elif password_properties.get('state') == 'Enabled':
            _conflict('an existing password administrator is enabled despite Entra-only intent')
    administrators = list(client.microsoft_entra_administrators.list(rg, name))
    administrators = [
        _settled(cmd, lambda aid=_wire(admin)['name']: client.microsoft_entra_administrators.get(rg, name, aid), admin)
        for admin in administrators
    ]
    for admin in administrators:
        wire = _wire(admin)
        if not plan['administrator'] or wire.get('name', '').lower() != plan['administrator']['objectId'].lower():
            _conflict('an unexpected Entra administrator exists')
        for key in ('principalName', 'principalType', 'tenantId'):
            _check(_properties(admin).get(key), plan['administrator'][key], 'Entra administrator differs')
    if existing_tags.get(_AUTH) == 'true' or existing_tags.get(_COMPLETE) == 'true':
        _check(_properties(password).get('state'), plan['passwordState'], 'password authentication differs')
        _check(_properties(entra).get('state'), plan['entraState'], 'Entra authentication differs')
    elif _properties(password).get('state') == 'Enabled' and plan['passwordState'] == 'Enabled':
        logger.warning('Existing enabled password authentication will be preserved; no password will be reset.')
    ai = props.get('aiModelManagement', 'Disabled')
    # Only an incomplete opt-in may still need enabling. Never disable a pre-existing enabled feature.
    pending_ai = (ai == 'Disabled' and plan['ai'] == 'Enabled' and
                  existing_tags.get(_COMPLETE) != 'true')
    if ai != plan['ai'] and not pending_ai:
        _conflict('AI Model Management differs')
    rules = list(client.firewall_rules.list(rg, name, plan['pool'])) if pool else []
    rules = [
        _settled(cmd, lambda rule_name=_wire(rule)['name']: client.firewall_rules.get(
            rg, name, plan['pool'], rule_name), rule)
        for rule in rules
    ]
    expected_rules = {f'cli-create-{index}': pair for index, pair in enumerate(plan['ranges'], 1)}
    for rule in rules:
        wire, rp = _wire(rule), _properties(rule)
        _check((rp.get('startIpAddress'), rp.get('endIpAddress')), expected_rules.get(wire.get('name')),
               'an existing firewall rule would broaden or change the requested access')
    return {'cluster': cluster, 'pool': pool, 'password': password, 'entra': entra,
            'administrators': administrators, 'rules': rules}


def _password(authentication, login, password, needs_password):
    if authentication == 'Entra':
        if login is not None or password is not None:
            raise InvalidArgumentValueError('Password credentials require --authentication Password or Both.')
        return None, None
    login = login or 'hdbadmin'
    custom._password_credentials('Enabled', login, password)  # pylint: disable=protected-access
    if needs_password and password is None:
        from knack.prompting import prompt_pass, NoTTYException
        try:
            password = prompt_pass('Administrator password: ', confirm=True)
        except NoTTYException as ex:
            raise RequiredArgumentMissingError(
                'Initial Password/Both setup requires --administrator-login-password '
                'or an interactive terminal.') from ex
    custom._password_credentials('Enabled', login, password)  # pylint: disable=protected-access
    return login, password


def horizondb_create(cmd, client, resource_group_name, cluster_name, location=None, postgresql_version='17',
                     v_cores=None, authentication='Entra', public_access=None, enable_ai=False,
                     administrator_object_id=None, administrator_principal_name=None,
                     administrator_principal_type=None, tenant_id=None,
                     administrator_login=None, administrator_login_password=None):
    # pylint: disable=too-many-locals,too-many-statements
    dns_name(cluster_name)
    if authentication not in ('Entra', 'Password', 'Both'):
        raise InvalidArgumentValueError('--authentication must be Entra, Password, or Both.')
    if v_cores is not None:
        v_cores = bounded_int(1, 192)(v_cores)
    # Resolve the RG even with explicit location: a missing RG must fail before a provisioning PUT.
    from azure.cli.core.commands.client_factory import get_mgmt_service_client
    from azure.cli.core.profiles import ResourceType
    with _stage('resource group and prerequisite discovery'):
        group = get_mgmt_service_client(cmd.cli_ctx, ResourceType.MGMT_RESOURCE_RESOURCES).resource_groups.get(
            resource_group_name)
        location = (location or group.location).lower().replace(' ', '')
        ranges = _public_ranges(public_access)
        administrator = _administrator(
            cmd, authentication, administrator_object_id, administrator_principal_name,
            administrator_principal_type, tenant_id)
        login, password = _password(authentication, administrator_login, administrator_login_password, False)
        cores, processor, default = _regional_defaults(client, location, str(postgresql_version), v_cores)
    plan = {
        'location': location, 'version': str(postgresql_version), 'pool': cluster_name[:58] + '-pool',
        'cores': cores, 'processor': processor, 'default': default,
        'authentication': authentication, 'administrator': administrator, 'login': login,
        'passwordState': 'Enabled' if authentication in ('Password', 'Both') else 'Disabled',
        'entraState': 'Enabled' if authentication in ('Entra', 'Both') else 'Disabled',
        'ranges': ranges, 'ai': 'Enabled' if enable_ai else 'Disabled',
    }
    dns_name(plan['pool'])
    intent = _plan_digest(plan)
    tags = {_OWNER: 'v1', _INTENT: intent}
    with _stage('resume compatibility checks'):
        state = _snapshot(cmd, client, resource_group_name, cluster_name, plan, tags)
        fresh = state['cluster'] is None
        needs_password = fresh or _properties(state['password']).get('state') != 'Enabled'
        login, password = _password(authentication, login, password, needs_password)
    await_operation = LongRunningOperation(cmd.cli_ctx)
    with _stage('cluster creation'):
        if fresh:
            cluster = await_operation(custom.cluster_create(
                cmd, client, resource_group_name, cluster_name, location,
                postgresql_version=plan['version'], public_access='Enabled', tags=tags))
        else:
            cluster = state['cluster']
    with _stage('pool creation'):
        pool = state['pool']
        if pool is None:
            pool = await_operation(custom.pool_create(
                cmd, client, resource_group_name, cluster_name, plan['pool'], 'Provisioned', processor, 1, cores,
                1, 'Disabled', default, 'Immediate', maintenance_management_type='System'))
            pool = _settled(
                cmd, lambda: client.pools.get(resource_group_name, cluster_name, plan['pool']), pool)
            _check_pool(pool, plan)
    with _stage('authentication configuration'):
        if fresh or _properties(state['password']).get('state') != plan['passwordState']:
            await_operation(custom.password_authentication_create(
                cmd, client, resource_group_name, cluster_name, plan['passwordState'],
                administrator_login=login if plan['passwordState'] == 'Enabled' else None,
                administrator_login_password=password if plan['passwordState'] == 'Enabled' else None))
        if fresh or _properties(state['entra']).get('state') != plan['entraState']:
            await_operation(custom.microsoft_entra_authentication_create(
                cmd, client, resource_group_name, cluster_name, plan['entraState']))
        if administrator and not state['administrators']:
            await_operation(custom.entra_administrator_create(
                cmd, client, resource_group_name, cluster_name, administrator['objectId'],
                administrator['principalName'], administrator['principalType'], administrator['tenantId']))
    with _stage('authentication checkpoint'):
        password_state = _properties(client.password_authentications.get(
            resource_group_name, cluster_name, logging_enable=False)).get('state')
        entra_state = _properties(client.microsoft_entra_authentications.get(
            resource_group_name, cluster_name)).get('state')
        if password_state != plan['passwordState'] or entra_state != plan['entraState']:
            raise AzureResponseError(
                'The service did not confirm the requested authentication state. Resources were retained; '
                'inspect the password and Microsoft Entra authentication show commands before resuming.')
        cluster = _checkpoint(cmd, client, resource_group_name, cluster_name, intent, _AUTH)
    with _stage('firewall configuration'):
        present = {_wire(rule)['name'] for rule in state['rules']}
        for index, (start, end) in enumerate(ranges, 1):
            name = f'cli-create-{index}'
            if name not in present:
                await_operation(custom.firewall_rule_create(
                    cmd, client, resource_group_name, cluster_name, plan['pool'], name, start, end))
    with _stage('AI configuration'):
        if (fresh or _properties(cluster).get('aiModelManagement') is None or
                (enable_ai and _properties(cluster).get('aiModelManagement') != 'Enabled')):
            cluster = await_operation(custom.cluster_update(
                cmd, client, resource_group_name, cluster_name, ai_model_management=plan['ai']))
        elif not enable_ai and _properties(cluster).get('aiModelManagement', 'Disabled') != 'Disabled':
            _conflict('the service enabled AI despite the requested disabled default')
    with _stage('workflow checkpoint and connection discovery'):
        cluster = client.clusters.get(resource_group_name, cluster_name)
        if _properties(cluster).get('aiModelManagement') != plan['ai']:
            raise AzureResponseError(
                'The service did not confirm the requested AI Model Management state after PATCH. '
                'Resources were retained; inspect cluster show before resuming.')
        endpoints = list(client.endpoints.list(resource_group_name, cluster_name))
        if not any(_properties(endpoint).get('scope') == 'Cluster' and
                   _properties(endpoint).get('role') == 'ReadWrite' and
                   _properties(endpoint).get('fullyQualifiedDomainName') for endpoint in endpoints):
            raise AzureResponseError(
                'The service has not returned a usable read-write Cluster endpoint. Resources were retained; '
                'inspect endpoint list and resume once the endpoint is ready.')
        cluster = _checkpoint(cmd, client, resource_group_name, cluster_name, intent, _COMPLETE)
    return {
        'cluster': {'id': _wire(cluster).get('id'), 'name': cluster_name, 'location': location,
                    'postgresqlVersion': plan['version']},
        'pool': {'id': _wire(pool).get('id'), 'name': plan['pool'], 'vCores': cores, 'nodeCount': 1},
        'connection': {'authentication': authentication, 'sslMode': 'require',
                       'endpoints': [{'id': _wire(endpoint).get('id'),
                                      'host': _properties(endpoint).get('fullyQualifiedDomainName'),
                                      'scope': _properties(endpoint).get('scope'),
                                      'role': _properties(endpoint).get('role')} for endpoint in endpoints]},
        'firewall': [{'startIpAddress': start, 'endIpAddress': end} for start, end in ranges],
        'aiModelManagement': plan['ai'],
    }
