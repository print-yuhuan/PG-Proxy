"""Deterministic protocol fixtures; these do not prove live account availability."""
import copy
import pytest

from pgproxy.errors import PlaygroundError
from pgproxy.playground_protocol import (
    decode_frame, decode_struct, decode_value, encode_generate, encode_part,
    encode_schema, encode_struct, set_field,
)


def model(*codes, **extra):
    return {'name':'models/test', 'outputTokenLimit':70000, 'inputTokenLimit':120000,
            '_codes':[1, *codes], **extra}


def request(config=None, parts=None, **extra):
    return {'contents':[{'parts':parts or [{'text':'Hello'}]}],
            'generationConfig':config or {}, **extra}


def encode(config=None, codes=(), **extra):
    return encode_generate('test',request(config,**extra),model(*codes))[0]


def frame(parts, **extra):
    candidate = [[[p for p in parts],'model'],1]
    for key,value in extra.items():
        set_field(candidate,int(key),value)
    return [[candidate]]


def test_image_config_uses_verified_slots_and_dynamic_choices():
    entry = model(21,47,49,_options={'image_aspect_ratios':['1:1','16:9'],
                                    'image_output_resolutions':['1K','2K']})
    config = {'responseModalities':['TEXT','IMAGE'], 'imageConfig':{'aspectRatio':'16:9','imageSize':'2K'}}
    wire,binding,prompt = encode_generate('test',request(config),entry)
    assert wire[3][14] == [2,1]
    assert wire[3][26] == ['16:9','2K']
    assert wire[6] == [[None,None,None,[None,[]]]]
    assert binding == prompt == 'Hello'
    for bad in ({'aspectRatio':'4:3'},{'imageSize':'4K'}):
        with pytest.raises(PlaygroundError):
            encode_generate('test',request({'imageConfig':bad}),entry)
    default = encode_generate('test',request(),entry)[0]
    assert default[3][14] == [2,1] and default[3][26] == [None,'1K']


@pytest.mark.parametrize('config,codes',[
    ({'responseModalities':['IMAGE']},()),
    ({'responseModalities':['AUDIO','TEXT']},(37,)),
    ({'responseModalities':[]},(21,)),
    ({'imageConfig':{'aspectRatio':'1:1'}},(21,)),
    ({'imageConfig':{'imageSize':'10K'}},(21,49)),
    ({'imageConfig':{'aspectRatio':'0:1'}},(21,47)),
    ({'imageConfig':{'personGeneration':'ALLOW_ALL'}},(21,)),
    ({'mediaResolution':'MEDIA_RESOLUTION_HIGH'},()),
])
def test_image_and_modality_reject_invalid_or_unsupported(config,codes):
    with pytest.raises(PlaygroundError) as error:
        encode(config,codes)
    assert error.value.status == 400


def test_speech_single_multi_and_explicit_output_limit():
    voice = {'prebuiltVoiceConfig':{'voiceName':'Zephyr'}}
    wire = encode({'speechConfig':{'voiceConfig':voice}},(37,))
    assert wire[3][14] == [3] and wire[3][15] == [[['Zephyr']]]
    assert wire[3][3] is None
    multi = {'multiSpeakerVoiceConfig':{'speakerVoiceConfigs':[
        {'speaker':'Alice','voiceConfig':voice},
        {'speaker':'Bob','voiceConfig':{'prebuiltVoiceConfig':{'voiceName':'Puck'}}}], 'mode':'VERBATIM'}}
    wire = encode({'speechConfig':multi,'maxOutputTokens':64},(37,))
    assert wire[3][15] == [None,None,[None,[['Alice',[['Zephyr']]],['Bob',[['Puck']]]],1]]
    assert wire[3][3] == 64
    with pytest.raises(PlaygroundError):
        encode_generate('test',request({'speechConfig':{'voiceConfig':voice}}),model(37,_options={'voices':['Puck']}))


def test_transcription_and_media_configuration():
    config = {'transcriptionConfig':{'wordTimestamps':True,'speakerLabels':True,
              'languageCodes':['zh']},
              'mediaResolution':'MEDIA_RESOLUTION_HIGH'}
    wire = encode(config,(74,80,81,76,43))
    assert wire[3][17] == 3
    assert wire[3][31] == [None,None,None,None,1,1,None,['zh']]
    assert encode({'transcriptionConfig':{'smartTranscription':True}},(84,))[3][31][8] == 2
    with pytest.raises(PlaygroundError):
        encode({'transcriptionConfig':{'wordTimestamps':True}},())
    with pytest.raises(PlaygroundError):
        encode({'transcriptionConfig':{'smartTranscription':True,'wordTimestamps':True}},(74,84))


