"""Independent JSON consumer contracts, not execution of the RikkaHub app.

Observed against local reference commit a5db9c9b770f316c76136970aa01a9d790e10e51:
OpenAIProvider.listModels reads data[].id; GoogleProvider.listModels requests
models?pageSize=100 and reads name/displayName/supportedGenerationMethods.
Neither list reader consumes capability extensions or follows a next-page token.
SettingProviderDetailPage obtains capability badges from its own ModelRegistry.

Only these public JSON contracts informed the tests; no RikkaHub implementation
is imported, copied, or required at runtime. All catalog rows/transport are fake.
"""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import model_row
from pgproxy.errors import PlaygroundError
from pgproxy.main import create_app
from pgproxy.model_catalog import openai_model, parse_model_row
from pgproxy.openai_transformers import openai_request_to_gemini
from pgproxy.playground_protocol import encode_generate


def openai_consumer_view(body):
    """Only the ID is used for both identifier and label by this consumer."""
    assert isinstance(body['data'], list)
    return [(row['id'], row['id']) for row in body['data'] if isinstance(row.get('id'), str)]


def google_consumer_view(body):
    """Required native field names and method filtering, independent of metadata."""
    result = []
    for row in body['models']:
        assert isinstance(row['supportedGenerationMethods'], list)
        if 'generateContent' in row['supportedGenerationMethods'] or 'embedContent' in row['supportedGenerationMethods']:
            result.append((row['name'].split('/', 1)[1], row['displayName']))
    return result


def catalog_client(client_factory, rows):
    def handler(req):
        if req.url.path.endswith('/ListModels'):
            return httpx.Response(200, json=[rows])
    return client_factory(extra_handler=handler)


def synthetic_rows(count=19):
    rows = []
    for number in range(count):
        name = 'gemini-4-argon' if number == 0 else 'future-catalog-model-' + str(number)
        row = model_row('models/' + name)
        row[3] = 'Synthetic display ' + str(number)
        row[64] += {0: [91, 10], 1: [21, 47, 49], 2: [37], 3: [77]}.get(number, [])
        row[7].append('countTokens')
        rows.append(row)
    return rows


def test_rikka_openai_catalog_reads_all_19_ids_including_continuable_and_future_models(client_factory):
    rows = synthetic_rows()
    client, transport = catalog_client(client_factory, rows)
    with TestClient(create_app(client)) as api:
        response = api.get('/v1/models', headers={'Authorization': 'Bearer ' + client.settings.password})
        assert response.status_code == 200
        body = response.json()
        view = openai_consumer_view(body)
        expected = [(row[0][7:], row[0][7:]) for row in rows]
        assert view == expected and len(view) == 19
        assert ('gemini-4-argon', 'gemini-4-argon') in view
        # Optional enrichment cannot alter this client's simple list reader.
        assert openai_consumer_view({'data': [{'id': row['id']} for row in body['data']]}) == view
        assert body['data'][0]['name'] != view[0][1]
    assert all(not req.url.path.endswith('/GenerateContent') for req in transport)


@pytest.mark.parametrize('auth_mode', ['header', 'query'])
def test_rikka_google_catalog_exact_page_size_auth_and_fields(client_factory, auth_mode):
    rows = synthetic_rows()
    client, transport = catalog_client(client_factory, rows)
    params, headers = {'pageSize': '100'}, {}
    if auth_mode == 'header':
        headers['x-goog-api-key'] = client.settings.password
    else:
        params['key'] = client.settings.password
    with TestClient(create_app(client)) as api:
        response = api.get('/v1beta/models', params=params, headers=headers)
        assert response.status_code == 200
        body = response.json()
        expected = sorted((row[0][7:], row[3]) for row in rows)
        assert google_consumer_view(body) == expected
        assert 'nextPageToken' not in body
        minimal = {'models': [{key: row[key] for key in ('name', 'displayName', 'supportedGenerationMethods')} for row in body['models']]}
        assert google_consumer_view(minimal) == expected
        assert len(body['models']) == 19
    assert all(not req.url.path.endswith('/GenerateContent') for req in transport)


def test_native_page_token_remains_available_without_claiming_rikka_follows_it(client_factory):
    # The consumer's single pageSize=100 request is sufficient for today's 19;
    # a future larger catalog needs a client that follows nextPageToken.
    client, _ = catalog_client(client_factory, synthetic_rows(101))
    with TestClient(create_app(client)) as api:
        headers = {'x-goog-api-key': client.settings.password}
        first = api.get('/v1beta/models?pageSize=100', headers=headers).json()
        assert len(google_consumer_view(first)) == 100 and first['nextPageToken']
        second = api.get('/v1beta/models', params={'pageSize': 100, 'pageToken': first['nextPageToken']}, headers=headers).json()
        assert len(google_consumer_view(second)) == 1
        assert len(set(google_consumer_view(first) + google_consumer_view(second))) == 101


