"""
Smart Notifications v3.0 — Proactive AI-generated messages for 4 daily schedules
Замість список-даних → повністю Gemini-аналізи (300-400 слів per блок)
"""

import os
import json
import time
import re
import imaplib
import email as email_lib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from email.header import decode_header

try:
    from recommendations_engine import get_recommendations_for_schedule
    _RECOMMENDATIONS_AVAILABLE = True
except ImportError:
    _RECOMMENDATIONS_AVAILABLE = False
    print("⚠️ recommendations_engine not available", flush=True)

# ============ CONFIG ============

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GMAIL_USER = os.getenv("GMAIL_USER", "novosadovoleg@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "2100366814")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_LOOKBACK_DAYS = 30
_TZ = ZoneInfo("Europe/Bratislava")

VIP_KEYWORDS = {
    "boss": ["minebea", "mitsumi", "director", "manager", "ceo"],
    "investors": ["interfin", "maros", "sivak", "invest"],
    "hr": ["hr", "recruit", "interview", "job", "position"],
}

# ============ UTILS ============

def _log(msg):
    """Log з timestamp"""
    ts = datetime.now(tz=_TZ).strftime("%H:%M:%S")
    print(f"[SMART_NOTIF {ts}] {msg}")

def _send_to_telegram(text):
    """Надішліть повідомлення до Telegram"""
    if not text:
        return False
    
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        _log("Telegram credentials missing")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        body = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                _log(f"Sent {len(text)} chars to Telegram")
                return True
            else:
                _log(f"Telegram error: {result.get('description', 'unknown')}")
                return False
    except Exception as e:
        _log(f"Telegram send error: {e}")
        return False

def _load_json(path):
    """Завантажити JSON файл"""
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except:
            pass
    return {}

def _save_json(path, data):
    """Зберегти JSON файл"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        _log(f"Save JSON error: {e}")

def _decode_header(header_str):
    """Декодує заголовок email"""
    if not header_str:
        return ""
    try:
        decoded_parts = []
        for part, charset in decode_header(header_str):
            if isinstance(part, bytes):
                decoded_parts.append(part.decode(charset or 'utf-8', errors='ignore'))
            else:
                decoded_parts.append(str(part))
        return "".join(decoded_parts)
    except:
        return str(header_str)

# ============ EMAIL DATA ============

def _get_important_emails(max_emails=5):
    """Отримати важливі листи за останні дні"""
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=10)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("INBOX")
        
        # Пошук листів за останні дні
        date_7_days_ago = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'SINCE {date_7_days_ago}')
        
        if status != 'OK' or not messages[0]:
            return []
        
        email_ids = messages[0].split()[-max_emails:]
        important = []
        
        for email_id in email_ids:
            status, msg_data = mail.fetch(email_id, '(RFC822)')
            if status != 'OK':
                continue
            
            msg = email_lib.message_from_bytes(msg_data[0][1])
            sender = _decode_header(msg.get("From", ""))
            subject = _decode_header(msg.get("Subject", ""))
            
            # Позначення VIP
            is_vip = False
            for category, keywords in VIP_KEYWORDS.items():
                if any(kw.lower() in (sender + subject).lower() for kw in keywords):
                    is_vip = True
                    break
            
            important.append({
                "from": sender,
                "subject": subject,
                "vip": is_vip,
                "date": msg.get("Date", ""),
            })
        
        mail.close()
        mail.logout()
        return important
    except Exception as e:
        _log(f"Gmail error: {e}")
        return []

# ============ CRYPTO DATA ============

def _get_crypto_prices():
    """CoinGecko: BTC, ETH, AVAX, ONDO. Кешується через monitor.fetch_json_cached (60с)
    — уникає burst 429 коли 4 щоденні розклади + event-listener дзвонять паралельно."""
    try:
        import sys as _sys_cp
        _sys_cp.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from monitor import fetch_json_cached

        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,avalanche-2,ondo-finance&vs_currencies=usd&include_24h_change=true&include_market_cap=true"
        data = fetch_json_cached(url, ttl=60)
        if not data:
            return {}

        result = {}
        for coin_id, coin_name in [("bitcoin", "BTC"), ("ethereum", "ETH"), ("avalanche-2", "AVAX"), ("ondo-finance", "ONDO")]:
            if coin_id in data:
                coin = data[coin_id]
                result[coin_name] = {
                    "price": coin.get("usd", 0),
                    "change_24h": coin.get("usd_24h_change", 0),
                    "market_cap": coin.get("usd_market_cap", 0),
                }

        return result
    except Exception as e:
        _log(f"CoinGecko error: {e}")
        return {}

# ============ HEALTH DATA ============

def _get_health_summary():
    """Отримати останні дані здоров'я (вага, кроки, сон)"""
    health_file = os.path.join(_DATA_DIR, "health.json")
    weight_file = os.path.join(_DATA_DIR, "weight.json")
    
    health = _load_json(health_file)
    weight_data = _load_json(weight_file)
    
    today_str = datetime.now(tz=_TZ).strftime("%Y-%m-%d")
    
    steps = 0
    sleep_hours = 0
    
    if health and today_str in health:
        today_health = health[today_str]
        steps = today_health.get("steps", 0)
        sleep_hours = today_health.get("sleep_hours", 0)
    
    current_weight = None
    if weight_data:
        # Останнє значення
        latest_date = max(weight_data.keys())
        current_weight = weight_data[latest_date]
    
    return {
        "steps": steps,
        "sleep_hours": sleep_hours,
        "current_weight": current_weight,
    }

# ============ CALENDAR DATA ============

def _get_upcoming_events(days_ahead=7):
    """Отримати упсcoming события з Google Calendar"""
    # Зараз повертаємо пусто, бо треба OAuth
    # TODO: Інтегрувати з Google Calendar API (google-auth-httplib2)
    return []

# ============ GEMINI ANALYSIS ============

_GEM_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
_GEM_MODEL_IDX = 0
_GEM_LAST_CALL = 0
_GEM_MIN_GAP = 4.0

def _gemini_post(url, body, timeout=20, tag=""):
    """Делегує до monitor._gem_post — СПІЛЬНИЙ rate-limiter на весь процес."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from monitor import _gem_post
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{_GEM_MODELS[0]}:generateContent?key={GEMINI_API_KEY}"
        resp = _gem_post(gemini_url, json.dumps(body).encode(), timeout=timeout, tag=tag or "smart_notif", max_retries=3)
        if isinstance(resp, dict) and resp.get("candidates"):
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"]
        _log(f"{tag}: empty response from _gem_post")
    except Exception as e:
        _log(f"{tag}: Error {e}")
    return ""

def _analyze_morning(emails, crypto, health, events):
    """Ранок (6am): Обзор дня, крипто, здоровя"""
    health_hint = f"Current weight: {health.get('current_weight', 'N/A')} kg, Yesterday sleep: {health.get('sleep_hours', 0)}h"
    
    prompt = f"""You are Oleh's smart morning assistant. Write a brief, motivating morning message (250 words, Ukrainian).

CONTEXT:
- Time: 6:00 AM (start of day in Kosice)
- Health: {health_hint}
- Upcoming events: {len(events)} events
- Important emails: {len([e for e in emails if e['vip']])} VIP emails
- Crypto prices: {json.dumps(crypto, indent=2)}

WRITE A MESSAGE THAT:
1. Greets Oleh warmly (Привіт Олеже!)
2. Summarizes key events for today
3. Highlights key cryptocurrency moves (BTC, ETH, AVAX, ONDO)
4. Gives 1-2 health/fitness tips based on yesterday's data
5. Sets positive tone for the day

TONE: Motivating, professional, supportive. Use emojis appropriately.
LANGUAGE: Ukrainian (Українська)
FORMAT: Plain text, no markdown."""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 500,
            "temperature": 0.7,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    result = _gemini_post(
        "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        body,
        timeout=15,
        tag="MORNING_AI"
    )
    
    if not result:
        result = f"🌅 Привіт Олеже! Новий день для нових можливостей.\n💪 Крипто: BTC ${crypto.get('BTC', {}).get('price', 'N/A')} (сьогодні буде цікаво)\n✅ Разом ми досягнемо цілей!"
    
    return result

def _get_portfolio_hint() -> str:
    """Короткий контекст портфеля для lunch/evening промптів."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from portfolio import get_portfolio_summary
        s = get_portfolio_summary()
        tv = s.get("total_value", 0)
        pnl = s.get("total_pnl")
        ch = s.get("change_24h", 0)
        parts = [f"Портфель: ${tv:,.0f}"]
        if pnl is not None:
            parts.append(f"P&L: {pnl:+,.0f} USD")
        parts.append(f"Зміна 24г: {ch:+,.0f} USD")
        return ", ".join(parts)
    except Exception:
        return "Портфель: дані недоступні"

def _analyze_lunch(emails, crypto, health):
    """Обід (12pm): Email VIP, крипто, здоровя обід, фінанси/портфель"""
    vip_summary = "\n".join([f"- {e['from']}: {e['subject']}" for e in emails if e['vip']][:3])
    portfolio_hint = _get_portfolio_hint()
    
    prompt = f"""You are Oleh's smart midday assistant. Write a brief update message (250-300 words, Ukrainian).

CONTEXT:
- Time: 12:00 PM (lunch time)
- VIP emails today:
{vip_summary or "- No VIP emails"}
- Crypto updates: {json.dumps(crypto, indent=2)}
- Portfolio: {portfolio_hint}
- Steps so far: {health.get('steps', 0)}

WRITE A MESSAGE THAT:
1. Summarizes important emails (focus on VIP/boss/investors)
2. Highlights crypto movements worth watching
3. Gives a quick portfolio/finance progress note (toward financial independence goal)
4. Encourages lunch & hydration break
5. Suggests 1 quick action if needed

TONE: Professional, helpful, brief but informative.
LANGUAGE: Ukrainian.
FORMAT: Plain text."""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 550,
            "temperature": 0.7,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    result = _gemini_post(
        "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        body,
        timeout=15,
        tag="LUNCH_AI"
    )
    
    if not result:
        result = f"☀️ Полудень! Час на обід.\n📧 Важливі листи: {len([e for e in emails if e['vip']])} VIP\n💵 Крипто: все на місці"
    
    return result

def _get_shift_hint() -> str:
    """Контекст зміни (рання/нічна/вихідний) для afternoon/evening промптів."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from habits import _get_shift_type
        shift = _get_shift_type()
        labels = {"early": "рання зміна (06:00-18:00)", "night": "нічна зміна (18:00-06:00)", "weekend": "вихідний/без зміни"}
        return labels.get(shift, "невідомо")
    except Exception:
        return "невідомо"

def _get_traffic_hint() -> str:
    """Короткий контекст трафіку/погоди Кошице для afternoon промпту."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from traffic_kosice import format_traffic_report
        return (format_traffic_report() or "")[:300]
    except Exception:
        return "дані про трафік недоступні"

def _analyze_afternoon(emails, crypto, health, events):
    """Після обід (3pm): Рекомендації, планування, робота/зміни, погода/трафік"""
    shift_hint = _get_shift_hint()
    traffic_hint = _get_traffic_hint()
    prompt = f"""You are Oleh's smart afternoon assistant. Write a brief recommendations message (250-300 words, Ukrainian).

CONTEXT:
- Time: 3:00 PM (afternoon productivity window)
- Work shift today: {shift_hint}
- Progress today: {health.get('steps', 0)} steps
- Unread VIP emails: {len([e for e in emails if e['vip']])}
- Upcoming events: {len(events)} scheduled
- Crypto trend: {'Up' if all(crypto.get(c, {}).get('change_24h', 0) > 0 for c in ['BTC', 'ETH']) else 'Mixed'}
- Traffic/weather Košice: {traffic_hint}

WRITE A MESSAGE THAT:
1. Encourages afternoon productivity, accounting for his work shift (early/night/off)
2. Suggests 2-3 priority actions for the rest of the day, balanced with the shift
3. Reminds about crypto price points to watch
4. Mentions traffic/weather if relevant to his commute
5. Promotes activity goal (10k steps)

TONE: Practical, motivating, action-oriented.
LANGUAGE: Ukrainian.
FORMAT: Plain text."""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 550,
            "temperature": 0.7,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    result = _gemini_post(
        "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        body,
        timeout=15,
        tag="AFTERNOON_AI"
    )
    
    if not result:
        result = f"⚡ Полудень крок! Ще {10000 - health.get('steps', 0)} кроків до цілі.\n📌 Крипто на радарі: ONDO, AVAX\n🎯 Ви на правильному шляху!"
    
    return result

def _analyze_evening(emails, crypto, health, astro_brief=""):
    """Вечір (8pm): День summary, астро, мотивація, фінансовий підсумок"""
    portfolio_hint = _get_portfolio_hint()
    prompt = f"""You are Oleh's smart evening assistant. Write a reflective summary message (300-400 words, Ukrainian).

CONTEXT:
- Time: 8:00 PM (end of work shift / evening)
- Daily steps: {health.get('steps', 0)}
- Daily sleep goal: 7-8h (last night: {health.get('sleep_hours', 0)}h)
- Weight: {health.get('current_weight', 'N/A')} kg
- Processed emails: {len(emails)} total, {len([e for e in emails if e['vip']])} VIP
- Crypto 24h changes: {json.dumps({k: v.get('change_24h', 0) for k, v in crypto.items()}, indent=2)}
- Portfolio/finance: {portfolio_hint}
- Astro brief: {astro_brief or "Use your knowledge of his birth chart"}

WRITE A MESSAGE THAT:
1. Reflects on the day's achievements
2. Acknowledges crypto movements (winners/losers)
3. Gives a short finance/portfolio progress note (toward financial independence goal)
4. Celebrates progress (steps, email management, etc.)
5. Gives 1-2 evening/next-day tips
6. Ends with an astrological insight or motivation

TONE: Reflective, supportive, closing-the-day vibe.
LANGUAGE: Ukrainian.
FORMAT: Plain text with emojis."""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 700,
            "temperature": 0.7,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    result = _gemini_post(
        "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        body,
        timeout=15,
        tag="EVENING_AI"
    )
    
    if not result:
        result = f"""🌙 Вечір приходить...
День фінішу! Ви досягли {health.get('steps', 0)} кроків.
💵 Крипто: BTC ${crypto.get('BTC', {}).get('price', 'N/A')}
✨ Завтра буде краще. Спокійної ночі! 🌟"""
    
    return result

# ============ MAIN CALLBACKS ============

def handle_morning_schedule(schedule_name, now_tz):
    """Called by scheduler at 6:00 AM"""
    _log(f"=== {schedule_name.upper()} ANALYSIS START ===")
    
    try:
        emails = _get_important_emails(5)
        crypto = _get_crypto_prices()
        health = _get_health_summary()
        events = _get_upcoming_events(1)
        
        message = _analyze_morning(emails, crypto, health, events)
        _log(f"Generated: {len(message)} chars")
        
        # Add recommendations
        if _RECOMMENDATIONS_AVAILABLE:
            try:
                recs = get_recommendations_for_schedule("morning")
                if recs:
                    message += "\n\n🎯 МОЇ РЕКОМЕНДАЦІЇ:\n" + recs
                    _log(f"Added recommendations: {len(recs)} chars")
            except Exception as e:
                _log(f"⚠️ Recommendations failed: {e}")
        
        if message:
            ok = _send_to_telegram(message)
            _log(f"Sent to Telegram: {ok}")
        
        return message
    except Exception as e:
        _log(f"❌ ERROR in handle_morning_schedule: {e}")
        return ""

def handle_lunch_schedule(schedule_name, now_tz):
    """Called by scheduler at 12:00 PM"""
    _log(f"=== {schedule_name.upper()} ANALYSIS START ===")
    
    try:
        emails = _get_important_emails(5)
        crypto = _get_crypto_prices()
        health = _get_health_summary()
        
        message = _analyze_lunch(emails, crypto, health)
        _log(f"Generated: {len(message)} chars")
        
        # Add recommendations
        if _RECOMMENDATIONS_AVAILABLE:
            try:
                recs = get_recommendations_for_schedule("lunch")
                if recs:
                    message += "\n\n🎯 МОЇ РЕКОМЕНДАЦІЇ:\n" + recs
                    _log(f"Added recommendations: {len(recs)} chars")
            except Exception as e:
                _log(f"⚠️ Recommendations failed: {e}")
        
        if message:
            ok = _send_to_telegram(message)
            _log(f"Sent to Telegram: {ok}")
        
        return message
    except Exception as e:
        _log(f"❌ ERROR in handle_lunch_schedule: {e}")
        return ""

def handle_afternoon_schedule(schedule_name, now_tz):
    """Called by scheduler at 3:00 PM"""
    _log(f"=== {schedule_name.upper()} ANALYSIS START ===")
    
    try:
        emails = _get_important_emails(3)
        crypto = _get_crypto_prices()
        health = _get_health_summary()
        events = _get_upcoming_events(1)
        
        message = _analyze_afternoon(emails, crypto, health, events)
        _log(f"Generated: {len(message)} chars")
        
        # Add recommendations
        if _RECOMMENDATIONS_AVAILABLE:
            try:
                recs = get_recommendations_for_schedule("afternoon")
                if recs:
                    message += "\n\n🎯 МОЇ РЕКОМЕНДАЦІЇ:\n" + recs
                    _log(f"Added recommendations: {len(recs)} chars")
            except Exception as e:
                _log(f"⚠️ Recommendations failed: {e}")
        
        if message:
            ok = _send_to_telegram(message)
            _log(f"Sent to Telegram: {ok}")
        
        return message
    except Exception as e:
        _log(f"❌ ERROR in handle_afternoon_schedule: {e}")
        return ""

def handle_evening_schedule(schedule_name, now_tz):
    """Called by scheduler at 8:00 PM"""
    _log(f"=== {schedule_name.upper()} ANALYSIS START ===")
    
    try:
        emails = _get_important_emails(7)
        crypto = _get_crypto_prices()
        health = _get_health_summary()
        
        # TODO: Load astro brief from astro.py
        astro = "Твоя натальна карта показує силу і потенціал."
        
        message = _analyze_evening(emails, crypto, health, astro)
        _log(f"Generated: {len(message)} chars")
        
        # Add recommendations
        if _RECOMMENDATIONS_AVAILABLE:
            try:
                recs = get_recommendations_for_schedule("evening")
                if recs:
                    message += "\n\n🎯 МОЇ РЕКОМЕНДАЦІЇ:\n" + recs
                    _log(f"Added recommendations: {len(recs)} chars")
            except Exception as e:
                _log(f"⚠️ Recommendations failed: {e}")
        
        if message:
            ok = _send_to_telegram(message)
            _log(f"Sent to Telegram: {ok}")
        
        return message
    except Exception as e:
        _log(f"❌ ERROR in handle_evening_schedule: {e}")
        return ""
    
    return message

# ============ EXPORTS ============

CALLBACKS = {
    "morning": handle_morning_schedule,
    "lunch": handle_lunch_schedule,
    "afternoon": handle_afternoon_schedule,
    "evening": handle_evening_schedule,
}

if __name__ == "__main__":
    # TEST: Load & analyze
    emails = _get_important_emails(3)
    crypto = _get_crypto_prices()
    health = _get_health_summary()
    
    print("=== MORNING ===")
    msg = _analyze_morning(emails, crypto, health, [])
    print(msg[:300])
