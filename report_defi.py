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

def main():
    now        = datetime.now(timezone.utc)
    local      = now + timedelta(hours=2)
    time_str   = local.strftime("%H:%M")
    date_str   = local.strftime("%d.%m.%Y")
    period     = "🌅 Ранковий" if local.hour < 13 else "🌆 Вечірній"

    print(f"=== DeFi Report run at {now.isoformat()} ===")

    # Завантажуємо протоколи один раз
    print("Loading protocols...")
    protocols = _get(f"{LLAMA}/protocols")
    if not protocols:
        send_part("⚠️ DeFi звіт: не вдалось завантажити дані DeFiLlama")
        return

    # Заголовок
    header = (
        f"📡 <b>{period} DeFi & RWA звіт</b>\n"
        f"🕐 {time_str} · {date_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    send_part(header)
    time.sleep(0.5)

    # Частина 1: Загальне TVL + Chains
    part1 = get_total_tvl(protocols)
    chains = get_chains()
    if chains:
        part1 += "\n\n" + chains
    send_part(part1)
    time.sleep(0.5)

    # Частина 2: Топ-20 DeFi
    send_part(get_top_defi(protocols))
    time.sleep(0.5)

    # Частина 3: RWA
    send_part(get_rwa(protocols))
    time.sleep(0.5)

    # Частина 4: DEX + Lending
    dex = get_dex_volumes()
    lending = get_lending(protocols)
    send_part(dex + "\n\n" + lending)
    time.sleep(0.5)

    # Частина 5: Yields + Stablecoins
    yields = get_top_yields()
    stables = get_stablecoins()
    send_part(yields + "\n\n" + stables)
    time.sleep(0.5)

    # Футер
    footer = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Дані: <a href='https://defillama.com'>DeFiLlama</a> · "
        f"Наступний звіт через ~12г"
    )
    send_part(footer)
    print("DeFi report sent.")


if __name__ == "__main__":
    main()
