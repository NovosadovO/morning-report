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
  - Схуднення: зараз ~83-84 кг, ціль 75 кг
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

# Кеш останнього успішного результату get_shift_from_calendar
# Якщо Google Calendar впав — повертаємо останній відомий результат
_SHIFT_CACHE: dict = {}
_SHIFT_CACHE_TTL = 600  # 10 хвилин

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
    import time as _time
    now_ts = _time.time()
    cache_hour = _now_local().strftime("%Y-%m-%d-%H")

    # Перевіряємо свіжий кеш (10 хвилин)
    if cache_hour in _SHIFT_CACHE:
        ts, cached_result = _SHIFT_CACHE[cache_hour]
        if now_ts - ts < _SHIFT_CACHE_TTL:
            return cached_result

    result = {"today": "free", "tomorrow": "free",
              "today_start": None, "today_end": None,
              "tomorrow_start": None}

    token = _get_token()
    if not token:
        # Повертаємо останній кешований результат якщо є
        if _SHIFT_CACHE:
            last = max(_SHIFT_CACHE.values(), key=lambda x: x[0])
            print("get_shift_from_calendar: no token, returning cached result")
            return last[1]
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

    # Зберігаємо в кеш перед поверненням
    import time as _time2
    _SHIFT_CACHE[cache_hour] = (_time2.time(), result)
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
    "working_night": "на нічній зміні (18:00–06:00)",
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
        return f"вага {w} кг (ціль 75 кг, залишилось -{diff} кг)"
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


def _get_recent_emails_context() -> str:
    """Повертає короткий текст з останніх непрочитаних листів для AI."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from monitor import get_emails
        emails = get_emails()
        if not emails:
            return "непрочитані листи: немає"
        items = []
        for e in emails[:5]:
            subj = e.get("subject", "")[:60]
            sender = e.get("sender_name", e.get("sender", ""))[:30]
            items.append(f"· {sender}: {subj}")
        return "непрочитані листи:\n" + "\n".join(items)
    except Exception as _e:
        return f"пошта: недоступна"


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

    # Email контекст — завжди включаємо в систем промпт
    try:
        ctx["email"] = _get_recent_emails_context()
    except Exception:
        ctx["email"] = "пошта: недоступна"

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
        "night": "нічна (18:00–06:00)",
        "free":  "вихідний / вільний",
    }

    weekday_ua = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"][now.weekday()]

    lines = [
        "Ти персональний AI-асистент Олега Новосадова. Твоя роль — одночасно:",
        "• Особистий помічник (нагадування, Calendar, пошта, плани)",
        "• Тренер (фітнес, біг, схуднення до 75 кг)",
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

    if ctx.get("email"):
        lines += ["", "══ НЕПРОЧИТАНА ПОШТА ══", ctx["email"]]

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
        "• Якщо питає про їжу/вагу — враховуй ціль 75 кг і голодування 16:8.",
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
    Стійкий до markdown-обгортки ```json``` і вкладених фігурних дужок.
    Повертає dict або None.
    """
    import re
    if not text or "create_event" not in text:
        return None
    candidates = []
    # 1) JSON у markdown-блоці
    for m in re.finditer(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL):
        candidates.append(m.group(1))
    # 2) Будь-який збалансований {...} що містить "create_event"
    for m in re.finditer(r'\{(?:[^{}]|\{[^{}]*\})*\}', text, re.DOTALL):
        if "create_event" in m.group():
            candidates.append(m.group())
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and obj.get("action") == "create_event":
                return obj
        except Exception:
            continue
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


_GEM_MODELS_CHAT = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]


def _gemini_generate(api_key, contents, max_tokens=900, temperature=0.7):
    """Надійний виклик Gemini: thinkingBudget=0 (щоб не з'їдало токени на reasoning),
    безпечний парсинг parts, авто model-fallback при 429/порожній відповіді, retry.
    Повертає (text, error) — error=None при успіху."""
    import time as _t
    body = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    payload = json.dumps(body).encode()
    last_err = "невідома помилка"
    for model in _GEM_MODELS_CHAT:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=40) as r:
                    resp = json.loads(r.read())
                cands = resp.get("candidates") or []
                if not cands:
                    last_err = f"{model}: no candidates ({resp.get('promptFeedback', '')})"
                    break  # інша модель не допоможе при блокуванні промпту
                parts = (cands[0].get("content") or {}).get("parts") or []
                texts = [p.get("text", "") for p in parts if p.get("text")]
                text = "".join(texts).strip()
                if text:
                    return text, None
                # Порожня відповідь (часто MAX_TOKENS на reasoning) → пробуємо наступну модель
                fr = cands[0].get("finishReason", "")
                last_err = f"{model}: empty parts (finishReason={fr})"
                break
            except urllib.error.HTTPError as he:
                code = he.code
                try:
                    err_body = he.read().decode("utf-8", "replace")
                except Exception:
                    err_body = ""
                last_err = f"{model}: HTTP {code} {err_body[:120]}"
                if code == 429:
                    # квота вичерпана → одразу інша модель (інший пул), без довгого чекання
                    break
                if code in (500, 503):
                    _t.sleep(4 * (attempt + 1))
                    continue
                break  # 400/403 тощо — інша модель не врятує цей виклик
            except Exception as e:
                last_err = f"{model}: {e}"
                _t.sleep(3)
                continue
    return "", last_err


