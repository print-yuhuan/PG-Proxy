"""Bounded, private request diagnostics. Never writes prompts or response content.

The ASGI wrapper owns request completion, including early guard rejections and
stream failures. Context-local helpers add diagnostics without changing transport
behaviour; Android reads compact summaries and fetches one detail on demand.
"""
import asyncio
import base64
import contextvars
import copy
import json
import math
import re
import threading
import time
import uuid
from collections import OrderedDict
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

from .errors import PlaygroundError


_CURRENT = contextvars.ContextVar('pg_request_log', default=None)
_LEVELS = {'success', 'failure', 'info', 'warning', 'error'}
_REDACTED = '[已脱敏]'
_MAX_RAW = 2048
_MAX_STAGES = 24
_MAX_UPSTREAM = 12
_MAX_PRIVATE_CHARACTERS = 8192
_MAX_PRIVATE_FINGERPRINTS = 4096
_SECRET_KEY = re.compile(
    r'authorization|cookie|password|passwd|secret|credential|session|proof|'
    r'access.?token|refresh.?token|id.?token|api.?key|sapisid|signature|'
    r'page.?token|continuation.?token|logging.?context', re.I)
_PRIVATE_CONTENT_KEY = re.compile(
    r'^(?:text|content|contents|messages|prompt|input|data|arguments|'
    r'inlineData|inline_data|fileData|file_data|systemInstruction|system_instruction)$', re.I)
_TYPE_LABELS = {
    'authentication': '接口鉴权失败', 'credential_missing': '凭证缺失或不可用',
    'credential_expired': '凭证缺失或已过期', 'credential_invalid': '凭证校验失败',
    'permission': '上游拒绝访问', 'timeout': '请求超时', 'connection': '连接异常',
    'parameters': '参数错误', 'not_found': '接口或模型不存在',
    'rate_limit': '上游限流', 'queue_timeout': '排队超时',
    'upstream_5xx': '上游服务异常', 'server_error': '本机服务异常',
    'protocol': '响应协议异常', 'waa': '请求校验异常', 'cancelled': '请求已取消',
    'client_disconnect': '客户端断开', 'interrupted': '响应未完整结束',
}


def _plain(value, limit=256):
    text = str(value) if value is not None else ''
    return ''.join(c for c in text[:limit * 2] if c in '\n\t' or ord(c) >= 32)[:limit]


def _safe_url(value, redact=lambda value, limit=1024: _plain(value, limit)):
    """Preserve routing information, omit URL userinfo and arbitrary query values."""
    try:
        parts = urlsplit(_plain(value, 2048))
        host = parts.hostname or ''
        if ':' in host:
            host = '[' + host + ']'
        if parts.port is not None:
            host += ':' + str(parts.port)
        query = []
        for name, item in parse_qsl(parts.query[:4096], keep_blank_values=True, max_num_fields=48):
            if name == 'alt' and item in ('sse', 'json'):
                query.append('alt=' + item)
            elif name == 'pageSize' and item.isascii() and item.isdecimal() and len(item) <= 6:
                query.append('pageSize=' + item)
            else:
                label = name if name in ('key', 'pageToken', 'access_token', 'api_key', 'token') else 'other'
                query.append(label + '=' + _REDACTED)
        path = re.sub(r'(?<=/)[A-Za-z0-9_=-]{80,}(?=/|$)', _REDACTED, unquote(parts.path))
        return redact(urlunsplit((parts.scheme, host, path, '&'.join(query), '')), 1024)
    except (ValueError, TypeError, UnicodeError):
        return '[地址无法解析；查询参数已隐藏]'


