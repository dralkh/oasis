.pragma library

var META = {
    codex:       { name: "Codex",       short: "Cx", color: "#10A37F" },
    claude:      { name: "Claude",      short: "Cl", color: "#D97757" },
    antigravity: { name: "Antigravity", short: "Ag", color: "#4285F4" },
    opencodego:  { name: "OpenCode Go", short: "Go", color: "#6E56CF" },
    qwen:        { name: "Qwen",        short: "Qw", color: "#615CED" },
    muse:        { name: "Muse Code",   short: "Mu", color: "#0866FF" },
    kimi:        { name: "Kimi Code",   short: "Ki", color: "#EC4899" },
    grok:        { name: "Grok",        short: "Gr", color: "#9CA3AF" },
    cursor:      { name: "Cursor",      short: "Cu", color: "#8B5CF6" }
};

function meta(id) {
    return META[id] || { name: id, short: "?", color: "#9CA3AF" };
}

// The helper emits epoch seconds; QML uses milliseconds.
function toMs(value) {
    var number = Number(value);
    if (!isFinite(number) || number <= 0) return 0;
    return number < 100000000000 ? number * 1000 : number;
}

function resetMs(window) {
    return window ? toMs(window.resets_at) : 0;
}

function round(value) {
    var number = Number(value);
    return isFinite(number) ? Math.round(number) : 0;
}

function clamp(value) {
    var number = Number(value);
    if (!isFinite(number)) return 0;
    return Math.max(0, Math.min(100, number));
}

function pad(value) {
    return value < 10 ? "0" + value : String(value);
}

// "2d 4h", "4h 12m", "37m"
function countdown(ms, now) {
    var seconds = Math.round((ms - now) / 1000);
    if (!isFinite(seconds) || seconds <= 0) return "";
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) return days + "d " + hours + "h";
    if (hours > 0) return hours + "h " + minutes + "m";
    return minutes + "m";
}

function percent(window) {
    if (!window) return "";
    return (window.estimated ? "~" : "") + round(window.used) + "%";
}

// "↑2h", "ready", "✓" — compact popup chip.
function resetShort(window, now) {
    if (!window) return "";
    if (isIdle(window)) return "ready";
    var at = resetMs(window);
    if (!at) return "";
    if (at <= now) return "✓";
    var left = countdownShort(at, now);
    return left.length > 0 ? "↑" + left : "✓";
}

// "2h", "6d", "37m" — fits a 30px vertical panel cell.
function countdownShort(ms, now) {
    var seconds = Math.round((ms - now) / 1000);
    if (!isFinite(seconds) || seconds <= 0) return "";
    var days = Math.floor(seconds / 86400);
    var hours = Math.floor((seconds % 86400) / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) return days + "d";
    if (hours > 0) return hours + "h";
    return minutes + "m";
}

// "Thu 09:46"
function clock(ms) {
    if (!ms) return "";
    var date = new Date(ms);
    var days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    return days[date.getDay()] + " " + pad(date.getHours()) + ":" + pad(date.getMinutes());
}

// "Dec 12"
function shortMonthDay(ms) {
    if (!ms) return "";
    var date = new Date(ms);
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return months[date.getMonth()] + " " + date.getDate();
}

// "Dec 12, 23:05"
function fullDateTime(ms) {
    if (!ms) return "";
    var date = new Date(ms);
    return shortMonthDay(ms) + ", " + pad(date.getHours()) + ":" + pad(date.getMinutes());
}

// Summarize available usage-limit reset credits/grants for a provider.
// Returns null when none are active so the UI stays uncluttered.
function resetCreditsSummary(provider, now) {
    if (!provider || !provider.resets || !provider.resets.length) return null;
    now = now || Date.now();
    var items = [];
    var total = 0;
    for (var i = 0; i < provider.resets.length; i++) {
        var item = provider.resets[i];
        if (!item) continue;
        var count = Math.floor(Number(item.count) || 0);
        if (count <= 0) continue;
        var expMs = toMs(item.expires_at);
        if (expMs > 0 && expMs <= now) continue;
        items.push({
            count: count,
            expiresMs: expMs,
            label: item.label ? String(item.label) : "Usage reset"
        });
        total += count;
    }
    if (total <= 0) return null;
    items.sort(function(a, b) {
        if (a.expiresMs === b.expiresMs) return 0;
        if (!a.expiresMs) return 1;
        if (!b.expiresMs) return -1;
        return a.expiresMs - b.expiresMs;
    });
    var nextExpiryMs = items[0].expiresMs || 0;
    var distinctDates = {};
    var distinctCount = 0;
    for (var j = 0; j < items.length; j++) {
        if (!items[j].expiresMs) continue;
        var tag = shortMonthDay(items[j].expiresMs);
        if (!distinctDates[tag]) {
            distinctDates[tag] = true;
            distinctCount++;
        }
    }
    return {
        total: total,
        items: items,
        nextExpiryMs: nextExpiryMs,
        multipleExpiries: distinctCount > 1,
        expiringSoon: nextExpiryMs > 0 && (nextExpiryMs - now) <= 48 * 3600 * 1000
    };
}

