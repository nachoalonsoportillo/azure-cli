# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Memory-only SDK record/replay regression; no sockets, persisted cassettes, or Azure resources."""

import copy
import hashlib
import json
import unittest

import vcr
from vcr.cassette import Cassette
from vcr.request import Request
from azure.mgmt.horizondb import HorizonDBMgmtClient
from azure.cli.core.azclierror import InvalidArgumentValueError
from azure.cli.command_modules.horizondb import _create
from azure.cli.testsdk.scenario_tests.base import ReplayableTest
from azure.cli.testsdk.scenario_tests.const import MOCKED_SUBSCRIPTION_ID, MOCKED_TENANT_ID
from azure.cli.testsdk.scenario_tests.preparers import SingleValueReplacer
from azure.cli.testsdk.scenario_tests.recording_processors import (
    ContentLengthProcessor, GeneralNameReplacer, RequestUrlNormalizer, SubscriptionRecordingProcessor)
from azure.cli.testsdk.utilities import EmailAddressReplacer

from . import test_horizondb_create as workflow_tests
from .create_recording import CreateRecordingNormalizer, create_recording_context
from .test_horizondb_sdk import _Credential, _Response, _Transport, SUBSCRIPTION, RESOURCE_GROUP, CLUSTER


_RECORDED_SUBSCRIPTION = '33333333-3333-3333-3333-333333333333'
_REPLAY_CLUSTER = 'hdbquick000002'
_REPLAY_GROUP = 'clitest-hdb-000001'
_INTENT = 'horizondb-cli-create-intent'


class _GroupReplacer(SingleValueReplacer):
    """Exercise the actual preparer's replacement implementation without creating a resource group."""

    @property
    def random_name(self):
        return RESOURCE_GROUP

    @property
    def moniker(self):
        return _REPLAY_GROUP


def _owner():
    owner = object.__new__(ReplayableTest)
    owner.disable_recording = False
    owner.in_recording = True
    names = GeneralNameReplacer()
    names.register_name_pair(CLUSTER, _REPLAY_CLUSTER)
    owner.recording_processors = [
        SubscriptionRecordingProcessor(MOCKED_SUBSCRIPTION_ID), EmailAddressReplacer(),
        RequestUrlNormalizer(), names, _GroupReplacer(),
    ]
    owner.recording_post_processors = [ContentLengthProcessor()]
    owner.replay_processors = [RequestUrlNormalizer()]
    return owner


class _CaptureTransport(_Transport):

    def __init__(self, cassette, backend):
        super().__init__()
        self.cassette = cassette
        self.backend = backend

    def send(self, request, **kwargs):
        captured = Request(request.method, request.url, request.body, dict(request.headers))
        # Match the real VCR urllib3 stub: can_play_response_for -> __contains__ -> _responses
        # repeatedly filters the same mutable request before a live response is appended.
        if self.cassette.can_play_response_for(captured):
            raise AssertionError('A record-all cassette must not supply a playback response.')
        # The existing offline backend uses the zero subscription. Expose its responses through a
        # nonzero synthetic subscription so the real subscription sanitizer is exercised too.
        backend_request = copy.copy(request)
        backend_request.url = request.url.replace(_RECORDED_SUBSCRIPTION, SUBSCRIPTION)
        response = self.backend.send(backend_request, **kwargs)
        payload = response.body().decode().replace(
            '/subscriptions/' + SUBSCRIPTION, '/subscriptions/' + _RECORDED_SUBSCRIPTION)
        response = _Response(request, json.loads(payload), response.status_code)
        self.cassette.append(captured, {
            'status': {'code': response.status_code, 'message': response.reason},
            'headers': {'content-type': ['application/json']},
            'body': {'string': response.body()},
        })
        return response


class _ReplayTransport(_Transport):

    def __init__(self, cassette):
        super().__init__()
        self.cassette = cassette

    def send(self, request, **_kwargs):
        self.requests.append(request)
        response = self.cassette.play_response(
            Request(request.method, request.url, request.body, dict(request.headers)))
        return _Response(request, json.loads(response['body']['string']), response['status']['code'])


