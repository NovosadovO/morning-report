"""
Intelligent Assistant v2.1 — Проактивний помічник Олега
Сам читає: пошту, календар, крипто, здоров'я, звички, графік, астро
Пише першим: 2-3 рази на день з РЕАЛЬНИМИ аналітиками + контекстом
Gemini-аналіз: персоналізований з точними даними
"""

import os
import json
import time
import sys
from datetime import datetime, timedelta, timezone
import urllib.request
import urllib.error

# Імпортуємо функції
sys.path.insert(0, os.path.dirname(__file__))
try:
    from monitor import get_emails, get_calendar, get_prices, _gem_post
except ImportError:
    get_emails = get_calendar = get_prices = _gem_post = None
    print("⚠️ Could not import monitor functions")

# ============ CONFIG ============
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

TRIGGER_BTC_THRESHOLD = 5.0  # 5% зміна = алерт
TRIGGER_EMAIL_THRESHOLD = 3  # 3+ нові листи = алерт

# ============ CRYPTO: CoinGecko TOP-20 ============

def get_coingecko_top20():
    """Отримує TOP-20 монет з CoinGecko (free API)"""
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 20,
            "page": 1,
            "sparkline": False,
            "price_change_percentage": "24h"
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{url}?{query_string}"
        
        req = urllib.request.Request(full_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data
    except Exception as e:
        print(f"❌ CoinGecko error: {e}")
        return []

def get_user_watch_list():
    """Отримує монети що стежить Олег (BTC, ETH, AVAX, ONDO)"""
    watch_ids = ["bitcoin", "ethereum", "avalanche-2", "ondo"]
    watch_symbols = ["BTC", "ETH", "AVAX", "ONDO"]
    
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "ids": ",".join(watch_ids),
            "sparkline": False,
            "price_change_percentage": "24h,7d,30d"
        }
        
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        full_url = f"{url}?{query_string}"
        
        req = urllib.request.Request(full_url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            result = {}
            for coin in data:
                sym = coin.get('symbol', '').upper()
                result[sym] = {
                    "name": coin.get('name'),
                    "price": coin.get('current_price', 0),
                    "change_24h": coin.get('price_change_percentage_24h', 0),
                    "change_7d": coin.get('price_change_percentage_7d', 0),
                    "market_cap": coin.get('market_cap', 0)
                }
            return result
    except Exception as e:
        print(f"❌ Watch list error: {e}")
        return {}

def analyze_crypto_changes(watch_list):
    """Аналізує зміни в крипто портфелі Олега"""
    important = []
    
    for symbol, data in watch_list.items():
        change_24h = data.get('change_24h', 0)
        change_7d = data.get('change_7d', 0)
        price = data.get('price', 0)
        
        # Алерти на значні зміни
        if abs(change_24h) >= TRIGGER_BTC_THRESHOLD or abs(change_7d) >= 15:
            direction_24h = "📈" if change_24h > 0 else "📉"
            direction_7d = "📈" if change_7d > 0 else "📉"
            important.append({
                "symbol": symbol,
                "price": f"${price:,.0f}" if price > 1 else f"${price:.4f}",
                "change_24h": f"{change_24h:+.1f}%",
                "change_7d": f"{change_7d:+.1f}%",
                "direction_24h": direction_24h,
                "direction_7d": direction_7d
            })
    
    return important

# ============ HEALTH: Вага, кроки, сон ============

def get_health_data():
    """Витягує поточні дані про здоров'я"""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    today_str = now_local.strftime("%Y-%m-%d")
    yest_str = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
    
    health = {
        "weight": None,
        "weight_trend": None,
        "steps": None,
        "steps_goal": 8000,
        "sleep": None,
        "sleep_goal": 7
    }
    
    try:
        # Вага
        import storage as _st
        weight_data = _st.load("weight_data.json") or _st.load_weight() or {}
        if weight_data:
            last_weight_key = sorted(weight_data.keys())[-1] if weight_data else None
            if last_weight_key:
                health["weight"] = weight_data[last_weight_key]
                # Тренд (різниця з вчора)
                yest_weight = weight_data.get(yest_str)
                if yest_weight:
                    health["weight_trend"] = round(health["weight"] - yest_weight, 1)
        
        # Кроки (QWatch + Apple Health)
        qwatch = _st.load("qwatch_data.json", default={})
        health_json = _st.load("health_data.json", default={})
        
        today_steps = (qwatch.get(today_str) or {}).get("steps", 0) or (health_json.get(today_str) or {}).get("steps", 0) or 0
        health["steps"] = today_steps if today_steps > 0 else None
        
        # Сон (QWatch)
        today_sleep = (qwatch.get(today_str) or {}).get("sleep_hours", 0) or 0
        health["sleep"] = round(today_sleep, 1) if today_sleep > 0 else None
    
    except Exception as e:
        print(f"❌ Health data error: {e}")
    
    return health

# ============ HABITS: Холодний душ, біг, вода ============

def get_habits_data():
    """Витягує дані про звички сьогодні"""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    today_str = now_local.strftime("%Y-%m-%d")
    
    habits = {
        "shower": None,  # cold shower
        "running": None,  # біг
        "water": None  # вода
    }
    
    try:
        import storage as _st
        habits_data = _st.load("habits.json") or {}
        today_habits = habits_data.get(today_str, {})
        
        habits["shower"] = today_habits.get("shower")
        habits["running"] = today_habits.get("running")
        habits["water"] = today_habits.get("water")
    
    except Exception as e:
        print(f"❌ Habits data error: {e}")
    
    return habits

# ============ WORK SHIFT: Графік Олега ============

def get_work_shift():
    """Визначає чи Олег на ранній чи нічній зміні"""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    hour = now_local.hour
    
    # Рання: 06:00-18:00, Нічна: 18:00-06:00
    if 6 <= hour < 18:
        shift = "Рання зміна ☀️"
        status = "Олег на роботі"
    else:
        shift = "Нічна зміна 🌙"
        status = "Олег на роботі" if 18 <= hour < 23 else "Олег вдома"
    
    return {"shift": shift, "status": status}

# ============ EMAIL: Важливі листи ============

def get_important_emails():
    """Отримує важливі нові листи"""
    try:
        if not get_emails:
            return {"count": 0, "items": []}
        email_block = get_emails()
        
        # Рахуємо листи
        count = 0
        items = []
        
        if isinstance(email_block, dict) and "items" in email_block:
            items = email_block["items"][:5]
            count = len(items)
        
        return {"count": count, "items": items}
    except Exception as e:
        print(f"❌ Email error: {e}")
        return {"count": 0, "items": []}

# ============ CALENDAR: События ============

def get_calendar_events():
    """Отримує найближчі события на 48 годин"""
    try:
        if not get_calendar:
            return []
        
        cal_text = get_calendar()
        if isinstance(cal_text, str):
            # Парсимо текст
            events = [line.strip() for line in cal_text.split('\n') if line.strip() and '🔔' in line or '📍' in line]
            return events[:5]
        return []
    except Exception as e:
        print(f"❌ Calendar error: {e}")
        return []

# ============ ASTRO: Проста астрологія ============

def get_astro_brief():
    """Получає коротку астро-прогноз"""
    try:
        import astro
        report = astro.get_astro_report()
        if report:
            # Витягуємо перший параграф
            lines = report.split('\n')
            brief = ' '.join(lines[:3])
            return brief[:200]
        return "🔮 Астро-дані недоступні"
    except Exception as e:
        print(f"⚠️ Astro error: {e}")
        return "🔮 Астро-дані недоступні"

# ============ GEMINI: Контекстна Gemini-аналітика ============

def _gem_post_local(url, body, tag, max_retries=2):
    """Спрощена версія _gem_post для proactive messages"""
    for attempt in range(max_retries):
        try:
            import urllib.request, json as _json
            data = _json.dumps(body).encode('utf-8')
            req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                response = _json.loads(resp.read().decode())
                
                # Перевіряємо finish_reason
                try:
                    finish_reason = response['candidates'][0]['content'].get('finishReason', 'UNKNOWN')
                    if finish_reason == 'MAX_TOKENS':
                        print(f"⚠️ [{tag}] MAX_TOKENS reached, retrying...")
                        time.sleep(2)
                        continue
                    
                    text = response['candidates'][0]['content']['parts'][0].get('text', '')
                    return text if text else None
                except (KeyError, IndexError) as e:
                    print(f"⚠️ [{tag}] Parse error: {e}")
                    return None
        
        except Exception as e:
            if '429' in str(e):
                print(f"⚠️ [{tag}] Rate limited, waiting...")
                time.sleep(5)
            else:
                print(f"❌ [{tag}] error: {e}")
                return None
    
    return None

def generate_contextual_insight(crypto_data, health_data, habits_data, work_shift, emails, calendar, astro):
    """Генеріує КОНТЕКСТНУ аналітику через Gemini з РЕАЛЬНИМИ даними"""
    
    if not GEMINI_API_KEY:
        return "🔮 AI недоступний (мало ключа)"
    
    # Формуємо детальний контекст
    context_lines = []
    
    # 1. КРИПТО
    if crypto_data:
        context_lines.append("💰 КРИПТО ПОРТФЕЛЬ:")
        for coin in crypto_data:
            context_lines.append(f"  {coin['symbol']}: {coin['price']} ({coin['change_24h']}) | 7д: {coin['change_7d']}")
    else:
        context_lines.append("💰 КРИПТО: Стабільно, немає резких змін")
    
    # 2. ЗДОРОВ'Я
    context_lines.append("\n💪 ЗДОРОВ'Я СЬОГОДНІ:")
    if health_data["weight"]:
        trend_str = f"({health_data['weight_trend']:+.1f} від вчора)" if health_data['weight_trend'] else ""
        context_lines.append(f"  Вага: {health_data['weight']} кг {trend_str}")
    if health_data["steps"]:
        pct = int(health_data['steps'] / health_data['steps_goal'] * 100)
        context_lines.append(f"  Кроки: {health_data['steps']:,} (мета: {health_data['steps_goal']}) {pct}%")
    if health_data["sleep"]:
        context_lines.append(f"  Сон: {health_data['sleep']}г (мета: {health_data['sleep_goal']}г)")
    
    # 3. ЗВИЧКИ
    context_lines.append("\n🎯 ЗВИЧКИ:")
    shower_str = "✅ Холодний душ" if habits_data["shower"] is True else ("❌ Забув душ" if habits_data["shower"] is False else "⏳ Ще не давав про душ")
    running_str = "✅ Біг" if habits_data["running"] is True else ("❌ Без бігу" if habits_data["running"] is False else "⏳ Ще не біг")
    water_str = "✅ Вода" if habits_data["water"] is True else ("❌ Забув пити" if habits_data["water"] is False else "⏳ Невідомо")
    context_lines.extend([f"  {shower_str}", f"  {running_str}", f"  {water_str}"])
    
    # 4. ГРАФІК
    context_lines.append(f"\n📅 ГРАФІК: {work_shift['shift']}")
    context_lines.append(f"   {work_shift['status']}")
    
    # 5. ЛИСТИ
    if emails["count"] > 0:
        context_lines.append(f"\n📧 ПОШТА: {emails['count']} нові листи")
        for item in emails["items"][:3]:
            if isinstance(item, str):
                context_lines.append(f"  • {item[:60]}...")
    else:
        context_lines.append("\n📧 ПОШТА: Немає нових листів")
    
    # 6. СОБЫТИЯ
    if calendar:
        context_lines.append(f"\n📍 СОБЫТИЯ:")
        for event in calendar[:3]:
            context_lines.append(f"  • {event[:70]}")
    
    # 7. АСТРО
    context_lines.append(f"\n🔮 АСТРО: {astro}")
    
    context = "\n".join(context_lines)
    
    # Генеруємо Gemini-аналітику
    prompt = f"""Ти — персональний помічник Олега. РОЗБЕРИ його дні детально (200-250 слів, три параграфи):

{context}

СТИЛЬ: Теплий, мотивуючий, з конкретними діями. 
СТРУКТУРА:
1️⃣ ПОТОЧНИЙ СТАН: Як він робить сьогодні? (крипто, здоров'я, роботу)
2️⃣ ЧТО ВДАЛОСЬ: Позитивні моменти, досягнення
3️⃣ РЕКОМЕНДАЦІЇ: 1-2 конкретні дії на сьогодні

МОВА: Українська, неформальна, с емодзі

Піши чітко, без повторів."""
    
    body = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 1,
            "maxOutputTokens": 1000,
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    text = _gem_post_local(url, body, "proactive_insight")
    
    if text:
        return text
    else:
        return "⚠️ AI нема часу, але твій день буде цікавим! 🚀"

# ============ MAIN: Проактивне повідомлення ============

def should_send_proactive_message(last_message_time, user_activity_idle_minutes=120):
    """Вирішує чи потреба писати першим (1 РАЗ НА ГОДИНУ)"""
    
    current_time = time.time()
    current_hour = datetime.now().strftime("%Y-%m-%d %H:00:00")
    
    # 1. Перевіряємо деdup — чи вже надіслали у ЦІЙ ГОДИНІ
    try:
        dedup_file = os.path.join(os.path.dirname(__file__), "data", "proactive_sent_hours.json")
        if os.path.exists(dedup_file):
            with open(dedup_file, "r") as f:
                sent_hours = json.load(f) or {}
                if current_hour in sent_hours:
                    print(f"[proactive] Already sent in {current_hour}, skipping", flush=True)
                    return False
    except Exception as e:
        print(f"[proactive] dedup error: {e}", flush=True)
    
    # 2. Timing-критерії
    time_since_last = current_time - last_message_time
    hour = datetime.now().hour
    
    should_send = False
    reason = ""
    
    # Idle > 120 хвилин
    if time_since_last > (user_activity_idle_minutes * 60):
        should_send = True
        reason = "idle>120m"
    # Ранок (07:00-09:00)
    elif 7 <= hour <= 9:
        should_send = True
        reason = "morning"
    # Після роботи (17:00-19:00)
    elif 17 <= hour <= 19:
        should_send = True
        reason = "after-work"
    
    if should_send:
        print(f"[proactive] Should send: {reason}", flush=True)
    
    return should_send

def send_proactive_message(telegram_send_func):
    """Основна функція проактивного повідомлення — З РЕАЛЬНИМИ ДАНИМИ"""
    
    print("\n🤖 [Intelligent Assistant v2.1] Generating proactive message...", flush=True)
    
    # Збираємо ВСІ дані
    crypto_watch = get_user_watch_list()
    crypto_changes = analyze_crypto_changes(crypto_watch)
    
    health_data = get_health_data()
    habits_data = get_habits_data()
    work_shift = get_work_shift()
    
    emails = get_important_emails()
    calendar = get_calendar_events()
    astro = get_astro_brief()
    
    # Генеруємо аналітику
    insight = generate_contextual_insight(
        crypto_changes,
        health_data,
        habits_data,
        work_shift,
        emails,
        calendar,
        astro
    )
    
    # Формуємо повідомлення
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    message = f"""👋 Привіт Олег! 

Я сам проаналізував твою ситуацію:

{insight}

---
🤖 Проактивний аналіз | {now_local.strftime('%H:%M')}"""
    
    # Надсилаємо
    try:
        telegram_send_func(message)
        
        # Записуємо dedup
        current_hour = datetime.now().strftime("%Y-%m-%d %H:00:00")
        dedup_file = os.path.join(os.path.dirname(__file__), "data", "proactive_sent_hours.json")
        
        try:
            os.makedirs(os.path.dirname(dedup_file), exist_ok=True)
            sent_hours = {}
            if os.path.exists(dedup_file):
                with open(dedup_file, "r") as f:
                    sent_hours = json.load(f) or {}
            
            sent_hours[current_hour] = datetime.now().isoformat()
            
            # Очищуємо старі (>25 годин)
            cutoff = (datetime.now() - timedelta(hours=25)).strftime("%Y-%m-%d %H:00:00")
            sent_hours = {k: v for k, v in sent_hours.items() if k >= cutoff}
            
            with open(dedup_file, "w") as f:
                json.dump(sent_hours, f, indent=2)
            
            print(f"✅ Проактивне повідомлення надіслано о {now_local.strftime('%H:%M')}", flush=True)
        except Exception as _de:
            print(f"⚠️ Dedup error: {_de}", flush=True)
        
        return True
    except Exception as e:
        print(f"❌ Send error: {e}", flush=True)
        return False

# ============ EXPORTS ============

if __name__ == "__main__":
    print("✅ intelligent_assistant_v2.1 loaded")
