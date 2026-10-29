"""Real Waa/Create challenge execution in a managed local JavaScript VM.

A returned snapshot is not evidence that Google accepts it: only authenticated
end-to-end generation establishes acceptance.
"""
import asyncio
import base64
import hashlib
import json
import time
import uuid
from urllib.parse import urlparse

from .errors import PlaygroundError

WAA_URL = 'https://waa-pa.clients6.google.com/$rpc/google.internal.waa.v1.Waa/Create'
WAA_KEY = 'lmnUSbltwc5ULv48iKLX'
WAA_API_KEY = 'AIzaSyBGb5fGAyC-pRcRU6MUHb__b_vKha71HRE'


def parse_challenge(raw):
    try:
        outer = json.loads(raw)
        if len(outer) > 1 and isinstance(outer[1], str) and outer[1]:
            data = base64.b64decode(outer[1], validate=True)
            fields = json.loads(bytes((n + 97) & 255 for n in data))
        else:
            fields = outer[0]
        first = lambda a: next((v for v in a if isinstance(v, str) and v), '') if isinstance(a, list) else ''
        c = dict(message_id=fields[0], interpreter=first(fields[1]),
                 interpreter_url=first(fields[2]), interpreter_hash=fields[3],
                 program=fields[4], global_name=fields[5], experiments=fields[7] or '')
        if not all(isinstance(c[k], str) and c[k] for k in ('interpreter_hash', 'program', 'global_name')):
            raise ValueError()
        if c['interpreter_url'].startswith('//'):
            c['interpreter_url'] = 'https:' + c['interpreter_url']
        return c
    except (ValueError, TypeError, IndexError, KeyError):
        raise PlaygroundError('Waa/Create challenge format changed', code='waa_protocol_error') from None


def interpreter_hash(source):
    return base64.urlsafe_b64encode(hashlib.sha256(source.encode()).digest()).decode().rstrip('=')


async def load_interpreter(client, challenge, cached=None):
    source = challenge['interpreter']
    if not source and cached and interpreter_hash(cached) == challenge['interpreter_hash']:
        source = cached
    if not source:
        url = urlparse(challenge['interpreter_url'])
        if url.scheme != 'https' or url.hostname not in ('www.google.com', 'www.gstatic.com') or url.username or url.port:
            raise PlaygroundError('Unexpected WAA interpreter origin', code='waa_protocol_error')
        resp = await client.get(url.geturl(), headers={'Referer': 'https://aistudio.google.com/'})
        if resp.status_code != 200:
            raise PlaygroundError('WAA interpreter download failed', code='waa_unavailable')
        source = resp.text
    if interpreter_hash(source) != challenge['interpreter_hash']:
        raise PlaygroundError('WAA interpreter SHA-256 mismatch', code='waa_protocol_error')
    return source


async def fetch_resource(client, resource):
    """Load real iframe/script resources over the Python session and fixed proxy."""
    try:
        target = urlparse(resource['url'])
        allowed = ('aistudio.google.com','www.google.com','www.gstatic.com')
        if target.scheme!='https' or target.hostname not in allowed or target.username or target.password or target.port:
            return {'ok':False}
        referer = resource.get('referrer','')
        ref = urlparse(referer)
        headers = {'Referer':referer} if ref.scheme=='https' and ref.hostname in allowed else {}
        async with client.stream('GET',target.geturl(),headers=headers) as response:
            if response.status_code!=200:
                return {'ok':False,'http_status':response.status_code}
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body)>4*1024*1024:
                    return {'ok':False}
            headers = {'content-type':response.headers.get('content-type','text/html')}
            cookies = response.headers.get_list('set-cookie')
            if cookies:
                headers['set-cookie'] = cookies
            return {'ok':True,'body':base64.b64encode(body).decode(),'headers':headers,'http_status':response.status_code}
    except (KeyError,ValueError):
        return {'ok':False}


class AndroidWAA:
    """Kotlin owns the installed native worker; Python handles protocol resources."""
    def __init__(self, bridge, timeout=45):
        from .config import WAA_USER_AGENT
        self.bridge = bridge
        self.timeout = timeout
        self.user_agent = WAA_USER_AGENT
        self.process = None
        self.worker_id = None
        self.lock = asyncio.Lock()
        self.sequence = 0
        self.resource_handler = None

    async def _stop(self):
        worker, self.worker_id = self.worker_id, None
        process, self.process = self.process, None
        if process is not None:
            process.returncode = -1
        if worker is not None:
            await asyncio.to_thread(self.bridge.workerStop, worker)

    async def call(self, op, **fields):
        from types import SimpleNamespace
        async with self.lock:
            try:
                deadline = time.monotonic() + self.timeout
                if self.worker_id is None:
                    # Reserve synchronously before dispatching native creation so
                    # cancellation removes a real slot, without unbounded tombstones.
                    self.worker_id = str(uuid.uuid4())
                    self.bridge.reserveWorker(self.worker_id)
                    started = await asyncio.wait_for(asyncio.to_thread(
                        self.bridge.startWorker,self.worker_id),max(.001,deadline-time.monotonic()))
                    if str(started) != self.worker_id:
                        raise ValueError()
                    self.process = SimpleNamespace(returncode=None)
                self.sequence += 1
                async def write(value):
                    await asyncio.wait_for(asyncio.to_thread(self.bridge.workerWrite, self.worker_id,
                        json.dumps(value, separators=(',', ':')) + '\n'),max(.001,deadline-time.monotonic()))
                await write(dict(id=self.sequence, op=op, timeout_seconds=self.timeout, **fields))
                while True:
                    remaining = max(.001, deadline-time.monotonic())
                    raw = await asyncio.wait_for(asyncio.to_thread(
                        self.bridge.workerRead, self.worker_id, max(1, int(remaining*1000))), remaining+.1)
                    if not raw or len(str(raw)) > 4*1024*1024:
                        raise ValueError()
                    result = json.loads(str(raw))
                    if 'resource' not in result:
                        break
                    resource = result['resource']
                    response = {'ok':False}
                    if self.resource_handler:
                        response = await asyncio.wait_for(self.resource_handler(resource),
                                                          max(.001,deadline-time.monotonic()))
                    await write(dict(op='resource_result', resource_id=resource['id'], **response))
                if 'error' in result:
                    raise PlaygroundError('Local WAA program failed',503,'waa_execution_error')
                if result.get('id') != self.sequence or not isinstance(result.get('result'), dict):
                    raise ValueError()
                return result['result']
            except asyncio.CancelledError:
                await asyncio.shield(self._stop())
                raise
            except PlaygroundError:
                await self._stop()
                raise
            except Exception:
                await self._stop()
                raise PlaygroundError('Bundled WAA worker failed or timed out',503,'waa_unavailable') from None

    async def close(self):
        async with self.lock:
            await self._stop()


class UnavailableWAA:
    """No implicit compiler, downloaded executable, or fabricated proof fallback."""
    process = None
    resource_handler = None
    async def call(self, *args, **kwargs):
        raise PlaygroundError('Android WAA bridge is not configured',503,'waa_unavailable')
    async def close(self):
        pass
