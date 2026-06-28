"""
Message Generator v1.0 — AI генерує & надсилає messages на основі тригерів
Інтегрується з intelligent_listener.py
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from recommendations_engine import get_recommendations_for_schedule
    _RECOMMENDATIONS_AVAILABLE = True
except ImportError:
    _RECOMMENDATIONS_AVAILABLE = False
    print("⚠️ recommendations_engine not available", flush=True)

try:
    from contextual_briefing_engine import get_contextual_briefing
    _BRIEFING_AVAILABLE = True
except ImportError:
    _BRIEFING_AVAILABLE = False
    print("⚠️ contextual_briefing_engine not available", flush=True)

try:
    from aggressive_briefing_v3 import get_brief_v3
    _BRIEFING_V3_AVAILABLE = True
except ImportError:
    _BRIEFING_V3_AVAILABLE = False
    print("⚠️ aggressive_briefing_v3 not available", flush=True)

try:
    from deep_analysis_engine import build_deep_analysis
    _DEEP_ANALYSIS_AVAILABLE = True
except ImportError:
    _DEEP_ANALYSIS_AVAILABLE = False
    print("⚠️ deep_analysis_engine not available", flush=True)

# ============ CONFIG ============

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "2100366814")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_TZ = ZoneInfo("Europe/Bratislava")

_GEM_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
_GEM_MODEL_IDX = 0
_GEM_LAST_CALL = 0
_GEM_MIN_GAP = 4.0

# ============ GEMINI HELPERS ============

def _log(msg):
    """Log з timestamp"""
    ts = datetime.now(tz=_TZ).strftime("%H:%M:%S")
    print(f"[MESSAGE_GEN {ts}] {msg}", flush=True)

def _gemini_post(url, body, timeout=20, tag="", max_retries=3):
    """Надійний Gemini запит з retry та fallback моделей"""
    global _GEM_MODEL_IDX, _GEM_LAST_CALL
    
    # Rate limit
    now = time.time()
    gap = now - _GEM_LAST_CALL
    if gap < _GEM_MIN_GAP:
        time.sleep(_GEM_MIN_GAP - gap)
    _GEM_LAST_CALL = time.time()
    
    for attempt in range(max_retries):
        try:
            model = _GEM_MODELS[_GEM_MODEL_IDX % len(_GEM_MODELS)]
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
            
            req = urllib.request.Request(
                gemini_url,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                response = json.loads(resp.read())
                
                if response.get("candidates"):
                    cand = response["candidates"][0]
                    content = cand.get("content", {})
                    parts = content.get("parts", [])
                    
                    if parts and parts[0].get("text"):
                        _GEM_LAST_CALL = time.time()
                        return parts[0]["text"]
                
                # Fallback на наступну модель
                _log(f"{tag}: Empty response, trying next model...")
                _GEM_MODEL_IDX += 1
                
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _log(f"{tag}: 429 rate limit, switching model")
                _GEM_MODEL_IDX += 1
                time.sleep(5 + attempt * 3)
            else:
                _log(f"{tag}: HTTP {e.code}")
                time.sleep(2 + attempt * 2)
        except Exception as e:
            _log(f"{tag}: Error {e}")
            time.sleep(2 + attempt * 2)
    
    return ""

def _should_send_message(trigger_type: str, trigger_data) -> bool:
    """
    AI вирішує чи ДІЙСНО потребує цей тригер написати
    (уникаємо spam)
    """
    prompt = f"""You are Oleh's smart assistant. Analyze this trigger and decide if it REALLY needs a message NOW.

TRIGGER TYPE: {trigger_type}
DATA: {json.dumps(trigger_data, default=str)}

RULES:
1. VIP email from boss/investors → ALWAYS YES (critical)
2. Crypto ±5% → YES if > 7% move or interesting pattern
3. Event in 1h → YES if not a routine (not "shower", "water", "tea")
4. Idle 2h+ → YES if it's morning (6-9am) or evening (19-22)
5. Morning/Evening routine → YES
6. Health update → YES if weight changed >0.5kg or sleep <5h

RESPOND WITH ONLY:
YES (one word)
OR
NO (one word)

