"""Playground JSON+protobuf arrays (ported from AIStudio2API, see NOTICE)."""
import base64
import json
import math
import re
from datetime import datetime
from urllib.parse import urlsplit
from .errors import PlaygroundError


CONTINUATION_ROUND_TOKENS = 32768


def invalid(message):
    descriptions = {
        'A valid model is required':'model 必须填写有效且非空的模型名称。',
        'Only n=1 is supported':'当前接口仅支持整数 n=1。',
        'stream must be a boolean':'stream 必须为 true 或 false。',
        'messages must be a nonempty array':'messages 必须为非空数组。',
        'Invalid stream_options':'stream_options 必须为对象，include_usage 必须为布尔值。',
        'contents must be a nonempty array':'contents 必须为非空数组。',
        'content.parts must be a nonempty array':'每条内容的 parts 必须为非空数组。',
        'Unsupported safetySettings':'safetySettings 的 category 与 threshold 必须是受支持的字符串。',
        'Playground supports one candidate in this adapter':'当前接口仅支持整数 candidateCount=1。',
        'Invalid inlineData base64':'inlineData.data 必须是有效的 Base64 数据。',
    }
    public_message = descriptions.get(message)
    if message.startswith('Invalid ') and message[8:] in ('temperature','topP','topK','seed','maxOutputTokens','thinkingBudget'):
        public_message = f'参数 {message[8:]} 的类型或数值超出该模型支持范围。'
    return PlaygroundError(message,400,'invalid_request_error',public_message=public_message)


def at(values, index, default=None):
    if not isinstance(values, list):
        raise PlaygroundError('Unexpected Playground array layout', code='protocol_error')
    return values[index] if len(values) > index and values[index] is not None else default


class SparseJSON:
    """Normalize JSPB array holes without touching quoted strings, across chunks."""
    def __init__(self):
        self.string = self.escape = False
        self.previous = ''

    def feed(self, data):
        out = []
        for c in data:
            if self.string:
                out.append(c)
                if self.escape:
                    self.escape = False
                elif c == '\\':
                    self.escape = True
                elif c == '"':
                    self.string = False
                continue
            if c == '"':
                self.string = True
            if (c == ',' and self.previous in ('[', ',')) or (c == ']' and self.previous == ','):
                out.append('null')
            out.append(c)
            if not c.isspace():
                self.previous = c
        return ''.join(out)


def loads(data):
    try:
        return json.loads(SparseJSON().feed(data))
    except (ValueError, TypeError):
        raise PlaygroundError('Malformed Playground JSON+protobuf response', code='protocol_error') from None


def parse_models(raw):
    from .model_catalog import parse_model_row
    root = loads(raw)
    rows = at(root, 0)
    if not isinstance(rows, list):
        raise PlaygroundError('ListModels response missing catalog', code='protocol_error')
    result = {}
    for row in rows:
        model = parse_model_row(row)
        if model is not None:
            name = model['name'].removeprefix('models/')
            if name in result:
                raise PlaygroundError('ListModels contains duplicate model IDs',code='protocol_error')
            result[name] = model
    return result


def public_model(model):
    from .model_catalog import native_model
    return native_model(model)


def set_field(wire, index, value):
    wire += [None] * (index + 1 - len(wire))
    wire[index] = value
    return wire


def object_fields(value, allowed, name):
    if not isinstance(value,dict) or set(value) - set(allowed):
        raise invalid(f'{name} 包含不支持的字段或不是对象')
    return value


def string_list(value, name, *, nonempty=False):
    if not isinstance(value,list) or (nonempty and not value) or any(not isinstance(v,str) or not v for v in value):
        raise invalid(f'{name} 必须为非空字符串数组')
    return value


def encode_struct(value, depth=0):
    if depth > 64 or not isinstance(value,dict) or any(not isinstance(k,str) for k in value):
        raise invalid('函数参数和返回值必须为深度不超过 64 的 JSON 对象')
    return [[[key,encode_value(value[key],depth+1)] for key in sorted(value)]] if value else []


def encode_value(value, depth=0):
    if depth > 64:
        raise invalid('JSON 嵌套层级过深')
    if value is None:
        return [0]
    if isinstance(value,bool):
        return [None,None,None,value]
    if type(value) in (int,float):
        numeric(value,'JSON number',-float('inf'))
        return [None,value]
    if isinstance(value,str):
        return [None,None,value]
    if isinstance(value,dict):
        return [None,None,None,None,encode_struct(value,depth+1)]
    if isinstance(value,list):
        return [None,None,None,None,None,[[encode_value(v,depth+1) for v in value]]]
    raise invalid('函数参数包含不支持的 JSON 值')


def decode_struct(value, depth=0):
    if depth > 64 or not isinstance(value,list):
        raise PlaygroundError('Invalid Playground Struct',code='protocol_error')
    entries = at(value,0,[])
    if not isinstance(entries,list):
        raise PlaygroundError('Invalid Playground Struct entries',code='protocol_error')
    result = {}
    for entry in entries:
        if not isinstance(entry,list) or len(entry) != 2 or not isinstance(entry[0],str) or entry[0] in result:
            raise PlaygroundError('Invalid Playground Struct entry',code='protocol_error')
        result[entry[0]] = decode_value(entry[1],depth+1)
    return result


def decode_value(value, depth=0):
    if depth > 64 or not isinstance(value,list):
        raise PlaygroundError('Invalid Playground Value',code='protocol_error')
    variants = [i for i,v in enumerate(value) if v is not None]
    if len(variants) != 1 or variants[0] > 5:
        raise PlaygroundError('Invalid Playground Value oneof',code='protocol_error')
    index = variants[0]
    item = value[index]
    if index == 0 and type(item) is int and item == 0:
        return None
    if index == 1 and type(item) in (int,float):
        try:
            if math.isfinite(item):
                return item
        except OverflowError:
            pass
    if index == 2 and isinstance(item,str):
        return item
    if index == 3 and (type(item) is bool or type(item) is int and item in (0,1)):
        return bool(item)
    if index == 4:
        return decode_struct(item,depth+1)
    if index == 5 and isinstance(item,list):
        items = at(item,0,[])
        if isinstance(items,list):
            return [decode_value(v,depth+1) for v in items]
    raise PlaygroundError('Invalid Playground Value type',code='protocol_error')


def duration_wire(value):
    match = re.fullmatch(r'(\d+)(?:\.(\d{1,9}))?s',value) if isinstance(value,str) else None
    if not match or len(match[1]) > 18:
        raise invalid('视频时间必须为秒数加 s，例如 2.5s')
    seconds, nanos = int(match[1]),int((match[2] or '').ljust(9,'0'))
    return [seconds,nanos] if nanos else [seconds]


