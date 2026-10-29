import json
import socket

import httpx
import pytest

from pgproxy.android_host import Controller
from pgproxy.config import VERSION, Settings
from test_accounts import MemoryBridge, FakeClient, credentials


def call(controller, action, **payload):
    return json.loads(controller.call(action,json.dumps(payload)))


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def test_controller_private_management_listener_restart_auth_and_shutdown():
    bridge = MemoryBridge()
    controller = Controller(bridge,FakeClient)
    try:
        port = free_port()
        assert call(controller,'configure',port=port,apiKey='unit-test-local-api-key')['ok']
        imported = call(controller,'import_account',label='Test',session=credentials('never-in-snapshot'))
        assert imported['ok']
        account_id = imported['result']['accountId']
        assert 'never-in-snapshot' not in json.dumps(imported)
        assert call(controller,'check_account',id=account_id)['result']['check']['verified']
        assert call(controller,'start')['result']['running']
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as client:
            assert client.get('/health').status_code==200
            assert client.get('/v1/models').status_code==401
            assert client.get('/v1/models',headers={'Authorization':'Bearer unit-test-local-api-key'}).status_code==200
            # Internal app management has no public API, including over LAN.
            assert client.post('/accounts',json={'action':'delete'}).status_code==404
            assert client.get('/health').json()=={'status':'running','service':'PG Proxy','version':VERSION}
        changed = call(controller,'configure',apiKey='second-test-local-api-key')
        assert changed['ok'] and changed['result']['running']
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False) as client:
            assert client.get('/v1/models',headers={'Authorization':'Bearer unit-test-local-api-key'}).status_code==401
            assert client.get('/v1/models',headers={'x-goog-api-key':'second-test-local-api-key'}).status_code==200
        assert call(controller,'stop')['result']['running'] is False
        assert not controller.pool.clients
        assert call(controller,'delete_account',id=account_id)['ok']
        assert account_id in bridge.deleted
    finally:
        controller.close()
    assert not controller.thread.is_alive()


@pytest.mark.parametrize('payload',[{'port':80},{'port':True},{'lan':'yes'},{'apiKey':'12'},
                                    {'apiKey':''},{'apiKey':'a b'},{'apiKey':'a\tb'},{'apiKey':'a\nb'},
                                    {'apiKey':'a\x7fb'},{'apiKey':'密钥abc'},{'apiKey':'a'*257},{'apiKey':123},
                                    {'timeout':float('nan')},{'queueTimeout':0},{'unexpected':1},
                                    *({'keepAwake':value} for value in (None,0,1,'true','false',[],{}))])
def test_configuration_validation_preserves_saved_state(payload):
    bridge=MemoryBridge()
    controller=Controller(bridge,FakeClient)
    try:
        before=json.loads(bridge.state)['config']
        assert not json.loads(controller.call('configure',json.dumps(payload)))['ok']
        assert json.loads(bridge.state)['config']==before
    finally:
        controller.close()


@pytest.mark.parametrize('stored_state',[
    '', '{}', 'null',
    json.dumps({'schema':1,'accounts':[]}),
    json.dumps({'schema':1,'config':{},'accounts':[]}),
])
def test_new_or_missing_configuration_uses_requested_defaults(stored_state):
    bridge=MemoryBridge()
    bridge.state=stored_state
    controller=Controller(bridge,FakeClient)
    try:
        snapshot=call(controller,'snapshot')['result']
        assert snapshot['port']==8888 and snapshot['apiKey']=='123'
        assert snapshot['keepAwake'] is False
        assert not snapshot['running']
        assert controller.pool.settings.password=='123'
        assert json.loads(bridge.state)['config']['apiKey']=='123'
    finally:
        controller.close()


