"""Account-isolated clients and conservative request-level round-robin routing."""
import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager

from .config import Settings
from .errors import PlaygroundError
from .playground_client import PlaygroundClient
from .session import BridgeSession, GoogleSession
from .waa import AndroidWAA
from .model_catalog import merge_model_entries


class AccountPool:
    def __init__(self, bridge, state, persist, client_factory=None):
        self.bridge = bridge
        self.state = state
        self.state.setdefault('deletedAccountIds',[])
        self.persist = persist
        self.client_factory = client_factory
        self.clients = {}
        self.gate = asyncio.Lock()
        self.cursor = 0
        self.active = None
        self.settings = self._settings()
        self.deletions = {}
        self._recover_deletions()

    def _recover_deletions(self):
        """Before creating clients, finish deletion journals left by process death."""
        deleted = set(self.state['deletedAccountIds'])
        if not deleted:
            return
        for record in self.state['accounts']:
            if record['id'] in deleted:
                record.update(enabled=False,status='deleting')
        self.persist()
        try:
            # Include orphan files whose account-list update was persisted first.
            for account_id in deleted:
                self.bridge.deleteAccount(account_id)
        except Exception:
            raise PlaygroundError('Pending credential deletion could not be completed; restart to retry',
                                  503,'account_deletion_failed') from None
        self.state['accounts'][:] = [r for r in self.state['accounts'] if r['id'] not in deleted]
        self.persist()

    def require_live_id(self, account_id):
        if account_id in self.state['deletedAccountIds']:
            raise PlaygroundError('This account was deleted; add it again as a new account',410,'account_deleted')

    def _settings(self):
        config = self.state['config']
        return Settings(password=config['apiKey'], timeout=config['timeout'],
                        queue_timeout=config['queueTimeout'])

    def record(self, account_id):
        record = next((x for x in self.state['accounts'] if x['id'] == account_id), None)
        if record is None:
            raise PlaygroundError('Account not found',404,'account_not_found')
        return record

    def client(self, record):
        account_id = record['id']
        self.require_live_id(account_id)
        if account_id not in self.clients:
            settings = self._settings()
            self.clients[account_id] = (self.client_factory(record,settings) if self.client_factory
                else PlaygroundClient(settings, session=BridgeSession(account_id,self.bridge),
                                      worker=AndroidWAA(self.bridge,settings.waa_timeout)))
        return self.clients[account_id]

    @asynccontextmanager
    async def queued(self):
        try:
            await asyncio.wait_for(self.gate.acquire(),self.settings.queue_timeout)
        except asyncio.TimeoutError:
            raise PlaygroundError('Service request queue is busy',429,'account_busy','5') from None
        try:
            yield
        finally:
            self.gate.release()

    def eligible(self):
        now = time.time()
        return [r for r in self.state['accounts'] if r['enabled'] and r['id'] not in self.state['deletedAccountIds']
                and r.get('status') not in ('needs_login','validation_error','deleting')
                and r.get('cooldownUntil',0) <= now]

    def failed(self, record, error):
        if record['id'] in self.state['deletedAccountIds']:
            return
        record['lastError'] = error.code
        if error.code in ('session_required','session_invalid','upstream_authentication_error'):
            record['status'] = 'needs_login'
        elif error.code in ('upstream_permission_denied','waa_execution_error','waa_unavailable','waa_protocol_error'):
            record['status'] = 'validation_error'
        elif error.status == 429:
            record['status'] = 'cooldown'
            try:
                seconds = max(1,int(error.retry_after or 60))
            except (ValueError,TypeError):
                seconds = 60
            record['cooldownUntil'] = time.time()+seconds
        else:
            record['status'] = 'error'
        self.persist()

    async def _catalog(self, record):
        catalog = await self.client(record).models()
        record['modelCount'] = len(catalog)
        return catalog

    async def models(self):
        async with self.queued():
            result = {}
            candidates = self.eligible()
            last = None
            for record in candidates:
                try:
                    catalog = await self._catalog(record)
                    if record['enabled']:
                        for model_id, entry in catalog.items():
                            result[model_id] = (merge_model_entries(result[model_id], entry)
                                                if model_id in result else entry)
                except PlaygroundError as error:
                    self.failed(record,error)
                    last = error
            if not result:
                raise last or PlaygroundError('No enabled account is available; add or check an account',503,'no_available_account')
            self.persist()
            return result

    async def _select(self, model, advance=True):
        candidates = self.eligible()
        if not candidates:
            raise PlaygroundError('No enabled account is available; add or check an account',503,'no_available_account')
        start = self.cursor % len(candidates)
        valid_catalog = False
        last = None
        for offset in range(len(candidates)):
            index = (start+offset) % len(candidates)
            record = candidates[index]
            try:
                catalog = await self._catalog(record)
                valid_catalog = True
            except PlaygroundError as error:
                self.failed(record,error)
                last = error
                continue
            if model in catalog and record['enabled']:
                if advance:
                    self.cursor = index+1
                return record
        if valid_catalog:
            raise PlaygroundError('Model is not in the available Playground catalogs',404,'model_not_found')
        raise last or PlaygroundError('No enabled account is available',503,'no_available_account')

    async def _release_other_workers(self, account_id):
        # Retain each account's HTTP/cookie state, with at most one resident VM.
        for key, client in self.clients.items():
            if key != account_id and client.waa.process is not None:
                await client.waa.close()
                client.waa_at = 0

    async def count_tokens(self, model, request):
        model = model.removeprefix('models/')
        async with self.queued():
            record = await self._select(model, advance=False)
            try:
                return await self.client(record).count_tokens(model, request)
            except PlaygroundError as error:
                self.failed(record,error)
                raise

    async def generate(self, model, request):
        model = model.removeprefix('models/')
        async with self.queued():
            record = await self._select(model)
            client = self.client(record)
            await self._release_other_workers(record['id'])
            self.active = record['id']
            source = client.generate(model,request)
            try:
                async for frame in source:
                    yield frame
                if client.generation_verified and record['id'] not in self.state['deletedAccountIds']:
                    record.update(status='verified',lastError=None,lastChecked=time.time(),lastModel=model,cooldownUntil=0)
                    self.persist()
            except PlaygroundError as error:
                self.failed(record,error)
                raise
            finally:
                try:
                    await source.aclose()
                finally:
                    self.active = None
                    if not record['enabled']:
                        await client.waa.close()
                        client.waa_at = 0

    async def check(self, account_id, model=None, force_refresh=False):
        self.require_live_id(account_id)
        record = self.record(account_id)
        async with self.queued():
            self.require_live_id(account_id)
            client = self.client(record)
            try:
                catalog = await self._catalog(record)
                previous_program = (getattr(client,'challenge',None) or {}).get('program')
                if force_refresh:
                    client.waa_at = 0
                if model is None:
                    choices = [m for m,entry in catalog.items() if 21 not in entry.get('_codes',[])
                               and not any(x in m.lower() for x in ('image','audio','tts','transcribe'))]
                    model = next((m for m in choices if 'flash' in m),next(iter(choices),None))
                if not model or model not in catalog:
                    raise PlaygroundError('Select a model supported by this account',404,'model_not_found')
                await self._release_other_workers(account_id)
                self.active = account_id
                entry = catalog[model]
                config = {'maxOutputTokens':min(1024,entry.get('outputTokenLimit',1024))}
                thinking = entry.get('_thinking',[])
                if {25,35}.issubset(entry.get('_codes',[])) and len(thinking)>3 and thinking[3]:
                    config['thinkingConfig'] = {'thinkingBudget':0,'includeThoughts':False}
                source = client.generate(model,{'contents':[{'role':'user','parts':[{'text':'Reply exactly OK.'}]}],
                                                'generationConfig':config})
                visible = False
                finished = False
                try:
                    async for frame in source:
                        for candidate in frame.get('candidates',[]):
                            visible |= any(p.get('text') and not p.get('thought') for p in candidate.get('content',{}).get('parts',[]))
                            finished |= bool(candidate.get('finishReason'))
                finally:
                    await source.aclose()
                if not visible or not finished:
                    raise PlaygroundError('The model did not return a completed visible response',502,'generation_unverified')
                if account_id not in self.state['deletedAccountIds']:
                    record.update(status='verified',lastChecked=time.time(),lastModel=model,lastError=None,cooldownUntil=0)
                    self.persist()
                next_program = (getattr(client,'challenge',None) or {}).get('program')
                return {'verified':True,'model':model,'checkedAt':record['lastChecked'],
                        'forceRefreshRequested':force_refresh,
                        'waaRefreshed':bool(force_refresh and previous_program and next_program
                                            and previous_program != next_program)}
            except PlaygroundError as error:
                if account_id not in self.state['deletedAccountIds']:
                    record['lastChecked'] = time.time()
                self.failed(record,error)
                raise
            finally:
                self.active = None
                if not record['enabled']:
                    await client.waa.close()
                    client.waa_at = 0

    async def import_account(self, payload):
        data = payload.get('session')
        if not isinstance(data,dict) or len(json.dumps(data)) > 1024*1024:
            raise PlaygroundError('Invalid account session',400,'invalid_session_import')
        # Validate in memory before writing any credential to the encrypted store.
        validator = GoogleSession(data)
        validator.load()
        if not any(c['name']=='__Secure-1PSID' and c['value'] for c in validator.state['cookies']):
            raise PlaygroundError('The browser has not supplied a signed-in Google session',400,'invalid_session_import')
        account_id = payload.get('id') or uuid.uuid4().hex
        if not isinstance(account_id,str) or len(account_id)!=32 or any(c not in '0123456789abcdef' for c in account_id):
            raise PlaygroundError('Invalid account identifier',400,'invalid_request')
        self.require_live_id(account_id)
        label = payload.get('label') or 'Google 凭证'
        if not isinstance(label,str):
            raise PlaygroundError('Invalid account name',400,'invalid_request')
        label = label.strip()[:80]
        if not label or any(ord(c)<32 for c in label):
            raise PlaygroundError('Invalid account name',400,'invalid_request')
        existing = next((r for r in self.state['accounts'] if r['id']==account_id),None)
        async with self.queued():
            self.require_live_id(account_id)
            if account_id in self.clients:
                await self.clients.pop(account_id).close()
            self.bridge.writeAccount(account_id,json.dumps(validator.state,separators=(',',':')))
            record = {'id':account_id,'label':label,'enabled':True,'status':'unchecked','lastChecked':None,
                      'lastModel':None,'lastError':None,'modelCount':0,'cooldownUntil':0}
            if existing:
                existing.update(record)
            else:
                self.state['accounts'].append(record)
            self.persist()
        return account_id

    async def set_enabled(self, account_id, enabled):
        if not isinstance(enabled,bool):
            raise PlaygroundError('enabled must be boolean',400,'invalid_request')
        self.require_live_id(account_id)
        record = self.record(account_id)
        record['enabled'] = enabled
        self.persist()
        if not enabled and self.active != account_id:
            client = self.clients.get(account_id)
            if client:
                await client.waa.close()
                client.waa_at = 0

    async def delete(self, account_id):
        record = next((r for r in self.state['accounts'] if r['id']==account_id),None)
        if record is None:
            if account_id in self.state['deletedAccountIds']:
                return  # Replayed deletion is idempotent.
            self.record(account_id)  # Raise the normal not-found error.
        record.update(enabled=False,status='deleting')
        if account_id not in self.state['deletedAccountIds']:
            self.state['deletedAccountIds'].append(account_id)
        self.persist()
        task = self.deletions.get(account_id)
        if task is None or task.done():
            task = asyncio.create_task(self._finish_delete(account_id))
            self.deletions[account_id] = task
            # UI IPC timeouts and cancellation must not cancel durable cleanup.
            task.add_done_callback(lambda future: None if future.cancelled() else future.exception())
        try:
            await asyncio.wait_for(asyncio.shield(task),self.settings.queue_timeout)
        except asyncio.TimeoutError:
            raise PlaygroundError('Account disabled; credential deletion is waiting for the current request to finish',
                                  409,'account_deletion_pending') from None

    async def _finish_delete(self, account_id):
        # Unlike request admission, this durable cleanup has no queue expiration.
        async with self.gate:
            if account_id in self.clients:
                await self.clients.pop(account_id).close()
            try:
                self.bridge.deleteAccount(account_id)
            except Exception:
                raise PlaygroundError('Credential deletion failed; the account remains blocked and deletion can be retried',
                                      503,'account_deletion_failed') from None
            self.state['accounts'][:] = [r for r in self.state['accounts'] if r['id']!=account_id]
            self.persist()

    async def close(self):
        failures = []
        async with self.gate:
            clients, self.clients = self.clients, {}
            for client in clients.values():
                try:
                    await client.close()
                except Exception as error:
                    failures.append(error)
            self.active = None
        if self.deletions:
            await asyncio.gather(*self.deletions.values(),return_exceptions=True)
            self.deletions.clear()
        if failures:
            raise PlaygroundError('Client resource cleanup failed',503,'service_error') from None