def encode_part(part):
    variants = {'text','inlineData','fileData','functionCall','functionResponse','executableCode','codeExecutionResult',
                'toolCall','toolResponse'}
    object_fields(part,variants | {'thought','thoughtSignature','videoMetadata','mediaProcessing','speechMetadata','transcriptionMetadata'},'part')
    selected = set(part) & variants
    if len(selected) != 1 and not (not selected and part.get('thoughtSignature')):
        raise invalid('Each part requires exactly one content variant')
    wire, binding = [], ''
    if 'text' in part:
        if not isinstance(part['text'],str):
            raise invalid('part.text must be a string')
        wire, binding = [None,part['text']], part['text']
    elif 'inlineData' in part:
        blob = part['inlineData']
        if not isinstance(blob,dict) or set(blob) != {'mimeType','data'} or not isinstance(blob['mimeType'],str) or not blob['mimeType'] or not isinstance(blob['data'],str):
            raise invalid('inlineData requires mimeType and base64 data')
        try:
            binary = base64.b64decode(blob['data'],validate=True)
        except ValueError:
            raise invalid('Invalid inlineData base64') from None
        if not binary:
            raise invalid('inlineData must not be empty')
        binding = base64.b64encode(binary).decode()
        wire = [None,None,[blob['mimeType'],binding]]
    elif 'fileData' in part:
        media = object_fields(part['fileData'],{'mimeType','fileUri'},'fileData')
        uri = media.get('fileUri')
        try:
            parsed = urlsplit(uri) if isinstance(uri,str) else None
        except ValueError:
            parsed = None
        if not isinstance(media.get('mimeType'),str) or not media['mimeType'] or parsed is None or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise invalid('fileData 需要 mimeType 和 HTTPS 媒体地址；当前不支持 Files API 或本地文件路径')
        wire = set_field([],6,[media['mimeType'],uri])
    elif 'functionCall' in part or 'functionResponse' in part:
        call = 'functionCall' in part
        key, data_key, index = ('functionCall','args',10) if call else ('functionResponse','response',11)
        value = object_fields(part[key],{'name',data_key,'id'},key)
        if not isinstance(value.get('name'),str) or not value['name']:
            raise invalid(f'{key}.name 必须为非空字符串')
        encoded = [value['name'],encode_struct(value.get(data_key,{}))]
        if 'id' in value:
            if not isinstance(value['id'],str) or not value['id']:
                raise invalid(f'{key}.id 必须为非空字符串')
            encoded.append(value['id'])
        wire = set_field([],index,encoded)
    elif 'executableCode' in part or 'codeExecutionResult' in part:
        executable = 'executableCode' in part
        key, enum_key, data_key, index = ('executableCode','language','code',7) if executable else ('codeExecutionResult','outcome','output',8)
        enums = {'LANGUAGE_UNSPECIFIED':0,'PYTHON':1} if executable else {'OUTCOME_UNSPECIFIED':0,'OUTCOME_OK':1,'OUTCOME_FAILED':2,'OUTCOME_DEADLINE_EXCEEDED':3}
        value = object_fields(part[key],{enum_key,data_key},key)
        if not isinstance(value.get(enum_key),str) or value[enum_key] not in enums or not isinstance(value.get(data_key,''),str):
            raise invalid(f'{key} 的枚举或内容无效')
        wire = set_field([],index,[enums[value[enum_key]],value.get(data_key,'')])
    elif 'toolCall' in part or 'toolResponse' in part:
        key,index = ('toolCall',25) if 'toolCall' in part else ('toolResponse',26)
        value = object_fields(part[key],{'toolType','toolName','args','response','id'},key)
        if value.get('toolType') != 'MEDIA_PROCESSING':
            raise invalid('目前仅支持 MEDIA_PROCESSING 服务端工具事件')
        for name in ('toolName','id'):
            if name in value and not isinstance(value[name],str):
                raise invalid(f'{key}.{name} 必须为字符串')
        data_key = 'args' if index == 25 else 'response'
        if ({'args','response'} - {data_key}) & set(value):
            raise invalid(f'{key} 的数据字段必须为 {data_key}')
        wire = set_field([],index,[13,value.get('toolName'),encode_struct(value.get(data_key,{})),value.get('id','')])
    if 'videoMetadata' in part or 'mediaProcessing' in part:
        if not (selected & {'inlineData','fileData'}):
            raise invalid('视频元数据仅可用于媒体 part')
        if 'videoMetadata' in part:
            video = object_fields(part['videoMetadata'],{'startOffset','endOffset','fps'},'videoMetadata')
            if part.get('mediaProcessing') == 'AGENTIC':
                raise PlaygroundError('videoMetadata is incompatible with AGENTIC mediaProcessing',400,'invalid_request_error',
                    public_message='videoMetadata 静态视频参数不能与 mediaProcessing=AGENTIC 同时使用。')
            metadata = []
            for index,key in enumerate(('startOffset','endOffset')):
                if key in video:
                    set_field(metadata,index,duration_wire(video[key]))
            if 'fps' in video:
                fps = video['fps']
                if type(fps) not in (int,float) or not 0 < fps <= 24:
                    raise PlaygroundError('videoMetadata.fps must be in (0,24]',400,'invalid_request_error',
                        public_message='videoMetadata.fps 必须为大于 0 且不超过 24 的有限数值。')
                set_field(metadata,2,fps)
            if all(k in video for k in ('startOffset','endOffset')):
                start,end = duration_wire(video['startOffset']),duration_wire(video['endOffset'])
                if tuple((end+[0])[:2]) <= tuple((start+[0])[:2]):
                    raise invalid('videoMetadata.endOffset 必须晚于 startOffset')
            if metadata:
                set_field(wire,15,metadata)
        if 'mediaProcessing' in part:
            choices = {'MEDIA_PROCESSING_UNSPECIFIED':0,'STATIC':1,'AGENTIC':2}
            if not isinstance(part['mediaProcessing'],str) or part['mediaProcessing'] not in choices:
                raise invalid('mediaProcessing 必须为 STATIC 或 AGENTIC')
            set_field(wire,39,choices[part['mediaProcessing']])
    if 'speechMetadata' in part:
        if 'text' not in part:
            raise invalid('speechMetadata 仅可用于文本 part')
        speech = object_fields(part['speechMetadata'],{'speaker','style'},'speechMetadata')
        if any(not isinstance(v,str) for v in speech.values()):
            raise invalid('speechMetadata 中的值必须为字符串')
        set_field(wire,40,[speech.get('speaker'),speech.get('style')])
    if 'transcriptionMetadata' in part:
        if 'text' not in part:
            raise invalid('transcriptionMetadata 需要同一 part 的 text')
        metadata = object_fields(part['transcriptionMetadata'],{'speaker','timestamps'},'transcriptionMetadata')
        transcript = [part['text']]
        if 'speaker' in metadata:
            if not isinstance(metadata['speaker'],str):
                raise invalid('transcriptionMetadata.speaker 必须为字符串')
            set_field(transcript,1,metadata['speaker'])
        if 'timestamps' in metadata:
            if not isinstance(metadata['timestamps'],list):
                raise invalid('transcriptionMetadata.timestamps 必须为数组')
            timestamps = []
            for timestamp in metadata['timestamps']:
                object_fields(timestamp,{'text','start','end'},'transcription timestamp')
                if 'text' in timestamp and not isinstance(timestamp['text'],str):
                    raise invalid('transcription timestamp.text 必须为字符串')
                encoded = [timestamp.get('text')]
                for index,key in ((1,'start'),(2,'end')):
                    if key in timestamp:
                        duration = object_fields(timestamp[key],{'seconds','nanos'},'transcription duration')
                        seconds = numeric(duration.get('seconds',0),'duration.seconds',0,integer=True)
                        nanos = numeric(duration.get('nanos',0),'duration.nanos',0,999999999,True)
                        set_field(encoded,index,[seconds,nanos])
                timestamps.append(encoded)
            set_field(transcript,2,timestamps)
        set_field(wire,22,transcript)
    if part.get('thought') is not None:
        if not isinstance(part['thought'],bool):
            raise invalid('thought must be a boolean')
        wire += [None] * (13-len(wire))
        wire[12] = part['thought']
    if part.get('thoughtSignature') is not None:
        if not isinstance(part['thoughtSignature'],str):
            raise invalid('thoughtSignature must be a string')
        wire += [None] * (15-len(wire))
        wire[14] = part['thoughtSignature']
    return wire, binding


