#!/usr/bin/env python3
"""Oasis AI Intel helper: two-stage grounded web intelligence engine.

Stage 1: Executes a parallel Tavily `advanced` search sweep across benchmarks,
         measured cost-per-task leaderboards, regional/local deals, retention
         offers, and policy changes across all Oasis-supported providers.
Stage 2: Feeds the deduplicated, spam-filtered evidence dossier into `pi`
         (using your configured default model + Tavily MCP for follow-up
         extraction) to synthesize a compact, evidence-grounded JSON brief.

Subcommands:
  read   Return the cached intel report enriched with live local limit projections.
  scan   Run Stage 1 + Stage 2, cache at ~/.cache/ai-limits/intel.json (0600),
         and print JSON.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

HOME = Path.home()
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or (HOME / ".cache")) / "ai-limits"
USAGE_CACHE = CACHE_DIR / "last.json"
INTEL_CACHE = CACHE_DIR / "intel.json"
PI_SETTINGS = HOME / ".pi" / "agent" / "settings.json"
MCP_CONFIG = HOME / ".config" / "mcp" / "mcp.json"

PROVIDERS_TRACKED = [
    "Codex (OpenAI ChatGPT Plus / Pro / Go)",
    "Claude (Anthropic Claude Pro / Max / Claude Code)",
    "Antigravity (Google One / Google AI Plus / Pro / Ultra)",
    "OpenCode Go",
    "Qwen (Qwen Code / Alibaba Cloud Model Studio Coding Plan & Token Plan)",
    "Cursor",
    "Kimi Code",
    "Grok (SuperGrok / xAI)",
    "Muse Code (Meta)",
]

SPAM_DOMAINS = [
    "aipromonow.com",
    "sociablelabs.com",
    "retailmenot.com",
    "worthepenny.com",
    "coupert.com",
    "couponbirds.com",
    "gamsgo.com",
    "instagram.com",
    "tiktok.com",
    "pinterest.com",
    "facebook.com",
]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def save_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_tavily_key() -> Optional[str]:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if key:
        return key
    env_file = HOME / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if re.match(r"^\s*TAVILY_API_KEY\s*=", line):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    except OSError:
        pass
    return None


def _tavily_search(api_key: str, query: str, extra: dict[str, Any]) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": 4,
        "exclude_domains": SPAM_DOMAINS,
        **extra,
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "oasis-intel/2.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=22) as resp:
            data = json.loads(resp.read(2 * 1024 * 1024))
        results = data.get("results") if isinstance(data, dict) else None
        return results if isinstance(results, list) else []
    except Exception:
        return []


def gather_tavily_dossier(now_dt: datetime) -> str:
    """Run a parallel worldwide Tavily sweep across benchmarks, cost-per-task,
    global + regional deals across all countries, retention offers, and policy news."""
    api_key = read_tavily_key()
    if not api_key:
        return ""

    year = now_dt.strftime("%Y")
    month_year = now_dt.strftime("%B %Y")

    tasks: list[tuple[str, str, dict[str, Any]]] = [
        # --- Model Benchmarks & Cost per Task ---
        (
            "MAIN_BENCHMARKS_CLAUDE",
            f"Claude Fable 5.2 Claude Opus 5.5 benchmarks SWE-bench Pro Terminal-Bench 4.0 cost per task pricing {year}",
            {},
        ),
        (
            "MAIN_LEADERBOARD",
            f"SWE-bench Pro Terminal-Bench 4.0 leaderboard {month_year} Claude Fable 5.2 Opus 5.5 Sonnet 5.5 Gemini 4 Neon GPT-6 Astra",
            {},
        ),
        (
            "OPENAI_TERRA_SOL",
            f"GPT-6.1 Terra GPT-6.1 Sol GPT-6 Astra coding benchmarks Vals Index cost per task AA task {year}",
            {},
        ),
        (
            "GEMINI_4_COST",
            f"Gemini 4 Neon Gemini 4 Carbon Gemini 4 Hydrogen real cost per agent task benchmarks pricing {year}",
            {},
        ),
        (
            "SUBAGENT_CONSENSUS",
            f"best coding subagent model {month_year} cost per task GPT-6.1 Terra Claude Haiku 5.5 Sonnet 5.5 Gemini Hydrogen DeepSeek V4.1",
            {},
        ),
        (
            "OPEN_MODELS_CODING",
            f"Qwen 4 Qwen 3.5 DeepSeek V4.1 MiniMax M3 Kimi K2.5 coding benchmarks pricing cost per task {year}",
            {},
        ),
        # --- Worldwide & Regional Deals Across All Countries & Deal Types ---
        (
            "ANTIGRAVITY_GLOBAL_REGIONAL",
            f'Google AI Pro Google AI Plus Antigravity promo discount "Black Friday" OR "50% off" OR "$4.99" OR "first month" OR student regional pricing {year}',
            {"time_range": "month"},
        ),
        (
            "WORLDWIDE_REGIONAL_PROMOS",
            f"Google AI Pro ChatGPT Plus Go Claude Pro regional discount free months countries India Europe Saudi UAE UK US Brazil Japan {year}",
            {"time_range": "year"},
        ),
        (
            "CODEX_DEALS_RETENTION",
            f'ChatGPT Plus Go Codex Pro discount "free month" OR "50% off" OR cancel retention OR OSS OR student {year}',
            {"time_range": "year"},
        ),
        (
            "CLAUDE_DEALS_RETENTION",
            f'Claude Pro Max Code cancel retention "50% off" OR "free month" OR student OR Black Friday discount {year}',
            {"time_range": "year"},
        ),
        (
            "CURSOR_DEALS_RETENTION",
            f'Cursor Pro Ultra cancel retention "50% off" OR student OR annual OR Black Friday discount {year}',
            {"time_range": "year"},
        ),
        (
            "QWEN_DEALS",
            f"Qwen Coding Plan Qwen Token Plan Alibaba Cloud Model Studio China International discount first month price {year}",
            {"time_range": "year"},
        ),
        (
            "OTHER_PROVIDERS_DEALS",
            f"OpenCode Go Kimi Code SuperGrok Grok Muse Code discount promo referral free trial regional {year}",
            {"time_range": "year"},
        ),
        (
            "POLICY_ALERTS",
            f"Claude Code Codex Antigravity Cursor Qwen quota limit change price increase deprecation {month_year}",
            {"time_range": "month"},
        ),
    ]

    seen_urls: set[str] = set()
    sections: list[str] = []

    def _run(item: tuple[str, str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        tag, q, extra = item
        return tag, _tavily_search(api_key, q, extra)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for tag, results in pool.map(_run, tasks):
            lines: list[str] = []
            for res in results:
                url = str(res.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                title = str(res.get("title") or "").strip()
                pub = str(res.get("published_date") or "").strip()
                content = re.sub(r"\s+", " ", str(res.get("content") or "")).strip()[:620]
                date_tag = f" [{pub}]" if pub else ""
                lines.append(f"- {title}{date_tag} | {url}\n  {content}")
            if lines:
                sections.append(f"[{tag}]\n" + "\n".join(lines))

    return "\n\n".join(sections)


def pi_default_model() -> str:
    cfg = load_json(PI_SETTINGS)
    if isinstance(cfg, dict):
        prov = cfg.get("defaultProvider")
        model = cfg.get("defaultModel")
        if isinstance(prov, str) and isinstance(model, str) and prov and model:
            return f"{prov}/{model}"
        if isinstance(model, str) and model:
            return model
    return "pi default"


def find_pi() -> tuple[Optional[str], dict[str, str]]:
    """Locate `pi` even when plasmashell runs with a minimal PATH."""
    env = dict(os.environ)
    extra_dirs: list[str] = []

    nvm_bins = sorted(glob.glob(str(HOME / ".nvm/versions/node/*/bin")), reverse=True)
    extra_dirs.extend(nvm_bins)
    for cand in (
        HOME / ".local/bin",
        HOME / ".local/share/pnpm",
        HOME / ".npm-global/bin",
        Path("/usr/local/bin"),
        Path("/usr/bin"),
    ):
        if cand.is_dir():
            extra_dirs.append(str(cand))

    current_path = env.get("PATH", "")
    parts = [p for p in current_path.split(os.pathsep) if p]
    for d in extra_dirs:
        if d not in parts:
            parts.append(d)
    env["PATH"] = os.pathsep.join(parts)

    pi_bin = shutil.which("pi", path=env["PATH"])
    if pi_bin:
        bin_dir = str(Path(pi_bin).resolve().parent)
        env["PATH"] = bin_dir + os.pathsep + env["PATH"]
    return pi_bin, env


def _fmt_duration(seconds: float) -> str:
    sec = max(0, int(round(seconds)))
    days = sec // 86400
    hours = (sec % 86400) // 3600
    mins = (sec % 3600) // 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%b %-d")


def compute_live_projections(usage_doc: Any) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Compute live next-limit projections & quick highlights from Oasis's usage cache."""
    now = time.time()
    projections: list[dict[str, Any]] = []
    providers = usage_doc.get("providers", []) if isinstance(usage_doc, dict) else []

    active_count = 0
    total_resets = 0
    soonest_reset: Optional[tuple[int, str]] = None
    soonest_sub: Optional[tuple[int, str]] = None

    for p in providers:
        if not isinstance(p, dict) or p.get("status") != "ok":
            continue
        windows = p.get("windows") or []
        if not windows:
            continue
        active_count += 1
        name = str(p.get("name") or p.get("id") or "Provider")

        resets = p.get("resets") or []
        avail_resets = sum(
            int(r.get("count") or 0)
            for r in resets
            if isinstance(r, dict)
            and int(r.get("count") or 0) > 0
            and (not r.get("expires_at") or float(r["expires_at"]) > now)
        )
        total_resets += avail_resets

        sub = p.get("subscription") if isinstance(p.get("subscription"), dict) else None
        sub_label: Optional[str] = None
        if sub:
            ends_at = sub.get("ends_at")
            if isinstance(ends_at, (int, float)) and ends_at > now:
                days_left = max(1, int((ends_at - now) // 86400))
                verb = "ends" if sub.get("renews") is False else "renews"
                sub_label = f"{days_left}d ({verb} {_fmt_date(int(ends_at))})"
                if soonest_sub is None or ends_at < soonest_sub[0]:
                    soonest_sub = (int(ends_at), f"{name} · {days_left}d")

        best_w = max(
            windows,
            key=lambda w: (float(w.get("used") or 0), -(int(w.get("period") or 10**9))),
        )
        used = round(float(best_w.get("used") or 0), 1)
        win_short = str(best_w.get("short") or best_w.get("label") or "win")
        resets_at = best_w.get("resets_at")
        period = best_w.get("period")

        if isinstance(resets_at, (int, float)) and resets_at > now:
            if soonest_reset is None or resets_at < soonest_reset[0]:
                soonest_reset = (int(resets_at), f"{name} {win_short} in {_fmt_duration(resets_at - now)}")

        status = "ok"
        if used >= 100:
            status = "critical"
            if isinstance(resets_at, (int, float)) and resets_at > now:
                proj = f"At limit · resets in {_fmt_duration(resets_at - now)}"
            else:
                proj = "At limit · reset available"
            if avail_resets > 0:
                proj += f" ({avail_resets} reset pass ready)"
        elif isinstance(resets_at, (int, float)) and resets_at > now and isinstance(period, (int, float)) and period > 0:
            to_reset = resets_at - now
            elapsed = period - to_reset
            if elapsed >= max(300, period * 0.12) and used > 0:
                rate = used / elapsed
                eta = (100.0 - used) / rate if rate > 0 else float("inf")
                projected = used * period / elapsed
                if projected >= 100 and eta < to_reset:
                    status = "warn" if eta > 7200 else "critical"
                    proj = f"Hits 100% in ~{_fmt_duration(eta)} (resets in {_fmt_duration(to_reset)})"
                else:
                    status = "warn" if projected >= 85 else "ok"
                    proj = f"On pace for ~{int(round(projected))}% by reset ({_fmt_duration(to_reset)})"
            else:
                proj = f"{int(round(used))}% used · resets in {_fmt_duration(to_reset)}"
        elif used <= 0.1:
            status = "ready"
            proj = "Idle / full capacity ready"
        else:
            proj = f"{int(round(used))}% used"

        projections.append({
            "provider": name,
            "window": win_short,
            "current_used": used,
            "projection": proj,
            "subscription_left": sub_label,
            "reset_passes": avail_resets,
            "status": status,
        })

    highlights = [
        {"label": "Active CLIs", "value": str(active_count), "note": "Live usage tracked"},
        {
            "label": "Reset Passes",
            "value": str(total_resets),
            "note": "Ready to use now" if total_resets > 0 else "None held",
        },
        {
            "label": "Next Reset",
            "value": _fmt_duration(soonest_reset[0] - now) if soonest_reset else "—",
            "note": soonest_reset[1] if soonest_reset else "No active timer",
        },
        {
            "label": "Next Sub End",
            "value": soonest_sub[1].split(" · ")[1] if soonest_sub else "—",
            "note": soonest_sub[1].split(" · ")[0] if soonest_sub else "Not reported",
        },
    ]
    return projections, highlights


def build_prompt(
    live_projections: list[dict[str, Any]],
    dossier: str,
    now_dt: datetime,
) -> str:
    today = now_dt.strftime("%Y-%m-%d")
    month_year = now_dt.strftime("%B %Y")
    live_ctx = json.dumps(live_projections, ensure_ascii=False)

    dossier_block = (
        f"### PRE-FETCHED TAVILY ADVANCED EVIDENCE DOSSIER (gathered {today})\n{dossier}"
        if dossier
        else "No pre-fetched dossier; run thorough `tavily_search` (`search_depth: \"advanced\"`) and `tavily_extract` calls across all tracks."
    )

    return f"""You are the Oasis AI Intel senior research analyst. Today is **{today} ({month_year})**.
Scope: **WORLDWIDE (ALL COUNTRIES & REGIONS)** — do NOT bias toward or restrict to any single country.

Below is a fresh multi-query Tavily `advanced` evidence dossier gathered right now.
You ALSO have `tavily_search` and `tavily_extract` tools available — USE THEM (2–5 targeted follow-ups/extractions) whenever a high-value global/regional deal, country-specific pricing promo, or benchmark figure is truncated in a snippet.

{dossier_block}

---

### STRICT SYNTHESIS & GROUNDING REQUIREMENTS

#### 1. `deals` — Worldwide Discounts, Free Months, Regional Promos & Cancel/Retention Offers (ALL COUNTRIES)
Extract a **balanced, unbiased, worldwide** catalog of verified active deals across ALL supported subscriptions ({", ".join(PROVIDERS_TRACKED)}) and ALL regions (Global, US, EU/UK, India, MENA/Saudi/UAE, China, SEA, LatAm, etc.):
- **Do NOT skew to one country.** Cover diverse regions AND all deal types:
  a) **Global & Seasonal / Black Friday / First-Month Intro Offers** (e.g. Google AI Pro/Antigravity Black Friday 50% off & $4.99/mo intro promos, Qwen Token Plan Lite $3/Essential $5, Cursor annual savings, OpenCode Go referral credit, SuperGrok free trials).
  b) **Country-Specific & Regional Deals** — clearly specify the EXACT country/region and local currency (e.g. India carrier/student/ChatGPT Go offers, US/Canada student & Black Friday offers, China vs International Qwen Coding Plan ¥39.90 vs $50, MENA/Saudi/UAE student or localized intro promos, EU/UK regional deals) — ONLY from **trusted sources** (official provider pages/blogs, reputable tech press, Google AI Developers Forum, verified community reports).
  c) **Cancel-Flow / Retention Offers** — explicitly include verified cancel-flow retention deals (e.g. ChatGPT Plus 1 free month / 50% off 3mo on cancel, Claude Pro targeted 50% off 2mo on cancel, Cursor Ultra 50% off on cancel), clearly tagged `audience: "Canceling / retention"` with account-eligibility caveat.
- Aim for **8–12 distinct, high-signal, verified deals** spanning multiple providers, countries, and audience types (`New users`, `Canceling / retention`, `Students`, `OSS maintainers`, `All subscribers`).
- Reject expired promos, coupon-farm spam, and gray-market resellers. Every deal MUST have a real trusted `source_url`.

#### 2. `model_tiers` — Grounded `main_agent` & `subagent` Rankings (Diamond / Gold / Silver) WITH COST PER TASK
Rank strictly from the grounded {month_year} benchmarks (`SWE-bench Pro`, `Terminal-Bench 4.0/4.1`, `Vals Index`, `AA Intelligence Index`) and real task-cost data in the dossier:
- **`main_agent` (4–6 models, Diamond → Gold → Silver):**
  - **Diamond MUST be the genuine apex frontier coding leaders** by SWE-bench Pro + Terminal-Bench 4.0/4.1 — verify **Claude Fable 5.2** (e.g. 93% SWE-bench Pro, 65.8% TB-4.0/4.1) and **Claude Opus 5.5** (e.g. 89.9% SWE-bench Pro, 65.8% TB-4.0/4.1). NEVER place a weaker-scoring model in Main Diamond above Fable 5.2 / Opus 5.5!
  - **Gold**: Near-frontier contenders (e.g. **Claude Sonnet 5.5**, **Gemini 4 Neon**, **GPT-6 Astra**).
  - **Silver**: Cost-effective workhorse fallback (e.g. **GPT-6.1 Sol**).
- **`subagent` (4–6 models, Diamond → Gold → Silver):**
  - Evaluate strictly on **cost-per-task vs agentic coding capability & speed** — explicitly evaluate **GPT-6.1 Terra** (`$0.26/AA task`, `$1/$5/1M`, `$0.05` cached), **Claude Sonnet 5.5**, **Gemini 4 Hydrogen / Carbon**, **DeepSeek V4.1 Flash**, **Claude Haiku 5.5**.
  - **Diamond Subagent** = top-tier capability-per-dollar fast agentic workers (NEVER weak nano models like `GPT-6 Luna`!).
- **MANDATORY `cost_per_task` FORMAT on EVERY model:**
  - MUST include an explicit **per-task cost** AND token rates (e.g. `"$0.26/AA task at high · $1/$5/1M"` or `"~$0.42/task est · $8/$40/1M"`).

#### 3. `limit_advice` & `crucial_metrics`
User's live Oasis usage projections (no secrets):
{live_ctx}
- `limit_advice`: 1 sharp, actionable sentence for the user's current limits/resets.
- `crucial_metrics.best_value_stack`: 1 concrete sentence pairing the top Main Agent + Subagent from your grounded table.
- `crucial_metrics.policy_alerts`: 4–6 verified {month_year} quota/price/deprecation/free-tier changes grounded in the dossier.

Output ONLY a single valid JSON object (no prose or markdown outside the JSON):
{{
  "deals": [
    {{
      "provider": "Provider",
      "offer": "Concise offer & price",
      "audience": "New users | Canceling / retention | Students | OSS maintainers | All subscribers",
      "countries": "Exact country/countries (e.g. Global, US, India, China, EU/UK, Saudi Arabia, 140+ countries)",
      "details": "Verified terms, renewal price, caveats (<=24 words).",
      "verified_date": "{today}",
      "source_url": "https://..."
    }}
  ],
  "limit_advice": "One concise sentence.",
  "model_tiers": {{
    "main_agent": [
      {{
        "tier": "Diamond",
        "model": "Model Name",
        "provider": "Provider",
        "cost_per_task": "~$0.42/task est · $8/$40/1M",
        "benchmark": "SWE-Pro 93% · TB-4.1 65.8%",
        "why": "Grounded rationale (<=16 words).",
        "source_url": "https://..."
      }}
    ],
    "subagent": [
      {{
        "tier": "Diamond",
        "model": "Model Name",
        "provider": "Provider",
        "cost_per_task": "$0.26/AA task at high · $1/$5/1M",
        "benchmark": "AA 42.4 at high, ~37s · Vals TB-4.0 49.5%",
        "why": "Grounded rationale (<=16 words).",
        "source_url": "https://..."
      }}
    ]
  }},
  "crucial_metrics": {{
    "best_value_stack": "Grounded 1-sentence Main + Subagent combo.",
    "policy_alerts": [
      "Verified {month_year} alert (<=20 words)."
    ]
  }},
  "sources": [
    {{ "title": "Short authoritative title", "url": "https://..." }}
  ]
}}
"""


def extract_json(text: str) -> Optional[dict[str, Any]]:
    text = text.strip()
    if not text:
        return None
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates = fenced + [text]
    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first : last + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None


def merge_report(
    raw: Optional[dict[str, Any]],
    live_projections: list[dict[str, Any]],
    highlights: list[dict[str, str]],
    model_used: str,
    error: Optional[str] = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "version": 4,
        "has_scan": bool(raw and raw.get("generated_at")),
        "generated_at": raw.get("generated_at") if isinstance(raw, dict) else None,
        "model_used": (raw.get("model_used") if isinstance(raw, dict) and raw.get("model_used") else model_used),
        "deals": raw.get("deals", []) if isinstance(raw, dict) and isinstance(raw.get("deals"), list) else [],
        "limit_advice": raw.get("limit_advice", "") if isinstance(raw, dict) and isinstance(raw.get("limit_advice"), str) else "",
        "limit_projections": live_projections,
        "model_tiers": {
            "main_agent": [],
            "subagent": [],
        },
        "crucial_metrics": {
            "best_value_stack": "",
            "policy_alerts": [],
            "highlights": highlights,
        },
        "sources": raw.get("sources", []) if isinstance(raw, dict) and isinstance(raw.get("sources"), list) else [],
        "error": error,
    }
    if isinstance(raw, dict):
        tiers = raw.get("model_tiers")
        if isinstance(tiers, dict):
            if isinstance(tiers.get("main_agent"), list):
                report["model_tiers"]["main_agent"] = tiers["main_agent"]
            if isinstance(tiers.get("subagent"), list):
                report["model_tiers"]["subagent"] = tiers["subagent"]
        metrics = raw.get("crucial_metrics")
        if isinstance(metrics, dict):
            if isinstance(metrics.get("best_value_stack"), str):
                report["crucial_metrics"]["best_value_stack"] = metrics["best_value_stack"]
            if isinstance(metrics.get("policy_alerts"), list):
                report["crucial_metrics"]["policy_alerts"] = [
                    str(x) for x in metrics["policy_alerts"] if isinstance(x, str) and x.strip()
                ]
    return report


def cmd_read() -> dict[str, Any]:
    usage = load_json(USAGE_CACHE)
    projections, highlights = compute_live_projections(usage)
    cached = load_json(INTEL_CACHE)
    return merge_report(
        cached if isinstance(cached, dict) else None,
        projections,
        highlights,
        pi_default_model(),
    )


def cmd_scan(timeout: int) -> dict[str, Any]:
    usage = load_json(USAGE_CACHE)
    projections, highlights = compute_live_projections(usage)
    cached = load_json(INTEL_CACHE)
    model_used = pi_default_model()

    pi_bin, env = find_pi()
    if not pi_bin:
        return merge_report(
            cached if isinstance(cached, dict) else None,
            projections,
            highlights,
            model_used,
            error="`pi` CLI not found in PATH or ~/.nvm.",
        )

    now_dt = datetime.now(timezone.utc)
    dossier = gather_tavily_dossier(now_dt)
    prompt = build_prompt(projections, dossier, now_dt)

    cmd = [
        pi_bin,
        "--print",
        "--no-session",
        "--no-builtin-tools",
        "--no-lens",
        "--no-lsp",
        "--mode",
        "text",
    ]
    if MCP_CONFIG.is_file():
        cmd += ["--mcp-config", str(MCP_CONFIG)]
    cmd += ["--", prompt]

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            cwd=str(HOME),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return merge_report(
            cached if isinstance(cached, dict) else None,
            projections,
            highlights,
            model_used,
            error=f"pi intel scan timed out after {timeout}s.",
        )
    except OSError as exc:
        return merge_report(
            cached if isinstance(cached, dict) else None,
            projections,
            highlights,
            model_used,
            error=f"failed to run pi: {exc}",
        )

    parsed = extract_json(proc.stdout or "")
    if parsed is None:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = detail[-1][:180] if detail else f"pi exited with code {proc.returncode}"
        return merge_report(
            cached if isinstance(cached, dict) else None,
            projections,
            highlights,
            model_used,
            error=f"could not parse pi output ({msg})",
        )

    parsed["generated_at"] = int(time.time())
    parsed["model_used"] = model_used
    save_json_atomic(INTEL_CACHE, parsed)
    return merge_report(parsed, projections, highlights, model_used)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="oasis-intel")
    parser.add_argument("command", nargs="?", default="read", choices=("read", "scan"))
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    result = cmd_scan(args.timeout) if args.command == "scan" else cmd_read()
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
