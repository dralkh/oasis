"""End-to-end integration, API contract, concurrency, and security tests for Proxy Mode.

Includes:
1. Mock management backend tests (always executed, zero external binaries required).
2. Full end-to-end tests against the real compiled CLIProxyAPI-Rust binary + local
   mock upstream HTTP/SSE server (executed whenever the binary is available).
"""

from __future__ import annotations

import concurrent.futures
import http.server
import json
import socket
import socketserver
import stat
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from conftest import find_real_proxy_binary, free_port


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# 1. Mock management backend tests (always run)
# ---------------------------------------------------------------------------


def test_mock_management_auth_pin_unpin_and_error_redaction(proxy_mod: Any) -> None:
    state = proxy_mod.load_state(create_if_missing=True)
    assert state is not None
    port = free_port()
    state["port"] = port
    proxy_mod.save_state_and_render(state)
    mgmt_key = state["management_key"]

    accounts_store = [
        {
            "id": "codex-a.json",
            "provider": "codex",
            "label": "alice@example.com",
            "email": "alice@example.com",
            "kind": "oauth",
            "disabled": False,
            "cooldowns": {},
            "last_error": f"Bearer {mgmt_key} sk-secret-leak-999999",
            "counters": {"requests": 5, "failures": 1, "input_tokens": 100, "output_tokens": 50, "cache_tokens": 20},
            "quota": {},
            "models": ["gpt-6-astra"],
        },
        {
            "id": "codex-b.json",
            "provider": "codex",
            "label": "bob@example.com",
            "email": "bob@example.com",
            "kind": "oauth",
            "disabled": False,
            "cooldowns": {},
            "last_error": None,
            "counters": {"requests": 2, "failures": 0, "input_tokens": 40, "output_tokens": 20, "cache_tokens": 0},
            "quota": {},
            "models": ["gpt-6-astra"],
        },
    ]
    revision = "rev-1"

    class MockMgmt(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
            pass

        def _send(self, code: int, body: Any) -> None:
            raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json" if not isinstance(body, str) else "text/plain")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _check_auth(self) -> bool:
            if self.headers.get("Authorization") != f"Bearer {mgmt_key}":
                self._send(401, {"error": "management key required"})
                return False
            return True

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send(200, "ok")
                return
            if not self._check_auth():
                return
            if self.path == "/api/overview":
                self._send(200, {
                    "version": "0.3.2",
                    "uptime_secs": 42,
                    "client_keys": [state["client_api_key"]],
                    "routing": "smart-quota",
                    "session_affinity": True,
                    "active": 1,
                    "totals": {"requests": 7, "ok": 6, "failed": 1, "input_tokens": 140, "output_tokens": 70, "cache_tokens": 20},
                })
            elif self.path == "/api/accounts":
                self._send(200, accounts_store)
            elif self.path == "/api/models":
                self._send(200, [{"id": "gpt-6-astra", "provider": "codex"}])
            elif self.path == "/api/requests":
                self._send(200, [{"id": 1, "client": "responses", "provider": "codex", "model": "gpt-6-astra", "status": 200, "latency_ms": 120, "error": "Bearer super_secret_987654"}])
            elif self.path == "/api/config/settings":
                self._send(200, {"revision": revision, "values": {}})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if not self._check_auth():
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if self.path.startswith("/api/accounts/") and self.path.endswith("/toggle"):
                aid = self.path.split("/")[3]
                for acc in accounts_store:
                    if acc["id"] == aid:
                        acc["disabled"] = bool(body.get("disabled"))
                        self._send(200, {"ok": True})
                        return
                self._send(404, {"error": "unknown"})
                return
            self._send(200, {"ok": True})

        def do_PATCH(self) -> None:
            if not self._check_auth():
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if body.get("revision") != revision:
                self._send(409, {"error": "stale revision"})
                return
            self._send(200, {"ok": True, "revision": "rev-2"})

    srv = _ThreadedHTTPServer(("127.0.0.1", port), MockMgmt)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        st = proxy_mod.get_status()
        assert st["health"]["ready"] is True
        assert st["active_requests"] == 1
        dumped = json.dumps(st)
        # Verify zero secrets leaked from overview, accounts.last_error, or requests.error!
        assert state["client_api_key"] not in dumped
        assert mgmt_key not in dumped
        assert "sk-secret-leak-999999" not in dumped
        assert "super_secret_987654" not in dumped

        # Pin account A -> account B becomes disabled
        pin_res = proxy_mod.pin_account("codex", "codex-a.json")
        assert pin_res["ok"] is True
        assert accounts_store[0]["disabled"] is False
        assert accounts_store[1]["disabled"] is True

        # Unpin -> restores account B to enabled!
        unpin_res = proxy_mod.unpin_account("codex")
        assert unpin_res["ok"] is True
        assert accounts_store[0]["disabled"] is False
        assert accounts_store[1]["disabled"] is False

        # Partial failure during pin_account must roll back toggled sibling!
        accounts_store.append({
            "id": "codex-c-fail.json",
            "provider": "codex",
            "label": "carol@example.com",
            "email": "carol@example.com",
            "kind": "oauth",
            "disabled": False,
            "cooldowns": {},
            "last_error": None,
            "counters": {"requests": 0, "failures": 0, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0},
            "quota": {},
            "models": ["gpt-6-astra"],
        })
        # Simulate failing toggle for codex-c-fail.json by temporarily removing it from store lookup during toggle
        orig_toggle = proxy_mod.CliProxyApiRustBackend.toggle_account

        def _flaky_toggle(self: Any, p: int, mk: str, aid: str, disabled: bool) -> tuple[bool, str | None]:
            if aid == "codex-c-fail.json" and disabled:
                return False, "injected toggle failure"
            return orig_toggle(self, p, mk, aid, disabled)

        proxy_mod.CliProxyApiRustBackend.toggle_account = _flaky_toggle  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError, match="injected toggle failure"):
                proxy_mod.pin_account("codex", "codex-a.json")
            # Sibling B must have been rolled back to disabled=False!
            assert accounts_store[1]["disabled"] is False
        finally:
            proxy_mod.CliProxyApiRustBackend.toggle_account = orig_toggle  # type: ignore[method-assign]
            accounts_store.pop()

        # Live routing patch via PATCH /api/config/settings
        rt = proxy_mod.update_routing(strategy="round-robin", session_affinity=True)
        assert rt["ok"] is True
        assert rt["applied_live"] is True
    finally:
        srv.shutdown()
        srv.server_close()