def test_capability_enrichment_does_not_claim_rikka_imported_its_badges():
    row = synthetic_rows()[1]
    public = openai_model(parse_model_row(row))
    assert public['architecture']['output_modalities'] == ['text', 'image']
    assert public['pg_proxy']['proxy']['interfaces']['openai_chat_completions']['client_must_preserve_signatures'] is True
    modified = copy.deepcopy(public)
    modified['architecture'] = {'input_modalities': ['document'], 'output_modalities': ['audio']}
    modified['supported_parameters'] = ['fictional_parameter']
    modified['pg_proxy'] = {'ignored_by_this_list_reader': True}
    assert openai_consumer_view({'data': [public]}) == openai_consumer_view({'data': [modified]})


def model(codes, *, minimum=0, maximum=1024, can_disable=True, levels=()):
    row = model_row('models/synthetic-advertisement-contract')
    row[64] = list(codes)
    row[71] = [128, maximum, minimum, can_disable, None, 2, list(levels)]
    return parse_model_row(row)


def accepts(entry, **options):
    try:
        _, payload = openai_request_to_gemini({'model': entry['baseModelId'], 'messages': [{'role': 'user', 'content': 'Synthetic'}], **options})
        encode_generate(entry['baseModelId'], payload, entry)
    except PlaygroundError as error:
        assert error.status == 400
        return False
    return True


@pytest.mark.parametrize('entry', [
    model([1]),
    model([1, 25]),
    model([1, 25, 35], minimum=16, can_disable=False),
    model([1, 25, 35], minimum=16, can_disable=True),
    model([1, 25, 35], minimum=0, can_disable=False),
    model([1, 25, 52]),
    model([1, 25, 52], levels=[1, 3]),
])
def test_advertised_reasoning_effort_has_at_least_one_encodable_value(entry):
    parameters = openai_model(entry)['supported_parameters']
    supported = [effort for effort in ('none', 'minimal', 'low', 'medium', 'high') if accepts(entry, reasoning_effort=effort)]
    assert ('reasoning_effort' in parameters) == bool(supported)
    if 25 in entry['_codes']:
        assert 'thinking_config' in parameters
        assert accepts(entry, thinking_config={'includeThoughts': True})


@pytest.mark.parametrize('codes', [[1], [1, 21], [1, 21, 47], [1, 21, 49], [1, 21, 47, 49]])
def test_advertised_image_config_has_a_real_adjustable_option(codes):
    entry = model(codes)
    parameters = openai_model(entry)['supported_parameters']
    adjustable = accepts(entry, image_config={'aspectRatio': '16:9'}) or accepts(entry, image_config={'imageSize': '1K'})
    assert ('image_config' in parameters) == adjustable
    if 21 in codes:
        assert 'modalities' in parameters
        assert accepts(entry, modalities=['text', 'image'])


@pytest.mark.parametrize('name,code', [('code_execution', 9), ('google_search', 12), ('url_context', 13), ('image_search', 54), ('google_maps', 58)])
def test_advertised_builtin_tool_is_accepted_by_actual_openai_and_native_encoder(name, code):
    entry = model([1, code])
    interface = openai_model(entry)['pg_proxy']['proxy']['interfaces']['openai_chat_completions']
    assert interface['tools'] == [name]
    assert accepts(entry, tools=[{'type': name}])
    assert not accepts(model([1]), tools=[{'type': name}])


def test_advertised_function_selection_excludes_unsupported_constraints():
    entry = model([1, 10])
    public = openai_model(entry)
    interface = public['pg_proxy']['proxy']['interfaces']['openai_chat_completions']
    tool = {'type': 'function', 'function': {'name': 'inspect', 'parameters': {'type': 'object'}}}
    assert interface['function_calling'] is True
    assert interface['tool_choice_modes'] == ['auto', 'none']
    assert interface['parallel_tool_calls_values'] == [True]
    for mode in interface['tool_choice_modes']:
        assert accepts(entry, tools=[tool], tool_choice=mode)
    assert accepts(entry, tools=[tool], parallel_tool_calls=True)
    assert not accepts(entry, tools=[tool], tool_choice='required')
    assert not accepts(entry, tools=[tool], parallel_tool_calls=False)


def test_modality_ads_distinguish_native_media_from_openai_extensions():
    row = model_row('models/gemini-4-argon')
    public = openai_model(parse_model_row(row))
    native_interface = public['pg_proxy']['proxy']['interfaces']['gemini_generate_content']
    interface = public['pg_proxy']['proxy']['interfaces']['openai_chat_completions']
    assert public['architecture']['input_modalities'] == interface['input_modalities'] == ['text', 'image', 'audio']
    assert native_interface['input_modalities'] == ['text', 'image', 'audio', 'video', 'document']
    assert interface['unsupported_input_modalities'] == ['video', 'document']
    assert interface['remote_media_download'] is False
    speech = openai_model(model([1, 37]))
    assert speech['architecture']['output_modalities'] == []
    assert speech['pg_proxy']['proxy']['interfaces']['gemini_generate_content']['output_modalities'] == ['audio']
    speech_openai = speech['pg_proxy']['proxy']['interfaces']['openai_chat_completions']
    assert speech_openai['standard_audio_output'] is False
    assert 'audio_chunks' in speech_openai['nonstandard_output_fields']
    assert speech['pg_proxy']['proxy']['metadata_is_generation_verification'] is False