class HorizonDBCreateRecordingTest(unittest.TestCase):

    def setUp(self):
        self.fixture = workflow_tests.HorizonDBCreateTest(
            'test_horizondb_create_successful_rerun_is_read_only_and_uses_identical_metadata')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.owner = _owner()
        self.owner.cli_ctx = self.fixture.cmd.cli_ctx
        self.digest = _create._plan_digest  # pylint: disable=protected-access
        self.plan = {
            'pool': CLUSTER + '-pool',
            'default': '/subscriptions/' + _RECORDED_SUBSCRIPTION + '/providers/test/default',
            'administrator': {'tenantId': workflow_tests.TENANT_ID, 'principalName': 'person@microsoft.com'},
            'cores': 2,
        }

    def _record(self):
        self.fixture.graph_factory.return_value.signed_in_user_get.return_value = {
            'id': workflow_tests.OBJECT_ID, 'userPrincipalName': 'person@microsoft.com'}
        recorder = vcr.VCR()
        # Keep all standard matchers and ADD body matching; no relaxed request matching.
        matchers = tuple(recorder.matchers[name] for name in recorder.match_on + ('body',))
        cassette = Cassette(
            ':in-memory-never-saved:', record_mode='all', match_on=matchers,
            before_record_request=self.owner._process_request_recording,  # pylint: disable=protected-access
            before_record_response=self.owner._process_response_recording)  # pylint: disable=protected-access
        transport = _CaptureTransport(cassette, self.fixture.transport)
        with create_recording_context(self.owner) as normalizer:
            with HorizonDBMgmtClient(
                    _Credential(), _RECORDED_SUBSCRIPTION, transport=transport,
                    retry_total=0, polling_interval=0) as client:
                first = _create.horizondb_create(
                    self.fixture.cmd, client, RESOURCE_GROUP, CLUSTER, public_access=['203.0.113.10'])
                second = _create.horizondb_create(
                    self.fixture.cmd, client, RESOURCE_GROUP, CLUSTER, public_access=['203.0.113.10'])
        self.assertEqual(first, second)
        actual_digest = self.fixture.transport.resources[workflow_tests.CLUSTER_PATH]['tags'][_INTENT]
        self.assertIn(actual_digest, normalizer.intent_digests)
        self.assertNotEqual(actual_digest, normalizer.intent_digests[actual_digest])
        recorded_put = next(request for request in cassette.requests
                            if request.method == 'PUT' and
                            request.uri.split('?')[0].endswith('/clusters/' + _REPLAY_CLUSTER))
        self.assertEqual(normalizer.intent_digests[actual_digest], json.loads(recorded_put.body)['tags'][_INTENT])
        self.owner.in_recording = False
        self.fixture.profile.return_value['tenantId'] = MOCKED_TENANT_ID
        # This is the actual email processor's result for the existing offline Graph fixture.
        user = {'headers': {'content-type': ['application/json']},
                'body': {'string': json.dumps({'id': workflow_tests.OBJECT_ID,
                                               'userPrincipalName': 'person@microsoft.com'})}}
        EmailAddressReplacer().process_response(user)
        self.fixture.graph_factory.return_value.signed_in_user_get.return_value = json.loads(user['body']['string'])
        return cassette

    def _replay(self, client, **kwargs):
        kwargs.setdefault('public_access', ['203.0.113.10'])
        return _create.horizondb_create(self.fixture.cmd, client, _REPLAY_GROUP, _REPLAY_CLUSTER, **kwargs)

    def test_horizondb_create_recording_replays_both_root_invocations_with_strict_body_matching(self):
        cassette = self._record()
        backend_calls = len(self.fixture.transport.requests)
        replay = _ReplayTransport(cassette)
        with create_recording_context(self.owner):
            self.assertIs(self.digest, _create._plan_digest)  # pylint: disable=protected-access
            with HorizonDBMgmtClient(
                    _Credential(), MOCKED_SUBSCRIPTION_ID, transport=replay,
                    retry_total=0, polling_interval=0) as client:
                first = self._replay(client)
                start = len(replay.requests)
                second = self._replay(client)
        self.assertEqual(first, second)
        self.assertEqual(_REPLAY_CLUSTER, first['cluster']['name'])
        self.assertIn('/subscriptions/' + MOCKED_SUBSCRIPTION_ID, first['pool']['id'])
        self.assertTrue(all(request.method == 'GET' for request in replay.requests[start:]))
        self.assertTrue(cassette.all_played)
        self.assertEqual(backend_calls, len(self.fixture.transport.requests))

    def test_horizondb_create_recording_keeps_negative_intent_conflicts_before_writes(self):
        cassette = self._record()
        for conflict in ({'v_cores': 8}, {'public_access': ['203.0.113.11']},
                         {'enable_ai': True}, {'authentication': 'Both'}):
            with self.subTest(conflict=conflict):
                cassette.rewind()
                replay = _ReplayTransport(cassette)
                with HorizonDBMgmtClient(
                        _Credential(), MOCKED_SUBSCRIPTION_ID, transport=replay,
                        retry_total=0, polling_interval=0) as client:
                    self._replay(client)
                    start = len(replay.requests)
                    with self.assertRaisesRegex(InvalidArgumentValueError, 'intent differs'):
                        self._replay(client, **conflict)
                self.assertTrue(all(request.method == 'GET' for request in replay.requests[start:]))

    def test_horizondb_create_recording_observes_but_never_changes_live_digest_or_processor_scope(self):
        original = copy.deepcopy(self.plan)
        post_processors = self.owner.recording_post_processors
        with create_recording_context(self.owner) as normalizer:
            actual = _create._plan_digest(self.plan)  # pylint: disable=protected-access
            self.assertEqual(self.digest(self.plan), actual)
            self.assertNotEqual(actual, normalizer.intent_digests[actual])
            self.assertIs(normalizer, self.owner.recording_post_processors[0])
        self.assertIs(post_processors, self.owner.recording_post_processors)
        self.assertIs(self.digest, _create._plan_digest)  # pylint: disable=protected-access
        self.assertEqual(original, self.plan)

    def test_horizondb_create_recording_normalizes_profile_tenant_even_before_plan_capture(self):
        with create_recording_context(self.owner) as normalizer:
            self.assertEqual({}, normalizer.intent_digests)
            response = {'body': {'string': json.dumps({'tenantId': workflow_tests.TENANT_ID})}}
            result = normalizer.process_response(response)
            self.assertEqual(MOCKED_TENANT_ID, json.loads(result['body']['string'])['tenantId'])

    def test_horizondb_create_recording_rewrites_only_corresponding_intent_tags_and_bare_tenant_fields(self):
        normalizer = CreateRecordingNormalizer(self.owner, self.digest)
        actual = self.digest(self.plan)
        normalizer.capture_plan(self.plan, actual)
        payload = {'tags': {_INTENT: actual, 'unrelated-tag': actual}, 'unrelated-value': actual,
                   'tenantId': workflow_tests.TENANT_ID, 'objectId': workflow_tests.TENANT_ID}
        response = {'body': {'string': json.dumps(payload)}}
        normalized = json.loads(normalizer.process_response(response)['body']['string'])
        self.assertEqual(normalizer.intent_digests[actual], normalized['tags'][_INTENT])
        self.assertEqual(actual, normalized['tags']['unrelated-tag'])
        self.assertEqual(actual, normalized['unrelated-value'])
        self.assertEqual(MOCKED_TENANT_ID, normalized['tenantId'])
        self.assertEqual(workflow_tests.TENANT_ID, normalized['objectId'])
        with self.assertRaisesRegex(AssertionError, 'no captured production plan'):
            normalizer.process_response({'body': {'string': json.dumps({'tags': {_INTENT: 'unobserved'}})}})

    def test_horizondb_create_recording_digest_remains_exact_sha256_of_the_complete_sorted_plan(self):
        expected = hashlib.sha256(json.dumps(self.plan, sort_keys=True).encode('utf-8')).hexdigest()
        self.assertEqual(expected, self.digest(self.plan))
        self.assertEqual(expected, self.digest(dict(reversed(list(self.plan.items())))))
        for key, value in (('cores', 8), ('pool', 'another-pool'), ('default', 'another-group')):
            with self.subTest(field=key):
                self.assertNotEqual(expected, self.digest(dict(self.plan, **{key: value})))

    def test_horizondb_create_recording_request_and_response_normalization_is_idempotent(self):
        with create_recording_context(self.owner) as normalizer:
            actual = _create._plan_digest(self.plan)  # pylint: disable=protected-access
            expected = normalizer.intent_digests[actual]
            body = json.dumps({'tags': {_INTENT: actual}, 'tenantId': workflow_tests.TENANT_ID,
                               'pool': CLUSTER + '-pool', 'principalName': 'person@microsoft.com'})
            request = Request(
                'PUT', 'https://management.azure.com/subscriptions/' + _RECORDED_SUBSCRIPTION +
                '/resourceGroups/' + RESOURCE_GROUP + '/providers/test/clusters/' + CLUSTER,
                body, {'Content-Type': 'application/json'})
            first = self.owner._process_request_recording(request)  # pylint: disable=protected-access
            first_body, first_uri = first.body, first.uri
            second = self.owner._process_request_recording(first)  # pylint: disable=protected-access
            self.assertIs(first, second)
            self.assertEqual(first_body, second.body)
            self.assertEqual(first_uri, second.uri)
            self.assertEqual(expected, json.loads(second.body)['tags'][_INTENT])
            self.assertEqual(MOCKED_TENANT_ID, json.loads(second.body)['tenantId'])
            response = {'headers': {'content-type': ['application/json']}, 'body': {'string': body}}
            first_response = self.owner._process_response_recording(response)  # pylint: disable=protected-access
            first_copy = copy.deepcopy(first_response)
            second_response = self.owner._process_response_recording(  # pylint: disable=protected-access
                first_response)
            self.assertEqual(first_copy, second_response)
            self.assertEqual(expected, json.loads(second_response['body']['string'])['tags'][_INTENT])
            # An arbitrary digest must still fail, even after a known digest was normalized twice.
            unknown = 'f' * 64
            self.assertNotIn(unknown, normalizer.intent_digests)
            self.assertNotIn(unknown, normalizer.intent_digests.values())
            with self.assertRaisesRegex(AssertionError, 'no captured production plan'):
                normalizer.process_request(Request(
                    'PUT', first_uri, json.dumps({'tags': {_INTENT: unknown}}),
                    {'Content-Type': 'application/json'}))
            with self.assertRaisesRegex(AssertionError, 'no captured production plan'):
                normalizer.process_response({'body': {'string': json.dumps({'tags': {_INTENT: unknown}})}})
