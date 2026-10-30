"""Best-effort optional settings for the strict Playground wire encoder.

Only settings are relaxed. Message data, function history and opaque signatures
remain subject to the encoder's validation. Nothing here retries a generation.
"""
import copy
import json
import math
import re
from datetime import datetime

from .playground_protocol import duration_wire, encode_value, invalid
from .errors import PlaygroundError


def _fields(value, names, aliases=None):
    if not isinstance(value, dict):
        return {}
    aliases = aliases or {}
    result = {key: val for key, val in value.items() if key in names and val is not None}
    for old, new in aliases.items():
        if new not in result and value.get(old) is not None:
            result[new] = value[old]
    return result


def _number(value, low=None, high=None, integer=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value) if isinstance(value, str) else value
        if type(value) not in (int, float) or not math.isfinite(value):
            return None
        if integer:
            if int(value) != value:
                return None
            value = int(value)
        if low is not None:
            value = max(low, value)
        if high is not None:
            value = min(high, value)
        return value
    except (ValueError, TypeError, OverflowError):
        return None


def _strings(value):
    if isinstance(value, str):
        value = [value]
    return list(dict.fromkeys(x for x in value if isinstance(x, str) and x)) if isinstance(value, list) else []


class _SchemaExpansionLimit(Exception):
    """Abort a whole optional schema, never keep a partially expanded constraint."""


class _SchemaBudget:
    """Shared work and encoded-size bounds, including repeated reference targets."""
    def __init__(self, nodes=4096, encoded_bytes=256 * 1024):
        self.nodes = nodes
        self.encoded_bytes = encoded_bytes

    def visit(self):
        self.nodes -= 1
        if self.nodes < 0:
            raise _SchemaExpansionLimit()

    def charge(self, value):
        # Charge local fields only: child schemas charge their own fields. Small
        # object wrappers deliberately overestimate punctuation, so the final
        # normalized schema cannot exceed this byte budget. iterencode bounds
        # temporary memory for large examples instead of serializing them whole.
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        for piece in encoder.iterencode(value):
            if len(piece) > self.encoded_bytes:
                raise _SchemaExpansionLimit()
            self.encoded_bytes -= len(piece.encode('utf-8'))
            if self.encoded_bytes < 0:
                raise _SchemaExpansionLimit()


def normalize_schema(value, *, root=None, references=(), depth=0, _budget=None):
    """Project a schema onto the wire subset; None means no usable constraint.

    Unsupported constraints are omitted, never translated to invented rules.
    Local references have depth, cycle, total-work and output-size limits. An
    exhausted budget drops the entire optional schema, including earlier fields.
    """
    budget = _budget if _budget is not None else _SchemaBudget()
    try:
        return _normalize_schema(value, root=root, references=references, depth=depth, budget=budget)
    except _SchemaExpansionLimit:
        # A shared request budget stays exhausted for later function schemas.
        budget.nodes = -1
        return None
    except UnicodeError:
        # JSON can contain escaped lone surrogates; an optional schema must not
        # turn those unencodable strings into an internal server error.
        return None


