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
    """Gemini запит з fallback"""
    global _GEM_MODEL_IDX, _GEM_LAST_CALL
    
    now = time.time()
    gap = now - _GEM_LAST_CALL
    if gap < _GEM_MIN_GAP:
        time.sleep(_GEM_MIN_GAP - gap)
    _GEM_LAST_CALL = time.time()
    
    for attempt in range(max_retries):
        try:
            model = _GEM_MODELS[_GEM_MODEL_IDX % len(_GEM_MODELS)]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
            
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                if "candidates" not in data or not data["candidates"]:
                    _GEM_MODEL_IDX += 1
                    continue
                
                parts = data["candidates"][0].get("content", {}).get("parts", [])
                if not parts or "text" not in parts[0]:
                    _GEM_MODEL_IDX += 1
                    continue
                
                return parts[0]["text"]
        except Exception as e:
            _GEM_MODEL_IDX += 1
            time.sleep(2)
    
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
    """BTC/ETH цінові дані"""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,avalanche-2,ondo&vs_currencies=usd&include_24h_change=true"
        req = urllib.request.Request(url, headers={"User-Agent": "OlegBot"})
        
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            
            return {
                "BTC": {
                    "price": data.get("bitcoin", {}).get("usd"),
                    "change_24h": data.get("bitcoin", {}).get("usd_24h_change", 0),
                },
                "ETH": {
                    "price": data.get("ethereum", {}).get("usd"),
                    "change_24h": data.get("ethereum", {}).get("usd_24h_change", 0),
                },
            }
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
