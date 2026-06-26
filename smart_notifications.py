"""
Smart Notifications v1.0 — Розумні, окремі сповіщення
Берем дані БЕЗПОСЕРЕДНЬО з API (Gmail, Google Calendar, CoinGecko)
Аналізуємо і пишемо ОКРЕМО, без спаму
"""

import os
import json
import time
import re
from datetime import datetime, timedelta, timezone

# ============ CONFIG ============

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

NOTIFICATIONS_SENT_FILE = os.path.join(_DATA_DIR, "smart_notifications_sent.json")

VIP_CONTACTS = {
    "boss": ["boss", "manager", "ceo", "director", "minebea", "mitsumi"],
    "investors": ["interfinance", "interfin", "maros", "sivak", "invest"],
    "hr": ["hr", "recruit", "interview", "job", "position"],
}

# ============ UTILS ============

def _load_dedup():
    if os.path.exists(NOTIFICATIONS_SENT_FILE):
        try:
            with open(NOTIFICATIONS_SENT_FILE, "r") as f:
                return json.load(f) or {}
        except:
            pass
    return {}

def _save_dedup(dedup):
    try:
        os.makedirs(os.path.dirname(NOTIFICATIONS_SENT_FILE), exist_ok=True)
        with open(NOTIFICATIONS_SENT_FILE, "w") as f:
            json.dump(dedup, f, indent=2)
    except:
        pass

def _should_notify(notification_id: str, min_hours_between: int = 3) -> bool:
    dedup = _load_dedup()
    
    if notification_id not in dedup:
        return True
    
    last_sent_ts = dedup[notification_id]
    now_ts = datetime.now(timezone.utc).timestamp()
    hours_passed = (now_ts - last_sent_ts) / 3600
    
    return hours_passed >= min_hours_between

def _mark_notified(notification_id: str):
    dedup = _load_dedup()
    dedup[notification_id] = datetime.now(timezone.utc).timestamp()
    _save_dedup(dedup)

# ============ DUMMY FUNCTIONS (для тесту без реальних API) ============

def get_important_event() -> dict or None:
    """Для тесту — можемо повернути щось потішне
    У реальності це читатиме Gmail/Calendar/CoinGecko
    """
    
    # Тестова імітація
    notification_id = f"test_notification_{datetime.now().strftime('%H')}"
    
    if _should_notify(notification_id, min_hours_between=1):
        return {
            "type": "email",
            "from": "boss@minebea.com",
            "subject": "Project Update",
            "snippet": "Please review the attached document",
            "priority": "critical",
            "reason": "VIP boss",
            "notification_id": notification_id
        }
    
    return None

def format_notification(event: dict) -> str:
    if not event:
        return ""
    
    event_type = event.get("type")
    
    if event_type == "email":
        from_addr = event.get("from", "Unknown")
        subject = event.get("subject", "No subject")
        reason = event.get("reason", "")
        
        return f"""📧 <b>ВАЖЛИВИЙ ЛИСТ</b>

<b>Від:</b> {from_addr}
<b>Тема:</b> {subject}
<b>Причина:</b> {reason}"""
    
    elif event_type == "calendar":
        title = event.get("title", "Event")
        time_until = event.get("time_until", "")
        
        return f"""📅 <b>ПОДІЯ</b>

<b>Назва:</b> {title}
<b>Коли:</b> {time_until}"""
    
    elif event_type == "health":
        message = event.get("message", "")
        return f"""💪 <b>ЗДОРОВ'Я</b>

{message}"""
    
    elif event_type == "crypto":
        coin = event.get("coin", "")
        change = event.get("change", 0)
        price = event.get("price", 0)
        emoji = "📈" if change > 0 else "📉"
        
        return f"""{emoji} <b>КРИПТО</b>

<b>Монета:</b> {coin}
<b>Зміна:</b> {change:+.1f}%
<b>Ціна:</b> ${price:,.0f}"""
    
    return ""

def get_next_important_event():
    """Заглушка функція"""
    return get_important_event()

# ============ TEST ============

if __name__ == "__main__":
    print("Smart Notifications модуль завантажено ✅")
    event = get_next_important_event()
    if event:
        text = format_notification(event)
        print(f"\nТестовий event:\n{text}")
    else:
        print("Немає подій для сповіщення")

