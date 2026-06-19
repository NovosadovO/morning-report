"""
Інтелектуальний асистент — «мозок» бота.
Сам розуміє що робити, коли писати, що актуально.
"""

import os, sys, json, time, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
import threading
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

# ─── СТАН АСИСТЕНТА ───────────────────────────────────────────────────────────

_CHAT_ID = 2100366814
_STATE = {
    "last_message_sent_at": 0,  # Unix time — щоб не спамити
    "morning_greeted": False,   # привіт уранці вже був
    "shift_notified": False,    # про зміну попередили
    "events_checked": False,    # календар вже перевірили
    "last_context": {},         # останній контекст для порівняння
}
_STATE_FILE = "data/assistant_state.json"


def _load_state():
    global _STATE
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE) as f:
                _STATE.update(json.load(f))
    except Exception as e:
        print(f"[assistant] load_state error: {e}")


def _save_state():
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, 'w') as f:
            json.dump(_STATE, f)
    except Exception as e:
        print(f"[assistant] save_state error: {e}")


# ─── УТИЛІТАРНІ ФУНКЦІЇ ───────────────────────────────────────────────────────

def _now_local():
    """Поточний час у Кошіце (UTC+2)."""
    return datetime.now(timezone.utc) + timedelta(hours=2)


def _send_telegram(text: str):
    """Відправляє повідомлення в Telegram."""
    try:
        token = os.environ.get("TELEGRAM_TOKEN", "")
        if not token:
            print("[assistant] TELEGRAM_TOKEN не налаштовано")
            return False
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            pass
        print(f"[assistant] message sent: {len(text)} chars")
        return True
    except Exception as e:
        print(f"[assistant] send_telegram error: {e}")
        return False


def _should_send_message() -> bool:
    """Чи достатньо часу пройшло з останнього повідомлення (щоб не спамити)."""
    now = time.time()
    last = _STATE.get("last_message_sent_at", 0)
    gap = 5 * 60  # мінімум 5 хвилин між повідомленнями
    return (now - last) >= gap


def _mark_message_sent():
    """Відзначає що повідомлення було відправлено."""
    _STATE["last_message_sent_at"] = time.time()
    _save_state()


# ─── КОНТЕКСТ (що сьогодні актуально) ─────────────────────────────────────────

def _get_context():
    """Збирає весь контекст: час, дата, календар, пошта, ціни, здоров'я."""
    ctx = {}
    now = _now_local()
    
    # Час/дата
    ctx["now"] = now
    ctx["hour"] = now.hour
    ctx["minute"] = now.minute
    ctx["date"] = now.strftime("%d.%m.%Y")
    ctx["weekday"] = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][now.weekday()]
    
    # Яка зміна?
    try:
        from context import get_shift_from_calendar
        shift = get_shift_from_calendar()
        ctx["shift"] = shift  # "early", "night", "free"
    except Exception:
        ctx["shift"] = "unknown"
    
    # Календар на сьогодні/завтра
    try:
        from context import get_calendar_events
        cal = get_calendar_events(days=2)
        ctx["today_events"] = cal.get("today_text", "")
        ctx["tomorrow_events"] = cal.get("tomorrow_text", "")
    except Exception:
        ctx["today_events"] = ""
        ctx["tomorrow_events"] = ""
    
    # Непрочитана пошта (скорочено)
    try:
        from monitor import _get_unread_email_count, get_latest_emails
        count = _get_unread_email_count()
        ctx["unread_count"] = count
        if count > 0:
            emails = get_latest_emails(limit=2)
            ctx["latest_emails"] = emails[:150]
    except Exception:
        ctx["unread_count"] = 0
        ctx["latest_emails"] = ""
    
    # Крипто ціни (якщо піднялись/впали > 5%)
    try:
        from monitor import get_prices
        prices = get_prices()
        ctx["prices"] = prices[:200] if prices else ""
    except Exception:
        ctx["prices"] = ""
    
    # Вага / здоров'я
    try:
        from weight import get_trend
        ctx["weight_trend"] = get_trend() or ""
    except Exception:
        ctx["weight_trend"] = ""
    
    return ctx


# ─── ЛОГІКА ІНТЕЛЕКТУАЛЬНОГО НАДСИЛАННЯ ───────────────────────────────────────

def _is_wakeup_time(hour: int) -> bool:
    """Чи це ранок (06:00–08:00)?"""
    return 6 <= hour < 8


