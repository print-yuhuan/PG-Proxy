import asyncio
import base64
import hashlib
import json
import pytest
import httpx
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect
from pgproxy.main import create_app
from pgproxy.errors import PlaygroundError
from pgproxy.responses import generation_response
from conftest import frame


def auth(client):
    return {'Authorization':'Bearer '+client.settings.password}


def test_auth_and_missing_auth_do_not_contact_upstream(client_factory):
    c,requests=client_factory()
    with TestClient(create_app(c)) as api:
        assert api.get('/v1/models').status_code==401
        assert not requests
        password=c.settings.password
        for headers,query in [({'Authorization':'Bearer '+password},''),({'Authorization':'Basic '+base64.b64encode(('any:'+password).encode()).decode()},''),({'x-goog-api-key':password},''),({},'?key='+password)]:
            response=api.get('/v1/models'+query,headers=headers)
            assert response.status_code==200 and response.json()['data'][0]['id']=='test-chat'
        assert api.get('/health').json()['service']=='PG Proxy'
        assert api.options('/v1/chat/completions',headers={'Origin':'https://example.test','Access-Control-Request-Method':'POST'}).status_code==200
    assert c.closed and c.http.is_closed


def test_current_gecko_language_preserved_and_unused_metadata_removed(client_factory):
    from pgproxy.config import WAA_USER_AGENT

    client, requests = client_factory()
    client.session.load()
    metadata = {'userAgent': 'Legacy browser export', 'language': 'zh-CN,zh;q=0.9'}
    client.session.state['browserMetadata'] = metadata
    client.session.save()
    with TestClient(create_app(client)) as api:
        response = api.get('/v1/models', headers=auth(client))
        assert response.status_code == 200
        assert requests
        assert all(request.headers['User-Agent'] == WAA_USER_AGENT for request in requests)
        assert all(request.headers['Accept-Language'] == metadata['language'] for request in requests)
        assert client.session.state['browserMetadata'] == {'language':metadata['language']}


def test_generation_shapes_and_real_binding_contract(client_factory):
    c,requests=client_factory()
    with TestClient(create_app(c)) as api:
        body={'model':'test-chat','messages':[{'role':'system','content':'S'},{'role':'user','content':'hello'}]}
        response=api.post('/v1/chat/completions',headers=auth(c),json=body)
        assert response.status_code==200,response.text
        assert response.json()['choices'][0]['message']['content']=='OK'
        assert response.json()['choices'][0]['finish_reason']=='stop'
        generation=next(r for r in requests if r.url.path.endswith('/GenerateContent'))
        wire=json.loads(generation.content)
        assert generation.headers['x-aistudio-g1-tier']=='TIER1'
        assert generation.headers['x-aistudio-visit-id'].startswith('v1_')
        assert len(base64.b64decode(generation.headers['x-aistudio-visit-id'][3:]))==36
        assert wire[4]=='!UNIT_TEST_ONLY' # Synthetic contract, not acceptance evidence.
        assert c.waa.calls[-1][1]['digest']==hashlib.sha256(b'hello').hexdigest()
        assert c.waa.calls[-1][1]['prompt']=='hello'
        body['stream']=True;body['stream_options']={'include_usage':True}
        response=api.post('/v1/chat/completions',headers=auth(c),json=body)
        assert response.text.endswith('data: [DONE]\n\n')
        events=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
        assert len({e['id'] for e in events})==1
        assert events[0]['choices'][0]['delta']['role']=='assistant'
        assert events[-1]['choices']==[] and events[-1]['usage']['total_tokens']==6


@pytest.mark.parametrize('prefix',['/v1beta/models','/v1/models'])
def test_native_standard_paths(client_factory,prefix):
    c,_=client_factory()
    body={'contents':[{'role':'user','parts':[{'text':'hi'}]}]}
    with TestClient(create_app(c)) as api:
        path=prefix+'/test-chat:'
        r=api.post(path+'generateContent',headers=auth(c),json=body)
        assert r.status_code==200 and len(r.json()['candidates'][0]['content']['parts'])==2
        r=api.post(path+'streamGenerateContent?alt=sse',headers=auth(c),json=body)
        assert r.status_code==200 and 'data: ' in r.text
        r=api.post(path+'streamGenerateContent',headers=auth(c),json=body)
        assert r.status_code==200 and len(r.json())==2
        assert api.post(path+'delete',headers=auth(c),json=body).status_code==404
        assert api.post(prefix+'/test-chat/generateContent',headers=auth(c),json=body).status_code==404


@pytest.mark.parametrize('status',[401,403,429,500])
def test_initial_upstream_errors_keep_http_status(client_factory,status):
    c,_=client_factory(generate_status=status)
    with TestClient(create_app(c)) as api:
        r=api.post('/v1/chat/completions',headers=auth(c),json={'model':'test-chat','messages':[{'role':'user','content':'hello'}],'stream':True})
        assert r.status_code==(502 if status==500 else status)
        assert 'UNIT_TEST' not in r.text and 'synthetic' not in r.text
        if status==429:
            assert r.headers['retry-after']=='25'
            assert c.cooldown_until>0