@pytest.mark.parametrize('transcription,setting',[
    ({'wordTimestamps':True,'customVocabulary':['PG Proxy']},'customVocabulary'),
    ({'wordTimestamps':True,'speakerLabels':True,'customVocabulary':['PG Proxy'],
      'languageCodes':['en']},'customVocabulary'),
    ({'speakerLabels':True,'customVocabulary':['PG Proxy']},'speakerLabels'),
    ({'wordTimestamps':False,'speakerLabels':True,'customVocabulary':['PG Proxy'],'languageCodes':['en']},'speakerLabels'),
    ({'smartTranscription':True,'wordTimestamps':True},'smartTranscription'),
    ({'smartTranscription':True,'speakerLabels':True},'smartTranscription'),
    ({'smartTranscription':True,'wordTimestamps':True,'speakerLabels':True},'smartTranscription'),
])
def test_transcription_conflicting_combinations_fail_before_sending(transcription,setting):
    original = copy.deepcopy(transcription)
    with pytest.raises(PlaygroundError) as error:
        encode({'transcriptionConfig':transcription},(74,76,80,81,84))
    assert error.value.status == 400 and error.value.code == 'invalid_request_error'
    assert setting in error.value.public_message and '不能' in error.value.public_message and '同时' in error.value.public_message
    assert transcription == original


@pytest.mark.parametrize('transcription,expected',[
    ({'wordTimestamps':True,'customVocabulary':[]},[None,None,None,None,1]),
    ({'wordTimestamps':False,'speakerLabels':False,'customVocabulary':['PG Proxy'],'languageCodes':['en']},
      [None,None,None,None,None,None,['PG Proxy'],['en']]),
    ({'speakerLabels':True,'customVocabulary':[],'languageCodes':['en']},
      [None,None,None,None,None,1,None,['en']]),
    ({'smartTranscription':True,'wordTimestamps':False,'speakerLabels':False,
      'customVocabulary':['PG Proxy'],'languageCodes':['en']},
      [None,None,None,None,None,None,['PG Proxy'],['en'],2]),
    ({'smartTranscription':False,'wordTimestamps':True,'speakerLabels':True},
      [None,None,None,None,1,1]),
])
def test_transcription_compatible_combinations_are_not_silently_disabled(transcription,expected):
    original = copy.deepcopy(transcription)
    wire = encode({'transcriptionConfig':transcription},(74,76,80,81,84))
    assert wire[3][31] == expected
    assert transcription == original


def test_transcription_language_codes_follow_nonempty_catalog_without_rewriting():
    entry = model(76,_options={'languages':['en','zh']})
    config = {'transcriptionConfig':{'languageCodes':['en','zh']}}
    original = copy.deepcopy(config)
    wire,_,_ = encode_generate('test',request(config),entry)
    assert wire[3][31][7] == ['en','zh']
    assert config == original
    empty,_,_ = encode_generate('test',request({'transcriptionConfig':{'languageCodes':[]}}),entry)
    assert len(empty[3]) < 32


@pytest.mark.parametrize('languages',[['en-US'],['EN'],['en','zh-CN']])
def test_transcription_unsupported_language_is_clear_400_and_never_normalized(languages):
    config = {'transcriptionConfig':{'languageCodes':languages}}
    original = copy.deepcopy(config)
    with pytest.raises(PlaygroundError) as error:
        encode_generate('test',request(config),model(76,_options={'languages':['en','zh']}))
    assert error.value.status == 400 and error.value.code == 'invalid_request_error'
    assert 'transcriptionConfig.languageCodes' in error.value.public_message
    assert 'pg_proxy.upstream.options.languages' in error.value.public_message
    assert config == original


@pytest.mark.parametrize('options',[{}, {'languages':[]}])
def test_transcription_missing_catalog_choices_does_not_invent_language_restrictions(options):
    wire,_,_ = encode_generate('test',request({'transcriptionConfig':{'languageCodes':['en-US']}}),
                               model(76,_options=options))
    assert wire[3][31][7] == ['en-US']


