"""
healthai.py — здоров'я під AI-контролем.

Що робить:
1. ЗБЕРІГАЄ все, що Олег надсилає (вечірні дані з годинника/вручну) — і сирий
   текст (health_journal.json), і розібрані числа (через qwsync у qwatch_data.json).
2. АНАЛІТИКА фактами: вага/сон/кроки/пульс/HRV/калорії — середні, дельти,
   тренди, streak, пропущені дні, прогрес до цілі 78 кг.
3. AI-АНАЛІЗ + AI-РЕКОМЕНДАЦІЇ на цих числах (без вигадок).
4. AI-КОУЧ (ранок) — план на день; AI-ТРЕКЕР (вечір) — що зафіксовано і чого бракує.
5. ІНІЦІАТИВА: сам помічає аномалії (вага росте, сон короткий, кроки низькі,
   пульс високий, дані зникли) і пише першим.

Джерела даних (нічого не дублюємо):
    storage.load_health()  — health.json + qwatch_data.json (мердж)
    weight_data.json       — канонічна вага
    health_journal.json    — журнал усього, що надіслав Олег (новий, тут)
"""

import os
import re
from datetime import datetime, timedelta

import ai_kit as K

TAG = "healthai"

JOURNAL_FILE = "health_journal.json"
STATE_FILE = "healthai_state.json"
JOURNAL_KEEP = 800

WEIGHT_GOAL = 78.0          # ціль Олега
SLEEP_MIN_OK = 6.5          # менше — недосип
STEPS_GOAL = 8000
HR_HIGH = 85                # середній пульс спокою вище — сигнал
STALE_HOURS = 36            # дані не приходять довше — питаємо

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


# ─── ЖУРНАЛ: зберігаємо ВСЕ ──────────────────────────────────────────────────

def _now():
    return K.now().replace(tzinfo=None)


def load_journal() -> list:
    d = K.load(JOURNAL_FILE, default={}) or {}
    items = d.get("items") if isinstance(d, dict) else None
    return items if isinstance(items, list) else []


def _save_journal(items):
    K.save(JOURNAL_FILE, {"items": items[-JOURNAL_KEEP:],
                          "updated": _now().isoformat(timespec="seconds")})


def capture(text: str, source: str = "telegram") -> dict:
    """
    Зберігає будь-яке повідомлення Олега з даними здоров'я: сирий текст назавжди
    + розібрані числа в qwatch_data.json (щоб їх бачили всі звіти й аналітика).
    Повертає розібрані поля ({} якщо чисел здоров'я не знайдено).
    """
    raw = (text or "").strip()
    if not raw:
        return {}

    fields = {}
    try:
        import qwsync
        fields = qwsync.normalize(_kv_from_text(raw)) or {}
        if fields:
            qwsync.save(fields, notify=False)
    except Exception as e:
        K.log(TAG, f"parse/save error: {e}")

    items = load_journal()
    items.append({
        "raw": raw[:1200],
        "fields": fields,
        "source": source,
        "ts": _now().isoformat(timespec="seconds"),
        "day": _now().strftime("%Y-%m-%d"),
    })
    _save_journal(items)

    try:
        import selfact
        selfact.journal("note", "збережено дані здоров'я",
                        ", ".join(f"{k}={v}" for k, v in fields.items())[:200] or raw[:120],
                        module=TAG)
    except Exception:
        pass

    K.log(TAG, f"capture: fields={fields} raw={raw[:60]!r}")
    return fields


_KEYS = {
    "вага": "weight", "weight": "weight", "кг": "weight",
    "сон": "sleep", "sleep": "sleep", "спав": "sleep",
    "кроки": "steps", "steps": "steps", "крокiв": "steps", "кроків": "steps",
    "пульс": "hr", "hr": "hr", "серце": "hr",
    "hrv": "hrv", "варіабельність": "hrv",
    "калорії": "calories", "калорий": "calories", "calories": "calories", "ккал": "calories",
    "вода": "water", "water": "water",
    "стрес": "stress", "stress": "stress",
    "тиск": "bp", "sp02": "spo2", "spo2": "spo2", "кисень": "spo2",
    "дистанція": "distance", "км": "distance", "distance": "distance",
}


