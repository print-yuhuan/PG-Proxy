"""Explicit Chat Completions mappings, without remote media fetch or fake signatures.

Opaque Google signatures round-trip via ``extra_content.google.thought_signature``
and ``extra_content.google.parts``. No account-independent signature cache is used.
"""
import base64
import copy
import json
import re
import time
import uuid
from .playground_protocol import invalid
from .errors import PlaygroundError


def _object(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise invalid('Invalid ' + label)
    return value


def _json_object(value, label):
    def nonfinite(_):
        raise ValueError('non-finite number')
    try:
        result = json.loads(value, parse_constant=nonfinite)
    except (TypeError, ValueError, RecursionError):
        raise invalid(label + ' must be a JSON object') from None
    if not isinstance(result, dict):
        raise invalid(label + ' must be a JSON object')
    return result


def _name(value, label='function name'):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]{0,63}', value):
        raise invalid('Invalid ' + label)
    return value


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 33 for c in value):
        raise invalid('Invalid tool call id')
    return value


def _google_extra(owner, *, native_parts=False):
    extra = owner.get('extra_content')
    if extra is None:
        return {}
    _object(extra, {'google'}, 'extra_content')
    google = _object(extra.get('google'), {'thought_signature', 'parts', 'groundingMetadata', 'citationMetadata'} if native_parts else {'thought_signature'}, 'Google extra_content')
    signature = google.get('thought_signature')
    if 'thought_signature' in google and (not isinstance(signature, str) or not signature):
        raise invalid('thought_signature must be a nonempty string')
    return google


def _signed(part, owner):
    signature = _google_extra(owner).get('thought_signature')
    if signature:
        part['thoughtSignature'] = signature
    return part


def _inline(data, mime):
    if not isinstance(data, str) or not data:
        raise invalid('Media data must be nonempty base64')
    try:
        base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raise invalid('Media data must be valid base64') from None
    return {'inlineData': {'mimeType': mime, 'data': data}}


def _parts(content, *, allow_empty=False):
    if isinstance(content, str):
        return [{'text': content}] if content or not allow_empty else []
    if content is None and allow_empty:
        return []
    if not isinstance(content, list) or not content:
        raise invalid('message.content must be a string or nonempty part array')
    parts = []
    for item in content:
        if not isinstance(item, dict):
            raise invalid('Content part must be an object')
        kind = item.get('type')
        if kind == 'text':
            _object(item, {'type', 'text', 'extra_content'}, 'text part')
            if not isinstance(item.get('text'), str):
                raise invalid('text must be a string')
            part = {'text': item['text']}
        elif kind == 'image_url':
            _object(item, {'type', 'image_url', 'extra_content'}, 'image part')
            image = _object(item.get('image_url'), {'url', 'detail'}, 'image_url')
            if image.get('detail', 'auto') != 'auto':
                raise invalid('image_url.detail supports auto only')
            url = image.get('url')
            match = re.fullmatch(r'data:(image/[A-Za-z0-9.+-]+);base64,(.+)', url) if isinstance(url, str) else None
            if not match:
                raise invalid('Images require a base64 data:image URL; remote downloads are not supported')
            part = _inline(match[2], match[1])
        elif kind == 'input_audio':
            _object(item, {'type', 'input_audio', 'extra_content'}, 'audio part')
            audio = _object(item.get('input_audio'), {'data', 'format'}, 'input_audio')
            formats = {'wav': 'audio/wav', 'mp3': 'audio/mpeg', 'flac': 'audio/flac', 'ogg': 'audio/ogg', 'aac': 'audio/aac', 'mp4': 'audio/mp4', 'webm': 'audio/webm'}
            mime = formats.get(audio.get('format')) if isinstance(audio.get('format'), str) else None
            if not mime:
                raise invalid('Unsupported input_audio.format')
            part = _inline(audio.get('data'), mime)
        else:
            raise invalid('Unsupported OpenAI content part')
        parts.append(_signed(part, item))
    return parts


