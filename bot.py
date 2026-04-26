#!/usr/bin/env python3
"""
Telegram bot — відповідає на команди користувача.
Команди:
  /start    — привітання
  /звіт     — повний звіт зараз
  /ціни     — ціни активів
  /погода   — погода Košice
  /календар — події на сьогодні
  /листи    — останні email
  /допомога — список команд
"""

import os
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
OFFSET_FILE    = "/tmp/bot_offset.json"

# Імпортуємо функції з monitor.py
import sys
sys.path.insert(0, os.path.dirname(__file__))
from monitor import get_prices, get_weather, get_calendar, get_emails

# ─── TELEGRAM API ─────────────────────────────────────────────────────────────

def api(method, data=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    payload = json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"API error {method}: {e}")
        return {}


def send(chat_id, text):
    api("sendMessage", {
        "chat_id": chat_id,
        "text": text[:4090],
        "parse_mode": "HTML"
    })


def send_with_buttons(chat_id, text, habit_id):
    return api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Так", "callback_data": f"habit_yes_{habit_id}"},
                {"text": "❌ Ні",  "callback_data": f"habit_no_{habit_id}"},
            ]]
        }
    })


def log_to_calendar(summary, date_str, hour, minute):
    """Додає подію-висновок в Google Calendar через API напряму."""
    try:
        import sys, json as _json, urllib.request, urllib.parse
        from datetime import datetime, timedelta
        sys.path.insert(0, os.path.dirname(__file__))
        from monitor import _get_google_token

        creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
        if not creds_json:
            print("Calendar log: no credentials")
            return

        creds_data = _json.loads(creds_json)
        token = _get_google_token(
            creds_data, "https://www.googleapis.com/auth/calendar.events")

        start_str = f"{date_str}T{hour:02d}:{minute:02d}:00+02:00"
        end_dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M") + timedelta(minutes=30)
        end_str = f"{date_str}T{end_dt.hour:02d}:{end_dt.minute:02d}:00+02:00"

        event = {
            "summary": summary,
            "start": {"dateTime": start_str, "timeZone": "Europe/Bratislava"},
            "end":   {"dateTime": end_str,   "timeZone": "Europe/Bratislava"},
        }
        body = _json.dumps(event).encode()
        req = urllib.request.Request(
            "https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        print(f"Calendar log OK: {summary}")
    except Exception as e:
        print(f"Calendar log error: {e}")


def handle_event_done_callback(callback_query):
    """Обробляє ✅/❌ відповідь на питання 'Виконано?'."""
    import json as _json
    data    = callback_query.get("data", "")
    msg_id  = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]
    cb_id   = callback_query["id"]
    orig    = callback_query["message"].get("text", "")

    # evdone_yes_<key> або evdone_no_<key>
    parts  = data.split("_", 2)
    answer = parts[1] if len(parts) > 1 else "?"
    key    = parts[2] if len(parts) > 2 else ""

    if answer == "yes":
        reply = orig.split("\n")[0] + "\n✅ <b>Виконано!</b>"
    else:
        reply = orig.split("\n")[0] + "\n❌ <b>Не виконано.</b>"

    api("editMessageText", {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": reply,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": []}
    })
    api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Записано ✓"})

    # Зберігаємо статус у файл для підсумку дня
    try:
        results_file = os.path.join(os.path.dirname(__file__), "monitor_event_results.json")
        if os.path.exists(results_file):
            with open(results_file) as f:
                results = _json.load(f)
        else:
            results = {}
        results[key] = answer
        with open(results_file, "w") as f:
            _json.dump(results, f)
    except Exception as e:
        print(f"event results save error: {e}")


