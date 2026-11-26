"""Adversarial, fuzz/property, streaming-cancellation, rollback, and CLI round-trip tests."""

from __future__ import annotations

import http.client
import http.server
import json
import random
import socketserver
import string
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import CODE_DIR, find_real_proxy_binary, free_port


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def test_fuzz_validate_upstream_url_never_crashes_or_allows_dangerous_schemes(proxy_mod: Any) -> None:
    rng = random.Random(20261126)
    alphabet = string.ascii_letters + string.digits + ":/?#[]@!$&'()*+,;=-._~ \t\r\n"
    for _ in range(250):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
        try:
            out = proxy_mod.validate_upstream_url(raw, allow_loopback=False)
            assert out.startswith("https://")
            assert "\n" not in out and "\r" not in out
        except ValueError:
            pass


def test_fuzz_redact_secrets_always_removes_injected_tokens(proxy_mod: Any) -> None:
    rng = random.Random(42)
    for _ in range(100):
        tok = "sk-" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(24))
        mgmt = "oasis_mgmt_" + "".join(rng.choice("0123456789abcdef") for _ in range(32))
        noise = f"err: Authorization: Bearer {tok} failed for {mgmt} at https://u:p@host.test/v1?key=secret123"
        out = proxy_mod.redact_secrets(noise, extra_secrets=[tok, mgmt])
        assert tok not in out
        assert mgmt not in out
        assert "u:p@" not in out
        assert "secret123" not in out


def test_canceled_login_rollback_restores_previous_credential(accounts_mod: Any, isolated_env: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    codex_dir = isolated_env["home"] / ".codex"
    codex_dir.mkdir(mode=0o700)
    auth_path = codex_dir / "auth.json"
    orig_doc = {"tokens": {"access_token": "orig_tok", "refresh_token": "orig_rt", "account_id": "acc-orig"}}
    auth_path.write_text(json.dumps(orig_doc), encoding="utf-8")

    # Simulate a native CLI login that exits 130 (Ctrl-C) without writing a new credential
    monkeypatch.setattr(accounts_mod, "_login_command_for", lambda p, e: (["/bin/false"], "test"))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "")

    rc = accounts_mod.run_login_interactive("codex", "")
    assert rc == 1
    # Original live credential MUST be restored intact!
    restored = json.loads(auth_path.read_text(encoding="utf-8"))
    assert restored["tokens"]["refresh_token"] == "orig_rt"
    assert restored["tokens"]["account_id"] == "acc-orig"


