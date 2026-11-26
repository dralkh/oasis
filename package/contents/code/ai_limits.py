#!/usr/bin/env python3
"""Oasis helper: read-only subscription usage for KDE Plasma.

Prints one JSON document. Never prints, logs or caches credentials.

Security model
--------------
* Every HTTP request is pinned to an explicit host allowlist and redirects
  are refused (urllib would otherwise forward Authorization on a 30x).
* Environment proxies are ignored (ProxyHandler({})) so a hostile
  http_proxy cannot capture bearer tokens.
* Codex/Grok/Cursor refresh tokens are never touched; those credential files
  are opened read-only.
* Antigravity refreshes its non-rotating Google OAuth token into a private
  0600 cache file (the refresh token is never copied there).
* Claude Code access tokens expire every ~8h. When expired, the helper acquires
  Claude Code's own lock directories (.oauth_refresh.lock and .claude.lock),
  refreshes via platform.claude.com/v1/oauth/token, and atomically updates
  ~/.claude/.credentials.json with a compare-and-swap check so running Claude
  CLI sessions stay signed in.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

VERSION = "1.3.0"
UA = f"ai-limits-kde/{VERSION}"

HOME = Path.home()
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache")) / "ai-limits"
CACHE_FILE = CACHE_DIR / "last.json"
ANTIGRAVITY_CACHE = CACHE_DIR / "antigravity.json"

TIMEOUT = 12
MAX_BODY = 4 * 1024 * 1024
MAX_CRED = 4 * 1024 * 1024

ALLOWED_HOSTS = {
    "chatgpt.com",
    "api.anthropic.com",
    "platform.claude.com",
    "cloudcode-pa.googleapis.com",
    "daily-cloudcode-pa.googleapis.com",
    "oauth2.googleapis.com",
    "opencode.ai",
    "cli-chat-proxy.grok.com",
    "auth.x.ai",
    "api.meta.ai",
    "api.kimi.com",
    "api.kimi.ai",
    "api2.cursor.sh",
    "portal.qwen.ai",
    "chat.qwen.ai",
    "coding.dashscope.aliyuncs.com",
    "coding-intl.dashscope.aliyuncs.com",
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
}

# The daily summary endpoint carries live Antigravity quota across both shared
# pools (Gemini and Claude/GPT); cloudcode-pa is kept as a fallback.
AG_USER_AGENT = "antigravity/hub/2.24.0"
AG_SUMMARY_URLS = (
    "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
    "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary",
)
AG_CANONICAL_BUCKETS = ("gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly")

# Public Claude Code OAuth client ID and CLI user-agent used for token renewal
# and to query usage-limit reset grants (cedar_ember).
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_USER_AGENT = "claude-cli/2.1.330 (external, cli)"

WINDOW_LABELS = {
    18000: ("5h", "5h"),
    604800: ("Week", "1w"),
    2628000: ("Month", "1mo"),
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect,
)


def _check_host(url: str) -> None:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"refusing non-allowlisted host: {host}")


def http_with_headers(
    method: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: float = TIMEOUT,
) -> tuple[int, Any, dict[str, str]]:
    """Return (status, parsed-json-or-None, lowercase-response-headers)."""
    _check_host(url)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})},
        method=method,
    )
    resp_headers: dict[str, str] = {}
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_BODY)
            status = resp.status
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_BODY) if exc.fp else b""
        status = exc.code
        if exc.headers:
            resp_headers = {k.lower(): v for k, v in exc.headers.items()}
    except Exception:
        return 0, None, {}
    if not raw:
        return status, None, resp_headers
    try:
        return status, json.loads(raw), resp_headers
    except (ValueError, UnicodeDecodeError):
        return status, None, resp_headers


def http(
    method: str,
    url: str,
    headers: Optional[dict[str, str]] = None,
    body: Optional[bytes] = None,
    timeout: float = TIMEOUT,
) -> tuple[int, Any]:
    """Return (status, parsed-json-or-None). Body is never included in errors."""
    status, data, _ = http_with_headers(method, url, headers, body, timeout)
    return status, data


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    return http(
        "POST",
        url,
        {"Content-Type": "application/json", **headers},
        json.dumps(payload, separators=(",", ":")).encode(),
    )


def post_form(url: str, form: dict[str, str], headers: Optional[dict[str, str]] = None) -> tuple[int, Any]:
    return http(
        "POST",
        url,
        {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
        urllib.parse.urlencode(form).encode(),
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@dataclass
class Window:
    id: str
    label: str
    short: str
    used: float
    resets_at: Optional[int] = None
    period: Optional[int] = None
    primary: bool = True
    estimated: bool = False


@dataclass
class ResetCredit:
    count: int
    expires_at: Optional[int] = None
    label: Optional[str] = None
    usable_now: Optional[bool] = None


@dataclass
class Subscription:
    ends_at: Optional[int] = None
    renews: Optional[bool] = None
    status: Optional[str] = None
    period: Optional[str] = None


@dataclass
class Provider:
    id: str
    name: str
    status: str = "ok"
    plan: Optional[str] = None
    windows: list[Window] = field(default_factory=list)
    resets: list[ResetCredit] = field(default_factory=list)
    subscription: Optional[Subscription] = None
    hint: Optional[str] = None
    error: Optional[str] = None
    stale: bool = False
    account: Optional[str] = None
    account_id: Optional[str] = None
    accounts: list[dict[str, Any]] = field(default_factory=list)
    supports_accounts: bool = False


def load_json(path: Path) -> Any:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_CRED)
        return json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def jwt_claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        value = json.loads(base64.urlsafe_b64decode(part))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def epoch(value: Any) -> Optional[int]:
    if isinstance(value, (int, float)) and value > 0:
        # Accept both seconds and milliseconds.
        return int(value / 1000) if value > 10_000_000_000 else int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
            return epoch(number)
        except ValueError:
            pass
        try:
            return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 1)


def secret_lookup(service: str, username: Optional[str] = None) -> Optional[str]:
    """Read one Secret Service entry without ever putting a secret in argv."""
    tool = None
    for candidate in ("secret-tool", "/usr/bin/secret-tool"):
        if os.path.isabs(candidate):
            if os.access(candidate, os.X_OK):
                tool = candidate
                break
        else:
            from shutil import which

            tool = which(candidate)
            if tool:
                break
    if not tool:
        return None
    args = [tool, "lookup", "service", service]
    if username:
        args += ["username", username]
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Codex (ChatGPT plan)
# ---------------------------------------------------------------------------


def _codex_reset_credits(headers: dict[str, str], usage_data: dict[str, Any]) -> list[ResetCredit]:
    """Fetch available Codex rate-limit reset credits and their expiry dates."""
    summary = usage_data.get("rate_limit_reset_credits")
    if not isinstance(summary, dict):
        return []
    avail = number(summary.get("available_count"))
    applicable = number(summary.get("applicable_available_count"))
    if (avail is None or int(avail) <= 0) and (applicable is None or int(applicable) <= 0):
        return []

    now = int(time.time())
    status, data = http("GET", "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits", headers)
    items: list[ResetCredit] = []
    if status == 200 and isinstance(data, dict) and isinstance(data.get("credits"), list):
        grouped: dict[tuple[Optional[int], str], int] = {}
        for credit in data["credits"]:
            if not isinstance(credit, dict):
                continue
            if credit.get("status") != "available":
                continue
            if credit.get("is_supported_by_plan") is False:
                continue
            exp = epoch(credit.get("expires_at"))
            if exp is not None and exp <= now:
                continue
            title = credit.get("title")
            label = title.strip() if isinstance(title, str) and title.strip() else "Rate limit reset"
            key = (exp, label)
            grouped[key] = grouped.get(key, 0) + 1
        for (exp, label), count in grouped.items():
            items.append(ResetCredit(count=count, expires_at=exp, label=label))
        items.sort(key=lambda item: (item.expires_at is None, item.expires_at or 0))

    if not items:
        fallback_count = int(avail or applicable or 0)
        if fallback_count > 0:
            items.append(ResetCredit(count=fallback_count, label="Rate limit reset"))
    return items


def _codex_subscription(headers: dict[str, str], account: str) -> Optional[Subscription]:
    if not account:
        return None
    url = "https://chatgpt.com/backend-api/subscriptions?account_id=" + urllib.parse.quote(account, safe="")
    status, data = http("GET", url, headers)
    if status != 200 or not isinstance(data, dict):
        return None
    entitlement = data.get("entitlement") if isinstance(data.get("entitlement"), dict) else {}
    ends_at = (
        epoch(data.get("active_until"))
        or epoch(entitlement.get("renews_at"))
        or epoch(entitlement.get("expires_at"))
    )
    will_renew = data.get("will_renew")
    if not isinstance(will_renew, bool):
        last_sub = data.get("last_active_subscription") if isinstance(data.get("last_active_subscription"), dict) else {}
        if isinstance(last_sub.get("will_renew"), bool):
            will_renew = last_sub["will_renew"]
        else:
            will_renew = None
    period = data.get("billing_period") or entitlement.get("billing_period")
    period_str = period.strip().lower() if isinstance(period, str) and period.strip() else None
    sub_status = "canceled" if will_renew is False else ("active" if data.get("is_active") else None)
    if ends_at is None and sub_status is None:
        return None
    return Subscription(ends_at=ends_at, renews=will_renew, status=sub_status, period=period_str)


def _codex_fetch_with_token(
    token: str,
    account: str,
    plan: Optional[str] = None,
    *,
    include_extras: bool = True,
) -> Provider:
    provider = Provider("codex", "Codex")
    if not token:
        provider.status = "not_configured"
        provider.hint = "Sign in with `codex login`."
        return provider

    claims = jwt_claims(token)
    if claims.get("exp") and int(claims["exp"]) <= int(time.time()) + 30:
        provider.status = "expired"
        provider.hint = "Codex token expired — run any codex command to renew it."
        return provider

    if not account:
        auth_claim = claims.get("https://api.openai.com/auth")
        if isinstance(auth_claim, dict):
            account = auth_claim.get("chatgpt_account_id") or ""

    headers = {"Authorization": f"Bearer {token}"}
    if account:
        headers["ChatGPT-Account-Id"] = account
    status, data = http("GET", "https://chatgpt.com/backend-api/wham/usage", headers)

    if status in (401, 403):
        provider.status = "expired"
        provider.hint = "ChatGPT rejected this token — run `codex login`."
        return provider
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider

    provider.plan = data.get("plan_type") if isinstance(data.get("plan_type"), str) else plan

    raw_windows: list[tuple[dict[str, Any], int]] = []
    rate = data.get("rate_limit")
    if isinstance(rate, dict):
        for key, fallback in (("primary_window", 18000), ("secondary_window", 604800)):
            value = rate.get(key)
            if isinstance(value, dict):
                raw_windows.append((value, fallback))
    elif isinstance(rate, list):
        for value in rate:
            if isinstance(value, dict):
                raw_windows.append((value, int(number(value.get("limit_window_seconds")) or 18000)))

    seen: set[str] = set()
    for raw, fallback in raw_windows:
        used = number(raw.get("used_percent"))
        if used is None:
            remaining = number(raw.get("remaining_percent"))
            used = 100 - remaining if remaining is not None else None
        if used is None:
            continue
        period = int(number(raw.get("limit_window_seconds")) or fallback)
        key, short_label = WINDOW_LABELS.get(period, ("Window", "win"))
        if key in seen:
            continue
        seen.add(key)
        reset = epoch(raw.get("reset_at"))
        if reset is None:
            after = number(raw.get("reset_after_seconds"))
            reset = int(time.time() + after) if after is not None else None
        provider.windows.append(Window(key, key if key != "Week" else "Weekly", short_label, clamp(used), reset, period))

    if include_extras:
        provider.resets = _codex_reset_credits(headers, data)
        provider.subscription = _codex_subscription(headers, account)

    if not provider.windows:
        provider.status = "error"
        provider.error = "no quota windows in response"
    return provider


def fetch_codex() -> Provider:
    token = ""
    account = ""
    plan = None

    doc = load_json(Path(os.environ.get("CODEX_HOME") or (HOME / ".codex")) / "auth.json")
    if isinstance(doc, dict):
        tokens = doc.get("tokens") if isinstance(doc.get("tokens"), dict) else {}
        token = tokens.get("access_token") or ""
        account = tokens.get("account_id") or ""
        plan = doc.get("plan_type") if isinstance(doc.get("plan_type"), str) else None

    if not token:
        auth = load_json(Path(os.environ.get("XDG_DATA_HOME") or (HOME / ".local/share")) / "opencode" / "auth.json")
        entry = auth.get("openai") if isinstance(auth, dict) else None
        if isinstance(entry, dict) and entry.get("type") == "oauth":
            token = entry.get("access") or ""
            account = entry.get("accountId") or entry.get("account_id") or ""

    return _codex_fetch_with_token(token, account, plan, include_extras=True)


# ---------------------------------------------------------------------------
# Claude (Claude Code OAuth)
# ---------------------------------------------------------------------------


def _claude_read_oauth(path: Path) -> Optional[dict[str, Any]]:
    doc = load_json(path)
    oauth = doc.get("claudeAiOauth") if isinstance(doc, dict) else None
    return oauth if isinstance(oauth, dict) else None


def _claude_write_oauth_cas(
    path: Path,
    expected_refresh: str,
    updated: dict[str, Any],
) -> bool:
    """Atomically update claudeAiOauth if refreshToken still matches expected."""
    doc = load_json(path)
    if not isinstance(doc, dict):
        return False
    current = doc.get("claudeAiOauth")
    if not isinstance(current, dict):
        return False
    current_refresh = current.get("refreshToken")
    if current_refresh not in ("", None, expected_refresh):
        # Another process rotated the credential while we were refreshing.
        return False
    merged = dict(current)
    merged.update(updated)
    new_doc = dict(doc)
    new_doc["claudeAiOauth"] = merged
    tmp = path.with_name(f".credentials.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(new_doc, fh, separators=(",", ":"))
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _claude_acquire_locks(config_dir: Path) -> Optional[tuple[Path, Optional[Path]]]:
    """Acquire Claude Code's own directory locks (.oauth_refresh.lock + legacy .lock)."""
    new_lock = config_dir / ".oauth_refresh.lock"
    try:
        real_dir = config_dir.resolve()
    except OSError:
        real_dir = config_dir
    legacy_lock = real_dir.with_name(real_dir.name + ".lock")
    try:
        os.mkdir(new_lock, 0o700)
    except OSError:
        return None
    try:
        os.mkdir(legacy_lock, 0o700)
        return new_lock, legacy_lock
    except FileExistsError:
        try:
            os.rmdir(new_lock)
        except OSError:
            pass
        return None
    except OSError:
        # Parent directory might not be writable; keep the primary lock.
        return new_lock, None


