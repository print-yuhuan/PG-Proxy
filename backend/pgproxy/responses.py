"""HTTP/SSE response handling shared by OpenAI and native Gemini routes."""
import asyncio
import json
import time
import uuid
from contextlib import aclosing
import anyio
from fastapi.responses import StreamingResponse
from .errors import PlaygroundError
from .openai_transformers import OpenAIProjector, finish_reason, usage_metadata, gemini_response_to_openai
from .request_statistics import mark_request_failed
from .request_logging import record_error, record_parameters


SSE_HEARTBEAT_SECONDS = 15
_END_OF_STREAM = object()


class OwnedStreamResponse(StreamingResponse):
    def __init__(self, content, source, **kwargs):
        super().__init__(content, **kwargs)
        self.source = source

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also covers disconnect before the body generator starts.
            with anyio.CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                finally:
                    await self.source.aclose()


def dumps(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


async def collect(stream):
    result = {'candidates':[{'index':0,'content':{'role':'model','parts':[]}}]}
    try:
        async for chunk in stream:
            if 'promptFeedback' in chunk:
                result['promptFeedback'] = chunk['promptFeedback']
            for c in chunk.get('candidates',[]):
                target = result['candidates'][0]
                target['content']['parts'].extend(c.get('content',{}).get('parts',[]))
                for key, value in c.items():
                    if key not in ('content', 'index'):
                        target[key] = value
            if 'usageMetadata' in chunk:
                result['usageMetadata'] = chunk['usageMetadata']
            for key, value in chunk.items():
                if key not in ('candidates', 'usageMetadata', 'promptFeedback') and not key.startswith('_'):
                    result[key] = value
        return result
    finally:
        with anyio.CancelScope(shield=True):
            await stream.aclose()


async def _start_stream(source):
    # A metadata-only prefix must not turn an upstream failure into HTTP 200.
    metadata = {}
    async for chunk in source:
        candidates = chunk.get('candidates',[])
        if 'promptFeedback' in chunk or any(
                candidate.get('finishReason') or any(
                    part.get('text') or part.keys() - {'text','thought'}
                    for part in candidate.get('content',{}).get('parts',[]))
                for candidate in candidates):
            return (metadata,chunk) if metadata else (chunk,)
        metadata.update((key,value) for key,value in chunk.items() if key != 'candidates')
        for candidate in candidates:
            fields = {key:value for key,value in candidate.items() if key not in {'index','content'}}
            if fields:
                merged = metadata.setdefault('candidates',[{'index':0}])[0]
                merged.update(fields)
    raise PlaygroundError('Playground stream ended without output',code='stream_interrupted')


async def _stream_chunks(source, initial, heartbeat):
    for chunk in initial:
        yield chunk
    if not heartbeat:
        async for chunk in source:
            yield chunk
        return

    # At most one read remains pending during keepalives, including through cancellation.
    pending = None
    try:
        while True:
            pending = asyncio.create_task(anext(source,_END_OF_STREAM))
            while not (await asyncio.wait({pending},timeout=SSE_HEARTBEAT_SECONDS))[0]:
                yield None
            chunk = pending.result()
            pending = None
            if chunk is _END_OF_STREAM:
                return
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(pending,return_exceptions=True)


async def generation_response(client,model,payload,streaming=False,openai=False,sse=True,include_usage=False,request=None):
    record_parameters({'model': model, 'stream': streaming})
    source = client.generate(model,payload)
    if not streaming:
        result = await collect(source)
        return gemini_response_to_openai(result,model) if openai else result
    # Establish upstream and wait for output or a valid finish before committing HTTP 200.
    try:
        initial_chunks = await _start_stream(source)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await source.aclose()
        raise

    async def events():
        response_id,created = 'chatcmpl-'+uuid.uuid4().hex,int(time.time())
        first_json = True
        last_usage = None
        projector = OpenAIProjector()
        def event(delta=None,finish=None,usage=None):
            obj = dict(id=response_id,object='chat.completion.chunk',created=created,model=model,
                       choices=[] if usage is not None else [{'index':0,'delta':delta or {},'finish_reason':finish}])
            if usage is not None:
                obj['usage'] = usage
            return 'data: '+dumps(obj)+'\n\n'
        try:
            if openai:
                yield event({'role':'assistant','content':''})
            elif not sse:
                yield '['
            async with aclosing(_stream_chunks(source,initial_chunks,heartbeat=openai or sse)) as output:
                async for chunk in output:
                    if chunk is None:
                        yield ': keepalive\n\n'
                    elif openai:
                        last_usage = usage_metadata(chunk) or last_usage
                        delta = projector.project(chunk, streaming=True)
                        if delta:
                            yield event(delta)
                        reason = next((c['finishReason'] for c in chunk.get('candidates',[]) if 'finishReason' in c),None)
                        if reason or 'promptFeedback' in chunk:
                            yield event(finish=finish_reason(reason or 'SAFETY', tool_calls=bool(projector.tool_count)))
                    elif sse:
                        yield 'data: '+dumps(chunk)+'\n\n'
                    else:
                        yield ('' if first_json else ',')+dumps(chunk)
                        first_json = False
            if openai:
                if include_usage and last_usage is not None:
                    yield event(usage=last_usage)
                yield 'data: [DONE]\n\n'
            elif not sse:
                yield ']'
        except Exception as failure:
            error = failure if isinstance(failure,PlaygroundError) else PlaygroundError('Stream processing failed',code='protocol_error')
            if error is not failure:
                error.diagnostic_raw = str(failure)
                error.diagnostic_type = type(failure).__name__
            record_error(error)
            mark_request_failed(request)
            if openai or sse:
                yield 'data: '+dumps(error.body())+'\n\n'
            else:
                yield ('' if first_json else ',')+dumps(error.body())+']'
            # No success [DONE] after an interrupted generation.
        finally:
            with anyio.CancelScope(shield=True):
                await source.aclose()
    return OwnedStreamResponse(events(),source,media_type='text/event-stream' if openai or sse else 'application/json',
                             headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
