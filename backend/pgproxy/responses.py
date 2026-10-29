"""HTTP/SSE response handling shared by OpenAI and native Gemini routes."""
import json
import time
import uuid
import anyio
from fastapi.responses import StreamingResponse
from .errors import PlaygroundError
from .openai_transformers import OpenAIProjector, finish_reason, usage_metadata, gemini_response_to_openai
from .request_statistics import mark_request_failed


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


async def generation_response(client,model,payload,streaming=False,openai=False,sse=True,include_usage=False,request=None):
    source = client.generate(model,payload)
    if not streaming:
        result = await collect(source)
        return gemini_response_to_openai(result,model) if openai else result
    # Establish upstream and validate the first frame before committing HTTP 200.
    try:
        first = await anext(source)
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
        async def chunks():
            yield first
            async for chunk in source:
                yield chunk
        try:
            if openai:
                yield event({'role':'assistant','content':''})
            elif not sse:
                yield '['
            async for chunk in chunks():
                if openai:
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
