"""Public model metadata and pagination, independent of provider credentials.

The standard API fields remain separate from the versioned ``pg_proxy``
extension. Capability evidence describes the provider; proxy fields describe
the subset that this adapter can encode. No model-name heuristics are used.
"""
import base64
import copy
import hashlib
import json
import math
import re

from .errors import PlaygroundError


ASPECT_RATIOS = {1:'1:1',2:'9:16',3:'16:9',4:'3:4',5:'4:3',6:'3:2',7:'2:3',
                 8:'5:4',9:'4:5',10:'21:9',11:'9:21',12:'1:4',13:'4:1',14:'1:8',15:'8:1'}
IMAGE_SIZES = {1:'1K',2:'2K',3:'4K',4:'512'}
THINKING_LEVELS = {1:'LOW',2:'MEDIUM',3:'HIGH',4:'MINIMAL'}
TOOL_CAPABILITIES = {9:'code_execution',10:'function_calling',12:'google_search',
                     13:'url_context',54:'image_search',58:'google_maps'}
TYPE_CAPABILITIES = {1:'chat',20:'video',21:'image',26:'live',37:'speech',53:'music',77:'transcription'}


def _documented(inputs, model, *, output=('text',), context=None, source=None):
    return {'input_modalities':list(inputs),'output_modalities':list(output),'context_tokens':context,
            'source_url':source or 'https://ai.google.dev/gemini-api/docs/models/'+model,
            'checked_at':'2026-10-30'}


# Supplemental documentation facts, NOT an allowlist. New models remain dynamic
# and receive null input modalities until a reliable source establishes them.
_MULTIMODAL = ('text','image','audio','video','document')
_IMAGE_INPUTS = ('text','image','video','document')
_GEMMA_CARD = 'https://ai.google.dev/gemma/docs/core/model_card_4'
DOCUMENTED_MODELS = {
    'gemini-4-argon':_documented(_MULTIMODAL,'gemini-4-argon',context=1048576,
        source='https://aistudio.google.com/docs/models/gemini-4-argon'),
    'gemini-3.8-flash':_documented(_MULTIMODAL,'gemini-3.8-flash',
        source='https://aistudio.google.com/docs/models/gemini-3.8-flash'),
    'gemini-3.6-flash':_documented(_MULTIMODAL,'gemini-3.6-flash',
        source='https://aistudio.google.com/docs/models/gemini-3.6-flash'),
    'gemini-3.5-flash-lite':_documented(_MULTIMODAL,'gemini-3.5-flash-lite',
        source='https://aistudio.google.com/docs/models/gemini-3.5-flash-lite'),
    'gemini-3.1-pro-preview':_documented(_MULTIMODAL,'gemini-3.1-pro-preview'),
    'gemini-3.1-flash-lite':_documented(_MULTIMODAL,'gemini-3.1-flash-lite'),
    'gemini-3-flash-preview':_documented(_MULTIMODAL,'gemini-3-flash-preview'),
    'gemini-nano-banana-2.1':_documented(_IMAGE_INPUTS,'gemini-nano-banana-2.1',output=('text','image'),
        source='https://aistudio.google.com/docs/models/gemini-nano-banana-2.1'),
    'gemini-3.1-flash-image':_documented(_IMAGE_INPUTS,'gemini-3.1-flash-image',output=('text','image')),
    'gemini-3.1-flash-lite-image':_documented(_IMAGE_INPUTS,'gemini-3.1-flash-lite-image',output=('text','image')),
    'gemini-3-pro-image':_documented(('text','image'),'gemini-3-pro-image',output=('text','image')),
    'gemini-2.5-flash-image':_documented(('text','image'),'gemini-2.5-flash-image',output=('text','image')),
    'gemini-3.5-transcribe':_documented(('audio',),'gemini-3.5-transcribe'),
    'gemini-robotics-er-2-preview':_documented(('text','image','audio','video'),'gemini-robotics-er-2-preview'),
    'gemma-4-26b-a4b-it':_documented(('text','image'),'gemma-4-26b-a4b-it',context=262144,source=_GEMMA_CARD),
    'gemma-4-31b-it':_documented(('text','image'),'gemma-4-31b-it',context=262144,source=_GEMMA_CARD),
}


def field(row, index, default=None):
    if not isinstance(row,list):
        raise PlaygroundError('Unexpected model metadata array',code='protocol_error')
    return row[index] if len(row)>index and row[index] is not None else default


def typed_list(value, kind, label):
    if not isinstance(value,list) or any(type(item) is not kind for item in value):
        raise PlaygroundError('Invalid model '+label,code='protocol_error')
    return list(dict.fromkeys(value))


