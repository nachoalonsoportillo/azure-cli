# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

# pylint: disable=line-too-long

import json

from knack.help_files import helps


def _help(path, summary, examples=(), details=None, parameters=None, group=False):
    """Register YAML help, quoting prose so colons and examples remain literal."""
    lines = [
        'type: ' + ('group' if group else 'command'),
        'short-summary: ' + json.dumps(summary),
    ]
    if details:
        lines.append('long-summary: ' + json.dumps(details))
    if parameters:
        lines.append('parameters:')
        for name, description in parameters.items():
            lines.extend(['  - name: ' + name, '    short-summary: ' + json.dumps(description)])
    if examples:
        lines.append('examples:')
        for title, command in examples:
            lines.extend(['  - name: ' + json.dumps(title), '    text: ' + json.dumps(command)])
    helps['horizondb' + (' ' + path if path else '')] = '\n'.join(lines)


_CLUSTER = '--resource-group example-rg --cluster-name example-cluster'
_POOL = _CLUSTER + ' --pool-name primary'
_RESOURCE = '--resource-group example-rg --name example-cluster'
_LOCATION = '--location eastus2'

_help('create', 'Create or resume a minimal usable HorizonDB Cluster, Pool, authentication, and caller firewall.',
      [
          ('Use Entra for the signed-in identity and allow only its detected public IPv4.',
           'az horizondb create --resource-group example-rg --name example-cluster'),
          ('Use explicit IPv4 rules without public IP discovery and opt in to system-managed AI models.',
           'az horizondb create --resource-group example-rg --name example-cluster '
           '--location indonesiacentral --public-access 203.0.113.10 198.51.100.0/28 --enable-ai'),
          ('Enable both authentication methods, prompting securely for the initial password.',
           'az horizondb create --resource-group example-rg --name example-cluster --authentication Both '
           '--administrator-login hdbadmin --public-access 203.0.113.10'),
      ],
      details='Awaits every stage; --no-wait is not supported. Defaults to the resource group region, PostgreSQL 17, '
              'the smallest advertised generation-1 Provisioned size, one node, HA Disabled, SystemManaged encryption, '
              'and System maintenance. Pool name is the first 58 Cluster-name characters plus -pool. '
              'Entra is enabled for the signed-in user or service principal; Password is disabled unless requested. '
              'Explicit administrator identifiers avoid Graph discovery for service connections lacking directory access. '
              'AI Model Management is explicitly Disabled unless --enable-ai is supplied. '
              'Default access is only the detected caller IPv4, never all IPs. Explicit rules bypass IP discovery. '
              'Reruns resume matching workflow-owned deployments using ownership and intent tags; unrelated clusters, '
              'changed intent, and conflicting resource settings are rejected without replacement. '
              'Keep the same options and IPv4 intent when resuming. Enabled passwords are never reset; use the password '
              'update command to rotate them. No automatic rollback or deletion is attempted on failure. '
              'Output keys are cluster, pool, connection, firewall, and aiModelManagement. '
              'Connection endpoints contain id, host, scope, and role; credentials and tokens are never returned. '
              'Do not use --debug with credentials: CLI debug logs can include expanded command arguments.')
_DEFAULT_GROUP = '"$DEFAULT_PARAMETER_GROUP_ID"'
_GUID = '11111111-1111-1111-1111-111111111111'
_IDENTITY = '"$IDENTITY_ID"'

