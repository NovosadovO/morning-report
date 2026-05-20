"""
planner.py — Планувальник подій через чат

Схема:
  1. Щовечора о 21:00 (або неділя о 20:00 для тижня) бот питає про плани
  2. Користувач відповідає вільним текстом
  3. Gemini парсить → список подій з датою/часом/назвою
  4. Бот показує список і питає "Підтвердити?"
  5. Після підтвердження — записує в Google Calendar з нагадуванням за 30 хв

Стан зберігається в data/planner_state.json
"""

import os, json, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

# ─── ENV ─────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8374312425:AAHqrQCEqrgtVdl5Te5WhWblM2ESCnqhpfk")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "2100366814")
GEMINI_KEY     = os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")

_DIR       = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR  = os.path.join(_DIR, "data")
os.makedirs(_DATA_DIR, exist_ok=True)

STATE_FILE = os.path.join(_DATA_DIR, "planner_state.json")

# ─── STATE ───────────────────────────────────────────────────────────────────

def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {}

def _save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

def get_state():
    return _load_state()

def set_state(mode, data=None):
    """mode: None | 'awaiting_tomorrow' | 'awaiting_week' | 'awaiting_confirm'"""
    s = _load_state()
    s["mode"] = mode
    s["data"] = data or {}
    s["updated"] = datetime.now(timezone.utc).isoformat()
    _save_state(s)

def clear_state():
    _save_state({"mode": None, "data": {}})

# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def _tg(method, params):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    body = json.dumps(params).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def send(text, keyboard=None):
    params = {"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"}
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    _tg("sendMessage", params)

# ─── GEMINI ──────────────────────────────────────────────────────────────────

def _gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024}
    }).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

# ─── ПАРСИНГ ПОДІЙ ───────────────────────────────────────────────────────────

def parse_events_from_text(user_text: str, base_date: datetime) -> list:
    """
    Gemini парсить вільний текст → список подій.
    Повертає: [{"title": str, "date": "YYYY-MM-DD", "time": "HH:MM" or None, "allday": bool}]
    """
    base_str = base_date.strftime("%Y-%m-%d")
    weekday_ua = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"]
    today_name = weekday_ua[base_date.weekday()]

    prompt = f"""Сьогодні {today_name}, {base_str}.
Користувач написав про свої плани: "{user_text}"

Витягни всі заплановані події і поверни JSON масив.
Для кожної події:
- "title": назва події (коротко, по-українськи)
- "date": дата у форматі YYYY-MM-DD (якщо "завтра" → {(base_date + timedelta(days=1)).strftime('%Y-%m-%d')}, якщо "в неділю" → відповідна дата, якщо без дати → {(base_date + timedelta(days=1)).strftime('%Y-%m-%d')})
- "time": час початку HH:MM (24г формат) або null якщо не вказано
- "allday": true якщо весь день (немає часу), false якщо є час

Поверни ТІЛЬКИ JSON масив без пояснень, наприклад:
[{{"title":"Спортзал","date":"2024-05-21","time":"08:00","allday":false}}]

Якщо немає жодних подій — поверни [].
"""
    try:
        raw = _gemini(prompt)
        # Витягуємо JSON з відповіді
        m = re.search(r'\[.*?\]', raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        print(f"parse_events_from_text error: {e}")
    return []

# ─── GOOGLE CALENDAR WRITE ───────────────────────────────────────────────────

def _get_token_write():
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return None
    try:
        import sys
        sys.path.insert(0, _DIR)
        from monitor import _get_google_token
        creds_data = json.loads(creds_json)
        return _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar")
    except Exception as e:
        print(f"_get_token_write error: {e}")
        return None

def create_calendar_event(title: str, date: str, time_str: str = None, allday: bool = True) -> bool:
    """Створює подію в Google Calendar. Повертає True якщо успішно."""
    token = _get_token_write()
    if not token:
        print("create_calendar_event: no token")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    cal_id = "novosadovoleg@gmail.com"

    if allday or not time_str:
        event = {
            "summary": title,
            "start": {"date": date},
            "end":   {"date": date},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 480}]  # нагадування о 8 ранку
            }
        }
    else:
        # Конвертуємо в UTC (Олег у UTC+2)
        try:
            dt_local = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M")
            dt_local = dt_local.replace(tzinfo=timezone(timedelta(hours=2)))
            start_iso = dt_local.isoformat()
            end_iso   = (dt_local + timedelta(hours=1)).isoformat()
        except:
            return False

        event = {
            "summary": title,
            "start": {"dateTime": start_iso, "timeZone": "Europe/Kiev"},
            "end":   {"dateTime": end_iso,   "timeZone": "Europe/Kiev"},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 30}]  # нагадування за 30 хв
            }
        }

    try:
        url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal_id, safe='')}/events"
        body = json.dumps(event).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            return bool(result.get("id"))
    except Exception as e:
        print(f"create_calendar_event error: {e}")
        return False

