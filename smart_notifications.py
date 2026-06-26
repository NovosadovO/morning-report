"""
Smart Notifications v1.0 — Розумні, окремі сповіщення
АІ слідкує за ВСІМ: email, календар, крипто, здоров'я, астро
Пише ОКРЕМО для кожної подієї, коли реально потрібно
БЕЗ спаму, БЕЗ повторень, БЕЗ занадто часто
"""

import os
import json
import time
from datetime import datetime, timedelta, timezone
import re

# ============ CONFIG ============

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

# Деdup-файли
NOTIFICATIONS_SENT_FILE = os.path.join(_DATA_DIR, "smart_notifications_sent.json")

# VIP контакти
VIP_CONTACTS = {
    "boss": ["boss", "manager", "ceo", "director", "minebea", "mitsumi"],
    "investors": ["interfinance", "interfin", "maros", "sivak", "invest", "maroš"],
    "hr": ["hr", "recruit", "interview", "job", "position"],
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
    """Перевіра чи вже відправили це сповіщення (деdup)
    
    Args:
        notification_id: унікальна ідентифікація (напр "email_from_boss_20260626")
        min_hours_between: мінімум годин між повторень
    
    Returns:
        True якщо можна відправити, False якщо вже надсилали
    """
    dedup = _load_dedup()
    
    if notification_id not in dedup:
        return True  # Ніколи не надсилали
    
    last_sent_ts = dedup[notification_id]
    now_ts = datetime.now(timezone.utc).timestamp()
    hours_passed = (now_ts - last_sent_ts) / 3600
    
    return hours_passed >= min_hours_between

def _mark_notified(notification_id: str):
    """Позначити що сповіщення було надіслано"""
    dedup = _load_dedup()
    dedup[notification_id] = datetime.now(timezone.utc).timestamp()
    _save_dedup(dedup)

# ============ EVENT DETECTION ============

def check_for_important_email(get_emails_func) -> dict or None:
    """Перевіра на важливі листи — ВІД VIP, невідомих, важливі теми
    
    Returns:
        {
            "type": "email",
            "from": "...",
            "subject": "...",
            "snippet": "...",
            "priority": "critical|high|medium",
            "reason": "VIP|unknown|important_topic"
        }
        або None якщо немає нічого важливого
    """
    if not get_emails_func:
        return None
    
    try:
        emails = get_emails_func(max_results=5, only_unread=True) or []
        
        for email in emails:
            sender = email.get("from", "").lower()
            subject = email.get("subject", "").lower()
            snippet = email.get("snippet", "")[:100]
            email_id = email.get("id", "")
            
            # Перевіра на VIP контакти
            for vip_type, patterns in VIP_CONTACTS.items():
                for pattern in patterns:
                    if re.search(pattern, sender) or re.search(pattern, subject):
                        notification_id = f"email_vip_{vip_type}_{email_id[:10]}"
                        if _should_notify(notification_id, min_hours_between=2):
                            return {
                                "type": "email",
                                "from": email.get("from"),
                                "subject": email.get("subject"),
                                "snippet": snippet,
                                "priority": "critical" if vip_type == "boss" else "high",
                                "reason": f"VIP {vip_type}",
                                "email_id": email_id,
                                "notification_id": notification_id
                            }
            
            # Перевіра на невідомих людей (не в контактах)
            if "@" in sender:
                notification_id = f"email_unknown_{email_id[:10]}"
                if _should_notify(notification_id, min_hours_between=4):
                    return {
                        "type": "email",
                        "from": email.get("from"),
                        "subject": email.get("subject"),
                        "snippet": snippet,
                        "priority": "high",
                        "reason": "Unknown sender",
                        "email_id": email_id,
                        "notification_id": notification_id
                    }
            
            # Перевіра на важливі теми
            important_topics = ["job", "investment", "health", "interview", "offer", "position"]
            for topic in important_topics:
                if re.search(topic, subject):
                    notification_id = f"email_topic_{topic}_{email_id[:10]}"
                    if _should_notify(notification_id, min_hours_between=3):
                        return {
                            "type": "email",
                            "from": email.get("from"),
                            "subject": email.get("subject"),
                            "snippet": snippet,
                            "priority": "high",
                            "reason": f"Important topic: {topic}",
                            "email_id": email_id,
                            "notification_id": notification_id
                        }
    
    except Exception as e:
        print(f"[SMART_NOTIF] email check error: {e}")
    
    return None

def check_for_health_alert(load_health_func) -> dict or None:
    """Перевіра на здоров'я: сон, біг, вага
    
    Returns:
        {
            "type": "health",
            "alert_type": "sleep|running|weight",
            "message": "...",
            "priority": "high|medium"
        }
        або None
    """
    if not load_health_func:
        return None
    
    try:
        health = load_health_func()
        
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        notification_id = f"health_{today_str}"
        
        if not _should_notify(notification_id, min_hours_between=24):
            return None  # Вже надсилали сьогодні
        
        alerts = []
        
        # Перевіра сну (>5 годин)
        sleep_hours = health.get("sleep_hours", 0)
        if sleep_hours > 0 and sleep_hours < 5:
            alerts.append(f"⚠️ Сьогодні спав лише {sleep_hours}h — потрібно більше сну!")
        
        # Перевіра бігу (3+ дні без)
        last_run_date = health.get("last_run_date")
        if last_run_date:
            try:
                last_run = datetime.fromisoformat(last_run_date)
                days_since = (datetime.now(timezone.utc) - last_run).days
                if days_since >= 3:
                    alerts.append(f"🏃 Ти не тренувався {days_since} днів — час рухатись!")
            except:
                pass
        
        # Перевіра ваги (↑2+ кг)
        current_weight = health.get("weight", 0)
        prev_weight = health.get("prev_weight", 0)
        if current_weight > 0 and prev_weight > 0:
            weight_diff = current_weight - prev_weight
            if weight_diff >= 2:
                alerts.append(f"⚖️ Вага виросла на {weight_diff:.1f}кг ({current_weight}кг) — уважай дієту!")
        
        if alerts:
            return {
                "type": "health",
                "alerts": alerts,
                "message": "\n".join(alerts),
                "priority": "medium",
                "notification_id": notification_id
            }
    
    except Exception as e:
        print(f"[SMART_NOTIF] health check error: {e}")
    
    return None

def check_for_calendar_alert(get_calendar_func) -> dict or None:
    """Перевіра на приближні события (за 1 день, 2 години, 10 хвилин)
    
    Returns:
        {
            "type": "calendar",
            "title": "...",
            "time_until": "за 2 години",
            "priority": "high|medium"
        }
        або None
    """
    if not get_calendar_func:
        return None
    
    try:
        now = datetime.now(timezone.utc) + timedelta(hours=2)
        
        # Фільтруємо рутину
        routine_keywords = ["біг", "вода", "чай", "сауна", "зміна", "armolopid", "ванна", "душ"]
        
        events = get_calendar_func() or []
        
        for event in events:
            title = event.get("summary", "")
            
            # Пропускаємо рутину
            if any(kw.lower() in title.lower() for kw in routine_keywords):
                continue
            
            start_str = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
            if not start_str:
                continue
            
            try:
                event_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                time_until = event_time - now
                minutes_until = time_until.total_seconds() / 60
                
                # Перевіра на відповідні часові проміжки
                notification_id = f"calendar_{title}_{event_time.strftime('%Y%m%d')}"
                
                if minutes_until < 0:
                    continue  # Подія вже минула
                
                # За 1 день (1440 хвилин)
                if 1380 <= minutes_until <= 1440:
                    if _should_notify(notification_id + "_1day", min_hours_between=20):
                        return {
                            "type": "calendar",
                            "title": title,
                            "time_until": "завтра",
                            "minutes_until": minutes_until,
                            "priority": "medium",
                            "notification_id": notification_id + "_1day"
                        }
                
                # За 2 години (120 хвилин)
                elif 110 <= minutes_until <= 130:
                    if _should_notify(notification_id + "_2hours", min_hours_between=1):
                        return {
                            "type": "calendar",
                            "title": title,
                            "time_until": "за 2 години",
                            "minutes_until": minutes_until,
                            "priority": "high",
                            "notification_id": notification_id + "_2hours"
                        }
                
                # За 10 хвилин
                elif 5 <= minutes_until <= 15:
                    if _should_notify(notification_id + "_10min", min_hours_between=0.5):
                        return {
                            "type": "calendar",
                            "title": title,
                            "time_until": "за 10 хвилин",
                            "minutes_until": minutes_until,
                            "priority": "critical",
                            "notification_id": notification_id + "_10min"
                        }
            except:
                pass
    
    except Exception as e:
        print(f"[SMART_NOTIF] calendar check error: {e}")
    
    return None

def check_for_crypto_movement(get_prices_func) -> dict or None:
    """Перевіра на крипто рухи (значні зміни, важливі новини)
    
    Returns:
        {
            "type": "crypto",
            "coin": "BTC",
            "change": -8.5,
            "price": 63500,
            "message": "...",
            "priority": "high"
        }
        або None
    """
    if not get_prices_func:
        return None
    
    try:
        prices = get_prices_func() or {}
        
        for symbol, data in prices.items():
            change_24h = data.get("change_24h", 0)
            
            # Перевіра на значні зміни (>10%)
            if abs(change_24h) >= 10:
                notification_id = f"crypto_{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
                
                if _should_notify(notification_id, min_hours_between=6):
                    emoji = "📈" if change_24h > 0 else "📉"
                    return {
                        "type": "crypto",
                        "coin": symbol,
                        "change": change_24h,
                        "price": data.get("price", 0),
                        "message": f"{emoji} {symbol} змінився на {change_24h:+.1f}% — погляд потрібен",
                        "priority": "high",
                        "notification_id": notification_id
                    }
    
    except Exception as e:
        print(f"[SMART_NOTIF] crypto check error: {e}")
    
    return None

# ============ MAIN ============

def get_next_important_event(get_emails_func, get_calendar_func, load_health_func, get_prices_func) -> dict or None:
    """Знайти НАСТУПНУ найважливішу подію для сповіщення
    
    Пріоритет:
    1. Календар: за 10 хвилин (критично)
    2. Email: від VIP (критично)
    3. Календар: за 2 години (high)
    4. Календар: завтра (medium)
    5. Крипто: >10% (high)
    6. Здоров'я: сон/біг/вага (medium)
    
    Returns: dict або None
    """
    
    # Перевірка критичних подій першими
    cal_critical = check_for_calendar_alert(get_calendar_func)
    if cal_critical and cal_critical.get("priority") == "critical":
        return cal_critical
    
    email_critical = check_for_important_email(get_emails_func)
    if email_critical and email_critical.get("priority") == "critical":
        return email_critical
    
    # Потім high-priority
    if email_critical and email_critical.get("priority") == "high":
        return email_critical
    
    if cal_critical:
        return cal_critical
    
    cal_medium = check_for_calendar_alert(get_calendar_func)
    if cal_medium:
        return cal_medium
    
    crypto_alert = check_for_crypto_movement(get_prices_func)
    if crypto_alert:
        return crypto_alert
    
    health_alert = check_for_health_alert(load_health_func)
    if health_alert:
        return health_alert
    
    return None

def format_notification(event: dict) -> str:
    """Форматує сповіщення в красивий текст
    
    Returns: текст для надсилання в Telegram
    """
    if not event:
        return ""
    
    event_type = event.get("type")
    
    if event_type == "email":
        from_addr = event.get("from", "Unknown")
        subject = event.get("subject", "No subject")
        reason = event.get("reason", "")
        
        text = f"""📧 <b>ВАЖЛИВИЙ ЛИСТ</b>

<b>Від:</b> {from_addr}
<b>Тема:</b> {subject}

<b>Причина:</b> {reason}

⚡ <i>Потребує уваги!</i>"""
        
        return text
    
    elif event_type == "calendar":
        title = event.get("title", "Event")
        time_until = event.get("time_until", "")
        
        emoji = "🔴" if event.get("priority") == "critical" else "📅"
        
        text = f"""{emoji} <b>ПОДІЯ</b>

<b>Назва:</b> {title}
<b>Коли:</b> {time_until}

⏰ <i>Приготуйся!</i>"""
        
        return text
    
    elif event_type == "health":
        message = event.get("message", "")
        
        text = f"""💪 <b>ЗДОРОВ'Я</b>

{message}

🎯 <i>Твій шлях до успіху!</i>"""
        
        return text
    
    elif event_type == "crypto":
        coin = event.get("coin", "")
        change = event.get("change", 0)
        price = event.get("price", 0)
        
        emoji = "📈" if change > 0 else "📉"
        
        text = f"""{emoji} <b>КРИПТО</b>

<b>Монета:</b> {coin}
<b>Зміна:</b> {change:+.1f}%
<b>Ціна:</b> ${price:,.0f}

💰 <i>Потреби анализу!</i>"""
        
        return text
    
    return ""