_GROUPS = {
    '': (
        'Manage HorizonDB clusters, compute pools, authentication, connectivity, logs, and change data capture.',
        'Start with az horizondb create to provision a minimal instance with its dependencies. '
        'Use the resource-specific groups for advanced configuration and ongoing management. '
        'Commands use management API 2026-10-01-preview. Long-running resource commands wait by default; '
        'use --no-wait to return after acceptance and wait commands to poll completion. '
        'Where supported, --ids replaces individual resource selectors. Examples are independent, '
        'not an end-to-end script; substitute your resource names and ARM IDs.'),
    'operation': ('List Microsoft.HorizonDb resource-provider operations.', None),
    'cluster': (
        'Manage HorizonDB clusters and point-in-time restore.',
        'A cluster name is a lowercase DNS label of 1-63 characters. Encryption type and PostgreSQL '
        'major version are immutable. Restore hydrates service-owned database storage, not pools, '
        'pool endpoints, firewall rules, parameter-group associations, or other children.'),
    'pool': (
        'Manage provisioned compute pools and power operations.',
        'This API supports at most one pool per cluster. The pool name must differ from the cluster '
        'name. The first pool is designated read-write automatically. Configured nodeCount and '
        'highAvailability.mode are not realized topology; use node show/list to inspect allocation. '
        'Only customer-initiated restart applies static or PendingReboot parameter changes. '
        'Stop, start, and scheduled maintenance leave them pending.'),
    'node': (
        'Inspect read-only, service-managed pool nodes.',
        'Nodes expose realized roles and availability zones. Customers cannot create, modify, or delete nodes.'),
    'endpoint': (
        'Discover read-only, service-generated cluster and pool endpoints.',
        'Use the cluster name for the ReadWrite endpoint or the pool name for its ReadOnly endpoint. '
        'Endpoint aliases are not supported. A ReadOnly endpoint remains discoverable but has no '
        'routing destination when its pool is stopped, empty, or has only the read-write node.'),
    'firewall-rule': (
        'Manage IPv4 firewall ranges directly under a pool.',
        'No PATCH operation exists. Replace a rule with create using the same name, complete desired '
        'IPv4 range, and description. Descriptions are limited to 1024 characters.'),
    'authentication': (
        'List service-created cluster authentication configurations.',
        'Each cluster has passwordMethod and microsoftEntra configurations. Their fixed names are '
        'hidden by the password and microsoft-entra subgroups. Both methods may be disabled. '
        'There are no authentication DELETE operations.'),
    'authentication password': (
        'Configure username-and-password authentication on the fixed passwordMethod resource.',
        'Create maps to PUT, not an additional authentication method. Initial setup requires state '
        'Enabled and both credentials. Administrator login is create-only; renaming is not supported. '
        'PATCH preserves omitted values. Disabling cannot include credentials and does not change '
        'database users or roles. Credentials are write-only secrets; use secure input or variables, '
        'never literal passwords in command history. Do not use --debug when supplying credentials: '
        'CLI debug logs can include expanded command arguments.'),
    'authentication microsoft-entra': (
        'Configure the fixed microsoftEntra authentication resource.',
        'Create maps to idempotent PUT and requires state. Update is sparse. Disabling does not '
        'remove database roles or users created through the administrator API or pgaadauth.'),
    'microsoft-entra-administrator': (
        'Manage PostgreSQL administrator roles mapped to Microsoft Entra principals.',
        'Mappings represent PostgreSQL roles that are members of azure_pg_admin. The object ID is '
        'an RFC 4122 GUID; tenant defaults to the subscription tenant.'),
    'log-capture-configuration': (
        'Manage predefined PostgreSQL server-log capture and retention.',
        'Only PostgreSqlServer is supported. The service creates the configuration with the cluster. '
        'Customers may show, list, and patch it, not create, replace, or delete it. Use state Disabled '
        'to disable capture; omitted PATCH fields preserve their values.'),
    'log-file': (
        'Discover captured log metadata and explicitly request short-lived download URLs.',
        'Files are read-only cluster children. Metadata includes source pool, node and role, times, '
        'retention deadline, mutability, and size in KiB. Signed download URLs are bearer credentials '
        'and are returned only by list-download-urls. No compressed copies are exposed.'),
    'change-data-capture': (
        'Manage the cluster change data capture capability named default.',
        'The fixed name is hidden. This API supports Microsoft Fabric Native protocol with Mirroring '
        'usage, not Event Hubs, Kafka, or ChangeStreaming destinations.'),
    'change-data-capture-destination': (
        'Manage Microsoft Fabric change data capture destinations.',
        'The CLI supplies destinationType Fabric, protocol Native, and usageProfile Mirroring. '
        'Configure destination authorization separately from attaching the managed identity to '
        'the cluster. Activation requires the destination ETag and a validated identity assignment.'),
    'change-data-capture-identity-assignment': (
        'Manage destination identity candidates for Microsoft Fabric.',
        'The identity must exactly match a user-assigned managed identity ARM ID attached to the '
        'parent cluster. Attach it with cluster update --user-assigned-identities first. Attachment '
        'does not grant Fabric RBAC permissions; configure destination roles separately. '
        'Delete all referencing assignments before detaching an identity from the cluster.'),
    'change-data-capture-configuration': (
        'Manage Microsoft Fabric Mirroring database enrollment.',
        'Use full ARM IDs for destination references. Deleting enrollment never deletes the Fabric '
        'mirrored-database item; first stop or detach destination-side replication.'),
    'private-endpoint-connection': (
        'Inspect, approve, reject, or delete cluster private endpoint connections.',
        'Approve and reject replace approval state using PUT. Pending is service-managed and cannot '
        'be set. A cluster needs a pool before connection creation or approval; otherwise approval '
        'fails with 409 PrivateEndpointRequiresPool. Rejection and cleanup remain allowed.'),
    'private-link-resource': (
        'Discover cluster private link groups and DNS zone metadata.',
        'Group IDs are endpoint resource names, not default. Use the cluster name for its ReadWrite '
        'endpoint or a pool name for that pool ReadOnly endpoint.'),
    'parameter-group': (
        'Manage custom PostgreSQL parameter groups and pool associations.',
        'PostgreSQL version is immutable. Values distinguish effective value, system defaultValue, '
        'and valueSource; UserOverride means explicitly supplied even when equal to the default. '
        'Read-only parameters cannot be overridden. Each connected pool controls apply timing; '
        'static changes require a customer-initiated pool restart. An update conflicts while a '
        'previous group update is processing; deletion conflicts while any pool is associated.'),
    'default-parameter-group': (
        'Discover location-scoped default PostgreSQL parameter groups.',
        'The service selects at most one default group per PostgreSQL version in a location. '
        'Default groups contain engine-defined parameters only and are read-only.'),
}
for _path, (_summary, _details) in _GROUPS.items():
    _help(_path, _summary, details=_details, group=True)


