#!/usr/bin/env python3
"""Oasis optional local CLI inference proxy control plane.

Provides a narrowly scoped, replaceable backend abstraction (ProxyBackend)
implementing discovery, version negotiation, health/readiness, systemd and
non-systemd lifecycle management, isolated credential ownership, routing &
account pinning, model/capability discovery, request stats, error normalization,
safe clipboard helpers, and verified local backend installation/removal.
"""

from __future__ import annotations

import abc
import argparse
import base64
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

VERSION = "1.5.0"
MIN_BACKEND_VERSION = (0, 3, 0)
PINNED_BACKEND_TAG = "v0.3.2"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8317
SYSTEMD_UNIT_NAME = "oasis-proxy.service"
HTTP_TIMEOUT = 6.0
MAX_HTTP_BODY = 4 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_INSTALL_BYTES = 100 * 1024 * 1024
MAX_LOG_BYTES = 5 * 1024 * 1024

VALID_ROUTING_STRATEGIES = ("least-used", "smart-quota", "round-robin", "fill-first")
VALID_TIERS = ("diamond", "gold", "silver", "subagent")
VALID_THINKING_MODES = ("auto", "none", "low", "medium", "high", "xhigh", "max")
VALID_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")
VALID_PROVIDER_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# OpenCode Go per-model protocol boundaries verified from opencode-go-cliproxyapi v0.2.3.
# Only Chat Completions prefixes can be safely routed through openai-compatibility.
OPENCODEGO_CHAT_PREFIXES = ("deepseek", "longcat", "kimi", "glm", "mimo", "hy")
OPENCODEGO_MESSAGES_PREFIXES = ("minimax", "qwen")
OPENCODEGO_RESPONSES_PREFIXES = ("muse-spark", "grok", "gpt")
OPENCODEGO_DEFAULT_CHAT_MODELS = [
    "deepseek-v3.2",
    "deepseek-v4-pro",
    "deepseek-v4.1-flash",
    "glm-5",
    "glm-5.3",
    "kimi-k2.5",
    "kimi-k3",
]

QWEN_CODING_BASES = {
    "international": "https://coding-intl.dashscope.aliyuncs.com/v1",
    "china": "https://coding.dashscope.aliyuncs.com/v1",
}
QWEN_API_BASES = {
    "international": "https://dashscope-intl.dashscope.aliyuncs.com/compatible-mode/v1",
    "china": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}
QWEN_DEFAULT_MODELS = [
    "qwen3-coder-plus",
    "qwen3-max",
    "qwen3.5-plus",
    "qwen3-coder-480b-a35b-instruct",
]

# Full built-in model catalog mirroring oasis-proxy's provider registries so users
# can always select any model in the UI without manual typing, even before adding
# an isolated proxy account. Live-connected models from `/api/models` are merged
# ahead of these and marked `connected: True`.
PROVIDER_BUILTIN_MODELS: dict[str, list[str]] = {
    "codex": [
        "gpt-6-astra",
        "gpt-6.1-sol",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.3-codex",
        "gpt-image-2",
    ],
    "claude": [
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "antigravity": [
        "claude-opus-4-6-thinking",
        "claude-sonnet-4-6",
        "gemini-3.8-flash-high",
        "gemini-3.7-flash-high",
        "gemini-3.6-flash-high",
        "gemini-pro-agent",
        "gemini-3.1-pro-low",
        "gemini-3-flash",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-image",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gpt-oss-120b-medium",
    ],
    "opencodego": list(OPENCODEGO_DEFAULT_CHAT_MODELS),
    "qwen": list(QWEN_DEFAULT_MODELS),
    "muse": [
        "muse-spark-1.3",
        "muse-spark-1.2",
        "muse-spark-1.1",
    ],
    "kimi": [
        "kimi-k3",
        "kimi-k3-256k",
        "kimi-k2.8",
        "kimi-k2.8-code",
        "kimi-k2.7-code",
        "kimi-k2.7-code-highspeed",
        "kimi-k2.6",
        "kimi-k2.5",
        "kimi-k2-thinking",
    ],
    "grok": [
        "grok-4.7",
        "grok-4.7-build-fast",
        "grok-4.6",
        "grok-build-0.1",
        "grok-4.5",
        "grok-4.3",
        "grok-4.20-0309-reasoning",
        "grok-4.20-0309-non-reasoning",
        "grok-3-mini",
        "grok-imagine-image-2.0",
        "grok-imagine-video-1.5",
    ],
}

KIMI_API_BASES = {
    "china": "https://api.kimi.com/coding",
    "international": "https://api.kimi.ai/coding",
}

PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "codex": {
        "id": "codex",
        "name": "Codex",
        "backend_provider": "codex",
        "proxy_support": "native",
        "proxy_support_label": "Full native",
        "auth_types": ["proxy_oauth", "api_key"],
        "rotating_oauth": True,
        "sync_static_key_allowed": True,
        "protocols": ["openai-responses", "openai-responses-ws"],
        "streaming": True,
        "websockets": True,
        "tool_calls": True,
        "structured_outputs": True,
        "reasoning": True,
        "prompt_caching": True,
        "quota_in_proxy": True,
        "ownership_note": "Proxy OAuth is isolated from ~/.codex/auth.json so refresh tokens never race.",
    },
    "claude": {
        "id": "claude",
        "name": "Claude",
        "backend_provider": "claude",
        "proxy_support": "native",
        "proxy_support_label": "Full native",
        "auth_types": ["proxy_oauth", "api_key"],
        "rotating_oauth": True,
        "sync_static_key_allowed": True,
        "protocols": ["anthropic-messages"],
        "streaming": True,
        "websockets": False,
        "tool_calls": True,
        "structured_outputs": True,
        "reasoning": True,
        "prompt_caching": True,
        "quota_in_proxy": True,
        "ownership_note": "Proxy OAuth is isolated from ~/.claude/.credentials.json to protect active sessions and mcpOAuth.",
    },
    "antigravity": {
        "id": "antigravity",
        "name": "Antigravity",
        "backend_provider": "antigravity",
        "proxy_support": "native",
        "proxy_support_label": "Full native",
        "auth_types": ["proxy_oauth"],
        "rotating_oauth": False,
        "sync_static_key_allowed": False,
        "protocols": ["gemini-cloudcode"],
        "streaming": True,
        "websockets": False,
        "tool_calls": True,
        "structured_outputs": True,
        "reasoning": True,
        "prompt_caching": False,
        "quota_in_proxy": False,
        "ownership_note": "Isolated proxy Google OAuth with separate Cloud Code project onboarding.",
    },
    "opencodego": {
        "id": "opencodego",
        "name": "OpenCode Go",
        "backend_provider": "openai-compat",
        "compat_group": "opencode-go",
        "proxy_support": "partial_chat_only",
        "proxy_support_label": "Chat models only",
        "auth_types": ["api_key"],
        "rotating_oauth": False,
        "sync_static_key_allowed": True,
        "protocols": ["openai-chat-completions"],
        "unsupported_protocols": ["anthropic-messages (minimax/qwen)", "openai-responses (muse-spark/grok/gpt)"],
        "streaming": True,
        "websockets": False,
        "tool_calls": True,
        "structured_outputs": True,
        "reasoning": False,
        "prompt_caching": False,
        "quota_in_proxy": False,
        "ownership_note": "Static API key safe to sync. Only Chat Completions models (deepseek, glm, kimi, longcat, mimo, hy) are proxied; Messages/Responses models remain Direct CLI only.",
    },
    "qwen": {
        "id": "qwen",
        "name": "Qwen",
        "backend_provider": "openai-compat",
        "compat_group": "qwen",
        "proxy_support": "partial_api_key_only",
        "proxy_support_label": "API key / Coding Plan only",
        "auth_types": ["api_key"],
        "rotating_oauth": True,
        "sync_static_key_allowed": True,
        "protocols": ["openai-chat-completions"],
        "unsupported_protocols": ["qwen-oauth (Direct CLI only)"],
        "streaming": True,
        "websockets": False,
        "tool_calls": True,
        "structured_outputs": True,
        "reasoning": True,
        "prompt_caching": False,
        "quota_in_proxy": False,
        "ownership_note": "Coding Plan (sk-sp-*) and DashScope API keys sync cleanly with regional base URLs. Qwen OAuth stays Direct CLI only.",
    },
    "muse": {
        "id": "muse",
        "name": "Muse Code",
        "backend_provider": "meta",
        "proxy_support": "native",
        "proxy_support_label": "Full native",
        "auth_types": ["proxy_device", "api_key"],
        "rotating_oauth": False,
        "sync_static_key_allowed": True,
        "protocols": ["openai-responses"],
        "streaming": True,
        "websockets": False,
        "tool_calls": True,
        "structured_outputs": True,
        "reasoning": True,
        "prompt_caching": False,
        "quota_in_proxy": False,
        "ownership_note": "Isolated proxy device login (auth.meta.com) or static Meta API key.",
    },
    "kimi": {
        "id": "kimi",
        "name": "Kimi Code",
        "backend_provider": "kimi",
        "proxy_support": "native",
        "proxy_support_label": "Full native",
        "auth_types": ["proxy_device", "api_key"],
        "rotating_oauth": True,
        "sync_static_key_allowed": True,
        "protocols": ["openai-chat-completions", "anthropic-messages", "openai-responses"],
        "streaming": True,
        "websockets": False,
        "tool_calls": True,
        "structured_outputs": True,
        "reasoning": True,
        "prompt_caching": False,
        "quota_in_proxy": False,
        "ownership_note": "Static Kimi API keys sync cleanly; device-bound OAuth tokens remain proxy-isolated.",
    },
    "grok": {
        "id": "grok",
        "name": "Grok",
        "backend_provider": "xai",
        "proxy_support": "native",
        "proxy_support_label": "Full native",
        "auth_types": ["proxy_device", "api_key"],
        "rotating_oauth": True,
        "sync_static_key_allowed": True,
        "protocols": ["openai-responses"],
        "streaming": True,
        "websockets": False,
        "tool_calls": True,
        "structured_outputs": True,
        "reasoning": True,
        "prompt_caching": True,
        "quota_in_proxy": False,
        "ownership_note": "Proxy device login is isolated from ~/.grok/auth.json so OIDC refresh tokens never collide.",
    },
    "cursor": {
        "id": "cursor",
        "name": "Cursor",
        "backend_provider": None,
        "proxy_support": "monitoring_only",
        "proxy_support_label": "Monitoring only",
        "auth_types": [],
        "rotating_oauth": True,
        "sync_static_key_allowed": False,
        "protocols": [],
        "streaming": False,
        "websockets": False,
        "tool_calls": False,
        "structured_outputs": False,
        "reasoning": False,
        "prompt_caching": False,
        "quota_in_proxy": False,
        "ownership_note": "Cursor credentials are IDE-managed. Monitoring-only; no authorized proxy inference.",
    },
}

BACKEND_TO_OASIS_PROVIDER = {
    "codex": "codex",
    "claude": "claude",
    "antigravity": "antigravity",
    "kimi": "kimi",
    "xai": "grok",
    "meta": "muse",
    "gemini": "gemini",
    "vertex": "vertex",
    "devin": "devin",
    "openai-compat": "compat",
}

OASIS_TO_BACKEND_PROVIDER = {
    "codex": "codex",
    "claude": "claude",
    "antigravity": "antigravity",
    "kimi": "kimi",
    "grok": "xai",
    "muse": "meta",
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _home() -> Path:
    return Path.home()


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config"))


def _cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or (_home() / ".cache"))


def _intel_cache_path() -> Path:
    return _cache_home() / "ai-limits" / "intel.json"


def _oasis_dir() -> Path:
    return _config_home() / "oasis"


def _proxy_dir() -> Path:
    return _oasis_dir() / "proxy"


def _auths_dir() -> Path:
    return _proxy_dir() / "auths"


def _state_path() -> Path:
    return _proxy_dir() / "state.json"


def _policy_path() -> Path:
    return _proxy_dir() / "policy.json"


def _runtime_path() -> Path:
    return _proxy_dir() / "runtime.json"


def _config_yaml_path() -> Path:
    return _proxy_dir() / "config.yaml"


def _lock_path() -> Path:
    return _proxy_dir() / ".control.lock"


def _systemd_unit_path() -> Path:
    return _config_home() / "systemd" / "user" / SYSTEMD_UNIT_NAME


def _bundled_bin_path() -> Path:
    return Path(__file__).resolve().parent.parent / "bin" / "oasis-proxy"


def _local_bin_path() -> Path:
    return _home() / ".local" / "bin" / "oasis-proxy"


# ---------------------------------------------------------------------------
# Filesystem, locking, process verification, redaction, and SSRF validation
# ---------------------------------------------------------------------------


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise PermissionError(f"refusing symlink at sensitive path: {path.name}")


def _ensure_private_dir(path: Path) -> None:
    _reject_symlink(path)
    # Also ensure oasis parent dir is 0700 without touching ~/.config itself.
    oasis_root = _oasis_dir()
    if path == oasis_root or oasis_root in path.parents:
        _reject_symlink(oasis_root)
        oasis_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(oasis_root, 0o700)
        except OSError:
            pass
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_symlink(path)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _read_private_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        _reject_symlink(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        fd_owned = True
        try:
            fh = os.fdopen(fd, "rb")
            fd_owned = False
            with fh:
                raw = fh.read(MAX_FILE_BYTES)
        finally:
            if fd_owned:
                try:
                    os.close(fd)
                except OSError:
                    pass
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _write_text_atomic(path: Path, text: str, mode: int = 0o600, *, private_parent: bool = True) -> None:
    if private_parent:
        _ensure_private_dir(path.parent)
    else:
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    _reject_symlink(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, mode)
    fd_owned = True
    try:
        fh = os.fdopen(fd, "w", encoding="utf-8")
        fd_owned = False
        with fh:
            os.fchmod(fh.fileno(), mode)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if fd_owned:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _write_json_atomic(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    _write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode=mode)


@contextmanager
def _control_lock() -> Iterator[None]:
    _ensure_private_dir(_proxy_dir())
    lock = _lock_path()
    _reject_symlink(lock)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"',;]+"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)[^\s\"',;]+"),
    re.compile(r"(?i)(api[-_]?key\s*[:=]\s*)[^\s\"',;]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|oasis_mgmt_[0-9a-f]{16,}|AIza[0-9A-Za-z_-]{20,}|"
        r"GOCSPX-[0-9A-Za-z_-]{12,}|xai-[A-Za-z0-9_-]{8,}|dca:[A-Za-z0-9._~+/=-]{8,}|"
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{4,})\b"
    ),
    re.compile(r"(https?|socks5h?)://([^/\s@]+)@"),
    re.compile(r"(?i)([?&](?:key|api_key|token|access_token|secret)=)[^&\s]+"),
]


def _known_secrets(state: Optional[dict[str, Any]]) -> list[str]:
    if not isinstance(state, dict):
        return []
    out: list[str] = []
    for k in ("management_key", "client_api_key"):
        v = state.get(k)
        if isinstance(v, str) and v:
            out.append(v)
    for entries in (state.get("api_keys") or {}).values():
        for item in entries or []:
            if isinstance(item, dict) and isinstance(item.get("api_key"), str) and item["api_key"]:
                out.append(item["api_key"])
    for entry in state.get("openai_compatibility") or []:
        if isinstance(entry, dict):
            for k in entry.get("api_keys") or []:
                if isinstance(k, str) and k:
                    out.append(k)
    return out


def redact_secrets(text: str, extra_secrets: Optional[list[str]] = None) -> str:
    """Scrub tokens, API keys, JWTs, proxy credentials, and query keys from any string."""
    if not text:
        return ""
    cleaned = str(text)
    for secret in extra_secrets or []:
        if secret and len(secret) >= 6:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    for idx, pat in enumerate(_SECRET_PATTERNS):
        if idx in (0, 1, 2, 3, 6):
            cleaned = pat.sub(r"\1[REDACTED]", cleaned)
        elif idx == 5:
            cleaned = pat.sub(r"\1://[REDACTED]@", cleaned)
        else:
            cleaned = pat.sub("[REDACTED]", cleaned)
    return cleaned


def _is_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_loopback: bool) -> bool:
    if isinstance(ip_obj, ipaddress.IPv6Address):
        if ip_obj.ipv4_mapped is not None:
            return _is_forbidden_ip(ip_obj.ipv4_mapped, allow_loopback=allow_loopback)
        if ip_obj.sixtofour is not None:
            return _is_forbidden_ip(ip_obj.sixtofour, allow_loopback=allow_loopback)
        if ip_obj.teredo is not None:
            return _is_forbidden_ip(ip_obj.teredo[1], allow_loopback=allow_loopback)
    if str(ip_obj) in ("169.254.169.254", "100.100.100.200", "fd00:ec2::254"):
        return True
    if ip_obj.is_loopback:
        return not allow_loopback
    # Any non-global IP (private RFC1918, CGNAT 100.64.0.0/10, link-local, multicast, unspecified, reserved) is blocked.
    if not ip_obj.is_global:
        return True
    return False


def _looks_like_alt_ipv4(host: str) -> bool:
    """Detect decimal (2130706433), hex (0x7f000001), octal (0177.0.0.1), or shorthand (127.1) IPv4 literals."""
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        pass
    labels = [lbl for lbl in host.split(".") if lbl]
    if labels and re.fullmatch(r"(?:0x[0-9a-f]+|0[0-7]+|\d+)", labels[-1]):
        return True
    return False


