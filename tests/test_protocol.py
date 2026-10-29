import json
import pytest
from pgproxy.errors import PlaygroundError
from pgproxy.playground_protocol import loads, SparseJSON, parse_models, encode_generate, iter_frames, decode_frame, encode_logging_context
from pgproxy.openai_transformers import openai_request_to_gemini
from conftest import model_row,frame


def model():
    return parse_models(json.dumps([[model_row()]]))['test-chat']


def continuation_model():
    row = model_row('models/future-long-chat')
    row[5],row[6] = 120000,70000
    row[64].append(91)
    return parse_models(json.dumps([[row]]))['future-long-chat']


def test_sparse_strings_and_chunk_boundaries():
    raw = '[, [1,,"a,]\\\"b"],]'
    normalizer=SparseJSON()
    assert json.loads(''.join(normalizer.feed(c) for c in raw)) == [None,[1,None,'a,]"b'],None]
    assert loads(raw)==[None,[1,None,'a,]"b'],None]


def test_catalog_excludes_non_generate_routes():
    rows=[model_row()]
    for code in (20,26,53):
        row=model_row('models/special-'+str(code));row[64].extend([91,code]);rows.append(row)
    interaction=model_row('models/interaction');interaction[78]=[None,None,False,2];rows.append(interaction)
    assert list(parse_models(json.dumps([rows])))==['test-chat']
    video_capable=model_row('models/chat-with-video-tool');video_capable[64].append(69)
    assert 'chat-with-video-tool' in parse_models(json.dumps([[video_capable]]))


@pytest.mark.parametrize('paid_code',[0,1,2])
def test_catalog_continuation_is_dynamic_and_paid_is_not_a_route(paid_code):
    standard = model_row('models/ordinary-paid-chat')
    extended = model_row('models/new-provider-long-chat')
    standard[77] = extended[77] = paid_code
    extended[64].append(91)
    parsed = parse_models(json.dumps([[standard,extended]]))
    assert set(parsed) == {'ordinary-paid-chat','new-provider-long-chat'}
    assert 91 in parsed['new-provider-long-chat']['_codes']


def test_wire_fields_binding_and_system():
    payload={'contents':[{'role':'user','parts':[{'text':'你好\r\nA'},{'inlineData':{'mimeType':'image/png','data':'eA=='}}]},
                         {'role':'model','parts':[{'text':'previous'}]},{'role':'user','parts':[{'text':'final'}]}],
             'systemInstruction':{'parts':[{'text':'system'}]},'generationConfig':{'temperature':0,'topP':0,'maxOutputTokens':64,'thinkingConfig':{'thinkingLevel':'HIGH'}},
             'tools':[{'googleSearch':{}}]}
    wire,binding,page=encode_generate('test-chat',payload,model(),'Asia/Shanghai')
    assert binding=='你好\r\nA eA== previous final' and page=='final'
    assert wire[4] is None and wire[0]=='models/test-chat' and wire[5]==[[[None,'system']], 'user']
    assert wire[3][3:6]==[64,0,0] and wire[3][16]==[1,None,None,3]
    assert wire[6]==[[None,None,None,[None,[[]]]]]
    assert wire[10] == 1 and len(wire[3]) < 46 and len(wire) < 17


def test_continuation_wire_defaults_and_cumulative_round_limits():
    entry = continuation_model()
    payload = {'contents':[{'parts':[{'text':'synthetic continuation request'}]}]}
    first,binding,prompt = encode_generate('future-long-chat',payload,entry,'Asia/Shanghai')
    second,binding2,prompt2 = encode_generate('future-long-chat',payload,entry,'Asia/Shanghai',
        continuation_round=1,continuation_token='synthetic-token-1')
    third,_,_ = encode_generate('future-long-chat',payload,entry,
        continuation_round=2,continuation_token='synthetic-token-2')
    assert [first[3][3],second[3][3],third[3][3]] == [32768,65536,70000]
    assert first[10] is second[10] is third[10] is None
    assert first[3][45] == second[3][45] == third[3][45] == 1
    assert len(first) == 14 and second[16] == 'synthetic-token-1' and third[16] == 'synthetic-token-2'
    assert first[13] == second[13] == [[None,None,'Asia/Shanghai']]
    assert first[0:3] == second[0:3] and first[4] is second[4] is None
    assert binding == binding2 == prompt == prompt2 == 'synthetic continuation request'
    assert 'generationConfig' not in payload and entry['outputTokenLimit'] == 70000