def test_midstream_truncation_error_without_success_done(client_factory):
    c,_=client_factory(generate_body=json.dumps([[frame('partial',None)]]).encode())
    with TestClient(create_app(c)) as api:
        r=api.post('/v1/chat/completions',headers=auth(c),json={'model':'test-chat','messages':[{'role':'user','content':'hello'}],'stream':True})
        assert r.status_code==200 and 'stream_interrupted' in r.text
        assert '[DONE]' not in r.text
        assert not c.lock.locked()


def test_bad_requests_model_and_json(client_factory):
    c,_=client_factory()
    with TestClient(create_app(c)) as api:
        assert api.post('/v1/chat/completions',headers=auth(c),content='{').status_code==400
        assert api.post('/v1/chat/completions',headers=auth(c),json={'model':'test-chat','messages':[]}).status_code==400
        r=api.post('/v1/chat/completions',headers=auth(c),json={'model':'missing','messages':[{'role':'user','content':'hello'}]})
        assert r.status_code==404


@pytest.mark.asyncio
async def test_closing_generation_releases_account(client_factory):
    c,_=client_factory()
    stream=c.generate('test-chat',{'contents':[{'parts':[{'text':'hello'}]}]})
    await anext(stream)
    assert c.lock.locked()
    await stream.aclose()
    assert not c.lock.locked()
    await c.close()
    assert c.http.is_closed


@pytest.mark.asyncio
async def test_disconnect_before_body_closes_prepared_source(client_factory):
    c,_=client_factory()
    response=await generation_response(c,'test-chat',{'contents':[{'parts':[{'text':'hello'}]}]},streaming=True,openai=True)
    async def send(msg): raise OSError('client disconnected')
    async def receive(): return {'type':'http.disconnect'}
    try:
        await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
    except (OSError,ClientDisconnect):
        pass
    assert not c.lock.locked()
    await c.close()


@pytest.mark.parametrize('changes',[
    {'n':True},{'n':1.0},{'stream':'true'},{'model':'models/'},{'model':[]},
    {'tools':[{'type':{}}]},{'stream_options':[]},{'stream_options':{'include_usage':1}},
    {'temperature':10**400},
])
def test_invalid_openai_types_are_controlled_without_generation(client_factory,changes):
    client,requests=client_factory()
    body={'model':'test-chat','messages':[{'role':'user','content':'private-prompt'}],**changes}
    with TestClient(create_app(client)) as api:
        response=api.post('/v1/chat/completions',headers=auth(client),json=body)
    assert response.status_code==400 and 'private-prompt' not in response.text
    assert not any(r.url.path.endswith('/GenerateContent') for r in requests)


@pytest.mark.parametrize('changes',[
    {'safetySettings':[{'category':[],'threshold':'OFF'}]},
    {'safetySettings':[{'category':'HARM_CATEGORY_HARASSMENT','threshold':{}}]},
    {'generationConfig':{'candidateCount':True}},
    {'generationConfig':{'topK':10**400}},
])
def test_invalid_native_types_are_controlled_without_generation(client_factory,changes):
    client,requests=client_factory()
    body={'contents':[{'parts':[{'text':'private-prompt'}]}],**changes}
    with TestClient(create_app(client)) as api:
        response=api.post('/v1beta/models/test-chat:generateContent',headers=auth(client),json=body)
    assert response.status_code==400 and 'private-prompt' not in response.text
    assert not any(r.url.path.endswith('/GenerateContent') for r in requests)


def test_error_messages_never_reflect_upstream_or_internal_exception_text(client_factory):
    client,_=client_factory()
    async def broken_models(): raise RuntimeError('Cookie=secret-token')
    client.models=broken_models
    with TestClient(create_app(client),raise_server_exceptions=False) as api:
        response=api.get('/v1/models',headers=auth(client))
        assert response.status_code==500 and 'secret-token' not in response.text
    error=PlaygroundError('Authorization=secret-token',403,'upstream_permission_denied')
    assert 'secret-token' not in error.body()['error']['message']


@pytest.mark.asyncio
async def test_http_client_closes_when_worker_cleanup_fails(client_factory):
    client,_=client_factory()
    async def fail(): raise RuntimeError('worker stop failed')
    client.waa.close=fail
    with pytest.raises(RuntimeError): await client.close()
    assert client.http.is_closed


@pytest.mark.parametrize('method,path,status,message',[
    ('GET','/missing',404,'接口路径不存在。'),
    ('POST','/health',405,'此接口不支持该请求方法。'),
    ('GET','/v1/models',401,'接口密钥不正确，请检查客户端认证设置。'),
])
def test_framework_http_errors_use_safe_chinese_structure(client_factory,method,path,status,message):
    client,requests=client_factory()
    with TestClient(create_app(client)) as api:
        response=api.request(method,path)
    assert response.status_code==status
    assert response.json()=={'error':{'message':message,'type':'authentication_error' if status==401 else 'invalid_request_error','code':status}}
    assert not requests
    if status==401:
        assert response.headers['www-authenticate']=='Basic'
    elif status==405:
        assert 'GET' in response.headers['allow']