def validate_upstream_url(
    url: str,
    *,
    allow_loopback: bool = False,
    allow_socks: bool = False,
    resolve_dns: bool = False,
) -> str:
    """Validate an upstream base URL or proxy URL against SSRF, parser differentials, and injection."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL cannot be empty")
    if "\\" in raw:
        raise ValueError("URL must not contain backslashes")
    if any(ord(ch) < 32 or ch.isspace() for ch in raw):
        raise ValueError("URL contains whitespace or control characters")
    parsed = urllib.parse.urlsplit(raw)
    scheme = parsed.scheme.lower()
    allowed_schemes = {"https"}
    if allow_loopback:
        allowed_schemes.add("http")
    if allow_socks:
        allowed_schemes.update({"http", "socks5", "socks5h"})
    if scheme not in allowed_schemes:
        raise ValueError(f"unsupported URL scheme '{scheme}'")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs with embedded credentials are not allowed")
    if parsed.query or parsed.fragment:
        raise ValueError("base/proxy URLs must not contain query strings or fragments")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("URL is missing a hostname")
    if host in ("metadata.google.internal", "metadata", "instance-data") or host.endswith(".internal"):
        raise ValueError(f"blocked internal/metadata host: {host}")

    is_localhost_name = host == "localhost" or host.endswith((".localhost", ".localdomain"))
    if is_localhost_name:
        if not allow_loopback:
            raise ValueError("loopback URLs are not allowed for remote providers")
        return raw.rstrip("/")

    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        ip_obj = None

    if ip_obj is not None:
        if _is_forbidden_ip(ip_obj, allow_loopback=allow_loopback):
            raise ValueError(f"blocked IP address: {host}")
    else:
        if _looks_like_alt_ipv4(host):
            raise ValueError(f"blocked numeric/alternate IP host: {host}")
        if scheme == "http" and not allow_socks:
            raise ValueError("remote provider base URLs must use https://")
        if resolve_dns:
            try:
                infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            except OSError:
                infos = []
            for info in infos:
                addr_str = info[4][0].split("%", 1)[0]
                try:
                    resolved_ip = ipaddress.ip_address(addr_str)
                except ValueError:
                    continue
                if _is_forbidden_ip(resolved_ip, allow_loopback=allow_loopback):
                    raise ValueError(f"host '{host}' resolves to blocked IP {addr_str}")

    return raw.rstrip("/")


def _sanitize_label(label: str, default: str = "") -> str:
    cleaned = re.sub(r"[\r\n\t]+", " ", (label or "").strip())
    return cleaned[:64] if cleaned else default


def _validate_port(port: Any) -> int:
    try:
        p = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid port number") from exc
    if p < 1024 or p > 65535:
        raise ValueError("proxy port must be between 1024 and 65535")
    return p


def _validate_account_id(account_id: str) -> str:
    if not isinstance(account_id, str) or not VALID_ACCOUNT_ID_RE.match(account_id):
        raise ValueError("invalid account id")
    if account_id in (".", "..") or ".." in account_id or "/" in account_id or "\\" in account_id:
        raise ValueError("invalid account id")
    return account_id


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _proc_stat_info(pid: int) -> Optional[tuple[str, int]]:
    """Return (state_char, start_time_ticks) from Linux /proc/<pid>/stat, or None."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="ignore")
        rparen = raw.rfind(")")
        if rparen < 0:
            return None
        rest = raw[rparen + 2 :].split()
        if len(rest) < 20:
            return None
        return rest[0], int(rest[19])
    except (OSError, ValueError):
        return None


def _proc_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return [part.decode("utf-8", "ignore") for part in raw.split(b"\x00") if part]
    except OSError:
        return []


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    info = _proc_stat_info(pid)
    if info is not None:
        return info[0] != "Z"
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _verify_runtime_pid(runtime: dict[str, Any]) -> Optional[int]:
    """Verify runtime.json PID still belongs to our exact Oasis proxy process before signaling."""
    pid = runtime.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return None
    if not _pid_alive(pid):
        return None
    expected_start = runtime.get("proc_start_ticks")
    info = _proc_stat_info(pid)
    if isinstance(expected_start, int) and info is not None and info[1] != expected_start:
        return None
    cmdline = _proc_cmdline(pid)
    expected_bin = str(runtime.get("binary_path") or "")
    expected_cfg = str(_config_yaml_path())
    if cmdline:
        if expected_bin and Path(cmdline[0]).name != Path(expected_bin).name:
            return None
        if expected_cfg not in cmdline:
            return None
    return pid


def _enriched_env(strip_proxies: bool = False) -> dict[str, str]:
    env = dict(os.environ)
    if strip_proxies:
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(var, None)
    extra = [
        str(_home() / ".local" / "bin"),
        str(_home() / ".cargo" / "bin"),
        str(_home() / ".opencode" / "bin"),
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    existing = [p for p in env.get("PATH", "").split(":") if p]
    merged: list[str] = []
    seen: set[str] = set()
    for p in extra + existing:
        if p and p not in seen and os.path.isdir(p):
            seen.add(p)
            merged.append(p)
    env["PATH"] = ":".join(merged)
    return env


def _parse_semver(text: str) -> Optional[tuple[int, int, int]]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _yaml_quote(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Strict loopback-only HTTP client (no redirects, no ambient proxies)
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_LOCAL_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect,
)


def _local_http(
    method: str,
    port: int,
    path: str,
    *,
    bearer: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    timeout: float = HTTP_TIMEOUT,
) -> tuple[int, Any, Optional[str]]:
    if not path.startswith("/"):
        raise ValueError("path must start with '/'")
    url = f"http://127.0.0.1:{_validate_port(port)}{path}"
    headers = {"Accept": "application/json", "User-Agent": f"oasis-proxy-ctl/{VERSION}"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    body_bytes: Optional[bytes] = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with _LOCAL_OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_HTTP_BODY)
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_HTTP_BODY) if exc.fp else b""
        status = exc.code
    except urllib.error.URLError as exc:
        return 0, None, f"unreachable: {exc.reason}"
    except OSError as exc:
        return 0, None, f"unreachable: {exc}"

    if not raw:
        return status, None, None
    text = raw.decode("utf-8", "replace")
    try:
        return status, json.loads(text), None
    except ValueError:
        return status, text.strip(), None


# ---------------------------------------------------------------------------
# Oasis proxy state, policy, and config.yaml rendering
# ---------------------------------------------------------------------------


def _default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "backend_id": "cliproxyapi-rust",
        "binary_path": "",
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "client_api_key": f"sk-oasis-{secrets.token_hex(32)}",
        "management_key": f"oasis_mgmt_{secrets.token_hex(32)}",
        "proxy_url": "direct",
        "routing": {
            "strategy": "smart-quota",
            "session_affinity": True,
            "session_affinity_idle_seconds": 86400,
            "five_hour_reserve_percent": 30,
            "request_retry": 3,
            "codex_websockets": True,
            "claude_cloak": True,
            "force_model_prefix": False,
        },
        "api_keys": {
            "claude": [],
            "codex": [],
            "gemini": [],
            "kimi": [],
            "xai": [],
            "meta": [],
        },
        "openai_compatibility": [],
        "oauth_excluded_models": {},
        "tiers": {},
        "provider_defaults": {},
    }


def _default_policy() -> dict[str, Any]:
    return {
        "version": 1,
        "mode": "direct",
        "selected_provider": "codex",
        "selected_model": "",
        "pinned_accounts": {},
        "allow_metered_fallback": False,
        "allow_aggregator_overflow": True,
        "use_systemd": True,
    }


def load_policy() -> dict[str, Any]:
    base = _default_policy()
    doc = _read_private_json(_policy_path())
    if doc:
        for k in base:
            if k in doc:
                base[k] = doc[k]
    return base


def save_policy(policy: dict[str, Any]) -> None:
    _write_json_atomic(_policy_path(), policy, mode=0o600)


def load_state(*, create_if_missing: bool = False) -> Optional[dict[str, Any]]:
    doc = _read_private_json(_state_path())
    if doc:
        base = _default_state()
        for k, v in base.items():
            if k not in doc:
                doc[k] = v
            elif isinstance(v, dict) and isinstance(doc[k], dict):
                for sk, sv in v.items():
                    doc[k].setdefault(sk, sv)
        return doc
    if not create_if_missing:
        return None
    state = _default_state()
    _ensure_private_dir(_proxy_dir())
    _ensure_private_dir(_auths_dir())
    _write_json_atomic(_state_path(), state, mode=0o600)
    render_config_yaml(state)
    return state


def save_state_and_render(state: dict[str, Any]) -> None:
    _ensure_private_dir(_proxy_dir())
    _ensure_private_dir(_auths_dir())
    _write_json_atomic(_state_path(), state, mode=0o600)
    render_config_yaml(state)


def render_config_yaml(state: dict[str, Any]) -> Path:
    """Deterministically render CLIProxyAPI-Rust config.yaml with strict security defaults."""
    routing = state.get("routing") or {}
    host = DEFAULT_HOST
    port = _validate_port(state.get("port") or DEFAULT_PORT)
    strategy = str(routing.get("strategy") or "smart-quota")
    if strategy not in VALID_ROUTING_STRATEGIES:
        strategy = "smart-quota"
    proxy_url = str(state.get("proxy_url") or "direct").strip() or "direct"
    if proxy_url not in ("direct", "none"):
        proxy_url = validate_upstream_url(proxy_url, allow_loopback=True, allow_socks=True)

    lines: list[str] = [
        "# Generated by Oasis proxy control plane (mode 0600). Do not commit or share.",
        f"host: {_yaml_quote(host)}",
        f"port: {port}",
        f"auth-dir: {_yaml_quote(str(_auths_dir()))}",
        f"api-keys: [{_yaml_quote(state['client_api_key'])}]",
        f"management-key: {_yaml_quote(state['management_key'])}",
        "management-allow-remote: false",
        f"proxy-url: {_yaml_quote(proxy_url)}",
        f"request-retry: {max(1, min(10, int(routing.get('request_retry', 3))))}",
        f"routing: {_yaml_quote(strategy)}",
        f"five-hour-reserve-percent: {max(0, min(100, int(routing.get('five_hour_reserve_percent', 30))))}",
        f"session-affinity: {'true' if routing.get('session_affinity', True) else 'false'}",
        f"session-affinity-idle-seconds: {max(60, min(604800, int(routing.get('session_affinity_idle_seconds', 86400))))}",
        f"codex-websockets: {'true' if routing.get('codex_websockets', True) else 'false'}",
        f"claude-cloak: {'true' if routing.get('claude_cloak', True) else 'false'}",
        f"force-model-prefix: {'true' if routing.get('force_model_prefix', False) else 'false'}",
        "banked-resets: false",
        "debug: false",
        "notifications:",
        "  enabled: false",
        "  credential-ui-enabled: false",
    ]

    api_keys_map = state.get("api_keys") or {}
    for section, key_name in (
        ("claude", "claude-api-key"),
        ("codex", "codex-api-key"),
        ("gemini", "gemini-api-key"),
        ("kimi", "kimi-api-key"),
        ("xai", "xai-api-key"),
        ("meta", "meta-api-key"),
    ):
        entries = api_keys_map.get(section) or []
        if not entries:
            lines.append(f"{key_name}: []")
            continue
        lines.append(f"{key_name}:")
        for item in entries:
            if not isinstance(item, dict) or not item.get("api_key"):
                continue
            lines.append(f"  - api-key: {_yaml_quote(item['api_key'])}")
            if item.get("label"):
                lines.append(f"    label: {_yaml_quote(item['label'])}")
            if item.get("base_url"):
                validated = validate_upstream_url(str(item["base_url"]), allow_loopback=True)
                lines.append(f"    base-url: {_yaml_quote(validated)}")
            if item.get("prefix"):
                lines.append(f"    prefix: {_yaml_quote(item['prefix'])}")

    compat_entries = state.get("openai_compatibility") or []
    if not compat_entries:
        lines.append("openai-compatibility: []")
    else:
        lines.append("openai-compatibility:")
        for entry in compat_entries:
            if not isinstance(entry, dict) or not entry.get("base_url"):
                continue
            validated_base = validate_upstream_url(str(entry["base_url"]), allow_loopback=True)
            lines.append(f"  - name: {_yaml_quote(entry.get('name') or 'compat')}")
            lines.append(f"    base-url: {_yaml_quote(validated_base)}")
            keys = [k for k in (entry.get("api_keys") or []) if isinstance(k, str) and k.strip()]
            if keys:
                lines.append("    api-keys:")
                for k in keys:
                    lines.append(f"      - {_yaml_quote(k)}")
            else:
                lines.append("    api-keys: []")
            if entry.get("prefix"):
                lines.append(f"    prefix: {_yaml_quote(entry['prefix'])}")
            if entry.get("disabled"):
                lines.append("    disabled: true")
            models = entry.get("models") or []
            if models:
                lines.append("    models:")
                for m in models:
                    if isinstance(m, dict) and m.get("name"):
                        lines.append(f"      - name: {_yaml_quote(m['name'])}")
                        if m.get("alias"):
                            lines.append(f"        alias: {_yaml_quote(m['alias'])}")
                    elif isinstance(m, str) and m.strip():
                        lines.append(f"      - name: {_yaml_quote(m.strip())}")

    excluded = state.get("oauth_excluded_models") or {}
    if excluded:
        lines.append("oauth-excluded-models:")
        for prov, patterns in excluded.items():
            if not patterns:
                continue
            lines.append(f"  {_yaml_quote(prov)}:")
            for pat in patterns:
                lines.append(f"    - {_yaml_quote(pat)}")

    tiers_map = state.get("tiers") or {}
    rendered_tiers = {
        str(t).strip().lower(): [
            item for item in (targets or [])
            if isinstance(item, dict) and str(item.get("model") or "").strip()
        ]
        for t, targets in tiers_map.items()
        if str(t).strip().lower() in VALID_TIERS and isinstance(targets, list)
    }
    rendered_tiers = {k: v for k, v in rendered_tiers.items() if v}
    if rendered_tiers:
        lines.append("tiers:")
        for tier_name in VALID_TIERS:
            targets = rendered_tiers.get(tier_name)
            if not targets:
                continue
            lines.append(f"  {tier_name}:")
            for item in targets:
                lines.append(f"    - model: {_yaml_quote(str(item['model']).strip())}")
                thinking = str(item.get("thinking") or "").strip().lower()
                if thinking and thinking != "auto":
                    lines.append(f"      thinking: {_yaml_quote(thinking)}")

    prov_defaults = state.get("provider_defaults") or {}
    valid_pdefs: dict[str, dict[str, str]] = {}
    for prov, entry in prov_defaults.items():
        pid = str(prov).strip().lower()
        if pid not in PROVIDER_CAPABILITIES or not isinstance(entry, dict):
            continue
        m_val = str(entry.get("model") or "").strip()
        t_val = str(entry.get("thinking") or "").strip().lower()
        if m_val or (t_val and t_val != "auto"):
            valid_pdefs[pid] = {"model": m_val, "thinking": t_val or "auto"}
    if valid_pdefs:
        lines.append("provider-defaults:")
        for pid, entry in sorted(valid_pdefs.items()):
            lines.append(f"  {pid}:")
            if entry["model"]:
                lines.append(f"    model: {_yaml_quote(entry['model'])}")
            if entry["thinking"] and entry["thinking"] != "auto":
                lines.append(f"    thinking: {_yaml_quote(entry['thinking'])}")

    cfg_path = _config_yaml_path()
    _write_text_atomic(cfg_path, "\n".join(lines) + "\n", mode=0o600)
    return cfg_path


# ---------------------------------------------------------------------------
# Backend abstraction
# ---------------------------------------------------------------------------


@dataclass
class BackendDiscovery:
    backend_id: str
    installed: bool
    binary_path: Optional[str]
    version: Optional[str]
    compatible: bool
    min_version: str
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "installed": self.installed,
            "binary_path": self.binary_path,
            "version": self.version,
            "compatible": self.compatible,
            "min_version": self.min_version,
            "error": self.error,
        }


class ProxyBackend(abc.ABC):
    """Replaceable backend contract for Oasis's optional local inference proxy."""

    backend_id: str = "generic"

    @abc.abstractmethod
    def discover(self, configured_path: str = "", *, exact_only: bool = False) -> BackendDiscovery: ...

    @abc.abstractmethod
    def health(self, port: int, management_key: str) -> dict[str, Any]: ...

    @abc.abstractmethod
    def overview(self, port: int, management_key: str) -> tuple[bool, Any]: ...

    @abc.abstractmethod
    def list_accounts(
        self, port: int, management_key: str, extra_secrets: Optional[list[str]] = None
    ) -> tuple[bool, list[dict[str, Any]], Optional[str]]: ...

    @abc.abstractmethod
    def list_models(
        self, port: int, management_key: str, state: Optional[dict[str, Any]] = None
    ) -> tuple[bool, list[dict[str, Any]], Optional[str]]: ...

    @abc.abstractmethod
    def recent_requests(
        self, port: int, management_key: str, extra_secrets: Optional[list[str]] = None
    ) -> tuple[bool, list[dict[str, Any]], Optional[str]]: ...

    @abc.abstractmethod
    def toggle_account(self, port: int, management_key: str, account_id: str, disabled: bool) -> tuple[bool, Optional[str]]: ...

    @abc.abstractmethod
    def reset_account_cooldown(self, port: int, management_key: str, account_id: str) -> tuple[bool, Optional[str]]: ...

    @abc.abstractmethod
    def refresh_quota(self, port: int, management_key: str, account_id: str) -> tuple[bool, Optional[str]]: ...

    @abc.abstractmethod
    def delete_account(self, port: int, management_key: str, account_id: str) -> tuple[bool, Optional[str]]: ...

    @abc.abstractmethod
    def patch_routing(self, port: int, management_key: str, changes: dict[str, Any]) -> tuple[bool, Optional[str]]: ...

    @abc.abstractmethod
    def start_login(self, port: int, management_key: str, backend_provider: str) -> tuple[bool, Any]: ...

    @abc.abstractmethod
    def poll_login(self, port: int, management_key: str, login_state: str) -> tuple[bool, Any]: ...


