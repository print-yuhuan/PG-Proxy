import asyncio
import json
import queue
import threading

import pytest

from pgproxy.waa import AndroidWAA
from pgproxy.errors import PlaygroundError


class WorkerBridge:
    def __init__(self,resource=False,hang=False):
        self.lines=queue.Queue()
        self.resource=resource
        self.hang=hang
        self.closed=[]
        self.writes=[]
        self.id=None
        self.reserved=set()
    def reserveWorker(self,worker):
        assert worker not in self.reserved
        self.reserved.add(worker)
    def startWorker(self,worker):
        assert worker in self.reserved
        self.id=worker
        return worker
    def workerWrite(self,worker,line):
        assert line.endswith('\n')
        value=json.loads(line)
        self.writes.append(value)
        if self.hang: return
        if value['op']=='resource_result':
            self.lines.put(json.dumps({'id':1,'result':{'initialized':True}}))
        elif self.resource:
            self.lines.put(json.dumps({'resource':{'id':5,'url':'https://aistudio.google.com/app/_/bscframe'}}))
        else:
            self.lines.put(json.dumps({'id':value['id'],'result':{'runtime':'test-contract-only'}}))
    def workerRead(self,worker,timeout):
        try: return self.lines.get(timeout=max(.001,timeout/1000))
        except queue.Empty: return ''
    def workerStop(self,worker):
        self.reserved.discard(worker)
        self.closed.append(worker)
        self.lines.put('')


@pytest.mark.asyncio
async def test_bidirectional_resource_protocol_and_cleanup():
    bridge=WorkerBridge(resource=True)
    worker=AndroidWAA(bridge,1)
    requests=[]
    async def resource(value):
        requests.append(value)
        return {'ok':True,'body':'test'}
    worker.resource_handler=resource
    assert (await worker.call('init'))['initialized']
    assert requests[0]['id']==5
    assert bridge.writes[1]['resource_id']==5
    await worker.close()
    assert bridge.closed==[bridge.id] and worker.process is None


@pytest.mark.asyncio
async def test_timeout_and_cancellation_reap_native_process():
    bridge=WorkerBridge(hang=True)
    worker=AndroidWAA(bridge,.05)
    with pytest.raises(PlaygroundError) as error:
        await worker.call('init')
    assert error.value.code=='waa_unavailable'
    assert worker.process is None and bridge.closed==[bridge.id]


@pytest.mark.asyncio
async def test_blocked_pipe_write_is_bounded_and_reaped():
    class BlockedWrite(WorkerBridge):
        def __init__(self):
            super().__init__()
            self.unblock=threading.Event()
        def workerWrite(self,worker,line):
            self.unblock.wait(2)
        def workerStop(self,worker):
            self.unblock.set()
            super().workerStop(worker)
    bridge=BlockedWrite()
    worker=AndroidWAA(bridge,.05)
    with pytest.raises(PlaygroundError):
        await asyncio.wait_for(worker.call('init'),.5)
    assert bridge.closed==[bridge.id] and bridge.unblock.is_set()


@pytest.mark.asyncio
async def test_cancel_during_native_start_reclaims_late_worker_id():
    class SlowStart(WorkerBridge):
        def __init__(self):
            super().__init__()
            self.release=threading.Event()
        def startWorker(self,worker):
            self.id=worker
            self.release.wait(2)
            if worker in self.closed:
                raise RuntimeError('Creation cancelled')
            return worker
    bridge=SlowStart()
    worker=AndroidWAA(bridge,5)
    task=asyncio.create_task(worker.call('ping'))
    await asyncio.sleep(.02)
    task.cancel()
    bridge.release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert worker.worker_id is None and bridge.closed==[bridge.id]
    bridge=WorkerBridge(hang=True)
    worker=AndroidWAA(bridge,10)
    task=asyncio.create_task(worker.call('init'))
    await asyncio.sleep(.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert worker.process is None and bridge.closed==[bridge.id]


@pytest.mark.asyncio
async def test_start_timeout_is_bounded_and_preallocated_id_cancelled():
    class SlowStart(WorkerBridge):
        def __init__(self):
            super().__init__()
            self.release=threading.Event()
            self.created=False
        def startWorker(self,worker):
            self.id=worker
            self.release.wait(2)
            if worker in self.closed:
                raise RuntimeError('Creation cancelled')
            self.created=True
            return worker
        def workerStop(self,worker):
            super().workerStop(worker)
            self.release.set()
    bridge=SlowStart()
    worker=AndroidWAA(bridge,.05)
    with pytest.raises(PlaygroundError):
        await asyncio.wait_for(worker.call('ping'),.5)
    assert bridge.closed==[bridge.id] and not bridge.created
    assert not bridge.reserved


@pytest.mark.asyncio
async def test_failed_creation_releases_reserved_slot_before_next_attempt():
    class FailedStart(WorkerBridge):
        def startWorker(self,worker):
            assert worker in self.reserved
            raise RuntimeError('native creation failure')
    bridge=FailedStart()
    worker=AndroidWAA(bridge,1)
    for _ in range(2):
        with pytest.raises(PlaygroundError): await worker.call('ping')
        assert not bridge.reserved and worker.worker_id is None
    assert len(bridge.closed)==2 and bridge.closed[0]!=bridge.closed[1]
