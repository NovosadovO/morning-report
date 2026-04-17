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


def get_updates(offset=0):
    result = api("getUpdates", {"offset": offset, "timeout": 30, "limit": 10})
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
