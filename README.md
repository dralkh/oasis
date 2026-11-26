# Oasis — AI Usage Limits & Quota Monitor for KDE Plasma 6

[![Release](https://img.shields.io/github/v/release/dralkh/oasis?label=release)](https://github.com/dralkh/oasis/releases/latest)
[![KDE Plasma 6](https://img.shields.io/badge/KDE-Plasma%206-1D99F3?logo=kde&logoColor=white)](https://kde.org/plasma-desktop/)
[![Python Stdlib](https://img.shields.io/badge/Python-3%20stdlib%20only-3776AB?logo=python&logoColor=white)](#security-model-short-version)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Lightweight, auditable **KDE Plasma 6 panel widget** (Linux **CodexBar alternative** with zero external binaries) showing live 5-hour, weekly, and monthly rate limits, **live on-the-fly multi-account switching** (reflecting directly in your CLI agents), reset countdowns, available usage-reset passes, subscription countdowns, desktop limit alerts, and an integrated **AI Intel** modal with a **Capability vs. Cost/Task Pareto graph** for:

**OpenAI Codex · Claude Code · Google Antigravity (Gemini & Claude) · OpenCode Go · Qwen · Muse Code · Kimi Code · Grok · Cursor**

Every provider is a two-line micro-cell in a vertical panel (or a one-line chip
in a horizontal panel):

```
Cx100        Codex, 100% of the 5-hour window (red = at limit)
W17↑1h       weekly 17%, nearest reset in 1 hour
Cl28
W28↑6d
Ag6
W3↑4h
Go5
W32↑1h
```

The popup uses full-width usage bars with a percentage and short reset. Active
providers get one bar per window; Antigravity gets one block per model group
with its 5-hour and weekly bars stacked underneath. Signed-out providers
collapse into one footer line. Hovering a bar shows the exact reset time.

A desktop notification fires when any active usage window crosses **100%**.
Simultaneous limits are combined into one notification; the first fetch after
startup is a silent baseline, and the alert re-arms after that window resets.

The top-right corner of the popup has a small **×** to quit, and the tray
context menu has **Quit Oasis**. Quitting removes the applet from the panel;
search **Oasis** in the application launcher to put it back in the system tray.

![panel](docs/panel.png)

![popup](docs/popup.png)

## Provider coverage

| Provider | Credential source (read-only) | Windows shown |
|---|---|---|
| Codex | `~/.codex/auth.json` (`tokens.access_token`), or OpenCode's `openai` OAuth entry | 5-hour, weekly |
| Claude | `~/.claude/.credentials.json` (`claudeAiOauth.accessToken`) | 5-hour, weekly, optional Opus/Sonnet weekly |
| Antigravity | Secret Service `service=gemini username=antigravity`; daily summary endpoint, with production summary and `fetchAvailableModels` fallbacks | 5-hour + weekly for Gemini and Claude |
| OpenCode Go | `~/.local/share/opencode/opencode.db` → `credential` (active `opencode-go` row); legacy `auth.json` fallback | 5-hour, weekly, monthly (+ subscription countdown) |
| Qwen | `~/.qwen/oauth_creds.json` (Qwen Code OAuth), `QWEN_CODING_API_KEY` / `DASHSCOPE_API_KEY`, `~/.codexbar/config.json`, or OpenCode DB | Rate-limit headers / connection status |
| Muse Code | `~/.config/muse/auth.json` (`providers.meta.access_token`, `dca:`) or Secret Service `ai.meta.dev.credentials/meta` | 5-hour, weekly |
| Kimi Code | `KIMI_CODE_API_KEY`, `~/.codexbar/config.json` Kimi API key, or `~/.kimi-code/credentials/kimi-code.json` | 5-hour, weekly, monthly |
| Grok | `~/.grok/auth.json` | weekly |
| Cursor | `~/.config/Cursor/User/globalStorage/state.vscdb` or Secret Service | billing cycle (+ Auto/API breakdown) |

`reset ↑2h` is the reset of the shortest active window (5-hour first, then
weekly); the popup shows every window and its own reset. A green `✓` means that
reset time has passed — refresh to collect the new numbers. The monthly
OpenCode Go and Kimi monthly windows are popup-only so they cannot hide the
daily/weekly numbers in the panel.

## Install

Requirements: **KDE Plasma 6**, Python 3 (stdlib only), `secret-tool` (`libsecret`) for Antigravity / Cursor / Muse keyring reads, `sqlite3` (stdlib) for Cursor & OpenCode Go.

### Option 1: Prebuilt `.plasmoid` (GitHub Releases)

Download `oasis-1.4.0.plasmoid` from [GitHub Releases](https://github.com/dralkh/oasis/releases/latest) and run:

```sh
kpackagetool6 -t Plasma/Applet -i oasis-1.4.0.plasmoid
# or upgrade an existing install:
kpackagetool6 -t Plasma/Applet -u oasis-1.4.0.plasmoid
```

### Option 2: From Source (includes Application Launcher entry)

```sh
git clone https://github.com/dralkh/oasis.git
cd oasis
./install.sh          # widget + application-menu entry, no network, no pip, no sudo
./add-to-panel.sh     # optional: add it to the panel that contains the system tray
```

### Option 3: KDE Store / Arch Linux (AUR)

- **KDE Store:** Right-click panel → **Add Widgets…** → **Get New…** → search **Oasis**.
- **Arch Linux / AUR:** `packaging/aur/PKGBUILD` (`plasma6-applets-oasis`) is included; see [docs/STORE_AND_AUR.md](docs/STORE_AND_AUR.md).

`install.sh` also installs an **Oasis** entry in the application launcher. Its
sole job is to add the applet to the system-tray panel — it does not open a
separate window. Or add it manually: right-click the panel → **Add Widgets…** →
**Oasis**.

Settings: refresh interval, provider names in the panel, reset countdown,
hide signed-out providers, limit notifications, warning/critical thresholds,
number of vertical panel slots (default 4), and an optional helper path
override.

Uninstall:

```sh
./uninstall.sh
rm -rf ~/.cache/ai-limits
```

`uninstall.sh` removes the widget, the launcher entry, and the installed icon.

## Performance

* No daemon: the helper starts, fetches all configured providers in parallel, writes a
  1.6 KB cache and exits. Nothing stays resident.
* Default refresh is **300 s**; the countdown re-renders once a minute without
  touching the network.
* Measured on this machine: median **1.2 s**, p95 **1.8 s**, peak transient
  RSS **~36 MiB** per run (mostly Python + TLS). That is about one second of
  one core every five minutes, almost all of it network wait.
* The Antigravity access token is cached for its lifetime (~1 h), so
  `secret-tool` is not spawned on every refresh.
* A 30 s watchdog clears a stuck `Plasma5Support` source and resets `loading`,
  so failed/timed-out fetches cannot accumulate sources or block future polls.
* Notifications run only when a window crosses 100%; `notify-send` exits
  immediately and no extra process stays resident.
* The panel keeps a fixed number of delegates (four by default) and replaces
  the payload object instead of appending history.

## Compact popup

The popup is built for scanning: bold provider headers, full-width bars, a
right-aligned percentage and a short reset (`↑4h`, `↑6d`). Bars are blue below
80%, amber at 80–94%, red at 95% and above, and green when the reset is
available. When a provider has available usage-limit resets (such as Codex
reset credits or Claude reset grants), a compact pill (`↻ 1 reset · Dec 12`)
appears in that provider's header row with per-grant expiry details on hover;
when none are available, nothing extra is rendered. Where a provider exposes billing-period end dates (Codex, OpenCode Go, Cursor,
or Claude when a period/trial end date is returned), remaining subscription time
appears subtly beside the plan name.

Click **✦ Intel** in the popup header to open the integrated **AI Intel** modal,
which invokes `pi` (using your configured default model + Tavily MCP web search)
on demand to compile a compact tabbed brief: **Tiers** (Diamond / Gold / Silver
models for Main Agent and Subagent by cost per task and current consensus),
**Deals** (active discounts, free months, and cancel/retention offers with
country and audience tags), **Limits** (live pace projections and subscription
countdowns), and **Pulse** (best value stack, policy/pricing alerts, and sources).
Results are cached at `~/.cache/ai-limits/intel.json` (`0600`) so reopening the
modal is instant. Antigravity gets one
indented block per model group, with the group's reported windows stacked
underneath the model name. Signed-out providers collapse into a single footer
line with tooltips (no per-provider setup paragraphs), so four active providers
plus four signed-out ones fit in about 400 px instead of 760 px.

## Multi-account switching (live CLI sync)

Oasis lets you save, add, preview, and switch accounts on the fly for all CLI
providers (**Codex, Claude Code, Antigravity, OpenCode Go, Qwen, Muse Code,
Kimi Code, Grok**), updating each CLI's active credential store directly:

* **Inline switcher:** Click the account pill on any provider row in the popup
  to expand all saved accounts with **live usage previews** (so you can see
  which account still has quota left before switching). Clicking an account
  switches the CLI's live credential immediately and refreshes usage.
* **Add & manage (`⇄ Accounts`):** Click **⇄ Accounts** in the popup header (or
  **+ Add account** / a signed-out CLI provider in the footer) to:
  * **Sign in via native CLI login** (`codex login`, `claude auth login`,
    `agy`, `opencode auth login -p opencode-go`, `qwen`, `muse login`, `kimi`,
    `grok login`) — Oasis snapshots your current active sign-in first so nothing
    is lost, runs the CLI login in a terminal, saves & activates the new
    account, and automatically restores your previous account if you cancel.
  * **Save active CLI sign-in** into the local vault (`~/.config/oasis/accounts`,
    mode `0700`/`0600`) with an optional custom label.
  * **Add API keys directly** for key-based providers (OpenCode Go, Qwen, Kimi
    with region selection).
  * **Rename or remove** saved accounts.
* **OpenCode Go native DB:** Existing multi-account entries in
  `~/.local/share/opencode/opencode.db` appear automatically and switch via
  `opencode auth switch opencode-go <id>`.
* **Safe token sync:** Before switching away from an active account, Oasis
  syncs the live credential back to its vault snapshot so CLI-rotated refresh
  tokens are never lost.

## Integrated local inference proxy (`⚡ Proxy`)

Oasis supports two independent operating modes:

* **Direct CLI mode (default, daemon-free):** Identical to v1.3.0 — zero
  resident processes, native CLI credential switching and quota monitoring.
* **Oasis Proxy mode (opt-in, bundled):** Ships with an integrated native
  local inference proxy engine (`contents/bin/oasis-proxy`, built automatically
  by `./install.sh`, `./build-plasmoid.sh`, AUR `PKGBUILD`, and GitHub Releases;
  ~11 MiB idle RSS) that loads automatically when you switch to **Oasis Proxy**
  or click **Start**. Binds strictly to `127.0.0.1:8317` with separate
  generated 256-bit client and management keys, `UMask=0077`,
  `proxy-url: "direct"`, and optional `systemd --user` lifecycle
  (`oasis-proxy.service`) independent from Plasma.

> **Credential ownership:** Rotating OAuth credentials (`codex`, `claude`,
> `grok`, `kimi` OAuth) are strictly isolated in `~/.config/oasis/proxy/auths/`
> (`0700`/`0600`) and are **never** copied or symlinked from your native CLI
> directories so refresh tokens never collide. Direct CLI switching updates
> native CLI files for new sessions; Oasis Proxy routes sessions through
> `http://127.0.0.1:8317` with prompt-cache-preserving session affinity and
> explicit per-provider account pinning.

### Controlling Oasis Proxy from the CLI

```sh
# Inspect status (metadata only, never prints keys)
python3 package/contents/code/oasis_proxy.py status --pretty

# Start / stop / restart explicitly (loads the bundled Oasis Proxy engine automatically)
python3 package/contents/code/oasis_proxy.py start
python3 package/contents/code/oasis_proxy.py stop --direct

# Set per-provider default model & thinking/reasoning mode
python3 package/contents/code/oasis_proxy.py set-provider-default --provider=claude --model=claude-sonnet-5-5 --thinking=high

# Configure single-daemon Tier Routing (Diamond / Gold / Silver / Subagent)
python3 package/contents/code/oasis_proxy.py set-tier --tier=diamond --model=claude-opus-4-6 --thinking=high

# Preview or apply cached AI Intel rankings directly into Tier Routing
python3 package/contents/code/oasis_proxy.py import-intel
python3 package/contents/code/oasis_proxy.py import-intel --apply
```

### Tier Routing & Per-Provider Thinking Modes

A single `oasis-proxy` instance (~11 MiB RAM, shared OAuth pool and quotas) exposes both:
* **Virtual model IDs** on `http://127.0.0.1:8317/v1`: `oasis/diamond`, `oasis/gold`, `oasis/silver`, `oasis/subagent`, and `oasis/<provider>`
* **Dedicated tier base URLs** for clients that expect a separate endpoint per tier: `http://127.0.0.1:8317/tier/{diamond,gold,silver,subagent}/v1` (OpenAI) and `http://127.0.0.1:8317/tier/{tier}` (Anthropic / Gemini)

Configured thinking/reasoning effort (`auto`, `none`, `low`, `medium`, `high`, `xhigh`, `max`) is translated natively into Anthropic `thinking`/`output_config`, OpenAI Responses `reasoning.effort`, OpenAI Chat `reasoning_effort`, and Gemini `thinkingConfig` whenever the client does not pass an explicit override.

### Connecting coding agents & SDKs to Oasis Proxy

Click **Copy URL** and **Copy Key** in the `⚡ Proxy` view (keys copy directly
via `wl-copy`/`xclip`/`xsel` stdin and never appear in UI properties or logs),
or run `python3 package/contents/code/oasis_proxy.py client-configs`:

* **Claude Code:**
  ```sh
  export ANTHROPIC_BASE_URL=http://127.0.0.1:8317
  export ANTHROPIC_AUTH_TOKEN="<paste Oasis proxy client key>"
  claude
  ```
* **OpenAI Codex (`~/.codex/config.toml`):**
  ```toml
  model_provider = "oasis"

  [model_providers.oasis]
  name = "Oasis Proxy"
  base_url = "http://127.0.0.1:8317/v1"
  wire_api = "responses"
  env_key = "OASIS_PROXY_KEY"
  ```
* **OpenCode & Hermes Agent:**
  ```sh
  export OPENAI_BASE_URL=http://127.0.0.1:8317/v1
  export OPENAI_API_KEY="<paste Oasis proxy client key>"
  export ANTHROPIC_BASE_URL=http://127.0.0.1:8317
  export ANTHROPIC_API_KEY="<paste Oasis proxy client key>"
  ```
* **OpenAI Python SDK:**
  ```python
  from openai import OpenAI
  client = OpenAI(base_url="http://127.0.0.1:8317/v1", api_key="<paste Oasis proxy client key>")
  ```

See the full architecture, security, provider protocol, and benchmark docs:
- [ADR-0001: Proxy Backend Architecture & Dependency Audit](docs/ADR-0001-proxy-backend.md)
- [Credential Ownership, Concurrency & Threat Model](docs/SECURITY_AND_CREDENTIALS.md)
- [Provider Compatibility & Protocol Matrix](docs/PROVIDER_MATRIX.md)
- [Performance Benchmarks (v1.3.0 vs. Integrated)](docs/BENCHMARKS.md)

## Limit notifications

`notify-send` delivers a local desktop notification when a provider window
first reaches 100% (`5h`, `1w`, or `1mo`). Several limits reached in the same
fetch are combined into a single notification, for example:

```
Heads up
Claude 5h 100% · in 1h 12m; OpenCode Go 1mo 100% · in 3d
```

The first successful fetch only records the current state, so starting or
restarting Plasma does not replay alerts for limits that were already active.
When a window drops below 100% and later reaches it again, the notification
re-arms. Toggle it under **Notifications** in the widget settings.

## Security model (short version)

* Background polling (`ai_limits.py`) opens Codex/Grok/Cursor/Muse/Kimi/Qwen/OpenCode
  credential files **read-only**. Codex/Grok/Cursor refresh tokens rotate, so
  Oasis never refreshes them behind the owning CLI's back and shows `expired`
  until the real client renews.
* Claude Code access tokens expire every ~8 hours. When expired, the helper
  acquires Claude Code's own lock directories (`.oauth_refresh.lock` and
  `.claude.lock`), refreshes via `https://platform.claude.com/v1/oauth/token`,
  and atomically updates `~/.claude/.credentials.json` with a compare-and-swap
  check so running Claude sessions stay signed in.
* Antigravity refreshes its non-rotating Google OAuth token into a private
  short-lived cache at `~/.cache/ai-limits/antigravity*.json` (mode `0600`).
  The refresh token is never copied there.
* Multi-account switching (`oasis_accounts.py`) stores metadata (no secrets) in
  `~/.config/oasis/accounts/index.json` (`0600`) and credential snapshots in
  `~/.config/oasis/accounts/secrets/` (`0700`/`0600`). Live CLI credential files,
  `opencode.db`, or Secret Service (`secret-tool store` via stdin) are updated
  **only** when you explicitly switch, add an API key, or complete a login.
* Usage cache is written to `~/.cache/ai-limits/last.json` (`0600`): percentages,
  account labels (never tokens), plan names, and reset timestamps, so a network
  failure shows stale numbers instead of blanks.
* Every request is pinned to an explicit host allowlist, redirects are refused
  (otherwise urllib would forward `Authorization` to a 30x target), and
  environment proxies are ignored.
* No telemetry. No analytics. No auto-update. No external CLI, no downloaded
  binaries, no `pip install`, no `curl | bash`.
* The helpers are stdlib Python 3 only; the UI is plain QML. There is no
  compiled artifact to trust.

See [SECURITY.md](SECURITY.md) for the full data-flow table, threat model and
audit commands.

## Why this instead of the five existing widgets

Static review on 2026-10-29 (none of the install scripts or binaries were run):

| Repository | Stack | Providers | Main trust issue |
|---|---|---|---|
| `EvilFreelancer/CodexBar-KDE` | QML + JS | via CodexBar CLI | Command line built by concatenation; `codexbarPath` and provider IDs are unquoted (the proxy URL is quoted). Delegates all credentials to the upstream Swift CLI |
| `tylxr59/KodexBar` | QML | via CodexBar CLI | Best UI/provider coverage (incl. OpenCode Go), but the actual credential access is the 99 MB, macOS-first CodexBar CLI installed from a brew tap or release tarball |
| `yusufipk/kde-agents-usage` | Python stdlib | Claude, Codex | Only 2 providers; refreshes and rewrites rotating Codex tokens (can log out a running CLI) |
| `burakgon/ai-usage-kde-widget` | Python stdlib | 7 (no OpenCode Go) | Closest existing fit and clean code, but installs with `pip --user --break-system-packages`, refreshes/writes Claude/Codex/Cursor credentials, and the compact view is single-provider |
| `materemias/codexbar-agentview` | Python + C++ + Luau | via CodexBar CLI | Native C++ plugin plus CodexBar CLI; largest unauditable surface; macOS/Tern oriented |

No repository showed telemetry or a non-first-party endpoint in a quick pass.
The differentiator is **how much credential-holding code you trust**: three of
the five import the entire CodexBar CLI, and two of the Python widgets rotate
your tokens. This project keeps the credential surface to eight small parsers
and never refreshes rotating tokens.

Endpoint/parser knowledge was cross-checked against the MIT-licensed
`burakgon/ai-usage-kde-widget`, CodexBar's provider docs, and OpenCode's public
usage shapes. The code here is original.

## Verification

```sh
# what the helper can see (paths and presence only, never values)
python3 package/contents/code/ai_limits.py selftest

# list detected & saved accounts across all CLI providers (metadata only, never secrets)
python3 package/contents/code/oasis_accounts.py list --pretty

# the exact JSON the panel consumes
python3 package/contents/code/ai_limits.py fetch --pretty

# every network host is allowlisted
grep -n "ALLOWED_HOSTS" -A16 package/contents/code/ai_limits.py

# observe outbound connections during a fetch
strace -f -e trace=connect python3 package/contents/code/ai_limits.py fetch
```

Expected hosts: `chatgpt.com`, `api.anthropic.com`,
`daily-cloudcode-pa.googleapis.com`, `cloudcode-pa.googleapis.com`,
`oauth2.googleapis.com`, `opencode.ai`, `cli-chat-proxy.grok.com`,
`api2.cursor.sh`.

## Known limitations

* Codex access tokens expire (~5 days). The widget reports `expired` rather
  than refreshing and risking a logout; running `codex` once renews it. Claude
  access tokens (~8h) are renewed automatically using Claude Code's own lock
  protocol; if the refresh token itself is revoked, run `claude` to sign in
  again.
* OpenCode Go returns `api_key_expired` until you create a new key and run
  `opencode auth login -p opencode-go`.
* Cursor has no daily/weekly quota API; the billing cycle and Auto/API split
  are the closest equivalent.
* Antigravity is collapsed to two groups: **Gemini** (shared Gemini 5-hour +
  weekly) and **Claude** (shared 3p 5-hour + weekly).
* Muse Code reads only the `muse login` device token; the optional
  `dev.meta.ai` browser-team quota is not read. Kimi reads an API key or the
  `kimi` CLI token; browser cookies are never imported.
* Kimi defaults to the China region (`api.kimi.com`). For International, set
  `{"providers":[{"id":"kimi","region":"international","apiKey":"…"}]}` in
  `~/.codexbar/config.json` (or use `KIMI_CODE_API_KEY` with that config).
* Claude's 5-hour window shows **ready** when the API reports 0% with no
  reset time: the window is idle and starts on the next Claude request. If
  Anthropic rate-limits the usage endpoint (HTTP 429), the last good numbers
  stay visible with a `stale` badge; polling is every 5 minutes to avoid it.
* Plasma caches a panel item's size hint. The vertical layout therefore uses a
  fixed number of slots (default 4) rather than growing after the first fetch.
  Set **Vertical panel slots** to 6 when Muse/Kimi are also signed in.

## Acknowledgments

- **[`IuCC123/CLIProxyAPI-Rust`](https://github.com/IuCC123/CLIProxyAPI-Rust)** *(Unlicense)* — powers Oasis's optional local CLI inference proxy mode behind a replaceable backend adapter.

## License

MIT. See [LICENSE](LICENSE).
