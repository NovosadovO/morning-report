#!/usr/bin/env python3
"""
DeFi & RWA звіт — надсилається 2 рази на день: 07:00 і 19:00 (місцевого часу).
Джерело: DeFiLlama публічний API (без ключа).
"""

import os, json, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

try:
    import requests as _req
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
_DATA_DIR      = os.path.dirname(os.path.abspath(__file__))

LLAMA   = "https://api.llama.fi"
STABLES = "https://stablecoins.llama.fi"
YIELDS  = "https://yields.llama.fi"


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _get(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            if _HAS_REQUESTS:
                r = _req.get(url, headers={"User-Agent": "defi-report/1.0"}, timeout=25)
                r.raise_for_status()
                return r.json()
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "defi-report/1.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    return json.loads(r.read().decode())
        except Exception as e:
            print(f"GET attempt {attempt}/{retries} [{url[:60]}]: {e}")
            if attempt < retries:
                time.sleep(3 * attempt)
    return None


def send_part(text: str) -> bool:
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4090],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def esc(s): return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def fmt_b(v):
    """Форматує число: B/M/K"""
    if v is None: return "—"
    v = float(v)
    if abs(v) >= 1e9:  return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:  return f"${v/1e6:.1f}M"
    if abs(v) >= 1e3:  return f"${v/1e3:.0f}K"
    return f"${v:.2f}"

def fmt_pct(v, decimals=2):
    if v is None: return "—"
    v = float(v)
    sign = "+" if v > 0 else ""
    arrow = "📈" if v > 0 else ("📉" if v < 0 else "▪️")
    return f"{arrow} {sign}{v:.{decimals}f}%"

def bar_chart(items, value_fn, label_fn, width=12, max_items=8):
    """Генерує текстовий bar chart."""
    vals = [value_fn(x) for x in items[:max_items]]
    max_val = max(vals) if vals else 1
    lines = []
    for i, item in enumerate(items[:max_items]):
        v = vals[i]
        bar_len = int((v / max_val) * width) if max_val > 0 else 0
        bar = "█" * bar_len + "░" * (width - bar_len)
        label = label_fn(item)
        lines.append(f"{bar} {label}")
    return "\n".join(lines)


# ─── 1. ЗАГАЛЬНЕ TVL ──────────────────────────────────────────────────────────

def get_total_tvl(protocols):
    total = sum(p.get("tvl") or 0 for p in protocols if p.get("tvl"))
    # Зміни по категоріях
    cat_tvl = {}
    for p in protocols:
        cat = p.get("category", "Other")
        tvl = p.get("tvl") or 0
        cat_tvl[cat] = cat_tvl.get(cat, 0) + tvl

    top_cats = sorted(cat_tvl.items(), key=lambda x: -x[1])[:6]

    lines = [f"💎 <b>Загальний DeFi TVL: {fmt_b(total)}</b>\n"]
    lines.append("<b>TVL по категоріях:</b>")
    for cat, tvl in top_cats:
        pct = tvl / total * 100 if total else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"{bar[:10]} {esc(cat)}: {fmt_b(tvl)} ({pct:.1f}%)")

    return "\n".join(lines)


# ─── 2. ТОП-20 DEFI ПРОТОКОЛІВ ────────────────────────────────────────────────

DEFI_CATS = {
    "Liquid Staking", "Lending", "CDP", "Dexs", "Yield",
    "Derivatives", "Yield Aggregator", "Bridge", "RWA"
}

def get_top_defi(protocols):
    defi = [p for p in protocols if p.get("category") in DEFI_CATS and (p.get("tvl") or 0) > 0]
    top  = sorted(defi, key=lambda x: x.get("tvl") or 0, reverse=True)[:20]

    lines = ["🏆 <b>Топ-20 DeFi за TVL</b>\n"]
    for i, p in enumerate(top, 1):
        tvl    = p.get("tvl") or 0
        ch1d   = p.get("change_1d")
        ch7d   = p.get("change_7d")
        cat    = p.get("category", "")[:12]
        arrow  = "📈" if (ch1d or 0) > 0 else ("📉" if (ch1d or 0) < 0 else "▪️")
        ch_str = f"{'+' if (ch1d or 0)>0 else ''}{ch1d:.1f}%" if ch1d is not None else "—"
        lines.append(
            f"{i:>2}. <b>{esc(p['name'])}</b> <i>({esc(cat)})</i>\n"
            f"    TVL: {fmt_b(tvl)} {arrow} 1д: {ch_str}"
        )

    # Мінітаблиця bar chart
    lines.append("\n<b>Bar chart TVL (топ-10):</b>")
    lines.append(bar_chart(
        top[:10],
        lambda p: p.get("tvl") or 0,
        lambda p: f"{p['name'][:14]} {fmt_b(p.get('tvl'))}",
        width=10
    ))

    return "\n".join(lines)


