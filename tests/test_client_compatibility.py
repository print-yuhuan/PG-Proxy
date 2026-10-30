"""Independent HTTP compatibility contracts with an entirely synthetic upstream.

These tests exercise PG Proxy's actual request/response adapters and serialized
Playground wire body. They do not run desktop/mobile clients or contact Google.
Client-specific shapes are observational contracts, not copied client code.

Source snapshots inspected on 2026-10-30 (no upstream implementation imported):
- Cherry Studio 7643520c6db0695a7800edc0901b1d32235d4d50,
  src/main/ai/runtime/aiSdk/params/customParamsFetch.ts: custom POST JSON fields
  are merged with the SDK body, so unrecognized user options can reach a server.
- RikkaHub a5db9c9b770f316c76136970aa01a9d790e10e51,
  ai/src/main/java/me/rerere/ai/provider/providers/openai/ChatCompletionsAPI.kt:
  top_p, max_tokens, stream, stream_options.include_usage and reasoning_effort.
- Kelivo ca2721e07b31cc8acc331c7a9f400109f8c1e298,
  lib/core/services/api/providers/openai/chat_completions_api.dart: message names,
  assistant tool history/reasoning and tool_choice=auto in follow-up requests.
- SillyTavern 06bde939fb1e9c4c8d8641d810f0a916b5bce127,
  src/endpoints/backends/chat-completions.js: common sampling fields, logit_bias,
  n, tools/tool_choice and JSON-schema strict=true default.
"""
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import model_row
from pgproxy.main import create_app


def chat(**changes):
    return {"model": "test-chat", "messages": [{"role": "user", "content": "Hello"}], **changes}


def headers(client):
    return {"Authorization": "Bearer " + client.settings.password}


def generations(requests):
    return [json.loads(request.content) for request in requests if request.url.path.endswith("/GenerateContent")]


def assert_completion(response, model="test-chat"):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == model
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "OK"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 6


