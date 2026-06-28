"""
Aggressive Briefing Engine v3.0 — Much more active & responsive
Більш частові перевірки, м'якші умови, fallbacks
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_TZ = ZoneInfo("Europe/Bratislava")

_GEM_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
_GEM_MODEL_IDX = 0
_GEM_LAST_CALL = 0
_GEM_MIN_GAP = 4.0

_STATE = {
    "last_sent": 0,
    "last_check": 0,
    "check_count": 0,
}

def _log(msg):
    ts = datetime.now(tz=_TZ).strftime("%H:%M:%S")
    print(f"[BRIEFING3 {ts}] {msg}", flush=True)

def _gemini_post(body, timeout=15, max_retries=2):
    """Fast Gemini post with aggressive fallback"""
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
        
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _GEM_MODEL_IDX += 1
                time.sleep(3)
                continue
            _log(f"HTTP {e.code}")
            _GEM_MODEL_IDX += 1
        except Exception as e:
            _log(f"Gemini error: {str(e)[:50]}")
            time.sleep(2)
    
    return None

# ============ FALLBACK GENERATION ============

def _generate_fallback_briefing(context, location):
    """Локальна генерація якщо Gemini недоступна"""
    now = datetime.now(tz=_TZ)
    hour = now.hour
    
    parts = []
    
    # Ранок
    if 6 <= hour < 12:
        parts.append("🌅 РАНОК")
        cal = context.get("calendar", {})
        if cal.get("today"):
            parts.append(f"  📅 Сьогодні {len(cal['today'])} подій")
        else:
            parts.append("  📅 Ніяких планів — день вільний!")
        
        health = context.get("health", {})
        if health.get("weight_current"):
            parts.append(f"  ⚖️ Вага: {health['weight_current']}kg")
        if health.get("sleep_avg"):
            parts.append(f"  😴 Сон вчора: {health['sleep_avg']:.1f}h")
        
        parts.append("\n💡 Рекомендація: Почни день з води та розминки!")
    
    # Обід
    elif 12 <= hour < 17:
        parts.append("☀️ ОБІД")
        emails = context.get("emails", {})
        if emails.get("important"):
            parts.append(f"  📧 {len(emails['important'])} важливих листів")
        else:
            parts.append("  📧 Листи у порядку")
        
        parts.append("  💡 Рекомендація: Перевір листи, пообідай, пройдись 10 хв")
    
    # Вечір
    else:
        parts.append("🌙 ВЕЧІР")
        health = context.get("health", {})
        if health.get("sleep_avg", 0) < 6:
            parts.append(f"  😴 Сон критичний: {health['sleep_avg']:.1f}h")
            parts.append("  💡 Лягай на годину раніше!")
        else:
            parts.append("  😴 Сон у нормі")
        
        parts.append("  💡 Рекомендація: Вечір спокійний, хороший час для рефлексії")
    
    return "\n".join(parts)

# ============ CONTEXT ============

def _load_context():
    """Швидко збрати мінімальний контекст"""
    try:
        cal_file = os.path.join(_DATA_DIR, "calendar.json")
        calendar = {}
        if os.path.exists(cal_file):
            with open(cal_file) as f:
                calendar = json.load(f)
        
        health_file = os.path.join(_DATA_DIR, "daily_health.json")
        health = {}
        if os.path.exists(health_file):
            with open(health_file) as f:
                data = json.load(f)
                entries = data.get("entries", {})
                if entries:
                    last_date = max(entries.keys())
                    last = entries[last_date]
                    health = {
                        "weight_current": last.get("weight"),
                        "sleep_avg": last.get("sleep_hours"),
                    }
        
        emails_file = os.path.join(_DATA_DIR, "emails_cache.json")
        emails = {"important": []}
        if os.path.exists(emails_file):
            with open(emails_file) as f:
                emails = json.load(f)
        
        return {
            "calendar": calendar,
            "health": health,
            "emails": emails,
        }
    except Exception as e:
        _log(f"Context load error: {e}")
        return {"calendar": {}, "health": {}, "emails": {"important": []}}

# ============ DECISION ============

def should_send_briefing_v3(location, idle_hours):
    """Більш м'які умови"""
    now = datetime.now(tz=_TZ)
    hour = now.hour
    
    # 1. Ранок (6-9am) — завжди
    if 6 <= hour < 9:
        return True
    
    # 2. На роботі (9-17) — якщо простій > 2h
    if 9 <= hour < 17 and location == "robota" and idle_hours > 2:
        return True
    
    # 3. Вечір (18-21) — якщо простій > 1h або час вечері
    if 18 <= hour < 21:
        if idle_hours > 1:
            return True
        if hour == 19:  # 7pm завжди
            return True
    
    # 4. Давно не було (> 3h)
    if time.time() - _STATE["last_sent"] > 10800:
        return True
    
    return False

# ============ MAIN ============

def get_brief_v3(location="doma", idle_hours=0):
    """Отримати briefing (Gemini або fallback)"""
    _STATE["check_count"] += 1
    
    if not should_send_briefing_v3(location, idle_hours):
        return None
    
    _log(f"⚡ CHECK #{_STATE['check_count']}: Should send? YES")
    
    # Збрати контекст
    context = _load_context()
    
    # Спробуємо Gemini
    prompt = f"""Write a SHORT personal briefing for Oleh (150-250 words, Ukrainian).

CONTEXT: {json.dumps(context)[:800]}

STYLE: "Привіт Олеже! Ось що я помітив..."

Include: current time relevant message, health status, calendar, any recommendations."""
    
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 400,
            "temperature": 0.8,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    briefing = _gemini_post(body, timeout=12)
    
    if not briefing:
        _log("⚠️ Gemini failed, using fallback")
        briefing = _generate_fallback_briefing(context, location)
    
    if briefing:
        _STATE["last_sent"] = time.time()
        _log(f"✅ Briefing ready ({len(briefing)} chars)")
    
    return briefing

if __name__ == "__main__":
    print("Testing Aggressive Briefing v3.0...\n")
    
    for i in range(3):
        brief = get_brief_v3("doma", 1.5)
        if brief:
            print(f"[Test {i+1}]\n{brief}\n")
        else:
            print(f"[Test {i+1}] No briefing\n")
        time.sleep(1)
