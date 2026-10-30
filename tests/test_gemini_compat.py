"""Local compatibility contracts, not evidence of upstream acceptance."""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import model_row
from pgproxy.errors import PlaygroundError
from pgproxy.gemini_compat import normalize_count_tokens_request, normalize_gemini_request, normalize_schema
from pgproxy.main import create_app
from pgproxy.playground_protocol import encode_generate


def model(*codes, **extra):
    return {'name': 'models/test-chat', 'outputTokenLimit': 512, 'inputTokenLimit': 2048,
            '_codes': [1, *codes], **extra}


def body(**extra):
    return {'contents': [{'parts': [{'text': 'Hello'}]}], **extra}


def normalized(request, entry=None):
    entry = entry or model()
    result = normalize_gemini_request(request, entry)
    encode_generate('test-chat', result, entry)
    return result


def test_unknown_settings_filtered_without_mutating_prompt_or_caller():
    source = body(unknown='discard', cachedContent='not-implemented', generationConfig={
        'temperature': 0.6, 'frequencyPenalty': 1, 'presencePenalty': 1, 'responseLogprobs': True,
        'thinkingConfig': {'includeThoughts': True}, 'imageConfig': {'imageSize': '2K'},
        'candidateCount': 4, 'maxOutputTokens': 999999})
    source['contents'][0]['client_metadata'] = {'private': 'do-not-forward'}
    source['contents'][0]['parts'][0]['cache_control'] = {'type': 'ephemeral'}
    original = copy.deepcopy(source)
    result = normalized(source)
    assert source == original
    assert result == {'contents': [{'role': 'user', 'parts': [{'text': 'Hello'}]}],
                      'generationConfig': {'temperature': 0.6, 'candidateCount': 1, 'maxOutputTokens': 512}}


def test_safe_defaults_and_native_sdk_aliases():
    result = normalized({'contents': 'Hello', 'system_instruction': 'Be concise',
                         'generation_config': {'max_output_tokens': '80', 'top_p': 0.8, 'thinking_config': {}}})
    assert result['contents'] == [{'role': 'user', 'parts': [{'text': 'Hello'}]}]
    assert result['systemInstruction']['parts'] == [{'text': 'Be concise'}]
    assert result['generationConfig'] == {'maxOutputTokens': 80, 'topP': 0.8}


@pytest.mark.parametrize('value', [None, {}, [], 'invalid', True, float('nan'), float('inf')])
def test_optional_numeric_values_never_create_server_errors(value):
    result = normalized(body(generationConfig={'temperature': value, 'maxOutputTokens': value, 'seed': value}))
    assert result['generationConfig'] == {}


def test_numeric_settings_are_bounded_to_selected_model():
    result = normalized(body(generationConfig={'temperature': 99, 'topP': -1, 'topK': '-8', 'seed': 2**80,
                                              'maxOutputTokens': 99999}), model(91, maxTemperature=1.5))
    assert result['generationConfig'] == {'temperature': 1.5, 'topP': 0, 'topK': 0, 'seed': 2147483647, 'maxOutputTokens': 2048}


def test_model_options_filter_modalities_image_thinking_and_transcription():
    entry = model(21, 47, 49, 25, 35, 52, 74, 80, 81, 84, 76, 43,
                  _thinking=[128, 1024, 128, False, None, 2, [1, 3]],
                  _options={'image_aspect_ratios': ['1:1'], 'image_output_resolutions': ['1K'], 'languages': ['en']})
    result = normalized(body(generationConfig={
        'responseModalities': ['text', 'audio', 'image', 'text'],
        'imageConfig': {'aspectRatio': '1:1', 'imageSize': '4K', 'personGeneration': 'ALLOW_ALL'},
        'thinkingConfig': {'includeThoughts': False, 'thinkingBudget': 1, 'thinkingLevel': 'medium', 'other': 1},
        'transcriptionConfig': {'wordTimestamps': True, 'speakerLabels': True, 'smartTranscription': True,
                                'customVocabulary': ['PG'], 'languageCodes': ['en', 'unknown'], 'other': 1},
        'mediaResolution': 'MEDIA_RESOLUTION_HIGH'}), entry)
    config = result['generationConfig']
    assert config['responseModalities'] == ['TEXT', 'IMAGE']
    assert config['imageConfig'] == {'aspectRatio': '1:1'}
    assert config['thinkingConfig'] == {'includeThoughts': False, 'thinkingBudget': 128}
    assert config['transcriptionConfig'] == {'wordTimestamps': True, 'speakerLabels': True, 'languageCodes': ['en']}
    assert config['mediaResolution'] == 'MEDIA_RESOLUTION_HIGH'


