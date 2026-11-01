"""Independent Python Playground transport with a managed per-account WAA VM."""
import asyncio
import base64
import hashlib
import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlparse
import httpx
from .config import Settings, ORIGIN, RPC_BASE
from .errors import PlaygroundError
from .request_logging import note_upstream, register_secrets, set_stage
from .gemini_compat import normalize_gemini_request, normalize_count_tokens_request
from .session import GoogleSession
from .waa import UnavailableWAA, WAA_URL, WAA_KEY, WAA_API_KEY, parse_challenge, load_interpreter
from .playground_protocol import (loads, parse_models, encode_generate, iter_frames, decode_frame,
                                  encode_logging_context, CONTINUATION_ROUND_TOKENS)


def parse_page_toggles(text):
    """Page globals use JavaScript integer literals, not JSON (hex and trailing comma)."""
    try:
        values=[]
        for token in text.strip()[1:-1].split(','):
            token=token.strip()
            if not token:continue
            if not re.fullmatch(r'-?(?:0[xX][0-9a-fA-F]+|[0-9]+)',token):
                raise ValueError()
            values.append(int(token,16 if token.lstrip('-').lower().startswith('0x') else 10))
        return values
    except ValueError:
        raise PlaygroundError('AI Studio page build toggles format changed',503,'page_initialization_failed') from None


