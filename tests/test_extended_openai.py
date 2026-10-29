"""Synthetic compatibility contracts only; not evidence of live model acceptance.

No Google account, external transport, or real thought signature is used here.
"""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import model_row
from pgproxy.errors import PlaygroundError
from pgproxy.main import create_app
from pgproxy.openai_transformers import OpenAIProjector, gemini_response_to_openai, openai_request_to_gemini
from pgproxy.responses import collect


SIGNATURE = 'SYNTHETIC_SIGNATURE_ONLY'
TOOL = {'type': 'function', 'function': {'name': 'get_weather', 'description': 'Look up weather', 'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}}, 'required': ['city']}}}


def request(**changes):
    return {'model': 'test-chat', 'messages': [{'role': 'user', 'content': 'Weather?'}], **changes}


def call(call_id='call_weather', name='get_weather', signature=SIGNATURE):
    result = {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': '{"city":"Paris"}'}}
    if signature:
        result['extra_content'] = {'google': {'thought_signature': signature}}
    return result


def native(parts, finish='STOP', **metadata):
    candidate = {'index': 0, 'content': {'role': 'model', 'parts': parts}, **metadata}
    if finish:
        candidate['finishReason'] = finish
    return {'candidates': [candidate]}


def test_function_history_preserves_ids_args_signatures_and_groups_parallel_replies():
    messages = [
        {'role': 'user', 'content': 'Weather?'},
        {'role': 'assistant', 'content': None, 'tool_calls': [call(), call('call_second', signature=None)]},
        {'role': 'tool', 'tool_call_id': 'call_second', 'content': 'sunny'},
        {'role': 'tool', 'tool_call_id': 'call_weather', 'content': '{"temperature":20}'},
    ]
    original = copy.deepcopy(messages)
    _, payload = openai_request_to_gemini(request(messages=messages, tools=[TOOL], tool_choice='auto', parallel_tool_calls=True))
    assert messages == original
    assert payload['tools'] == [{'functionDeclarations': [TOOL['function']]}]
    assert payload['toolConfig'] == {'functionCallingConfig': {'mode': 'AUTO'}}
    model_parts = payload['contents'][1]['parts']
    assert model_parts[0] == {'functionCall': {'name': 'get_weather', 'args': {'city': 'Paris'}, 'id': 'call_weather'}, 'thoughtSignature': SIGNATURE}
    assert len(payload['contents']) == 3
    responses = payload['contents'][2]['parts']
    assert responses[0]['functionResponse'] == {'name': 'get_weather', 'id': 'call_second', 'response': {'output': 'sunny'}}
    assert responses[1]['functionResponse']['response'] == {'temperature': 20}


@pytest.mark.parametrize('choice', ['auto', 'none'])
def test_supported_function_selection(choice):
    _, payload = openai_request_to_gemini(request(tools=[TOOL], tool_choice=choice))
    assert payload['toolConfig']['functionCallingConfig']['mode'] == choice.upper()


def test_builtin_tools_and_transcription_extensions_map_to_native_validation():
    names = {'google_search': 'googleSearch', 'image_search': 'imageSearch', 'code_execution': 'codeExecution', 'url_context': 'urlContext', 'google_maps': 'googleMaps'}
    _, payload = openai_request_to_gemini(request(tools=[{'type': k} for k in names], transcription_config={'wordTimestamps': True}, media_resolution='MEDIA_RESOLUTION_HIGH'))
    assert payload['tools'] == [{k: {}} for k in names.values()]
    assert payload['generationConfig']['transcriptionConfig'] == {'wordTimestamps': True}
    assert payload['generationConfig']['mediaResolution'] == 'MEDIA_RESOLUTION_HIGH'


@pytest.mark.parametrize('changes', [
    {'tool_choice': 'required', 'tools': [TOOL]},
    {'tool_choice': {'type': 'function', 'function': {'name': 'get_weather'}}, 'tools': [TOOL]},
    {'tool_choice': 'auto'},
    {'parallel_tool_calls': False, 'tools': [TOOL]},
    {'parallel_tool_calls': 1, 'tools': [TOOL]},
    {'tools': [TOOL, TOOL]},
    {'tools': [{'type': 'function', 'function': {'name': 'f', 'strict': True}}]},
    {'messages': [{'role': 'tool', 'tool_call_id': 'missing', 'content': 'no'}]},
    {'messages': [{'role': 'assistant', 'content': None, 'tool_calls': [call()]}]},
    {'messages': [{'role': 'assistant', 'content': None, 'tool_calls': [call()]}, {'role': 'user', 'content': 'skip response'}]},
    {'messages': [{'role': 'user', 'content': 'x', 'tool_calls': []}]},
    {'response_format': {'type': 'json_schema', 'json_schema': {'name': 'answer', 'schema': {}, 'strict': True}}},
    {'thinking_config': {}, 'reasoning_effort': 'high'},
    {'reasoning_effort': 'max'},
    {'modalities': ['image', 'image']},
    {'modalities': [{}]},
    {'max_tokens': 10, 'max_completion_tokens': 20},
    {'audio': {'format': 'mp3', 'voice': 'alloy'}},
])
def test_unsupported_or_ambiguous_features_rejected(changes):
    with pytest.raises(PlaygroundError) as caught:
        openai_request_to_gemini(request(**changes))
    assert caught.value.status == 400


@pytest.mark.parametrize('arguments', ['[1]', 'null', '{broken', '{"x":NaN}'])
def test_tool_arguments_require_json_object(arguments):
    item = call()
    item['function']['arguments'] = arguments
    with pytest.raises(PlaygroundError):
        openai_request_to_gemini(request(messages=[{'role': 'assistant', 'content': None, 'tool_calls': [item]}, {'role': 'tool', 'tool_call_id': item['id'], 'content': 'ok'}]))


def test_structured_json_thinking_modalities_and_native_options():
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}
    _, payload = openai_request_to_gemini(request(response_format={'type': 'json_schema', 'json_schema': {'name': 'answer', 'schema': schema}}, reasoning_effort='high', modalities=['text', 'image'], image_config={'aspectRatio': '16:9', 'imageSize': '1K'}))
    config = payload['generationConfig']
    assert config['responseSchema'] == schema
    assert config['responseMimeType'] == 'application/json'
    assert config['thinkingConfig'] == {'thinkingLevel': 'HIGH'}
    assert config['responseModalities'] == ['TEXT', 'IMAGE']
    assert config['imageConfig'] == {'aspectRatio': '16:9', 'imageSize': '1K'}
    _, payload = openai_request_to_gemini(request(reasoning_effort='none', modalities=['audio'], speech_config={'voiceConfig': {'prebuiltVoiceConfig': {'voiceName': 'Zephyr'}}}))
    assert payload['generationConfig']['thinkingConfig'] == {'thinkingBudget': 0}
    assert payload['generationConfig']['speechConfig']['voiceConfig']['prebuiltVoiceConfig']['voiceName'] == 'Zephyr'


def test_image_and_audio_input_never_download_remote_urls():
    content = [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,eA==', 'detail': 'auto'}}, {'type': 'input_audio', 'input_audio': {'data': 'eQ==', 'format': 'mp3'}}]
    _, payload = openai_request_to_gemini(request(messages=[{'role': 'user', 'content': content}]))
    assert payload['contents'][0]['parts'] == [{'inlineData': {'mimeType': 'image/png', 'data': 'eA=='}}, {'inlineData': {'mimeType': 'audio/mpeg', 'data': 'eQ=='}}]


@pytest.mark.parametrize('part', [
    {'type': 'image_url', 'image_url': {'url': 'https://example.test/private.png'}},
    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,!!!'}},
    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,eA==', 'detail': 'high'}},
    {'type': 'input_audio', 'input_audio': {'data': 'eA==', 'format': 'unknown'}},
    {'type': 'input_audio', 'input_audio': {'data': 'eA==', 'format': {}}},
])
def test_media_errors_are_controlled(part):
    with pytest.raises(PlaygroundError):
        openai_request_to_gemini(request(messages=[{'role': 'user', 'content': [part]}]))


def test_generated_media_uses_real_mime_and_rikka_image_contract_and_replays_signatures():
    parts = [{'text': 'Here'}, {'inlineData': {'mimeType': 'image/png', 'data': 'eA=='}, 'thoughtSignature': SIGNATURE}, {'inlineData': {'mimeType': 'audio/L16;codec=pcm;rate=24000', 'data': 'eQ=='}}]
    output = gemini_response_to_openai(native(parts), 'test-chat')
    message = output['choices'][0]['message']
    assert message['content'] == 'Here'
    assert message['images'][0]['image_url']['url'] == 'data:image/png;base64,eA=='
    assert message['audio_chunks'] == [{'data': 'eQ==', 'mime_type': 'audio/L16;codec=pcm;rate=24000'}]
    assert 'audio' not in message and '![generated]' not in message['content']
    assert message['extra_content']['google']['parts'] == parts
    _, replay = openai_request_to_gemini(request(messages=[message, {'role': 'user', 'content': 'continue'}]))
    assert replay['contents'][0]['parts'] == parts


def test_native_tool_projection_roundtrip_generates_unique_public_id_without_faking_signature():
    parts = [{'functionCall': {'name': 'get_weather', 'args': {'city': 'Paris'}}, 'thoughtSignature': SIGNATURE}]
    source = native(parts)
    output = gemini_response_to_openai(source, 'test-chat')
    choice = output['choices'][0]
    message = choice['message']
    call_id = message['tool_calls'][0]['id']
    assert call_id.startswith('call_') and choice['finish_reason'] == 'tool_calls'
    assert message['content'] is None
    assert message['tool_calls'][0]['extra_content']['google']['thought_signature'] == SIGNATURE
    assert 'id' not in source['candidates'][0]['content']['parts'][0]['functionCall']
    _, replay = openai_request_to_gemini(request(messages=[message, {'role': 'tool', 'tool_call_id': call_id, 'content': 'sunny'}]))
    assert replay['contents'][0]['parts'][0]['functionCall']['id'] == call_id


def test_stream_projector_preserves_unsigned_prefix_signed_tail_and_multiple_tool_indexes():
    projector = OpenAIProjector()
    first = projector.project(native([{'text': 'prefix'}], None), streaming=True)
    assert first == {'content': 'prefix'}
    second = projector.project(native([{'text': '', 'thoughtSignature': SIGNATURE}], None), streaming=True)
    assert second['extra_content']['google']['parts'] == [{'text': 'prefix'}, {'text': '', 'thoughtSignature': SIGNATURE}]
    third = projector.project(native([{'functionCall': {'name': 'a', 'args': {}}}, {'functionCall': {'name': 'b', 'args': {}}}]), streaming=True)
    assert [c['index'] for c in third['tool_calls']] == [0, 1]
    assert len({c['id'] for c in third['tool_calls']}) == 2
    assert projector.tool_count == 2


def test_transcription_and_media_processing_metadata_are_not_lost():
    parts = [{'text': 'hello', 'transcriptionMetadata': {'speaker': 'A'}}, {'toolCall': {'toolType': 'MEDIA_PROCESSING', 'toolName': 'inspect', 'args': {}, 'id': 'media-1'}}]
    candidate_metadata = {'groundingMetadata': {'webSearchQueries': ['q']}, 'citationMetadata': {'citationSources': []}}
    message = gemini_response_to_openai(native(parts, **candidate_metadata), 'test-chat')['choices'][0]['message']
    assert message['extra_content']['google']['parts'] == parts
    assert message['extra_content']['google']['groundingMetadata'] == candidate_metadata['groundingMetadata']
    _, payload = openai_request_to_gemini(request(messages=[message, {'role': 'user', 'content': 'next'}]))
    assert payload['contents'][0]['parts'] == parts


@pytest.mark.asyncio
async def test_native_collect_preserves_candidate_metadata_and_closes_source():
    closed = []
    async def chunks():
        try:
            yield native([{'text': 'first'}], None)
            yield {**native([{'text': 'last'}], groundingMetadata={'webSearchQueries': ['query']}, citationMetadata={'citationSources': [{'uri': 'https://example.test'}]}), 'modelVersion': 'synthetic', '_internal': 'hidden'}
        finally:
            closed.append(True)
    result = await collect(chunks())
    assert closed == [True]
    candidate = result['candidates'][0]
    assert candidate['groundingMetadata']['webSearchQueries'] == ['query']
    assert candidate['citationMetadata']['citationSources'][0]['uri'] == 'https://example.test'
    assert result['modelVersion'] == 'synthetic' and '_internal' not in result


@pytest.mark.parametrize('stream', [False, True])
def test_function_wire_to_openai_response_and_followup_api(client_factory, stream):
    generated = []
    def handler(req):
        if req.url.path.endswith('/ListModels'):
            row = model_row()
            row[64].append(10)
            return httpx.Response(200, json=[[row]])
        if req.url.path.endswith('/GenerateContent'):
            generated.append(json.loads(req.content))
            part = [None] * 15
            part[10] = ['get_weather', [[['city', [None, None, 'Paris']]]], 'call_weather']
            part[14] = SIGNATURE
            first = [[[[[part], 'model'], None]], None, [5, 1, 6]]
            last = [[[[[], 'model'], 1]], None, [5, 1, 6]]
            return httpx.Response(200, json=[[first, last]])
    client, _ = client_factory(extra_handler=handler)
    headers = {'Authorization': 'Bearer ' + client.settings.password}
    with TestClient(create_app(client)) as api:
        response = api.post('/v1/chat/completions', headers=headers, json=request(tools=[TOOL], stream=stream, stream_options={'include_usage': True}))
        assert response.status_code == 200, response.text
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
            calls = [tool for e in events for choice in e['choices'] for tool in choice['delta'].get('tool_calls', [])]
            assert len(calls) == 1 and calls[0]['index'] == 0
            assert [c['finish_reason'] for e in events for c in e['choices'] if c['finish_reason']] == ['tool_calls']
            assert response.text.count('data: [DONE]') == 1
            assert events[-1]['usage']['total_tokens'] == 6
        else:
            message = response.json()['choices'][0]['message']
            assert response.json()['choices'][0]['finish_reason'] == 'tool_calls'
            assert message['tool_calls'][0]['id'] == 'call_weather'
            followup = api.post('/v1/chat/completions', headers=headers, json=request(tools=[TOOL], messages=[{'role': 'user', 'content': 'Weather?'}, message, {'role': 'tool', 'tool_call_id': 'call_weather', 'content': '{"temp":20}'}]))
            assert followup.status_code == 200, followup.text
            assert generated[-1][1][1][0][0][10][2] == 'call_weather'
            assert generated[-1][1][1][0][0][14] == SIGNATURE
            assert generated[-1][1][2][0][0][11][2] == 'call_weather'
    assert client.closed


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('mime,capability,field', [('image/png', 21, 'images'), ('audio/L16;codec=pcm;rate=24000', 37, 'audio_chunks')])
def test_media_only_generation_does_not_become_empty_text(client_factory, stream, mime, capability, field):
    def handler(req):
        if req.url.path.endswith('/ListModels'):
            row = model_row()
            row[64].append(capability)
            return httpx.Response(200, json=[[row]])
        if req.url.path.endswith('/GenerateContent'):
            part = [None, None, [mime, 'eA==']]
            return httpx.Response(200, json=[[[[[[[part], 'model'], 1]], None, [5, 1, 6]]]])
    client, _ = client_factory(extra_handler=handler)
    with TestClient(create_app(client)) as api:
        response = api.post('/v1/chat/completions', headers={'Authorization': 'Bearer ' + client.settings.password}, json=request(stream=stream))
        assert response.status_code == 200, response.text
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: {')]
            assert any(choice['delta'].get(field) for event in events for choice in event['choices'])
            assert response.text.endswith('data: [DONE]\n\n')
        else:
            assert response.json()['choices'][0]['message'][field]
            assert response.json()['choices'][0]['finish_reason'] == 'stop'