def test_schemas_keep_supported_properties_and_complete_response_mime():
    schema = {'type': 'object', 'additionalProperties': False, 'properties': {
        'answer': {'type': 'string', 'minLength': 1, 'unknownKeyword': True},
        'nested': {'$ref': '#/$defs/Result'}, 'unexpressible': {}},
        'required': ['answer', 'unexpressible'], '$defs': {'Result': {'type': 'integer', 'minimum': 0}}}
    result = normalized(body(generationConfig={'responseJsonSchema': schema}))
    assert result['generationConfig'] == {'responseMimeType': 'application/json', 'responseSchema': {
        'type': 'object', 'properties': {'answer': {'type': 'string', 'minLength': 1},
                                       'nested': {'type': 'integer', 'minimum': 0}}, 'required': ['answer']}}


def test_thinking_level_precedes_budget_and_catalog_drives_voice_and_image_defaults():
    config = normalized(body(generationConfig={'thinkingConfig': {'thinkingLevel': 'high', 'thinkingBudget': 512}}),
                        model(25, 35, 52, _thinking=[128, 1024, 128, False, None, 3, [3]]))['generationConfig']
    assert config['thinkingConfig'] == {'thinkingLevel': 'HIGH'}
    voice = normalized(body(), model(37, _options={'voices': ['Puck']}))
    assert voice['generationConfig']['speechConfig']['voiceConfig']['prebuiltVoiceConfig']['voiceName'] == 'Puck'
    image = normalized(body(), model(21, 49, _options={'image_output_resolutions': ['2K']}))
    assert image['generationConfig']['imageConfig'] == {'imageSize': '2K'}


def test_system_prompt_and_native_history_metadata_are_preserved():
    source = body(systemInstruction={'parts': [{'text': 'Follow this instruction', 'cache_control': {}}]},
                  generationConfig={'responseSchema': {'type': 'object', 'example': {'answer': 1}, 'additionalProperties': True}})
    result = normalized(source)
    assert result['systemInstruction']['parts'] == [{'text': 'Follow this instruction'}]
    assert result['generationConfig']['responseSchema']['example'] == {'answer': 1}
    assert normalize_schema({'enum': [1, 2]}) is None


def test_optional_media_metadata_filters_unknown_values_without_losing_media():
    result = normalized({'contents': [{'parts': [{'inlineData': {'mimeType': 'video/mp4', 'data': 'eA==', 'unused': 1},
                       'videoMetadata': {'fps': 100, 'startOffset': '1s', 'endOffset': '0s', 'unused': 1}, 'mediaProcessing': 'AGENTIC'}]}]}, model(69))
    part = result['contents'][0]['parts'][0]
    assert part['inlineData'] == {'mimeType': 'video/mp4', 'data': 'eA=='}
    assert part['videoMetadata'] == {'fps': 24, 'startOffset': '1s'}
    assert part['mediaProcessing'] == 'STATIC'


def test_recursive_or_remote_schema_refs_do_not_fetch_or_crash():
    assert normalize_schema({'$ref': 'https://example.test/schema'}) is None
    assert normalize_schema({'$ref': '#/$defs/node', '$defs': {'node': {'$ref': '#/$defs/node'}}}) is None
    assert normalize_schema({'type': 'array'}) is None
    assert normalized(body(generationConfig={'responseSchema': {'$ref': '#/missing'}, 'responseMimeType': 'application/json'}))['generationConfig'] == {'responseMimeType': 'application/json'}


def test_tool_options_are_projected_and_supported_declarations_preserved():
    result = normalized(body(tools=[{'unknown': {}}, {'googleSearch': {'searchTypes': {'webSearch': {}, 'imageSearch': {}}, 'other': 1}},
                                   {'googleMaps': {}}, {'codeExecution': {'unknown': True}},
                                   {'functionDeclarations': [{'name': 'weather', 'strict': True, 'parameters': {
                                       'type': 'object', 'additionalProperties': False, 'properties': {'city': {'type': 'string'}}}}]}],
                             toolConfig={'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': ['weather', 'absent'], 'unknown': 1}, 'unknown': True}),
                        model(10, 12, 9))
    assert result['tools'] == [{'googleSearch': {'searchTypes': {'webSearch': {}}}}, {'codeExecution': {}},
                               {'functionDeclarations': [{'name': 'weather', 'parameters': {'type': 'object', 'properties': {'city': {'type': 'string'}}}}]}]
    assert result['toolConfig'] == {'functionCallingConfig': {'mode': 'AUTO', 'allowedFunctionNames': ['weather']}}


