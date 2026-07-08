"""
Message Generator v2.0 — Live data injection + anti-repeat
Pulls real CoinGecko / Gmail / Health / Calendar data into every Gemini prompt.
Anti-repeat: tracks last 5 topics, forbids repeating same angle.
"""

import os
import json
import time
import imaplib
import email as email_lib
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from email.header import decode_header as _decode_hdr
from zoneinfo import ZoneInfo

# ─── Optional engine imports ─────────────────────────────────────────────────
try:
    from aggressive_briefing_v3 import get_brief_v3
    _BRIEFING_V3_AVAILABLE = True
except ImportError:
    _BRIEFING_V3_AVAILABLE = False

try:
    from deep_analysis_engine import build_deep_analysis
    _DEEP_ANALYSIS_AVAILABLE = True
except ImportError:
    _DEEP_ANALYSIS_AVAILABLE = False

try:
    from contextual_briefing_engine import get_contextual_briefing
    _BRIEFING_AVAILABLE = True
except ImportError:
    _BRIEFING_AVAILABLE = False

# ─── Config ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "2100366814")
GMAIL_USER      = os.getenv("GMAIL_USER", "novosadovoleg@gmail.com")
GMAIL_APP_PASS  = os.getenv("GMAIL_APP_PASSWORD", "")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_TZ = ZoneInfo("Europe/Bratislava")
_HISTORY_FILE = os.path.join(_DATA_DIR, "message_history.json")
_HISTORY_MAX  = 8   # remember last N messages for anti-repeat

_GEM_MODELS   = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
_GEM_MODEL_IDX = 0
_GEM_LAST_CALL = 0.0
_GEM_MIN_GAP   = 4.0

# ─── Logging ─────────────────────────────────────────────────────────────────
def _log(msg: str):
    ts = datetime.now(tz=_TZ).strftime("%H:%M:%S")
    print(f"[MSG_GEN {ts}] {msg}", flush=True)

