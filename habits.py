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
    # shower handled dynamically by check_shower_reminder()
    {"id": "run",    "name": "Біг",           "emoji": "🏃", "hour": 19, "minute": 10},
    {"id": "water",  "name": "Вода (2л+)",    "emoji": "💧", "hour": 20, "minute": 0},
    {"id": "tea",    "name": "Трав'яний чай", "emoji": "🍵", "hour": 20, "minute": 10},
    {"id": "sauna",  "name": "Сауна",          "emoji": "🧖", "hour": 20, "minute": 20},
]

SLEEP_HOUR   = 8
SLEEP_MINUTE = 0

# ─── ТИП ЗМІНИ З GOOGLE CALENDAR ─────────────────────────────────────────────

def _get_shift_type():
    """
    Повертає тип дня по Google Calendar:
      'early'   — рання зміна → душ о 05:00
      'night'   — нічна зміна  → душ о 16:30
      'weekend' — вихідний / немає змін → душ о 10:00
    """
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return "weekend"
    try:
        import json as _json, sys, urllib.parse as _up
        sys.path.insert(0, _DIR)
        from monitor import _get_google_token
        creds_data = _json.loads(creds_json)
        token   = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"
        now_utc = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
        from datetime import timedelta as _td
        day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) - _td(hours=2)
        day_end   = day_start + _td(hours=24)
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={_up.quote(day_start.isoformat())}"
            f"&timeMax={_up.quote(day_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=20"
        )
        import urllib.request as _ur
        req = _ur.Request(url, headers=headers)
        with _ur.urlopen(req, timeout=15) as r:
            events = _json.loads(r.read()).get("items", [])
        for ev in events:
            s = ev.get("summary", "").lower()
            if any(x in s for x in ["рання", "ранн", "early"]):
                return "early"
            if any(x in s for x in ["нічна", "нічн", "night"]):
                return "night"
        return "weekend"
    except Exception as e:
        print(f"_get_shift_type error: {e}")
        return "weekend"


def check_shower_reminder():
    """
    Надсилає питання про холодний душ у правильний час:
      рання зміна → 05:00
      нічна зміна → 16:30
      вихідний    → 10:00
    Якщо не відповів — повтор через 1г.
    """
    now   = now_local()
    today = today_key()
    sent  = load_sent()

    # Вже відмітив сьогодні
    db = load_data()
    if db.get(today, {}).get("shower") is not None:
        return

    remind_key = f"{today}_shower_smart"
    if sent.get(remind_key):
        # Повтор через 1г якщо не відповів
        remind2_key = f"{today}_shower_smart2"
        if not sent.get(remind2_key):
            first_sent_time = sent.get(f"{today}_shower_smart_time", 0)
            cur_min = now.hour * 60 + now.minute
            if first_sent_time and cur_min >= first_sent_time + 60:
                if db.get(today, {}).get("shower") is None:
                    _send_shower_question(today)
                    sent[remind2_key] = True
                    save_sent(sent)
        return

    shift   = _get_shift_type()
    cur_min = now.hour * 60 + now.minute

    if shift == "early":
        trigger = 5 * 60       # 05:00
    elif shift == "night":
        trigger = 16 * 60 + 30  # 16:30
    else:
        trigger = 10 * 60       # 10:00

    if cur_min >= trigger:
        _send_shower_question(today)
        sent[remind_key] = True
        sent[f"{today}_shower_smart_time"] = cur_min
        save_sent(sent)


def _send_shower_question(today):
    api("sendMessage", {
        "chat_id": TELEGRAM_CHAT,
        "text": (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🚿 <b>ХОЛОДНИЙ ДУШ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "❄️ Холодна вода бадьорить, зміцнює імунітет\n"
            "та підвищує рівень енергії на весь день.\n\n"
            "<i>Зробив сьогодні?</i>"
        ),
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Так", "callback_data": "habit_yes_shower"},
                {"text": "❌ Ні",  "callback_data": "habit_no_shower"},
            ]]
        }
    })
    print(f"Shower question sent for {today}")


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

HABIT_MESSAGES = {
    "shower": (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚿 <b>ХОЛОДНИЙ ДУШ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "❄️ Холодна вода бадьорить, зміцнює імунітет\n"
        "та підвищує рівень енергії на весь день.\n\n"
        "<i>Зробив сьогодні?</i>"
    ),
    "run": (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🏃 <b>ПРОБІЖКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💨 Навіть 20 хвилин бігу — це заряд\n"
        "ендорфінів і здорове серце.\n\n"
        "<i>Бігав сьогодні?</i>"
    ),
    "water": (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💧 <b>ВОДА 2Л+</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Випив достатньо води сьогодні?</i>"
    ),
    "tea": (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🍵 <b>ТРАВ'ЯНИЙ ЧАЙ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Пив трав'яний чай сьогодні?</i>"
    ),
    "sauna": (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🧖 <b>САУНА</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Був у сауні сьогодні?</i>"
    ),
}

