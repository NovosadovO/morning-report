#!/usr/bin/env python3
"""
ETF crypto звіт — потоки капіталу (net inflow/outflow) у BTC/ETH/SOL spot ETF.

Джерело: SoSoValue OpenAPI (офіційний, безкоштовний Demo API tier).
https://sosovalue.com/developer  ->  /openapi/v1/etfs/summary-history

Ключ береться з env SOSOVALUE_API_KEY. Якщо його нема — використовується
публічний demo-ключ (community demo tier), який може будь-коли отримати
rate-limit або бути відкликаний SoSoValue. Щоб мати стабільний власний
лічильник запитів — Олег може зареєструватись на sosovalue.com/developer,
взяти свій Demo API key і додати його як SOSOVALUE_API_KEY на Railway.
"""

import os, json, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

try:
    import requests as _req
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "2100366814")

SOSO_BASE = "https://openapi.sosovalue.com/openapi/v1"
# Fallback community demo key (публічно відомий, може бути rate-limited/revoked
# будь-коли). Свій ключ: SOSOVALUE_API_KEY на Railway.
_SOSO_FALLBACK_KEY = "SOSO-ed3a4f77582943bab2b77556662acdb6"
SOSO_API_KEY = os.environ.get("SOSOVALUE_API_KEY", "").strip() or _SOSO_FALLBACK_KEY

# Символ -> (людська назва, емодзі)
ETF_SYMBOLS = [
    ("BTC", "₿ Bitcoin spot ETF", "🟠"),
    ("ETH", "Ξ Ethereum spot ETF", "🔵"),
    ("SOL", "◎ Solana spot ETF", "🟣"),
]

_cache = {}
_CACHE_TTL = 900  # 15 хв


def esc(s): return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_usd(v):
    if v is None:
        return "—"
    v = float(v)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e9:  return f"{sign}${v/1e9:.2f}B"
    if v >= 1e6:  return f"{sign}${v/1e6:.1f}M"
    if v >= 1e3:  return f"{sign}${v/1e3:.0f}K"
    return f"{sign}${v:.0f}"