def _claude_release_locks(locks: tuple[Path, Optional[Path]]) -> None:
    new_lock, legacy_lock = locks
    for lock in (legacy_lock, new_lock):
        if lock is None:
            continue
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _claude_refresh_oauth(
    config_dir: Path,
    path: Path,
    stale_access: str,
) -> tuple[str, Optional[dict[str, Any]]]:
    """Renew Claude's OAuth token safely under Claude Code's refresh lock.

    Returns (outcome, oauth_dict) where outcome is one of:
    'ok', 'expired', 'transient'.
    """
    locks: Optional[tuple[Path, Optional[Path]]] = None
    for attempt in range(5):
        locks = _claude_acquire_locks(config_dir)
        if locks is not None:
            break
        time.sleep(0.5)
        fresh = _claude_read_oauth(path)
        if fresh and fresh.get("accessToken") and fresh.get("accessToken") != stale_access:
            return "ok", fresh
    if locks is None:
        fresh = _claude_read_oauth(path)
        if fresh and fresh.get("accessToken"):
            exp = number(fresh.get("expiresAt"))
            if fresh.get("accessToken") != stale_access or (exp is not None and exp / 1000 > time.time() + 30):
                return "ok", fresh
        return "transient", None

    try:
        oauth = _claude_read_oauth(path)
        if not oauth:
            return "expired", None
        access = oauth.get("accessToken")
        exp = number(oauth.get("expiresAt"))
        if isinstance(access, str) and access and access != stale_access and (exp is None or exp / 1000 > time.time() + 30):
            return "ok", oauth

        refresh_token = oauth.get("refreshToken")
        if not isinstance(refresh_token, str) or not refresh_token:
            return "expired", None

        client_id = oauth.get("clientId")
        if not isinstance(client_id, str) or not client_id:
            client_id = CLAUDE_CLIENT_ID
        scopes = oauth.get("scopes")
        body: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if isinstance(scopes, list) and scopes:
            scope_str = " ".join(s for s in scopes if isinstance(s, str) and s)
            if scope_str:
                body["scope"] = scope_str

        status, data = post_json(CLAUDE_TOKEN_URL, body, {"User-Agent": CLAUDE_USER_AGENT})
        if status in (400, 401, 403):
            return "expired", None
        if status != 200 or not isinstance(data, dict):
            return "transient", None

        new_access = data.get("access_token")
        if not isinstance(new_access, str) or not new_access:
            return "transient", None
        new_refresh = data.get("refresh_token")
        if not isinstance(new_refresh, str) or not new_refresh:
            new_refresh = refresh_token
        expires_in = number(data.get("expires_in"))
        now_s = time.time()

        updated: dict[str, Any] = {
            "accessToken": new_access,
            "refreshToken": new_refresh,
        }
        if expires_in is not None and expires_in > 0:
            updated["expiresAt"] = int((now_s + expires_in) * 1000)
        refresh_exp = number(data.get("refresh_token_expires_in"))
        if refresh_exp is not None and refresh_exp > 0:
            updated["refreshTokenExpiresAt"] = int((now_s + refresh_exp) * 1000)
        granted_scope = data.get("scope")
        if isinstance(granted_scope, str) and granted_scope.strip():
            updated["scopes"] = granted_scope.split()

        _claude_write_oauth_cas(path, refresh_token, updated)
        merged = dict(oauth)
        merged.update(updated)
        return "ok", merged
    finally:
        _claude_release_locks(locks)


def _claude_reset_credits(data: dict[str, Any]) -> list[ResetCredit]:
    """Parse available Claude usage-limit resets (cedar_ember grants)."""
    block = data.get("cedar_ember")
    if not isinstance(block, dict) or block.get("eligible") is not True:
        return []
    grants = block.get("grants")
    if not isinstance(grants, list):
        return []
    now = int(time.time())
    type_labels = {
        "full": "Full limit reset",
        "weekly": "Weekly limit reset",
        "session": "5-hour limit reset",
    }
    items: list[ResetCredit] = []
    for grant in grants:
        if not isinstance(grant, dict) or grant.get("paused") is True:
            continue
        left = number(grant.get("resets_left"))
        if left is None or int(left) <= 0:
            continue
        exp = epoch(grant.get("ends_at"))
        if exp is not None and exp <= now:
            continue
        raw_label = grant.get("label")
        label = raw_label.strip() if isinstance(raw_label, str) and raw_label.strip() else None
        if not label:
            gtype = grant.get("type")
            label = type_labels.get(gtype, "Limit reset") if isinstance(gtype, str) else "Limit reset"
        usable = grant.get("usable_now") if isinstance(grant.get("usable_now"), bool) else None
        items.append(ResetCredit(count=int(left), expires_at=exp, label=label, usable_now=usable))
    items.sort(key=lambda item: (item.expires_at is None, item.expires_at or 0))
    return items


