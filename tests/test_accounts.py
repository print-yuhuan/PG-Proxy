import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from pgproxy.accounts import AccountPool
from pgproxy.errors import PlaygroundError


class MemoryBridge:
    """Test-only in-memory storage. Android production storage is Keystore-backed."""
    def __init__(self):
        self.state = ''
        self.accounts = {}
        self.deleted = []
    def loadState(self): return self.state
    def saveState(self, state): self.state = state
    def readAccount(self, key): return self.accounts.get(key, '')
    def writeAccount(self, key, data): self.accounts[key] = data
    def deleteAccount(self, key):
        self.accounts.pop(key,None)
        self.deleted.append(key)


def credentials(value='test-only'):
    return {'cookies':[{'name':name,'value':value,'domain':'.google.com','path':'/',
                       'secure':True,'httpOnly':True,'expires':-1}
                      for name in ('SAPISID','__Secure-1PAPISID','__Secure-3PAPISID','__Secure-1PSID')],
            'origins':[]}


class FakeClient:
    def __init__(self, record, settings):
        self.record, self.settings = record,settings
        self.closed = False
        self.generation_verified = False
        self.waa_at = 0
        self.waa = SimpleNamespace(process=None,close=self.close_worker)
        self.catalog = {'test-chat':{'name':'test-chat'}}
        self.failure = None
        self.calls = []
        self.active = False
    async def close_worker(self): self.waa.process = None
    async def models(self): return self.catalog
    async def generate(self,model,request):
        self.calls.append(model)
        self.active = True
        try:
            if self.failure: raise self.failure
            yield {'candidates':[{'content':{'parts':[{'text':self.record['id']}]}}]}
            yield {'candidates':[{'finishReason':'STOP'}]}
            self.generation_verified = True
        finally:
            self.active = False
    async def close(self):
        await self.close_worker()
        self.closed = True


def pool_fixture():
    bridge = MemoryBridge()
    state = {'config':{'apiKey':'unit-test-api-key','timeout':30,'queueTimeout':1},'accounts':[]}
    pool = AccountPool(bridge,state,lambda:bridge.saveState(json.dumps(state)),FakeClient)
    return pool,bridge


async def add(pool, label='A'):
    return await pool.import_account({'label':label,'session':credentials(label)})


async def consume(pool,model='test-chat'):
    return [frame async for frame in pool.generate(model,{})]


@pytest.mark.asyncio
async def test_round_robin_isolated_credentials_and_model_eligibility():
    pool,bridge = pool_fixture()
    a,b = await add(pool,'A'),await add(pool,'B')
    assert bridge.accounts[a] != bridge.accounts[b]
    for expected in (a,b,a,b):
        frames = await consume(pool)
        assert frames[0]['candidates'][0]['content']['parts'][0]['text'] == expected
    pool.client(pool.record(a)).catalog = {'other':{}}
    assert (await consume(pool))[0]['candidates'][0]['content']['parts'][0]['text'] == b
    assert set(await pool.models()) == {'other','test-chat'}
    await pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('release_by', ['complete', 'disconnect'])
async def test_second_generation_waits_then_uses_next_account(release_by):
    pool,_ = pool_fixture()
    a,b = await add(pool,'A'),await add(pool,'B')
    first,second = pool.client(pool.record(a)),pool.client(pool.record(b))
    waiting = asyncio.Event()

    class ObservedLock(asyncio.Lock):
        async def acquire(self):
            if self.locked():
                waiting.set()
            return await super().acquire()

    pool.gate = ObservedLock()
    pool.settings.queue_timeout = 5
    stream = pool.generate('test-chat',{})
    await anext(stream)
    request = asyncio.create_task(consume(pool))
    try:
        # Observe actual admission contention instead of relying on a sleep.
        await asyncio.wait_for(waiting.wait(),1)
        assert not request.done() and not second.calls
        assert pool.active == a and pool.cursor == 1 and first.active
        if release_by == 'complete':
            assert [frame async for frame in stream]
        else:
            await stream.aclose()
        frames = await asyncio.wait_for(request,1)
        assert frames[0]['candidates'][0]['content']['parts'][0]['text'] == b
        assert first.calls == second.calls == ['test-chat']
        assert not first.active and not second.active and not pool.gate.locked()
    finally:
        request.cancel()
        await asyncio.gather(request,return_exceptions=True)
        await stream.aclose()
        await pool.close()