class PlaygroundClient:
    def __init__(self, settings=None, transport=None, session=None, worker=None):
        self.settings = settings or Settings()
        self.session = session or GoogleSession()
        self.http = httpx.AsyncClient(transport=transport,
            timeout=httpx.Timeout(self.settings.timeout,connect=30),http2=True,follow_redirects=False,trust_env=False,
            headers={'User-Agent':self.settings.user_agent,'Accept-Language':self.settings.language})
        self.waa = worker or UnavailableWAA()
        self.waa.resource_handler = self._resource
        self.lock = asyncio.Lock()
        self.session_stamp = None
        self.catalog = {}
        self.models_at = 0
        self.api_key = self.logging_context = self.visit_id = ''
        self.benefit_tier_header = ''
        self.page_url = ORIGIN+'/prompts/new_chat'
        self.page_globals = {}
        self.waa_at = 0
        self.challenge = None
        self.interpreter = None
        self.cooldown_until = 0
        self.closed = False
        self.generation_verified = False

    async def _resource(self,resource):
        from .waa import fetch_resource
        result = await fetch_resource(self.http,resource)
        self._capture()
        return result

    def _merge_runtime_cookies(self,result):
        from .session import google_domain
        cookies = result.get('cookies',[])
        if not cookies:
            return
        # Merge browser-script changes, retaining HTTP-only session cookies.
        original = self.session.state['cookies']
        by_key = {(c['name'],c['domain'].lstrip('.'),c.get('path','/')):c for c in original}
        for c in cookies:
            if google_domain(c.get('domain','')):
                by_key[(c['name'],c['domain'].lstrip('.'),c.get('path','/'))] = c
        self.session.state['cookies'] = list(by_key.values())
        self.session.install(self.http.cookies)
        self._capture()

    @asynccontextmanager
    async def operation(self):
        set_stage('queue')
        try:
            await asyncio.wait_for(self.lock.acquire(),self.settings.queue_timeout)
        except asyncio.TimeoutError:
            raise PlaygroundError('Account request queue is busy',429,'account_busy','5') from None
        try:
            if self.closed:
                raise PlaygroundError('Playground client is closed',503)
            remaining = int(self.cooldown_until-time.monotonic())
            if remaining>0:
                raise PlaygroundError('Upstream rate limit cooldown active',429,'rate_limit',str(remaining+1))
            await self._load_session()
            yield
        except httpx.TimeoutException as failure:
            error = PlaygroundError('Playground request timed out',504,'upstream_timeout')
            error.diagnostic_raw, error.diagnostic_type = str(failure), type(failure).__name__
            raise error from None
        except httpx.HTTPError as failure:
            error = PlaygroundError('Playground network request failed',502,'upstream_connection_error')
            error.diagnostic_raw, error.diagnostic_type = str(failure), type(failure).__name__
            raise error from None
        except (TypeError,ValueError,IndexError,KeyError) as failure:
            error = PlaygroundError('Playground protocol or session format changed',502,'protocol_error')
            error.diagnostic_raw, error.diagnostic_type = str(failure), type(failure).__name__
            raise error from None
        finally:
            self.lock.release()

    async def _load_session(self):
        set_stage('credential')
        stamp = self.session.revision
        if stamp != self.session_stamp:
            self.session.load()
            metadata=self.session.state.get('browserMetadata',{})
            language=metadata.get('language')
            # Gecko supplies the current session language; WAA owns the HTTP UA.
            if isinstance(language,str) and language and not any(c in language for c in '\r\n'):
                self.settings.language=language
                self.http.headers['Accept-Language']=language
            self.http.cookies.clear()
            self.session.install(self.http.cookies)
            await self.waa.close()
            self.challenge = None
            self.api_key = ''
            self.catalog = {}
            self.waa_at = self.models_at = 0
            self.generation_verified = False
            self.session_stamp = stamp
        if getattr(self.waa,'user_agent',None):
            self.settings.user_agent=self.waa.user_agent
            self.http.headers['User-Agent']=self.waa.user_agent
        # Clients are cached across requests; register the current session on
        # every trace, including when the encrypted session did not change.
        register_secrets(*(c.get('value') for c in self.session.state.get('cookies',[])),self.api_key)

    def _capture(self):
        try:
            self.session.capture(self.http.cookies)
            self.session_stamp = self.session.revision
            register_secrets(*(c.get('value') for c in self.session.state.get('cookies',[])))
        except OSError:
            raise PlaygroundError('Cannot save updated Google session',503,'session_storage_error') from None

    def _headers(self, key=None, tier=False):
        h = {'Content-Type':'application/json+protobuf','Origin':ORIGIN,'Referer':ORIGIN+'/',
             'x-goog-api-key':key or self.api_key,'x-goog-authuser':'0',
             'x-user-agent':'grpc-web-javascript/0.1','Authorization':self.session.authorization()}
        if self.visit_id:
            h['x-aistudio-visit-id'] = self.visit_id
        if self.logging_context and key is None:
            h['x-goog-ext-519733851-bin'] = self.logging_context
        if tier and self.benefit_tier_header:
            h['x-aistudio-g1-tier']=self.benefit_tier_header
        register_secrets(h['Authorization'],h['x-goog-api-key'],self.logging_context,self.visit_id)
        return h

    async def _error_context(self,response):
        """Read only a bounded error prefix; never log a login HTML document.

        Generation owns/ closes its streaming response. This reader only runs
        after an HTTP failure and has its own short deadline, so diagnostics
        cannot leave a failed upstream stream hanging indefinitely.
        """
        content_type = response.headers.get('content-type','').lower()
        if 'html' in content_type:
            return '上游返回 HTML 页面；为保护登录会话，未记录页面正文。', None
        raw = ''
        try:
            if response.is_stream_consumed:
                data = response.content[:8192]
            else:
                data = bytearray()
                async with asyncio.timeout(min(2,self.settings.timeout)):
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        data.extend(chunk[:8192-len(data)])
                        if len(data)>=8192:
                            break
            raw = bytes(data).decode('utf-8',errors='replace')[:4096]
        except Exception as failure:
            raw = f'错误响应正文未完整读取：{type(failure).__name__}: {failure}'[:4096]
        if raw.lstrip().lower().startswith(('<!doctype html','<html')):
            return '上游返回 HTML 页面；为保护登录会话，未记录页面正文。', None
        code = None
        if raw.lstrip().startswith(('{','[')):
            try:
                payload = json.loads(raw)
                info = payload.get('error') if isinstance(payload,dict) else None
                if isinstance(info,dict):
                    value = info.get('status') or info.get('code')
                    if isinstance(value,(str,int)) and not isinstance(value,bool):
                        code = str(value)[:128]
            except (ValueError,TypeError,RecursionError):
                pass
        return raw, code

    async def _check(self,response,method):
        self._capture()
        note_upstream('POST',str(response.url),status=response.status_code)
        if response.status_code == 200:
            if 'text/html' in response.headers.get('content-type',''):
                error = PlaygroundError('Google returned a login or challenge page; refresh the session',503,'session_required')
                error.diagnostic_status = 200
                raise error
            return
        status = response.status_code
        raw, upstream_code = await self._error_context(response)
        note_upstream('POST',str(response.url),status=status,code=upstream_code,raw=raw)
        def failed(message, public_status, code, retry_after=None):
            error = PlaygroundError(message,public_status,code,retry_after)
            error.diagnostic_raw = raw or message
            error.diagnostic_status = status
            error.diagnostic_upstream_code = upstream_code
            return error
        if status==403:
            self.waa_at=0
            raise failed('Google refused the request',403,'upstream_permission_denied')
        if status in (401,403) or 300 <= status <400:
            self.api_key = ''; self.waa_at = 0
            raise failed(f'{method} rejected the Google session (HTTP {status}); verify account access in AI Studio and re-import cookies',status if status in (401,403) else 503,'upstream_authentication_error')
        if status == 429:
            from email.utils import parsedate_to_datetime
            retry = response.headers.get('retry-after','60')
            try:
                seconds = int(retry)
            except ValueError:
                try:
                    seconds = max(1,int(parsedate_to_datetime(retry).timestamp()-time.time()))
                except (ValueError,TypeError,OverflowError):
                    seconds = 60
            self.cooldown_until = time.monotonic()+max(1,seconds)
            raise failed('Playground rate limit reached; no automatic generation retry',429,'rate_limit',str(max(1,seconds)))
        raise failed(f'{method} upstream HTTP {status}', status if status in (400,404) else 502,'upstream_error')

    async def _rpc(self,method,body,key=None,*,tier=False):
        note_upstream('POST',RPC_BASE+method)
        response = await self.http.post(RPC_BASE+method,headers=self._headers(key,tier=tier),content=json.dumps(body,ensure_ascii=False,separators=(',',':')).encode())
        await self._check(response,method)
        return response.text

    async def _page(self):
        set_stage('page_initialization')
        target = self.page_url
        for _ in range(5):
            note_upstream('GET',target)
            response = await self.http.get(target,headers={'Accept':'text/html'})
            note_upstream('GET',target,status=response.status_code)
            self._capture()
            if response.is_redirect:
                location = str(response.url.join(response.headers.get('location','')))
                target_url = urlparse(location)
                if (target_url.scheme != 'https' or target_url.hostname != 'aistudio.google.com'
                        or target_url.username or target_url.password or target_url.port not in (None,443)):
                    error = PlaygroundError('AI Studio redirected to login or consent; refresh the Google session in a browser',503,'session_required')
                    error.diagnostic_status = response.status_code
                    raise error
                target = location
                continue
            if response.status_code != 200:
                raw, code = await self._error_context(response)
                note_upstream('GET',target,status=response.status_code,code=code,raw=raw)
                error = PlaygroundError(f'AI Studio page HTTP {response.status_code}',503,'page_initialization_failed')
                error.diagnostic_raw, error.diagnostic_status = raw, response.status_code
                raise error
            match = re.search(r'"WIu0Nc"\s*:\s*"([^"\\]+)"',response.text)
            if not match:
                error = PlaygroundError('AI Studio page has no MakerSuite API key; check login, account terms or protocol changes',503,'page_initialization_failed')
                error.diagnostic_status = response.status_code
                raise error
            self.api_key = match[1]
            register_secrets(self.api_key)
            self.visit_id = 'v1_'+base64.b64encode(str(uuid.uuid4()).encode()).decode()
            self.logging_context = ''
            toggles = re.search(r'window\["_F_toggles_default_MakerSuite"\]\s*=\s*(\[[^\]]*\])',response.text)
            self.page_globals = {}
            if toggles:
                self.page_globals['_F_toggles_default_MakerSuite'] = parse_page_toggles(toggles[1])
            for name,value in re.findall(r"var (_F_cssRowKey|_F_combinedSignature) = '([^']*)'",response.text):
                self.page_globals[name] = value
            self.logging_context = encode_logging_context(loads(await self._rpc('GetLoggingContext',[])))
            # Playground page initialization only. No application/Build initialization.
            await self._rpc('GetUserPreferences',[])
            benefit=loads(await self._rpc('GetAiStudioBenefitTier',[]))
            code=benefit[0] if benefit and benefit[0] is not None else 0
            if type(code) is not int or code not in (0,1,2,3):
                raise PlaygroundError('AI Studio benefit tier format changed',503,'page_initialization_failed')
            self.benefit_tier_header={0:'',1:'TIER1',2:'TIER2',3:'TIER0'}[code]
            return
        raise PlaygroundError('AI Studio page redirect limit exceeded',503,'page_initialization_failed')

    async def _models(self):
        if not self.api_key:
            try:
                await self._page()
            except BaseException:
                # The page key is assigned before initialization RPCs finish.
                # Roll back partial state, including cancellation, so only the
                # next independent request retries initialization; never replay
                # a generation here.
                self.api_key = self.logging_context = self.visit_id = ''
                self.benefit_tier_header = ''
                self.page_globals = {}
                self.catalog = {}
                self.models_at = self.waa_at = 0
                raise
        if not self.catalog or time.monotonic()-self.models_at>=self.settings.model_ttl:
            set_stage('model_catalog')
            self.catalog = parse_models(await self._rpc('ListModels',[]))
            self.models_at = time.monotonic()
        return self.catalog

    async def models(self):
        async with self.operation():
            set_stage('model_catalog')
            return await self._models()

    async def count_tokens(self, model_id, request):
        from .token_protocol import encode_count_tokens, decode_token_count
        model_id = model_id.removeprefix('models/')
        async with self.operation():
            catalog = await self._models()
            if model_id not in catalog:
                raise PlaygroundError('Model is not in this account\'s supported Playground catalog',404,'model_not_found')
            model = catalog[model_id]
            if 'countTokens' not in model.get('_upstream_methods', []):
                raise PlaygroundError('This model does not support token counting',400,'unsupported_method')
            set_stage('request_encoding')
            wire = encode_count_tokens(model_id, normalize_count_tokens_request(request, model), model)
            set_stage('upstream_count_tokens')
            return decode_token_count(await self._rpc('CountTokens', wire, tier=True))

    async def _prepare_waa(self):
        set_stage('waa_challenge')
        if self.waa_at and self.waa.process and self.waa.process.returncode is None and time.monotonic()-self.waa_at<self.settings.waa_ttl:
            return
        refresh = []
        if self.challenge and self.waa.process and self.waa.process.returncode is None:
            old = await self.waa.call('refresh')
            register_secrets(old.get('proof'))
            self._merge_runtime_cookies(old)
            refresh = [self.challenge['interpreter_hash'],old['proof']]
        set_stage('waa_challenge')
        register_secrets(WAA_KEY)
        note_upstream('POST',WAA_URL)
        response = await self.http.post(WAA_URL,headers=self._headers(WAA_API_KEY),json=[WAA_KEY,*refresh])
        await self._check(response,'Waa/Create')
        challenge = parse_challenge(response.text)
        register_secrets(challenge.get('program'),challenge.get('message_id'))
        source = await load_interpreter(self.http,challenge,self.interpreter)
        # Verify source on both sides of IPC; no proof is loaded from disk.
        set_stage('waa_execution')
        result = await self.waa.call('init',challenge=challenge,interpreter=source,page_url=self.page_url,
                           user_agent=self.settings.user_agent,cookies=self.session.matching(),
                           local_storage=self.session.local_storage(),globals=self.page_globals,timezone=self.settings.timezone)
        self._merge_runtime_cookies(result)
        self.challenge,self.interpreter = challenge,source
        self.waa_at = time.monotonic()

    async def generate(self,model_id,request):
        model_id = model_id.removeprefix('models/')
        async with self.operation():
            models = await self._models()
            if model_id not in models:
                raise PlaygroundError('Model is not in this account\'s supported Playground catalog',404,'model_not_found')
            model = models[model_id]
            set_stage('request_encoding')
            request = normalize_gemini_request(request, model)
            wire,binding,page_prompt = encode_generate(model_id,request,model,self.settings.timezone)
            register_secrets(page_prompt)
            continuable = 91 in model.get('_codes',[])
            total = request.get('generationConfig',{}).get('maxOutputTokens',model['outputTokenLimit'])
            rounds = (total+CONTINUATION_ROUND_TOKENS-1)//CONTINUATION_ROUND_TOKENS if continuable else 1
            continuation_token, previous_usage = None, None
            used_tokens = set()
            visible = False
            for round_index in range(rounds):
                if round_index:
                    wire,binding,page_prompt = encode_generate(model_id,request,model,self.settings.timezone,
                        continuation_round=round_index,continuation_token=continuation_token)
                try:
                    await self._prepare_waa()
                    # Each continuation is a new protected request, never a replay of a proof.
                    set_stage('waa_execution')
                    proof = await self.waa.call('proof',digest=hashlib.sha256(binding.encode()).hexdigest(),prompt=page_prompt)
                    register_secrets(proof.get('proof'))
                    self._merge_runtime_cookies(proof)
                    wire[4] = proof['proof']
                except BaseException:
                    self.waa_at = 0
                    await self.waa.close()
                    raise
                set_stage('upstream_generate')
                note_upstream('POST',RPC_BASE+'GenerateContent')
                response = await self.http.send(self.http.build_request('POST',RPC_BASE+'GenerateContent',headers=self._headers(tier=True),
                    content=json.dumps(wire,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()),stream=True)
                finished = need_continuation = False
                latest_token, round_usage, terminal, round_finish_code = None, None, None, None
                try:
                    await self._check(response,'GenerateContent')
                    set_stage('response_parsing')
                    async for frame in iter_frames(response.aiter_text()):
                        result = decode_frame(frame,continuable=continuable)
                        if result is None:
                            continue
                        frame_token = result.pop('_continuationToken',None)
                        finish_code = result.pop('_finishCode',None)
                        if frame_token:
                            latest_token = frame_token
                        visible |= any(not p.get('thought') and (
                            bool(p.get('text')) or bool(p.get('inlineData',{}).get('data'))
                            or bool(p.get('functionCall')) or bool(p.get('codeExecutionResult')))
                            for c in result.get('candidates',[])
                            for p in c.get('content',{}).get('parts',[]))
                        is_terminal = 'promptFeedback' in result or any('finishReason' in c for c in result.get('candidates',[]))
                        if not continuable:
                            finished |= is_terminal
                            yield result
                            continue
                        if 'usageMetadata' in result:
                            round_usage = result.pop('usageMetadata')
                        if finished:
                            # A trailing metadata frame may finalize usage after the finish marker.
                            # Do not duplicate terminal events or accept new content after completion.
                            if ('promptFeedback' in result or finish_code not in (None,round_finish_code)
                                    or any(c.get('content',{}).get('parts') for c in result.get('candidates',[]))):
                                raise PlaygroundError('Playground returned content after a finish frame',code='protocol_error')
                            continue
                        if is_terminal:
                            finished = True
                            round_finish_code = finish_code
                            can_continue = round_index+1 < rounds
                            requested_continuation = finish_code == 22 or (finish_code == 2 and frame_token and can_continue)
                            if requested_continuation:
                                if not latest_token:
                                    raise PlaygroundError('Playground continuation token missing',code='protocol_error')
                                if latest_token in used_tokens:
                                    raise PlaygroundError('Playground repeated a continuation token',code='protocol_error')
                                need_continuation = can_continue
                            if need_continuation:
                                # The provider ended a round, not the caller's logical response.
                                for candidate in result.get('candidates',[]):
                                    candidate.pop('finishReason',None)
                                yield result
                            else:
                                terminal = result
                        else:
                            yield result
                    if not finished:
                        raise PlaygroundError('Playground stream ended without a finish frame',code='stream_interrupted')
                finally:
                    await response.aclose()
                if not need_continuation:
                    if terminal is not None:
                        if round_usage is not None:
                            terminal['usageMetadata'] = self._continued_usage(previous_usage,round_usage)
                        yield terminal
                    self.generation_verified |= bool(visible)
                    return
                if round_usage is not None:
                    previous_usage = self._continued_usage(previous_usage,round_usage)
                continuation_token = latest_token
                used_tokens.add(continuation_token)

    @staticmethod
    def _continued_usage(previous, current):
        """Continuation keeps the latest input/cache counts and accumulates generated tokens."""
        merged = dict(current)
        if previous is not None:
            generated = ('candidatesTokenCount','thoughtsTokenCount','toolUsePromptTokenCount')
            for key in generated:
                if key in previous or key in current:
                    merged[key] = previous.get(key,0)+current.get(key,0)
            merged['totalTokenCount'] = current.get('totalTokenCount',0)+sum(previous.get(key,0) for key in generated)
        return merged

    async def close(self):
        self.closed = True
        try:
            await self.waa.close()
        finally:
            await self.http.aclose()
