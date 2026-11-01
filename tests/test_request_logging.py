"""API logging acceptance contracts; all Google transport is local/synthetic."""
import asyncio
import base64
import json
import time
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from pgproxy.errors import PlaygroundError
from pgproxy.main import create_app
from pgproxy.network_info import NetworkInfo, diagnose_failure
from pgproxy.request_logging import (LogStore, RequestLoggingMiddleware, note_upstream,
    record_error, record_parameters, record_credential_issue, register_secrets, set_stage)
from conftest import frame


def details(store):
    return [store.detail(item['id']) for item in store.snapshot()]


def request_detail(store):
    return next(item for item in reversed(details(store)) if item['event'] == 'api_request')


def exported(store):
    return json.dumps(details(store), ensure_ascii=False)


def auth(client):
    return {'Authorization': 'Bearer ' + client.settings.password}


def body(**extra):
    return {'model': 'test-chat', 'messages': [{'role': 'user', 'content': 'private-prompt-DoNotExport'}], **extra}


@pytest.mark.parametrize('scheme', ['bearer', 'basic', 'google', 'query', 'missing'])
def test_wrong_auth_is_traced_before_any_google_request_and_secrets_are_absent(client_factory, scheme):
    client, upstream = client_factory()
    # A short default like 123 can occur innocently inside timestamps/UUIDs;
    # use a distinct synthetic key to detect an actual credential disclosure.
    client.settings.password = 'local-api-sensitive-key-DoNotExport'
    bad = 'bad-private-key-DoNotExport'
    encoded = base64.b64encode(('username-private:' + bad).encode()).decode()
    headers, suffix = {}, ''
    if scheme == 'bearer': headers = {'Authorization': 'Bearer ' + bad}
    elif scheme == 'basic': headers = {'Authorization': 'Basic ' + encoded}
    elif scheme == 'google': headers = {'x-goog-api-key': bad}
    elif scheme == 'query': suffix = '?key=' + bad
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        response = api.get('/v1/models' + suffix, headers=headers)
    assert response.status_code == 401 and not upstream
    record = request_detail(store)
    assert record['method'] == 'GET' and record['path'] == '/v1/models'
    assert record['status'] == 401 and record['durationMs'] >= 0
    assert record['error']['type'] == 'authentication'
    assert record['error']['code'] == 'authentication_error'
    assert record['level'] == 'failure' and record['stage'] == 'authentication'
    warnings = [entry for entry in details(store) if entry['event'] == 'credential_issue']
    assert len(warnings) == 1 and warnings[0]['requestId'] == record['id']
    assert warnings[0]['failureType'] == 'authentication'
    assert bad not in exported(store) and encoded not in exported(store)
    assert client.settings.password not in exported(store)


@pytest.mark.parametrize('method,path,status', [
    ('GET', '/unknown-address', 404), ('POST', '/v1/models', 405),
    ('GET', '/v1/chat/completions', 405), ('GET', '/health', 200),
])
def test_all_application_routes_have_one_final_record_without_false_credential_warning(client_factory, method, path, status):
    client, sent = client_factory()
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        assert api.request(method, path, headers=auth(client)).status_code == status
    records = details(store)
    assert len(records) == 1 and records[0]['path'] == path
    assert records[0]['status'] == status
    assert records[0]['level'] == ('success' if status == 200 else 'failure')
    assert not records[0]['credentialIssue'] and not sent


def test_cors_failure_retains_original_middleware_error(client_factory):
    client, _ = client_factory()
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        response = api.options('/v1/models', headers={'Origin': 'https://example.test',
            'Access-Control-Request-Method': 'DELETE'})
    assert response.status_code == 400
    detail = request_detail(store)
    assert detail['error']['type'] == 'parameters'
    assert 'Disallowed CORS method' in detail['error']['raw']