class LogStore:
    """100 in-memory events; payloads never enter the periodic bridge snapshot."""
    def __init__(self, network_snapshot=None):
        self.network_snapshot = network_snapshot
        self._lock = threading.Lock()
        self._entries = OrderedDict()

    def _put(self, entry):
        # Limits in _Trace bound every field before it reaches this store.
        with self._lock:
            identifier = entry['id']
            self._entries[identifier] = copy.deepcopy(entry)
            while len(self._entries) > 100:
                self._entries.popitem(last=False)

    def snapshot(self):
        keys = ('id', 'at', 'event', 'level', 'title', 'summary', 'method', 'path',
                'status', 'durationMs', 'credentialIssue', 'outcome', 'errorCode', 'failureType')
        with self._lock:
            return [{key: item[key] for key in keys if key in item} for item in self._entries.values()]

    def detail(self, identifier):
        if not isinstance(identifier, str) or len(identifier) > 64:
            return None
        with self._lock:
            return copy.deepcopy(self._entries.get(identifier))

    def event(self, code, level=None, **fields):
        """Management events contain application-owned labels, never raw payloads."""
        try:
            entry = {'id': uuid.uuid4().hex, 'at': time.time(), 'event': _plain(code, 96),
                     'credentialIssue': code in ('session_required', 'session_invalid',
                         'session_storage_error', 'no_available_account', 'upstream_authentication_error',
                         'upstream_permission_denied', 'invalid_session_import')}
            if level in _LEVELS:
                entry['level'] = level
            for key in ('title', 'summary'):
                if isinstance(fields.get(key), str):
                    entry[key] = _plain(fields[key], 256)
            self._put(entry)
        except Exception:
            # Diagnostics must not turn an otherwise successful operation into a failure.
            pass


