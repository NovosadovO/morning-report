"""
planner.py — Планувальник подій через чат

Схема:
  1. О 10:00 бот питає про плани на сьогодні
  2. О 14:00 легке нагадування — "чи є щось записати?" (якщо нічого за день)
  3. О 19:30 — вечірнє питання про плани на завтра (або тиждень у неділю)
  4. Якщо до 19:30 нічого не записано — питання більш наполегливе
  5. Gemini парсить вільний текст → список подій → підтвердження → Google Calendar

Стан: data/planner_state.json
Надіслані: data/planner_sent.json
"""

import os, json, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

# ─── ENV ─────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8374312425:AAHqrQCEqrgtVdl5Te5WhWblM2ESCnqhpfk")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "2100366814")
GEMINI_KEY     = os.environ.get("GEMINI_API_KEY", "")

_DIR       = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR  = os.path.join(_DIR, "data")
os.makedirs(_DATA_DIR, exist_ok=True)

STATE_FILE = os.path.join(_DATA_DIR, "planner_state.json")
SENT_FILE  = os.path.join(_DATA_DIR, "planner_sent.json")

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
    """mode: None | 'awaiting_today' | 'awaiting_tomorrow' | 'awaiting_week' | 'awaiting_confirm' | 'awaiting_time' | 'awaiting_minutes' | 'awaiting_shopping'"""
    s = _load_state()
    s["mode"] = mode
    s["data"] = data or {}
    s["updated"] = datetime.now(timezone.utc).isoformat()
    _save_state(s)

def clear_state():
    _save_state({"mode": None, "data": {}})

# ─── SENT TRACKER ────────────────────────────────────────────────────────────

def _load_sent():
    try:
        with open(SENT_FILE) as f:
            return json.load(f)
    except:
        return {}

def _mark_sent(key):
    sent = _load_sent()
    sent[key] = True
    with open(SENT_FILE, "w") as f:
        json.dump(sent, f)

def _was_sent(key):
    return bool(_load_sent().get(key))

def _mark_recorded(date_str):
    """Відмітити що сьогодні вже щось записали."""
    sent = _load_sent()
    sent[f"{date_str}_recorded"] = True
    with open(SENT_FILE, "w") as f:
        json.dump(sent, f)

def _has_recorded_today(date_str):
    return bool(_load_sent().get(f"{date_str}_recorded"))

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

def _send_force_reply(text):
    """Надсилає повідомлення з force_reply — Telegram відкриє поле вводу."""
    params = {
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"force_reply": True, "input_field_placeholder": "Напиши свої плани..."}
    }
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
    """Gemini парсить вільний текст → список подій."""
    base_str = base_date.strftime("%Y-%m-%d")
    weekday_ua = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"]
    today_name = weekday_ua[base_date.weekday()]

    prompt = f"""Сьогодні {today_name}, {base_str}.
Користувач написав про свої плани: "{user_text}"

Витягни всі заплановані події і поверни JSON масив.
Для кожної події:
- "title": назва події (коротко, по-українськи)
- "date": дата у форматі YYYY-MM-DD. Правила:
  * якщо написано "сьогодні" або без дати → {base_str}
  * якщо написано "завтра" → {(base_date + timedelta(days=1)).strftime('%Y-%m-%d')}
  * якщо написано день тижня ("в понеділок" тощо) → відповідна найближча дата
- "time": час початку HH:MM (24г формат) або null якщо не вказано
- "allday": true якщо весь день (немає часу), false якщо є час

Поверни ТІЛЬКИ JSON масив без пояснень, наприклад:
[{{"title":"Спортзал","date":"{base_str}","time":"08:00","allday":false}}]

Якщо немає жодних подій — поверни [].
"""
    try:
        raw = _gemini(prompt)
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

