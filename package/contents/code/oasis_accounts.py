#!/usr/bin/env python3
"""Oasis multi-account vault and live CLI switcher.

Manages multiple accounts per CLI provider and switches the active credential
directly in each CLI agent's native credential store so running or new CLI
sessions immediately use the selected account.

Security model
--------------
* Account metadata lives in ~/.config/oasis/accounts/index.json (0600, no secrets).
* Credential snapshots live in ~/.config/oasis/accounts/secrets/<provider>/<id>.json
  inside a 0700 directory with 0600 file permissions.
* Secret Service values (Antigravity, Muse) are read via `secret-tool lookup`
  and written via `secret-tool store` using stdin (never argv).
* Secrets are never printed to stdout or stderr.
* Before switching away from a live CLI account or starting a new login flow,
  the currently active CLI credential is automatically synced back into its
  vault entry so CLI-refreshed tokens or manual logins are never lost.
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

HOME = Path.home()
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or (HOME / ".local/share"))
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache")) / "ai-limits"
ANTIGRAVITY_CACHE = CACHE_DIR / "antigravity.json"

VAULT_DIR = CONFIG_HOME / "oasis" / "accounts"
INDEX_FILE = VAULT_DIR / "index.json"
SECRETS_DIR = VAULT_DIR / "secrets"

MAX_FILE = 8 * 1024 * 1024

# Supported CLI providers and their UI/CLI capabilities.
PROVIDER_SPECS: dict[str, dict[str, Any]] = {
    "codex": {
        "name": "Codex",
        "supports_accounts": True,
        "supports_login": True,
        "supports_api_key": False,
        "login_hint": "codex login",
        "cli_binary": "codex",
    },
    "claude": {
        "name": "Claude",
        "supports_accounts": True,
        "supports_login": True,
        "supports_api_key": False,
        "login_hint": "claude auth login",
        "cli_binary": "claude",
    },
    "antigravity": {
        "name": "Antigravity",
        "supports_accounts": True,
        "supports_login": True,
        "supports_api_key": False,
        "login_hint": "agy (Google OAuth)",
        "cli_binary": "agy",
    },
    "opencodego": {
        "name": "OpenCode Go",
        "supports_accounts": True,
        "supports_login": True,
        "supports_api_key": True,
        "api_key_placeholder": "OpenCode Go API key",
        "login_hint": "opencode auth login -p opencode-go",
        "cli_binary": "opencode",
    },
    "qwen": {
        "name": "Qwen",
        "supports_accounts": True,
        "supports_login": True,
        "supports_api_key": True,
        "api_key_placeholder": "sk-sp-... or DashScope API key",
        "regions": ["international", "china"],
        "login_hint": "qwen",
        "cli_binary": "qwen",
    },
    "muse": {
        "name": "Muse Code",
        "supports_accounts": True,
        "supports_login": True,
        "supports_api_key": False,
        "login_hint": "muse login",
        "cli_binary": "muse",
    },
    "kimi": {
        "name": "Kimi Code",
        "supports_accounts": True,
        "supports_login": True,
        "supports_api_key": True,
        "api_key_placeholder": "Kimi Code API key",
        "regions": ["china", "international"],
        "login_hint": "kimi",
        "cli_binary": "kimi",
    },
    "grok": {
        "name": "Grok",
        "supports_accounts": True,
        "supports_login": True,
        "supports_api_key": False,
        "login_hint": "grok login",
        "cli_binary": "grok",
    },
}


# ---------------------------------------------------------------------------
# Filesystem, PATH, JWT, and Secret Service utilities
# ---------------------------------------------------------------------------


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def load_json(path: Path) -> Any:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE)
        return json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def write_json_atomic(path: Path, data: Any, *, indent: Optional[int] = None, mode: int = 0o600) -> None:
    _ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if indent is not None:
                json.dump(data, fh, indent=indent, ensure_ascii=False)
                fh.write("\n")
            else:
                json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, mode)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def jwt_claims(token: str) -> dict[str, Any]:
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        value = json.loads(base64.urlsafe_b64decode(part))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def short_email_label(email: str) -> str:
    email = email.strip()
    if not email:
        return ""
    return email


def sanitize_label(label: str) -> str:
    cleaned = re.sub(r"[\r\n\t]+", " ", (label or "").strip())
    return cleaned[:64]


def enriched_env() -> dict[str, str]:
    """Return environment with user CLI paths (nvm, ~/.local/bin, opencode)."""
    env = dict(os.environ)
    extra: list[str] = [
        str(HOME / ".local" / "bin"),
        str(HOME / ".opencode" / "bin"),
        str(HOME / ".cargo" / "bin"),
        str(HOME / "bin"),
    ]
    nvm_bins = glob.glob(str(HOME / ".nvm" / "versions" / "node" / "*" / "bin"))

    def _ver_key(p: str) -> tuple[int, ...]:
        m = re.search(r"v(\d+)\.(\d+)\.(\d+)", p)
        return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)

    nvm_bins.sort(key=_ver_key, reverse=True)
    extra.extend(nvm_bins)
    extra.extend(["/usr/local/bin", "/usr/bin", "/bin"])

    existing = [p for p in env.get("PATH", "").split(":") if p]
    merged: list[str] = []
    seen: set[str] = set()
    for p in extra + existing:
        if p and p not in seen and os.path.isdir(p):
            seen.add(p)
            merged.append(p)
    env["PATH"] = ":".join(merged)
    return env


def find_bin(name: str, env: Optional[dict[str, str]] = None) -> Optional[str]:
    search_env = env or enriched_env()
    return shutil.which(name, path=search_env.get("PATH"))


def _secret_tool() -> Optional[str]:
    return find_bin("secret-tool")


def secret_lookup(service: str, username: Optional[str] = None) -> Optional[str]:
    tool = _secret_tool()
    if not tool:
        return None
    args = [tool, "lookup", "service", service]
    if username:
        args += ["username", username]
    try:
        res = subprocess.run(args, check=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    value = res.stdout.strip()
    return value or None


def secret_store(service: str, username: str, label: str, secret_value: str) -> bool:
    tool = _secret_tool()
    if not tool:
        return False
    args = [tool, "store", f"--label={label}", "service", service, "username", username]
    try:
        res = subprocess.run(
            args,
            input=secret_value,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def secret_clear(service: str, username: str) -> bool:
    tool = _secret_tool()
    if not tool:
        return False
    args = [tool, "clear", "service", service, "username", username]
    try:
        res = subprocess.run(args, check=False, capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def notify_desktop(title: str, body: str) -> None:
    tool = find_bin("notify-send")
    if not tool:
        return
    try:
        subprocess.run(
            [tool, "-a", "Oasis", "-i", "org.dralk.ailimits", "-t", "6000", title, body],
            check=False,
            capture_output=True,
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        pass


# ---------------------------------------------------------------------------
# Vault index & secret storage
# ---------------------------------------------------------------------------


def _load_index() -> dict[str, Any]:
    doc = load_json(INDEX_FILE)
    if not isinstance(doc, dict):
        return {"version": 1, "providers": {}}
    providers = doc.get("providers")
    if not isinstance(providers, dict):
        doc["providers"] = {}
    return doc


def _save_index(index: dict[str, Any]) -> None:
    write_json_atomic(INDEX_FILE, index, indent=2, mode=0o600)


def _secret_path(provider: str, account_id: str) -> Path:
    safe_provider = re.sub(r"[^a-z0-9_-]", "", provider.lower())
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", account_id)
    if not safe_provider or not safe_id:
        raise ValueError("invalid provider or account_id")
    return SECRETS_DIR / safe_provider / f"{safe_id}.json"


def load_account_secret(provider: str, account_id: str) -> Optional[dict[str, Any]]:
    try:
        path = _secret_path(provider, account_id)
    except ValueError:
        return None
    doc = load_json(path)
    return doc if isinstance(doc, dict) else None


def save_account_secret(provider: str, account_id: str, payload: dict[str, Any]) -> None:
    path = _secret_path(provider, account_id)
    _ensure_private_dir(VAULT_DIR)
    _ensure_private_dir(SECRETS_DIR)
    write_json_atomic(path, payload, mode=0o600)


def delete_account_secret(provider: str, account_id: str) -> None:
    try:
        path = _secret_path(provider, account_id)
        path.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


def _new_account_id() -> str:
    return f"acc_{secrets.token_hex(6)}"


def _provider_bucket(index: dict[str, Any], provider: str) -> dict[str, Any]:
    providers = index.setdefault("providers", {})
    bucket = providers.get(provider)
    if not isinstance(bucket, dict):
        bucket = {"active_id": None, "accounts": []}
        providers[provider] = bucket
    if not isinstance(bucket.get("accounts"), list):
        bucket["accounts"] = []
    return bucket


# ---------------------------------------------------------------------------
# Provider-specific live adapters (read / write / identity)
# ---------------------------------------------------------------------------


def _codex_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (HOME / ".codex")) / "auth.json"


def _claude_dirs() -> tuple[Path, Path, Path]:
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (HOME / ".claude"))
    creds_path = config_dir / ".credentials.json"
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        state_path = config_dir / ".claude.json"
    else:
        state_path = HOME / ".claude.json"
    return config_dir, creds_path, state_path


def _claude_acquire_locks(config_dir: Path) -> Optional[tuple[Path, Optional[Path]]]:
    config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    new_lock = config_dir / ".oauth_refresh.lock"
    try:
        real_dir = config_dir.resolve()
    except OSError:
        real_dir = config_dir
    legacy_lock = real_dir.with_name(real_dir.name + ".lock")
    for _ in range(10):
        try:
            os.mkdir(new_lock, 0o700)
        except OSError:
            time.sleep(0.2)
            continue
        try:
            os.mkdir(legacy_lock, 0o700)
            return new_lock, legacy_lock
        except FileExistsError:
            try:
                os.rmdir(new_lock)
            except OSError:
                pass
            time.sleep(0.2)
        except OSError:
            return new_lock, None
    return None


def _claude_release_locks(locks: Optional[tuple[Path, Optional[Path]]]) -> None:
    if not locks:
        return
    new_lock, legacy_lock = locks
    for lock in (legacy_lock, new_lock):
        if lock is None:
            continue
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _opencode_db_path() -> Path:
    db_env = os.environ.get("OPENCODE_DB")
    if db_env and os.path.isabs(db_env):
        return Path(db_env)
    return DATA_HOME / "opencode" / "opencode.db"


def _opencode_auth_json_path() -> Path:
    return DATA_HOME / "opencode" / "auth.json"


def _qwen_oauth_path() -> Path:
    return Path(os.environ.get("QWEN_CONFIG_DIR") or (HOME / ".qwen")) / "oauth_creds.json"


def _muse_auth_path() -> Path:
    return Path(os.environ.get("MUSE_AUTH_PATH") or (HOME / ".config" / "muse" / "auth.json"))


def _kimi_cli_path() -> Path:
    return Path(os.environ.get("KIMI_CODE_HOME") or (HOME / ".kimi-code")) / "credentials" / "kimi-code.json"


def _grok_auth_path() -> Path:
    return HOME / ".grok" / "auth.json"


def _codexbar_config_path() -> Path:
    return HOME / ".codexbar" / "config.json"


def _update_codexbar_provider(provider_id: str, api_key: str, region: Optional[str] = None) -> None:
    path = _codexbar_config_path()
    doc = load_json(path)
    if not isinstance(doc, dict):
        doc = {"providers": []}
    providers = doc.get("providers")
    if not isinstance(providers, list):
        providers = []
        doc["providers"] = providers
    updated = False
    for item in providers:
        if isinstance(item, dict) and item.get("id") == provider_id:
            item["apiKey"] = api_key
            if region:
                item["region"] = region
            updated = True
            break
    if not updated:
        entry: dict[str, Any] = {"id": provider_id, "apiKey": api_key}
        if region:
            entry["region"] = region
        providers.append(entry)
    write_json_atomic(path, doc, indent=2, mode=0o600)


def _describe_codex(doc: dict[str, Any]) -> dict[str, Any]:
    tokens = doc.get("tokens") if isinstance(doc.get("tokens"), dict) else {}
    id_claims = jwt_claims(tokens.get("id_token") or "")
    acc_claims = jwt_claims(tokens.get("access_token") or "")
    email = id_claims.get("email") or acc_claims.get("email") or ""
    if not isinstance(email, str):
        email = ""
    email = email.strip()
    account_id = tokens.get("account_id") or ""
    if not account_id:
        auth_claim = id_claims.get("https://api.openai.com/auth") or acc_claims.get("https://api.openai.com/auth")
        if isinstance(auth_claim, dict):
            account_id = auth_claim.get("chatgpt_account_id") or ""
    login_owner = tokens.get("login_owner_id") or ""
    identity = account_id or email.lower() or login_owner
    fp_basis = identity or tokens.get("refresh_token") or tokens.get("access_token") or json.dumps(doc, sort_keys=True)
    default_label = email or (f"Codex {account_id[:8]}" if account_id else "Codex")
    return {
        "email": email or None,
        "identity_key": f"codex:{identity}" if identity else None,
        "fingerprint": sha256_hex(f"codex:{fp_basis}"),
        "default_label": default_label,
        "kind": "oauth",
    }


def _describe_claude(snap: dict[str, Any]) -> dict[str, Any]:
    oauth = snap.get("claudeAiOauth") if isinstance(snap.get("claudeAiOauth"), dict) else {}
    acct = snap.get("oauthAccount") if isinstance(snap.get("oauthAccount"), dict) else {}
    email = acct.get("emailAddress") if isinstance(acct.get("emailAddress"), str) else ""
    email = email.strip()
    acct_uuid = acct.get("accountUuid") if isinstance(acct.get("accountUuid"), str) else ""
    org_uuid = acct.get("organizationUuid") if isinstance(acct.get("organizationUuid"), str) else ""
    display = acct.get("displayName") if isinstance(acct.get("displayName"), str) else ""
    identity = f"{acct_uuid}/{org_uuid}" if acct_uuid else email.lower()
    fp_basis = identity or oauth.get("refreshToken") or oauth.get("accessToken") or json.dumps(oauth, sort_keys=True)
    default_label = email or display or "Claude"
    return {
        "email": email or None,
        "identity_key": f"claude:{identity}" if identity else None,
        "fingerprint": sha256_hex(f"claude:{fp_basis}"),
        "default_label": default_label,
        "kind": "oauth",
    }


def _parse_antigravity_raw(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("go-keyring-base64:"):
        try:
            text = base64.b64decode(text[len("go-keyring-base64:") :]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return {}
    try:
        doc = json.loads(text)
        return doc if isinstance(doc, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _describe_antigravity(snap: dict[str, Any]) -> dict[str, Any]:
    raw = snap.get("raw") if isinstance(snap.get("raw"), str) else ""
    doc = _parse_antigravity_raw(raw)
    email = doc.get("id_token_email") or doc.get("email") or ""
    if not isinstance(email, str):
        email = ""
    email = email.strip()
    token = doc.get("token") if isinstance(doc.get("token"), dict) else doc
    refresh = token.get("refresh_token") or token.get("refreshToken") or ""
    identity = email.lower() or (sha256_hex(refresh)[:16] if refresh else "")
    fp_basis = identity or raw
    return {
        "email": email or None,
        "identity_key": f"antigravity:{identity}" if identity else None,
        "fingerprint": sha256_hex(f"antigravity:{fp_basis}"),
        "default_label": email or "Antigravity",
        "kind": "oauth",
    }


def _describe_qwen(snap: dict[str, Any]) -> dict[str, Any]:
    kind = snap.get("kind") or "oauth"
    if kind == "api_key":
        key = snap.get("api_key") or ""
        region = snap.get("region") or "international"
        suffix = key[-4:] if len(key) >= 4 else ""
        return {
            "email": None,
            "identity_key": f"qwen:key:{sha256_hex(key)[:16]}",
            "fingerprint": sha256_hex(f"qwen:key:{key}:{region}"),
            "default_label": f"Qwen Key ···{suffix}" if suffix else "Qwen API Key",
            "kind": "api_key",
            "region": region,
        }
    doc = snap.get("oauth") if isinstance(snap.get("oauth"), dict) else {}
    access = doc.get("access_token") or doc.get("accessToken") or ""
    claims = jwt_claims(access)
    email = claims.get("email") if isinstance(claims.get("email"), str) else ""
    sub = claims.get("sub") if isinstance(claims.get("sub"), str) else ""
    res_url = doc.get("resource_url") if isinstance(doc.get("resource_url"), str) else ""
    identity = email.lower() or sub or doc.get("refresh_token") or access
    return {
        "email": email or None,
        "identity_key": f"qwen:{identity}" if identity else None,
        "fingerprint": sha256_hex(f"qwen:oauth:{identity}"),
        "default_label": email or (res_url.split("/")[0] if res_url else "Qwen OAuth"),
        "kind": "oauth",
    }


def _describe_muse(snap: dict[str, Any]) -> dict[str, Any]:
    doc = snap.get("auth") if isinstance(snap.get("auth"), dict) else {}
    providers = doc.get("providers") if isinstance(doc.get("providers"), dict) else {}
    meta = providers.get("meta") if isinstance(providers.get("meta"), dict) else {}
    token = meta.get("access_token") or snap.get("keyring_raw") or ""
    email = meta.get("email") or meta.get("user") or ""
    if not isinstance(email, str):
        email = ""
    identity = email.lower() or (sha256_hex(token)[:16] if token else "")
    return {
        "email": email or None,
        "identity_key": f"muse:{identity}" if identity else None,
        "fingerprint": sha256_hex(f"muse:{token}"),
        "default_label": email or (f"Muse {identity[:6]}" if identity else "Muse Code"),
        "kind": "oauth",
    }


def _describe_kimi(snap: dict[str, Any]) -> dict[str, Any]:
    kind = snap.get("kind") or "cli"
    region = snap.get("region") or "china"
    if kind == "api_key":
        key = snap.get("api_key") or ""
        suffix = key[-4:] if len(key) >= 4 else ""
        return {
            "email": None,
            "identity_key": f"kimi:key:{sha256_hex(key)[:16]}",
            "fingerprint": sha256_hex(f"kimi:key:{key}:{region}"),
            "default_label": f"Kimi Key ···{suffix}" if suffix else "Kimi API Key",
            "kind": "api_key",
            "region": region,
        }
    doc = snap.get("cli") if isinstance(snap.get("cli"), dict) else {}
    access = doc.get("access_token") or ""
    claims = jwt_claims(access)
    email = claims.get("email") or claims.get("sub") or ""
    if not isinstance(email, str):
        email = ""
    identity = email.lower() or doc.get("refresh_token") or access
    return {
        "email": email if "@" in email else None,
        "identity_key": f"kimi:{identity}" if identity else None,
        "fingerprint": sha256_hex(f"kimi:cli:{identity}"),
        "default_label": email or "Kimi CLI",
        "kind": "cli",
        "region": region,
    }


def _describe_grok(snap: dict[str, Any]) -> dict[str, Any]:
    doc = snap.get("auth") if isinstance(snap.get("auth"), dict) else {}
    email = ""
    token = ""
    for key, entry in doc.items():
        if isinstance(entry, dict) and isinstance(entry.get("key"), str) and entry["key"]:
            token = entry["key"]
            claims = jwt_claims(token)
            email = claims.get("email") or claims.get("sub") or (key if "@" in key else "")
            if not isinstance(email, str):
                email = ""
            break
    identity = email.lower() or (sha256_hex(token)[:16] if token else "")
    return {
        "email": email if "@" in email else None,
        "identity_key": f"grok:{identity}" if identity else None,
        "fingerprint": sha256_hex(f"grok:{token}"),
        "default_label": email or "Grok",
        "kind": "oauth",
    }


def read_live_snapshot(provider: str) -> Optional[dict[str, Any]]:
    """Read the currently active live CLI credential for a vault-managed provider."""
    if provider == "codex":
        doc = load_json(_codex_path())
        if isinstance(doc, dict):
            tokens = doc.get("tokens")
            if isinstance(tokens, dict) and (tokens.get("access_token") or tokens.get("refresh_token")):
                return {"auth": doc}
        return None

    if provider == "claude":
        _, creds_path, state_path = _claude_dirs()
        creds = load_json(creds_path)
        oauth = creds.get("claudeAiOauth") if isinstance(creds, dict) else None
        if not isinstance(oauth, dict) or not (oauth.get("accessToken") or oauth.get("refreshToken")):
            return None
        state = load_json(state_path)
        oauth_account = state.get("oauthAccount") if isinstance(state, dict) else None
        return {
            "claudeAiOauth": oauth,
            "oauthAccount": oauth_account if isinstance(oauth_account, dict) else None,
        }

    if provider == "antigravity":
        raw = secret_lookup("gemini", "antigravity")
        if not raw:
            return None
        return {"raw": raw}

    if provider == "qwen":
        oauth = load_json(_qwen_oauth_path())
        if isinstance(oauth, dict) and (oauth.get("access_token") or oauth.get("accessToken")):
            return {"kind": "oauth", "oauth": oauth}
        cfg = load_json(_codexbar_config_path())
        if isinstance(cfg, dict):
            for item in cfg.get("providers") or []:
                if isinstance(item, dict) and item.get("id") in ("qwen", "dashscope", "alibaba"):
                    key = item.get("apiKey")
                    if isinstance(key, str) and key.strip():
                        return {
                            "kind": "api_key",
                            "api_key": key.strip(),
                            "region": str(item.get("region") or "international").lower(),
                        }
        return None

    if provider == "muse":
        auth = load_json(_muse_auth_path())
        if isinstance(auth, dict):
            providers = auth.get("providers") if isinstance(auth.get("providers"), dict) else {}
            meta = providers.get("meta") if isinstance(providers.get("meta"), dict) else {}
            if isinstance(meta.get("access_token"), str) and meta["access_token"].strip():
                return {"auth": auth}
        if find_bin("muse"):
            raw = secret_lookup("ai.meta.dev.credentials", "meta")
            if raw:
                return {"keyring_raw": raw}
        return None

    if provider == "kimi":
        cfg = load_json(_codexbar_config_path())
        region = "china"
        if isinstance(cfg, dict):
            for item in cfg.get("providers") or []:
                if isinstance(item, dict) and item.get("id") == "kimi":
                    region = str(item.get("region") or "china").lower()
                    key = item.get("apiKey")
                    if isinstance(key, str) and key.strip():
                        return {"kind": "api_key", "api_key": key.strip(), "region": region}
        cli = load_json(_kimi_cli_path())
        if isinstance(cli, dict) and isinstance(cli.get("access_token"), str) and cli["access_token"].strip():
            return {"kind": "cli", "cli": cli, "region": "china"}
        return None

    if provider == "grok":
        auth = load_json(_grok_auth_path())
        if isinstance(auth, dict):
            for entry in auth.values():
                if isinstance(entry, dict) and isinstance(entry.get("key"), str) and entry["key"].strip():
                    return {"auth": auth}
        return None

    return None


def describe_snapshot(provider: str, snap: dict[str, Any]) -> dict[str, Any]:
    if provider == "codex":
        return _describe_codex(snap.get("auth") if isinstance(snap.get("auth"), dict) else snap)
    if provider == "claude":
        return _describe_claude(snap)
    if provider == "antigravity":
        return _describe_antigravity(snap)
    if provider == "qwen":
        return _describe_qwen(snap)
    if provider == "muse":
        return _describe_muse(snap)
    if provider == "kimi":
        return _describe_kimi(snap)
    if provider == "grok":
        return _describe_grok(snap)
    return {"email": None, "identity_key": None, "fingerprint": sha256_hex(json.dumps(snap, sort_keys=True)), "default_label": provider.title(), "kind": "oauth"}


def apply_live_snapshot(provider: str, snap: dict[str, Any]) -> None:
    """Write a credential snapshot directly into the CLI's live credential store."""
    if provider == "codex":
        auth = snap.get("auth") if isinstance(snap.get("auth"), dict) else snap
        write_json_atomic(_codex_path(), auth, indent=2, mode=0o600)
        return

    if provider == "claude":
        config_dir, creds_path, state_path = _claude_dirs()
        oauth = snap.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            raise ValueError("missing claudeAiOauth in snapshot")
        locks = _claude_acquire_locks(config_dir)
        try:
            current_creds = load_json(creds_path)
            if not isinstance(current_creds, dict):
                current_creds = {}
            new_creds = dict(current_creds)
            new_creds["claudeAiOauth"] = oauth
            write_json_atomic(creds_path, new_creds, mode=0o600)

            current_state = load_json(state_path)
            if isinstance(current_state, dict):
                new_state = dict(current_state)
                oauth_account = snap.get("oauthAccount")
                if isinstance(oauth_account, dict):
                    new_state["oauthAccount"] = oauth_account
                else:
                    new_state.pop("oauthAccount", None)
                write_json_atomic(state_path, new_state, indent=2, mode=0o600)
        finally:
            _claude_release_locks(locks)
        return

    if provider == "antigravity":
        raw = snap.get("raw")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("missing Antigravity keyring payload")
        ok = secret_store("gemini", "antigravity", "Password for 'antigravity' on 'gemini'", raw)
        if not ok:
            raise RuntimeError("secret-tool store failed (is Secret Service / KWallet unlocked?)")
        try:
            ANTIGRAVITY_CACHE.unlink(missing_ok=True)
        except OSError:
            pass
        return

    if provider == "qwen":
        kind = snap.get("kind") or "oauth"
        if kind == "api_key":
            api_key = snap.get("api_key")
            if not isinstance(api_key, str) or not api_key.strip():
                raise ValueError("missing Qwen API key")
            # Remove OAuth file if switching to API key so Qwen/Oasis pick API key cleanly.
            try:
                _qwen_oauth_path().unlink(missing_ok=True)
            except OSError:
                pass
            _update_codexbar_provider("qwen", api_key.strip(), snap.get("region") or "international")
        else:
            oauth = snap.get("oauth")
            if not isinstance(oauth, dict):
                raise ValueError("missing Qwen OAuth snapshot")
            write_json_atomic(_qwen_oauth_path(), oauth, indent=2, mode=0o600)
        return

    if provider == "muse":
        if isinstance(snap.get("auth"), dict):
            write_json_atomic(_muse_auth_path(), snap["auth"], indent=2, mode=0o600)
        if isinstance(snap.get("keyring_raw"), str) and snap["keyring_raw"].strip():
            secret_store("ai.meta.dev.credentials", "meta", "Muse Code credentials", snap["keyring_raw"])
        return

    if provider == "kimi":
        kind = snap.get("kind") or "cli"
        if kind == "api_key":
            api_key = snap.get("api_key")
            if not isinstance(api_key, str) or not api_key.strip():
                raise ValueError("missing Kimi API key")
            _update_codexbar_provider("kimi", api_key.strip(), snap.get("region") or "china")
        else:
            cli = snap.get("cli")
            if not isinstance(cli, dict):
                raise ValueError("missing Kimi CLI snapshot")
            write_json_atomic(_kimi_cli_path(), cli, indent=2, mode=0o600)
            # Clear codexbar apiKey override if present so CLI credential takes precedence.
            cfg_path = _codexbar_config_path()
            cfg = load_json(cfg_path)
            if isinstance(cfg, dict) and isinstance(cfg.get("providers"), list):
                changed = False
                for item in cfg["providers"]:
                    if isinstance(item, dict) and item.get("id") == "kimi" and item.get("apiKey"):
                        item.pop("apiKey", None)
                        changed = True
                if changed:
                    write_json_atomic(cfg_path, cfg, indent=2, mode=0o600)
        return

    if provider == "grok":
        auth = snap.get("auth") if isinstance(snap.get("auth"), dict) else snap
        write_json_atomic(_grok_auth_path(), auth, indent=2, mode=0o600)
        return

    raise ValueError(f"unsupported provider: {provider}")


