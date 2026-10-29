"""Synthetic HTTP contracts only; these do not verify Google continuation acceptance."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import frame, model_row
from pgproxy.main import create_app


MODEL = 'synthetic-continuation'
TOKEN = 'SYNTHETIC_CONTINUATION_TOKEN_DO_NOT_EXPOSE'
NATIVE_USAGE = {
    'promptTokenCount': 7,
    'candidatesTokenCount': 120,
    'totalTokenCount': 134,
    'cachedContentTokenCount': 4,
    'thoughtsTokenCount': 7,
}
OPENAI_USAGE = {
    'prompt_tokens': 7,
    'completion_tokens': 127,
    'total_tokens': 134,
    'completion_tokens_details': {'reasoning_tokens': 7},
    'prompt_tokens_details': {'cached_tokens': 4},
}


def continuation_client(client_factory, second_status=200):
    generated = []

    def handler(request):
        method = request.url.path.rsplit('/', 1)[-1]
        if method == 'ListModels':
            row = model_row('models/' + MODEL)
            row[5], row[6], row[77] = 1048576, 65536, 2
            row[64].append(91)
            return httpx.Response(200, json=[[row]])
        if method != 'GenerateContent':
            return None
        wire = json.loads(request.content)
        generated.append(wire)
        assert len(generated) <= 2, 'A completed two-round response must not be submitted again'
        assert wire[3][45] == 1 and wire[10] is None
        if len(generated) == 1:
            assert wire[3][3] == 32768
            first = frame('A', 22)
            first[0][0] += [None] * (9 - len(first[0][0]))
            first[0][0][8] = TOKEN
            first[2] = [5, 100, 108, 2, None, None, None, None, None, 3]
            return httpx.Response(200, json=[[first]])
        assert wire[3][3] == 65536 and wire[16] == TOKEN
        if second_status != 200:
            return httpx.Response(second_status, json={'error': 'synthetic upstream denial'})
        last = frame('B', 1)
        last[2] = [7, 20, 31, 4, None, None, None, None, None, 4]
        return httpx.Response(200, json=[[last]])

    client, _ = client_factory(extra_handler=handler)
    return client, generated


def auth(client):
    return {'Authorization': 'Bearer ' + client.settings.password}


def sse_events(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: {')]


def assert_no_private_metadata(text):
    assert TOKEN not in text
    assert '_continuationToken' not in text
    assert '_finishCode' not in text


def test_continuation_model_visible_in_both_catalogs_without_private_fields(client_factory):
    client, generated = continuation_client(client_factory)
    with TestClient(create_app(client)) as api:
        openai = api.get('/v1/models', headers=auth(client))
        native = api.get('/v1beta/models', headers=auth(client))
        assert openai.status_code == native.status_code == 200
        assert [entry['id'] for entry in openai.json()['data']] == [MODEL]
        assert [entry['name'] for entry in native.json()['models']] == ['models/' + MODEL]
        assert not any(key.startswith('_') for entry in native.json()['models'] for key in entry)
    assert not generated


@pytest.mark.parametrize('stream', [False, True])
def test_openai_continuation_is_one_response_with_combined_usage(client_factory, stream):
    client, generated = continuation_client(client_factory)
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Synthetic prompt'}], 'stream': stream}
    if stream:
        body['stream_options'] = {'include_usage': True}
    with TestClient(create_app(client)) as api:
        response = api.post('/v1/chat/completions', headers=auth(client), json=body)
        assert response.status_code == 200
        assert_no_private_metadata(response.text)
        if stream:
            events = sse_events(response.text)
            choices = [choice for event in events for choice in event['choices']]
            assert ''.join(choice['delta'].get('content', '') for choice in choices) == 'AB'
            assert [choice['finish_reason'] for choice in choices if choice.get('finish_reason')] == ['stop']
            assert len({event['id'] for event in events}) == 1
            assert events[-1]['usage'] == OPENAI_USAGE
            assert response.text.count('data: [DONE]') == 1
            assert response.text.endswith('data: [DONE]\n\n')
        else:
            result = response.json()
            assert result['choices'][0]['message']['content'] == 'AB'
            assert result['choices'][0]['finish_reason'] == 'stop'
            assert result['usage'] == OPENAI_USAGE
    assert len(generated) == 2 and client.closed


@pytest.mark.parametrize('mode', ['unary', 'json', 'sse'])
def test_native_continuation_has_no_intermediate_finish_or_private_token(client_factory, mode):
    client, generated = continuation_client(client_factory)
    action = 'generateContent' if mode == 'unary' else 'streamGenerateContent?alt=' + mode
    body = {'contents': [{'role': 'user', 'parts': [{'text': 'Synthetic prompt'}]}]}
    with TestClient(create_app(client)) as api:
        response = api.post('/v1beta/models/' + MODEL + ':' + action, headers=auth(client), json=body)
        assert response.status_code == 200
        assert_no_private_metadata(response.text)
        chunks = ([response.json()] if mode == 'unary' else
                  response.json() if mode == 'json' else sse_events(response.text))
        candidates = [candidate for chunk in chunks for candidate in chunk.get('candidates', [])]
        assert ''.join(part.get('text', '') for candidate in candidates
                       for part in candidate.get('content', {}).get('parts', [])) == 'AB'
        assert [candidate['finishReason'] for candidate in candidates if 'finishReason' in candidate] == ['STOP']
        usages = [chunk['usageMetadata'] for chunk in chunks if 'usageMetadata' in chunk]
        assert usages[-1] == NATIVE_USAGE
        assert '[DONE]' not in response.text
    assert len(generated) == 2 and client.closed


def test_second_round_denial_ends_openai_stream_without_success_done(client_factory):
    client, generated = continuation_client(client_factory, second_status=403)
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Synthetic prompt'}], 'stream': True}
    with TestClient(create_app(client)) as api:
        response = api.post('/v1/chat/completions', headers=auth(client), json=body)
        assert response.status_code == 200  # The first round already started the stream.
        events = sse_events(response.text)
        assert events[-1]['error']['type'] == 'upstream_permission_denied'
        assert ''.join(choice['delta'].get('content', '') for event in events
                       for choice in event.get('choices', [])) == 'A'
        assert not any(choice.get('finish_reason') for event in events for choice in event.get('choices', []))
        assert 'data: [DONE]' not in response.text
        assert_no_private_metadata(response.text)
    assert len(generated) == 2 and client.closed and not client.lock.locked()