def _kv_from_text(raw: str) -> dict:
    """
    Витягує пари «слово — число» з вільного тексту:
    "вага 83.4, сон 7г 20хв, кроки 9 120, пульс 68" → dict для qwsync.normalize.
    """
    out = {}
    low = raw.lower().replace("\n", " ")

    # сон у форматі 7г20 / 7h 20min / 7:20
    m = re.search(r"(?:сон|sleep|спав)\D{0,6}(\d{1,2})\s*(?:г|год|h)\D{0,4}(\d{1,2})?", low)
    if m:
        h = int(m.group(1)); mi = int(m.group(2) or 0)
        out["sleep_min"] = h * 60 + mi

    for word, key in _KEYS.items():
        if key == "sleep" and "sleep_min" in out:
            continue
        idx = low.find(word)
        while idx != -1:
            tail = low[idx + len(word): idx + len(word) + 18]
            num = _NUM.search(tail.replace(" ", "") if key == "steps" else tail)
            if num:
                out.setdefault(key, num.group(0))
                break
            idx = low.find(word, idx + 1)
    return out


# ─── АНАЛІТИКА ───────────────────────────────────────────────────────────────

def _series(health: dict, field: str, days: int):
    """[(день, значення)] за останні N днів, за зростанням дати."""
    today = _now().date()
    out = []
    for day, rec in (health or {}).items():
        if not isinstance(rec, dict):
            continue
        v = rec.get(field)
        if v in (None, "", 0):
            continue
        try:
            d = datetime.strptime(day, "%Y-%m-%d").date()
        except Exception:
            continue
        if 0 <= (today - d).days < days:
            try:
                out.append((day, float(v)))
            except Exception:
                pass
    return sorted(out)


def _avg(vals):
    return round(sum(vals) / len(vals), 1) if vals else None


def _trend(pairs):
    """Порівнює першу і другу половину періоду. Повертає (дельта, словами)."""
    if len(pairs) < 4:
        return None, "мало даних"
    vals = [v for _, v in pairs]
    half = len(vals) // 2
    a, b = _avg(vals[:half]), _avg(vals[half:])
    if a is None or b is None:
        return None, "мало даних"
    d = round(b - a, 1)
    if abs(d) < 0.1:
        return d, "стабільно"
    return d, ("зростає" if d > 0 else "падає")


def analytics(days: int = 30) -> dict:
    """Повна картина фактами. Нічого не вигадує: чого немає — None."""
    import storage
    try:
        health = storage.load_health() or {}
    except Exception as e:
        K.log(TAG, f"load_health error: {e}")
        health = {}

    out = {"days": days, "generated": _now().isoformat(timespec="seconds")}

    spec = [("weight_kg", "weight"), ("sleep_hours", "sleep"), ("steps", "steps"),
            ("hr_avg", "hr"), ("hrv", "hrv"), ("calories", "calories")]
    for field, name in spec:
        pairs = _series(health, field, days)
        vals = [v for _, v in pairs]
        d7 = [v for _, v in _series(health, field, 7)]
        delta, word = _trend(pairs)
        out[name] = {
            "n": len(vals),
            "last": vals[-1] if vals else None,
            "last_day": pairs[-1][0] if pairs else None,
            "avg": _avg(vals),
            "avg7": _avg(d7),
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
            "delta": delta,
            "trend": word,
        }

    # прогрес до цілі
    w = out["weight"]["last"]
    out["weight"]["goal"] = WEIGHT_GOAL
    out["weight"]["to_goal"] = round(w - WEIGHT_GOAL, 1) if w else None

    # свіжість даних і streak
    all_days = sorted(d for d in health.keys() if re.match(r"^\d{4}-\d{2}-\d{2}$", str(d)))
    out["last_data_day"] = all_days[-1] if all_days else None
    streak = 0
    day = _now().date()
    while streak < 90 and day.strftime("%Y-%m-%d") in health:
        streak += 1
        day -= timedelta(days=1)
    out["streak"] = streak
    missing = []
    for i in range(1, 8):
        d = (_now().date() - timedelta(days=i)).strftime("%Y-%m-%d")
        if d not in health:
            missing.append(d)
    out["missing_7d"] = missing

    hours = None
    if out["last_data_day"]:
        try:
            last = datetime.strptime(out["last_data_day"], "%Y-%m-%d")
            hours = round((_now() - last).total_seconds() / 3600)
        except Exception:
            pass
    out["stale_hours"] = hours

    # біг зі Strava (якщо доступний) — контекст, не критично
    try:
        import strava
        wk = strava.get_week_stats() or {}
        out["run_week_km"] = wk.get("distance_km") or wk.get("km")
    except Exception:
        out["run_week_km"] = None

    return out