def test_saved_configuration_is_preserved_across_restart():
    bridge=MemoryBridge()
    saved={'port':8080,'apiKey':'existing-user-key','lan':True,'timeout':90,'queueTimeout':17}
    bridge.state=json.dumps({'schema':1,'accounts':[],'config':saved})
    for _ in range(2):
        controller=Controller(bridge,FakeClient)
        try:
            snapshot=call(controller,'snapshot')['result']
            assert all(snapshot[key]==value for key,value in saved.items())
            assert controller.pool.settings.password==saved['apiKey']
            assert snapshot['keepAwake'] is False
            assert json.loads(bridge.state)['config']=={**saved,'keepAwake':False}
        finally:
            controller.close()


@pytest.mark.parametrize('saved,port,key',[
    ({'port':9001},9001,'123'),
    ({'apiKey':'saved-user-key'},8888,'saved-user-key'),
])
def test_missing_configuration_fields_use_defaults_without_replacing_saved_values(saved,port,key):
    bridge=MemoryBridge()
    bridge.state=json.dumps({'schema':1,'accounts':[],'config':saved})
    controller=Controller(bridge,FakeClient)
    try:
        snapshot=call(controller,'snapshot')['result']
        assert snapshot['port']==port and snapshot['apiKey']==key
        assert all(snapshot[name]==value for name,value in saved.items())
    finally:
        controller.close()


@pytest.mark.parametrize('key',['123','!~A','a'*256])
def test_configuration_accepts_supported_key_lengths_and_characters(key):
    bridge=MemoryBridge()
    controller=Controller(bridge,FakeClient)
    try:
        result=call(controller,'configure',apiKey=key)
        assert result['ok']
        assert result['result']['apiKey']==key
        assert controller.pool.settings.password==key
        assert json.loads(bridge.state)['config']['apiKey']==key
    finally:
        controller.close()


def test_settings_default_and_explicit_password():
    assert Settings().password=='123'
    assert Settings(password='explicit-user-key').password=='explicit-user-key'


def test_keep_awake_persists_without_restarting_a_running_listener():
    bridge=MemoryBridge()
    controller=Controller(bridge,FakeClient)
    try:
        assert call(controller,'configure',port=free_port())['ok']
        assert call(controller,'start')['ok']
        server, task, listener = controller.server, controller.server_task, controller.listener
        result=call(controller,'configure',keepAwake=True)
        assert result['ok'] and result['result']['running']
        assert result['result']['keepAwake'] is True
        assert (controller.server,controller.server_task,controller.listener)==(server,task,listener)
        assert not task.done()
        assert json.loads(bridge.state)['config']['keepAwake'] is True
    finally:
        controller.close()
    restarted=Controller(bridge,FakeClient)
    try:
        snapshot=call(restarted,'snapshot')['result']
        assert snapshot['keepAwake'] is True and snapshot['running'] is False
        disabled=call(restarted,'configure',keepAwake=False)
        assert disabled['ok'] and disabled['result']['keepAwake'] is False
        assert json.loads(bridge.state)['config']['keepAwake'] is False
    finally:
        restarted.close()


def test_keep_awake_save_failure_preserves_previous_configuration_and_listener():
    bridge=MemoryBridge()
    controller=Controller(bridge,FakeClient)
    original_save=bridge.saveState
    try:
        assert call(controller,'configure',port=free_port())['ok']
        assert call(controller,'start')['ok']
        before=json.loads(bridge.state)['config']
        listener=controller.listener
        def failed_save(_):
            raise OSError('simulated storage failure')
        bridge.saveState=failed_save
        result=call(controller,'configure',keepAwake=True)
        assert not result['ok'] and result['error']['code']=='service_error'
        assert result['result']['running'] and result['result']['keepAwake'] is False
        assert controller.listener is listener
        assert controller.state['config']==before
        assert json.loads(bridge.state)['config']==before
    finally:
        bridge.saveState=original_save
        controller.close()


@pytest.mark.parametrize('value',[1,'true',None])
def test_saved_keep_awake_cannot_enable_through_truthy_non_boolean_values(value):
    bridge=MemoryBridge()
    bridge.state=json.dumps({'schema':1,'accounts':[],'config':{'keepAwake':value}})
    before=bridge.state
    with pytest.raises(ValueError,match='background configuration'):
        Controller(bridge,FakeClient)
    assert bridge.state==before


