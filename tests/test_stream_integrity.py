import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from conftest import frame
from pgproxy import responses
from pgproxy.main import create_app


NATIVE_INPUT = {'contents': [{'parts': [{'text': 'hello'}]}]}


def generate(api, mode):
    headers = {'Authorization': 'Bearer 123'}
    if mode.startswith('openai'):
        return api.post('/v1/chat/completions', headers=headers, json={
            'model': 'test-chat', 'messages': [{'role': 'user', 'content': 'hello'}],
            'stream': mode == 'openai-sse',
        })
    action = {'native-unary': 'generateContent',
              'native-json': 'streamGenerateContent?alt=json',
              'native-sse': 'streamGenerateContent?alt=sse'}[mode]
    return api.post('/v1beta/models/test-chat:' + action, headers=headers, json=NATIVE_INPUT)


def sse_payloads(body):
    return [json.loads(line[6:]) for line in body.splitlines()
            if line.startswith('data: ') and line != 'data: [DONE]']


def generation_calls(calls):
    return sum(request.url.path.endswith('/GenerateContent') for request in calls)


@pytest.mark.parametrize('mode', ['openai-sse', 'openai-unary', 'native-sse', 'native-json', 'native-unary'])
@pytest.mark.parametrize(('frames', 'trailer', 'error_type'), [
    ([frame('', 1)], [0], 'protocol_error'),
    ([[None, None, [3, 0, 3]], frame('', 19)], [0], 'protocol_error'),
    ([[None, None, [3, 0, 3]], frame('cannot complete', 1)],
     [13, 'private diagnostic in trailer'], 'upstream_error'),
    ([frame('cannot complete', 1)], [True], 'protocol_error'),
])
def test_first_output_failure_preserves_http_status_and_never_replays(client_factory, mode, frames, trailer, error_type):
    client, calls = client_factory(generate_body=json.dumps([frames, trailer]).encode())
    app = create_app(client)
    with TestClient(app) as api:
        response = generate(api, mode)
        assert response.status_code == 502
        assert response.json()['error']['type'] == error_type
        assert 'private diagnostic' not in response.text
        assert 'cannot complete' not in response.text
        assert generation_calls(calls) == 1
        assert app.state.request_statistics.snapshot() == {'requestsSucceeded': 0, 'requestsFailed': 1}
        assert not client.lock.locked()


@pytest.mark.parametrize('mode', ['openai-sse', 'native-sse', 'native-json'])
def test_failed_trailer_after_actual_text_uses_committed_error_without_success(client_factory, mode):
    body = json.dumps([[frame('partial', None), frame('', 1)], [13, 'private diagnostic in trailer']])
    client, calls = client_factory(generate_body=body.encode())
    app = create_app(client)
    with TestClient(app) as api:
        response = generate(api, mode)
        assert response.status_code == 200
        assert 'partial' in response.text
        assert '[DONE]' not in response.text
        assert 'private diagnostic' not in response.text
        payloads = response.json() if mode == 'native-json' else sse_payloads(response.text)
        assert payloads[-1]['error']['type'] == 'upstream_error'
        assert 'finishReason' not in response.text
        assert not any(choice['finish_reason'] for item in payloads for choice in item.get('choices', []))
        assert generation_calls(calls) == 1
        assert app.state.request_statistics.snapshot() == {'requestsSucceeded': 0, 'requestsFailed': 1}

        store = app.state.request_logs
        detail = store.detail(store.snapshot()[-1]['id'])
        assert detail['status'] == 200
        assert detail['error']['businessHttpStatus'] == 502
        assert detail['error']['upstreamCode'] == 'google.rpc.Status 13'
        assert 'private diagnostic' not in json.dumps(detail)


@pytest.mark.parametrize('mode', ['native-unary', 'native-json', 'native-sse'])
@pytest.mark.parametrize('finish', ['feedback', 3, 2])
def test_native_safety_and_length_finishes_keep_structured_signal_without_private_text(client_factory, mode, finish):
    if finish == 'feedback':
        terminal = [None, [1], [3, 0, 3]]
    else:
        terminal = frame('', finish)
        terminal[0][0].extend([None, 'private diagnostic in finish'])
    body = json.dumps([[terminal], [0]])
    client, _ = client_factory(generate_body=body.encode())
    with TestClient(create_app(client)) as api:
        response = generate(api, mode)
        assert response.status_code == 200
        assert 'private diagnostic' not in response.text
        result = sse_payloads(response.text)[-1] if mode == 'native-sse' else response.json()
        if mode == 'native-json':
            result = result[-1]
        if finish == 'feedback':
            assert result['promptFeedback']['blockReason'] == 'SAFETY'
        else:
            assert result['candidates'][0]['finishReason'] == {3: 'SAFETY', 2: 'MAX_TOKENS'}[finish]


def test_standalone_late_usage_is_retained_with_one_confirmed_finish(client_factory):
    body = json.dumps([[frame('answer', 1), [None, None, [7, 2, 9]]], [0]])
    client, _ = client_factory(generate_body=body.encode())
    with TestClient(create_app(client)) as api:
        response = generate(api, 'native-json')
        assert response.status_code == 200
        result, = response.json()
        assert result['candidates'][0]['finishReason'] == 'STOP'
        assert result['usageMetadata']['totalTokenCount'] == 9


class DelayedGeneration(httpx.AsyncByteStream):
    def __init__(self):
        self.release = asyncio.Event()
        self.closed = False
        self.reads = 0

    async def __aiter__(self):
        self.reads += 1
        yield ('[[' + json.dumps(frame('partial', None)) + ',').encode()
        self.reads += 1
        await self.release.wait()
        yield (json.dumps(frame('complete', 1)) + '],[0]]').encode()

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect', [False, True])
async def test_sse_keepalive_does_not_cancel_pending_read_and_disconnect_releases_it(client_factory, monkeypatch, disconnect):
    monkeypatch.setattr(responses, 'SSE_HEARTBEAT_SECONDS', 0.001)
    upstream = DelayedGeneration()

    def handler(request):
        if request.url.path.endswith('/GenerateContent'):
            return httpx.Response(200, stream=upstream)

    client, calls = client_factory(extra_handler=handler)
    seen = []

    async def send(message):
        body = message.get('body', b'')
        seen.append(body)
        if body == b': keepalive\n\n':
            assert upstream.reads == 2
            if disconnect:
                raise OSError('disconnected')
            upstream.release.set()

    async def receive():
        await asyncio.Future()

    try:
        response = await responses.generation_response(
            client, 'test-chat', NATIVE_INPUT, streaming=True, openai=True)
        request = {'type': 'http', 'asgi': {'spec_version': '2.4'}}
        if disconnect:
            with pytest.raises((OSError, ClientDisconnect)):
                await asyncio.wait_for(response(request, receive, send), timeout=2)
        else:
            await asyncio.wait_for(response(request, receive, send), timeout=2)
        assert b': keepalive\n\n' in seen
        wire = b''.join(seen)
        assert b'partial' in wire
        assert (b'[DONE]' in wire) is not disconnect
        assert (b'complete' in wire) is not disconnect
        assert upstream.closed and not client.lock.locked()
        assert generation_calls(calls) == 1
    finally:
        await client.close()