def client_shape(client_name, stream):
    """Small independent examples of fields observed in the cited request paths."""
    body = chat(stream=stream)
    if client_name == "Cherry Studio":
        body.update(temperature=0.4, max_tokens=64, top_k=45, min_p=0.1,
                    repetition_penalty=1.1, metadata={"custom": "synthetic"},
                    custom_option={"future": True})
    elif client_name == "RikkaHub":
        body.update(top_p=0.85, max_tokens=200, reasoning_effort="high",
                    stream_options={"include_usage": True})
    elif client_name == "Kelivo":
        body.update(messages=[
            {"role": "user", "name": "ClientUser", "content": "Weather?"},
            {"role": "assistant", "content": None, "reasoning_content": "Check the weather tool.",
             "tool_calls": [{"id": "call_weather", "type": "function", "function": {"name": "weather", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_weather", "name": "weather", "content": "Sunny"},
        ], tools=[{"type": "function", "function": {"name": "weather", "parameters": {"type": "object", "properties": {}}}}],
                    tool_choice="auto")
    elif client_name == "SillyTavern":
        body.update(temperature=0.8, max_tokens=128, presence_penalty=0.1,
                    frequency_penalty=0.2, top_p=0.9, top_k=40,
                    logit_bias={"123": 1}, seed=42, n=3,
                    response_format={"type": "json_schema", "json_schema": {
                        "name": "reply", "strict": True,
                        "schema": {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}}})
    return body


@pytest.mark.parametrize("client_name", ["Cherry Studio", "RikkaHub", "Kelivo", "SillyTavern"])
@pytest.mark.parametrize("stream", [False, True])
def test_observed_client_request_shapes_use_real_proxy_adapters(client_factory, client_name, stream):
    def upstream(request):
        if request.url.path.endswith("/ListModels"):
            row = model_row()
            row[64].append(10)
            return httpx.Response(200, json=[[row]])

    client, requests = client_factory(extra_handler=upstream)
    with TestClient(create_app(client)) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=client_shape(client_name, stream))
    assert response.status_code == 200, response.text
    if stream:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
        assert "".join(choice["delta"].get("content", "") for event in events for choice in event["choices"]) == "OK"
        assert response.text.endswith("data: [DONE]\n\n")
        if client_name == "RikkaHub":
            assert events[-1]["usage"]["total_tokens"] == 6
    else:
        assert_completion(response)
    wire, = generations(requests)
    if client_name == "Cherry Studio":
        assert wire[3][3] == 64 and wire[3][6] == 45
        assert "custom_option" not in json.dumps(wire)
    elif client_name == "RikkaHub":
        assert wire[3][3] == 200 and wire[3][5] == 0.85
    elif client_name == "Kelivo":
        assert wire[1][2][0][0][11][2] == "call_weather"
        assert "weather" in json.dumps(wire[6])
    elif client_name == "SillyTavern":
        assert wire[3][3] == 128 and wire[3][13] == 1
        assert wire[3][7] == "application/json" and "reply" in json.dumps(wire[3][8])
    assert not client.lock.locked() and client.closed


def test_optional_provider_fields_are_ignored_without_losing_supported_generation_options(client_factory):
    client, requests = client_factory()
    body = chat(temperature=0.4, top_p=0.75, max_tokens=128, stop=["END"], n=4,
                presence_penalty=0.2, frequency_penalty=0.3, logit_bias={"123": 2},
                logprobs=True, top_logprobs=5, user="synthetic-client-user",
                metadata={"conversation": "synthetic"}, service_tier="auto", store=False,
                cache_control={"type": "ephemeral"}, future_provider_parameter={"opaque": True})
    original = copy.deepcopy(body)
    with TestClient(create_app(client)) as api:
        assert_completion(api.post("/v1/chat/completions", headers=headers(client), json=body))
    assert body == original
    wire, = generations(requests)
    assert wire[1][0][0][0][1] == "Hello"
    assert wire[3][1] == ["END"]
    assert wire[3][3:6] == [128, 0.4, 0.75]
    assert wire[3][13] == 1
    assert "synthetic-client-user" not in json.dumps(wire)
    assert client.closed and client.http.is_closed


@pytest.mark.parametrize("stream", [False, True])
def test_null_optional_settings_do_not_break_request_or_stream_selection(client_factory, stream):
    client, requests = client_factory()
    body = chat(stream=stream, stream_options=None, tools=None, tool_choice=None,
                parallel_tool_calls=None, temperature=None, top_p=None, top_k=None,
                max_tokens=None, max_completion_tokens=None, stop=None, seed=None,
                response_format=None, reasoning_effort=None, thinking_config=None,
                modalities=None, image_config=None)
    with TestClient(create_app(client)) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=body)
    assert response.status_code == 200, response.text
    if stream:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text.count("data: [DONE]") == 1
    else:
        assert_completion(response)
    assert len(generations(requests)) == 1


def test_stream_options_extensions_preserve_usage_and_valid_sse_contract(client_factory):
    client, requests = client_factory()
    body = chat(stream=True, stream_options={"include_usage": True, "include_obfuscation": True},
                presence_penalty=0, frequency_penalty=0, logprobs=False)
    with TestClient(create_app(client)) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=body)
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
    assert len({event["id"] for event in events}) == 1
    assert all(event["object"] == "chat.completion.chunk" for event in events)
    assert events[0]["choices"][0]["delta"]["role"] == "assistant"
    assert "".join(choice["delta"].get("content", "") for event in events for choice in event["choices"]) == "OK"
    assert [choice["finish_reason"] for event in events for choice in event["choices"] if choice["finish_reason"]] == ["stop"]
    assert events[-1]["choices"] == [] and events[-1]["usage"]["total_tokens"] == 6
    assert response.text.endswith("data: [DONE]\n\n")
    assert len(generations(requests)) == 1
    assert not client.lock.locked()


def test_message_names_and_optional_metadata_do_not_discard_conversation(client_factory):
    client, requests = client_factory()
    body = chat(messages=[
        {"role": "system", "name": "Narrator", "content": "Follow the story.", "cache_control": {"type": "ephemeral"}},
        {"role": "user", "name": "Player", "content": [{"type": "text", "text": "Hello", "cache_control": {"type": "ephemeral"}}], "timestamp": 123},
        {"role": "assistant", "name": "Character", "content": "Welcome.", "refusal": None, "annotations": []},
        {"role": "user", "name": "Player", "content": "Continue."},
    ])
    with TestClient(create_app(client)) as api:
        assert_completion(api.post("/v1/chat/completions", headers=headers(client), json=body))
    wire, = generations(requests)
    assert [content[1] for content in wire[1]] == ["user", "model", "user"]
    assert [content[0][0][1] for content in wire[1]] == ["Hello", "Welcome.", "Continue."]
    assert wire[5][0][0][1] == "Follow the story."


def test_missing_model_and_message_role_use_live_catalog_and_safe_defaults(client_factory):
    client, requests = client_factory()
    with TestClient(create_app(client)) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json={"messages": [{"content": "Hello"}]})
    assert_completion(response)
    wire, = generations(requests)
    assert wire[0] == "models/test-chat"
    assert wire[1][0][1] == "user"
    assert any(request.url.path.endswith("/ListModels") for request in requests)