NO explanation, NO extra text."""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 10,
            "temperature": 0.5,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    response = _gemini_post(
        "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        body,
        timeout=10,
        tag="SHOULD_SEND"
    )
    
    decision = response.strip().upper()
    _log(f"Should send '{trigger_type}'? → {decision}")
    
    return decision == "YES"

def _generate_message(trigger_type: str, trigger_data, location: str, idle_hours: float) -> str:
    """
    Gemini генерує 300-400 слів message на основі тригеру
    """
    
    # 🎯 Special case: deep analysis (новий!)
    if trigger_type == "deep_analysis":
        if _DEEP_ANALYSIS_AVAILABLE:
            try:
                analysis = build_deep_analysis(location, idle_hours)
                if analysis:
                    _log(f"Generated deep analysis ({len(analysis)} chars)")
                    return analysis
            except Exception as e:
                _log(f"⚠️ Deep analysis failed: {e}")
        return "📝 Глибокий аналіз у розробці! 💭"
    
    # 🎯 Special case: contextual briefing (use v3 if available)
    if trigger_type == "briefing" or trigger_type == "contextual_briefing":
        # Try v3 first (more aggressive)
        if _BRIEFING_V3_AVAILABLE:
            try:
                briefing = get_brief_v3(location, idle_hours)
                if briefing:
                    _log(f"Generated aggressive briefing v3 ({len(briefing)} chars)")
                    return briefing
            except Exception as e:
                _log(f"⚠️ Briefing v3 failed: {e}")
        
        # Fallback to v1
        if _BRIEFING_AVAILABLE:
            try:
                briefing, themes = get_contextual_briefing(location, idle_hours)
                if briefing:
                    _log(f"Generated contextual briefing v1 ({len(briefing)} chars)")
                    return briefing
            except Exception as e:
                _log(f"⚠️ Briefing v1 failed: {e}")
        
        # Last resort fallback
        return "📝 Аналіз у розробці, але я тримаю вас в курсі! 💪"
    
    # Формуємо контекст
    context_lines = [
        f"Trigger: {trigger_type}",
        f"Location: {location}",
        f"Idle: {idle_hours:.1f}h",
        f"Data: {json.dumps(trigger_data, default=str)[:500]}",
    ]
    context = "\n".join(context_lines)
    
    # Генеруємо промпт
    prompts = {
        "vip_email": f"""Write a brief professional response to Oleh about incoming VIP emails (300 words, Ukrainian).

CONTEXT:
{context}

INCLUDE:
1. Who sent the email (from field)
2. What's the subject (summary)
3. Recommended action (reply urgency, what to do)
4. Any follow-up needed

TONE: Professional, helpful, action-oriented.
LANGUAGE: Ukrainian.""",

        "crypto_move": f"""Write a brief crypto market analysis message for Oleh (250 words, Ukrainian).

CONTEXT:
{context}

INCLUDE:
1. Which coins moved and by how much
2. Market interpretation (bullish/bearish)
3. Risk assessment
4. Recommended action (watch, hold, rebalance?)

TONE: Analytical, calm, educational.
LANGUAGE: Ukrainian.""",

        "event_soon": f"""Write a brief reminder about an upcoming event (200 words, Ukrainian).

CONTEXT:
{context}

INCLUDE:
1. What event is coming
2. How much time left
3. Preparation tips
4. What to bring/do

TONE: Helpful, encouraging.
LANGUAGE: Ukrainian.""",

        "idle_timeout": f"""Write an encouraging message to Oleh about taking a break (250 words, Ukrainian).

CONTEXT:
He's been inactive for {idle_hours:.1f} hours.
Current location: {location}

INCLUDE:
1. Acknowledge the work he's done
2. Suggest a break activity (walk, stretch, drink water)
3. If morning: energizing tips
4. If evening: relaxation tips

TONE: Warm, supportive, motivating.
LANGUAGE: Ukrainian.""",

        "morning": f"""Write a warm morning greeting & daily briefing for Oleh (300 words, Ukrainian).

CONTEXT:
{context}

INCLUDE:
1. Greeting (Привіт Олеже!)
2. Today's focus areas
3. Motivation
4. Quick checklist (3 things to do today)

TONE: Energizing, motivating, personal.
LANGUAGE: Ukrainian.""",

        "evening": f"""Write an evening summary & reflection for Oleh (300 words, Ukrainian).

CONTEXT:
{context}

INCLUDE:
1. Acknowledgement of today's work
2. Highlights (what went well)
3. Learnings or reflections
4. Tomorrow's outlook
5. Evening relaxation tips

TONE: Reflective, warm, closure-focused.
LANGUAGE: Ukrainian.""",

        "health": f"""Write a brief health analysis message for Oleh (250 words, Ukrainian).

CONTEXT:
{context}

INCLUDE:
1. What health data changed
2. Assessment (good/needs attention)
3. Personalized advice
4. Motivation for goals