def _claude_fetch_usage(access_token: str) -> tuple[int, Any]:
    return http(
        "GET",
        "https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1",
        {
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": CLAUDE_USER_AGENT,
        },
    )


def _claude_subscription(access_token: str) -> Optional[Subscription]:
    status, data = http(
        "GET",
        "https://api.anthropic.com/api/oauth/profile",
        {
            "Authorization": f"Bearer {access_token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": CLAUDE_USER_AGENT,
        },
    )
    if status != 200 or not isinstance(data, dict):
        return None
    org = data.get("organization") if isinstance(data.get("organization"), dict) else {}
    sub_status = org.get("subscription_status")
    sub_status = sub_status.strip().lower() if isinstance(sub_status, str) and sub_status.strip() else None
    ends_at = epoch(org.get("claude_code_trial_ends_at") or org.get("subscription_ends_at") or org.get("current_period_end"))
    renews = False if sub_status == "canceled" else (True if sub_status == "active" else None)
    if ends_at is None:
        return None
    return Subscription(ends_at=ends_at, renews=renews, status=sub_status, period="monthly")


def _claude_populate_provider(
    provider: Provider,
    oauth: dict[str, Any],
    data: dict[str, Any],
    access: str,
    *,
    include_extras: bool = True,
) -> Provider:
    scopes = oauth.get("scopes")
    if isinstance(scopes, list) and "user:profile" not in scopes:
        provider.hint = "Token lacks the user:profile scope; re-login with `claude`."
    plan = oauth.get("subscriptionType")
    if isinstance(plan, str):
        provider.plan = plan.capitalize()

    for key, label, short, period in (
        ("five_hour", "5-hour", "5h", 18000),
        ("seven_day", "Weekly", "1w", 604800),
        ("seven_day_opus", "Opus weekly", "Opus", 604800),
        ("seven_day_sonnet", "Sonnet weekly", "Sonnet", 604800),
    ):
        item = data.get(key)
        if not isinstance(item, dict):
            continue
        used = number(item.get("utilization"))
        if used is None:
            continue
        provider.windows.append(
            Window(key, label, short, clamp(used), epoch(item.get("resets_at")), period)
        )

    limits = data.get("limits")
    if isinstance(limits, list):
        for item in limits:
            if not isinstance(item, dict) or item.get("kind") != "weekly_scoped":
                continue
            percent = number(item.get("percent"))
            scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
            model = scope.get("model") if isinstance(scope.get("model"), dict) else {}
            name = model.get("display_name")
            if percent is None or not isinstance(name, str):
                continue
            provider.windows.append(
                Window(f"scoped:{name}", f"{name} weekly", name, clamp(percent), epoch(item.get("resets_at")), 604800, False)
            )

    if include_extras:
        provider.resets = _claude_reset_credits(data)
        provider.subscription = _claude_subscription(access)

    if not provider.windows:
        provider.status = "error"
        provider.error = "no quota windows in response"
    return provider


def _claude_fetch_snapshot(oauth: dict[str, Any], account_id: str) -> Provider:
    """Fetch quota preview for an inactive Claude vault snapshot."""
    provider = Provider("claude", "Claude")
    if not isinstance(oauth, dict) or not (oauth.get("accessToken") or oauth.get("refreshToken")):
        provider.status = "not_configured"
        return provider

    access = oauth.get("accessToken") if isinstance(oauth.get("accessToken"), str) else ""
    expires_ms = number(oauth.get("expiresAt"))
    needs_refresh = not access or (expires_ms is not None and expires_ms / 1000 <= time.time() + 60)

    def _refresh_inactive() -> Optional[dict[str, Any]]:
        refresh_token = oauth.get("refreshToken")
        if not isinstance(refresh_token, str) or not refresh_token:
            return None
        client_id = oauth.get("clientId")
        if not isinstance(client_id, str) or not client_id:
            client_id = CLAUDE_CLIENT_ID
        body: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        scopes = oauth.get("scopes")
        if isinstance(scopes, list) and scopes:
            scope_str = " ".join(s for s in scopes if isinstance(s, str) and s)
            if scope_str:
                body["scope"] = scope_str
        st, resp = post_json(CLAUDE_TOKEN_URL, body, {"User-Agent": CLAUDE_USER_AGENT})
        if st != 200 or not isinstance(resp, dict) or not isinstance(resp.get("access_token"), str):
            return None
        now_s = time.time()
        updated: dict[str, Any] = {
            "accessToken": resp["access_token"],
            "refreshToken": resp.get("refresh_token") if isinstance(resp.get("refresh_token"), str) and resp.get("refresh_token") else refresh_token,
        }
        exp_in = number(resp.get("expires_in"))
        if exp_in is not None and exp_in > 0:
            updated["expiresAt"] = int((now_s + exp_in) * 1000)
        rexp_in = number(resp.get("refresh_token_expires_in"))
        if rexp_in is not None and rexp_in > 0:
            updated["refreshTokenExpiresAt"] = int((now_s + rexp_in) * 1000)
        granted = resp.get("scope")
        if isinstance(granted, str) and granted.strip():
            updated["scopes"] = granted.split()
        mod = _load_accounts_module()
        if mod is not None:
            mod.update_vault_claude_oauth(account_id, updated)
        merged = dict(oauth)
        merged.update(updated)
        return merged

    if needs_refresh:
        refreshed = _refresh_inactive()
        if refreshed and isinstance(refreshed.get("accessToken"), str):
            oauth = refreshed
            access = refreshed["accessToken"]
        else:
            provider.status = "expired"
            return provider

    status, data = _claude_fetch_usage(access)
    if status in (401, 403):
        refreshed = _refresh_inactive()
        if refreshed and isinstance(refreshed.get("accessToken"), str):
            oauth = refreshed
            access = refreshed["accessToken"]
            status, data = _claude_fetch_usage(access)

    if status in (401, 403):
        provider.status = "expired"
        return provider
    if status == 429:
        provider.status = "error"
        provider.error = "rate limited (429)"
        return provider
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider
    return _claude_populate_provider(provider, oauth, data, access, include_extras=False)


def fetch_claude() -> Provider:
    provider = Provider("claude", "Claude")
    config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (HOME / ".claude"))
    path = config_dir / ".credentials.json"
    oauth = _claude_read_oauth(path)
    if not oauth or not (oauth.get("accessToken") or oauth.get("refreshToken")):
        provider.status = "not_configured"
        provider.hint = "Sign in with `claude` first."
        return provider

    access = oauth.get("accessToken") if isinstance(oauth.get("accessToken"), str) else ""
    expires_ms = number(oauth.get("expiresAt"))
    needs_refresh = not access or (expires_ms is not None and expires_ms / 1000 <= time.time() + 60)
    if needs_refresh:
        outcome, refreshed = _claude_refresh_oauth(config_dir, path, access)
        if outcome == "ok" and refreshed and isinstance(refreshed.get("accessToken"), str):
            oauth = refreshed
            access = refreshed["accessToken"]
        elif outcome == "transient":
            provider.status = "error"
            provider.error = "token refresh unavailable"
            return provider
        else:
            provider.status = "expired"
            provider.hint = "Claude session expired — sign in with `claude`."
            return provider

    status, data = _claude_fetch_usage(access)
    if status in (401, 403):
        # Another Claude process may have rotated the token; re-read first, then refresh.
        latest = _claude_read_oauth(path)
        latest_access = latest.get("accessToken") if isinstance(latest, dict) and isinstance(latest.get("accessToken"), str) else ""
        if latest and latest_access and latest_access != access:
            oauth = latest
            access = latest_access
            status, data = _claude_fetch_usage(access)
        if status in (401, 403):
            outcome, refreshed = _claude_refresh_oauth(config_dir, path, access)
            if outcome == "ok" and refreshed and isinstance(refreshed.get("accessToken"), str):
                oauth = refreshed
                access = refreshed["accessToken"]
                status, data = _claude_fetch_usage(access)
            elif outcome == "transient":
                provider.status = "error"
                provider.error = "token refresh unavailable"
                return provider

    if status in (401, 403):
        provider.status = "expired"
        provider.hint = "Anthropic rejected this token — open Claude Code to renew it."
        return provider
    if status == 429:
        provider.status = "error"
        provider.error = "rate limited (429)"
        return provider
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider

    return _claude_populate_provider(provider, oauth, data, access, include_extras=True)


# ---------------------------------------------------------------------------
# Antigravity (Google / Cloud Code)
# ---------------------------------------------------------------------------


def _antigravity_clients() -> list[tuple[str, str]]:
    """Discover the Antigravity OAuth client from env, IDE bundle, or `agy` CLI.

    No client secret is bundled in Oasis; it is read from the locally installed
    Antigravity IDE / CLI (or ANTIGRAVITY_CLIENT_ID / ANTIGRAVITY_CLIENT_SECRET).
    """
    import re

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(cid: str, sec: str) -> None:
        key = (cid.strip(), sec.strip())
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            pairs.append(key)

    env_id = os.environ.get("ANTIGRAVITY_CLIENT_ID", "")
    env_sec = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "")
    if env_id and env_sec:
        _add(env_id, env_sec)

    ide_candidates = [
        Path("/opt/antigravity-ide/resources/app/out/main.js"),
        Path("/usr/share/antigravity-ide/resources/app/out/main.js"),
        Path("/usr/lib/antigravity-ide/resources/app/out/main.js"),
        Path("/usr/lib64/antigravity-ide/resources/app/out/main.js"),
        HOME / ".local/share/antigravity-ide/resources/app/out/main.js",
    ]
    marker = "platform/cloudCode/common/oauthClient.js"
    ide_pattern = re.compile(
        r"""["'](\d+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com)["']\s*,\s*"""
        r"""[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*["']([A-Za-z0-9._~-]{16,})["']"""
    )
    for path in ide_candidates:
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        at = source.find(marker)
        if at < 0:
            continue
        match = ide_pattern.search(source, at, at + 16_384)
        if match:
            _add(match.group(1), match.group(2))

    # Fall back to the installed Antigravity CLI (`agy`) binary if present.
    cli_candidates: list[Path] = []
    which_agy = shutil.which("agy")
    if which_agy:
        cli_candidates.append(Path(which_agy))
    for cand in (HOME / ".local/bin/agy", Path("/usr/local/bin/agy"), Path("/usr/bin/agy")):
        if cand not in cli_candidates:
            cli_candidates.append(cand)

    cid_re = re.compile(rb"\d+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com")
    sec_re = re.compile(rb"GOCSPX-[A-Za-z0-9._~-]{28}")
    for path in cli_candidates:
        try:
            real = path.resolve()
            if not real.is_file() or real.stat().st_size > 400_000_000:
                continue
            raw = real.read_bytes()
        except OSError:
            continue
        cids = [m.group(0).decode("ascii", "ignore") for m in cid_re.finditer(raw)]
        secs = [m.group(0).decode("ascii", "ignore") for m in sec_re.finditer(raw)]
        # Pair matching indices first (prod/staging/dev order in Go string tables).
        for idx in range(min(len(cids), len(secs))):
            _add(cids[idx], secs[idx])
        for cid in cids[:4]:
            for sec in secs[:4]:
                _add(cid, sec)
        if pairs:
            break

    return pairs


