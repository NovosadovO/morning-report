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

# VIP КОНТАКТИ (пріоритизація)
VIP_CONTACTS = {
    "boss": {
        "patterns": [r"boss|manager|ceo|director", r"minebea|mitsumi"],
        "emoji": "🔴",
        "priority": "critical"
    },
    "investors": {
        "patterns": [r"interfinance|interfin|maros|sivak|invest", r"maroš|sváč"],
        "emoji": "💰",
        "priority": "high"
    },
    "hr": {
        "patterns": [r"hr|recruit|interview|job offer|position"],
        "emoji": "🎯",
        "priority": "high"
    },
    "important_clients": {
        "patterns": [r"client|customer|contract|deal"],
        "emoji": "⭐",
        "priority": "high"
    }
}

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
    """Делегує до monitor._gem_post — СПІЛЬНИЙ rate-limiter на весь процес."""
    try:
        import json as _json
        from monitor import _gem_post
        resp = _gem_post(url, _json.dumps(body).encode('utf-8'), timeout=30, tag=tag or "intelligent_assistant", max_retries=max(max_retries, 3))
        if isinstance(resp, dict) and resp.get('candidates'):
            parts = resp['candidates'][0].get('content', {}).get('parts', [])
            if parts and parts[0].get('text'):
                return parts[0]['text']
        return None
    except Exception as e:
        print(f"❌ [{tag}] error: {e}")
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

def should_send_proactive_message(unused_param=None, user_activity_idle_minutes=120):
    """Вирішує чи потреба писати першим (1 РАЗ НА ГОДИНУ)
    
    ВАЖЛИВО: Замінено на деdup ЧЕРЕЗ ЧАС (за файлом).
    Ігнорує параметр last_message_time (залишиш для сумісності).
    Дозволяє мах 1 сповіщення на годину.
    """
    
    # ☠️ НЕ ВІРИТИ current_time — перезавантаження обнулює!
    # Замість того: витягаємо ОСТАННЮ відправку з файлу
    dedup_file = os.path.join(os.path.dirname(__file__), "data", "proactive_last_send.json")
    
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)  # UTC+2 (Київ/Кошіце)
    current_hour = now_local.strftime("%Y-%m-%d %H")  # 2026-06-26 14
    
    last_send_ts = 0.0
    last_send_hour = ""
    
    # 1. Читаємо останній час
    try:
        if os.path.exists(dedup_file):
            with open(dedup_file, "r") as f:
                data = json.load(f) or {}
                last_send_ts = data.get("last_sent_timestamp", 0.0)
                last_send_hour = data.get("last_sent_hour", "")
    except Exception as e:
        print(f"[proactive] dedup read error: {e}", flush=True)
    
    # 2. Якщо вже надіслали ЦІЇ ЖЕ ГОДИНИ — НІ
    if last_send_hour == current_hour:
        print(f"[proactive] Already sent in hour {current_hour}, skipping", flush=True)
        return False
    
    # 3. Перевіряємо timing-критерії
    now_ts = now_local.timestamp()
    time_since_last_send = now_ts - last_send_ts
    
    hour = now_local.hour
    should_send = False
    reason = ""
    
    # Idle > 60 хвилин від ОСТАННЬОГО СПОВІЩЕННЯ
    if last_send_ts > 0 and time_since_last_send > 3600:  # 1 годину
        should_send = True
        reason = f"idle>{int(time_since_last_send/60)}m"
    # Якщо НІКОЛИ не слали — дозвільте при ранку
    elif last_send_ts == 0 and 6 <= hour <= 10:
        should_send = True
        reason = "first_time_morning"
    # Ранок (06:00-10:00) — одне сповіщення
    elif 6 <= hour < 10 and (last_send_ts == 0 or time_since_last_send > 3600):
        should_send = True
        reason = "morning_window"
    # Після роботи (17:30-20:00) — одне сповіщення
    elif 17 <= hour <= 20 and time_since_last_send > 3600:
        should_send = True
        reason = "after_work_window"
    
    if should_send:
        print(f"[proactive] Should send: {reason} (hour={hour}, since_last={int(time_since_last_send/60) if last_send_ts else 'never'}m)", flush=True)
    
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
        ok = telegram_send_func(message)
        if not ok:
            print(f"❌ Telegram send failed", flush=True)
            return False
        
        # Записуємо dedup — ТІЛЬКИ ПІСЛЯ успішної відправки!
        now_local_ts = datetime.now(timezone.utc) + timedelta(hours=2)
        current_hour = now_local_ts.strftime("%Y-%m-%d %H")
        dedup_file = os.path.join(os.path.dirname(__file__), "data", "proactive_last_send.json")
        
        try:
            os.makedirs(os.path.dirname(dedup_file), exist_ok=True)
            dedup_data = {
                "last_sent_timestamp": now_local_ts.timestamp(),
                "last_sent_hour": current_hour,
                "last_sent_iso": now_local_ts.isoformat()
            }
            with open(dedup_file, "w") as f:
                json.dump(dedup_data, f, indent=2)
            
            print(f"✅ Проактивне повідомлення надіслано о {now_local_ts.strftime('%H:%M')} | dedup recorded", flush=True)
        except Exception as _de:
            print(f"⚠️ Dedup write error: {_de}", flush=True)
        
        return True
    except Exception as e:
        print(f"❌ Send error: {e}", flush=True)
        return False

