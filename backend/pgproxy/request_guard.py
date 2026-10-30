"""Bound public JSON uploads before FastAPI reads or parses the body."""
import asyncio
import re

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import authenticate_user


MAX_REQUEST_BYTES = 64 * 1024 * 1024
UPLOAD_TIMEOUT_SECONDS = 60
MAX_HTTP_CONCURRENCY = 16
_MODEL_PATH = re.compile(r'/v1(?:beta)?/models(?:/[^/]+)?')
_OPERATION_PATH = re.compile(r'/v1(?:beta)?/models/[^/]+:[^/]+')


class RequestGuardMiddleware:
    """Authenticate from headers/query, then buffer one bounded JSON request.

    Downstream receives the body once; subsequent receive calls still reach the
    socket, so cancellation/disconnect handling of streaming responses is intact.
    OPTIONS stays with CORS. Unknown paths retain the router's ordinary 404/405.
    """
    def __init__(self, app, max_body_bytes=MAX_REQUEST_BYTES,
                 upload_timeout=UPLOAD_TIMEOUT_SECONDS):
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.upload_timeout = upload_timeout

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '').rstrip('/')
        protected = (scope.get('method') == 'GET' and _MODEL_PATH.fullmatch(path)
                     or scope.get('method') == 'POST' and
                     (path == '/v1/chat/completions' or _OPERATION_PATH.fullmatch(path)))
        if scope['type'] != 'http' or not protected:
            return await self.app(scope, receive, send)

        request = Request(scope)
        payload = None
        try:
            authenticate_user(request)
            if scope['method'] == 'POST':
                lengths = request.headers.getlist('content-length')
                if lengths:
                    value = lengths[0]
                    if len(lengths) != 1 or not value.isascii() or not value.isdecimal():
                        raise HTTPException(400, 'Content-Length 无效，请检查客户端请求。')
                    try:
                        declared = int(value)
                    except ValueError:
                        raise HTTPException(400, 'Content-Length 无效，请检查客户端请求。') from None
                    if declared > self.max_body_bytes:
                        raise HTTPException(413, '请求体过大，请缩小文本或附件后重试。')

                async def collect():
                    data = bytearray()
                    while True:
                        message = await receive()
                        if message['type'] == 'http.disconnect':
                            return None
                        if message['type'] != 'http.request':
                            continue
                        chunk = message.get('body', b'')
                        if len(data) + len(chunk) > self.max_body_bytes:
                            raise HTTPException(413, '请求体过大，请缩小文本或附件后重试。')
                        data.extend(chunk)
                        if not message.get('more_body', False):
                            return bytes(data)

                try:
                    payload = await asyncio.wait_for(collect(), self.upload_timeout)
                except TimeoutError:
                    raise HTTPException(408, '请求体上传超时，请检查连接后重试。') from None
                if payload is None:
                    return
        except HTTPException as error:
            headers = dict(error.headers or {})
            # An early response must not reuse an HTTP/1 connection whose body
            # remains unread. Uvicorn applies transport flow control meanwhile.
            if scope.get('http_version', '1.1').startswith('1.'):
                headers['Connection'] = 'close'
            message = ('接口密钥不正确，请检查客户端认证设置。'
                       if error.status_code == 401 else error.detail)
            await JSONResponse({'error': {
                'message': message, 'code': error.status_code,
                'type': 'authentication_error' if error.status_code == 401 else 'invalid_request_error',
            }}, status_code=error.status_code, headers=headers)(scope, receive, send)
            return

        replayed = False

        async def replay():
            nonlocal replayed, payload
            if payload is not None and not replayed:
                replayed = True
                message = {'type': 'http.request', 'body': payload, 'more_body': False}
                payload = None
                return message
            return await receive()

        await self.app(scope, replay, send)