@pytest.mark.parametrize('payload', ['{"private":"not-json-private-prompt"', '["private-validation-input"]'])
def test_malformed_body_errors_do_not_echo_submitted_input(client_factory, payload):
    client, sent = client_factory()
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        response = api.post('/v1/chat/completions', headers={**auth(client), 'Content-Type': 'application/json'}, content=payload)
    assert response.status_code == 400 and not sent
    detail = request_detail(store)
    assert detail['error']['type'] == 'parameters' and not detail['credentialIssue']
    assert 'private-validation-input' not in exported(store)
    assert 'not-json-private-prompt' not in exported(store)


@pytest.mark.parametrize('stream', [False, True])
def test_success_keeps_only_parameter_summary_and_never_response_content(client_factory, stream):
    response_text = 'private-generation-DoNotExport'
    client, _ = client_factory(generate_body=json.dumps([[frame(response_text, 1)]]).encode())
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        response = api.post('/v1/chat/completions', headers=auth(client), json=body(stream=stream, temperature=.25, max_tokens=16))
    assert response.status_code == 200
    detail = request_detail(store)
    assert detail['level'] == 'success' and detail['status'] == 200
    assert detail['parameters']['model'] == 'test-chat'
    assert detail['parameters']['stream'] == stream and detail['parameters']['temperature'] == .25
    assert detail['parameters']['messageCount'] == 1
    assert 'private-prompt-DoNotExport' not in exported(store) and response_text not in exported(store)
    assert 'synthetic-SAPISID' not in exported(store) and '!UNIT_TEST_ONLY' not in exported(store)
    assert detail['finishedAt'] >= detail['startedAt'] and detail['durationMs'] >= 0
    assert any(entry['url'].endswith('/GenerateContent') and entry['status'] == 200 for entry in detail['upstream'])
    assert all('parameters' not in item and 'upstream' not in item and 'error' not in item for item in store.snapshot())


@pytest.mark.parametrize('error_class,status,kind', [
    (httpx.ConnectError, 502, 'connection'), (httpx.ReadError, 502, 'connection'),
    (httpx.ReadTimeout, 504, 'timeout'), (httpx.ConnectTimeout, 504, 'timeout'),
])
def test_network_failure_keeps_type_phase_original_reason_without_fabricated_upstream_http(client_factory, error_class, status, kind):
    def handler(request):
        if request.url.path.endswith('/GenerateContent'):
            raise error_class('socket diagnostic at https://example.test/path?key=hidden-query-key', request=request)
    client, _ = client_factory(extra_handler=handler)
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        response = api.post('/v1/chat/completions', headers=auth(client), json=body())
    assert response.status_code == status
    detail = request_detail(store)
    assert detail['error']['type'] == kind and detail['error']['exceptionType'] == error_class.__name__
    assert 'socket diagnostic' in detail['error']['raw']
    assert 'hidden-query-key' not in exported(store)
    assert detail['stage'] == 'upstream_generate'
    assert 'upstreamStatus' not in detail['error']


@pytest.mark.parametrize('status,kind', [(400, 'parameters'), (401, 'credential_invalid'),
    (403, 'permission'), (429, 'rate_limit'), (500, 'upstream_5xx'), (503, 'upstream_5xx')])
def test_upstream_http_and_business_error_preserved_separately_with_secret_redaction(client_factory, status, kind):
    payload = {'error': {'status': 'SYNTHETIC_FAILURE', 'code': status,
        'message': 'Google original reason: private-prompt-DoNotExport',
        'cookie': 'cookie-private-value', 'proof': 'proof-private-value'}}
    client, _ = client_factory(generate_status=status, generate_body=json.dumps(payload).encode())
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        response = api.post('/v1/chat/completions', headers=auth(client), json=body())
    detail = request_detail(store)
    assert detail['status'] == response.status_code
    assert detail['error']['upstreamStatus'] == status
    assert detail['error']['upstreamCode'] == 'SYNTHETIC_FAILURE'
    assert detail['error']['type'] == kind and 'Google original reason' in detail['error']['raw']
    assert 'cookie-private-value' not in exported(store) and 'proof-private-value' not in exported(store)
    assert 'private-prompt-DoNotExport' not in exported(store)