def _options(row):
    result = {}
    for index,key,mapping in ((75,'image_aspect_ratios',ASPECT_RATIOS),(76,'image_output_resolutions',IMAGE_SIZES)):
        values = typed_list(field(row,index,[]),int,key)
        result[key] = [mapping[value] for value in values if value in mapping]
    for indices,key in (((66,65),'voices'),((67,),'languages')):
        result[key] = []
        for index in indices:
            rows = field(row,index,[])
            if not isinstance(rows,list):
                raise PlaygroundError('Invalid model '+key,code='protocol_error')
            for entry in rows:
                value = field(entry,0)
                if value is not None and not isinstance(value,str):
                    raise PlaygroundError('Invalid model '+key,code='protocol_error')
                if value and value not in result[key]:
                    result[key].append(value)
    return result


def parse_model_row(row):
    """Return a supported model with private, typed capability evidence."""
    name = field(row,0)
    if not isinstance(name,str) or not name.startswith('models/') or not name[7:]:
        raise PlaygroundError('ListModels model name changed',code='protocol_error')
    methods = typed_list(field(row,7,[]),str,'generation methods')
    codes = typed_list(field(row,64,[]),int,'capability codes')
    if 'generateContent' not in methods or 1 not in codes or any(code in codes for code in (20,26,53)) or field(row,78) is not None:
        return None
    model = {'name':name,'baseModelId':name.removeprefix('models/'),
             'displayName':field(row,3,name),'description':field(row,4,''),
             'inputTokenLimit':field(row,5,0),'outputTokenLimit':field(row,6,0),
             'supportedGenerationMethods':['generateContent','streamGenerateContent']+
                 (['countTokens'] if 'countTokens' in methods else []),'thinking':25 in codes,
             '_codes':codes,'_thinking':field(row,71,[]),'_options':_options(row),
             '_upstream_methods':methods,'_secondary_codes':typed_list(field(row,74,[]),int,'secondary capability codes')}
    for key in ('displayName','description'):
        if not isinstance(model[key],str):
            raise PlaygroundError('Invalid model '+key,code='protocol_error')
    for key in ('inputTokenLimit','outputTokenLimit'):
        if type(model[key]) is not int or model[key]<0:
            raise PlaygroundError('Invalid model '+key,code='protocol_error')
    if not isinstance(model['_thinking'],list):
        raise PlaygroundError('Invalid model thinking configuration',code='protocol_error')
    version = field(row,2)
    if version is not None:
        if not isinstance(version,str):
            raise PlaygroundError('Invalid model version',code='protocol_error')
        model['version'] = version
    details = field(row,38,[])
    alias = field(details,14)
    if isinstance(alias,str):
        target = re.fullmatch(r'Points to ([A-Za-z0-9_.-]+)',alias)
        if target:
            model['_alias_target'] = target[1]
    for index,key in ((8,'temperature'),(9,'topP'),(10,'topK'),(27,'maxTemperature')):
        if field(row,index) is not None:
            if type(row[index]) not in (int,float) or not math.isfinite(row[index]):
                raise PlaygroundError('Invalid model '+key,code='protocol_error')
            model[key] = row[index]
    return model


def _reasoning(model):
    codes,config = model.get('_codes',[]),model.get('_thinking',[])
    result = {'supported':25 in codes,'budget_supported':25 in codes and 35 in codes,
              'level_supported':25 in codes and 52 in codes}
    if result['budget_supported']:
        budget = {}
        for index,key in ((0,'default'),(1,'max'),(2,'min')):
            value = field(config,index)
            if type(value) is int:
                budget[key] = value
        can_disable = field(config,3)
        if type(can_disable) is bool:
            budget['can_disable'] = can_disable
        result['budget'] = budget
    if result['level_supported']:
        levels = field(config,6,[])
        result['levels'] = [THINKING_LEVELS[value] for value in levels if type(value) is int and value in THINKING_LEVELS] if isinstance(levels,list) else []
        default = field(config,5)
        if type(default) is int and default in THINKING_LEVELS and THINKING_LEVELS[default] in result['levels']:
            result['default_level'] = THINKING_LEVELS[default]
    return result


def _documentation(model):
    key = model.get('_alias_target',model['name'].removeprefix('models/'))
    return DOCUMENTED_MODELS.get(key)


def _output_modalities(model):
    codes = model.get('_codes',[])
    if 37 in codes or 53 in codes:
        return ['audio']
    if 21 in codes:
        return ['text','image']
    return ['text'] if 1 in codes else None