def facts_block(a: dict) -> str:
    """Числа одним компактним блоком — і для AI-промпту, і для звіту."""
    def line(label, m, unit="", extra=""):
        if not m or m.get("last") is None:
            return f"{label}: немає даних"
        s = f"{label}: {m['last']}{unit}"
        if m.get("avg7") is not None:
            s += f" | сер.7д {m['avg7']}{unit}"
        if m.get("avg") is not None:
            s += f" | сер.{a['days']}д {m['avg']}{unit}"
        if m.get("delta") is not None:
            s += f" | тренд {m['trend']} ({m['delta']:+})"
        return s + extra

    w = a.get("weight") or {}
    goal = f" | до цілі {w['to_goal']:+} кг" if w.get("to_goal") is not None else ""
    rows = [
        line("Вага", w, " кг", goal),
        line("Сон", a.get("sleep"), " год"),
        line("Кроки", a.get("steps")),
        line("Пульс", a.get("hr"), " уд/хв"),
        line("HRV", a.get("hrv"), " мс"),
        line("Калорії", a.get("calories"), " ккал"),
    ]
    if a.get("run_week_km"):
        rows.append(f"Біг за тиждень: {a['run_week_km']} км")
    rows.append(f"Днів підряд з даними: {a.get('streak', 0)}")
    if a.get("missing_7d"):
        rows.append("Пропущені дні (7д): " + ", ".join(a["missing_7d"]))
    if a.get("stale_hours") is not None:
        rows.append(f"Останні дані: {a['last_data_day']} ({a['stale_hours']} год тому)")
    return "\n".join(rows)


# ─── AI ──────────────────────────────────────────────────────────────────────

_STYLE = (
    "Ти особистий тренер і лікар-аналітик Олега (37 р., Кошице, зміни 06-18 / 18-06, "
    "ціль — 78 кг). Пиши українською, тепло і по-людськи, але тільки ФАКТАМИ з даних "
    "нижче. ЗАБОРОНЕНО вигадувати числа, яких немає. Якщо даних бракує — скажи прямо, "
    "яких саме і попроси надіслати. Без порожніх фраз."
)


def ai_analysis(a: dict) -> str:
    prompt = (f"{_STYLE}\n\nДАНІ:\n{facts_block(a)}\n\n"
              "Дай аналіз 5-8 речень: що реально відбувається з організмом, які "
              "зв'язки між сном, кроками, пульсом і вагою ти бачиш саме в цих числах, "
              "що насторожує, що вдається добре. Без списків, суцільним текстом.")
    return (K.gemini_text(prompt, max_tokens=900, temperature=0.6, tag=TAG) or "").strip()


def ai_recommendations(a: dict) -> str:
    prompt = (f"{_STYLE}\n\nДАНІ:\n{facts_block(a)}\n\n"
              "Дай 3-4 КОНКРЕТНІ дії на найближчі 24 години. Кожна — один рядок з "
              "емодзі на початку, з числом або часом (наприклад «🚶 доходити 3 000 "
              "кроків після 18:00»). Тільки те, що випливає з цих даних.")
    return (K.gemini_text(prompt, max_tokens=700, temperature=0.6, tag=TAG) or "").strip()


# ─── ЗВІТИ ───────────────────────────────────────────────────────────────────

def _kb():
    # K.send_card очікує СПИСОК рядів кнопок, не {"inline_keyboard": ...}
    return [[
        {"text": "📊 Аналітика", "callback_data": "hai_stats"},
        {"text": "🎯 Рекомендації", "callback_data": "hai_reco"},
    ], [
        {"text": "✅ Зрозумів", "callback_data": "hai_ok"},
        {"text": "🔇 Не зараз", "callback_data": "hai_mute"},
    ]]


