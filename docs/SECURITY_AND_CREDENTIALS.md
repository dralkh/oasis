# Oasis Credential Ownership, Concurrency & Threat Model

This document defines the release-blocking credential integrity rules and threat model governing Oasis in both **Direct CLI mode** (v1.3.0 baseline) and **Proxy mode** (optional `CLIProxyAPI-Rust` backend).

---

## 1. Fundamental Rule: Single Authoritative Refresh Owner

> **No rotating OAuth credential may ever be shared concurrently between a native CLI process and the inference proxy.**

In `CLIProxyAPI-Rust`, `src/oauth.rs:253-428` runs a 60-second background `refresher` loop and an on-demand `ensure_fresh` path protected **only** by an in-process `tokio::sync::Mutex<()>`. It does **not** acquire cross-process file locks (e.g., Claude Code's `.oauth_refresh.lock` + `.claude.lock` or Codex's `flock`), nor does it perform compare-and-swap against native CLI credential files.

If a rotating refresh token (Codex, Claude, Grok, Kimi OAuth) were copied or symlinked from Oasis's native CLI store (`~/.codex/auth.json`, `~/.claude/.credentials.json`, `~/.grok/auth.json`, `~/.kimi-code/credentials/kimi-code.json`, or `~/.config/oasis/accounts/secrets/`) into the proxy's `auth-dir`, both the native CLI (or Oasis's Claude auto-refresh) and the proxy would independently consume the single-use refresh token, resulting in `invalid_grant` revocation and unexpected logouts.

**Therefore:**
1. Oasis **NEVER** copies, imports, or symlinks `~/.config/oasis/accounts/` or native CLI OAuth files into `~/.config/oasis/proxy/auths/`.
2. The proxy uses a strictly isolated authentication directory: `~/.config/oasis/proxy/auths/` (mode `0700`, files `0600`).
3. Rotating OAuth accounts used by the proxy are authenticated through the proxy's own OAuth/device-code flow (`cliproxyapi-rust login <provider>` or `/api/login/{provider}`), which issues an independent OAuth refresh token family owned solely by the proxy.
4. When a user points a native CLI (e.g., `claude` via `ANTHROPIC_BASE_URL=http://127.0.0.1:8317`, `codex` via `base_url`) at the Oasis proxy, that CLI authenticates to localhost using Oasis's local proxy client key (`sk-oasis-...`) and does **not** refresh its own upstream OAuth token.

---

## 2. Credential Ownership Matrix by Provider

| Provider | Native CLI Store (Direct CLI Mode) | Refresh Token Behavior | Native CLI Live Reload Behavior | Proxy Mode Credential Ownership | Safe Sync From Oasis? |
|---|---|---|---|---|---|
| **Codex** | `~/.codex/auth.json` | **Rotating** (single-use refresh token; no shared cross-process lock with proxy) | Loads on start/turn; active WS sessions retain in-memory token until reconnect/restart | **Isolated proxy OAuth** (`auths/codex-*.json`) OR static OpenAI API key | **NO** for OAuth; **YES** for static API key |
| **Claude Code** | `~/.claude/.credentials.json` (`claudeAiOauth` + preserved `mcpOAuth`) + `~/.claude.json` (`oauthAccount`) | **Rotating** (~8h access token; native CLI uses `.oauth_refresh.lock` + `.claude.lock` + CAS) | Reads on startup and refresh under lock; active session holds token in memory until expiry/401 or restart | **Isolated proxy OAuth** (`auths/claude-*.json`) OR static Anthropic API key | **NO** for OAuth; **YES** for static API key |
| **Antigravity** | Secret Service `gemini/antigravity` (`go-keyring-base64:`) | **Non-rotating** Google OAuth refresh token; ~1h access token | `agy` caches access token in memory until expiry | **Isolated proxy OAuth** (`auths/antigravity-*.json`) — separate consent & Cloud Code project onboarding | **NO** (keep isolated so proxy project onboarding & client_id never collide with `agy`) |
| **OpenCode Go** | `~/.local/share/opencode/opencode.db` (`credential` table, `integration_id='opencode-go'`) + legacy `auth.json` | **Static API key** (non-rotating) | `opencode` queries SQLite `active=1` row per session/request | **Synced API key** (`openai-compatibility` entry for Chat Completions models only; Messages/Responses models remain Direct CLI only) | **YES** (explicit user opt-in; static key) |
| **Qwen** | `~/.qwen/oauth_creds.json` (OAuth) or `~/.codexbar/config.json` / env (Coding Plan `sk-sp-*` / DashScope API key) | OAuth: **Rotating**; Coding Plan / DashScope: **Static API key** | `qwen` CLI caches OAuth token in memory per process | **Synced API key** (`openai-compatibility` with exact regional base URL); Qwen OAuth is **Direct CLI only** | **NO** for OAuth; **YES** for static API key |
| **Muse Code** | `~/.config/muse/auth.json` (`dca:` device token) or Secret Service `ai.meta.dev.credentials/meta` | Device token mints stateless API key via `api.meta.ai/muse-code/key` | Loaded on CLI startup | **Isolated proxy device login** (`auths/meta-*.json`) OR static Meta API key | **NO** for device token (keep isolated); **YES** for static API key |
| **Kimi Code** | `~/.kimi-code/credentials/kimi-code.json` (device-bound OAuth) or `~/.codexbar/config.json` / `KIMI_CODE_API_KEY` | OAuth: **Rotating + device-ID bound** (`X-Msh-Device-Id`); API key: **Static** | Loaded on CLI startup | **Isolated proxy device login** (`auths/kimi-*.json`) OR **Synced static API key** (`kimi-api-key` with regional base URL) | **NO** for OAuth; **YES** for static API key |
| **Grok** | `~/.grok/auth.json` | **Rotating** (OIDC refresh token) | Loaded on CLI startup | **Isolated proxy device login** (`auths/xai-*.json`) OR static xAI API key | **NO** for OAuth; **YES** for static API key |
| **Cursor** | `~/.config/Cursor/User/globalStorage/state.vscdb` or Secret Service | **Rotating** IDE-managed token | Managed exclusively by Cursor desktop app | **Monitoring only** (not proxied) | **NO** |

### 2.1 Native CLI Session Behavior vs. Proxy Routing

Updating a native CLI credential file in **Direct CLI mode** updates the active credential for **new CLI invocations** (and for OpenCode SQLite queries), but **does not retroactively switch an already-running in-flight inference session** (e.g., an open Codex WebSocket or Claude Code process holding a cached bearer token in memory).

In **Proxy mode**, account selection, pinning, and failover happen **per request / per session at the local proxy** (`http://127.0.0.1:8317`) without touching native CLI credential files, `mcpOAuth`, `~/.claude.json` project history, or OpenCode SQLite state. Both modes are kept clearly labeled and separate in Oasis's UI.

---

## 3. Atomic Writes, Locking, and Permission Invariants

### 3.1 Oasis Vault & Direct CLI Mode (`oasis_accounts.py`, `ai_limits.py`)
- All vault directories (`~/.config/oasis/accounts`, `.../secrets`, `.../secrets/<provider>`) are `0700`; all JSON files (`index.json`, `secrets/<provider>/<id>.json`, `~/.cache/ai-limits/*.json`) are written atomically via random-suffixed temp files (`os.open(..., 0o600)` + `fsync` + `os.replace`) and verified `0600`.
- **Symlink & path traversal hardening:** Vault secret paths reject symlinks (`O_NOFOLLOW` / `is_symlink()` check) and strictly validate `provider` against `PROVIDER_SPECS` and `account_id` against `^[A-Za-z0-9_-]{1,80}$` with resolved path containment inside `SECRETS_DIR`.
- **Claude locking & preservation:** Acquires both `.oauth_refresh.lock` and legacy `.claude.lock`, performs compare-and-swap on `refreshToken`, and preserves `mcpOAuth` in `.credentials.json` and all non-`oauthAccount` keys/project history in `~/.claude.json`.
- **Failed/canceled login rollback:** Before clearing any live credential for a fresh native login, Oasis snapshots the current live credential into the vault; if login exits without producing a distinct credential, the previous live snapshot is atomically restored.
- **OpenCode SQLite locking:** Reads open SQLite with `?mode=ro` + `PRAGMA query_only=ON`; writes use bounded `timeout=5` transactions and never drop/alter unrelated rows or integrations.

### 3.2 Proxy Control Plane (`oasis_proxy.py`)
- Proxy home directory: `~/.config/oasis/proxy/` (`0700`), containing:
  - `config.yaml` (`0600`, atomic write via `0o600` temp file + `fsync` + `os.replace`) — contains generated `api-keys` and `management-key` plus proxy settings.
  - `auths/` (`0700`) — isolated `CLIProxyAPI-Rust` OAuth credential directory.
  - `policy.json` (`0600`) — Oasis-owned routing metadata (pinned accounts, disabled-before-pin state, preferred model/provider, billing preferences, no secrets).
  - `runtime.json` (`0600`) — PID, start timestamp, bound port, backend version (no secrets).
- **Mandatory `umask 0077`:** Both direct spawning (`os.umask(0o077)` in `preexec_fn` / child wrapper) and the systemd user unit (`UMask=0077`) enforce `0077`, guaranteeing that every file written by `cliproxyapi-rust` (OAuth JSONs, `.routing-sessions.state`, `config.yaml`, `config.yaml.bak`) is created `0600` from the very first syscall, eliminating upstream's `0644` TOCTOU window.
- **Exclusive proxy lockfile:** `~/.config/oasis/proxy/.control.lock` (`fcntl.flock`) serializes concurrent config mutations, lifecycle actions (`start`/`stop`/`restart`), and API key syncs.

---

## 4. Secret Handling Guarantees

Secrets (`management-key`, client `api-keys`, upstream API keys, OAuth access/refresh tokens, `dca:` tokens, Secret Service payloads) MUST NEVER appear in:
- Process `argv` (never pass keys on command line; API keys enter via stdin; clipboard copy pipes via stdin to `wl-copy`/`xclip`/`xsel`).
- Standard output or standard error of `ai_limits.py`, `oasis_accounts.py`, `oasis_proxy.py` (except explicit local-only secret display if requested via CLI `--show-key`, never used by QML!).
- QML properties, Plasma logs, or desktop notifications (`notify-send`).
- Management HTTP query strings (always `Authorization: Bearer`, never `?key=`).
- Request history summaries or normalized error strings (Oasis runs a redaction pass stripping `Bearer ...`, `sk-...`, `AIza...`, `GOCSPX-...`, `xai-...`, `dca:...`, and URL `user:pass@` credentials before returning errors or request logs to QML).
- Temporary files with mode wider than `0600`.
- Test fixtures or Git commits (tests use clearly synthetic `test-...` placeholders only).

---

## 5. Threat Model & Mitigations

| Threat | Attack Vector | Mitigation in Oasis |
|---|---|---|
| **Local malicious same-user process** | Reads world-readable config/cache, connects to unauthenticated localhost proxy, inspects `/proc/<pid>/cmdline` | `0700` dirs + `0600` files + `umask 0077`; random 256-bit `management-key` and client `api-keys` mandatory; zero secrets in argv; note same-user root/compromised user account remains out of scope (OS boundary, same as `SECURITY.md`). |
| **DNS Rebinding** | Malicious webpage rebinds `attacker.com` → `127.0.0.1:8317` to call `/api/config` or `/v1/chat/completions` | Mandatory 256-bit `management-key` on `/api/*` and mandatory 256-bit `api-keys` on `/v1/*`; attacker webpage cannot read or guess either key. |
| **CSRF & Cross-Site WebSocket Hijacking (CSWSH)** | Malicious webpage sends simple `POST /api/accounts/{id}/reset` or opens `ws://127.0.0.1:8317/api/live` | Mandatory `management-key` (`Authorization: Bearer`) rejects all ambient browser requests and unauthenticated WebSocket handshakes with `401`. |
| **Permissive CORS on Inference** | `CLIProxyAPI-Rust` sets `CorsLayer::permissive()` on `/v1/*` | Mandatory random client `api-keys` prevents any untrusted origin from making authorized inference calls. |
| **SSRF / Malicious Upstream Config** | Attacker or typo configures `base-url: http://169.254.169.254/...` or `file://` or `vertex` `token_uri` | `oasis_proxy.py` strictly validates all `base_url` / `proxy_url` inputs: allows only `https://` for remote hosts (blocks loopback/RFC1918/link-local/metadata IPs, userinfo, control chars), allows `http://127.0.0.1` / `http://localhost` ONLY for explicit local-server `openai-compatibility` entries (e.g., Ollama/vLLM), and never exposes `/api/vertex`. |
| **Ambient Proxy Env Hijacking** | Malicious `HTTP_PROXY`/`HTTPS_PROXY` redirects bearer tokens to an attacker proxy | `ai_limits.py` uses `ProxyHandler({})`; `oasis_proxy.py` writes `proxy-url: "direct"` by default (`no_proxy()` in `CLIProxyAPI-Rust`), strips proxy env vars from backend subprocess, and uses `ProxyHandler({})` + `_NoRedirect` for all Oasis→proxy localhost calls. |
| **HTTP Redirect Token Leakage** | Upstream returns 302 redirecting `x-api-key` or `Authorization` to a third-party host | `ai_limits.py` and `oasis_proxy.py` refuse all redirects (`_NoRedirect`). Upstream `CLIProxyAPI-Rust` redirect hardening is tracked as upstream patch #1 and mitigated by pinning known first-party HTTPS hosts in Oasis-managed config. |
| **Path Traversal & Symlink Attacks** | Crafted `account_id`, `provider`, or symlink inside `~/.config/oasis/` overwrites arbitrary user files | Strict regex validation (`^[A-Za-z0-9_-]{1,80}$`), `Path.resolve()` containment check inside vault/proxy dir, explicit `is_symlink()` rejection before read/write/unlink, and `O_NOFOLLOW` where opening secrets. |
| **Command Injection** | Crafted account label, model name, URL, or path passed from QML `DataSource` to shell | QML strictly `shellQuote()`s all arguments, passes secrets exclusively via base64→stdin, and Python `subprocess.run` NEVER uses `shell=True`. |
| **Unauthorized Inference / Silent Billing** | Proxy falls back from free/subscription account to a metered API key or swaps provider silently | Metered API keys are clearly separated (`billing: "metered"` vs `"subscription"`), `force-model-prefix` and explicit provider/account pinning are supported, and metered keys are never added without explicit user action. |
| **Backend Supply-Chain / Silent Downloads** | Widget silently downloads unsigned binary from the internet | Oasis NEVER auto-downloads binaries. Backend installation is explicit, user-triggered, pinned to an exact version, and verified against an explicit SHA-256 checksum (or built locally from verified source). |
