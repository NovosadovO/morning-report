"""
Intelligent Assistant v2.0 — Проактивний помічник Олега
Сам читає: пошту, календар, крипто
Пише першим: 2-3 рази на день + при подіях (алерти)
Аналіз: контекстна Gemini-аналітика
"""

import os
import json
import time
import sys
from datetime import datetime, timedelta
import urllib.request
import urllib.error

# Імпортуємо функції з monitor.py
sys.path.insert(0, os.path.dirname(__file__))
try:
    from monitor import get_emails, get_calendar, get_prices
except ImportError:
    get_emails = get_calendar = get_prices = None
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

def analyze_crypto_changes(crypto_data):
    """Аналізує важливі зміни у крипто"""
    important = []
    
    for coin in crypto_data:
        name = coin.get('name', 'Unknown')
        symbol = coin.get('symbol', '').upper()
        price = coin.get('current_price', 0)
        change_24h = coin.get('price_change_percentage_24h', 0)
        
        # Алерти
        if abs(change_24h) >= TRIGGER_BTC_THRESHOLD:
            direction = "📈 РІСТ" if change_24h > 0 else "📉 ПАДІННЯ"
            important.append({
                "coin": f"{name} ({symbol})",
                "price": f"${price:,.2f}",
                "change": f"{change_24h:+.2f}%",
                "direction": direction,
                "alert": True
            })
    
    return important

# ============ EMAIL: Важливі листи ============

def get_important_emails():
    """Отримує важливі нові листи (через monitor.get_emails)"""
    try:
        if not get_emails:
            return []
        email_block = get_emails()
        # Повертає строку HTML або dict з items
        if isinstance(email_block, dict) and "items" in email_block:
            return email_block["items"][:5]
        return []
    except Exception as e:
        print(f"❌ Email error: {e}")
        return []

# ============ CALENDAR: Найближчі события ============

def get_upcoming_calendar_events():
    """Отримує события з календаря на наступні 48 годин"""
    try:
        if not get_calendar:
            return []
        # get_calendar повертає HTML-блок на сьогодні+завтра
        # Для проактивного помічника просто отримуємо інформацію
        calendar_block = get_calendar()
        # Це текстовий блок, не стуктуровані данные
        # Для справжньої роботи потребуємо _fetch_events_all_calendars з monitor.py
        return calendar_block
    except Exception as e:
        print(f"❌ Calendar error: {e}")
        return ""

# ============ AI ANALYSIS: Gemini контекстна аналітика ============

def _gem_post(url, body, tag="gem"):
    """POST запит до Gemini з обробкою помилок"""
    try:
        headers = {"Content-Type": "application/json"}
        data = json.dumps(body).encode('utf-8')
        
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_data = json.loads(e.read().decode('utf-8'))
        return {"error": error_data.get('error', {}).get('message', str(e))}
    except Exception as e:
        return {"error": str(e)}