def test_thinking_media_and_continuation_coexist():
    entry = model(91,21,47,49,25,35,52,43,_thinking=[None,32768,1024,True,None,None,[1,3,4]])
    config = {'maxOutputTokens':100000,'thinkingConfig':{'includeThoughts':False,'thinkingLevel':'HIGH','thinkingBudget':2048},
              'imageConfig':{'aspectRatio':'1:1','imageSize':'2K'},'mediaResolution':'MEDIA_RESOLUTION_LOW'}
    wire,_,_ = encode_generate('test',request(config),entry,continuation_round=1,continuation_token='test-continuation')
    assert wire[3][16] == [0,2048,None,3]
    assert wire[3][17] == 1 and wire[3][26] == ['1:1','2K']
    assert wire[3][45] == 1 and wire[3][3] == 65536 and wire[16] == 'test-continuation'


def test_schema_nested_fields_refs_and_alias():
    schema = {'type':'object','$defs':{'Count':{'type':'integer','minimum':0,'maximum':10}},
              'properties':{'count':{'$ref':'#/$defs/Count'},'tags':{'type':'array',
              'items':{'type':['string','null'],'enum':['a','b']},'minItems':1,'maxItems':3}},
              'required':['count'],'propertyOrdering':['count','tags']}
    original = copy.deepcopy(schema)
    g = encode({'responseMimeType':'application/json','responseJsonSchema':schema})[3]
    assert g[7] == 'application/json' and g[8][0] == 6 and g[8][7] == ['count']
    assert g[8][6][0] == ['count',[3,None,None,None,None,None,None,None,None,None,0,10]]
    tags = g[8][6][1][1]
    assert tags[5] == [1,None,None,True,['a','b']]
    assert tags[20:23] == [3,1] and g[8][22] == ['count','tags']
    assert schema == original
    wire = encode_schema({'type':'string','minLength':1,'maxLength':8,'pattern':'^[a-z]+$',
                          'example':'abc','oneOf':[{'type':'string','enum':['abc']}]})
    assert wire[12:16] == [1,8,'^[a-z]+$',[None,None,'abc']]
    assert wire[16] == [[1,None,None,None,['abc']]]


@pytest.mark.parametrize('schema',[
    {'type':'object','additionalProperties':False},
    {'type':'array'},
    {'type':'number','minimum':2,'maximum':1},
    {'type':'integer','enum':[1,2]},
    {'type':'object','properties':{},'required':['missing']},
    {'$ref':'https://example.test/schema'},
    {'$defs':{'A':{'$ref':'#/$defs/A'}},'$ref':'#/$defs/A'},
    {'type':['string','integer']},
    {'type':'string','minLength':True},
])
def test_schema_never_silently_discards_unimplemented_constraints(schema):
    with pytest.raises(PlaygroundError) as error:
        encode_schema(schema)
    assert error.value.status == 400


def test_function_wire_round_trip_ids_signatures_and_binding():
    args = {'city':'Taipei','days':2,'metric':True,'note':None,'units':['C','F'],'nested':{'x':1}}
    call = {'functionCall':{'name':'weather','args':args,'id':'call-1'},'thoughtSignature':'real-upstream-signature'}
    reply = {'functionResponse':{'name':'weather','response':{'temperature':25},'id':'call-1'}}
    encoded,binding = encode_part(call)
    assert binding == '' and encoded[10][0] == 'weather' and encoded[10][2] == 'call-1'
    assert encoded[14] == 'real-upstream-signature'
    assert decode_struct(encoded[10][1]) == args
    assert decode_frame(frame([encoded]))['candidates'][0]['content']['parts'][0] == call
    contents = [{'role':'user','parts':[{'text':'Weather'}]}, {'role':'model','parts':[call]},
                {'role':'user','parts':[reply,{'text':'Summarize'}]}]
    wire,binding,prompt = encode_generate('test',{'contents':contents},model(10))
    assert binding == 'Weather   Summarize' and prompt == 'Summarize'
    assert wire[1][2][0][0][11] == ['weather',[[['temperature',[None,25]]]],'call-1']
    no_signature = encode_part({'functionCall':{'name':'weather','args':{}}})[0]
    assert len(no_signature) == 11  # Never invent a thought signature.
    signature_only = encode_part({'thoughtSignature':'from-upstream'})
    assert signature_only[0][14] == 'from-upstream' and signature_only[1] == ''