@pytest.mark.parametrize('cookie_state', ['missing', 'expired', 'invalid'])
def test_google_credential_failures_are_distinct_from_local_key_rejection(client_factory, cookie_state):
    client, _ = client_factory()
    if cookie_state == 'missing':
        client.session.state['cookies'] = []
        client.session.save()
    elif cookie_state == 'expired':
        for cookie in client.session.state['cookies']:
            cookie['expires'] = time.time()-60
        client.session.save()
    else:
        client.session.state['cookies'] = 'not-a-cookie-list'
        client.session.save()
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        response = api.get('/v1/models', headers=auth(client))
    assert response.status_code == 503
    detail = request_detail(store)
    assert detail['credentialIssue'] and detail['error']['type'] != 'authentication'
    assert detail['error']['type'] == ('credential_missing' if cookie_state == 'missing' else
                                       'credential_expired' if cookie_state == 'expired' else 'credential_invalid')
    assert any(entry['event'] == 'credential_issue' for entry in details(store))


@pytest.mark.parametrize('native', [False, True])
def test_http_200_stream_failure_retains_business_status_and_never_success(client_factory, native):
    client, _ = client_factory(generate_body=json.dumps([[frame('private-partial-response', None)]]).encode())
    store = LogStore()
    with TestClient(create_app(client, request_logs=store)) as api:
        response = api.post('/v1beta/models/test-chat:streamGenerateContent?alt=sse' if native else '/v1/chat/completions',
            headers=auth(client), json={'contents':[{'parts':[{'text':'private-prompt-DoNotExport'}]}]} if native else body(stream=True))
    assert response.status_code == 200 and 'stream_interrupted' in response.text
    detail = request_detail(store)
    assert detail['level'] == 'failure' and detail['status'] == 200
    assert detail['error']['code'] == 'stream_interrupted' and detail['error']['businessHttpStatus'] == 502
    assert detail['error']['httpStatus'] == 200 and 'private-partial-response' not in exported(store)


async def run_asgi(application, store, *, path='/unit', receive=None, send=None):
    messages = []
    async def default_receive(): return {'type': 'http.disconnect'}
    async def default_send(message): messages.append(message)
    await RequestLoggingMiddleware(application, store)(
        {'type':'http','method':'POST','path':path,'headers':[],'query_string':b'',
         'server':('127.0.0.1',8888),'scheme':'http'}, receive or default_receive, send or default_send)
    return messages


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,kind', [('disconnect','client_disconnect'), ('cancel','cancelled'),
                                      ('send','client_disconnect'), ('incomplete','interrupted')])
async def test_request_interruptions_record_exactly_one_failed_outcome(mode, kind):
    store = LogStore()
    async def send(message):
        if mode == 'send' and message['type'] == 'http.response.body':
            raise BrokenPipeError('peer disconnected')
    async def app(scope, receive, send):
        await send({'type':'http.response.start','status':200})
        if mode == 'cancel': raise asyncio.CancelledError()
        if mode == 'disconnect': await receive()
        if mode != 'incomplete': await send({'type':'http.response.body','body':b'unit'})
    try:
        await run_asgi(app, store, send=send)
    except (asyncio.CancelledError, BrokenPipeError):
        assert mode in ('cancel','send')
    detail = request_detail(store)
    assert len(details(store)) == 1 and detail['status'] == 200
    assert detail['level'] == 'failure' and detail['error']['type'] == kind


