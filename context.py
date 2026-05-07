#!/usr/bin/env python3
"""
context.py — спільний контекст про стан Олега.
Використовується ботом (AI-чат) і monitor.py (розумні нотифікації).

get_context() → dict з усією інформацією про поточний момент.
get_context_text() → готовий текст для Gemini system prompt.
should_notify() → чи доречно зараз писати (не спить, не на роботі в тиху зону).
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
    "early": (6, 18),   # 06:00–18:00
    "night": (18, 6),   # 18:00–06:00
}

# ─── ВИЗНАЧЕННЯ СТАНУ ─────────────────────────────────────────────────────────

def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)  # CEST


def get_shift_from_calendar():
    """
    Читає Google Calendar і повертає shift для СЬОГОДНІ + ЗАВТРА.
    Returns: {"today": "early"|"night"|"free", "tomorrow": ..., 
              "today_start": datetime|None, "today_end": datetime|None}
    """
    result = {"today": "free", "tomorrow": "free",
              "today_start": None, "today_end": None,
              "tomorrow_start": None}

    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return result

    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from monitor import _get_google_token

        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id = "novosadovoleg%40gmail.com"

        now_utc = datetime.now(timezone.utc)

        for offset, key in [(0, "today"), (1, "tomorrow")]:
            # Шукаємо події що ПОЧИНАЮТЬСЯ в цей день (00:00–23:59 за UTC+2)
            # Для нічної зміни: старт 18:00 цього дня — це правильно
            # Не захоплюємо наступний день щоб нічна зміна не дублювалась
            day_local_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=offset) - timedelta(hours=2)
            day_local_end   = day_local_start + timedelta(hours=24)  # рівно 24г, не 28

            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
                f"?timeMin={urllib.parse.quote(day_local_start.isoformat())}"
                f"&timeMax={urllib.parse.quote(day_local_end.isoformat())}"
                f"&singleEvents=true&orderBy=startTime&maxResults=20"
            )
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                events = json.loads(r.read()).get("items", [])

            for ev in events:
                s = ev.get("summary", "").lower()
                start_str = ev["start"].get("dateTime") or ev["start"].get("date")
                try:
                    dt_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    dt_local = dt_start + timedelta(hours=2)
                except Exception:
                    dt_local = None

                # Перевіряємо що подія ПОЧИНАЄТЬСЯ саме в цей день (за локальним часом)
                if dt_local:
                    day_local_date = (_now_local() + timedelta(days=offset)).date()
                    if dt_local.date() != day_local_date:
                        continue  # подія починається в інший день — пропускаємо

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
                        # Нічна закінчується наступного дня о 06:00 — але для
                        # відображення показуємо кінець як 06:00 наступного дня
                        result["today_end"] = dt_local + timedelta(hours=12) if dt_local else None
                    else:
                        result["tomorrow_start"] = dt_local
                    break

    except Exception as e:
        print(f"get_shift_from_calendar error: {e}")

    return result


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
    start = shift_info.get("today_start")

    if today == "early":
        # Рання: 06:00–18:00
        if 6 <= h < 18:
            return "working_early"
        elif 4 <= h < 6:
            return "pre_shift"
        elif 18 <= h < 23:
            return "post_shift"
        else:
            return "sleeping"

    elif today == "night":
        # Нічна: 18:00–06:00
        if h >= 18 or h < 6:
            return "working_night"
        elif 16 <= h < 18:
            return "pre_shift"
        elif 6 <= h < 10:
            return "post_shift"
        else:
            return "home"

    else:
        # Вільний день
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
    """
    True якщо можна писати нотифікації.
    Не пишемо якщо: спить, або на зміні (тільки важливе).
    """
    if status is None:
        status = get_status()
    return status not in ("sleeping",)


def should_notify_low_priority(status=None):
    """
    True тільки якщо вдома і не зайнятий.
    Для некритичних порад і нагадувань.
    """
    if status is None:
        status = get_status()
    return status in ("home", "post_shift")


# ─── HEALTH / HABITS CONTEXT ──────────────────────────────────────────────────

def _get_health_context():
    try:
        sys_path = os.path.dirname(__file__)
        import sys
        sys.path.insert(0, sys_path)
        from storage import load_health
        health = load_health()
        if not health:
            return "health дані відсутні"
        last_day = sorted(health.keys())[-1]
        h = health[last_day]
        parts = [f"дата: {last_day}"]
        if h.get("steps"):     parts.append(f"кроки: {h['steps']}")
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
        goal = 78.0
        diff = round(w - goal, 1)
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
        from meds import load_meds, now_local as meds_now
        from habits import today_key
        db = load_meds()
        today = today_key()
        taken = db.get(today)
        if taken is True:   return "ліки сьогодні прийнято ✅"
        if taken is False:  return "ліки сьогодні НЕ прийнято ❌"
        return "ліки сьогодні: не відмічено"
    except Exception:
        return "ліки: невідомо"


def _get_crypto_context():
    try:
        ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&price_change_percentage=24h"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read())
        parts = []
        for c in raw:
            sym = c["symbol"].upper()
            price = c["current_price"]
            ch24 = c.get("price_change_percentage_24h") or 0
            sign = "+" if ch24 > 0 else ""
            parts.append(f"{sym} ${price:,.0f} ({sign}{ch24:.1f}%)")
        return ", ".join(parts)
    except Exception:
        return "крипто ціни недоступні"


def _get_calendar_today():
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from monitor import get_calendar
        return get_calendar()
    except Exception:
        return "календар недоступний"


# ─── ГОЛОВНА ФУНКЦІЯ ──────────────────────────────────────────────────────────

def get_context(include_calendar=False, include_crypto=False):
    """Повертає dict з усім контекстом про Олега зараз."""
    now = _now_local()
    shift_info = get_shift_from_calendar()
    status = get_status(shift_info)

    ctx = {
        "now":           now,
        "time_str":      now.strftime("%H:%M"),
        "date_str":      now.strftime("%d.%m.%Y"),
        "weekday":       ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][now.weekday()],
        "shift_today":   shift_info["today"],
        "shift_tomorrow": shift_info["tomorrow"],
        "status":        status,
        "status_label":  STATUS_LABELS.get(status, status),
        "health":        _get_health_context(),
        "weight":        _get_weight_context(),
        "habits":        _get_habits_context(),
        "meds":          _get_meds_context(),
    }

    if include_crypto:
        ctx["crypto"] = _get_crypto_context()
    if include_calendar:
        ctx["calendar"] = _get_calendar_today()

    return ctx


def get_system_prompt(ctx=None):
    """
    Системний промпт для Gemini — хто такий бот і що знає про Олега зараз.
    """
    if ctx is None:
        ctx = get_context(include_crypto=True)

    now = ctx["now"]
    shift_labels = {"early": "рання (06:00–18:00)", "night": "нічна (18:00–06:00)", "free": "вихідний"}

    lines = [
        "Ти персональний AI-асистент Олега Новосадова. Твоя роль — одночасно:",
        "• Особистий тренер (фітнес, біг, схуднення)",
        "• Дієтолог (ціль 78 кг, зараз ~83–84 кг, інтервальне голодування 16:8)",
        "• Фінансовий радник (крипто BTC/ETH/AVAX/ONDO, ETF, InterFin)",
        "• Особистий помічник (нагадування, календар, пошта)",
        "• Друг і мотиватор",
        "",
        "Стиль спілкування: коротко і по суті, як хороший друг. Без зайвого офіціозу.",
        "Мова: завжди українська.",
        f"Поточний час: {ctx['time_str']}, {ctx['weekday']} {ctx['date_str']}",
        f"Статус Олега зараз: {ctx['status_label']}",
        f"Зміна сьогодні: {shift_labels.get(ctx['shift_today'], ctx['shift_today'])}",
        f"Зміна завтра: {shift_labels.get(ctx['shift_tomorrow'], ctx['shift_tomorrow'])}",
        "",
        "Що відомо про Олега зараз:",
        f"• {ctx['weight']}",
        f"• Здоров'я (останні дані): {ctx['health']}",
        f"• Звички сьогодні: {ctx['habits']}",
        f"• {ctx['meds']}",
    ]

    if ctx.get("crypto"):
        lines.append(f"• Крипто зараз: {ctx['crypto']}")

    lines += [
        "",
        "Профіль:",
        PROFILE.strip(),
        "",
        "Важливо: якщо Олег спить або на зміні — будь лаконічним, не грузи зайвим.",
        "Якщо питає про крипто — давай конкретику з цінами.",
        "Якщо питає про їжу/вагу — враховуй ціль 78 кг і голодування 16:8.",
        "Якщо питає про біг — знаєш що він бігає, мотивуй конкретно.",
        "Відповідай в межах 3–5 речень якщо питання загальне. Більше тільки якщо просить деталі.",
    ]

    return "\n".join(lines)


# ─── GEMINI CALL ──────────────────────────────────────────────────────────────

_CHAT_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "data", "chat_history.json")
MAX_HISTORY = 10  # останні N пар повідомлень


def _load_history():
    try:
        with open(_CHAT_HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(history):
    os.makedirs(os.path.dirname(_CHAT_HISTORY_FILE), exist_ok=True)
    try:
        with open(_CHAT_HISTORY_FILE, "w") as f:
            json.dump(history[-MAX_HISTORY*2:], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def ask_ai(user_message, include_calendar=False):
    """
    Відправляє повідомлення в Gemini з контекстом + пам'яттю розмови.
    Повертає текст відповіді.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "⚠️ Gemini API key не налаштований."

    ctx = get_context(include_crypto=True, include_calendar=include_calendar)
    system_prompt = get_system_prompt(ctx)

    history = _load_history()

    # Будуємо contents: спочатку system через перший user turn
    contents = []

    # System prompt як перший user повідомлення (Gemini не має system role)
    contents.append({
        "role": "user",
        "parts": [{"text": f"[SYSTEM CONTEXT — не відповідай на це, просто прийми до відома]\n{system_prompt}"}]
    })
    contents.append({
        "role": "model",
        "parts": [{"text": "Зрозумів. Я твій персональний асистент, тренер і радник. Готовий допомагати!"}]
    })

    # Додаємо історію розмови
    for turn in history:
        contents.append({"role": turn["role"], "parts": [{"text": turn["text"]}]})

    # Поточне питання
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = json.dumps({
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 600,
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
        with urllib.request.urlopen(req, timeout=25) as r:
            resp = json.loads(r.read())
        answer = resp["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Зберігаємо в історію
        history.append({"role": "user",  "text": user_message})
        history.append({"role": "model", "text": answer})
        _save_history(history)

        return answer

    except Exception as e:
        return f"⚠️ AI помилка: {e}"


def clear_history():
    """Очищає пам'ять розмови."""
    _save_history([])