def _command(path, summary, arguments='', details=None, parameters=None, title=None):
    _help(path, summary, [(title or summary, 'az horizondb ' + path + (' ' + arguments if arguments else ''))],
          details, parameters)


_command('operation list', 'List Microsoft.HorizonDb provider operations.',
         '--subscription "$SUBSCRIPTION_ID"')

_CLUSTER_CREATE_PARAMETERS = {
    '--postgresql-version': 'PostgreSQL major version: 17 or 18. Required for ordinary creation; omit for restore.',
    '--creation-mode': 'Create (default) or PointInTimeRestore.',
    '--creation-source-cluster': 'Source cluster ARM ID. Required only for PointInTimeRestore.',
    '--creation-source-cluster-location': 'Source location. Required for cross-location restore; prohibited for Create.',
    '--point-in-time': 'RFC 3339 UTC timestamp. Required for restore only. Cross-location restore ignores the '
                       'timestamp and uses the latest available backup.',
    '--public-access': 'Enabled or Disabled; defaults to Disabled.',
    '--geo-redundancy': 'Enabled or Disabled. Required with backup configuration; immutable. '
                        'Must be Disabled with AzureKeyVault encryption.',
    '--backup-retention-days': 'Automatic-backup retention, 1-35 days; default 7. Also requires --geo-redundancy.',
    '--data-encryption-type': 'SystemManaged (default) or AzureKeyVault. Immutable after creation.',
    '--primary-encryption-key-uri': 'Versionless Key Vault key URI. Required with AzureKeyVault; '
                                    'omit for SystemManaged. Versionless keys support automatic rotation.',
    '--primary-encryption-identity': 'User-assigned identity ARM ID attached through --user-assigned-identities '
                                    'and authorized for the key. Required with AzureKeyVault.',
    '--user-assigned-identities': 'Space-separated user-assigned identity ARM IDs. PUT replaces the supplied '
                                  'assignment set; omission preserves existing assignments. No system-assigned identity.',
}
_help('cluster create', 'Create or replace a cluster, or restore service-owned storage to a point in time.', [
    ('Create a cluster with PostgreSQL 17 and disabled public access.',
     'az horizondb cluster create ' + _RESOURCE + ' ' + _LOCATION +
     ' --postgresql-version 17 --public-access Disabled'),
    ('Restore in the same location; obtain SOURCE_CLUSTER_ID from cluster show --query id --output tsv.',
     'az horizondb cluster create --resource-group example-rg --name restored-cluster --location eastus2 '
     '--creation-mode PointInTimeRestore --creation-source-cluster "$SOURCE_CLUSTER_ID" '
     '--point-in-time "2026-08-30T12:00:00Z"'),
    ('Restore the latest available cross-region backup; the required timestamp is ignored.',
     'az horizondb cluster create --resource-group example-rg --name cross-region-restored-cluster '
     '--location westus2 --creation-mode PointInTimeRestore --creation-source-cluster "$SOURCE_CLUSTER_ID" '
     '--creation-source-cluster-location eastus2 --point-in-time "2026-08-30T12:00:00Z"'),
    ('Create with a versionless customer-managed key and its attached, authorized identity ARM ID.',
     'az horizondb cluster create --resource-group example-rg --name encrypted-cluster --location eastus2 '
     '--postgresql-version 17 --geo-redundancy Disabled --user-assigned-identities "$PRIMARY_ENCRYPTION_ID" '
     '--data-encryption-type AzureKeyVault '
     '--primary-encryption-key-uri "https://primary-key-vault.vault.azure.net/keys/horizondb-primary-key" '
     '--primary-encryption-identity "$PRIMARY_ENCRYPTION_ID"'),
], 'Restore does not clone pools, pool endpoints, firewall rules, parameter associations, or other children. '
   'The new cluster receives a service-generated cluster endpoint. AI Model Management is update-only. '
   'On replacement of an existing cluster, change only one primary key or identity per request and wait '
   'for completion before changing the other.', _CLUSTER_CREATE_PARAMETERS)