def test_busy_port_reports_failure_without_claiming_started():
    bridge=MemoryBridge()
    controller=Controller(bridge,FakeClient)
    try:
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0))
            occupied.listen()
            call(controller,'configure',port=occupied.getsockname()[1])
            result=call(controller,'start')
            assert not result['ok'] and result['error']['code']=='port_unavailable'
            assert not result['result']['running']
    finally:
        controller.close()


def test_running_configuration_rolls_back_when_new_port_cannot_start():
    bridge=MemoryBridge()
    bridge.state='{}'  # Also tolerate an empty native preference object on first launch.
    controller=Controller(bridge,FakeClient)
    try:
        original=free_port()
        call(controller,'configure',port=original)
        assert call(controller,'start')['ok']
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0))
            occupied.listen()
            result=call(controller,'configure',port=occupied.getsockname()[1])
            assert not result['ok'] and result['error']['code']=='port_unavailable'
            assert result['result']['running'] and result['result']['port']==original
        with httpx.Client(trust_env=False) as client:
            assert client.get(f'http://127.0.0.1:{original}/health').status_code==200
        assert json.loads(bridge.state)['config']['port']==original
    finally:
        controller.close()


def test_deleted_id_tombstone_rejects_late_import_across_restart():
    bridge=MemoryBridge()
    controller=Controller(bridge,FakeClient)
    imported=call(controller,'import_account',label='A',session=credentials('a'))
    account_id=imported['result']['accountId']
    assert call(controller,'delete_account',id=account_id)['ok']
    for restarted in (False,True):
        if restarted:
            controller.close()
            controller=Controller(bridge,FakeClient)
        late=call(controller,'import_account',id=account_id,label='Late',session=credentials('late'))
        assert not late['ok'] and late['error']['code']=='account_deleted'
        assert account_id not in bridge.accounts
        assert not late['result']['accounts']
    fresh=call(controller,'import_account',label='New account',session=credentials('new'))
    assert fresh['ok'] and fresh['result']['accountId']!=account_id
    controller.close()


def test_restart_erases_crash_leftover_before_exposing_account_state():
    bridge=MemoryBridge()
    original=Controller(bridge,FakeClient)
    account_id=call(original,'import_account',label='A',session=credentials('residual'))['result']['accountId']
    original.close()
    state=json.loads(bridge.state)
    # Emulate death after persisting deletion intent, before removing its encrypted file.
    state['deletedAccountIds']=[account_id]
    state['accounts'][0].update(enabled=True,status='verified')
    bridge.state=json.dumps(state)
    restored=Controller(bridge,FakeClient)
    try:
        assert account_id not in bridge.accounts
        assert not call(restored,'snapshot')['result']['accounts']
        assert account_id in json.loads(bridge.state)['deletedAccountIds']
        assert not call(restored,'import_account',id=account_id,session=credentials('late'))['ok']
    finally:
        restored.close()


def test_restart_with_failed_deletion_fails_closed_and_next_restart_retries():
    from pgproxy.errors import PlaygroundError
    bridge=MemoryBridge()
    original=Controller(bridge,FakeClient)
    account_id=call(original,'import_account',session=credentials('residual'))['result']['accountId']
    original.close()
    state=json.loads(bridge.state)
    state['deletedAccountIds']=[account_id]
    bridge.state=json.dumps(state)
    erase=bridge.deleteAccount
    def failed_erase(key): raise OSError('simulated storage failure')
    bridge.deleteAccount=failed_erase
    with pytest.raises(PlaygroundError) as failure:
        Controller(bridge,FakeClient)
    assert failure.value.code=='account_deletion_failed'
    assert account_id in bridge.accounts
    assert json.loads(bridge.state)['accounts'][0]['enabled'] is False
    bridge.deleteAccount=erase
    restored=Controller(bridge,FakeClient)
    try:
        assert not call(restored,'snapshot')['result']['accounts']
        assert account_id not in bridge.accounts
    finally:
        restored.close()
