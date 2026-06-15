#!/usr/bin/env python3
"""
context.py — спільний контекст про стан Олега.
Використовується ботом (AI-чат) і monitor.py (розумні нотифікації).

get_context()        → dict з усією інформацією про поточний момент
get_system_prompt()  → готовий system prompt для Gemini (з Calendar завжди)
ask_ai()             → чат з Gemini (підтримує create_event)
create_calendar_event() → створити подію в Google Calendar
should_notify()      → чи доречно зараз писати
"""

import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ─── ПРОФІЛЬ ──────────────────────────────────────────────────────────────────

PROFILE = """
Ім'я: Олег Новосадов, Кошіце, Словаччина (UTC+1, влітку UTC+2 / CEST)
Вік: ~30 років
Робота: Minebea Mitsumi, змінний графік
  - Рання зміна: 06:00–18:00
  - Нічна зміна: 18:00–06:00 наступного дня
Цілі:
  - Схуднення: зараз ~83-84 кг, ціль 78 кг
  - Фінансова незалежність через інвестиції (InterFin / Maroš Sivák)
  - Крипто: BTC, ETH, AVAX, ONDO
  - Регулярний біг
  - Вивчення інвестицій (самоосвіта)
Звички: холодний душ, ліки (Armolopid Plus), вода, біг, навчання
Ліки: Armolopid Plus (курс 27.04–27.07.2026)
"""

SHIFT_HOURS = {
    "early": (6, 18),
    "night": (18, 6),
}

_CAL_ID = "novosadovoleg%40gmail.com"

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)  # CEST

def _get_token():
    """Отримує Google OAuth token."""
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return None
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from monitor import _get_google_token
        creds_data = json.loads(creds_json)
        return _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar")
    except Exception as e:
        print(f"_get_token error: {e}")
        return None

