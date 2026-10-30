"""Bounded, synthetic regression tests for the protocol code review findings."""
import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from pgproxy import gemini_compat
from pgproxy.errors import PlaygroundError
from pgproxy.gemini_compat import normalize_gemini_request, normalize_schema
from pgproxy.main import create_app


def shared_schema(depth, *, leaf=None):
    definitions = {'n0': leaf or {'type': 'string'}}
    for index in range(1, depth + 1):
        definitions[f'n{index}'] = {'type': 'object', 'properties': {
            'left': {'$ref': f'#/$defs/n{index-1}'},
            'right': {'$ref': f'#/$defs/n{index-1}'},
        }}
    return {'$ref': f'#/$defs/n{depth}', '$defs': definitions}


def test_shared_reference_dag_has_a_total_work_limit(monkeypatch):
    visited = 0
    original = gemini_compat._normalize_schema

    def measured(*args, **kwargs):
        nonlocal visited
        visited += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(gemini_compat, '_normalize_schema', measured)
    schema = shared_schema(24)
    assert len(json.dumps(schema)) < 3000
    assert normalize_schema(schema) is None
    # Count operations instead of using a flaky machine-dependent time limit.
    assert visited <= 4097


@pytest.mark.parametrize('field', ['description', 'enum', 'example'])
def test_repeated_large_leaf_values_have_an_encoded_size_limit(field):
    text = '长字符串' * 1500
    leaf = {'type': 'string', field: [text] if field == 'enum' else text}
    schema = shared_schema(5, leaf=leaf)
    assert len(json.dumps(schema, ensure_ascii=False).encode()) < 32 * 1024
    assert normalize_schema(schema) is None


def test_repeated_large_property_names_consume_the_byte_budget():
    leaf = {'type': 'object', 'properties': {'property_' + 'x' * 12000: {'type': 'string'}}}
    assert normalize_schema(shared_schema(5, leaf=leaf)) is None


def test_small_shared_schema_preserves_all_fields_and_input():
    schema = {'type': 'object', '$defs': {'value': {'type': 'integer', 'minimum': 0, 'maximum': 9}},
              'properties': {'a': {'$ref': '#/$defs/value'}, 'b': {'$ref': '#/$defs/value'}},
              'required': ['a', 'b'], 'description': 'Small supported schema'}
    original = copy.deepcopy(schema)
    assert normalize_schema(schema) == {
        'type': 'object', 'description': 'Small supported schema',
        'properties': {'a': {'type': 'integer', 'minimum': 0, 'maximum': 9},
                       'b': {'type': 'integer', 'minimum': 0, 'maximum': 9}},
        'required': ['a', 'b'],
    }
    assert schema == original


def test_schema_budget_is_shared_across_the_request_and_resets_next_request():
    entry = {'_codes': [1, 10], 'outputTokenLimit': 512}
    schema = {'type': 'object', 'description': 'x' * 100000, 'properties': {'answer': {'type': 'string'}}}
    request = {'contents': 'Hello', 'generationConfig': {'responseSchema': schema, 'temperature': 0.4},
               'tools': [{'functionDeclarations': [{'name': 'first', 'parameters': schema},
                                                   {'name': 'second', 'parameters': schema}]}]}
    normalized = normalize_gemini_request(request, entry)
    declarations = normalized['tools'][0]['functionDeclarations']
    assert normalized['generationConfig']['responseSchema'] == schema
    assert declarations[0]['parameters'] == schema
    assert declarations[1]['parameters'] == {'type': 'object'}
    assert normalized['generationConfig']['temperature'] == 0.4
    assert normalize_gemini_request(request, entry) == normalized


def test_oversized_root_is_discarded_whole_without_leaking_partial_properties():
    schema = {'type': 'object', 'properties': {
        'first': {'type': 'string'}, 'large': shared_schema(5, leaf={'type': 'string', 'description': 'x' * 32000}),
    }}
    # Put the shared definitions at the schema root, where local references resolve.
    schema['$defs'] = schema['properties']['large'].pop('$defs')
    assert normalize_schema(schema) is None
    assert normalize_schema({'type': 'string'}) == {'type': 'string'}


