# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from azure.cli.core.commands.parameters import (
    get_enum_type, tags_type, resource_group_name_type, get_location_type)

from ._validators import (
    bounded_int, description, dns_name, firewall_name, guid, identity_reference, key_uri,
    parameter_group_name, resource_reference, utc_datetime, validate_tags)


def load_arguments(self, _):
    with self.argument_context('horizondb') as c:
        c.argument('resource_group_name', arg_type=resource_group_name_type)
        c.argument('cluster_name', options_list=['--cluster-name'], id_part='name', type=dns_name,
                   help='Name of the parent Cluster.')
        c.argument('pool_name', options_list=['--pool-name'], id_part='child_name_1', type=dns_name,
                   help='Name of the parent Pool.')
        c.argument('location', get_location_type(self.cli_ctx), required=True)
        c.argument('description', type=description, help='Description, at most 1024 characters.')
        c.argument('state', arg_type=get_enum_type(['Enabled', 'Disabled']),
                   help='Desired authentication or log capture state.')
        c.argument('desired_state', arg_type=get_enum_type(['Enabled', 'Disabled']),
                   help='Desired change data capture state.')
        c.argument('postgresql_version', arg_type=get_enum_type(['17', '18']),
                   help='PostgreSQL major version. Immutable after creation.')

    with self.argument_context('horizondb create') as c:
        c.argument('cluster_name', options_list=['--name', '-n'], type=dns_name,
                   help='Name of the new Cluster or matching workflow-owned partial Cluster to resume.')
        c.argument('location', get_location_type(self.cli_ctx), required=False,
                   help='Region. Defaults to the resource group location.')
        c.argument('postgresql_version', help='PostgreSQL major version advertised by the region. Default: 17.')
        c.argument('v_cores', type=bounded_int(1, 192),
                   help='Advertised Provisioned vCore size. Defaults to the smallest generation-1 size.')
        c.argument('authentication', arg_type=get_enum_type(['Entra', 'Password', 'Both']),
                   help='Authentication mode. Default: Entra for the signed-in principal.')
        c.argument('public_access', nargs='+',
                   help='Explicit IPv4 addresses, CIDRs, or start-end ranges. Default: detected caller IPv4 only.')
        c.argument('enable_ai', action='store_true',
                   help='Opt in to system-managed AI Model Management. Disabled by default.')
        c.argument('administrator_object_id', type=guid, help='Tenant object ID of the Entra administrator.')
        c.argument('administrator_principal_name', help='Entra principal display or sign-in name.')
        c.argument('administrator_principal_type', arg_type=get_enum_type(['User', 'Group', 'ServicePrincipal']),
                   help='Entra principal type. Supply together with administrator object ID and principal name.')
        c.argument('tenant_id', type=guid, help='Administrator tenant ID. Defaults to the subscription tenant.')
        c.argument('administrator_login', help='Password administrator login. Default for Password/Both: hdbadmin.')
        c.argument('administrator_login_password',
                   help='Initial password. Prompts securely if omitted; never resets an enabled password on resume. '
                        'Do not use --debug with credentials; expanded arguments can appear in CLI debug logs.')

    with self.argument_context('horizondb cluster') as c:
        c.argument('cluster_name', options_list=['--name', '-n'], id_part='name',
                   help='Name of the Cluster.')
        c.argument('public_access', arg_type=get_enum_type(['Enabled', 'Disabled']),
                   help='Public network access.')
        c.argument('backup_retention_days', type=bounded_int(1, 35),
                   help='Automatic backup retention in days, from 1 to 35.')
        c.argument('data_encryption_type', arg_type=get_enum_type(['SystemManaged', 'AzureKeyVault']),
                   help='Data encryption type. Cannot change after creation.')
        c.argument('primary_encryption_key_uri', type=key_uri,
                   help='Versionless HTTPS Azure Key Vault key URI.')
        c.argument('primary_encryption_identity', type=identity_reference,
                   help='ARM ID of the attached user-assigned identity used for encryption.')
        c.argument('user_assigned_identities', nargs='+', type=identity_reference,
                   help='User-assigned managed identity ARM IDs. PATCH merges these assignments.')
    with self.argument_context('horizondb cluster create') as c:
        c.argument('cluster_name', type=dns_name)
        c.argument('creation_mode', arg_type=get_enum_type(['Create', 'PointInTimeRestore']),
                   help='Creation mode. Default: Create.')
        c.argument('creation_source_cluster', type=resource_reference,
                   help='Source Cluster ARM ID, required only for point-in-time restore.')
        c.argument('creation_source_cluster_location',
                   help='Source Cluster location, required for cross-location restore.')
        c.argument('point_in_time', type=utc_datetime,
                   help='Restore time in RFC 3339 UTC. Cross-location restore uses the latest backup.')
        c.argument('geo_redundancy', arg_type=get_enum_type(['Enabled', 'Disabled']),
                   help='Backup geo-redundancy. Required with backup retention; immutable after creation.')
    with self.argument_context('horizondb cluster update') as c:
        c.argument('identity_type', arg_type=get_enum_type(['None', 'UserAssigned']),
                   help='Identity type. None clears all user-assigned identities.')
        c.argument('remove_user_assigned_identities', nargs='+', type=identity_reference,
                   help='User-assigned identity ARM IDs to remove using null PATCH entries.')
        c.argument('ai_model_management', arg_type=get_enum_type(['Enabled', 'Disabled']),
                   help='Enable or disable AI Model Management.')

    for group in ('cluster', 'parameter-group'):
        with self.argument_context('horizondb ' + group) as c:
            c.argument('tags', tags_type, validator=validate_tags,
                       help='Space-separated key[=value] tags, a JSON object, or @file. Use --tags to clear tags.')

    with self.argument_context('horizondb pool') as c:
        c.argument('pool_name', options_list=['--name', '-n'], id_part='child_name_1',
                   help='Name of the Pool, different from the Cluster name.')
        c.argument('compute_type', arg_type=get_enum_type(['Provisioned']), help='Compute model type.')
        c.argument('compute_processor', arg_type=get_enum_type(['Intel', 'AMD']), help='Compute processor family.')
        c.argument('compute_generation', type=bounded_int(1), help='Compute generation, at least 1.')
        c.argument('v_cores', type=bounded_int(1, 192), help='Provisioned vCores, from 1 to 192.')
        c.argument('node_count', type=bounded_int(1, 16), help='Configured Node count, from 1 to 16.')
        c.argument('high_availability_mode', arg_type=get_enum_type(['Disabled', 'AnyZone', 'ZoneRedundant']),
                   help='One Node requires Disabled; multiple Nodes require AnyZone or ZoneRedundant.')
        c.argument('parameter_group', type=resource_reference,
                   help='Custom or location-scoped default ParameterGroup ARM ID.')
        c.argument('parameter_apply_method', arg_type=get_enum_type(['Immediate', 'PendingReboot']),
                   help='Apply method, required with --parameter-group.')
        c.argument('maintenance_management_type', arg_type=get_enum_type(['System']),
                   help='System-managed maintenance.')
    with self.argument_context('horizondb pool create') as c:
        c.argument('pool_name', type=dns_name)

    for group, name, part in [
            ('node', 'node_name', 'child_name_2'),
            ('endpoint', 'endpoint_name', 'child_name_1'),
            ('firewall-rule', 'firewall_rule_name', 'child_name_2'),
            ('log-capture-configuration', 'log_capture_configuration_name', 'child_name_1'),
            ('log-file', 'log_file_name', 'child_name_1'),
            ('change-data-capture-destination', 'destination_name', 'child_name_1'),
            ('change-data-capture-identity-assignment', 'assignment_name', 'child_name_2'),
            ('change-data-capture-configuration', 'configuration_name', 'child_name_1'),
            ('private-endpoint-connection', 'private_endpoint_connection_name', 'child_name_1'),
            ('private-link-resource', 'group_name', 'child_name_1'),
            ('parameter-group', 'parameter_group_name', 'name'),
            ('default-parameter-group', 'name', 'child_name_1')]:
        name_type = {
            'firewall_rule_name': firewall_name,
            'parameter_group_name': parameter_group_name,
            'log_file_name': guid,
            'destination_name': dns_name,
            'assignment_name': dns_name,
            'configuration_name': dns_name,
            'node_name': dns_name,
            'endpoint_name': dns_name,
        }.get(name, str)
        with self.argument_context('horizondb ' + group) as c:
            c.argument(name, options_list=['--name', '-n'], id_part=part, type=name_type,
                       help='Name of the resource.')

    with self.argument_context('horizondb firewall-rule create') as c:
        c.argument('start_ip_address', help='First IPv4 address in the range.')
        c.argument('end_ip_address', help='Last IPv4 address in the range.')

    with self.argument_context('horizondb authentication password') as c:
        c.argument('administrator_login_password', options_list=['--administrator-login-password', '-p'],
                   help='Secret administrator password, 8-128 characters. '
                        'Omit to securely prompt during initial setup.')
    with self.argument_context('horizondb authentication password create') as c:
        c.argument('administrator_login',
                   help='Secret administrator login, 1-63 characters. Omit to securely prompt during initial setup.')

    with self.argument_context('horizondb microsoft-entra-administrator') as c:
        c.argument('object_id', type=guid, id_part='child_name_1', help='Microsoft Entra principal object ID.')
    with self.argument_context('horizondb microsoft-entra-administrator create') as c:
        c.argument('principal_name', help='Principal name, at most 256 characters.')
        c.argument('principal_type', arg_type=get_enum_type(['User', 'Group', 'ServicePrincipal']),
                   help='Microsoft Entra principal type.')
        c.argument('tenant_id', type=guid, help='Tenant ID. Defaults to the active subscription tenant.')
    with self.argument_context('horizondb log-capture-configuration') as c:
        c.argument('log_capture_configuration_name', options_list=['--name', '-n'], id_part='child_name_1',
                   arg_type=get_enum_type(['PostgreSqlServer']), help='Log type: PostgreSqlServer.')
        c.argument('retention_days', type=bounded_int(1, 35), help='Captured log retention in days, from 1 to 35.')
    with self.argument_context('horizondb change-data-capture') as c:
        c.argument('auto_upgrade_extension', arg_type=get_enum_type(['true', 'false']),
                   help='Automatically upgrade the CDC extension.')
    with self.argument_context('horizondb change-data-capture-destination create') as c:
        c.argument('workspace_id', type=guid, help='Microsoft Fabric workspace GUID.')
    with self.argument_context('horizondb change-data-capture-destination activate-identity') as c:
        c.argument('identity_assignment', type=resource_reference,
                   help='Full ARM ID of a validated identity assignment under this destination.')
        c.argument('if_match', help='Required destination ETag.')
        c.argument('drain_existing_sessions', arg_type=get_enum_type(['true', 'false']),
                   help='Drain existing sessions. Default: false.')
    with self.argument_context('horizondb change-data-capture-identity-assignment') as c:
        c.argument('destination_name', options_list=['--destination-name'], id_part='child_name_1', type=dns_name,
                   help='Name of the parent CDC destination.')
        c.argument('identity', type=identity_reference,
                   help='Full ARM ID of a user-assigned identity already attached to the Cluster.')
    with self.argument_context('horizondb change-data-capture-configuration create') as c:
        c.argument('database_name', help='PostgreSQL database to enroll in Microsoft Fabric Mirroring.')
        c.argument('destination', type=resource_reference, help='Full ARM ID of the CDC destination.')
    with self.argument_context('horizondb parameter-group') as c:
        c.argument('parameters', nargs='+',
                   help='Space-separated name=value overrides, a JSON name/value object array, or @file.')
    with self.argument_context('horizondb default-parameter-group') as c:
        c.argument('location', get_location_type(self.cli_ctx), required=True, id_part='name',
                   help='Azure location.')

    # LIST selectors identify the parent collection rather than an individual resource.
    for group in ('cluster', 'parameter-group'):
        with self.argument_context('horizondb ' + group + ' list') as c:
            c.argument('resource_group_name', required=False)