# ─── 3. RWA РИНОК ─────────────────────────────────────────────────────────────

def get_rwa(protocols):
    rwa = [p for p in protocols if p.get("category") == "RWA" and (p.get("tvl") or 0) > 0]
    rwa = sorted(rwa, key=lambda x: x.get("tvl") or 0, reverse=True)

    total_rwa = sum(p.get("tvl") or 0 for p in rwa)
    lines = [f"🏦 <b>RWA ринок — Загальний TVL: {fmt_b(total_rwa)}</b>\n"]

    lines.append("<b>Топ-15 RWA протоколів:</b>")
    for i, p in enumerate(rwa[:15], 1):
        tvl   = p.get("tvl") or 0
        ch1d  = p.get("change_1d")
        ch7d  = p.get("change_7d")
        arrow = "📈" if (ch1d or 0) > 0 else ("📉" if (ch1d or 0) < 0 else "▪️")
        d1    = f"{'+' if (ch1d or 0)>0 else ''}{ch1d:.2f}%" if ch1d is not None else "—"
        d7    = f"{'+' if (ch7d or 0)>0 else ''}{ch7d:.1f}%" if ch7d is not None else "—"
        chains = ", ".join((p.get("chains") or [])[:2])
        lines.append(
            f"{i:>2}. <b>{esc(p['name'])}</b> [{esc(chains)}]\n"
            f"    TVL: {fmt_b(tvl)} {arrow} 1д: {d1} | 7д: {d7}"
        )

    lines.append("\n<b>Bar chart RWA (топ-10):</b>")
    lines.append(bar_chart(
        rwa[:10],
        lambda p: p.get("tvl") or 0,
        lambda p: f"{p['name'][:16]} {fmt_b(p.get('tvl'))}",
        width=10
    ))

    return "\n".join(lines)


# ─── 4. DEX ОБСЯГИ ────────────────────────────────────────────────────────────

def get_dex_volumes():
    data = _get(f"{LLAMA}/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume")
    if not data:
        return "📊 <b>DEX Обсяги</b>\n⚠️ Недоступно"

    total24h = data.get("total24h") or 0
    ch1d     = data.get("change_1d")
    ch7d     = data.get("change_7d")

    protos = sorted(
        [p for p in data.get("protocols", []) if (p.get("total24h") or 0) > 0],
        key=lambda x: x.get("total24h") or 0, reverse=True
    )[:10]

    lines = [
        f"📊 <b>DEX Обсяги (24г)</b>\n"
        f"Всього: <b>{fmt_b(total24h)}</b> {fmt_pct(ch1d)} | 7д: {fmt_pct(ch7d)}\n"
    ]
    lines.append("<b>Топ-10 DEX:</b>")
    for i, p in enumerate(protos, 1):
        vol  = p.get("total24h") or 0
        ch   = p.get("change_1d")
        ar   = "📈" if (ch or 0) > 0 else ("📉" if (ch or 0) < 0 else "▪️")
        ch_s = f"{'+' if (ch or 0)>0 else ''}{ch:.1f}%" if ch is not None else ""
        lines.append(f"{i:>2}. <b>{esc(p['name'])}</b>: {fmt_b(vol)} {ar} {ch_s}")

    lines.append("\n<b>Bar chart (топ-8):</b>")
    lines.append(bar_chart(
        protos[:8],
        lambda p: p.get("total24h") or 0,
        lambda p: f"{p['name'][:14]} {fmt_b(p.get('total24h'))}",
        width=10
    ))

    return "\n".join(lines)


# ─── 5. LENDING ───────────────────────────────────────────────────────────────

def get_lending(protocols):
    lending = [p for p in protocols if p.get("category") in ("Lending", "CDP") and (p.get("tvl") or 0) > 0]
    lending = sorted(lending, key=lambda x: x.get("tvl") or 0, reverse=True)[:10]

    total = sum(p.get("tvl") or 0 for p in lending)
    lines = [f"🏛 <b>Lending / CDP — TVL: {fmt_b(total)}</b>\n"]

    for i, p in enumerate(lending, 1):
        tvl  = p.get("tvl") or 0
        ch1d = p.get("change_1d")
        ar   = "📈" if (ch1d or 0) > 0 else ("📉" if (ch1d or 0) < 0 else "▪️")
        d1   = f"{'+' if (ch1d or 0)>0 else ''}{ch1d:.1f}%" if ch1d is not None else "—"
        lines.append(f"{i:>2}. <b>{esc(p['name'])}</b> <i>({esc(p.get('category',''))})</i>: {fmt_b(tvl)} {ar} {d1}")

    return "\n".join(lines)


