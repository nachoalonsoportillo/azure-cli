# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError
from azure.cli.core.azclierror import InvalidArgumentValueError, RequiredArgumentMissingError
from azure.cli.core.util import sdk_no_wait
from azure.mgmt.core.tools import parse_resource_id

from ._validators import (
    bounded_int, description as validate_description, dns_name, guid, identity_reference,
    key_uri, parameter_values, require_change, resource_reference, utc_datetime,
    validate_ipv4_range, validate_topology)


def _model(_cmd, name, body):
    # The SDK's mapping constructor retains wire names and distinguishes omission from null.
    # HorizonDB uses the single-version SDK directly, rather than a core API profile.
    from azure.mgmt.horizondb import models
    return getattr(models, name)(body)


def _supplied(**values):
    return {key: value for key, value in values.items() if value is not None}


def _boolean(value):
    return value if isinstance(value, bool) else str(value).lower() == 'true'


def _get(value, key, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _properties(value):
    return _get(value, 'properties', {}) or {}


def _identities(cluster):
    return _get(_get(cluster, 'identity', {}), 'user_assigned_identities', {}) or {}


def _same_cluster_reference(client, parsed, resource_group_name, cluster_name):
    subscription = _get(_get(client, '_config', {}), 'subscription_id')
    same_subscription = not isinstance(subscription, str) or subscription.lower() == parsed['subscription'].lower()
    return (same_subscription and parsed['resource_group'].lower() == resource_group_name.lower() and
            parsed['name'].lower() == cluster_name.lower())


def _validate_encryption(encryption_type, primary_key, primary_identity, identities, geo_redundancy=None):
    if encryption_type == 'AzureKeyVault':
        if not primary_key or not primary_identity:
            raise RequiredArgumentMissingError(
                'AzureKeyVault requires --primary-encryption-key-uri and --primary-encryption-identity.')
        key_uri(primary_key)
        identity_reference(primary_identity)
        if primary_identity.lower() not in {item.lower() for item in identities}:
            raise InvalidArgumentValueError('The primary encryption identity must be attached to the Cluster.')
        if geo_redundancy == 'Enabled':
            raise InvalidArgumentValueError('AzureKeyVault encryption does not support geo-redundant backup.')
    elif primary_key is not None or primary_identity is not None:
        raise InvalidArgumentValueError('Encryption key and identity options require AzureKeyVault encryption.')


def _validate_identity_removals(client, resource_group_name, cluster_name, removals):
    removed = {item.lower() for item in removals}
    if not removed:
        return
    for destination in client.change_data_capture_destinations.list(resource_group_name, cluster_name):
        for assignment in client.identity_assignments.list(
                resource_group_name, cluster_name, _get(destination, 'name')):
            identity = _get(_properties(assignment), 'identity_resource_id', '')
            if identity.lower() in removed:
                raise InvalidArgumentValueError(
                    'Delete CDC identity assignments referencing an identity before detaching it from the Cluster.')


def operation_list(client):
    return client.operations.list()


def cluster_show(client, resource_group_name, cluster_name):
    return client.clusters.get(resource_group_name, cluster_name)


def cluster_list(client, resource_group_name=None):
    if resource_group_name:
        return client.clusters.list_by_resource_group(resource_group_name)
    return client.clusters.list_by_subscription()


def cluster_create(cmd, client, resource_group_name, cluster_name, location, postgresql_version=None,
                   creation_mode='Create', creation_source_cluster=None, creation_source_cluster_location=None,
                   point_in_time=None, public_access='Disabled', geo_redundancy=None, backup_retention_days=None,
                   data_encryption_type='SystemManaged', primary_encryption_key_uri=None,
                   primary_encryption_identity=None, user_assigned_identities=None, tags=None, no_wait=False):
    # pylint: disable=too-many-locals
    dns_name(cluster_name)
    identities = user_assigned_identities or []
    for identity in identities:
        identity_reference(identity)
    if creation_mode == 'PointInTimeRestore':
        if not creation_source_cluster or not point_in_time:
            raise RequiredArgumentMissingError(
                'PointInTimeRestore requires --creation-source-cluster and --point-in-time.')
        resource_reference(creation_source_cluster, 'Microsoft.HorizonDb/clusters')
        utc_datetime(point_in_time)
        if postgresql_version is not None:
            raise InvalidArgumentValueError('Omit --postgresql-version for PointInTimeRestore.')
        if creation_source_cluster_location is None:
            source_id = parse_resource_id(creation_source_cluster)
            from azure.cli.core.commands.client_factory import get_mgmt_service_client
            from azure.mgmt.horizondb import HorizonDBMgmtClient
            source_client = get_mgmt_service_client(
                cmd.cli_ctx, HorizonDBMgmtClient, subscription_id=source_id['subscription'],
                api_version='2026-10-01-preview')
            source = source_client.clusters.get(source_id['resource_group'], source_id['name'])
            if _get(source, 'location', '').replace(' ', '').lower() != location.replace(' ', '').lower():
                raise RequiredArgumentMissingError(
                    'Cross-location restore requires --creation-source-cluster-location.')
    else:
        if not postgresql_version:
            raise RequiredArgumentMissingError('--postgresql-version is required for ordinary creation.')
        if any(value is not None for value in (
                creation_source_cluster, creation_source_cluster_location, point_in_time)):
            raise InvalidArgumentValueError('Restore source and time options require PointInTimeRestore.')
    if backup_retention_days is not None:
        bounded_int(1, 35)(backup_retention_days)
        if geo_redundancy is None:
            raise RequiredArgumentMissingError('--backup-retention-days requires --geo-redundancy.')
    # PUT may replace an existing resource, but cannot change its encryption type or both key references.
    try:
        existing = client.clusters.get(resource_group_name, cluster_name)
    except ResourceNotFoundError:
        existing = None
    if existing is not None:
        if user_assigned_identities is None:
            identities = list(_identities(existing))
        encryption = _get(_properties(existing), 'data_encryption')
        existing_type = _get(encryption, 'type', 'SystemManaged')
        if existing_type != data_encryption_type:
            raise InvalidArgumentValueError('The data encryption type cannot change after creation.')
        existing_geo = _get(_get(_properties(existing), 'backup', {}), 'geo_redundancy')
        if geo_redundancy is not None and existing_geo is not None and existing_geo != geo_redundancy:
            raise InvalidArgumentValueError('Backup geo-redundancy cannot change after creation.')
        if (primary_encryption_key_uri is not None and primary_encryption_identity is not None and
                primary_encryption_key_uri != _get(encryption, 'primary_key_uri') and
                primary_encryption_identity != _get(encryption, 'primary_user_assigned_identity_id')):
            raise InvalidArgumentValueError(
                'Change only one of the primary key URI and encryption identity per request.')
        if user_assigned_identities is not None:
            new_identities = {item.lower() for item in identities}
            removed = [item for item in _identities(existing) if item.lower() not in new_identities]
            _validate_identity_removals(client, resource_group_name, cluster_name, removed)
    _validate_encryption(data_encryption_type, primary_encryption_key_uri,
                         primary_encryption_identity, identities, geo_redundancy)
    properties = _supplied(postgreSqlVersion=postgresql_version, creationMode=creation_mode,
                           creationSourceClusterResourceId=creation_source_cluster,
                           creationSourceClusterLocation=creation_source_cluster_location, pointInTimeUtc=point_in_time)
    properties['network'] = {'publicAccess': public_access}
    properties['dataEncryption'] = _supplied(
        type=data_encryption_type, primaryKeyUri=primary_encryption_key_uri,
        primaryUserAssignedIdentityId=primary_encryption_identity)
    if geo_redundancy is not None:
        properties['backup'] = {'geoRedundancy': geo_redundancy,
                                'retentionDays': backup_retention_days if backup_retention_days is not None else 7}
    body = _supplied(location=location, tags=tags, properties=properties)
    if user_assigned_identities is not None:
        body['identity'] = {'type': 'UserAssigned', 'userAssignedIdentities': {item: {} for item in identities}}
    return sdk_no_wait(no_wait, client.clusters.begin_create_or_replace, resource_group_name, cluster_name,
                       _model(cmd, 'Cluster', body))


def cluster_update(cmd, client, resource_group_name, cluster_name, public_access=None, backup_retention_days=None,
                   data_encryption_type=None, primary_encryption_key_uri=None, primary_encryption_identity=None,
                   ai_model_management=None, user_assigned_identities=None, remove_user_assigned_identities=None,
                   identity_type=None, tags=None, no_wait=False):
    # pylint: disable=too-many-locals
    require_change(public_access=public_access, backup_retention_days=backup_retention_days,
                   data_encryption_type=data_encryption_type, primary_key=primary_encryption_key_uri,
                   primary_identity=primary_encryption_identity, ai=ai_model_management,
                   identities=user_assigned_identities, removals=remove_user_assigned_identities,
                   identity_type=identity_type, tags=tags)
    if primary_encryption_key_uri is not None and primary_encryption_identity is not None:
        raise InvalidArgumentValueError('Change only one of the primary key URI and encryption identity per request.')
    if identity_type == 'None' and (user_assigned_identities or remove_user_assigned_identities):
        raise InvalidArgumentValueError('--identity-type None cannot be combined with identity assignment options.')
    adds = user_assigned_identities or []
    removes = remove_user_assigned_identities or []
    for identity in adds + removes:
        identity_reference(identity)
    if {item.lower() for item in adds} & {item.lower() for item in removes}:
        raise InvalidArgumentValueError('The same identity cannot be added and removed in one request.')
    properties = {}
    if public_access is not None:
        properties['network'] = {'publicAccess': public_access}
    if backup_retention_days is not None:
        properties['backup'] = {'retentionDays': bounded_int(1, 35)(backup_retention_days)}
    if ai_model_management is not None:
        properties['aiModelManagement'] = ai_model_management
    identity_change = user_assigned_identities is not None or remove_user_assigned_identities is not None
    if (data_encryption_type is not None or primary_encryption_key_uri is not None or
            primary_encryption_identity is not None or identity_change or identity_type is not None):
        existing = client.clusters.get(resource_group_name, cluster_name)
        encryption = _get(_properties(existing), 'data_encryption', {})
        existing_type = _get(encryption, 'type', 'SystemManaged')
        if data_encryption_type is not None and data_encryption_type != existing_type:
            raise InvalidArgumentValueError('The data encryption type cannot change after creation.')
        attached = dict(_identities(existing))
        removed = list(attached) if identity_type == 'None' else removes
        removed_lower = {item.lower() for item in removed}
        attached = {key: value for key, value in attached.items() if key.lower() not in removed_lower}
        attached.update({item: {} for item in adds})
        primary_key = primary_encryption_key_uri or _get(encryption, 'primary_key_uri')
        primary_identity = primary_encryption_identity or _get(encryption, 'primary_user_assigned_identity_id')
        _validate_encryption(existing_type, primary_key, primary_identity, attached)
        _validate_identity_removals(client, resource_group_name, cluster_name, removed)
        if any(value is not None for value in
               (data_encryption_type, primary_encryption_key_uri, primary_encryption_identity)):
            properties['dataEncryption'] = _supplied(
                type=data_encryption_type, primaryKeyUri=primary_encryption_key_uri,
                primaryUserAssignedIdentityId=primary_encryption_identity)
    body = _supplied(tags=tags)
    if properties:
        body['properties'] = properties
    if identity_change or identity_type is not None:
        entries = {item: {} for item in adds}
        entries.update({item: None for item in removes})
        body['identity'] = {'type': identity_type or 'UserAssigned'}
        if identity_type != 'None':
            body['identity']['userAssignedIdentities'] = entries
    return sdk_no_wait(no_wait, client.clusters.begin_update, resource_group_name, cluster_name,
                       _model(cmd, 'ClusterForPatchUpdate', body))


def cluster_delete(client, resource_group_name, cluster_name, no_wait=False):
    return sdk_no_wait(no_wait, client.clusters.begin_delete, resource_group_name, cluster_name)


def cluster_failover(client, resource_group_name, cluster_name, no_wait=False):
    return sdk_no_wait(no_wait, client.clusters.begin_failover, resource_group_name, cluster_name)


def pool_show(client, resource_group_name, cluster_name, pool_name):
    return client.pools.get(resource_group_name, cluster_name, pool_name)


def pool_list(client, resource_group_name, cluster_name):
    return client.pools.list(resource_group_name, cluster_name)


def _pool_properties(compute_type=None, compute_processor=None, compute_generation=None, v_cores=None,
                     node_count=None, high_availability_mode=None, parameter_group=None,
                     parameter_apply_method=None, maintenance_management_type=None):
    properties = {}
    compute = _supplied(type=compute_type, computeProcessor=compute_processor,
                        computeGeneration=compute_generation, vCores=v_cores)
    if compute_generation is not None:
        bounded_int(1)(compute_generation)
    if v_cores is not None:
        bounded_int(1, 192)(v_cores)
    if node_count is not None:
        properties['nodeCount'] = bounded_int(1, 16)(node_count)
    if compute:
        properties['computeModel'] = compute
    if high_availability_mode is not None:
        properties['highAvailability'] = {'mode': high_availability_mode}
    if (parameter_group is None) != (parameter_apply_method is None):
        raise RequiredArgumentMissingError('Supply --parameter-group and --parameter-apply-method together.')
    if parameter_group is not None:
        resource_reference(parameter_group)
        parsed = parse_resource_id(parameter_group)
        resource_type = parsed['namespace'].lower() + '/' + parsed['type'].lower()
        if resource_type != 'microsoft.horizondb/parametergroups' and not (
                resource_type == 'microsoft.horizondb/locations' and
                parsed.get('child_type_1', '').lower() == 'parametergroups'):
            raise InvalidArgumentValueError('--parameter-group must identify a custom or default HorizonDB group.')
        properties['parameterGroup'] = {'parameterGroupResourceId': parameter_group,
                                        'applyMethod': parameter_apply_method}
    if maintenance_management_type is not None:
        properties['scheduledMaintenance'] = {'maintenanceManagementType': maintenance_management_type}
    validate_topology(node_count, high_availability_mode)
    return properties


def pool_create(cmd, client, resource_group_name, cluster_name, pool_name, compute_type, compute_processor,
                compute_generation, v_cores, node_count, high_availability_mode, parameter_group,
                parameter_apply_method, maintenance_management_type=None, no_wait=False):
    dns_name(pool_name)
    if pool_name.lower() == cluster_name.lower():
        raise InvalidArgumentValueError('The Pool name must differ from the parent Cluster name.')
    properties = _pool_properties(
        compute_type, compute_processor, compute_generation, v_cores, node_count, high_availability_mode,
        parameter_group, parameter_apply_method, maintenance_management_type)
    try:
        existing = client.pools.get(resource_group_name, cluster_name, pool_name)
    except ResourceNotFoundError:
        existing = None
    if existing is None:
        group = parse_resource_id(parameter_group)
        if group.get('child_type_1', '').lower() != 'parametergroups':
            raise InvalidArgumentValueError(
                'Initial Pool creation requires the location-scoped default parameter group.')
        cluster = client.clusters.get(resource_group_name, cluster_name)
        if group['name'].replace(' ', '').lower() != _get(cluster, 'location', '').replace(' ', '').lower():
            raise InvalidArgumentValueError('The default parameter group must be in the Cluster location.')
        default = client.default_parameter_groups.get(group['name'], group['child_name_1'])
        version = _get(_properties(default), 'postgre_sql_version')
        if version != _get(_properties(cluster), 'postgre_sql_version'):
            raise InvalidArgumentValueError('The default parameter group must match the Cluster PostgreSQL version.')
    body = {'properties': properties}
    return sdk_no_wait(no_wait, client.pools.begin_create_or_replace, resource_group_name, cluster_name, pool_name,
                       _model(cmd, 'Pool', body))


def pool_update(cmd, client, resource_group_name, cluster_name, pool_name, compute_type=None, compute_processor=None,
                compute_generation=None, v_cores=None, node_count=None, high_availability_mode=None,
                parameter_group=None, parameter_apply_method=None, maintenance_management_type=None,
                no_wait=False):
    properties = _pool_properties(
        compute_type, compute_processor, compute_generation, v_cores, node_count, high_availability_mode,
        parameter_group, parameter_apply_method, maintenance_management_type)
    require_change(properties=properties or None)
    if node_count is not None or high_availability_mode is not None:
        existing = _properties(client.pools.get(resource_group_name, cluster_name, pool_name))
        old_count = _get(existing, 'node_count')
        old_mode = _get(_get(existing, 'high_availability', {}), 'mode')
        if node_count is not None and old_count is not None and (node_count == 1) != (old_count == 1):
            if high_availability_mode is None:
                raise RequiredArgumentMissingError(
                    'Supply --high-availability-mode when crossing the one-Node boundary.')
        validate_topology(node_count if node_count is not None else old_count,
                          high_availability_mode if high_availability_mode is not None else old_mode)
    body = {'properties': properties}
    return sdk_no_wait(no_wait, client.pools.begin_update, resource_group_name, cluster_name, pool_name,
                       _model(cmd, 'PoolForPatchUpdate', body))


def pool_delete(client, resource_group_name, cluster_name, pool_name, no_wait=False):
    return sdk_no_wait(no_wait, client.pools.begin_delete, resource_group_name, cluster_name, pool_name)


def pool_start(client, resource_group_name, cluster_name, pool_name, no_wait=False):
    return sdk_no_wait(no_wait, client.pools.begin_start, resource_group_name, cluster_name, pool_name)


def pool_stop(client, resource_group_name, cluster_name, pool_name, no_wait=False):
    return sdk_no_wait(no_wait, client.pools.begin_stop, resource_group_name, cluster_name, pool_name)


def pool_restart(client, resource_group_name, cluster_name, pool_name, no_wait=False):
    return sdk_no_wait(no_wait, client.pools.begin_restart, resource_group_name, cluster_name, pool_name)


def node_show(client, resource_group_name, cluster_name, pool_name, node_name):
    return client.nodes.get(resource_group_name, cluster_name, pool_name, node_name)


def node_list(client, resource_group_name, cluster_name, pool_name):
    return client.nodes.list(resource_group_name, cluster_name, pool_name)


def endpoint_show(client, resource_group_name, cluster_name, endpoint_name):
    return client.endpoints.get(resource_group_name, cluster_name, endpoint_name)


def endpoint_list(client, resource_group_name, cluster_name):
    return client.endpoints.list(resource_group_name, cluster_name)


def firewall_rule_show(client, resource_group_name, cluster_name, pool_name, firewall_rule_name):
    return client.firewall_rules.get(resource_group_name, cluster_name, pool_name, firewall_rule_name)


def firewall_rule_list(client, resource_group_name, cluster_name, pool_name):
    return client.firewall_rules.list(resource_group_name, cluster_name, pool_name)


def firewall_rule_create(cmd, client, resource_group_name, cluster_name, pool_name, firewall_rule_name,
                         start_ip_address, end_ip_address, description=None, no_wait=False):
    validate_ipv4_range(start_ip_address, end_ip_address)
    if description is not None:
        validate_description(description)
    body = {'properties': _supplied(startIpAddress=start_ip_address, endIpAddress=end_ip_address,
                                    description=description)}
    return sdk_no_wait(no_wait, client.firewall_rules.begin_create_or_replace, resource_group_name,
                       cluster_name, pool_name, firewall_rule_name, _model(cmd, 'FirewallRule', body))


def firewall_rule_delete(client, resource_group_name, cluster_name, pool_name, firewall_rule_name, no_wait=False):
    return sdk_no_wait(no_wait, client.firewall_rules.begin_delete, resource_group_name, cluster_name,
                       pool_name, firewall_rule_name)


def _redact_authentication(result):
    properties = _properties(result)
    for key in ('administratorLogin', 'administratorLoginPassword',
                'administrator_login', 'administrator_login_password'):
        properties.pop(key, None)
    return result


def _redact_authentication_result(_response, result, _headers):
    return _redact_authentication(result) if result is not None else result


def _redact_authentication_collection(result):
    return [_redact_authentication(item) for item in result]


def authentication_list(client, resource_group_name, cluster_name):
    return client.authentications.list(resource_group_name, cluster_name, logging_enable=False,
                                       cls=_redact_authentication_collection)


def password_authentication_show(client, resource_group_name, cluster_name):
    return client.password_authentications.get(resource_group_name, cluster_name, logging_enable=False,
                                               cls=_redact_authentication_result)


def _password_credentials(state, login=None, password=None):
    if state == 'Disabled' and (login is not None or password is not None):
        raise InvalidArgumentValueError('Disabling password authentication cannot include credentials.')
    if login is not None and not 1 <= len(login) <= 63:
        raise InvalidArgumentValueError('--administrator-login must contain 1-63 characters.')
    if password is not None and not 8 <= len(password) <= 128:
        raise InvalidArgumentValueError('--administrator-login-password must contain 8-128 characters.')


def password_authentication_create(cmd, client, resource_group_name, cluster_name, state,
                                   administrator_login=None, administrator_login_password=None, no_wait=False):
    _password_credentials(state, administrator_login, administrator_login_password)
    if state == 'Enabled' and (administrator_login is not None or administrator_login_password is not None):
        from knack.prompting import prompt_pass, NoTTYException
        try:
            if administrator_login is None:
                administrator_login = prompt_pass('Administrator login: ')
            if administrator_login_password is None:
                administrator_login_password = prompt_pass('Administrator password: ', confirm=True)
        except NoTTYException as ex:
            raise RequiredArgumentMissingError(
                'Supply both --administrator-login and --administrator-login-password for initial setup.') from ex
    _password_credentials(state, administrator_login, administrator_login_password)
    body = {'properties': _supplied(state=state, administratorLogin=administrator_login,
                                    administratorLoginPassword=administrator_login_password)}
    return sdk_no_wait(no_wait, client.password_authentications.begin_create_or_replace,
                       resource_group_name, cluster_name, _model(cmd, 'PasswordAuthentication', body),
                       logging_enable=False, cls=_redact_authentication_result)


def password_authentication_update(cmd, client, resource_group_name, cluster_name, state=None,
                                   administrator_login_password=None, no_wait=False):
    require_change(state=state, password=administrator_login_password)
    _password_credentials(state, password=administrator_login_password)
    body = {'properties': _supplied(state=state, administratorLoginPassword=administrator_login_password)}
    return sdk_no_wait(no_wait, client.password_authentications.begin_update,
                       resource_group_name, cluster_name, _model(cmd, 'PasswordAuthenticationForPatchUpdate', body),
                       logging_enable=False, cls=_redact_authentication_result)


def microsoft_entra_authentication_show(client, resource_group_name, cluster_name):
    return client.microsoft_entra_authentications.get(resource_group_name, cluster_name)


def microsoft_entra_authentication_create(cmd, client, resource_group_name, cluster_name, state, no_wait=False):
    return sdk_no_wait(no_wait, client.microsoft_entra_authentications.begin_create_or_replace,
                       resource_group_name, cluster_name,
                       _model(cmd, 'MicrosoftEntraAuthentication', {'properties': {'state': state}}))


def microsoft_entra_authentication_update(cmd, client, resource_group_name, cluster_name, state=None, no_wait=False):
    body = {'properties': _supplied(state=state)}
    return sdk_no_wait(no_wait, client.microsoft_entra_authentications.begin_update,
                       resource_group_name, cluster_name,
                       _model(cmd, 'MicrosoftEntraAuthenticationForPatchUpdate', body))


def entra_administrator_show(client, resource_group_name, cluster_name, object_id):
    return client.microsoft_entra_administrators.get(resource_group_name, cluster_name, object_id)


def entra_administrator_list(client, resource_group_name, cluster_name):
    return client.microsoft_entra_administrators.list(resource_group_name, cluster_name)


def entra_administrator_create(cmd, client, resource_group_name, cluster_name, object_id,
                               principal_name, principal_type, tenant_id=None, no_wait=False):
    guid(object_id)
    if not principal_name or len(principal_name) > 256:
        raise InvalidArgumentValueError('--principal-name must contain 1-256 characters.')
    if tenant_id is None:
        from azure.cli.core._profile import Profile
        tenant_id = Profile(cli_ctx=cmd.cli_ctx).get_subscription()['tenantId']
    guid(tenant_id)
    body = {'properties': {'principalName': principal_name, 'principalType': principal_type, 'tenantId': tenant_id}}
    return sdk_no_wait(no_wait, client.microsoft_entra_administrators.begin_create_or_replace,
                       resource_group_name, cluster_name, object_id,
                       _model(cmd, 'MicrosoftEntraAdministratorAdd', body))


def entra_administrator_delete(client, resource_group_name, cluster_name, object_id, no_wait=False):
    return sdk_no_wait(no_wait, client.microsoft_entra_administrators.begin_delete,
                       resource_group_name, cluster_name, object_id)


def log_capture_configuration_show(client, resource_group_name, cluster_name, log_capture_configuration_name):
    return client.log_capture_configurations.get(resource_group_name, cluster_name, log_capture_configuration_name)


def log_capture_configuration_list(client, resource_group_name, cluster_name):
    return client.log_capture_configurations.list(resource_group_name, cluster_name)


def log_capture_configuration_update(cmd, client, resource_group_name, cluster_name, log_capture_configuration_name,
                                     state=None, retention_days=None, no_wait=False):
    require_change(state=state, retention_days=retention_days)
    if retention_days is not None:
        bounded_int(1, 35)(retention_days)
    body = {'properties': _supplied(state=state, retentionDays=retention_days)}
    return sdk_no_wait(no_wait, client.log_capture_configurations.begin_update, resource_group_name, cluster_name,
                       log_capture_configuration_name, _model(cmd, 'LogCaptureConfigurationForPatchUpdate', body))


def log_file_show(client, resource_group_name, cluster_name, log_file_name):
    return client.log_files.get(resource_group_name, cluster_name, log_file_name)


def log_file_list(client, resource_group_name, cluster_name):
    return client.log_files.list(resource_group_name, cluster_name)


def log_file_list_download_urls(client, resource_group_name, cluster_name, log_file_name):
    # Signed URLs must only reach explicit command output, never HTTP debug logs or telemetry.
    return client.log_files.list_download_urls(resource_group_name, cluster_name, log_file_name, logging_enable=False)


def cdc_show(client, resource_group_name, cluster_name):
    return client.change_data_capture_capabilities.get(resource_group_name, cluster_name)


def cdc_list(client, resource_group_name, cluster_name):
    return client.change_data_capture_capabilities.list(resource_group_name, cluster_name)


def cdc_create(cmd, client, resource_group_name, cluster_name, desired_state,
               auto_upgrade_extension=None, no_wait=False):
    properties = {'desiredState': desired_state}
    if auto_upgrade_extension is not None:
        properties['autoUpgradeExtension'] = _boolean(auto_upgrade_extension)
    return sdk_no_wait(no_wait, client.change_data_capture_capabilities.begin_create_or_replace,
                       resource_group_name, cluster_name,
                       _model(cmd, 'ChangeDataCaptureCapability', {'properties': properties}))


def cdc_update(cmd, client, resource_group_name, cluster_name, desired_state=None,
               auto_upgrade_extension=None, no_wait=False):
    properties = _supplied(desiredState=desired_state)
    if auto_upgrade_extension is not None:
        properties['autoUpgradeExtension'] = _boolean(auto_upgrade_extension)
    return sdk_no_wait(no_wait, client.change_data_capture_capabilities.begin_update,
                       resource_group_name, cluster_name,
                       _model(cmd, 'ChangeDataCaptureCapabilityForPatchUpdate', {'properties': properties}))


def cdc_delete(client, resource_group_name, cluster_name, no_wait=False):
    return sdk_no_wait(no_wait, client.change_data_capture_capabilities.begin_delete, resource_group_name, cluster_name)


def cdc_destination_show(client, resource_group_name, cluster_name, destination_name):
    return client.change_data_capture_destinations.get(resource_group_name, cluster_name, destination_name)


def cdc_destination_list(client, resource_group_name, cluster_name):
    return client.change_data_capture_destinations.list(resource_group_name, cluster_name)


def cdc_destination_create(cmd, client, resource_group_name, cluster_name, destination_name,
                           workspace_id, desired_state, no_wait=False):
    guid(workspace_id)
    body = {'properties': {'desiredState': desired_state, 'destination': {
        'destinationType': 'Fabric', 'protocol': 'Native', 'workspaceId': workspace_id}}}
    return sdk_no_wait(no_wait, client.change_data_capture_destinations.begin_create_or_replace,
                       resource_group_name, cluster_name, destination_name,
                       _model(cmd, 'ChangeDataCaptureDestination', body))


def cdc_destination_update(cmd, client, resource_group_name, cluster_name, destination_name,
                           desired_state=None, no_wait=False):
    body = {'properties': _supplied(desiredState=desired_state)}
    return sdk_no_wait(no_wait, client.change_data_capture_destinations.begin_update,
                       resource_group_name, cluster_name, destination_name,
                       _model(cmd, 'ChangeDataCaptureDestinationForPatchUpdate', body))


def cdc_destination_activate_identity(cmd, client, resource_group_name, cluster_name, destination_name,
                                      identity_assignment, if_match, drain_existing_sessions=False, no_wait=False):
    resource_reference(
        identity_assignment, 'Microsoft.HorizonDb/clusters/changeDataCaptureDestinations/identityAssignments')
    assignment_id = parse_resource_id(identity_assignment)
    if (not _same_cluster_reference(client, assignment_id, resource_group_name, cluster_name) or
            assignment_id['child_name_1'].lower() != destination_name.lower()):
        raise InvalidArgumentValueError('The identity assignment must belong to this destination.')
    if not if_match:
        raise RequiredArgumentMissingError('--if-match requires the destination ETag.')
    assignment = client.identity_assignments.get(
        resource_group_name, cluster_name, destination_name, assignment_id['child_name_2'])
    validation = _get(_properties(assignment), 'validation', {})
    if _get(validation, 'state') != 'Succeeded':
        raise InvalidArgumentValueError('The identity assignment must be successfully validated before activation.')
    body = _model(cmd, 'ActivateChangeDataCaptureIdentityRequest', {
        'identityAssignmentResourceId': identity_assignment,
        'drainExistingSessions': _boolean(drain_existing_sessions)})
    return sdk_no_wait(no_wait, client.change_data_capture_destinations.begin_activate_identity,
                       resource_group_name, cluster_name, destination_name, body,
                       etag=if_match, match_condition=MatchConditions.IfNotModified)


def cdc_destination_delete(client, resource_group_name, cluster_name, destination_name, no_wait=False):
    return sdk_no_wait(no_wait, client.change_data_capture_destinations.begin_delete,
                       resource_group_name, cluster_name, destination_name)


def cdc_identity_assignment_show(client, resource_group_name, cluster_name, destination_name, assignment_name):
    return client.identity_assignments.get(resource_group_name, cluster_name, destination_name, assignment_name)


def cdc_identity_assignment_list(client, resource_group_name, cluster_name, destination_name):
    return client.identity_assignments.list(resource_group_name, cluster_name, destination_name)


def cdc_identity_assignment_create(cmd, client, resource_group_name, cluster_name, destination_name,
                                   assignment_name, identity, no_wait=False):
    identity_reference(identity)
    cluster = client.clusters.get(resource_group_name, cluster_name)
    if identity not in _identities(cluster):
        raise InvalidArgumentValueError(
            '--identity must exactly match a user-assigned identity attached to the Cluster.')
    body = _model(cmd, 'IdentityAssignment', {'properties': {'identityResourceId': identity}})
    return sdk_no_wait(no_wait, client.identity_assignments.begin_create_or_replace,
                       resource_group_name, cluster_name, destination_name, assignment_name, body)


def cdc_identity_assignment_delete(client, resource_group_name, cluster_name, destination_name,
                                   assignment_name, no_wait=False):
    assignment = client.identity_assignments.get(resource_group_name, cluster_name, destination_name, assignment_name)
    if _get(_properties(assignment), 'activation_state') == 'Active':
        raise InvalidArgumentValueError('Only inactive identity assignments can be deleted.')
    return sdk_no_wait(no_wait, client.identity_assignments.begin_delete,
                       resource_group_name, cluster_name, destination_name, assignment_name)


def cdc_configuration_show(client, resource_group_name, cluster_name, configuration_name):
    return client.change_data_capture_configurations.get(resource_group_name, cluster_name, configuration_name)


def cdc_configuration_list(client, resource_group_name, cluster_name):
    return client.change_data_capture_configurations.list(resource_group_name, cluster_name)


def cdc_configuration_create(cmd, client, resource_group_name, cluster_name, configuration_name,
                             database_name, destination, desired_state, no_wait=False):
    resource_reference(destination, 'Microsoft.HorizonDb/clusters/changeDataCaptureDestinations')
    destination_id = parse_resource_id(destination)
    if not _same_cluster_reference(client, destination_id, resource_group_name, cluster_name):
        raise InvalidArgumentValueError('The destination must belong to the same Cluster.')
    body = {'properties': {'databaseName': database_name, 'destinationResourceId': destination,
                           'usageProfile': 'Mirroring', 'desiredState': desired_state}}
    return sdk_no_wait(no_wait, client.change_data_capture_configurations.begin_create_or_replace,
                       resource_group_name, cluster_name, configuration_name,
                       _model(cmd, 'ChangeDataCaptureConfiguration', body))


def cdc_configuration_update(cmd, client, resource_group_name, cluster_name, configuration_name,
                             desired_state=None, no_wait=False):
    body = {'properties': _supplied(desiredState=desired_state)}
    return sdk_no_wait(no_wait, client.change_data_capture_configurations.begin_update,
                       resource_group_name, cluster_name, configuration_name,
                       _model(cmd, 'ChangeDataCaptureConfigurationForPatchUpdate', body))


def cdc_configuration_delete(client, resource_group_name, cluster_name, configuration_name, no_wait=False):
    return sdk_no_wait(no_wait, client.change_data_capture_configurations.begin_delete,
                       resource_group_name, cluster_name, configuration_name)


def private_endpoint_connection_show(client, resource_group_name, cluster_name, private_endpoint_connection_name):
    return client.private_endpoint_connections.get(resource_group_name, cluster_name, private_endpoint_connection_name)


def private_endpoint_connection_list(client, resource_group_name, cluster_name):
    return client.private_endpoint_connections.list(resource_group_name, cluster_name)


def _private_endpoint_state(cmd, client, resource_group_name, cluster_name, name, status, description, no_wait):
    if description is not None:
        validate_description(description)
    properties = {'privateLinkServiceConnectionState': _supplied(status=status, description=description)}
    return sdk_no_wait(no_wait, client.private_endpoint_connections.begin_approve_or_reject,
                       resource_group_name, cluster_name, name,
                       _model(cmd, 'PrivateEndpointConnectionResource', {'properties': properties}))


def private_endpoint_connection_approve(cmd, client, resource_group_name, cluster_name,
                                        private_endpoint_connection_name, description=None, no_wait=False):
    return _private_endpoint_state(cmd, client, resource_group_name, cluster_name,
                                   private_endpoint_connection_name, 'Approved', description, no_wait)


def private_endpoint_connection_reject(cmd, client, resource_group_name, cluster_name,
                                       private_endpoint_connection_name, description=None, no_wait=False):
    return _private_endpoint_state(cmd, client, resource_group_name, cluster_name,
                                   private_endpoint_connection_name, 'Rejected', description, no_wait)


def private_endpoint_connection_delete(client, resource_group_name, cluster_name,
                                       private_endpoint_connection_name, no_wait=False):
    return sdk_no_wait(no_wait, client.private_endpoint_connections.begin_delete,
                       resource_group_name, cluster_name, private_endpoint_connection_name)


def private_link_resource_show(client, resource_group_name, cluster_name, group_name):
    return client.private_link_resources.get(resource_group_name, cluster_name, group_name)


def private_link_resource_list(client, resource_group_name, cluster_name):
    return client.private_link_resources.list(resource_group_name, cluster_name)


def parameter_group_show(client, resource_group_name, parameter_group_name):
    return client.parameter_groups.get(resource_group_name, parameter_group_name)


def parameter_group_list(client, resource_group_name=None):
    if resource_group_name:
        return client.parameter_groups.list_by_resource_group(resource_group_name)
    return client.parameter_groups.list_by_subscription()


def parameter_group_create(cmd, client, resource_group_name, parameter_group_name, location, postgresql_version,
                           parameters=None, description=None, tags=None, no_wait=False):
    if parameters is not None:
        parameters = parameter_values(parameters)
    if description is not None:
        validate_description(description)
    properties = _supplied(postgreSqlVersion=postgresql_version, parameters=parameters, description=description)
    body = _supplied(location=location, tags=tags, properties=properties)
    return sdk_no_wait(no_wait, client.parameter_groups.begin_create_or_replace,
                       resource_group_name, parameter_group_name, _model(cmd, 'ParameterGroup', body))


def parameter_group_update(cmd, client, resource_group_name, parameter_group_name,
                           parameters=None, description=None, tags=None, no_wait=False):
    require_change(parameters=parameters, description=description, tags=tags)
    if parameters is not None:
        parameters = parameter_values(parameters)
    if description is not None:
        validate_description(description)
    body = _supplied(tags=tags)
    properties = _supplied(parameters=parameters, description=description)
    if properties:
        body['properties'] = properties
    return sdk_no_wait(no_wait, client.parameter_groups.begin_update,
                       resource_group_name, parameter_group_name, _model(cmd, 'ParameterGroupForPatchUpdate', body))


def parameter_group_delete(client, resource_group_name, parameter_group_name, no_wait=False):
    return sdk_no_wait(no_wait, client.parameter_groups.begin_delete, resource_group_name, parameter_group_name)


def parameter_group_list_connections(client, resource_group_name, parameter_group_name):
    return client.parameter_groups.list_connections(resource_group_name, parameter_group_name)


def default_parameter_group_show(client, location, name):
    return client.default_parameter_groups.get(location, name)


def default_parameter_group_list(client, location):
    return client.default_parameter_groups.list_by_subscription_and_region(location)
