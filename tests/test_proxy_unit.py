"""Unit tests for oasis_proxy.py control plane, security validators, and backend abstraction."""

from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from pathlib import Path
from typing import Any

import pytest


def _make_fake_backend_script(path: Path, version: str = "0.3.2") -> Path:
    path.write_text(
        f"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo \"cliproxyapi-rust {version}\"; exit 0; fi\nexit 0\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_discovery_absent_and_graceful_degradation(proxy_mod: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_mod, "_bundled_bin_path", lambda: tmp_path / "missing-oasis-proxy")
    status = proxy_mod.get_status()
    assert status["ok"] is True
    assert status["mode"] == "direct"
    assert status["backend"]["installed"] is False
    assert status["backend"]["compatible"] is False
    assert status["health"]["running"] is False
    assert status["health"]["ready"] is False
    assert status["initialized"] is False
    # Never leaks secrets even before init
    assert "management_key" not in status
    assert "client_api_key" not in status


def test_bundled_oasis_proxy_auto_discovered(proxy_mod: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = _make_fake_backend_script(tmp_path / "oasis-proxy", "0.3.2")
    monkeypatch.setattr(proxy_mod, "_bundled_bin_path", lambda: bundled)
    status = proxy_mod.get_status()
    assert status["ok"] is True
    assert status["backend"]["installed"] is True
    assert status["backend"]["compatible"] is True
    assert status["backend"]["binary_path"] == str(bundled)


def test_discovery_version_negotiation(proxy_mod: Any, tmp_path: Path) -> None:
    backend = proxy_mod.CliProxyApiRustBackend()
    old_bin = _make_fake_backend_script(tmp_path / "old-bin", "0.2.9")
    disc_old = backend.discover(str(old_bin))
    assert disc_old.installed is True
    assert disc_old.version == "0.2.9"
    assert disc_old.compatible is False
    assert "older than required" in (disc_old.error or "")

    good_bin = _make_fake_backend_script(tmp_path / "good-bin", "0.3.2")
    disc_good = backend.discover(str(good_bin))
    assert disc_good.installed is True
    assert disc_good.version == "0.3.2"
    assert disc_good.compatible is True
    assert disc_good.error is None


def test_state_init_permissions_and_auth_separation(proxy_mod: Any) -> None:
    state = proxy_mod.load_state(create_if_missing=True)
    assert state is not None
    assert state["client_api_key"].startswith("sk-oasis-")
    assert state["management_key"].startswith("oasis_mgmt_")
    assert state["client_api_key"] != state["management_key"]
    assert len(state["client_api_key"]) >= 64
    assert len(state["management_key"]) >= 64

    proxy_dir = proxy_mod._proxy_dir()
    auths_dir = proxy_mod._auths_dir()
    state_file = proxy_mod._state_path()
    cfg_file = proxy_mod._config_yaml_path()

    assert stat.S_IMODE(proxy_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(auths_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600

    yaml_text = cfg_file.read_text(encoding="utf-8")
    assert 'host: "127.0.0.1"' in yaml_text
    assert "management-allow-remote: false" in yaml_text
    assert 'proxy-url: "direct"' in yaml_text
    assert "banked-resets: false" in yaml_text
    assert "debug: false" in yaml_text
    assert "credential-ui-enabled: false" in yaml_text


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "file:///etc/passwd",
        "ftp://example.com/v1",
        "http://api.openai.com/v1",  # non-HTTPS remote
        "https://user:pass@api.openai.com/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://100.100.100.200/latest/meta-data",
        "https://100.64.0.1/v1",  # CGNAT
        "https://10.0.0.5/v1",
        "https://192.168.1.1/v1",
        "https://127.0.0.1:8080/v1",  # loopback blocked when allow_loopback=False
        "https://2130706433/v1",  # decimal IPv4 loopback
        "https://0177.0.0.1/v1",  # octal IPv4 loopback
        "https://0x7f000001/v1",  # hex IPv4 loopback
        "https://127.1/v1",  # shorthand IPv4 loopback
        "https://[::ffff:100.100.100.200]/v1",  # IPv4-mapped metadata IPv6
        "https://[::ffff:127.0.0.1]/v1",  # IPv4-mapped loopback IPv6
        "https://localhost/v1",
        "https://localhost./v1",
        "https://foo.localhost/v1",
        "https://metadata.google.internal/computeMetadata/v1",
        "https://api.openai.com\\@127.0.0.1/v1",  # backslash authority confusion
        "https://api.openai.com/v1?key=secret",  # query string rejected
        "https://api.openai.com/v1#frag",  # fragment rejected
        "https://example.com/v1\nInjected: header",
    ],
)
def test_ssrf_url_validation_rejects_unsafe_urls(proxy_mod: Any, bad_url: str) -> None:
    with pytest.raises(ValueError):
        proxy_mod.validate_upstream_url(bad_url, allow_loopback=False)


def test_ssrf_url_validation_allows_safe_and_explicit_loopback(proxy_mod: Any) -> None:
    assert proxy_mod.validate_upstream_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert proxy_mod.validate_upstream_url("http://127.0.0.1:11434/v1", allow_loopback=True) == "http://127.0.0.1:11434/v1"
    assert proxy_mod.validate_upstream_url("socks5://127.0.0.1:1080", allow_loopback=True, allow_socks=True) == "socks5://127.0.0.1:1080"


def test_secret_redaction_strips_all_sensitive_patterns(proxy_mod: Any) -> None:
    sample = (
        "Authorization: Bearer tok_super_secret_123456 "
        "key=sk-ant-api03-abcdef1234567890 "
        "mgmt=oasis_mgmt_0123456789abcdef0123456789abcdef "
        "google=AIzaSyDummyKeyForRedactionTest12345 "
        "proxy=http://alice:hunter2@proxy.local:8080 "
        "url=https://example.com/api?key=mysecretparam123 "
        "meta=dca:secret_device_token_999999"
    )
    redacted = proxy_mod.redact_secrets(sample, extra_secrets=["custom_secret_xyz"])
    for needle in (
        "tok_super_secret_123456",
        "sk-ant-api03-abcdef1234567890",
        "oasis_mgmt_0123456789abcdef0123456789abcdef",
        "AIzaSyDummyKeyForRedactionTest12345",
        "alice:hunter2",
        "mysecretparam123",
        "dca:secret_device_token_999999",
    ):
        assert needle not in redacted
    assert "[REDACTED]" in redacted


def test_opencodego_enforces_per_model_chat_protocol_boundary(proxy_mod: Any) -> None:
    # Attempting to sync ONLY Anthropic Messages / Responses models must fail explicitly.
    with pytest.raises(ValueError, match="Chat Completions"):
        proxy_mod.sync_static_api_key(
            "opencodego",
            "test-opencode-key-1234",
            models=["minimax-m2.5", "qwen3-coder", "gpt-5.4", "grok-4", "muse-spark-1.3"],
        )

    # Mixed list keeps ONLY verified Chat models and explicitly reports rejected non-Chat models.
    res = proxy_mod.sync_static_api_key(
        "opencodego",
        "test-opencode-key-1234",
        models=["deepseek-v3.2", "glm-5", "minimax-m2.5", "gpt-6"],
    )
    assert res["ok"] is True
    assert res["models"] == ["deepseek-v3.2", "glm-5"]
    assert res["rejected_non_chat_models"] == ["minimax-m2.5", "gpt-6"]


def test_qwen_coding_plan_and_api_key_regional_routing(proxy_mod: Any) -> None:
    res_coding = proxy_mod.sync_static_api_key("qwen", "sk-sp-test-coding-key", region="china")
    assert res_coding["ok"] is True
    yaml_text = proxy_mod._config_yaml_path().read_text(encoding="utf-8")
    assert "https://coding.dashscope.aliyuncs.com/v1" in yaml_text

    res_api = proxy_mod.sync_static_api_key("qwen", "sk-test-dashscope-key", region="international")
    assert res_api["ok"] is True
    yaml_text = proxy_mod._config_yaml_path().read_text(encoding="utf-8")
    assert "https://dashscope-intl.dashscope.aliyuncs.com/compatible-mode/v1" in yaml_text


def test_ownership_rejects_disallowed_sync_and_login(proxy_mod: Any) -> None:
    with pytest.raises(ValueError):
        proxy_mod.sync_static_api_key("cursor", "sk-cursor-fake")
    with pytest.raises(ValueError):
        proxy_mod.sync_static_api_key("antigravity", "fake-token")
    with pytest.raises(ValueError):
        proxy_mod.launch_proxy_login("cursor")
    with pytest.raises(ValueError):
        proxy_mod.launch_proxy_login("qwen")
    with pytest.raises(ValueError):
        proxy_mod.launch_proxy_login("opencodego")


def test_routing_updates_and_validation(proxy_mod: Any) -> None:
    res = proxy_mod.update_routing(
        strategy="least-used",
        session_affinity=False,
        five_hour_reserve=20,
        request_retry=2,
        codex_websockets=False,
        force_model_prefix=True,
        selected_provider="claude",
        selected_model="claude-sonnet-5-5",
        allow_metered_fallback=True,
    )
    assert res["ok"] is True
    yaml_text = proxy_mod._config_yaml_path().read_text(encoding="utf-8")
    assert 'routing: "least-used"' in yaml_text
    assert "session-affinity: false" in yaml_text
    assert "five-hour-reserve-percent: 20" in yaml_text
    assert "request-retry: 2" in yaml_text
    assert "codex-websockets: false" in yaml_text
    assert "force-model-prefix: true" in yaml_text

    policy = proxy_mod.load_policy()
    assert policy["selected_provider"] == "claude"
    assert policy["selected_model"] == "claude-sonnet-5-5"
    assert policy["allow_metered_fallback"] is True

    with pytest.raises(ValueError):
        proxy_mod.update_routing(strategy="invalid-strategy")


def test_verified_local_install_sha256_tarball_and_uninstall(proxy_mod: Any, tmp_path: Path) -> None:
    fake_bin = _make_fake_backend_script(tmp_path / "cliproxyapi-rust", "0.3.2")
    tar_path = tmp_path / "cliproxyapi-rust-v0.3.2.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(fake_bin, arcname="cliproxyapi-rust")

    good_sha = hashlib.sha256(tar_path.read_bytes()).hexdigest()

    # Wrong checksum must be rejected before installing
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        proxy_mod.install_backend_from_file(str(tar_path), expected_sha256="0" * 64)
    assert not proxy_mod._local_bin_path().exists()

    # Correct checksum installs atomically with 0755
    inst = proxy_mod.install_backend_from_file(str(tar_path), expected_sha256=good_sha)
    assert inst["ok"] is True
    assert inst["version"] == "0.3.2"
    assert proxy_mod._local_bin_path().is_file()
    assert os.access(proxy_mod._local_bin_path(), os.X_OK)

    # Uninstall without purge keeps config but removes binary
    un1 = proxy_mod.uninstall_backend(purge_config=False)
    assert un1["ok"] is True
    assert not proxy_mod._local_bin_path().exists()
    assert proxy_mod._proxy_dir().exists()

    # Uninstall with purge removes proxy config too
    un2 = proxy_mod.uninstall_backend(purge_config=True)
    assert un2["ok"] is True
    assert not proxy_mod._proxy_dir().exists()


def test_client_snippets_and_symlink_rejection(proxy_mod: Any, tmp_path: Path) -> None:
    state = proxy_mod.load_state(create_if_missing=True)
    assert state is not None
    snippets = proxy_mod.client_config_snippets()
    rendered = str(snippets)
    assert state["client_api_key"] not in rendered
    assert state["management_key"] not in rendered
    assert "<OASIS_PROXY_KEY>" in rendered
    assert "claude_code" in snippets["clients"]
    assert "codex" in snippets["clients"]
    assert "opencode" in snippets["clients"]
    assert "hermes" in snippets["clients"]

    # Symlink attack on state.json must be rejected
    victim = tmp_path / "victim.json"
    victim.write_text('{"important": true}', encoding="utf-8")
    proxy_mod._state_path().unlink()
    proxy_mod._state_path().symlink_to(victim)
    with pytest.raises(PermissionError, match="symlink"):
        proxy_mod.save_state_and_render(state)
    assert victim.read_text(encoding="utf-8") == '{"important": true}'


def test_install_requires_sha256_and_rejects_symlink_tarballs(proxy_mod: Any, tmp_path: Path) -> None:
    fake_bin = _make_fake_backend_script(tmp_path / "cliproxyapi-rust", "0.3.2")
    # Missing sha256 fails unless allow_unverified=True
    with pytest.raises(ValueError, match="SHA-256"):
        proxy_mod.install_backend_from_file(str(fake_bin))

    # Symlink member inside tar.gz must be rejected
    evil_tar = tmp_path / "evil.tar.gz"
    with tarfile.open(evil_tar, "w:gz") as tf:
        ti = tarfile.TarInfo("cliproxyapi-rust")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/bin/sh"
        tf.addfile(ti)
    evil_sha = hashlib.sha256(evil_tar.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="regular 'oasis-proxy' binary"):
        proxy_mod.install_backend_from_file(str(evil_tar), expected_sha256=evil_sha)

    # Explicit allow_unverified=True installs cleanly
    res = proxy_mod.install_backend_from_file(str(fake_bin), allow_unverified=True)
    assert res["ok"] is True
    assert res["version"] == "0.3.2"


def test_account_id_traversal_and_oauth_token_sync_rejected(proxy_mod: Any) -> None:
    for bad_id in ("", ".", "..", "../etc/passwd", "a/b", "a\\b", "id\nnewline"):
        with pytest.raises(ValueError):
            proxy_mod._validate_account_id(bad_id)

    # Rotating OAuth / JWT / Muse device tokens must never be accepted by sync_static_api_key
    with pytest.raises(ValueError, match="OAuth"):
        proxy_mod.sync_static_api_key("qwen", "dca:device_auth_secret_token_12345")
    with pytest.raises(ValueError, match="OAuth"):
        proxy_mod.sync_static_api_key("kimi", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature1234")


def test_ui_policy_update_does_not_initialize_proxy_state(proxy_mod: Any) -> None:
    assert not proxy_mod._state_path().exists()
    res = proxy_mod.update_routing(selected_provider="claude", selected_model="claude-sonnet-5-5")
    assert res["ok"] is True
    assert not proxy_mod._state_path().exists()
    assert not proxy_mod._config_yaml_path().exists()
    assert proxy_mod.load_policy()["selected_provider"] == "claude"


def test_redaction_happens_before_truncation(proxy_mod: Any) -> None:
    prefix = "A" * 250
    secret = "sk-ant-api03-verysecretvalue1234567890"
    out = proxy_mod.redact_secrets(prefix + secret, extra_secrets=[secret])
    assert secret not in out
    assert "verysecret" not in out


def test_list_models_resolves_openai_compat_provider_names(proxy_mod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    state = proxy_mod.load_state(create_if_missing=True)
    assert state is not None
    proxy_mod.sync_static_api_key("qwen", "sk-qwen-test-key", region="international", models=["qwen3-coder-plus"])
    proxy_mod.sync_static_api_key("opencodego", "ocg-test-key", models=["deepseek-v3.2"])
    fresh = proxy_mod.load_state(create_if_missing=False)
    assert fresh is not None
    backend = proxy_mod.CliProxyApiRustBackend()
    monkeypatch.setattr(
        proxy_mod,
        "_local_http",
        lambda *a, **kw: (
            200,
            [
                {"id": "qwen3-coder-plus", "provider": "openai-compat"},
                {"id": "deepseek-v3.2", "provider": "openai-compat"},
                {"id": "gpt-6-astra", "provider": "codex"},
            ],
            None,
        ),
    )
    ok, models, err = backend.list_models(fresh["port"], fresh["management_key"], state=fresh)
    assert ok is True and err is None
    by_id = {m["id"]: m["provider"] for m in models}
    assert by_id["qwen3-coder-plus"] == "qwen"
    assert by_id["deepseek-v3.2"] == "opencodego"
    assert by_id["gpt-6-astra"] == "codex"


def test_provider_default_and_tier_routing_yaml_rendering(proxy_mod: Any) -> None:
    res_p = proxy_mod.set_provider_default("claude", model="claude-sonnet-5-5", thinking="high")
    assert res_p["ok"] is True
    assert res_p["provider"] == "claude"
    assert res_p["model"] == "claude-sonnet-5-5"
    assert res_p["thinking"] == "high"

    res_t = proxy_mod.set_tier("diamond", model="claude-opus-4-6", thinking="max", provider="claude")
    assert res_t["ok"] is True
    assert res_t["tier"] == "diamond"
    assert res_t["targets"][0]["model"] == "claude-opus-4-6"
    assert res_t["targets"][0]["thinking"] == "max"

    yaml_text = proxy_mod._config_yaml_path().read_text(encoding="utf-8")
    assert "provider-defaults:" in yaml_text
    assert 'model: "claude-sonnet-5-5"' in yaml_text
    assert 'thinking: "high"' in yaml_text
    assert "tiers:" in yaml_text
    assert "diamond:" in yaml_text
    assert 'model: "claude-opus-4-6"' in yaml_text
    assert 'thinking: "max"' in yaml_text

    status = proxy_mod.get_status()
    assert status["provider_defaults"]["claude"] == {"model": "claude-sonnet-5-5", "thinking": "high"}
    assert status["tiers"]["diamond"]["model_id"] == "oasis/diamond"
    assert status["tiers"]["diamond"]["openai_base_url"].endswith("/tier/diamond/v1")
    assert status["tiers"]["diamond"]["targets"][0]["model"] == "claude-opus-4-6"

    # Clear tier
    res_c = proxy_mod.set_tier("diamond", clear=True)
    assert res_c["ok"] is True and res_c["cleared"] is True
    assert proxy_mod.get_status()["tiers"]["diamond"]["targets"] == []

    # Invalid inputs rejected
    with pytest.raises(ValueError):
        proxy_mod.set_provider_default("claude", thinking="ultra-invalid")
    with pytest.raises(ValueError):
        proxy_mod.set_tier("platinum", model="gpt-5.4")
    with pytest.raises(ValueError):
        proxy_mod.set_tier("gold", model="bad\nmodel")


def test_import_intel_tiers_preview_and_apply(proxy_mod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    intel_path = proxy_mod._intel_cache_path()
    intel_path.parent.mkdir(parents=True, exist_ok=True)
    intel_path.write_text(
        json.dumps({
            "generated_at": "2026-11-26T00:00:00Z",
            "model_tiers": {
                "main_agent": [
                    {"tier": "Diamond", "model": "Claude Opus 4.6", "provider": "Anthropic"},
                    {"tier": "Gold", "model": "GPT-5.4", "provider": "OpenAI"},
                    {"tier": "Silver", "model": "DeepSeek V3.2", "provider": "DeepSeek"},
                    {"tier": "Diamond", "model": "Unconnected Frontier 9", "provider": "UnknownLab"},
                ],
                "subagent": [
                    {"model": "Gemini 2.5 Flash", "provider": "Google"},
                ],
            },
        }),
        encoding="utf-8",
    )

    state = proxy_mod.load_state(create_if_missing=True)
    assert state is not None
    monkeypatch.setattr(proxy_mod, "_port_in_use", lambda *a, **kw: True)
    monkeypatch.setattr(
        proxy_mod.CliProxyApiRustBackend,
        "list_models",
        lambda self, port, key, state=None: (
            True,
            [
                {"id": "claude-opus-4-6", "provider": "claude"},
                {"id": "gpt-5.4", "provider": "codex"},
                {"id": "deepseek-v3.2", "provider": "opencodego"},
                {"id": "gemini-2.5-flash", "provider": "antigravity"},
            ],
            None,
        ),
    )

    # Preview only: must NOT mutate tiers in state
    prev = proxy_mod.import_intel_tiers(apply=False)
    assert prev["ok"] is True
    assert prev["applied"] is False
    assert prev["matched_live_models"] is True
    by_tier = {row["tier"]: row["targets"] for row in prev["preview"]}
    assert by_tier["diamond"][0]["model"] == "claude-opus-4-6"
    assert by_tier["diamond"][0]["thinking"] == "high"
    assert by_tier["gold"][0]["model"] == "gpt-5.4"
    assert by_tier["gold"][0]["thinking"] == "medium"
    assert by_tier["silver"][0]["model"] == "deepseek-v3.2"
    assert by_tier["silver"][0]["thinking"] == "low"
    assert by_tier["subagent"][0]["model"] == "gemini-2.5-flash"
    assert by_tier["subagent"][0]["thinking"] == "none"
    assert len(prev["skipped"]) == 1
    assert prev["skipped"][0]["intel_name"] == "Unconnected Frontier 9"
    assert proxy_mod.load_state(create_if_missing=False)["tiers"] == {}

    # Apply: writes matched tiers into state.json & config.yaml
    applied = proxy_mod.import_intel_tiers(apply=True)
    assert applied["ok"] is True and applied["applied"] is True
    fresh = proxy_mod.load_state(create_if_missing=False)
    assert fresh is not None
    assert fresh["tiers"]["diamond"][0]["model"] == "claude-opus-4-6"
    assert fresh["tiers"]["gold"][0]["model"] == "gpt-5.4"
    assert fresh["tiers"]["subagent"][0]["thinking"] == "none"
    yaml_text = proxy_mod._config_yaml_path().read_text(encoding="utf-8")
    assert "tiers:" in yaml_text
    assert 'model: "claude-opus-4-6"' in yaml_text


def test_catalog_models_populated_even_without_connected_accounts(proxy_mod: Any) -> None:
    status = proxy_mod.get_status()
    assert status["models"] == []
    catalog = status["catalog_models"]
    assert len(catalog) >= 40
    by_prov: dict[str, list[str]] = {}
    for entry in catalog:
        by_prov.setdefault(entry["provider"], []).append(entry["id"])
        assert entry["connected"] is False
    assert "gpt-6-astra" in by_prov["codex"]
    assert "claude-opus-5-5" in by_prov["claude"]
    assert "gemini-3.8-flash-high" in by_prov["antigravity"]
    assert "deepseek-v3.2" in by_prov["opencodego"]
    assert "qwen3-coder-plus" in by_prov["qwen"]
    assert "kimi-k3" in by_prov["kimi"]
    assert "grok-4.7" in by_prov["grok"]
    assert "muse-spark-1.3" in by_prov["muse"]

    # Live connected models take precedence and are marked connected=True
    merged = proxy_mod._build_catalog_models(
        [{"id": "claude-opus-5-5", "provider": "claude"}, {"id": "custom-live-model", "provider": "antigravity"}]
    )
    assert merged[0] == {"id": "claude-opus-5-5", "provider": "claude", "connected": True, "source": "live"}
    assert merged[1] == {"id": "custom-live-model", "provider": "antigravity", "connected": True, "source": "live"}
    # No duplicate (claude-opus-5-5, claude)
    assert sum(1 for m in merged if m["id"] == "claude-opus-5-5" and m["provider"] == "claude") == 1