def _gh(url, headers=None, method="GET", data=None):
    """Простий HTTP запит."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

# ─── CALENDAR: ЧИТАННЯ ────────────────────────────────────────────────────────

# Кеш подій: {date_str: (timestamp, events_list)}
_CAL_CACHE: dict = {}
_CAL_CACHE_TTL = 300  # 5 хвилин

def _fetch_events_for_day(token: str, day_offset: int = 0) -> list:
    """Завантажує події Google Calendar для дня (offset=0 сьогодні, 1 завтра)."""
    now_utc = datetime.now(timezone.utc)
    cache_key = (_now_local() + timedelta(days=day_offset)).strftime("%Y-%m-%d")

    # Перевіряємо кеш
    cached = _CAL_CACHE.get(cache_key)
    if cached:
        ts, events = cached
        if (datetime.now(timezone.utc).timestamp() - ts) < _CAL_CACHE_TTL:
            return events

    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) \
                + timedelta(days=day_offset) - timedelta(hours=2)
    day_end = day_start + timedelta(hours=24)

    url = (
        f"https://www.googleapis.com/calendar/v3/calendars/{_CAL_ID}/events"
        f"?timeMin={urllib.parse.quote(day_start.isoformat())}"
        f"&timeMax={urllib.parse.quote(day_end.isoformat())}"
        f"&singleEvents=true&orderBy=startTime&maxResults=30"
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        result = _gh(url, headers=headers)
        events = result.get("items", [])
        _CAL_CACHE[cache_key] = (datetime.now(timezone.utc).timestamp(), events)
        return events
    except Exception as e:
        print(f"_fetch_events_for_day error: {e}")
        return []

def _format_events_plain(events: list) -> str:
    """Форматує список подій в текст для system prompt."""
    if not events:
        return "нічого не заплановано"
    lines = []
    for ev in events:
        summary = ev.get("summary", "(без назви)")
        start_str = ev["start"].get("dateTime") or ev["start"].get("date")
        try:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            dt_local = dt + timedelta(hours=2)
            t = dt_local.strftime("%H:%M")
        except Exception:
            t = start_str[:10] if start_str else "?"
        desc = ev.get("description", "")
        loc = ev.get("location", "")
        extra = ""
        if loc:
            extra += f" | 📍{loc}"
        if desc:
            extra += f" | {desc[:60]}"
        lines.append(f"{t} — {summary}{extra}")
    return "\n".join(lines)

def get_calendar_events(days: int = 2) -> dict:
    """
    Повертає dict з подіями:
    {"today": [(time, summary, ev), ...], "tomorrow": [...], "today_text": "...", "tomorrow_text": "..."}
    """
    result = {
        "today": [], "tomorrow": [],
        "today_text": "нічого не заплановано",
        "tomorrow_text": "нічого не заплановано",
    }
    token = _get_token()
    if not token:
        return result

    try:
        today_events = _fetch_events_for_day(token, 0)
        tomorrow_events = _fetch_events_for_day(token, 1)
        result["today"] = today_events
        result["tomorrow"] = tomorrow_events
        result["today_text"] = _format_events_plain(today_events)
        result["tomorrow_text"] = _format_events_plain(tomorrow_events)
    except Exception as e:
        print(f"get_calendar_events error: {e}")

    return result

# ─── CALENDAR: ВИЗНАЧЕННЯ ЗМІНИ ───────────────────────────────────────────────

def get_shift_from_calendar():
    """
    Читає Google Calendar і повертає shift для СЬОГОДНІ + ЗАВТРА.
    ВАЖЛИВО: якщо зараз 00:00–05:59 і вчора була нічна зміна — вона ще триває!
    Returns: {"today": "early"|"night"|"free", "tomorrow": ...,
              "today_start": datetime|None, "today_end": datetime|None}
    """
    result = {"today": "free", "tomorrow": "free",
              "today_start": None, "today_end": None,
              "tomorrow_start": None}

    token = _get_token()
    if not token:
        return result

    try:
        now_local = _now_local()
        h = now_local.hour

        # Якщо зараз 00:00–14:59 — можливо нічна зміна почалась ВЧОРА
        # Нічна зміна: ~17:30 → ~06:00. Після неї людина відпочиває до ~14:00.
        # Тому до 15:00 перевіряємо вчорашній день.
        if h < 15:
            yesterday_events = _fetch_events_for_day(token, -1)
            for ev in yesterday_events:
                s = ev.get("summary", "").lower()
                if any(x in s for x in ["нічна", "night"]):
                    start_str = ev["start"].get("dateTime") or ev["start"].get("date")
                    try:
                        dt_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        dt_local = dt_start + timedelta(hours=2)
                    except Exception:
                        dt_local = None
                    if h < 6:
                        # Ще на роботі
                        result["today"] = "night"
                    else:
                        # Після нічної — відпочиває вдома
                        result["today"] = "after_night"
                    result["today_start"] = dt_local
                    result["today_end"] = dt_local + timedelta(hours=13) if dt_local else None
                    break

        for offset, key in [(0, "today"), (1, "tomorrow")]:
            # If today's status was already determined from yesterday's night shift
            # (after_night = resting at home after night shift), don't overwrite with
            # tonight's upcoming night shift event.
            if key == "today" and result["today"] == "after_night":
                # Still fetch today's events to get tomorrow data, but skip today key
                pass
            else:
                events = _fetch_events_for_day(token, offset)
                for ev in events:
                    s = ev.get("summary", "").lower()
                    start_str = ev["start"].get("dateTime") or ev["start"].get("date")
                    try:
                        dt_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        dt_local = dt_start + timedelta(hours=2)
                    except Exception:
                        dt_local = None

                    if dt_local:
                        day_local_date = (_now_local() + timedelta(days=offset)).date()
                        if dt_local.date() != day_local_date:
                            continue

                    if any(x in s for x in ["рання", "early"]):
                        result[key] = "early"
                        if key == "today":
                            result["today_start"] = dt_local
                            result["today_end"] = dt_local + timedelta(hours=12) if dt_local else None
                        else:
                            result["tomorrow_start"] = dt_local
                        break
                    elif any(x in s for x in ["нічна", "night"]):
                        result[key] = "night"
                        if key == "today":
                            result["today_start"] = dt_local
                            result["today_end"] = dt_local + timedelta(hours=12) if dt_local else None
                        else:
                            result["tomorrow_start"] = dt_local
                        break

    except Exception as e:
        print(f"get_shift_from_calendar error: {e}")

    return result

# ─── CALENDAR: СТВОРЕННЯ ПОДІЙ ────────────────────────────────────────────────

def create_calendar_event(summary: str, start_dt: datetime, end_dt: datetime = None,
                           description: str = "", location: str = "") -> dict:
    """
    Створює подію в Google Calendar.
    start_dt / end_dt — об'єкти datetime (локальний час UTC+2).
    Якщо end_dt не вказано — подія тривалістю 1 годину.
    Повертає {"ok": True, "event_id": "...", "link": "..."} або {"ok": False, "error": "..."}
    """
    token = _get_token()
    if not token:
        return {"ok": False, "error": "Google Calendar не підключений"}

    if end_dt is None:
        end_dt = start_dt + timedelta(hours=1)

    # Конвертуємо в UTC для API (UTC+2 → UTC)
    tz_offset = "+02:00"
    start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%S") + tz_offset
    end_iso   = end_dt.strftime("%Y-%m-%dT%H:%M:%S") + tz_offset

    body = {
        "summary": summary,
        "start": {"dateTime": start_iso, "timeZone": "Europe/Bratislava"},
        "end":   {"dateTime": end_iso,   "timeZone": "Europe/Bratislava"},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location

    url = f"https://www.googleapis.com/calendar/v3/calendars/{_CAL_ID}/events"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        data = json.dumps(body).encode()
        result = _gh(url, headers=headers, method="POST", data=data)
        event_id = result.get("id", "")
        link = result.get("htmlLink", "")
        # Скидаємо кеш щоб наступний запит побачив нову подію
        _CAL_CACHE.clear()
        return {"ok": True, "event_id": event_id, "link": link, "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def _parse_create_event_from_text(text: str):
    """
    Парсить запит типу "додай зустріч завтра о 15:00 — Лікар"
    Повертає (summary, start_dt, end_dt) або None якщо не вдалось розпарсити.
    Делегуємо Gemini — бот сам зрозуміє і поверне JSON.
    """
    return None  # обробляється через AI intent

# ─── СТАТУС ОЛЕГА ─────────────────────────────────────────────────────────────

def get_status(shift_info=None):
    """
    Повертає поточний статус Олега:
    'working_early' | 'working_night' | 'sleeping' | 'home' | 'pre_shift' | 'post_shift'
    """
    now = _now_local()
    h = now.hour

    if shift_info is None:
        shift_info = get_shift_from_calendar()

    today = shift_info.get("today", "free")

    if today == "early":
        if 6 <= h < 18:
            return "working_early"
        elif 4 <= h < 6:
            return "pre_shift"
        elif 18 <= h < 23:
            return "post_shift"
        else:
            return "sleeping"

    elif today == "night":
        if h >= 18 or h < 6:
            return "working_night"
        elif 16 <= h < 18:
            return "pre_shift"
        elif 6 <= h < 10:
            return "post_shift"
        else:
            return "home"

    else:
        if 0 <= h < 7:
            return "sleeping"
        elif 22 <= h <= 23:
            return "sleeping"
        else:
            return "home"

STATUS_LABELS = {
    "working_early": "на ранній зміні (06:00–18:00)",
    "working_night": "на нічній зміні (17:00–05:00)",
    "sleeping":      "спить / нічний відпочинок",
    "home":          "вдома, вільний час",
    "pre_shift":     "готується до зміни",
    "post_shift":    "після зміни, відпочиває",
}

def should_notify(status=None):
    if status is None:
        status = get_status()
    return status not in ("sleeping",)

def should_notify_low_priority(status=None):
    if status is None:
        status = get_status()
    return status in ("home", "post_shift")

# ─── HEALTH / HABITS / MEDS / КРИПТО КОНТЕКСТ ────────────────────────────────

def _get_health_context():
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import load_health
        health = load_health()
        if not health:
            return "health дані відсутні"
        last_day = sorted(health.keys())[-1]
        h = health[last_day]
        parts = [f"дата: {last_day}"]
        if h.get("steps"):       parts.append(f"кроки: {h['steps']}")
        if h.get("sleep_hours"): parts.append(f"сон: {h['sleep_hours']}г")
        if h.get("heart_rate"):  parts.append(f"ЧСС: {h['heart_rate']} bpm")
        if h.get("calories"):    parts.append(f"калорії: {h['calories']}")
        if h.get("health_score"): parts.append(f"health score: {h['health_score']}/100")
        return ", ".join(parts)
    except Exception:
        return "health дані недоступні"

def _get_weight_context():
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from weight import load_weight_data
        data = load_weight_data()
        if not data:
            return "вага невідома"
        last = sorted(data.keys())[-1]
        w = data[last]["weight"]
        diff = round(w - 78.0, 1)
        return f"вага {w} кг (ціль 78 кг, залишилось -{diff} кг)"
    except Exception:
        return "вага невідома"

def _get_habits_context():
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from habits import load_data, HABITS
        db = load_data()
        today = _now_local().strftime("%Y-%m-%d")
        day = db.get(today, {})
        if not day:
            return "звички сьогодні ще не відмічені"
        done   = [h["name"] for h in HABITS if day.get(h["id"]) is True]
        missed = [h["name"] for h in HABITS if day.get(h["id"]) is False]
        parts = []
        if done:   parts.append(f"виконано: {', '.join(done)}")
        if missed: parts.append(f"пропущено: {', '.join(missed)}")
        return "; ".join(parts) if parts else "звички не відмічені"
    except Exception:
        return "звички недоступні"

def _get_meds_context():
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from meds import load_meds
        from habits import today_key
        db = load_meds()
        today = today_key()
        taken = db.get(today)
        if taken is True:  return "ліки сьогодні прийнято ✅"
        if taken is False: return "ліки сьогодні НЕ прийнято ❌"
        return "ліки сьогодні: не відмічено"
    except Exception:
        return "ліки: невідомо"

def _get_strava_context():
    """Повертає рядок з останнім тренуванням і тижневою статистикою."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from strava import get_last_activity, get_week_stats
        last = get_last_activity()
        week = get_week_stats()
        parts = []
        if last:
            parts.append(
                f"Останнє тренування {last.get('when','')}: "
                f"{last.get('distance_km',0)} км за {last.get('duration_min',0)} хв "
                f"({last.get('pace','')})"
            )
            if last.get("hr"):
                parts[-1] += f", ЧСС {last['hr']:.0f}"
        else:
            parts.append("Останнє тренування: немає даних")
        if week:
            parts.append(
                f"Тиждень: {week.get('runs',0)} пробіжок, "
                f"{week.get('km',0)} км, {week.get('duration_min',0)} хв"
            )
        return " | ".join(parts)
    except Exception as e:
        return f"Strava: недоступно ({e})"