# ─── Gemini ──────────────────────────────────────────────────────────────────
def _gemini_post(body: dict, timeout: int = 25, tag: str = "") -> str:
    """Делегує до monitor._gem_post — СПІЛЬНИЙ rate-limiter на весь процес
    (усі потоки/модулі рахують ліміт Gemini в ОДНЕ і те саме місце,
    щоб сумарно не перевищувати 15 req/хв навіть при 60+ паралельних watcher-ах)."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from monitor import _gem_post
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{_GEM_MODELS[0]}:generateContent?key={GEMINI_API_KEY}"
        resp = _gem_post(url, json.dumps(body).encode(), timeout=timeout, tag=tag or "msg_gen", max_retries=3)
        if isinstance(resp, dict) and "candidates" in resp:
            cands = resp.get("candidates", [])
            if cands:
                parts = cands[0].get("content", {}).get("parts", [])
                if parts and parts[0].get("text"):
                    return parts[0]["text"]
        _log(f"{tag}: empty response from _gem_post")
    except Exception as e:
        _log(f"{tag}: {e}")
    return ""

# ─── Live Data ────────────────────────────────────────────────────────────────
def _get_live_crypto() -> dict:
    """CoinGecko free API — розширений watchlist + TOP-мувери за 24г.
    Основні монети Олега (BTC/ETH/AVAX/ONDO) + додаткові (SOL/BNB/XRP/DOGE)
    для ширшого контексту крипто-огляду, плюс окремий TOP-3 gainers/losers з ринку.
    Кешується через monitor.fetch_json_cached (60с) — уникає burst 429 коли
    кілька тригерів/щоденних звітів дзвонять цю функцію майже одночасно."""
    try:
        import sys as _sys_lc
        _sys_lc.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from monitor import fetch_json_cached

        ids = "bitcoin,ethereum,avalanche-2,ondo-finance,solana,binancecoin,ripple,dogecoin"
        url = (
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&ids=" + ids +
            "&order=market_cap_desc&per_page=10&page=1&sparkline=false"
            "&price_change_percentage=24h,7d"
        )
        raw = fetch_json_cached(url, ttl=60)
        if not raw:
            return {}
        mapping = {
            "bitcoin": "BTC", "ethereum": "ETH",
            "avalanche-2": "AVAX", "ondo-finance": "ONDO",
            "solana": "SOL", "binancecoin": "BNB",
            "ripple": "XRP", "dogecoin": "DOGE",
        }
        result = {}
        for coin in raw:
            cid = coin.get("id", "")
            if cid in mapping:
                cname = mapping[cid]
                result[cname] = {
                    "price":      round(coin.get("current_price") or 0, 4),
                    "change_24h": round(coin.get("price_change_percentage_24h") or 0, 2),
                    "change_7d":  round(coin.get("price_change_percentage_7d_in_currency") or 0, 2),
                }
        summary = ", ".join(
            f"{k}=${v['price']}({v['change_24h']:+.1f}%)" for k, v in result.items()
        )
        _log(f"Crypto: {summary}")

        # TOP-3 mовери за 24h із топ-100 монет за market cap (ширший контекст ринку)
        try:
            top_url = (
                "https://api.coingecko.com/api/v3/coins/markets"
                "?vs_currency=usd&order=market_cap_desc&per_page=100&page=1"
                "&sparkline=false&price_change_percentage=24h"
            )
            top_raw = fetch_json_cached(top_url, ttl=120)
            if top_raw:
                movers = [
                    (c.get("symbol", "").upper(), round(c.get("price_change_percentage_24h") or 0, 2))
                    for c in top_raw if c.get("price_change_percentage_24h") is not None
                ]
                movers.sort(key=lambda x: x[1], reverse=True)
                result["_top_gainers"] = movers[:3]
                result["_top_losers"] = movers[-3:][::-1]
        except Exception as e:
            _log(f"CoinGecko top-movers error: {e}")

        return result
    except Exception as e:
        _log(f"CoinGecko error: {e}")
        return {}

def _get_live_health() -> dict:
    """Latest weight, steps, sleep from data files (weight.json, daily_health.json, health.json)."""
    result = {}
    today_str = datetime.now(tz=_TZ).strftime("%Y-%m-%d")
    yesterday = (datetime.now(tz=_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")

    # 1) Weight from weight.json
    try:
        wfile = os.path.join(_DATA_DIR, "weight.json")
        if os.path.exists(wfile):
            with open(wfile) as f:
                wdata = json.load(f)
            if wdata:
                latest = sorted(wdata.keys())[-1]
                result["weight"] = wdata[latest]
                result["weight_date"] = latest
                week_ago = (datetime.now(tz=_TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
                old_keys = [k for k in sorted(wdata.keys()) if k <= week_ago]
                if old_keys:
                    result["weight_7d_delta"] = round(result["weight"] - wdata[old_keys[-1]], 1)
    except Exception as e:
        _log(f"Weight read error: {e}")

    # 2) Steps/sleep from daily_health.json
    try:
        hfile = os.path.join(_DATA_DIR, "daily_health.json")
        if os.path.exists(hfile):
            with open(hfile) as f:
                hdata = json.load(f)
            entries = hdata.get("entries", hdata) if isinstance(hdata, dict) else {}
            for day in [today_str, yesterday]:
                if day in entries:
                    e = entries[day]
                    if not result.get("steps"):
                        result["steps"] = e.get("steps") or e.get("steps_count")
                    if not result.get("sleep"):
                        result["sleep"] = e.get("sleep_hours") or e.get("sleep")
                    result["health_date"] = day
                    break
    except Exception as e:
        _log(f"daily_health read error: {e}")

    # 3) Fallback: health.json (older format with steps)
    try:
        hfile2 = os.path.join(_DATA_DIR, "health.json")
        if os.path.exists(hfile2):
            with open(hfile2) as f:
                hdata2 = json.load(f)
            if isinstance(hdata2, dict):
                for day in [today_str, yesterday]:
                    if day in hdata2:
                        e = hdata2[day]
                        if isinstance(e, dict):
                            if not result.get("steps") and e.get("steps"):
                                result["steps"] = e["steps"]
                            if not result.get("weight") and e.get("weight"):
                                result["weight"] = e["weight"]
                                result["weight_date"] = day
                        break
    except Exception as e:
        _log(f"health.json read error: {e}")

    # 4) Fallback: monitor_day_summary.json for today's stats
    try:
        dsfile = os.path.join(_DATA_DIR, "monitor_day_summary.json")
        if os.path.exists(dsfile):
            with open(dsfile) as f:
                ds = json.load(f)
            if isinstance(ds, dict):
                if not result.get("weight") and ds.get("weight"):
                    result["weight"] = ds["weight"]
                    result["weight_date"] = ds.get("date", today_str)
                if not result.get("steps") and ds.get("steps"):
                    result["steps"] = ds["steps"]
    except Exception as e:
        _log(f"day_summary read error: {e}")

    _log(f"Health: weight={result.get('weight')}, steps={result.get('steps')}, sleep={result.get('sleep')}")
    return result

def _get_live_emails(max_emails: int = 8) -> list:
    """Last important emails via IMAP — includes STARRED + recent 3 days."""
    if not GMAIL_APP_PASS:
        return []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_USER, GMAIL_APP_PASS)
        mail.select("INBOX")

        # Recent 3 days
        since = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
        _, msgs_recent = mail.search(None, f"SINCE {since}")
        recent_ids = set(msgs_recent[0].split()) if msgs_recent[0] else set()

        # STARRED (Flagged) — ALWAYS include regardless of date
        try:
            _, msgs_star = mail.search(None, "FLAGGED")
            starred_ids = set(msgs_star[0].split()) if msgs_star[0] else set()
        except Exception:
            starred_ids = set()

        # starred first, then recent
        starred_list = sorted(starred_ids, key=lambda x: int(x))
        recent_list  = [i for i in sorted(recent_ids, key=lambda x: int(x)) if i not in starred_ids]
        starred_to_use = starred_list[-4:]
        recent_to_use  = recent_list[-(max_emails - len(starred_to_use)):]
        final_ids = list(dict.fromkeys(starred_to_use + recent_to_use))

        result = []
        skip_kw = ["noreply@youtube", "no-reply@youtube",
                   "noreply@duolingo", "no-reply@duolingo", "maps-timeline",
                   "noreply@medium", "notification@facebookmail"]
        seen_subjects = set()

        for eid in reversed(final_ids):
            if len(result) >= max_emails:
                break
            try:
                _, data = mail.fetch(eid, "(RFC822)")
                if not data or not data[0]:
                    continue
                msg = email_lib.message_from_bytes(data[0][1])
                sender  = _decode_str(msg.get("From", ""))
                subject = _decode_str(msg.get("Subject", ""))
                date    = msg.get("Date", "")
                is_starred = eid in starred_ids
                if any(k in (sender + subject).lower() for k in skip_kw):
                    continue
                key = subject[:40].lower()
                if key in seen_subjects:
                    continue
                seen_subjects.add(key)
                result.append({
                    "from":    sender[:60],
                    "subject": subject[:80],
                    "date":    date[:30],
                    "starred": is_starred,
                })
            except Exception:
                continue

        mail.close(); mail.logout()
        return result
    except Exception as e:
        _log(f"Gmail error: {e}")
        return []

def _decode_str(s: str) -> str:
    try:
        parts = _decode_hdr(s)
        out = []
        for part, charset in parts:
            if isinstance(part, bytes):
                out.append(part.decode(charset or "utf-8", errors="ignore"))
            else:
                out.append(str(part))
        return "".join(out)
    except:
        return str(s)

def _get_live_strava() -> dict:
    """Fetch recent Strava activities (last 7 days)."""
    try:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(__file__))
        from strava import get_activities
        # Використовуємо централізовану get_activities() з TTL-кешем (10 хв) —
        # уникає прямого HTTP запиту в обхід кешу і зайвого Strava 429 burst
        acts = get_activities(days=7)
        if not acts:
            return {}
        runs = [a for a in acts if a.get("type") in ("Run", "VirtualRun")]
        if not runs:
            return {}
        last = runs[0]
        km = round(last.get("distance", 0) / 1000, 2)
        duration_min = round(last.get("moving_time", 0) / 60, 1)
        pace_sec = (last.get("moving_time", 0) / (last.get("distance", 1) / 1000)) if last.get("distance") else 0
        pace_str = f"{int(pace_sec//60)}:{int(pace_sec%60):02d}/км" if pace_sec else "—"
        date_str = last.get("start_date_local", "")[:10]
        weekly_km = round(sum(a.get("distance", 0) for a in runs) / 1000, 1)
        total_runs = len(runs)
        return {
            "last_run_km":    km,
            "last_run_date":  date_str,
            "last_run_pace":  pace_str,
            "last_run_min":   duration_min,
            "last_run_name":  last.get("name", "Пробіжка"),
            "weekly_km":      weekly_km,
            "weekly_runs":    total_runs,
        }
    except Exception as e:
        _log(f"Strava error: {e}")
        return {}

def _get_live_calendar() -> list:
    """Try to get upcoming events via monitor.get_upcoming_events()."""
    try:
        import monitor as _mon
        raw = _mon.get_upcoming_events(days_ahead=3)
        if raw:
            # raw is HTML text — strip tags for plain text
            import re
            clean = re.sub(r"<[^>]+>", "", raw)
            return [line.strip() for line in clean.splitlines() if line.strip() and "•" in line]
    except Exception as e:
        _log(f"Calendar error: {e}")
    return []

def _get_live_data() -> dict:
    """Collect all live data sources in parallel-ish."""
    _log("Collecting live data...")
    crypto   = _get_live_crypto()
    health   = _get_live_health()
    strava   = _get_live_strava()
    emails   = _get_live_emails(max_emails=8)
    calendar = _get_live_calendar()
    now      = datetime.now(tz=_TZ)
    return {
        "crypto":   crypto,
        "health":   health,
        "strava":   strava,
        "emails":   emails,
        "calendar": calendar,
        "now_str":  now.strftime("%d.%m.%Y %H:%M"),
        "weekday":  ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][now.weekday()],
        "hour":     now.hour,
    }

# ─── Anti-repeat ─────────────────────────────────────────────────────────────
def _load_history() -> list:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        if os.path.exists(_HISTORY_FILE):
            with open(_HISTORY_FILE) as f:
                return json.load(f)
    except:
        pass
    return []

def _save_to_history(trigger_type: str, topic_summary: str):
    hist = _load_history()
    hist.append({
        "ts":      datetime.now(tz=_TZ).isoformat(),
        "trigger": trigger_type,
        "topic":   topic_summary[:120],
    })
    hist = hist[-_HISTORY_MAX:]
    try:
        with open(_HISTORY_FILE, "w") as f:
            json.dump(hist, f, indent=2)
    except:
        pass

def _build_anti_repeat_block() -> str:
    hist = _load_history()
    if not hist:
        return ""
    lines = [f"- [{h['trigger']}] {h['topic']}" for h in hist[-5:]]
    return (
        "\n⚠️ ЗАБОРОНА ПОВТОРЕНЬ — ці теми вже надсилались. НЕ повторюй той самий кут зору:\n"
        + "\n".join(lines)
        + "\nОбери ІНШИЙ кут, ІНШИЙ акцент або ІНШУ деталь.\n"
    )

# ─── Data → Text ─────────────────────────────────────────────────────────────
def _crypto_text(crypto: dict) -> str:
    if not crypto:
        return "Крипто: дані недоступні (CoinGecko)"
    lines = []
    for coin, d in crypto.items():
        if coin.startswith("_"):
            continue
        c24 = d["change_24h"]
        c7  = d["change_7d"]
        arrow = "📈" if c24 > 0 else "📉"
        lines.append(f"  {arrow} {coin}: ${d['price']:,.2f} ({c24:+.1f}% за 24г, {c7:+.1f}% за тиж)")
    result = "Крипто ЗАРАЗ (портфель Олега + ширший контекст):\n" + "\n".join(lines)
    gainers = crypto.get("_top_gainers")
    losers = crypto.get("_top_losers")
    if gainers:
        result += "\n  🚀 ТОП-3 gainers ринку (24г): " + ", ".join(f"{s} {c:+.1f}%" for s, c in gainers)
    if losers:
        result += "\n  🔻 ТОП-3 losers ринку (24г): " + ", ".join(f"{s} {c:+.1f}%" for s, c in losers)
    return result

def _health_text(health: dict) -> str:
    if not health:
        return "Здоров'я: даних немає — надішли дані текстом (кроки/сон/вага)"
    parts = []
    if "weight" in health:
        delta_str = ""
        if "weight_7d_delta" in health:
            d = health["weight_7d_delta"]
            delta_str = f" ({d:+.1f} кг за тиж)"
        parts.append(f"Вага: {health['weight']} кг{delta_str}")
    if "steps" in health:
        parts.append(f"Кроки: {health['steps']:,}")
    if "sleep" in health:
        parts.append(f"Сон: {health['sleep']} год")
    return "Здоров'я: " + " | ".join(parts)

def _emails_text(emails: list) -> str:
    if not emails:
        return "Листи: нових важливих листів немає"
    lines = []
    for e in emails[:6]:
        star = "⭐" if e.get("starred") else "•"
        lines.append(f"  {star} {e['from'][:40]} — {e['subject']}")
    return "Нові листи (включно із зірочками):\n" + "\n".join(lines)

def _strava_text(strava: dict) -> str:
    if not strava:
        return ""
    s = strava
    # Рахуємо "коли" ЗАВЖДИ динамічно від поточної дати — щоб AI не плутав "4 дні тому" з учора
    when_str = ""
    try:
        _lrd = s.get("last_run_date", "")
        if _lrd:
            _rd = datetime.strptime(_lrd, "%Y-%m-%d").date()
            _today = datetime.now().date()
            _diff = (_today - _rd).days
            if _diff == 0:
                when_str = "СЬОГОДНІ"
            elif _diff == 1:
                when_str = "ВЧОРА"
            elif _diff > 1:
                when_str = f"{_diff} ДНІВ ТОМУ"
            else:
                when_str = _lrd
    except Exception:
        when_str = ""
    lines = [
        f"🏃 Остання пробіжка — {when_str} ({s.get('last_run_date','?')}): {s.get('last_run_km','?')} км"
        f" за {s.get('last_run_min','?')} хв, темп {s.get('last_run_pace','?')}",
        f"   За тиждень: {s.get('weekly_runs','?')} пробіжок = {s.get('weekly_km','?')} км",
    ]
    return "\n".join(lines)

def _calendar_text(events: list) -> str:
    if not events:
        return "Календар: найближчих подій немає"
    return "Найближчі події:\n" + "\n".join(f"  {e}" for e in events[:5])

def _build_live_context(data: dict) -> str:
    strava_str = _strava_text(data.get("strava", {}))
    parts = [
        f"📅 {data['weekday']}, {data['now_str']}",
        "",
        _crypto_text(data.get("crypto", {})),
        "",
        _health_text(data.get("health", {})),
    ]
    if strava_str:
        parts += ["", strava_str]
    parts += [
        "",
        _emails_text(data.get("emails", [])),
        "",
        _calendar_text(data.get("calendar", [])),
    ]
    return "\n".join(parts)

# ─── Tone variation ──────────────────────────────────────────────────────────
def get_tone_variation(trigger_type: str, hour: int) -> dict:
    variations = {
        "morning": [
            {"tone": "енергійний + конкретний план", "emoji": "🌅"},
            {"tone": "аналітичний + огляд можливостей", "emoji": "📊"},
            {"tone": "натхненний + мотиваційний", "emoji": "✨"},
            {"tone": "стратегічний + фокус на цілях", "emoji": "🎯"},
        ],
        "vip_email": [
            {"tone": "офіційний, чіткий, дії першочергові", "emoji": "📧"},
            {"tone": "дипломатичний, відносини важливі", "emoji": "🤝"},
            {"tone": "стратегічний, довгострокове бачення", "emoji": "🏹"},
        ],
        "crypto_move": [
            {"tone": "спокійний аналітик, факти без паніки", "emoji": "📈"},
            {"tone": "обережний інвестор, ризик-менеджмент", "emoji": "⚠️"},
            {"tone": "освітній, пояснити причину руху", "emoji": "💹"},
        ],
        "event_soon": [
            {"tone": "нагадування + підготовка", "emoji": "⏰"},
            {"tone": "мотивуючий + що взяти/зробити", "emoji": "✅"},
        ],
        "health": [
            {"tone": "підтримуючий + конкретні поради", "emoji": "💪"},
            {"tone": "аналітичний + тренд за тиждень", "emoji": "📉"},
            {"tone": "практичний + план покращення", "emoji": "🎯"},
        ],
        "idle_timeout": [
            {"tone": "турботливий + пропозиція активності", "emoji": "🚶"},
            {"tone": "енергетичний + короткий заряд", "emoji": "⚡"},
        ],
        "evening": [
            {"tone": "рефлексивний + підсумок дня", "emoji": "🌙"},
            {"tone": "планувальний + завтра починається сьогодні", "emoji": "📋"},
            {"tone": "відновлювальний + релакс і сон", "emoji": "😌"},
        ],
        "weekly_run_compare": [
            {"tone": "тренер-аналітик, факти + прогрес", "emoji": "🏃"},
            {"tone": "мотивуючий тренер, фокус на покращенні", "emoji": "📊"},
        ],
        "habit_checkin": [
            {"tone": "теплий друг, без нотацій", "emoji": "🌙"},
            {"tone": "підтримуючий, коротко і по-людськи", "emoji": "💬"},
        ],
    }
    base = variations.get(trigger_type, [{"tone": "дружелюбний, особистий", "emoji": "👋"}])
    idx = (hour + abs(hash(trigger_type))) % len(base)
    return base[idx]

# ─── Decision ────────────────────────────────────────────────────────────────
def _should_send_message(trigger_type: str, trigger_data) -> bool:
    always = {"vip_email", "deep_analysis", "briefing", "contextual_briefing",
              "morning", "evening", "health", "weekly_run_compare", "habit_checkin"}
    if trigger_type in always:
        return True
    if trigger_type == "crypto_move":
        if isinstance(trigger_data, dict):
            return any(abs(v) > 5 for v in trigger_data.values())
    if trigger_type == "event_soon":
        routine = ["shower","water","tea","чай","душ","вода","сауна","armolopid","армолопід"]
        if isinstance(trigger_data, list):
            return any(not any(r in str(e).lower() for r in routine) for e in trigger_data)
    if trigger_type == "idle_timeout":
        h = datetime.now(tz=_TZ).hour
        return 6 <= h < 9 or 19 <= h < 23
    return False

# ─── Generate ────────────────────────────────────────────────────────────────
def _generate_message(trigger_type: str, trigger_data, location: str, idle_hours: float) -> str:
    """Main generation: collect live data → build prompt with context → Gemini."""

    # ── Special engines ──────────────────────────────────────────────────────
    # deep_analysis: use live-data Gemini (same as other triggers) for accurate data
    # No longer delegates to deep_analysis_engine which had stale crypto/health data

    if trigger_type in ("briefing", "contextual_briefing"):
        if _BRIEFING_V3_AVAILABLE:
            try:
                result = get_brief_v3(location, idle_hours)
                if result:
                    return result
            except Exception as e:
                _log(f"briefing_v3 failed: {e}")
        if _BRIEFING_AVAILABLE:
            try:
                result, _ = get_contextual_briefing(location, idle_hours)
                if result:
                    return result
            except Exception as e:
                _log(f"briefing_v1 failed: {e}")

    # ── Collect live data ─────────────────────────────────────────────────────
    live = _get_live_data()
    live_ctx = _build_live_context(live)
    anti_repeat = _build_anti_repeat_block()
    tone = get_tone_variation(trigger_type, live["hour"])

    # ── Build trigger-specific context ───────────────────────────────────────
    trigger_extra = ""
    if trigger_type == "crypto_move" and isinstance(trigger_data, dict):
        pairs = [f"{k}: {v:+.1f}%" for k, v in trigger_data.items()]
        trigger_extra = f"\n🚨 ПОШТОВХ: Ринок рухнувся — {', '.join(pairs)}"
    elif trigger_type == "vip_email" and isinstance(trigger_data, dict):
        trigger_extra = f"\n📬 VIP ЛИСТ: від «{trigger_data.get('from','?')}» — тема: «{trigger_data.get('subject','?')}»"
    elif trigger_type == "event_soon":
        if isinstance(trigger_data, list) and trigger_data:
            evts = "; ".join(str(e) for e in trigger_data[:3])
            trigger_extra = f"\n⏰ НЕЗАБАРОМ (30-90 хв): {evts} — дай КОРОТКИЙ підготовчий брифінг: що взяти з собою, на чому зфокусуватись, чи є пов'язані листи/дедлайни."
    elif trigger_type == "idle_timeout":
        trigger_extra = f"\n😴 Олег неактивний {idle_hours:.1f} год | Локація: {location}"
    elif trigger_type == "weekly_run_compare":
        try:
            from strava import compare_weeks
            cmp = compare_weeks()
            tw, pw = cmp.get("this_week", {}), cmp.get("prev_week", {})
            trigger_extra = (
                f"\n🏃 ПОРІВНЯННЯ ТИЖНІВ: цей тиждень {tw.get('km',0)} км/{tw.get('runs',0)} пробіжок, "
                f"попередній {pw.get('km',0)} км/{pw.get('runs',0)} пробіжок. "
                f"Різниця дистанції: {cmp.get('km_diff',0):+.1f} км, темп: {cmp.get('pace_diff',0):+.1f} с/км "
                f"(від'ємне = швидше). Дай коротку оцінку прогресу тренера (як друг-тренер) і одну конкретну пораду на цей тиждень."
            )
        except Exception as e:
            trigger_extra = f"\n🏃 Порівняння тижнів недоступне: {e}"
    elif trigger_type == "habit_checkin":
        trigger_extra = (
            "\n🌙 ВЕЧІРНІЙ CHECK-IN: запитай як минув день (настрій/звички/дисципліна), "
            "нагадай відмітити звички якщо ще не зробив, дай коротку теплу підтримку без нотацій."
        )

    # ── Shift context ─────────────────────────────────────────────────────────
    h = live["hour"]
    if 6 <= h < 12:
        period = "РАНОК — заряди на день"
    elif 12 <= h < 17:
        period = "ОБІД — перевір прогрес"
    elif 17 <= h < 22:
        period = "ВЕЧІР — підсумуй і відпочинь"
    else:
        period = "НІЧ — спокій і відновлення"

    # ── Довжина/структура залежно від теми: короткі алерти vs довгі звіти ─────
    SHORT_TRIGGERS = {"event_soon", "habit_checkin", "idle_timeout"}
    MEDIUM_TRIGGERS = {"crypto_move", "vip_email", "weekly_run_compare", "health"}
    # решта (morning/evening/deep_analysis/briefing тощо) — LONG за замовчуванням

    if trigger_type in SHORT_TRIGGERS:
        max_tokens = 350
        length_instruction = "Напиши КОРОТКЕ, чітке повідомлення 80-150 слів УКРАЇНСЬКОЮ — по суті, без води."
        structure_instruction = "Структура: 1 вступне речення → суть тригера → 1-2 конкретні дії. Коротко і ясно."
    elif trigger_type in MEDIUM_TRIGGERS:
        max_tokens = 700
        length_instruction = "Напиши ЗМІСТОВНЕ повідомлення 250-350 слів УКРАЇНСЬКОЮ — конкретний аналіз без зайвої води."
        structure_instruction = "Структура: 1 яскраве вступне речення → аналіз ключової теми тригера (2-3 абзаци) → 2-3 конкретні дії."
    else:
        max_tokens = 1400
        length_instruction = "Напиши ЖИВЕ, ОСОБИСТЕ повідомлення 500-650 слів УКРАЇНСЬКОЮ — повний розгорнутий аналіз."
        structure_instruction = "Структура: 1 яскраве вступне речення → РОЗГОРНУТИЙ аналіз кожного блоку (крипто, здоров'я, пошта, астро, календар) → 3-5 конкретних дій. Кожен блок аналізу — мінімум 3-4 речення з деталями та порадами."

    # ── Prompt ────────────────────────────────────────────────────────────────
    prompt = f"""Ти персональний AI-асистент Олега Новосадова (36р, Кошіце SK, Minebea Mitsumi, крипто-інвестор, бігун, ціль: фінансова незалежність + схуднення до 78 кг).