def test_tools_schema_subsets_timezone_and_server_side_invocations():
    functions = [{'name':'weather','parameters':{'type':'object','properties':{'city':{'type':'string'}},'required':['city']}},
                 {'name':'unused'}]
    body = request(tools=[{'functionDeclarations':functions},{'urlContext':{}}],
                   toolConfig={'functionCallingConfig':{'mode':'AUTO','allowedFunctionNames':['weather']}})
    wire,_,_ = encode_generate('test',body,model(10,13),'Asia/Shanghai')
    assert len(wire[6][0][1]) == 1 and wire[6][0][1][0][0] == 'weather'
    assert wire[6][1] == [None]*7+[[]]
    assert wire[13] == [[None,None,'Asia/Shanghai'],None,True]
    body['toolConfig']['functionCallingConfig']['mode'] = 'NONE'
    assert encode_generate('test',body,model(10,13))[0][6] == []
    for mode in ('ANY','VALIDATED'):
        body['toolConfig']['functionCallingConfig']['mode'] = mode
        with pytest.raises(PlaygroundError):
            encode_generate('test',body,model(10,13))


def test_search_types_timerange_and_maps_exact_fields():
    tools = [{'googleSearch':{'searchTypes':{'webSearch':{},'imageSearch':{}},
              'timeRangeFilter':{'startTime':'2026-01-01T00:00:00Z','endTime':'2026-01-02T00:00:00+00:00'}}},
             {'googleMaps':{}}]
    wire = encode(codes=(12,54,58),tools=tools)
    assert wire[6][0] == [None]*10+[[]]
    assert wire[6][1] == [None,None,None,[[['1767225600'],['1767312000']],[[],[]]]]
    with pytest.raises(PlaygroundError):
        encode(codes=(58,13),tools=[{'googleMaps':{}},{'urlContext':{}}])
    with pytest.raises(PlaygroundError):
        encode(codes=(12,),tools=[{'googleSearch':{'searchTypes':{'imageSearch':{}}}}])


def test_external_media_video_code_and_speech_metadata_binding():
    media = {'fileData':{'fileUri':'https://www.youtube.com/watch?v=sample','mimeType':'video/mp4'},
             'videoMetadata':{'startOffset':'2.5s','endOffset':'4s','fps':1},'mediaProcessing':'STATIC'}
    encoded,binding = encode_part(media)
    assert encoded[6] == ['video/mp4','https://www.youtube.com/watch?v=sample']
    assert encoded[15] == [[2,500000000],[4],1] and encoded[39] == 1 and binding == ''
    code = {'executableCode':{'language':'PYTHON','code':'print(1)'}}
    result = {'codeExecutionResult':{'outcome':'OUTCOME_OK','output':'1'}}
    for part in (code,result):
        encoded,binding = encode_part(part)
        assert binding == '' and decode_frame(frame([encoded]))['candidates'][0]['content']['parts'][0] == part
    wire = encode(codes=(69,),parts=[media,{'fileData':media['fileData'],'mediaProcessing':'AGENTIC'}])
    assert wire[1][0][0][0][39] == 1
    assert wire[1][0][0][1][39] == 2 and wire[1][0][0][1][15] is None
    speech = encode_part({'text':'Hello','speechMetadata':{'speaker':'A','style':'calm'}})
    assert speech[0][40] == ['A','calm'] and speech[1] == 'Hello'
    with pytest.raises(PlaygroundError):
        encode_part({'fileData':{'fileUri':'file:///private','mimeType':'video/mp4'}})
    with pytest.raises(PlaygroundError):
        encode_part({**media,'videoMetadata':{'startOffset':'4s','endOffset':'2.5s'}})


@pytest.mark.parametrize('metadata',[{}, {'startOffset':'0s'}, {'endOffset':'2s'}, {'fps':1}])
def test_agentic_video_rejects_static_metadata_with_safe_error(metadata):
    part = {'inlineData':{'mimeType':'video/mp4','data':'AA=='},
            'mediaProcessing':'AGENTIC','videoMetadata':metadata}
    with pytest.raises(PlaygroundError) as error:
        encode(codes=(69,),parts=[part])
    assert error.value.status == 400 and error.value.code == 'invalid_request_error'
    assert 'videoMetadata' in error.value.public_message and 'AGENTIC' in error.value.public_message