@pytest.mark.asyncio
async def test_partial_credential_failure_does_not_turn_successful_directory_into_failure():
    store = LogStore()
    async def app(scope, receive, send):
        set_stage('credential')
        error = PlaygroundError('missing synthetic session',503,'session_required')
        record_credential_issue(error,'abcdef1234567890')
        await send({'type':'http.response.start','status':200})
        await send({'type':'http.response.body','body':b'{"data":[]}'})
    await run_asgi(app, store)
    result = request_detail(store)
    issue = next(item for item in details(store) if item['event'] == 'credential_issue')
    assert result['level'] == 'success' and 'error' not in result
    assert issue['requestId'] == result['id'] and issue['credential']['accountId'] == 'abcdef12'


@pytest.mark.asyncio
async def test_concurrent_requests_keep_secrets_and_errors_in_their_own_trace():
    store = LogStore()
    ready, release = asyncio.Event(), asyncio.Event()
    entered = 0
    async def app(scope, receive, send):
        nonlocal entered
        secret = scope['path'].rsplit('/',1)[-1] + '-private-value'
        register_secrets(secret)
        entered += 1
        if entered == 12: ready.set()
        await release.wait()
        error = PlaygroundError('cause for ' + scope['path'],502,'upstream_connection_error')
        error.diagnostic_raw = 'reason ' + secret
        record_error(error)
        await send({'type':'http.response.start','status':502})
        await send({'type':'http.response.body','body':b'{}'})
    tasks = [asyncio.create_task(run_asgi(app, store, path=f'/unit/{index}')) for index in range(12)]
    await asyncio.wait_for(ready.wait(),2)
    pending = details(store)
    assert len(pending) == 12 and all(item['outcome'] == 'running' for item in pending)
    release.set()
    await asyncio.gather(*tasks)
    records = details(store)
    assert len(records) == 12 and len({item['id'] for item in records}) == 12
    assert all(item['level'] == 'failure' and '-private-value' not in item['error']['raw'] for item in records)


@pytest.mark.asyncio
async def test_detail_limits_eviction_and_snapshot_copy_protect_ipc_and_store():
    store = LogStore()
    async def app(scope, receive, send):
        for index in range(80):
            set_stage('phase-' + str(index))
            note_upstream('POST', f'https://example.test/{index}', raw='error-' + 'x'*20_000, status=503)
        record_error(PlaygroundError('failure-' + 'z'*20_000,502,'upstream_error'))
        await send({'type':'http.response.start','status':502})
        await send({'type':'http.response.body','body':b'{}'})
    await run_asgi(app, store)
    entry = request_detail(store)
    assert entry['upstreamTruncated'] and entry['stagesTruncated']
    assert len(json.dumps(entry, ensure_ascii=False).encode('utf-16-le')) < 130_000
    assert len(json.dumps(store.snapshot()).encode('utf-16-le')) < 12_000
    entry['title'] = 'external mutation'
    assert store.detail(entry['id'])['title'] != 'external mutation'
    for _ in range(101): store.event('service_started')
    assert len(store.snapshot()) == 100 and store.detail(entry['id']) is None


@pytest.mark.parametrize('kind,stage,relevant', [('permission','upstream_generate',True),
    ('connection','page_initialization',True),('timeout','request_body',False),
    ('authentication','authentication',False),('parameters','parameters',False)])
def test_network_hints_use_cached_region_and_risk_without_misdiagnosing_local_error(kind, stage, relevant):
    network = NetworkInfo()
    network._data = {'ip':'1.2.3.4','countryCode':'HK','country':'Hong Kong','riskScore':91,
                     'source':'IPQuery','updatedAt':time.time()-600,'riskFlags':['proxy']}
    snapshot = network.cached_snapshot()
    result = diagnose_failure(snapshot, kind, stage)
    assert result['stale'] and result['data']['riskScore'] == 91
    assert '1.2.3.4' not in json.dumps(result)
    hints = ' '.join(result['hints'])
    assert ('不在' in hints) == relevant
    assert ('提醒阈值' in hints or '提示阈值' in hints) == relevant
    assert '不是 Google' in hints if relevant else '不将地区' in hints
    snapshot['data']['countryCode'] = 'US'
    assert network.cached_snapshot()['data']['countryCode'] == 'HK'


