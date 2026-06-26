"""
Smart Notifications v2.0 — Розумні, окремі сповіщення
Берем дані БЕЗПОСЕРЕДНЬО з API (Gmail IMAP, Google Calendar, CoinGecko)
Аналізуємо контекст і пишемо ОКРЕМО, коли дійсно потрібно
"""

import os
import json
import time
import re
import imaplib
import email as email_lib
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.header import decode_header

# ============ CONFIG ============

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GMAIL_USER = os.getenv("GMAIL_USER", "novosadovoleg@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
NOTIFICATIONS_SENT_FILE = os.path.join(_DATA_DIR, "smart_notifications_sent.json")

VIP_CONTACTS = {
    "boss": ["boss", "manager", "ceo", "director", "minebea", "mitsumi"],
    "investors": ["interfinance", "interfin", "maros", "sivak", "invest", "maroš"],
    "hr": ["hr", "recruit", "interview", "job", "position", "hire"],
}

# ============ UTILS ============

def _load_dedup():
    """Завантажити деdup-стан"""
    if os.path.exists(NOTIFICATIONS_SENT_FILE):
        try:
            with open(NOTIFICATIONS_SENT_FILE, "r") as f:
                return json.load(f) or {}
        except:
            pass
    return {}

def _save_dedup(dedup):
    """Зберегти dedup-стан"""
    try:
        os.makedirs(os.path.dirname(NOTIFICATIONS_SENT_FILE), exist_ok=True)
        with open(NOTIFICATIONS_SENT_FILE, "w") as f:
            json.dump(dedup, f, indent=2)
    except:
        pass

def _should_notify(notification_id: str, min_hours_between: int = 3) -> bool:
    """Перевіра чи можна надіслати (деdup)"""
    dedup = _load_dedup()
    
    if notification_id not in dedup:
        return True
    
    last_sent_ts = dedup[notification_id]
    now_ts = datetime.now(timezone.utc).timestamp()
    hours_passed = (now_ts - last_sent_ts) / 3600
    
    return hours_passed >= min_hours_between

def _mark_notified(notification_id: str):
    """Позначити що відправили"""
    dedup = _load_dedup()
    dedup[notification_id] = datetime.now(timezone.utc).timestamp()
    _save_dedup(dedup)

# ============ EMAIL ============

def _gmail_connect():
    """Підключення до Gmail IMAP (з більшим timeout)"""
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=10)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        return mail
    except Exception as e:
        print(f"[SMART_NOTIF] Gmail connect error: {e}")
        return None

def _decode_header(header_str):
    """Декодує заголовок email"""
    if not header_str:
        return ""
    try:
        decoded_parts = []
        for part, charset in decode_header(header_str):
            if isinstance(part, bytes):
                decoded_parts.append(part.decode(charset or 'utf-8', errors='ignore'))
            else:
                decoded_parts.append(str(part))
        return "".join(decoded_parts)
    except:
        return str(header_str)

def check_for_important_email() -> dict or None:
    """Перевіра на важливі листи
    
    Returns: dict або None
    """
    try:
        mail = _gmail_connect()
        if not mail:
            return None
        
        mail.select("INBOX")
        
        # Беремо останні 10 непрочитаних листів
        _, uids = mail.uid('search', None, 'UNSEEN')
        email_uids = uids[0].split()[-10:] if uids[0] else []
        
        for uid in email_uids:
            _, msg_data = mail.uid('fetch', uid, '(RFC822)')
            
            if not msg_data or not msg_data[0]:
                continue
            
            try:
                msg = email_lib.message_from_bytes(msg_data[0][1])
                sender = _decode_header(msg.get('From', ''))
                subject = _decode_header(msg.get('Subject', ''))
                
                sender_lower = sender.lower()
                subject_lower = subject.lower()
                
                # Перевіра на VIP
                for vip_type, patterns in VIP_CONTACTS.items():
                    for pattern in patterns:
                        if pattern in sender_lower or pattern in subject_lower:
                            notification_id = f"email_vip_{vip_type}_{uid.decode()[-8:]}"
                            if _should_notify(notification_id, min_hours_between=2):
                                return {
                                    "type": "email",
                                    "from": sender,
                                    "subject": subject,
                                    "priority": "critical" if vip_type == "boss" else "high",
                                    "reason": f"VIP {vip_type}",
                                    "notification_id": notification_id
                                }
                
                # Перевіра на важливі теми
                important_topics = ["job", "invest", "health", "interview", "offer"]
                for topic in important_topics:
                    if topic in subject_lower:
                        notification_id = f"email_topic_{topic}_{uid.decode()[-8:]}"
                        if _should_notify(notification_id, min_hours_between=3):
                            return {
                                "type": "email",
                                "from": sender,
                                "subject": subject,
                                "priority": "high",
                                "reason": f"Topic: {topic}",
                                "notification_id": notification_id
                            }
            
            except Exception as _e:
                continue
        
        mail.close()
    
    except Exception as e:
        print(f"[SMART_NOTIF] email check error: {e}")
    
    return None