# ─── 6. ТОПОВІ YIELD POOLS ────────────────────────────────────────────────────

def get_top_yields():
    data = _get(f"{YIELDS}/pools")
    if not data or not isinstance(data, dict):
        return "💰 <b>Топ Yield Pools</b>\n⚠️ Недоступно"

    pools = data.get("data", [])
    # Фільтр: APY > 5%, TVL > $1M, не ризиковані
    good = [
        p for p in pools
        if (p.get("apy") or 0) > 5
        and (p.get("tvlUsd") or 0) > 1_000_000
        and not p.get("outlier", False)
        and (p.get("stablecoin") or False)  # стейблкоіни безпечніше
    ]
    good_sorted = sorted(good, key=lambda x: x.get("apy") or 0, reverse=True)[:8]

    # Також топ за TVL (не тільки стейбли)
    all_good = [
        p for p in pools
        if (p.get("apy") or 0) > 3
        and (p.get("tvlUsd") or 0) > 5_000_000
        and not p.get("outlier", False)
    ]
    by_tvl = sorted(all_good, key=lambda x: x.get("tvlUsd") or 0, reverse=True)[:5]

    lines = ["💰 <b>Топ Yield Pools (DeFiLlama)</b>\n"]

    if good_sorted:
        lines.append("<b>Найкращі APY (стейблкоіни, TVL>$1M):</b>")
        for p in good_sorted[:6]:
            apy  = p.get("apy") or 0
            tvl  = p.get("tvlUsd") or 0
            proj = p.get("project", "")
            sym  = p.get("symbol", "")
            chain = p.get("chain", "")
            lines.append(f"  🔹 <b>{esc(sym)}</b> | {esc(proj)} [{esc(chain)}]\n"
                        f"     APY: <b>{apy:.1f}%</b> · TVL: {fmt_b(tvl)}")

    if by_tvl:
        lines.append("\n<b>Найбільші пули за TVL:</b>")
        for p in by_tvl:
            apy  = p.get("apy") or 0
            tvl  = p.get("tvlUsd") or 0
            proj = p.get("project", "")
            sym  = p.get("symbol", "")
            chain = p.get("chain", "")
            lines.append(f"  🔸 <b>{esc(sym)}</b> | {esc(proj)} [{esc(chain)}]\n"
                        f"     APY: {apy:.1f}% · TVL: <b>{fmt_b(tvl)}</b>")

    return "\n".join(lines)


# ─── 7. СТЕЙБЛКОІНИ ───────────────────────────────────────────────────────────

def get_stablecoins():
    data = _get(f"{STABLES}/stablecoins?includePrices=true")
    if not data:
        return "🪙 <b>Стейблкоіни</b>\n⚠️ Недоступно"

    assets = data.get("peggedAssets", [])
    assets = sorted(assets, key=lambda x: float((x.get("circulating") or {}).get("peggedUSD") or 0), reverse=True)

    total_mcap = sum(float((a.get("circulating") or {}).get("peggedUSD") or 0) for a in assets)

    lines = [f"🪙 <b>Стейблкоіни — Загальна капіталізація: {fmt_b(total_mcap)}</b>\n"]
    lines.append("<b>Топ-10:</b>")

    for i, a in enumerate(assets[:10], 1):
        circ     = float((a.get("circulating") or {}).get("peggedUSD") or 0)
        prev_day = float((a.get("circulatingPrevDay") or {}).get("peggedUSD") or 0)
        ch       = (circ - prev_day) / prev_day * 100 if prev_day else 0
        mech     = a.get("pegMechanism", "")[:10]
        arrow    = "📈" if ch > 0.1 else ("📉" if ch < -0.1 else "▪️")
        ch_s     = f"{'+' if ch>0 else ''}{ch:.2f}%"
        lines.append(f"{i:>2}. <b>{esc(a['symbol'])}</b> <i>({esc(mech)})</i>: {fmt_b(circ)} {arrow} {ch_s}")

    return "\n".join(lines)


# ─── 8. CHAINS TVL ────────────────────────────────────────────────────────────