def test_default_model_comes_from_online_text_catalog_not_image_first_or_fixed_name(client_factory):
    image = model_row("models/synthetic-image-first")
    image[64].append(21)
    text = model_row("models/synthetic-text-only")

    def upstream(request):
        if request.url.path.endswith("/ListModels"):
            return httpx.Response(200, json=[[image, text]])

    client, requests = client_factory(extra_handler=upstream)
    with TestClient(create_app(client)) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json={"messages": [{"role": "user", "content": "Hello"}]})
    assert_completion(response, "synthetic-text-only")
    assert generations(requests)[0][0] == "models/synthetic-text-only"


def test_latest_token_limit_parameter_wins_without_rejecting_legacy_parameter(client_factory):
    client, requests = client_factory()
    with TestClient(create_app(client)) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=chat(max_tokens=100, max_completion_tokens=200))
    assert_completion(response)
    assert generations(requests)[0][3][3] == 200


@pytest.mark.parametrize("selection", ["required", {"type": "function", "function": {"name": "weather"}}])
def test_unenforceable_tool_hints_fall_back_while_function_declaration_survives(client_factory, selection):
    def upstream(request):
        if request.url.path.endswith("/ListModels"):
            row = model_row()
            row[64].append(10)
            return httpx.Response(200, json=[[row]])

    client, requests = client_factory(extra_handler=upstream)
    body = chat(tools=[{"type": "function", "function": {"name": "weather", "strict": True,
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "additionalProperties": False}}}],
                tool_choice=selection, parallel_tool_calls=False)
    with TestClient(create_app(client)) as api:
        assert_completion(api.post("/v1/chat/completions", headers=headers(client), json=body))
    wire, = generations(requests)
    assert wire[6] and "weather" in json.dumps(wire[6])
    assert "city" in json.dumps(wire[6])


def test_missing_tool_type_and_arguments_are_filled_without_fabricating_signature(client_factory):
    def upstream(request):
        if request.url.path.endswith("/ListModels"):
            row = model_row()
            row[64].append(10)
            return httpx.Response(200, json=[[row]])

    client, requests = client_factory(extra_handler=upstream)
    body = chat(messages=[
        {"role": "user", "content": "Check the weather."},
        {"role": "assistant", "tool_calls": [{"id": "call_weather", "function": {"name": "weather"}}]},
        {"role": "tool", "tool_call_id": "call_weather", "content": "Sunny"},
    ], tools=[{"function": {"name": "weather"}}])
    with TestClient(create_app(client)) as api:
        assert_completion(api.post("/v1/chat/completions", headers=headers(client), json=body))
    wire, = generations(requests)
    part = wire[1][1][0][0]
    assert part[10][0] == "weather" and part[10][2] == "call_weather"
    assert len(part) < 15 or part[14] is None
    assert wire[1][2][0][0][11][2] == "call_weather"


def test_json_schema_strict_hint_degrades_but_schema_and_json_output_remain(client_factory):
    client, requests = client_factory()
    body = chat(response_format={"type": "json_schema", "json_schema": {
        "strict": True, "schema": {"type": "object", "properties": {"answer": {"type": "string"}},
                                   "required": ["answer"], "additionalProperties": False}}})
    with TestClient(create_app(client)) as api:
        assert_completion(api.post("/v1/chat/completions", headers=headers(client), json=body))
    wire, = generations(requests)
    assert wire[3][7] == "application/json"
    assert wire[3][8] and "answer" in json.dumps(wire[3][8])


