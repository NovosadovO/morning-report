"""
Recommendations Engine v1.0 — AI Life OS
Аналізує ВЕСЬ контекст твого життя і дає конкретні рекомендації
Інтегрується з message_generator.py для smart_notifications_v3.py
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
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_TZ = ZoneInfo("Europe/Bratislava")

# Gemini model rotation (for rate limiting)
_GEM_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
_GEM_MODEL_IDX = 0
_GEM_LAST_CALL = 0
_GEM_MIN_GAP = 4.0

# ============ UTILS ============

def _log(msg):
    """Log з timestamp"""
    ts = datetime.now(tz=_TZ).strftime("%H:%M:%S")
    print(f"[RECOMMENDATIONS {ts}] {msg}", flush=True)

def _gemini_post(body, timeout=20, tag="", max_retries=3):
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
    
    _log(f"{tag}: Failed after {max_retries} retries")
    return ""

# ============ DATA LOADERS ============

def _load_health_data():
    """Завантажити health дані"""
    try:
        health_file = os.path.join(_DATA_DIR, "daily_health.json")
        if os.path.exists(health_file):
            with open(health_file) as f:
                data = json.load(f)
                if data:
                    today = datetime.now(tz=_TZ).strftime("%Y-%m-%d")
                    return data.get(today, {})
    except:
        pass
    return {}

def _load_crypto_prices():
    """Завантажити крипто цени"""
    try:
        crypto_file = os.path.join(_DATA_DIR, "crypto_price_snapshots.json")
        if os.path.exists(crypto_file):
            with open(crypto_file) as f:
                data = json.load(f)
                if data:
                    latest = data.get("latest", {})
                    return latest
    except:
        pass
    return {}

def _load_calendar_events():
    """Завантажити события календаря на сьогодні"""
    try:
        from monitor import get_calendar
        events = get_calendar()
        return events if events else []
    except:
        pass
    return []

def _load_goals_progress():
    """Завантажити прогрес по цілям"""
    # TODO: Реалізувати коли є трекінг цілей
    return {
        "health": {"current": 80, "goal": 100, "target": "80kg weight, 30km/week run"},
        "finance": {"current": 35, "goal": 100, "target": "FI by 2028"},
        "career": {"current": 25, "goal": 100, "target": "New job in investments"},
        "learning": {"current": 40, "goal": 100, "target": "Complete investment course"},
    }

def _load_energy_data():
    """Завантажити енергію/мотивацію дані"""
    try:
        energy_file = os.path.join(_DATA_DIR, "energy_tracking.json")
        if os.path.exists(energy_file):
            with open(energy_file) as f:
                return json.load(f)
    except:
        pass
    return {"energy": 5, "motivation": 6, "stress": 5, "mood": "neutral"}

def _load_astro_brief():
    """Завантажити астро дані на сьогодні"""
    try:
        astro_file = os.path.join(_DATA_DIR, "monitor_planet_ingress.json")
        if os.path.exists(astro_file):
            with open(astro_file) as f:
                data = json.load(f)
                today = datetime.now(tz=_TZ).strftime("%Y-%m-%d")
                return data.get(today, {})
    except:
        pass
    return {}

# ============ CONTEXT BUILDERS ============

def build_full_context(schedule_type="morning"):
    """
    Побудувати повний контекст твого життя для рекомендацій
    
    schedule_type: "morning", "lunch", "afternoon", "evening"
    """
    now = datetime.now(tz=_TZ)
    
    health = _load_health_data()
    crypto = _load_crypto_prices()
    calendar = _load_calendar_events()
    goals = _load_goals_progress()
    energy = _load_energy_data()
    astro = _load_astro_brief()
    
    context = {
        "time": now.isoformat(),
        "schedule_type": schedule_type,
        "hour": now.hour,
        
        # HEALTH
        "health": {
            "weight": health.get("weight", 83.0),
            "weight_goal": 80.0,
            "weight_trend": "losing 0.5kg/week",
            "bmi": health.get("bmi", 27.2),
            "sleep_hours": health.get("sleep_hours", 4),
            "sleep_goal": 8,
            "steps_today": health.get("steps", 0),
            "steps_goal": 10000,
            "run_km": health.get("run_km", 0),
            "run_goal": 30,  # per week
            "run_sessions_week": health.get("run_sessions_week", 3),
            "hr": health.get("hr", 72),
            "hrv": health.get("hrv", 45),
            "stress_level": health.get("stress_level", 5),  # 1-10
            "hydration": health.get("hydration", "normal"),
        },
        
        # CRYPTO
        "crypto": {
            "btc": {
                "price": crypto.get("BTC", {}).get("price", 60000),
                "change_24h": crypto.get("BTC", {}).get("change_24h", 0),
                "portfolio": 0.5,  # твої BTC
            },
            "eth": {
                "price": crypto.get("ETH", {}).get("price", 1500),
                "change_24h": crypto.get("ETH", {}).get("change_24h", 0),
                "portfolio": 5.0,
            },
            "avax": {
                "price": crypto.get("AVAX", {}).get("price", 6.0),
                "change_24h": crypto.get("AVAX", {}).get("change_24h", 0),
                "portfolio": 100.0,
            },
            "portfolio_value": crypto.get("portfolio_value", 50000),
            "portfolio_change_24h": crypto.get("portfolio_change_24h", 0),
            "portfolio_change_percent": crypto.get("portfolio_change_percent", 0),
        },
        
        # EMAIL
        "email": {
            "vip_count": len([e for e in calendar if "VIP" in str(e)]),
            "important_count": 3,
            "unread_count": 5,
            "vip_senders": ["boss@minebea.com", "maros@interfin.sk"],
        },
        
        # CALENDAR
        "calendar": {
            "events_today": len(calendar),
            "next_event": calendar[0] if calendar else None,
            "work_shift": "early 6am-6pm" if now.hour < 12 else "night 6pm-6am",
            "is_night_shift": False,
        },
        
        # ASTRO
        "astro": {
            "aspects": astro.get("aspects", ["☉◻☽ conflict (1.73°)"]),
            "energy_level": "mixed",  # high, medium, low, mixed
            "recommendations": astro.get("recommendations", []),
        },
        
        # GOALS
        "goals": goals,
        
        # ENERGY
        "energy": energy,
    }
    
    return context

# ============ RECOMMENDATION GENERATOR ============

def generate_recommendations(context):
    """
    Генерувати 2-3 конкретні рекомендації на основі контексту
    
    Returns: list of {"category": str, "action": str, "why": str, "when": str, "impact": str}
    """
    
    schedule_type = context.get("schedule_type", "morning")
    hour = context.get("hour", 6)
    
    # Будуємо Gemini prompt
    prompt = f"""You are Oleh's AI Life Coach. Analyze his COMPLETE life context and generate 2-3 SPECIFIC, ACTIONABLE recommendations.