// Compact badge: "↻ 1 reset · Dec 12", "↻ 2 resets · 1st Dec 12", "↻ 1 reset · 14h"
function resetBadgeText(provider, now) {
    var summary = resetCreditsSummary(provider, now);
    if (!summary) return "";
    now = now || Date.now();
    var text = "↻ " + summary.total + (summary.total === 1 ? " reset" : " resets");
    if (summary.nextExpiryMs > 0) {
        var leftMs = summary.nextExpiryMs - now;
        var exp = leftMs < 24 * 3600 * 1000
            ? countdownShort(summary.nextExpiryMs, now)
            : shortMonthDay(summary.nextExpiryMs);
        if (exp.length > 0) {
            text += " · " + (summary.multipleExpiries ? "1st " : "") + exp;
        }
    }
    return text;
}

function resetBadgeTooltip(provider, now) {
    var summary = resetCreditsSummary(provider, now);
    if (!summary) return "";
    now = now || Date.now();
    var lines = [
        summary.total + (summary.total === 1 ? " usage reset available" : " usage resets available")
    ];
    for (var i = 0; i < summary.items.length; i++) {
        var item = summary.items[i];
        var prefix = item.count > 1 ? item.count + "× " : "";
        var line = "• " + prefix + item.label;
        if (item.expiresMs > 0) {
            var left = countdown(item.expiresMs, now);
            line += " — expires " + fullDateTime(item.expiresMs) + (left ? " (" + left + ")" : "");
        }
        lines.push(line);
    }
    return lines.join("\n");
}

// Compact subscription remaining label for a provider header, e.g.:
// "22d left" or "15d". Returns "" when no end date is available.
function subscriptionText(provider, now) {
    var sub = provider && provider.subscription ? provider.subscription : null;
    if (!sub) return "";
    now = now || Date.now();
    var endMs = toMs(sub.ends_at);
    if (endMs > now) {
        var left = countdownShort(endMs, now);
        if (left.length > 0) {
            return sub.renews === false ? left + " left" : left;
        }
    }
    return "";
}

function subscriptionTooltip(provider, now) {
    var sub = provider && provider.subscription ? provider.subscription : null;
    if (!sub) return "";
    now = now || Date.now();
    var endMs = toMs(sub.ends_at);
    if (endMs > now) {
        var verb = sub.renews === false ? "Subscription ends " : "Period renews ";
        var left = countdown(endMs, now);
        return verb + fullDateTime(endMs) + (left ? " (" + left + ")" : "");
    }
    return "";
}

function tierColor(tier) {
    var t = String(tier || "").toLowerCase();
    if (t === "diamond") return "#38BDF8";
    if (t === "gold") return "#F59E0B";
    if (t === "silver") return "#94A3B8";
    return "#9CA3AF";
}

function tierGlyph(tier) {
    var t = String(tier || "").toLowerCase();
    if (t === "diamond") return "◆";
    if (t === "gold") return "★";
    if (t === "silver") return "●";
    return "•";
}

function shortModelName(name) {
    var s = String(name || "").trim();
    s = s.replace(/^Claude\s+/i, "");
    s = s.replace(/^Gemini\s+4\s+/i, "");
    s = s.replace(/^Gemini\s+/i, "");
    s = s.replace(/^GPT-6\.1\s+/i, "6.1 ");
    s = s.replace(/^GPT-6\s+/i, "6 ");
    s = s.replace(/^DeepSeek\s+/i, "DS ");
    s = s.replace(/\s+Flash$/i, "");
    return s;
}