class CliProxyApiRustBackend(ProxyBackend):
    """Concrete adapter for upstream CLIProxyAPI-Rust (/healthz + /api/*)."""

    backend_id = "cliproxyapi-rust"

    def discover(self, configured_path: str = "", *, exact_only: bool = False) -> BackendDiscovery:
        min_str = ".".join(str(x) for x in MIN_BACKEND_VERSION)
        candidates: list[Path] = []
        if exact_only:
            if configured_path.strip():
                candidates.append(Path(configured_path.strip()).expanduser())
        else:
            env_override = os.environ.get("OASIS_PROXY_BIN", "").strip()
            for raw in (configured_path.strip(), env_override):
                if raw:
                    candidates.append(Path(raw).expanduser())
            candidates.extend([
                _bundled_bin_path(),
                _local_bin_path(),
                Path("/usr/local/bin/oasis-proxy"),
                Path("/usr/bin/oasis-proxy"),
            ])
            for bin_name in ("oasis-proxy", "cliproxyapi-rust"):
                which_path = shutil.which(bin_name, path=_enriched_env().get("PATH"))
                if which_path:
                    candidates.append(Path(which_path))

        found: Optional[Path] = None
        seen: set[str] = set()
        for cand in candidates:
            key = str(cand)
            if key in seen:
                continue
            seen.add(key)
            if cand.is_file() and os.access(cand, os.X_OK):
                found = cand
                break

        if not found:
            return BackendDiscovery(
                backend_id=self.backend_id,
                installed=False,
                binary_path=None,
                version=None,
                compatible=False,
                min_version=min_str,
                error="Oasis Proxy engine is not bundled yet — run ./install.sh to build and bundle it",
            )

        try:
            proc = subprocess.run(
                [str(found), "--version"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            out = (proc.stdout or proc.stderr or "").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return BackendDiscovery(
                backend_id=self.backend_id,
                installed=True,
                binary_path=str(found),
                version=None,
                compatible=False,
                min_version=min_str,
                error=f"failed to query backend version: {exc}",
            )

        ver_tuple = _parse_semver(out)
        if ver_tuple is None:
            return BackendDiscovery(
                backend_id=self.backend_id,
                installed=True,
                binary_path=str(found),
                version=out[:40] or None,
                compatible=False,
                min_version=min_str,
                error="could not parse backend version",
            )
        ver_str = ".".join(str(x) for x in ver_tuple)
        compatible = ver_tuple >= MIN_BACKEND_VERSION
        return BackendDiscovery(
            backend_id=self.backend_id,
            installed=True,
            binary_path=str(found),
            version=ver_str,
            compatible=compatible,
            min_version=min_str,
            error=None if compatible else f"backend {ver_str} is older than required {min_str}",
        )

    def _normalize_error(self, status: int, data: Any, net_err: Optional[str], secrets_list: list[str]) -> str:
        if net_err:
            return redact_secrets(net_err, secrets_list)[:240]
        if status == 401:
            return "management authentication failed (key mismatch)"
        if status == 403:
            return "management access denied"
        if status == 404:
            return "resource not found on proxy backend"
        if status == 409:
            return "proxy config changed concurrently; retry"
        if isinstance(data, dict) and data.get("error"):
            # Always redact BEFORE truncating so secrets straddling the cutoff cannot leak.
            return redact_secrets(str(data["error"]), secrets_list)[:240]
        return f"proxy backend returned HTTP {status}"

    def health(self, port: int, management_key: str) -> dict[str, Any]:
        st, body, err = _local_http("GET", port, "/healthz", timeout=2.0)
        if st != 200 or str(body).strip().lower() != "ok":
            return {
                "running": False,
                "ready": False,
                "auth_ok": False,
                "error": self._normalize_error(st, body, err, [management_key]) if st or err else "proxy stopped",
            }
        ov_st, ov_data, ov_err = _local_http("GET", port, "/api/overview", bearer=management_key, timeout=2.5)
        if ov_st == 200 and isinstance(ov_data, dict):
            return {
                "running": True,
                "ready": True,
                "auth_ok": True,
                "version": str(ov_data.get("version") or ""),
                "uptime_secs": int(ov_data.get("uptime_secs") or 0),
                "error": None,
            }
        return {
            "running": True,
            "ready": False,
            "auth_ok": False,
            "error": self._normalize_error(ov_st, ov_data, ov_err, [management_key]),
        }

    def overview(self, port: int, management_key: str) -> tuple[bool, Any]:
        st, data, err = _local_http("GET", port, "/api/overview", bearer=management_key)
        if st == 200 and isinstance(data, dict):
            sanitized = {k: v for k, v in data.items() if k != "client_keys"}
            sanitized["client_keys_configured"] = bool(data.get("client_keys"))
            return True, sanitized
        return False, self._normalize_error(st, data, err, [management_key])

    def list_accounts(
        self, port: int, management_key: str, extra_secrets: Optional[list[str]] = None
    ) -> tuple[bool, list[dict[str, Any]], Optional[str]]:
        sec_list = [management_key] + (extra_secrets or [])
        st, data, err = _local_http("GET", port, "/api/accounts", bearer=management_key)
        if st != 200 or not isinstance(data, list):
            return False, [], self._normalize_error(st, data, err, sec_list)
        out: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            raw_prov = str(item.get("provider") or "").lower()
            group = str(item.get("group") or "").lower()
            oasis_prov = BACKEND_TO_OASIS_PROVIDER.get(raw_prov, raw_prov)
            if raw_prov == "openai-compat":
                if group.startswith("qwen"):
                    oasis_prov = "qwen"
                elif group.startswith("opencode"):
                    oasis_prov = "opencodego"
            kind = str(item.get("kind") or "oauth")
            billing = "metered" if kind == "api-key" and oasis_prov not in ("opencodego",) else "subscription"
            if oasis_prov == "qwen" and kind == "api-key":
                billing = "subscription" if "coding" in group else "metered"
            ownership = "synced_api_key" if kind == "api-key" else "proxy_isolated"
            last_err = item.get("last_error")
            out.append({
                "id": str(item.get("id") or ""),
                "provider": oasis_prov,
                "backend_provider": raw_prov,
                "label": str(item.get("label") or item.get("email") or oasis_prov),
                "email": item.get("email"),
                "kind": kind,
                "group": item.get("group"),
                "billing": billing,
                "ownership": ownership,
                "disabled": bool(item.get("disabled")),
                "cooldowns": item.get("cooldowns") if isinstance(item.get("cooldowns"), dict) else {},
                "last_error": redact_secrets(str(last_err), sec_list)[:240] if last_err else None,
                "last_used": item.get("last_used"),
                "expires_at": item.get("expires_at"),
                "counters": item.get("counters") if isinstance(item.get("counters"), dict) else {},
                "quota": item.get("quota") if isinstance(item.get("quota"), dict) else {},
                "models": item.get("models") if isinstance(item.get("models"), list) else [],
            })
        return True, out, None

    def list_models(
        self, port: int, management_key: str, state: Optional[dict[str, Any]] = None
    ) -> tuple[bool, list[dict[str, Any]], Optional[str]]:
        st, data, err = _local_http("GET", port, "/api/models", bearer=management_key)
        if st != 200 or not isinstance(data, list):
            return False, [], self._normalize_error(st, data, err, [management_key])

        # Build lookup from configured openai-compatibility entries so Qwen & OpenCode Go models
        # resolve back to their Oasis provider IDs instead of generic "compat".
        compat_model_map: dict[str, str] = {}
        for entry in (state or {}).get("openai_compatibility") or []:
            if not isinstance(entry, dict):
                continue
            gname = str(entry.get("name") or "").lower()
            prefix = str(entry.get("prefix") or "").lower()
            target_prov = "compat"
            if gname.startswith("qwen") or prefix == "qwen":
                target_prov = "qwen"
            elif gname.startswith("opencode") or prefix == "opencode-go":
                target_prov = "opencodego"
            for m in entry.get("models") or []:
                mname = (m.get("alias") or m.get("name")) if isinstance(m, dict) else (m if isinstance(m, str) else "")
                if mname:
                    compat_model_map[mname.lower()] = target_prov
                    if prefix:
                        compat_model_map[f"{prefix}/{mname}".lower()] = target_prov

        out: list[dict[str, Any]] = []
        for m in data:
            if not isinstance(m, dict) or not m.get("id"):
                continue
            mid = str(m["id"])
            raw_prov = str(m.get("provider") or "").lower()
            oasis_prov = BACKEND_TO_OASIS_PROVIDER.get(raw_prov, raw_prov)
            if raw_prov == "openai-compat":
                oasis_prov = compat_model_map.get(mid.lower(), "compat")
                if oasis_prov == "compat":
                    low = mid.lower()
                    if low.startswith("qwen"):
                        oasis_prov = "qwen"
                    elif low.startswith("opencode-go/"):
                        oasis_prov = "opencodego"
            out.append({
                "id": mid,
                "provider": oasis_prov,
                "backend_provider": raw_prov,
            })
        return True, out, None

    def recent_requests(
        self, port: int, management_key: str, extra_secrets: Optional[list[str]] = None
    ) -> tuple[bool, list[dict[str, Any]], Optional[str]]:
        sec_list = [management_key] + (extra_secrets or [])
        st, data, err = _local_http("GET", port, "/api/requests", bearer=management_key)
        if st != 200 or not isinstance(data, list):
            return False, [], self._normalize_error(st, data, err, sec_list)
        out: list[dict[str, Any]] = []
        for r in data[:50]:
            if not isinstance(r, dict):
                continue
            raw_prov = str(r.get("provider") or "").lower()
            err_msg = r.get("error")
            out.append({
                "id": r.get("id"),
                "ts": r.get("ts"),
                "client": r.get("client"),
                "provider": BACKEND_TO_OASIS_PROVIDER.get(raw_prov, raw_prov),
                "model": r.get("model"),
                "account": r.get("account"),
                "routing_strategy": r.get("routing_strategy"),
                "routing_reason": r.get("routing_reason"),
                "status": r.get("status"),
                "latency_ms": r.get("latency_ms"),
                "ttft_ms": r.get("ttft_ms"),
                "input_tokens": r.get("input_tokens", 0),
                "output_tokens": r.get("output_tokens", 0),
                "cache_tokens": r.get("cache_tokens", 0),
                "stream": bool(r.get("stream")),
                "transport": r.get("transport"),
                "error": redact_secrets(str(err_msg), sec_list)[:240] if err_msg else None,
            })
        return True, out, None

    def toggle_account(self, port: int, management_key: str, account_id: str, disabled: bool) -> tuple[bool, Optional[str]]:
        try:
            clean_id = _validate_account_id(account_id)
        except ValueError as exc:
            return False, str(exc)
        enc = urllib.parse.quote(clean_id, safe="")
        st, data, err = _local_http(
            "POST", port, f"/api/accounts/{enc}/toggle", bearer=management_key, payload={"disabled": bool(disabled)}
        )
        if st == 200:
            return True, None
        return False, self._normalize_error(st, data, err, [management_key])

    def reset_account_cooldown(self, port: int, management_key: str, account_id: str) -> tuple[bool, Optional[str]]:
        try:
            clean_id = _validate_account_id(account_id)
        except ValueError as exc:
            return False, str(exc)
        enc = urllib.parse.quote(clean_id, safe="")
        st, data, err = _local_http("POST", port, f"/api/accounts/{enc}/reset", bearer=management_key)
        if st == 200:
            return True, None
        return False, self._normalize_error(st, data, err, [management_key])

    def refresh_quota(self, port: int, management_key: str, account_id: str) -> tuple[bool, Optional[str]]:
        try:
            clean_id = _validate_account_id(account_id)
        except ValueError as exc:
            return False, str(exc)
        enc = urllib.parse.quote(clean_id, safe="")
        st, data, err = _local_http("POST", port, f"/api/accounts/{enc}/quota/refresh", bearer=management_key, timeout=15.0)
        if st == 200:
            return True, None
        return False, self._normalize_error(st, data, err, [management_key])

    def delete_account(self, port: int, management_key: str, account_id: str) -> tuple[bool, Optional[str]]:
        try:
            clean_id = _validate_account_id(account_id)
        except ValueError as exc:
            return False, str(exc)
        enc = urllib.parse.quote(clean_id, safe="")
        st, data, err = _local_http("DELETE", port, f"/api/accounts/{enc}", bearer=management_key)
        if st == 200:
            return True, None
        return False, self._normalize_error(st, data, err, [management_key])

    def patch_routing(self, port: int, management_key: str, changes: dict[str, Any]) -> tuple[bool, Optional[str]]:
        st, cur, err = _local_http("GET", port, "/api/config/settings", bearer=management_key)
        if st != 200 or not isinstance(cur, dict) or not cur.get("revision"):
            return False, self._normalize_error(st, cur, err, [management_key])
        pst, pdata, perr = _local_http(
            "PATCH",
            port,
            "/api/config/settings",
            bearer=management_key,
            payload={"revision": cur["revision"], "changes": changes},
        )
        if pst == 200:
            return True, None
        return False, self._normalize_error(pst, pdata, perr, [management_key])

    def start_login(self, port: int, management_key: str, backend_provider: str) -> tuple[bool, Any]:
        if not VALID_PROVIDER_ID_RE.match(backend_provider):
            return False, "invalid backend provider"
        enc = urllib.parse.quote(backend_provider, safe="")
        st, data, err = _local_http("POST", port, f"/api/login/{enc}", bearer=management_key, timeout=12.0)
        if st == 200 and isinstance(data, dict):
            return True, data
        return False, self._normalize_error(st, data, err, [management_key])

    def poll_login(self, port: int, management_key: str, login_state: str) -> tuple[bool, Any]:
        try:
            clean_state = _validate_account_id(login_state)
        except ValueError as exc:
            return False, str(exc)
        enc = urllib.parse.quote(clean_state, safe="")
        st, data, err = _local_http("GET", port, f"/api/login/{enc}", bearer=management_key)
        if st == 200 and isinstance(data, dict):
            return True, data
        return False, self._normalize_error(st, data, err, [management_key])


def get_backend(backend_id: str = "cliproxyapi-rust") -> ProxyBackend:
    if backend_id in ("", "cliproxyapi-rust"):
        return CliProxyApiRustBackend()
    raise ValueError(f"unsupported proxy backend: {backend_id}")


# ---------------------------------------------------------------------------
# Lifecycle management (systemd --user + non-systemd verified-PID fallback)
# ---------------------------------------------------------------------------


def _systemd_available() -> bool:
    if os.environ.get("OASIS_PROXY_NO_SYSTEMD") == "1":
        return False
    if not shutil.which("systemctl"):
        return False
    try:
        res = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _systemd_escape_exec_arg(value: str) -> str:
    if any(ch in value for ch in ("\n", "\r", "\0")):
        raise ValueError("path contains forbidden control characters")
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _systemd_escape_path(value: str) -> str:
    if any(ch in value for ch in ("\n", "\r", "\0")):
        raise ValueError("path contains forbidden control characters")
    if not value.startswith("/"):
        raise ValueError("systemd WorkingDirectory must be an absolute path")
    return value.replace("%", "%%")


def _antigravity_env_path() -> Path:
    return _proxy_dir() / "antigravity.env"


def _discover_local_antigravity_oauth_client() -> Optional[tuple[str, str]]:
    """Discover public desktop OAuth client for Antigravity from env or local IDE/CLI (never hardcoded)."""
    cid = (os.environ.get("OASIS_ANTIGRAVITY_CLIENT_ID") or os.environ.get("ANTIGRAVITY_CLIENT_ID") or "").strip()
    csec = (os.environ.get("OASIS_ANTIGRAVITY_CLIENT_SECRET") or os.environ.get("ANTIGRAVITY_CLIENT_SECRET") or "").strip()
    if cid and csec:
        return cid, csec
    # Avoid scanning host IDE binaries during isolated unit tests with fake HOME.
    try:
        import pwd

        if str(_home().resolve()) != str(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()):
            return None
    except Exception:
        pass
    try:
        helper_dir = str(Path(__file__).resolve().parent)
        if helper_dir not in sys.path:
            sys.path.insert(0, helper_dir)
        import ai_limits  # type: ignore[import-not-found]

        pairs = ai_limits._antigravity_clients()
        if pairs:
            return str(pairs[0][0]), str(pairs[0][1])
    except Exception:
        pass
    return None


def _ensure_antigravity_env_file() -> Optional[Path]:
    """Write 0600 EnvironmentFile for Antigravity OAuth refresh when a local client is detected."""
    pair = _discover_local_antigravity_oauth_client()
    if not pair:
        return None
    cid, csec = pair
    if any(ch in cid + csec for ch in ("\n", "\r", "\0")):
        return None
    env_path = _antigravity_env_path()
    content = (
        f"OASIS_ANTIGRAVITY_CLIENT_ID={cid}\n"
        f"OASIS_ANTIGRAVITY_CLIENT_SECRET={csec}\n"
    )
    _write_text_atomic(env_path, content, mode=0o600, private_parent=True)
    return env_path


def _render_systemd_unit(binary_path: str, cfg_path: Path) -> str:
    b_q = _systemd_escape_exec_arg(binary_path)
    c_q = _systemd_escape_exec_arg(str(cfg_path))
    wd = _systemd_escape_path(str(cfg_path.parent))
    ag_env = _systemd_escape_path(str(cfg_path.parent / "antigravity.env"))
    return (
        "[Unit]\n"
        "Description=Oasis Proxy — Local AI Inference Service\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={b_q} --config {c_q}\n"
        f"WorkingDirectory={wd}\n"
        f"EnvironmentFile=-{ag_env}\n"
        "UMask=0077\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
        "KillSignal=SIGTERM\n"
        "TimeoutStopSec=15\n"
        "NoNewPrivileges=true\n"
        "PrivateTmp=true\n"
        "UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _install_systemd_unit(binary_path: str, cfg_path: Path) -> Path:
    _ensure_antigravity_env_file()
    unit_path = _systemd_unit_path()
    _write_text_atomic(unit_path, _render_systemd_unit(binary_path, cfg_path), mode=0o644, private_parent=False)
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=6, check=False)
    return unit_path


def _systemd_is_active() -> bool:
    if not _systemd_available():
        return False
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", SYSTEMD_UNIT_NAME],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return res.returncode == 0 and res.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


def _wait_ready(
    backend: ProxyBackend,
    port: int,
    management_key: str,
    *,
    proc: Optional[subprocess.Popen[bytes]] = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {"running": False, "ready": False, "auth_ok": False, "error": "timed out"}
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return {
                "running": False,
                "ready": False,
                "auth_ok": False,
                "error": f"proxy exited immediately with status {proc.returncode}",
            }
        last = backend.health(port, management_key)
        if last.get("ready"):
            return last
        time.sleep(0.08)
    return last


def _wait_stopped(port: int, pid: Optional[int], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid_down = (pid is None) or (not _pid_alive(pid))
        port_down = not _port_in_use(DEFAULT_HOST, port)
        if pid_down and port_down:
            return True
        time.sleep(0.08)
    return False


def _spawn_direct(binary_path: str, cfg_path: Path) -> subprocess.Popen[bytes]:
    env = _enriched_env(strip_proxies=True)
    ag_pair = _discover_local_antigravity_oauth_client()
    if ag_pair:
        env.setdefault("OASIS_ANTIGRAVITY_CLIENT_ID", ag_pair[0])
        env.setdefault("OASIS_ANTIGRAVITY_CLIENT_SECRET", ag_pair[1])
    log_path = _proxy_dir() / "proxy.log"
    _reject_symlink(log_path)
    try:
        if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
            log_path.unlink(missing_ok=True)
    except OSError:
        pass
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchmod(log_fd, 0o600)

        def _preexec() -> None:
            os.umask(0o077)
            os.setsid()

        return subprocess.Popen(
            [binary_path, "--config", str(cfg_path)],
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            cwd=str(_proxy_dir()),
            env=env,
            preexec_fn=_preexec,
            close_fds=True,
        )
    finally:
        os.close(log_fd)


def _start_proxy_locked(*, port_override: Optional[int] = None, binary_override: str = "") -> dict[str, Any]:
    state = load_state(create_if_missing=True)
    assert state is not None
    policy = load_policy()
    if port_override is not None:
        state["port"] = _validate_port(port_override)
    if binary_override:
        state["binary_path"] = binary_override.strip()

    backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
    disc = backend.discover(state.get("binary_path") or "")
    if not disc.installed or not disc.binary_path:
        return {"ok": False, "error": disc.error or "Proxy backend binary not installed."}
    if not disc.compatible:
        return {"ok": False, "error": disc.error or "Proxy backend version is incompatible."}

    port = _validate_port(state["port"])
    mgmt_key = state["management_key"]

    current_health = backend.health(port, mgmt_key)
    if current_health.get("ready"):
        policy["mode"] = "proxy"
        save_policy(policy)
        return {
            "ok": True,
            "already_running": True,
            "endpoint": f"http://{DEFAULT_HOST}:{port}",
            "version": current_health.get("version") or disc.version,
        }

    if _port_in_use(DEFAULT_HOST, port):
        return {
            "ok": False,
            "error": f"Port {port} on 127.0.0.1 is already in use by another process (or a proxy with a different key).",
        }

    # Auto-import already signed-in CLI accounts & Oasis vault accounts so Oasis Proxy works out-of-the-box!
    synced_summary = _sync_cli_accounts_into_proxy_locked(state)
    save_state_and_render(state)
    cfg_path = _config_yaml_path()

    use_systemd = bool(policy.get("use_systemd", True)) and _systemd_available()
    managed_by = "direct"
    pid: Optional[int] = None
    proc: Optional[subprocess.Popen[bytes]] = None

    if use_systemd:
        _install_systemd_unit(disc.binary_path, cfg_path)
        res = subprocess.run(
            ["systemctl", "--user", "start", SYSTEMD_UNIT_NAME],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        if res.returncode != 0:
            err_txt = redact_secrets((res.stderr or res.stdout).strip(), _known_secrets(state))[:200]
            return {"ok": False, "error": f"systemd failed to start {SYSTEMD_UNIT_NAME}: {err_txt}"}
        managed_by = "systemd"
        pid_res = subprocess.run(
            ["systemctl", "--user", "show", "-p", "MainPID", "--value", SYSTEMD_UNIT_NAME],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        try:
            pid = int(pid_res.stdout.strip())
        except ValueError:
            pid = None
    else:
        proc = _spawn_direct(disc.binary_path, cfg_path)
        pid = proc.pid

    ready = _wait_ready(backend, port, mgmt_key, proc=proc, timeout=8.0)
    if not ready.get("ready"):
        if managed_by == "systemd":
            subprocess.run(["systemctl", "--user", "stop", SYSTEMD_UNIT_NAME], capture_output=True, timeout=6, check=False)
        elif proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        return {
            "ok": False,
            "error": f"Proxy started but failed readiness check: {ready.get('error') or 'unknown error'}",
        }

    stat_info = _proc_stat_info(pid) if pid else None
    _write_json_atomic(
        _runtime_path(),
        {
            "pid": pid,
            "proc_start_ticks": stat_info[1] if stat_info else None,
            "managed_by": managed_by,
            "port": port,
            "binary_path": disc.binary_path,
            "version": ready.get("version") or disc.version,
            "started_at": int(time.time()),
        },
        mode=0o600,
    )
    policy["mode"] = "proxy"
    save_policy(policy)

    # Auto-populate Tier Routing on first start when accounts are connected and no tiers are set yet.
    if not any((state.get("tiers") or {}).get(t) for t in VALID_TIERS):
        try:
            _auto_setup_tiers_locked(state, port=port, backend=backend)
        except Exception:
            pass

    return {
        "ok": True,
        "managed_by": managed_by,
        "pid": pid,
        "endpoint": f"http://{DEFAULT_HOST}:{port}",
        "version": ready.get("version") or disc.version,
        "synced_accounts": synced_summary.get("imported", 0),
    }


def _stop_proxy_locked(*, switch_to_direct: bool = False) -> dict[str, Any]:
    state = load_state(create_if_missing=False)
    runtime = _read_private_json(_runtime_path()) or {}
    port = _validate_port((state or {}).get("port") or runtime.get("port") or DEFAULT_PORT)
    managed_by = str(runtime.get("managed_by") or "")

    if managed_by == "systemd" or _systemd_is_active():
        subprocess.run(
            ["systemctl", "--user", "stop", SYSTEMD_UNIT_NAME],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        _wait_stopped(port, None, timeout=8.0)

    verified_pid = _verify_runtime_pid(runtime)
    if verified_pid is not None:
        try:
            os.kill(verified_pid, signal.SIGTERM)
        except OSError:
            pass
        if not _wait_stopped(port, verified_pid, timeout=8.0):
            # Re-verify PID before SIGKILL escalation
            if _verify_runtime_pid(runtime) == verified_pid:
                try:
                    os.kill(verified_pid, signal.SIGKILL)
                except OSError:
                    pass
                _wait_stopped(port, verified_pid, timeout=2.0)

    try:
        _runtime_path().unlink(missing_ok=True)
    except OSError:
        pass

    if switch_to_direct:
        policy = load_policy()
        policy["mode"] = "direct"
        save_policy(policy)

    still_listening = _port_in_use(DEFAULT_HOST, port)
    return {
        "ok": not still_listening,
        "running": still_listening,
        "mode": load_policy().get("mode", "direct"),
        "error": f"port {port} is still bound by an external process" if still_listening else None,
    }


def start_proxy(*, port_override: Optional[int] = None, binary_override: str = "") -> dict[str, Any]:
    with _control_lock():
        return _start_proxy_locked(port_override=port_override, binary_override=binary_override)


def stop_proxy(*, switch_to_direct: bool = False) -> dict[str, Any]:
    with _control_lock():
        return _stop_proxy_locked(switch_to_direct=switch_to_direct)


def restart_proxy(*, port_override: Optional[int] = None, binary_override: str = "") -> dict[str, Any]:
    with _control_lock():
        _stop_proxy_locked(switch_to_direct=False)
        return _start_proxy_locked(port_override=port_override, binary_override=binary_override)


def set_mode(mode: str) -> dict[str, Any]:
    clean = (mode or "").strip().lower()
    if clean not in ("direct", "proxy"):
        raise ValueError("mode must be 'direct' or 'proxy'")
    with _control_lock():
        if clean == "direct":
            res = _stop_proxy_locked(switch_to_direct=True)
            res["mode"] = "direct"
            return res
        policy = load_policy()
        policy["mode"] = "proxy"
        save_policy(policy)
        return {"ok": True, "mode": "proxy"}


# ---------------------------------------------------------------------------
# High-level operations: status, routing, pinning, API key sync, login, install
# ---------------------------------------------------------------------------


def _connected_clients_summary(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for req in requests:
        client = str(req.get("client") or "unknown")
        transport = str(req.get("transport") or "http")
        key = f"{client}:{transport}"
        entry = counts.setdefault(key, {"client": client, "transport": transport, "requests": 0, "last_ts": req.get("ts")})
        entry["requests"] += 1
    return list(counts.values())


def get_status(
    *,
    include_requests: bool = True,
    port_override: Optional[int] = None,
    binary_override: str = "",
) -> dict[str, Any]:
    """Return full sanitized proxy status for Oasis QML / CLI (NEVER contains secrets)."""
    policy = load_policy()
    state = load_state(create_if_missing=False)
    backend = get_backend((state or {}).get("backend_id") or "cliproxyapi-rust")
    configured_bin = binary_override.strip() or (state or {}).get("binary_path") or ""
    disc = backend.discover(configured_bin)
    port = _validate_port(port_override if port_override is not None else ((state or {}).get("port") or DEFAULT_PORT))
    mgmt_key = str((state or {}).get("management_key") or "")
    extra_sec = _known_secrets(state)
    runtime = _read_private_json(_runtime_path()) or {}

    systemd_avail = _systemd_available()
    systemd_active = _systemd_is_active() if systemd_avail else False

    health: dict[str, Any] = {"running": False, "ready": False, "auth_ok": False, "error": None}
    overview_data: dict[str, Any] = {}
    accounts: list[dict[str, Any]] = []
    models: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []

    if mgmt_key and _port_in_use(DEFAULT_HOST, port):
        health = backend.health(port, mgmt_key)
        if health.get("ready"):
            ok_ov, ov = backend.overview(port, mgmt_key)
            if ok_ov and isinstance(ov, dict):
                overview_data = ov
            ok_ac, acs, _ = backend.list_accounts(port, mgmt_key, extra_secrets=extra_sec)
            if ok_ac:
                accounts = acs
            ok_mo, mods, _ = backend.list_models(port, mgmt_key, state=state)
            if ok_mo:
                models = mods
            if include_requests:
                ok_rq, rqs, _ = backend.recent_requests(port, mgmt_key, extra_secrets=extra_sec)
                if ok_rq:
                    requests = rqs

    pins = policy.get("pinned_accounts") or {}
    for acc in accounts:
        pin_info = pins.get(acc["provider"])
        acc["pinned"] = bool(isinstance(pin_info, dict) and pin_info.get("account_id") == acc["id"])

    routing_cfg = dict((state or _default_state()).get("routing") or {})
    if overview_data.get("routing"):
        routing_cfg["strategy"] = overview_data["routing"]
    if "session_affinity" in overview_data:
        routing_cfg["session_affinity"] = bool(overview_data["session_affinity"])

    managed_by = runtime.get("managed_by") or ("systemd" if systemd_active else "none")
    if managed_by == "direct" and _verify_runtime_pid(runtime) is None and not health.get("running"):
        managed_by = "none"

    tiers_state = (state or {}).get("tiers") or {}
    tiers_out: dict[str, Any] = {}
    for tname in VALID_TIERS:
        raw_targets = tiers_state.get(tname) if isinstance(tiers_state, dict) else None
        clean_targets = []
        if isinstance(raw_targets, list):
            for item in raw_targets:
                if isinstance(item, dict) and str(item.get("model") or "").strip():
                    clean_targets.append({
                        "model": str(item["model"]).strip(),
                        "thinking": str(item.get("thinking") or "auto").strip().lower() or "auto",
                        "provider": str(item.get("provider") or "").strip().lower(),
                    })
        tiers_out[tname] = {
            "id": tname,
            "model_id": f"oasis/{tname}",
            "endpoint": f"http://{DEFAULT_HOST}:{port}/tier/{tname}",
            "openai_base_url": f"http://{DEFAULT_HOST}:{port}/tier/{tname}/v1",
            "anthropic_base_url": f"http://{DEFAULT_HOST}:{port}/tier/{tname}",
            "targets": clean_targets,
        }

    pdefs_raw = (state or {}).get("provider_defaults") or {}
    provider_defaults_out: dict[str, dict[str, str]] = {}
    for pid in PROVIDER_CAPABILITIES:
        entry = pdefs_raw.get(pid) if isinstance(pdefs_raw, dict) else None
        if isinstance(entry, dict):
            provider_defaults_out[pid] = {
                "model": str(entry.get("model") or "").strip(),
                "thinking": str(entry.get("thinking") or "auto").strip().lower() or "auto",
            }
        else:
            provider_defaults_out[pid] = {"model": "", "thinking": "auto"}

    intel_path = _intel_cache_path()
    intel_doc = _read_private_json(intel_path) if intel_path.is_file() else None
    intel_available = bool(
        isinstance(intel_doc, dict)
        and isinstance(intel_doc.get("model_tiers"), dict)
        and (intel_doc["model_tiers"].get("main_agent") or intel_doc["model_tiers"].get("subagent"))
    )
    catalog_models = _build_catalog_models(models, intel_doc=intel_doc)
    connected_providers = sorted({
        str(a.get("provider") or "").lower()
        for a in accounts
        if isinstance(a, dict) and not a.get("disabled") and a.get("provider")
    })
    syncable_static = detect_syncable_static_providers(state, connected_providers=connected_providers)

    return {
        "ok": True,
        "version": VERSION,
        "mode": policy.get("mode", "direct"),
        "backend": disc.to_dict(),
        "endpoint": f"http://{DEFAULT_HOST}:{port}",
        "openai_base_url": f"http://{DEFAULT_HOST}:{port}/v1",
        "anthropic_base_url": f"http://{DEFAULT_HOST}:{port}",
        "gemini_base_url": f"http://{DEFAULT_HOST}:{port}/v1beta",
        "default_auto_model": "oasis/auto",
        "port": port,
        "initialized": state is not None,
        "client_key_configured": bool(state and state.get("client_api_key")),
        "systemd_available": systemd_avail,
        "systemd_active": systemd_active,
        "managed_by": managed_by,
        "health": health,
        "active_requests": int(overview_data.get("active") or 0),
        "totals": overview_data.get("totals") or {
            "requests": 0,
            "ok": 0,
            "failed": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_tokens": 0,
        },
        "uptime_secs": int(overview_data.get("uptime_secs") or 0),
        "routing": routing_cfg,
        "policy": {
            "selected_provider": policy.get("selected_provider", "codex"),
            "selected_model": policy.get("selected_model", ""),
            "pinned_accounts": {
                prov: info.get("account_id")
                for prov, info in pins.items()
                if isinstance(info, dict) and info.get("account_id")
            },
            "allow_metered_fallback": bool(policy.get("allow_metered_fallback", False)),
            "allow_aggregator_overflow": bool(policy.get("allow_aggregator_overflow", True)),
            "use_systemd": bool(policy.get("use_systemd", True)),
        },
        "provider_defaults": provider_defaults_out,
        "tiers": tiers_out,
        "intel_available": intel_available,
        "connected_providers": connected_providers,
        "syncable_static_providers": syncable_static,
        "accounts": accounts,
        "models": models,
        "catalog_models": catalog_models,
        "connected_clients": _connected_clients_summary(requests),
        "recent_requests": requests[:15],
        "capabilities": PROVIDER_CAPABILITIES,
    }


def update_routing(
    *,
    strategy: Optional[str] = None,
    session_affinity: Optional[bool] = None,
    five_hour_reserve: Optional[int] = None,
    request_retry: Optional[int] = None,
    codex_websockets: Optional[bool] = None,
    claude_cloak: Optional[bool] = None,
    force_model_prefix: Optional[bool] = None,
    selected_provider: Optional[str] = None,
    selected_model: Optional[str] = None,
    allow_metered_fallback: Optional[bool] = None,
) -> dict[str, Any]:
    with _control_lock():
        policy = load_policy()
        if selected_provider is not None:
            if selected_provider and selected_provider not in PROVIDER_CAPABILITIES:
                raise ValueError(f"unknown provider: {selected_provider}")
            policy["selected_provider"] = selected_provider
        if selected_model is not None:
            policy["selected_model"] = _sanitize_label(selected_model)
        if allow_metered_fallback is not None:
            policy["allow_metered_fallback"] = bool(allow_metered_fallback)

        has_backend_routing_change = any(
            v is not None
            for v in (strategy, session_affinity, five_hour_reserve, request_retry, codex_websockets, claude_cloak, force_model_prefix)
        )
        if not has_backend_routing_change:
            # Pure Oasis UI/policy selection — do NOT create proxy keys/config.yaml in Direct CLI mode.
            save_policy(policy)
            state = load_state(create_if_missing=False)
            return {
                "ok": True,
                "applied_live": False,
                "warning": None,
                "routing": (state or _default_state()).get("routing", {}),
            }

        state = load_state(create_if_missing=True)
        assert state is not None
        routing = state.setdefault("routing", {})
        patch: dict[str, Any] = {}

        if strategy is not None:
            clean = strategy.strip().lower()
            if clean not in VALID_ROUTING_STRATEGIES:
                raise ValueError(f"invalid routing strategy: {clean}")
            routing["strategy"] = clean
            patch["routing"] = clean
        if session_affinity is not None:
            routing["session_affinity"] = bool(session_affinity)
            patch["session-affinity"] = bool(session_affinity)
        if five_hour_reserve is not None:
            val = max(0, min(100, int(five_hour_reserve)))
            routing["five_hour_reserve_percent"] = val
            patch["five-hour-reserve-percent"] = val
        if request_retry is not None:
            val = max(1, min(10, int(request_retry)))
            routing["request_retry"] = val
            patch["request-retry"] = val
        if codex_websockets is not None:
            routing["codex_websockets"] = bool(codex_websockets)
            patch["codex-websockets"] = bool(codex_websockets)
        if claude_cloak is not None:
            routing["claude_cloak"] = bool(claude_cloak)
            patch["claude-cloak"] = bool(claude_cloak)
        if force_model_prefix is not None:
            routing["force_model_prefix"] = bool(force_model_prefix)
            patch["force-model-prefix"] = bool(force_model_prefix)

        save_policy(policy)
        save_state_and_render(state)

        applied_live = False
        live_error: Optional[str] = None
        port = _validate_port(state["port"])
        if patch and _port_in_use(DEFAULT_HOST, port):
            backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
            ok, err = backend.patch_routing(port, state["management_key"], patch)
            applied_live = ok
            live_error = err

        return {"ok": True, "applied_live": applied_live, "warning": live_error, "routing": routing}


def pin_account(provider: str, account_id: str) -> dict[str, Any]:
    """Pin one proxy account for `provider`, rolling back toggled siblings on partial failure."""
    if provider not in PROVIDER_CAPABILITIES:
        raise ValueError(f"unknown provider: {provider}")
    clean_id = _validate_account_id(account_id)

    with _control_lock():
        state = load_state(create_if_missing=False)
        if not state:
            raise RuntimeError("Proxy is not initialized yet.")
        backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
        port = _validate_port(state["port"])
        mgmt_key = state["management_key"]
        ok, accounts, err = backend.list_accounts(port, mgmt_key, extra_secrets=_known_secrets(state))
        if not ok:
            raise RuntimeError(err or "Proxy must be running to pin an account.")

        siblings = [a for a in accounts if a["provider"] == provider]
        target = next((a for a in siblings if a["id"] == clean_id), None)
        if not target:
            raise ValueError(f"Account '{clean_id}' not found under provider '{provider}'.")

        policy = load_policy()
        pins = policy.setdefault("pinned_accounts", {})
        existing = pins.get(provider)
        prior_disabled: dict[str, bool] = (
            dict(existing.get("prior_disabled") or {})
            if isinstance(existing, dict)
            else {a["id"]: bool(a["disabled"]) for a in siblings}
        )
        for a in siblings:
            prior_disabled.setdefault(a["id"], bool(a["disabled"]))

        toggled_history: list[tuple[str, bool]] = []
        try:
            for a in siblings:
                desired_disabled = a["id"] != clean_id
                if bool(a["disabled"]) != desired_disabled:
                    t_ok, t_err = backend.toggle_account(port, mgmt_key, a["id"], desired_disabled)
                    if not t_ok:
                        raise RuntimeError(t_err or f"failed toggling {a['id']}")
                    toggled_history.append((a["id"], bool(a["disabled"])))
        except Exception:
            for aid, old_disabled in reversed(toggled_history):
                backend.toggle_account(port, mgmt_key, aid, old_disabled)
            raise

        pins[provider] = {"account_id": clean_id, "prior_disabled": prior_disabled}
        save_policy(policy)
        return {"ok": True, "provider": provider, "pinned_account_id": clean_id, "label": target["label"]}


def unpin_account(provider: str) -> dict[str, Any]:
    if provider not in PROVIDER_CAPABILITIES:
        raise ValueError(f"unknown provider: {provider}")
    with _control_lock():
        policy = load_policy()
        pins = policy.setdefault("pinned_accounts", {})
        existing = pins.pop(provider, None)
        save_policy(policy)

        state = load_state(create_if_missing=False)
        if state and isinstance(existing, dict):
            port = _validate_port(state["port"])
            if _port_in_use(DEFAULT_HOST, port):
                backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
                mgmt_key = state["management_key"]
                prior = existing.get("prior_disabled") or {}
                ok, accounts, _ = backend.list_accounts(port, mgmt_key, extra_secrets=_known_secrets(state))
                if ok:
                    for a in accounts:
                        if a["provider"] == provider and a["id"] in prior:
                            desired = bool(prior[a["id"]])
                            if bool(a["disabled"]) != desired:
                                backend.toggle_account(port, mgmt_key, a["id"], desired)
        return {"ok": True, "provider": provider, "pinned_account_id": None}


def sync_static_api_key(
    provider: str,
    api_key: str,
    *,
    label: str = "",
    region: str = "international",
    base_url: str = "",
    models: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Add a non-rotating static API key to Oasis's proxy configuration."""
    cap = PROVIDER_CAPABILITIES.get(provider)
    if not cap:
        raise ValueError(f"unknown provider: {provider}")
    if not cap.get("sync_static_key_allowed"):
        raise ValueError(f"{cap['name']} does not support static API key sync ({cap['ownership_note']})")

    clean_key = (api_key or "").strip()
    if not clean_key:
        raise ValueError("API key cannot be empty")
    if clean_key.startswith("dca:") or (clean_key.startswith("eyJ") and clean_key.count(".") == 2):
        raise ValueError("Refusing to sync an OAuth/device token as a static API key; use isolated proxy login instead.")

    clean_label = _sanitize_label(label, default=f"{cap['name']} API Key")
    clean_region = (region or "international").strip().lower()

    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None

        if provider == "qwen":
            if clean_region not in ("international", "china"):
                clean_region = "international"
            resolved_base = (
                validate_upstream_url(base_url)
                if base_url.strip()
                else (QWEN_CODING_BASES[clean_region] if clean_key.startswith("sk-sp-") else QWEN_API_BASES[clean_region])
            )
            group_name = f"qwen-{'coding' if clean_key.startswith('sk-sp-') else 'api'}-{clean_region}"
            model_list = [m.strip() for m in (models or QWEN_DEFAULT_MODELS) if m and m.strip()]
            _upsert_compat_entry(
                state,
                name=group_name,
                base_url=resolved_base,
                api_key=clean_key,
                prefix="qwen",
                models=model_list,
            )
        elif provider == "opencodego":
            resolved_base = validate_upstream_url(base_url) if base_url.strip() else "https://opencode.ai/zen/go/v1"
            requested = [m.strip() for m in (models or OPENCODEGO_DEFAULT_CHAT_MODELS) if m and m.strip()]
            safe_models = [m for m in requested if m.lower().startswith(OPENCODEGO_CHAT_PREFIXES)]
            rejected = [m for m in requested if m not in safe_models]
            if not safe_models:
                raise ValueError(
                    "OpenCode Go proxy only supports Chat Completions models "
                    f"({', '.join(OPENCODEGO_CHAT_PREFIXES)}). "
                    "Anthropic Messages (minimax/qwen) and Responses (muse-spark/grok/gpt) require Direct CLI mode."
                )
            _upsert_compat_entry(
                state,
                name="opencode-go",
                base_url=resolved_base,
                api_key=clean_key,
                prefix="opencode-go",
                models=safe_models,
            )
            save_state_and_render(state)
            return {
                "ok": True,
                "provider": provider,
                "label": clean_label,
                "models": safe_models,
                "rejected_non_chat_models": rejected,
            }
        else:
            backend_key = OASIS_TO_BACKEND_PROVIDER.get(provider)
            if not backend_key or backend_key not in state["api_keys"]:
                raise ValueError(f"static API key sync unsupported for {provider}")
            resolved_base = ""
            if base_url.strip():
                resolved_base = validate_upstream_url(base_url)
            elif provider == "kimi":
                resolved_base = KIMI_API_BASES.get(clean_region, KIMI_API_BASES["china"])
            entries: list[dict[str, Any]] = state["api_keys"][backend_key]
            existing = next((e for e in entries if e.get("api_key") == clean_key), None)
            if existing:
                existing["label"] = clean_label
                if resolved_base:
                    existing["base_url"] = resolved_base
            else:
                item: dict[str, Any] = {"api_key": clean_key, "label": clean_label}
                if resolved_base:
                    item["base_url"] = resolved_base
                entries.append(item)

        save_state_and_render(state)
        return {"ok": True, "provider": provider, "label": clean_label}


def _upsert_compat_entry(
    state: dict[str, Any],
    *,
    name: str,
    base_url: str,
    api_key: str,
    prefix: str,
    models: list[str],
) -> None:
    entries: list[dict[str, Any]] = state.setdefault("openai_compatibility", [])
    existing = next((e for e in entries if e.get("name") == name), None)
    model_objs = [{"name": m} for m in models]
    if existing:
        existing["base_url"] = base_url
        existing["prefix"] = prefix
        existing["models"] = model_objs
        keys = existing.setdefault("api_keys", [])
        if api_key not in keys:
            keys.append(api_key)
    else:
        entries.append({
            "name": name,
            "base_url": base_url,
            "api_keys": [api_key],
            "prefix": prefix,
            "models": model_objs,
        })


def _validate_thinking(spec: str) -> str:
    clean = (spec or "").strip().lower()
    if not clean or clean == "auto":
        return "auto"
    if clean in ("off", "none", "0"):
        return "none"
    if clean in VALID_THINKING_MODES:
        return clean
    if clean.isdigit():
        n = int(clean)
        if 1 <= n <= 200000:
            return str(n)
    raise ValueError(f"invalid thinking mode '{spec}' (use auto, none, low, medium, high, xhigh, max, or token budget)")


def _validate_model_id(model: str) -> str:
    clean = (model or "").strip()
    if len(clean) > 160 or any(ch in clean for ch in ("\n", "\r", "\0")):
        raise ValueError("invalid model identifier")
    return clean


def set_provider_default(
    provider: str,
    *,
    model: Optional[str] = None,
    thinking: Optional[str] = None,
) -> dict[str, Any]:
    """Configure a provider's default model and thinking/reasoning mode (hot-reloaded by Oasis Proxy)."""
    pid = (provider or "").strip().lower()
    if pid not in PROVIDER_CAPABILITIES:
        raise ValueError(f"unknown provider: {provider}")
    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        pdefs = state.setdefault("provider_defaults", {})
        entry = dict(pdefs.get(pid) or {"model": "", "thinking": "auto"})
        if model is not None:
            entry["model"] = _validate_model_id(model)
        if thinking is not None:
            entry["thinking"] = _validate_thinking(thinking)
        pdefs[pid] = entry
        save_state_and_render(state)
        policy = load_policy()
        policy["selected_provider"] = pid
        if entry["model"]:
            policy["selected_model"] = entry["model"]
        save_policy(policy)
        return {"ok": True, "provider": pid, "model": entry["model"], "thinking": entry["thinking"]}


def set_tier(
    tier: str,
    *,
    model: str = "",
    thinking: str = "auto",
    provider: str = "",
    fallbacks: Optional[list[dict[str, str]]] = None,
    clear: bool = False,
) -> dict[str, Any]:
    """Configure or clear one single-daemon tier route (`diamond`, `gold`, `silver`, `subagent`)."""
    tname = (tier or "").strip().lower()
    if tname not in VALID_TIERS:
        raise ValueError(f"unknown tier '{tier}' (must be one of: {', '.join(VALID_TIERS)})")
    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        tiers_map = state.setdefault("tiers", {})
        if clear:
            tiers_map.pop(tname, None)
            save_state_and_render(state)
            return {"ok": True, "tier": tname, "cleared": True, "targets": []}

        clean_model = _validate_model_id(model)
        if not clean_model:
            raise ValueError("model is required when configuring a tier (or pass --clear)")
        clean_thinking = _validate_thinking(thinking)
        clean_prov = (provider or "").strip().lower()
        targets: list[dict[str, str]] = [
            {"model": clean_model, "thinking": clean_thinking, "provider": clean_prov}
        ]
        for fb in fallbacks or []:
            if not isinstance(fb, dict):
                continue
            fm = _validate_model_id(str(fb.get("model") or ""))
            if not fm or fm == clean_model:
                continue
            ft = _validate_thinking(str(fb.get("thinking") or "auto"))
            fp = str(fb.get("provider") or "").strip().lower()
            targets.append({"model": fm, "thinking": ft, "provider": fp})

        tiers_map[tname] = targets
        save_state_and_render(state)
        port = _validate_port(state.get("port") or DEFAULT_PORT)
        return {
            "ok": True,
            "tier": tname,
            "model_id": f"oasis/{tname}",
            "endpoint": f"http://{DEFAULT_HOST}:{port}/tier/{tname}",
            "targets": targets,
        }


def copy_tier_endpoint(tier: str) -> dict[str, Any]:
    """Copy a tier's OpenAI-compatible base URL (`http://127.0.0.1:<port>/tier/<tier>/v1`) to clipboard."""
    tname = (tier or "").strip().lower()
    if tname not in VALID_TIERS:
        raise ValueError(f"unknown tier '{tier}'")
    with _control_lock():
        state = load_state(create_if_missing=False)
        port = _validate_port((state or {}).get("port") or DEFAULT_PORT)
    endpoint = f"http://{DEFAULT_HOST}:{port}/tier/{tname}/v1"
    tool = _copy_to_clipboard(endpoint)
    return {"ok": True, "tier": tname, "copied": "tier_endpoint", "endpoint": endpoint, "tool": tool}


def _intel_provider_hint(raw_provider: str, raw_model: str) -> str:
    low_p = (raw_provider or "").lower()
    low_m = (raw_model or "").lower()
    if "claude" in low_p or "anthropic" in low_p or "claude" in low_m:
        return "claude"
    if "openai" in low_p or "codex" in low_p or "gpt" in low_m or "o3" in low_m or "o4" in low_m:
        return "codex"
    if "gemini" in low_p or "google" in low_p or "antigravity" in low_p or "gemini" in low_m:
        return "antigravity"
    if "opencode" in low_p or "deepseek" in low_m or "glm" in low_m:
        return "opencodego"
    if "qwen" in low_p or "alibaba" in low_p or "dashscope" in low_p or "qwen" in low_m:
        return "qwen"
    if "kimi" in low_p or "moonshot" in low_p or "kimi" in low_m:
        return "kimi"
    if "grok" in low_p or "xai" in low_p or "grok" in low_m:
        return "grok"
    if "muse" in low_p or "meta" in low_p:
        return "muse"
    return ""


def _slug_tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _builtin_catalog_entries() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pid, mids in PROVIDER_BUILTIN_MODELS.items():
        for mid in mids:
            out.append({"id": mid, "provider": pid, "connected": False, "source": "builtin"})
    return out


def _build_catalog_models(
    live_models: list[dict[str, Any]],
    *,
    intel_doc: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Build complete selectable model list: live connected models first, then full engine catalog, then AI Intel."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()

    # 1. Live connected models from oasis-proxy /api/models (highest priority)
    for m in live_models or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        prov = str(m.get("provider") or "").strip().lower()
        if not mid or mid.startswith("oasis/"):
            continue
        key = (mid.lower(), prov)
        if key in seen:
            continue
        seen.add(key)
        seen_ids.add(mid.lower())
        out.append({"id": mid, "provider": prov, "connected": True, "source": "live"})

    # 2. Full built-in engine model catalog across all proxy-capable providers
    for pid, mids in PROVIDER_BUILTIN_MODELS.items():
        for mid in mids:
            key = (mid.lower(), pid)
            if key in seen:
                continue
            seen.add(key)
            seen_ids.add(mid.lower())
            out.append({"id": mid, "provider": pid, "connected": False, "source": "builtin"})

    # 3. Any extra models discovered by cached AI Intel scan
    if isinstance(intel_doc, dict) and isinstance(intel_doc.get("model_tiers"), dict):
        mt = intel_doc["model_tiers"]
        entries: list[Any] = []
        if isinstance(mt.get("main_agent"), list):
            entries.extend(mt["main_agent"])
        if isinstance(mt.get("subagent"), list):
            entries.extend(mt["subagent"])
        builtin_list = _builtin_catalog_entries()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw_m = str(entry.get("model") or "").strip()
            raw_p = str(entry.get("provider") or "").strip()
            if not raw_m:
                continue
            prov = _intel_provider_hint(raw_p, raw_m)
            if not prov:
                continue
            if _match_intel_model_to_live(raw_m, raw_p, builtin_list, allow_provider_fallback=False):
                continue
            slug = "-".join(_slug_tokens(raw_m))
            if not slug or slug in seen_ids:
                continue
            seen.add((slug, prov))
            seen_ids.add(slug)
            out.append({"id": slug, "provider": prov, "connected": False, "source": "intel"})

    return out


def _match_intel_model_to_live(
    intel_model: str,
    intel_provider: str,
    live_models: list[dict[str, Any]],
    *,
    allow_provider_fallback: bool = True,
) -> Optional[dict[str, Any]]:
    """Match an AI Intel model display name against live discovered proxy models."""
    hint_prov = _intel_provider_hint(intel_provider, intel_model)
    target_tokens = set(_slug_tokens(intel_model))
    if not target_tokens or not live_models:
        return None

    best_score = 0.0
    best_item: Optional[dict[str, Any]] = None
    for m in live_models:
        mid = str(m.get("id") or "")
        mprov = str(m.get("provider") or "").lower()
        if not mid or mid.startswith("oasis/"):
            continue
        if hint_prov and mprov and mprov != hint_prov:
            # Allow gemini/antigravity equivalence
            if not ({hint_prov, mprov} <= {"gemini", "antigravity"}):
                continue
        m_tokens = set(_slug_tokens(mid))
        overlap = len(target_tokens & m_tokens)
        if overlap == 0:
            continue
        # Bonus for matching exact version numbers or family words
        score = overlap / max(len(target_tokens), 1)
        if overlap >= 2:
            score += 0.5
        if mid.lower() == "-".join(_slug_tokens(intel_model)):
            score += 2.0
        if score > best_score:
            best_score = score
            best_item = m

    if best_item is not None and best_score >= 0.45:
        return best_item
    # Fallback: if we have connected models for the exact provider, pick the first matching family
    if allow_provider_fallback and hint_prov:
        same_prov = [
            m for m in live_models
            if not str(m.get("id") or "").startswith("oasis/")
            and (str(m.get("provider") or "").lower() == hint_prov or ({hint_prov, str(m.get("provider") or "").lower()} <= {"gemini", "antigravity"}))
        ]
        if same_prov:
            return same_prov[0]
    return None


def _default_thinking_for_intel(tier_name: str, entry: dict[str, Any]) -> str:
    hint_text = f"{entry.get('cost_per_task') or ''} {entry.get('benchmark') or ''}".lower()
    for lvl in ("xhigh", "high", "medium", "low"):
        if f"at {lvl}" in hint_text or f"({lvl})" in hint_text:
            return lvl
    return {
        "diamond": "high",
        "gold": "medium",
        "silver": "low",
        "subagent": "none",
    }.get(tier_name, "auto")


def import_intel_tiers(*, apply: bool = False) -> dict[str, Any]:
    """Preview or apply tier routing imported from Oasis AI Intel (`~/.cache/ai-limits/intel.json`)."""
    intel_path = _intel_cache_path()
    doc = _read_private_json(intel_path) if intel_path.is_file() else None
    if not isinstance(doc, dict) or not isinstance(doc.get("model_tiers"), dict):
        raise RuntimeError("No AI Intel scan found yet. Open ✦ Intel and run a scan first.")

    mt = doc["model_tiers"]
    main_list = mt.get("main_agent") if isinstance(mt.get("main_agent"), list) else []
    sub_list = mt.get("subagent") if isinstance(mt.get("subagent"), list) else []

    # Query live models when Oasis Proxy is running so we only map connected providers.
    state_now = load_state(create_if_missing=False)
    live_models: list[dict[str, Any]] = []
    proxy_running = False
    if state_now and state_now.get("management_key"):
        port = _validate_port(state_now.get("port") or DEFAULT_PORT)
        if _port_in_use(DEFAULT_HOST, port):
            backend = get_backend(state_now.get("backend_id") or "cliproxyapi-rust")
            ok_m, mods, _ = backend.list_models(port, state_now["management_key"], state=state_now)
            if ok_m:
                proxy_running = True
                live_models = mods

    bucket_entries: dict[str, list[dict[str, Any]]] = {
        "diamond": [e for e in main_list if isinstance(e, dict) and str(e.get("tier") or "").strip().lower() == "diamond"],
        "gold": [e for e in main_list if isinstance(e, dict) and str(e.get("tier") or "").strip().lower() == "gold"],
        "silver": [e for e in main_list if isinstance(e, dict) and str(e.get("tier") or "").strip().lower() == "silver"],
        "subagent": [e for e in sub_list if isinstance(e, dict)],
    }

    proposed: dict[str, list[dict[str, str]]] = {}
    skipped: list[dict[str, str]] = []
    preview_rows: list[dict[str, Any]] = []

    for tier_name in VALID_TIERS:
        entries = bucket_entries.get(tier_name) or []
        matched_targets: list[dict[str, str]] = []
        seen_models: set[str] = set()
        for entry in entries:
            raw_m = str(entry.get("model") or "").strip()
            raw_p = str(entry.get("provider") or "").strip()
            if not raw_m:
                continue
            thinking = _default_thinking_for_intel(tier_name, entry)
            prov_hint = _intel_provider_hint(raw_p, raw_m)

            if live_models:
                matched = _match_intel_model_to_live(raw_m, raw_p, live_models)
                if matched:
                    mid = str(matched["id"])
                    if mid not in seen_models:
                        seen_models.add(mid)
                        matched_targets.append({
                            "model": mid,
                            "thinking": thinking,
                            "provider": str(matched.get("provider") or prov_hint),
                            "intel_name": raw_m,
                        })
                else:
                    skipped.append({
                        "tier": tier_name,
                        "intel_name": raw_m,
                        "provider": prov_hint or raw_p,
                        "reason": "no connected account serves this model",
                    })
            else:
                # Proxy stopped or no isolated accounts yet: match exact built-in catalog IDs first, then normalized slug.
                cat_match = _match_intel_model_to_live(
                    raw_m, raw_p, _builtin_catalog_entries(), allow_provider_fallback=False
                )
                mid = str(cat_match["id"]) if cat_match else "-".join(_slug_tokens(raw_m))
                mprov = str(cat_match["provider"]) if cat_match else prov_hint
                if mid and mid not in seen_models:
                    seen_models.add(mid)
                    matched_targets.append({
                        "model": mid,
                        "thinking": thinking,
                        "provider": mprov,
                        "intel_name": raw_m,
                    })

        if matched_targets:
            proposed[tier_name] = matched_targets[:3]
        preview_rows.append({
            "tier": tier_name,
            "targets": proposed.get(tier_name, []),
            "matched": bool(matched_targets),
        })

    applied = False
    if apply:
        if not proposed:
            raise RuntimeError("None of the AI Intel tier models match your currently connected Oasis Proxy accounts.")
        with _control_lock():
            state = load_state(create_if_missing=True)
            assert state is not None
            tiers_map = state.setdefault("tiers", {})
            for tname, targets in proposed.items():
                tiers_map[tname] = [
                    {
                        "model": t["model"],
                        "thinking": t["thinking"],
                        "provider": t.get("provider", ""),
                    }
                    for t in targets
                ]
            save_state_and_render(state)
            applied = True

    return {
        "ok": True,
        "applied": applied,
        "proxy_running": proxy_running,
        "matched_live_models": bool(live_models),
        "generated_at": doc.get("generated_at"),
        "preview": preview_rows,
        "skipped": skipped,
    }


def rotate_client_key() -> dict[str, Any]:
    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        new_key = f"sk-oasis-{secrets.token_hex(32)}"
        state["client_api_key"] = new_key
        save_state_and_render(state)
        port = _validate_port(state["port"])
        reloaded_live = False
        if _port_in_use(DEFAULT_HOST, port):
            backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
            ok, _ = backend.patch_routing(port, state["management_key"], {"api-keys": [new_key]})
            reloaded_live = ok
        return {"ok": True, "rotated": True, "applied_live": reloaded_live}


def _copy_to_clipboard(text: str, *, sensitive: bool = False) -> str:
    env = _enriched_env()
    # On KDE Wayland, wl-copy accepts a secret hint MIME type or normal text.
    candidates: list[tuple[str, list[str]]] = [
        ("wl-copy", ["--type", "text/plain;charset=utf-8"]),
        ("xclip", ["-selection", "clipboard"]),
        ("xsel", ["--clipboard", "--input"]),
    ]
    for tool, args in candidates:
        found = shutil.which(tool, path=env.get("PATH"))
        if not found:
            continue
        try:
            proc = subprocess.Popen(
                [found] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
            assert proc.stdin is not None
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.close()
            try:
                rc = proc.wait(timeout=1.0)
                if rc == 0:
                    return tool
            except subprocess.TimeoutExpired:
                # wl-copy/xclip stay alive detached to serve X11/Wayland clipboard selection.
                return tool
        except OSError:
            continue
    raise RuntimeError("No clipboard utility found (install wl-clipboard, xclip, or xsel).")


def _looks_like_oauth_token(key: str) -> bool:
    clean = (key or "").strip()
    return clean.startswith("dca:") or (clean.startswith("eyJ") and clean.count(".") == 2)


def _read_local_static_api_key(provider: str) -> Optional[tuple[str, str]]:
    """Read a non-rotating static API key from a local CLI store when safe.

    Returns (api_key, region) or None. Strictly refuses rotating OAuth tokens.
    """
    if provider == "opencodego":
        # 1. OpenCode SQLite credential table (non-rotating static API key)
        db_path = _home() / ".local" / "share" / "opencode" / "opencode.db"
        if db_path.is_file() and not db_path.is_symlink():
            try:
                import sqlite3

                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.5)
                try:
                    rows = conn.execute(
                        "SELECT data FROM credential WHERE provider_id = 'opencode-go' ORDER BY active DESC, updated_at DESC LIMIT 3"
                    ).fetchall()
                    for (raw,) in rows:
                        doc = json.loads(raw) if isinstance(raw, str) else {}
                        key = str(doc.get("key") or doc.get("apiKey") or doc.get("api_key") or "").strip()
                        if key and not _looks_like_oauth_token(key):
                            return key, "international"
                finally:
                    conn.close()
            except Exception:
                pass
        # 2. Legacy OpenCode auth.json fallback
        auth_path = _home() / ".local" / "share" / "opencode" / "auth.json"
        if auth_path.is_file() and not auth_path.is_symlink():
            doc = _read_private_json(auth_path) or {}
            entry = doc.get("opencode-go")
            if isinstance(entry, dict):
                key = str(entry.get("key") or entry.get("apiKey") or entry.get("api_key") or "").strip()
                if key and not _looks_like_oauth_token(key):
                    return key, "international"
        return None

    if provider == "qwen":
        for env_name in ("QWEN_CODING_API_KEY", "DASHSCOPE_API_KEY"):
            key = os.environ.get(env_name, "").strip()
            if key and not _looks_like_oauth_token(key):
                return key, "international"
        return None

    if provider == "kimi":
        key = os.environ.get("KIMI_CODE_API_KEY", "").strip()
        if key and not _looks_like_oauth_token(key):
            return key, "china"
        return None

    return None


def detect_syncable_static_providers(
    state: Optional[dict[str, Any]],
    *,
    connected_providers: Optional[list[str]] = None,
) -> list[str]:
    """Return non-rotating static API-key providers detected locally and not yet synced to proxy."""
    already = set(connected_providers or [])
    for entry in (state or {}).get("openai_compatibility") or []:
        if not isinstance(entry, dict):
            continue
        nm = str(entry.get("name") or "").lower()
        if nm.startswith("opencode"):
            already.add("opencodego")
        elif nm.startswith("qwen"):
            already.add("qwen")
    for prov_key in ("kimi",):
        if ((state or {}).get("api_keys") or {}).get(prov_key):
            already.add(prov_key)

    found: list[str] = []
    for pid in ("opencodego", "qwen", "kimi"):
        if pid in already:
            continue
        if _read_local_static_api_key(pid) is not None:
            found.append(pid)
    return found


def sync_detected_static_key(provider: str) -> dict[str, Any]:
    """Sync a locally detected non-rotating static API key (e.g. OpenCode Go) into Oasis Proxy."""
    if provider not in ("opencodego", "qwen", "kimi"):
        raise ValueError(f"auto-sync is only allowed for static API-key providers (opencodego, qwen, kimi); got '{provider}'")
    found = _read_local_static_api_key(provider)
    if found is None:
        cap_name = PROVIDER_CAPABILITIES.get(provider, {}).get("name", provider)
        raise RuntimeError(f"No local static API key found for {cap_name}.")
    raw_key, region = found
    res = sync_static_api_key(provider, raw_key, region=region)
    res["synced_provider"] = provider
    return res


def _rank_model_for_auto(model_id: str) -> int:
    low = model_id.lower()
    ranked = (
        "claude-fable-5-2",
        "claude-fable-5-1",
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "gpt-6-astra",
        "claude-sonnet-5-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "gpt-6.1-sol",
        "gpt-6-sol",
        "gpt-5.6-sol",
        "gpt-5.4",
        "gpt-5.3-codex",
        "gemini-3.1-pro",
        "gemini-2.5-pro",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-2.5-flash",
        "deepseek-v4-pro",
        "deepseek-v3.2",
        "kimi-k3",
        "grok-4.7",
        "qwen3-coder-plus",
        "glm-5",
        "muse-spark-1.3",
    )
    for idx, pat in enumerate(ranked):
        if low == pat or low.startswith(pat):
            return idx
    if "image" in low or "video" in low:
        return 900
    return 500


def _jwt_claims_unsafe(token: str) -> dict[str, Any]:
    """Decode JWT payload claims without signature verification (local metadata only)."""
    import base64

    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        obj = json.loads(base64.urlsafe_b64decode(part))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _safe_file_slug(text: str, fallback: str = "default") -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", (text or "").strip().lower()).strip("-._")
    return slug[:48] if slug else fallback


def _epoch_from_any(val: Any) -> int:
    if isinstance(val, (int, float)) and val > 0:
        iv = int(val)
        return iv // 1000 if iv > 10_000_000_000 else iv
    if isinstance(val, str) and val.strip():
        s = val.strip()
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return int(dt.timestamp())
        except Exception:
            pass
    return 0


def _write_oauth_if_fresher(dest: Path, payload: dict[str, Any]) -> bool:
    """Write an oasis-proxy auths/*.json file unless an existing copy has a strictly newer expiry."""
    _ensure_private_dir(dest.parent)
    new_exp = _epoch_from_any(payload.get("expired"))
    if dest.is_file() and not dest.is_symlink():
        existing = _read_private_json(dest)
        if isinstance(existing, dict):
            old_exp = _epoch_from_any(existing.get("expired") or existing.get("expires_at"))
            if old_exp > 0 and (new_exp == 0 or old_exp >= new_exp) and existing.get("access_token"):
                return False
    _write_json_atomic(dest, payload, mode=0o600)
    return True


def _secret_tool_lookup(service: str, username: str) -> Optional[str]:
    """Read-only lookup from GNOME/KDE Secret Service (skipped when HOME is isolated in tests)."""
    if os.environ.get("OASIS_PROXY_NO_KEYRING") == "1":
        return None
    try:
        import pwd

        if str(_home().resolve()) != str(Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()):
            return None
    except Exception:
        pass
    tool = shutil.which("secret-tool")
    if not tool:
        return None
    try:
        res = subprocess.run(
            [tool, "lookup", "service", service, "username", username],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _parse_antigravity_raw_for_sync(raw: str) -> Optional[dict[str, Any]]:
    """Parse Antigravity keyring/vault credential blob into OAuth fields for Oasis Proxy."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("go-keyring-base64:"):
        try:
            text = base64.b64decode(text[len("go-keyring-base64:") :]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    if text.startswith("Bearer "):
        tok = text[7:].strip()
        return {"access_token": tok, "refresh_token": "", "expired": 0, "email": ""} if tok else None
    try:
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    token_obj = doc.get("token") if isinstance(doc.get("token"), dict) else doc
    access = str(token_obj.get("access_token") or token_obj.get("accessToken") or "").strip()
    refresh = str(token_obj.get("refresh_token") or token_obj.get("refreshToken") or "").strip()
    id_tok = str(token_obj.get("id_token") or doc.get("id_token") or "").strip()
    exp = _epoch_from_any(token_obj.get("expiry") or token_obj.get("expires_at") or token_obj.get("expiresAt"))
    email = str(doc.get("id_token_email") or doc.get("email") or "").strip()
    if not email and id_tok:
        claims = _jwt_claims_unsafe(id_tok)
        email = str(claims.get("email") or "").strip()

    # Reuse fresher cached access token from ai_limits.py if refresh fingerprint matches.
    if refresh:
        try:
            cache_file = Path(os.environ.get("XDG_CACHE_HOME") or (_home() / ".cache")) / "ai-limits" / "antigravity.json"
            cdoc = _read_private_json(cache_file) if cache_file.is_file() and not cache_file.is_symlink() else None
            if isinstance(cdoc, dict):
                rf_fp = hashlib.sha256(refresh.encode()).hexdigest()
                c_exp = _epoch_from_any(cdoc.get("expires_at"))
                c_acc = str(cdoc.get("access_token") or "").strip()
                if c_acc and cdoc.get("refresh_fingerprint") == rf_fp and c_exp > exp:
                    access = c_acc
                    exp = c_exp
        except Exception:
            pass

    if not access and not refresh:
        return None
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expired": exp,
        "email": email,
    }


def _collect_vault_snapshots(provider: str) -> list[tuple[str, dict[str, Any]]]:
    """Read saved account snapshots for `provider` from ~/.config/oasis/accounts (read-only)."""
    vault_dir = _oasis_dir() / "accounts"
    index_path = vault_dir / "index.json"
    secrets_dir = vault_dir / "secrets"
    if not index_path.is_file() or index_path.is_symlink():
        return []
    idx = _read_private_json(index_path)
    if not isinstance(idx, dict):
        return []
    prov_entry = (idx.get("providers") or {}).get(provider)
    if not isinstance(prov_entry, dict):
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for acc in prov_entry.get("accounts") or []:
        if not isinstance(acc, dict):
            continue
        aid = str(acc.get("id") or "").strip()
        label = str(acc.get("label") or aid or provider).strip()
        if not aid or "/" in aid or "\\" in aid or ".." in aid:
            continue
        sec_path = secrets_dir / provider / f"{aid}.json"
        if not sec_path.is_file() or sec_path.is_symlink():
            continue
        snap = _read_private_json(sec_path)
        if isinstance(snap, dict):
            out.append((label, snap))
    return out


def _sync_cli_accounts_into_proxy_locked(state: dict[str, Any]) -> dict[str, Any]:
    """Import already signed-in local CLI credentials & Oasis Accounts vault snapshots into Oasis Proxy.

    Read-only on native CLI files; writes 0600 auths/*.json and static compat entries.
    Never downgrades an existing proxy-refreshed token that has a newer expiry.
    """
    auths_dir = _auths_dir()
    _ensure_private_dir(auths_dir)
    imported_providers: list[str] = []
    imported_count = 0
    seen_codex_keys: set[str] = set()
    seen_claude_keys: set[str] = set()

    # 1. Codex: active ~/.codex/auth.json + saved vault snapshots
    codex_sources: list[tuple[str, dict[str, Any]]] = []
    codex_live = _home() / ".codex" / "auth.json"
    if codex_live.is_file() and not codex_live.is_symlink():
        doc = _read_private_json(codex_live)
        if isinstance(doc, dict):
            codex_sources.append(("active", doc))
    codex_sources.extend(_collect_vault_snapshots("codex"))

    for label, doc in codex_sources:
        # Vault snapshots may wrap the auth.json dict under "data" or "raw" or directly contain "tokens"
        raw_doc = doc.get("data") if isinstance(doc.get("data"), dict) else doc
        tokens = raw_doc.get("tokens") if isinstance(raw_doc.get("tokens"), dict) else raw_doc
        access = str(tokens.get("access_token") or "").strip()
        refresh = str(tokens.get("refresh_token") or "").strip()
        id_tok = str(tokens.get("id_token") or "").strip()
        if not access and not refresh:
            continue
        claims = _jwt_claims_unsafe(id_tok) or _jwt_claims_unsafe(access)
        email = str(claims.get("email") or ("" if label == "active" else label) or "codex-cli").strip()
        acc_id = str(
            tokens.get("account_id")
            or (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id")
            or ""
        ).strip()
        exp = _epoch_from_any(claims.get("exp"))
        dedup_key = (acc_id or email or access[:20]).lower()
        if dedup_key in seen_codex_keys:
            continue
        seen_codex_keys.add(dedup_key)
        slug = _safe_file_slug(email or acc_id, "codex")
        dest = auths_dir / f"codex-cli-{slug}.json"
        payload: dict[str, Any] = {
            "type": "codex",
            "access_token": access,
            "refresh_token": refresh,
            "email": email,
            "source": "oasis_cli_sync",
        }
        if id_tok:
            payload["id_token"] = id_tok
        if acc_id:
            payload["account_id"] = acc_id
        if exp > 0:
            payload["expired"] = exp
        if _write_oauth_if_fresher(dest, payload):
            imported_count += 1
        if "codex" not in imported_providers:
            imported_providers.append("codex")

    # 2. Claude: active ~/.claude/.credentials.json + saved vault snapshots
    claude_sources: list[tuple[str, dict[str, Any]]] = []
    claude_live = _home() / ".claude" / ".credentials.json"
    if claude_live.is_file() and not claude_live.is_symlink():
        doc = _read_private_json(claude_live)
        if isinstance(doc, dict):
            claude_sources.append(("active", doc))
    claude_sources.extend(_collect_vault_snapshots("claude"))

    for label, doc in claude_sources:
        raw_doc = doc.get("data") if isinstance(doc.get("data"), dict) else doc
        oauth_obj = raw_doc.get("claudeAiOauth") if isinstance(raw_doc.get("claudeAiOauth"), dict) else raw_doc
        access = str(oauth_obj.get("accessToken") or oauth_obj.get("access_token") or "").strip()
        refresh = str(oauth_obj.get("refreshToken") or oauth_obj.get("refresh_token") or "").strip()
        if not access and not refresh:
            continue
        exp = _epoch_from_any(oauth_obj.get("expiresAt") or oauth_obj.get("expires_at") or oauth_obj.get("expired"))
        email = str(
            oauth_obj.get("email")
            or raw_doc.get("email")
            or ("" if label == "active" else label)
            or "claude-cli"
        ).strip()
        dedup_key = (refresh[:24] if refresh else access[:24])
        if dedup_key in seen_claude_keys:
            continue
        seen_claude_keys.add(dedup_key)
        slug = _safe_file_slug(email, "claude")
        dest = auths_dir / f"claude-cli-{slug}.json"
        payload = {
            "type": "claude",
            "access_token": access,
            "refresh_token": refresh,
            "email": email,
            "source": "oasis_cli_sync",
        }
        if exp > 0:
            payload["expired"] = exp
        if _write_oauth_if_fresher(dest, payload):
            imported_count += 1
        if "claude" not in imported_providers:
            imported_providers.append("claude")

    # 3. Antigravity (Google Cloud Code): Secret Service live login + Oasis Accounts vault snapshots
    ag_sources: list[tuple[str, str]] = []
    ag_live_raw = _secret_tool_lookup("gemini", "antigravity")
    if ag_live_raw:
        ag_sources.append(("active", ag_live_raw))
    for label, snap in _collect_vault_snapshots("antigravity"):
        raw = snap.get("raw") if isinstance(snap.get("raw"), str) else ""
        if raw:
            ag_sources.append((label, raw))

    seen_ag_keys: set[str] = set()
    for label, raw in ag_sources:
        parsed = _parse_antigravity_raw_for_sync(raw)
        if not parsed:
            continue
        access = parsed["access_token"]
        refresh = parsed["refresh_token"]
        exp = parsed["expired"]
        email = parsed["email"] or ("" if label == "active" else label) or "antigravity-cli"
        dedup_key = (email.lower() if "@" in email else "") or (refresh[:24] if refresh else access[:24])
        if dedup_key in seen_ag_keys:
            continue
        seen_ag_keys.add(dedup_key)
        slug = _safe_file_slug(email, "antigravity")
        dest = auths_dir / f"antigravity-cli-{slug}.json"
        payload = {
            "type": "antigravity",
            "access_token": access,
            "refresh_token": refresh,
            "email": email,
            "source": "oasis_cli_sync",
        }
        if exp > 0:
            payload["expired"] = exp
        if _write_oauth_if_fresher(dest, payload):
            imported_count += 1
        if "antigravity" not in imported_providers:
            imported_providers.append("antigravity")
    if "antigravity" in imported_providers:
        _ensure_antigravity_env_file()

    # 4. Muse (Meta): ~/.config/muse/auth.json + saved vault snapshots
    muse_sources: list[tuple[str, dict[str, Any]]] = []
    muse_live = Path(os.environ.get("MUSE_AUTH_PATH") or (_home() / ".config" / "muse" / "auth.json"))
    if muse_live.is_file() and not muse_live.is_symlink():
        mdoc = _read_private_json(muse_live)
        if isinstance(mdoc, dict):
            muse_sources.append(("active", {"auth": mdoc}))
    muse_sources.extend(_collect_vault_snapshots("muse"))
    seen_muse_keys: set[str] = set()
    for label, snap in muse_sources:
        auth = snap.get("auth") if isinstance(snap.get("auth"), dict) else snap
        meta = ((auth.get("providers") or {}).get("meta") if isinstance(auth.get("providers"), dict) else {}) or {}
        tok = str(meta.get("access_token") or "").strip()
        if not tok or tok.startswith("dca:"):
            continue
        email = str(meta.get("email") or meta.get("user") or ("" if label == "active" else label) or "muse-cli").strip()
        dedup_key = tok[:24]
        if dedup_key in seen_muse_keys:
            continue
        seen_muse_keys.add(dedup_key)
        dest = auths_dir / f"meta-cli-{_safe_file_slug(email, 'muse')}.json"
        payload = {
            "type": "meta",
            "api_key": tok,
            "access_token": tok,
            "email": email,
            "source": "oasis_cli_sync",
        }
        if _write_oauth_if_fresher(dest, payload):
            imported_count += 1
        if "muse" not in imported_providers:
            imported_providers.append("muse")

    # 5. Grok: active ~/.grok/auth.json
    grok_live = _home() / ".grok" / "auth.json"
    if grok_live.is_file() and not grok_live.is_symlink():
        gdoc = _read_private_json(grok_live)
        if isinstance(gdoc, dict):
            access = str(gdoc.get("access_token") or gdoc.get("accessToken") or "").strip()
            refresh = str(gdoc.get("refresh_token") or gdoc.get("refreshToken") or "").strip()
            if access or refresh:
                exp = _epoch_from_any(gdoc.get("expires_at") or gdoc.get("expiresAt"))
                email = str(gdoc.get("email") or "grok-cli").strip()
                dest = auths_dir / f"xai-cli-{_safe_file_slug(email, 'grok')}.json"
                payload = {
                    "type": "xai",
                    "access_token": access,
                    "refresh_token": refresh,
                    "email": email,
                    "source": "oasis_cli_sync",
                }
                if exp > 0:
                    payload["expired"] = exp
                if _write_oauth_if_fresher(dest, payload):
                    imported_count += 1
                if "grok" not in imported_providers:
                    imported_providers.append("grok")

    # 4. Non-rotating static API keys (OpenCode Go, Qwen, Kimi) directly into state
    for pid in detect_syncable_static_providers(state):
        found = _read_local_static_api_key(pid)
        if not found:
            continue
        raw_key, region = found
        try:
            if pid == "opencodego":
                valid_models = [m for m in OPENCODEGO_DEFAULT_CHAT_MODELS if m.lower().startswith(OPENCODEGO_CHAT_PREFIXES)]
                compat = [c for c in (state.get("openai_compatibility") or []) if c.get("name") != "opencode-go"]
                compat.append({
                    "name": "opencode-go",
                    "base_url": "https://opencode.ai/zen/go/v1",
                    "api_keys": [raw_key],
                    "models": [{"name": m} for m in valid_models],
                })
                state["openai_compatibility"] = compat
                imported_count += 1
                if "opencodego" not in imported_providers:
                    imported_providers.append("opencodego")
            elif pid == "qwen":
                is_coding = raw_key.startswith("sk-sp-")
                base_map = QWEN_CODING_BASES if is_coding else QWEN_API_BASES
                base = base_map.get(region, base_map["international"])
                gname = f"qwen-{'coding' if is_coding else 'api'}-{region}"
                compat = [c for c in (state.get("openai_compatibility") or []) if not str(c.get("name") or "").startswith("qwen-")]
                compat.append({
                    "name": gname,
                    "base_url": base,
                    "api_keys": [raw_key],
                    "models": [{"name": m} for m in QWEN_DEFAULT_MODELS],
                })
                state["openai_compatibility"] = compat
                imported_count += 1
                if "qwen" not in imported_providers:
                    imported_providers.append("qwen")
            elif pid == "kimi":
                base = KIMI_API_BASES.get(region, KIMI_API_BASES["china"])
                state.setdefault("api_keys", {})["kimi"] = [{"api_key": raw_key, "label": "Kimi CLI", "base_url": base}]
                imported_count += 1
                if "kimi" not in imported_providers:
                    imported_providers.append("kimi")
        except Exception:
            pass

    return {"imported": imported_count, "providers": imported_providers}


def _auto_setup_tiers_locked(
    state: dict[str, Any],
    *,
    port: int,
    backend: ProxyBackend,
) -> dict[str, Any]:
    """Populate Diamond/Gold/Silver/Subagent + provider defaults from live connected models (caller holds lock)."""
    live_models: list[dict[str, Any]] = []
    if _port_in_use(DEFAULT_HOST, port):
        for _ in range(5):
            ok_m, mods, _ = backend.list_models(port, state["management_key"], state=state)
            if ok_m and mods:
                live_models = [m for m in mods if not str(m.get("id") or "").startswith("oasis/")]
                if live_models:
                    break
            time.sleep(0.25)

    if not live_models:
        raise RuntimeError(
            "No connected proxy accounts yet. Sign into a CLI (or click Sync Accounts) first."
        )

    proposed: dict[str, list[dict[str, str]]] = {}
    used_intel = False
    intel_path = _intel_cache_path()
    if intel_path.is_file():
        try:
            doc = _read_private_json(intel_path)
            if isinstance(doc, dict) and isinstance(doc.get("model_tiers"), dict):
                mt = doc["model_tiers"]
                main_list = mt.get("main_agent") if isinstance(mt.get("main_agent"), list) else []
                sub_list = mt.get("subagent") if isinstance(mt.get("subagent"), list) else []
                buckets = {
                    "diamond": [e for e in main_list if isinstance(e, dict) and str(e.get("tier") or "").strip().lower() == "diamond"],
                    "gold": [e for e in main_list if isinstance(e, dict) and str(e.get("tier") or "").strip().lower() == "gold"],
                    "silver": [e for e in main_list if isinstance(e, dict) and str(e.get("tier") or "").strip().lower() == "silver"],
                    "subagent": [e for e in sub_list if isinstance(e, dict)],
                }
                for tname in VALID_TIERS:
                    matched_targets: list[dict[str, str]] = []
                    seen_m: set[str] = set()
                    for entry in buckets.get(tname) or []:
                        raw_m = str(entry.get("model") or "").strip()
                        raw_p = str(entry.get("provider") or "").strip()
                        if not raw_m:
                            continue
                        matched = _match_intel_model_to_live(raw_m, raw_p, live_models)
                        if matched:
                            mid = str(matched["id"])
                            if mid not in seen_m:
                                seen_m.add(mid)
                                matched_targets.append({
                                    "model": mid,
                                    "thinking": _default_thinking_for_intel(tname, entry),
                                    "provider": str(matched.get("provider") or ""),
                                })
                    if matched_targets:
                        proposed[tname] = matched_targets[:3]
                        used_intel = True
        except Exception:
            pass

    ranked_live = sorted(live_models, key=lambda m: (_rank_model_for_auto(str(m.get("id") or "")), str(m.get("id") or "")))
    fast_live = sorted(
        live_models,
        key=lambda m: (
            0 if any(k in str(m.get("id") or "").lower() for k in ("flash", "haiku", "mini", "lite", "terra", "luna", "deepseek", "glm")) else 1,
            _rank_model_for_auto(str(m.get("id") or "")),
        ),
    )

    def _pick_chain(source_list: list[dict[str, Any]], thinking: str, offset: int = 0) -> list[dict[str, str]]:
        chosen: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        seen_provs: set[str] = set()
        ordered = source_list[offset:] + source_list[:offset]
        for m in ordered:
            mid = str(m.get("id") or "")
            prov = str(m.get("provider") or "")
            if not mid or mid in seen_ids or "image" in mid.lower() or "video" in mid.lower():
                continue
            if prov and prov in seen_provs and len(chosen) < 2:
                continue
            seen_ids.add(mid)
            if prov:
                seen_provs.add(prov)
            chosen.append({"model": mid, "thinking": thinking, "provider": prov})
            if len(chosen) >= 3:
                break
        if not chosen and source_list:
            m0 = source_list[0]
            chosen.append({"model": str(m0["id"]), "thinking": thinking, "provider": str(m0.get("provider") or "")})
        return chosen

    if "diamond" not in proposed:
        proposed["diamond"] = _pick_chain(ranked_live, "high", offset=0)
    if "gold" not in proposed:
        proposed["gold"] = _pick_chain(ranked_live, "medium", offset=1 if len(ranked_live) > 1 else 0)
    if "silver" not in proposed:
        proposed["silver"] = _pick_chain(fast_live, "low", offset=0)
    if "subagent" not in proposed:
        proposed["subagent"] = _pick_chain(fast_live, "none", offset=0)

    tiers_map = state.setdefault("tiers", {})
    for tname in VALID_TIERS:
        if proposed.get(tname):
            tiers_map[tname] = proposed[tname]
    pdefs = state.setdefault("provider_defaults", {})
    by_prov: dict[str, list[dict[str, Any]]] = {}
    for m in ranked_live:
        p = str(m.get("provider") or "")
        if p in PROVIDER_CAPABILITIES:
            by_prov.setdefault(p, []).append(m)
    for p, mlist in by_prov.items():
        cur = pdefs.get(p) if isinstance(pdefs.get(p), dict) else {}
        if not str(cur.get("model") or "").strip():
            pdefs[p] = {"model": str(mlist[0]["id"]), "thinking": str(cur.get("thinking") or "auto")}
    save_state_and_render(state)
    return {"used_intel": used_intel, "tiers": proposed}


def sync_cli_accounts(*, auto_tiers: bool = True) -> dict[str, Any]:
    """Sync all already signed-in CLI accounts & Oasis vault accounts into Oasis Proxy."""
    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        summary = _sync_cli_accounts_into_proxy_locked(state)
        save_state_and_render(state)
        tiers_configured = False
        port = _validate_port(state.get("port") or DEFAULT_PORT)
        if auto_tiers and _port_in_use(DEFAULT_HOST, port) and not any((state.get("tiers") or {}).get(t) for t in VALID_TIERS):
            backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
            try:
                _auto_setup_tiers_locked(state, port=port, backend=backend)
                tiers_configured = True
            except Exception:
                pass
        return {
            "ok": True,
            "imported": summary["imported"],
            "providers": summary["providers"],
            "tiers_configured": tiers_configured,
        }


def auto_setup_tiers() -> dict[str, Any]:
    """Automatically sync CLI accounts and configure all 4 tiers & provider defaults."""
    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        summary = _sync_cli_accounts_into_proxy_locked(state)
        save_state_and_render(state)
        port = _validate_port(state.get("port") or DEFAULT_PORT)
        backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
        res = _auto_setup_tiers_locked(state, port=port, backend=backend)
        return {
            "ok": True,
            "used_intel": res["used_intel"],
            "synced_providers": summary["providers"],
            "tiers": res["tiers"],
            "auto_model": "oasis/auto",
        }


def copy_model_id(model: str = "oasis/auto") -> dict[str, Any]:
    clean = _validate_model_id(model)
    tool = _copy_to_clipboard(clean, sensitive=False)
    return {"ok": True, "copied": "model", "model": clean, "tool": tool}


def copy_endpoint() -> dict[str, Any]:
    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        endpoint = f"http://{DEFAULT_HOST}:{_validate_port(state['port'])}/v1"
    tool = _copy_to_clipboard(endpoint, sensitive=False)
    return {"ok": True, "copied": "endpoint", "endpoint": endpoint, "tool": tool}


def copy_client_key() -> dict[str, Any]:
    """Copy the proxy client API key directly into the system clipboard (never prints it)."""
    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        client_key = state["client_api_key"]
    tool = _copy_to_clipboard(client_key, sensitive=True)
    return {"ok": True, "copied": "client_api_key", "tool": tool}


def launch_proxy_login(provider: str) -> dict[str, Any]:
    cap = PROVIDER_CAPABILITIES.get(provider)
    if not cap:
        raise ValueError(f"unknown provider: {provider}")
    backend_prov = OASIS_TO_BACKEND_PROVIDER.get(provider)
    if not backend_prov:
        raise ValueError(f"{cap['name']} does not support isolated proxy OAuth/device login ({cap['ownership_note']})")

    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
        disc = backend.discover(state.get("binary_path") or "")
        if not disc.installed or not disc.binary_path or not disc.compatible:
            raise RuntimeError(disc.error or "Proxy backend binary not installed.")

    env = _enriched_env(strip_proxies=True)
    term_candidates: list[tuple[str, list[str]]] = [
        ("konsole", ["-e"]),
        ("alacritty", ["-e"]),
        ("kitty", ["--"]),
        ("ghostty", ["-e"]),
        ("wezterm", ["start", "--"]),
        ("xdg-terminal-exec", []),
        ("xterm", ["-e"]),
    ]
    term_name = ""
    term_prefix: list[str] = []
    for name, flags in term_candidates:
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            term_name = name
            term_prefix = [found] + flags
            break
    if not term_prefix:
        raise RuntimeError("No terminal emulator found (install konsole, alacritty, kitty, or xterm).")

    script_path = str(Path(__file__).resolve())
    runner_cmd = [sys.executable or "python3", script_path, "login-runner", "--provider", provider]
    subprocess.Popen(
        term_prefix + runner_cmd,
        env=env,
        cwd=str(_proxy_dir()),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        "ok": True,
        "launched": True,
        "provider": provider,
        "terminal": term_name,
        "message": f"Opened {term_name} for isolated {cap['name']} proxy sign-in (native CLI credentials untouched).",
    }


def run_proxy_login_interactive(provider: str) -> int:
    cap = PROVIDER_CAPABILITIES.get(provider)
    backend_prov = OASIS_TO_BACKEND_PROVIDER.get(provider)
    if not cap or not backend_prov:
        print(f"Provider {provider} does not support proxy OAuth/device login.", file=sys.stderr)
        return 2
    with _control_lock():
        state = load_state(create_if_missing=True)
        assert state is not None
        backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
        disc = backend.discover(state.get("binary_path") or "")
        if not disc.installed or not disc.binary_path:
            print(f"Error: {disc.error}", file=sys.stderr)
            return 1

    os.umask(0o077)
    print(f"=== Oasis Proxy: Isolated {cap['name']} Sign-In ===")
    print(f"Auth dir: {_auths_dir()} (isolated from native CLI credentials)")
    print()
    run_env = _enriched_env(strip_proxies=True)
    if provider == "antigravity":
        ag_pair = _discover_local_antigravity_oauth_client()
        if ag_pair:
            run_env.setdefault("OASIS_ANTIGRAVITY_CLIENT_ID", ag_pair[0])
            run_env.setdefault("OASIS_ANTIGRAVITY_CLIENT_SECRET", ag_pair[1])
    rc = subprocess.run(
        [disc.binary_path, "--config", str(_config_yaml_path()), "login", backend_prov],
        env=run_env,
        cwd=str(_proxy_dir()),
        check=False,
    ).returncode
    if rc == 0:
        print(f"\n✓ Added isolated {cap['name']} account to Oasis Proxy.")
        time.sleep(1.5)
    else:
        try:
            input("\nSign-in did not complete. Press Enter to close…")
        except EOFError:
            pass
    return rc


# ---------------------------------------------------------------------------
# Verified local backend installation & removal (single-pass SHA-256, zero TOCTOU)
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def install_backend_from_file(
    source: str,
    *,
    expected_sha256: str = "",
    allow_unverified: bool = False,
) -> dict[str, Any]:
    """Install a verified local cliproxyapi-rust binary or .tar.gz archive into ~/.local/bin.

    Streams the source once into a private staging file inside ~/.local/bin while
    computing SHA-256 in the same pass (eliminating hash-to-extract TOCTOU and
    avoiding noexec /tmp), verifies checksum before extracting/executing, and
    validates ONLY the exact staged binary (no PATH fallback).
    """
    want = (expected_sha256 or "").strip().lower()
    if want:
        if not re.fullmatch(r"[0-9a-f]{64}", want):
            raise ValueError("expected SHA-256 must be a 64-char hex digest")
    elif not allow_unverified:
        raise ValueError("SHA-256 checksum is required (provide --sha256 or pass --allow-unverified for local builds)")

    src = Path(source).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"Source file not found: {src}")

    with _control_lock():
        dest = _local_bin_path()
        dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        _reject_symlink(dest)

        stage_src = dest.with_name(f".oasis-stage-src.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        stage_bin = dest.with_name(f".oasis-stage-bin.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        try:
            # 1. Single-pass read + SHA-256 + 100 MiB cap into private staging file
            hasher = hashlib.sha256()
            total = 0
            fd_src = os.open(stage_src, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            fd_owned = True
            try:
                out_f = os.fdopen(fd_src, "wb")
                fd_owned = False
                with out_f, open(src, "rb") as in_f:
                    os.fchmod(out_f.fileno(), 0o600)
                    while True:
                        chunk = in_f.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_INSTALL_BYTES:
                            raise ValueError("Source file exceeds 100 MiB safety cap.")
                        hasher.update(chunk)
                        out_f.write(chunk)
                    out_f.flush()
                    os.fsync(out_f.fileno())
            finally:
                if fd_owned:
                    os.close(fd_src)

            actual_sha = hasher.hexdigest()
            if want and actual_sha != want:
                raise ValueError(f"SHA-256 mismatch: expected {want}, got {actual_sha}")

            # 2. Extract binary (if .tar.gz/.tgz) or promote staged binary directly
            if src.name.endswith((".tar.gz", ".tgz")):
                with tarfile.open(stage_src, "r:gz") as tf:
                    member = None
                    for m in tf:
                        if m.isfile() and not m.issym() and not m.islnk() and os.path.basename(m.name) in ("oasis-proxy", "cliproxyapi-rust"):
                            member = m
                            break
                    if member is None:
                        raise ValueError("Archive does not contain a regular 'oasis-proxy' binary.")
                    if member.size > MAX_INSTALL_BYTES:
                        raise ValueError("Archive binary exceeds 100 MiB safety cap.")
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        raise ValueError("Could not read binary from archive.")
                    fd_bin = os.open(stage_bin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o700)
                    b_owned = True
                    try:
                        bf = os.fdopen(fd_bin, "wb")
                        b_owned = False
                        with bf:
                            os.fchmod(bf.fileno(), 0o700)
                            written = 0
                            while True:
                                chunk = extracted.read(65536)
                                if not chunk:
                                    break
                                written += len(chunk)
                                if written > MAX_INSTALL_BYTES:
                                    raise ValueError("Extracted binary exceeds 100 MiB safety cap.")
                                bf.write(chunk)
                            bf.flush()
                            os.fsync(bf.fileno())
                    finally:
                        if b_owned:
                            os.close(fd_bin)
            else:
                os.replace(stage_src, stage_bin)
                os.chmod(stage_bin, 0o700)

            # 3. Verify ONLY the exact staged binary (exact_only=True prevents PATH fallback!)
            backend = CliProxyApiRustBackend()
            disc = backend.discover(str(stage_bin), exact_only=True)
            if not disc.compatible or disc.binary_path != str(stage_bin):
                raise ValueError(disc.error or "Binary failed version/compatibility verification.")

            os.chmod(stage_bin, 0o755)
            os.replace(stage_bin, dest)

            state = load_state(create_if_missing=True)
            assert state is not None
            state["binary_path"] = str(dest)
            save_state_and_render(state)

            return {
                "ok": True,
                "installed_path": str(dest),
                "version": disc.version,
                "source_sha256": actual_sha,
                "binary_sha256": _sha256_file(dest),
            }
        finally:
            stage_src.unlink(missing_ok=True)
            stage_bin.unlink(missing_ok=True)


def uninstall_backend(*, purge_config: bool = False) -> dict[str, Any]:
    """Stop proxy, remove systemd user unit, remove ~/.local/bin binary, and optionally purge config."""
    with _control_lock():
        _stop_proxy_locked(switch_to_direct=True)
        removed: list[str] = []

        unit = _systemd_unit_path()
        if unit.exists() or unit.is_symlink():
            if _systemd_available():
                subprocess.run(["systemctl", "--user", "disable", SYSTEMD_UNIT_NAME], capture_output=True, timeout=6, check=False)
            unit.unlink(missing_ok=True)
            if _systemd_available():
                subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=6, check=False)
            removed.append(str(unit))

        local_bin = _local_bin_path()
        if local_bin.exists() and not local_bin.is_symlink():
            local_bin.unlink(missing_ok=True)
            removed.append(str(local_bin))

        if purge_config:
            if _proxy_dir().exists():
                for child in sorted(_proxy_dir().iterdir()):
                    if child.name == ".control.lock":
                        continue
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
                removed.append(str(_proxy_dir()))
        else:
            state = load_state(create_if_missing=False)
            if state and state.get("binary_path") == str(local_bin):
                state["binary_path"] = ""
                save_state_and_render(state)

    if purge_config and _proxy_dir().exists():
        shutil.rmtree(_proxy_dir(), ignore_errors=True)

    return {"ok": True, "removed": removed, "purged_config": bool(purge_config)}


def client_config_snippets() -> dict[str, Any]:
    state = load_state(create_if_missing=False)
    port = _validate_port((state or {}).get("port") or DEFAULT_PORT)
    base = f"http://{DEFAULT_HOST}:{port}"
    return {
        "ok": True,
        "endpoint": base,
        "note": "Replace <OASIS_PROXY_KEY> using the 'Copy Key' button (secrets are never printed here).",
        "clients": {
            "claude_code": f"export ANTHROPIC_BASE_URL={base}\nexport ANTHROPIC_AUTH_TOKEN=<OASIS_PROXY_KEY>\nclaude",
            "codex": (
                "[model_providers.oasis]\n"
                'name = "Oasis Proxy"\n'
                f'base_url = "{base}/v1"\n'
                'wire_api = "responses"\n'
                'env_key = "OASIS_PROXY_KEY"'
            ),
            "opencode": (
                f"export OPENAI_BASE_URL={base}/v1\n"
                "export OPENAI_API_KEY=<OASIS_PROXY_KEY>"
            ),
            "hermes": (
                f"export OPENAI_BASE_URL={base}/v1\n"
                "export OPENAI_API_KEY=<OASIS_PROXY_KEY>\n"
                f"export ANTHROPIC_BASE_URL={base}\n"
                "export ANTHROPIC_API_KEY=<OASIS_PROXY_KEY>"
            ),
            "python_openai_sdk": (
                "from openai import OpenAI\n"
                f'client = OpenAI(base_url="{base}/v1", api_key="<OASIS_PROXY_KEY>")'
            ),
        },
    }


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="oasis-proxy")
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="Inspect proxy backend, health, accounts, models, and routing (no secrets)")
    p_status.add_argument("--pretty", action="store_true")
    p_status.add_argument("--no-requests", action="store_true")
    p_status.add_argument("--port", type=int, default=None)
    p_status.add_argument("--binary", default="")

    p_mode = sub.add_parser("set-mode", help="Set operating mode: direct or proxy")
    p_mode.add_argument("mode", choices=("direct", "proxy"))

    p_start = sub.add_parser("start", help="Start the optional local proxy backend")
    p_start.add_argument("--port", type=int, default=None)
    p_start.add_argument("--binary", default="")

    p_stop = sub.add_parser("stop", help="Stop the local proxy backend gracefully")
    p_stop.add_argument("--direct", action="store_true", help="Also switch mode back to direct")

    p_restart = sub.add_parser("restart", help="Restart the local proxy backend")
    p_restart.add_argument("--port", type=int, default=None)
    p_restart.add_argument("--binary", default="")

    p_route = sub.add_parser("set-routing", help="Update proxy routing and selection settings")
    p_route.add_argument("--strategy", choices=VALID_ROUTING_STRATEGIES, default=None)
    p_route.add_argument("--session-affinity", choices=("true", "false"), default=None)
    p_route.add_argument("--five-hour-reserve", type=int, default=None)
    p_route.add_argument("--request-retry", type=int, default=None)
    p_route.add_argument("--codex-websockets", choices=("true", "false"), default=None)
    p_route.add_argument("--claude-cloak", choices=("true", "false"), default=None)
    p_route.add_argument("--force-model-prefix", choices=("true", "false"), default=None)
    p_route.add_argument("--provider", default=None)
    p_route.add_argument("--model", default=None)
    p_route.add_argument("--allow-metered-fallback", choices=("true", "false"), default=None)

    p_pin = sub.add_parser("pin-account", help="Pin a specific proxy account for a provider")
    p_pin.add_argument("--provider", required=True)
    p_pin.add_argument("--account", required=True)

    p_unpin = sub.add_parser("unpin-account", help="Clear pinned proxy account for a provider")
    p_unpin.add_argument("--provider", required=True)

    p_toggle = sub.add_parser("toggle-account", help="Enable or disable a proxy account")
    p_toggle.add_argument("--account", required=True)
    p_toggle.add_argument("--disabled", choices=("true", "false"), required=True)

    p_reset = sub.add_parser("reset-cooldown", help="Clear cooldowns on a proxy account")
    p_reset.add_argument("--account", required=True)

    p_del = sub.add_parser("delete-account", help="Remove an isolated account from the proxy backend")
    p_del.add_argument("--account", required=True)

    p_qref = sub.add_parser("refresh-quota", help="Refresh subscription quota on a proxy account")
    p_qref.add_argument("--account", required=True)

    p_sync = sub.add_parser("sync-api-key", help="Sync a non-rotating static API key into proxy config (reads key from stdin)")
    p_sync.add_argument("--provider", required=True)
    p_sync.add_argument("--label", default="")
    p_sync.add_argument("--region", default="international")
    p_sync.add_argument("--base-url", default="")
    p_sync.add_argument("--models", default="")

    p_login = sub.add_parser("launch-login", help="Open terminal for isolated proxy OAuth/device login")
    p_login.add_argument("--provider", required=True)

    p_runner = sub.add_parser("login-runner", help="Internal terminal runner for proxy login")
    p_runner.add_argument("--provider", required=True)

    sub.add_parser("rotate-key", help="Rotate the local proxy client API key")
    sub.add_parser("copy-endpoint", help="Copy the localhost proxy endpoint URL to clipboard")
    sub.add_parser("copy-client-key", help="Copy the proxy client API key to clipboard (via stdin, never stdout)")
    sub.add_parser("client-configs", help="Print sanitized client setup snippets")
    sub.add_parser("capabilities", help="Print verified 9-provider capability matrix")

    p_pdef = sub.add_parser("set-provider-default", help="Set a provider's default model and thinking/reasoning mode")
    p_pdef.add_argument("--provider", required=True)
    p_pdef.add_argument("--model", default=None)
    p_pdef.add_argument("--thinking", default=None)

    p_tier = sub.add_parser("set-tier", help="Configure or clear a single-daemon tier route (diamond/gold/silver/subagent)")
    p_tier.add_argument("--tier", required=True, choices=VALID_TIERS)
    p_tier.add_argument("--model", default="")
    p_tier.add_argument("--thinking", default="auto")
    p_tier.add_argument("--provider", default="")
    p_tier.add_argument("--clear", action="store_true")

    p_tier_copy = sub.add_parser("copy-tier-endpoint", help="Copy a tier's localhost OpenAI base URL to clipboard")
    p_tier_copy.add_argument("--tier", required=True, choices=VALID_TIERS)

    p_intel = sub.add_parser("import-intel", help="Preview or apply tier routing from cached Oasis AI Intel rankings")
    p_intel.add_argument("--apply", action="store_true")

    sub.add_parser("auto-setup", help="Automatically configure all tiers & provider defaults from connected accounts")
    sub.add_parser("sync-cli-accounts", help="Sync already signed-in local CLI & Oasis Accounts logins into Oasis Proxy")

    p_sync_det = sub.add_parser("sync-detected-key", help="Sync a locally detected non-rotating static API key (opencodego/qwen/kimi)")
    p_sync_det.add_argument("--provider", required=True, choices=("opencodego", "qwen", "kimi"))

    p_copy_m = sub.add_parser("copy-model", help="Copy a virtual or concrete model ID (default: oasis/auto) to clipboard")
    p_copy_m.add_argument("--model", default="oasis/auto")

    p_inst = sub.add_parser("install-backend", help="Install a verified local cliproxyapi-rust binary or .tar.gz")
    p_inst.add_argument("--from-file", required=True)
    p_inst.add_argument("--sha256", default="")
    p_inst.add_argument("--allow-unverified", action="store_true")

    p_uninst = sub.add_parser("uninstall-backend", help="Stop proxy, remove systemd unit and ~/.local/bin binary")
    p_uninst.add_argument("--purge-config", action="store_true")

    args = parser.parse_args(argv)

    def _to_bool(val: Optional[str]) -> Optional[bool]:
        if val is None:
            return None
        return val == "true"

    try:
        if args.command == "login-runner":
            return run_proxy_login_interactive(args.provider)

        if args.command == "status":
            res = get_status(
                include_requests=not args.no_requests,
                port_override=args.port,
                binary_override=args.binary,
            )
            print(json.dumps(res, indent=2 if args.pretty else None, ensure_ascii=False))
            return 0

        if args.command == "set-mode":
            res = set_mode(args.mode)
        elif args.command == "start":
            res = start_proxy(port_override=args.port, binary_override=args.binary)
        elif args.command == "stop":
            res = stop_proxy(switch_to_direct=args.direct)
        elif args.command == "restart":
            res = restart_proxy(port_override=args.port, binary_override=args.binary)
        elif args.command == "set-routing":
            res = update_routing(
                strategy=args.strategy,
                session_affinity=_to_bool(args.session_affinity),
                five_hour_reserve=args.five_hour_reserve,
                request_retry=args.request_retry,
                codex_websockets=_to_bool(args.codex_websockets),
                claude_cloak=_to_bool(args.claude_cloak),
                force_model_prefix=_to_bool(args.force_model_prefix),
                selected_provider=args.provider,
                selected_model=args.model,
                allow_metered_fallback=_to_bool(args.allow_metered_fallback),
            )
        elif args.command == "pin-account":
            res = pin_account(args.provider, args.account)
        elif args.command == "unpin-account":
            res = unpin_account(args.provider)
        elif args.command == "toggle-account":
            state = load_state(create_if_missing=False)
            if not state:
                raise RuntimeError("Proxy is not initialized.")
            backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
            ok, err = backend.toggle_account(_validate_port(state["port"]), state["management_key"], args.account, args.disabled == "true")
            res = {"ok": ok, "error": err} if not ok else {"ok": True, "account": args.account, "disabled": args.disabled == "true"}
        elif args.command == "reset-cooldown":
            state = load_state(create_if_missing=False)
            if not state:
                raise RuntimeError("Proxy is not initialized.")
            backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
            ok, err = backend.reset_account_cooldown(_validate_port(state["port"]), state["management_key"], args.account)
            res = {"ok": ok, "error": err} if not ok else {"ok": True, "account": args.account}
        elif args.command == "delete-account":
            state = load_state(create_if_missing=False)
            if not state:
                raise RuntimeError("Proxy is not initialized.")
            backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
            ok, err = backend.delete_account(_validate_port(state["port"]), state["management_key"], args.account)
            res = {"ok": ok, "error": err} if not ok else {"ok": True, "account": args.account}
        elif args.command == "refresh-quota":
            state = load_state(create_if_missing=False)
            if not state:
                raise RuntimeError("Proxy is not initialized.")
            backend = get_backend(state.get("backend_id") or "cliproxyapi-rust")
            ok, err = backend.refresh_quota(_validate_port(state["port"]), state["management_key"], args.account)
            res = {"ok": ok, "error": err} if not ok else {"ok": True, "account": args.account}
        elif args.command == "sync-api-key":
            raw_key = sys.stdin.read().strip()
            models_list = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else None
            res = sync_static_api_key(
                args.provider,
                raw_key,
                label=args.label,
                region=args.region,
                base_url=args.base_url,
                models=models_list,
            )
        elif args.command == "launch-login":
            res = launch_proxy_login(args.provider)
        elif args.command == "rotate-key":
            res = rotate_client_key()
        elif args.command == "copy-endpoint":
            res = copy_endpoint()
        elif args.command == "copy-client-key":
            res = copy_client_key()
        elif args.command == "client-configs":
            res = client_config_snippets()
        elif args.command == "capabilities":
            res = {"ok": True, "capabilities": PROVIDER_CAPABILITIES}
        elif args.command == "set-provider-default":
            res = set_provider_default(args.provider, model=args.model, thinking=args.thinking)
        elif args.command == "set-tier":
            res = set_tier(
                args.tier,
                model=args.model,
                thinking=args.thinking,
                provider=args.provider,
                clear=args.clear,
            )
        elif args.command == "copy-tier-endpoint":
            res = copy_tier_endpoint(args.tier)
        elif args.command == "import-intel":
            res = import_intel_tiers(apply=args.apply)
        elif args.command == "auto-setup":
            res = auto_setup_tiers()
        elif args.command == "sync-cli-accounts":
            res = sync_cli_accounts()
        elif args.command == "sync-detected-key":
            res = sync_detected_static_key(args.provider)
        elif args.command == "copy-model":
            res = copy_model_id(args.model)
        elif args.command == "install-backend":
            res = install_backend_from_file(
                args.from_file,
                expected_sha256=args.sha256,
                allow_unverified=args.allow_unverified,
            )
        elif args.command == "uninstall-backend":
            res = uninstall_backend(purge_config=args.purge_config)
        else:
            raise ValueError(f"unknown command: {args.command}")

        print(json.dumps(res, ensure_ascii=False))
        return 0 if res.get("ok", True) else 1
    except Exception as exc:
        state_for_redact = load_state(create_if_missing=False)
        print(json.dumps({"ok": False, "error": redact_secrets(str(exc), _known_secrets(state_for_redact))}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