def test_unencodable_optional_schema_text_is_ignored():
    assert normalize_schema({'type': 'string', 'description': '\ud800'}) is None


@pytest.mark.parametrize('interface', ['openai', 'gemini'])
def test_oversized_schema_keeps_prompt_options_and_one_upstream_generation(client_factory, interface):
    client, captured = client_factory()
    schema = shared_schema(24)
    if interface == 'openai':
        path = '/v1/chat/completions'
        body = {'model': 'test-chat', 'messages': [{'role': 'user', 'content': 'Hello'}], 'temperature': 0.4,
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'result', 'schema': schema}}}
    else:
        path = '/v1beta/models/test-chat:generateContent'
        body = {'contents': 'Hello', 'generationConfig': {'temperature': 0.4, 'responseSchema': schema}}
    with TestClient(create_app(client)) as api:
        response = api.post(path, json=body, headers={'Authorization': 'Bearer ' + client.settings.password})
    assert response.status_code == 200, response.text
    requests = [r for r in captured if r.url.path.endswith('/GenerateContent')]
    assert len(requests) == 1
    wire = json.loads(requests[0].content)
    assert wire[1][0][0][0][1] == 'Hello'
    assert wire[3][4] == 0.4 and wire[3][8] is None
    assert len(requests[0].content) < 4096
    assert not client.lock.locked()


@pytest.mark.parametrize('failed_method', ['GetLoggingContext', 'GetUserPreferences', 'GetAiStudioBenefitTier'])
@pytest.mark.parametrize('failure', ['http', 'cancelled'])
@pytest.mark.asyncio
async def test_partial_initialization_rolls_back_and_next_request_completes(client_factory, failed_method, failure):
    seen, failed = [], False

    def handler(request):
        nonlocal failed
        method = request.url.path.rsplit('/', 1)[-1]
        seen.append(method)
        if method == failed_method and not failed:
            failed = True
            if failure == 'cancelled':
                raise asyncio.CancelledError()
            return httpx.Response(503)

    client, captured = client_factory(extra_handler=handler)
    try:
        expected = asyncio.CancelledError if failure == 'cancelled' else PlaygroundError
        with pytest.raises(expected):
            await client.models()
        assert not client.api_key and not client.logging_context and not client.visit_id
        assert not client.benefit_tier_header and client.page_globals == {}
        assert client.catalog == {} and client.models_at == client.waa_at == 0
        assert not client.lock.locked()
        split = len(seen)
        models = await client.models()
        assert list(models) == ['test-chat']
        assert seen[split:] == ['new_chat', 'GetLoggingContext', 'GetUserPreferences', 'GetAiStudioBenefitTier', 'ListModels']
        assert client.logging_context and client.visit_id and client.benefit_tier_header == 'TIER1'
        assert not any(request.url.path.endswith('/GenerateContent') for request in captured)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_initialization_protocol_error_is_rolled_back_without_replaying_generation(client_factory):
    failed = False

    def handler(request):
        nonlocal failed
        if request.url.path.endswith('/GetAiStudioBenefitTier') and not failed:
            failed = True
            return httpx.Response(200, json=['invalid-tier'])

    client, captured = client_factory(extra_handler=handler)
    try:
        body = {'contents': 'Hello'}
        with pytest.raises(PlaygroundError) as error:
            _ = [chunk async for chunk in client.generate('test-chat', body)]
        assert error.value.code == 'page_initialization_failed'
        assert not client.api_key
        assert not any(request.url.path.endswith('/GenerateContent') for request in captured)
        response = [chunk async for chunk in client.generate('test-chat', body)]
        assert response[-1]['candidates'][0]['finishReason'] == 'STOP'
        assert len([r for r in captured if r.url.path.endswith('/GenerateContent')]) == 1
    finally:
        await client.close()