def _messages(messages):
    if not isinstance(messages, list) or not messages:
        raise invalid('messages must be a nonempty array')
    contents, system, pending, used_ids = [], [], {}, set()
    for message in messages:
        _object(message, {'role', 'content', 'tool_calls', 'tool_call_id', 'name', 'reasoning_content', 'extra_content', 'images', 'audio_chunks'}, 'message field')
        role = message.get('role')
        if role not in ('user', 'assistant', 'system', 'developer', 'tool'):
            raise invalid('Supported roles: system, developer, user, assistant, tool')
        extra = _google_extra(message, native_parts=True)
        if role != 'assistant' and (extra or any(k in message for k in ('tool_calls', 'reasoning_content', 'images', 'audio_chunks'))):
            raise invalid('Tool calls and signed model history require assistant role')
        if role != 'tool' and ('tool_call_id' in message or 'name' in message):
            raise invalid('tool_call_id and name require tool role')
        if role == 'tool':
            call_id = _id(message.get('tool_call_id'))
            if call_id not in pending:
                raise invalid('Tool response has no matching preceding assistant tool call')
            name = pending.pop(call_id)
            if 'name' in message and message['name'] != name:
                raise invalid('Tool response name does not match its call')
            content = message.get('content')
            if isinstance(content, list):
                items = _parts(content)
                if any(set(p) != {'text'} for p in items):
                    raise invalid('Tool responses currently require text content')
                content = '\n'.join(p['text'] for p in items)
            if not isinstance(content, str):
                raise invalid('Tool response content must be text')
            try:
                response = _json_object(content, 'Tool response')
            except PlaygroundError:
                response = {'output': content}
            part = {'functionResponse': {'name': name, 'response': response, 'id': call_id}}
            if contents and contents[-1]['role'] == 'user' and all('functionResponse' in p for p in contents[-1]['parts']):
                contents[-1]['parts'].append(part)
            else:
                contents.append({'role': 'user', 'parts': [part]})
            continue
        if pending:
            raise invalid('Each assistant tool call needs a tool response before the next message')
        parts = _parts(message.get('content'), allow_empty=role == 'assistant')
        if 'images' in message:
            images = message['images']
            if not isinstance(images, list):
                raise invalid('images must be an array')
            if images:
                if any(not isinstance(p, dict) or p.get('type') != 'image_url' for p in images):
                    raise invalid('images must contain image_url parts')
                parts.extend(_parts(images))
        if 'audio_chunks' in message:
            chunks = message['audio_chunks']
            if not isinstance(chunks, list):
                raise invalid('audio_chunks must be an array')
            for audio in chunks:
                _object(audio, {'data', 'mime_type'}, 'audio chunk')
                mime = audio.get('mime_type')
                if not isinstance(mime, str) or not mime.startswith('audio/'):
                    raise invalid('audio_chunks requires an audio MIME type')
                parts.append(_inline(audio.get('data'), mime))
        reasoning = message.get('reasoning_content')
        if reasoning is not None:
            if not isinstance(reasoning, str):
                raise invalid('reasoning_content must be text')
            if reasoning:
                parts.insert(0, {'text': reasoning, 'thought': True})
        calls = message.get('tool_calls', [])
        if not isinstance(calls, list):
            raise invalid('tool_calls must be an array')
        for call in calls:
            _object(call, {'id', 'type', 'function', 'extra_content'}, 'tool call')
            if call.get('type') != 'function':
                raise invalid('Only function tool calls are supported')
            function = _object(call.get('function'), {'name', 'arguments'}, 'tool call function')
            name, call_id = _name(function.get('name')), _id(call.get('id'))
            if call_id in used_ids:
                raise invalid('Tool call ids must be unique')
            used_ids.add(call_id)
            pending[call_id] = name
            parts.append(_signed({'functionCall': {'name': name, 'args': _json_object(function.get('arguments'), 'Function arguments'), 'id': call_id}}, call))
        if 'parts' in extra:
            raw = extra['parts']
            if not isinstance(raw, list) or not raw or any(not isinstance(p, dict) for p in raw):
                raise invalid('extra_content.google.parts must be a nonempty native part array')
            if [p['functionCall'] for p in raw if 'functionCall' in p] != [p['functionCall'] for p in parts if 'functionCall' in p]:
                raise invalid('Native history function calls must match tool_calls')
            parts = copy.deepcopy(raw)
        elif extra.get('thought_signature'):
            if len(parts) != 1:
                raise invalid('Message thought_signature needs one part; use google.parts for mixed content')
            parts[0]['thoughtSignature'] = extra['thought_signature']
        if not parts:
            raise invalid('Message must contain text, media or a tool call')
        if role in ('system', 'developer'):
            if any(set(p) != {'text'} for p in parts):
                raise invalid('System messages must contain text only')
            system.extend(parts)
        else:
            contents.append({'role': 'model' if role == 'assistant' else 'user', 'parts': parts})
    if pending:
        raise invalid('Assistant tool calls require matching tool response messages')
    if not contents:
        raise invalid('At least one user or assistant message is required')
    return contents, system