def get_chains():
    data = _get(f"{LLAMA}/chains")
    if not data:
        return None

    top = sorted(data, key=lambda x: x.get("tvl") or 0, reverse=True)[:10]
    total = sum(c.get("tvl") or 0 for c in top)

    lines = ["⛓ <b>TVL по блокчейнах (топ-10):</b>"]
    lines.append(bar_chart(
        top,
        lambda c: c.get("tvl") or 0,
        lambda c: f"{c['name'][:12]} {fmt_b(c.get('tvl'))}",
        width=10, max_items=10
    ))

    return "\n".join(lines)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def _defi_dedup_check():
    """Returns (already_sent: bool, sent_data: dict, sha: str|None).
    Uses storage.py GitHub dedup — key: defi_{date}_{slot} where slot = 'am' or 'pm'."""
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from storage import _load_github, _save_github
        local = datetime.now(timezone.utc) + timedelta(hours=2)
        slot  = "am" if local.hour < 13 else "pm"
        key   = f"defi_{local.strftime('%Y-%m-%d')}_{slot}"
        data  = _load_github("defi_sent.json") or {}
        return data.get(key, False), data, key
    except Exception as e:
        print(f"defi dedup check error: {e}")
        return False, {}, None

def _defi_dedup_save(data, key):
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from storage import _save_github
        data[key] = True
        _save_github("defi_sent.json", data)
    except Exception as e:
        print(f"defi dedup save error: {e}")