def coach_report(send: bool = True) -> str:
    """AI-КОУЧ — ранок. План на день на основі свіжих даних."""
    a = analytics(30)
    parts = ["💪 <b>AI-КОУЧ — план на день</b>", "", facts_block(a)]
    an = ai_analysis(a)
    if an:
        parts += ["", "🧠 <b>Аналіз</b>", an]
    rec = ai_recommendations(a)
    if rec:
        parts += ["", "🎯 <b>Що зробити сьогодні</b>", rec]
    text = "\n".join(parts)
    if send:
        K.send_card(text, _kb(), tag=TAG)
        _journal("coach", "AI-коуч: план на день")
    return text


def tracker_report(send: bool = True) -> str:
    """AI-ТРЕКЕР — вечір. Що зафіксовано за день, чого бракує, оцінка."""
    a = analytics(14)
    today = _now().strftime("%Y-%m-%d")
    todays = [i for i in load_journal() if i.get("day") == today]

    got, missing = [], []
    for label, key in (("вага", "weight"), ("сон", "sleep"), ("кроки", "steps"),
                       ("пульс", "hr")):
        m = a.get(key) or {}
        (got if m.get("last_day") == today else missing).append(label)

    parts = [f"📋 <b>AI-ТРЕКЕР — {_now().strftime('%d.%m')}</b>", "", facts_block(a), ""]
    parts.append(f"✅ Сьогодні є: {', '.join(got) if got else '— нічого'}")
    if missing:
        parts.append(f"❓ Бракує: {', '.join(missing)} — надішли одним рядком")
    if todays:
        parts.append(f"📥 Твоїх записів за сьогодні збережено: {len(todays)}")

    prompt = (f"{_STYLE}\n\nДАНІ:\n{facts_block(a)}\n"
              f"Сьогодні зафіксовано: {', '.join(got) or 'нічого'}. "
              f"Бракує: {', '.join(missing) or 'нічого'}.\n\n"
              "Дай оцінку дня 3-5 речень: чи день був у плюс для цілі 78 кг, що "
              "конкретно зробити перед сном, і чи є привід хвилюватись.")
    ai = (K.gemini_text(prompt, max_tokens=700, temperature=0.6, tag=TAG) or "").strip()
    if ai:
        parts += ["", "🤖 <b>Підсумок дня</b>", ai]

    text = "\n".join(parts)
    if send:
        K.send_card(text, _kb(), tag=TAG)
        _journal("tracker", "AI-трекер: підсумок дня")
    return text


def weekly_report(send: bool = True) -> str:
    """Глибокий тижневий розбір — неділя ввечері."""
    a = analytics(30)
    prompt = (f"{_STYLE}\n\nДАНІ ЗА 30 ДНІВ:\n{facts_block(a)}\n\n"
              "Зроби тижневий розбір: 1) що змінилось за тиждень у числах, "
              "2) головна причина, 3) що працює і треба лишити, 4) що прибрати, "
              "5) конкретна ціль на наступний тиждень із числом. Розділи заголовками.")
    ai = (K.gemini_text(prompt, max_tokens=1200, temperature=0.6, tag=TAG) or "").strip()
    text = "\n".join(["🗓 <b>AI-РОЗБІР ТИЖНЯ — здоров'я</b>", "", facts_block(a)]
                     + (["", ai] if ai else []))
    if send:
        K.send_card(text, _kb(), tag=TAG)
        _journal("weekly", "AI-розбір тижня")
    return text


def stats_report() -> str:
    """Тільки числа — для кнопки/команди, без AI."""
    return "📊 <b>Здоров'я — факти</b>\n\n" + facts_block(analytics(30))


# ─── ІНІЦІАТИВА ──────────────────────────────────────────────────────────────

def _journal(kind, what, detail=""):
    try:
        import selfact
        selfact.journal(kind, what, detail, module=TAG)
    except Exception:
        pass


def _muted() -> bool:
    try:
        import dismissed
        if dismissed.is_muted(TAG):
            return True
    except Exception:
        pass
    try:
        import quiet
        if quiet.blocked("msg"):
            return True
    except Exception:
        pass
    return False