def numeric(value, key, minimum, maximum=None, integer=False):
    try:
        finite = type(value) in (int,float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < minimum or (maximum is not None and value > maximum) or (integer and not isinstance(value,int)):
        raise invalid(f'Invalid {key}')
    return value


def encode_schema(schema, *, root=None, depth=0, references=()):
    """Encode the verified Playground Schema subset; never discard constraints.

    Unlike the reference's descriptive fallback, unsupported JSON Schema
    keywords fail explicitly so a caller cannot mistake prose for enforcement.
    """
    if depth > 48 or not isinstance(schema,dict):
        raise invalid('responseSchema / parameters 必须为对象，嵌套不超过 48 层')
    root = schema if root is None else root
    schema = dict(schema)
    if '$ref' in schema:
        reference = schema.pop('$ref')
        if not isinstance(reference,str) or not reference.startswith(('#/$defs/','#/definitions/')) or reference in references:
            raise invalid('仅支持非递归的本地 $defs / definitions 引用')
        target = root
        for key in reference[2:].split('/'):
            key = key.replace('~1','/').replace('~0','~')
            if not isinstance(target,dict) or key not in target:
                raise invalid('JSON Schema $ref 引用不存在')
            target = target[key]
        if not isinstance(target,dict):
            raise invalid('JSON Schema $ref 必须指向对象')
        if schema and set(schema) - {'description','title'}:
            raise invalid('$ref 旁仅支持 description / title，避免覆盖约束')
        return encode_schema({**target,**schema},root=root,depth=depth+1,references=references+(reference,))
    allowed = {'type','format','description','nullable','enum','items','properties','required','minItems','maxItems',
               'minProperties','maxProperties','minimum','maximum','minLength','maxLength','pattern','example',
               'oneOf','anyOf','allOf','not','propertyOrdering','$defs','definitions','$schema','title','default'}
    if set(schema)-allowed:
        raise invalid('Playground 尚未实现这些 JSON Schema 约束：'+', '.join(sorted(set(schema)-allowed)))
    def child(value):
        return encode_schema(value,root=root,depth=depth+1,references=references)
    type_name = schema.get('type','')
    if isinstance(type_name,list):
        if not type_name or any(not isinstance(v,str) for v in type_name):
            raise invalid('schema.type 数组必须包含类型名称')
        nullable = any(v.lower() == 'null' for v in type_name)
        nonnull = [v for v in type_name if v.lower() != 'null']
        if len(nonnull) != 1:
            raise invalid('schema.type 数组目前支持单一类型与 null 的联合')
        type_name = nonnull[0]
        if nullable:
            schema['nullable'] = True
    if not isinstance(type_name,str):
        raise invalid('schema.type 必须为类型名称')
    type_name = type_name.lower()
    if type_name in ('','type_unspecified'):
        if 'properties' in schema or 'required' in schema:
            type_name = 'object'
        elif 'items' in schema:
            type_name = 'array'
        elif any(k in schema for k in ('format','enum','minLength','maxLength','pattern')):
            type_name = 'string'
        else:
            branches = next((schema[k] for k in ('anyOf','oneOf','allOf') if k in schema),[])
            names = {v.get('type','').lower() for v in branches if isinstance(v,dict) and isinstance(v.get('type',''),str)} if isinstance(branches,list) else set()
            if len(names) == 1:
                type_name = names.pop()
    types = {'string':1,'number':2,'integer':3,'boolean':4,'array':5,'object':6}
    if type_name not in types:
        raise invalid('Playground schema 需要明确的支持类型；开放节点和混合类型联合尚未验证')
    wire = [types[type_name]]
    for key,index in (('format',1),('description',2),('pattern',14)):
        if key in schema:
            if not isinstance(schema[key],str):
                raise invalid(f'schema.{key} 必须为字符串')
            set_field(wire,index,schema[key])
    if 'nullable' in schema:
        if not isinstance(schema['nullable'],bool):
            raise invalid('schema.nullable 必须为布尔值')
        set_field(wire,3,schema['nullable'])
    for key,index in (('enum',4),('required',7),('propertyOrdering',22)):
        if key in schema:
            set_field(wire,index,string_list(schema[key],'schema.'+key))
    if 'items' in schema:
        set_field(wire,5,child(schema['items']))
    elif type_name == 'array':
        raise invalid('array schema 必须提供已验证的 items 类型')
    if 'properties' in schema:
        properties = schema['properties']
        if not isinstance(properties,dict) or any(not isinstance(k,str) for k in properties):
            raise invalid('schema.properties 必须为对象')
        set_field(wire,6,[[key,child(properties[key])] for key in sorted(properties)])
        if any(key not in properties for key in schema.get('required',[])):
            raise invalid('schema.required 的属性必须在 properties 中定义')
        if any(key not in properties for key in schema.get('propertyOrdering',[])):
            raise invalid('schema.propertyOrdering 的属性必须在 properties 中定义')
    for key,index in (('minProperties',8),('maxProperties',9),('minLength',12),('maxLength',13),('maxItems',20),('minItems',21)):
        if key in schema:
            set_field(wire,index,numeric(schema[key],'schema.'+key,0,integer=True))
    for key,index in (('minimum',10),('maximum',11)):
        if key in schema:
            set_field(wire,index,numeric(schema[key],'schema.'+key,-float('inf')))
    for low,high in (('minimum','maximum'),('minProperties','maxProperties'),('minLength','maxLength'),('minItems','maxItems')):
        if low in schema and high in schema and schema[low] > schema[high]:
            raise invalid(f'schema.{low} 不能超过 {high}')
    if 'example' in schema:
        set_field(wire,15,encode_value(schema['example']))
    for key,index in (('oneOf',16),('anyOf',17),('allOf',18)):
        if key in schema:
            if not isinstance(schema[key],list) or not schema[key]:
                raise invalid(f'schema.{key} 必须为非空 schema 数组')
            set_field(wire,index,[child(v) for v in schema[key]])
    if 'not' in schema:
        set_field(wire,19,child(schema['not']))
    return wire


def encode_speech(config, model):
    object_fields(config,{'voiceConfig','multiSpeakerVoiceConfig'},'speechConfig')
    if 37 not in model.get('_codes',[]):
        raise invalid('该模型不支持 speechConfig')
    if len(config) != 1:
        raise invalid('speechConfig 必须指定单说话人或多说话人配置之一')
    def voice(value):
        object_fields(value,{'prebuiltVoiceConfig'},'voiceConfig')
        prebuilt = object_fields(value.get('prebuiltVoiceConfig'),{'voiceName'},'prebuiltVoiceConfig')
        name = prebuilt.get('voiceName')
        choices = model.get('_options',{}).get('voices',[])
        if not isinstance(name,str) or not name or choices and name not in choices:
            raise invalid('voiceName 不在该模型可用声音中')
        return [[name]]
    if 'voiceConfig' in config:
        return [voice(config['voiceConfig'])]
    multi = object_fields(config['multiSpeakerVoiceConfig'],{'speakerVoiceConfigs','mode'},'multiSpeakerVoiceConfig')
    speakers = multi.get('speakerVoiceConfigs')
    if not isinstance(speakers,list) or not speakers:
        raise invalid('speakerVoiceConfigs 必须为非空数组')
    values,names = [],set()
    for speaker in speakers:
        object_fields(speaker,{'speaker','voiceConfig'},'speakerVoiceConfigs item')
        name = speaker.get('speaker')
        if not isinstance(name,str) or not name or name in names:
            raise invalid('speaker 必须为不重复的非空名称')
        names.add(name)
        values.append([name,voice(speaker.get('voiceConfig'))])
    encoded = [None,values]
    if 'mode' in multi:
        modes = {'VERBATIM':1,'CONVERSATIONAL':2}
        mode = multi['mode']
        if not isinstance(mode,str) or mode not in modes:
            raise invalid('多说话人 mode 必须为 VERBATIM 或 CONVERSATIONAL')
        encoded.append(modes[mode])
    return [None,None,encoded]


def encode_tools(request, model):
    codes = model.get('_codes',[])
    tools = request.get('tools',[])
    if not isinstance(tools,list):
        raise invalid('tools must be an array')
    config = object_fields(request.get('toolConfig',{}),{'functionCallingConfig'},'toolConfig')
    calling = object_fields(config.get('functionCallingConfig',{}),{'mode','allowedFunctionNames'},'functionCallingConfig')
    mode = calling.get('mode','AUTO')
    if mode not in ('AUTO','MODE_UNSPECIFIED','NONE'):
        raise invalid('Playground 当前仅支持 AUTO / NONE；ANY / VALIDATED 尚无原生调用约束')
    selected = string_list(calling['allowedFunctionNames'],'allowedFunctionNames',nonempty=True) if 'allowedFunctionNames' in calling else None
    declarations, builtins, names, builtin_names = [],[],set(),set()
    search = None
    for tool in tools:
        object_fields(tool,{'functionDeclarations','googleSearch','imageSearch','codeExecution','urlContext','googleMaps'},'tool')
        if not tool:
            raise invalid('tool 不能为空')
        for name,value in tool.items():
            if name == 'functionDeclarations':
                if 10 not in codes or not isinstance(value,list) or not value:
                    raise invalid('该模型不支持函数声明或声明数组为空')
                for declaration in value:
                    object_fields(declaration,{'name','description','parameters','parametersJsonSchema'},'functionDeclaration')
                    fn = declaration.get('name')
                    if not isinstance(fn,str) or not fn or fn in names or not isinstance(declaration.get('description',''),str):
                        raise invalid('函数名称必须为不重复的非空字符串，description 必须为字符串')
                    if 'parameters' in declaration and 'parametersJsonSchema' in declaration:
                        raise invalid('parameters 和 parametersJsonSchema 不能同时设置')
                    parameters = declaration.get('parameters',declaration.get('parametersJsonSchema'))
                    if parameters is None or parameters == {}:
                        parameters = {'type':'object'}
                    names.add(fn)
                    declarations.append([fn,declaration.get('description',''),encode_schema(parameters)])
            elif name in ('googleSearch','imageSearch'):
                if name == 'googleSearch':
                    object_fields(value,{'searchTypes','timeRangeFilter'},name)
                    types = object_fields(value.get('searchTypes',{'webSearch':{}}),{'webSearch','imageSearch'},'searchTypes')
                    if not types:
                        types = {'webSearch':{}}
                    if any(v != {} for v in types.values()):
                        raise invalid('searchTypes 的选项必须为空对象')
                    if 'webSearch' in types and 12 not in codes or 'imageSearch' in types and 54 not in codes:
                        raise invalid('该模型不支持所选搜索类型')
                else:
                    if value != {} or 54 not in codes:
                        raise invalid('该模型不支持 imageSearch 或选项非空')
                    types = {'imageSearch':{}}
                if search is None:
                    search = [None,[]]
                for kind,index in (('webSearch',0),('imageSearch',1)):
                    if kind in types:
                        set_field(search[1],index,[])
                if name == 'googleSearch' and 'timeRangeFilter' in value:
                    if search[0] is not None:
                        raise invalid('每个请求仅允许一个搜索时间范围')
                    period = object_fields(value['timeRangeFilter'],{'startTime','endTime'},'timeRangeFilter')
                    times = [None,None]
                    for index,key in enumerate(('startTime','endTime')):
                        if key in period:
                            try:
                                stamp = datetime.fromisoformat(period[key].replace('Z','+00:00'))
                                if stamp.tzinfo is None:
                                    raise ValueError()
                                times[index] = [str(math.floor(stamp.timestamp()))]
                            except (ValueError,TypeError,AttributeError,OverflowError):
                                raise invalid('搜索时间必须为带时区的 RFC 3339 日期时间') from None
                    if all(times) and int(times[0][0]) > int(times[1][0]):
                        raise invalid('搜索开始时间不能晚于结束时间')
                    search[0] = times
                builtin_names.add(name)
            else:
                code,index = {'codeExecution':(9,0),'urlContext':(13,7),'googleMaps':(58,10)}[name]
                if code not in codes or value != {}:
                    raise invalid(f'该模型不支持 {name} 或其选项尚未实现')
                if name not in builtin_names:
                    builtins.append(set_field([],index,[]))
                builtin_names.add(name)
    if 'googleMaps' in builtin_names and builtin_names & {'codeExecution','urlContext'}:
        raise invalid('googleMaps 不能与 codeExecution / urlContext 同时使用')
    if selected is not None:
        if not set(selected) <= names:
            raise invalid('allowedFunctionNames 包含未声明的函数')
        declarations = [d for d in declarations if d[0] in selected]
    if mode == 'NONE':
        return [],False
    if search is not None:
        builtins.append([None,None,None,search])
    return ([[None,declarations]] if declarations else []) + builtins,bool(declarations and builtins)


def encode_generate(model_id, request, model, timezone='', *, continuation_round=0, continuation_token=None):
    if not isinstance(request,dict) or set(request)-{'contents','systemInstruction','generationConfig','safetySettings','tools','toolConfig'}:
        raise invalid('Unsupported Gemini request field')
    codes = model.get('_codes',[])
    contents = request.get('contents')
    if not isinstance(contents,list) or not contents:
        raise invalid('contents must be a nonempty array')
    wire_contents, bindings = [], []
    last_text = []
    for content in contents:
        if not isinstance(content,dict) or set(content)-{'role','parts'} or content.get('role','user') not in ('user','model'):
            raise invalid('Content role must be user or model')
        parts = content.get('parts')
        if not isinstance(parts,list) or not parts:
            raise invalid('content.parts must be a nonempty array')
        wire_parts, last_text = [], []
        for part in parts:
            encoded,binding = encode_part(part)
            if ('functionCall' in part or 'functionResponse' in part) and 10 not in codes:
                raise invalid('该模型不支持函数调用历史')
            if 'functionCall' in part and content.get('role','user') != 'model' or 'functionResponse' in part and content.get('role','user') != 'user':
                raise invalid('functionCall 必须属于 model，functionResponse 必须属于 user')
            if part.get('mediaProcessing') == 'AGENTIC' and 69 not in codes:
                raise invalid('该模型不支持 AGENTIC 视频处理')
            if ('toolCall' in part or 'toolResponse' in part) and 69 not in codes:
                raise invalid('该模型不支持视频处理工具事件')
            if 'speechMetadata' in part and 85 not in codes:
                raise invalid('该模型不支持分段 speechMetadata')
            wire_parts.append(encoded)
            bindings.append(binding)
            if part.get('text'):
                last_text.append(part['text'])
        wire_contents.append([wire_parts,content.get('role','user')])
    config = request.get('generationConfig',{})
    supported = {'maxOutputTokens','temperature','topP','topK','stopSequences','responseMimeType','seed','thinkingConfig','candidateCount',
                 'responseSchema','responseJsonSchema','responseModalities','imageConfig','speechConfig','mediaResolution','transcriptionConfig'}
    if not isinstance(config,dict) or set(config)-supported:
        raise invalid('Unsupported generationConfig field; see README supported parameters')
    if type(config.get('candidateCount',1)) is not int or config.get('candidateCount',1) != 1:
        raise invalid('Playground supports one candidate in this adapter')
    continuable = 91 in codes
    if (type(continuation_round) is not int or continuation_round < 0
            or continuation_token is not None and (not isinstance(continuation_token,str) or not continuation_token)
            or continuation_round > 0 and continuation_token is None
            or continuation_token is not None and continuation_round == 0
            or not continuable and (continuation_round != 0 or continuation_token is not None)):
        raise PlaygroundError('Invalid GenerateContent continuation state',code='protocol_error')
    g = [None]*14
    g[13] = 1
    limit = model['outputTokenLimit']
    if type(limit) is not int or limit <= 0:
        raise PlaygroundError('Model catalog has no output token limit',code='protocol_error')
    maximum = limit
    if continuable:
        input_limit = model.get('inputTokenLimit',0)
        if type(input_limit) is not int or input_limit < 0:
            raise PlaygroundError('Model catalog has an invalid input token limit',code='protocol_error')
        maximum = max(maximum,input_limit)
    total = numeric(config.get('maxOutputTokens',limit),'maxOutputTokens',1,maximum,True)
    g[3] = min(total,(continuation_round+1)*CONTINUATION_ROUND_TOKENS) if continuable else total
    for key,idx,low,high,is_int in [('temperature',4,0,model.get('maxTemperature',2),False),('topP',5,0,1,False),('topK',6,0,None,True),('seed',18,0,2147483647,True)]:
        value = config.get(key,model.get(key))
        if value is not None:
            if idx >= len(g):
                g += [None]*(idx+1-len(g))
            g[idx] = numeric(value,key,low,high,is_int)
    if 'stopSequences' in config:
        stops = config['stopSequences']
        if not isinstance(stops,list) or not all(isinstance(s,str) and s for s in stops):
            raise invalid('stopSequences must contain nonempty strings')
        g[1] = stops
    if 'responseMimeType' in config:
        if config['responseMimeType'] not in ('text/plain','application/json','text/x.enum'):
            raise invalid('Unsupported responseMimeType')
        g[7] = config['responseMimeType']
    if 'responseSchema' in config or 'responseJsonSchema' in config:
        if 'responseSchema' in config and 'responseJsonSchema' in config:
            raise invalid('responseSchema 和 responseJsonSchema 不能同时设置')
        if g[7] not in ('application/json','text/x.enum'):
            raise invalid('结构化输出需要 responseMimeType=application/json 或 text/x.enum')
        g[8] = encode_schema(config.get('responseSchema',config.get('responseJsonSchema')))
    modalities = config.get('responseModalities')
    if modalities is None:
        modalities = ['AUDIO'] if 37 in codes else ['TEXT','IMAGE'] if 21 in codes else None
    if modalities is not None:
        string_list(modalities,'responseModalities',nonempty=True)
        requested = set(modalities)
        available = {'AUDIO'} if 37 in codes else {'TEXT','IMAGE'} if 21 in codes else {'TEXT'}
        if not requested <= available:
            raise invalid('responseModalities 超出该模型支持的输出类型')
        set_field(g,14,[code for name,code in (('IMAGE',2),('TEXT',1),('AUDIO',3)) if name in requested])
    image = config.get('imageConfig')
    if image is not None:
        object_fields(image,{'aspectRatio','imageSize'},'imageConfig')
        if 21 not in codes:
            raise invalid('该模型不支持图像生成')
        image_wire = []
        for key,index,capability,option in (('aspectRatio',0,47,'image_aspect_ratios'),('imageSize',1,49,'image_output_resolutions')):
            if key in image:
                value = image[key]
                choices = model.get('_options',{}).get(option,[])
                if capability not in codes or not isinstance(value,str) or not value or choices and value not in choices:
                    raise invalid(f'imageConfig.{key} 超出该模型支持的选项')
                if key == 'aspectRatio' and not re.fullmatch(r'[1-9]\d*:[1-9]\d*',value):
                    raise invalid('imageConfig.aspectRatio 必须为正整数比例，例如 16:9')
                if key == 'imageSize' and value not in ('512','1K','2K','4K'):
                    raise invalid('imageConfig.imageSize 必须为目录支持的分辨率')
                set_field(image_wire,index,value)
        if image_wire:
            set_field(g,26,image_wire)
    elif 49 in codes and 21 in codes:
        set_field(g,26,[None,'1K'])
    speech = config.get('speechConfig')
    if speech is None and 37 in codes:
        speech = {'voiceConfig':{'prebuiltVoiceConfig':{'voiceName':'Zephyr'}}}
    if speech is not None:
        set_field(g,15,encode_speech(speech,model))
        if 'maxOutputTokens' not in config:
            g[3] = None
    if 'mediaResolution' in config:
        resolutions = {'MEDIA_RESOLUTION_UNSPECIFIED':None,'MEDIA_RESOLUTION_LOW':1,'MEDIA_RESOLUTION_MEDIUM':2,'MEDIA_RESOLUTION_HIGH':3}
        resolution = config['mediaResolution']
        if 43 not in codes or not isinstance(resolution,str) or resolution not in resolutions:
            raise invalid('mediaResolution 超出该模型支持的选项')
        if resolutions[resolution] is not None:
            set_field(g,17,resolutions[resolution])
    if 'transcriptionConfig' in config:
        transcript = object_fields(config['transcriptionConfig'],{'wordTimestamps','speakerLabels','customVocabulary','languageCodes','smartTranscription'},'transcriptionConfig')
        encoded = []
        for key,index,capability,code in (('wordTimestamps',4,74,1),('speakerLabels',5,80,1),('smartTranscription',8,84,2)):
            if key in transcript:
                if not isinstance(transcript[key],bool) or transcript[key] and capability not in codes:
                    raise invalid(f'该模型不支持 transcriptionConfig.{key} 或值不是布尔值')
                if transcript[key]:
                    set_field(encoded,index,code)
        for key,index,capability in (('customVocabulary',6,81),('languageCodes',7,76)):
            if key in transcript:
                values = string_list(transcript[key],'transcriptionConfig.'+key)
                if values and capability not in codes:
                    raise invalid(f'该模型不支持 transcriptionConfig.{key}')
                languages = model.get('_options',{}).get('languages',[])
                if key == 'languageCodes' and languages and any(value not in languages for value in values):
                    raise PlaygroundError('Transcription language code is not in the model catalog',400,
                        'invalid_request_error',public_message='transcriptionConfig.languageCodes 必须使用该模型目录 pg_proxy.upstream.options.languages 中列出的代码；不会自动转换地区代码。')
                if values:
                    set_field(encoded,index,values)
        if transcript.get('smartTranscription') and (transcript.get('wordTimestamps') or transcript.get('speakerLabels')):
            raise PlaygroundError('Smart transcription conflicts with timestamps or speaker labels',400,
                'invalid_request_error',public_message='transcriptionConfig.smartTranscription 不能与 wordTimestamps 或 speakerLabels 同时启用。')
        if transcript.get('wordTimestamps') and transcript.get('customVocabulary'):
            raise PlaygroundError('Custom vocabulary conflicts with word timestamps',400,
                'invalid_request_error',public_message='transcriptionConfig.wordTimestamps 与非空 customVocabulary 不能同时启用，请分开使用。')
        if transcript.get('speakerLabels') and transcript.get('customVocabulary'):
            raise PlaygroundError('Custom vocabulary conflicts with diarization',400,
                'invalid_request_error',public_message='transcriptionConfig.speakerLabels 与非空 customVocabulary 不能同时启用，请分开使用。')
        if encoded:
            set_field(g,31,encoded)
    thinking = config.get('thinkingConfig')
    if thinking is not None:
        if not isinstance(thinking,dict) or set(thinking)-{'includeThoughts','thinkingBudget','thinkingLevel'} or 25 not in codes:
            raise invalid('Unsupported thinkingConfig for this model')
        if 'includeThoughts' in thinking and not isinstance(thinking['includeThoughts'],bool):
            raise invalid('includeThoughts must be a boolean')
        t = [1 if thinking.get('includeThoughts',True) else 0]
        if 'thinkingBudget' in thinking:
            if 35 not in codes:
                raise invalid('This model does not support thinkingBudget')
            budget_info = model.get('_thinking',[])
            budget = numeric(thinking['thinkingBudget'],'thinkingBudget',-1,at(budget_info,1),True)
            minimum = at(budget_info,2,0)
            if budget != -1 and budget < minimum and not (budget == 0 and at(budget_info,3,False)):
                raise invalid('thinkingBudget below model minimum')
            t += [budget]
        if 'thinkingLevel' in thinking:
            levels = {'LOW':1,'MEDIUM':2,'HIGH':3,'MINIMAL':4}
            level = levels.get(str(thinking['thinkingLevel']).upper())
            supported_levels = at(model.get('_thinking',[]),6,[])
            if 52 not in codes or level is None or level not in supported_levels:
                raise invalid('Unsupported thinkingLevel for this model')
            t += [None]*(4-len(t)); t[3] = level
        if len(g)<17:
            g += [None]*(17-len(g))
        g[16] = t
    if continuable:
        g += [None]*(46-len(g))
        g[45] = 1
    wire = [None]*14 if timezone else [None]*11
    wire[0],wire[1],wire[3],wire[10] = 'models/'+model_id, wire_contents,g,None if continuable else 1
    if continuation_token is not None:
        wire += [None]*(17-len(wire))
        wire[16] = continuation_token
    if timezone:
        wire[13] = [[None,None,timezone]]
    system = request.get('systemInstruction')
    if system is not None:
        if not isinstance(system,dict) or set(system)-{'parts','role'} or not isinstance(system.get('parts'),list) or not system['parts'] or any(not isinstance(p,dict) or set(p) != {'text'} or not isinstance(p['text'],str) for p in system['parts']):
            raise invalid('systemInstruction must contain text parts')
        wire[5] = [[[None,p['text']] for p in system['parts']], 'user']
    if 'safetySettings' in request:
        categories = {'HARM_CATEGORY_HARASSMENT':7,'HARM_CATEGORY_HATE_SPEECH':8,'HARM_CATEGORY_SEXUALLY_EXPLICIT':9,'HARM_CATEGORY_DANGEROUS_CONTENT':10,'HARM_CATEGORY_CIVIC_INTEGRITY':11}
        thresholds = {'BLOCK_LOW_AND_ABOVE':1,'BLOCK_MEDIUM_AND_ABOVE':2,'BLOCK_ONLY_HIGH':3,'BLOCK_NONE':4,'OFF':5}
        safety = request['safetySettings']
        if not isinstance(safety,list):
            raise invalid('safetySettings must be an array')
        wire[2] = []
        for s in safety:
            if (not isinstance(s,dict) or set(s)-{'category','threshold'}
                    or not isinstance(s.get('category'),str) or not isinstance(s.get('threshold'),str)
                    or s['category'] not in categories or s['threshold'] not in thresholds):
                raise invalid('Unsupported safetySettings')
            wire[2].append([None,None,categories[s['category']],thresholds[s['threshold']]])
    if 'tools' in request or 'toolConfig' in request:
        wire[6],server_tools = encode_tools(request,model)
        if server_tools:
            if len(wire) < 14:
                wire += [None]*(14-len(wire))
            tool_config = wire[13] or [None]
            wire[13] = set_field(tool_config,2,True)
    elif 49 in codes and 21 in codes:
        # The website sends the empty search extension for resolution-capable images.
        wire[6] = [[None,None,None,[None,[]]]]
    return wire, ' '.join(bindings), ' '.join(last_text)


async def iter_frames(chunks, max_buffer=16*1024*1024):
    """Incrementally consume root field 1 repeated frames, never buffer the whole answer."""
    normalizer, decoder = SparseJSON(), json.JSONDecoder()
    buffer, state, need_item = '', 0, True
    async for chunk in chunks:
        buffer += normalizer.feed(chunk)
        if len(buffer)>max_buffer:
            raise PlaygroundError('Playground frame exceeded buffer limit',code='protocol_error')
        while True:
            buffer = buffer.lstrip()
            if state < 2:
                if not buffer:
                    break
                if not buffer.startswith('['):
                    raise PlaygroundError('GenerateContent response layout changed',code='protocol_error')
                buffer,state = buffer[1:],state+1
            elif state == 2:
                if not buffer:
                    break
                if buffer[0] == ']':
                    buffer,state = buffer[1:],3
                    break
                if not need_item:
                    if buffer[0] != ',':
                        raise PlaygroundError('Missing Playground frame separator',code='protocol_error')
                    buffer,need_item = buffer[1:],True
                    continue
                try:
                    item,end = decoder.raw_decode(buffer)
                except ValueError:
                    break
                if not isinstance(item,list):
                    raise PlaygroundError('Invalid Playground stream frame',code='protocol_error')
                yield item
                buffer,need_item = buffer[end:],False
            else:
                break
    try:
        tail = json.loads('[[]'+buffer) if state == 3 else None
        if tail is None:
            raise ValueError()
    except ValueError:
        raise PlaygroundError('Truncated or malformed Playground response',code='stream_interrupted') from None


FINISH = {0:'FINISH_REASON_UNSPECIFIED',1:'STOP',2:'MAX_TOKENS',3:'SAFETY',4:'RECITATION',5:'OTHER',6:'LANGUAGE',7:'BLOCKLIST',8:'PROHIBITED_CONTENT',9:'SPII',10:'MALFORMED_FUNCTION_CALL',11:'IMAGE_SAFETY',12:'UNEXPECTED_TOOL_CALL',13:'TOO_MANY_TOOL_CALLS',14:'IMAGE_PROHIBITED_CONTENT',15:'IMAGE_OTHER',16:'NO_IMAGE',17:'IMAGE_RECITATION',18:'MISSING_THOUGHT_SIGNATURE'}


def response_fields(raw, fields):
    result = {}
    if not isinstance(raw,list):
        raise PlaygroundError('Unexpected Playground metadata layout',code='protocol_error')
    for index,name,kind in fields:
        value = at(raw,index)
        if value is not None:
            if kind is int:
                valid = type(value) is int and value >= 0
            elif kind is float:
                try:
                    valid = type(value) in (int,float) and math.isfinite(value)
                except OverflowError:
                    valid = False
            else:
                valid = isinstance(value,kind)
            if not valid:
                raise PlaygroundError('Unexpected Playground metadata type: '+name,code='protocol_error')
            result[name] = value
    return result


def decode_grounding(raw):
    fields = response_fields(raw,((0,'entry',list),(1,'chunks',list),(2,'supports',list),(3,'retrieval',list),(4,'queries',list),(5,'queries2',list),(6,'mapsWidgetContextToken',str)))
    result = {}
    if 'entry' in fields:
        result['searchEntryPoint'] = response_fields(fields['entry'],((0,'renderedContent',str),(1,'sdkBlob',str)))
    if 'chunks' in fields:
        result['groundingChunks'] = []
        for chunk in fields['chunks']:
            variants = [index for index in range(3) if at(chunk,index) is not None]
            if len(variants) != 1:
                raise PlaygroundError('Unexpected Playground grounding chunk source',code='protocol_error')
            index = variants[0]
            value = response_fields(chunk[index],((0,'uri',str),(1,'title',str),(2,'text',str),(3,'placeId',str)))
            result['groundingChunks'].append({('web','retrievedContext','maps')[index]:value})
    if 'supports' in fields:
        result['groundingSupports'] = []
        for raw_support in fields['supports']:
            support = response_fields(raw_support,((0,'segment',list),(1,'groundingChunkIndices',list),(2,'confidenceScores',list)))
            if 'segment' in support:
                support['segment'] = response_fields(support['segment'],((0,'partIndex',int),(1,'startIndex',int),(2,'endIndex',int),(3,'text',str)))
            for name,kind in (('groundingChunkIndices',int),('confidenceScores',float)):
                if name in support:
                    for value in support[name]:
                        response_fields([value],((0,name,kind),))
            result['groundingSupports'].append(support)
    if 'retrieval' in fields:
        result['retrievalMetadata'] = response_fields(fields['retrieval'],((1,'googleSearchDynamicRetrievalScore',float),))
    if 'queries' in fields or 'queries2' in fields:
        queries = fields.get('queries',[]) + fields.get('queries2',[])
        if any(not isinstance(value,str) for value in queries):
            raise PlaygroundError('Unexpected Playground search query type',code='protocol_error')
        result['webSearchQueries'] = list(dict.fromkeys(queries))
    if 'mapsWidgetContextToken' in fields:
        result['mapsWidgetContextToken'] = fields['mapsWidgetContextToken']
    return result


def decode_transcript_duration(raw):
    # protobuf Duration seconds are int64: JSPB may emit a decimal string.
    # Transcript offsets are nonnegative; a missing seconds field means zero.
    seconds = at(raw,0,0)
    if isinstance(seconds,str):
        if not re.fullmatch(r'[0-9]{1,12}',seconds):
            raise PlaygroundError('Unexpected transcript seconds',code='protocol_error')
        seconds = int(seconds)
    if type(seconds) is not int or not 0 <= seconds <= 315576000000:
        raise PlaygroundError('Unexpected transcript seconds',code='protocol_error')
    result = {'seconds':seconds}
    nanos = at(raw,1)
    if nanos is not None:
        if type(nanos) is not int or not 0 <= nanos < 1000000000:
            raise PlaygroundError('Unexpected transcript nanoseconds',code='protocol_error')
        result['nanos'] = nanos
    return result


def decode_transcript(raw):
    result = response_fields(raw,((0,'text',str),(1,'speaker',str),(2,'timestamps',list)))
    if 'timestamps' in result:
        timestamps = []
        for timestamp in result['timestamps']:
            item = response_fields(timestamp,((0,'text',str),(1,'start',list),(2,'end',list)))
            for name in ('start','end'):
                if name in item:
                    item[name] = decode_transcript_duration(item[name])
            timestamps.append(item)
        result['timestamps'] = timestamps
    return result


def decode_frame(frame, *, continuable=False):
    candidates = at(frame,0)
    if candidates is None:
        feedback = at(frame,1)
        if feedback is not None:
            return {'promptFeedback':{'blockReason':{1:'SAFETY',2:'OTHER',3:'BLOCKLIST',4:'PROHIBITED_CONTENT',5:'IMAGE_SAFETY'}.get(at(feedback,0),'BLOCK_REASON_UNSPECIFIED')}}
        return None
    if not isinstance(candidates,list) or len(candidates)!=1:
        raise PlaygroundError('Unexpected Playground candidate count',code='protocol_error')
    source = candidates[0]
    candidate = {'index':0,'content':{'role':'model','parts':[]}}
    content = at(source,0)
    if content is not None:
        if at(content,1) != 'model':
            raise PlaygroundError('Unexpected Playground response role',code='protocol_error')
        parts = at(content,0,[])
        if not isinstance(parts,list):
            raise PlaygroundError('Unexpected Playground response parts',code='protocol_error')
        for part in parts:
            p = {}
            transcript_part = None
            if at(part,1) is not None:
                if not isinstance(part[1],str):
                    raise PlaygroundError('Unexpected Playground text type',code='protocol_error')
                p['text'] = part[1]
            if at(part,2) is not None:
                p['inlineData'] = response_fields(part[2],((0,'mimeType',str),(1,'data',str)))
                if set(p['inlineData']) != {'mimeType','data'}:
                    raise PlaygroundError('Incomplete Playground inline data',code='protocol_error')
            if at(part,7) is not None:
                p['executableCode'] = dict(language={0:'LANGUAGE_UNSPECIFIED',1:'PYTHON'}.get(at(part[7],0),'UNKNOWN'),code=at(part[7],1,''))
            if at(part,8) is not None:
                p['codeExecutionResult'] = dict(outcome={0:'OUTCOME_UNSPECIFIED',1:'OUTCOME_OK',2:'OUTCOME_FAILED',3:'OUTCOME_DEADLINE_EXCEEDED'}.get(at(part[8],0),'UNKNOWN'),output=at(part[8],1,''))
            if at(part,10) is not None:
                call = response_fields(part[10],((0,'name',str),(1,'args',list),(2,'id',str)))
                if not call.get('name'):
                    raise PlaygroundError('Playground function call missing name',code='protocol_error')
                call['args'] = decode_struct(call.get('args',[]))
                p['functionCall'] = call
            if at(part,11) is not None:
                response = response_fields(part[11],((0,'name',str),(1,'response',list),(2,'id',str)))
                if not response.get('name'):
                    raise PlaygroundError('Playground function response missing name',code='protocol_error')
                response['response'] = decode_struct(response.get('response',[]))
                p['functionResponse'] = response
            if at(part,22) is not None:
                transcript = decode_transcript(part[22])
                text = transcript.pop('text','')
                p['transcriptionMetadata'] = transcript
                if 'text' not in p:
                    p['text'] = text
                elif p['text'] != text:
                    transcript_part = {'text':text,'transcriptionMetadata':transcript}
                    del p['transcriptionMetadata']
            for index,key,data_key in ((25,'toolCall','args'),(26,'toolResponse','response')):
                if at(part,index) is not None and at(part[index],0) == 13:
                    step = response_fields(part[index],((1,'toolName',str),(2,data_key,list),(3,'id',str)))
                    step['toolType'] = 'MEDIA_PROCESSING'
                    if data_key in step:
                        step[data_key] = decode_struct(step[data_key])
                    p[key] = step
            if at(part,40) is not None:
                p['speechMetadata'] = response_fields(part[40],((0,'speaker',str),(1,'style',str)))
            # JSPB encodes bool as either JSON true/false or numeric 0/1.
            # Both forms occur in authenticated Playground thought frames.
            thought = at(part,12)
            if thought is not None and not (type(thought) is bool or type(thought) is int and thought in (0,1)):
                raise PlaygroundError('Unexpected Playground thought type',code='protocol_error')
            if thought:
                p['thought'] = True
            if at(part,14) is not None:
                if not isinstance(part[14],str):
                    raise PlaygroundError('Unexpected Playground thought signature type',code='protocol_error')
                p['thoughtSignature'] = part[14]
            if p:
                candidate['content']['parts'].append(p)
            if transcript_part is not None:
                if 'thoughtSignature' in p:
                    transcript_part['thoughtSignature'] = p['thoughtSignature']
                candidate['content']['parts'].append(transcript_part)
    code = at(source,1)
    if code is not None:
        if type(code) is not int:
            raise PlaygroundError('Unexpected Playground finish code type',code='protocol_error')
        if code == 22 and not continuable:
            raise PlaygroundError('Upstream requested unsupported continuation',code='protocol_error')
        candidate['finishReason'] = 'MAX_TOKENS' if code == 22 else FINISH.get(code,f'PROVIDER_{code}')
    result = {'candidates':[candidate]}
    if at(source,3) is not None:
        candidate.update(response_fields(source,((3,'finishMessage',str),)))
    if at(source,6) is not None:
        entries = at(source[6],0,[])
        if not isinstance(entries,list):
            raise PlaygroundError('Unexpected Playground citations',code='protocol_error')
        candidate['citationMetadata'] = {'citationSources':[response_fields(entry,((2,'uri',str),(3,'title',str))) for entry in entries]}
    if at(source,7) is not None:
        candidate['groundingMetadata'] = decode_grounding(source[7])
    if continuable:
        token = at(source,8)
        if token is not None and not isinstance(token,str):
            raise PlaygroundError('Unexpected Playground continuation token type',code='protocol_error')
        if token:
            result['_continuationToken'] = token
        if code is not None:
            result['_finishCode'] = code
    usage = at(frame,2)
    if usage is not None:
        metadata = {key:at(usage,idx,0) for idx,key in ((0,'promptTokenCount'),(1,'candidatesTokenCount'),(2,'totalTokenCount'),(3,'cachedContentTokenCount'),(9,'thoughtsTokenCount'))}
        if at(usage,7) is not None:
            metadata['toolUsePromptTokenCount'] = usage[7]
        if any(type(v) is not int or v<0 for v in metadata.values()):
            raise PlaygroundError('Unexpected Playground token usage type',code='protocol_error')
        # Some frames omit output usage. Preserve explicit values (including zero).
        if at(usage,1) is None:
            metadata['candidatesTokenCount'] = max(0,metadata['totalTokenCount']-
                metadata['promptTokenCount']-metadata['thoughtsTokenCount']-
                metadata.get('toolUsePromptTokenCount',0))
        result['usageMetadata'] = metadata
    return result


def encode_logging_context(fields):
    def varint(value):
        out = bytearray()
        value &= (1<<64)-1
        while value>=128:
            out.append((value&127)|128); value >>=7
        out.append(value)
        return bytes(out)
    out = b''
    if not isinstance(fields,list):
        raise PlaygroundError('Invalid logging context',code='protocol_error')
    for index,value in enumerate(fields,1):
        if value is None:
            continue
        if isinstance(value,str):
            data = value.encode()
            out += varint(index<<3|2)+varint(len(data))+data
        elif isinstance(value,int):
            out += varint(index<<3)+varint(value)
        else:
            raise PlaygroundError('Logging context layout changed',code='protocol_error')
    return base64.b64encode(out).decode()