def _get_crypto_context():
    try:
        ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
        url = (f"https://api.coingecko.com/api/v3/coins/markets"
               f"?vs_currency=usd&ids={ids}&price_change_percentage=24h")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read())
        parts = []
        for c in raw:
            sym   = c["symbol"].upper()
            price = c["current_price"]
            ch24  = c.get("price_change_percentage_24h") or 0
            sign  = "+" if ch24 > 0 else ""
            parts.append(f"{sym} ${price:,.0f} ({sign}{ch24:.1f}%)")
        return ", ".join(parts)
    except Exception:
        return "крипто ціни недоступні"

# ─── ГОЛОВНА ФУНКЦІЯ КОНТЕКСТУ ────────────────────────────────────────────────

def get_context(include_calendar=True, include_crypto=False):
    """Повертає dict з усім контекстом про Олега зараз."""
    now = _now_local()
    shift_info = get_shift_from_calendar()
    status = get_status(shift_info)
    cal = get_calendar_events()

    ctx = {
        "now":            now,
        "time_str":       now.strftime("%H:%M"),
        "date_str":       now.strftime("%d.%m.%Y"),
        "weekday":        ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][now.weekday()],
        "shift_today":    shift_info["today"],
        "shift_tomorrow": shift_info["tomorrow"],
        "status":         status,
        "status_label":   STATUS_LABELS.get(status, status),
        "health":         _get_health_context(),
        "weight":         _get_weight_context(),
        "habits":         _get_habits_context(),
        "meds":           _get_meds_context(),
        "strava":         _get_strava_context(),
        "calendar_today":    cal["today_text"],
        "calendar_tomorrow": cal["tomorrow_text"],
    }

    if include_crypto:
        ctx["crypto"] = _get_crypto_context()

    return ctx