def _normalize_schema(value, *, root, references, depth, budget):
    budget.visit()
    if not isinstance(value, dict) or depth > 48:
        return None
    root = value if root is None else root
    value = dict(value)
    reference = value.pop('$ref', None)
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith(('#/$defs/', '#/definitions/')) or reference in references:
            return None
        target = root
        for component in reference[2:].split('/'):
            if not isinstance(target, dict):
                return None
            target = target.get(component.replace('~1', '/').replace('~0', '~'))
        if not isinstance(target, dict):
            return None
        return _normalize_schema({**target, **{k: v for k, v in value.items() if k in ('description', 'title')}},
                                 root=root, references=references + (reference,), depth=depth + 1, budget=budget)

    def child(v):
        return _normalize_schema(v, root=root, references=references, depth=depth + 1, budget=budget)

    kind = value.get('type')
    nullable = value.get('nullable') is True
    if isinstance(kind, list):
        nullable |= any(isinstance(v, str) and v.lower() == 'null' for v in kind)
        kinds = list(dict.fromkeys(v.lower() for v in kind if isinstance(v, str) and v.lower() != 'null'))
        kind = kinds[0] if len(kinds) == 1 else None
    kind = kind.lower() if isinstance(kind, str) else None
    if kind in (None, '', 'type_unspecified'):
        if isinstance(value.get('properties'), dict) or 'required' in value:
            kind = 'object'
        elif 'items' in value:
            kind = 'array'
        elif any(k in value for k in ('format', 'minLength', 'maxLength', 'pattern')) or (isinstance(value.get('enum'), list) and value['enum'] and all(isinstance(v, str) for v in value['enum'])):
            kind = 'string'
        else:
            branches = next((value[k] for k in ('anyOf', 'oneOf', 'allOf') if isinstance(value.get(k), list)), [])
            kinds = {v['type'].lower() for v in branches if isinstance(v, dict) and isinstance(v.get('type'), str)}
            kind = kinds.pop() if len(kinds) == 1 else None
    if kind not in {'string', 'number', 'integer', 'boolean', 'array', 'object'}:
        return None
    output = {'type': kind}
    if nullable:
        output['nullable'] = True
    for key in ('format', 'description', 'pattern'):
        if isinstance(value.get(key), str):
            output[key] = value[key]
    budget.charge(output)
    if kind == 'array':
        budget.charge({'items': None})
        items = child(value.get('items'))
        if items is None:
            return None
        output['items'] = items
    if isinstance(value.get('properties'), dict):
        budget.charge({'properties': {}})
        output['properties'] = {}
        for key, source in value['properties'].items():
            if not isinstance(key, str):
                continue
            budget.charge({key: None})
            normalized = child(source)
            if normalized is not None:
                output['properties'][key] = normalized
        for key in ('required', 'propertyOrdering'):
            names = [n for n in _strings(value.get(key)) if n in output['properties']]
            if names:
                budget.charge({key: names})
                output[key] = names
    # The verified wire enum accepts strings only. Keep numeric enums unconstrained.
    enum = value.get('enum')
    if isinstance(enum, list) and enum and all(isinstance(v, str) and v for v in enum):
        enum = list(dict.fromkeys(enum))
        budget.charge({'enum': enum})
        output['enum'] = enum
    for key in ('minProperties', 'maxProperties', 'minLength', 'maxLength', 'minItems', 'maxItems', 'minimum', 'maximum'):
        if key in value:
            number = _number(value[key], None if key in ('minimum', 'maximum') else 0,
                             integer=key not in ('minimum', 'maximum'))
            if number is not None:
                budget.charge({key: number})
                output[key] = number
    for low, high in (('minimum', 'maximum'), ('minProperties', 'maxProperties'), ('minLength', 'maxLength'), ('minItems', 'maxItems')):
        if low in output and high in output and output[low] > output[high]:
            output.pop(low)
            output.pop(high)
    if 'example' in value:
        try:
            budget.charge({'example': value['example']})
            encode_value(value['example'])
            output['example'] = copy.deepcopy(value['example'])
        except (PlaygroundError, RecursionError, TypeError, ValueError, UnicodeError):
            pass
    for key in ('anyOf', 'oneOf', 'allOf'):
        if isinstance(value.get(key), list):
            budget.charge({key: []})
            children = [child(v) for v in value[key]]
            # Dropping one union branch would strengthen the input constraint.
            if children and all(v is not None for v in children):
                output[key] = children
    if isinstance(value.get('not'), dict):
        budget.charge({'not': None})
        result = child(value['not'])
        if result == value['not']:
            output['not'] = result
    return output


_PARTS = {'text', 'inlineData', 'fileData', 'functionCall', 'functionResponse', 'executableCode',
          'codeExecutionResult', 'toolCall', 'toolResponse', 'thought', 'thoughtSignature',
          'videoMetadata', 'mediaProcessing', 'speechMetadata', 'transcriptionMetadata'}
_PART_ALIASES = {'inline_data': 'inlineData', 'file_data': 'fileData', 'function_call': 'functionCall',
                 'function_response': 'functionResponse', 'thought_signature': 'thoughtSignature',
                 'video_metadata': 'videoMetadata', 'media_processing': 'mediaProcessing'}