// Compute an apples-to-apples standardized coding-agent task cost ($ USD) from
// verified "$in/$out/1M" rates (assuming 100k input @ 80% cache hit + 6k output).
function parseStdTaskCost(entry) {
    if (!entry) return null;
    if (typeof entry.cost_num === "number" && isFinite(entry.cost_num) && entry.cost_num > 0) {
        return entry.cost_num;
    }
    var text = String(entry.cost_per_task || "");
    var rateMatch = text.match(/\$([0-9]+(?:\.[0-9]+)?)\s*\/\s*\$([0-9]+(?:\.[0-9]+)?)/);
    if (rateMatch) {
        var inp = Number(rateMatch[1]);
        var out = Number(rateMatch[2]);
        var cachedMatch = text.match(/\$([0-9]+(?:\.[0-9]+)?)(?:\/1M)?\s*cached|cached\s*\$([0-9]+(?:\.[0-9]+)?)/i);
        var cached = cachedMatch ? Number(cachedMatch[1] || cachedMatch[2]) : inp * 0.1;
        if (isFinite(inp) && isFinite(out) && (inp > 0 || out > 0)) {
            return 0.02 * inp + 0.08 * cached + 0.006 * out;
        }
    }
    var taskMatch = text.match(/\$([0-9]+(?:\.[0-9]+)?)\s*\/\s*(?:AA\s+|idx\s+|Toloka[^·]*?)?task/i);
    if (taskMatch) {
        var val = Number(taskMatch[1]);
        if (isFinite(val) && val > 0) return val;
    }
    return null;
}

// Extract a comparable agentic capability score (roughly 25..75 scale) from
// benchmark citations so models plot accurately without mixing incompatible scales.
function parseCapabilityScore(entry) {
    if (!entry) return null;
    if (typeof entry.score_num === "number" && isFinite(entry.score_num) && entry.score_num > 0) {
        return entry.score_num;
    }
    var b = String(entry.benchmark || "");
    var m;
    // Prefer Terminal-Bench 4.1 / 4.0
    m = b.match(/(?:TB-?4\.1|Terminal-Bench\s*4\.1)[^0-9]{0,10}([0-9]+(?:\.[0-9]+)?)/i);
    if (!m) m = b.match(/(?:TB-?4\.0|Terminal-Bench\s*4\.0)[^0-9]{0,10}([0-9]+(?:\.[0-9]+)?)/i);
    var score = m ? Number(m[1]) : null;
    if (score === null) {
        m = b.match(/(?:AA(?:\s+Intelligence\s+Index)?)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)/i);
        if (m) score = Number(m[1]);
    }
    if (score === null) {
        m = b.match(/([0-9]+(?:\.[0-9]+)?)\s*%/);
        if (m) score = Number(m[1]);
    }
    if (score === null || !isFinite(score)) {
        var t = String(entry.tier || "").toLowerCase();
        score = t === "diamond" ? 66 : (t === "gold" ? 56 : 42);
    }
    // Slight tie-break bump when SWE-bench Pro >= 90% so Fable 5.2 (93%) sits above Opus 5.5 (89.9%)
    var swe = b.match(/SWE-?Pro[^0-9]{0,8}([0-9]+(?:\.[0-9]+)?)/i);
    if (swe && Number(swe[1]) >= 91) {
        score += 2.2;
    }
    return Math.round(score * 10) / 10;
}

function fmtUsdShort(val) {
    if (!isFinite(val) || val <= 0) return "";
    if (val < 0.01) return "$" + val.toFixed(3);
    if (val < 0.1) return "$" + val.toFixed(2);
    if (val < 1) return "$" + val.toFixed(2);
    return "$" + val.toFixed(1);
}