def _antigravity_parse_raw(raw: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[int]]:
    if not raw:
        return None, None, None
    text = raw.strip()
    if text.startswith("go-keyring-base64:"):
        try:
            text = base64.b64decode(text[len("go-keyring-base64:") :]).decode()
        except (ValueError, UnicodeDecodeError):
            return None, None, None
    if text.startswith("Bearer "):
        return text[7:].strip(), None, None
    doc: Any
    try:
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return text or None, None, None
    if isinstance(doc, str):
        return doc or None, None, None
    if not isinstance(doc, dict):
        return None, None, None
    token = doc.get("token") if isinstance(doc.get("token"), dict) else doc
    access = token.get("access_token") or token.get("accessToken") or token.get("id_token")
    refresh = token.get("refresh_token") or token.get("refreshToken")
    expiry = epoch(token.get("expiry") or token.get("expires_at") or token.get("expiresAt"))
    return (access or None), (refresh or None), expiry


def _antigravity_credentials() -> tuple[Optional[str], Optional[str], Optional[int]]:
    return _antigravity_parse_raw(secret_lookup("gemini", "antigravity"))


def _antigravity_cached_token(cache_path: Path = ANTIGRAVITY_CACHE) -> Optional[str]:
    """Use the cached access token when possible; saves a secret-tool spawn."""
    doc = load_json(cache_path)
    if not isinstance(doc, dict):
        return None
    access = doc.get("access_token")
    expires = epoch(doc.get("expires_at"))
    if not isinstance(access, str) or not access:
        return None
    if not expires or expires <= time.time() + 60:
        return None
    return access


def _antigravity_store(access: str, expires_in: float, refresh: str, cache_path: Path = ANTIGRAVITY_CACHE) -> None:
    try:
        CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = {
            "access_token": access,
            "expires_at": datetime.fromtimestamp(
                time.time() + expires_in, tz=timezone.utc
            ).isoformat(),
            "refresh_fingerprint": hashlib.sha256(refresh.encode()).hexdigest(),
        }
        tmp = cache_path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, cache_path)
        os.chmod(cache_path, 0o600)
    except OSError:
        pass