@pytest.mark.parametrize('fps',[0,-1,24.00001,True,'1',None,float('nan'),float('inf'),float('-inf'),10**400])
def test_video_fps_rejects_invalid_range_and_type(fps):
    with pytest.raises(PlaygroundError) as error:
        encode_part({'inlineData':{'mimeType':'video/mp4','data':'AA=='},'videoMetadata':{'fps':fps}})
    assert error.value.status == 400 and error.value.code == 'invalid_request_error'
    assert 'videoMetadata.fps' in error.value.public_message and '24' in error.value.public_message


@pytest.mark.parametrize('fps',[1e-10,0.5,1,24,24.0])
def test_video_fps_accepts_full_documented_range(fps):
    encoded,binding = encode_part({'inlineData':{'mimeType':'video/mp4','data':'AA=='},
                                  'mediaProcessing':'STATIC','videoMetadata':{'fps':fps}})
    assert encoded[15] == [None,None,fps] and encoded[39] == 1 and binding == 'AA=='


def test_transcript_and_media_processing_decoding():
    part = set_field([],22,['hi','speaker',[[None,[1,500000000],[2]]]])
    result = decode_frame(frame([part]))['candidates'][0]['content']['parts'][0]
    assert result['text'] == 'hi'
    assert result['transcriptionMetadata'] == {'speaker':'speaker','timestamps':[
        {'start':{'seconds':1,'nanos':500000000},'end':{'seconds':2}}]}
    assert encode_part(result)[0][22] == ['hi','speaker',[[None,[1,500000000],[2,0]]]]
    processing = {'toolCall':{'toolType':'MEDIA_PROCESSING','toolName':'inspect','args':{'time':2},'id':'video-1'}}
    encoded,binding = encode_part(processing)
    assert encoded[25][0] == 13 and binding == ''
    assert decode_frame(frame([encoded]))['candidates'][0]['content']['parts'][0] == processing


def test_transcript_duration_decimal_seconds_and_missing_zero_preserve_word_text():
    # Minimal synthetic shape observed in JSPB; no live signatures or visit IDs.
    part = set_field([],22,['hello world','speaker-1',[
        ['hello',[None,100000000],['1',200000000]],
        ['world',[1,200000000],['2']]]])
    result = decode_frame(frame([part]))['candidates'][0]['content']['parts'][0]
    assert result == {'text':'hello world','transcriptionMetadata':{
        'speaker':'speaker-1','timestamps':[
            {'text':'hello','start':{'seconds':0,'nanos':100000000},'end':{'seconds':1,'nanos':200000000}},
            {'text':'world','start':{'seconds':1,'nanos':200000000},'end':{'seconds':2}}]}}
    replay,binding = encode_part(result)
    assert replay[22] == ['hello world','speaker-1',[
        ['hello',[0,100000000],[1,200000000]],['world',[1,200000000],[2,0]]]]
    assert binding == 'hello world'


def test_transcript_timestamp_word_requires_text_in_response_and_history():
    with pytest.raises(PlaygroundError) as upstream:
        decode_frame(frame([set_field([],22,['word',None,[[123,[0],[1]]]])]))
    assert upstream.value.code == 'protocol_error'
    with pytest.raises(PlaygroundError) as history:
        encode_part({'text':'word','transcriptionMetadata':{'timestamps':[{'text':123,'start':{'seconds':0}}]}})
    assert history.value.status == 400


@pytest.mark.parametrize('seconds',[0,1,'0','1',315576000000,'315576000000'])
def test_transcript_duration_accepts_valid_integer_or_decimal_seconds(seconds):
    part = set_field([],22,['word',None,[['word',[seconds],[seconds,999999999]]]])
    stamp = decode_frame(frame([part]))['candidates'][0]['content']['parts'][0]['transcriptionMetadata']['timestamps'][0]
    assert stamp['start']['seconds'] == stamp['end']['seconds'] == int(seconds)


@pytest.mark.parametrize('seconds',[True,False,1.0,0.5,-1,'-1','+1','1.0','1e2',' 1','',
                                  'NaN',315576000001,'315576000001',9223372036854775807,{},[]])