// Build normalized scatter + Pareto frontier data for TierGraph.qml.
// filterMode: "all" | "main" | "sub"
function buildTierChartData(mainModels, subModels, filterMode) {
    mainModels = mainModels || [];
    subModels = subModels || [];
    filterMode = filterMode || "all";

    var raw = [];
    var seenKeys = {};

    function addList(list, role) {
        for (var i = 0; i < list.length; i++) {
            var e = list[i];
            if (!e || !e.model) continue;
            var cost = parseStdTaskCost(e);
            var score = parseCapabilityScore(e);
            if (cost === null || score === null) continue;
            var key = String(e.model).toLowerCase();
            if (filterMode === "all" && seenKeys[key] !== undefined) {
                // Model appears in both Main & Subagent: mark as "both" so no duplicate dot collides.
                raw[seenKeys[key]].role = "both";
                continue;
            }
            seenKeys[key] = raw.length;
            raw.push({
                model: String(e.model),
                shortName: shortModelName(e.model),
                provider: String(e.provider || ""),
                role: role,
                tier: String(e.tier || ""),
                cost: cost,
                costLabel: fmtUsdShort(cost),
                costRaw: String(e.cost_per_task || ""),
                score: score,
                benchmark: String(e.benchmark || ""),
                why: String(e.why || ""),
                sourceUrl: String(e.source_url || "")
            });
        }
    }

    if (filterMode === "all" || filterMode === "main") addList(mainModels, "main");
    if (filterMode === "all" || filterMode === "sub") addList(subModels, "sub");

    if (raw.length === 0) {
        return { points: [], frontier: [], xTicks: [], yTicks: [], yMin: 20, yMax: 80 };
    }

    var minCost = raw[0].cost;
    var maxCost = raw[0].cost;
    var minScore = raw[0].score;
    var maxScore = raw[0].score;
    for (var i = 1; i < raw.length; i++) {
        if (raw[i].cost < minCost) minCost = raw[i].cost;
        if (raw[i].cost > maxCost) maxCost = raw[i].cost;
        if (raw[i].score < minScore) minScore = raw[i].score;
        if (raw[i].score > maxScore) maxScore = raw[i].score;
    }

    // Pad log-cost domain & linear score domain cleanly.
    var logMin = Math.log10(Math.max(0.003, minCost * 0.62));
    var logMax = Math.log10(Math.max(minCost * 2, maxCost * 1.55));
    var yMin = Math.max(0, Math.floor((minScore - 6) / 10) * 10);
    var yMax = Math.min(100, Math.ceil((maxScore + 6) / 10) * 10);
    if (yMax - yMin < 20) yMax = Math.min(100, yMin + 20);

    // Compute normalized coordinates (0..1).
    for (var j = 0; j < raw.length; j++) {
        var p = raw[j];
        var lx = Math.log10(Math.max(0.001, p.cost));
        p.xNorm = logMax > logMin ? (lx - logMin) / (logMax - logMin) : 0.5;
        p.yNorm = yMax > yMin ? (p.score - yMin) / (yMax - yMin) : 0.5;
        p.onFrontier = false;
        p.labelSide = p.xNorm > 0.72 ? "left" : "right";
        p.labelDy = 0;
    }

    // Compute Pareto frontier: sort by cost asc, score desc; keep strictly increasing score.
    var byCost = raw.slice().sort(function(a, b) {
        if (Math.abs(a.cost - b.cost) < 1e-6) return b.score - a.score;
        return a.cost - b.cost;
    });
    var frontier = [];
    var bestSoFar = -1e9;
    for (var k = 0; k < byCost.length; k++) {
        if (byCost[k].score > bestSoFar + 0.05) {
            byCost[k].onFrontier = true;
            frontier.push(byCost[k]);
            bestSoFar = byCost[k].score;
        }
    }

    // Simple label collision nudging so adjacent points don't overwrite each other.
    var sortedY = raw.slice().sort(function(a, b) { return b.yNorm - a.yNorm; });
    for (var a = 0; a < sortedY.length; a++) {
        for (var b = 0; b < a; b++) {
            var pa = sortedY[a];
            var pb = sortedY[b];
            var dx = Math.abs(pa.xNorm - pb.xNorm);
            var dy = Math.abs((pa.yNorm - pa.labelDy / 140) - (pb.yNorm - pb.labelDy / 140));
            if (dx < 0.22 && dy < 0.12) {
                pa.labelDy += 11;
                if (pa.xNorm > 0.35 && pa.labelSide === pb.labelSide) {
                    pa.labelSide = pb.labelSide === "right" ? "left" : "right";
                    pa.labelDy = 0;
                }
            }
        }
    }

    var candidateTicks = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10];
    var xTicks = [];
    for (var t = 0; t < candidateTicks.length; t++) {
        var tv = candidateTicks[t];
        var lt = Math.log10(tv);
        if (lt >= logMin + 0.02 && lt <= logMax - 0.02) {
            xTicks.push({
                xNorm: (lt - logMin) / (logMax - logMin),
                label: fmtUsdShort(tv)
            });
        }
    }

    var yStep = (yMax - yMin) <= 30 ? 10 : 20;
    var yTicks = [];
    for (var yv = yMin; yv <= yMax; yv += yStep) {
        yTicks.push({
            yNorm: (yv - yMin) / (yMax - yMin),
            label: String(yv)
        });
    }

    return {
        points: raw,
        frontier: frontier,
        xTicks: xTicks,
        yTicks: yTicks,
        yMin: yMin,
        yMax: yMax
    };
}

