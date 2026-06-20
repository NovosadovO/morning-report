"""
Intelligent Assistant v2.0 — Проактивний помічник Олега
Сам читає: пошту, календар, крипто
Пише першим: 2-3 рази на день + при подіях (алерти)
Аналіз: контекстна Gemini-аналітика
"""

import os
import json
import time
from datetime import datetime, timedelta
import urllib.request
import urllib.error

# ============ CONFIG ============
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GMAIL_SERVICE = None  # підключається з контексту
CALENDAR_SERVICE = None

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

def get_important_emails(gmail_service, max_results=5):
    """Отримує важливі нові листи"""
    try:
        results = gmail_service.users().messages().list(
            userId='me',
            q='is:unread is:important',
            maxResults=max_results
        ).execute()
        
        messages = results.get('messages', [])
        emails = []
        
        for msg in messages:
            email_data = gmail_service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='metadata',
                metadataHeaders=['From', 'Subject', 'Date']
            ).execute()
            
            headers = email_data['payload']['headers']
            email_dict = {k['name']: k['value'] for k in headers}
            
            emails.append({
                "from": email_dict.get('From', 'Unknown'),
                "subject": email_dict.get('Subject', 'No subject'),
                "date": email_dict.get('Date', ''),
                "id": msg['id']
            })
        
        return emails
    except Exception as e:
        print(f"❌ Gmail error: {e}")
        return []

# ============ CALENDAR: Найближчі события ============

def get_upcoming_events(calendar_service, hours_ahead=2):
    """Отримує события з календаря на найближчі N годин"""
    try:
        now = datetime.utcnow().isoformat() + 'Z'
        future = (datetime.utcnow() + timedelta(hours=hours_ahead)).isoformat() + 'Z'
        
        events_result = calendar_service.events().list(
            calendarId='primary',
            timeMin=now,
            timeMax=future,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        return events
    except Exception as e:
        print(f"❌ Calendar error: {e}")
        return []

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
    """Вирішує чи потреба писати першим (динамічно)"""
    
    # Якщо останнє повідомлення давніше за 2 години
    if (time.time() - last_message_time) > (user_activity_idle_minutes * 60):
        return True
    
    # Ранок (07:00-09:00) — спец час?
    hour = datetime.now().hour
    if 7 <= hour <= 9:
        return True
    
    return False

def send_proactive_message(telegram_send_func, gmail_service, calendar_service):
    """Основна функція проактивного повідомлення"""
    
    print("\n🤖 [Intelligent Assistant] Generating proactive message...")
    
    # Збираємо всю інформацію
    crypto_top20 = get_coingecko_top20()
    crypto_changes = analyze_crypto_changes(crypto_top20)
    
    emails = get_important_emails(gmail_service, max_results=3)
    
    calendar_events = get_upcoming_events(calendar_service, hours_ahead=3)
    
    user_state = {
        "status": "active",
        "current_time": datetime.now().strftime("%H:%M"),
        "work_shift": "morning" if 6 <= datetime.now().hour < 18 else "night"
    }
    
    # Генеруємо контекстну аналітику
    insight = generate_contextual_insight(
        crypto_changes if crypto_changes else {"status": "Стабільно"},
        emails,
        calendar_events,
        user_state
    )
    
    # Формуємо повідомлення
    message = f"""
👋 Привіт Олег! 

Я сам проаналізував твою ситуацію:

{insight}

---
🤖 Проактивний аналіз | {datetime.now().strftime('%H:%M')}
    """
    
    # Надсилаємо
    try:
        telegram_send_func(message)
        print(f"✅ Повідомлення надіслано о {datetime.now().strftime('%H:%M')}")
        return True
    except Exception as e:
        print(f"❌ Помилка при відправці: {e}")
        return False

# ============ EXPORTS ============

if __name__ == "__main__":
    print("✅ intelligent_assistant_v2.py loaded")