OLEH'S CURRENT SITUATION:

HEALTH:
- Weight: {context['health']['weight']}kg (Goal: {context['health']['weight_goal']}kg, Trend: {context['health']['weight_trend']})
- Sleep: {context['health']['sleep_hours']}h (Goal: {context['health']['sleep_goal']}h) — {"CRITICAL!" if context['health']['sleep_hours'] < 6 else "OK"}
- Steps: {context['health']['steps_today']}/{context['health']['steps_goal']} ({int(context['health']['steps_today']*100/context['health']['steps_goal'])}%)
- Run: {context['health']['run_km']}km (Weekly goal: {context['health']['run_goal']}km)
- HR: {context['health']['hr']} bpm, HRV: {context['health']['hrv']}
- Stress: {context['health']['stress_level']}/10

CRYPTO:
- BTC: ${context['crypto']['btc']['price']:.0f} ({context['crypto']['btc']['change_24h']:+.1f}%)
- ETH: ${context['crypto']['eth']['price']:.0f} ({context['crypto']['eth']['change_24h']:+.1f}%)
- Portfolio: ${context['crypto']['portfolio_value']:,.0f} ({context['crypto']['portfolio_change_percent']:+.1f}%)

CALENDAR & WORK:
- Events today: {context['calendar']['events_today']}
- Shift: {context['calendar']['work_shift']}
- VIP emails: {context['email']['vip_count']}

ASTRO:
- Aspects: {', '.join(context['astro']['aspects'])}
- Energy: {context['astro']['energy_level']}

GOALS PROGRESS:
- Health: {context['goals']['health']['current']}% (Target: {context['goals']['health']['target']})
- Finance: {context['goals']['finance']['current']}% (Target: {context['goals']['finance']['target']})
- Career: {context['goals']['career']['current']}% (Target: {context['goals']['career']['target']})

TIME OF DAY: {schedule_type.upper()} ({hour}:00)
ENERGY LEVEL: {context['energy']['energy']}/10
MOTIVATION: {context['energy']['motivation']}/10

RULES FOR RECOMMENDATIONS:
1. SPECIFIC, NOT GENERIC. Example:
   ✅ "Run 7km today at 08:00 because weight is 83kg and goal is 80kg (need 0.5kg/week loss)"
   ❌ "Exercise more" or "Be healthy"