def _tools(request, native):
    tools = request.get('tools', [])
    if not isinstance(tools, list):
        raise invalid('tools must be an array')
    output, declarations, names = [], [], set()
    builtins = {'google_search': 'googleSearch', 'image_search': 'imageSearch', 'code_execution': 'codeExecution', 'url_context': 'urlContext', 'google_maps': 'googleMaps'}
    for tool in tools:
        _object(tool, {'type', 'function'}, 'tool')
        kind = tool.get('type')
        if isinstance(kind, str) and kind in builtins and set(tool) == {'type'}:
            output.append({builtins[kind]: {}})
        elif kind == 'function':
            function = _object(tool.get('function'), {'name', 'description', 'parameters', 'strict'}, 'function declaration')
            name = _name(function.get('name'))
            if name in names:
                raise invalid('Function names must be unique')
            names.add(name)
            if function.get('strict', False) is not False:
                raise invalid('Strict OpenAI function validation is not supported; omit strict or set false')
            declaration = {'name': name}
            if 'description' in function:
                if not isinstance(function['description'], str):
                    raise invalid('Function description must be text')
                declaration['description'] = function['description']
            if 'parameters' in function:
                if not isinstance(function['parameters'], dict):
                    raise invalid('Function parameters must be a JSON Schema object')
                declaration['parameters'] = copy.deepcopy(function['parameters'])
            declarations.append(declaration)
        else:
            raise invalid('Supported tools: function, google_search, image_search, code_execution, url_context, google_maps')
    if declarations:
        output.append({'functionDeclarations': declarations})
    if 'tools' in request:
        native['tools'] = output
    if 'tool_choice' in request:
        choice = request['tool_choice']
        if not isinstance(choice, str) or choice not in ('auto', 'none'):
            raise invalid('Playground tool_choice supports auto and none; required and named calls are not enforced')
        if not declarations:
            raise invalid('tool_choice requires function tools')
        native['toolConfig'] = {'functionCallingConfig': {'mode': choice.upper()}}
    if 'parallel_tool_calls' in request:
        if not isinstance(request['parallel_tool_calls'], bool):
            raise invalid('parallel_tool_calls must be a boolean')
        if not request['parallel_tool_calls']:
            raise invalid('Playground cannot enforce parallel_tool_calls=false')
        if not declarations:
            raise invalid('parallel_tool_calls requires function tools')


