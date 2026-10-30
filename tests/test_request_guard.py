"""Actual ASGI boundary probes; no upstream network or Google credentials."""
import asyncio
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pgproxy.config import Settings
from pgproxy.main import create_app
from pgproxy.request_guard import RequestGuardMiddleware, MAX_HTTP_CONCURRENCY
from pgproxy.request_statistics import RequestStatistics, RequestStatisticsMiddleware


def scope_for(path='/v1/chat/completions', method='POST', headers=None, query=b''):
    return {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
            'http_version': '1.1', 'method': method, 'scheme': 'http', 'path': path,
            'raw_path': path.encode(), 'query_string': query, 'headers': headers or [],
            'root_path': '', 'client': ('127.0.0.1', 1), 'server': ('127.0.0.1', 8888),
            'app': SimpleNamespace(state=SimpleNamespace(
                playground=SimpleNamespace(settings=Settings(password='test-secret'))))}


async def run_guard(*, chunks=None, headers=None, query=b'', delay=0, limit=64,
                    path='/v1/chat/completions', method='POST', disconnect=False,
                    after_body_disconnect=False, upload_timeout=.25):
    received, sent, forwarded = [], [], []
    # A simple queue makes arbitrary chunk boundaries and the final disconnect explicit.
    pending = list(chunks or [b'{}'])
    async def queue_receive():
        if delay:
            await asyncio.sleep(delay)
        if disconnect or not pending:
            received.append('disconnect')
            return {'type': 'http.disconnect'}
        chunk = pending.pop(0)
        received.append(chunk)
        return {'type': 'http.request', 'body': chunk, 'more_body': bool(pending)}

    async def downstream(scope, receive, send):
        forwarded.append(await receive())
        if after_body_disconnect:
            forwarded.append(await receive())
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'OK'})

    async def send(message):
        sent.append(message)

    statistics = RequestStatistics()
    guarded = RequestStatisticsMiddleware(
        RequestGuardMiddleware(downstream, max_body_bytes=limit, upload_timeout=upload_timeout), statistics)
    await guarded(scope_for(path, method, headers, query), queue_receive, send)
    return received, sent, forwarded, statistics.snapshot()


def status(sent):
    return next(message['status'] for message in sent if message['type'] == 'http.response.start')


AUTH = [(b'authorization', b'Bearer test-secret')]


@pytest.mark.parametrize('length', [[], [(b'content-length', b'8388656')]])
async def test_rejects_unauthorized_upload_before_any_read(length):
    read, sent, forwarded, counts = await run_guard(headers=length, chunks=[b'x' * 100])
    assert status(sent) == 401 and not read and not forwarded
    assert counts == {'requestsSucceeded': 0, 'requestsFailed': 1}
    headers = dict(sent[0]['headers'])
    assert headers[b'www-authenticate'] == b'Basic' and headers[b'connection'] == b'close'


@pytest.mark.parametrize('headers,query', [
    (AUTH, b''), ([(b'x-goog-api-key', b'test-secret')], b''),
    ([(b'authorization', b'Basic ' + base64.b64encode(b'any:test-secret'))], b''),
    ([], b'key=test-secret'),
])
async def test_all_auth_forms_replay_body_once_and_keep_disconnect(headers, query):
    read, sent, forwarded, counts = await run_guard(headers=headers, query=query,
        chunks=[b'{"a":', b'1}'], after_body_disconnect=True)
    assert status(sent) == 200
    assert forwarded == [{'type': 'http.request', 'body': b'{"a":1}', 'more_body': False},
                         {'type': 'http.disconnect'}]
    assert read == [b'{"a":', b'1}', 'disconnect']
    assert counts == {'requestsSucceeded': 0, 'requestsFailed': 1}


@pytest.mark.parametrize('path', ['/v1/chat/completions', '/v1/models/m:generateContent',
                                '/v1beta/models/m:streamGenerateContent', '/v1beta/models/m:countTokens'])
async def test_known_oversize_rejects_without_reading(path):
    read, sent, forwarded, _ = await run_guard(path=path, headers=AUTH + [(b'content-length', b'65')])
    assert status(sent) == 413 and not read and not forwarded


@pytest.mark.parametrize('length', [[], [(b'content-length', b'2')]])
async def test_chunked_or_underdeclared_body_cannot_bypass_limit(length):
    read, sent, forwarded, _ = await run_guard(headers=AUTH + length,
        chunks=[b'x' * 32, b'x' * 32, b'x', b'not read'])
    assert status(sent) == 413 and len(read) == 3 and not forwarded


@pytest.mark.parametrize('lengths', [[b'-1'], [b'1.0'], [b''], [b'12', b'12'], [b'9' * 5000]])
async def test_malformed_lengths_are_safe_400_before_read(lengths):
    read, sent, forwarded, _ = await run_guard(headers=AUTH + [(b'content-length', v) for v in lengths])
    assert status(sent) == 400 and not read and not forwarded


async def test_upload_timeout_covers_the_whole_body_and_does_not_forward_partial_json():
    read, sent, forwarded, _ = await run_guard(headers=AUTH, delay=.15, chunks=[b'{', b'}'])
    assert status(sent) == 408 and read == [b'{'] and not forwarded


async def test_disconnect_while_uploading_never_starts_generation():
    read, sent, forwarded, counts = await run_guard(headers=AUTH, disconnect=True)
    assert read == ['disconnect'] and not sent and not forwarded
    assert counts == {'requestsSucceeded': 0, 'requestsFailed': 1}


async def test_body_at_limit_is_accepted():
    _, sent, forwarded, counts = await run_guard(headers=AUTH, chunks=[b'x' * 32, b'x' * 32])
    assert status(sent) == 200 and len(forwarded[0]['body']) == 64
    assert counts == {'requestsSucceeded': 1, 'requestsFailed': 0}


def test_cors_early_auth_and_validation_errors_stay_compatible(client_factory):
    client, requests = client_factory()
    with TestClient(create_app(client)) as api:
        preflight = api.options('/v1/chat/completions', headers={
            'origin': 'https://example.test', 'access-control-request-method': 'POST'})
        assert preflight.status_code == 200
        denied = api.post('/v1/chat/completions', content=b'not json', headers={'origin': 'https://example.test'})
        assert denied.status_code == 401
        assert denied.headers['access-control-allow-origin'] == '*'
        assert not requests
        invalid = api.post('/v1/chat/completions', content=b'not json', headers={
            'authorization': 'Bearer ' + client.settings.password, 'content-type': 'application/json'})
        assert invalid.status_code == 400 and invalid.json()['error']['code'] == 400
        assert api.post('/v1/models/removed/generateContent').status_code == 404
        assert api.post('/v1/models').status_code == 405
        assert api.get('/health').status_code == 200


def test_android_listener_has_a_finite_concurrency_limit():
    from pgproxy.android_host import Controller
    from test_accounts import MemoryBridge, FakeClient
    from test_android_host import call, free_port
    controller = Controller(MemoryBridge(), FakeClient)
    try:
        assert call(controller, 'configure', port=free_port())['ok']
        assert call(controller, 'start')['ok']
        assert controller.server.config.limit_concurrency == MAX_HTTP_CONCURRENCY == 16
    finally:
        controller.close()