# ─── SYSTEM PROMPT ────────────────────────────────────────────────────────────

def get_system_prompt(ctx=None):
    """
    Системний промпт для Gemini — з повним контекстом часу, дня, Calendar.
    """
    if ctx is None:
        ctx = get_context(include_crypto=True)

    now = ctx["now"]
    shift_labels = {
        "early": "рання (06:00–18:00)",
        "night": "нічна (17:00–05:00)",
        "free":  "вихідний / вільний",
    }

    weekday_ua = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"][now.weekday()]

    lines = [
        "Ти персональний AI-асистент Олега Новосадова. Твоя роль — одночасно:",
        "• Особистий помічник (нагадування, Calendar, пошта, плани)",
        "• Тренер (фітнес, біг, схуднення до 78 кг)",
        "• Дієтолог (інтервальне голодування 16:8)",
        "• Фінансовий радник (крипто BTC/ETH/AVAX/ONDO, ETF, InterFin)",
        "• Друг і мотиватор",
        "",
        "Стиль: коротко і по суті, як хороший друг. Мова: завжди українська.",
        "",
        "══ ПОТОЧНИЙ МОМЕНТ ══",
        f"Час: {ctx['time_str']}  |  {weekday_ua}, {ctx['date_str']}",
        f"Статус Олега: {ctx['status_label']}",
        f"Зміна сьогодні: {shift_labels.get(ctx['shift_today'], ctx['shift_today'])}",
        f"Зміна завтра:   {shift_labels.get(ctx['shift_tomorrow'], ctx['shift_tomorrow'])}",
        "",
        "══ КАЛЕНДАР СЬОГОДНІ ══",
        ctx["calendar_today"],
        "",
        "══ КАЛЕНДАР ЗАВТРА ══",
        ctx["calendar_tomorrow"],
        "",
        "══ СТАН ОЛЕГА ══",
        f"Вага: {ctx['weight']}",
        f"Здоров'я: {ctx['health']}",
        f"Звички: {ctx['habits']}",
        f"Ліки: {ctx['meds']}",
        f"Біг/Strava: {ctx.get('strava', 'немає даних')}",
    ]

    if ctx.get("crypto"):
        lines += ["", "══ КРИПТО ══", ctx["crypto"]]

    # user_state — що Олег казав нещодавно
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from proactive import load_user_state
        state = load_user_state()
        if state:
            lines += ["", "══ ЩО ОЛЕГ КАЗАВ НЕЩОДАВНО ══"]
            if state.get("location"):
                lines.append(f"Місце: {state['location']}")
            if state.get("activity"):
                lines.append(f"Активність: {state['activity']}")
            if state.get("mood"):
                lines.append(f"Настрій: {state['mood']}")
            if state.get("last_message_from_oleg"):
                lines.append(f"Останнє: «{state['last_message_from_oleg'][:150]}»")
    except Exception:
        pass

    lines += [
        "",
        "══ ПРОФІЛЬ ══",
        PROFILE.strip(),
        "",
        "══ ЩО ТИ ВМІЄШ ══",
        "Ти можеш СТВОРЮВАТИ події в Google Calendar.",
        "Якщо Олег просить щось додати в Calendar — відповідай JSON:",
        '{"action":"create_event","summary":"...","date":"YYYY-MM-DD","time":"HH:MM","duration_hours":1,"description":"..."}',
        "Наприклад: 'додай лікар завтра о 14:00' → поверни JSON вище + підтвердження.",
        "Якщо не впевнений в даті/часі — перепитай.",
        "",
        "Важливо:",
        "• Завжди знаєш поточний час, день, що в Calendar — враховуй це в кожній відповіді.",
        "• Якщо Олег на зміні — лаконічно, без зайвого.",
        "• Якщо питає про крипто — давай конкретику з цінами.",
        "• Якщо питає про їжу/вагу — враховуй ціль 78 кг і голодування 16:8.",
        "• Відповідай 2–4 речення якщо питання загальне. Більше тільки якщо просить.",
    ]

    return "\n".join(lines)