@pytest.mark.asyncio
async def test_failure_network_snapshot_is_fixed_at_completion_and_callback_errors_do_not_break_response():
    raw = {'data': {'countryCode':'US','ip':'1.2.3.4','riskScore':None,'updatedAt':time.time()},'error':''}
    store = LogStore(lambda: raw)
    async def app(scope, receive, send):
        set_stage('upstream_generate')
        record_error(PlaygroundError('unreachable',502,'upstream_connection_error'))
        await send({'type':'http.response.start','status':502})
        await send({'type':'http.response.body','body':b'{}'})
    await run_asgi(app, store)
    record = request_detail(store)
    raw['data']['countryCode'] = 'HK'
    assert store.detail(record['id'])['network']['data']['countryCode'] == 'US'
    def broken(): raise RuntimeError('cache unavailable')
    store.network_snapshot = broken
    assert await run_asgi(app, store)
    assert '未能读取网络缓存' in request_detail(store)['network']['note']


@pytest.mark.asyncio
async def test_logging_failure_cannot_break_public_response():
    class BrokenStore(LogStore):
        def _put(self, entry): raise RuntimeError('diagnostic storage failure')
    async def app(scope, receive, send):
        set_stage('model_catalog')
        await send({'type':'http.response.start','status':200})
        await send({'type':'http.response.body','body':b'ok'})
    result = await run_asgi(app, BrokenStore())
    assert result[-1]['body'] == b'ok'


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [
    '{"error":{"refreshToken":"secret-camel-token","message":"reason",',
    'request failed; password=secret-password; proof=secret-proof; Authorization: Bearer secret-bearer',
    'request to https://name:secret-userinfo@example.test/path?token=secret-query failed',
])
async def test_unregistered_secrets_in_raw_diagnostics_are_scrubbed_even_for_truncated_json(raw):
    store = LogStore()
    async def app(scope, receive, send):
        error = PlaygroundError('original upstream error',502,'upstream_error')
        error.diagnostic_raw = raw
        record_error(error)
        await send({'type':'http.response.start','status':502})
        await send({'type':'http.response.body','body':b'{}'})
    await run_asgi(app, store)
    assert 'secret-' not in exported(store)


@pytest.mark.asyncio
async def test_long_private_prompt_is_not_exposed_when_error_echoes_its_prefix():
    store = LogStore()
    private = 'private-medical-note-DoNotExport/' * 400
    async def app(scope, receive, send):
        record_parameters({'messages':[{'role':'user','content':private}]})
        failure = PlaygroundError('input refused: ' + private[:300],400,'invalid_request_error')
        record_error(failure)
        await send({'type':'http.response.start','status':400})
        await send({'type':'http.response.body','body':b'{}'})
    await run_asgi(app, store)
    assert 'private-medical-note-DoNotExport' not in exported(store)


@pytest.mark.asyncio
@pytest.mark.parametrize('fragment', ['beginning', 'middle', 'unicode', 'overflow'])
async def test_partial_request_echoes_are_omitted_in_plain_and_json_errors(fragment):
    store = LogStore()
    private = 'private personal customer address for redaction'
    if fragment == 'unicode': private = '患者隐私资料请勿记录到日志正文中'
    if fragment == 'overflow': private = 'secret-document-' * 1000
    echo = private[:16] if fragment != 'middle' else private[9:27]
    async def app(scope, receive, send):
        record_parameters({'messages':[{'role':'user','content':private}]})
        failure = PlaygroundError('input rejected',400,'invalid_request_error')
        failure.diagnostic_raw = json.dumps({'error':{'code':400,'message':'invalid fragment: '+echo}})
        record_error(failure)
        await send({'type':'http.response.start','status':400})
        await send({'type':'http.response.body','body':b'{}'})
    await run_asgi(app, store)
    assert echo not in exported(store)
    assert request_detail(store)['error']['businessHttpStatus'] == 400