def test_systemd_unit_renders_hardening_directives(proxy_mod: Any, tmp_path: Path) -> None:
    import shutil

    bin_path = tmp_path / "cliproxyapi-rust"
    bin_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    bin_path.chmod(0o755)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(mode=0o700)
    cfg_path = cfg_dir / "config.yaml"
    cfg_path.write_text("port: 8317\n", encoding="utf-8")

    unit = proxy_mod._render_systemd_unit(str(bin_path), cfg_path)
    assert "UMask=0077" in unit
    assert "NoNewPrivileges=true" in unit
    assert "PrivateTmp=true" in unit
    assert "KillSignal=SIGTERM" in unit
    assert "TimeoutStopSec=15" in unit
    assert "UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY" in unit
    assert f"WorkingDirectory={cfg_dir}" in unit

    if shutil.which("systemd-analyze"):
        unit_file = tmp_path / "oasis-proxy.service"
        unit_file.write_text(unit, encoding="utf-8")
        res = subprocess.run(
            ["systemd-analyze", "--user", "verify", str(unit_file)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert "not an absolute path" not in (res.stderr + res.stdout)
        assert res.returncode == 0, res.stderr + res.stdout


def test_cli_subprocess_roundtrips_and_zero_secrets(isolated_env: dict[str, Path], tmp_path: Path) -> None:
    import hashlib

    env = {
        "HOME": str(isolated_env["home"]),
        "XDG_CONFIG_HOME": str(isolated_env["config"]),
        "XDG_DATA_HOME": str(isolated_env["data"]),
        "XDG_CACHE_HOME": str(isolated_env["cache"]),
        "OASIS_PROXY_NO_SYSTEMD": "1",
        "PATH": "/usr/bin:/bin",
    }

    def _run(*args: str) -> dict[str, Any]:
        res = subprocess.run(
            [sys.executable, str(CODE_DIR / "oasis_proxy.py"), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert res.returncode == 0, f"cmd {args} failed: {res.stderr} {res.stdout}"
        return json.loads(res.stdout)

    doc = _run("status")
    assert doc["ok"] is True
    assert doc["mode"] == "direct"
    assert set(doc["capabilities"].keys()) == {"codex", "claude", "antigravity", "opencodego", "qwen", "muse", "kimi", "grok", "cursor"}

    assert _run("set-mode", "proxy")["mode"] == "proxy"
    assert _run("set-routing", "--strategy=round-robin", "--session-affinity=true")["ok"] is True
    rot = _run("rotate-key")
    assert rot["ok"] is True and "sk-oasis-" not in json.dumps(rot)

    fake_bin = tmp_path / "cliproxyapi-rust"
    fake_bin.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "cliproxyapi-rust 0.3.2"; fi\n', encoding="utf-8")
    fake_bin.chmod(0o755)
    sha = hashlib.sha256(fake_bin.read_bytes()).hexdigest()
    inst = _run("install-backend", f"--from-file={fake_bin}", f"--sha256={sha}")
    assert inst["ok"] is True and inst["version"] == "0.3.2"
    assert _run("uninstall-backend")["ok"] is True


@pytest.mark.skipif(find_real_proxy_binary() is None, reason="compiled cliproxyapi-rust binary not present")
def test_real_backend_streaming_cancellation_and_malformed_sse(proxy_mod: Any) -> None:
    real_bin = find_real_proxy_binary()
    assert real_bin is not None
    upstream_port = free_port()
    proxy_port = free_port()

    class HostileUpstream(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}") if length else {}
            model = str(payload.get("model") or "")
            if model == "slow-stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    for i in range(40):
                        chunk = json.dumps({"id": "c1", "object": "chat.completion.chunk", "created": 1700000000, "model": model, "choices": [{"index": 0, "delta": {"content": f"tok{i} "}, "finish_reason": None}]})
                        self.wfile.write(f"data: {chunk}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(0.05)
                except OSError:
                    pass
                return
            if model == "bad-sse":
                data = b"data: {broken json\n\ndata: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(404)
            self.end_headers()

    up_srv = _ThreadedHTTPServer(("127.0.0.1", upstream_port), HostileUpstream)
    threading.Thread(target=up_srv.serve_forever, daemon=True).start()
    try:
        state = proxy_mod.load_state(create_if_missing=True)
        assert state is not None
        state["port"] = proxy_port
        state["binary_path"] = str(real_bin)
        state["openai_compatibility"] = [
            {
                "name": "hostile",
                "base_url": f"http://127.0.0.1:{upstream_port}/v1",
                "api_keys": ["sk-hostile-1"],
                "models": [{"name": "slow-stream"}, {"name": "bad-sse"}],
            }
        ]
        proxy_mod.save_state_and_render(state)
        client_key = state["client_api_key"]
        started = proxy_mod.start_proxy(port_override=proxy_port, binary_override=str(real_bin))
        assert started["ok"] is True

        # 1. Start slow SSE stream and abort client connection mid-stream (cancellation test)
        conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps({"model": "slow-stream", "stream": True, "messages": [{"role": "user", "content": "go"}]}),
            headers={"Authorization": f"Bearer {client_key}", "Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        assert resp.status == 200
        first_line = resp.readline()
        assert b"tok0" in first_line
        conn.close()  # Abrupt client disconnect!

        # Wait briefly and verify proxy stays healthy & active_requests drops back to 0!
        time.sleep(0.3)
        st = proxy_mod.get_status()
        assert st["health"]["ready"] is True
        assert st["active_requests"] == 0

        # 2. Malformed SSE from upstream must not crash proxy
        conn2 = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
        conn2.request(
            "POST",
            "/v1/chat/completions",
            body=json.dumps({"model": "bad-sse", "stream": True, "messages": [{"role": "user", "content": "go"}]}),
            headers={"Authorization": f"Bearer {client_key}", "Content-Type": "application/json"},
        )
        resp2 = conn2.getresponse()
        _ = resp2.read()
        conn2.close()
        assert proxy_mod.get_status()["health"]["ready"] is True
    finally:
        proxy_mod.stop_proxy(switch_to_direct=True)
        up_srv.shutdown()
        up_srv.server_close()
