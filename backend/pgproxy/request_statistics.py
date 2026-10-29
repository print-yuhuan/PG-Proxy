"""Process-local outcomes for public catalogs, model details, token counts and generation."""
import re
import threading


_MODEL_DETAIL_PATH = re.compile(r'/v1(?:beta)?/models/[^/:]+')
_MODEL_OPERATION_PATH = re.compile(
    r'/v1(?:beta)?/models/[^/:]+:(?:generateContent|streamGenerateContent|countTokens)'
)
_OUTCOME_STATE = '_pg_request_outcome'


class RequestStatistics:
    def __init__(self):
        self._lock = threading.Lock()
        self._succeeded = 0
        self._failed = 0

    def record(self, succeeded):
        with self._lock:
            if succeeded:
                self._succeeded += 1
            else:
                self._failed += 1

    def snapshot(self):
        with self._lock:
            return {'requestsSucceeded':self._succeeded,'requestsFailed':self._failed}


class _RequestOutcome:
    def __init__(self):
        self.failed = False


def mark_request_failed(request):
    """Called by structured streaming error handling before it emits an error frame."""
    if request is not None:
        outcome = request.scope.get('state',{}).get(_OUTCOME_STATE)
        if isinstance(outcome,_RequestOutcome):
            outcome.failed = True


def _is_counted_request(scope):
    if scope['type'] != 'http' or scope.get('method') == 'OPTIONS':
        return False
    path = scope.get('path','').rstrip('/')
    return path in ('/v1/models','/v1beta/models','/v1/chat/completions') or bool(
        _MODEL_DETAIL_PATH.fullmatch(path) or _MODEL_OPERATION_PATH.fullmatch(path)
    )


class RequestStatisticsMiddleware:
    """Count after the response closes, including errors hidden by an HTTP 200 stream."""
    def __init__(self, app, statistics):
        self.app = app
        self.statistics = statistics

    async def __call__(self, scope, receive, send):
        if not _is_counted_request(scope):
            await self.app(scope,receive,send)
            return

        outcome = _RequestOutcome()
        scope.setdefault('state',{})[_OUTCOME_STATE] = outcome
        status = None
        response_complete = False

        async def observed_receive():
            message = await receive()
            if message['type'] == 'http.disconnect' and not response_complete:
                outcome.failed = True
            return message

        async def observed_send(message):
            nonlocal status, response_complete
            await send(message)
            if message['type'] == 'http.response.start':
                status = message['status']
            elif message['type'] == 'http.response.body' and not message.get('more_body',False):
                response_complete = True

        try:
            await self.app(scope,observed_receive,observed_send)
        except BaseException:
            outcome.failed = True
            raise
        finally:
            # This is the only accounting point, including cancellation paths.
            # Completion is server-side: a disconnect not reported by ASGI cannot
            # establish whether the remote client actually consumed the response.
            self.statistics.record(bool(response_complete and status is not None
                                        and 200 <= status < 300 and not outcome.failed))