_command('cluster show', 'Show cluster configuration, read-write pool designation, and provisioning state.', _RESOURCE)
_help('cluster list', 'List clusters in a resource group or subscription.', [
    ('List clusters in a resource group.', 'az horizondb cluster list --resource-group example-rg --output table'),
    ('List clusters in a subscription.', 'az horizondb cluster list --subscription "$SUBSCRIPTION_ID" --output table'),
])
_help('cluster update', 'Sparsely update cluster settings, tags, backup retention, encryption references, or identities.', [
    ('Enable public access and update retention without waiting.',
     'az horizondb cluster update ' + _RESOURCE + ' --public-access Enabled --backup-retention-days 21 --no-wait'),
    ('Enable AI Model Management.', 'az horizondb cluster update ' + _RESOURCE + ' --ai-model-management Enabled'),
    ('Rotate only the primary key and wait before changing its identity.',
     'az horizondb cluster update --resource-group example-rg --name encrypted-cluster '
     '--primary-encryption-key-uri "https://primary-key-vault.vault.azure.net/keys/horizondb-replacement-key"'),
    ('Attach a user-assigned identity before creating a CDC identity assignment.',
     'az horizondb cluster update ' + _RESOURCE + ' --user-assigned-identities "$IDENTITY_ID"'),
    ('Remove an unreferenced user-assigned identity using its full ARM ID.',
     'az horizondb cluster update --ids "$CLUSTER_ID" --remove-user-assigned-identities "$IDENTITY_ID"'),
], 'Omitted settings are preserved. Encryption type cannot change; primary key URI and primary identity '
   'cannot change together. Wait for one operation to complete before changing the other. '
   'PATCH merges identity entries; empty dictionaries do not clear assignments. '
   'An identity referenced by a CDC assignment cannot be removed.', {
       '--data-encryption-type': 'If supplied, must match the existing SystemManaged or AzureKeyVault type.',
       '--primary-encryption-key-uri': 'Versionless Key Vault key URI; cannot change with primary identity.',
       '--primary-encryption-identity': 'Attached user-assigned identity ARM ID; cannot change with primary key.',
       '--backup-retention-days': 'Automatic-backup retention, 1-35 days.',
       '--ai-model-management': 'Enabled or Disabled. Supported only on update.',
       '--user-assigned-identities': 'Space-separated ARM IDs to merge into existing assignments.',
       '--remove-user-assigned-identities': 'Space-separated ARM IDs to remove with null PATCH dictionary entries.',
       '--identity-type': 'None clears assignments; UserAssigned assigns user identities. No system-assigned identity.',
})
_command('cluster delete', 'Delete a cluster and its children asynchronously.', _RESOURCE + ' --yes --no-wait',
         'Deletion starts immediately after acceptance. Examples are independent of creation examples.')
_command('cluster failover', 'Trigger service-selected failover in the designated read-write pool.',
         _RESOURCE + ' --no-wait',
         'No target node or availability zone is accepted. The service uses automatic-failover eligibility '
         'and node-selection rules. Returns the updated cluster after completion unless --no-wait is used. '
         'An ineligible pool or conflicting operation returns 409 Conflict.')

_POOL_PARAMETERS = {
    '--compute-type': 'Provisioned only. Required for create/replace; optional on update.',
    '--compute-processor': 'Intel or AMD. Required for create/replace; optional on update.',
    '--compute-generation': 'Integer at least 1. Required for create/replace; optional on update.',
    '--v-cores': 'Provisioned vCores, 1-192. Required for create/replace; optional on update.',
    '--node-count': '1-16. Value 1 requires Disabled HA; 2-16 require AnyZone or ZoneRedundant. '
                    'When crossing these ranges on update, supply both count and HA mode.',
    '--high-availability-mode': 'Disabled, AnyZone, or ZoneRedundant. Required on create; omission on update '
                                'preserves mode. ZoneRedundant requires cross-zone placement.',
    '--parameter-group': 'Full custom/default group ARM ID. Initial pool creation requires the corresponding '
                         'location default. Always pair with --parameter-apply-method.',
    '--parameter-apply-method': 'Immediate or PendingReboot. Required whenever --parameter-group is supplied.',
    '--maintenance-management-type': 'System only; maintenance is system-managed.',
}
_command('pool create', 'Create or replace the single provisioned compute pool in a cluster.',
         _CLUSTER + ' --name primary --compute-type Provisioned --compute-processor Intel '
         '--compute-generation 3 --v-cores 8 --node-count 3 --high-availability-mode AnyZone '
         '--parameter-group ' + _DEFAULT_GROUP + ' --parameter-apply-method Immediate',
         'Obtain DEFAULT_PARAMETER_GROUP_ID using default-parameter-group list --location eastus2 '
         '--query "[?properties.postgreSqlVersion==\'17\'].id | [0]" --output tsv. '
         'The pool name must differ from its cluster name. Only one pool is supported. '
         'Pools do not support resource tags.',
         _POOL_PARAMETERS)
_command('pool show', 'Show configured compute, HA, parameter synchronization, and provisioning state.',
         _CLUSTER + ' --name primary', 'Use node show/list to inspect realized topology.')
