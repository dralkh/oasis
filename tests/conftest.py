"""Shared isolated fixtures for Oasis automated tests.

Guarantees every test runs inside a temporary HOME / XDG tree and never touches
real developer credentials or performs live paid inference.
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = REPO_ROOT / "package" / "contents" / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def find_real_proxy_binary() -> Path | None:
    env_bin = os.environ.get("OASIS_TEST_PROXY_BIN", "").strip()
    if env_bin:
        p = Path(env_bin)
        if p.is_file() and os.access(p, os.X_OK):
            return p
    bundled = REPO_ROOT / "package" / "contents" / "bin" / "oasis-proxy"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return bundled
    return None


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Provide a fully isolated HOME and XDG hierarchy."""
    home = tmp_path / "home"
    cfg = home / ".config"
    data = home / ".local" / "share"
    cache = home / ".cache"
    for d in (home, cfg, data, cache):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setenv("OASIS_PROXY_NO_SYSTEMD", "1")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.delenv("QWEN_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MUSE_AUTH_PATH", raising=False)
    monkeypatch.delenv("KIMI_CODE_HOME", raising=False)
    monkeypatch.delenv("OASIS_PROXY_BIN", raising=False)
    return {"home": home, "config": cfg, "data": data, "cache": cache}


@pytest.fixture()
def accounts_mod(isolated_env: dict[str, Path]) -> Any:
    import oasis_accounts

    return importlib.reload(oasis_accounts)


@pytest.fixture()
def limits_mod(isolated_env: dict[str, Path]) -> Any:
    import ai_limits

    return importlib.reload(ai_limits)


@pytest.fixture()
def proxy_mod(isolated_env: dict[str, Path]) -> Any:
    import oasis_proxy

    return importlib.reload(oasis_proxy)