def _part(value, codes):
    if isinstance(value, str):
        return {'text': value}
    if not isinstance(value, dict):
        return value
    part = _fields(value, _PARTS, _PART_ALIASES)
    # Preserve explicit invalid content data for the strict validator.
    for key in _PARTS:
        if key in value and key not in part:
            part[key] = value[key]
    for key, fields, aliases in (
        ('inlineData', {'data', 'mimeType'}, {'mime_type': 'mimeType'}),
        ('fileData', {'fileUri', 'mimeType'}, {'file_uri': 'fileUri', 'mime_type': 'mimeType'}),
        ('functionCall', {'name', 'args', 'id'}, {}),
        ('functionResponse', {'name', 'response', 'id'}, {}),
        ('executableCode', {'language', 'code'}, {}),
        ('codeExecutionResult', {'outcome', 'output'}, {}),
        ('toolCall', {'toolType', 'toolName', 'args', 'response', 'id'}, {}),
        ('toolResponse', {'toolType', 'toolName', 'args', 'response', 'id'}, {}),
        ('speechMetadata', {'speaker', 'style'}, {}),
        ('videoMetadata', {'startOffset', 'endOffset', 'fps'}, {'start_offset': 'startOffset', 'end_offset': 'endOffset'}),
        ('transcriptionMetadata', {'speaker', 'timestamps'}, {}),
    ):
        if isinstance(part.get(key), dict):
            part[key] = _fields(part[key], fields, aliases)
    if part.get('mediaProcessing') == 'AGENTIC' and 69 not in codes:
        part.pop('mediaProcessing')
    if 'mediaProcessing' in part and part['mediaProcessing'] not in ('MEDIA_PROCESSING_UNSPECIFIED', 'STATIC', 'AGENTIC'):
        part.pop('mediaProcessing')
    if isinstance(part.get('videoMetadata'), dict):
        video = part['videoMetadata']
        for key in ('startOffset', 'endOffset'):
            if key in video:
                try:
                    duration_wire(video[key])
                except PlaygroundError:
                    video.pop(key)
        if 'fps' in video:
            fps = _number(video['fps'], high=24)
            if fps is None or fps <= 0:
                video.pop('fps')
            else:
                video['fps'] = fps
        if all(k in video for k in ('startOffset', 'endOffset')):
            start, end = duration_wire(video['startOffset']), duration_wire(video['endOffset'])
            if tuple((end + [0])[:2]) <= tuple((start + [0])[:2]):
                video.pop('endOffset')
        if video and part.get('mediaProcessing') == 'AGENTIC':
            part['mediaProcessing'] = 'STATIC'
        if not video:
            part.pop('videoMetadata')
    elif 'videoMetadata' in part:
        part.pop('videoMetadata')
    if not ('inlineData' in part or 'fileData' in part):
        part.pop('videoMetadata', None)
        part.pop('mediaProcessing', None)
    if 'speechMetadata' in part and (85 not in codes or 'text' not in part):
        part.pop('speechMetadata')
    elif 'speechMetadata' in part:
        speech = part['speechMetadata']
        part['speechMetadata'] = {k: v for k, v in speech.items() if isinstance(v, str)} if isinstance(speech, dict) else {}
    metadata = part.get('transcriptionMetadata')
    if isinstance(metadata, dict) and isinstance(metadata.get('timestamps'), list):
        timestamps = []
        for stamp in metadata['timestamps']:
            if isinstance(stamp, dict):
                stamp = _fields(stamp, {'text', 'start', 'end'})
                for key in ('start', 'end'):
                    if isinstance(stamp.get(key), dict):
                        stamp[key] = _fields(stamp[key], {'seconds', 'nanos'})
            timestamps.append(stamp)
        metadata['timestamps'] = timestamps
    if 'text' not in part:
        part.pop('transcriptionMetadata', None)
    elif 'transcriptionMetadata' in part and not isinstance(part['transcriptionMetadata'], dict):
        part.pop('transcriptionMetadata')
    return part


def _content(value, codes):
    if isinstance(value, str):
        value = {'parts': [{'text': value}]}
    if not isinstance(value, dict):
        return value
    content = _fields(value, {'role', 'parts'})
    if 'parts' not in content and 'text' in value:
        content['parts'] = [{'text': value['text']}]
    parts = content.get('parts')
    if isinstance(parts, (str, dict)):
        parts = [parts]
    if isinstance(parts, list):
        content['parts'] = [_part(p, codes) for p in parts]
    if content.get('role') == 'assistant':
        content['role'] = 'model'
    if 'role' not in content:
        normalized_parts = content.get('parts')
        calls = isinstance(normalized_parts, list) and any(isinstance(p, dict) and 'functionCall' in p for p in normalized_parts)
        content['role'] = 'model' if calls else 'user'
    return content