def generate_contextual_insight(crypto_info, emails, calendar_events, user_state):
    """Генерує контекстну аналітику через Gemini"""
    
    # Готуємо контекст для AI
    context = f"""
Профіль користувача: Олег (Kosice, SK)
Час: {datetime.now().strftime('%Y-%m-%d %H:%M')}
Статус: {user_state.get('status', 'active')}

📊 КРИПТО (CoinGecko TOP-20):
{json.dumps(crypto_info, indent=2, ensure_ascii=False)}

📧 ПОШТА (Важливі нові листи):
{json.dumps(emails, indent=2, ensure_ascii=False)}

📅 КАЛЕНДАР (Найближчі события):
{json.dumps(calendar_events, indent=2, ensure_ascii=False)}

Аналізуючи всю цю інформацію — дай мені ОДН цілісний висновок на українській мові.
Стиль: теплий, персональний, як розумний друг ("Привіт Олег, я сам проаналізував...").
Максимум 150 слів, але змістовно.
"""
    
    body = {
        "contents": [{
            "parts": [{
                "text": context
            }]
        }],
        "generationConfig": {
            "temperature": 1,
            "maxOutputTokens": 500,
            "thinkingConfig": {
                "thinkingBudget": 0
            }
        }
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    response = _gem_post(url, body, "contextual_insight")
    
    if 'error' in response:
        return f"⚠️ AI аналіз недоступний: {response['error']}"
    
    try:
        text = response['candidates'][0]['content']['parts'][0]['text']
        return text
    except (KeyError, IndexError):
        return "⚠️ AI не спрацювало"

# ============ MAIN: Проактивне повідомлення ============

def should_send_proactive_message(last_message_time, user_activity_idle_minutes=120):
    """Вирішує чи потреба писати першим (динамічно, 1 РАЗ НА ГОДИНУ)
    
    Критерії:
    - Якщо останнє повідомлення > 120 хв тому (idle)
    - Або ранок (07:00-09:00) 
    - Або після роботи (18:00-19:00)
    
    ВАЖЛИВО: Гарантує МАКСИМУМ 1 повідомлення на годину (dedup по годині)
    """
    
    import os
    import json
    
    current_time = time.time()
    current_hour = datetime.now().strftime("%Y-%m-%d %H:00:00")  # Формат: "2026-06-22 19:00:00"
    
    # 1. Перевіряємо чи вже надіслали повідомлення у ЦІЙ годині (GitHub dedup)
    try:
        _proactive_dedup_file = os.path.join(os.path.dirname(__file__), "data", "proactive_sent_hours.json")
        if os.path.exists(_proactive_dedup_file):
            with open(_proactive_dedup_file, "r") as f:
                _sent_hours = json.load(f) or {}
                if current_hour in _sent_hours:
                    print(f"[proactive] Already sent in hour {current_hour}, skipping", flush=True)
                    return False
    except Exception as e:
        print(f"[proactive] dedup file read error: {e}", flush=True)
    
    # 2. Перевіряємо timing-критерії
    time_since_last = current_time - last_message_time
    hour = datetime.now().hour
    
    should_send = False
    reason = ""
    
    # Якщо користувач неактивний > 120 хвилин
    if time_since_last > (user_activity_idle_minutes * 60):
        should_send = True
        reason = "idle>120m"
    # Ранок (07:00-09:00) — Олег прокидається
    elif 7 <= hour <= 9:
        should_send = True
        reason = "morning"
    # Після роботи (17:00-19:00) — вихід на нічну
    elif 17 <= hour <= 19:
        should_send = True
        reason = "after-work"
    
    if should_send:
        print(f"[proactive] Should send: {reason}, time_since_last={int(time_since_last/60)}m", flush=True)
    
    return should_send

def send_proactive_message(telegram_send_func):
    """Основна функція проактивного повідомлення
    
    Args:
        telegram_send_func: функція для надсилання Telegram-повідомлень
    
    Збирає: крипто (CoinGecko), пошту (monitor), календар (monitor)
    Генерує одну контекстну Gemini-аналітику + надсилає
    """
    
    print("\n🤖 [Intelligent Assistant] Generating proactive message...")
    
    # Збираємо всю інформацію
    crypto_top20 = get_coingecko_top20()
    crypto_changes = analyze_crypto_changes(crypto_top20)
    
    emails = get_important_emails()
    
    calendar_info = get_upcoming_calendar_events()
    
    user_state = {
        "status": "active",
        "current_time": datetime.now().strftime("%H:%M"),
        "work_shift": "morning" if 6 <= datetime.now().hour < 18 else "night"
    }
    
    # Генеруємо контекстну аналітику
    insight = generate_contextual_insight(
        crypto_changes if crypto_changes else {"status": "Стабільно"},
        emails,
        calendar_info,
        user_state
    )
    
    # Формуємо повідомлення
    message = f"""👋 Привіт Олег! 

Я сам проаналізував твою ситуацію:

{insight}

---
🤖 Проактивний аналіз | {datetime.now().strftime('%H:%M')}"""
    
    # Надсилаємо
    try:
        telegram_send_func(message)
        
        # ВАЖЛИВО: Записуємо що у ЦІЙ ГОДИНІ повідомлення вже надіслане
        import os
        import json
        current_hour = datetime.now().strftime("%Y-%m-%d %H:00:00")
        _proactive_dedup_file = os.path.join(os.path.dirname(__file__), "data", "proactive_sent_hours.json")
        
        try:
            os.makedirs(os.path.dirname(_proactive_dedup_file), exist_ok=True)
            _sent_hours = {}
            if os.path.exists(_proactive_dedup_file):
                with open(_proactive_dedup_file, "r") as f:
                    _sent_hours = json.load(f) or {}
            
            # Записуємо поточну годину
            _sent_hours[current_hour] = datetime.now().isoformat()
            
            # Очищуємо старі записи (старші за 25 годин)
            _cutoff = (datetime.now() - timedelta(hours=25)).strftime("%Y-%m-%d %H:00:00")
            _sent_hours = {k: v for k, v in _sent_hours.items() if k >= _cutoff}
            
            with open(_proactive_dedup_file, "w") as f:
                json.dump(_sent_hours, f, indent=2)
            
            print(f"✅ Повідомлення надіслано о {datetime.now().strftime('%H:%M')}, dedup recorded", flush=True)
        except Exception as _de:
            print(f"⚠️ Dedup record error: {_de}", flush=True)
        
        return True
    except Exception as e:
        print(f"❌ Помилка при відправці: {e}", flush=True)
        return False

# ============ EXPORTS ============

if __name__ == "__main__":
    print("✅ intelligent_assistant_v2.py loaded")