function isIdle(window) {
    return !!window && Number(window.used) <= 0.01 && !resetMs(window);
}

function resetText(window, now) {
    var at = resetMs(window);
    if (!at) {
        return isIdle(window) ? "ready" : "";
    }
    if (at <= now) return "reset";
    return "in " + countdown(at, now);
}

function resetDetail(window, now) {
    var at = resetMs(window);
    if (!at) return "";
    if (at <= now) return "reset available";
    return clock(at) + " (" + countdown(at, now) + ")";
}

function primaryWindows(provider) {
    if (!provider || !provider.windows) return [];
    var list = [];
    for (var i = 0; i < provider.windows.length; i++) {
        if (provider.windows[i].primary !== false) list.push(provider.windows[i]);
    }
    return list;
}

function limitKey(provider, window) {
    return String(provider.id) + ":" + String(window.id || window.short || window.label || "window");
}

function _removeProviderLimitKeys(map, providerId) {
    var prefix = String(providerId) + ":";
    for (var key in map) {
        if (key.indexOf(prefix) === 0) delete map[key];
    }
}

// Compare the latest window usage with the previous crossing state. Returns
// the state to remember and one human-readable line per newly reached limit.
// Callers establish a silent baseline on the first payload.
function limitNotificationLines(providers, previous, now) {
    previous = previous || {};
    now = now || Date.now();
    var next = {};
    for (var oldKey in previous) {
        next[oldKey] = previous[oldKey];
    }
    var lines = [];
    for (var i = 0; i < providers.length; i++) {
        var provider = providers[i];
        if (!provider) continue;
        if (provider.status === "not_configured") {
            _removeProviderLimitKeys(next, provider.id);
            continue;
        }
        // Preserve the state across transient errors so recovery at 100% does
        // not produce a duplicate notification.
        if (provider.status !== "ok") continue;
        _removeProviderLimitKeys(next, provider.id);
        var windows = provider.windows || [];
        for (var j = 0; j < windows.length; j++) {
            var window = windows[j];
            if (Number(window.used) < 100) continue;
            if (windowResetAvailable(window, now)) continue;
            var key = limitKey(provider, window);
            if (!previous[key] && !next[key]) {
                var line = meta(provider.id).name + " " + window.short + " " + percent(window);
                var reset = resetText(window, now);
                if (reset.length > 0) line += " · " + reset;
                lines.push(line);
            }
            next[key] = true;
        }
    }
    return { next: next, lines: lines };
}

// Antigravity has two display groups (Gemini and Claude), each with a 5-hour
// and weekly bucket. Pair the helper's stable ids so the popup can render one
// readable block per group instead of unrelated bars.
function antigravityGroups(provider) {
    var windows = provider && provider.windows ? provider.windows : [];
    var defs = [
        { id: "gemini", label: "Gemini" },
        { id: "claude", label: "Claude" }
    ];
    var result = [];
    for (var i = 0; i < defs.length; i++) {
        var def = defs[i];
        var five = null;
        var weekly = null;
        for (var j = 0; j < windows.length; j++) {
            if (windows[j].id === def.id + "-5h") five = windows[j];
            if (windows[j].id === def.id + "-weekly") weekly = windows[j];
        }
        if (five || weekly) {
            result.push({ label: def.label, five: five, weekly: weekly });
        }
    }
    return result;
}

// One window per period: max usage across pools that share a period
// (Antigravity can report both a Gemini and a Claude 5-hour window).
function periodSummary(provider) {
    var windows = primaryWindows(provider);
    var order = [];
    var best = {};
    for (var i = 0; i < windows.length; i++) {
        var window = windows[i];
        var key = window.period ? String(window.period) : "cycle";
        if (!(key in best)) {
            order.push(key);
            best[key] = window;
        } else if (Number(window.used) > Number(best[key].used)) {
            best[key] = window;
        }
    }
    order.sort(function(a, b) {
        var pa = best[a].period || 1e12;
        var pb = best[b].period || 1e12;
        return pa - pb;
    });
    var result = [];
    for (var j = 0; j < order.length; j++) {
        result.push(best[order[j]]);
    }
    return result;
}