def _is_before_shift(hour: int, shift: str) -> bool:
    """Чи за 30–60 хв до початку зміни?"""
    if shift == "early":
        return 5 <= hour < 6
    if shift == "night":
        return 17 <= hour < 18
    return False


def _is_during_shift(hour: int, shift: str) -> bool:
    """Чи прямо зараз зміна?"""
    if shift == "early":
        return 6 <= hour < 18
    if shift == "night":
        return 18 <= hour or hour < 6
    return False


def _is_after_shift(hour: int, minute: int, shift: str) -> bool:
    """Чи за 30 хв–2 год після кінця зміни?"""
    if shift == "early":
        return 18 <= hour < 20
    if shift == "night":
        return 6 <= hour < 8
    return False


def _is_evening(hour: int) -> bool:
    """Чи вечір (19:00–23:00)?"""
    return 19 <= hour < 23


def should_send_proactive_message(ctx: dict) -> Optional[str]:
    """
    Визначає чи потрібно відправити проактивне повідомлення.
    Повертає (reason, message_text) або None.
    """
    if not _should_send_message():
        return None
    
    hour = ctx["hour"]
    shift = ctx["shift"]
    today_events = ctx.get("today_events", "")
    unread = ctx.get("unread_count", 0)
    latest = ctx.get("latest_emails", "")
    
    # 1. Ранковий привіт (коли прокидається) — із календарем й пошою
    if _is_wakeup_time(hour) and not _STATE.get("morning_greeted"):
        msg = f"☀️ Доброго ранку, Олеже!\n\n"
        if today_events and "нічого" not in today_events.lower():
            msg += f"Сьогодні:\n{today_events}\n\n"
        if unread > 0:
            msg += f"✉️ Нових листів: {unread}\n"
            if latest:
                msg += f"Останні: {latest}\n"
        msg += "🎯 Готовий рухатися вперед!"
        _STATE["morning_greeted"] = True
        _save_state()
        return msg
    
    # 2. Перед зміною — нагадування про час
    if _is_before_shift(hour, shift) and not _STATE.get("shift_notified"):
        if shift == "early":
            msg = "⏰ Рання зміна вже близько! Вставай, чай готовий ☕\n06:00–18:00"
        else:
            msg = "🌙 Нічна зміна за 30 хв!\n18:00–06:00"
        _STATE["shift_notified"] = True
        _save_state()
        return msg
    
    # 3. Перевірка важливих подій в календарі на день
    if hour == 8 and not _STATE.get("events_checked"):
        if today_events and "нічого" not in today_events.lower():
            msg = f"📅 Не забудь про важливе сьогодні:\n{today_events}"
            _STATE["events_checked"] = True
            _save_state()
            return msg
    
    # 4. Нагадування про неважну пошту чи можливості
    if unread > 3 and hour in [11, 15, 20]:
        msg = f"📬 У тебе {unread} непрочитаних листів.\n"
        if latest:
            msg += f"Останній: {latest[:80]}..."
        return msg
    
    # 5. Вечірній check-in (19:00) — як день прийшов
    if _is_evening(hour) and hour == 19:
        msg = "📊 Як в тебе справи? Як день пройшов? (Можеш просто написати мені)"
        return msg
    
    # 6. Крипто alert (якщо піднялись/впали > 5%)
    prices = ctx.get("prices", "")
    if prices and ("▲" in prices or "▼" in prices) and hour in [8, 12, 16, 20]:
        msg = f"📈 Обновлення крипто:\n{prices}"
        return msg
    
    return None


# ─── ГОЛОВНА ФУНКЦІЯ (запускати щохвилини або щогодини) ──────────────────────

def run():
    """Основна функція: перевіряє контекст і відправляє необхідні повідомлення."""
    _load_state()
    
    try:
        ctx = _get_context()
        msg = should_send_proactive_message(ctx)
        
        if msg:
            print(f"[assistant] proactive message: {msg[:80]}...")
            if _send_telegram(msg):
                _mark_message_sent()
        
        # Оновлюємо стан щодня (скидаємо флаги о 00:05)
        if ctx["hour"] == 0 and ctx["minute"] < 10:
            _STATE.update({
                "morning_greeted": False,
                "shift_notified": False,
                "events_checked": False,
            })
            _save_state()
            
    except Exception as e:
        print(f"[assistant] run error: {e}")


# ─── ЗАПУСК ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