ТРИГЕР: {trigger_type} | ЧАС: {period}
СТИЛЬ: {tone['emoji']} {tone['tone']}
ЛОКАЦІЯ: {location}{trigger_extra}

━━━━━━━━━━━━━━━━━━ ПОТОЧНІ ЖИВІ ДАНІ ━━━━━━━━━━━━━━━━━━
{live_ctx}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{anti_repeat}

ЗАВДАННЯ:
{length_instruction}

Вимоги:
1. ВИКОРИСТАЙ реальні цифри з "ПОТОЧНІ ЖИВІ ДАНІ" — ціни, вагу, кроки, листи, події
0. КРИТИЧНО: для дат (пробіжка, події) використовуй ТІЛЬКИ слово СЬОГОДНІ/ВЧОРА/N ДНІВ ТОМУ, яке вказане в даних нижче — НІКОЛИ не вигадуй і не змінюй цю інформацію, навіть якщо здається що дата стара
2. НЕ пиши "Даних немає" — якщо дані є, використай; якщо немає — зроби розумний висновок без цієї фрази
3. Стиль ЖИВИЙ і ОСОБИСТИЙ — як старший друг, не корпоративний бот
4. Конкретні рекомендації — не "відпочинь", а "зроби X зараз"
5. {structure_instruction}
6. НЕ повторюй попередні теми (анти-репіт вище)
7. НЕ скорочуй штучно — пиши повний, завершений текст у вказаному обсязі