def test_transcript_duration_rejects_invalid_seconds_type_or_range(seconds):
    part = set_field([],22,['word',None,[['word',[seconds],[2]]]])
    with pytest.raises(PlaygroundError) as error:
        decode_frame(frame([part]))
    assert error.value.code == 'protocol_error'


@pytest.mark.parametrize('nanos',[True,1.0,'100',-1,1000000000])
def test_transcript_duration_rejects_invalid_nanoseconds(nanos):
    part = set_field([],22,['word',None,[['word',[0,nanos],[2]]]])
    with pytest.raises(PlaygroundError) as error:
        decode_frame(frame([part]))
    assert error.value.code == 'protocol_error'


def test_grounding_citations_queries_and_maps():
    grounding = [['<div>Search</div>','blob'],[[['https://example.test','Example']],
                 [None,None,['https://maps.test','Place','Address','place-1']]],
                 [[[0,0,5,'hello'],[0,1],[0.8,0.9]]],[None,0.6],['query'],['query','query2'],'maps-widget']
    result = decode_frame(frame([[None,'hello']],**{'7':grounding,'6':[[[None,None,'https://example.test','Example']]]}))
    candidate = result['candidates'][0]
    metadata = candidate['groundingMetadata']
    assert metadata['webSearchQueries'] == ['query','query2']
    assert metadata['groundingChunks'][1]['maps']['placeId'] == 'place-1'
    assert metadata['groundingSupports'][0]['segment']['text'] == 'hello'
    assert metadata['searchEntryPoint'] == {'renderedContent':'<div>Search</div>','sdkBlob':'blob'}
    assert candidate['citationMetadata']['citationSources'][0]['uri'] == 'https://example.test'


@pytest.mark.parametrize('raw',[[None,True],[None,None,3],[1],[None,2,'double'],[],[None,None,None,None,None,[['bad']]]])
def test_malformed_function_values_are_protocol_errors(raw):
    with pytest.raises(PlaygroundError) as error:
        decode_value(raw)
    assert error.value.code == 'protocol_error'


def test_nested_struct_json_roundtrip():
    value = {'a':None,'b':False,'c':0,'d':[{},[],None,True,1.5],'e':{'x':'测试'}}
    assert decode_struct(encode_struct(value)) == value
    with pytest.raises(PlaygroundError):
        encode_struct({'nonfinite':float('nan')})


@pytest.mark.parametrize('value,expected',[(0,False),(1,True),(False,False),(True,True)])
def test_struct_boolean_accepts_jspb_numeric_boolean_and_normalizes(value,expected):
    wire = [None,None,None,value]
    assert decode_value(wire) is expected
    # A real function args Struct reaches the same decoder, preserving bool
    # rather than accidentally returning a Python integer for caller execution.
    part = set_field([],10,['check',[[['enabled',wire]]]])
    call = decode_frame(frame([part]))['candidates'][0]['content']['parts'][0]['functionCall']
    assert call['args']['enabled'] is expected


@pytest.mark.parametrize('value',[0.0,1.0,0.5,2,-1,'0','1','true','false',None,{},[]])
def test_struct_boolean_rejects_non_boolean_values(value):
    with pytest.raises(PlaygroundError) as error:
        decode_value([None,None,None,value])
    assert error.value.code == 'protocol_error'


@pytest.mark.parametrize('thought,expected',[(True,True),(1,True),(False,False),(0,False),(None,False)])
def test_response_thought_accepts_jspb_boolean_and_numeric_boolean(thought,expected):
    part = set_field([None,'reasoning or answer'],12,thought)
    set_field(part,14,'upstream-fixture-signature')
    response = decode_frame(frame([part]))
    decoded = response['candidates'][0]['content']['parts'][0]
    assert decoded.get('thought',False) is expected
    assert ('thought' in decoded) is expected
    assert decoded['text'] == 'reasoning or answer'
    assert decoded['thoughtSignature'] == 'upstream-fixture-signature'


@pytest.mark.parametrize('thought',[0.0,1.0,0.5,2,-1,'0','1','true','false',{},[]])
def test_response_thought_rejects_values_other_than_exact_jspb_booleans(thought):
    part = set_field([None,'text'],12,thought)
    with pytest.raises(PlaygroundError) as error:
        decode_frame(frame([part]))
    assert error.value.code == 'protocol_error'
