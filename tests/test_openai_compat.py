"""Compatibility policy tests; synthetic data, never Google acceptance evidence."""
import copy
import json

import pytest

from pgproxy.errors import PlaygroundError
from pgproxy.openai_compat import normalize_openai_request, select_default_model
from pgproxy.openai_transformers import openai_request_to_gemini


def request(**changes):
    return {'model': 'test-chat', 'messages': [{'role': 'user', 'content': 'hello'}], **changes}


def test_normalization_is_immutable_idempotent_and_keeps_supported_options():
    source = request(
        temperature='0.7', max_tokens=64, max_completion_tokens='128',
        frequency_penalty=1, metadata={'private': 'never forward'},
        stream='true', stream_options={'include_usage': 'true', 'include_obfuscation': True},
        messages=[{'role': 'user', 'name': 'Speaker', 'content': [{'text': 'hello', 'cache_control': {'type': 'ephemeral'}}]}],
        response_format={'type': 'json_schema', 'json_schema': {'schema': {'type': 'object'}, 'strict': True}},
        tools=[{'function': {'name': 'ping', 'strict': True}}],
        tool_choice='required', parallel_tool_calls=False,
    )
    original = copy.deepcopy(source)
    normalized = normalize_openai_request(source)
    assert source == original
    assert normalize_openai_request(normalized) == normalized
    assert normalized['temperature'] == .7
    assert normalized['max_completion_tokens'] == 128 and 'max_tokens' not in normalized
    assert normalized['stream'] is True and normalized['stream_options'] == {'include_usage': True}
    assert 'metadata' not in normalized and 'frequency_penalty' not in normalized
    assert normalized['messages'][0] == {'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}]}
    assert normalized['response_format']['json_schema']['name'] == 'response'
    assert 'strict' not in normalized['response_format']['json_schema']
    assert normalized['tools'] == [{'type': 'function', 'function': {'name': 'ping', 'parameters': {'type': 'object', 'properties': {}}}}]
    assert normalized['tool_choice'] == 'auto' and 'parallel_tool_calls' not in normalized


def test_legacy_function_history_has_deterministic_local_ids_and_no_invented_signatures():
    source = request(
        functions=[{'name': 'ping', 'parameters': {'type': 'object', 'properties': {}}}],
        function_call={'name': 'ping'},
        messages=[
            {'role': 'user', 'content': 'ping'},
            {'role': 'assistant', 'content': None, 'function_call': {'name': 'ping'}},
            {'role': 'function', 'name': 'ping', 'content': '{"ok":true}'},
        ],
    )
    normalized = normalize_openai_request(source)
    assert normalize_openai_request(normalized) == normalized
    call = normalized['messages'][1]['tool_calls'][0]
    assert call['function']['arguments'] == '{}'
    assert call['id'] == normalized['messages'][2]['tool_call_id']
    _, native = openai_request_to_gemini(source)
    assert native['toolConfig']['functionCallingConfig']['mode'] == 'AUTO'
    assert native['contents'][1]['parts'][0]['functionCall']['id'] == call['id']
    assert native['contents'][2]['parts'][0]['functionResponse']['id'] == call['id']
    assert 'thoughtSignature' not in native['contents'][1]['parts'][0]


def test_missing_tool_id_can_only_match_one_pending_name():
    source = request(messages=[
        {'role': 'assistant', 'tool_calls': [
            {'function': {'name': 'a', 'arguments': {'x': 1}}},
            {'function': {'name': 'b'}},
        ]},
        {'role': 'tool', 'name': 'b', 'content': 'B'},
        {'role': 'tool', 'name': 'a', 'content': 'A'},
    ])
    _, native = openai_request_to_gemini(source)
    calls = native['contents'][0]['parts']
    replies = native['contents'][1]['parts']
    assert calls[0]['functionCall']['args'] == {'x': 1}
    assert replies[0]['functionResponse']['id'] == calls[1]['functionCall']['id']
    assert replies[1]['functionResponse']['id'] == calls[0]['functionCall']['id']
    source['messages'][1].pop('name')
    with pytest.raises(PlaygroundError):
        openai_request_to_gemini(source)


def test_explicit_call_ids_and_google_signatures_survive_without_provider_metadata():
    signature = 'SYNTHETIC-NOT-A-GOOGLE-SIGNATURE'
    source = request(messages=[
        {'role': 'assistant', 'tool_calls': [{'id': 'client-id', 'function': {'name': 'f'},
         'extra_content': {'vendor': {'ignored': True}, 'google': {'thought_signature': signature, 'unknown': 1}}}]},
        {'role': 'tool', 'tool_call_id': 'client-id', 'content': 'ok'},
    ])
    _, native = openai_request_to_gemini(source)
    part = native['contents'][0]['parts'][0]
    assert part['functionCall']['id'] == 'client-id'
    assert part['thoughtSignature'] == signature
    source['messages'][1]['tool_call_id'] = 'different-id'
    with pytest.raises(PlaygroundError):
        openai_request_to_gemini(source)


@pytest.mark.parametrize('content', [None, [], {}, [{'type': 'file', 'file': {'data': 'unknown'}}]])
def test_missing_or_unknown_conversational_content_is_not_fabricated(content):
    with pytest.raises(PlaygroundError) as caught:
        openai_request_to_gemini(request(messages=[{'content': content}]))
    assert caught.value.status == 400


def test_image_resolution_hint_is_ignored_but_media_data_remains_unchanged():
    source = request(messages=[{'content': [{'image_url': {'url': 'data:image/png;base64,eA==', 'detail': 'high', 'unused': 1}}]}])
    _, native = openai_request_to_gemini(source)
    assert native['contents'][0]['parts'] == [{'inlineData': {'mimeType': 'image/png', 'data': 'eA=='}}]


@pytest.mark.parametrize('key', [
    'n', 'stream_options', 'temperature', 'top_p', 'top_k', 'max_tokens',
    'max_completion_tokens', 'seed', 'reasoning_effort', 'thinking_config',
    'response_format', 'tools', 'tool_choice', 'parallel_tool_calls', 'modalities',
    'stop', 'image_config', 'speech_config', 'transcription_config',
])
@pytest.mark.parametrize('value', [None, {}, [], False, 1, 'unsupported', 10**500])
def test_optional_control_types_never_raise_unhandled_exceptions(key, value):
    # Low-level native configuration validation happens after this translation;
    # neither a giant optional integer nor unhashable option should become 500.
    _, native = openai_request_to_gemini(request(**{key: value}))
    assert native['contents'][0]['parts'] == [{'text': 'hello'}]


@pytest.mark.parametrize('calls', [1, {}, 'bad'])
def test_malformed_tool_history_is_a_controlled_error(calls):
    with pytest.raises(PlaygroundError) as error:
        openai_request_to_gemini(request(messages=[{'role': 'assistant', 'content': 'x', 'tool_calls': calls}]))
    assert error.value.status == 400


def test_default_model_comes_from_current_text_catalog_and_avoids_media_models():
    catalog = {
        'new-image': {'_codes': [1, 21]},
        'new-transcribe': {'_codes': [1, 77]},
        'new-pro': {'_codes': [1]},
        'new-flash': {'_codes': [1]},
    }
    assert select_default_model(catalog) == 'new-flash'
    del catalog['new-flash']
    assert select_default_model(catalog) == 'new-pro'
    del catalog['new-pro']
    with pytest.raises(PlaygroundError) as caught:
        select_default_model(catalog)
    assert caught.value.code == 'model_required'


def test_assistant_tool_defaults_do_not_shadow_existing_id():
    source = request(messages=[
        {'role': 'assistant', 'tool_calls': [
            {'function': {'name': 'a'}},
            {'id': 'call_compat_0_0', 'function': {'name': 'b'}},
        ]},
        {'role': 'tool', 'name': 'a', 'content': 'a'},
        {'role': 'tool', 'tool_call_id': 'call_compat_0_0', 'content': 'b'},
    ])
    _, native = openai_request_to_gemini(source)
    ids = [part['functionCall']['id'] for part in native['contents'][0]['parts']]
    assert len(set(ids)) == 2 and ids[1] == 'call_compat_0_0'


def test_standard_json_can_serialize_normalized_output():
    normalized = normalize_openai_request(request(temperature=float('nan'), max_tokens=float('inf')))
    assert 'temperature' not in normalized and 'max_tokens' not in normalized
    json.dumps(normalized, allow_nan=False)


def test_supported_stop_sequences_are_not_arbitrarily_truncated():
    stops = [f'END_{index}' for index in range(8)]
    _, native = openai_request_to_gemini(request(stop=stops))
    assert native['generationConfig']['stopSequences'] == stops