# ─── ПИТАННЯ ПРО ПЛАНИ ───────────────────────────────────────────────────────

def ask_tomorrow_plans():
    """Бот питає про плани на завтра. Викликається о 21:00."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    tomorrow = (now + timedelta(days=1)).strftime("%A")
    weekday_ua = {
        "Monday":"понеділок","Tuesday":"вівторок","Wednesday":"середу",
        "Thursday":"четвер","Friday":"п'ятницю","Saturday":"суботу","Sunday":"неділю"
    }
    tomorrow_ua = weekday_ua.get(tomorrow, "завтра")

    set_state("awaiting_tomorrow", {"base_date": now.strftime("%Y-%m-%d")})
    send(
        f"📅 <b>Плани на {tomorrow_ua}?</b>\n\n"
        f"Напиши вільним текстом що плануєш — я сам розберу і запишу в календар.\n\n"
        f"<i>Наприклад: зранку спортзал о 8, о 14 лікар, ввечері зателефонувати Максиму</i>\n\n"
        f"Або напиши <b>немає</b> якщо нічого не плануєш.",
        keyboard=[[{"text": "⏭ Пропустити", "callback_data": "planner_skip"}]]
    )

def ask_week_plans():
    """Бот питає про плани на тиждень. Викликається в неділю о 20:00."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    set_state("awaiting_week", {"base_date": now.strftime("%Y-%m-%d")})
    send(
        "📆 <b>Плани на наступний тиждень?</b>\n\n"
        "Напиши що плануєш — я сам розберу дати і запишу всі події в календар.\n\n"
        "<i>Наприклад: в понеділок о 10 стоматолог, в середу тренування о 18, в п'ятницю відгул</i>\n\n"
        "Або напиши <b>немає</b> якщо нічого не плануєш.",
        keyboard=[[{"text": "⏭ Пропустити", "callback_data": "planner_skip"}]]
    )

# ─── ОБРОБКА ВІДПОВІДІ ───────────────────────────────────────────────────────

