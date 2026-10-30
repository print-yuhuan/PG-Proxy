"""Private Android bridge entry point; account management is never an HTTP route."""
import asyncio
from collections import deque
import contextlib
import importlib.metadata
import json
import socket
import threading
import time

import uvicorn

from .accounts import AccountPool
from .config import VERSION, DEFAULT_PORT, DEFAULT_API_KEY
from .errors import PlaygroundError
from .main import create_app
from .waa import AndroidWAA
from .request_statistics import RequestStatistics
from .request_guard import MAX_HTTP_CONCURRENCY
from .network_info import NetworkInfo


class Controller:
    def __init__(self, bridge, client_factory=None):
        self.bridge = bridge
        raw = bridge.loadState()
        self.state = (json.loads(str(raw)) if raw else None) or {'schema':1,'accounts':[]}
        if self.state.get('schema') != 1 or not isinstance(self.state.get('accounts'),list):
            raise ValueError('Unsupported encrypted application state')
        config = self.state.get('config')
        if config is None:
            config = {}
        if not isinstance(config,dict):
            raise ValueError('Unsupported encrypted application configuration')
        # Fill absent fields without replacing any previously saved preferences.
        self.state['config'] = {'port':DEFAULT_PORT,'lan':False,'apiKey':DEFAULT_API_KEY,
                                'timeout':180,'queueTimeout':30,'keepAwake':False,**config}
        if type(self.state['config']['keepAwake']) is not bool:
            raise ValueError('Unsupported encrypted background configuration')
        self.logs = deque(maxlen=100)
        self.request_statistics = RequestStatistics()
        self.network_info = NetworkInfo()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run,name='PGProxyPython',daemon=True)
        self.server = None
        self.server_task = None
        self.listener = None
        self.control_lock = asyncio.Lock()
        try:
            self.pool = AccountPool(bridge,self.state,self.persist,client_factory)
            self.persist()
        except BaseException:
            self.loop.close()
            raise
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    def persist(self):
        self.bridge.saveState(json.dumps(self.state,separators=(',',':')))

    def _log(self, event):
        self.logs.append({'at':time.time(),'event':event})

    def snapshot(self):
        config = self.state['config']
        accounts = []
        for record in self.state['accounts']:
            item = {key:record.get(key) for key in (
                'id','label','enabled','status','lastChecked','lastModel','lastError','modelCount')}
            item['cooldownSeconds'] = max(0,int(record.get('cooldownUntil',0)-time.time()))
            item['active'] = self.pool.active == record['id']
            accounts.append(item)
        return {'version':VERSION,'running':bool(self.server and self.server.started and
                    self.server_task and not self.server_task.done()),
                'host':'0.0.0.0' if config['lan'] else '127.0.0.1',
                **config,'accounts':accounts,'logs':list(self.logs),**self.request_statistics.snapshot()}

    def call(self, action, payload_json='{}'):
        """Call off the Android UI thread. Results intentionally omit exception traces."""
        if not self.thread.is_alive():
            return json.dumps({'ok':False,'error':{'status':503,'code':'service_closed','message':'服务已关闭，请返回概览页查看运行状态。'}})
        try:
            payload = json.loads(str(payload_json))
            if not isinstance(payload,dict):
                raise ValueError()
        except (TypeError,ValueError):
            return json.dumps({'ok':False,'error':{'status':400,'code':'invalid_request','message':'操作参数格式不正确。'}})
        future = asyncio.run_coroutine_threadsafe(self._call(str(action),payload),self.loop)
        # Every upstream/queue/worker operation has its own bounded timeout.
        try:
            return json.dumps(future.result(),separators=(',',':'))
        except Exception:
            return json.dumps({'ok':False,'error':{'status':503,'code':'service_error','message':'服务操作未完成，请稍后重试。'}})

    async def _call(self, action, payload):
        try:
            if action == 'snapshot':
                return {'ok':True,'result':self.snapshot()}
            if action == 'network_info':
                force = payload.get('force',False)
                if type(force) is not bool or set(payload)-{'force'}:
                    return {'ok':False,'error':{'status':400,'code':'invalid_request',
                                               'message':'网络刷新参数格式不正确。'},
                            'result':{'data':None,'error':'网络刷新参数无效'}}
                return {'ok':True,'result':await self.network_info.get(force)}
            if action == 'diagnostics':
                worker = AndroidWAA(self.bridge,10)
                try:
                    runtime = await worker.call('ping')
                finally:
                    await worker.close()
                return {'ok':True,'result':{'runtime':runtime,'python':__import__('sys').version.split()[0],
                    'dependencies':{name:importlib.metadata.version(name) for name in ('fastapi','uvicorn','httpx','pydantic')},
                    'generationVerified':any(r.get('status')=='verified' for r in self.state['accounts'])}}
            async with self.control_lock:
                extra = {}
                if action == 'start':
                    await self._start()
                elif action == 'stop':
                    await self._stop()
                elif action == 'configure':
                    await self._configure(payload)
                elif action == 'import_account':
                    extra['accountId'] = await self.pool.import_account(payload)
                    self._log('account_imported')
                elif action == 'set_enabled':
                    await self.pool.set_enabled(payload['id'],payload['enabled'])
                    self._log('account_enabled' if payload['enabled'] else 'account_disabled')
                elif action == 'delete_account':
                    await self.pool.delete(payload['id'])
                    self._log('account_deleted')
                elif action in ('check_account','refresh_waa'):
                    force_refresh = action=='refresh_waa' or payload.get('forceRefresh',False)
                    if type(force_refresh) is not bool:
                        raise ValueError()
                    extra['check'] = await self.pool.check(payload['id'],payload.get('model'),force_refresh)
                    self._log('account_generation_verified')
                else:
                    raise PlaygroundError('Unknown internal operation',400,'invalid_request')
                return {'ok':True,'result':{**self.snapshot(),**extra}}
        except PlaygroundError as error:
            self._log(error.code)
            return {'ok':False,'error':{'status':error.status,'code':error.code,'message':error.public_message},'result':self.snapshot()}
        except (ValueError,TypeError,KeyError):
            return {'ok':False,'error':{'status':400,'code':'invalid_request','message':'操作参数格式不正确。'},'result':self.snapshot()}
        except Exception:
            self._log('service_error')
            return {'ok':False,'error':{'status':503,'code':'service_error','message':'服务操作未完成，请稍后重试。'},'result':self.snapshot()}

    async def _configure(self, payload):
        allowed = {'port','lan','apiKey','timeout','queueTimeout','keepAwake'}
        if set(payload)-allowed:
            raise ValueError()
        config = {**self.state['config'],**payload}
        if type(config['port']) is not int or not 1024 <= config['port'] <= 65535:
            raise PlaygroundError('Port must be between 1024 and 65535',400,'invalid_port')
        if type(config['lan']) is not bool or type(config['keepAwake']) is not bool:
            raise ValueError()
        key = config['apiKey']
        if not isinstance(key,str) or not 3<=len(key)<=256 or any(ord(c)<33 or ord(c)>126 for c in key):
            raise PlaygroundError('API key must contain 3–256 visible ASCII characters',400,'invalid_api_key')
        for field in ('timeout','queueTimeout'):
            if type(config[field]) not in (float,int) or not 1<=config[field]<=600:
                raise PlaygroundError('Timeout must be between 1 and 600 seconds',400,'invalid_timeout')
        running = self.snapshot()['running']
        previous = self.state['config']
        # The wake lock belongs to the Android service. A background-only change
        # must not restart the listener or interrupt an in-flight generation.
        if all(config[key] == value for key,value in previous.items() if key != 'keepAwake'):
            self.state['config'] = config
            try:
                self.persist()
            except Exception:
                self.state['config'] = previous
                raise
            self._log('configuration_saved')
            return
        await self._stop()
        self.state['config'] = config
        self.pool.settings = self.pool._settings()
        try:
            if running:
                await self._start()
            self.persist()
        except Exception:
            await self._stop()
            self.state['config'] = previous
            self.pool.settings = self.pool._settings()
            self.persist()
            if running:
                try:
                    await self._start()
                except PlaygroundError:
                    self._log('configuration_rollback_restart_failed')
            raise
        self._log('configuration_saved')

    async def _start(self):
        if self.snapshot()['running']:
            return
        config = self.state['config']
        listener = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('0.0.0.0' if config['lan'] else '127.0.0.1',config['port']))
            listener.setblocking(False)
        except OSError:
            listener.close()
            raise PlaygroundError('Port is unavailable; choose another port',503,'port_unavailable') from None
        self.listener = listener
        self.server = uvicorn.Server(uvicorn.Config(create_app(self.pool,close_on_shutdown=False,
                                                              request_statistics=self.request_statistics),
            host='0.0.0.0' if config['lan'] else '127.0.0.1',port=config['port'],
            loop='asyncio',http='h11',ws='none',lifespan='on',access_log=False,log_config=None,
            timeout_graceful_shutdown=3,limit_concurrency=MAX_HTTP_CONCURRENCY))
        self.server_task = asyncio.create_task(self.server.serve(sockets=[listener]))
        for _ in range(100):
            if self.server.started:
                self._log('service_started')
                return
            if self.server_task.done():
                break
            await asyncio.sleep(.05)
        await self._stop()
        raise PlaygroundError('HTTP service did not start',503,'service_start_failed')

    async def _stop(self):
        server, task = self.server, self.server_task
        if server:
            server.should_exit = True
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task),8)
            except (asyncio.TimeoutError,asyncio.CancelledError):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.server = self.server_task = None
        if self.listener:
            self.listener.close()
            self.listener = None
        await self.pool.close()
        if server:
            self._log('service_stopped')

    async def _shutdown(self):
        await self.network_info.close()
        await self._call('stop',{})

    def close(self):
        if self.thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown(),self.loop).result()
            finally:
                self.loop.call_soon_threadsafe(self.loop.stop)
                self.thread.join(timeout=10)