@pytest.mark.asyncio
async def test_secret_budget_exhaustion_hides_raw_error_instead_of_leaking_rotated_credentials():
    store = LogStore()
    async def app(scope, receive, send):
        for index in range(220): register_secrets(f'private-cookie-{index}')
        failure = PlaygroundError('upstream refused credentials',403,'upstream_permission_denied')
        failure.diagnostic_raw = 'Rejected private-cookie-219'
        record_error(failure)
        await send({'type':'http.response.start','status':403})
        await send({'type':'http.response.body','body':b'{}'})
    await run_asgi(app, store)
    assert 'private-cookie-219' not in exported(store)


@pytest.mark.asyncio
async def test_later_parameter_error_does_not_borrow_old_failed_account_http_status():
    store = LogStore()
    async def app(scope, receive, send):
        note_upstream('POST','https://example.test/ListModels',status=503)
        set_stage('parameters')
        record_error(PlaygroundError('unsupported model option',400,'invalid_request_error'))
        await send({'type':'http.response.start','status':400})
        await send({'type':'http.response.body','body':b'{}'})
    await run_asgi(app, store)
    failure = request_detail(store)['error']
    assert failure['type'] == 'parameters' and 'upstreamStatus' not in failure


def test_private_controller_details_are_isolated_from_periodic_snapshot_and_expire():
    from pgproxy.android_host import Controller
    from test_accounts import MemoryBridge, FakeClient
    controller = Controller(MemoryBridge(),FakeClient)
    def call(action, payload=None):
        return json.loads(controller.call(action,json.dumps(payload or {})))
    try:
        controller._log('session_required')
        item = call('snapshot')['result']['logs'][0]
        assert item['credentialIssue']
        detail = call('log_detail',{'id':item['id']})
        assert detail['ok'] and detail['result']['id'] == item['id']
        assert not call('log_detail',{'id':item['id'],'extra':1})['ok']
        assert not call('log_detail',{'id':None})['ok']
        for _ in range(101): controller._log('service_started')
        assert call('log_detail',{'id':item['id']})['error']['code'] == 'log_not_found'
    finally:
        controller.close()


@pytest.mark.asyncio
async def test_real_account_pool_catalog_partial_failure_remains_successful_and_correlated():
    from test_accounts import pool_fixture, add
    pool, _ = pool_fixture()
    bad = await add(pool,'Bad account private label')
    await add(pool,'Good account private label')
    async def missing():
        raise PlaygroundError('missing session file',503,'session_required')
    pool.client(pool.record(bad)).models = missing
    store = LogStore()
    app = create_app(pool,close_on_shutdown=False,request_logs=store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://unit.test') as api:
            response = await api.get('/v1/models',headers={'Authorization':'Bearer '+pool.settings.password})
    await pool.close()
    assert response.status_code == 200 and response.json()['data']
    result = request_detail(store)
    issue = next(item for item in details(store) if item['event'] == 'credential_issue')
    assert result['level'] == 'success' and issue['requestId'] == result['id']
    assert 'Bad account private label' not in exported(store)


@pytest.mark.asyncio
@pytest.mark.parametrize('after_headers', [False, True])
async def test_asgi_oserror_is_client_disconnect_and_does_not_invent_unsent_http_status(after_headers):
    store = LogStore()
    async def send(message):
        if not after_headers or message['type'] == 'http.response.body':
            raise OSError('socket transport closed')
    async def app(scope, receive, send):
        await send({'type':'http.response.start','status':200})
        await send({'type':'http.response.body','body':b'{}'})
    with pytest.raises(OSError):
        await run_asgi(app, store, send=send)
    result = request_detail(store)
    assert result['error']['type'] == 'client_disconnect'
    assert result['status'] == (200 if after_headers else None)
