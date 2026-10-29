import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import model_row
from pgproxy.errors import PlaygroundError
from pgproxy.main import create_app
from pgproxy.playground_protocol import parse_models
from pgproxy.token_protocol import encode_count_tokens, decode_token_count


def model():
    row = model_row()
    row[7] = ['generateContent', 'countTokens']
    return parse_models(json.dumps([[row]]))['test-chat']


def test_count_tokens_simple_and_inline_use_provider_wire_shapes():
    contents = [{'role': 'user', 'parts': [{'text': 'hello'}]}]
    simple = encode_count_tokens('test-chat', {'contents': contents}, model())
    assert simple[0] == 'models/test-chat' and len(simple) == 2
    blob = base64.b64encode(b'synthetic-image').decode()
    contents[0]['parts'] = [{'inlineData': {'mimeType': 'image/png', 'data': blob}}]
    nested = encode_count_tokens('test-chat', {'contents': contents}, model())
    assert nested[1] is None and nested[2][0] == 'models/test-chat'


@pytest.mark.parametrize('key,data_key,slot', [('toolCall','args',25),('toolResponse','response',26)])
def test_count_tokens_video_tool_history_requires_nested_generate_request(key,data_key,slot):
    entry = model()
    entry['_codes'].append(69)
    # This is direct contents, not generateContentRequest: the part itself must
    # select the nested provider message, including its actual Struct and ID.
    contents = [{'role':'model','parts':[{key:{'toolType':'MEDIA_PROCESSING',
                'toolName':'inspect_video', data_key:{'frame':2},'id':'video-fixture'}}]}]
    wire = encode_count_tokens('test-chat',{'contents':contents},entry)
    assert wire[:2] == ['models/test-chat',None]
    assert len(wire) == 3 and wire[2][0] == 'models/test-chat'
    part = wire[2][1][0][0][0]
    assert part[slot] == [13,'inspect_video',[[['frame',[None,2]]]],'video-fixture']


def test_count_tokens_system_and_model_identity():
    body = {'generateContentRequest': {'model': 'models/test-chat',
            'systemInstruction': {'parts': [{'text': 'system'}]}}}
    encoded = encode_count_tokens('test-chat', body, model())
    assert encoded[2][1] is None and encoded[2][5] is not None
    body['generateContentRequest']['model'] = 'other-model'
    with pytest.raises(PlaygroundError) as caught:
        encode_count_tokens('test-chat', body, model())
    assert caught.value.status == 400


@pytest.mark.parametrize('body', [{}, {'contents': []}, {'unknown': 1},
    {'contents': [], 'generateContentRequest': {}}, {'generateContentRequest': None}])
def test_count_invalid_inputs_are_rejected(body):
    with pytest.raises(PlaygroundError):
        encode_count_tokens('test-chat', body, model())


@pytest.mark.parametrize('raw', ['[]', '[null]', '[-1]', '[true]', '[1.5]', '["5"]'])
def test_count_requires_authoritative_integer(raw):
    with pytest.raises(PlaygroundError):
        decode_token_count(raw)
    assert decode_token_count('[0]') == {'totalTokens': 0}


@pytest.mark.parametrize('prefix', ['/v1beta/models', '/v1/models'])
def test_count_api_auth_no_generation_no_waa(client_factory, prefix):
    row = model_row()
    row[7] = ['generateContent', 'countTokens']
    def handler(request):
        if request.url.path.endswith('/ListModels'):
            return httpx.Response(200, json=[[row]])
        if request.url.path.endswith('/CountTokens'):
            return httpx.Response(200, json=[7])
        return None
    client, sent = client_factory(extra_handler=handler)
    with TestClient(create_app(client)) as api:
        path = prefix + '/test-chat:countTokens'
        body = {'contents': [{'parts': [{'text': 'hello'}]}]}
        assert api.post(path, json=body).status_code == 401
        assert not sent
        response = api.post(path, json=body, headers={'Authorization': 'Bearer '+client.settings.password})
        assert response.status_code == 200, response.text
        assert response.json() == {'totalTokens': 7}
        assert not any(r.url.path.endswith('/GenerateContent') for r in sent)
        assert not any(op == 'proof' for op, _ in client.waa.calls)


@pytest.mark.parametrize('tier,expected',[(0,None),(1,'TIER1'),(2,'TIER2'),(3,'TIER0')])
def test_count_tokens_uses_account_benefit_header_without_changing_catalog_calls(client_factory,tier,expected):
    row = model_row()
    row[7] = ['generateContent','countTokens']
    replies = []
    def handler(request):
        method = request.url.path.rsplit('/',1)[-1]
        if method == 'GetAiStudioBenefitTier':
            return httpx.Response(200,json=[tier])
        if method == 'ListModels':
            assert 'x-aistudio-g1-tier' not in request.headers
            return httpx.Response(200,json=[[row]])
        if method == 'CountTokens':
            assert request.headers.get('x-aistudio-g1-tier') == expected
            assert request.headers['Authorization'].startswith('SAPISIDHASH ')
            assert request.headers['x-goog-api-key'] == 'test-page-key'
            reply = httpx.Response(200,json=[11])
            replies.append(reply)
            return reply
        return None
    client,sent = client_factory(extra_handler=handler)
    with TestClient(create_app(client)) as api:
        response = api.post('/v1beta/models/test-chat:countTokens',
            json={'contents':[{'parts':[{'text':'count this'}]}]},
            headers={'Authorization':'Bearer '+client.settings.password})
        assert response.status_code == 200, response.text
        assert response.json() == {'totalTokens':11}
        assert replies and all(reply.is_closed for reply in replies)
        assert len([r for r in sent if r.url.path.endswith('/CountTokens')]) == 1
        assert not client.waa.calls


@pytest.mark.asyncio
async def test_counting_keeps_generation_rotation_and_does_not_mark_verified():
    from test_accounts import pool_fixture, add, consume
    pool, _ = pool_fixture()
    try:
        a, b = await add(pool, 'A'), await add(pool, 'B')
        counted = []
        async def count(model_id, body):
            counted.append(model_id)
            return {'totalTokens': 9}
        pool.client(pool.record(a)).count_tokens = count
        cursor = pool.cursor
        assert await pool.count_tokens('test-chat', {}) == {'totalTokens': 9}
        assert counted == ['test-chat'] and pool.cursor == cursor
        assert pool.record(a)['status'] != 'verified'
        assert (await consume(pool))[0]['candidates'][0]['content']['parts'][0]['text'] == a
        assert (await consume(pool))[0]['candidates'][0]['content']['parts'][0]['text'] == b
    finally:
        await pool.close()