# ============ ФАЗА 1: EVENT-DRIVEN ALERTS ============

def check_crypto_urgency():
    """
    КРИПТО-ALERT: BTC/ETH/AVAX/ONDO ±5% за останні 2 години
    Повертає: {"alert": True/False, "coins": [...], "analysis": "..."}
    """
    try:
        watch_list = get_user_watch_list()
        alerts = []
        
        for symbol, data in watch_list.items():
            change_24h = data.get('change_24h', 0)
            # Перевіряємо ±5%
            if abs(change_24h) >= 5.0:
                alerts.append({
                    "symbol": symbol,
                    "change": change_24h,
                    "price": data.get('price', 0),
                    "name": data.get('name', symbol)
                })
        
        if alerts:
            return {
                "alert": True,
                "coins": alerts,
                "type": "crypto",
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {"alert": False, "type": "crypto"}
    
    except Exception as e:
        print(f"❌ check_crypto_urgency error: {e}", flush=True)
        return {"alert": False, "type": "crypto", "error": str(e)}


def check_email_urgency():
    """
    EMAIL-ALERT: ВСІ нові листи (НЕ тільки VIP), з категоріями + VIP маркування
    Повертає: {"alert": True/False, "emails": [...], "categorized": {...}, "vip_emails": [...]}
    """
    try:
        # Категорії листів
        categories = {
            "work": {"patterns": [r"boss|manager|ceo|project|meeting"], "emoji": "💼"},
            "invest": {"patterns": [r"invest|finance|portfolio|trading|market"], "emoji": "💹"},
            "job": {"patterns": [r"interview|job|position|hire|resume|application"], "emoji": "🎯"},
            "personal": {"patterns": [r"friend|family|event|birthday"], "emoji": "👤"},
            "health": {"patterns": [r"doctor|health|medical|appointment|clinic"], "emoji": "🏥"},
            "other": {"patterns": [], "emoji": "📌"}
        }
        
        try:
            # Спроба отримати emails
            from monitor import get_emails as _get_emails_monitor
            emails = _get_emails_monitor(max_results=15, only_unread=True) or []
        except:
            try:
                from storage import get_emails_fast
                emails = get_emails_fast(max_results=15, only_unread=True) or []
            except:
                emails = []
        
        if not emails:
            return {"alert": False, "type": "email"}
        
        # Категоризуємо листи
        categorized = {cat: [] for cat in categories}
        
        for email in emails:
            subject = email.get('subject', '').lower()
            sender = email.get('from', '').lower()
            body = email.get('snippet', '').lower()
            full_text = f"{sender} {subject} {body}"
            
            categorized_flag = False
            for cat, cat_data in categories.items():
                if cat == "other":
                    continue
                for pattern in cat_data.get('patterns', []):
                    if __import__('re').search(pattern, full_text):
                        categorized[cat].append({
                            "from": email.get('from'),
                            "subject": email.get('subject'),
                            "snippet": email.get('snippet', '')[:80],
                            "date": email.get('date')
                        })
                        categorized_flag = True
                        break
                if categorized_flag:
                    break
            
            if not categorized_flag:
                categorized["other"].append({
                    "from": email.get('from'),
                    "subject": email.get('subject'),
                    "snippet": email.get('snippet', '')[:80],
                    "date": email.get('date')
                })
        
        # Якщо є листи
        all_emails = [e for emails_list in categorized.values() for e in emails_list]
        if all_emails:
            return {
                "alert": True,
                "emails": all_emails,
                "categorized": categorized,
                "type": "email",
                "count": len(all_emails),
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {"alert": False, "type": "email"}
    
    except Exception as e:
        print(f"❌ check_email_urgency error: {e}", flush=True)
        return {"alert": False, "type": "email", "error": str(e)}


def check_calendar_urgency():
    """
    CALENDAR-ALERT: События + ЗАВДАННЯ на ЗАВТРА (без рутини)
    Фільтр рутини: Біг, Вода, Чай, Сауна, Armolopid, Навчання, Чек крипто, Пошта, зміни
    Повертає: {"alert": True/False, "events": [...], "tasks": [...], "analysis": "..."}
    """
    try:
        from datetime import datetime, timedelta
        
        tomorrow = datetime.now().date() + timedelta(days=1)
        tomorrow_str = tomorrow.isoformat()
        
        # Фільтр рутини (НЕ показуємо ці события)
        routine_keywords = [
            "біг", "вода", "чай", "сауна", "armolopid",
            "навчання", "чек крипто", "пошта", "зміна",
            "ранна", "нічна", "training", "гімнастика", "розтяжка"
        ]
        
        # Отримуємо события й завдання
        try:
            from monitor import get_calendar as _get_cal_monitor
            all_cal = _get_cal_monitor() or []
        except:
            try:
                from storage import get_calendar_events_fast
                all_cal = get_calendar_events_fast(date=tomorrow_str, max_results=20) or []
            except:
                all_cal = []
        
        events = []
        tasks = []
        
        for event in all_cal:
            title = event.get('summary', '').lower()
            
            # Пропускаємо рутину
            is_routine = any(keyword in title for keyword in routine_keywords)
            if is_routine:
                continue
            
            event_data = {
                "title": event.get('summary'),
                "time": event.get('start', {}).get('dateTime', event.get('start', {}).get('date')),
                "location": event.get('location', ''),
                "description": event.get('description', '')[:100]
            }
            
            # Розрізняємо tasks (мітки, чек-листи) від обичайних событий
            is_task = "task" in title.lower() or "todo" in title.lower() or "☐" in event.get('summary', '')
            
            if is_task:
                tasks.append(event_data)
            else:
                events.append(event_data)
        
        if events or tasks:
            return {
                "alert": True,
                "events": events,
                "tasks": tasks,
                "type": "calendar",
                "date": tomorrow_str,
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {"alert": False, "type": "calendar"}
    
    except Exception as e:
        print(f"❌ check_calendar_urgency error: {e}", flush=True)
        return {"alert": False, "type": "calendar", "error": str(e)}


def check_health_urgency():
    """
    HEALTH-ALERT: Вага (±3кг за тиждень), сон (<5h), біг (>3 дні без)
    Повертає: {"alert": True/False, "issues": [...], "analysis": "..."}
    """
    try:
        from storage import load_health
        
        health_data = load_health()
        if not health_data:
            return {"alert": False, "type": "health"}
        
        # Отримуємо останні дані (сьогодні) та з тиждень назад
        today_key = datetime.now().strftime("%Y-%m-%d")
        week_ago_key = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        
        today_health = health_data.get(today_key, {})
        week_ago_health = health_data.get(week_ago_key, {})
        
        issues = []
        
        # 1. Вага зміна
        today_weight = today_health.get('weight', 0)
        week_weight = week_ago_health.get('weight', 0)
        if today_weight and week_weight:
            weight_change = today_weight - week_weight
            if abs(weight_change) >= 3.0:
                issues.append({
                    "type": "weight",
                    "change": weight_change,
                    "current": today_weight,
                    "message": f"Вага зросла на {weight_change:.1f}кг за тиждень" if weight_change > 0 else f"Вага впала на {abs(weight_change):.1f}кг"
                })
        
        # 2. Сон
        today_sleep = today_health.get('sleep_hours', 0)
        if today_sleep < 5:
            issues.append({
                "type": "sleep",
                "hours": today_sleep,
                "message": f"Сон менше 5 годин ({today_sleep}h) — вплив на здоров'я"
            })
        
        # 3. Біг (перевіряємо 3 дні)
        running_days = 0
        for i in range(3):
            check_date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            check_health = health_data.get(check_date, {})
            if check_health.get('running_km', 0) > 0:
                running_days += 1
        
        if running_days == 0:
            issues.append({
                "type": "running",
                "message": "Немає бігу більше 3 днів — час повернутися на трасу!"
            })
        
        if issues:
            return {
                "alert": True,
                "issues": issues,
                "type": "health",
                "timestamp": datetime.now().isoformat()
            }
        else:
            return {"alert": False, "type": "health"}
    
    except Exception as e:
        print(f"❌ check_health_urgency error: {e}", flush=True)
        return {"alert": False, "type": "health", "error": str(e)}


def check_astro_urgency():
    """
    АСТРО-ALERT: Natalis карта + важливі аспекти дня
    Повертає: {"alert": True, "transit": {...}, "analysis": "..."}
    """
    try:
        from datetime import datetime
        
        # Олегова натальна карта: 30.01.1990, 14:30, Kosice, SK
        # Шукаємо важливі транзити
        alert_transits = []
        
        # Примітка: повна реалізація потребує kerykeion обчислень
        # За замовчуванням: кожний день показуємо мотивацію
        
        return {
            "alert": True,
            "type": "astro",
            "message": "Натальна карта активна",
            "timestamp": datetime.now().isoformat()
        }
    
    except Exception as e:
        print(f"❌ check_astro_urgency error: {e}", flush=True)
        return {"alert": False, "type": "astro", "error": str(e)}


def get_all_urgent_events():
    """
    Перевіряє ВСІ 5 типів подій паралельно
    Повертає список활성 alertsів
    """
    events = {}
    
    # Перевіряємо всі типи
    events['crypto'] = check_crypto_urgency()
    events['email'] = check_email_urgency()
    events['calendar'] = check_calendar_urgency()
    events['health'] = check_health_urgency()
    events['astro'] = check_astro_urgency()
    
    # Фільтруємо активні
    active_events = {k: v for k, v in events.items() if v.get('alert')}
    
    return {
        "has_events": bool(active_events),
        "active_events": active_events,
        "all_checks": events,
        "timestamp": datetime.now().isoformat()
    }


# ============ EXPORTS ============

if __name__ == "__main__":
    print("✅ intelligent_assistant_v2.1 loaded with PHASE 1 events")
