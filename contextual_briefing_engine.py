"""
Contextual Briefing Engine v1.0 — Dynamic, context-aware briefings
Не по розкладу, а коли актуально для Олега
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# ============ CONFIG ============

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GMAIL_USER = os.getenv("GMAIL_USER", "novosadovoleg@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_TZ = ZoneInfo("Europe/Bratislava")

_GEM_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
_GEM_MODEL_IDX = 0
_GEM_LAST_CALL = 0
_GEM_MIN_GAP = 4.0

_BRIEFING_STATE = {
    "last_sent": 0,
    "last_context_hash": None,
    "sent_themes": set(),
}

# ============ UTILS ============

def _log(msg):
    """Log з timestamp"""
    ts = datetime.now(tz=_TZ).strftime("%H:%M:%S")
    print(f"[BRIEFING {ts}] {msg}", flush=True)

def _gemini_post(body, timeout=20, tag="", max_retries=3):
    """Делегує до monitor._gem_post — СПІЛЬНИЙ rate-limiter на весь процес."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from monitor import _gem_post
        gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{_GEM_MODELS[0]}:generateContent?key={GEMINI_API_KEY}"
        resp = _gem_post(gemini_url, json.dumps(body).encode(), timeout=timeout, tag=tag or "contextual_briefing", max_retries=max_retries)
        if isinstance(resp, dict) and resp.get("candidates"):
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                return parts[0]["text"]
        _log("⚠️ Empty response from _gem_post")
    except Exception as e:
        _log(f"Gemini error: {e}")
    return None

# ============ DATA LOADERS ============

def load_calendar_data():
    """Отримати события на сьогодні/завтра"""
    try:
        cal_file = os.path.join(_DATA_DIR, "calendar.json")
        if not os.path.exists(cal_file):
            return {"today": [], "tomorrow": []}
        
        with open(cal_file) as f:
            data = json.load(f)
        
        return {
            "today": data.get("today", []),
            "tomorrow": data.get("tomorrow", []),
        }
    except Exception as e:
        _log(f"Calendar load error: {e}")
        return {"today": [], "tomorrow": []}

def load_health_data():
    """Отримати здоров'я (вага, біг, сон, кроки за місяць)"""
    try:
        health_file = os.path.join(_DATA_DIR, "daily_health.json")
        if not os.path.exists(health_file):
            return {}
        
        with open(health_file) as f:
            data = json.load(f)
        
        # Обчислити тренди
        entries = data.get("entries", {})
        if not entries:
            return {}
        
        dates = sorted(entries.keys())[-30:]  # Останні 30 днів
        
        weights = []
        runs = 0
        sleeps = []
        steps = []
        
        for date in dates:
            entry = entries[date]
            if "weight" in entry:
                weights.append(entry["weight"])
            if "run" in entry:
                runs += 1
            if "sleep_hours" in entry:
                sleeps.append(entry["sleep_hours"])
            if "steps" in entry:
                steps.append(entry["steps"])
        
        return {
            "weight_current": weights[-1] if weights else None,
            "weight_trend": (weights[-1] - weights[0]) if len(weights) > 1 else 0,
            "runs_month": runs,
            "sleep_avg": sum(sleeps) / len(sleeps) if sleeps else 0,
            "steps_avg": sum(steps) / len(steps) if steps else 0,
        }
    except Exception as e:
        _log(f"Health load error: {e}")
        return {}