def model_extension(model):
    codes = model.get('_codes',[])
    reasoning = _reasoning(model)
    types = [name for code,name in TYPE_CAPABILITIES.items() if code in codes]
    documentation = _documentation(model)
    inputs = list(documentation['input_modalities']) if documentation else None
    outputs = _output_modalities(model)
    tools = [name for code,name in TOOL_CAPABILITIES.items() if code in codes]
    limits = {'input_tokens':model.get('inputTokenLimit',0),'output_tokens':model.get('outputTokenLimit',0),
              'context_tokens':documentation['context_tokens'] if documentation else None}
    total = max(limits['input_tokens'],limits['output_tokens']) if 91 in codes else limits['output_tokens']
    model_type = 'image' if 21 in codes else 'transcription' if 77 in codes else 'chat'
    modality_source = ({'source_url':documentation['source_url'],'checked_at':documentation['checked_at'],
                        'resolved_model':model.get('_alias_target',model['name'].removeprefix('models/'))}
                       if documentation else None)
    openai_inputs = [value for value in inputs if value in ('text','image','audio')] if inputs is not None else None
    openai_outputs = [value for value in outputs if value in ('text','image')] if outputs is not None else None
    interfaces = {
        'gemini_generate_content':{'input_modalities':inputs,'output_modalities':outputs,
            'function_calling':10 in codes,'function_calling_modes':['AUTO','NONE'] if 10 in codes else [],
            'media_transport':['inlineData','fileData_https'],'tools':list(tools),
            'json_schema_constraints':'validated_subset','client_must_preserve_signatures':True,
            'unsupported_features':['function_calling_ANY','function_calling_VALIDATED','files_upload','maps_coordinates']},
        'openai_chat_completions':{'input_modalities':openai_inputs,'output_modalities':openai_outputs,
            'function_calling':10 in codes,'tool_choice_modes':['auto','none'] if 10 in codes else [],
            'parallel_tool_calls_values':[True] if 10 in codes else [],
            'tools':list(tools),'remote_media_download':False,
            'input_formats':{'image':'base64_data_url','audio':['wav','mp3','flac','ogg','aac','mp4','webm']},
            'unsupported_input_modalities':['video','document'],
            'nonstandard_output_fields':['reasoning_content','images','audio_chunks','extra_content'],
            'standard_audio_output':False,'strict_json_schema':False,
            'client_must_preserve_signatures':True,'signature_transport':'extra_content.google',
            'unsupported_features':['required_tool_choice','named_tool_choice','audio_transcoding']},
    }
    return {'schema_version':1,'source':'google_ai_studio_playground',
        'upstream':{'generation_methods':list(model.get('_upstream_methods',[])),
            'capability_codes':list(codes),'secondary_capability_codes':list(model.get('_secondary_codes',[])),
            'types':types,'input_modalities':inputs,'output_modalities':outputs,'modality_source':modality_source,
            'tools':tools,'reasoning':copy.deepcopy(reasoning),'limits':limits,
            'options':copy.deepcopy(model.get('_options',{}))},
        'proxy':{'type':model_type,'generation_methods':list(model.get('supportedGenerationMethods',[])),
            'input_modalities':inputs,'output_modalities':outputs,'tools':list(tools),
            'interfaces':interfaces,
            'request_compatibility':{'unknown_optional_parameters':'ignore',
                'unsupported_optional_parameters':'ignore',
                'missing_openai_model':'select_available_text_model',
                'invalid_explicit_model':'error',
                'strict_schema_enforced':False,'forced_tool_choice_enforced':False,
                'required_message_data':'validate','automatic_generation_retry':False},
            'function_calling':10 in codes,'reasoning':reasoning,
            'limits':{**limits,'max_output_tokens':total,'default_output_tokens':limits['output_tokens']},
            'output_continuation':91 in codes,
            'unsupported_operations':['createCachedContent','batchGenerateContent','interactions',
                'bidiGenerateContent','GenerateVideo','bidiGenerateMusic'],
            'metadata_is_generation_verification':False}}


def native_model(model):
    result = {key:copy.deepcopy(value) for key,value in model.items() if not key.startswith('_')}
    result['pg_proxy'] = model_extension(model)
    return result


def openai_model(model):
    extension = model_extension(model)
    proxy = extension['proxy']
    parameters = ['temperature','top_p','top_k','max_tokens','max_completion_tokens','stop','seed','response_format']
    if proxy['tools']:
        parameters.append('tools')
    if proxy['function_calling']:
        parameters += ['tool_choice','parallel_tool_calls']
    if proxy['reasoning']['supported']:
        parameters.append('thinking_config')
        reasoning = proxy['reasoning']
        budget = reasoning.get('budget',{})
        supports_level = reasoning['level_supported'] and bool(reasoning.get('levels'))
        supports_zero_budget = (reasoning['budget_supported'] and budget.get('max',0)>=0
            and (budget.get('can_disable',False) or budget.get('min',0)<=0))
        if supports_level or supports_zero_budget:
            parameters.append('reasoning_effort')
    codes = model.get('_codes',[])
    if 21 in codes:
        parameters.append('modalities')
        if 47 in codes or 49 in codes:
            parameters.append('image_config')
    if 37 in codes:
        parameters += ['modalities','speech_config']
    if 43 in codes:
        parameters.append('media_resolution')
    if any(code in codes for code in (74,76,77,80,81,84)):
        parameters.append('transcription_config')
    interface = proxy['interfaces']['openai_chat_completions']
    result = {'id':model['name'].removeprefix('models/'),'object':'model','created':0,'owned_by':'google',
            'name':model.get('displayName',model['name']),'description':model.get('description',''),
            'type':proxy['type'],'architecture':{'input_modalities':interface['input_modalities'],
                'output_modalities':interface['output_modalities']},
            'supported_parameters':parameters,'pg_proxy':extension}
    if proxy['limits']['context_tokens'] is not None:
        result['context_length'] = proxy['limits']['context_tokens']
    return result