def test_high_image_detail_hint_does_not_drop_inline_image(client_factory):
    client, requests = client_factory()
    body = chat(messages=[{"role": "user", "content": [
        {"type": "text", "text": "Describe"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA==", "detail": "high"}},
    ]}])
    with TestClient(create_app(client)) as api:
        assert_completion(api.post("/v1/chat/completions", headers=headers(client), json=body))
    wire, = generations(requests)
    assert wire[1][0][0][1][2] == ["image/png", "eA=="]


@pytest.mark.parametrize("body", [
    {},
    {"model": "test-chat"},
    chat(messages=[]),
    chat(messages=[{"role": "user"}]),
    chat(messages=[{"role": "user", "content": None}]),
    chat(messages=[{"role": "tool", "tool_call_id": "missing", "content": "no matching call"}]),
    chat(messages=[{"role": "assistant", "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "weather", "arguments": "{}"}}]},
                   {"role": "tool", "tool_call_id": "call_b", "content": "wrong id"}]),
    chat(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!"}}]}]),
    chat(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.test/private-image"}}]}]),
])
def test_unrecoverable_content_still_returns_controlled_error_without_generation(client_factory, body):
    client, requests = client_factory()
    with TestClient(create_app(client)) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=body)
    assert response.status_code == 400, response.text
    assert isinstance(response.json()["error"]["message"], str)
    assert not generations(requests)


def test_bad_json_and_missing_auth_do_not_become_default_generation(client_factory):
    client, requests = client_factory()
    with TestClient(create_app(client)) as api:
        assert api.post("/v1/chat/completions", json=chat(metadata={"ignore_me": True})).status_code == 401
        assert not requests
        response = api.post("/v1/chat/completions", headers={**headers(client), "Content-Type": "application/json"}, content='{ "model": ')
        assert response.status_code == 400
    assert not generations(requests)


def test_explicit_unknown_model_is_not_replaced_by_default(client_factory):
    client, requests = client_factory()
    with TestClient(create_app(client)) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=chat(model="explicitly-missing", metadata={"ignore_me": True}))
    assert response.status_code == 404
    assert not generations(requests)


@pytest.mark.parametrize("field", [
    "future_parameter", "metadata", "n", "stream_options", "temperature", "top_p",
    "top_k", "max_tokens", "seed", "response_format", "reasoning_effort", "thinking_config",
    "tools", "tool_choice", "parallel_tool_calls", "modalities", "image_config",
    "speech_config", "transcription_config", "media_resolution", "stop",
])
def test_optional_parameter_json_type_matrix_never_retries_or_returns_500(client_factory, field):
    # Deterministic JSON types include values which historically raised unhashable
    # type / .get / overflow errors. There is no non-JSON NaN or Python cycle here.
    values = [None, False, True, 0, -1, 10**400, 1e308, "unsupported", [], {}, [None, {}], {"unknown": []}]
    client, requests = client_factory()
    with TestClient(create_app(client), raise_server_exceptions=False) as api:
        for value in values:
            before = len(generations(requests))
            response = api.post("/v1/chat/completions", headers=headers(client), json=chat(**{field: value}))
            assert response.status_code == 200, (field, type(value).__name__, response.text)
            assert len(generations(requests)) == before + 1
            assert not client.lock.locked()


def deep_schema(depth):
    schema = {"type": "string"}
    for _ in range(depth):
        schema = {"type": "object", "properties": {"child": schema}, "required": ["child"]}
    return schema


@pytest.mark.parametrize("schema", [
    deep_schema(70),
    {"$ref": "#/$defs/node", "$defs": {"node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/node"}}}}},
    {"$ref": "#/$defs/a", "$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}},
    {"$ref": "https://example.test/private-schema"},
    {"type": "object", "properties": {"answer": {"type": "string", "unknown": [False, {}]}},
     "required": ["answer", "missing"], "additionalProperties": False},
])
def test_deep_recursive_or_unsupported_schema_constraints_degrade_without_fetch_or_retry(client_factory, schema):
    client, requests = client_factory()
    with TestClient(create_app(client), raise_server_exceptions=False) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=chat(response_format={
            "type": "json_schema", "json_schema": {"strict": True, "schema": schema}}))
    assert_completion(response)
    assert len(generations(requests)) == 1
    assert all(request.url.host != "example.test" for request in requests)


