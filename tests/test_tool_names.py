import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import model_row
from pgproxy.main import create_app
from pgproxy.playground_protocol import encode_generate, set_field
from pgproxy.token_protocol import encode_count_tokens


NAME = 'weather_for_' + 'a' * 82
CALL_ID = 'call_weather'
SIGNATURE = 'opaque_test_signature'


def _request(names):
    return {
        'contents': [
            {'role': 'model', 'parts': [{'functionCall': {'name': names[0], 'id': CALL_ID},
                                         'thoughtSignature': SIGNATURE}]},
            {'role': 'user', 'parts': [{'functionResponse': {'name': names[0], 'id': CALL_ID,
                                                          'response': {'temperature': 20}}}]},
        ],
        'tools': [{'functionDeclarations': [{'name': name} for name in names]}],
    }


def _model():
    return {'name': 'models/test-chat', 'inputTokenLimit': 1024, 'outputTokenLimit': 512,
            '_codes': [1, 10, 91]}


def test_tool_names_use_the_same_wire_alias_in_history_declarations_and_count():
    request = _request([NAME, 'excluded'])
    request['toolConfig'] = {'functionCallingConfig': {'mode': 'AUTO', 'allowedFunctionNames': [NAME]}}
    before = copy.deepcopy(request)
    aliases = {}
    wire, _, _ = encode_generate('test-chat', request, _model(), function_aliases=aliases)
    alias, = aliases
    assert aliases[alias] == NAME
    assert len(alias.encode('utf-8')) <= 63
    assert [declaration[0] for declaration in wire[6][0][1]] == [alias]
    assert wire[1][0][0][0][10] == [alias, [], CALL_ID]
    assert wire[1][0][0][0][14] == SIGNATURE
    assert wire[1][1][0][0][11] == [alias, [[['temperature', [None, 20]]]], CALL_ID]
    continued, _, _ = encode_generate('test-chat', request, _model(), continuation_round=1,
                                      continuation_token='fixture-token')
    counted = encode_count_tokens('test-chat', {'generateContentRequest': request}, _model())
    assert continued[1] == counted[2][1] == wire[1]
    assert continued[6] == counted[2][6] == wire[6]
    assert request == before


def test_alias_assignment_reserves_existing_short_names_and_uses_utf8_bytes():
    aliases = {}
    encode_generate('test-chat', _request([NAME]), _model(), function_aliases=aliases)
    reserved, = aliases
    unicode_name = 'tool_' + '雨' * 23
    exact_limit = 'x' * 63
    long_boundary = 'y' * 64
    names = [NAME, unicode_name, reserved, exact_limit, long_boundary]
    wire, _, _ = encode_generate('test-chat', _request(names), _model(), function_aliases=aliases)
    distinct = [declaration[0] for declaration in wire[6][0][1]]
    assert len(set(distinct)) == len(names)
    assert all(len(name.encode('utf-8')) <= 63 for name in distinct)
    assert distinct[2:4] == [reserved, exact_limit]
    assert set(aliases.values()) == {NAME, unicode_name, long_boundary}
    backwards = {}
    encode_generate('test-chat', _request(list(reversed(names))), _model(), function_aliases=backwards)
    assert backwards == aliases


@pytest.mark.parametrize('endpoint', ['gemini', 'gemini_json', 'gemini_sse', 'openai', 'openai_sse'])
def test_public_function_calls_restore_the_name_and_keep_signature(client_factory, endpoint):
    def handler(request):
        if request.url.path.endswith('/ListModels'):
            row = model_row()
            row[64].append(10)
            return httpx.Response(200, json=[[row]])
        if request.url.path.endswith('/GenerateContent'):
            upstream = json.loads(request.content)
            alias = upstream[6][0][1][0][0]
            assert alias != NAME and len(alias.encode('utf-8')) <= 63
            part = set_field([], 10, [alias, [], CALL_ID])
            set_field(part, 14, SIGNATURE)
            frame = [[[[[part], 'model'], 1]]]
            return httpx.Response(200, json=[[frame]])

    client, _ = client_factory(extra_handler=handler)
    openai = endpoint.startswith('openai')
    stream = endpoint.endswith(('sse', 'json'))
    if openai:
        payload = {'model': 'test-chat', 'messages': [{'role': 'user', 'content': 'Weather?'}],
                   'tools': [{'type': 'function', 'function': {'name': NAME}}], 'stream': stream}
        path = '/v1/chat/completions'
    else:
        payload = {'contents': [{'parts': [{'text': 'Weather?'}]}],
                   'tools': [{'functionDeclarations': [{'name': NAME}]}]}
        path = '/v1beta/models/test-chat:' + ('streamGenerateContent' if stream else 'generateContent')
        if endpoint.endswith('sse'):
            path += '?alt=sse'
    with TestClient(create_app(client)) as api:
        response = api.post(path, json=payload, headers={'Authorization': 'Bearer ' + client.settings.password})
        assert response.status_code == 200, response.text
    if endpoint.endswith('sse'):
        chunks = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
    else:
        chunks = response.json() if stream else [response.json()]
    if openai:
        message_key = 'delta' if stream else 'message'
        call = next(c['choices'][0][message_key]['tool_calls'][0] for c in chunks
                    if c['choices'] and c['choices'][0][message_key].get('tool_calls'))
        assert call['function']['name'] == NAME
        assert call['id'] == CALL_ID
        assert call['extra_content']['google']['thought_signature'] == SIGNATURE
    else:
        part = chunks[0]['candidates'][0]['content']['parts'][0]
        assert part['functionCall'] == {'name': NAME, 'args': {}, 'id': CALL_ID}
        assert part['thoughtSignature'] == SIGNATURE