def _get_primary_calendar_id() -> str:
    """Знаходить primary calendar ID через calendarList (де accessRole=owner або primary=True)."""
    token = _get_token_write()
    if not token:
        return "novosadovoleg@gmail.com"
    try:
        url = "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=50"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            items = json.loads(r.read()).get("items", [])
        # Шукаємо primary calendar
        for it in items:
            if it.get("primary"):
                print(f"_get_primary_calendar_id: found primary = {it['id']}")
                return it["id"]
        # Якщо немає primary — беремо перший з owner
        for it in items:
            if it.get("accessRole") == "owner":
                print(f"_get_primary_calendar_id: found owner = {it['id']}")
                return it["id"]
        print(f"_get_primary_calendar_id: fallback, items={[i['id'] for i in items]}")
    except Exception as e:
        print(f"_get_primary_calendar_id error: {e}")
    return "novosadovoleg@gmail.com"


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
    cal_id = _get_primary_calendar_id()
    print(f"create_calendar_event: writing to cal_id={cal_id}, title={title}")

    if allday or not time_str:
        event = {
            "summary": title,
            "start": {"date": date},
            "end":   {"date": date},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 480}]
            }
        }
    else:
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
                "overrides": [{"method": "popup", "minutes": 30}]
            }
        }

    try:
        url = f"https://www.googleapis.com/calendar/v3/calendars/{urllib.parse.quote(cal_id, safe='')}/events"
        body = json.dumps(event).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            event_id = result.get("id")
            event_link = result.get("htmlLink", "")
            print(f"create_calendar_event: OK id={event_id} link={event_link}")
            return bool(event_id)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"create_calendar_event HTTP {e.code}: {err_body}")
        return False
    except Exception as e:
        print(f"create_calendar_event error: {e}")
        return False

