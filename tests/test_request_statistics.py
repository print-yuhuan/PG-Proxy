import asyncio
from types import SimpleNamespace

import pytest
import httpx
from fastapi.testclient import TestClient

from pgproxy.main import create_app
from pgproxy.request_statistics import RequestStatistics, RequestStatisticsMiddleware, mark_request_failed
from conftest import frame, model_row
import json


def test_api_outcomes_count_once_and_ignore_health_options_and_removed_paths(client_factory):
    statistics=RequestStatistics()
    client,_=client_factory()
    headers={'Authorization':'Bearer '+client.settings.password}
    with TestClient(create_app(client,request_statistics=statistics)) as api:
        api.get('/health')
        api.options('/v1/models')
        api.post('/v1/models/test-chat/generateContent')
        assert statistics.snapshot()=={'requestsSucceeded':0,'requestsFailed':0}
        assert api.get('/v1/models',headers=headers).status_code==200
        assert api.get('/v1/models').status_code==401
        body={'model':'test-chat','messages':[{'role':'user','content':'hello'}]}
        assert api.post('/v1/chat/completions',headers=headers,json=body).status_code==200
        assert api.post('/v1/chat/completions',headers=headers,json={**body,'stream':True}).status_code==200
        assert api.post('/v1/chat/completions',headers=headers,json=[]).status_code==400
    assert statistics.snapshot()=={'requestsSucceeded':3,'requestsFailed':2}
    # Recreating HTTP routing with the same Controller-owned object retains totals.
    client,_=client_factory(generate_body=json.dumps([[frame('partial',None)]]).encode())
    with TestClient(create_app(client,request_statistics=statistics)) as api:
        response=api.post('/v1/chat/completions',headers=headers,json={**body,'stream':True})
        assert response.status_code==200 and 'stream_interrupted' in response.text
    assert statistics.snapshot()=={'requestsSucceeded':3,'requestsFailed':3}


def test_root_catalog_documents_details_counting_and_generation_aliases_without_counting_itself(client_factory):
    statistics = RequestStatistics()
    client, sent = client_factory()
    with TestClient(create_app(client, request_statistics=statistics)) as api:
        root = api.get('/').json()
        expected = {'/v1/models', '/v1/models/{model}', '/v1/chat/completions',
                    '/v1beta/models', '/v1beta/models/{model}'}
        expected.update(prefix + '/{model}:' + action
                        for prefix in ('/v1/models', '/v1beta/models')
                        for action in ('countTokens', 'generateContent', 'streamGenerateContent'))
        assert set(root['endpoints']) == expected
        assert len(root['endpoints']) == len(expected)
        api.get('/health')
        api.get('/openapi.json')
        api.get('/internal/models')
        api.get('/internal/v1/models/test-chat')
        api.post('/v1/models/test-chat/generateContent')
        api.post('/v1beta/models/test-chat:delete', json={})
        for prefix in ('/v1/models', '/v1beta/models'):
            for path in (prefix, prefix + '/test-chat', prefix + '/test-chat:countTokens'):
                api.options(path, headers={'Origin': 'https://example.test', 'Access-Control-Request-Method': 'POST'})
        assert statistics.snapshot() == {'requestsSucceeded': 0, 'requestsFailed': 0}
        assert not sent


@pytest.mark.parametrize('prefix', ['/v1/models', '/v1beta/models'])
def test_actual_model_detail_asgi_success_auth_and_not_found_count_once(client_factory, prefix):
    statistics = RequestStatistics()
    client, _ = client_factory()
    headers = {'Authorization': 'Bearer ' + client.settings.password}
    with TestClient(create_app(client, request_statistics=statistics)) as api:
        assert api.get(prefix + '/test-chat', headers=headers).status_code == 200
        assert statistics.snapshot() == {'requestsSucceeded': 1, 'requestsFailed': 0}
        assert api.get(prefix + '/test-chat').status_code == 401
        assert statistics.snapshot() == {'requestsSucceeded': 1, 'requestsFailed': 1}
        assert api.get(prefix + '/missing-model', headers=headers).status_code == 404
        assert statistics.snapshot() == {'requestsSucceeded': 1, 'requestsFailed': 2}