def find_model(catalog, name):
    model = catalog.get(name.removeprefix('models/'))
    if model is None:
        raise PlaygroundError('Model is not in the available Playground catalogs',404,'model_not_found')
    return model


def _paging_error(message):
    return PlaygroundError(message,400,'invalid_request_error',
        public_message='模型列表分页参数无效或目录已更新，请从第一页重新获取。')


def paginate_models(catalog, page_size=None, page_token=None):
    if page_size is None:
        size = 50
    elif not re.fullmatch(r'[1-9][0-9]{0,3}',str(page_size)) or int(page_size)>1000:
        raise _paging_error('Invalid pageSize')
    else:
        size = int(page_size)
    models = [native_model(catalog[name]) for name in sorted(catalog)]
    fingerprint = hashlib.sha256(json.dumps(models,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()[:24]
    offset = 0
    if page_token is not None:
        try:
            if not isinstance(page_token,str) or len(page_token)>256 or not re.fullmatch(r'[A-Za-z0-9_-]+',page_token):
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(page_token+'='*(-len(page_token)%4)))
            if (not isinstance(payload,list) or len(payload)!=3 or type(payload[0]) is not int or payload[0]!=1
                    or type(payload[1]) is not int or payload[1]<=0 or payload[1]>=len(models)
                    or payload[2]!=fingerprint):
                raise ValueError()
            offset = payload[1]
        except (ValueError,TypeError,UnicodeError):
            raise _paging_error('Invalid or expired pageToken') from None
    result = {'models':models[offset:offset+size]}
    if offset+size<len(models):
        payload = json.dumps([1,offset+size,fingerprint],separators=(',',':')).encode()
        result['nextPageToken'] = base64.urlsafe_b64encode(payload).decode().rstrip('=')
    return result


def merge_model_entries(previous, current):
    """Conservative common capabilities for one ID across eligible accounts."""
    if previous.get('name')!=current.get('name'):
        raise ValueError('Cannot merge different model IDs')
    result = copy.deepcopy(previous)
    for key in ('_codes','_secondary_codes','_upstream_methods','supportedGenerationMethods'):
        result[key] = [value for value in previous.get(key,[]) if value in current.get(key,[])]
    for key in ('inputTokenLimit','outputTokenLimit'):
        result[key] = min(previous.get(key,0),current.get(key,0))
    result['thinking'] = 25 in result['_codes']
    result['_options'] = {key:[value for value in values if value in current.get('_options',{}).get(key,[])]
        for key,values in previous.get('_options',{}).items()}
    result['_thinking'] = [None]*7
    for index in range(7):
        left,right = field(previous.get('_thinking',[]),index),field(current.get('_thinking',[]),index)
        if index==6:
            result['_thinking'][index] = [value for value in left if value in right] if isinstance(left,list) and isinstance(right,list) else []
        elif index in (1,2) and type(left) is int and type(right) is int:
            result['_thinking'][index] = min(left,right) if index==1 else max(left,right)
        elif index==3 and type(left) is bool and type(right) is bool:
            result['_thinking'][index] = left and right
        elif left==right:
            result['_thinking'][index] = copy.deepcopy(left)
    if type(previous.get('maxTemperature')) in (int,float) and type(current.get('maxTemperature')) in (int,float):
        result['maxTemperature'] = min(previous['maxTemperature'],current['maxTemperature'])
    elif previous.get('maxTemperature')!=current.get('maxTemperature'):
        result.pop('maxTemperature',None)
    budget = result['_thinking']
    if type(budget[1]) is int and type(budget[2]) is int and budget[2]>budget[1]:
        result['_codes'] = [code for code in result['_codes'] if code!=35]
    if not budget[6]:
        result['_codes'] = [code for code in result['_codes'] if code!=52]
    if type(budget[0]) is int and ((type(budget[1]) is int and budget[0]>budget[1])
                                 or (type(budget[2]) is int and budget[0]<budget[2])):
        budget[0] = None
    if budget[5] not in budget[6]:
        budget[5] = None
    for key in ('version','_alias_target','temperature','topP','topK'):
        if previous.get(key)!=current.get(key):
            result.pop(key,None)
    return result