def _antigravity_refresh(refresh: str, cache_path: Path = ANTIGRAVITY_CACHE) -> Optional[str]:
    for client_id, client_secret in _antigravity_clients():
        status, data = post_form(
            "https://oauth2.googleapis.com/token",
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
        if status == 200 and isinstance(data, dict):
            access = data.get("access_token")
            if isinstance(access, str) and access:
                _antigravity_store(access, number(data.get("expires_in")) or 3600, refresh, cache_path=cache_path)
                return access
        if status == 0:
            break
    return None


def _antigravity_reauth(raw_override: Optional[str] = None, cache_path: Path = ANTIGRAVITY_CACHE) -> Optional[str]:
    access, refresh, expiry = _antigravity_parse_raw(raw_override) if raw_override is not None else _antigravity_credentials()
    if refresh:
        return _antigravity_refresh(refresh, cache_path=cache_path)
    if access and (expiry is None or expiry > time.time() + 60):
        return access
    return None


def _antigravity_quota(access: str) -> tuple[int, Any]:
    """Fetch the quota summary, preferring the daily host over production."""
    headers = {
        "Authorization": f"Bearer {access}",
        "User-Agent": AG_USER_AGENT,
    }
    status, data = 0, None
    for url in AG_SUMMARY_URLS:
        status, data = post_json(url, {}, headers)
        if status == 200 and isinstance(data, dict):
            return status, data
        if status in (401, 403):
            break
    return status, data


def _antigravity_buckets(data: Any) -> dict[str, dict[str, Any]]:
    """Flatten the quota summary into buckets keyed by bucket id."""
    container = data.get("response") if isinstance(data.get("response"), dict) else data
    groups = container.get("groups") if isinstance(container, dict) else None
    buckets: dict[str, dict[str, Any]] = {}
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        group_name = group.get("displayName")
        group_name = group_name.strip() if isinstance(group_name, str) else ""
        raw_buckets = group.get("buckets")
        for bucket in raw_buckets if isinstance(raw_buckets, list) else []:
            if not isinstance(bucket, dict):
                continue
            bucket_id = bucket.get("bucketId")
            remaining = number(bucket.get("remainingFraction"))
            if not isinstance(bucket_id, str) or not bucket_id or remaining is None:
                continue
            window = bucket.get("window")
            buckets.setdefault(bucket_id, {
                "id": bucket_id,
                "group": group_name,
                "window": window.strip().lower() if isinstance(window, str) else "",
                "used": clamp((1 - remaining) * 100),
                "reset": epoch(bucket.get("resetTime")),
            })
    return buckets


def _antigravity_windows(data: Any) -> dict[str, tuple[float, Optional[int]]]:
    """Map a quota summary onto the shared Gemini and Claude quota windows."""
    buckets = _antigravity_buckets(data)
    windows: dict[str, tuple[float, Optional[int]]] = {}
    for key in AG_CANONICAL_BUCKETS:
        entry = buckets.get(key)
        if entry is not None:
            windows[key] = (entry["used"], entry["reset"])

    if not any(key in buckets for key in AG_CANONICAL_BUCKETS):
        # Bucket ids were renamed. Infer the shared slots from group names.
        for entry in buckets.values():
            label = f"{entry['id']} {entry['group']}".lower()
            if "gemini" in label:
                prefix = "gemini"
            elif "claude" in label or "gpt" in label:
                prefix = "3p"
            else:
                continue
            if entry["window"] == "weekly":
                key = f"{prefix}-weekly"
            elif entry["window"] == "5h":
                key = f"{prefix}-5h"
            else:
                continue
            windows.setdefault(key, (entry["used"], entry["reset"]))
    return windows


def _antigravity_models(access: str) -> tuple[int, list[dict[str, Any]]]:
    """Per-model quota. Gemini quotas live here, not in the summary endpoint."""
    status, data = post_json(
        "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
        {},
        {"Authorization": f"Bearer {access}", "User-Agent": AG_USER_AGENT},
    )
    if status != 200 or not isinstance(data, dict):
        return status, []
    models = data.get("models")
    if not isinstance(models, dict):
        return status, []
    seen: dict[str, dict[str, Any]] = {}
    for raw in models.values():
        if not isinstance(raw, dict) or raw.get("isInternal") is True:
            continue
        quota = raw.get("quotaInfo")
        remaining = number(quota.get("remainingFraction")) if isinstance(quota, dict) else None
        name = raw.get("displayName")
        if remaining is None or not isinstance(name, str) or not name.strip():
            continue
        used = clamp((1 - remaining) * 100)
        key = name.strip().lower()
        entry = {
            "name": name.strip(),
            "used": used,
            "reset": epoch(quota.get("resetTime")),
        }
        if key not in seen or used > seen[key]["used"]:
            seen[key] = entry
    result = []
    for entry in seen.values():
        name = entry["name"].lower()
        if "gemini" in name:
            entry["family"] = "gemini"
        elif "claude" in name:
            entry["family"] = "claude"
        elif "gpt" in name:
            entry["family"] = "gpt"
        else:
            entry["family"] = "other"
        result.append(entry)
    return status, result


def _fetch_antigravity_impl(raw_override: Optional[str] = None, cache_path: Path = ANTIGRAVITY_CACHE) -> Provider:
    provider = Provider("antigravity", "Antigravity")

    access = _antigravity_cached_token(cache_path)
    if not access:
        access, refresh, expiry = _antigravity_parse_raw(raw_override) if raw_override is not None else _antigravity_credentials()
        if not access and not refresh:
            provider.status = "not_configured"
            provider.hint = "Sign in through the Antigravity IDE."
            return provider
        if refresh and (not access or (expiry is not None and expiry <= time.time() + 60)):
            access = _antigravity_refresh(refresh, cache_path=cache_path)

    if not access:
        provider.status = "expired"
        provider.hint = "Antigravity session expired — sign in again."
        return provider

    status, data = _antigravity_quota(access)
    if status in (401, 403):
        access = _antigravity_reauth(raw_override=raw_override, cache_path=cache_path)
        if not access:
            provider.status = "expired"
            provider.hint = "Antigravity session expired — sign in again."
            return provider
        status, data = _antigravity_quota(access)
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider

    summary = _antigravity_windows(data)
    gemini_5h = summary.get("gemini-5h")
    gemini_weekly = summary.get("gemini-weekly")
    claude_5h = summary.get("3p-5h")
    claude_weekly = summary.get("3p-weekly")

    # The summary is authoritative and gives both periods. The per-model list
    # only carries 5-hour data, so use it solely to fill a missing 5-hour pool.
    models: list[dict[str, Any]] = []
    if gemini_5h is None or claude_5h is None:
        model_status, models = _antigravity_models(access)
        if model_status in (401, 403):
            renewed = _antigravity_reauth(raw_override=raw_override, cache_path=cache_path)
            if renewed:
                access = renewed
                model_status, models = _antigravity_models(access)

    if gemini_5h is None:
        gemini_models = [m for m in models if m.get("family") == "gemini"]
        if gemini_models:
            worst = max(gemini_models, key=lambda m: m["used"])
            gemini_5h = (worst["used"], worst["reset"])
    if claude_5h is None:
        claude_models = [m for m in models if m.get("family") in ("claude", "gpt")]
        if claude_models:
            worst = max(claude_models, key=lambda m: m["used"])
            claude_5h = (worst["used"], worst["reset"])

    if gemini_5h is not None:
        provider.windows.append(
            Window("gemini-5h", "Gemini 5h", "Gemini", gemini_5h[0], gemini_5h[1], 18000, True)
        )
    if gemini_weekly is not None:
        provider.windows.append(
            Window("gemini-weekly", "Gemini weekly", "Gemini W",
                   gemini_weekly[0], gemini_weekly[1], 604800, True)
        )
    if claude_5h is not None:
        provider.windows.append(
            Window("claude-5h", "Claude 5h", "Claude", claude_5h[0], claude_5h[1], 18000, True)
        )
    if claude_weekly is not None:
        provider.windows.append(
            Window("claude-weekly", "Claude weekly", "Claude W",
                   claude_weekly[0], claude_weekly[1], 604800, True)
        )

    if not provider.windows:
        provider.status = "error"
        provider.error = "no quota windows in response"
    return provider


def fetch_antigravity() -> Provider:
    return _fetch_antigravity_impl()


# ---------------------------------------------------------------------------
# OpenCode Go
# ---------------------------------------------------------------------------


def _opencode_key() -> Optional[str]:
    """OpenCode v2 keeps credentials in SQLite; auth.json is the legacy path."""
    data_home = Path(os.environ.get("XDG_DATA_HOME") or (HOME / ".local/share"))
    db_env = os.environ.get("OPENCODE_DB")
    db = Path(db_env) if db_env and os.path.isabs(db_env) else data_home / "opencode" / "opencode.db"
    if db.exists():
        try:
            con = sqlite3.connect(
                "file:" + urllib.parse.quote(str(db)) + "?mode=ro",
                uri=True,
                timeout=2,
            )
            try:
                con.execute("PRAGMA query_only=ON")
                rows = con.execute(
                    "select value from credential where integration_id = ? "
                    "order by active desc, time_updated desc",
                    ("opencode-go",),
                ).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            rows = []
        for (value,) in rows:
            if isinstance(value, bytes):
                value = value.decode("utf-8", "ignore")
            if not isinstance(value, str):
                continue
            try:
                doc = json.loads(value)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(doc, dict):
                continue
            candidate = doc.get("key") if doc.get("type") == "key" else doc.get("access")
            if isinstance(candidate, str) and candidate:
                return candidate

    auth = load_json(data_home / "opencode" / "auth.json")
    entry = None
    if isinstance(auth, dict):
        entry = auth.get("opencode-go") or auth.get("opencode")
    if isinstance(entry, dict):
        candidate = entry.get("key") if entry.get("type") != "oauth" else entry.get("access")
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _opencodego_fetch_with_key(key: Optional[str]) -> Provider:
    provider = Provider("opencodego", "OpenCode Go")
    if not key:
        provider.status = "not_configured"
        provider.hint = "Run `opencode auth login -p opencode-go`."
        return provider

    status, data = http(
        "GET",
        "https://opencode.ai/zen/go/v1/usage",
        {"Authorization": f"Bearer {key}", "User-Agent": "opencode/1.18.4"},
    )

    if status in (401, 403):
        code = ""
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                code = str(err.get("code") or "")
        if code == "api_key_expired":
            provider.status = "expired"
            provider.hint = "API key expired — create a new one and run `opencode auth login -p opencode-go`."
        else:
            provider.status = "expired"
            provider.hint = "OpenCode Go rejected this key — sign in again."
        return provider
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    monthly_reset: Optional[int] = None
    for key_name, label, short, period in (
        ("rolling", "5-hour", "5h", 18000),
        ("weekly", "Weekly", "1w", 604800),
        ("monthly", "Monthly", "1mo", 2628000),
    ):
        item = usage.get(key_name)
        if not isinstance(item, dict):
            continue
        if item.get("status") not in (None, "ok", "rate-limited"):
            continue
        percent = number(item.get("percent"))
        if percent is None:
            continue
        resets_at = epoch(item.get("resetsAt"))
        if key_name == "monthly" and resets_at is not None:
            monthly_reset = resets_at
        provider.windows.append(
            Window(key_name, label, short, clamp(percent), resets_at, period, True)
        )
    if monthly_reset is not None:
        provider.subscription = Subscription(ends_at=monthly_reset, renews=True, status="active", period="monthly")

    if not provider.windows:
        # CodexBar's documented legacy shape.
        for key_name, label, short, period in (
            ("rollingUsage", "5-hour", "5h", 18000),
            ("weeklyUsage", "Weekly", "1w", 604800),
            ("monthlyUsage", "Monthly", "1mo", 2628000),
        ):
            item = data.get(key_name)
            if not isinstance(item, dict):
                continue
            percent = number(item.get("usagePercent"))
            if percent is None:
                continue
            after = number(item.get("resetInSec"))
            reset = int(time.time() + after) if after is not None else None
            provider.windows.append(Window(key_name, label, short, clamp(percent), reset, period))

    if not provider.windows:
        provider.status = "error"
        provider.error = "no quota windows in response"
    return provider


def fetch_opencodego() -> Provider:
    return _opencodego_fetch_with_key(_opencode_key())


# ---------------------------------------------------------------------------
# Grok (xAI CLI)
# ---------------------------------------------------------------------------


def _grok_fetch_with_auth(auth: Any) -> Provider:
    provider = Provider("grok", "Grok")
    if not isinstance(auth, dict):
        provider.status = "not_configured"
        provider.hint = "Sign in with `grok login`."
        return provider

    token = ""
    expiry: Optional[int] = None
    for key, entry in auth.items():
        if not isinstance(entry, dict):
            continue
        candidate = entry.get("key")
        if not isinstance(candidate, str) or not candidate:
            continue
        expires = jwt_claims(candidate).get("exp")
        if expires is None:
            expires = epoch(entry.get("expires_at") or entry.get("expires"))
        token = candidate
        expiry = int(expires) if isinstance(expires, (int, float)) else None
        if key:
            break
    if not token:
        provider.status = "not_configured"
        provider.hint = "Sign in with `grok login`."
        return provider
    if expiry is not None and expiry <= time.time() + 30:
        provider.status = "expired"
        provider.hint = "Grok token expired — run `grok login`."
        return provider

    status, data = http(
        "GET",
        "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
        {
            "Authorization": f"Bearer {token}",
            "X-XAI-Token-Auth": "xai-grok-cli",
        },
    )
    if status in (401, 403):
        provider.status = "expired"
        provider.hint = "Grok rejected this token — run `grok login`."
        return provider
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider

    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    period = config.get("currentPeriod") if isinstance(config.get("currentPeriod"), dict) else {}
    reset = epoch(period.get("end"))
    used = number(config.get("creditUsagePercent"))
    if used is not None:
        provider.windows.append(
            Window("weekly", "Weekly", "1w", clamp(used), reset, 604800)
        )
    if not provider.windows:
        provider.status = "error"
        provider.error = "no quota windows in response"
    return provider


def fetch_grok() -> Provider:
    return _grok_fetch_with_auth(load_json(HOME / ".grok" / "auth.json"))


# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------


def _cursor_token() -> Optional[str]:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
    candidates = [
        config_home / "Cursor" / "User" / "globalStorage" / "state.vscdb",
        HOME / ".cursor" / "User" / "globalStorage" / "state.vscdb",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                con.execute("PRAGMA query_only=ON")
                row = con.execute(
                    "SELECT value FROM ItemTable WHERE key = ?",
                    ("cursorAuth/accessToken",),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if row and row[0]:
            value = row[0]
            if isinstance(value, bytes):
                value = value.decode("utf-8", "ignore")
            if isinstance(value, str) and value:
                return value
    return secret_lookup("cursor-access-token")


def fetch_cursor() -> Provider:
    provider = Provider("cursor", "Cursor")
    token = _cursor_token()
    if not token:
        provider.status = "not_configured"
        provider.hint = "Sign in through the Cursor app."
        return provider

    expires = jwt_claims(token).get("exp")
    if expires and int(expires) <= time.time() + 30:
        provider.status = "expired"
        provider.hint = "Cursor token expired — restart Cursor to renew it."
        return provider

    status, data = post_json(
        "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage",
        {},
        {
            "Authorization": f"Bearer {token}",
            "Connect-Protocol-Version": "1",
        },
    )
    if status in (401, 403):
        provider.status = "expired"
        provider.hint = "Cursor rejected this token — restart Cursor to renew it."
        return provider
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider
    if data.get("enabled") is False:
        provider.status = "not_configured"
        provider.hint = "No active Cursor subscription."
        return provider

    plan_usage = data.get("planUsage") if isinstance(data.get("planUsage"), dict) else None
    if plan_usage is None:
        provider.status = "error"
        provider.error = "no quota windows in response"
        return provider

    reset = epoch(data.get("billingCycleEnd"))
    if reset is not None:
        provider.subscription = Subscription(ends_at=reset, renews=True, status="active", period="monthly")
    total = number(plan_usage.get("totalPercentUsed"))
    if total is None:
        limit = number(plan_usage.get("limit"))
        spent = number(plan_usage.get("totalSpend"))
        if limit and limit > 0 and spent is not None:
            total = spent / limit * 100
    if total is not None:
        provider.windows.append(Window("cycle", "Billing cycle", "cycle", clamp(total), reset, None, True))

    for key, label, short in (
        ("autoPercentUsed", "Auto", "auto"),
        ("apiPercentUsed", "API", "api"),
    ):
        used = number(plan_usage.get(key))
        if used is not None:
            provider.windows.append(Window(key, label, short, clamp(used), reset, None, False))

    if not provider.windows:
        provider.status = "error"
        provider.error = "no quota windows in response"
    return provider


# ---------------------------------------------------------------------------
# Muse Code (Meta)
# ---------------------------------------------------------------------------


def _muse_token() -> Optional[str]:
    path = Path(os.environ.get("MUSE_AUTH_PATH") or (HOME / ".config/muse/auth.json"))
    doc = load_json(path)
    if isinstance(doc, dict):
        providers = doc.get("providers") if isinstance(doc.get("providers"), dict) else {}
        meta = providers.get("meta") if isinstance(providers.get("meta"), dict) else {}
        token = meta.get("access_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    # Only touch Secret Service when the Muse CLI is actually installed;
    # otherwise this would spawn secret-tool every refresh for nothing.
    if not shutil.which("muse"):
        return None
    raw = secret_lookup("ai.meta.dev.credentials", "meta")
    if not raw:
        raw = secret_lookup("ai.meta.dev.credentials")
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict) and isinstance(payload.get("access_token"), str):
                return payload["access_token"].strip()
        except (ValueError, UnicodeDecodeError):
            pass
        return raw.strip()
    return None


def _muse_fetch_with_token(token: Optional[str]) -> Provider:
    provider = Provider("muse", "Muse Code")
    if not token:
        provider.status = "not_configured"
        provider.hint = "Sign in with `muse login`."
        return provider
    if not token.startswith("dca:"):
        provider.status = "expired"
        provider.hint = "Muse Code needs a device-code login — run `muse login`."
        return provider

    status, data = post_json(
        "https://api.meta.ai/muse-code/key",
        {},
        {
            "Authorization": f"Bearer {token}",
            "x-api-version": "1.0.0",
            "User-Agent": UA,
        },
    )
    if status in (401, 403):
        provider.status = "expired"
        provider.hint = "Muse Code login was rejected — run `muse login`."
        return provider
    if status == 429:
        provider.status = "error"
        provider.error = "rate limited (429)"
        return provider
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider

    if data.get("require_payment") is True:
        provider.status = "not_configured"
        provider.hint = "Muse Code requires a payment method at dev.meta.ai."
        return provider
    if data.get("is_subs_active") is not True:
        provider.status = "not_configured"
        provider.hint = "No active Muse Code subscription."
        return provider

    plan = data.get("subs_tier_name")
    if isinstance(plan, str) and plan.strip():
        provider.plan = plan.strip()

    usage = data.get("subs_usage")
    if not isinstance(usage, dict):
        provider.hint = "Quota omitted while the 5-hour window is idle."
        return provider

    window = usage.get("window") if isinstance(usage.get("window"), dict) else {}
    weekly = usage.get("weekly") if isinstance(usage.get("weekly"), dict) else {}
    minutes = number(window.get("window_duration_mins")) or 300
    used = number(window.get("used_percent"))
    if used is not None:
        provider.windows.append(
            Window("5h", "5-hour", "5h", clamp(used), epoch(window.get("resets_at")), int(minutes * 60))
        )
    weekly_used = number(weekly.get("used_percent"))
    if weekly_used is not None:
        provider.windows.append(
            Window("weekly", "Weekly", "1w", clamp(weekly_used), epoch(weekly.get("resets_at")), 604800)
        )
    if not provider.windows:
        provider.hint = "Quota omitted while the 5-hour window is idle."
    return provider


def fetch_muse() -> Provider:
    return _muse_fetch_with_token(_muse_token())


# ---------------------------------------------------------------------------
# Kimi Code (Moonshot)
# ---------------------------------------------------------------------------

KIMI_ENDPOINTS = {
    "china": "https://api.kimi.com/coding/v1/usages",
    "international": "https://api.kimi.ai/coding/v1/usages",
}
KIMI_PLANS = {
    "LEVEL_FREE": "Adagio",
    "LEVEL_TRIAL": "Andante",
    "LEVEL_BASIC": "Moderato",
    "LEVEL_INTERMEDIATE": "Allegretto",
    "LEVEL_ADVANCED": "Allegro",
}


def _kimi_settings() -> tuple[Optional[str], str, str]:
    """Return (token, kind, region); kind is 'api' or 'cli'."""
    cfg = load_json(HOME / ".codexbar" / "config.json")
    entry: dict[str, Any] = {}
    if isinstance(cfg, dict):
        for item in cfg.get("providers") or []:
            if isinstance(item, dict) and item.get("id") == "kimi":
                entry = item
                break
    region = str(entry.get("region") or "china").lower()
    if region not in KIMI_ENDPOINTS:
        region = "china"

    api_key = entry.get("apiKey")
    if not isinstance(api_key, str) or not api_key.strip():
        api_key = os.environ.get("KIMI_CODE_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip(), "api", region

    if region == "china":
        home = Path(os.environ.get("KIMI_CODE_HOME") or (HOME / ".kimi-code"))
        credential = load_json(home / "credentials" / "kimi-code.json")
        if isinstance(credential, dict):
            access = credential.get("access_token")
            expires = number(credential.get("expires_at"))
            if (isinstance(access, str) and access.strip()
                    and (expires is None or expires > time.time() + 60)):
                return access.strip(), "cli", region
    return None, "", region


def _kimi_headers(token: str, kind: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": UA,
    }
    if kind == "cli":
        home = Path(os.environ.get("KIMI_CODE_HOME") or (HOME / ".kimi-code"))
        device = "unknown"
        try:
            device = (home / "device_id").read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            pass
        headers.update({
            "X-Msh-Platform": "kimi_code_cli",
            "X-Msh-Version": VERSION,
            "X-Msh-Device-Name": platform.node() or "linux",
            "X-Msh-Device-Model": f"Linux {platform.release()} {platform.machine()}",
            "X-Msh-Os-Version": platform.release(),
            "X-Msh-Device-Id": device,
        })
    return headers


def _kimi_fetch_with_settings(token: Optional[str], kind: str, region: str) -> Provider:
    provider = Provider("kimi", "Kimi Code")
    if not token:
        provider.status = "not_configured"
        provider.hint = "Set KIMI_CODE_API_KEY or run `kimi` to sign in."
        return provider

    status, data = http("GET", KIMI_ENDPOINTS.get(region, KIMI_ENDPOINTS["china"]), _kimi_headers(token, kind))
    if status in (401, 403):
        provider.status = "expired"
        provider.hint = "Kimi rejected this credential — run `kimi` or update KIMI_CODE_API_KEY."
        return provider
    if status == 429:
        provider.status = "error"
        provider.error = "rate limited (429)"
        return provider
    if status == 404:
        provider.status = "error"
        provider.error = "endpoint not found — check the Kimi region"
        return provider
    if status != 200 or not isinstance(data, dict):
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider

    usages = data.get("usages") if isinstance(data.get("usages"), dict) else {}
    pools = (
        ("limit_5h", "5-hour", "5h", 18000),
        ("limit_7d", "Weekly", "1w", 604800),
        ("limit_month_total", "Monthly", "1mo", 2628000),
    )
    for key, label, short, period in pools:
        pool = usages.get(key)
        if not isinstance(pool, dict):
            continue
        ratio = number(pool.get("used_ratio"))
        if ratio is None:
            continue
        provider.windows.append(
            Window(key, label, short, clamp(min(1.0, max(0.0, ratio)) * 100),
                   epoch(pool.get("reset_time")), period)
        )

    # Legacy count-based response.
    limits = data.get("limits") if isinstance(data.get("limits"), list) else []
    if not any(w.period == 18000 for w in provider.windows) and limits:
        first = limits[0] if isinstance(limits[0], dict) else {}
        window = first.get("window") if isinstance(first.get("window"), dict) else {}
        detail = first.get("detail") if isinstance(first.get("detail"), dict) else {}
        duration = number(window.get("duration"))
        unit = window.get("time_unit")
        multipliers = {"TIME_UNIT_MINUTE": 60, "TIME_UNIT_HOUR": 3600, "TIME_UNIT_DAY": 86400}
        period = int(duration * multipliers[unit]) if duration and unit in multipliers else 18000
        used = number(detail.get("used"))
        limit = number(detail.get("limit"))
        if used is not None and limit and limit > 0:
            provider.windows.append(
                Window("limits-5h", "5-hour", "5h", clamp(used / limit * 100),
                       epoch(detail.get("reset_time") or detail.get("reset_at")), period)
            )

    if not any(w.period == 604800 for w in provider.windows):
        detail = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        used = number(detail.get("used"))
        limit = number(detail.get("limit"))
        if used is not None and limit and limit > 0:
            provider.windows.append(
                Window("usage-weekly", "Weekly", "1w", clamp(used / limit * 100),
                       epoch(detail.get("reset_time") or detail.get("reset_at")), 604800)
            )

    level = None
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    membership = user.get("membership") if isinstance(user.get("membership"), dict) else {}
    if isinstance(membership.get("level"), str):
        level = membership["level"]
    if level:
        provider.plan = KIMI_PLANS.get(level, level)

    if not provider.windows:
        provider.status = "error"
        provider.error = "no quota windows in response"
    return provider


def fetch_kimi() -> Provider:
    token, kind, region = _kimi_settings()
    return _kimi_fetch_with_settings(token, kind, region)


# ---------------------------------------------------------------------------
# Qwen (Qwen Code OAuth / Alibaba Cloud Coding Plan & DashScope)
# ---------------------------------------------------------------------------

QWEN_CODING_ENDPOINTS = {
    "international": "https://coding-intl.dashscope.aliyuncs.com/v1/models",
    "china": "https://coding.dashscope.aliyuncs.com/v1/models",
}
QWEN_API_ENDPOINTS = {
    "international": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models",
    "china": "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
}


def _qwen_credentials() -> tuple[Optional[str], str, str, Optional[int], Optional[str]]:
    """Return (token, kind, region, expires_epoch, resource_url).

    kind is one of 'oauth', 'coding_plan', 'api', or ''.
    """
    qwen_dir = Path(os.environ.get("QWEN_CONFIG_DIR") or (HOME / ".qwen"))
    oauth = load_json(qwen_dir / "oauth_creds.json")
    if isinstance(oauth, dict):
        access = oauth.get("access_token") or oauth.get("accessToken")
        if isinstance(access, str) and access.strip():
            exp = epoch(oauth.get("expiry_date") or oauth.get("expires_at") or oauth.get("expiresAt"))
            res_url = oauth.get("resource_url")
            res_url = res_url.strip() if isinstance(res_url, str) and res_url.strip() else None
            return access.strip(), "oauth", "international", exp, res_url

    region = (os.environ.get("QWEN_REGION") or "").strip().lower()
    cfg = load_json(HOME / ".codexbar" / "config.json")
    cfg_key = None
    if isinstance(cfg, dict):
        for item in cfg.get("providers") or []:
            if isinstance(item, dict) and item.get("id") in ("qwen", "dashscope", "alibaba"):
                if not region and isinstance(item.get("region"), str):
                    region = item["region"].strip().lower()
                if isinstance(item.get("apiKey"), str) and item["apiKey"].strip():
                    cfg_key = item["apiKey"].strip()
                break
    if region not in ("international", "china"):
        region = "international"

    key = cfg_key
    for env_name in ("QWEN_CODING_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY"):
        if not key:
            val = os.environ.get(env_name)
            if isinstance(val, str) and val.strip():
                key = val.strip()

    if not key:
        settings = load_json(qwen_dir / "settings.json")
        if isinstance(settings, dict):
            for field_name in ("apiKey", "api_key", "dashscopeApiKey"):
                val = settings.get(field_name)
                if isinstance(val, str) and val.strip():
                    key = val.strip()
                    break

    if not key:
        data_home = Path(os.environ.get("XDG_DATA_HOME") or (HOME / ".local/share"))
        db = data_home / "opencode" / "opencode.db"
        if db.exists():
            try:
                con = sqlite3.connect("file:" + urllib.parse.quote(str(db)) + "?mode=ro", uri=True, timeout=2)
                try:
                    con.execute("PRAGMA query_only=ON")
                    rows = con.execute(
                        "select value from credential where integration_id in ('qwen','qwen-coding-plan','dashscope','alibaba') "
                        "order by active desc, time_updated desc"
                    ).fetchall()
                finally:
                    con.close()
            except sqlite3.Error:
                rows = []
            for (value,) in rows:
                if isinstance(value, bytes):
                    value = value.decode("utf-8", "ignore")
                if not isinstance(value, str):
                    continue
                try:
                    doc = json.loads(value)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(doc, dict):
                    cand = doc.get("key") or doc.get("access")
                    if isinstance(cand, str) and cand.strip():
                        key = cand.strip()
                        break

    if key:
        kind = "coding_plan" if key.startswith("sk-sp-") else "api"
        return key, kind, region, None, None
    return None, "", region, None, None


def _qwen_parse_rate_headers(headers: dict[str, str]) -> list[Window]:
    windows: list[Window] = []
    for kind, label, short, period in (
        ("requests", "Requests", "req", 86400),
        ("tokens", "Tokens", "tok", 86400),
    ):
        limit = number(headers.get(f"x-ratelimit-limit-{kind}"))
        remaining = number(headers.get(f"x-ratelimit-remaining-{kind}"))
        if limit and limit > 0 and remaining is not None:
            used = clamp((1.0 - remaining / limit) * 100.0)
            reset = epoch(headers.get(f"x-ratelimit-reset-{kind}"))
            windows.append(Window(f"qwen-{kind}", label, short, used, reset, period))
    return windows


def _qwen_fetch_with_creds(
    token: Optional[str],
    kind: str,
    region: str,
    expires_at: Optional[int],
    resource_url: Optional[str],
) -> Provider:
    provider = Provider("qwen", "Qwen")
    if not token:
        provider.status = "not_configured"
        provider.hint = "Sign in with `qwen` or set QWEN_CODING_API_KEY / DASHSCOPE_API_KEY."
        return provider

    if kind == "oauth" and expires_at is not None and expires_at <= time.time() + 30:
        provider.status = "expired"
        provider.hint = "Qwen OAuth token expired — run `qwen` to renew it."
        return provider

    if kind == "oauth":
        host = "portal.qwen.ai"
        if resource_url:
            parsed = urllib.parse.urlsplit(resource_url if "://" in resource_url else f"https://{resource_url}")
            if parsed.hostname in ALLOWED_HOSTS:
                host = parsed.hostname
        url = f"https://{host}/v1/models"
        provider.plan = "OAuth"
    elif kind == "coding_plan":
        url = QWEN_CODING_ENDPOINTS.get(region, QWEN_CODING_ENDPOINTS["international"])
        provider.plan = "Coding Plan"
    else:
        url = QWEN_API_ENDPOINTS.get(region, QWEN_API_ENDPOINTS["international"])
        provider.plan = "API"

    status, data, resp_headers = http_with_headers(
        "GET",
        url,
        {"Authorization": f"Bearer {token}", "User-Agent": UA},
    )
    if status in (401, 403):
        provider.status = "expired"
        provider.hint = "Qwen rejected this credential — re-login with `qwen` or check your API key."
        return provider
    if status == 429:
        provider.windows.append(Window("qwen-daily", "Daily", "1d", 100.0, None, 86400))
        provider.hint = "Qwen quota limit reached (HTTP 429)."
        return provider
    if status != 200:
        provider.status = "error"
        provider.error = f"HTTP {status}" if status else "unreachable"
        return provider

    provider.windows.extend(_qwen_parse_rate_headers(resp_headers))
    if not provider.windows:
        provider.hint = "Connected (Qwen does not expose quota counters on this endpoint)."
    return provider


def fetch_qwen() -> Provider:
    token, kind, region, expires_at, resource_url = _qwen_credentials()
    return _qwen_fetch_with_creds(token, kind, region, expires_at, resource_url)


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

FETCHERS = {
    "codex": fetch_codex,
    "claude": fetch_claude,
    "antigravity": fetch_antigravity,
    "opencodego": fetch_opencodego,
    "qwen": fetch_qwen,
    "muse": fetch_muse,
    "kimi": fetch_kimi,
    "grok": fetch_grok,
    "cursor": fetch_cursor,
}

ORDER = ["codex", "claude", "antigravity", "opencodego", "qwen", "muse", "kimi", "grok", "cursor"]


def load_cache() -> dict[str, Any]:
    doc = load_json(CACHE_FILE)
    return doc if isinstance(doc, dict) else {}


def save_cache(payload: dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, CACHE_FILE)
        os.chmod(CACHE_FILE, 0o600)
    except OSError:
        pass


_ACCOUNTS_MOD: Any = None
_ACCOUNTS_MOD_LOADED = False


def _load_accounts_module() -> Any:
    global _ACCOUNTS_MOD, _ACCOUNTS_MOD_LOADED
    if _ACCOUNTS_MOD_LOADED:
        return _ACCOUNTS_MOD
    _ACCOUNTS_MOD_LOADED = True
    try:
        code_dir = str(Path(__file__).resolve().parent)
        if code_dir not in sys.path:
            sys.path.insert(0, code_dir)
        import oasis_accounts as mod  # type: ignore[import-not-found]
        _ACCOUNTS_MOD = mod
    except Exception:
        _ACCOUNTS_MOD = None
    return _ACCOUNTS_MOD


def _fetch_account_preview(provider_id: str, account: dict[str, Any]) -> Provider:
    """Fetch live usage windows for an inactive saved account."""
    aid = str(account.get("id") or "")
    if provider_id == "opencodego":
        return _opencodego_fetch_with_key(account.get("_key"))

    snap = account.get("_secret")
    if not isinstance(snap, dict):
        p = Provider(provider_id, provider_id.title())
        p.status = "not_configured"
        return p

    if provider_id == "codex":
        auth = snap.get("auth") if isinstance(snap.get("auth"), dict) else snap
        tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
        token = tokens.get("access_token") or ""
        acct = tokens.get("account_id") or ""
        plan = auth.get("plan_type") if isinstance(auth.get("plan_type"), str) else None
        return _codex_fetch_with_token(token, acct, plan, include_extras=False)

    if provider_id == "claude":
        oauth = snap.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            p = Provider("claude", "Claude")
            p.status = "not_configured"
            return p
        return _claude_fetch_snapshot(oauth, aid)

    if provider_id == "antigravity":
        raw = snap.get("raw")
        safe_id = "".join(ch for ch in aid if ch.isalnum() or ch in ("_", "-")) or "alt"
        return _fetch_antigravity_impl(
            raw_override=raw if isinstance(raw, str) else "",
            cache_path=CACHE_DIR / f"antigravity_{safe_id}.json",
        )

    if provider_id == "qwen":
        kind = snap.get("kind") or "oauth"
        if kind == "api_key":
            key = str(snap.get("api_key") or "").strip()
            region = str(snap.get("region") or "international").lower()
            qkind = "coding_plan" if key.startswith("sk-sp-") else "api"
            return _qwen_fetch_with_creds(key or None, qkind, region, None, None)
        oauth = snap.get("oauth") if isinstance(snap.get("oauth"), dict) else {}
        access = oauth.get("access_token") or oauth.get("accessToken")
        exp = epoch(oauth.get("expiry_date") or oauth.get("expires_at") or oauth.get("expiresAt"))
        res_url = oauth.get("resource_url")
        res_url = res_url.strip() if isinstance(res_url, str) and res_url.strip() else None
        return _qwen_fetch_with_creds(
            access.strip() if isinstance(access, str) and access.strip() else None,
            "oauth",
            "international",
            exp,
            res_url,
        )

    if provider_id == "muse":
        token: Optional[str] = None
        auth = snap.get("auth") if isinstance(snap.get("auth"), dict) else {}
        providers = auth.get("providers") if isinstance(auth.get("providers"), dict) else {}
        meta = providers.get("meta") if isinstance(providers.get("meta"), dict) else {}
        if isinstance(meta.get("access_token"), str) and meta["access_token"].strip():
            token = meta["access_token"].strip()
        elif isinstance(snap.get("keyring_raw"), str) and snap["keyring_raw"].strip():
            raw = snap["keyring_raw"].strip()
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and isinstance(parsed.get("access_token"), str):
                    token = parsed["access_token"].strip()
                else:
                    token = raw
            except ValueError:
                token = raw
        return _muse_fetch_with_token(token)

    if provider_id == "kimi":
        kind = snap.get("kind") or "cli"
        region = str(snap.get("region") or "china").lower()
        if kind == "api_key":
            key = str(snap.get("api_key") or "").strip()
            return _kimi_fetch_with_settings(key or None, "api", region)
        cli = snap.get("cli") if isinstance(snap.get("cli"), dict) else {}
        access = cli.get("access_token")
        expires = number(cli.get("expires_at"))
        if isinstance(access, str) and access.strip() and (expires is None or expires > time.time() + 60):
            return _kimi_fetch_with_settings(access.strip(), "cli", region)
        p = Provider("kimi", "Kimi Code")
        p.status = "expired"
        return p

    if provider_id == "grok":
        auth = snap.get("auth") if isinstance(snap.get("auth"), dict) else snap
        return _grok_fetch_with_auth(auth)

    p = Provider(provider_id, provider_id.title())
    p.status = "not_configured"
    return p


def _public_account_entry(raw_acc: dict[str, Any], preview: Optional[Provider], *, stale: bool = False) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": raw_acc.get("id"),
        "label": raw_acc.get("label"),
        "email": raw_acc.get("email"),
        "active": bool(raw_acc.get("active")),
        "source": raw_acc.get("source"),
        "kind": raw_acc.get("kind"),
        "region": raw_acc.get("region"),
    }
    if preview is not None:
        entry["status"] = preview.status
        entry["plan"] = preview.plan
        entry["windows"] = [asdict(w) for w in preview.windows]
        entry["error"] = preview.error
        entry["stale"] = stale or preview.stale
    return entry


def run(selected: list[str]) -> dict[str, Any]:
    accounts_mod = _load_accounts_module()
    account_states: dict[str, dict[str, Any]] = {}
    if accounts_mod is not None:
        for key in selected:
            try:
                account_states[key] = accounts_mod.list_provider_accounts(key, include_secrets=True)
            except Exception:
                pass

    inactive_jobs: list[tuple[str, dict[str, Any]]] = []
    for key in selected:
        state = account_states.get(key)
        if not state or not state.get("supports_accounts"):
            continue
        for acc in state.get("accounts") or []:
            if not acc.get("active"):
                inactive_jobs.append((key, acc))

    max_workers = min(16, max(1, len(selected) + len(inactive_jobs)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {key: pool.submit(FETCHERS[key]) for key in selected}
        preview_futures = {
            (pid, str(acc.get("id") or "")): pool.submit(_fetch_account_preview, pid, acc)
            for pid, acc in inactive_jobs
        }
        results = []
        for key in selected:
            try:
                results.append(futures[key].result())
            except Exception:
                failed = Provider(key, key.title())
                failed.status = "error"
                failed.error = "helper failure"
                results.append(failed)

        preview_results: dict[tuple[str, str], Provider] = {}
        for job_key, fut in preview_futures.items():
            try:
                preview_results[job_key] = fut.result()
            except Exception:
                failed = Provider(job_key[0], job_key[0].title())
                failed.status = "error"
                failed.error = "preview failure"
                preview_results[job_key] = failed

    previous = load_cache()
    previous_providers = {
        item.get("id"): item
        for item in previous.get("providers", [])
        if isinstance(item, dict)
    }
    previous_ts = previous.get("ts")
    previous_age = (time.time() - previous_ts) if isinstance(previous_ts, (int, float)) else 1e9

    payload: dict[str, Any] = {"version": 1, "ts": int(time.time()), "fetchedAt": now_iso(), "providers": []}
    for provider in results:
        state = account_states.get(provider.id)
        if state and state.get("supports_accounts"):
            provider.supports_accounts = True
            provider.account = state.get("active_label")
            provider.account_id = state.get("active_id")

        cached = previous_providers.get(provider.id)
        cached_accounts_by_id = {
            str(a.get("id") or ""): a
            for a in (cached.get("accounts") if isinstance(cached, dict) and isinstance(cached.get("accounts"), list) else [])
            if isinstance(a, dict)
        }

        # Network failures fall back to the last good reading for 24h (only for the same account).
        provider_dict: dict[str, Any]
        if (
            provider.status == "error"
            and cached
            and cached.get("status") == "ok"
            and previous_age < 86400
            and cached.get("account_id") == provider.account_id
        ):
            provider_dict = dict(cached)
            provider_dict["stale"] = True
            provider_dict["error"] = provider.error
            provider_dict["supports_accounts"] = provider.supports_accounts
            provider_dict["account"] = provider.account
            provider_dict["account_id"] = provider.account_id
        else:
            provider_dict = asdict(provider)

        # Build enriched accounts list (active + live/cached previews for inactive accounts).
        if state and state.get("supports_accounts"):
            public_accounts: list[dict[str, Any]] = []
            for raw_acc in state.get("accounts") or []:
                aid = str(raw_acc.get("id") or "")
                if raw_acc.get("active"):
                    entry = _public_account_entry(raw_acc, None)
                    entry["status"] = provider_dict.get("status")
                    entry["plan"] = provider_dict.get("plan")
                    entry["windows"] = provider_dict.get("windows") or []
                    entry["error"] = provider_dict.get("error")
                    entry["stale"] = bool(provider_dict.get("stale"))
                    public_accounts.append(entry)
                else:
                    prev_p = preview_results.get((provider.id, aid))
                    cached_acc = cached_accounts_by_id.get(aid)
                    if (
                        prev_p is not None
                        and prev_p.status == "error"
                        and cached_acc
                        and cached_acc.get("status") == "ok"
                        and previous_age < 86400
                    ):
                        entry = _public_account_entry(raw_acc, None)
                        entry["status"] = "ok"
                        entry["plan"] = cached_acc.get("plan")
                        entry["windows"] = cached_acc.get("windows") or []
                        entry["error"] = prev_p.error
                        entry["stale"] = True
                        public_accounts.append(entry)
                    else:
                        public_accounts.append(_public_account_entry(raw_acc, prev_p))

            provider_dict["accounts"] = public_accounts
            provider_dict["account_capabilities"] = {
                "supports_login": bool(state.get("supports_login")),
                "supports_api_key": bool(state.get("supports_api_key")),
                "api_key_placeholder": state.get("api_key_placeholder") or "",
                "regions": state.get("regions") or [],
                "login_hint": state.get("login_hint") or "",
            }

        payload["providers"].append(provider_dict)

    save_cache(payload)
    return payload


def selftest() -> int:
    """Print credential-source presence without values."""
    checks = [
        ("codex", Path(os.environ.get("CODEX_HOME") or (HOME / ".codex")) / "auth.json"),
        ("claude", Path(os.environ.get("CLAUDE_CONFIG_DIR") or (HOME / ".claude")) / ".credentials.json"),
        ("opencode-go", Path(os.environ.get("XDG_DATA_HOME") or (HOME / ".local/share")) / "opencode" / "auth.json"),
        ("qwen", Path(os.environ.get("QWEN_CONFIG_DIR") or (HOME / ".qwen")) / "oauth_creds.json"),
        ("muse", Path(os.environ.get("MUSE_AUTH_PATH") or (HOME / ".config/muse/auth.json"))),
        ("kimi", Path(os.environ.get("KIMI_CODE_HOME") or (HOME / ".kimi-code")) / "credentials" / "kimi-code.json"),
        ("grok", HOME / ".grok" / "auth.json"),
        ("cursor", Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config")) / "Cursor" / "User" / "globalStorage" / "state.vscdb"),
    ]
    print(f"ai-limits-helper {VERSION}")
    for name, path in checks:
        print(f"  {name:12} {'found' if path.exists() else 'missing':7} {path}")
    print(f"  {'antigravity':12} {'found' if secret_lookup('gemini', 'antigravity') else 'missing':7} Secret Service (gemini/antigravity)")
    print(f"  {'muse keyring':12} {'found' if secret_lookup('ai.meta.dev.credentials', 'meta') else 'missing':7} Secret Service (ai.meta.dev.credentials/meta)")
    print(f"  {'kimi key':12} {'found' if os.environ.get('KIMI_CODE_API_KEY') else 'missing':7} KIMI_CODE_API_KEY")
    print(f"  cache        {CACHE_FILE}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="ai-limits-helper")
    parser.add_argument("command", nargs="?", default="fetch", choices=("fetch", "selftest", "version"))
    parser.add_argument("--providers", default="", help="comma-separated subset")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "version":
        print(VERSION)
        return 0
    if args.command == "selftest":
        return selftest()

    selected = [item.strip() for item in args.providers.split(",") if item.strip()]
    selected = [item for item in (selected or ORDER) if item in FETCHERS]
    if not selected:
        print(json.dumps({"version": 1, "providers": [], "error": "no valid providers"}))
        return 2

    payload = run(selected)
    print(json.dumps(payload, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