def clear_live_credential(provider: str) -> None:
    """Clear active live credential before a clean fresh login (only after snapshotting)."""
    if provider == "codex":
        try:
            _codex_path().unlink(missing_ok=True)
        except OSError:
            pass
    elif provider == "antigravity":
        secret_clear("gemini", "antigravity")
        try:
            ANTIGRAVITY_CACHE.unlink(missing_ok=True)
        except OSError:
            pass
    elif provider == "qwen":
        try:
            _qwen_oauth_path().unlink(missing_ok=True)
        except OSError:
            pass
    elif provider == "muse":
        try:
            _muse_auth_path().unlink(missing_ok=True)
        except OSError:
            pass
        if find_bin("muse"):
            secret_clear("ai.meta.dev.credentials", "meta")
    elif provider == "kimi":
        try:
            _kimi_cli_path().unlink(missing_ok=True)
        except OSError:
            pass
    elif provider == "grok":
        try:
            _grok_auth_path().unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# OpenCode Go native SQLite / CLI multi-account adapter
# ---------------------------------------------------------------------------


def _opencode_list_rows(include_secrets: bool = False) -> list[dict[str, Any]]:
    db = _opencode_db_path()
    rows_out: list[dict[str, Any]] = []
    if db.exists():
        try:
            con = sqlite3.connect("file:" + urllib.parse.quote(str(db)) + "?mode=ro", uri=True, timeout=2)
            try:
                con.execute("PRAGMA query_only=ON")
                rows = con.execute(
                    "SELECT id, label, value, active, time_created, time_updated "
                    "FROM credential WHERE integration_id = 'opencode-go' "
                    "ORDER BY active DESC, time_updated DESC"
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            rows = []

        for cid, label, raw_val, active, created, updated in rows:
            if isinstance(raw_val, bytes):
                raw_val = raw_val.decode("utf-8", "ignore")
            key = ""
            kind = "api_key"
            if isinstance(raw_val, str):
                try:
                    doc = json.loads(raw_val)
                    if isinstance(doc, dict):
                        kind = "oauth" if doc.get("type") == "oauth" else "api_key"
                        key = doc.get("key") if doc.get("type") != "oauth" else (doc.get("access") or "")
                except ValueError:
                    pass
            item: dict[str, Any] = {
                "id": str(cid),
                "label": str(label or "OpenCode Go"),
                "email": None,
                "active": bool(active),
                "source": "opencode-db",
                "kind": kind,
                "created_at": int(created / 1000) if isinstance(created, (int, float)) and created > 10_000_000_000 else int(created or 0),
                "updated_at": int(updated / 1000) if isinstance(updated, (int, float)) and updated > 10_000_000_000 else int(updated or 0),
            }
            if include_secrets:
                item["_key"] = key
            rows_out.append(item)

    if rows_out:
        # Ensure at most one is marked active (if none active, first by time_updated is active)
        if not any(r["active"] for r in rows_out):
            rows_out[0]["active"] = True
        return rows_out

    # Legacy auth.json fallback
    auth = load_json(_opencode_auth_json_path())
    if isinstance(auth, dict):
        entry = auth.get("opencode-go") or auth.get("opencode")
        if isinstance(entry, dict):
            key = entry.get("key") if entry.get("type") != "oauth" else (entry.get("access") or "")
            if isinstance(key, str) and key:
                item = {
                    "id": "legacy_opencode_go",
                    "label": "OpenCode Go",
                    "email": None,
                    "active": True,
                    "source": "legacy",
                    "kind": "oauth" if entry.get("type") == "oauth" else "api_key",
                    "created_at": 0,
                    "updated_at": 0,
                }
                if include_secrets:
                    item["_key"] = key
                return [item]
    return []


def _opencode_sync_legacy_auth_json(raw_value_json: str) -> None:
    auth_path = _opencode_auth_json_path()
    if not auth_path.exists():
        return
    try:
        val_doc = json.loads(raw_value_json)
        if not isinstance(val_doc, dict):
            return
        auth = load_json(auth_path)
        if not isinstance(auth, dict):
            return
        auth["opencode-go"] = val_doc
        write_json_atomic(auth_path, auth, indent=2, mode=0o600)
    except Exception:
        pass


def _opencode_switch(account_id: str) -> dict[str, Any]:
    rows = _opencode_list_rows(include_secrets=True)
    target = next((r for r in rows if r["id"] == account_id), None)
    if not target:
        raise ValueError(f"OpenCode Go account not found: {account_id}")

    env = enriched_env()
    opencode_bin = find_bin("opencode", env)
    switched_via = "sqlite"

    if opencode_bin and target["source"] == "opencode-db":
        try:
            res = subprocess.run(
                [opencode_bin, "auth", "switch", "opencode-go", account_id],
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode == 0:
                switched_via = "opencode-cli"
        except (OSError, subprocess.SubprocessError):
            pass

    db = _opencode_db_path()
    if db.exists() and target["source"] == "opencode-db":
        con = sqlite3.connect(str(db), timeout=5)
        try:
            if switched_via != "opencode-cli":
                now_ms = int(time.time() * 1000)
                con.execute("UPDATE credential SET active = 0 WHERE integration_id = 'opencode-go'")
                con.execute(
                    "UPDATE credential SET active = 1, time_updated = ? WHERE id = ? AND integration_id = 'opencode-go'",
                    (now_ms, account_id),
                )
                con.commit()
            row = con.execute("SELECT value FROM credential WHERE id = ?", (account_id,)).fetchone()
            if row and isinstance(row[0], str):
                _opencode_sync_legacy_auth_json(row[0])
        finally:
            con.close()

    return {
        "ok": True,
        "provider": "opencodego",
        "account_id": account_id,
        "label": target["label"],
        "method": switched_via,
    }


def _opencode_add_key(label: str, api_key: str, activate: bool = True) -> dict[str, Any]:
    clean_key = api_key.strip()
    if not clean_key:
        raise ValueError("API key cannot be empty")
    clean_label = sanitize_label(label) or "OpenCode Go"
    val_json = json.dumps({"type": "key", "key": clean_key}, separators=(",", ":"))
    db = _opencode_db_path()
    now_ms = int(time.time() * 1000)

    if db.exists():
        new_id = f"cred_oasis_{secrets.token_hex(8)}"
        con = sqlite3.connect(str(db), timeout=5)
        try:
            if activate:
                con.execute("UPDATE credential SET active = 0 WHERE integration_id = 'opencode-go'")
            con.execute(
                "INSERT INTO credential (id, integration_id, label, value, connector_id, method_id, active, time_created, time_updated) "
                "VALUES (?, 'opencode-go', ?, ?, NULL, NULL, ?, ?, ?)",
                (new_id, clean_label, val_json, 1 if activate else 0, now_ms, now_ms),
            )
            con.commit()
        finally:
            con.close()
        if activate:
            _opencode_sync_legacy_auth_json(val_json)
        return {"ok": True, "provider": "opencodego", "account_id": new_id, "label": clean_label}

    # Fallback: write legacy auth.json if DB doesn't exist
    auth_path = _opencode_auth_json_path()
    auth = load_json(auth_path)
    if not isinstance(auth, dict):
        auth = {}
    auth["opencode-go"] = {"type": "key", "key": clean_key}
    write_json_atomic(auth_path, auth, indent=2, mode=0o600)
    return {"ok": True, "provider": "opencodego", "account_id": "legacy_opencode_go", "label": clean_label}


def _opencode_rename(account_id: str, new_label: str) -> dict[str, Any]:
    clean_label = sanitize_label(new_label)
    if not clean_label:
        raise ValueError("label cannot be empty")
    db = _opencode_db_path()
    if not db.exists():
        raise ValueError("OpenCode database not found")
    con = sqlite3.connect(str(db), timeout=5)
    try:
        cur = con.execute(
            "UPDATE credential SET label = ?, time_updated = ? WHERE id = ? AND integration_id = 'opencode-go'",
            (clean_label, int(time.time() * 1000), account_id),
        )
        con.commit()
        if cur.rowcount == 0:
            raise ValueError(f"OpenCode Go account not found: {account_id}")
    finally:
        con.close()
    return {"ok": True, "provider": "opencodego", "account_id": account_id, "label": clean_label}


def _opencode_remove(account_id: str) -> dict[str, Any]:
    rows = _opencode_list_rows(include_secrets=False)
    target = next((r for r in rows if r["id"] == account_id), None)
    if not target:
        raise ValueError(f"OpenCode Go account not found: {account_id}")
    if target["active"] and len(rows) > 1:
        raise ValueError("Switch to another OpenCode Go account before removing the active one.")
    db = _opencode_db_path()
    if not db.exists():
        raise ValueError("OpenCode database not found")
    con = sqlite3.connect(str(db), timeout=5)
    try:
        con.execute("DELETE FROM credential WHERE id = ? AND integration_id = 'opencode-go'", (account_id,))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "provider": "opencodego", "account_id": account_id}


# ---------------------------------------------------------------------------
# Vault synchronization & CRUD operations
# ---------------------------------------------------------------------------


def _match_account(accounts: list[dict[str, Any]], desc: dict[str, Any], active_id: Optional[str]) -> Optional[dict[str, Any]]:
    ident = desc.get("identity_key")
    fp = desc.get("fingerprint")
    if ident:
        for acc in accounts:
            if acc.get("identity_key") == ident:
                return acc
    if fp:
        for acc in accounts:
            if acc.get("fingerprint") == fp:
                return acc
    # If the provider rotated tokens (e.g. Codex/Claude) without changing identity,
    # check if email matches.
    email = desc.get("email")
    if email:
        for acc in accounts:
            if acc.get("email") and acc["email"].lower() == email.lower():
                return acc
    # If only active_id had no identity_key (e.g., anonymous token) and same kind:
    if active_id and not ident:
        for acc in accounts:
            if acc.get("id") == active_id and not acc.get("identity_key"):
                return acc
    return None


def sync_live_to_vault(provider: str, *, label_override: Optional[str] = None, auto_create: bool = True) -> Optional[dict[str, Any]]:
    """Sync the currently active live CLI credential into the vault.

    Returns the matched/created account metadata dict, or None if not signed in.
    """
    if provider == "opencodego":
        rows = _opencode_list_rows(include_secrets=False)
        return next((r for r in rows if r["active"]), None)

    if provider not in PROVIDER_SPECS:
        return None

    snap = read_live_snapshot(provider)
    if not snap:
        return None

    desc = describe_snapshot(provider, snap)
    index = _load_index()
    bucket = _provider_bucket(index, provider)
    accounts: list[dict[str, Any]] = bucket["accounts"]
    matched = _match_account(accounts, desc, bucket.get("active_id"))
    now = int(time.time())

    if matched is not None:
        matched["fingerprint"] = desc["fingerprint"]
        if desc.get("identity_key"):
            matched["identity_key"] = desc["identity_key"]
        if desc.get("email"):
            matched["email"] = desc["email"]
        if desc.get("region"):
            matched["region"] = desc["region"]
        if desc.get("kind"):
            matched["kind"] = desc["kind"]
        if label_override:
            clean = sanitize_label(label_override)
            if clean:
                matched["label"] = clean
        matched["updated_at"] = now
        bucket["active_id"] = matched["id"]
        save_account_secret(provider, matched["id"], snap)
        _save_index(index)
        return dict(matched)

    if not auto_create:
        return None

    base_label = sanitize_label(label_override or "") or desc["default_label"]
    existing_labels = {str(a.get("label", "")).lower() for a in accounts}
    label = base_label
    suffix = 2
    while label.lower() in existing_labels:
        label = f"{base_label} ({suffix})"
        suffix += 1

    acc_id = _new_account_id()
    entry: dict[str, Any] = {
        "id": acc_id,
        "label": label,
        "email": desc.get("email"),
        "identity_key": desc.get("identity_key"),
        "fingerprint": desc["fingerprint"],
        "kind": desc.get("kind") or "oauth",
        "created_at": now,
        "updated_at": now,
    }
    if desc.get("region"):
        entry["region"] = desc["region"]

    accounts.append(entry)
    bucket["active_id"] = acc_id
    save_account_secret(provider, acc_id, snap)
    _save_index(index)
    return dict(entry)


def list_provider_accounts(provider: str, *, include_secrets: bool = False) -> dict[str, Any]:
    """Return structured account state for one provider (never leaks secrets unless internal)."""
    spec = PROVIDER_SPECS.get(provider)
    if not spec:
        return {
            "provider": provider,
            "supports_accounts": False,
            "active_id": None,
            "active_label": None,
            "accounts": [],
        }

    if provider == "opencodego":
        rows = _opencode_list_rows(include_secrets=include_secrets)
        active = next((r for r in rows if r["active"]), None)
        return {
            "provider": provider,
            "name": spec["name"],
            "supports_accounts": True,
            "supports_login": spec["supports_login"],
            "supports_api_key": spec["supports_api_key"],
            "api_key_placeholder": spec.get("api_key_placeholder", ""),
            "regions": spec.get("regions", []),
            "login_hint": spec.get("login_hint", ""),
            "active_id": active["id"] if active else None,
            "active_label": active["label"] if active else None,
            "accounts": rows,
        }

    index = _load_index()
    bucket = _provider_bucket(index, provider)
    accounts_raw: list[dict[str, Any]] = bucket["accounts"]

    live_snap = read_live_snapshot(provider)
    live_desc = describe_snapshot(provider, live_snap) if live_snap else None
    matched = _match_account(accounts_raw, live_desc, bucket.get("active_id")) if live_desc else None

    # Keep the active vault entry fresh in-place if the CLI rotated tokens, without
    # creating vault files on pure read unless there are already vault entries or live login.
    active_id: Optional[str] = None
    active_label: Optional[str] = None
    out: list[dict[str, Any]] = []

    if matched is not None and live_snap is not None:
        active_id = matched["id"]
        active_label = matched.get("label") or live_desc["default_label"]
        # Silently refresh vault snapshot if fingerprint/tokens changed on disk
        if matched.get("fingerprint") != live_desc["fingerprint"]:
            matched["fingerprint"] = live_desc["fingerprint"]
            matched["updated_at"] = int(time.time())
            if live_desc.get("email"):
                matched["email"] = live_desc["email"]
            bucket["active_id"] = active_id
            try:
                save_account_secret(provider, active_id, live_snap)
                _save_index(index)
            except OSError:
                pass
        elif bucket.get("active_id") != active_id:
            bucket["active_id"] = active_id
            try:
                _save_index(index)
            except OSError:
                pass

    for acc in accounts_raw:
        aid = str(acc.get("id") or "")
        if not aid:
            continue
        is_active = aid == active_id
        item: dict[str, Any] = {
            "id": aid,
            "label": str(acc.get("label") or acc.get("email") or spec["name"]),
            "email": acc.get("email"),
            "active": is_active,
            "source": "vault",
            "kind": acc.get("kind") or "oauth",
            "region": acc.get("region"),
            "created_at": acc.get("created_at", 0),
            "updated_at": acc.get("updated_at", 0),
        }
        if include_secrets:
            item["_secret"] = live_snap if is_active and live_snap else load_account_secret(provider, aid)
        out.append(item)

    if live_snap is not None and live_desc is not None and matched is None:
        # Live CLI login exists that hasn't been explicitly saved to vault yet.
        active_id = "__live__"
        active_label = live_desc["default_label"]
        live_item: dict[str, Any] = {
            "id": "__live__",
            "label": live_desc["default_label"],
            "email": live_desc.get("email"),
            "active": True,
            "source": "live",
            "kind": live_desc.get("kind") or "oauth",
            "region": live_desc.get("region"),
            "created_at": 0,
            "updated_at": int(time.time()),
        }
        if include_secrets:
            live_item["_secret"] = live_snap
        out.insert(0, live_item)

    # Sort active first, then by label
    out.sort(key=lambda x: (not x["active"], x["label"].lower()))

    return {
        "provider": provider,
        "name": spec["name"],
        "supports_accounts": True,
        "supports_login": spec["supports_login"],
        "supports_api_key": spec["supports_api_key"],
        "api_key_placeholder": spec.get("api_key_placeholder", ""),
        "regions": spec.get("regions", []),
        "login_hint": spec.get("login_hint", ""),
        "active_id": active_id,
        "active_label": active_label,
        "accounts": out,
    }


def list_all_accounts() -> dict[str, Any]:
    return {
        "version": 1,
        "providers": {pid: list_provider_accounts(pid) for pid in PROVIDER_SPECS},
    }


def switch_account(provider: str, account_id: str) -> dict[str, Any]:
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unsupported provider: {provider}")

    if provider == "opencodego":
        result = _opencode_switch(account_id)
        notify_desktop("Oasis — Account Switched", f"OpenCode Go → {result['label']}")
        return result

    if account_id == "__live__":
        synced = sync_live_to_vault(provider)
        if not synced:
            raise ValueError("No active sign-in found to keep.")
        return {"ok": True, "provider": provider, "account_id": synced["id"], "label": synced["label"]}

    # Step 1: Always sync current live credential into vault first so we never lose
    # refreshed tokens or an unsaved manual login!
    sync_live_to_vault(provider, auto_create=True)

    index = _load_index()
    bucket = _provider_bucket(index, provider)
    target = next((a for a in bucket["accounts"] if a.get("id") == account_id), None)
    if not target:
        raise ValueError(f"Account not found: {account_id}")

    snap = load_account_secret(provider, account_id)
    if not snap:
        raise ValueError(f"Saved credential missing for {target.get('label', account_id)}")

    # Step 2: Apply target snapshot directly to the CLI's live credential store.
    apply_live_snapshot(provider, snap)

    bucket["active_id"] = account_id
    target["updated_at"] = int(time.time())
    _save_index(index)

    label = str(target.get("label") or PROVIDER_SPECS[provider]["name"])
    notify_desktop("Oasis — Account Switched", f"{PROVIDER_SPECS[provider]['name']} → {label}")
    return {
        "ok": True,
        "provider": provider,
        "account_id": account_id,
        "label": label,
    }


def add_api_key_account(
    provider: str,
    label: str,
    api_key: str,
    region: Optional[str] = None,
    activate: bool = True,
) -> dict[str, Any]:
    spec = PROVIDER_SPECS.get(provider)
    if not spec or not spec.get("supports_api_key"):
        raise ValueError(f"Provider {provider} does not support API key accounts")

    clean_key = (api_key or "").strip()
    if not clean_key:
        raise ValueError("API key cannot be empty")

    if provider == "opencodego":
        res = _opencode_add_key(label, clean_key, activate=activate)
        notify_desktop("Oasis — Account Added", f"OpenCode Go → {res['label']}")
        return res

    # Save current live login first so switching to the new API key doesn't lose it.
    if activate:
        sync_live_to_vault(provider, auto_create=True)

    default_region = (spec.get("regions") or ["international"])[0]
    chosen_region = (region or default_region).strip().lower()
    if spec.get("regions") and chosen_region not in spec["regions"]:
        chosen_region = default_region

    snap = {"kind": "api_key", "api_key": clean_key, "region": chosen_region}
    desc = describe_snapshot(provider, snap)

    index = _load_index()
    bucket = _provider_bucket(index, provider)
    now = int(time.time())
    acc_id = _new_account_id()
    acc_label = sanitize_label(label) or desc["default_label"]

    entry: dict[str, Any] = {
        "id": acc_id,
        "label": acc_label,
        "email": None,
        "identity_key": desc["identity_key"],
        "fingerprint": desc["fingerprint"],
        "kind": "api_key",
        "region": chosen_region,
        "created_at": now,
        "updated_at": now,
    }
    bucket["accounts"].append(entry)
    save_account_secret(provider, acc_id, snap)

    if activate:
        apply_live_snapshot(provider, snap)
        bucket["active_id"] = acc_id

    _save_index(index)
    notify_desktop("Oasis — Account Added", f"{spec['name']} → {acc_label}")
    return {"ok": True, "provider": provider, "account_id": acc_id, "label": acc_label}


def rename_account(provider: str, account_id: str, new_label: str) -> dict[str, Any]:
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unsupported provider: {provider}")
    clean = sanitize_label(new_label)
    if not clean:
        raise ValueError("Label cannot be empty")

    if provider == "opencodego":
        return _opencode_rename(account_id, clean)

    if account_id == "__live__":
        synced = sync_live_to_vault(provider, label_override=clean, auto_create=True)
        if not synced:
            raise ValueError("No active sign-in found")
        return {"ok": True, "provider": provider, "account_id": synced["id"], "label": synced["label"]}

    index = _load_index()
    bucket = _provider_bucket(index, provider)
    target = next((a for a in bucket["accounts"] if a.get("id") == account_id), None)
    if not target:
        raise ValueError(f"Account not found: {account_id}")
    target["label"] = clean
    target["updated_at"] = int(time.time())
    _save_index(index)
    return {"ok": True, "provider": provider, "account_id": account_id, "label": clean}


def remove_account(provider: str, account_id: str) -> dict[str, Any]:
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unsupported provider: {provider}")

    if provider == "opencodego":
        return _opencode_remove(account_id)

    if account_id == "__live__":
        raise ValueError("Live account is not saved in Oasis vault.")

    index = _load_index()
    bucket = _provider_bucket(index, provider)
    before = len(bucket["accounts"])
    bucket["accounts"] = [a for a in bucket["accounts"] if a.get("id") != account_id]
    if len(bucket["accounts"]) == before:
        raise ValueError(f"Account not found: {account_id}")
    if bucket.get("active_id") == account_id:
        bucket["active_id"] = None
    delete_account_secret(provider, account_id)
    _save_index(index)
    return {"ok": True, "provider": provider, "account_id": account_id}


def update_vault_claude_oauth(account_id: str, updated_oauth: dict[str, Any]) -> None:
    """Persist refreshed Claude OAuth tokens back into an inactive vault snapshot."""
    snap = load_account_secret("claude", account_id)
    if not isinstance(snap, dict):
        return
    current = snap.get("claudeAiOauth")
    if not isinstance(current, dict):
        return
    merged = dict(current)
    merged.update(updated_oauth)
    snap["claudeAiOauth"] = merged
    try:
        save_account_secret("claude", account_id, snap)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Native CLI interactive login launcher & runner
# ---------------------------------------------------------------------------


def _find_terminal(env: dict[str, str]) -> Optional[tuple[str, list[str]]]:
    """Return (name, argv_prefix) for an installed Linux terminal emulator."""
    candidates: list[tuple[str, list[str]]] = [
        ("konsole", ["-e"]),
        ("alacritty", ["-e"]),
        ("kitty", ["--"]),
        ("ghostty", ["-e"]),
        ("wezterm", ["start", "--"]),
        ("xdg-terminal-exec", []),
        ("xterm", ["-e"]),
    ]
    for name, flags in candidates:
        path = find_bin(name, env)
        if path:
            return name, [path] + flags
    return None


def _login_command_for(provider: str, env: dict[str, str]) -> tuple[Optional[list[str]], str]:
    spec = PROVIDER_SPECS.get(provider, {})
    binary_name = spec.get("cli_binary") or provider
    binary = find_bin(binary_name, env)
    if not binary:
        return None, f"`{binary_name}` CLI was not found in PATH."

    if provider == "codex":
        return [binary, "login"], "Complete OpenAI sign-in in your browser."
    if provider == "claude":
        return [binary, "auth", "login"], "Complete Anthropic sign-in in your browser."
    if provider == "antigravity":
        return [binary], "Complete Google sign-in in your browser, then press Ctrl+C or exit agy once signed in."
    if provider == "opencodego":
        return [binary, "auth", "login", "-p", "opencode-go"], "Enter your OpenCode Go credentials."
    if provider == "qwen":
        return [binary], "Complete Qwen sign-in, then exit qwen to save the account."
    if provider == "muse":
        return [binary, "login"], "Complete Meta Muse device-code login."
    if provider == "kimi":
        return [binary], "Complete Kimi sign-in, then exit kimi to save the account."
    if provider == "grok":
        return [binary, "login"], "Complete Grok sign-in."
    return None, f"No native login flow configured for {provider}."


def launch_login_terminal(provider: str, label: str = "") -> dict[str, Any]:
    if provider not in PROVIDER_SPECS:
        raise ValueError(f"unsupported provider: {provider}")
    env = enriched_env()
    cmd, _ = _login_command_for(provider, env)
    if not cmd:
        binary_name = PROVIDER_SPECS[provider].get("cli_binary") or provider
        raise RuntimeError(f"`{binary_name}` CLI not found in PATH.")

    term = _find_terminal(env)
    if not term:
        raise RuntimeError("No terminal emulator found (install konsole, alacritty, kitty, or xterm).")

    term_name, prefix = term
    script_path = str(Path(__file__).resolve())
    runner_cmd = [sys.executable or "python3", script_path, "login-runner", "--provider", provider]
    clean_label = sanitize_label(label)
    if clean_label:
        runner_cmd += ["--label", clean_label]

    subprocess.Popen(
        prefix + runner_cmd,
        env=env,
        cwd=str(HOME),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {
        "ok": True,
        "launched": True,
        "provider": provider,
        "terminal": term_name,
        "message": f"Opened {term_name} to sign into {PROVIDER_SPECS[provider]['name']}.",
    }


def run_login_interactive(provider: str, label: str = "") -> int:
    """Interactive terminal workflow: backup current -> run CLI login -> snapshot or restore."""
    spec = PROVIDER_SPECS.get(provider)
    if not spec:
        print(f"Unsupported provider: {provider}", file=sys.stderr)
        return 2

    env = enriched_env()
    cmd, instructions = _login_command_for(provider, env)
    if not cmd:
        print(f"Error: {instructions}", file=sys.stderr)
        input("\nPress Enter to close…")
        return 1

    print(f"=== Oasis: Add {spec['name']} Account ===")
    print(instructions)
    print()

    if provider == "opencodego":
        before_ids = {r["id"] for r in _opencode_list_rows(include_secrets=False)}
        try:
            subprocess.run(cmd, env=env, cwd=str(HOME), check=False)
        except KeyboardInterrupt:
            pass
        after_rows = _opencode_list_rows(include_secrets=False)
        added = [r for r in after_rows if r["id"] not in before_ids]
        if added:
            new_row = added[0]
            if sanitize_label(label):
                try:
                    _opencode_rename(new_row["id"], label)
                    new_row["label"] = sanitize_label(label)
                except Exception:
                    pass
            try:
                _opencode_switch(new_row["id"])
            except Exception:
                pass
            print(f"\n✓ Added and activated OpenCode Go account: {new_row['label']}")
            notify_desktop("Oasis — Account Added", f"OpenCode Go → {new_row['label']}")
        else:
            print("\nNo new OpenCode Go account detected.")
        time.sleep(1.5)
        return 0

    # Vault-managed provider: snapshot current live account FIRST so nothing is lost.
    previous_snap = read_live_snapshot(provider)
    previous_fp: Optional[str] = None
    if previous_snap:
        prev_entry = sync_live_to_vault(provider, auto_create=True)
        previous_fp = describe_snapshot(provider, previous_snap)["fingerprint"]
        if prev_entry:
            print(f"Saved current active account to Oasis vault: {prev_entry['label']}")

    # Clear live credential for CLIs that skip login if already authenticated.
    needs_clear = provider in ("codex", "antigravity", "qwen", "muse", "kimi", "grok")
    if needs_clear and previous_snap:
        clear_live_credential(provider)

    print(f"Running: {' '.join(cmd)}\n")
    rc = 0
    try:
        res = subprocess.run(cmd, env=env, cwd=str(HOME), check=False)
        rc = res.returncode
    except KeyboardInterrupt:
        rc = 130
    except OSError as exc:
        print(f"\nFailed to start CLI: {exc}", file=sys.stderr)
        rc = 1

    new_snap = read_live_snapshot(provider)
    new_fp = describe_snapshot(provider, new_snap)["fingerprint"] if new_snap else None

    if new_snap and (new_fp != previous_fp or previous_snap is None):
        saved = sync_live_to_vault(provider, label_override=label or None, auto_create=True)
        saved_label = saved["label"] if saved else spec["name"]
        print(f"\n✓ Saved and activated {spec['name']} account: {saved_label}")
        notify_desktop("Oasis — Account Added", f"{spec['name']} → {saved_label}")
        # Refresh usage cache immediately in the background so Oasis updates cleanly.
        helper = Path(__file__).with_name("ai_limits.py")
        if helper.exists():
            try:
                subprocess.run([sys.executable or "python3", str(helper), "fetch"], env=env, capture_output=True, timeout=20, check=False)
            except Exception:
                pass
        time.sleep(1.2)
        return 0

    # Login was canceled or produced no new credential — restore previous live account!
    if previous_snap:
        try:
            apply_live_snapshot(provider, previous_snap)
            print("\nLogin canceled or unchanged — restored your previous active account.")
        except Exception as exc:
            print(f"\nWarning: failed to restore previous account: {exc}", file=sys.stderr)
    else:
        print(f"\nNo {spec['name']} sign-in detected (exit code {rc}).")

    try:
        input("\nPress Enter to close…")
    except EOFError:
        pass
    return 1 if rc != 0 else 0


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="oasis-accounts")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List accounts (metadata only, no secrets)")
    p_list.add_argument("--provider", default="")
    p_list.add_argument("--pretty", action="store_true")

    p_snap = sub.add_parser("snapshot", help="Save currently active CLI sign-in to Oasis vault")
    p_snap.add_argument("--provider", required=True)
    p_snap.add_argument("--label", default="")

    p_switch = sub.add_parser("switch", help="Switch active account (updates CLI credential directly)")
    p_switch.add_argument("--provider", required=True)
    p_switch.add_argument("--account", required=True)

    p_key = sub.add_parser("add-key", help="Add an API key account (reads key from stdin)")
    p_key.add_argument("--provider", required=True)
    p_key.add_argument("--label", default="")
    p_key.add_argument("--region", default="")
    p_key.add_argument("--no-activate", action="store_true")

    p_login = sub.add_parser("launch-login", help="Open terminal to add a new account via native CLI login")
    p_login.add_argument("--provider", required=True)
    p_login.add_argument("--label", default="")

    p_runner = sub.add_parser("login-runner", help="Internal interactive login wrapper")
    p_runner.add_argument("--provider", required=True)
    p_runner.add_argument("--label", default="")

    p_rename = sub.add_parser("rename", help="Rename a saved account")
    p_rename.add_argument("--provider", required=True)
    p_rename.add_argument("--account", required=True)
    p_rename.add_argument("--label", required=True)

    p_remove = sub.add_parser("remove", help="Remove a saved account from Oasis vault")
    p_remove.add_argument("--provider", required=True)
    p_remove.add_argument("--account", required=True)

    args = parser.parse_args(argv)

    try:
        if args.command == "login-runner":
            return run_login_interactive(args.provider, args.label)

        if args.command == "list":
            result = list_provider_accounts(args.provider) if args.provider else list_all_accounts()
            print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
            return 0

        if args.command == "snapshot":
            if args.provider not in PROVIDER_SPECS:
                raise ValueError(f"unsupported provider: {args.provider}")
            saved = sync_live_to_vault(args.provider, label_override=args.label or None, auto_create=True)
            if not saved:
                raise RuntimeError(f"No active {PROVIDER_SPECS[args.provider]['name']} sign-in found to save.")
            out = {
                "ok": True,
                "provider": args.provider,
                "account_id": saved["id"],
                "label": saved["label"],
                "state": list_provider_accounts(args.provider),
            }
            print(json.dumps(out, ensure_ascii=False))
            return 0

        if args.command == "switch":
            res = switch_account(args.provider, args.account)
            res["state"] = list_provider_accounts(args.provider)
            print(json.dumps(res, ensure_ascii=False))
            return 0

        if args.command == "add-key":
            raw_key = sys.stdin.read().strip()
            res = add_api_key_account(
                args.provider,
                args.label,
                raw_key,
                region=args.region or None,
                activate=not args.no_activate,
            )
            res["state"] = list_provider_accounts(args.provider)
            print(json.dumps(res, ensure_ascii=False))
            return 0

        if args.command == "launch-login":
            res = launch_login_terminal(args.provider, args.label)
            print(json.dumps(res, ensure_ascii=False))
            return 0

        if args.command == "rename":
            res = rename_account(args.provider, args.account, args.label)
            res["state"] = list_provider_accounts(args.provider)
            print(json.dumps(res, ensure_ascii=False))
            return 0

        if args.command == "remove":
            res = remove_account(args.provider, args.account)
            res["state"] = list_provider_accounts(args.provider)
            print(json.dumps(res, ensure_ascii=False))
            return 0

    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