def load_emails_summary():
    """Отримати SUMMARY важливих листів (останні 2-3 листи)"""
    try:
        import imaplib
        import email as email_lib
        from email.header import decode_header
        
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=10)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("INBOX")
        
        # Листи за останні 24h
        since_time = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f"SINCE {since_time}")
        
        if status != "OK" or not messages[0]:
            mail.close()
            return {"count": 0, "important": []}
        
        email_ids = messages[0].split()[-5:]
        important = []
        
        vip_keywords = {
            "boss": ["minebea", "mitsumi"],
            "investors": ["interfin", "maros", "sivak"],
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
                
                is_important = False
                for category, keywords in vip_keywords.items():
                    if any(kw.lower() in (sender + subject).lower() for kw in keywords):
                        is_important = True
                        break
                
                if is_important:
                    important.append({"from": sender[:40], "subject": subject[:60]})
            except:
                pass
        
        mail.close()
        return {"count": len(email_ids), "important": important}
    
    except Exception as e:
        _log(f"Email load error: {e}")
        return {"count": 0, "important": []}

def load_socials_status():
    """Статус соцмереж (коли був останній пост?)"""
    try:
        socials_file = os.path.join(_DATA_DIR, "socials_log.json")
        if not os.path.exists(socials_file):
            return {"facebook": None, "youtube": None}
        
        with open(socials_file) as f:
            data = json.load(f)
        
        return {
            "facebook": data.get("facebook_last_post"),
            "youtube": data.get("youtube_last_post"),
        }
    except:
        return {"facebook": None, "youtube": None}

def load_astro_brief():
    """Натальна карта & транзити"""
    try:
        astro_file = os.path.join(_DATA_DIR, "astro_brief.json")
        if not os.path.exists(astro_file):
            return {"natal": None, "today": None}
        
        with open(astro_file) as f:
            data = json.load(f)
        
        return data
    except:
        return {"natal": "Твоя натальна карта показує силу.", "today": None}

def load_goals_progress():
    """Прогрес до цілей"""
    try:
        goals_file = os.path.join(_DATA_DIR, "goals.json")
        if not os.path.exists(goals_file):
            return {}
        
        with open(goals_file) as f:
            data = json.load(f)
        
        return {
            "fi": data.get("financial_independence", {}),
            "weight": data.get("weight_loss", {}),
            "learning": data.get("learning", {}),
        }
    except:
        return {}

# ============ CONTEXT BUILDING ============

def build_full_context():
    """Збрати ВСЕ дані про поточний контекст"""
    _log("Building full context...")
    
    context = {
        "timestamp": datetime.now(tz=_TZ).isoformat(),
        "calendar": load_calendar_data(),
        "health": load_health_data(),
        "emails": load_emails_summary(),
        "socials": load_socials_status(),
        "astro": load_astro_brief(),
        "goals": load_goals_progress(),
    }
    
    return context

def _hash_context(context):
    """Хеш контексту для детектування змін"""
    key_data = {
        "cal_today": len(context["calendar"].get("today", [])),
        "emails": context["emails"].get("count", 0),
        "health_weight": context["health"].get("weight_current"),
    }
    return str(key_data)

# ============ DECISION LOGIC ============

def should_send_briefing(context, user_location, idle_hours):
    """АІ вирішує: чи актуально ЗАРАЗ?"""
    try:
        now = datetime.now(tz=_TZ)
        hour = now.hour
        
        # Умови для відправки
        conditions = []
        
        # 1. Ранок (прокинулся) + щось на день
        if 6 <= hour < 9 and context["calendar"]["today"]:
            conditions.append("morning_has_events")
        
        # 2. На роботі + листи накопилися
        if user_location == "robota" and context["emails"]["important"]:
            conditions.append("work_has_important_emails")
        
        # 3. Після роботи (17-20) + здоров'я критичне
        if 17 <= hour < 21:
            health = context["health"]
            if health.get("sleep_avg", 0) < 6:
                conditions.append("evening_low_sleep")
            if health.get("weight_current", 0) > 84:
                conditions.append("evening_weight_high")
        
        # 4. Простій > 2h + можна активувати
        if idle_hours > 2:
            conditions.append("idle_long")
        
        # 5. Контекст змінився (нові листи, новий день)
        ctx_hash = _hash_context(context)
        if ctx_hash != _BRIEFING_STATE["last_context_hash"]:
            conditions.append("context_changed")
        
        # 6. Давно не надсилали briefing (> 4h)
        if time.time() - _BRIEFING_STATE["last_sent"] > 14400:
            conditions.append("timeout_4h")
        
        result = len(conditions) > 0
        _log(f"Should send briefing? {result} (conditions: {conditions})")
        
        return result
    
    except Exception as e:
        _log(f"Decision error: {e}")
        return False

# ============ BRIEFING GENERATION ============

def group_themes(context):
    """Групує теми (спільно якщо пов'язано)"""
    themes = []
    
    # 📅 Календар
    if context["calendar"]["today"]:
        themes.append(("calendar", context["calendar"]["today"]))
    
    # 🏃 Здоров'я
    if context["health"]:
        themes.append(("health", context["health"]))
    
    # 📧 Листи
    if context["emails"]["important"]:
        themes.append(("emails", context["emails"]["important"]))
    
    # 📱 Соцмережи
    if context["socials"]["facebook"] or context["socials"]["youtube"]:
        themes.append(("socials", context["socials"]))
    
    # 🌙 Астро + 🎯 Цілі (спільно)
    if context["astro"] or context["goals"]:
        themes.append(("astro_goals", {"astro": context["astro"], "goals": context["goals"]}))
    
    _log(f"Grouped into {len(themes)} themes")
    return themes

def generate_briefing(context, themes):
    """Генерує персональний briefing через Gemini"""
    try:
        theme_descs = []
        for ttype, tdata in themes:
            if ttype == "calendar":
                theme_descs.append(f"📅 Календар: {len(tdata)} подій")
            elif ttype == "health":
                theme_descs.append(f"🏃 Здоров'я: вага {tdata.get('weight_current')}kg, сон {tdata.get('sleep_avg'):.1f}h")
            elif ttype == "emails":
                theme_descs.append(f"📧 Листи: {len(tdata)} важливих")
            elif ttype == "socials":
                theme_descs.append(f"📱 Соцмережи: Facebook/YouTube")
            elif ttype == "astro_goals":
                theme_descs.append(f"🌙 Астро + 🎯 Цілі")
        
        prompt = f"""Ти персональний AI помічник Олега. Напиши ОДИН персональний briefing (300-400 слів, українською).

КОНТЕКСТ:
{json.dumps(context, indent=2, ensure_ascii=False)[:1500]}

ТЕМИ ДЛЯ АНАЛІЗУ:
{', '.join(theme_descs)}

СТИЛЬ:
- Адресуйся: "Привіт Олеже" або "Олеже,"
- Теплий, дружелюбний тон
- Розповідай що ти бачиш, аналізуй, давай конкретні рекомендації
- Формат: "Я помітив X, рекомендую Y"
- Емодзі для читаності
- Закінчи з 1-2 конкретними діями

ПРИКЛАД СТИЛЮ:
"Привіт Олеже! Я проаналізував твій день і ось що помітив:

🏃 ЗДОРОВ'Я
Сон вчора був 5.2h — критично нижче норми (7.5h). Це впливає на енергію.
💡 Рекомендація: сьогодні постарайся лягти на годину раніше.

📧 ЛИСТИ
От InterFin новина про портфель. Терміновість? Не критична, але бажано відповісти до вечора.
💡 Готова відповідь: "Дякую! Переглядаю наступного дня..."

═══════════════════════════════════
ДІЯ #1: Лег сьогодні раніше на 1h
ДІЯ #2: Відповідь InterFin до вечора
"

Напиши brieing за цим стилем:"""
        
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 800,
                "temperature": 0.85,
                "thinkingConfig": {"thinkingBudget": 0}
            }
        }
        
        briefing = _gemini_post(
            body,
            timeout=25,
            tag="BRIEFING_GEN"
        )
        
        if not briefing:
            briefing = "📝 Контекст анаміз не вдалось. Але я вірю у тебе, Олеже! 💪"
        
        return briefing
    
    except Exception as e:
        _log(f"Briefing generation error: {e}")
        return None