def test_unknown_safety_categories_ignored_valid_categories_retained():
    result = normalized(body(safetySettings=[{'category': 'NEW_HARM', 'threshold': 'OFF'},
                                            {'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'OFF', 'method': 'UNKNOWN'}]))
    assert result['safetySettings'] == [{'category': 'HARM_CATEGORY_HARASSMENT', 'threshold': 'OFF'}]


def test_bad_optional_function_declarations_do_not_discard_valid_functions_or_text():
    result = normalized(body(tools=[{'functionDeclarations': [{'name': None}, {'name': {}},
                         {'name': 'invalid name'}, {'name': 'valid'}, {'name': 'valid'}]}]), model(10))
    assert result['tools'] == [{'functionDeclarations': [{'name': 'valid', 'parameters': {'type': 'object'}}]}]
    assert result['contents'][0]['parts'] == [{'text': 'Hello'}]


@pytest.mark.parametrize('source', [{}, {'contents': []}, {'contents': [{'parts': []}]},
                                     {'contents': [{'parts': [{'text': 42}]}]},
                                     {'contents': [{'parts': [{'inlineData': {'mimeType': 'image/png', 'data': '!!!'}}]}]},
                                     {'contents': [{'parts': [{'thoughtSignature': 42}]}]}])
def test_required_content_and_signatures_are_not_invented(source):
    with pytest.raises(PlaygroundError) as caught:
        normalized(source)
    assert caught.value.status == 400


def test_function_history_opaque_data_and_signatures_survive_normalization():
    source = {'contents': [{'parts': [{'function_call': {'name': 'f', 'args': {'unfamiliar': {'deep': 1}}, 'id': 'call-1'},
                                      'thought_signature': 'SYNTHETIC-ONLY'}]},
                           {'parts': [{'functionResponse': {'name': 'f', 'response': {'unfamiliar': [1, 2]}, 'id': 'call-1'}}]}]}
    result = normalized(source, model(10))
    assert result['contents'][0]['role'] == 'model'
    assert result['contents'][0]['parts'][0]['thoughtSignature'] == 'SYNTHETIC-ONLY'
    assert result['contents'][0]['parts'][0]['functionCall']['args'] == {'unfamiliar': {'deep': 1}}
    assert result['contents'][1]['parts'][0]['functionResponse']['response'] == {'unfamiliar': [1, 2]}


@pytest.mark.parametrize('stream', [False, True])
def test_native_http_compatibility_runs_one_upstream_generation(client_factory, stream):
    client, captured = client_factory()
    with TestClient(create_app(client)) as api:
        route = 'streamGenerateContent?alt=sse' if stream else 'generateContent'
        response = api.post('/v1beta/models/test-chat:' + route, headers={'x-goog-api-key': client.settings.password},
                            json={'contents': 'Hello', 'cachedContent': 'ignored', 'generationConfig': {
                                'maxOutputTokens': 12345, 'frequencyPenalty': 1, 'imageConfig': {'imageSize': '2K'}, 'temperature': 0.7}})
        assert response.status_code == 200, response.text
        if stream:
            assert 'data: ' in response.text
        else:
            assert ''.join(part.get('text', '') for part in response.json()['candidates'][0]['content']['parts']) == 'OK'
    requests = [r for r in captured if r.url.path.endswith('/GenerateContent')]
    assert len(requests) == 1
    wire = json.loads(requests[0].content)
    assert wire[3][3] == 512 and wire[3][4] == 0.7
    assert wire[1][0][0][0][1] == 'Hello'


def test_count_tokens_compat_preserves_path_model_check_and_no_generation(client_factory):
    def handler(request):
        if request.url.path.endswith('/ListModels'):
            row = model_row()
            row[7].append('countTokens')
            return httpx.Response(200, json=[[row]])
        if request.url.path.endswith('/CountTokens'):
            return httpx.Response(200, json=[8])
    client, captured = client_factory(extra_handler=handler)
    with TestClient(create_app(client)) as api:
        response = api.post('/v1beta/models/test-chat:countTokens', headers={'x-goog-api-key': client.settings.password},
                            json={'generateContentRequest': {'model': 'models/test-chat', 'contents': 'Hello',
                                  'generationConfig': {'newSetting': 1}}, 'unknown': True})
        assert response.status_code == 200 and response.json()['totalTokens'] == 8
        mismatch = api.post('/v1beta/models/test-chat:countTokens', headers={'x-goog-api-key': client.settings.password},
                           json={'generateContentRequest': {'model': 'different', 'contents': 'Hello'}})
        assert mismatch.status_code == 400
    assert not any(r.url.path.endswith('/GenerateContent') for r in captured)
    assert len([r for r in captured if r.url.path.endswith('/CountTokens')]) == 1


def test_count_wrapper_conflict_is_not_silently_changed():
    source = {'contents': 'A', 'generateContentRequest': {'contents': 'B'}}
    result = normalize_count_tokens_request(source, model())
    assert 'contents' in result and 'generateContentRequest' in result