_command('pool list', 'List compute pools in a cluster.', _CLUSTER + ' --output table')
_help('pool update', 'Sparsely update compute, HA, parameter association, or maintenance.', [
    ('Resize provisioned compute.', 'az horizondb pool update ' + _CLUSTER + ' --name primary --v-cores 32'),
    ('Use two nodes with cross-zone placement.',
     'az horizondb pool update ' + _CLUSTER +
     ' --name primary --node-count 2 --high-availability-mode ZoneRedundant --no-wait'),
    ('Use one node and disable high availability.',
     'az horizondb pool update ' + _CLUSTER +
     ' --name primary --node-count 1 --high-availability-mode Disabled --no-wait'),
    ('Associate a custom group and defer parameter application until restart.',
     'az horizondb pool update ' + _CLUSTER + ' --name primary '
     '--parameter-group "$CUSTOM_PARAMETER_GROUP_ID" --parameter-apply-method PendingReboot'),
    ('Return to the location default parameter group.',
     'az horizondb pool update ' + _CLUSTER + ' --name primary '
     '--parameter-group "$DEFAULT_PARAMETER_GROUP_ID" --parameter-apply-method Immediate'),
], 'Compute PATCH recursively preserves omitted fields. Supply a complete target model when changing '
   'compute type; only Provisioned is supported. Node allocation and HA updates are atomic on failure. '
   'Changing to ZoneRedundant may replace a read-only node and disconnect its sessions. '
   'Pools do not support resource tags.', _POOL_PARAMETERS)
_command('pool delete', 'Delete a pool, its pool endpoint, and firewall rules.',
         _CLUSTER + ' --name primary --yes --no-wait',
         'Deleting the last pool rejects all existing cluster private endpoint connections before completion. '
         'Connection resources remain for inspection/cleanup; customer-owned private endpoints are not deleted.')
for _action, _summary, _details in [
    ('start', 'Start a stopped pool without applying pending parameter changes.',
     'Start on a Ready pool is an idempotent no-op. Conflicting operations or incompatible states can return 409.'),
    ('stop', 'Stop and deallocate pool compute.',
     'Stop on a Stopped pool is an idempotent no-op. Pending parameter changes remain pending.'),
    ('restart', 'Restart PostgreSQL and apply pending parameter changes.',
     'Only customer-initiated restart applies static or PendingReboot changes. With no allocated nodes, '
     'restart marks pending changes InSync for future allocations.'),
]:
    _command('pool ' + _action, _summary,
             _CLUSTER + ' --name primary' + (' --no-wait' if _action == 'stop' else ''), _details)

_command('node show', 'Show the role and availability zone of a service-managed node.', _POOL + ' --name node-001')
_command('node list', 'List nodes allocated to a pool.', _POOL + ' --output table')
_command('endpoint show', 'Show endpoint scope, routing role, associated resource ID, and FQDN.',
         _CLUSTER + ' --name example-cluster')
_command('endpoint list', 'List cluster and pool endpoints.', _CLUSTER + ' --output table')
_help('firewall-rule create', 'Create or completely replace a pool IPv4 firewall rule.', [
    ('Allow a single documentation-range client address.',
     'az horizondb firewall-rule create ' + _POOL +
     ' --name office-client --start-ip-address 203.0.113.10 --end-ip-address 203.0.113.10'),
    ('Replace a rule with the complete range and description.',
     'az horizondb firewall-rule create ' + _POOL +
     ' --name office-client --start-ip-address 203.0.113.10 --end-ip-address 203.0.113.10 '
     '--description "Office egress"'),
], 'There is no firewall-rule update command; create replaces the desired representation.', {
    '--name -n': 'Required rule name, 2-128 alphanumeric or hyphen characters, starting and ending alphanumeric.',
    '--start-ip-address': 'Required first IPv4 address of the allowed range.',
    '--end-ip-address': 'Required last IPv4 address of the allowed range.',
    '--description': 'Optional description, at most 1024 characters.',
})
_command('firewall-rule show', 'Show a pool IPv4 firewall rule.', _POOL + ' --name office-client')
_command('firewall-rule list', 'List IPv4 firewall rules in a pool.', _POOL + ' --output table')
_command('firewall-rule delete', 'Delete a pool IPv4 firewall rule.', _POOL + ' --name office-client --yes')

_command('authentication list', 'List the two service-created authentication configurations.', _CLUSTER + ' --output table')
_command('authentication password show', 'Show password authentication state without returning credentials.', _CLUSTER)
_command('authentication password create', 'Create or replace the fixed password authentication configuration.',
         _CLUSTER + ' --state Enabled --administrator-login "$HORIZONDB_ADMIN_LOGIN" '
         '--administrator-login-password "$HORIZONDB_ADMIN_PASSWORD"',
         'For initial setup supply both credentials and state Enabled. Credentials are write-only and '
         'must not be embedded in literal examples or logged. Do not use --debug when supplying credentials: '
         'CLI debug logs can include expanded command arguments. Disabling cannot include credentials.', {
             '--state': 'Required state: Enabled or Disabled. Credentials require Enabled in the same PUT.',
             '--administrator-login': 'Secret, create-only administrator login, 1-63 characters.',
             '--administrator-login-password -p': 'Secret administrator password, 8-128 characters.',
         })