def anomalies(a: dict) -> list:
    """Список приводів написати першим. Кожен — (ключ, текст)."""
    out = []

    w = a.get("weight") or {}
    if w.get("delta") is not None and w["delta"] >= 0.7 and w.get("n", 0) >= 4:
        out.append(("weight_up",
                    f"⚖️ Вага йде вгору: {w['delta']:+} кг за період, зараз {w['last']} кг "
                    f"(до цілі {w.get('to_goal')} кг)"))

    s = a.get("sleep") or {}
    if s.get("avg7") is not None and s["avg7"] < SLEEP_MIN_OK:
        out.append(("sleep_low",
                    f"😴 Сон за тиждень у середньому {s['avg7']} год — нижче за {SLEEP_MIN_OK}"))

    st = a.get("steps") or {}
    if st.get("avg7") is not None and st["avg7"] < STEPS_GOAL * 0.6:
        out.append(("steps_low",
                    f"🚶 Кроки за тиждень у середньому {int(st['avg7'])} — це менше "
                    f"{int(STEPS_GOAL * 0.6)} при цілі {STEPS_GOAL}"))

    hr = a.get("hr") or {}
    if hr.get("avg7") is not None and hr["avg7"] > HR_HIGH:
        out.append(("hr_high", f"❤️ Середній пульс {hr['avg7']} уд/хв — вище за {HR_HIGH}"))

    if a.get("stale_hours") is not None and a["stale_hours"] >= STALE_HOURS:
        out.append(("stale",
                    f"📵 Дані здоров'я не оновлювались {a['stale_hours']} год "
                    f"(останні — {a['last_data_day']}). Автосинк із годинника міг зламатись"))

    return out


def initiative(force: bool = False) -> int:
    """
    Кожні 30 хв: якщо є привід — пише першим. Один привід не частіше разу на 12 год.
    Повертає кількість надісланих сповіщень.
    """
    if not force and _muted():
        return 0
    if not force and not K.rate_ok(STATE_FILE, 30):
        return 0
    K.rate_mark(STATE_FILE)

    a = analytics(30)
    found = anomalies(a)
    if not found:
        return 0

    state = K.load(STATE_FILE, default={}) or {}
    seen = state.get("seen") or {}
    now = _now()
    sent = 0

    for key, line in found:
        prev = seen.get(key)
        if prev and not force:
            try:
                if (now - datetime.fromisoformat(prev)).total_seconds() < 12 * 3600:
                    continue
            except Exception:
                pass

        prompt = (f"{_STYLE}\n\nДАНІ:\n{facts_block(a)}\n\nПРИВІД: {line}\n\n"
                  "Напиши Олегу коротко (3-4 речення): що це означає саме для нього і "
                  "одна конкретна дія зараз. Без вступів.")
        ai = (K.gemini_text(prompt, max_tokens=450, temperature=0.6, tag=TAG) or "").strip()
        text = f"🩺 <b>Помітив у твоїх даних</b>\n\n{line}"
        if ai:
            text += f"\n\n{ai}"
        if K.send_card(text, _kb(), tag=TAG):
            seen[key] = now.isoformat(timespec="seconds")
            sent += 1
            _journal("notify", f"сповіщення про здоров'я: {key}", line)

    state["seen"] = seen
    K.save(STATE_FILE, state)
    K.log(TAG, f"initiative: приводів {len(found)}, надіслано {sent}")
    return sent


# ─── ПЛАНУВАЛЬНИК (викликається з monitor_loop щохвилини) ────────────────────

# Звіти перенесені у hcoach.py (AI-коуч 2.0): ранковий план 07:00, вечірній
# розбір + оцінка 21:20, сон, тижневий і місячний з графіками. Тут лишається
# захоплення даних, аналітика і ініціатива по аномаліях — без дублювання звітів.
_SLOTS = {}


def tick() -> str:
    """
    Один прохід: ранковий коуч 08:15, вечірній трекер 21:15, тижневий розбір
    у неділю 19:30, плюс ініціатива кожні 30 хв. Дедуп — за днем у стані.
    """
    now = _now()
    state = K.load(STATE_FILE, default={}) or {}
    day = now.strftime("%Y-%m-%d")
    done = []

    for name, (h, m, fn) in _SLOTS.items():
        if now.hour == h and m <= now.minute < m + 6 and state.get(f"{name}_day") != day:
            if _muted():
                continue
            try:
                fn(send=True)
                state[f"{name}_day"] = day
                done.append(name)
            except Exception as e:
                K.log(TAG, f"{name} error: {e}")

    if done:
        K.save(STATE_FILE, state)

    try:
        n = initiative()
        if n:
            done.append(f"initiative:{n}")
    except Exception as e:
        K.log(TAG, f"initiative error: {e}")

    return ", ".join(done)
