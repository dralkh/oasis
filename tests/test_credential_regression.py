"""Credential integrity, concurrency, locking, and security regression tests."""

from __future__ import annotations

import base64
import json
import sqlite3
import stat
from pathlib import Path
from typing import Any

import pytest


def _fake_jwt(claims: dict[str, Any]) -> str:
    hdr = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{hdr}.{body}.sig"


def test_codex_snapshot_switch_and_token_rotation_preservation(accounts_mod: Any, isolated_env: dict[str, Path]) -> None:
    codex_dir = isolated_env["home"] / ".codex"
    codex_dir.mkdir(mode=0o700)
    auth_path = codex_dir / "auth.json"

    acct_a_doc = {
        "tokens": {
            "id_token": _fake_jwt({"email": "alice@example.com"}),
            "access_token": _fake_jwt({"email": "alice@example.com", "exp": 2_000_000_000}),
            "refresh_token": "rt_alice_v1",
            "account_id": "acc-alice",
        }
    }
    auth_path.write_text(json.dumps(acct_a_doc), encoding="utf-8")
    snap_a = accounts_mod.sync_live_to_vault("codex", label_override="Alice")
    assert snap_a is not None

    # Simulate signing in as Bob
    acct_b_doc = {
        "tokens": {
            "id_token": _fake_jwt({"email": "bob@example.com"}),
            "access_token": _fake_jwt({"email": "bob@example.com", "exp": 2_000_000_000}),
            "refresh_token": "rt_bob_v1",
            "account_id": "acc-bob",
        }
    }
    auth_path.write_text(json.dumps(acct_b_doc), encoding="utf-8")
    snap_b = accounts_mod.sync_live_to_vault("codex", label_override="Bob")
    assert snap_b is not None

    # Simulate native Codex CLI rotating Bob's refresh token before user switches back to Alice!
    acct_b_doc["tokens"]["refresh_token"] = "rt_bob_v2_rotated"
    auth_path.write_text(json.dumps(acct_b_doc), encoding="utf-8")

    # Switch to Alice: must sync Bob's rotated token back into Bob's vault entry BEFORE overwriting live auth.json
    res = accounts_mod.switch_account("codex", snap_a["id"])
    assert res["ok"] is True
    live = json.loads(auth_path.read_text(encoding="utf-8"))
    assert live["tokens"]["account_id"] == "acc-alice"

    # Switch back to Bob: must restore rt_bob_v2_rotated (never stale rt_bob_v1!)
    accounts_mod.switch_account("codex", snap_b["id"])
    live_b = json.loads(auth_path.read_text(encoding="utf-8"))
    assert live_b["tokens"]["refresh_token"] == "rt_bob_v2_rotated"

    # Verify permissions & zero secrets in index.json
    assert stat.S_IMODE(accounts_mod.INDEX_FILE.stat().st_mode) == 0o600
    assert stat.S_IMODE(accounts_mod.SECRETS_DIR.stat().st_mode) == 0o700
    index_text = accounts_mod.INDEX_FILE.read_text(encoding="utf-8")
    assert "rt_alice_v1" not in index_text
    assert "rt_bob_v2_rotated" not in index_text