ПОЧНИ З: "Привіт Олеже! {tone['emoji']}"
"""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.85,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    message = _gemini_post(body, timeout=25, tag=f"MSG_{trigger_type.upper()}")

    # ── Fallback if Gemini fails ──────────────────────────────────────────────
    # Розширений fallback — використовує ВСІ доступні живі дані (не тільки BTC+вага),
    # щоб навіть без AI повідомлення було змістовним, а не голим шматком інформації.
    if not message:
        _log(f"⚠️ Gemini unavailable for {trigger_type} — using EXPANDED local fallback")
        crypto = live.get("crypto", {})
        health = live.get("health", {})
        strava = live.get("strava", {})
        emails = live.get("emails", [])
        calendar = live.get("calendar", [])
        now_str = live["now_str"]
        parts = [f"Привіт Олеже! {tone['emoji']} (AI зараз перевантажена — коротка версія на реальних даних)\n"]

        if crypto:
            crypto_lines = []
            for sym in ["BTC", "ETH", "AVAX", "ONDO", "SOL"]:
                d = crypto.get(sym)
                if d:
                    arrow = "📈" if d["change_24h"] > 0 else "📉"
                    crypto_lines.append(f"{arrow} {sym}: ${d['price']:,.2f} ({d['change_24h']:+.1f}%)")
            if crypto_lines:
                parts.append("💹 Крипто:\n" + "\n".join(f"  {l}" for l in crypto_lines))

        health_lines = []
        if health.get("weight"):
            delta = health.get("weight_7d_delta", 0)
            health_lines.append(f"⚖️ Вага: {health['weight']} кг ({delta:+.1f} кг за тиж)")
        if health.get("steps"):
            health_lines.append(f"🚶 Кроки: {health['steps']:,}")
        if health.get("sleep"):
            health_lines.append(f"😴 Сон: {health['sleep']} год")
        if health_lines:
            parts.append("\n" + "\n".join(health_lines))

        if strava and strava.get("last_run_km"):
            parts.append(f"\n🏃 Остання пробіжка: {strava['last_run_km']} км ({strava.get('last_run_date','?')}), темп {strava.get('last_run_pace','—')}")

        if emails:
            vip = [e for e in emails if e.get("starred")]
            if vip:
                parts.append(f"\n📬 Важливі листи: {len(vip)} (перевір Telegram-кнопки нижче)")
            elif emails:
                parts.append(f"\n📬 Нових листів: {len(emails)}")

        if calendar:
            parts.append(f"\n📅 Найближчі події: {'; '.join(str(e) for e in calendar[:2])}")

        parts.append(f"\n⏰ {now_str} | {location}")
        message = "\n".join(parts)

    return message

# ─── Telegram sender ─────────────────────────────────────────────────────────
def _send_to_telegram(text: str) -> bool:
    if not text or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    for attempt in range(3):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            body = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
            req = urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                r = json.loads(resp.read())
                if r.get("ok"):
                    _log(f"✅ Sent {len(text)} chars")
                    return True
                _log(f"TG error: {r.get('description','?')}")
        except Exception as e:
            _log(f"TG send {attempt+1}: {e}")
            time.sleep(2 + attempt * 2)
    return False

# ─── Main API ─────────────────────────────────────────────────────────────────
def process_trigger(trigger_type: str, trigger_data, location: str = "doma", idle_hours: float = 0) -> bool:
    _log(f"Processing: {trigger_type}")
    if not TELEGRAM_TOKEN:
        _log("❌ No TELEGRAM_TOKEN")
        return False
    if not GEMINI_API_KEY:
        _log("⚠️ No GEMINI_API_KEY — fallback only")

    try:
        if not _should_send_message(trigger_type, trigger_data):
            _log(f"Skipping {trigger_type}")
            return False

        message = _generate_message(trigger_type, trigger_data, location, idle_hours)
        if not message:
            return False

        success = _send_to_telegram(message)
        if success:
            # Save first 100 chars as "topic" for anti-repeat
            topic = message[:100].replace("\n", " ")
            _save_to_history(trigger_type, topic)
        return success
    except Exception as e:
        _log(f"❌ process_trigger: {e}")
        return False


if __name__ == "__main__":
    print("=== Message Generator v2.0 ===")
    live = _get_live_data()
    print("\n--- LIVE DATA ---")
    print(_build_live_context(live))
    print("\n--- HISTORY ---")
    hist = _load_history()
    for h in hist:
        print(f"  {h.get('ts','')[:16]} [{h.get('trigger','')}] {h.get('topic','')[:60]}")
    print("\n--- TONE VARIATIONS ---")
    for ttype in ["morning", "crypto_move", "vip_email", "health", "evening"]:
        for hr in [7, 13, 20]:
            v = get_tone_variation(ttype, hr)
            print(f"  {ttype:15} @{hr:02d}h → {v['emoji']} {v['tone']}")
