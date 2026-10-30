"""Best-effort Chat Completions options, without inventing conversational data.

Unknown optional controls are discarded before the strict protocol translator.
Actual messages, media, function results and Google signatures remain validated.
The defaults below are deterministic and normalization never changes its input.
"""
import copy
import json
import math
import re

from .errors import PlaygroundError
from .playground_protocol import invalid


_TOP = {
    'model', 'messages', 'stream', 'temperature', 'top_p', 'top_k', 'max_tokens',
    'max_completion_tokens', 'stop', 'n', 'seed', 'response_format',
    'stream_options', 'thinking_config', 'reasoning_effort', 'tools',
    'tool_choice', 'parallel_tool_calls', 'modalities', 'image_config',
    'speech_config', 'transcription_config', 'media_resolution',
}
_BUILTINS = {'google_search', 'image_search', 'code_execution', 'url_context', 'google_maps'}
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.:-]{0,63}')


def _pick(value, keys):
    return {k: v for k, v in value.items() if k in keys} if isinstance(value, dict) else value


def _boolean(value, default=False):
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ('true', 'false', '0', '1'):
        return value.strip().lower() in ('true', '1')
    return default


def _number(value, integer=False):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if type(value) not in (int, float):
        return None
    try:
        if not math.isfinite(value):
            return None
    except OverflowError:
        return None
    if integer:
        return int(value) if value == int(value) else None
    return value


def _extra(owner, *, native=False):
    """Keep opaque Google history intact, discard other provider metadata."""
    extra = owner.get('extra_content')
    if not isinstance(extra, dict) or 'google' not in extra:
        owner.pop('extra_content', None)
        return
    google = extra['google']
    if google is None:
        owner.pop('extra_content', None)
        return
    allowed = {'thought_signature', 'parts', 'groundingMetadata', 'citationMetadata'} if native else {'thought_signature'}
    google = _pick(google, allowed)
    if isinstance(google, dict):
        google = {k: v for k, v in google.items() if v is not None}
        if not google:
            owner.pop('extra_content', None)
            return
    # Invalid supplied signatures still reach the strict translator.
    owner['extra_content'] = {'google': google}


def _content(value):
    if not isinstance(value, list):
        return value
    parts = []
    for item in value:
        if not isinstance(item, dict):
            parts.append(item)
            continue
        kind = item.get('type')
        if kind is None:
            candidates = [key for key in ('text', 'image_url', 'input_audio') if key in item]
            if len(candidates) == 1:
                kind = candidates[0]
        if kind not in ('text', 'image_url', 'input_audio'):
            # Unknown content is not an optional generation parameter: do not
            # silently drop a user's attachment and answer a different question.
            parts.append(item)
            continue
        part = _pick(item, {'type', kind, 'extra_content'})
        part['type'] = kind
        if kind == 'image_url':
            image = part.get('image_url')
            if isinstance(image, str):
                image = {'url': image}
            # Playground has its own resolution controls; OpenAI detail is only
            # an optional hint and cannot be enforced by this transport.
            part['image_url'] = _pick(image, {'url'})
        elif kind == 'input_audio':
            part['input_audio'] = _pick(part.get('input_audio'), {'data', 'format'})
        _extra(part)
        parts.append(part)
    return parts