_help('authentication password update', 'Enable or disable password authentication, or reset its password.', [
    ('Reset the existing administrator password using a securely populated variable.',
     'az horizondb authentication password update ' + _CLUSTER +
     ' --administrator-login-password "$HORIZONDB_NEW_ADMIN_PASSWORD"'),
    ('Disable password authentication without changing database users.',
     'az horizondb authentication password update ' + _CLUSTER + ' --state Disabled'),
    ('Re-enable authentication and reset the administrator password without waiting.',
     'az horizondb authentication password update ' + _CLUSTER +
     ' --state Enabled --administrator-login-password "$HORIZONDB_NEW_ADMIN_PASSWORD" --no-wait'),
], 'PATCH is sparse. Administrator login cannot be changed; omit it on update. '
   'Disable cannot be combined with credentials. Do not use --debug when supplying credentials: '
   'CLI debug logs can include expanded command arguments.', {
       '--state': 'Enabled or Disabled. Omission preserves state.',
       '--administrator-login-password -p': 'Write-only secret, 8-128 characters. Omission preserves password.',
})
_command('authentication microsoft-entra show', 'Show Microsoft Entra authentication state.', _CLUSTER)
for _action in ('create', 'update'):
    _command('authentication microsoft-entra ' + _action,
             ('Create or replace' if _action == 'create' else 'Sparsely update') +
             ' Microsoft Entra authentication.', _CLUSTER + ' --state Enabled',
             'Disabling does not remove database users or roles.', {
                 '--state': ('Required on create. ' if _action == 'create' else 'Optional; omission preserves state. ') +
                 'Enabled or Disabled.',
             })
_ADMIN = _CLUSTER + ' --object-id ' + _GUID
_command('microsoft-entra-administrator create', 'Create or replace a Microsoft Entra administrator mapping.',
         _ADMIN + ' --principal-name admin@contoso.com --principal-type User', parameters={
             '--object-id': 'Required Microsoft Entra principal object ID (RFC 4122 GUID).',
             '--principal-name': 'Required principal name, at most 256 characters.',
             '--principal-type': 'Required principal type: User, Group, or ServicePrincipal.',
             '--tenant-id': 'Optional tenant GUID; defaults to the subscription tenant.',
         })
_command('microsoft-entra-administrator show', 'Show a Microsoft Entra administrator mapping.', _ADMIN)
_command('microsoft-entra-administrator list', 'List Microsoft Entra administrator mappings.', _CLUSTER + ' --output table')
_command('microsoft-entra-administrator delete', 'Delete a Microsoft Entra administrator mapping.', _ADMIN + ' --yes')

_LOG = _CLUSTER + ' --name PostgreSqlServer'
_command('log-capture-configuration show', 'Show log capture state, retention, and provisioning state.', _LOG)
_command('log-capture-configuration list', 'List predefined cluster log capture configurations.', _CLUSTER)
_command('log-capture-configuration update', 'Sparsely update PostgreSQL server-log capture or retention.',
         _LOG + ' --state Enabled --retention-days 21 --no-wait', parameters={
             '--name -n': 'Log type. This API supports only PostgreSqlServer.',
             '--state': 'Enabled or Disabled. Omission preserves capture state.',
             '--retention-days': 'Captured-log retention, 1-35 days. Omission preserves current retention.',
         })
_command('log-file show', 'Show captured log file metadata without download credentials.',
         _CLUSTER + ' --name "$LOG_FILE_NAME"')
_command('log-file list', 'List captured log file metadata for a cluster.',
         _CLUSTER + ' --query "[].{name:name,properties:properties}" --output json')
_command('log-file list-download-urls', 'Explicitly request a short-lived signed URL for one captured log file.',
         _CLUSTER + ' --name "$LOG_FILE_NAME"',
         'Returns downloadUri and expiresAtUtc synchronously. Treat downloadUri as a bearer credential: '
         'do not log, share, or expose it in telemetry. URL expiration is independent of the file retention '
         'deadline retentionExpiresAtUtc. Obtain LOG_FILE_NAME from log-file list --query "[0].name" --output tsv.')
for _action, _summary, _arguments in [
    ('show', 'Show the cluster change data capture capability named default.', _CLUSTER),
    ('list', 'List the cluster change data capture capability.', _CLUSTER),
    ('create', 'Create or replace the cluster change data capture capability.', _CLUSTER + ' --desired-state Enabled'),
    ('update', 'Sparsely update change data capture state or extension auto-upgrade.',
     _CLUSTER + ' --auto-upgrade-extension true'),
    ('delete', 'Delete the cluster change data capture capability.', _CLUSTER + ' --yes'),
]:
    _command('change-data-capture ' + _action, _summary, _arguments,
             'Delete requires all destinations and configurations to be removed.' if _action == 'delete' else
             'The CLI hides the fixed capability name default.',
             {'--desired-state': 'Enabled or Disabled. Required on create; optional on update.',
              '--auto-upgrade-extension': 'true or false. Omission preserves the existing value on update.'}
             if _action in ('create', 'update') else None)

