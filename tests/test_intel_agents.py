"""Unit tests for multi-CLI AI Intel agent discovery, selection, and execution."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import CODE_DIR


def _load_intel_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    cache_dir = tmp_path / "cache" / "ai-limits"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    if str(CODE_DIR) not in sys.path:
        sys.path.insert(0, str(CODE_DIR))
    sys.modules.pop("oasis_intel", None)
    mod = importlib.import_module("oasis_intel")
    monkeypatch.setattr(mod, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(mod, "USAGE_CACHE", cache_dir / "last.json")
    monkeypatch.setattr(mod, "INTEL_CACHE", cache_dir / "intel.json")
    monkeypatch.setattr(mod, "MCP_CONFIG", tmp_path / "no-mcp.json")
    monkeypatch.setattr(mod, "gather_tavily_dossier", lambda now_dt: "")
    return mod


def _make_fake_cli(bin_dir: Path, name: str, script_body: str) -> Path:
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + script_body + "\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_discover_agents_prefers_signed_in_cli_and_supports_pi_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _load_intel_mod(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_fake_cli(bin_dir, "codex", "exit 0")
    _make_fake_cli(bin_dir, "claude", "exit 0")
    _make_fake_cli(bin_dir, "pi", "exit 0")

    monkeypatch.setattr(mod, "enriched_env", lambda: {"PATH": str(bin_dir)})

    # When only codex is signed in, Auto should prefer codex over claude
    usage = {"providers": [{"id": "codex", "status": "ok"}, {"id": "claude", "status": "not_configured"}]}
    agents = mod.discover_agents(usage)
    by_id = {a["id"]: a for a in agents}
    assert by_id["auto"]["installed"] is True
    assert by_id["auto"]["resolved"] == "codex"
    assert by_id["claude"]["installed"] is True
    assert by_id["pi"]["installed"] is True
    assert by_id["kimi"]["installed"] is False

    # When only pi is installed, Auto falls back cleanly to pi
    bin_pi_only = tmp_path / "bin_pi"
    bin_pi_only.mkdir()
    _make_fake_cli(bin_pi_only, "pi", "exit 0")
    monkeypatch.setattr(mod, "enriched_env", lambda: {"PATH": str(bin_pi_only)})
    agents_pi = mod.discover_agents({})
    assert agents_pi[0]["resolved"] == "pi"


@pytest.mark.parametrize("agent_id", ["claude", "codex", "opencode", "gemini", "pi"])
def test_cmd_scan_executes_selected_cli_agent_and_caches_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent_id: str
) -> None:
    mod = _load_intel_mod(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    sample_json = json.dumps({
        "deals": [{"provider": "Claude", "offer": "50% off", "audience": "New users", "countries": "Global", "details": "Verified", "verified_date": "2026-11-27", "source_url": "https://claude.ai"}],
        "limit_advice": "Plenty of quota left.",
        "model_tiers": {
            "main_agent": [{"tier": "Diamond", "model": "Claude Opus 5.5", "provider": "Anthropic", "cost_per_task": "$0.38/task · $4/$20/1M", "benchmark": "TB-4.1 69.8%", "why": "Top coding model", "source_url": "https://anthropic.com"}],
            "subagent": [{"tier": "Gold", "model": "Gemini 4 Hydrogen", "provider": "Google", "cost_per_task": "$0.014/task · $0.15/$0.60/1M", "benchmark": "TB-4.1 52.4%", "why": "Fast worker", "source_url": "https://ai.google.dev"}],
        },
        "crucial_metrics": {"best_value_stack": "Opus 5.5 + Hydrogen", "policy_alerts": ["No breaking changes."]},
        "sources": [{"title": "Anthropic", "url": "https://anthropic.com"}],
    })

    if agent_id == "codex":
        # codex writes final answer to the `-o <file>` argument
        body = (
            'out=""\n'
            'while [ $# -gt 0 ]; do\n'
            '  if [ "$1" = "-o" ]; then out="$2"; shift 2; continue; fi\n'
            '  shift\n'
            'done\n'
            f'printf \'%s\\n\' \'{sample_json}\' > "$out"\n'
        )
    else:
        body = f"printf '%s\\n' '{sample_json}'"

    _make_fake_cli(bin_dir, agent_id, body)
    monkeypatch.setattr(mod, "enriched_env", lambda: {"PATH": str(bin_dir)})

    res = mod.cmd_scan(timeout=15, agent=agent_id)
    assert res["error"] == ""
    assert res["has_scan"] is True
    assert res["agent_used"] == agent_id
    assert res["selected_agent"] == agent_id
    assert len(res["deals"]) == 1
    assert res["model_tiers"]["main_agent"][0]["model"] == "Claude Opus 5.5"
    assert mod.INTEL_CACHE.is_file()

    # Subsequent read preserves cached scan + reports available agents
    read_res = mod.cmd_read(agent=agent_id)
    assert read_res["has_scan"] is True
    assert read_res["agent_used"] == agent_id
    assert any(a["id"] == agent_id and a["installed"] for a in read_res["available_agents"])


def test_cmd_scan_missing_agent_returns_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _load_intel_mod(monkeypatch, tmp_path)
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    monkeypatch.setattr(mod, "enriched_env", lambda: {"PATH": str(empty_bin)})

    res_auto = mod.cmd_scan(timeout=10, agent="auto")
    assert "No supported CLI agent found" in res_auto["error"]

    res_claude = mod.cmd_scan(timeout=10, agent="claude")
    assert "Claude CLI" in res_claude["error"]
