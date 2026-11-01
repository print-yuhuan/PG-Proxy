"""Runs installed Python modules on Android, with isolated local fault scenarios.

This asset belongs only to the instrumentation APK. It cannot select a real
credential, change saved settings, or reach Google.
"""
import asyncio
import json
import socket
import time
from types import SimpleNamespace

import httpx

from pgproxy.errors import PlaygroundError
from pgproxy.main import create_app
from pgproxy.request_logging import LogStore, register_secrets, set_stage


_KEY = 'instrumentation-only-interface-secret'
_COOKIE = 'instrumentation-only-google-cookie'
_PROOF = 'instrumentation-only-proof-secret'


class FixtureClient:
    settings = SimpleNamespace(password=_KEY)
    mode = 'missing'

    async def models(self):
        set_stage('credential')
        if self.mode == 'missing':
            raise PlaygroundError('Google session missing.', 503, 'session_required')
        if self.mode == 'expired':
            failure = PlaygroundError('Session unavailable', 503, 'session_required')
            failure.diagnostic_raw = 'Google signing cookie has expired: SAPISID'
            raise failure
        set_stage('model_catalog')
        register_secrets(_COOKIE, _PROOF)
        if self.mode == 'network':
            # A real loopback connection failure, not an Internet request.
            with socket.socket() as reserved:
                reserved.bind(('127.0.0.1', 0))
                port = reserved.getsockname()[1]
                async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
                    try:
                        await client.get(f'http://127.0.0.1:{port}/')
                    except httpx.HTTPError as original:
                        failure = PlaygroundError('Cannot connect to upstream', 502, 'upstream_connection_error')
                        failure.diagnostic_raw = str(original)
                        failure.diagnostic_type = type(original).__name__
                        raise failure from None
            raise AssertionError('Non-listening loopback socket unexpectedly accepted a connection')
        if self.mode == 'timeout':
            failure = PlaygroundError('Upstream timed out', 504, 'upstream_timeout')
            failure.diagnostic_raw = 'ReadTimeout: waiting for response headers'
            failure.diagnostic_type = 'ReadTimeout'
            raise failure
        failure = PlaygroundError('Upstream rejected request', 502, 'upstream_error')
        failure.diagnostic_status = 503
        failure.diagnostic_upstream_code = 'UNAVAILABLE'
        failure.diagnostic_raw = json.dumps({'error': {'status': 'UNAVAILABLE',
            'message': f'unavailable; Cookie: {_COOKIE}; proof={_PROOF}'}})
        raise failure

    async def generate(self, model, payload):
        set_stage('upstream_generate')
        register_secrets(_COOKIE, _PROOF)
        yield {'candidates': [{'index': 0, 'content': {'role': 'model', 'parts': [{'text': 'partial'}]}}]}
        failure = PlaygroundError('Stream connection reset', 502, 'upstream_connection_error')
        failure.diagnostic_raw = f'connection reset; Cookie: {_COOKIE}; proof={_PROOF}'
        failure.diagnostic_type = 'ReadError'
        raise failure

    async def close(self):
        pass


async def _run():
    client = FixtureClient()
    logs = LogStore(network_snapshot=lambda: {'data': {
        'ip': '8.8.8.8', 'countryCode': 'HK', 'country': 'Hong Kong',
        'riskScore': 90, 'riskFlags': ['proxy'], 'source': 'Synthetic fixture',
        'updatedAt': time.time(),
    }, 'error': ''})
    app = create_app(client, request_logs=logs)
    results = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture',
                                     headers={'Authorization': 'Bearer '+_KEY}) as api:
            for mode, status, kind in (
                ('missing', 503, 'credential_missing'), ('expired', 503, 'credential_expired'),
                ('network', 502, 'connection'), ('timeout', 504, 'timeout'), ('upstream', 502, 'upstream_5xx'),
            ):
                client.mode = mode
                response = await api.get('/v1/models')
                assert response.status_code == status, mode
                entry = [item for item in logs.snapshot() if item['event'] == 'api_request'][-1]
                detail = logs.detail(entry['id'])
                assert detail['outcome'] == 'failure', mode
                assert detail['error']['type'] == kind, mode
                assert detail['status'] == status and detail['durationMs'] >= 0, mode
                if mode in ('missing', 'expired'):
                    assert detail['credentialIssue'], mode
                if mode == 'upstream':
                    assert detail['error']['upstreamStatus'] == 503
                    assert detail['error']['upstreamCode'] == 'UNAVAILABLE'
                    assert detail['network']['data']['riskScore'] == 90
                    assert len(detail['network']['hints']) >= 2
                results.append(mode)
            for path, payload in (
                ('/v1/chat/completions', {'model': 'fixture-chat', 'messages': [{'role':'user', 'content':'fixture input'}], 'stream':True}),
                ('/v1beta/models/fixture-chat:streamGenerateContent?alt=sse', {'contents':[{'role':'user','parts':[{'text':'fixture input'}]}]}),
            ):
                response = await api.post(path, json=payload)
                assert response.status_code == 200 and 'error' in response.text
                assert '[DONE]' not in response.text
                entry = [item for item in logs.snapshot() if item['event'] == 'api_request'][-1]
                detail = logs.detail(entry['id'])
                assert detail['status'] == 200 and detail['outcome'] == 'failure'
                assert detail['error']['businessHttpStatus'] == 502
                assert detail['error']['type'] == 'connection'
                results.append('stream-openai' if path.startswith('/v1/chat') else 'stream-gemini')
            stored = json.dumps([logs.detail(row['id']) for row in logs.snapshot()])
            for secret in (_KEY, _COOKIE, _PROOF, 'fixture input'):
                assert secret not in stored, 'Secret was recorded'
    return {'ok': True, 'scenarios': len(results), 'passed': results,
            'realGoogleGeneration': False, 'networkFailure': 'real loopback refusal',
            'otherUpstreamFailures': 'controlled fixture', 'applicationStateChanged': False}


def run_scenarios():
    return json.dumps(asyncio.run(_run()))