2. ACTIONABLE. Include:
   - EXACT ACTION (what, how much)
   - WHY (numbers, context, logic)
   - WHEN (specific time)
   - IMPACT (what will improve)

3. CONTEXTUAL. Consider:
   - Time of day (morning = planning, evening = reflection)
   - Energy level (low = light tasks, high = important work)
   - Astro aspects (conflicts = reflect, harmonies = act)
   - Work shift (night shift = rest priority)
   - Goals (what moves the needle?)

4. PRIORITIZE by impact:
   - Critical health issues first
   - Finance opportunities second
   - Career/learning third
   - Astro/energy last

5. Language: Ukrainian, warm, specific, motivating

OUTPUT FORMAT (Ukrainian, with emojis):

1️⃣ [CATEGORY: HEALTH/CRYPTO/WORK/LEARNING/ENERGY/ASTRO]
   🎯 ДІЯ: [specific action - 1 sentence]
   ❓ ЧОМУ: [2-3 sentences with numbers and reasoning]
   ⏰ КОЛИ: [specific time or "ЗАРАЗ"]
   📊 РЕЗУЛЬТАТ: [what will improve, 1 sentence]

2️⃣ [ANOTHER CATEGORY]
   [same format]

3️⃣ [THIRD, if applicable]
   [same format]

GENERATE RECOMMENDATIONS NOW:"""

    body = {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "maxOutputTokens": 2000,
            "temperature": 0.7,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    response = _gemini_post(body, timeout=15, tag="REC_GEN")
    
    if response:
        _log(f"Generated recommendations: {len(response)} chars")
        return response
    
    _log("Failed to generate recommendations, returning fallback")
    return _get_fallback_recommendations(context)

def _get_fallback_recommendations(context):
    """Fallback рекомендації коли Gemini не відповідає"""
    
    recommendations = []
    health = context["health"]
    crypto = context["crypto"]
    
    # Health recommendation
    if health["weight"] > 80:
        recommendations.append("""
1️⃣ ЗДОРОВ'Я
   🎯 ДІЯ: Пробіжка 7км сьогодні
   ❓ ЧОМУ: Твоя вага 83кг, а ціль 80кг. Потреба -0.5кг на тиждень. 1 пробіжка = -200kcal
   ⏰ КОЛИ: 08:00-08:30
   📊 РЕЗУЛЬТАТ: Прогрес до 80кг (+1 день)
""")
    
    # Crypto recommendation
    if crypto["btc"]["change_24h"] < -5:
        recommendations.append(f"""
2️⃣ КРИПТО
   🎯 ДІЯ: Розглянь купити BTC на -7%
   ❓ ЧОМУ: Гарна ціна для DCA (Dollar Cost Averaging). Якщо куписш $500, витримаєш 2+ роки
   ⏰ КОЛИ: Сьогодні до 12:00
   📊 РЕЗУЛЬТАТ: Долгострокова позиція, можливий 3x за 2 роки
""")
    
    # Sleep recommendation
    if health["sleep_hours"] < 6:
        recommendations.append("""
3️⃣ СОН
   🎯 ДІЯ: Ляжи на 1h раніше сьогодні (23:00 вместо 00:00)
   ❓ ЧОМУ: Сон вчора 4h — МАЛО! Нічна зміна завтра потребує повного сну
   ⏰ КОЛИ: СЬОГОДНІ 23:00
   📊 РЕЗУЛЬТАТ: Енергія +30%, готовність до нічної зміни
""")
    
    return "\n".join(recommendations)

# ============ INTEGRATION ============

def get_recommendations_for_schedule(schedule_type="morning"):
    """
    Основна функція — отримати рекомендації для schedule типу
    
    Args:
        schedule_type: "morning", "lunch", "afternoon", "evening"
    
    Returns:
        str: Отформатовані рекомендації для вставки в повідомлення
    """
    try:
        _log(f"Building context for {schedule_type}...")
        context = build_full_context(schedule_type)
        
        _log(f"Generating recommendations...")
        recommendations = generate_recommendations(context)
        
        return recommendations
        
    except Exception as e:
        _log(f"❌ Error generating recommendations: {e}")
        return ""

# ============ TEST ============

if __name__ == "__main__":
    print("Testing Recommendations Engine...")
    
    for sched_type in ["morning", "lunch", "afternoon", "evening"]:
        print(f"\n{'='*60}")
        print(f"Testing {sched_type.upper()}")
        print(f"{'='*60}")
        
        recs = get_recommendations_for_schedule(sched_type)
        print(recs)
        
        time.sleep(2)