def test_vendor_fields_do_not_change_supplied_tool_signature_id_or_arguments(client_factory):
    signature = "SYNTHETIC_OPAQUE_SIGNATURE_DO_NOT_REWRITE"

    def upstream(request):
        if request.url.path.endswith("/ListModels"):
            row = model_row()
            row[64].append(10)
            return httpx.Response(200, json=[[row]])

    client, requests = client_factory(extra_handler=upstream)
    body = chat(messages=[
        {"role": "user", "content": "Weather?", "client_metadata": {"discard": True}},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "caller-supplied-id", "type": "function", "index": 0,
            "function": {"name": "weather", "arguments": '{"city":"Paris","days":2}', "unknown": True},
            "extra_content": {"other_provider": {"discard": True}, "google": {"thought_signature": signature, "unknown": 1}},
        }]},
        {"role": "tool", "tool_call_id": "caller-supplied-id", "content": '{"result":"sunny"}'},
    ], tools=[{"type": "function", "function": {"name": "weather"}}], unknown={"remove": True})
    with TestClient(create_app(client)) as api:
        assert_completion(api.post("/v1/chat/completions", headers=headers(client), json=body))
    wire, = generations(requests)
    part = wire[1][1][0][0]
    assert part[14] == signature
    assert part[10][2] == "caller-supplied-id"
    assert "Paris" in json.dumps(part[10][1])
    assert wire[1][2][0][0][11][2] == "caller-supplied-id"


def test_ambiguous_missing_tool_response_id_is_not_guessed(client_factory):
    client, requests = client_factory()
    body = chat(messages=[
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call-a", "type": "function", "function": {"name": "weather", "arguments": "{}"}},
            {"id": "call-b", "type": "function", "function": {"name": "weather", "arguments": "{}"}},
        ]},
        {"role": "tool", "name": "weather", "content": "Which call?"},
    ], unknown_optional=True)
    with TestClient(create_app(client), raise_server_exceptions=False) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=body)
    assert response.status_code == 400
    assert not generations(requests)


@pytest.mark.parametrize("field", [
    "candidateCount", "maxOutputTokens", "temperature", "topP", "topK", "seed",
    "stopSequences", "responseMimeType", "responseSchema", "responseJsonSchema",
    "responseModalities", "thinkingConfig", "imageConfig", "speechConfig",
    "transcriptionConfig", "mediaResolution", "futureOption",
])
def test_native_optional_json_type_matrix_keeps_one_generation(client_factory, field):
    values = [None, False, True, 0, -1, 10**400, 1e308, "unsupported", [], {}, [None, {}], {"unknown": []}]
    client, requests = client_factory()
    with TestClient(create_app(client), raise_server_exceptions=False) as api:
        for value in values:
            before = len(generations(requests))
            body = {"contents": [{"parts": [{"text": "Hello"}]}], "generationConfig": {field: value},
                    "futureRequestOption": value}
            response = api.post("/v1beta/models/test-chat:generateContent", headers=headers(client), json=body)
            assert response.status_code == 200, (field, type(value).__name__, response.text)
            assert len(generations(requests)) == before + 1
            assert not client.lock.locked()


def test_bad_native_optional_function_declarations_do_not_remove_valid_tool_or_prompt(client_factory):
    def upstream(request):
        if request.url.path.endswith("/ListModels"):
            row = model_row()
            row[64].append(10)
            return httpx.Response(200, json=[[row]])

    client, requests = client_factory(extra_handler=upstream)
    body = {"contents": [{"parts": [{"text": "Hello"}]}], "tools": [{"functionDeclarations": [
        {"name": None}, {"name": {}}, {"name": []}, {"name": "bad name"},
        {"name": "weather", "parameters": {"type": "object", "properties": {}}},
    ]}]}
    with TestClient(create_app(client), raise_server_exceptions=False) as api:
        response = api.post("/v1beta/models/test-chat:generateContent", headers=headers(client), json=body)
    assert response.status_code == 200, response.text
    wire, = generations(requests)
    assert wire[1][0][0][0][1] == "Hello"
    assert "weather" in json.dumps(wire[6])
    assert "bad name" not in json.dumps(wire[6])


@pytest.mark.parametrize("signature", ["", False, 1, {}, []])
def test_invalid_supplied_signature_is_not_repaired_or_silently_discarded(client_factory, signature):
    client, requests = client_factory()
    body = chat(messages=[
        {"role": "assistant", "content": "Earlier answer", "extra_content": {"google": {"thought_signature": signature}}},
        {"role": "user", "content": "Continue"},
    ], unknown_optional=True)
    with TestClient(create_app(client), raise_server_exceptions=False) as api:
        response = api.post("/v1/chat/completions", headers=headers(client), json=body)
    assert response.status_code == 400, response.text
    assert not generations(requests)