def get_today_planned_events() -> list:
    """Повертає події з календаря на сьогодні (для звіту)."""
    try:
        import sys
        sys.path.insert(0, _DIR)
        from monitor import _get_google_token, _fetch_events_all_calendars
        creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
        if not creds_json:
            return []
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        now = datetime.now(timezone.utc)
        today_start = (now + timedelta(hours=2)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        today_end   = today_start + timedelta(hours=24)
        return _fetch_events_all_calendars(headers, today_start, today_end)
    except Exception as e:
        print(f"get_today_planned_events error: {e}")
        return []

def format_planner_for_report() -> str:
    """Секція для щоденного звіту: плани на сьогодні + чи щось занотовано."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today_str = now.strftime("%Y-%m-%d")
    recorded = _has_recorded_today(today_str)

    events = get_today_planned_events()

    lines = ["📋 <b>ПЛАНИ СЬОГОДНІ</b>"]
    if events:
        for ev in events[:8]:
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            summary = ev.get("summary", "(без назви)")
            try:
                from datetime import datetime as _dt
                dt = _dt.fromisoformat(start.replace("Z", "+00:00"))
                t = (dt + timedelta(hours=2)).strftime("%H:%M") if "T" in start else "весь день"
            except:
                t = "?"
            lines.append(f"• {t} — {summary}")
    else:
        lines.append("• Нічого не заплановано")

    # Віконце: чи занотовано щось нове сьогодні
    if recorded:
        lines.append("\n✏️ <i>Сьогодні ти вже щось занотував</i> ✅")
    else:
        lines.append("\n✏️ <i>Ще нічого не занотовано сьогодні</i>")

    return "\n".join(lines)

# ─── ПИТАННЯ ПРО ПЛАНИ ───────────────────────────────────────────────────────

def ask_today_plans():
    """О 10:00 — питання про плани на сьогодні."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.strftime("%A")
    weekday_ua = {
        "Monday":"понеділок","Tuesday":"вівторок","Wednesday":"середа",
        "Thursday":"четвер","Friday":"п'ятниця","Saturday":"субота","Sunday":"неділя"
    }
    today_ua = weekday_ua.get(today, "сьогодні").upper()

    set_state("awaiting_today", {"base_date": now.strftime("%Y-%m-%d"), "context": "today"})

    send(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌅 <b>ДОБРОГО РАНКУ! {today_ua}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Що плануєш на <b>сьогодні</b>? 📝\n"
        f"<i>Запишу в календар автоматично</i>",
        keyboard=[
            [{"text": "✏️ Написати плани", "callback_data": "planner_write_today"}],
            [{"text": "⏭ Пропустити",      "callback_data": "planner_skip"}]
        ]
    )

def ask_tomorrow_plans():
    """О 19:30 — питання про плани на завтра."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today_str = now.strftime("%Y-%m-%d")
    already_recorded = _has_recorded_today(today_str)

    tomorrow = (now + timedelta(days=1)).strftime("%A")
    weekday_ua = {
        "Monday":"понеділок","Tuesday":"вівторок","Wednesday":"середу",
        "Thursday":"четвер","Friday":"п'ятницю","Saturday":"суботу","Sunday":"неділю"
    }
    tomorrow_ua = weekday_ua.get(tomorrow, "завтра").upper()

    set_state("awaiting_tomorrow", {"base_date": now.strftime("%Y-%m-%d")})

    if already_recorded:
        # Вже щось записував сьогодні — легше питання
        header = f"🌆 <b>ВЕЧІР. ПЛАНИ НА {tomorrow_ua}</b>"
        body = "Щось ще плануєш на завтра? Додати в календар?"
    else:
        # Нічого не записано за день — більш наполегливо
        header = f"🌆 <b>ВЕЧІР. ПЛАНИ НА {tomorrow_ua}</b>"
        body = "⚠️ Сьогодні нічого не занотовано.\nЩо плануєш на завтра — записую?"

    send(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{body}\n"
        f"<i>Наприклад: спортзал о 8, лікар о 14, зателефонувати Максиму</i>",
        keyboard=[
            [{"text": "✏️ Написати плани", "callback_data": "planner_write"}],
            [{"text": "⏭ Пропустити",      "callback_data": "planner_skip"}]
        ]
    )

def ask_week_plans():
    """В неділю о 10:00 — плани на тиждень."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    set_state("awaiting_week", {"base_date": now.strftime("%Y-%m-%d")})
    send(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📆 <b>НЕДІЛЯ — ПЛАНИ НА ТИЖДЕНЬ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Що плануєш на наступний тиждень? 🗓\n"
        "<i>Наприклад: в понеділок о 10 стоматолог, в середу тренування о 18</i>",
        keyboard=[
            [{"text": "✏️ Написати плани", "callback_data": "planner_write"}],
            [{"text": "⏭ Пропустити",      "callback_data": "planner_skip"}]
        ]
    )

def ask_midday_reminder():
    """О 14:00 — легке нагадування якщо нічого не записано."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today_str = now.strftime("%Y-%m-%d")

    if _has_recorded_today(today_str):
        return  # вже щось записав — не турбуємо

    send(
        "💡 <b>Нагадування</b>\n\n"
        "Чи є щось, що хочеш занотувати? 📝\n"
        "<i>Зустріч, завдання, ідея — просто напиши</i>",
        keyboard=[
            [{"text": "✏️ Записати", "callback_data": "planner_write_today"}],
            [{"text": "👍 Все ок",   "callback_data": "planner_skip"}]
        ]
    )

# ─── ОБРОБКА ВІДПОВІДІ ───────────────────────────────────────────────────────

def handle_planner_reply(user_text: str) -> bool:
    """
    Обробляє відповідь користувача.
    Повертає True якщо повідомлення оброблено.
    """
    state = _load_state()
    mode = state.get("mode")

    if mode not in ("awaiting_today", "awaiting_tomorrow", "awaiting_week"):
        return False

    text_lower = user_text.strip().lower()

    # Скасування
    if text_lower in ("немає", "нічого", "нема", "ні", "no", "skip", "пропустити", "-"):
        clear_state()
        send("✅ Зрозумів, нічого не записую.")
        return True

    # База дати
    base_date_str = state.get("data", {}).get("base_date", "")
    context = state.get("data", {}).get("context", "")
    try:
        base_date = datetime.strptime(base_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        base_date = base_date + timedelta(hours=2)
    except:
        base_date = datetime.now(timezone.utc) + timedelta(hours=2)

    send("⏳ Аналізую...")

    events = parse_events_from_text(user_text, base_date)

    if not events:
        clear_state()
        send("🤷 Не зміг розпізнати жодної події. Спробуй чіткіше:\n<i>завтра о 10 лікар, спортзал о 8</i>")
        return True

    # Якщо є події без часу — запитуємо час по черзі
    allday_indices = [i for i, ev in enumerate(events) if ev.get("allday", True) and not ev.get("time")]
    if allday_indices:
        set_state("awaiting_time", {
            "events": events,
            "original": user_text,
            "context": context,
            "pending_time_indices": allday_indices,
            "current_time_idx": allday_indices[0],
        })
        _ask_hour(events[allday_indices[0]])
        return True

    # Всі події мають час — показуємо підтвердження
    _show_confirm(events)
    set_state("awaiting_confirm", {"events": events, "original": user_text, "context": context})
    return True

def _ask_hour(ev: dict):
    """Надсилає кнопки вибору години для події."""
    title = ev.get("title", "подія")
    d = ev.get("date", "")
    try:
        d_fmt = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m")
    except:
        d_fmt = d

    # Кнопки годин: 06–22 по 4 в ряд
    hours = list(range(6, 23))
    rows = []
    row = []
    for h in hours:
        row.append({"text": f"{h:02d}", "callback_data": f"planner_hour_{h:02d}"})
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "🗓 Весь день", "callback_data": "planner_hour_allday"}])

    send(
        f"🕐 <b>О котрій:</b> <i>{title}</i> ({d_fmt})?\n\nОберіть годину:",
        keyboard=rows
    )

def _ask_minute(hour: str, ev: dict):
    """Надсилає кнопки вибору хвилин."""
    title = ev.get("title", "подія")
    rows = [
        [
            {"text": f"{hour}:00", "callback_data": f"planner_min_{hour}_00"},
            {"text": f"{hour}:15", "callback_data": f"planner_min_{hour}_15"},
            {"text": f"{hour}:30", "callback_data": f"planner_min_{hour}_30"},
            {"text": f"{hour}:45", "callback_data": f"planner_min_{hour}_45"},
        ],
        [{"text": "◀️ Назад (година)", "callback_data": "planner_time_back"}]
    ]
    send(f"🕐 <b>{title}</b> о <b>{hour}:??</b>\n\nОберіть хвилини:", keyboard=rows)

def _show_confirm(events: list):
    """Показує підсумок і кнопки підтвердження."""
    lines = []
    for i, ev in enumerate(events, 1):
        t = f" о {ev['time']}" if ev.get("time") else " (весь день)"
        d = ev.get("date", "")
        try:
            d_fmt = datetime.strptime(d, "%Y-%m-%d").strftime("%d.%m")
        except:
            d_fmt = d
        lines.append(f"{i}. 📌 <b>{ev['title']}</b> — {d_fmt}{t}")

    send(
        "Ось що знайшов:\n\n" + "\n".join(lines) + "\n\n"
        "Записати в календар? 📅",
        keyboard=[
            [{"text": "✅ Так, записати", "callback_data": "planner_confirm"}],
            [{"text": "✏️ Редагувати", "callback_data": "planner_edit"},
             {"text": "❌ Скасувати",  "callback_data": "planner_cancel"}]
        ]
    )

def handle_planner_hour(hour: str) -> bool:
    """Обробляє вибір години (або 'allday')."""
    state = _load_state()
    if state.get("mode") != "awaiting_time":
        return False

    data = state.get("data", {})
    events = data.get("events", [])
    pending = data.get("pending_time_indices", [])
    cur_idx = data.get("current_time_idx")

    if hour == "allday":
        # Залишаємо allday=True, переходимо до наступної події без часу
        _advance_time_state(state, events, pending, cur_idx)
        return True

    # Зберігаємо вибрану годину, запитуємо хвилини
    data["selected_hour"] = hour
    set_state("awaiting_minutes", data)
    _ask_minute(hour, events[cur_idx])
    return True

def handle_planner_minute(hour: str, minute: str) -> bool:
    """Обробляє вибір хвилин."""
    state = _load_state()
    if state.get("mode") != "awaiting_minutes":
        return False

    data = state.get("data", {})
    events = data.get("events", [])
    pending = data.get("pending_time_indices", [])
    cur_idx = data.get("current_time_idx")

    # Встановлюємо час для поточної події
    time_str = f"{hour}:{minute}"
    events[cur_idx]["time"] = time_str
    events[cur_idx]["allday"] = False
    data["events"] = events
    data.pop("selected_hour", None)

    set_state("awaiting_time", data)
    _advance_time_state(state, events, pending, cur_idx)
    return True

def handle_planner_time_back() -> bool:
    """Повернутись до вибору години."""
    state = _load_state()
    if state.get("mode") != "awaiting_minutes":
        return False
    data = state.get("data", {})
    events = data.get("events", [])
    cur_idx = data.get("current_time_idx", 0)
    data.pop("selected_hour", None)
    set_state("awaiting_time", data)
    _ask_hour(events[cur_idx])
    return True

def _advance_time_state(state, events, pending, cur_idx):
    """Переходить до наступної події без часу або до підтвердження."""
    data = state.get("data", {})
    remaining = [i for i in pending if i != cur_idx and not events[i].get("time")]

    if remaining:
        data["events"] = events
        data["pending_time_indices"] = remaining
        data["current_time_idx"] = remaining[0]
        set_state("awaiting_time", data)
        _ask_hour(events[remaining[0]])
    else:
        # Всі часи зібрані — показуємо підтвердження
        context = data.get("context", "")
        original = data.get("original", "")
        set_state("awaiting_confirm", {"events": events, "original": original, "context": context})
        _show_confirm(events)

def handle_planner_confirm() -> bool:
    """Підтверджено — записуємо в Calendar."""
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

    # Відмічаємо що сьогодні записали
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    _mark_recorded(now.strftime("%Y-%m-%d"))

    clear_state()

    result = "\n".join(lines)
    reminder_note = "\n\n🔔 Нагадування встановлено." if ok_count > 0 else ""

    send(f"📅 <b>Записано в календар:</b>\n\n{result}{reminder_note}")
    return True

def handle_planner_cancel():
    clear_state()
    send("❌ Скасовано. Нічого не записано.")

def handle_planner_edit():
    """Просимо переписати."""
    state = _load_state()
    events = state.get("data", {}).get("events", [])
    base_date_str = events[0].get("date", "") if events else ""
    try:
        base_date = datetime.strptime(base_date_str, "%Y-%m-%d")
        base_date_out = base_date.strftime("%Y-%m-%d")
    except:
        base_date_out = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")

    set_state("awaiting_tomorrow", {"base_date": base_date_out})
    _send_force_reply("✏️ <b>Напиши плани ще раз:</b>")

# ─── ТРИГЕРИ ─────────────────────────────────────────────────────────────────

def check_planner_triggers():
    """Щохвилинно перевіряє чи час надіслати нагадування."""
    try:
        now = datetime.now(timezone.utc) + timedelta(hours=2)
        h, m = now.hour, now.minute

        if m > 4:  # вікно 5 хвилин
            return

        today_str = now.strftime("%Y-%m-%d")
        weekday   = now.weekday()  # 0=пн, 6=нд

        # ── Неділя о 10:00 — плани на тиждень ─────────────────────────────
        if weekday == 6 and h == 10:
            key = f"{today_str}_week"
            if not _was_sent(key):
                _mark_sent(key)
                ask_week_plans()
                return

        # ── Щодня о 10:00 (крім неділі) — плани на сьогодні ──────────────
        if h == 10 and weekday != 6:
            key = f"{today_str}_morning"
            if not _was_sent(key):
                _mark_sent(key)
                ask_today_plans()
                return

        # ── О 14:00 — нагадування про нотатки (якщо нічого не записано) ───
        if h == 14:
            key = f"{today_str}_midday"
            if not _was_sent(key):
                _mark_sent(key)
                ask_midday_reminder()
                return

        # ── О 19:30 — плани на завтра (або тиждень у неділю) ─────────────
        if h == 19 and m >= 30:
            # Вікно 19:30–19:34
            if m > 34:
                return
            if weekday == 6:
                # В неділю вже питали про тиждень вранці — ввечері плани на завтра
                key = f"{today_str}_evening"
                if not _was_sent(key):
                    _mark_sent(key)
                    ask_tomorrow_plans()
            else:
                key = f"{today_str}_evening"
                if not _was_sent(key):
                    _mark_sent(key)
                    ask_tomorrow_plans()

    except Exception as e:
        print(f"check_planner_triggers error: {e}")