def ask_ai(user_message: str, include_calendar: bool = True) -> str:
    """
    Відправляє повідомлення в Gemini з повним контекстом (Calendar завжди).
    Якщо Gemini хоче створити подію — створює її автоматично.
    Повертає текст відповіді. Ніколи не повертає порожній рядок.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "⚠️ Gemini API key не налаштований."

    try:
        ctx = get_context(include_crypto=True, include_calendar=True)
        system_prompt = get_system_prompt(ctx)
    except Exception as e:
        print(f"ask_ai get_context error: {e}")
        ctx = {"now": _now_local()}
        system_prompt = "Ти персональний AI-асистент Олега. Мова: українська. Коротко і по суті."

    # Якщо запит про тиждень — додаємо розширений Calendar контекст
    week_keywords = ["тиждень", "наступний тиждень", "week", "7 днів", "7 дні", "на тижні"]
    if any(kw in user_message.lower() for kw in week_keywords):
        try:
            token = _get_token()
            if token:
                week_text = _fetch_week_calendar(token)
                system_prompt += f"\n\n══ КАЛЕНДАР НА НАСТУПНІ 7 ДНІВ ══\n{week_text}"
        except Exception:
            pass

    history = _load_history()

    contents = [
        {"role": "user", "parts": [{"text": f"[SYSTEM — прийми до відома, не відповідай]\n{system_prompt}"}]},
        {"role": "model", "parts": [{"text": "Зрозумів. Я твій персональний асистент, знаю час, дату і що в Calendar. Готовий!"}]},
    ]
    for turn in history:
        contents.append({"role": turn["role"], "parts": [{"text": turn["text"]}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    text, err = _gemini_generate(api_key, contents, max_tokens=900, temperature=0.8)

    if not text:
        print(f"ask_ai: empty answer, err={err}")
        # Остання спроба — напряму обробити intent створення події навіть без AI
        local_intent = _local_event_intent(user_message, ctx.get("now", _now_local()))
        if local_intent:
            return _handle_create_event(local_intent, ctx.get("now", _now_local()))
        return ("🤔 Не зміг обробити запит зараз (AI тимчасово недоступний). "
                "Спробуй ще раз за хвилину або напиши конкретніше, напр.: "
                "«додай зустріч 21.06 о 11:00 — STK».")

    answer = text

    # Перевіряємо чи Gemini хоче створити подію
    event_intent = _try_parse_create_event(answer)
    if not event_intent:
        # Якщо AI не повернув JSON, але користувач явно просив нагадування — парсимо самі
        if _looks_like_reminder_request(user_message):
            event_intent = _local_event_intent(user_message, ctx.get("now", _now_local()))

    if event_intent:
        result_text = _handle_create_event(event_intent, ctx.get("now", _now_local()))
        # Прибираємо будь-який JSON-блок з відповіді
        import re
        clean_answer = re.sub(r'```(?:json)?\s*\{.*?\}\s*```', '', answer, flags=re.DOTALL)
        clean_answer = re.sub(r'\{.*?"action".*?"create_event".*?\}', '', clean_answer, flags=re.DOTALL).strip()
        answer = f"{clean_answer}\n\n{result_text}".strip() if clean_answer else result_text

    try:
        history.append({"role": "user",  "text": user_message})
        history.append({"role": "model", "text": answer})
        _save_history(history)
    except Exception as e:
        print(f"ask_ai save history error: {e}")

    return answer


def _looks_like_reminder_request(text: str) -> bool:
    t = (text or "").lower()
    keys = ["нагада", "нагадув", "додай", "додати", "створи подію", "створити подію",
            "запиши", "постав", "заплануй", "запланувати", "in calendar", "в календар",
            "до календар", "reminder", "remind", "нагадай"]
    return any(k in t for k in keys)


_UA_MONTHS = {
    "січ": 1, "лют": 2, "бер": 3, "квіт": 4, "трав": 5, "черв": 6,
    "лип": 7, "серп": 8, "вер": 9, "жовт": 10, "лист": 11, "груд": 12,
}


def _local_event_intent(text: str, now: datetime):
    """Fallback-парсер нагадувань без AI. Розуміє:
    'додай нагадування STK на 21.06 о 11:00', 'нагадай завтра о 14:00 лікар',
    'постав подію 28.06.2026 STK'. Повертає intent dict або None."""
    import re
    t = (text or "").strip()
    tl = t.lower()
    if not _looks_like_reminder_request(t):
        return None

    work = t  # робоча копія, з якої поступово вирізаємо розпізнане
    date_obj = None
    time_str = None

    # ── 1. Час (ПЕРШИМ — щоб не сплутати з датою) ──
    # 1a. HH:MM з обов'язковим маркером "о/об/at" АБО двокрапкою
    mt = re.search(r'(?:\b(?:о|об|at)\s*)(\d{1,2})[:.](\d{2})\b', tl)
    if not mt:
        mt = re.search(r'\b(\d{1,2}):(\d{2})\b', tl)  # лише з двокрапкою (крапка = дата)
    if mt:
        hh, mm = int(mt.group(1)), int(mt.group(2))
        if 0 <= hh < 24 and 0 <= mm < 60:
            time_str = f"{hh:02d}:{mm:02d}"
            work = work[:mt.start()] + " " + work[mt.end():]
            tl = work.lower()
    # 1b. "о 11" / "об 9" (година без хвилин)
    if time_str is None:
        mt2 = re.search(r'\b(?:о|об)\s+(\d{1,2})\b(?!\s*[:.]\d)', tl)
        if mt2:
            hh = int(mt2.group(1))
            if 0 <= hh < 24:
                time_str = f"{hh:02d}:00"
                work = work[:mt2.start()] + " " + work[mt2.end():]
                tl = work.lower()

    # ── 2. Дата ──
    # 2a. 21.06 / 21.06.2026 / 21/06
    m = re.search(r'\b(\d{1,2})[.\/](\d{1,2})(?:[.\/](\d{2,4}))?\b', work)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = m.group(3)
        year = now.year
        if y:
            year = int(y) if len(y) == 4 else 2000 + int(y)
        try:
            date_obj = now.replace(year=year, month=mo, day=d).date()
            work = work[:m.start()] + " " + work[m.end():]
            tl = work.lower()
        except Exception:
            date_obj = None
    # 2b. словесні
    if date_obj is None:
        for word, days in [("післязавтра", 2), ("завтра", 1), ("сьогодні", 0)]:
            if word in tl:
                date_obj = (now + timedelta(days=days)).date()
                work = re.sub(word, " ", work, flags=re.IGNORECASE)
                tl = work.lower()
                break
    # 2c. "22 червня"
    if date_obj is None:
        m2 = re.search(r'\b(\d{1,2})\s+([а-яіїєґ]{3,})', tl)
        if m2:
            d = int(m2.group(1))
            mon_word = m2.group(2)
            for pref, mo in _UA_MONTHS.items():
                if mon_word.startswith(pref):
                    try:
                        date_obj = now.replace(month=mo, day=d).date()
                        work = work[:m2.start()] + " " + work[m2.end():]
                        tl = work.lower()
                    except Exception:
                        pass
                    break

    if date_obj is None:
        return None  # без дати не створюємо — нехай AI/користувач уточнить
    if time_str is None:
        time_str = "10:00"

    # ── 3. Назва події (з того, що лишилось) ──
    summary = work
    for w in ["додай нагадування", "додай нагадуван", "нагадування про", "нагадай мені",
              "нагадай", "нагадування", "додай подію", "створи подію", "створити подію",
              "додати подію", "запланувати подію", "додай", "додати", "постав",
              "заплануй", "запланувати", "запиши", "до календаря", "у календар",
              "в календар", "подію", "про"]:
        summary = re.sub(r'\b' + re.escape(w) + r'\b', " ", summary, flags=re.IGNORECASE)
    summary = re.sub(r'\b(сьогодні|завтра|післязавтра|на|о|об|в|у)\b', ' ', summary, flags=re.IGNORECASE)
    summary = re.sub(r'\s+', ' ', summary).strip(" -—:,.")
    if not summary or len(summary) < 2:
        summary = "Нагадування"

    return {
        "action": "create_event",
        "summary": summary,
        "date": date_obj.strftime("%Y-%m-%d"),
        "time": time_str,
        "duration_hours": 1,
        "description": "Створено асистентом за запитом Олега",
    }


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
            wd = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"][start_dt.weekday()]
            date_fmt = start_dt.strftime("%d.%m.%Y")
            return (f"✅ <b>Додано в Google Calendar</b>\n"
                    f"📌 {summary}\n"
                    f"🗓 {date_fmt} ({wd}) о {time_str}")
        else:
            return (f"❌ Не вдалось створити подію «{summary}»: {res['error']}\n"
                    f"Спробуй ще раз або перевір підключення Google Calendar.")
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