_DESTINATION = _CLUSTER + ' --name fabric-main'
_command('change-data-capture-destination create', 'Create or replace a Microsoft Fabric Mirroring destination.',
         _DESTINATION + ' --workspace-id 22222222-2222-2222-2222-222222222222 --desired-state Enabled',
         'The CLI supplies Fabric destination type, Native protocol, and Mirroring usage.', {
             '--workspace-id': 'Required Microsoft Fabric workspace GUID.',
             '--desired-state': 'Required desired state: Enabled or Disabled.',
         })
_command('change-data-capture-destination show', 'Show a Microsoft Fabric destination and ETag.', _DESTINATION)
_command('change-data-capture-destination list', 'List Microsoft Fabric destinations in a cluster.', _CLUSTER)
_command('change-data-capture-destination update', 'Update a Microsoft Fabric destination desired state.',
         _DESTINATION + ' --desired-state Disabled --no-wait',
         parameters={'--desired-state': 'Enabled or Disabled. Omission preserves current desired state.'})
_command('change-data-capture-destination activate-identity', 'Activate a validated destination identity assignment.',
         _DESTINATION + ' --identity-assignment "$ASSIGNMENT_ID" --if-match "$ETAG"',
         'Obtain ASSIGNMENT_ID from change-data-capture-identity-assignment show --query id --output tsv '
         'and ETAG from change-data-capture-destination show --query eTag --output tsv. '
         'The assignment must belong to this destination and be successfully validated. '
         'Reactivating the active assignment is idempotent.', {
             '--identity-assignment': 'Required full ARM ID of an assignment under this destination.',
             '--if-match': 'Required destination ETag for optimistic concurrency.',
             '--drain-existing-sessions': 'true or false; defaults to false.',
         })
_command('change-data-capture-destination delete', 'Delete an unreferenced Microsoft Fabric destination.',
         _DESTINATION + ' --yes', 'Remove all referencing configurations and identity assignments first.')

_ASSIGNMENT = _CLUSTER + ' --destination-name fabric-main --name primary'
_command('change-data-capture-identity-assignment create', 'Create or replace a destination identity candidate.',
         _ASSIGNMENT + ' --identity ' + _IDENTITY,
         'Attach the identity to the cluster first and configure destination RBAC separately.',
         {'--identity': 'Required full user-assigned identity ARM ID, exactly matching an identity attached to the cluster.'})
_command('change-data-capture-identity-assignment show', 'Show a destination identity assignment.', _ASSIGNMENT)
_command('change-data-capture-identity-assignment list', 'List identity candidates under a destination.',
         _CLUSTER + ' --destination-name fabric-main')
_command('change-data-capture-identity-assignment delete', 'Delete an inactive destination identity assignment.',
         _CLUSTER + ' --destination-name fabric-main --name retired --yes',
         'The active primary assignment cannot be deleted; this independent example assumes retired is inactive.')

_CONFIGURATION = _CLUSTER + ' --name sales'
_command('change-data-capture-configuration create', 'Enroll a database in Microsoft Fabric Mirroring.',
         _CONFIGURATION + ' --database-name sales --destination "$DESTINATION_ID" --desired-state Enabled',
         'Obtain DESTINATION_ID from change-data-capture-destination show --query id --output tsv.', {
             '--database-name': 'Required PostgreSQL database name.',
             '--destination': 'Required full ARM ID of the Microsoft Fabric destination.',
             '--desired-state': 'Required desired state: Enabled or Disabled.',
         })
_command('change-data-capture-configuration show', 'Show a database Mirroring configuration.', _CONFIGURATION)
_command('change-data-capture-configuration list', 'List database Mirroring configurations in a cluster.', _CLUSTER)
_command('change-data-capture-configuration update', 'Update a database Mirroring configuration desired state.',
         _CONFIGURATION + ' --desired-state Disabled',
         parameters={'--desired-state': 'Enabled or Disabled. Omission preserves desired state.'})
_command('change-data-capture-configuration delete', 'Delete database enrollment without deleting the Fabric item.',
         _CONFIGURATION + ' --yes', 'First stop or detach destination-side replication.')

_CONNECTION = _CLUSTER + ' --name example-pec'
_command('private-endpoint-connection show', 'Show a private endpoint connection and approval state.', _CONNECTION)
_command('private-endpoint-connection list', 'List cluster private endpoint connections.', _CLUSTER,
         'Deleting the last pool rejects these connections but leaves their resources for inspection and cleanup.')
_command('private-endpoint-connection approve', 'Approve a private endpoint connection using replacement PUT.',
         _CONNECTION + ' --description "Approved"',
         'Requires at least one cluster pool; without it the service returns 409 PrivateEndpointRequiresPool.',
         {'--description': 'Optional explanation for the approval.'})