# ============ CALENDAR ============

def check_for_calendar_alert() -> dict or None:
    """Перевіра на приближні события"""
    try:
        # TODO: Інтегрувати з Google Calendar API
        # На даний момент просто заглушка
        return None
    except Exception as e:
        print(f"[SMART_NOTIF] calendar check error: {e}")
    
    return None

# ============ CRYPTO ============

def check_for_crypto_movement() -> dict or None:
    """Перевіра на крипто рухи"""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
        
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode())
        except:
            return None
        
        # Перевіра BTC
        btc_data = data.get("bitcoin", {})
        btc_change = btc_data.get("usd_24h_change", 0)
        
        if abs(btc_change) >= 10:
            notification_id = f"crypto_btc_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
            
            if _should_notify(notification_id, min_hours_between=6):
                emoji = "📈" if btc_change > 0 else "📉"
                return {
                    "type": "crypto",
                    "coin": "BTC",
                    "change": btc_change,
                    "price": btc_data.get("usd", 0),
                    "priority": "high",
                    "notification_id": notification_id
                }
    
    except Exception as e:
        print(f"[SMART_NOTIF] crypto check error: {e}")
    
    return None

# ============ HEALTH ============

def check_for_health_alert() -> dict or None:
    """Перевіра на здоров'я"""
    try:
        health_file = os.path.join(_DATA_DIR, "health.json")
        if not os.path.exists(health_file):
            return None
        
        with open(health_file, "r") as f:
            health_data = json.load(f) or {}
        
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_health = health_data.get(today_str, {})
        
        notification_id = f"health_{today_str}"
        
        if not _should_notify(notification_id, min_hours_between=24):
            return None
        
        alerts = []
        
        # Перевіра сну
        sleep_hours = today_health.get("sleep_hours", 0)
        if sleep_hours > 0 and sleep_hours < 5:
            alerts.append(f"⚠️ Спав лише {sleep_hours}h — потрібно більше!")
        
        # Перевіра ваги
        weight = today_health.get("weight", 0)
        if weight > 85:
            alerts.append(f"⚖️ Вага {weight}kg — час худнути!")
        
        if alerts:
            return {
                "type": "health",
                "message": "\n".join(alerts),
                "priority": "medium",
                "notification_id": notification_id
            }
    
    except Exception as e:
        print(f"[SMART_NOTIF] health check error: {e}")
    
    return None

# ============ MAIN ============

def get_next_important_event() -> dict or None:
    """Знайти наступну важливу подію для сповіщення
    
    Пріоритет:
    1. Email VIP (critical)
    2. Email важливі теми (high)
    3. Крипто >10% (high)
    4. Здоров'я (medium)
    
    Returns: dict або None
    """
    
    # Перевіра в порядку пріоритету
    email_alert = check_for_important_email()
    if email_alert:
        return email_alert
    
    crypto_alert = check_for_crypto_movement()
    if crypto_alert:
        return crypto_alert
    
    health_alert = check_for_health_alert()
    if health_alert:
        return health_alert
    
    return None

def format_notification(event: dict) -> str:
    """Форматує сповіщення для Telegram"""
    if not event:
        return ""
    
    event_type = event.get("type")
    
    if event_type == "email":
        from_addr = event.get("from", "Unknown")
        subject = event.get("subject", "No subject")
        reason = event.get("reason", "")
        
        # Обрізаємо довгі рядки
        if len(from_addr) > 40:
            from_addr = from_addr[:37] + "..."
        if len(subject) > 50:
            subject = subject[:47] + "..."
        
        return f"""📧 <b>ВАЖЛИВИЙ ЛИСТ</b>

<b>Від:</b> {from_addr}
<b>Тема:</b> {subject}
<b>Причина:</b> {reason}"""
    
    elif event_type == "crypto":
        coin = event.get("coin", "")
        change = event.get("change", 0)
        price = event.get("price", 0)
        emoji = "📈" if change > 0 else "📉"
        
        return f"""{emoji} <b>КРИПТО-ALERT</b>

<b>Монета:</b> {coin}
<b>Зміна 24h:</b> {change:+.1f}%
<b>Ціна:</b> ${price:,.0f}"""
    
    elif event_type == "health":
        message = event.get("message", "")
        return f"""💪 <b>ЗДОРОВ'Я</b>

{message}"""
    
    return ""