@pytest.mark.parametrize('total,round_index,expected',[(1024,0,1024),(32768,0,32768),
    (32769,0,32768),(32769,1,32769),(120000,3,120000)])
def test_continuation_wire_honors_explicit_total_up_to_input_limit(total,round_index,expected):
    payload = {'contents':[{'parts':[{'text':'test'}]}],'generationConfig':{'maxOutputTokens':total}}
    wire,_,_ = encode_generate('future-long-chat',payload,continuation_model(),
        continuation_round=round_index,continuation_token='synthetic-token' if round_index else None)
    assert wire[3][3] == expected and wire[3][45] == 1
    with pytest.raises(PlaygroundError) as error:
        encode_generate('test-chat',payload,model())
    assert error.value.status == 400


def test_continuation_wire_rejects_total_above_catalog_limits():
    payload = {'contents':[{'parts':[{'text':'test'}]}],'generationConfig':{'maxOutputTokens':120001}}
    with pytest.raises(PlaygroundError) as error:
        encode_generate('future-long-chat',payload,continuation_model())
    assert error.value.status == 400


@pytest.mark.parametrize('round_index,token',[(True,None),(-1,None),(1.5,'token'),(1,None),
    (1,''),(1,17),(1,False),(1,[]),(0,'token')])
def test_continuation_wire_rejects_invalid_internal_state(round_index,token):
    payload = {'contents':[{'parts':[{'text':'test'}]}]}
    with pytest.raises(PlaygroundError) as error:
        encode_generate('future-long-chat',payload,continuation_model(),
            continuation_round=round_index,continuation_token=token)
    assert error.value.code == 'protocol_error'


def test_standard_model_cannot_accept_internal_continuation_state():
    payload = {'contents':[{'parts':[{'text':'test'}]}]}
    with pytest.raises(PlaygroundError) as error:
        encode_generate('test-chat',payload,model(),continuation_round=1,continuation_token='token')
    assert error.value.code == 'protocol_error'


@pytest.mark.parametrize('config',[{'temperature':float('nan')},{'maxOutputTokens':9999},{'topP':2},{'topK':True},{'candidateCount':2},{'stopSequences':'bad'},{'unknown':1},{'thinkingConfig':{'thinkingLevel':'nonsense'}}])
def test_invalid_parameters_fail_before_network(config):
    with pytest.raises(PlaygroundError) as e:
        encode_generate('test-chat',{'contents':[{'parts':[{'text':'hello'}]}],'generationConfig':config},model())
    assert e.value.status==400


def test_openai_system_is_system_instruction_and_inline_image():
    name,payload=openai_request_to_gemini({'model':'test-chat','messages':[{'role':'system','content':'S'},{'role':'user','content':[{'type':'text','text':'Hi'},{'type':'image_url','image_url':{'url':'data:image/png;base64,eA=='}}]}]})
    assert payload['systemInstruction']=={'parts':[{'text':'S'}]}
    assert len(payload['contents'])==1 and name=='test-chat'
    assert payload['contents'][0]['parts'][1]['inlineData']['data']=='eA=='


@pytest.mark.asyncio
async def test_incremental_sparse_frames_unicode():
    text=json.dumps([[frame('中文',None),frame('end',1)]],ensure_ascii=False)
    async def chunks():
        for c in text:
            yield c
    decoded=[decode_frame(f) async for f in iter_frames(chunks())]
    assert decoded[0]['candidates'][0]['content']['parts']==[{'text':'中文'}]
    assert decoded[1]['candidates'][0]['finishReason']=='STOP'
    assert decoded[1]['usageMetadata']['totalTokenCount']==6