_command('private-endpoint-connection reject', 'Reject a private endpoint connection using replacement PUT.',
         _CLUSTER + ' --name unknown-pec --description "Unrecognized"',
         'Rejection is allowed even when the cluster has no pools.',
         {'--description': 'Optional explanation for the rejection.'})
_command('private-endpoint-connection delete', 'Delete a cluster private endpoint connection.', _CONNECTION + ' --yes')
_command('private-link-resource show', 'Show endpoint-specific private link group metadata.',
         _CLUSTER + ' --name example-cluster')
_command('private-link-resource list', 'List private link groups and required DNS zones.', _CLUSTER)

_PARAMETER_GROUP = '--resource-group example-rg --name pg-transactional'
_command('parameter-group create', 'Create or replace a custom PostgreSQL parameter group.',
         _PARAMETER_GROUP + ' --location eastus2 --postgresql-version 17 '
         '--parameters shared_buffers=2048MB work_mem=64MB', parameters={
             '--name -n': 'Required group name, 2-63 alphanumeric or hyphen characters, starting and ending alphanumeric.',
             '--postgresql-version': 'Required immutable PostgreSQL major version: 17 or 18.',
             '--parameters': 'Space-separated PostgreSQL name=value overrides, for example work_mem=64MB.',
             '--description': 'Optional description, at most 1024 characters.',
         })
_command('parameter-group show', 'Show effective parameter values, defaults, and value sources.', _PARAMETER_GROUP)
_help('parameter-group list', 'List custom parameter groups in a resource group or subscription.', [
    ('List resource-group custom parameter groups.',
     'az horizondb parameter-group list --resource-group example-rg'),
    ('List subscription custom parameter groups.',
     'az horizondb parameter-group list --subscription "$SUBSCRIPTION_ID"'),
])
_command('parameter-group update', 'Sparsely update custom parameter overrides, description, or tags.',
         _PARAMETER_GROUP + ' --parameters work_mem=96MB',
         'Apply timing belongs to connected pools, not this group. Static changes always require '
         'customer-initiated pool restart; stop/start and maintenance do not apply pending changes.', {
             '--parameters': 'Space-separated PostgreSQL name=value overrides. Read-only parameters cannot be overridden.',
             '--description': 'Optional description, at most 1024 characters.',
         })
_command('parameter-group list-connections', 'List pools associated with a custom parameter group.', _PARAMETER_GROUP)
_command('parameter-group delete', 'Delete a custom parameter group with no associated pools.',
         _PARAMETER_GROUP + ' --yes',
         'First return connected pools to a default or another custom parameter group using pool update.')
_command('default-parameter-group list', 'List default PostgreSQL parameter groups in a location.',
         _LOCATION, parameters={'--location -l': 'Required Azure location.'})
_command('default-parameter-group show', 'Show a location-scoped default parameter group.',
         '--ids "$DEFAULT_PARAMETER_GROUP_ID"',
         'Use default-parameter-group list to obtain the full ARM ID for the desired location and PostgreSQL version.')

_WAITS = {
    'cluster': (_RESOURCE, '--created'),
    'pool': (_CLUSTER + ' --name primary', '--custom "properties.state==\'Ready\'"'),
    'firewall-rule': (_POOL + ' --name office-client', '--created'),
    'authentication password': (_CLUSTER, '--updated'),
    'authentication microsoft-entra': (_CLUSTER, '--updated'),
    'microsoft-entra-administrator': (_ADMIN, '--created'),
    'log-capture-configuration': (_LOG, '--updated'),
    'change-data-capture': (_CLUSTER, '--updated'),
    'change-data-capture-destination': (_DESTINATION, '--updated'),
    'change-data-capture-identity-assignment': (_ASSIGNMENT, '--created'),
    'change-data-capture-configuration': (_CONFIGURATION, '--updated'),
    'private-endpoint-connection': (
        _CONNECTION, '--custom "properties.privateLinkServiceConnectionState.status==\'Approved\'"'),
    'parameter-group': (_PARAMETER_GROUP, '--updated'),
}
for _group, (_selectors, _condition) in _WAITS.items():
    _examples = [('Wait for the requested resource condition.',
                  'az horizondb ' + _group + ' wait ' + _selectors + ' ' + _condition)]
    if _group == 'pool':
        _examples.append(('Wait for pool deletion after asynchronous delete.',
                          'az horizondb pool wait ' + _selectors + ' --deleted'))
    _help(_group + ' wait', 'Wait for a ' + _group.replace('authentication ', '') + ' condition.',
          _examples,
          'Poll GET until the chosen condition succeeds. --created and --updated wait for provisioningState '
          'Succeeded; --deleted waits until GET no longer finds the resource; --exists waits for GET success. '
          'Use --custom for a JMESPath condition. Poll interval defaults to 30 seconds and timeout to 3600 seconds.')