def _arguments(function):
    if not isinstance(function, dict):
        return function
    result = _pick(function, {'name', 'arguments'})
    if result.get('arguments') is None:
        result['arguments'] = '{}'
    elif isinstance(result['arguments'], dict):
        try:
            result['arguments'] = json.dumps(result['arguments'], ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise invalid('Function arguments must be a finite JSON object') from None
    return result


def _messages(value):
    if not isinstance(value, list):
        return value
    result, pending = [], {}
    # Caller-supplied IDs are never rewritten. Generated IDs only link an
    # otherwise unambiguous local history pair; they are not Google signatures.
    used = {call.get('id') for m in value if isinstance(m, dict)
            for call in (m.get('tool_calls') if isinstance(m.get('tool_calls'), list) else [])
            if isinstance(call, dict) and isinstance(call.get('id'), str)}
    for index, original in enumerate(value):
        if not isinstance(original, dict):
            result.append(original)
            continue
        message = _pick(original, {'role', 'content', 'tool_calls', 'tool_call_id', 'name',
                                   'reasoning_content', 'extra_content', 'images', 'audio_chunks'})
        if 'tool_calls' not in message and isinstance(original.get('function_call'), dict):
            message['tool_calls'] = [{'type': 'function', 'function': original['function_call']}]
        role = message.get('role')
        if role is None:
            role = ('tool' if message.get('tool_call_id') else
                    'assistant' if message.get('tool_calls') else 'user')
        if role == 'function':
            role = 'tool'
        elif role == 'model':
            role = 'assistant'
        message['role'] = role
        if role != 'tool':
            message.pop('name', None)
        message['content'] = _content(message.get('content'))
        for key in ('tool_calls', 'images', 'audio_chunks', 'reasoning_content'):
            if message.get(key) is None:
                message.pop(key, None)
        # Some SDKs attach empty assistant-only arrays to every role.
        if role != 'assistant':
            for key in ('tool_calls', 'images', 'audio_chunks', 'reasoning_content'):
                if message.get(key) in ([], ''):
                    message.pop(key, None)
        if isinstance(message.get('images'), list):
            message['images'] = _content(message['images'])
        if isinstance(message.get('audio_chunks'), list):
            message['audio_chunks'] = [_pick(v, {'data', 'mime_type'}) for v in message['audio_chunks']]
        _extra(message, native=True)
        calls = message.get('tool_calls')
        if isinstance(calls, list):
            clean = []
            for number, call in enumerate(calls):
                if not isinstance(call, dict):
                    clean.append(call)
                    continue
                call = _pick(call, {'id', 'type', 'function', 'extra_content'})
                if call.get('type') is None and isinstance(call.get('function'), dict):
                    call['type'] = 'function'
                call['function'] = _arguments(call.get('function'))
                if call.get('id') is None:
                    call_id = f'call_compat_{index}_{number}'
                    while call_id in used:
                        call_id += '_'
                    call['id'] = call_id
                    used.add(call_id)
                _extra(call)
                if isinstance(call.get('id'), str) and isinstance(call.get('function'), dict):
                    pending[call['id']] = call['function'].get('name')
                clean.append(call)
            message['tool_calls'] = clean
        if role == 'tool':
            if message.get('tool_call_id') is None:
                candidates = [key for key, name in pending.items()
                              if message.get('name') is None or message['name'] == name]
                if len(candidates) == 1:
                    message['tool_call_id'] = candidates[0]
            call_id = message.get('tool_call_id')
            if isinstance(call_id, str):
                pending.pop(call_id, None)
        result.append(message)
    return result


def _tools(value):
    if not isinstance(value, list):
        return []
    result, names = [], set()
    for tool in value:
        if not isinstance(tool, dict):
            continue
        kind = tool.get('type')
        if kind is None and isinstance(tool.get('function'), dict):
            kind = 'function'
        if isinstance(kind, str) and kind in _BUILTINS:
            result.append({'type': kind})
        elif kind == 'function' and isinstance(tool.get('function'), dict):
            function = _pick(tool['function'], {'name', 'description', 'parameters'})
            name = function.get('name')
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                continue
            if name in names:
                continue
            names.add(name)
            if not isinstance(function.get('description'), str):
                function.pop('description', None)
            if not isinstance(function.get('parameters'), dict):
                function['parameters'] = {'type': 'object', 'properties': {}}
            result.append({'type': 'function', 'function': function})
    return result


def _format(value):
    if not isinstance(value, dict):
        return None
    kind = value.get('type')
    if kind is None and isinstance(value.get('json_schema'), dict):
        kind = 'json_schema'
    if kind in ('text', 'json_object'):
        return {'type': kind}
    if kind == 'json_schema':
        schema = value.get('json_schema')
        if not isinstance(schema, dict) or not isinstance(schema.get('schema'), dict):
            return {'type': 'json_object'}
        schema = _pick(schema, {'name', 'description', 'schema'})
        if not isinstance(schema.get('name'), str) or not _NAME.fullmatch(schema['name']):
            schema['name'] = 'response'
        if not isinstance(schema.get('description'), str):
            schema.pop('description', None)
        return {'type': kind, 'json_schema': schema}
    return None


def normalize_openai_request(body):
    """Return compatible options; missing conversational data stays missing."""
    if not isinstance(body, dict):
        raise invalid('请求体须为 JSON 对象。')
    try:
        source = copy.deepcopy(body)
    except RecursionError:
        raise invalid('请求嵌套层级过深。') from None
    request = {k: v for k, v in source.items() if k in _TOP and v is not None}
    if isinstance(request.get('model'), str) and not request['model'].strip():
        request.pop('model')
    request['messages'] = _messages(request.get('messages'))
    request['stream'] = _boolean(request.get('stream'))
    request['n'] = 1
    options = request.get('stream_options')
    request['stream_options'] = ({'include_usage': _boolean(options['include_usage'])}
                                 if isinstance(options, dict) and 'include_usage' in options else {})
    for key in ('temperature', 'top_p', 'top_k', 'max_tokens', 'max_completion_tokens', 'seed'):
        if key in request:
            value = _number(request[key], integer=key not in ('temperature', 'top_p'))
            if value is None:
                request.pop(key)
            else:
                request[key] = value
    if 'max_completion_tokens' in request:
        request.pop('max_tokens', None)
    for key in ('thinking_config', 'image_config', 'speech_config', 'transcription_config'):
        if key in request and not isinstance(request[key], dict):
            request.pop(key)
    effort = request.get('reasoning_effort')
    if 'thinking_config' in request or not isinstance(effort, str) or effort not in ('none', 'minimal', 'low', 'medium', 'high'):
        request.pop('reasoning_effort', None)
    if 'modalities' in request:
        values = request['modalities']
        modalities = list(dict.fromkeys(v.lower() for v in values if isinstance(v, str) and v.lower() in ('text', 'image', 'audio'))) if isinstance(values, list) else []
        if modalities:
            request['modalities'] = modalities
        else:
            request.pop('modalities')
    if 'stop' in request:
        value = request['stop']
        if isinstance(value, list):
            request['stop'] = [v for v in value if isinstance(v, str) and v]
            if not request['stop']:
                request.pop('stop')
        elif not isinstance(value, str) or not value:
            request.pop('stop')
    if 'response_format' in request:
        value = _format(request['response_format'])
        if value:
            request['response_format'] = value
        else:
            request.pop('response_format')
    if 'tools' not in request and isinstance(source.get('functions'), list):
        request['tools'] = [{'type': 'function', 'function': v} for v in source['functions']]
    if 'tool_choice' not in request and 'function_call' in source:
        request['tool_choice'] = source['function_call']
    if 'tools' in request:
        request['tools'] = _tools(request['tools'])
    functions = any(t.get('type') == 'function' for t in request.get('tools', []))
    if 'tool_choice' in request:
        if functions:
            choice = request['tool_choice']
            request['tool_choice'] = 'none' if choice == 'none' else 'auto'
        else:
            request.pop('tool_choice')
    if not functions or request.get('parallel_tool_calls') is not True:
        request.pop('parallel_tool_calls', None)
    return request


def select_default_model(catalog):
    """Pick a current text model, never an invented or fixed model ID."""
    choices = []
    for model_id, model in catalog.items():
        codes = model.get('_codes', [])
        if any(code in codes for code in (21, 37, 53, 77)):
            continue
        if any(word in model_id.lower() for word in ('image', 'audio', 'tts', 'transcribe', 'live')):
            continue
        choices.append(model_id)
    if not choices:
        raise PlaygroundError('没有可自动选择的文本模型，请在客户端明确填写模型 ID。', 400, 'model_required')
    return next((name for name in choices if 'flash' in name.lower()), choices[0])