def send_question(habit):
    """Надсилає питання з inline кнопками ✅ ❌"""
    text = HABIT_MESSAGES.get(
        habit["id"],
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{habit['emoji']} <b>{habit['name'].upper()}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<i>Ти це зробив сьогодні?</i>"
    )
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

        # Душ — динамічний час по Calendar
        check_shower_reminder()

        # Перевіряємо чи час надсилати питання про звички
        for h in HABITS:
            if h.get("id") == "shower":
                continue  # shower handled by check_shower_reminder
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

        # Нагадування про біг — о 17:30 якщо ще не відмітив
        run_key_done = f"{today}_run"
        run_remind_key = f"{today}_run_remind"
        if (not sent.get(run_remind_key) and
                now.hour == 17 and now.minute >= 30 and
                load_data().get(today, {}).get("run") is not True):
            api("sendMessage", {
                "chat_id": TELEGRAM_CHAT,
                "text": "🏃 <b>Ще не бігав сьогодні!</b>\nЗалишилось кілька годин — саме час 💪",
                "parse_mode": "HTML"
            })
            sent[run_remind_key] = True
            save_sent(sent)

        # Нагадування перед нічною зміною — о 16:00 (за 2г до 18:00)
        night_pre_key = f"{today}_night_pre"
        if (not sent.get(night_pre_key) and now.hour == 16 and now.minute >= 0 and now.minute < 5):
            # Перевіряємо чи є нічна зміна сьогодні — просто надсилаємо якщо понеділок-неділя
            api("sendMessage", {
                "chat_id": TELEGRAM_CHAT,
                "text": (
                    "🌙 <b>Підготовка до нічної зміни</b>\n\n"
                    "🚿 Прийми холодний душ\n"
                    "💧 Випий воду зараз\n"
                    "🍵 Завари чай з собою\n"
                    "😴 Поспи 1-2 години якщо є час"
                ),
                "parse_mode": "HTML"
            })
            sent[night_pre_key] = True
            save_sent(sent)

        # Нагадування після нічної зміни — о 07:00
        night_post_key = f"{today}_night_post"
        if (not sent.get(night_post_key) and now.hour == 7 and now.minute >= 0 and now.minute < 5):
            api("sendMessage", {
                "chat_id": TELEGRAM_CHAT,
                "text": (
                    "☀️ <b>Після нічної зміни</b>\n\n"
                    "🍵 Випий трав'яний чай\n"
                    "💧 Не забудь про воду\n"
                    "😴 Час відпочивати — солодких снів!"
                ),
                "parse_mode": "HTML"
            })
            sent[night_post_key] = True
            save_sent(sent)

        # Тижневий звіт ліків — щонеділі о 20:40
        meds_weekly_key = f"meds_weekly_{today}"
        if (now.weekday() == 6 and now.hour == 20 and now.minute >= 40
                and not sent.get(meds_weekly_key)):
            try:
                import sys, os as _os
                sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
                from bot import get_meds_report
                api("sendMessage", {
                    "chat_id": TELEGRAM_CHAT,
                    "text": get_meds_report("week"),
                    "parse_mode": "HTML"
                })
                sent[meds_weekly_key] = True
                save_sent(sent)
            except Exception as e:
                print(f"Meds weekly report error: {e}")

        # Місячний звіт ліків — останній день місяця о 21:05
        next_day2 = (now + timedelta(days=1))
        if next_day2.month != now.month and now.hour == 21 and now.minute >= 5:
            mkey2 = f"meds_monthly_{now.strftime('%Y-%m')}"
            if not sent.get(mkey2):
                try:
                    from bot import get_meds_report
                    api("sendMessage", {
                        "chat_id": TELEGRAM_CHAT,
                        "text": get_meds_report("month"),
                        "parse_mode": "HTML"
                    })
                    sent[mkey2] = True
                    save_sent(sent)
                except Exception as e:
                    print(f"Meds monthly report error: {e}")

        # Тижневий звіт ваги — щонеділі о 20:35
        weight_weekly_key = f"weight_weekly_{today}"
        if (now.weekday() == 6 and now.hour == 20 and now.minute >= 35
                and not sent.get(weight_weekly_key)):
            try:
                from weight import format_weekly_weight_report
                api("sendMessage", {
                    "chat_id": TELEGRAM_CHAT,
                    "text": format_weekly_weight_report(),
                    "parse_mode": "HTML"
                })
                sent[weight_weekly_key] = True
                save_sent(sent)
            except Exception as e:
                print(f"Weight weekly report error: {e}")

        # Недільний підсумок о 18:45 — повний звіт тижня
        if now.weekday() == 6 and now.hour == 18 and now.minute >= 45:
            sunday_key = f"sunday_summary_{today}"
            if not sent.get(sunday_key):
                try:
                    import sys, os as _os
                    sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
                    from weekly_report import send_weekly_report
                    send_weekly_report()
                    sent[sunday_key] = True
                    save_sent(sent)
                except Exception as e:
                    print(f"Sunday summary error: {e}")

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