def _tools(request, model, schema_budget):
    codes = model.get('_codes', [])
    output, names, builtin_names = [], set(), set()
    source = request.get('tools')
    for tool in source if isinstance(source, list) else []:
        if not isinstance(tool, dict):
            continue
        declarations = tool.get('functionDeclarations', tool.get('function_declarations'))
        if 10 in codes and isinstance(declarations, list):
            result = []
            for declaration in declarations:
                if not isinstance(declaration, dict):
                    continue
                entry = _fields(declaration, {'name', 'description', 'parameters', 'parametersJsonSchema'},
                                {'parameters_json_schema': 'parametersJsonSchema'})
                name = entry.get('name')
                if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.:-]{0,63}', name) or name in names:
                    continue
                names.add(name)
                if 'description' in entry and not isinstance(entry['description'], str):
                    entry.pop('description')
                schema = normalize_schema(entry.pop('parametersJsonSchema', entry.get('parameters', {})), _budget=schema_budget)
                entry['parameters'] = schema or {'type': 'object'}
                result.append(entry)
            if result:
                output.append({'functionDeclarations': result})
        for key, code in (('googleSearch', 12), ('imageSearch', 54), ('codeExecution', 9), ('urlContext', 13), ('googleMaps', 58)):
            if key not in tool or key in builtin_names:
                continue
            if key == 'googleSearch':
                search = tool[key] if isinstance(tool[key], dict) else {}
                types = search.get('searchTypes')
                if not isinstance(types, dict):
                    types = {'webSearch': {}}
                types = {k: {} for k, c in (('webSearch', 12), ('imageSearch', 54)) if k in types and c in codes}
                if not types:
                    continue
                value = {'searchTypes': types}
                period = _fields(search.get('timeRangeFilter'), {'startTime', 'endTime'})
                normalized = {}
                for name, stamp in period.items():
                    try:
                        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                        if parsed.tzinfo is not None:
                            normalized[name] = stamp
                    except (ValueError, TypeError, AttributeError):
                        pass
                if len(normalized) == 2 and datetime.fromisoformat(normalized['startTime'].replace('Z', '+00:00')) > datetime.fromisoformat(normalized['endTime'].replace('Z', '+00:00')):
                    normalized = {}
                if normalized:
                    value['timeRangeFilter'] = normalized
            elif code not in codes:
                continue
            else:
                value = {}
            if key == 'googleMaps' and builtin_names & {'codeExecution', 'urlContext'} or key in ('codeExecution', 'urlContext') and 'googleMaps' in builtin_names:
                continue
            output.append({key: value})
            builtin_names.add(key)
    if 'tools' in request:
        request['tools'] = output
    calling = _fields(_fields(request.get('toolConfig'), {'functionCallingConfig'}).get('functionCallingConfig'), {'mode', 'allowedFunctionNames'})
    if calling and (names or calling.get('mode') == 'NONE'):
        mode = calling.get('mode')
        calling['mode'] = mode if mode in ('AUTO', 'NONE', 'MODE_UNSPECIFIED') else 'AUTO'
        allowed = [n for n in _strings(calling.get('allowedFunctionNames')) if n in names]
        if allowed:
            calling['allowedFunctionNames'] = allowed
        else:
            calling.pop('allowedFunctionNames', None)
        request['toolConfig'] = {'functionCallingConfig': calling}
    else:
        request.pop('toolConfig', None)


