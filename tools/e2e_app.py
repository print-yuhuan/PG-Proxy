"""Exercise an already-running PG Proxy app API, optionally with real generation.

The app must already be configured and started. Use adb forward or a LAN address;
this tool never starts Termux or a replacement desktop backend. Credentials are
read from a private JSON connection file or PG_PROXY_API_KEY, never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlparse

import httpx


class CheckFailed(Exception):
    pass


def require(value, code):
    if not value:
        raise CheckFailed(code)


def success(response):
    if response.status_code != 200:
        raise CheckFailed(f"http_{response.status_code}")
    return response.json()


def native_text(body):
    return "".join(p.get("text", "") for c in body.get("candidates", [])
                   for p in c.get("content", {}).get("parts", []) if not p.get("thought"))


async def run(args):
    connection = {}
    if args.connection_file:
        source = Path(args.connection_file)
        require(source.stat().st_size <= 65536, "connection_file_too_large")
        connection = json.loads(source.read_text(encoding="utf-8-sig"))
    key = os.environ.get("PG_PROXY_API_KEY") or connection.get("apiKey")
    require(isinstance(key, str) and key, "missing_private_api_key")
    parsed = urlparse(args.base_url)
    require(parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment, "invalid_base_url")
    report = {"tested_at_utc": datetime.now(timezone.utc).isoformat(), "ok": False, "steps": [],
              "existing_server_only": True, "desktop_backend_started": False, "termux_started": False,
              "real_generation_requested": args.live, "authenticated_generation_verified": False,
              "limitations": ["APK process identity and worker cleanup require the separate instrumentation report",
                              "WAA refresh requires the separate private-control instrumentation test",
                              "Single-account HTTP tests do not verify real multi-account rotation"]}

    async def step(name, fn):
        started = time.monotonic()
        report["current_step"] = name
        metadata = await fn()
        report["steps"].append({"name": name, "ok": True, "seconds": round(time.monotonic() - started, 3), **(metadata or {})})
        print("PASS " + name, flush=True)

    headers = {"Authorization": "Bearer " + key}
    try:
        async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), trust_env=False,
                                    timeout=httpx.Timeout(args.timeout, connect=15)) as http:
            models = []
            native_models = []

            async def health():
                root = success(await http.get("/"))
                require(root.get("name") in ("PG Proxy", "PG-Proxy"), "unexpected_server")
                require(success(await http.get("/health")).get("status") == "running", "health_not_running")
                return {"service": root["name"], "version": root.get("version")}
            await step("service_identity_and_health", health)

            async def catalog():
                nonlocal models, native_models
                body = success(await http.get("/v1/models", headers=headers))
                models = [item["id"] for item in body["data"]]
                require(models, "empty_live_catalog")
                native_models = success(await http.get("/v1beta/models", headers=headers))["models"]
                require(set(models) == {m["name"].removeprefix("models/") for m in native_models}, "catalog_mismatch")
                return {"model_count": len(models)}
            await step("real_account_catalog_and_native_catalog", catalog)

            async def auth():
                variants = [
                    ({"x-goog-api-key": key}, {}),
                    ({}, {"key": key}),
                    ({"Authorization": "Basic " + base64.b64encode(("local:" + key).encode()).decode()}, {}),
                ]
                for auth_headers, params in variants:
                    success(await http.get("/v1/models", headers=auth_headers, params=params))
                require((await http.get("/v1/models")).status_code == 401, "missing_key_accepted")
                require((await http.get("/v1/models", headers={"Authorization": "Bearer invalid-key"})).status_code == 401,
                        "incorrect_key_accepted")
                return {"methods": 4}
            await step("four_authentication_methods", auth)

            async def local_errors():
                response = await http.post("/v1/chat/completions", headers=headers, content="{")
                require(response.status_code == 400, "malformed_json_not_rejected")
                response = await http.post("/v1/chat/completions", headers=headers, json={
                    "model": "pg-proxy-test-no-such-model", "messages": [{"role": "user", "content": "OK"}]})
                require(response.status_code == 404, "unknown_model_not_rejected")
                for path in ("/accounts", "/admin/accounts", "/session"):
                    require((await http.post(path, headers=headers, json={})).status_code == 404, "management_route_exposed")
            await step("local_errors_and_management_separation", local_errors)

            if args.live:
                choices = [m for m in models if not any(word in m.lower() for word in ("image", "audio", "tts", "transcribe"))]
                selected = args.model or connection.get("model") or next((m for m in choices if "flash" in m), None)
                require(selected in models, "no_supported_text_model_selected")
                require(re.fullmatch(r"[A-Za-z0-9._/-]+", selected) is not None, "invalid_model_identifier")
                report["model"] = selected
                cap = next((int(m.get("outputTokenLimit", 1024)) for m in native_models
                            if m["name"].removeprefix("models/") == selected), 1024)
                maximum = min(1024, cap)
                payload = {"contents": [{"role": "user", "parts": [{"text": "Reply exactly OK."}]}],
                           "generationConfig": {"maxOutputTokens": maximum}}
                openai = {"model": selected, "messages": [{"role": "user", "content": "Reply exactly OK."}], "max_tokens": maximum}

                async def native_unary():
                    result = success(await http.post(f"/v1beta/models/{selected}:generateContent", headers=headers, json=payload))
                    text = native_text(result)
                    require(text and any(c.get("finishReason") for c in result.get("candidates", [])), "native_response_incomplete")
                    report["authenticated_generation_verified"] = True
                    return {"visible_text_length": len(text)}
                await step("real_native_generation", native_unary)

                async def openai_unary():
                    result = success(await http.post("/v1/chat/completions", headers=headers, json=openai))
                    choice = result["choices"][0]
                    text = choice["message"].get("content")
                    require(text and choice.get("finish_reason"), "openai_response_incomplete")
                    return {"visible_text_length": len(text)}
                await step("real_openai_generation", openai_unary)

                async def streaming(path, body, openai_format):
                    length = count = 0
                    finished = done = False
                    first_token = None
                    started = time.monotonic()
                    async with http.stream("POST", path, headers=headers, json=body) as response:
                        require(response.status_code == 200, f"http_{response.status_code}")
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                done = True
                                continue
                            event = json.loads(data)
                            require("error" not in event, "stream_error")
                            count += 1
                            if openai_format:
                                text = "".join(c.get("delta", {}).get("content", "") for c in event.get("choices", []))
                                finished |= any(c.get("finish_reason") for c in event.get("choices", []))
                            else:
                                text = native_text(event)
                                finished |= any(c.get("finishReason") for c in event.get("candidates", []))
                            length += len(text)
                            if text and first_token is None:
                                first_token = time.monotonic() - started
                    require(length and finished and (done or not openai_format), "stream_incomplete")
                    return {"frames": count, "visible_text_length": length, "first_token_seconds": round(first_token, 3)}
                await step("real_native_sse", lambda: streaming(f"/v1beta/models/{selected}:streamGenerateContent?alt=sse", payload, False))
                await step("real_openai_sse", lambda: streaming("/v1/chat/completions", {**openai, "stream": True}, True))

                async def native_json_stream():
                    frames = success(await http.post(f"/v1/models/{selected}:streamGenerateContent", headers=headers, json=payload))
                    require(isinstance(frames, list), "native_json_stream_not_array")
                    length = sum(len(native_text(frame)) for frame in frames)
                    require(length and any(c.get("finishReason") for frame in frames for c in frame.get("candidates", [])),
                            "native_json_stream_incomplete")
                    return {"frames": len(frames), "visible_text_length": length}
                await step("real_native_json_stream", native_json_stream)

                async def disconnect():
                    received = False
                    async with http.stream("POST", f"/v1beta/models/{selected}:streamGenerateContent?alt=sse", headers=headers, json=payload) as response:
                        require(response.status_code == 200, f"http_{response.status_code}")
                        async for line in response.aiter_lines():
                            if line.startswith("data:"):
                                received = True
                                break
                    require(received, "no_frame_before_disconnect")
                    success(await http.get("/v1/models", headers=headers))
                    return {"closed_stream_early": True, "subsequent_catalog_request_completed": True}
                await step("real_stream_disconnect_and_subsequent_request", disconnect)
            report["ok"] = True
            report.pop("current_step", None)
    except Exception as error:
        report["failed_step"] = report.pop("current_step", "configuration")
        report["error"] = str(error) if isinstance(error, CheckFailed) else type(error).__name__
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False), flush=True)
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080", help="An existing app API over adb forward or LAN")
    parser.add_argument("--connection-file", help="Private JSON file containing apiKey and optional model; never commit this file")
    parser.add_argument("--model")
    parser.add_argument("--live", action="store_true", help="Perform real model generation (uses account quota)")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--output", default="artifacts/app-e2e.json")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as error:
        # Configuration errors may contain URLs or private values: print only a safe class/code.
        print(str(error) if isinstance(error, CheckFailed) else type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