@pytest.mark.asyncio
async def test_generation_queue_timeout_does_not_submit_or_advance_rotation():
    pool,_ = pool_fixture()
    a,b = await add(pool,'A'),await add(pool,'B')
    first,second = pool.client(pool.record(a)),pool.client(pool.record(b))
    stream = pool.generate('test-chat',{})
    await anext(stream)
    pool.settings.queue_timeout = .03
    try:
        with pytest.raises(PlaygroundError) as error:
            await consume(pool)
        assert (error.value.status,error.value.code,error.value.retry_after) == (429,'account_busy','5')
        assert pool.active == a and pool.gate.locked() and pool.cursor == 1
        assert first.calls == ['test-chat'] and not second.calls
        await stream.aclose()
        # The expired request is gone; only this new request may use B.
        assert not second.calls and pool.cursor == 1
        frames = await consume(pool)
        assert frames[0]['candidates'][0]['content']['parts'][0]['text'] == b
        assert first.calls == second.calls == ['test-chat']
    finally:
        await stream.aclose()
        await pool.close()


@pytest.mark.asyncio
async def test_disable_during_stream_pins_account_and_delete_waits_for_release():
    pool,bridge = pool_fixture()
    a,b = await add(pool,'A'),await add(pool,'B')
    stream = pool.generate('test-chat',{})
    await anext(stream)
    client = pool.client(pool.record(a))
    await pool.set_enabled(a,False)
    assert pool.active == a and client.active
    deletion = asyncio.create_task(pool.delete(a))
    await asyncio.sleep(.02)
    assert not deletion.done() and a not in bridge.deleted
    assert a in json.loads(bridge.state)['deletedAccountIds']
    with pytest.raises(PlaygroundError) as error:
        await pool.import_account({'id':a,'session':credentials('late')})
    assert error.value.code=='account_deleted'
    await stream.aclose()
    await deletion
    assert client.closed and not client.active and not pool.gate.locked()
    assert a not in bridge.accounts and pool.record(b)['enabled']
    assert (await consume(pool))[0]['candidates'][0]['content']['parts'][0]['text']==b
    await pool.close()


@pytest.mark.asyncio
async def test_submitted_generation_is_not_retried_on_another_account():
    pool,_ = pool_fixture()
    a,b = await add(pool,'A'),await add(pool,'B')
    first,second = pool.client(pool.record(a)),pool.client(pool.record(b))
    first.failure = PlaygroundError('Quota',429,'rate_limit','15')
    with pytest.raises(PlaygroundError,match='Quota'):
        await consume(pool)
    assert first.calls == ['test-chat'] and not second.calls
    assert pool.record(a)['cooldownUntil'] > time.time()+10
    await consume(pool)
    assert second.calls == ['test-chat']
    await pool.close()


@pytest.mark.asyncio
async def test_403_is_validation_error_not_cookie_expiry_and_probe_recovers():
    pool,_ = pool_fixture()
    account_id = await add(pool)
    client = pool.client(pool.record(account_id))
    client.failure = PlaygroundError('Denied',403,'upstream_permission_denied')
    with pytest.raises(PlaygroundError): await consume(pool)
    assert pool.record(account_id)['status']=='validation_error'
    client.failure = None
    result = await pool.check(account_id)
    assert result['verified'] and pool.record(account_id)['status']=='verified'
    assert client.calls == ['test-chat','test-chat']
    await pool.close()


@pytest.mark.asyncio
async def test_catalog_alone_never_marks_generation_verified():
    pool,_ = pool_fixture()
    account_id = await add(pool)
    await pool.models()
    assert pool.record(account_id)['status']=='unchecked'
    await pool.set_enabled(account_id,False)
    with pytest.raises(PlaygroundError) as error:
        await consume(pool)
    assert error.value.code=='no_available_account'
    # User may explicitly test a disabled account without enabling routing.
    assert (await pool.check(account_id))['verified']
    assert not pool.record(account_id)['enabled']
    await pool.close()


@pytest.mark.asyncio
async def test_reimport_replaces_one_client_and_invalid_data_never_persists():
    pool,bridge = pool_fixture()
    a,b = await add(pool,'A'),await add(pool,'B')
    old = pool.client(pool.record(a))
    await pool.import_account({'id':a,'label':'A2','session':credentials('new')})
    assert old.closed and 'new' in bridge.accounts[a] and 'new' not in bridge.accounts[b]
    before = dict(bridge.accounts)
    for data in ({}, {'cookies':[]}, {'cookies':[{'name':'SAPISID','value':'bad','domain':'evil.test'}]}):
        with pytest.raises(PlaygroundError):
            await pool.import_account({'session':data})
    assert bridge.accounts == before
    await pool.close()


@pytest.mark.asyncio
async def test_pool_disconnect_closes_pinned_client_and_releases_service_queue():
    from pgproxy.responses import generation_response
    from starlette.requests import ClientDisconnect
    pool,_=pool_fixture()
    account_id=await add(pool)
    response=await generation_response(pool,'test-chat',{},streaming=True,openai=True)
    assert pool.gate.locked() and pool.active==account_id
    async def send(message): raise OSError('disconnected')
    async def receive(): return {'type':'http.disconnect'}
    try:
        await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
    except (OSError,ClientDisconnect):
        pass
    assert not pool.gate.locked() and pool.active is None
    assert not pool.client(pool.record(account_id)).active
    await consume(pool)
    await pool.close()