function peak(provider) {
    var windows = primaryWindows(provider);
    var value = 0;
    for (var i = 0; i < windows.length; i++) {
        value = Math.max(value, Number(windows[i].used) || 0);
    }
    return value;
}

function tightest(provider) {
    var windows = primaryWindows(provider);
    var best = null;
    for (var i = 0; i < windows.length; i++) {
        var window = windows[i];
        if (best === null || Number(window.used) > Number(best.used)) best = window;
    }
    return best;
}

function headline(provider) {
    var windows = periodSummary(provider);
    if (!windows.length) return "";
    var parts = [];
    for (var i = 0; i < windows.length && i < 2; i++) {
        parts.push(round(windows[i].used));
    }
    return parts.join("/");
}

function isResetAvailable(provider, now) {
    var window = tightest(provider);
    var at = resetMs(window);
    return at > 0 && at <= now;
}

function windowResetAvailable(window, now) {
    var at = resetMs(window);
    return at > 0 && at <= now;
}

// Projected usage at reset, and whether the limit runs out first.
function pace(window, now) {
    var at = resetMs(window);
    if (!window || !window.period || !at) return "";
    var elapsed = window.period - (at - now) / 1000;
    // Ignore the first 15% of a window: projections from a few hours of a
    // weekly/monthly quota are noise and look alarming.
    if (!isFinite(elapsed) || elapsed < 300 || elapsed < window.period * 0.15) return "";
    var used = Number(window.used) || 0;
    if (used >= 100) return "at limit · resets in " + countdown(at, now);
    var rate = used / elapsed;
    if (rate <= 0) return "";
    var toReset = (at - now) / 1000;
    var eta = (100 - used) / rate;
    var projected = used * window.period / elapsed;
    if (projected >= 100 && eta < toReset) {
        return "on pace to hit 100% in " + countdown(now + eta * 1000, now);
    }
    return "on pace for " + round(projected) + "% by reset";
}

function statusText(provider) {
    if (!provider) return "";
    if (provider.status === "not_configured") return "not signed in";
    if (provider.status === "expired") return "sign-in expired";
    if (provider.status === "error") return provider.error || "unavailable";
    return "";
}

function updatedText(ts, now) {
    if (!ts) return "never";
    var seconds = Math.max(0, Math.round((now - ts) / 1000));
    if (seconds < 10) return "just now";
    if (seconds < 60) return seconds + "s ago";
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + "m ago";
    return Math.round(minutes / 60) + "h ago";
}

// Compact label for an inline account switcher pill (e.g. "dralkh@proton.me" -> "dralkh").
function shortAccountLabel(label) {
    var s = String(label || "").trim();
    if (!s) return "Account";
    var at = s.indexOf("@");
    if (at > 0 && s.length > 14) {
        s = s.substring(0, at);
    }
    if (s.length > 16) {
        s = s.substring(0, 15) + "…";
    }
    return s;
}

// Compact live usage preview badges for a saved account row.
function accountPreviewChips(account, now) {
    if (!account) return [];
    if (account.status !== "ok") return [];
    var windows = primaryWindows(account);
    if (!windows.length) return [];
    var out = [];
    // Keep at most 3 chips (5h, 1w, 1mo or Gemini/Claude).
    for (var i = 0; i < windows.length && out.length < 3; i++) {
        var w = windows[i];
        var used = Number(w.used) || 0;
        var ready = windowResetAvailable(w, now) || isIdle(w);
        out.push({
            label: w.short || w.label || "",
            text: (w.short ? w.short + " " : "") + round(used) + "%",
            used: used,
            ready: ready,
            reset: resetShort(w, now)
        });
    }
    return out;
}

function accountStatusBadge(account) {
    if (!account) return "";
    if (account.stale) return "stale";
    if (account.status === "expired") return "expired";
    if (account.status === "error") return account.error ? String(account.error) : "error";
    if (account.status === "not_configured") return "signed out";
    if (account.status === "ok" && (!account.windows || !account.windows.length)) return "ready";
    return "";
}