def main():
    now      = datetime.now(timezone.utc)
    local    = now + timedelta(hours=2)
    time_str = local.strftime("%H:%M")
    date_str = local.strftime("%d.%m.%Y")
    period   = "🌅 Ранковий" if local.hour < 13 else "🌆 Вечірній"

    print(f"=== DeFi Report run at {now.isoformat()} ===")

    # ── GitHub dedup — захист від дублів при Railway restart ──
    already_sent, sent_data, sent_key = _defi_dedup_check()
    if already_sent:
        print(f"DeFi report already sent this slot ({sent_key}), skipping.")
        return

    protocols = _get(f"{LLAMA}/protocols")
    if not protocols:
        send_part("⚠️ DeFi звіт: не вдалось завантажити дані DeFiLlama")
        return

    SEP = "\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"

    # ── Загальний TVL ──
    total = sum(p.get("tvl") or 0 for p in protocols if p.get("tvl"))
    cat_tvl = {}
    for p in protocols:
        cat = p.get("category", "Other")
        cat_tvl[cat] = cat_tvl.get(cat, 0) + (p.get("tvl") or 0)
    top_cats = sorted(cat_tvl.items(), key=lambda x: -x[1])[:5]

    tvl_lines = [
        f"📡 <b>{period} DeFi звіт</b>  ·  {time_str} {date_str}\n",
        f"💎 <b>ЗАГАЛЬНИЙ TVL:  {fmt_b(total)}</b>\n",
    ]
    for cat, tvl in top_cats:
        pct = tvl / total * 100 if total else 0
        bar = "▓" * int(pct / 8) + "░" * (12 - int(pct / 8))
        tvl_lines.append(f"<code>{bar}</code>  {esc(cat)}: <b>{fmt_b(tvl)}</b> <i>{pct:.1f}%</i>")

    # ── Топ-10 DeFi ──
    defi = [p for p in protocols if p.get("category") in DEFI_CATS and (p.get("tvl") or 0) > 0]
    top10 = sorted(defi, key=lambda x: x.get("tvl") or 0, reverse=True)[:10]

    defi_lines = ["🏆 <b>ТОП-10 DeFi  (TVL)</b>\n"]
    for i, p in enumerate(top10, 1):
        tvl  = p.get("tvl") or 0
        ch1d = p.get("change_1d")
        ar   = "🟢" if (ch1d or 0) > 0 else ("🔴" if (ch1d or 0) < 0 else "⚪️")
        d1   = f"{'+' if (ch1d or 0)>0 else ''}{ch1d:.1f}%" if ch1d is not None else "—"
        defi_lines.append(f"{i:>2}. {ar} <b>{esc(p['name'])}</b>  <code>{fmt_b(tvl)}</code>  <i>{d1}</i>")

    # ── RWA топ-8 ──
    rwa = [p for p in protocols if p.get("category") == "RWA" and (p.get("tvl") or 0) > 0]
    rwa = sorted(rwa, key=lambda x: x.get("tvl") or 0, reverse=True)[:8]
    total_rwa = sum(p.get("tvl") or 0 for p in rwa)

    rwa_lines = [f"🏦 <b>RWA  —  {fmt_b(total_rwa)}</b>\n"]
    for i, p in enumerate(rwa, 1):
        tvl  = p.get("tvl") or 0
        ch1d = p.get("change_1d")
        ar   = "🟢" if (ch1d or 0) > 0 else ("🔴" if (ch1d or 0) < 0 else "⚪️")
        d1   = f"{'+' if (ch1d or 0)>0 else ''}{ch1d:.1f}%" if ch1d is not None else "—"
        chains = "/".join((p.get("chains") or [])[:2])
        rwa_lines.append(f"{i:>2}. {ar} <b>{esc(p['name'])}</b>  <code>{fmt_b(tvl)}</code>  <i>{d1}</i>  <i>[{esc(chains)}]</i>")

    # ── Lending топ-7 ──
    lending = [p for p in protocols if p.get("category") in ("Lending", "CDP") and (p.get("tvl") or 0) > 0]
    lending = sorted(lending, key=lambda x: x.get("tvl") or 0, reverse=True)[:7]
    total_lend = sum(p.get("tvl") or 0 for p in lending)

    lend_lines = [f"🏛 <b>LENDING  —  {fmt_b(total_lend)}</b>\n"]
    for i, p in enumerate(lending, 1):
        tvl  = p.get("tvl") or 0
        ch1d = p.get("change_1d")
        ar   = "🟢" if (ch1d or 0) > 0 else ("🔴" if (ch1d or 0) < 0 else "⚪️")
        d1   = f"{'+' if (ch1d or 0)>0 else ''}{ch1d:.1f}%" if ch1d is not None else "—"
        lend_lines.append(f"{i:>2}. {ar} <b>{esc(p['name'])}</b>  <code>{fmt_b(tvl)}</code>  <i>{d1}</i>")

    # ── Стейблкоіни ──
    stables_data = _get(f"{STABLES}/stablecoins?includePrices=true")
    stable_lines = []
    if stables_data:
        assets = sorted(stables_data.get("peggedAssets", []),
                        key=lambda x: float((x.get("circulating") or {}).get("peggedUSD") or 0), reverse=True)
        total_mcap = sum(float((a.get("circulating") or {}).get("peggedUSD") or 0) for a in assets)
        stable_lines = [f"🪙 <b>СТЕЙБЛКОІНИ  —  {fmt_b(total_mcap)}</b>\n"]
        for i, a in enumerate(assets[:6], 1):
            circ  = float((a.get("circulating") or {}).get("peggedUSD") or 0)
            prev  = float((a.get("circulatingPrevDay") or {}).get("peggedUSD") or 0)
            ch    = (circ - prev) / prev * 100 if prev else 0
            ar    = "🟢" if ch > 0.1 else ("🔴" if ch < -0.1 else "⚪️")
            ch_s  = f"{'+' if ch>0 else ''}{ch:.2f}%"
            stable_lines.append(f"{i:>2}. {ar} <b>{esc(a['symbol'])}</b>  <code>{fmt_b(circ)}</code>  <i>{ch_s}</i>")

    # ── Збираємо в один звіт ──
    report = (
        "\n".join(tvl_lines)
        + SEP
        + "\n".join(defi_lines)
        + SEP
        + "\n".join(rwa_lines)
        + SEP
        + "\n".join(lend_lines)
        + SEP
        + ("\n".join(stable_lines) if stable_lines else "")
        + f"\n\n<i>📊 DeFiLlama · Наступний о {'19:00' if local.hour < 13 else '07:00'}</i>"
    )

    # Зберігаємо ПЕРЕД надсиланням — захист від дублів
    if sent_key:
        _defi_dedup_save(sent_data, sent_key)

    # Telegram ліміт 4096 — ріжемо якщо треба
    if len(report) <= 4090:
        send_part(report)
    else:
        mid = report[:4090].rfind(SEP.strip())
        send_part(report[:mid] if mid > 0 else report[:4090])
        time.sleep(0.5)
        send_part(report[mid:] if mid > 0 else "")

    print("DeFi report sent.")


# ─── 24H DIGEST ───────────────────────────────────────────────────────────────

def _digest_dedup_check():
    """Dedup для 18:15 дайджесту. Ключ: digest_18h_{date}."""
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from storage import _load_github, _save_github
        local = datetime.now(timezone.utc) + timedelta(hours=2)
        key   = f"digest_18h_{local.strftime('%Y-%m-%d')}"
        data  = _load_github("defi_sent.json") or {}
        return data.get(key, False), data, key
    except Exception as e:
        print(f"digest dedup check error: {e}")
        return False, {}, None


def _digest_dedup_save(data, key):
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from storage import _save_github
        data[key] = True
        _save_github("defi_sent.json", data)
    except Exception as e:
        print(f"digest dedup save error: {e}")