@pytest.mark.asyncio
async def test_delete_queue_timeout_blocks_every_entrypoint_and_finishes_after_stream():
    pool,bridge=pool_fixture()
    a,b=await add(pool,'A'),await add(pool,'B')
    stream=pool.generate('test-chat',{})
    await anext(stream)
    record=pool.record(a)
    client=pool.clients[a]
    pool.settings.queue_timeout=.03
    with pytest.raises(PlaygroundError) as pending:
        await pool.delete(a)
    assert pending.value.code=='account_deletion_pending'
    assert a in bridge.accounts and record['status']=='deleting' and not record['enabled']
    assert [r['id'] for r in pool.eligible()]==[b]
    for operation in (pool.set_enabled(a,True),pool.set_enabled(a,False),pool.check(a)):
        with pytest.raises(PlaygroundError) as rejected:
            await operation
        assert rejected.value.code=='account_deleted'
    # Completing the previously admitted generation must not restore verified status.
    assert [frame async for frame in stream]
    await pool.deletions[a]
    assert record['status']=='deleting' and not record['enabled']
    assert a not in bridge.accounts and client.closed
    assert [r['id'] for r in pool.state['accounts']]==[b]
    assert (await consume(pool))[0]['candidates'][0]['content']['parts'][0]['text']==b
    await pool.delete(a)  # Browser-journal replay after completion is harmless.
    await pool.close()


@pytest.mark.asyncio
async def test_cancelled_delete_caller_does_not_cancel_durable_cleanup():
    pool,bridge=pool_fixture()
    account_id=await add(pool)
    stream=pool.generate('test-chat',{})
    await anext(stream)
    request=asyncio.create_task(pool.delete(account_id))
    await asyncio.sleep(.02)
    request.cancel()
    with pytest.raises(asyncio.CancelledError): await request
    assert account_id in pool.state['deletedAccountIds']
    assert not pool.deletions[account_id].cancelled()
    await stream.aclose()
    await pool.deletions[account_id]
    assert account_id not in bridge.accounts and not pool.state['accounts']
    await pool.close()


@pytest.mark.asyncio
async def test_storage_erase_failure_keeps_tombstone_and_retry_completes():
    pool,bridge=pool_fixture()
    account_id=await add(pool)
    erase=bridge.deleteAccount
    def failed_erase(key): raise OSError('simulated storage failure')
    bridge.deleteAccount=failed_erase
    with pytest.raises(PlaygroundError) as failure:
        await pool.delete(account_id)
    assert failure.value.code=='account_deletion_failed'
    assert account_id in bridge.accounts and not pool.eligible()
    assert pool.record(account_id)['status']=='deleting'
    bridge.deleteAccount=erase
    await pool.delete(account_id)
    assert account_id not in bridge.accounts and not pool.state['accounts']
    await pool.close()


@pytest.mark.asyncio
async def test_check_queued_before_deletion_is_rejected_after_acquiring_gate():
    pool,bridge=pool_fixture()
    account_id=await add(pool)
    await pool.gate.acquire()
    checking=asyncio.create_task(pool.check(account_id))
    await asyncio.sleep(.01)
    deleting=asyncio.create_task(pool.delete(account_id))
    await asyncio.sleep(.01)
    pool.gate.release()
    with pytest.raises(PlaygroundError) as error:
        await checking
    assert error.value.code=='account_deleted'
    await deleting
    assert not pool.clients and account_id not in bridge.accounts
    await pool.close()


@pytest.mark.asyncio
async def test_close_attempts_every_client_after_one_cleanup_fails():
    pool,_=pool_fixture()
    a,b=await add(pool,'A'),await add(pool,'B')
    first,second=pool.client(pool.record(a)),pool.client(pool.record(b))
    async def fail(): raise RuntimeError('private-worker-details')
    first.close=fail
    with pytest.raises(PlaygroundError) as error: await pool.close()
    assert second.closed and not pool.clients and pool.active is None
    assert 'private-worker-details' not in error.value.body()['error']['message']


@pytest.mark.asyncio
async def test_source_close_failure_still_clears_active_and_releases_queue():
    pool,_=pool_fixture()
    account_id=await add(pool)
    client=pool.client(pool.record(account_id))
    async def broken_source(model,request):
        try:
            yield {'candidates':[{'content':{'parts':[{'text':'partial'}]}}]}
        finally:
            raise RuntimeError('source cleanup failed')
    client.generate=broken_source
    client.waa.process=object()
    client.waa_at=123
    stream=pool.generate('test-chat',{})
    await anext(stream)
    assert pool.active==account_id and pool.gate.locked()
    await pool.set_enabled(account_id,False)
    with pytest.raises(RuntimeError,match='source cleanup failed'):
        await stream.aclose()
    assert pool.active is None and not pool.gate.locked()
    assert client.waa.process is None and client.waa_at==0
    await pool.close()
