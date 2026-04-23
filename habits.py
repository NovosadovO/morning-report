#!/usr/bin/env python3
"""
Трекер звичок — щоденні питання + тижневий/місячний звіт.

Звички:
  🚿 Холодний душ  — питання о 09:00
  🏃 Біг           — питання о 19:10
  💧 Вода          — питання о 20:00

Inline кнопки ✅ / ❌ в Telegram.
"""

import os, json, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]

_DIR        = os.path.dirname(os.path.abspath(__file__))
HABITS_FILE = os.path.join("/tmp", "habits_data.json")
SENT_FILE   = os.path.join("/tmp", "habits_sent.json")

# ─── КОНФІГ ЗВИЧОК ────────────────────────────────────────────────────────────

HABITS = [
    {"id": "shower", "name": "Холодний душ", "emoji": "🚿", "hour": 9,  "minute": 0},
    {"id": "run",    "name": "Біг",           "emoji": "🏃", "hour": 19, "minute": 10},
    {"id": "water",  "name": "Вода (2л)",      "emoji": "💧", "hour": 20, "minute": 0},
]

SLEEP_HOUR   = 8
SLEEP_MINUTE = 0

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def api(method, data=None):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    payload = json.dumps(data or {}).encode()
    req     = urllib.request.Request(url, data=payload,
              headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"API error {method}: {e}")
        return {}

def load_data():
    try:
        with open(HABITS_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(HABITS_FILE, "w") as f:
        json.dump(data, f)

def load_sent():
    try:
        with open(SENT_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_sent(sent):
    with open(SENT_FILE, "w") as f:
        json.dump(sent, f)

def today_key():
    return (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")

def now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)

# ─── НАДСИЛАННЯ ПИТАННЯ ───────────────────────────────────────────────────────

def send_question(habit):
    """Надсилає питання з inline кнопками ✅ ❌"""
    text = f"{habit['emoji']} <b>{habit['name']}</b>\nТи це зробив сьогодні?"
    result = api("sendMessage", {
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Так",  "callback_data": f"habit_yes_{habit['id']}"},
                {"text": "❌ Ні",   "callback_data": f"habit_no_{habit['id']}"},
            ]]
        }
    })
    print(f"Sent question: {habit['name']}")
    return result


def send_sleep_question():
    """Надсилає питання про сон з кнопками годин."""
    api("sendMessage", {
        "chat_id": TELEGRAM_CHAT,
        "text": "😴 <b>Сон</b>\nСкільки годин спав цієї ночі?",
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "😩 ≤5г", "callback_data": "sleep_5"},
                {"text": "😐 6г",  "callback_data": "sleep_6"},
                {"text": "🙂 7г",  "callback_data": "sleep_7"},
                {"text": "😊 8г+", "callback_data": "sleep_8"},
            ]]
        }
    })
    print("Sent sleep question")

# ─── ОБРОБКА ВІДПОВІДІ ────────────────────────────────────────────────────────

def handle_callback(callback_query):
    """Обробляє натискання кнопки."""
    data    = callback_query.get("data", "")
    msg_id  = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]
    cb_id   = callback_query["id"]

    if not data.startswith("habit_"):
        return

    parts  = data.split("_")  # habit_yes_shower
    answer = parts[1]          # yes / no
    hab_id = parts[2]          # shower / run / water

    habit = next((h for h in HABITS if h["id"] == hab_id), None)
    if not habit:
        return

    today    = today_key()
    db       = load_data()
    day_data = db.setdefault(today, {})
    day_data[hab_id] = (answer == "yes")
    save_data(db)

    # Відповідь на кнопку
    if answer == "yes":
        reply = f"✅ Відмінно! <b>{habit['name']}</b> — зараховано 💪"
    else:
        reply = f"❌ <b>{habit['name']}</b> — не зараховано. Завтра краще!"

    # Редагуємо повідомлення (прибираємо кнопки)
    api("editMessageText", {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": reply,
        "parse_mode": "HTML"
    })

    # Підтверджуємо callback
    api("answerCallbackQuery", {"callback_query_id": cb_id})

# ─── ТИЖНЕВИЙ ЗВІТ ────────────────────────────────────────────────────────────

