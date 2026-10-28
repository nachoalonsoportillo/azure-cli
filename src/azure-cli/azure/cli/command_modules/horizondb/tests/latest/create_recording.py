# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

"""Root-create-only normalization of values derived from sanitized recording inputs."""

from contextlib import contextmanager
from functools import wraps
import json
from unittest import mock

from azure.cli.testsdk.scenario_tests import RecordingProcessor
from azure.cli.testsdk.scenario_tests.const import MOCKED_TENANT_ID


_INTENT_TAG = 'horizondb-cli-create-intent'


class CreateRecordingNormalizer(RecordingProcessor):
    """Translate actual plan digests, never bypass or replace the production digest calculation."""

    def __init__(self, test, digest, tenant_id=None):
        self.test = test
        self.digest = digest
        self.intent_digests = {}
        self.tenant_ids = {tenant_id.lower()} if tenant_id else set()

    def capture_plan(self, plan, actual_digest):
        if self.digest(plan) != actual_digest:
            raise AssertionError('The captured plan does not produce its actual production digest.')
        administrator = plan.get('administrator') or {}
        if administrator.get('tenantId'):
            self.tenant_ids.add(administrator['tenantId'].lower())
        # Use the actual, currently registered recording processors (including resource preparers).
        # This is a normalization document, not an HTTP interaction or an invented service response.
        document = {'headers': {'content-type': ['application/json']},
                    'body': {'string': json.dumps(plan)}}
        for processor in self.test.recording_processors:
            document = processor.process_response(document)
            if document is None:
                raise AssertionError('A recording processor discarded the captured plan document.')
        normalized_plan = self._normalize(json.loads(document['body']['string']))
        normalized_digest = self.digest(normalized_plan)
        previous = self.intent_digests.get(actual_digest)
        if previous is not None and previous != normalized_digest:
            raise AssertionError('Recording sanitizers changed the same plan inconsistently between invocations.')
        self.intent_digests[actual_digest] = normalized_digest

    def _normalize(self, value):
        if isinstance(value, list):
            return [self._normalize(item) for item in value]
        if not isinstance(value, dict):
            return value
        normalized = {}
        for key, item in value.items():
            if key == 'tenantId' and isinstance(item, str) and item.lower() in self.tenant_ids:
                normalized[key] = MOCKED_TENANT_ID
            elif key == 'tags' and isinstance(item, dict) and _INTENT_TAG in item:
                normalized[key] = dict(item)
                original = item[_INTENT_TAG]
                if original in self.intent_digests.values():
                    # VCR can filter the SAME request again while checking cassette membership.
                    # Accept only a normalized digest already computed from a captured real plan.
                    normalized[key][_INTENT_TAG] = original
                elif original in self.intent_digests:
                    normalized[key][_INTENT_TAG] = self.intent_digests[original]
                else:
                    raise AssertionError('An intent tag has no captured production plan; refusing to invent a digest.')
            else:
                normalized[key] = self._normalize(item)
        return normalized

    def _body(self, body):
        if not body:
            return body
        try:
            value = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return body
        return json.dumps(self._normalize(value))

    def process_request(self, request):
        request.body = self._body(request.body)
        return request

    def process_response(self, response):
        response['body']['string'] = self._body(response['body']['string'])
        return response


@contextmanager
def create_recording_context(test):
    if not test.in_recording:
        yield None
        return
    from azure.cli.command_modules.horizondb import _create
    from azure.cli.core._profile import Profile
    digest = _create._plan_digest  # pylint: disable=protected-access
    # Seed the local profile tenant before even the Graph/catalog reads preceding plan capture.
    tenant_id = Profile(cli_ctx=test.cli_ctx).get_subscription()['tenantId']
    normalizer = CreateRecordingNormalizer(test, digest, tenant_id)
    original_post_processors = test.recording_post_processors
    # Post-process after all ordinary sanitizers, but before ContentLengthProcessor.
    test.recording_post_processors = [normalizer] + original_post_processors

    def observe_plan(plan):
        actual_digest = digest(plan)
        normalizer.capture_plan(plan, actual_digest)
        return actual_digest  # Live resource tags and all live conflict checks remain unmodified.

    try:
        with mock.patch.object(_create, '_plan_digest', side_effect=observe_plan):
            yield normalizer
    finally:
        test.recording_post_processors = original_post_processors


def normalize_create_recording(test_method):
    @wraps(test_method)
    def wrapped(test, *args, **kwargs):
        with create_recording_context(test):
            return test_method(test, *args, **kwargs)
    return wrapped