def test_claude_switch_preserves_mcp_oauth_project_history_and_cas(
    accounts_mod: Any, limits_mod: Any, isolated_env: dict[str, Path]
) -> None:
    claude_dir = isolated_env["home"] / ".claude"
    claude_dir.mkdir(mode=0o700)
    creds_path = claude_dir / ".credentials.json"
    state_path = isolated_env["home"] / ".claude.json"

    mcp_oauth = {"tavily": {"accessToken": "mcp_secret_keep_me", "serverUrl": "https://mcp.tavily.com"}}
    project_history = {"/home/user/repo": {"allowedTools": ["Bash"], "history": ["keep me"]}}

    creds_path.write_text(
        json.dumps({
            "claudeAiOauth": {"accessToken": "cl_a_tok", "refreshToken": "cl_a_ref", "expiresAt": 2_000_000_000_000},
            "mcpOAuth": mcp_oauth,
        }),
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps({
            "oauthAccount": {"emailAddress": "alice@claude.test", "accountUuid": "uuid-a", "organizationUuid": "org-a"},
            "projects": project_history,
            "theme": "dark",
        }),
        encoding="utf-8",
    )
    snap_a = accounts_mod.sync_live_to_vault("claude", label_override="Claude A")
    assert snap_a is not None

    # Save second account B
    creds_path.write_text(
        json.dumps({
            "claudeAiOauth": {"accessToken": "cl_b_tok", "refreshToken": "cl_b_ref", "expiresAt": 2_000_000_000_000},
            "mcpOAuth": mcp_oauth,
        }),
        encoding="utf-8",
    )
    state_path.write_text(
        json.dumps({
            "oauthAccount": {"emailAddress": "bob@claude.test", "accountUuid": "uuid-b", "organizationUuid": "org-b"},
            "projects": project_history,
            "theme": "dark",
        }),
        encoding="utf-8",
    )
    snap_b = accounts_mod.sync_live_to_vault("claude", label_override="Claude B")
    assert snap_b is not None

    # Switch back to Claude A -> verify mcpOAuth, projects, and theme untouched!
    accounts_mod.switch_account("claude", snap_a["id"])
    creds_after = json.loads(creds_path.read_text(encoding="utf-8"))
    state_after = json.loads(state_path.read_text(encoding="utf-8"))
    assert creds_after["claudeAiOauth"]["accessToken"] == "cl_a_tok"
    assert creds_after["mcpOAuth"] == mcp_oauth
    assert state_after["oauthAccount"]["emailAddress"] == "alice@claude.test"
    assert state_after["projects"] == project_history
    assert state_after["theme"] == "dark"

    # Test CAS guard: if refreshToken rotated concurrently, CAS write MUST return False and preserve newer token!
    ok_cas_fail = limits_mod._claude_write_oauth_cas(
        creds_path,
        expected_refresh="stale_refresh_token",
        updated={"accessToken": "should_not_overwrite", "refreshToken": "bad_ref"},
    )
    assert ok_cas_fail is False
    assert json.loads(creds_path.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"] == "cl_a_tok"

    # Matching expected_refresh succeeds and preserves mcpOAuth
    ok_cas_pass = limits_mod._claude_write_oauth_cas(
        creds_path,
        expected_refresh="cl_a_ref",
        updated={"accessToken": "cl_a_tok_refreshed", "refreshToken": "cl_a_ref_2"},
    )
    assert ok_cas_pass is True
    creds_cas = json.loads(creds_path.read_text(encoding="utf-8"))
    assert creds_cas["claudeAiOauth"]["accessToken"] == "cl_a_tok_refreshed"
    assert creds_cas["mcpOAuth"] == mcp_oauth


def test_duplicate_account_identity_and_corrupted_index_recovery(accounts_mod: Any, isolated_env: dict[str, Path]) -> None:
    codex_dir = isolated_env["home"] / ".codex"
    codex_dir.mkdir(mode=0o700)
    auth_path = codex_dir / "auth.json"
    auth_path.write_text(
        json.dumps({
            "tokens": {
                "id_token": _fake_jwt({"email": "same@example.com"}),
                "access_token": _fake_jwt({"email": "same@example.com"}),
                "refresh_token": "rt_1",
                "account_id": "acc-same",
            }
        }),
        encoding="utf-8",
    )
    first = accounts_mod.sync_live_to_vault("codex")
    # Rotate token with same identity -> must update existing entry, NOT duplicate
    auth_path.write_text(
        json.dumps({
            "tokens": {
                "id_token": _fake_jwt({"email": "same@example.com"}),
                "access_token": _fake_jwt({"email": "same@example.com"}),
                "refresh_token": "rt_2",
                "account_id": "acc-same",
            }
        }),
        encoding="utf-8",
    )
    second = accounts_mod.sync_live_to_vault("codex")
    assert first is not None and second is not None
    assert first["id"] == second["id"]
    listed = accounts_mod.list_provider_accounts("codex")
    assert len(listed["accounts"]) == 1

    # Corrupt index.json -> must degrade gracefully without crashing
    accounts_mod.INDEX_FILE.write_text("{corrupt json", encoding="utf-8")
    listed_after = accounts_mod.list_provider_accounts("codex")
    assert listed_after["active_id"] == "__live__"
    assert len(listed_after["accounts"]) == 1


def test_symlink_and_path_traversal_attacks_blocked(accounts_mod: Any, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        accounts_mod._secret_path("codex", "../etc/passwd")
    with pytest.raises(ValueError):
        accounts_mod._secret_path("codex", "a/b")
    with pytest.raises(ValueError):
        accounts_mod._secret_path("../../tmp", "acc_1")

    # Symlink placed where secret file would be written
    victim = tmp_path / "victim.txt"
    victim.write_text("DO NOT OVERWRITE", encoding="utf-8")
    target_dir = accounts_mod.SECRETS_DIR / "codex"
    target_dir.mkdir(parents=True, exist_ok=True)
    evil_link = target_dir / "acc_evil.json"
    evil_link.symlink_to(victim)

    with pytest.raises(PermissionError, match="symlink"):
        accounts_mod.save_account_secret("codex", "acc_evil", {"auth": {"tokens": {}}})
    assert victim.read_text(encoding="utf-8") == "DO NOT OVERWRITE"


def test_opencode_sqlite_preserves_unrelated_rows_and_switches(accounts_mod: Any, isolated_env: dict[str, Path]) -> None:
    oc_dir = isolated_env["data"] / "opencode"
    oc_dir.mkdir(parents=True, exist_ok=True)
    db_path = oc_dir / "opencode.db"
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE credential ("
        "id TEXT PRIMARY KEY, integration_id TEXT, label TEXT, value TEXT, "
        "connector_id TEXT, method_id TEXT, active INTEGER, time_created INTEGER, time_updated INTEGER)"
    )
    con.execute(
        "INSERT INTO credential VALUES ('cred_other', 'anthropic', 'Anthropic', '{\"type\":\"key\",\"key\":\"keep\"}', NULL, NULL, 1, 1000, 1000)"
    )
    con.commit()
    con.close()

    r1 = accounts_mod.add_api_key_account("opencodego", "Go One", "test-key-1", activate=True)
    r2 = accounts_mod.add_api_key_account("opencodego", "Go Two", "test-key-2", activate=True)
    assert r1["ok"] and r2["ok"]

    listed = accounts_mod.list_provider_accounts("opencodego")
    assert len(listed["accounts"]) == 2
    assert listed["active_id"] == r2["account_id"]

    # Switch back to Go One
    sw = accounts_mod.switch_account("opencodego", r1["account_id"])
    assert sw["ok"] is True
    assert accounts_mod.list_provider_accounts("opencodego")["active_id"] == r1["account_id"]

    # Rename & remove inactive Go Two
    accounts_mod.rename_account("opencodego", r2["account_id"], "Go Two Renamed")
    accounts_mod.remove_account("opencodego", r2["account_id"])
    assert len(accounts_mod.list_provider_accounts("opencodego")["accounts"]) == 1

    # Verify unrelated 'anthropic' row remained completely intact!
    con = sqlite3.connect(str(db_path))
    other = con.execute("SELECT label, active FROM credential WHERE id = 'cred_other'").fetchone()
    con.close()
    assert other == ("Anthropic", 1)


def test_secret_service_unavailable_degrades_gracefully(accounts_mod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounts_mod, "_secret_tool", lambda: None)
    assert accounts_mod.read_live_snapshot("antigravity") is None
    with pytest.raises(RuntimeError, match="secret-tool"):
        accounts_mod.apply_live_snapshot("antigravity", {"raw": "go-keyring-base64:e30="})


def test_proxy_and_direct_cli_storage_strictly_isolated(
    accounts_mod: Any, proxy_mod: Any, isolated_env: dict[str, Path]
) -> None:
    # Populate native Codex credential
    codex_dir = isolated_env["home"] / ".codex"
    codex_dir.mkdir(mode=0o700)
    codex_auth = codex_dir / "auth.json"
    codex_auth.write_text('{"tokens":{"access_token":"native_only","refresh_token":"native_rt"}}', encoding="utf-8")
    accounts_mod.sync_live_to_vault("codex", label_override="Native")

    # Initialize proxy control plane
    proxy_mod.load_state(create_if_missing=True)
    assert list(proxy_mod._auths_dir().glob("*.json")) == []
    # Native Codex credential is untouched and never copied into proxy auths dir
    assert "native_rt" in codex_auth.read_text(encoding="utf-8")
    assert "native_rt" not in proxy_mod._config_yaml_path().read_text(encoding="utf-8")

    # Even if proxy config/state files are corrupted, Direct CLI accounts & usage keep working cleanly!
    proxy_mod._state_path().write_text("{corrupt proxy state", encoding="utf-8")
    proxy_mod._config_yaml_path().write_text(":::bad yaml:::", encoding="utf-8")
    listed = accounts_mod.list_provider_accounts("codex")
    assert listed["active_label"] == "Native"
    assert len(listed["accounts"]) == 1


def test_claude_lock_busy_refuses_overwrite(accounts_mod: Any, isolated_env: dict[str, Path]) -> None:
    claude_dir = isolated_env["home"] / ".claude"
    claude_dir.mkdir(mode=0o700)
    creds_path = claude_dir / ".credentials.json"
    creds_path.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok_a", "refreshToken": "ref_a"}}),
        encoding="utf-8",
    )
    snap_a = accounts_mod.sync_live_to_vault("claude", label_override="A")
    assert snap_a is not None
    creds_path.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "tok_b", "refreshToken": "ref_b"}}),
        encoding="utf-8",
    )
    accounts_mod.sync_live_to_vault("claude", label_override="B")

    # Simulate an active Claude Code OAuth refresh holding .oauth_refresh.lock
    lock_dir = claude_dir / ".oauth_refresh.lock"
    lock_dir.mkdir(mode=0o700)
    try:
        with pytest.raises(RuntimeError, match="lock is busy"):
            accounts_mod.switch_account("claude", snap_a["id"])
        # Live credentials must remain completely untouched
        assert json.loads(creds_path.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"] == "tok_b"
    finally:
        lock_dir.rmdir()


def test_qwen_and_kimi_never_store_raw_tokens_in_index(accounts_mod: Any, isolated_env: dict[str, Path]) -> None:
    qwen_dir = isolated_env["home"] / ".qwen"
    qwen_dir.mkdir(mode=0o700)
    qwen_secret = "opaque_qwen_refresh_secret_987654321"
    (qwen_dir / "oauth_creds.json").write_text(
        json.dumps({"access_token": "opaque_qwen_access_123456789", "refresh_token": qwen_secret}),
        encoding="utf-8",
    )
    accounts_mod.sync_live_to_vault("qwen", label_override="Qwen Main")

    kimi_dir = isolated_env["home"] / ".kimi" / "credentials"
    kimi_dir.mkdir(parents=True, mode=0o700)
    kimi_secret = "opaque_kimi_refresh_secret_987654321"
    (kimi_dir / "kimi-code.json").write_text(
        json.dumps({"access_token": "opaque_kimi_access_123456789", "refresh_token": kimi_secret}),
        encoding="utf-8",
    )
    accounts_mod.sync_live_to_vault("kimi", label_override="Kimi Main")

    idx_text = accounts_mod.INDEX_FILE.read_text(encoding="utf-8")
    assert qwen_secret not in idx_text
    assert "opaque_qwen_access" not in idx_text
    assert kimi_secret not in idx_text
    assert "opaque_kimi_access" not in idx_text


def test_concurrent_vault_operations_preserve_index_integrity(accounts_mod: Any, isolated_env: dict[str, Path]) -> None:
    import concurrent.futures

    def _add_one(i: int) -> str:
        res = accounts_mod.add_api_key_account("qwen", f"Acct-{i}", f"sk-qwen-conc-{i}", activate=False)
        return str(res["account_id"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(_add_one, range(10)))

    assert len(set(ids)) == 10
    listed = accounts_mod.list_provider_accounts("qwen")
    assert len(listed["accounts"]) == 10