def weekly_report():
    db   = load_data()
    now  = now_local()
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]

    lines = ["📊 <b>ТИЖНЕВИЙ ЗВІТ ЗВИЧОК</b>\n"]
    for h in HABITS:
        done  = sum(1 for d in days if db.get(d, {}).get(h["id"]) is True)
        pct   = done / 7 * 100
        bar   = "▓" * done + "░" * (7 - done)
        medal = "🥇" if done == 7 else ("🥈" if done >= 5 else ("🥉" if done >= 3 else "😔"))
        lines.append(f"{h['emoji']} <b>{h['name']}</b>\n"
                     f"<code>{bar}</code>  {done}/7  {pct:.0f}%  {medal}")

    # Сон
    sleep_vals = [db.get(d, {}).get("sleep") for d in days if db.get(d, {}).get("sleep")]
    if sleep_vals:
        avg = sum(sleep_vals) / len(sleep_vals)
        sleep_icon = "😊" if avg >= 8 else ("🙂" if avg >= 7 else ("😐" if avg >= 6 else "😩"))
        lines.append(f"\n😴 <b>Сон</b>\nСередній: <b>{avg:.1f}г</b>  {sleep_icon}  (за {len(sleep_vals)} днів)")

    lines.append(f"\n<i>Тиждень {days[0]} – {days[-1]}</i>")
    api("sendMessage", {
        "chat_id": TELEGRAM_CHAT,
        "text": "\n".join(lines),
        "parse_mode": "HTML"
    })
    print("Weekly report sent.")

# ─── МІСЯЧНИЙ ЗВІТ ────────────────────────────────────────────────────────────

def monthly_report():
    db  = load_data()
    now = now_local()
    days_in_month = now.day
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_in_month - 1, -1, -1)]

    lines = [f"📅 <b>МІСЯЧНИЙ ЗВІТ — {now.strftime('%B %Y')}</b>\n"]
    for h in HABITS:
        done  = sum(1 for d in days if db.get(d, {}).get(h["id"]) is True)
        pct   = done / days_in_month * 100
        bar_l = int(pct / 10)
        bar   = "▓" * bar_l + "░" * (10 - bar_l)
        medal = "🏆" if pct >= 90 else ("🥇" if pct >= 70 else ("🥈" if pct >= 50 else "💪"))
        lines.append(f"{h['emoji']} <b>{h['name']}</b>\n"
                     f"<code>{bar}</code>  {done}/{days_in_month}  {pct:.0f}%  {medal}")

    # Сон
    sleep_vals = [db.get(d, {}).get("sleep") for d in days if db.get(d, {}).get("sleep")]
    if sleep_vals:
        avg = sum(sleep_vals) / len(sleep_vals)
        best = max(sleep_vals)
        worst = min(sleep_vals)
        sleep_icon = "😊" if avg >= 8 else ("🙂" if avg >= 7 else ("😐" if avg >= 6 else "😩"))
        lines.append(f"\n😴 <b>Сон</b>\n"
                     f"Середній: <b>{avg:.1f}г</b>  {sleep_icon}\n"
                     f"Найкращий: {best}г  ·  Найгірший: {worst}г")

    lines.append(f"\n<i>Дані за {days_in_month} днів</i>")
    api("sendMessage", {
        "chat_id": TELEGRAM_CHAT,
        "text": "\n".join(lines),
        "parse_mode": "HTML"
    })
    print("Monthly report sent.")

# ─── ГОЛОВНИЙ ЦИКЛ ────────────────────────────────────────────────────────────

def run():
    print("=== Habits tracker started ===", flush=True)
    offset = 0

    while True:
        now  = now_local()
        sent = load_sent()
        today = today_key()

        # Перевіряємо чи час надсилати питання про звички
        for h in HABITS:
            key = f"{today}_{h['id']}"
            if sent.get(key):
                continue
            if now.hour == h["hour"] and now.minute >= h["minute"]:
                send_question(h)
                sent[key] = True
                save_sent(sent)

        # Питання про сон о 8:00
        sleep_key = f"{today}_sleep_q"
        if not sent.get(sleep_key) and now.hour == SLEEP_HOUR and now.minute >= SLEEP_MINUTE:
            send_sleep_question()
            sent[sleep_key] = True
            save_sent(sent)

        # Тижневий звіт — щонеділі о 20:30
        if now.weekday() == 6 and now.hour == 20 and now.minute >= 30:
            wkey = f"weekly_{today}"
            if not sent.get(wkey):
                weekly_report()
                sent[wkey] = True
                save_sent(sent)

        # Місячний звіт — останній день місяця о 21:00
        next_day = (now + timedelta(days=1))
        if next_day.month != now.month and now.hour == 21:
            mkey = f"monthly_{now.strftime('%Y-%m')}"
            if not sent.get(mkey):
                monthly_report()
                sent[mkey] = True
                save_sent(sent)

        time.sleep(30)  # перевіряємо кожні 30 секунд


if __name__ == "__main__":
    run()
