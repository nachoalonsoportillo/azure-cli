# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------


def load_command_table(self, _):
    from azure.cli.core.commands import CliCommandType
    from ._client_factory import cf_horizondb
    create_type = CliCommandType(
        operations_tmpl='azure.cli.command_modules.horizondb._create#{}',
        client_factory=cf_horizondb)
    with self.command_group('horizondb', custom_command_type=create_type) as g:
        g.custom_command('create', 'horizondb_create')

    with self.command_group('horizondb operation') as g:
        g.custom_command('list', 'operation_list')

    with self.command_group('horizondb cluster') as g:
        g.custom_command('create', 'cluster_create', supports_no_wait=True)
        g.custom_show_command('show', 'cluster_show')
        g.custom_command('list', 'cluster_list')
        g.custom_command('update', 'cluster_update', supports_no_wait=True)
        g.custom_command('delete', 'cluster_delete', supports_no_wait=True, confirmation=True)
        g.custom_command('failover', 'cluster_failover', supports_no_wait=True)
        g.custom_wait_command('wait', 'cluster_show')

    with self.command_group('horizondb pool') as g:
        g.custom_command('create', 'pool_create', supports_no_wait=True)
        g.custom_show_command('show', 'pool_show')
        g.custom_command('list', 'pool_list')
        g.custom_command('update', 'pool_update', supports_no_wait=True)
        g.custom_command('delete', 'pool_delete', supports_no_wait=True, confirmation=True)
        g.custom_command('start', 'pool_start', supports_no_wait=True)
        g.custom_command('stop', 'pool_stop', supports_no_wait=True)
        g.custom_command('restart', 'pool_restart', supports_no_wait=True)
        g.custom_wait_command('wait', 'pool_show')

    with self.command_group('horizondb node') as g:
        g.custom_show_command('show', 'node_show')
        g.custom_command('list', 'node_list')

    with self.command_group('horizondb endpoint') as g:
        g.custom_show_command('show', 'endpoint_show')
        g.custom_command('list', 'endpoint_list')

    with self.command_group('horizondb firewall-rule') as g:
        g.custom_command('create', 'firewall_rule_create', supports_no_wait=True)
        g.custom_show_command('show', 'firewall_rule_show')
        g.custom_command('list', 'firewall_rule_list')
        g.custom_command('delete', 'firewall_rule_delete', supports_no_wait=True, confirmation=True)
        g.custom_wait_command('wait', 'firewall_rule_show')

    with self.command_group('horizondb authentication') as g:
        g.custom_command('list', 'authentication_list')
    with self.command_group('horizondb authentication password') as g:
        g.custom_show_command('show', 'password_authentication_show')
        g.custom_command('create', 'password_authentication_create', supports_no_wait=True)
        g.custom_command('update', 'password_authentication_update', supports_no_wait=True)
        g.custom_wait_command('wait', 'password_authentication_show')
    with self.command_group('horizondb authentication microsoft-entra') as g:
        g.custom_show_command('show', 'microsoft_entra_authentication_show')
        g.custom_command('create', 'microsoft_entra_authentication_create', supports_no_wait=True)
        g.custom_command('update', 'microsoft_entra_authentication_update', supports_no_wait=True)
        g.custom_wait_command('wait', 'microsoft_entra_authentication_show')

    with self.command_group('horizondb microsoft-entra-administrator') as g:
        g.custom_command('create', 'entra_administrator_create', supports_no_wait=True)
        g.custom_show_command('show', 'entra_administrator_show')
        g.custom_command('list', 'entra_administrator_list')
        g.custom_command('delete', 'entra_administrator_delete', supports_no_wait=True, confirmation=True)
        g.custom_wait_command('wait', 'entra_administrator_show')

    with self.command_group('horizondb log-capture-configuration') as g:
        g.custom_show_command('show', 'log_capture_configuration_show')
        g.custom_command('list', 'log_capture_configuration_list')
        g.custom_command('update', 'log_capture_configuration_update', supports_no_wait=True)
        g.custom_wait_command('wait', 'log_capture_configuration_show')
    with self.command_group('horizondb log-file') as g:
        g.custom_show_command('show', 'log_file_show')
        g.custom_command('list', 'log_file_list')
        g.custom_command('list-download-urls', 'log_file_list_download_urls')

    with self.command_group('horizondb change-data-capture') as g:
        g.custom_show_command('show', 'cdc_show')
        g.custom_command('list', 'cdc_list')
        g.custom_command('create', 'cdc_create', supports_no_wait=True)
        g.custom_command('update', 'cdc_update', supports_no_wait=True)
        g.custom_command('delete', 'cdc_delete', supports_no_wait=True, confirmation=True)
        g.custom_wait_command('wait', 'cdc_show')
    with self.command_group('horizondb change-data-capture-destination') as g:
        g.custom_show_command('show', 'cdc_destination_show')
        g.custom_command('list', 'cdc_destination_list')
        g.custom_command('create', 'cdc_destination_create', supports_no_wait=True)
        g.custom_command('update', 'cdc_destination_update', supports_no_wait=True)
        g.custom_command('activate-identity', 'cdc_destination_activate_identity', supports_no_wait=True)
        g.custom_command('delete', 'cdc_destination_delete', supports_no_wait=True, confirmation=True)
        g.custom_wait_command('wait', 'cdc_destination_show')
    with self.command_group('horizondb change-data-capture-identity-assignment') as g:
        g.custom_show_command('show', 'cdc_identity_assignment_show')
        g.custom_command('list', 'cdc_identity_assignment_list')
        g.custom_command('create', 'cdc_identity_assignment_create', supports_no_wait=True)
        g.custom_command('delete', 'cdc_identity_assignment_delete', supports_no_wait=True, confirmation=True)
        g.custom_wait_command('wait', 'cdc_identity_assignment_show')
    with self.command_group('horizondb change-data-capture-configuration') as g:
        g.custom_show_command('show', 'cdc_configuration_show')
        g.custom_command('list', 'cdc_configuration_list')
        g.custom_command('create', 'cdc_configuration_create', supports_no_wait=True)
        g.custom_command('update', 'cdc_configuration_update', supports_no_wait=True)
        g.custom_command('delete', 'cdc_configuration_delete', supports_no_wait=True, confirmation=True)
        g.custom_wait_command('wait', 'cdc_configuration_show')

    with self.command_group('horizondb private-endpoint-connection') as g:
        g.custom_show_command('show', 'private_endpoint_connection_show')
        g.custom_command('list', 'private_endpoint_connection_list')
        g.custom_command('approve', 'private_endpoint_connection_approve', supports_no_wait=True)
        g.custom_command('reject', 'private_endpoint_connection_reject', supports_no_wait=True)
        g.custom_command('delete', 'private_endpoint_connection_delete', supports_no_wait=True, confirmation=True)
        g.custom_wait_command('wait', 'private_endpoint_connection_show')
    with self.command_group('horizondb private-link-resource') as g:
        g.custom_show_command('show', 'private_link_resource_show')
        g.custom_command('list', 'private_link_resource_list')

    with self.command_group('horizondb parameter-group') as g:
        g.custom_command('create', 'parameter_group_create', supports_no_wait=True)
        g.custom_show_command('show', 'parameter_group_show')
        g.custom_command('list', 'parameter_group_list')
        g.custom_command('update', 'parameter_group_update', supports_no_wait=True)
        g.custom_command('delete', 'parameter_group_delete', supports_no_wait=True, confirmation=True)
        g.custom_command('list-connections', 'parameter_group_list_connections')
        g.custom_wait_command('wait', 'parameter_group_show')
    with self.command_group('horizondb default-parameter-group') as g:
        g.custom_show_command('show', 'default_parameter_group_show')
        g.custom_command('list', 'default_parameter_group_list')