def openai_request_to_gemini(request):
    supported = {'model', 'messages', 'stream', 'temperature', 'top_p', 'top_k', 'max_tokens', 'max_completion_tokens',
                 'stop', 'n', 'seed', 'response_format', 'stream_options', 'thinking_config', 'reasoning_effort',
                 'tools', 'tool_choice', 'parallel_tool_calls', 'modalities', 'image_config', 'speech_config', 'transcription_config', 'media_resolution'}
    _object(request, supported, 'OpenAI request field')
    model = request.get('model')
    if (not isinstance(model, str) or not model.removeprefix('models/') or len(model) > 256
            or '/' in model.removeprefix('models/') or any(c.isspace() or ord(c) < 32 for c in model)):
        raise invalid('A valid model is required')
    if type(request.get('n', 1)) is not int or request.get('n', 1) != 1:
        raise invalid('Only n=1 is supported')
    if not isinstance(request.get('stream', False), bool):
        raise invalid('stream must be a boolean')
    contents, system = _messages(request.get('messages'))
    native = {'contents': contents, 'generationConfig': {}}
    if system:
        native['systemInstruction'] = {'parts': system}
    config = native['generationConfig']
    if 'max_tokens' in request and 'max_completion_tokens' in request and request['max_tokens'] != request['max_completion_tokens']:
        raise invalid('max_tokens and max_completion_tokens conflict')
    for source, target in [('temperature', 'temperature'), ('top_p', 'topP'), ('top_k', 'topK'), ('max_tokens', 'maxOutputTokens'),
                           ('max_completion_tokens', 'maxOutputTokens'), ('seed', 'seed'), ('thinking_config', 'thinkingConfig'),
                           ('image_config', 'imageConfig'), ('speech_config', 'speechConfig'),
                           ('transcription_config', 'transcriptionConfig'), ('media_resolution', 'mediaResolution')]:
        if source in request:
            config[target] = copy.deepcopy(request[source])
    if 'reasoning_effort' in request:
        effort = request['reasoning_effort']
        if not isinstance(effort, str) or effort not in ('none', 'minimal', 'low', 'medium', 'high'):
            raise invalid('Unsupported reasoning_effort')
        if 'thinking_config' in request:
            raise invalid('Use either reasoning_effort or thinking_config')
        config['thinkingConfig'] = {'thinkingBudget': 0} if effort == 'none' else {'thinkingLevel': effort.upper()}
    if 'modalities' in request:
        modalities = request['modalities']
        if (not isinstance(modalities, list) or not modalities or any(not isinstance(m, str) or m not in ('text', 'image', 'audio') for m in modalities)
                or len(set(modalities)) != len(modalities)):
            raise invalid('modalities must contain unique text, image or audio values')
        config['responseModalities'] = [m.upper() for m in modalities]
    if 'stop' in request:
        config['stopSequences'] = [request['stop']] if isinstance(request['stop'], str) else request['stop']
    if 'response_format' in request:
        fmt = _object(request['response_format'], {'type', 'json_schema'}, 'response_format')
        kind = fmt.get('type')
        if kind in ('text', 'json_object') and set(fmt) == {'type'}:
            config['responseMimeType'] = 'application/json' if kind == 'json_object' else 'text/plain'
        elif kind == 'json_schema':
            schema = _object(fmt.get('json_schema'), {'name', 'description', 'schema', 'strict'}, 'json_schema')
            _name(schema.get('name'), 'schema name')
            if not isinstance(schema.get('schema'), dict) or ('description' in schema and not isinstance(schema['description'], str)):
                raise invalid('json_schema requires an object schema')
            if schema.get('strict', False) is not False:
                raise invalid('Strict OpenAI schema validation is not supported; omit strict or set false')
            config.update(responseMimeType='application/json', responseSchema=copy.deepcopy(schema['schema']))
        else:
            raise invalid('response_format supports text, json_object and json_schema')
    _tools(request, native)
    options = _object(request.get('stream_options', {}), {'include_usage'}, 'stream_options')
    if 'include_usage' in options and not isinstance(options['include_usage'], bool):
        raise invalid('stream_options.include_usage must be boolean')
    return model, native


def finish_reason(reason, *, tool_calls=False):
    if reason == 'STOP':
        return 'tool_calls' if tool_calls else 'stop'
    return 'length' if reason == 'MAX_TOKENS' else 'content_filter'


def usage_metadata(native):
    usage = native.get('usageMetadata')
    if usage is None:
        return None
    result = dict(prompt_tokens=usage.get('promptTokenCount', 0),
                  completion_tokens=usage.get('candidatesTokenCount', 0) + usage.get('thoughtsTokenCount', 0), total_tokens=usage.get('totalTokenCount', 0))
    if usage.get('thoughtsTokenCount'):
        result['completion_tokens_details'] = {'reasoning_tokens': usage['thoughtsTokenCount']}
    if usage.get('cachedContentTokenCount'):
        result['prompt_tokens_details'] = {'cached_tokens': usage['cachedContentTokenCount']}
    return result