def _get(path, params, retries=2):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{SOSO_BASE}{path}?{qs}"
    cache_key = url
    now = time.time()
    hit = _cache.get(cache_key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    for attempt in range(1, retries + 1):
        try:
            headers = {"x-soso-api-key": SOSO_API_KEY, "User-Agent": "etf-report/1.0"}
            if _HAS_REQUESTS:
                r = _req.get(url, headers=headers, timeout=20)
                r.raise_for_status()
                body = r.json()
            else:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=20) as r:
                    body = json.loads(r.read().decode())
            if isinstance(body, dict) and body.get("code") not in (0, None):
                print(f"SoSoValue API code={body.get('code')} msg={body.get('message')}")
                return None
            data = body.get("data") if isinstance(body, dict) else body
            _cache[cache_key] = (now, data)
            return data
        except Exception as e:
            print(f"SoSoValue GET attempt {attempt}/{retries} [{path}]: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


def get_etf_flows(symbol, days=10):
    """Останні `days` днів потоків для одного ETF (BTC/ETH/SOL spot).
    Повертає список dict, найновіший день ПЕРШИЙ (як віддає API)."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days + 3)
    params = {
        "symbol": symbol,
        "country_code": "US",
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "limit": str(days + 5),
    }
    rows = _get("/etfs/summary-history", params)
    if not rows:
        return None
    return rows[:days]


def _bar_row(v, max_abs, width=10):
    if max_abs <= 0:
        return "░" * width
    n = int(abs(v) / max_abs * width)
    n = min(n, width)
    return ("🟢" if v >= 0 else "🔴") * n + "▪️" * (width - n)


def format_etf_block(symbol, label, emoji, rows):
    if not rows:
        return f"{emoji} <b>{esc(label)}</b>\n⚠️ Дані тимчасово недоступні"

    today = rows[0]
    net_today = today.get("total_net_inflow")
    aum       = today.get("total_net_assets")
    cum_all   = today.get("cum_net_inflow")

    last7 = rows[:7]
    sum7  = sum((r.get("total_net_inflow") or 0) for r in last7)

    ar_today = "🟢" if (net_today or 0) > 0 else ("🔴" if (net_today or 0) < 0 else "⚪️")
    ar_7d    = "🟢" if sum7 > 0 else ("🔴" if sum7 < 0 else "⚪️")

    lines = [
        f"{emoji} <b>{esc(label)}</b>",
        f"   Сьогодні ({today.get('date','?')}): {ar_today} <b>{fmt_usd(net_today)}</b>",
        f"   7 днів сума: {ar_7d} <b>{fmt_usd(sum7)}</b>",
        f"   AUM зараз: <b>{fmt_usd(aum)}</b>",
        f"   Всього з початку торгів: <b>{fmt_usd(cum_all)}</b>",
    ]

    # Мінітаблиця останніх 5 днів
    last5 = list(reversed(rows[:5]))  # старі -> нові
    max_abs = max((abs(r.get("total_net_inflow") or 0) for r in last5), default=0)
    lines.append("   <i>Останні 5 днів:</i>")
    for r in last5:
        v = r.get("total_net_inflow") or 0
        d = r.get("date", "")[5:]  # MM-DD
        bar = _bar_row(v, max_abs, width=8)
        lines.append(f"   <code>{d}</code> {bar} {fmt_usd(v)}")

    return "\n".join(lines)


def compact_block(symbols=("BTC", "ETH")):
    """Компактний блок ETF-потоків (без мінітаблиці 5 днів) — для вбудовування
    у двічі-денний DeFi/market звіт."""
    lookup = {s: (label, emoji) for s, label, emoji in ETF_SYMBOLS}
    lines = ["💹 <b>Крипто-ETF потоки (US spot):</b>\n"]
    any_ok = False
    for symbol in symbols:
        label, emoji = lookup.get(symbol, (symbol, "•"))
        rows = get_etf_flows(symbol, days=8)
        if not rows:
            continue
        any_ok = True
        today = rows[0]
        net_today = today.get("total_net_inflow")
        aum       = today.get("total_net_assets")
        sum7      = sum((r.get("total_net_inflow") or 0) for r in rows[:7])
        ar_t = "🟢" if (net_today or 0) > 0 else ("🔴" if (net_today or 0) < 0 else "⚪️")
        ar_7 = "🟢" if sum7 > 0 else ("🔴" if sum7 < 0 else "⚪️")
        lines.append(
            f"{emoji} <b>{esc(label)}</b> ({today.get('date','?')}): {ar_t} {fmt_usd(net_today)}  "
            f"· 7д {ar_7} {fmt_usd(sum7)}  · AUM {fmt_usd(aum)}"
        )
    if not any_ok:
        return None
    return "\n".join(lines)


def build_report_text():
    local = datetime.now(timezone.utc) + timedelta(hours=2)
    time_str = local.strftime("%H:%M")
    date_str = local.strftime("%d.%m.%Y")

    blocks = [f"💹 <b>КРИПТО-ETF ПОТОКИ (US spot)</b>  ·  {time_str} {date_str}\n"]
    any_ok = False
    for symbol, label, emoji in ETF_SYMBOLS:
        rows = get_etf_flows(symbol, days=10)
        if rows:
            any_ok = True
        blocks.append(format_etf_block(symbol, label, emoji, rows))

    if not any_ok:
        return None

    blocks.append(
        "\n<i>ℹ️ Джерело: SoSoValue (офіційний безкоштовний API). "
        "Netflow — чисті надходження/відтоки капіталу у $ за день, "
        "AUM — загальні активи під управлінням усіх фондів символу.</i>"
    )
    return "\n\n".join(blocks)


def _gemini_etf_summary(context: str) -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return ""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
        prompt = (
            "Ти криптo-аналітик. На основі цих даних по потоках капіталу в BTC/ETH/SOL spot ETF "
            "напиши 2-4 речення українською для Олега: що це означає для настроїв інституційних "
            "інвесторів, чи це bullish/bearish сигнал, на що звернути увагу найближчі дні. "
            "Тепло, конкретно, без загальних фраз.\n\n" + context
        )
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 260, "temperature": 0.7, "thinkingConfig": {"thinkingBudget": 0}}
        }).encode()
        from monitor import _gem_post
        resp = _gem_post(url, payload, timeout=20, tag="etf_summary", max_retries=3)
        if isinstance(resp, dict) and resp.get("candidates"):
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"].strip()
    except Exception as e:
        print(f"Gemini ETF summary error: {e}")
    return ""


def send_part(text: str) -> bool:
    try:
        import quiet as _q_g
        if _q_g.blocked("msg"):
            print("[quiet] 🌙 сон: report_etf.send_part придушено", flush=True)
            return False
    except Exception:
        pass
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4090],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def send_etf_report():
    """Команда /etf — надсилає повний ETF-flows звіт з AI-коментарем."""
    print("=== ETF flows report ===")
    text = build_report_text()
    if not text:
        send_part("⚠️ ETF звіт: дані SoSoValue тимчасово недоступні (спробуй пізніше)")
        return

    ctx_lines = []
    for symbol, label, emoji in ETF_SYMBOLS:
        rows = get_etf_flows(symbol, days=7)
        if rows:
            sum7 = sum((r.get("total_net_inflow") or 0) for r in rows[:7])
            ctx_lines.append(f"{symbol}: today {fmt_usd(rows[0].get('total_net_inflow'))}, 7d sum {fmt_usd(sum7)}, AUM {fmt_usd(rows[0].get('total_net_assets'))}")
    ai_text = _gemini_etf_summary("\n".join(ctx_lines)) if ctx_lines else ""

    full = text
    if ai_text:
        full += f"\n\n🤖 <i>{esc(ai_text)}</i>"

    if len(full) <= 4090:
        send_part(full)
    else:
        send_part(text)
        time.sleep(0.5)
        if ai_text:
            send_part(f"🤖 <i>{esc(ai_text)}</i>")
    print("ETF flows report sent.")


if __name__ == "__main__":
    send_etf_report()