class _Trace:
    def __init__(self, store, scope):
        self.store = store
        self.started = time.monotonic()
        self.secrets = []
        # Fingerprints detect truncated/quoted request-body fragments without
        # retaining another full copy of the prompt. If the bounded budget is
        # exceeded, raw diagnostic strings are omitted rather than risking a
        # leak; status, business code, phase and exception class remain usable.
        self.private_fingerprints = set()
        self.private_widths = set()
        self.private_characters = 0
        self.private_overflow = False
        self.closed = False
        self.failure = None
        self.status = None
        self.complete = False
        self.disconnected = False
        self.error_response = bytearray()
        self.credential_codes = set()
        self.upstream_starts = {}
        self.entry = {'id': uuid.uuid4().hex, 'at': time.time(), 'event': 'api_request',
                      'level': 'info', 'title': '正在处理请求', 'summary': '请求已到达本机服务',
                      'method': _plain(scope.get('method', ''), 12), 'path': '',
                      'status': None, 'durationMs': None, 'credentialIssue': False,
                      'outcome': 'running', 'stage': 'received', 'parameters': {},
                      'stages': [], 'upstream': []}
        self.entry['requestId'] = self.entry['id']
        self.entry['startedAt'] = self.entry['at']
        # Credentials are only held for this request, for replacement in errors.
        for name, value in scope.get('headers', []):
            label = name.decode('latin-1').lower()
            if _SECRET_KEY.search(label):
                secret = value.decode('latin-1')
                self.register(secret)
                if label == 'authorization':
                    scheme, _, token = secret.partition(' ')
                    self.register(token)
                    if scheme.lower() == 'basic':
                        try:
                            decoded = base64.b64decode(token, validate=True).decode('utf-8')
                            self.register(decoded)
                            self.register(decoded.partition(':')[2])
                        except (ValueError, UnicodeError):
                            pass
        try:
            query = scope.get('query_string', b'').decode('utf-8', errors='replace')
            for name, value in parse_qsl(query[:4096], keep_blank_values=True, max_num_fields=48):
                if name not in ('alt', 'pageSize'):
                    self.register(value)
        except ValueError:
            query = ''
        app = scope.get('app')
        settings = getattr(getattr(getattr(app, 'state', None), 'playground', None), 'settings', None)
        self.register(getattr(settings, 'password', ''))
        path = scope.get('path', '/')
        self.entry['path'] = _safe_url(path, self.redact)[:512]
        server = scope.get('server') or ('localhost', 0)
        hostname = str(server[0])
        if ':' in hostname:
            hostname = '[' + hostname + ']'
        self.entry['address'] = _safe_url(
            f"{scope.get('scheme', 'http')}://{hostname}:{server[1]}{path}" + ('?' + query if query else ''),
            self.redact)
        self.stage('received')

    def register(self, value):
        if not isinstance(value, str) or not value:
            return
        if len(self.secrets) >= 192:
            # Later rotated credentials must not silently escape redaction.
            if value not in self.secrets:
                self.private_overflow = True
            return
        # A 64 MiB prompt must not be duplicated in diagnostic state.
        value = value[:8192]
        if value not in self.secrets:
            self.secrets.append(value)

    def register_private(self, value):
        if not isinstance(value, str) or not value or self.private_overflow:
            return
        self.private_characters += len(value)
        if self.private_characters > _MAX_PRIVATE_CHARACTERS:
            self.private_overflow = True
            return
        width = min(8, len(value))
        self.private_widths.add(width)
        for start in range(len(value)-width+1):
            # Hash values are process-private and never included in a log.
            self.private_fingerprints.add((width, hash(value[start:start+width])))
            if len(self.private_fingerprints) > _MAX_PRIVATE_FINGERPRINTS:
                self.private_overflow = True
                break

    def private_text(self, value):
        if self.private_overflow:
            return '[请求正文超出脱敏比对预算，已隐藏可能回显输入的原始错误文本]'
        for width in self.private_widths:
            if any((width, hash(value[start:start+width])) in self.private_fingerprints
                   for start in range(len(value)-width+1)):
                return '[原始错误可能包含请求正文片段，已脱敏]'
        return value

    def redact(self, value, limit=_MAX_RAW):
        text = _plain(value, limit * 3)
        # Error strings may contain a URL with credentials unrelated to the
        # public request URL. Never keep arbitrary URL query values or userinfo.
        text = re.sub(r'https?://[^\s<>"\']+', lambda match: _safe_url(match.group(0)), text)
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, _REDACTED)
            escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
            if escaped != secret:
                text = text.replace(escaped, _REDACTED)
            encoded = quote(secret, safe='')
            if encoded != secret:
                text = re.sub(re.escape(encoded), _REDACTED, text, flags=re.I)
        # Also protect values not yet registered, e.g. errors before session load.
        text = re.sub(r'(?im)(?:authorization|(?:set-)?cookie)\s*:\s*[^\r\n]+',
                      '[认证请求头已脱敏]', text)
        text = re.sub(r'(?i)\b(?:Bearer|Basic|SAPISID(?:1P|3P)?HASH)\s+[A-Za-z0-9_+/.=:-]+',
                      '[认证信息已脱敏]', text)
        text = re.sub(r'(?i)(["\']?(?:api[_-]?key|key|password|secret|token|proof|'
                      r'access_token|refresh_token|authorization|cookie|sapisid)["\']?\s*[=:]\s*)'
                      r'(?:"[^"\r\n]*"|\x27[^\x27\r\n]*\x27|[^\s&,;}]+)',
                      lambda match: match.group(1) + _REDACTED, text)
        text = re.sub(r'\b[A-Za-z0-9_-]{64,}\b', '[长令牌已隐藏]', text)
        text = re.sub(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', '[邮箱已隐藏]', text, flags=re.I)
        return text[:limit] + ('…[已截断]' if len(text) > limit else '')

    def raw(self, value):
        """Keep the original diagnostic structure, redacting sensitive JSON leaves."""
        structured = False
        def clean(item, depth=0):
            if depth > 6:
                return '[嵌套内容已省略]'
            if isinstance(item, dict):
                return {self.redact(key, 80): (_REDACTED if _SECRET_KEY.search(str(key)) or
                        _PRIVATE_CONTENT_KEY.search(str(key)) else clean(child, depth + 1))
                        for key, child in list(item.items())[:32]}
            if isinstance(item, list):
                return [clean(child, depth + 1) for child in item[:16]]
            if isinstance(item, str):
                return self.private_text(self.redact(item, _MAX_RAW))
            return item if item is None or type(item) in (bool, int, float) else self.redact(item)
        if isinstance(value, (dict, list)):
            value = json.dumps(clean(value), ensure_ascii=False)
            structured = True
        elif isinstance(value, str) and len(value) <= 16384:
            try:
                value = json.dumps(clean(json.loads(value)), ensure_ascii=False)
                structured = True
            except (ValueError, RecursionError):
                pass
        # Apply to incomplete JSON/plain text as well: an upstream server can
        # quote just the beginning or middle of a submitted message.
        text = self.redact(value, _MAX_RAW)
        return text if structured else self.private_text(text)

    def publish(self):
        if not self.closed:
            self.store._put(self.entry)

    def stage(self, name):
        name = self.redact(name, 96)
        if not self.entry['stages'] or self.entry['stage'] != name:
            self.entry['stage'] = name
            if len(self.entry['stages']) < _MAX_STAGES:
                self.entry['stages'].append({'at': time.time(), 'stage': name})
            else:
                self.entry['stagesTruncated'] = True
        self.publish()

    def error(self, error):
        status = getattr(error, 'status', getattr(error, 'status_code', 500))
        code = getattr(error, 'code', None)
        if not isinstance(code, str):
            code = ('authentication_error' if status == 401 else
                    'not_found' if status in (404, 405) else
                    'invalid_request_error' if 400 <= status < 500 else 'service_error')
        if self.failure is not None and code == 'service_error':
            return
        is_validation = type(error).__name__ == 'RequestValidationError'
        raw = '' if is_validation else getattr(error, 'diagnostic_raw', str(error))
        # Validation exceptions contain the entire submitted input. Keep only
        # safe locations/types and exclude their input/context payloads.
        if is_validation:
            code, status = 'invalid_request_error', 400
            raw = json.dumps([{'type': item.get('type'), 'loc': item.get('loc')}
                              for item in error.errors()[:8]], ensure_ascii=False)
        upstream_status = getattr(error, 'diagnostic_status', None)
        # Only the exception's own response can supply this field. A directory
        # lookup may have tolerated an earlier account's HTTP failure; borrowing
        # that status would mislabel a later local parameter error as a 5xx.
        kind = _failure_type(code, status, upstream_status)
        if code == 'session_required' and any(marker in str(raw).lower() for marker in (
                'session missing.', 'missing required signing cookie')):
            kind = 'credential_missing'
        diagnostic_type = getattr(error, 'diagnostic_type', type(error).__name__)
        if kind == 'waa':
            if 'Timeout' in diagnostic_type:
                kind = 'timeout'
            elif diagnostic_type in ('ConnectError', 'NetworkError', 'ReadError', 'WriteError'):
                kind = 'connection'
        detail = {'type': kind, 'typeLabel': _TYPE_LABELS[kind], 'code': self.redact(code, 96),
                  'message': ('请求体须为有效 JSON 对象，请检查字段类型。' if is_validation else
                              self.redact(error.public_message, 512) if hasattr(error, 'public_message') else
                              self.private_text(self.redact(getattr(error, 'detail', str(error)), 512))),
                  'raw': self.raw(raw), 'exceptionType': self.redact(diagnostic_type, 96),
                  'httpStatus': status, 'businessHttpStatus': status}
        if upstream_status is not None:
            detail['upstreamStatus'] = upstream_status
        upstream_code = getattr(error, 'diagnostic_upstream_code', None)
        if upstream_code is not None:
            detail['upstreamCode'] = self.redact(upstream_code, 128)
        self.failure = detail
        self.entry['error'] = detail
        self.entry['credentialIssue'] = kind.startswith('credential_') or kind == 'authentication'

    def finish(self):
        if self.closed:
            return
        if self.failure is None and (self.disconnected or not self.complete or self.status is None or not 200 <= self.status < 400):
            code = 'client_disconnect' if self.disconnected else 'stream_interrupted' if not self.complete else 'http_error'
            status = self.status if self.status is not None else 499 if self.disconnected else 500
            error = PlaygroundError('客户端已断开连接。' if self.disconnected else
                                    '响应未完整结束。' if not self.complete else f'HTTP {status}', status, code)
            if self.complete and self.error_response:
                # Covers middleware-generated failures (for example a rejected
                # CORS preflight), which have no route exception to annotate.
                error.diagnostic_raw = bytes(self.error_response).decode('utf-8', errors='replace')
            self.error(error)
        entry = self.entry
        entry['status'] = self.status
        entry['finishedAt'] = time.time()
        entry['durationMs'] = max(0, round((time.monotonic() - self.started) * 1000))
        entry['outcome'] = 'failure' if self.failure else 'success'
        entry['level'] = 'failure' if self.failure else 'success'
        entry['title'] = '请求失败' if self.failure else '请求成功'
        entry['summary'] = (self.failure['typeLabel'] + ' · ' + self.failure['message']
                            if self.failure else '响应已完成')[:200]
        if self.failure:
            entry['errorCode'] = self.failure['code']
            entry['failureType'] = self.failure['type']
            entry['error']['httpStatus'] = self.status
            if self.store.network_snapshot:
                from .network_info import diagnose_failure
                try:
                    entry['network'] = diagnose_failure(self.store.network_snapshot(),
                                                        self.failure['type'], entry['stage'])
                except Exception:
                    entry['network'] = {'note': '未能读取网络缓存；本次请求未额外发起网络查询。'}
        self.publish()
        self.closed = True
        self.secrets.clear()
        self.private_fingerprints.clear()
        self.private_widths.clear()
        self.error_response.clear()


def _failure_type(code, status=None, upstream_status=None):
    if code in ('authentication_error', 'invalid_api_key'):
        return 'authentication'
    if code == 'no_available_account':
        return 'credential_missing'
    if code == 'session_required':
        return 'credential_expired'
    if code in ('session_invalid', 'upstream_authentication_error', 'invalid_session_import'):
        return 'credential_invalid'
    if code == 'account_busy':
        return 'queue_timeout'
    if code == 'upstream_permission_denied':
        return 'permission'
    if code == 'client_disconnect':
        return 'client_disconnect'
    if code == 'request_cancelled':
        return 'cancelled'
    if code == 'stream_interrupted':
        return 'interrupted'
    if code == 'upstream_timeout' or status in (408, 504):
        return 'timeout'
    if code == 'upstream_connection_error':
        return 'connection'
    if code == 'rate_limit' or status == 429:
        return 'rate_limit'
    if isinstance(upstream_status, int) and upstream_status >= 500:
        return 'upstream_5xx'
    if code.startswith('waa_'):
        return 'waa'
    if code in ('protocol_error', 'page_initialization_failed'):
        return 'protocol'
    if status in (404, 405) or code in ('not_found', 'model_not_found'):
        return 'not_found'
    if status in (400, 413, 422) or code.startswith('invalid_') or code == 'unsupported_method':
        return 'parameters'
    if status == 401:
        return 'authentication'
    if status == 403:
        return 'permission'
    return 'server_error'


def _with_trace(callback):
    trace = _CURRENT.get()
    if trace is None or trace.closed:
        return
    try:
        callback(trace)
    except Exception:
        pass


def register_secrets(*values):
    _with_trace(lambda trace: [trace.register(value) for value in values])


def set_stage(stage):
    _with_trace(lambda trace: trace.stage(stage))


def record_error(error):
    def apply(trace):
        trace.error(error)
        if getattr(error, 'code', None) not in trace.credential_codes:
            record_credential_issue(error)
    _with_trace(apply)


def record_parameters(body):
    """Only scalar generation options and content counts; no message/media text."""
    if not isinstance(body, dict):
        return
    def apply(trace):
        # Register bounded string leaves for redaction, never publish them.
        pending = [(body, False)]
        visited = 0
        while pending and visited < 256:
            item, private = pending.pop()
            visited += 1
            if isinstance(item, dict):
                if len(item) > 48:
                    trace.private_overflow = True
                pending.extend((value, private or bool(_SECRET_KEY.search(str(key))) or
                                bool(_PRIVATE_CONTENT_KEY.search(str(key))))
                               for key, value in list(item.items())[:48]
                               if key not in ('role', 'type', 'mimeType', 'mime_type', 'model'))
            elif isinstance(item, list):
                if len(item) > 48:
                    trace.private_overflow = True
                pending.extend((value, private) for value in item[:48])
            elif isinstance(item, str) and private:
                trace.register_private(item)
        if pending:
            trace.private_overflow = True
        result = trace.entry['parameters']
        model = body.get('model')
        if isinstance(model, str) and re.fullmatch(r'(?:models/)?[A-Za-z0-9_.-]{1,128}', model):
            result['model'] = trace.redact(model, 160)
        for key in ('stream', 'temperature', 'top_p', 'top_k', 'max_tokens', 'max_completion_tokens',
                    'frequency_penalty', 'presence_penalty', 'seed', 'n'):
            value = body.get(key)
            if type(value) in (bool, int, float) and (type(value) is bool or math.isfinite(value)):
                result[key] = value
        config = body.get('generationConfig') or body.get('generation_config')
        if isinstance(config, dict):
            for key in ('temperature', 'topP', 'topK', 'maxOutputTokens', 'candidateCount', 'seed',
                        'presencePenalty', 'frequencyPenalty'):
                value = config.get(key)
                if type(value) in (int, float) and math.isfinite(value):
                    result[key] = value
            modalities = config.get('responseModalities')
            if isinstance(modalities, list):
                result['responseModalities'] = [item for item in modalities[:4]
                                                if item in ('TEXT', 'IMAGE', 'AUDIO', 'VIDEO')]
        for key, label in (('messages', 'messageCount'), ('contents', 'contentCount'), ('tools', 'toolCount')):
            if isinstance(body.get(key), list):
                result[label] = len(body[key])
        result['contentNote'] = '仅记录参数与内容数量；消息正文、附件及工具参数不写入日志。'
        trace.publish()
    _with_trace(apply)


def note_upstream(method, url, status=None, code=None, raw=None):
    def apply(trace):
        address = _safe_url(url, trace.redact)
        history = trace.entry['upstream']
        found = next((entry for entry in reversed(history) if entry['url'] == address and
                      entry['method'] == method), None)
        if found is None or status is None and raw is None:
            if len(history) >= _MAX_UPSTREAM:
                trace.entry['upstreamTruncated'] = True
                # Preserve recent attempts, especially the eventual failing RPC.
                removed = history.pop(0)
                trace.upstream_starts.pop(id(removed), None)
            found = {'at': time.time(), 'method': trace.redact(method, 12), 'url': address}
            history.append(found)
            trace.upstream_starts[id(found)] = time.monotonic()
        if type(status) is int:
            found['status'] = status
            found['durationMs'] = max(0, round((time.monotonic() - trace.upstream_starts.get(id(found), time.monotonic())) * 1000))
        if code is not None:
            found['code'] = trace.redact(code, 128)
        if raw is not None:
            found['raw'] = trace.raw(raw)[:1024]
        trace.publish()
    _with_trace(apply)


def record_credential_issue(error, account_id=None):
    def apply(trace):
        code = getattr(error, 'code', None)
        if code is None and getattr(error, 'status_code', None) == 401:
            code = 'authentication_error'
        if code not in ('session_required', 'session_invalid', 'upstream_authentication_error',
                        'invalid_session_import', 'no_available_account', 'upstream_permission_denied',
                        'authentication_error'):
            return
        status = getattr(error, 'status', getattr(error, 'status_code', 503))
        trace.credential_codes.add(code)
        kind = _failure_type(code, status, getattr(error, 'diagnostic_status', None))
        entry = {'id': uuid.uuid4().hex, 'at': time.time(), 'event': 'credential_issue',
                 'level': 'warning', 'title': '接口鉴权失败' if kind == 'authentication' else '凭证异常',
                 'credentialIssue': True,
                 'summary': trace.redact(getattr(error, 'public_message', '接口密钥不正确，请检查客户端认证设置。'
                                                if kind == 'authentication' else '凭证检查未通过。'), 200),
                 'requestId': trace.entry['id'], 'method': trace.entry['method'], 'path': trace.entry['path'],
                 'stage': trace.entry['stage'],
                 'errorCode': trace.redact(code, 96), 'failureType': kind,
                 'credential': {'kind': kind, 'accountId': str(account_id)[:8] if account_id else '未选择'},
                 'error': {'type': kind, 'typeLabel': _TYPE_LABELS[kind], 'code': trace.redact(code, 96),
                           'message': trace.redact(getattr(error, 'public_message', str(error)), 512),
                           'raw': trace.raw(getattr(error, 'diagnostic_raw', str(error))),
                           'businessHttpStatus': status}}
        upstream_status = getattr(error, 'diagnostic_status', None)
        if upstream_status is not None:
            entry['error']['upstreamStatus'] = upstream_status
        upstream_code = getattr(error, 'diagnostic_upstream_code', None)
        if upstream_code is not None:
            entry['error']['upstreamCode'] = trace.redact(upstream_code, 128)
        trace.store._put(entry)
    _with_trace(apply)


class RequestLoggingMiddleware:
    def __init__(self, app, logs):
        self.app = app
        self.logs = logs

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        try:
            trace = _Trace(self.logs, scope)
        except Exception:
            return await self.app(scope, receive, send)
        token = _CURRENT.set(trace)

        async def observed_receive():
            message = await receive()
            if message['type'] == 'http.disconnect' and not trace.complete:
                trace.disconnected = True
            return message

        async def observed_send(message):
            try:
                await send(message)
            except OSError as error:
                trace.disconnected = True
                diagnostic = PlaygroundError('客户端连接中断，响应未完整发送。', 499, 'client_disconnect')
                diagnostic.diagnostic_raw = str(error)
                diagnostic.diagnostic_type = type(error).__name__
                record_error(diagnostic)
                raise
            if message['type'] == 'http.response.start':
                trace.status = message['status']
            elif message['type'] == 'http.response.body':
                if trace.failure is None and trace.status is not None and trace.status >= 400:
                    trace.error_response.extend(message.get('body', b'')[:max(0, _MAX_RAW-len(trace.error_response))])
                if not message.get('more_body', False):
                    trace.complete = True

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            if trace.failure is None:
                record_error(PlaygroundError('请求被取消，响应未完整发送。', 499, 'request_cancelled'))
            raise
        except Exception as error:
            if trace.failure is None:
                record_error(error)
            if trace.status is None and not trace.disconnected:
                # Starlette's outer ServerErrorMiddleware writes the 500 response
                # after this wrapper unwinds; its fixed handler is in main.py.
                trace.status = 500
            raise
        finally:
            try:
                trace.finish()
            except Exception:
                pass
            finally:
                trace.closed = True
                trace.secrets.clear()
                trace.private_fingerprints.clear()
                trace.private_widths.clear()
                trace.error_response.clear()
                _CURRENT.reset(token)