def handle_planner_reply(user_text: str) -> bool:
    """
    Обробляє відповідь користувача якщо бот очікує плани.
    Повертає True якщо повідомлення оброблено (не треба передавати далі).
    """
    state = _load_state()
    mode = state.get("mode")

    if mode not in ("awaiting_tomorrow", "awaiting_week"):
        return False

    text_lower = user_text.strip().lower()

    # Скасування
    if text_lower in ("немає", "нічого", "нема", "ні", "no", "skip", "пропустити", "-"):
        clear_state()
        send("✅ Зрозумів, нічого не записую.")
        return True

    # Парсимо події
    base_date_str = state.get("data", {}).get("base_date", "")
    try:
        base_date = datetime.strptime(base_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        base_date = base_date + timedelta(hours=2)  # local
    except:
        base_date = datetime.now(timezone.utc) + timedelta(hours=2)

    send("⏳ Аналізую...")

    events = parse_events_from_text(user_text, base_date)

    if not events:
        clear_state()
        send("🤷 Не зміг розпізнати жодної події. Спробуй написати чіткіше, наприклад: <i>завтра о 10 лікар</i>")
        return True

    # Показуємо що знайшли і просимо підтвердити
    lines = []
    for i, ev in enumerate(events, 1):
        t = f" о {ev['time']}" if ev.get("time") else " (весь день)"
        d = ev.get("date", "")
        try:
            d_fmt = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m")
        except:
            d_fmt = d
        lines.append(f"{i}. 📌 <b>{ev['title']}</b> — {d_fmt}{t}")

    set_state("awaiting_confirm", {"events": events, "original": user_text})

    send(
        "Ось що я знайшов:\n\n" + "\n".join(lines) + "\n\n"
        "Записати все в календар?",
        keyboard=[
            [{"text": "✅ Так, записати", "callback_data": "planner_confirm"}],
            [{"text": "✏️ Редагувати", "callback_data": "planner_edit"},
             {"text": "❌ Скасувати",  "callback_data": "planner_cancel"}]
        ]
    )
    return True

def handle_planner_confirm() -> bool:
    """Підтверджено — записуємо всі події в Calendar."""
    state = _load_state()
    if state.get("mode") != "awaiting_confirm":
        return False

    events = state.get("data", {}).get("events", [])
    if not events:
        clear_state()
        send("⚠️ Немає подій для запису.")
        return True

    ok_count = 0
    fail_count = 0
    lines = []

    for ev in events:
        title   = ev.get("title", "Подія")
        date    = ev.get("date", "")
        time_s  = ev.get("time")
        allday  = ev.get("allday", not bool(time_s))

        success = create_calendar_event(title, date, time_s, allday)
        if success:
            ok_count += 1
            t = f" о {time_s}" if time_s else " (весь день)"
            try:
                d_fmt = datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m")
            except:
                d_fmt = date
            lines.append(f"✅ <b>{title}</b> — {d_fmt}{t}")
        else:
            fail_count += 1
            lines.append(f"❌ <b>{title}</b> — помилка запису")

    clear_state()

    result = "\n".join(lines)
    if ok_count > 0:
        reminder_note = "\n\n🔔 Нагадування встановлено автоматично."
    else:
        reminder_note = ""

    send(
        f"📅 <b>Записано в календар:</b>\n\n{result}{reminder_note}"
    )
    return True

def handle_planner_cancel():
    clear_state()
    send("❌ Скасовано. Нічого не записано.")

def handle_planner_edit():
    """Просимо переписати."""
    state = _load_state()
    base_date_str = state.get("data", {}).get("events", [{}])[0].get("date", "")
    try:
        base_date = datetime.strptime(base_date_str, "%Y-%m-%d")
        base_date_out = base_date.strftime("%Y-%m-%d")
    except:
        base_date_out = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")

    set_state("awaiting_tomorrow", {"base_date": base_date_out})
    send("✏️ Напиши плани ще раз:")

# ─── ТРИГЕРИ (викликаються з monitor_loop або habits) ────────────────────────

def check_planner_triggers():
    """Щохвилинно перевіряє чи час питати про плани."""
    try:
        now = datetime.now(timezone.utc) + timedelta(hours=2)
        h, m = now.hour, now.minute

        if m > 4:  # вікно 5 хвилин
            return

        sent_file = os.path.join(_DATA_DIR, "planner_sent.json")
        try:
            with open(sent_file) as f:
                sent = json.load(f)
        except:
            sent = {}

        today_str = now.strftime("%Y-%m-%d")
        weekday   = now.weekday()  # 0=пн, 6=нд

        # Неділя о 20:00 — плани на тиждень
        if weekday == 6 and h == 20:
            key = f"{today_str}_week"
            if not sent.get(key):
                sent[key] = True
                with open(sent_file, "w") as f:
                    json.dump(sent, f)
                ask_week_plans()
                return

        # Щодня о 21:00 (крім неділі — вже питали про тиждень) — плани на завтра
        if h == 21 and weekday != 6:
            key = f"{today_str}_tomorrow"
            if not sent.get(key):
                sent[key] = True
                with open(sent_file, "w") as f:
                    json.dump(sent, f)
                ask_tomorrow_plans()

    except Exception as e:
        print(f"check_planner_triggers error: {e}")