def _gemini_digest_summary(context: str) -> str:
    """Короткий AI-висновок через Gemini (2-3 речення)."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return ""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
        prompt = (
            "На основі цих DeFi даних за останні 24 години напиши 2-3 речення українською: "
            "що найважливіше змінилось, які тренди варто відстежити, чи є ризики.\n\n"
            + context
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 200, "temperature": 0.7, "thinkingConfig": {"thinkingBudget": 0}}
        }).encode()
        from monitor import _gem_post
        resp = _gem_post(url, payload, timeout=20, tag="defi_digest", max_retries=3)
        if isinstance(resp, dict) and resp.get("candidates"):
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"].strip()
        return ""
    except Exception as e:
        print(f"Gemini digest error: {e}")
        return ""


def _make_digest_chart(gainers, losers) -> str | None:
    """Малює bar chart gainers/losers, повертає шлях до PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import tempfile

        BG     = "#0D1117"
        PANEL  = "#161B22"
        GRID   = "#1E2530"
        BORDER = "#30363D"
        TEXT   = "#E6EDF3"
        SUBTEXT = "#8B949E"

        names_g = [p["name"][:14] for p in gainers]
        vals_g  = [p.get("change_1d") or 0 for p in gainers]
        names_l = [p["name"][:14] for p in losers]
        vals_l  = [p.get("change_1d") or 0 for p in losers]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
        fig.patch.set_facecolor(BG)

        for ax in (ax1, ax2):
            ax.set_facecolor(PANEL)
            ax.tick_params(colors=TEXT, labelsize=10)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)
                spine.set_linewidth(1.2)
            ax.grid(True, axis="x", color=GRID, linewidth=0.7, alpha=0.7)

        if names_g:
            bars = ax1.barh(names_g[::-1], vals_g[::-1], color="#2EA043", height=0.6)
            ax1.set_title("Зростання 24h", color=TEXT, fontsize=13, fontweight="bold", pad=8)
            ax1.set_xlabel("Зміна %", color=SUBTEXT, fontsize=10)
            ax1.tick_params(axis="y", labelsize=10, labelcolor=TEXT)
            for bar, v in zip(bars, vals_g[::-1]):
                ax1.text(v + 0.2, bar.get_y() + bar.get_height() / 2,
                         f"+{v:.1f}%", va="center", color="#3FB950", fontsize=11, fontweight="bold")

        if names_l:
            bars = ax2.barh(names_l[::-1], vals_l[::-1], color="#DA3633", height=0.6)
            ax2.set_title("Падіння 24h", color=TEXT, fontsize=13, fontweight="bold", pad=8)
            ax2.set_xlabel("Зміна %", color=SUBTEXT, fontsize=10)
            ax2.tick_params(axis="y", labelsize=10, labelcolor=TEXT)
            for bar, v in zip(bars, vals_l[::-1]):
                ax2.text(v - 0.2, bar.get_y() + bar.get_height() / 2,
                         f"{v:.1f}%", va="center", ha="right", color="#F85149", fontsize=11, fontweight="bold")

        plt.suptitle("DeFi 24h зміни", color=TEXT, fontsize=15, fontweight="bold", y=1.02)
        plt.tight_layout(pad=1.2)

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        plt.savefig(tmp.name, dpi=150, bbox_inches="tight", facecolor=BG)
        plt.close()
        return tmp.name
    except Exception as e:
        print(f"Chart error: {e}")
        return None

def _send_photo_digest(photo_path: str, caption: str):
    """Шле фото з підписом через Telegram multipart."""
    boundary = "----DigestBoundary"
    try:
        with open(photo_path, "rb") as f:
            img_data = f.read()
        caption_bytes = caption[:1024].encode("utf-8")
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{TELEGRAM_CHAT}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n'
        ).encode() + caption_bytes + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="digest.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception as e:
        print(f"sendPhoto error: {e}")
        return False
    finally:
        try:
            import os as _os
            _os.unlink(photo_path)
        except Exception:
            pass