@pytest.mark.parametrize('output,total,expected',[(None,15,4),(None,8,0),(0,15,0),(6,15,6)])
def test_usage_derives_only_missing_output_and_includes_tool_tokens(output,total,expected):
    raw = frame('test',1)
    raw[2] = [5,output,total,1,None,None,None,4,None,2]
    assert decode_frame(raw)['usageMetadata'] == {'promptTokenCount':5,
        'candidatesTokenCount':expected,'totalTokenCount':total,'cachedContentTokenCount':1,
        'toolUsePromptTokenCount':4,'thoughtsTokenCount':2}


def test_usage_without_tool_tokens_preserves_existing_response_shape():
    raw = frame('test',1)
    raw[2] = [5,None,12,1,None,None,None,None,None,2]
    metadata = decode_frame(raw)['usageMetadata']
    assert metadata['candidatesTokenCount'] == 5
    assert 'toolUsePromptTokenCount' not in metadata


@pytest.mark.parametrize('tool_tokens',[-1,True,'4',{}])
def test_usage_rejects_invalid_tool_token_counts(tool_tokens):
    raw = frame('test',1)
    raw[2] = [5,None,12,1,None,None,None,tool_tokens,None,2]
    with pytest.raises(PlaygroundError) as error:
        decode_frame(raw)
    assert error.value.code == 'protocol_error'


@pytest.mark.parametrize('finish',[None,1,2,22])
def test_continuation_decode_keeps_private_token_and_raw_finish(finish):
    raw = frame('synthetic chunk',finish)
    raw[0][0] += [None]*(9-len(raw[0][0]))
    raw[0][0][8] = 'synthetic-next-token'
    decoded = decode_frame(raw,continuable=True)
    assert decoded['_continuationToken'] == 'synthetic-next-token'
    assert decoded['candidates'][0]['content']['parts'] == [{'text':'synthetic chunk'}]
    if finish is None:
        assert '_finishCode' not in decoded and 'finishReason' not in decoded['candidates'][0]
    else:
        assert decoded['_finishCode'] == finish
        assert decoded['candidates'][0]['finishReason'] == ('STOP' if finish == 1 else 'MAX_TOKENS')
    if finish == 22:
        with pytest.raises(PlaygroundError) as error:
            decode_frame(raw)
        assert error.value.code == 'protocol_error'
    else:
        assert not any(k.startswith('_') for k in decode_frame(raw))


@pytest.mark.parametrize('token',[None,''])
def test_continuation_decode_does_not_invent_missing_tokens(token):
    raw = frame('',22)
    raw[0][0] += [None]*(9-len(raw[0][0]))
    raw[0][0][8] = token
    decoded = decode_frame(raw,continuable=True)
    assert '_continuationToken' not in decoded and decoded['_finishCode'] == 22


@pytest.mark.parametrize('token',[17,False,[],{}])
def test_continuation_decode_rejects_wrong_token_type(token):
    raw = frame('',22)
    raw[0][0] += [None]*(9-len(raw[0][0]))
    raw[0][0][8] = token
    with pytest.raises(PlaygroundError) as error:
        decode_frame(raw,continuable=True)
    assert error.value.code == 'protocol_error'


@pytest.mark.parametrize('finish',[True,'22',22.0,[]])
def test_continuation_decode_rejects_wrong_finish_type(finish):
    with pytest.raises(PlaygroundError) as error:
        decode_frame(frame('',finish),continuable=True)
    assert error.value.code == 'protocol_error'


@pytest.mark.parametrize('raw',['[[','[[[]]','{"error":{}}','[[[[],null,null]]]trailing','[[null]]','[[[]][]]'])
@pytest.mark.asyncio
async def test_truncated_and_malformed_streams(raw):
    async def chunks(): yield raw
    with pytest.raises(PlaygroundError):
        [f async for f in iter_frames(chunks())]


def test_logging_context_binary():
    assert encode_logging_context(['A',42,True])=='CgFBECoYAQ=='


def test_live_page_javascript_hex_toggles_are_not_json():
    from pgproxy.playground_client import parse_page_toggles
    assert parse_page_toggles('[0x4002881, 0x1cc, 0xc0, ]')==[0x4002881,0x1cc,0xc0]
    assert parse_page_toggles('[1, -2,]')==[1,-2]
    with pytest.raises(PlaygroundError):parse_page_toggles('[dangerous()]')