# ─── AI ЧАТ ───────────────────────────────────────────────────────────────────

MAX_HISTORY = 20  # зберігаємо останні 20 повідомлень (10 пар)


def _load_history():
    """Завантажує історію розмови з GitHub storage (персистентно між рестартами)."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import load
        data = load("chat_history.json", default=[])
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"_load_history error: {e}")
        return []


def _save_history(history):
    """Зберігає останні MAX_HISTORY*2 повідомлень у GitHub storage."""
    try:
        import sys, threading
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import save
        trimmed = history[-(MAX_HISTORY * 2):]
        # Зберігаємо асинхронно щоб не блокувати відповідь
        threading.Thread(target=save, args=("chat_history.json", trimmed), daemon=True).start()
    except Exception as e:
        print(f"_save_history error: {e}")


def _try_parse_create_event(text: str):
    """
    Шукає JSON з action=create_event у відповіді Gemini.
    Повертає dict або None.
    """
    import re
    m = re.search(r'\{[^{}]*"action"\s*:\s*"create_event"[^{}]*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


def _fetch_week_calendar(token: str) -> str:
    """Завантажує події на наступні 7 днів і форматує текстом."""
    lines = []
    for offset in range(1, 8):
        try:
            events = _fetch_events_for_day(token, offset)
            if events:
                day = (_now_local() + timedelta(days=offset))
                day_name = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][day.weekday()]
                date_str = day.strftime("%d.%m")
                lines.append(f"{day_name} {date_str}: {_format_events_plain(events)}")
        except Exception:
            pass
    return "\n".join(lines) if lines else "нічого не заплановано на тижень"


def ask_ai(user_message: str, include_calendar: bool = True) -> str:
    """
    Відправляє повідомлення в Gemini з повним контекстом (Calendar завжди).
    Якщо Gemini хоче створити подію — створює її автоматично.
    Повертає текст відповіді.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "⚠️ Gemini API key не налаштований."

    ctx = get_context(include_crypto=True, include_calendar=True)
    system_prompt = get_system_prompt(ctx)

    # Якщо запит про тиждень — додаємо розширений Calendar контекст
    week_keywords = ["тиждень", "наступний тиждень", "week", "7 днів", "7 дні", "на тижні"]
    extra_calendar = ""
    if any(kw in user_message.lower() for kw in week_keywords):
        try:
            token = _get_token()
            if token:
                week_text = _fetch_week_calendar(token)
                extra_calendar = f"\n\n══ КАЛЕНДАР НА НАСТУПНІ 7 ДНІВ ══\n{week_text}"
        except Exception:
            pass
    system_prompt += extra_calendar

    history = _load_history()

    contents = []
    contents.append({
        "role": "user",
        "parts": [{"text": f"[SYSTEM — прийми до відома, не відповідай]\n{system_prompt}"}]
    })
    contents.append({
        "role": "model",
        "parts": [{"text": "Зрозумів. Я твій персональний асистент, знаю час, дату і що в Calendar. Готовий!"}]
    })

    for turn in history:
        contents.append({"role": turn["role"], "parts": [{"text": turn["text"]}]})

    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = json.dumps({
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 700,
            "temperature": 0.8,
        }
    }).encode()

    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        answer = resp["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Перевіряємо чи Gemini хоче створити подію
        event_intent = _try_parse_create_event(answer)
        if event_intent:
            result_text = _handle_create_event(event_intent, ctx["now"])
            # Прибираємо JSON з відповіді, додаємо результат
            import re
            clean_answer = re.sub(r'\{[^{}]*"action"\s*:\s*"create_event"[^{}]*\}', '', answer, flags=re.DOTALL).strip()
            answer = f"{clean_answer}\n\n{result_text}".strip()

        history.append({"role": "user",  "text": user_message})
        history.append({"role": "model", "text": answer})
        _save_history(history)

        return answer

    except Exception as e:
        return f"⚠️ AI помилка: {e}"


def _handle_create_event(intent: dict, now: datetime) -> str:
    """Виконує create_event з intent dict і повертає підтвердження."""
    try:
        summary = intent.get("summary", "Нова подія")
        date_str = intent.get("date", now.strftime("%Y-%m-%d"))
        time_str = intent.get("time", "10:00")
        duration = float(intent.get("duration_hours", 1))
        description = intent.get("description", "")

        start_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_dt   = start_dt + timedelta(hours=duration)

        res = create_calendar_event(summary, start_dt, end_dt, description)
        if res["ok"]:
            date_fmt = start_dt.strftime("%d.%m.%Y")
            return f"✅ Подію додано в Calendar: <b>{summary}</b> — {date_fmt} о {time_str}"
        else:
            return f"❌ Не вдалось створити подію: {res['error']}"
    except Exception as e:
        return f"❌ Помилка при створенні події: {e}"


def clear_history():
    """Очищає пам'ять розмови (GitHub + local)."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import save
        save("chat_history.json", [])
    except Exception as e:
        print(f"clear_history error: {e}")