def digest_24h(force: bool = False):
    """Компактний дайджест змін DeFi за останні 24год."""
    local    = datetime.now(timezone.utc) + timedelta(hours=2)
    time_str = local.strftime("%H:%M")
    date_str = local.strftime("%d.%m.%Y")

    print(f"=== DeFi Digest 24h run at {local.isoformat()} ===")

    # ── Dedup ──
    if not force:
        already, sent_data, sent_key = _digest_dedup_check()
        if already:
            print(f"Digest already sent ({sent_key}), skipping.")
            return
    else:
        _, sent_data, sent_key = _digest_dedup_check()

    # ── Отримуємо дані ──
    protocols = _get(f"{LLAMA}/protocols")
    if not protocols:
        send_part("⚠️ DeFi дайджест: не вдалось завантажити дані")
        return

    # ── Блок 1+2: Gainers / Losers ──
    DEFI_CATS = {"DEX", "Lending", "CDP", "Yield", "Bridge", "Liquid Staking",
                 "Derivatives", "Staking", "Restaking", "Yield Aggregator"}
    defi = [p for p in protocols
            if p.get("category") in DEFI_CATS
            and (p.get("tvl") or 0) > 50_000_000
            and p.get("change_1d") is not None]

    gainers = sorted([p for p in defi if (p.get("change_1d") or 0) > 3],
                     key=lambda x: x["change_1d"], reverse=True)[:5]
    losers  = sorted([p for p in defi if (p.get("change_1d") or 0) < -3],
                     key=lambda x: x["change_1d"])[:5]

    # ── Блок 3: Chains TVL (агрегуємо з protocols — там є change_1d) ──
    chain_tvl = {}
    chain_delta = {}   # summed absolute TVL delta (to estimate %)
    for p in protocols:
        ch1d = p.get("change_1d")
        tvl  = p.get("tvl") or 0
        for ch in (p.get("chains") or []):
            chain_tvl[ch] = chain_tvl.get(ch, 0) + tvl
            if ch1d is not None:
                chain_delta.setdefault(ch, []).append(ch1d * tvl / 100)
    top_chain_names = sorted(chain_tvl, key=lambda x: -chain_tvl[x])[:8]
    top_chains = []
    for name in top_chain_names:
        tvl  = chain_tvl[name]
        deltas = chain_delta.get(name, [])
        avg_ch = (sum(deltas) / tvl * 100) if deltas and tvl > 0 else None
        top_chains.append({"name": name, "tvl": tvl, "change_1d": avg_ch})

    # ── Блок 4: DEX volumes ──
    dex_data = _get(f"{LLAMA}/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true") or {}
    dex_protos = sorted(
        [p for p in (dex_data.get("protocols") or []) if p.get("totalAllTime")],
        key=lambda x: x.get("dailyVolume") or x.get("total24h") or 0, reverse=True
    )[:5]

    # ── Блок 5: Топ yields ──
    yields_data = _get(f"{YIELDS}/pools") or {}
    pools = [p for p in (yields_data.get("data") or [])
             if (p.get("apy") or 0) > 10
             and (p.get("tvlUsd") or 0) > 1_000_000
             and not p.get("outlier", False)]
    top_pools = sorted(pools, key=lambda x: x.get("tvlUsd") or 0, reverse=True)[:5]

    # ── Блок 6: Stablecoins ──
    stables_data = _get(f"{STABLES}/stablecoins?includePrices=true") or {}
    stable_assets = sorted(
        (stables_data.get("peggedAssets") or []),
        key=lambda x: float((x.get("circulating") or {}).get("peggedUSD") or 0), reverse=True
    )[:6]

    # ─── Будуємо текст ───────────────────────────────────────────────────────

    SEP = "\n──────────────────\n"

    lines = [f"📡 <b>DeFi дайджест — зміни 24год</b>  ·  {time_str} {date_str}\n"]

    # Gainers
    if gainers:
        lines.append("📈 <b>Топ зростання (TVL 24h):</b>")
        for p in gainers:
            lines.append(
                f"  🟢 <b>{esc(p['name'])}</b>  "
                f"<code>{fmt_b(p.get('tvl'))}</code>  "
                f"<i>+{p['change_1d']:.1f}%</i>"
                + (f"  7d:{'+' if (p.get('change_7d') or 0)>0 else ''}{p['change_7d']:.1f}%"
                   if p.get("change_7d") is not None else "")
            )
    else:
        lines.append("📈 <b>Значних зростань немає</b>")

    lines.append(SEP)

    # Losers
    if losers:
        lines.append("📉 <b>Топ падіння (TVL 24h):</b>")
        for p in losers:
            lines.append(
                f"  🔴 <b>{esc(p['name'])}</b>  "
                f"<code>{fmt_b(p.get('tvl'))}</code>  "
                f"<i>{p['change_1d']:.1f}%</i>"
                + (f"  7d:{'+' if (p.get('change_7d') or 0)>0 else ''}{p['change_7d']:.1f}%"
                   if p.get("change_7d") is not None else "")
            )
    else:
        lines.append("📉 <b>Значних падінь немає</b>")

    lines.append(SEP)

    # Chains
    if top_chains:
        lines.append("⛓ <b>Chains TVL (топ-8):</b>")
        for c in top_chains:
            ch1d = c.get("change_1d")
            ar   = "🟢" if (ch1d or 0) > 0 else ("🔴" if (ch1d or 0) < 0 else "⚪️")
            d1   = f"{'+' if (ch1d or 0)>0 else ''}{ch1d:.1f}%" if ch1d is not None else "—"
            lines.append(
                f"  {ar} <b>{esc(c.get('name','?'))}</b>  "
                f"<code>{fmt_b(c.get('tvl'))}</code>  <i>{d1}</i>"
            )

    lines.append(SEP)

    # DEX
    if dex_protos:
        lines.append("🔄 <b>DEX обсяги 24h:</b>")
        for p in dex_protos:
            vol = p.get("dailyVolume") or p.get("total24h") or 0
            lines.append(
                f"  • <b>{esc(p.get('displayName') or p.get('name','?'))}</b>  "
                f"<code>{fmt_b(vol)}</code>"
            )

    lines.append(SEP)

    # Yields
    if top_pools:
        lines.append("💰 <b>Топ yields (APY >10%, за TVL):</b>")
        for p in top_pools:
            lines.append(
                f"  • <b>{esc(p.get('project','?'))}</b> {esc(p.get('symbol',''))}  "
                f"APY <b>{p.get('apy',0):.1f}%</b>  "
                f"TVL <code>{fmt_b(p.get('tvlUsd'))}</code>"
            )

    lines.append(SEP)

    # Stablecoins
    if stable_assets:
        total_mcap = sum(float((a.get("circulating") or {}).get("peggedUSD") or 0) for a in stable_assets)
        lines.append(f"🪙 <b>Стейблкоіни — {fmt_b(total_mcap)}:</b>")
        for a in stable_assets:
            circ = float((a.get("circulating") or {}).get("peggedUSD") or 0)
            prev = float((a.get("circulatingPrevDay") or {}).get("peggedUSD") or 0)
            ch   = (circ - prev) / prev * 100 if prev else 0
            ar   = "🟢" if ch > 0.1 else ("🔴" if ch < -0.1 else "⚪️")
            lines.append(
                f"  {ar} <b>{esc(a.get('symbol','?'))}</b>  "
                f"<code>{fmt_b(circ)}</code>  "
                f"<i>{'+' if ch>0 else ''}{ch:.2f}%</i>"
            )

    text_body = "\n".join(lines)

    # ── Gemini AI висновок ──
    _g_str = ", ".join(f"{p['name']} +{p['change_1d']:.1f}%" for p in gainers)
    _l_str = ", ".join(f"{p['name']} {p['change_1d']:.1f}%" for p in losers)
    _c_str = ", ".join(f"{c.get('name')} {fmt_b(c.get('tvl'))}" for c in top_chains[:4])
    _s_str = ", ".join(a.get("symbol", "") for a in stable_assets[:4])
    context_for_ai = (
        f"Gainers 24h: {_g_str}\n"
        f"Losers 24h: {_l_str}\n"
        f"Chains top TVL: {_c_str}\n"
        f"Stables mcap changes: {_s_str}\n"
    )
    ai_text = _gemini_digest_summary(context_for_ai)

    if ai_text:
        text_body += f"\n\n🤖 <i>{esc(ai_text)}</i>"

    text_body += "\n\n<i>📊 DeFiLlama · Наступний о 07:00</i>"

    # ── Зберігаємо dedup ──
    if sent_key:
        _digest_dedup_save(sent_data, sent_key)

    # ── Генеруємо графік і шлемо ──
    chart_path = _make_digest_chart(gainers, losers)
    if chart_path:
        # caption = перший блок (до 1024 символів)
        caption = "\n".join(lines[:15])[:1020]
        _send_photo_digest(chart_path, caption)
        # Решта тексту (AI + stables) як окреме повідомлення
        rest = text_body
        if len(rest) > 4090:
            rest = rest[:4090]
        send_part(rest)
    else:
        # Без графіка — просто текст
        if len(text_body) <= 4090:
            send_part(text_body)
        else:
            mid = text_body[:4090].rfind(SEP.strip())
            send_part(text_body[:mid] if mid > 0 else text_body[:4090])
            time.sleep(0.5)
            send_part(text_body[mid:] if mid > 0 else "")

    print("DeFi digest 24h sent.")


def send_defi_digest():
    """Entry point для monitor_loop.py."""
    digest_24h()


if __name__ == "__main__":
    main()