def test_port_conflict_rejected_cleanly(proxy_mod: Any, tmp_path: Path) -> None:
    fake_bin = tmp_path / "cliproxyapi-rust"
    fake_bin.write_text("#!/bin/sh\necho 'cliproxyapi-rust 0.3.2'\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    busy_port = sock.getsockname()[1]
    try:
        res = proxy_mod.start_proxy(port_override=busy_port, binary_override=str(fake_bin))
        assert res["ok"] is False
        assert "already in use" in res["error"]
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# 2. Real CLIProxyAPI-Rust end-to-end lifecycle, contract & security tests
# ---------------------------------------------------------------------------


def _http_req(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> tuple[int, str, dict[str, str]]:
    hdrs = dict(headers or {})
    data = None
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace"), {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}


@pytest.mark.skipif(find_real_proxy_binary() is None, reason="compiled cliproxyapi-rust binary not present")
def test_real_backend_lifecycle_auth_contracts_and_concurrency(proxy_mod: Any) -> None:
    real_bin = find_real_proxy_binary()
    assert real_bin is not None

    # Start a local mock upstream speaking OpenAI Chat Completions, Anthropic Messages, OpenAI Responses, and Gemini!
    upstream_port = free_port()
    seen_upstream_keys: list[str] = []
    seen_upstream_payloads: list[dict[str, Any]] = []

    class MockUpstream(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8", "replace"))
            auth = self.headers.get("Authorization") or self.headers.get("x-api-key") or self.headers.get("x-goog-api-key") or ""
            seen_upstream_keys.append(auth)
            seen_upstream_payloads.append({"path": self.path, "body": payload})

            # Simulate upstream error when model requests it
            model = str(payload.get("model") or "")
            if "fail-500" in model:
                err = json.dumps({"error": {"message": "synthetic upstream 500", "type": "server_error"}}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
                return

            stream = bool(payload.get("stream")) or "streamGenerateContent" in self.path

            # 1. OpenAI Chat Completions (used by openai-compatibility)
            if self.path.endswith("/chat/completions"):
                if stream:
                    chunks = [
                        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1700000000, "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hello "}, "finish_reason": None}]},
                        {"id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 1700000000, "model": model, "choices": [{"index": 0, "delta": {"content": "Oasis!"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}},
                    ]
                    sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                    data = sse.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    # Include tool call if tools were requested
                    has_tools = bool(payload.get("tools"))
                    msg: dict[str, Any] = {"role": "assistant", "content": None if has_tools else "pong-chat"}
                    if has_tools:
                        msg["tool_calls"] = [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"Riyadh"}'}}]
                    resp = {
                        "id": "chatcmpl-1",
                        "object": "chat.completion",
                        "created": 1700000000,
                        "model": model,
                        "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if has_tools else "stop"}],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
                    }
                    data = json.dumps(resp).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                return

            # 2. Anthropic Messages
            if "/v1/messages" in self.path:
                resp = {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": model or "claude-sonnet-5-5",
                    "content": [{"type": "text", "text": "pong-claude"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 9, "output_tokens": 3},
                }
                data = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            # 3. OpenAI Responses
            if self.path.endswith("/responses"):
                resp = {
                    "id": "resp_1",
                    "object": "response",
                    "status": "completed",
                    "model": model or "gpt-6-astra",
                    "output": [
                        {
                            "id": "msg_r1",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "pong-responses"}],
                        }
                    ],
                    "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
                }
                data = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            # 4. Gemini generateContent
            if ":generateContent" in self.path:
                resp = {
                    "candidates": [{"content": {"role": "model", "parts": [{"text": "pong-gemini"}]}, "finishReason": "STOP"}],
                    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3, "totalTokenCount": 10},
                }
                data = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            self.send_response(404)
            self.end_headers()

    up_srv = _ThreadedHTTPServer(("127.0.0.1", upstream_port), MockUpstream)
    threading.Thread(target=up_srv.serve_forever, daemon=True).start()

    proxy_port = free_port()
    try:
        # Configure state pointing synthetic test keys at our local mock upstream
        state = proxy_mod.load_state(create_if_missing=True)
        assert state is not None
        state["port"] = proxy_port
        state["binary_path"] = str(real_bin)
        state["api_keys"]["claude"] = [{"api_key": "sk-ant-mock-1", "label": "Mock Claude", "base_url": f"http://127.0.0.1:{upstream_port}"}]
        state["api_keys"]["codex"] = [
            {"api_key": "sk-codex-mock-1", "label": "Mock Codex 1", "base_url": f"http://127.0.0.1:{upstream_port}/v1"},
            {"api_key": "sk-codex-mock-2", "label": "Mock Codex 2", "base_url": f"http://127.0.0.1:{upstream_port}/v1"},
        ]
        state["api_keys"]["gemini"] = [{"api_key": "AIzaMockGeminiKey", "label": "Mock Gemini", "base_url": f"http://127.0.0.1:{upstream_port}"}]
        state["openai_compatibility"] = [
            {
                "name": "mock-compat",
                "base_url": f"http://127.0.0.1:{upstream_port}/v1",
                "api_keys": ["sk-compat-mock-1"],
                "models": [{"name": "mock-chat-model"}, {"name": "mock-fail-500"}],
            }
        ]
        state["tiers"] = {
            "diamond": [{"model": "claude-sonnet-5-5", "thinking": "high", "provider": "claude"}],
            "gold": [{"model": "gpt-6-astra", "thinking": "medium", "provider": "codex"}],
            "silver": [{"model": "mock-chat-model", "thinking": "low", "provider": "compat"}],
        }
        state["provider_defaults"] = {
            "claude": {"model": "claude-sonnet-5-5", "thinking": "low"},
        }
        proxy_mod.save_state_and_render(state)
        client_key = state["client_api_key"]
        mgmt_key = state["management_key"]

        # Start real proxy via oasis_proxy
        started = proxy_mod.start_proxy(port_override=proxy_port, binary_override=str(real_bin))
        assert started["ok"] is True, started
        base = f"http://127.0.0.1:{proxy_port}"

        # Verify umask 0077 kept log and config files strictly 0600!
        assert stat.S_IMODE(proxy_mod._config_yaml_path().stat().st_mode) == 0o600
        assert stat.S_IMODE((proxy_mod._proxy_dir() / "proxy.log").stat().st_mode) == 0o600

        # --- Security: Auth separation & rejection ---
        assert _http_req("GET", f"{base}/api/overview")[0] == 401
        assert _http_req("GET", f"{base}/api/overview", headers={"Authorization": f"Bearer {client_key}"})[0] == 401
        assert _http_req("GET", f"{base}/v1/models")[0] == 401
        assert _http_req("GET", f"{base}/v1/models", headers={"Authorization": f"Bearer {mgmt_key}"})[0] == 401

        # --- Contract 1: GET /v1/models (includes virtual oasis/* tier & provider models) ---
        st, body, _ = _http_req("GET", f"{base}/v1/models", headers={"Authorization": f"Bearer {client_key}"})
        assert st == 200
        model_ids = {m["id"] for m in json.loads(body)["data"]}
        assert "mock-chat-model" in model_ids
        assert "claude-sonnet-5-5" in model_ids
        assert "gpt-6-astra" in model_ids
        assert "oasis/diamond" in model_ids
        assert "oasis/gold" in model_ids
        assert "oasis/silver" in model_ids
        assert "oasis/claude" in model_ids

        # --- Contract 1b: Tier endpoints (/tier/{tier}/*) + Thinking injection ---
        st_tm, body_tm, _ = _http_req("GET", f"{base}/tier/diamond/v1/models", headers={"Authorization": f"Bearer {client_key}"})
        assert st_tm == 200
        tm_ids = {m["id"] for m in json.loads(body_tm)["data"]}
        assert "oasis/diamond" in tm_ids and "claude-sonnet-5-5" in tm_ids

        # /tier/diamond/v1/messages rewrites model -> claude-sonnet-5-5 & injects Anthropic thinking effort=high
        seen_upstream_payloads.clear()
        st_td, body_td, _ = _http_req(
            "POST",
            f"{base}/tier/diamond/v1/messages",
            headers={"x-api-key": client_key, "anthropic-version": "2023-06-01"},
            body={"model": "any-client-model", "max_tokens": 1024, "messages": [{"role": "user", "content": "reason deeply"}]},
        )
        assert st_td == 200, body_td
        assert seen_upstream_payloads[-1]["body"]["model"] == "claude-sonnet-5-5"
        assert seen_upstream_payloads[-1]["body"].get("thinking", {}).get("type") == "adaptive"
        assert seen_upstream_payloads[-1]["body"].get("output_config", {}).get("effort") == "high"

        # /tier/gold/v1/responses rewrites model -> gpt-6-astra & injects Responses reasoning.effort=medium
        seen_upstream_payloads.clear()
        st_tg, body_tg, _ = _http_req(
            "POST",
            f"{base}/tier/gold/v1/responses",
            headers={"Authorization": f"Bearer {client_key}"},
            body={"model": "ignored", "input": "plan"},
        )
        assert st_tg == 200, body_tg
        assert seen_upstream_payloads[-1]["body"]["model"] == "gpt-6-astra"
        assert seen_upstream_payloads[-1]["body"].get("reasoning", {}).get("effort") == "medium"

        # Virtual model ID oasis/gold on main /v1/responses behaves identically
        seen_upstream_payloads.clear()
        st_vg, body_vg, _ = _http_req(
            "POST",
            f"{base}/v1/responses",
            headers={"Authorization": f"Bearer {client_key}"},
            body={"model": "oasis/gold", "input": "plan-2"},
        )
        assert st_vg == 200, body_vg
        assert seen_upstream_payloads[-1]["body"]["model"] == "gpt-6-astra"
        assert seen_upstream_payloads[-1]["body"].get("reasoning", {}).get("effort") == "medium"

        # Virtual model ID oasis/silver on /v1/chat/completions injects reasoning_effort=low
        seen_upstream_payloads.clear()
        st_vs, body_vs, _ = _http_req(
            "POST",
            f"{base}/v1/chat/completions",
            headers={"Authorization": f"Bearer {client_key}"},
            body={"model": "oasis/silver", "messages": [{"role": "user", "content": "fast"}]},
        )
        assert st_vs == 200, body_vs
        assert seen_upstream_payloads[-1]["body"]["model"] == "mock-chat-model"
        assert seen_upstream_payloads[-1]["body"].get("reasoning_effort") == "low"

        # Explicit client reasoning_effort must NOT be overwritten by tier/provider default
        seen_upstream_payloads.clear()
        st_ov, body_ov, _ = _http_req(
            "POST",
            f"{base}/v1/chat/completions",
            headers={"Authorization": f"Bearer {client_key}"},
            body={"model": "oasis/silver", "reasoning_effort": "max", "messages": [{"role": "user", "content": "override"}]},
        )
        assert st_ov == 200, body_ov
        assert seen_upstream_payloads[-1]["body"].get("reasoning_effort") == "max"

        # Unconfigured tier returns 404 cleanly
        st_404, _, _ = _http_req(
            "POST",
            f"{base}/tier/subagent/v1/chat/completions",
            headers={"Authorization": f"Bearer {client_key}"},
            body={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert st_404 == 404

        # --- Contract 2: OpenAI Chat Completions (non-stream, stream, tool calls) ---
        st, body, _ = _http_req(
            "POST",
            f"{base}/v1/chat/completions",
            headers={"Authorization": f"Bearer {client_key}"},
            body={"model": "mock-chat-model", "messages": [{"role": "user", "content": "ping"}]},
        )
        assert st == 200, body
        chat_doc = json.loads(body)
        assert chat_doc["choices"][0]["message"]["content"] == "pong-chat"
        assert chat_doc["usage"]["total_tokens"] == 17

        st_s, body_s, hdrs_s = _http_req(
            "POST",
            f"{base}/v1/chat/completions",
            headers={"Authorization": f"Bearer {client_key}"},
            body={"model": "mock-chat-model", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert st_s == 200
        assert "text/event-stream" in hdrs_s.get("content-type", "")
        assert "Hello " in body_s and "Oasis!" in body_s and "[DONE]" in body_s

        st_t, body_t, _ = _http_req(
            "POST",
            f"{base}/v1/chat/completions",
            headers={"Authorization": f"Bearer {client_key}"},
            body={
                "model": "mock-chat-model",
                "messages": [{"role": "user", "content": "weather?"}],
                "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}],
            },
        )
        assert st_t == 200
        assert json.loads(body_t)["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_weather"

        # --- Contract 3: Anthropic Messages ---
        st_m, body_m, _ = _http_req(
            "POST",
            f"{base}/v1/messages",
            headers={"x-api-key": client_key, "anthropic-version": "2023-06-01"},
            body={"model": "claude-sonnet-5-5", "max_tokens": 64, "messages": [{"role": "user", "content": "ping"}]},
        )
        assert st_m == 200, body_m
        assert json.loads(body_m)["content"][0]["text"] == "pong-claude"

        # --- Contract 4: OpenAI Responses + Session Affinity ---
        seen_upstream_keys.clear()
        for _ in range(3):
            st_r, body_r, _ = _http_req(
                "POST",
                f"{base}/v1/responses",
                headers={"Authorization": f"Bearer {client_key}", "x-cliproxy-session-id": "sess-sticky-42"},
                body={"model": "gpt-6-astra", "input": "ping"},
            )
            assert st_r == 200, body_r
            assert json.loads(body_r)["output"][0]["content"][0]["text"] == "pong-responses"
        # Session affinity must keep all 3 turns on the exact same Codex account key!
        assert len(set(seen_upstream_keys)) == 1

        # --- Contract 5: Gemini generateContent ---
        st_g, body_g, _ = _http_req(
            "POST",
            f"{base}/v1beta/models/gemini-2.5-flash:generateContent",
            headers={"x-goog-api-key": client_key},
            body={"contents": [{"role": "user", "parts": [{"text": "ping"}]}]},
        )
        assert st_g == 200, body_g
        assert json.loads(body_g)["candidates"][0]["content"]["parts"][0]["text"] == "pong-gemini"

        # --- Contract 6: Cross-format translation (Claude client -> OpenAI Chat compat upstream) ---
        st_x, body_x, _ = _http_req(
            "POST",
            f"{base}/v1/messages",
            headers={"x-api-key": client_key, "anthropic-version": "2023-06-01"},
            body={"model": "mock-chat-model", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert st_x == 200, body_x
        assert "Hello Oasis!" in body_x

        # --- Contract 7: Malformed JSON returns 400 cleanly ---
        req_bad = urllib.request.Request(
            f"{base}/v1/chat/completions",
            data=b"{not valid json",
            headers={"Authorization": f"Bearer {client_key}", "Content-Type": "application/json"},
            method="POST",
        )
        opener_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            opener_direct.open(req_bad, timeout=5)
            pytest.fail("expected 400 for malformed JSON")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400

        # --- Contract 8: Upstream 500 error propagates cleanly without crashing proxy ---
        st_500, body_500, _ = _http_req(
            "POST",
            f"{base}/v1/chat/completions",
            headers={"Authorization": f"Bearer {client_key}"},
            body={"model": "mock-fail-500", "messages": [{"role": "user", "content": "boom"}]},
        )
        assert st_500 >= 500, body_500

        # --- Concurrency: 12 parallel requests ---
        def _call_one(i: int) -> int:
            code, _, _ = _http_req(
                "POST",
                f"{base}/v1/chat/completions",
                headers={"Authorization": f"Bearer {client_key}"},
                body={"model": "mock-chat-model", "messages": [{"role": "user", "content": f"req-{i}"}]},
            )
            return code

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(pool.map(_call_one, range(12)))
        assert all(c == 200 for c in codes)

        # --- Status & Secret Non-Disclosure ---
        status = proxy_mod.get_status()
        assert status["health"]["ready"] is True
        assert status["totals"]["requests"] >= 18
        dumped = json.dumps(status)
        assert client_key not in dumped
        assert mgmt_key not in dumped
        assert "sk-ant-mock-1" not in dumped
        assert "sk-codex-mock-1" not in dumped

        # --- Hot routing reload + Pin/Unpin on live backend ---
        rt = proxy_mod.update_routing(strategy="least-used", session_affinity=True)
        assert rt["ok"] is True and rt["applied_live"] is True
        codex_accs = [a for a in status["accounts"] if a["provider"] == "codex"]
        assert len(codex_accs) == 2
        pin = proxy_mod.pin_account("codex", codex_accs[1]["id"])
        assert pin["ok"] is True
        unpin = proxy_mod.unpin_account("codex")
        assert unpin["ok"] is True

        # --- Graceful stop & restart ---
        restarted = proxy_mod.restart_proxy()
        assert restarted["ok"] is True
        stopped = proxy_mod.stop_proxy(switch_to_direct=True)
        assert stopped["ok"] is True
        assert stopped["mode"] == "direct"
        time.sleep(0.2)
        assert not proxy_mod._port_in_use("127.0.0.1", proxy_port)
    finally:
        proxy_mod.stop_proxy(switch_to_direct=True)
        up_srv.shutdown()
        up_srv.server_close()