@pytest.mark.parametrize('prefix', ['/v1/models', '/v1beta/models'])
def test_actual_count_tokens_asgi_success_auth_validation_and_model_failure_count_once(client_factory, prefix):
    row = model_row()
    row[7].append('countTokens')
    def handler(request):
        if request.url.path.endswith('/ListModels'):
            return httpx.Response(200, json=[[row]])
        if request.url.path.endswith('/CountTokens'):
            return httpx.Response(200, json=[7])
    statistics = RequestStatistics()
    client, sent = client_factory(extra_handler=handler)
    headers = {'Authorization': 'Bearer ' + client.settings.password}
    body = {'contents': [{'parts': [{'text': 'synthetic text'}]}]}
    path = prefix + '/test-chat:countTokens'
    with TestClient(create_app(client, request_statistics=statistics)) as api:
        assert api.post(path, json=body).status_code == 401
        assert not sent
        assert statistics.snapshot() == {'requestsSucceeded': 0, 'requestsFailed': 1}
        response = api.post(path, headers=headers, json=body)
        assert response.status_code == 200 and response.json() == {'totalTokens': 7}
        assert statistics.snapshot() == {'requestsSucceeded': 1, 'requestsFailed': 1}
        assert api.post(path, headers=headers, json={}).status_code == 400
        assert statistics.snapshot() == {'requestsSucceeded': 1, 'requestsFailed': 2}
        assert api.post(prefix + '/missing:countTokens', headers=headers, json=body).status_code == 404
        assert statistics.snapshot() == {'requestsSucceeded': 1, 'requestsFailed': 3}
    assert len([request for request in sent if request.url.path.endswith('/CountTokens')]) == 1
    assert not any(request.url.path.endswith('/GenerateContent') for request in sent)


@pytest.mark.parametrize('prefix', ['/v1/models', '/v1beta/models'])
@pytest.mark.parametrize('upstream_status,upstream_body,expected', [(403, {}, 403), (429, {}, 429), (200, [None], 502)])
def test_count_tokens_upstream_and_protocol_asgi_failures_are_not_successes(client_factory, prefix, upstream_status, upstream_body, expected):
    row = model_row()
    row[7].append('countTokens')
    def handler(request):
        if request.url.path.endswith('/ListModels'):
            return httpx.Response(200, json=[[row]])
        if request.url.path.endswith('/CountTokens'):
            return httpx.Response(upstream_status, json=upstream_body)
    statistics = RequestStatistics()
    client, _ = client_factory(extra_handler=handler)
    with TestClient(create_app(client, request_statistics=statistics)) as api:
        response = api.post(prefix + '/test-chat:countTokens',
                            headers={'Authorization': 'Bearer ' + client.settings.password},
                            json={'contents': [{'parts': [{'text': 'synthetic text'}]}]})
        assert response.status_code == expected
        assert statistics.snapshot() == {'requestsSucceeded': 0, 'requestsFailed': 1}


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['disconnect','send','cancel','stream'])
async def test_observed_interruptions_are_failures_even_with_http_200(failure):
    statistics=RequestStatistics()
    scope={'type':'http','method':'POST','path':'/v1beta/models/test:streamGenerateContent'}
    async def receive(): return {'type':'http.disconnect'}
    async def send(message):
        if failure=='send': raise OSError('socket closed')
    async def app(scope,receive,send):
        await send({'type':'http.response.start','status':200})
        if failure=='disconnect': await receive()
        elif failure=='cancel': raise asyncio.CancelledError()
        elif failure=='stream': mark_request_failed(SimpleNamespace(scope=scope))
        await send({'type':'http.response.body','body':b'OK'})
    middleware=RequestStatisticsMiddleware(app,statistics)
    if failure in ('send','cancel'):
        with pytest.raises(OSError if failure=='send' else asyncio.CancelledError):
            await middleware(scope,receive,send)
    else:
        await middleware(scope,receive,send)
    assert statistics.snapshot()=={'requestsSucceeded':0,'requestsFailed':1}