def handle_habit_callback(callback_query):
    """Обробляє натискання ✅/❌ на звичках."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from habits import HABITS, load_data, save_data, today_key

    data    = callback_query.get("data", "")
    msg_id  = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]
    cb_id   = callback_query["id"]

    # Обробка сну
    if data.startswith("sleep_"):
        hours = int(data.split("_")[1])
        today    = today_key()
        db       = load_data()
        db.setdefault(today, {})["sleep"] = hours
        save_data(db)
        icons = {5: "😩", 6: "😐", 7: "🙂", 8: "😊"}
        icon  = icons.get(hours, "😴")
        label = f"{hours}г+" if hours == 8 else f"{hours}г"
        api("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": f"😴 <b>Сон</b> — {label} записано  {icon}",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []}
        })
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Збережено ✓"})
        # Логуємо в календар
        from datetime import datetime, timezone, timedelta
        now_l = datetime.now(timezone.utc) + timedelta(hours=2)
        log_to_calendar(f"😴 Сон — {label} {icon}", now_l.strftime("%Y-%m-%d"), 8, 0)
        return True

    if not data.startswith("habit_"):
        return False

    parts  = data.split("_")   # habit_yes_shower
    answer = parts[1]           # yes / no
    hab_id = parts[2]           # shower / run / water

    habit = next((h for h in HABITS if h["id"] == hab_id), None)
    if not habit:
        return False

    # Зберігаємо результат
    today    = today_key()
    db       = load_data()
    db.setdefault(today, {})[hab_id] = (answer == "yes")
    save_data(db)

    # Редагуємо повідомлення — прибираємо кнопки і показуємо результат
    if answer == "yes":
        reply = f"✅ <b>{habit['name']}</b> — зараховано! 💪"
    else:
        reply = f"❌ <b>{habit['name']}</b> — не зараховано. Завтра краще!"

    api("editMessageText", {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": reply,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": []}  # прибираємо кнопки
    })

    # Підтверджуємо callback (прибирає годинник)
    api("answerCallbackQuery", {
        "callback_query_id": cb_id,
        "text": "Збережено ✓"
    })

    # Логуємо результат в Google Calendar
    from datetime import datetime, timezone, timedelta
    now_l = datetime.now(timezone.utc) + timedelta(hours=2)
    mark = "✅" if answer == "yes" else "❌"
    cal_summary = f"{habit['emoji']} {habit['name']} {mark}"
    log_to_calendar(cal_summary, now_l.strftime("%Y-%m-%d"), habit["hour"], habit["minute"])

    return True


def get_updates(offset=0):
    result = api("getUpdates", {"offset": offset, "timeout": 30, "limit": 10,
                                "allowed_updates": ["message", "callback_query"]})
    return result.get("result", [])


def load_offset():
    try:
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    except Exception:
        return 0


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


# ─── КОМАНДИ ──────────────────────────────────────────────────────────────────

HELP_TEXT = """
🤖 <b>Команди бота:</b>

/звіт — повний звіт зараз
/ціни — ціни BTC/ETH/AVAX/ONDO
/погода — погода Košice
/календар — події на сьогодні
/листи — останні email
/допомога — цей список
"""


def handle_command(chat_id, text):
    text = text.strip().lower()

    if text in ["/start", "start"]:
        send(chat_id, "👋 Привіт! Я твій асистент.\n" + HELP_TEXT)

    elif text.startswith("/маршрут") or text.startswith("маршрут"):
        dest = text.replace("/маршрут", "").replace("маршрут", "").strip()
        if not dest:
            send(chat_id, "Вкажи місто: /маршрут Прешов")
        else:
            try:
                from traffic import handle_route_command
                send(chat_id, handle_route_command(dest))
            except Exception as e:
                send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/допомога", "/help", "допомога"]:
        send(chat_id, HELP_TEXT)

    elif text in ["/звіт", "звіт"]:
        send(chat_id, "⏳ Збираю звіт...")
        now = datetime.now(timezone.utc)
        local_time = (now + timedelta(hours=2)).strftime("%H:%M")
        local_date = (now + timedelta(hours=2)).strftime("%d.%m.%Y")
        sections = []
        for fn in [get_prices, get_weather, get_calendar, get_emails]:
            try:
                sections.append(fn())
            except Exception as e:
                print(f"Error in {fn.__name__}: {e}")
        report = f"🕐 <b>Звіт {local_time} · {local_date}</b>\n\n" + "\n\n".join(sections)
        send(chat_id, report)

    elif text in ["/ціни", "ціни"]:
        try:
            send(chat_id, get_prices())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/погода", "погода"]:
        try:
            send(chat_id, get_weather())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/календар", "календар"]:
        try:
            send(chat_id, get_calendar())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/листи", "листи"]:
        try:
            send(chat_id, get_emails())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    else:
        send(chat_id, f"Не розумію команду. Напиши /допомога щоб побачити список команд.")


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    print("=== Bot started, listening for messages ===", flush=True)
    offset = load_offset()

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                save_offset(offset)

                # Обробка кнопок (callback_query)
                cb = update.get("callback_query")
                if cb:
                    if str(cb["message"]["chat"]["id"]) == str(TELEGRAM_CHAT):
                        data = cb.get("data", "")
                        if data.startswith("evdone_"):
                            handle_event_done_callback(cb)
                        else:
                            handle_habit_callback(cb)
                    continue

                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                text = msg.get("text", "")

                # Тільки від авторизованого користувача
                if str(chat_id) != str(TELEGRAM_CHAT):
                    send(chat_id, "⛔ Немає доступу.")
                    continue

                print(f"Message: {text}", flush=True)
                handle_command(chat_id, text)

        except Exception as e:
            print(f"Loop error: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
