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
    global _GEM_MODEL_IDX, _GEM_LAST_CALL
    now = time.time()
    gap = now - _GEM_LAST_CALL
    if gap < _GEM_MIN_GAP:
        time.sleep(_GEM_MIN_GAP - gap)
    _GEM_LAST_CALL = time.time()

    for attempt in range(4):
        model = _GEM_MODELS[_GEM_MODEL_IDX % len(_GEM_MODELS)]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                cands = data.get("candidates", [])
                if cands:
                    parts = cands[0].get("content", {}).get("parts", [])
                    if parts and parts[0].get("text"):
                        _GEM_LAST_CALL = time.time()
                        return parts[0]["text"]
                _log(f"{tag}: empty response, trying next model")
                _GEM_MODEL_IDX += 1
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _log(f"{tag}: 429 → switching model")
                _GEM_MODEL_IDX += 1
                time.sleep(6 + attempt * 4)
            else:
                _log(f"{tag}: HTTP {e.code}")
                time.sleep(3)
        except Exception as e:
            _log(f"{tag}: {e}")
            time.sleep(3)
    return ""

# ─── Live Data ────────────────────────────────────────────────────────────────
def _get_live_crypto() -> dict:
    """CoinGecko free API — BTC/ETH/AVAX/ONDO."""
    try:
        ids = "bitcoin,ethereum,avalanche-2,ondo"
        url = (f"https://api.coingecko.com/api/v3/simple/price"
               f"?ids={ids}&vs_currencies=usd"
               f"&include_24h_change=true&include_7d_change=true")
        req = urllib.request.Request(url, headers={"User-Agent": "SmartAssistantBot/2.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read())
        mapping = {"bitcoin": "BTC", "ethereum": "ETH", "avalanche-2": "AVAX", "ondo": "ONDO"}
        result = {}
        for cid, cname in mapping.items():
            if cid in raw:
                coin = raw[cid]
                result[cname] = {
                    "price":       round(coin.get("usd", 0), 2),
                    "change_24h":  round(coin.get("usd_24h_change", 0), 2),
                    "change_7d":   round(coin.get("usd_7d_change", 0), 2),
                }
        summary = ", ".join(f"{k}=${v['price']}({v['change_24h']:+.1f}%)" for k, v in result.items())
        _log(f"Crypto: {summary}")
        return result
    except Exception as e:
        _log(f"CoinGecko error: {e}")
        return {}

def _get_live_health() -> dict:
    """Latest weight, steps, sleep from data files."""
    result = {}
    try:
        # Weight
        wfile = os.path.join(_DATA_DIR, "weight.json")
        if os.path.exists(wfile):
            with open(wfile) as f:
                wdata = json.load(f)
            if wdata:
                latest = sorted(wdata.keys())[-1]
                result["weight"] = wdata[latest]
                result["weight_date"] = latest
                # Trend: compare with 7 days ago
                week_ago = (datetime.now(tz=_TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
                old_keys = [k for k in sorted(wdata.keys()) if k <= week_ago]
                if old_keys:
                    old_w = wdata[old_keys[-1]]
                    result["weight_7d_delta"] = round(result["weight"] - old_w, 1)
    except Exception as e:
        _log(f"Weight read error: {e}")

    try:
        # Health (steps, sleep)
        hfile = os.path.join(_DATA_DIR, "daily_health.json")
        if os.path.exists(hfile):
            with open(hfile) as f:
                hdata = json.load(f)
            entries = hdata.get("entries", hdata) if isinstance(hdata, dict) else {}
            if entries:
                today_str = datetime.now(tz=_TZ).strftime("%Y-%m-%d")
                yesterday = (datetime.now(tz=_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
                for day in [today_str, yesterday]:
                    if day in entries:
                        e = entries[day]
                        result["steps"]       = e.get("steps", 0)
                        result["sleep"]       = e.get("sleep_hours", 0)
                        result["health_date"] = day
                        break
    except Exception as e:
        _log(f"Health read error: {e}")

    return result

def _get_live_emails(max_emails: int = 5) -> list:
    """Last important emails via IMAP."""
    if not GMAIL_APP_PASS:
        return []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_USER, GMAIL_APP_PASS)
        mail.select("INBOX")
        since = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
        _, msgs = mail.search(None, f"SINCE {since}")
        if not msgs[0]:
            return []
        ids = msgs[0].split()[-max_emails:]
        result = []
        skip_kw = ["unsubscribe", "newsletter", "noreply", "no-reply",
                   "marketing", "promo", "notification", "доставка"]
        for eid in reversed(ids):
            _, data = mail.fetch(eid, "(RFC822)")
            msg = email_lib.message_from_bytes(data[0][1])
            sender  = _decode_str(msg.get("From", ""))
            subject = _decode_str(msg.get("Subject", ""))
            date    = msg.get("Date", "")
            if any(k in (sender + subject).lower() for k in skip_kw):
                continue
            result.append({"from": sender[:60], "subject": subject[:80], "date": date[:30]})
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
    emails   = _get_live_emails(max_emails=4)
    calendar = _get_live_calendar()
    now      = datetime.now(tz=_TZ)
    return {
        "crypto":   crypto,
        "health":   health,
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
        c24 = d["change_24h"]
        c7  = d["change_7d"]
        arrow = "📈" if c24 > 0 else "📉"
        lines.append(f"  {arrow} {coin}: ${d['price']:,.2f} ({c24:+.1f}% за 24г, {c7:+.1f}% за тиж)")
    return "Крипто ЗАРАЗ:\n" + "\n".join(lines)

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
    lines = [f"  • {e['from'][:40]} — {e['subject']}" for e in emails[:4]]
    return "Нові листи:\n" + "\n".join(lines)

def _calendar_text(events: list) -> str:
    if not events:
        return "Календар: найближчих подій немає"
    return "Найближчі події:\n" + "\n".join(f"  {e}" for e in events[:5])

def _build_live_context(data: dict) -> str:
    parts = [
        f"📅 {data['weekday']}, {data['now_str']}",
        "",
        _crypto_text(data.get("crypto", {})),
        "",
        _health_text(data.get("health", {})),
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
    }
    base = variations.get(trigger_type, [{"tone": "дружелюбний, особистий", "emoji": "👋"}])
    idx = (hour + abs(hash(trigger_type))) % len(base)
    return base[idx]

# ─── Decision ────────────────────────────────────────────────────────────────
def _should_send_message(trigger_type: str, trigger_data) -> bool:
    always = {"vip_email", "deep_analysis", "briefing", "contextual_briefing",
              "morning", "evening", "health"}
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
    if trigger_type == "deep_analysis" and _DEEP_ANALYSIS_AVAILABLE:
        try:
            result = build_deep_analysis(location, idle_hours)
            if result:
                return result
        except Exception as e:
            _log(f"deep_analysis failed: {e}")

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
            trigger_extra = f"\n⏰ НЕЗАБАРОМ: {evts}"
    elif trigger_type == "idle_timeout":
        trigger_extra = f"\n😴 Олег неактивний {idle_hours:.1f} год | Локація: {location}"

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
Напиши ЖИВЕ, ОСОБИСТЕ повідомлення 250-350 слів УКРАЇНСЬКОЮ.

Вимоги:
1. ВИКОРИСТАЙ реальні цифри з "ПОТОЧНІ ЖИВІ ДАНІ" — ціни, вагу, кроки, листи, події
2. НЕ пиши "Даних немає" — якщо дані є, використай; якщо немає — зроби розумний висновок без цієї фрази
3. Стиль ЖИВИЙ і ОСОБИСТИЙ — як старший друг, не корпоративний бот
4. Конкретні рекомендації — не "відпочинь", а "зроби X зараз"
5. Структура: 1 яскраве вступне речення → основний аналіз → 2-3 конкретні дії
6. НЕ повторюй попередні теми (анти-репіт вище)
7. Максимум 400 слів — якість важливіша за кількість

ПОЧНИ З: "Привіт Олеже! {tone['emoji']}"
"""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 700,
            "temperature": 0.85,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    message = _gemini_post(body, timeout=25, tag=f"MSG_{trigger_type.upper()}")

    # ── Fallback if Gemini fails ──────────────────────────────────────────────
    if not message:
        crypto = live.get("crypto", {})
        health = live.get("health", {})
        now_str = live["now_str"]
        parts = [f"Привіт Олеже! {tone['emoji']}\n"]
        if crypto:
            btc = crypto.get("BTC", {})
            if btc:
                parts.append(f"💹 BTC зараз: ${btc['price']:,.0f} ({btc['change_24h']:+.1f}% за 24г)")
        if health.get("weight"):
            delta = health.get("weight_7d_delta", 0)
            parts.append(f"⚖️ Вага: {health['weight']} кг ({delta:+.1f} кг за тиж)")
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
