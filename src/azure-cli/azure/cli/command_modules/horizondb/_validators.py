# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import ipaddress
import re
import uuid
from datetime import datetime
from urllib.parse import urlparse

from azure.cli.core.azclierror import InvalidArgumentValueError, RequiredArgumentMissingError
from azure.cli.core.util import get_file_json, shell_safe_json_parse
from azure.mgmt.core.tools import is_valid_resource_id, parse_resource_id


def dns_name(value):
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value):
        raise InvalidArgumentValueError('Names must be lowercase DNS labels of 1-63 characters.')
    return value


def bounded_int(minimum, maximum=None):
    def parse(value):
        try:
            number = int(value)
        except (TypeError, ValueError) as ex:
            raise InvalidArgumentValueError('Expected an integer.') from ex
        if number < minimum or (maximum is not None and number > maximum):
            upper = str(maximum) if maximum is not None else 'unbounded'
            raise InvalidArgumentValueError(f'Expected an integer in the range {minimum}-{upper}.')
        return number
    return parse


def guid(value):
    try:
        if str(uuid.UUID(value)).lower() != value.lower():
            raise ValueError()
    except (ValueError, AttributeError) as ex:
        raise InvalidArgumentValueError('Expected a GUID in xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx format.') from ex
    return value


def utc_datetime(value):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)', value):
            raise ValueError()
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, TypeError) as ex:
        raise InvalidArgumentValueError('Expected an RFC 3339 UTC date-time, for example 2026-10-01T12:00:00Z.') from ex
    return value


def resource_reference(value, resource_type=None):
    if not is_valid_resource_id(value):
        raise InvalidArgumentValueError('Expected a complete ARM resource ID.')
    parsed = parse_resource_id(value)
    if not all(parsed.get(key) for key in ('subscription', 'namespace', 'type', 'name')):
        raise InvalidArgumentValueError('Expected a complete ARM resource ID.')
    if resource_type:
        actual = parsed.get('namespace', '') + '/' + parsed.get('type', '')
        for key in ('child_type_1', 'child_type_2', 'child_type_3'):
            if parsed.get(key):
                actual += '/' + parsed[key]
        if actual.lower() != resource_type.lower():
            raise InvalidArgumentValueError(f'Expected a {resource_type} resource ID.')
    return value


def identity_reference(value):
    return resource_reference(value, 'Microsoft.ManagedIdentity/userAssignedIdentities')


def key_uri(value):
    parsed = urlparse(value)
    has_credentials = parsed.username is not None or parsed.password is not None
    invalid_path = not re.fullmatch(r'/keys/[^/]+/?', parsed.path)
    invalid_origin = parsed.scheme != 'https' or not parsed.hostname or has_credentials
    if invalid_origin or parsed.query or parsed.fragment or invalid_path:
        raise InvalidArgumentValueError('Use a versionless HTTPS Azure Key Vault key URI: https://<vault>/keys/<key>.')
    return value


def firewall_name(value):
    if not 1 <= len(value) <= 128 or not re.fullmatch(r'[a-zA-Z0-9][-a-zA-Z0-9]*[a-zA-Z0-9]', value):
        raise InvalidArgumentValueError('Firewall rule names must be 2-128 alphanumeric or hyphen characters, '
                                        'starting and ending with an alphanumeric character.')
    return value


def parameter_group_name(value):
    if not 1 <= len(value) <= 63 or not re.fullmatch(r'[a-zA-Z0-9][-a-zA-Z0-9]*[a-zA-Z0-9]', value):
        raise InvalidArgumentValueError('Parameter group names must be 2-63 alphanumeric or hyphen characters, '
                                        'starting and ending with an alphanumeric character.')
    return value


def description(value):
    if len(value) > 1024:
        raise InvalidArgumentValueError('--description must contain at most 1024 characters.')
    return value


def validate_ipv4_range(start, end):
    try:
        first, last = ipaddress.IPv4Address(start), ipaddress.IPv4Address(end)
    except ipaddress.AddressValueError as ex:
        raise InvalidArgumentValueError('Firewall addresses must be IPv4 addresses.') from ex
    if first > last:
        raise InvalidArgumentValueError('--start-ip-address must not be greater than --end-ip-address.')


def validate_topology(node_count, mode):
    if node_count is not None and mode is not None:
        if (node_count == 1) != (mode == 'Disabled'):
            raise InvalidArgumentValueError(
                'One Node requires Disabled high availability; 2-16 Nodes require AnyZone or ZoneRedundant.')


def require_change(**values):
    if all(value is None for value in values.values()):
        raise RequiredArgumentMissingError('Supply at least one property to update.')


def parameter_values(value):
    """Accept only the writable name/value shape, never response-only metadata."""
    if isinstance(value, str):
        if value.startswith('@'):
            value = get_file_json(value[1:])
        elif value.lstrip().startswith('['):
            value = shell_safe_json_parse(value)
        else:
            value = [value]
    if not isinstance(value, list):
        raise InvalidArgumentValueError(
            '--parameters requires name=value entries or a JSON array of name/value objects.')
    if len(value) == 1 and isinstance(value[0], str) and value[0].lstrip().startswith(('[', '@')):
        return parameter_values(value[0])
    if value and all(isinstance(entry, str) for entry in value):
        if any('=' not in entry for entry in value):
            raise InvalidArgumentValueError('--parameters entries must use name=value.')
        value = [dict(zip(('name', 'value'), entry.split('=', 1))) for entry in value]
    names = set()
    for entry in value:
        if (not isinstance(entry, dict) or set(entry) != {'name', 'value'} or
                not isinstance(entry['name'], str) or not entry['name'] or
                not isinstance(entry['value'], str)):
            raise InvalidArgumentValueError('--parameters entries must contain only string name and value fields.')
        if entry['name'] in names:
            raise InvalidArgumentValueError('Duplicate parameter names are not allowed.')
        names.add(entry['name'])
    return value


def validate_tags(namespace):
    values = namespace.tags
    json_input = False
    if isinstance(values, list) and len(values) == 1:
        text = values[0]
        if text.startswith('@'):
            values = get_file_json(text[1:])
            json_input = True
        elif text.lstrip().startswith('{'):
            values = shell_safe_json_parse(text)
            json_input = True
    if json_input and not isinstance(values, dict):
        raise InvalidArgumentValueError('Tag JSON must be an object with string keys and string values.')
    if isinstance(values, dict):
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
            raise InvalidArgumentValueError('Tag JSON must be an object with string keys and string values.')
        namespace.tags = values
    else:
        from azure.cli.core.commands.validators import validate_tags as validate_standard_tags
        validate_standard_tags(namespace)
