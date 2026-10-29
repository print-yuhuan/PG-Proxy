"""Synthetic continuation protocol tests; MockTransport never contacts Google."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import frame, model_row
from pgproxy.errors import PlaygroundError
from pgproxy.main import create_app


REQUEST = {'contents':[{'parts':[{'text':'hello'}]}]}
MODEL = 'synthetic-continuable'


def continued_frame(text='piece', finish=None, token=None, usage=None):
    result = frame(text,finish)
    if token is not None:
        result[0][0] += [None]*(9-len(result[0][0]))
        result[0][0][8] = token
    if usage is not None:
        result[2] = usage
    return result


def usage(prompt, output, thought=0, cached=0):
    return [prompt,output,prompt+output+thought,cached,None,None,None,None,None,thought]


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, frames):
        self.content = json.dumps([frames]).encode()
        self.closed = False
    async def __aiter__(self):
        yield self.content
    async def aclose(self):
        self.closed = True


def create_continuable(client_factory, responses, output_limit=65536):
    streams, sent = [], []
    row = model_row('models/'+MODEL)
    row[5],row[6] = 131072,output_limit
    row[64].append(91)
    def handler(request):
        if request.url.path.endswith('/ListModels'):
            return httpx.Response(200,json=[[row]])
        if request.url.path.endswith('/GenerateContent'):
            assert len(sent)<len(responses), 'Unexpected additional generation'
            spec = responses[len(sent)]
            sent.append(json.loads(request.content))
            if isinstance(spec,int):
                return httpx.Response(spec,headers={'retry-after':'5'})
            stream = spec if isinstance(spec,httpx.AsyncByteStream) else TrackedStream(spec)
            streams.append(stream)
            return httpx.Response(200,stream=stream,headers={'content-type':'application/json+protobuf'})
        return None
    client,_ = client_factory(extra_handler=handler)
    proofs = []
    async def proof(op,**parameters):
        assert op=='proof'
        proofs.append(parameters)
        return {'proof':'!SYNTHETIC_CONTINUATION_'+str(len(proofs))}
    client.waa.call = proof
    return client,sent,streams,proofs


async def consume(client, request=None):
    return [result async for result in client.generate(MODEL,request or REQUEST)]


def assert_no_private_metadata(results):
    encoded = json.dumps(results)
    assert '_continuation' not in encoded and '_finishCode' not in encoded
    assert 'private-token' not in encoded


@pytest.mark.asyncio
async def test_continuable_short_generation_uses_single_fresh_proof(client_factory):
    client,sent,streams,proofs = create_continuable(client_factory,[[continued_frame('OK',1)]])
    try:
        results = await consume(client)
        assert len(sent)==len(proofs)==1 and streams[0].closed
        assert sent[0][3][3]==32768 and sent[0][3][45]==1 and sent[0][10] is None
        assert results[-1]['candidates'][0]['finishReason']=='STOP'
        assert client.generation_verified and not client.lock.locked()
        assert_no_private_metadata(results)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', [22,2])
async def test_continuation_two_rounds_merges_usage_and_only_finishes_once(client_factory,finish):
    first = [continued_frame('one',None,usage=usage(5,2,1)),
             continued_frame('two',finish,'private-token-1',usage(5,3,2,1))]
    second = [continued_frame('three',None,usage=usage(7,2,1)),
              continued_frame('four',1,usage=usage(7,4,3,2))]
    client,sent,streams,proofs = create_continuable(client_factory,[first,second])
    try:
        results = await consume(client)
        assert len(sent)==len(proofs)==2 and all(stream.closed for stream in streams)
        assert [wire[3][3] for wire in sent]==[32768,65536]
        assert len(sent[0])<17 and sent[1][16]=='private-token-1'
        assert sent[0][4]!=sent[1][4] and proofs[0]==proofs[1]
        assert [part['text'] for result in results for c in result['candidates'] for part in c['content']['parts']]==['one','two','three','four']
        assert [c['finishReason'] for result in results for c in result['candidates'] if 'finishReason' in c]==['STOP']
        assert [r for r in results if 'usageMetadata' in r]==[results[-1]]
        assert results[-1]['usageMetadata']=={'promptTokenCount':7,'candidatesTokenCount':7,
            'totalTokenCount':19,'cachedContentTokenCount':2,'thoughtsTokenCount':5}
        assert client.generation_verified and not client.lock.locked()
        assert_no_private_metadata(results)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('round_count', [2,3])
async def test_continuation_accumulates_tool_and_missing_output_usage_without_recounting_input(client_factory,round_count):
    round_usages = [
        [5,None,15,1,None,None,None,4,None,2],
        [7,3,17,2,None,None,None,6,None,1],
        [9,None,15,3,None,None,None,1,None,2],
    ]
    responses = [[continued_frame(str(i),1 if i==round_count-1 else 22,
        None if i==round_count-1 else 'private-token-'+str(i),round_usages[i])]
        for i in range(round_count)]
    client,sent,streams,_ = create_continuable(client_factory,responses)
    expected = {
        2:{'promptTokenCount':7,'candidatesTokenCount':7,'totalTokenCount':27,
           'cachedContentTokenCount':2,'thoughtsTokenCount':3,'toolUsePromptTokenCount':10},
        3:{'promptTokenCount':9,'candidatesTokenCount':10,'totalTokenCount':35,
           'cachedContentTokenCount':3,'thoughtsTokenCount':5,'toolUsePromptTokenCount':11},
    }
    try:
        results = await consume(client,{**REQUEST,'generationConfig':{'maxOutputTokens':100000}})
        assert len(sent)==round_count and all(stream.closed for stream in streams)
        assert [r['usageMetadata'] for r in results if 'usageMetadata' in r]==[expected[round_count]]
        assert_no_private_metadata(results)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_continuation_retains_prior_tool_usage_when_last_round_omits_it(client_factory):
    first_usage = [5,None,15,1,None,None,None,4,None,2]
    client,sent,streams,_ = create_continuable(client_factory,[
        [continued_frame('one',22,'private-token-1',first_usage)],
        [continued_frame('two',1,usage=usage(7,3,1,2))]])
    try:
        results = await consume(client)
        assert len(sent)==2 and all(stream.closed for stream in streams)
        assert results[-1]['usageMetadata']=={'promptTokenCount':7,'candidatesTokenCount':7,
            'totalTokenCount':21,'cachedContentTokenCount':2,'thoughtsTokenCount':3,
            'toolUsePromptTokenCount':4}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_finish_22_uses_token_from_an_earlier_frame(client_factory):
    client,sent,streams,_ = create_continuable(client_factory,[
        [continued_frame('one',None,'private-token-early'),continued_frame('two',22)],
        [continued_frame('three',1)]])
    try:
        results = await consume(client)
        assert sent[1][16]=='private-token-early' and all(s.closed for s in streams)
        assert results[-1]['candidates'][0]['finishReason']=='STOP'
        assert_no_private_metadata(results)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_max_tokens_without_same_frame_token_does_not_continue(client_factory):
    client,sent,streams,_ = create_continuable(client_factory,[[
        continued_frame('one',None,'private-token-early'),continued_frame('two',2)]])
    try:
        results = await consume(client)
        assert len(sent)==1 and streams[0].closed
        assert results[-1]['candidates'][0]['finishReason']=='MAX_TOKENS'
        assert_no_private_metadata(results)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit,expected_limits', [(1024,[1024]),(32768,[32768]),(32769,[32768,32769])])
async def test_explicit_limit_bounds_rounds_even_when_provider_requests_more(client_factory,limit,expected_limits):
    responses = [[continued_frame('round',22,'private-token-'+str(i))] for i in range(len(expected_limits))]
    client,sent,streams,_ = create_continuable(client_factory,responses)
    try:
        results = await consume(client,{**REQUEST,'generationConfig':{'maxOutputTokens':limit}})
        assert [wire[3][3] for wire in sent]==expected_limits
        assert len([c for r in results for c in r['candidates'] if 'finishReason' in c])==1
        assert results[-1]['candidates'][0]['finishReason']=='MAX_TOKENS'
        assert all(s.closed for s in streams) and not client.lock.locked()
        assert_no_private_metadata(results)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('token', [None,'',{},123])
async def test_missing_or_malformed_continuation_token_is_error_not_success(client_factory,token):
    client,sent,streams,_ = create_continuable(client_factory,[[continued_frame('partial',22,token)]])
    try:
        with pytest.raises(PlaygroundError) as error:
            await consume(client)
        assert error.value.code=='protocol_error' and len(sent)==1
        assert streams[0].closed and not client.lock.locked() and not client.generation_verified
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_repeated_continuation_token_stops_without_third_request(client_factory):
    client,sent,streams,_ = create_continuable(client_factory,[
        [continued_frame('one',22,'private-token-1')],
        [continued_frame('two',22,'private-token-1')]])
    try:
        with pytest.raises(PlaygroundError) as error:
            await consume(client,{**REQUEST,'generationConfig':{'maxOutputTokens':100000}})
        assert error.value.code=='protocol_error' and len(sent)==2
        assert all(s.closed for s in streams) and not client.lock.locked() and not client.generation_verified
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_second_round_failure_closes_response_and_does_not_retry(client_factory):
    client,sent,streams,_ = create_continuable(client_factory,[[continued_frame('one',22,'private-token-1')],403])
    try:
        with pytest.raises(PlaygroundError) as error:
            await consume(client)
        assert error.value.status==403 and len(sent)==2
        assert streams[0].closed and not client.lock.locked() and not client.generation_verified
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_disconnect_at_round_boundary_does_not_start_next_round(client_factory):
    client,sent,streams,proofs = create_continuable(client_factory,[[continued_frame('one',22,'private-token-1')]])
    source = client.generate(MODEL,REQUEST)
    try:
        result = await anext(source)
        assert 'finishReason' not in result['candidates'][0]
        assert_no_private_metadata([result])
        await source.aclose()
        assert len(sent)==len(proofs)==1 and streams[0].closed and not client.lock.locked()
    finally:
        await source.aclose()
        await client.close()


@pytest.mark.asyncio
async def test_cancellation_while_reading_second_round_closes_both_streams(client_factory):
    started = asyncio.Event()
    class WaitingStream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            started.set()
            await asyncio.Future()
            yield b''
        async def aclose(self): self.closed = True
    waiting = WaitingStream()
    client,sent,streams,proofs = create_continuable(client_factory,[
        [continued_frame('one',22,'private-token-1')],waiting])
    task = asyncio.create_task(consume(client))
    try:
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(sent)==len(proofs)==2 and all(s.closed for s in streams)
        assert not client.lock.locked() and not client.generation_verified
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await client.close()


@pytest.mark.asyncio
async def test_trailing_usage_metadata_is_merged_without_duplicate_finish(client_factory):
    late_usage = [[[None,None]],None,usage(5,4,2)]
    client,sent,streams,_ = create_continuable(client_factory,[
        [continued_frame('one',22,'private-token-1',usage(5,3,1)),late_usage],
        [continued_frame('two',1,usage=usage(7,2,1))]])
    try:
        results = await consume(client)
        assert len(sent)==2 and all(s.closed for s in streams)
        assert [c['finishReason'] for r in results for c in r['candidates'] if 'finishReason' in c]==['STOP']
        assert results[-1]['usageMetadata']['totalTokenCount']==16
        assert results[-1]['usageMetadata']['candidatesTokenCount']==6
        assert_no_private_metadata(results)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_truncated_round_with_finish_marker_does_not_continue(client_factory):
    broken = TrackedStream([continued_frame('one',22,'private-token-1')])
    broken.content = broken.content[:-1]
    client,sent,streams,_ = create_continuable(client_factory,[broken])
    try:
        with pytest.raises(PlaygroundError) as error:
            await consume(client)
        assert error.value.code=='stream_interrupted'
        assert len(sent)==1 and streams[0].closed and not client.lock.locked()
        assert not client.generation_verified
    finally:
        await client.close()


def test_openai_stream_has_one_finish_and_no_continuation_metadata(client_factory):
    client,sent,_,_ = create_continuable(client_factory,[
        [continued_frame('one',22,'private-token-1',usage(5,3,2))],
        [continued_frame('two',1,usage=usage(7,4,3))]])
    with TestClient(create_app(client)) as api:
        response = api.post('/v1/chat/completions',headers={'Authorization':'Bearer '+client.settings.password},
            json={'model':MODEL,'messages':[{'role':'user','content':'hello'}],'stream':True,'stream_options':{'include_usage':True}})
        assert response.status_code==200 and response.text.count('data: [DONE]')==1
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
        assert [c['finish_reason'] for e in events for c in e['choices'] if c.get('finish_reason')]==['stop']
        assert events[-1]['usage']=={'prompt_tokens':7,'completion_tokens':12,'total_tokens':19,
                                   'completion_tokens_details':{'reasoning_tokens':5}}
        assert_no_private_metadata(events)
        assert len(sent)==2