TONE: Supportive, motivating, non-judgmental.
LANGUAGE: Ukrainian.""",
    }
    
    prompt = prompts.get(trigger_type, f"Write a helpful message to Oleh about {trigger_type}.")
    
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": 600,
            "temperature": 0.8,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    message = _gemini_post(
        "generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        body,
        timeout=20,
        tag=f"MESSAGE_{trigger_type.upper()}"
    )
    
    if not message:
        message = f"📝 Тригер: {trigger_type}\n⏱️ Локація: {location}\n💭 Деталі: {str(trigger_data)[:200]}..."
    
    # 🎯 Add AI recommendations if available
    if _RECOMMENDATIONS_AVAILABLE and trigger_type in ["morning", "evening", "lunch", "afternoon"]:
        try:
            # Map trigger type to schedule type for recommendations
            schedule_map = {"morning": "morning", "lunch": "lunch", "afternoon": "afternoon", "evening": "evening"}
            if trigger_type in schedule_map:
                recs = get_recommendations_for_schedule(schedule_map[trigger_type])
                if recs:
                    message += "\n\n🎯 МОЇ РЕКОМЕНДАЦІЇ:\n" + recs
                    _log(f"Added recommendations ({len(recs)} chars)")
        except Exception as e:
            _log(f"⚠️ Recommendations failed: {e}")
    
    return message

# ============ TELEGRAM SENDING ============

def _send_to_telegram(text: str, parse_mode: str = "HTML") -> bool:
    """Надішліть message до Telegram з retry"""
    if not text or not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        _log("Telegram credentials missing")
        return False
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            body = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode
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
                    _log(f"✅ Sent {len(text)} chars to Telegram")
                    return True
                else:
                    desc = result.get("description", "unknown")
                    _log(f"⚠️ Telegram error: {desc}")
                    if attempt < max_retries - 1:
                        time.sleep(2 + attempt * 2)
        except urllib.error.HTTPError as e:
            _log(f"⚠️ HTTP {e.code}, retrying...")
            time.sleep(2 + attempt * 2)
        except Exception as e:
            _log(f"⚠️ Send error: {e}")
            time.sleep(2 + attempt * 2)
    
    return False

# ============ MESSAGE LOG ============

def _log_message(trigger_type: str, trigger_data, message_text: str, location: str):
    """Зберегти記錄 відправленого message"""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        log_file = os.path.join(_DATA_DIR, "ai_messages.json")
        
        logs = {}
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                logs = json.load(f)
        
        timestamp = datetime.now(tz=_TZ).isoformat()
        logs[timestamp] = {
            "trigger": trigger_type,
            "location": location,
            "data_summary": str(trigger_data)[:200],
            "message_len": len(message_text),
            "sent": True,
        }
        
        with open(log_file, "w") as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        _log(f"Log error: {e}")

# ============ MAIN ============

def process_trigger(trigger_type: str, trigger_data, location: str = "doma", idle_hours: float = 0):
    """
    Основна функція — інтегрується з intelligent_listener.py
    
    Args:
        trigger_type: "vip_email", "crypto_move", "event_soon", "idle_timeout", "morning", "evening", "health"
        trigger_data: дані тригеру (emails, moves, etc)
        location: "doma" або "robota"
        idle_hours: скільки часу неактивності
    
    Returns:
        bool: True якщо message надіслан
    """
    _log(f"Processing trigger: {trigger_type}")
    
    # Перевіримо credentials
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        _log(f"❌ Missing TELEGRAM credentials (TOKEN={bool(TELEGRAM_TOKEN)}, CHAT={bool(TELEGRAM_CHAT_ID)})")
        return False
    
    if not GEMINI_API_KEY:
        _log(f"⚠️ Missing GEMINI_API_KEY")
    
    try:
        # 1. Запитаємо AI чи писати
        if not _should_send_message(trigger_type, trigger_data):
            _log(f"AI decided NOT to send for {trigger_type}")
            return False
        
        # 2. Генеруємо message
        _log(f"Generating message for {trigger_type}...")
        message = _generate_message(trigger_type, trigger_data, location, idle_hours)
        
        if not message:
            _log(f"Failed to generate message")
            return False
        
        # 3. Надсилаємо на Telegram
        _log(f"Sending to Telegram...")
        success = _send_to_telegram(message)
        
        if success:
            _log_message(trigger_type, trigger_data, message, location)
        
        return success
    except Exception as e:
        _log(f"❌ process_trigger error: {e}")
        return False

if __name__ == "__main__":
    # TEST
    test_triggers = [
        ("vip_email", {"from": "boss@minebea.com", "subject": "Important project"}, "robota", 0),
        ("crypto_move", {"BTC": 7.2, "ETH": 3.1}, "doma", 2.5),
        ("morning", {}, "doma", 0),
    ]
    
    for ttype, tdata, tloc, tidle in test_triggers:
        print(f"\n=== Testing {ttype} ===")
        # process_trigger(ttype, tdata, tloc, tidle)  # Uncomment to test with real API
        _log(f"Would process: {ttype}")
    
    print("\n✅ Message generator ready")