def _config(value, model, schema_budget):
    codes, options = model.get('_codes', []), model.get('_options', {})
    source = value if isinstance(value, dict) else {}
    aliases = {'max_output_tokens': 'maxOutputTokens', 'top_p': 'topP', 'top_k': 'topK', 'stop_sequences': 'stopSequences',
               'response_mime_type': 'responseMimeType', 'thinking_config': 'thinkingConfig', 'candidate_count': 'candidateCount',
               'response_schema': 'responseSchema', 'response_json_schema': 'responseJsonSchema', 'response_modalities': 'responseModalities',
               'image_config': 'imageConfig', 'speech_config': 'speechConfig', 'media_resolution': 'mediaResolution', 'transcription_config': 'transcriptionConfig'}
    source = {**{new: source[old] for old, new in aliases.items() if old in source}, **source}
    result = {}
    maximum = model.get('outputTokenLimit')
    if 91 in codes and type(maximum) is int and type(model.get('inputTokenLimit')) is int:
        maximum = max(maximum, model['inputTokenLimit'])
    for key, low, high, integer in (('maxOutputTokens', 1, maximum, True), ('temperature', 0, model.get('maxTemperature', 2), False),
                                   ('topP', 0, 1, False), ('topK', 0, 2147483647, True), ('seed', 0, 2147483647, True)):
        if key in source:
            number = _number(source[key], low, high, integer)
            if number is not None:
                result[key] = number
    if 'candidateCount' in source:
        result['candidateCount'] = 1
    if 'stopSequences' in source:
        result['stopSequences'] = _strings(source['stopSequences'])
    mime = source.get('responseMimeType')
    if mime in ('text/plain', 'application/json', 'text/x.enum'):
        result['responseMimeType'] = mime
    schema = normalize_schema(source.get('responseSchema', source.get('responseJsonSchema')), _budget=schema_budget)
    if schema:
        result['responseSchema'] = schema
        result['responseMimeType'] = mime if mime in ('application/json', 'text/x.enum') else 'application/json'
    available = {'AUDIO'} if 37 in codes else {'TEXT', 'IMAGE'} if 21 in codes else {'TEXT'}
    modalities = [m.upper() for m in _strings(source.get('responseModalities')) if m.upper() in available]
    if modalities:
        result['responseModalities'] = list(dict.fromkeys(modalities))
    image = _fields(source.get('imageConfig'), {'aspectRatio', 'imageSize'}, {'aspect_ratio': 'aspectRatio', 'image_size': 'imageSize'})
    if 21 in codes:
        selected = {}
        for key, code, choices in (('aspectRatio', 47, options.get('image_aspect_ratios', [])), ('imageSize', 49, options.get('image_output_resolutions', []))):
            v = image.get(key)
            valid = isinstance(v, str) and (bool(re.fullmatch(r'[1-9]\d*:[1-9]\d*', v)) if key == 'aspectRatio' else v in ('512', '1K', '2K', '4K'))
            if code in codes and valid and (not choices or v in choices):
                selected[key] = v
        if selected:
            result['imageConfig'] = selected
        elif 49 in codes and options.get('image_output_resolutions') and '1K' not in options['image_output_resolutions']:
            sizes = [v for v in options['image_output_resolutions'] if v in ('512', '1K', '2K', '4K')]
            if sizes:
                result['imageConfig'] = {'imageSize': sizes[0]}
    if 37 in codes and isinstance(source.get('speechConfig'), dict):
        speech = _fields(source['speechConfig'], {'voiceConfig', 'multiSpeakerVoiceConfig'})
        voices = options.get('voices', [])
        def voice(v):
            name = _fields(_fields(v, {'prebuiltVoiceConfig'}).get('prebuiltVoiceConfig'), {'voiceName'}).get('voiceName')
            if not isinstance(name, str) or not name or voices and name not in voices:
                name = voices[0] if voices else 'Zephyr'
            return {'prebuiltVoiceConfig': {'voiceName': name}}
        if 'voiceConfig' in speech:
            result['speechConfig'] = {'voiceConfig': voice(speech['voiceConfig'])}
        elif isinstance(speech.get('multiSpeakerVoiceConfig'), dict):
            multi = speech['multiSpeakerVoiceConfig']
            speakers = multi.get('speakerVoiceConfigs')
            selected, names = [], set()
            for speaker in speakers if isinstance(speakers, list) else []:
                if not isinstance(speaker, dict) or not isinstance(speaker.get('speaker'), str) or not speaker['speaker'] or speaker['speaker'] in names:
                    continue
                names.add(speaker['speaker'])
                selected.append({'speaker': speaker['speaker'], 'voiceConfig': voice(speaker.get('voiceConfig'))})
            if selected:
                multi = {'speakerVoiceConfigs': selected, **({'mode': multi['mode']} if multi.get('mode') in ('VERBATIM', 'CONVERSATIONAL') else {})}
                result['speechConfig'] = {'multiSpeakerVoiceConfig': multi}
    if 37 in codes and 'speechConfig' not in result and options.get('voices'):
        result['speechConfig'] = {'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': options['voices'][0]}}}
    resolution = source.get('mediaResolution')
    if 43 in codes and resolution in ('MEDIA_RESOLUTION_UNSPECIFIED', 'MEDIA_RESOLUTION_LOW', 'MEDIA_RESOLUTION_MEDIUM', 'MEDIA_RESOLUTION_HIGH'):
        result['mediaResolution'] = resolution
    transcript = _fields(source.get('transcriptionConfig'), {'wordTimestamps', 'speakerLabels', 'customVocabulary', 'languageCodes', 'smartTranscription'})
    selected = {k: v for k, code in (('wordTimestamps', 74), ('speakerLabels', 80), ('smartTranscription', 84))
                if isinstance((v := transcript.get(k)), bool) and (not v or code in codes)}
    for key, code in (('customVocabulary', 81), ('languageCodes', 76)):
        if code in codes:
            values = _strings(transcript.get(key))
            if key == 'languageCodes' and options.get('languages'):
                values = [v for v in values if v in options['languages']]
            if values:
                selected[key] = values
    if selected.get('wordTimestamps') or selected.get('speakerLabels'):
        selected.pop('customVocabulary', None)
        selected.pop('smartTranscription', None)
    if selected:
        result['transcriptionConfig'] = selected
    thinking = _fields(source.get('thinkingConfig'), {'includeThoughts', 'thinkingBudget', 'thinkingLevel'},
                       {'include_thoughts': 'includeThoughts', 'thinking_budget': 'thinkingBudget', 'thinking_level': 'thinkingLevel'})
    if 25 in codes:
        selected = {}
        if isinstance(thinking.get('includeThoughts'), bool):
            selected['includeThoughts'] = thinking['includeThoughts']
        metadata = model.get('_thinking', [])
        def at(index, default=None):
            return metadata[index] if isinstance(metadata, list) and len(metadata) > index and metadata[index] is not None else default
        if 35 in codes and 'thinkingBudget' in thinking:
            budget = _number(thinking['thinkingBudget'], -1, at(1), True)
            if budget is not None:
                if budget != -1 and not (budget == 0 and at(3, False)):
                    budget = max(budget, at(2, 0))
                selected['thinkingBudget'] = budget
        level = thinking.get('thinkingLevel')
        if isinstance(level, str):
            level = level.upper()
            if 52 in codes and {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3, 'MINIMAL': 4}.get(level) in at(6, []):
                selected['thinkingLevel'] = level
                selected.pop('thinkingBudget', None)
        if selected:
            result['thinkingConfig'] = selected
    return result


