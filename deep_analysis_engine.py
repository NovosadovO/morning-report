"""
Deep Analysis Engine v4.0 — Comprehensive Life Context Analysis
Глибокий аналіз всіх сфер: календар, здоров'я, листи, соцмережи, крипто, астро, цілі
Персональний стиль: "Привіт Олеже, я помітив X, рекомендую Y"
"""

import os
import json
import time
import urllib.request
import urllib.error
import imaplib
import email as email_lib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from email.header import decode_header

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GMAIL_USER = os.getenv("GMAIL_USER", "novosadovoleg@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_TZ = ZoneInfo("Europe/Bratislava")

_GEM_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
_GEM_MODEL_IDX = 0
_GEM_LAST_CALL = 0
_GEM_MIN_GAP = 3.0

def _log(msg):
    ts = datetime.now(tz=_TZ).strftime("%H:%M:%S")
    print(f"[DEEP {ts}] {msg}", flush=True)

def _gemini_post(body, timeout=12, max_retries=2):
    """Делегує до monitor._gem_post — СПІЛЬНИЙ rate-limiter на весь процес."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from monitor import _gem_post
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{_GEM_MODELS[0]}:generateContent?key={GEMINI_API_KEY}"
        resp = _gem_post(url, json.dumps(body).encode(), timeout=timeout, tag="deep_analysis", max_retries=max(max_retries, 3))
        if isinstance(resp, dict) and resp.get("candidates"):
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"]
    except Exception as e:
        _log(f"Gemini error: {e}")
    return None

# ============ DATA LOADERS ============

def _load_calendar():
    """Отримати события сьогодні/завтра"""
    try:
        cal_file = os.path.join(_DATA_DIR, "calendar.json")
        if os.path.exists(cal_file):
            with open(cal_file) as f:
                return json.load(f)
    except:
        pass
    return {"today": [], "tomorrow": []}

def _load_health():
    """Здоров'я за місяць (вага, біг, сон, кроки)"""
    try:
        health_file = os.path.join(_DATA_DIR, "daily_health.json")
        if not os.path.exists(health_file):
            return {}
        
        with open(health_file) as f:
            data = json.load(f)
        
        entries = data.get("entries", {})
        if not entries:
            return {}
        
        dates = sorted(entries.keys())[-30:]
        
        weights = [entries[d].get("weight") for d in dates if "weight" in entries[d]]
        runs = sum(1 for d in dates if "run" in entries[d])
        sleeps = [entries[d].get("sleep_hours") for d in dates if "sleep_hours" in entries[d]]
        steps = [entries[d].get("steps") for d in dates if "steps" in entries[d]]
        
        return {
            "weight_current": weights[-1] if weights else None,
            "weight_start_month": weights[0] if weights else None,
            "weight_trend": (weights[-1] - weights[0]) if len(weights) > 1 else 0,
            "runs_month": runs,
            "sleep_avg": sum(sleeps) / len(sleeps) if sleeps else 0,
            "sleep_min": min(sleeps) if sleeps else 0,
            "steps_avg": sum(steps) / len(steps) if steps else 0,
            "entries_30d": len(dates),
        }
    except Exception as e:
        _log(f"Health load error: {e}")

    # Fallback: weight.json
    try:
        wfile = os.path.join(_DATA_DIR, "weight.json")
        if os.path.exists(wfile):
            with open(wfile) as f:
                wdata = json.load(f)
            if wdata:
                latest_key = sorted(wdata.keys())[-1]
                latest_w = wdata[latest_key]
                # Build minimal health dict if empty
                result = {
                    "weight_current": latest_w,
                    "weight_start_month": latest_w,
                    "weight_trend": 0,
                    "runs_month": 0,
                    "sleep_avg": 0,
                    "entries_30d": 1,
                }
                week_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
                old_keys = [k for k in sorted(wdata.keys()) if k <= week_ago]
                if old_keys:
                    result["weight_start_month"] = wdata[old_keys[-1]]
                    result["weight_trend"] = round(latest_w - wdata[old_keys[-1]], 1)
                _log(f"Weight fallback: {latest_w}kg from {latest_key}")
                return result
    except Exception as e2:
        _log(f"Weight fallback error: {e2}")
    return {}

def _load_emails():
    """Важливі листи за 24h"""
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=10)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("INBOX")
        
        since_time = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f"SINCE {since_time}")
        
        if status != "OK" or not messages[0]:
            mail.close()
            return []
        
        email_ids = messages[0].split()[-10:]
        emails = []
        
        vip_keywords = {
            "boss": ["minebea", "mitsumi", "директор"],
            "investor": ["interfin", "maros", "sivak"],
            "hr": ["hr", "recruit"],
        }
        
        for email_id in email_ids:
            try:
                status, msg_data = mail.fetch(email_id, "(RFC822)")
                if status != "OK":
                    continue
                
                msg = email_lib.message_from_bytes(msg_data[0][1])
                sender = decode_header(msg.get("From", ""))[0]
                if isinstance(sender, bytes):
                    sender = sender.decode()
                
                subject = decode_header(msg.get("Subject", ""))[0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                
                body_text = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body_text = part.get_payload(decode=True).decode()[:300]
                            break
                else:
                    body_text = msg.get_payload(decode=True).decode()[:300]
                
                category = "other"
                for cat, keywords in vip_keywords.items():
                    if any(kw.lower() in (sender + subject).lower() for kw in keywords):
                        category = cat
                        break
                
                emails.append({
                    "from": sender[:50],
                    "subject": subject[:80],
                    "preview": body_text[:150],
                    "category": category,
                })
            except:
                pass
        
        mail.close()
        return emails
    except Exception as e:
        _log(f"Email load error: {e}")
        return []

def _load_socials():
    """Статус соцмереж"""
    try:
        socials_file = os.path.join(_DATA_DIR, "socials_log.json")
        if os.path.exists(socials_file):
            with open(socials_file) as f:
                return json.load(f)
    except:
        pass
    return {"facebook_last_post": None, "youtube_last_post": None}

def _load_crypto():
    """BTC/ETH/AVAX/ONDO via /coins/markets (real 24h change)"""
    try:
        ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
        url = (
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&ids=" + ids +
            "&order=market_cap_desc&per_page=10&page=1&sparkline=false"
            "&price_change_percentage=24h"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "OlegBot/2.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read())
        mapping = {
            "bitcoin": "BTC", "ethereum": "ETH",
            "avalanche-2": "AVAX", "ondo-finance": "ONDO"
        }
        result = {}
        for coin in raw:
            cid = coin.get("id", "")
            if cid in mapping:
                result[mapping[cid]] = {
                    "price":      round(coin.get("current_price") or 0, 4),
                    "change_24h": round(coin.get("price_change_percentage_24h") or 0, 2),
                }
        return result
    except Exception as e:
        _log(f"Crypto load error: {e}")
        return {}

def _load_astro():
    """Натальна карта & транзити"""
    try:
        astro_file = os.path.join(_DATA_DIR, "astro_brief.json")
        if os.path.exists(astro_file):
            with open(astro_file) as f:
                return json.load(f)
    except:
        pass
    return {"natal": None, "today": None}

def _load_goals():
    """Цілі & прогрес"""
    try:
        goals_file = os.path.join(_DATA_DIR, "goals.json")
        if os.path.exists(goals_file):
            with open(goals_file) as f:
                return json.load(f)
    except:
        pass
    return {}

# ============ ANALYSIS ============

def _analyze_calendar(cal_data):
    """Аналіз календаря"""
    today_events = cal_data.get("today", [])
    tomorrow_events = cal_data.get("tomorrow", [])
    
    if not today_events and not tomorrow_events:
        return """📅 КАЛЕНДАР
Я бачу що у тебе нічого не заплановано сьогодні.
💡 Можливо час для:
  • Планування тижня
  • Довгої пробіжки
  • Аналізу інвестицій
"""
    
    analysis = "📅 КАЛЕНДАР\n"
    if today_events:
        analysis += f"Сьогодні {len(today_events)} подій:\n"
        for event in today_events[:3]:
            analysis += f"  • {event.get('title', 'Event')}\n"
    
    if tomorrow_events:
        analysis += f"\nЗавтра {len(tomorrow_events)} подій (планування!)\n"
    
    return analysis

def _analyze_health(health_data):
    """Аналіз здоров'я"""
    if not health_data:
        return "🏃 ЗДОРОВ'Я\nДаних немає, почни трекування!"
    
    analysis = "🏃 ЗДОРОВ'Я (місячний тренд)\n"
    
    # Вага
    if health_data.get("weight_current"):
        w_current = health_data["weight_current"]
        w_trend = health_data.get("weight_trend", 0)
        analysis += f"  ⚖️ Вага: {w_current}kg"
        if w_trend < -0.5:
            analysis += " ✓ Прогрес! (межень)"
        elif w_trend > 0.5:
            analysis += " ⚠️ Зростає"
        analysis += "\n"
    
    # Сон
    if health_data.get("sleep_avg"):
        s_avg = health_data["sleep_avg"]
        analysis += f"  😴 Сон: {s_avg:.1f}h (ціль 7.5h)"
        if s_avg < 6:
            analysis += " ❌ КРИТИЧНИЙ"
        elif s_avg < 7:
            analysis += " ⚠️ Низько"
        analysis += "\n"
    
    # Біг
    if health_data.get("runs_month"):
        runs = health_data["runs_month"]
        pct = (runs / 12) * 100
        analysis += f"  🏃 Біг: {runs}x/місяць (ціль 12) — {pct:.0f}%\n"
    
    # Рекомендації
    analysis += "\n💡 Рекомендації:\n"
    if health_data.get("sleep_avg", 0) < 6:
        analysis += "  • ПЕРШІСТЬ: Додай 1h сну сьогодні\n"
    if health_data.get("runs_month", 0) < 6:
        analysis += "  • Почни з 2x бігу/тиждень\n"
    if health_data.get("weight_current", 0) > 84:
        analysis += "  • Комбінація: біг + дефіцит 500cal\n"
    
    return analysis

def _analyze_emails(emails_list):
    """Аналіз листів"""
    if not emails_list:
        return "📧 ЛИСТИ\nНічого нового, ти на правильному шляху! ✓"
    
    analysis = "📧 ЛИСТИ\n"
    
    for email in emails_list[:3]:
        category = email.get("category", "other")
        sender = email.get("from", "Unknown")[:30]
        subject = email.get("subject", "No subject")[:50]
        
        analysis += f"\n  📬 {sender}\n     Тема: {subject}\n"
        
        if category == "investor":
            analysis += "     → Готова відповідь: 'Дякую! Переглядаю...'\n"
        elif category == "boss":
            analysis += "     → Терміноус, відповідь до дня\n"
    
    return analysis

def _analyze_socials(socials_data):
    """Аналіз соцмереж & контент ідеї"""
    last_fb = socials_data.get("facebook_last_post")
    last_yt = socials_data.get("youtube_last_post")
    
    days_since_fb = None
    days_since_yt = None
    
    if last_fb:
        try:
            last_date = datetime.fromisoformat(last_fb)
            days_since_fb = (datetime.now() - last_date).days
        except:
            pass
    
    if last_yt:
        try:
            last_date = datetime.fromisoformat(last_yt)
            days_since_yt = (datetime.now() - last_date).days
        except:
            pass
    
    analysis = "📱 СОЦМЕРЕЖИ\n"
    
    if days_since_fb and days_since_fb > 3:
        analysis += f"  🟦 Facebook: останній пост {days_since_fb} днів тому\n"
    if days_since_yt and days_since_yt > 5:
        analysis += f"  🟥 YouTube: останній пост {days_since_yt} днів тому\n"
    
    if (days_since_fb and days_since_fb > 3) or (days_since_yt and days_since_yt > 5):
        analysis += "\n💡 Ідеї постів:\n"
        analysis += "  1️⃣ 'ETF vs Крипто: що краще новачку?' (аналітична)\n"
        analysis += "  2️⃣ 'Мої 2 роки інвестування: уроки' (персональна)\n"
        analysis += "  3️⃣ 'Фондовий ринок: як почати?' (туторіал)\n"
    else:
        analysis += "Добра активність! 👍\n"
    
    return analysis

def _analyze_crypto(crypto_data):
    """Аналіз крипто"""
    if not crypto_data:
        return "💹 КРИПТО\nДанні недоступні"
    
    analysis = "💹 КРИПТО\n"
    
    for coin, data in crypto_data.items():
        price = data.get("price")
        change = data.get("change_24h", 0)
        
        if price:
            analysis += f"  {coin}: ${price:,.0f} "
            if change > 5:
                analysis += f"🟢 +{change:.1f}%\n"
            elif change < -5:
                analysis += f"🔴 {change:.1f}%\n"
            else:
                analysis += f"🟡 {change:+.1f}%\n"
    
    return analysis

def _analyze_astro(astro_data):
    """Аналіз астро"""
    analysis = "🌙 АСТРО\n"
    
    if astro_data.get("today"):
        analysis += f"{astro_data['today']}\n"
    else:
        analysis += "Венера в Близнюках — хороший день для комунікації! 💬\n"
    
    analysis += "💡 Рекомендація: Ідеально для листування, переговорів, мережевого будування.\n"
    
    return analysis

def _analyze_goals(goals_data):
    """Аналіз цілей & прогресу"""
    if not goals_data:
        return "🎯 ЦІЛІ\nІди до цілей! 💪"
    
    analysis = "🎯 ЦІЛІ\n"
    
    fi = goals_data.get("fi", {})
    if fi and fi.get("progress_percent"):
        analysis += f"  📈 Фінансова Незалежність: {fi['progress_percent']:.0f}% ✓\n"
    
    weight = goals_data.get("weight", {})
    if weight and weight.get("progress"):
        analysis += f"  ⚖️ Схуднення: {weight['progress']} ✓\n"
    
    return analysis

# ============ MAIN ============

def build_deep_analysis(location="doma", idle_hours=0):
    """Побудувати глибокий персональний аналіз"""
    _log("Building deep analysis...")
    
    # Завантажити дані
    cal = _load_calendar()
    health = _load_health()
    emails = _load_emails()
    socials = _load_socials()
    crypto = _load_crypto()
    astro = _load_astro()
    goals = _load_goals()
    
    # Аналізувати кожну сферу
    parts = [
        f"Привіт Олеже! 👋\n",
        "Ось що я помітив сьогодні:\n",
        "\n" + _analyze_calendar(cal),
        "\n" + _analyze_health(health),
        "\n" + _analyze_emails(emails),
        "\n" + _analyze_socials(socials),
        "\n" + _analyze_crypto(crypto),
        "\n" + _analyze_astro(astro),
        "\n" + _analyze_goals(goals),
    ]
    
    analysis = "\n".join(parts)
    
    # Якщо дуже короткий — спробуємо Gemini
    if len(analysis) < 500 and GEMINI_API_KEY:
        _log("Analysis too short, trying Gemini...")
        
        prompt = f"""Write a DEEP, PERSONALIZED briefing for Oleh (600-800 words, Ukrainian).

CONTEXT:
Calendar: {json.dumps(cal)[:300]}
Health: {json.dumps(health)[:300]}
Emails: {json.dumps(emails)[:300]}
Socials: {json.dumps(socials)[:300]}
Crypto: {json.dumps(crypto)[:300]}

STYLE:
- Address: "Привіт Олеже!" or "Олеже,"
- Warm, personal tone
- Specific observations: "Я бачу..."
- Concrete recommendations: "Рекомендую..."
- Ready templates where needed
- Format sections: 📅 🏃 📧 📱 💹 🌙 🎯
- End with 3 specific ACTIONS

START NOW:"""
        
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 1000,
                "temperature": 0.85,
                "thinkingConfig": {"thinkingBudget": 0}
            }
        }
        
        gemini_result = _gemini_post(body, timeout=15)
        if gemini_result:
            analysis = gemini_result
            _log(f"✅ Gemini analysis ({len(analysis)} chars)")
    
    _log(f"Analysis ready ({len(analysis)} chars)")
    return analysis

if __name__ == "__main__":
    print("Testing Deep Analysis Engine...\n")
    
    analysis = build_deep_analysis("doma", 1.5)
    print(analysis)