# ============ MAIN ============

def get_contextual_briefing(user_location="doma", idle_hours=0):
    """
    Отримати персональний contextual briefing або None
    
    Returns:
        (briefing_text, themes) або (None, None)
    """
    _log(f"Checking contextual briefing (location={user_location}, idle={idle_hours}h)...")
    
    # 1. Збрати контекст
    context = build_full_context()
    
    # 2. Вирішити чи надсилати
    if not should_send_briefing(context, user_location, idle_hours):
        return None, None
    
    # 3. Групувати теми
    themes = group_themes(context)
    
    if not themes:
        _log("No themes to brief about")
        return None, None
    
    # 4. Генерувати briefing
    briefing = generate_briefing(context, themes)
    
    if briefing:
        _BRIEFING_STATE["last_sent"] = time.time()
        _BRIEFING_STATE["last_context_hash"] = _hash_context(context)
        _log(f"✅ Briefing generated ({len(briefing)} chars)")
    
    return briefing, themes

if __name__ == "__main__":
    print("Testing contextual briefing engine...")
    
    briefing, themes = get_contextual_briefing(user_location="doma", idle_hours=2)
    
    if briefing:
        print(f"\n📋 BRIEFING:\n{briefing}")
        print(f"\nThemes: {themes}")
    else:
        print("No briefing triggered")