class OpenAIProjector:
    """Per-response state for stable streamed tool indexes and opaque signatures."""
    def __init__(self):
        self.tool_count = 0
        self.pending_parts = []
        self.signed_history = False

    def project(self, native, *, streaming=False):
        delta = {}
        for candidate in native.get('candidates', []):
            preserved = []
            for part in candidate.get('content', {}).get('parts', []):
                preserved.append(copy.deepcopy(part))
                if 'text' in part:
                    key = 'reasoning_content' if part.get('thought') else 'content'
                    delta[key] = delta.get(key, '') + part['text']
                if 'executableCode' in part:
                    delta['content'] = delta.get('content', '') + '\n```python\n' + part['executableCode']['code'] + '\n```\n'
                if 'codeExecutionResult' in part:
                    delta['content'] = delta.get('content', '') + '\n```text\n' + part['codeExecutionResult']['output'] + '\n```\n'
                if 'functionCall' in part:
                    function = part['functionCall']
                    call_id = function.get('id') or 'call_' + uuid.uuid4().hex
                    preserved[-1]['functionCall']['id'] = call_id
                    call = {'id': call_id, 'type': 'function', 'function': {'name': function['name'], 'arguments': json.dumps(function.get('args', {}), ensure_ascii=False, separators=(',', ':'), allow_nan=False)}}
                    if streaming:
                        call['index'] = self.tool_count
                    self.tool_count += 1
                    if part.get('thoughtSignature'):
                        call['extra_content'] = {'google': {'thought_signature': part['thoughtSignature']}}
                    delta.setdefault('tool_calls', []).append(call)
                if 'inlineData' in part:
                    blob = part['inlineData']
                    mime = blob['mimeType']
                    if mime.startswith('image/'):
                        image = {'type': 'image_url', 'image_url': {'url': f"data:{mime};base64,{blob['data']}"}}
                        if part.get('thoughtSignature'):
                            image['extra_content'] = {'google': {'thought_signature': part['thoughtSignature']}}
                        delta.setdefault('images', []).append(image)
                    elif mime.startswith('audio/'):
                        # Keep the real upstream codec; no fictional OpenAI audio
                        # retrieval id, expiry time, or unimplemented transcoding.
                        delta.setdefault('audio_chunks', []).append({'data': blob['data'], 'mime_type': mime})
            if streaming:
                self.pending_parts.extend(preserved)
                self.signed_history = self.signed_history or any(p.get('thoughtSignature') or set(p) - {'text', 'thought', 'inlineData', 'functionCall', 'executableCode', 'codeExecutionResult'} for p in preserved)
                if self.signed_history:
                    delta.setdefault('extra_content', {}).setdefault('google', {}).setdefault('parts', []).extend(self.pending_parts)
                    self.pending_parts = []
            elif any(p.get('thoughtSignature') or set(p) - {'text', 'thought', 'inlineData', 'functionCall', 'executableCode', 'codeExecutionResult'} for p in preserved):
                delta.setdefault('extra_content', {}).setdefault('google', {}).setdefault('parts', []).extend(preserved)
            for name in ('groundingMetadata', 'citationMetadata'):
                if name in candidate:
                    delta.setdefault('extra_content', {}).setdefault('google', {})[name] = copy.deepcopy(candidate[name])
        return delta


def content_delta(native):
    return OpenAIProjector().project(native)


def gemini_response_to_openai(native, model, response_id=None):
    reason = native.get('candidates', [{}])[0].get('finishReason', 'SAFETY')
    projector = OpenAIProjector()
    message = {'role': 'assistant', 'content': '', **projector.project(native)}
    if message.get('tool_calls') and not message['content']:
        message['content'] = None
    result = dict(id=response_id or 'chatcmpl-' + uuid.uuid4().hex, object='chat.completion', created=int(time.time()), model=model,
                  choices=[{'index': 0, 'message': message, 'finish_reason': finish_reason(reason, tool_calls=bool(projector.tool_count))}])
    usage = usage_metadata(native)
    if usage is not None:
        result['usage'] = usage
    return result