def normalize_count_tokens_request(request, model):
    if not isinstance(request, dict):
        raise invalid('countTokens request must be an object')
    result = {}
    if 'contents' in request:
        result['contents'] = normalize_gemini_request({'contents': request['contents']}, model).get('contents')
    if 'generateContentRequest' in request:
        nested = request['generateContentRequest']
        if not isinstance(nested, dict):
            raise invalid('generateContentRequest must be an object')
        result['generateContentRequest'] = normalize_gemini_request(nested, model)
        if 'model' in nested:
            result['generateContentRequest']['model'] = nested['model']
    return result


def normalize_gemini_request(request, model):
    if not isinstance(request, dict):
        raise invalid('Gemini request must be an object')
    try:
        request = copy.deepcopy(request)
    except RecursionError:
        raise invalid('Request nesting is too deep') from None
    result = _fields(request, {'contents', 'systemInstruction', 'generationConfig', 'safetySettings', 'tools', 'toolConfig'},
                     {'system_instruction': 'systemInstruction', 'generation_config': 'generationConfig', 'safety_settings': 'safetySettings', 'tool_config': 'toolConfig'})
    codes = model.get('_codes', [])
    contents = result.get('contents')
    if isinstance(contents, (str, dict)):
        contents = [contents]
    if isinstance(contents, list):
        result['contents'] = [_content(c, codes) for c in contents]
    if 'systemInstruction' in result:
        system = _content(result['systemInstruction'], codes)
        if isinstance(system, dict) and isinstance(system.get('parts'), list):
            system['parts'] = [{k: v for k, v in part.items() if k == 'text'} if isinstance(part, dict) else part for part in system['parts']]
        result['systemInstruction'] = system
    schema_budget = _SchemaBudget()
    result['generationConfig'] = _config(result.get('generationConfig'), model, schema_budget)
    _tools(result, model, schema_budget)
    if 'safetySettings' in result:
        categories = {'HARM_CATEGORY_HARASSMENT', 'HARM_CATEGORY_HATE_SPEECH', 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'HARM_CATEGORY_DANGEROUS_CONTENT', 'HARM_CATEGORY_CIVIC_INTEGRITY'}
        thresholds = {'BLOCK_LOW_AND_ABOVE', 'BLOCK_MEDIUM_AND_ABOVE', 'BLOCK_ONLY_HIGH', 'BLOCK_NONE', 'OFF'}
        safety = result['safetySettings']
        result['safetySettings'] = [{'category': s['category'], 'threshold': s['threshold']} for s in safety
                                    if isinstance(s, dict) and isinstance(s.get('category'), str) and isinstance(s.get('threshold'), str)
                                    and s['category'] in categories and s['threshold'] in thresholds] if isinstance(safety, list) else []
    return result
