"""
healthai.py — здоров'я під AI-контролем.

Що робить:
1. ЗБЕРІГАЄ все, що Олег надсилає (вечірні дані з годинника/вручну) — і сирий
   текст (health_journal.json), і розібрані числа (через qwsync у qwatch_data.json).
2. АНАЛІТИКА фактами: вага/сон/кроки/пульс/HRV/калорії — середні, дельти,
   тренди, streak, пропущені дні, прогрес до цілі 75 кг.
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

WEIGHT_GOAL = 75.0          # ціль Олега
SLEEP_MIN_OK = 6.5          # менше — недосип
STEPS_GOAL = 8000
HR_HIGH = 85                # середній пульс спокою вище — сигнал
STALE_HOURS = 36            # дані не приходять довше — питаємо
STRESS_HIGH = 60            # body battery/watch stress 0-100, вище — сигнал
ENERGY_LOW = 30             # body battery 0-100, нижче — виснаження

# Олег оновлює дані 3 рази на день сам — якщо пройшло довше цього без
# жодного нового запису (не плутати з STALE_HOURS вище, це для великого
# розриву) — сам нагадує, поки день ще активний (запит Олега 21.09).
REMINDER_STALE_HOURS = 5
REMINDER_GAP_HOURS = 5      # не частіше цього між нагадуваннями

_SHIFT_UA = {"early": "☀️ рання 06:00–18:00",
             "night": "🌙 нічна 18:00–06:00",
             "free": "🏠 вихідний"}

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


# Розлогий AI-опис зі скріну QWatch Pro («Показатель здоровья: 87%. ... вес —
# 83 кг ... составила 725 шагов ... средний пульс составляет 68 ударов») —
# число стоїть ОДРАЗУ ПЕРЕД одиницею вимірювання, а не після ключового слова,
# як у короткому форматі нижче. group(1) — завжди саме число.
_UNIT_PATTERNS = [
    ("weight", r"(\d{2,3}(?:[.,]\d{1,2})?)\s*кг\b"),
    ("steps", r"(\d{2,6})\s*(?:шаг(?:ов|и|а)?|крок(?:ів|и|у)?)\b"),
    ("hr", r"(\d{2,3})\s*(?:ударов|уд[./]мин|ударів|bpm)\b"),
    ("health_score", r"(?:показник\w*\s+здоров['’ʼя]*|"
                      r"показатель\s+здоровья|health\s*score|"
                      r"оцінка\s+здоров['’ʼя]*)\D{0,15}?(\d{1,3})"),
]


def _kv_from_text(raw: str) -> dict:
    """
    Витягує пари «слово — число» з вільного тексту. Підтримує і короткий
    формат ("вага 83.4, сон 7г 20хв, кроки 9 120, пульс 68"), і розлогий
    AI-опис зі скріну QWatch Pro (число перед одиницею: "вес — 83 кг",
    "725 шагов", "68 ударов", "общем времени 11:09 (глубокий сон — 2:51,
    легкий сон — 5:18)") → dict для qwsync.normalize.
    """
    out = {}
    low = raw.lower().replace("\n", " ")

    # 1) розлогий AI-опис: число ОДРАЗУ ПЕРЕД одиницею
    for key, pat in _UNIT_PATTERNS:
        m = re.search(pat, low)
        if m:
            out[key] = m.group(1)

    # сон: явний загальний час ("общем времени 11:09" / "загальний час 11:09")
    # має пріоритет над розбивкою глибокий/легкий — інакше зловимо лише
    # частину ночі (наприклад "глубокий сон 2:51" замість повних 11:09).
    m = re.search(r"(?:загальн\w*\s+час\w*|общ\w*\s+врем\w*|total\s+time|"
                  r"total\s+sleep)\D{0,12}?(\d{1,2})[:.](\d{2})", low)
    if m:
        out["sleep_min"] = int(m.group(1)) * 60 + int(m.group(2))
    else:
        dm = re.search(r"(?:глубок\w*|глибок\w*)\s+сон\D{0,10}?(\d{1,2})[:.](\d{2})", low)
        lm = re.search(r"легк\w*\s+сон\D{0,10}?(\d{1,2})[:.](\d{2})", low)
        if dm and lm:
            out["sleep_min"] = (int(dm.group(1)) * 60 + int(dm.group(2))
                                 + int(lm.group(1)) * 60 + int(lm.group(2)))

    # 2) короткий формат: "сон 7г20" / "7h 20min" / "спав 7:20"
    if "sleep_min" not in out:
        m2 = re.search(r"(?:сон|sleep|спав)\D{0,6}(\d{1,2})\s*(?:г|год|h)\D{0,4}(\d{1,2})?", low)
        if m2:
            h = int(m2.group(1)); mi = int(m2.group(2) or 0)
            out["sleep_min"] = h * 60 + mi

    for word, key in _KEYS.items():
        if key == "sleep" and "sleep_min" in out:
            continue
        if key in out:
            continue  # уже знайдено розлогим AI-парсером вище
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


def _shift_for(offset: int = 0) -> str:
    """'early' | 'night' | 'free' — зміна на день (offset у днях від сьогодні)."""
    try:
        return K.classify_shift(K.events_for_day(offset))
    except Exception as e:
        K.log(TAG, f"shift error: {e}")
        return "free"


def sleep_consistency(health: dict, days: int = 14) -> dict:
    """
    Стабільність сну — не тільки тривалість, а й розкид (якість ритму).
    Розкид (стдев) днів з даними за period. None якщо даних < 4.
    """
    pairs = _series(health, "sleep_hours", days)
    vals = [v for _, v in pairs]
    if len(vals) < 4:
        return {"n": len(vals), "std": None, "word": "мало даних"}
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    std = round(var ** 0.5, 2)
    if std < 0.6:
        word = "стабільний ритм"
    elif std < 1.2:
        word = "помірні перепади"
    else:
        word = "дуже нерівний ритм"
    return {"n": len(vals), "std": std, "word": word}


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


def _weight_series(days: int):
    """[(день, вага)] — weight_data.json (канонічний, пише /вага) виграє для
    кожного дня; qwatch weight_kg використовується ЛИШЕ для днів, яких немає
    у weight_data.json. Раніше analytics() брав вагу тільки з qwatch weight_kg
    через storage.load_health(), тому показував застарілі/чужі числа (24.09 —
    сказав "83.0 кг за 22.09", хоча weight_data.json уже мав 84.1 за 24.09 і
    82.3 за 22.09 — 83.0 було старим значенням із qwatch за 21-22.09)."""
    import storage
    try:
        wd = storage.load_weight() or {}
    except Exception as e:
        K.log(TAG, f"load_weight error: {e}")
        wd = {}
    try:
        health = storage.load_health() or {}
    except Exception:
        health = {}

    today = _now().date()
    merged = {}
    for day, rec in (health or {}).items():
        if not isinstance(rec, dict):
            continue
        v = rec.get("weight_kg")
        if v not in (None, "", 0):
            merged[day] = v
    for day, v in (wd or {}).items():
        if v not in (None, "", 0):
            merged[day] = v  # канонічне джерело перекриває qwatch

    out = []
    for day, v in merged.items():
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


def analytics(days: int = 30) -> dict:
    """Повна картина фактами. Нічого не вигадує: чого немає — None."""
    import storage
    try:
        health = storage.load_health() or {}
    except Exception as e:
        K.log(TAG, f"load_health error: {e}")
        health = {}

    today_str = _now().strftime("%Y-%m-%d")
    out = {"days": days, "generated": _now().isoformat(timespec="seconds"), "today": today_str}

    spec = [("weight_kg", "weight"), ("sleep_hours", "sleep"), ("steps", "steps"),
            ("hr_avg", "hr"), ("hrv", "hrv"), ("calories", "calories"),
            ("body_battery", "energy"), ("stress", "stress")]
    for field, name in spec:
        if field == "weight_kg":
            pairs = _weight_series(days)
            d7 = [v for _, v in _weight_series(7)]
        else:
            pairs = _series(health, field, days)
            d7 = [v for _, v in _series(health, field, 7)]
        vals = [v for _, v in pairs]
        delta, word = _trend(pairs)
        last_day = pairs[-1][0] if pairs else None
        days_ago = None
        if last_day:
            try:
                days_ago = (datetime.strptime(today_str, "%Y-%m-%d").date()
                            - datetime.strptime(last_day, "%Y-%m-%d").date()).days
            except Exception:
                days_ago = None
        # Час останнього синку за last_day (з qwatch "saved_at") — потрібен,
        # щоб відрізнити "сьогоднішнє число ЗАФІКСОВАНЕ ОСТАННІМ" від
        # "сьогоднішнє число може ще ЗМІНИТИСЬ" (25.09: годинник синкнув
        # сон о 11:27 як 3.0г, а насправді за ніч було 6:47 — повний
        # показник дозаписався пізніше, автосинк його ще не підхопив;
        # AI не мав про це знати і написав категоричне "сон лише 3.0г").
        last_saved_at = None
        if last_day and isinstance(health.get(last_day), dict):
            last_saved_at = health[last_day].get("saved_at")
        out[name] = {
            "n": len(vals),
            "last": vals[-1] if vals else None,
            "last_day": last_day,
            "days_ago": days_ago,          # 0 = сьогодні, 1+ = застаріле для ЦЬОГО показника
            "last_saved_at": last_saved_at,
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

    # якість/стабільність сну — не тільки скільки годин, а й наскільки рівно
    out["sleep_quality"] = sleep_consistency(health, min(days, 14))

    # зміна сьогодні/завтра — щоб AI бачив зв'язок графіка зі здоров'ям
    out["shift_today"] = _shift_for(0)
    out["shift_tomorrow"] = _shift_for(1)

    # біг зі Strava (якщо доступний) — контекст, не критично
    try:
        import running as strava
        wk = strava.get_week_stats() or {}
        out["run_week_km"] = wk.get("distance_km") or wk.get("km")
    except Exception:
        out["run_week_km"] = None

    return out


def facts_block(a: dict) -> str:
    """Числа одним компактним блоком — і для AI-промпту, і для звіту."""
    def line(label, m, unit="", extra="", same_day_caveat=False):
        if not m or m.get("last") is None:
            return f"{label}: немає даних"
        s = f"{label}: {m['last']}{unit}"
        da = m.get("days_ago")
        if da is not None and da >= 1:
            # ЦЕ ГОЛОВНИЙ ЗАХИСТ ВІД "НЕПРАВДИВОЇ ІНФОРМАЦІЇ": показник міг
            # оновитись на попередній день (напр. QWatch-скрін без розділу
            # сну), тоді як інші показники сьогодні вже свіжі. Без цього
            # маркера AI бачив просто число і сам вирішував, що воно "за
            # сьогодні" — звідси "ти спав 2.0 год за 22 вересня", хоча ці
            # 2.0 год насправді за 21-ше.
            s += f" [!!СТАРІ ДАНІ за {m['last_day']}, {da} дн. тому, НЕ сьогодні!!]"
        elif da == 0 and same_day_caveat:
            # ДРУГИЙ ЗАХИСТ (25.09): значення "за сьогодні" саме по собі
            # НЕ застаріле (days_ago=0), АЛЕ автосинк годинника пише
            # ЧАСТКОВІ дані протягом дня — сон, зафіксований о 11:27, може
            # бути НЕ фінальним нічним підсумком (реальний приклад: синк
            # показав 3.0г, а насправді за ніч було 6:47 — доповнилось
            # пізніше, новий синк того ж дня ще не прийшов). Без цієї
            # позначки AI писав категоричне "сон сьогодні лише 3.0 години,
            # значно менше середнього" — хоча це було ПРОМІЖНЕ число.
            when = f" о {m['last_saved_at'][-5:]}" if m.get("last_saved_at") else ""
            s += (f" [⚠️ ЗАФІКСОВАНО СЬОГОДНІ{when}, МОЖЕ БУТИ ЧАСТКОВИМ — "
                  f"годинник ще може дослати повніше число пізніше того ж дня; "
                  f"НЕ роби категоричних/тривожних висновків типа 'сон лише Xг' "
                  f"на основі цього значення, подавай як 'поки що зафіксовано'. "
                  f"НЕ називай це 'суперечністю' чи 'протиріччям' і НЕ звинувачуй "
                  f"Олега в невідповідності між тим, що він написав/натиснув, і цим "
                  f"числом — часткове число саме по собі НІЧОГО не доводить і не "
                  f"спростовує, це просто ще не повне вимірювання]")
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
        f"Сьогодні: {a.get('today', '?')}",
        line("Вага", w, " кг", goal),
        line("Сон", a.get("sleep"), " год", same_day_caveat=True),
        line("Кроки", a.get("steps")),
        line("Пульс", a.get("hr"), " уд/хв"),
        line("HRV", a.get("hrv"), " мс"),
        line("Калорії", a.get("calories"), " ккал"),
        line("Енергія (body battery)", a.get("energy")),
        line("Стрес (з годинника)", a.get("stress")),
    ]
    sq = a.get("sleep_quality") or {}
    if sq.get("std") is not None:
        rows.append(f"Стабільність сну: {sq['word']} (розкид ±{sq['std']} год)")
    if a.get("shift_today"):
        rows.append("Зміна сьогодні: " + _SHIFT_UA.get(a["shift_today"], a["shift_today"])
                    + " | завтра: " + _SHIFT_UA.get(a.get("shift_tomorrow"), "—"))
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
    "Ти особистий тренер і лікар-аналітик Олега (37 р., Кошице, змінний графік "
    "06:00-18:00 / 18:00-06:00, ціль — 75 кг). Пиши українською, тепло і по-людськи, "
    "але тільки ФАКТАМИ з даних нижче. ЗАБОРОНЕНО вигадувати числа чи стан (настрій, "
    "енергію, стрес), яких немає в даних. Якщо якогось показника немає — прямо скажи, "
    "чого саме бракує, і попроси надіслати, а не вигадуй за нього. Без порожніх фраз.\n"
    "КРИТИЧНО ПРО ДАТИ: рядок 'Сьогодні: YYYY-MM-DD' — це справжня сьогоднішня дата. "
    "Якщо біля показника стоїть позначка [!!СТАРІ ДАНІ за ..., N дн. тому, НЕ сьогодні!!] "
    "— це означає останнє ЗНАЧЕННЯ цього показника прийшло НЕ сьогодні, а раніше. "
    "ТОБІ АБСОЛЮТНО ЗАБОРОНЕНО писати про це число 'сьогодні', 'зараз' чи вказувати "
    "сьогоднішню дату — це буде НЕПРАВДА. Замість цього прямо скажи: 'за сьогодні "
    "[показник] ще не приходив, останнє відоме — [дата], [N] дн. тому' і попроси "
    "надіслати свіжі дані. Показники без цієї позначки — дійсно за сьогодні, про них "
    "можна писати 'сьогодні'/'зараз'.\n"
    "ГЛИБИНА: не просто перелічуй числа — РОЗБИРАЙ зв'язки: як графік зміни "
    "(сьогодні/завтра) впливає на сон і коли він встигає відновитись; що якість і "
    "стабільність сну (не тільки тривалість) кажуть про організм; якщо є дані по "
    "енергії (body battery) чи стресу з годинника — пов'яжи їх зі сном і зміною; як усе "
    "це разом тягне вагу до/від цілі 75 кг. Один показник окремо нічого не значить — "
    "цінність у зв'язках між ними."
)


def ai_analysis(a: dict) -> str:
    ctx = (facts_block(a) + "\n"
           + "Зміна сьогодні: " + _SHIFT_UA.get(a.get("shift_today"), "—")
           + " | завтра: " + _SHIFT_UA.get(a.get("shift_tomorrow"), "—"))
    prompt = (f"{_STYLE}\n\nДАНІ:\n{ctx}\n\n"
              "Дай глибокий персональний аналіз 400-600 слів (не список, суцільним "
              "текстом кількома абзацами): 1) що зараз реально відбувається з "
              "організмом і чому — саме в цих числах; 2) як графік зміни, сон і його "
              "стабільність тягнуть за собою кроки/пульс/енергію; 3) як усе це впливає "
              "на прогрес до 75 кг; 4) що насторожує і що вдається добре; 5) одна річ, "
              "яку сам Олег міг не помітити в цих даних. Якщо енергії/стресу немає в "
              "даних — не вигадуй їх, просто пропусти цей зв'язок.")
    return (K.gemini_text(prompt, max_tokens=1600, temperature=0.65, tag=TAG) or "").strip()


def ai_recommendations(a: dict) -> str:
    ctx = (facts_block(a) + "\n"
           + "Зміна сьогодні: " + _SHIFT_UA.get(a.get("shift_today"), "—")
           + " | завтра: " + _SHIFT_UA.get(a.get("shift_tomorrow"), "—"))
    prompt = (f"{_STYLE}\n\nДАНІ:\n{ctx}\n\n"
              "Дай КОНКРЕТНІ дії на найближчі 24-48 годин — СТІЛЬКИ, скільки реально "
              "випливає з цих даних (не обмежуй себе трьома пунктами: якщо є 6-7 "
              "самостійних приводів — по вазі, сну, крокам, пульсу, енергії/стресу, "
              "графіку зміни — дай всі; якщо привід один — дай один, без вигадування "
              "зайвого лише для кількості). Кожна дія — окремий рядок з емодзі на "
              "початку, з конкретним числом або часом (наприклад «🚶 доходити 3 000 "
              "кроків після 18:00», «💧 випити 0.5л води до 12:00 бо вчора сон "
              "короткий»). Одне слово — одна причина в дужках, чому саме ця дія "
              "випливає з даних. Без загальних порад типу «більше рухайся».")
    return (K.gemini_text(prompt, max_tokens=1100, temperature=0.6, tag=TAG) or "").strip()


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
              "Дай оцінку дня 3-5 речень: чи день був у плюс для цілі 75 кг, що "
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
    """
    /здоров'я — факти + ГЛИБОКИЙ AI-аналіз (зв'язки графік зміни↔сон↔
    енергія/стрес↔вага) + КОНКРЕТНІ дії, скільки їх реально випливає з даних
    (не шаблонний фіксований список).
    """
    a = analytics(30)
    parts = ["📊 <b>Здоров'я — факти, аналіз і дії</b>", "", facts_block(a)]
    an = ai_analysis(a)
    if an:
        parts += ["", "🧠 <b>Глибокий аналіз</b>", an]
    rec = ai_recommendations(a)
    if rec:
        parts += ["", "🎯 <b>Що зробити (з твоїх даних)</b>", rec]
    return "\n".join(parts)


def facts_only() -> str:
    """Чисті факти без AI (для внутрішнього використання/дебагу)."""
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

    stress = a.get("stress") or {}
    if stress.get("avg7") is not None and stress["avg7"] >= STRESS_HIGH:
        out.append(("stress_high",
                    f"😣 Стрес з годинника за тиждень у середньому {stress['avg7']} "
                    f"— вище за {STRESS_HIGH}"))

    energy = a.get("energy") or {}
    if energy.get("avg7") is not None and energy["avg7"] <= ENERGY_LOW:
        out.append(("energy_low",
                    f"🔋 Енергія (body battery) за тиждень у середньому {energy['avg7']} "
                    f"— нижче за {ENERGY_LOW}, організм виснажений"))

    sq = a.get("sleep_quality") or {}
    if sq.get("std") is not None and sq["std"] >= 1.2 and sq.get("n", 0) >= 5:
        out.append(("sleep_unstable",
                    f"😴 Сон дуже нерівний останні дні (розкид ±{sq['std']} год) — "
                    "тіло не встигає підлаштуватись під ритм"))

    if a.get("stale_hours") is not None and a["stale_hours"] >= STALE_HOURS:
        out.append(("stale",
                    f"📵 Дані здоров'я не оновлювались {a['stale_hours']} год "
                    f"(останні — {a['last_data_day']}). Автосинк із годинника міг зламатись"))

    # Прямий фікс на "AI пише неправдиву інформацію": сьогодні прийшли
    # СВІЖІ дані (last_data_day == сьогодні), але конкретний показник у них
    # відсутній — і без цієї перевірки healthai мовчки бере ЙОГО останнє
    # відоме значення з попереднього дня і подає як актуальне. Кожен такий
    # показник — окремий привід написати й прямо попросити конкретні дані,
    # а не здогадуватись.
    today_str = a.get("today")
    if today_str and a.get("last_data_day") == today_str:
        for key, label, emoji in (("sleep", "сон", "😴"), ("hr", "пульс", "❤️"),
                                    ("hrv", "HRV", "💓"), ("steps", "кроки", "🚶")):
            m = a.get(key) or {}
            da = m.get("days_ago")
            if da is not None and da >= 1:
                out.append((f"{key}_field_stale",
                            f"{emoji} Сьогоднішні дані вже прийшли, але без {label} — "
                            f"останнє відоме значення за {m['last_day']} ({da} дн. тому). "
                            f"Скинь, будь ласка, {label} окремо, якщо він є в застосунку QWatch."))

    return out


def good_signals(a: dict) -> list:
    """Приводи похвалити (не тільки проблеми) — теж приводи написати першим."""
    out = []

    st = a.get("steps") or {}
    if st.get("avg7") is not None and st["avg7"] >= STEPS_GOAL and st.get("n", 0) >= 4:
        out.append(("steps_good",
                    f"🚶 Кроки за тиждень у середньому {int(st['avg7'])} — ціль "
                    f"{STEPS_GOAL} досягнута"))

    w = a.get("weight") or {}
    if w.get("delta") is not None and w["delta"] <= -0.4 and w.get("n", 0) >= 4:
        out.append(("weight_good",
                    f"⚖️ Вага йде вниз: {w['delta']:+} кг за період, зараз {w['last']} кг "
                    f"(до цілі {w.get('to_goal')} кг)"))

    s = a.get("sleep") or {}
    if s.get("avg7") is not None and s["avg7"] >= SLEEP_MIN_OK + 0.5 and s.get("n", 0) >= 4:
        out.append(("sleep_good",
                    f"😴 Сон за тиждень у середньому {s['avg7']} год — стабільно добре"))

    return out


def initiative(force: bool = False) -> int:
    """
    Кожні 20 хв: якщо є привід — пише першим (проблема або привід похвалити).
    Проблема — не частіше разу на 8 год того самого типу, похвала — разу на 24 год.
    Повертає кількість надісланих сповіщень.
    """
    if not force and _muted():
        return 0
    if not force and not K.rate_ok(STATE_FILE, 20):
        return 0
    K.rate_mark(STATE_FILE)

    a = analytics(30)
    problems = anomalies(a)
    good = good_signals(a)
    if not problems and not good:
        return 0

    state = K.load(STATE_FILE, default={}) or {}
    seen = state.get("seen") or {}
    now = _now()
    sent = 0

    def _due(key, hours):
        prev = seen.get(key)
        if not prev or force:
            return True
        try:
            return (now - datetime.fromisoformat(prev)).total_seconds() >= hours * 3600
        except Exception:
            return True

    for key, line, header, hours, tone in (
        [(k, l, "🩺 <b>Помітив у твоїх даних</b>", 8,
          "Напиши Олегу коротко (3-4 речення): що це означає саме для нього і "
          "одна конкретна дія зараз. Без вступів.") for k, l in problems]
        + [(k, l, "👍 <b>Помітив дещо хороше</b>", 24,
            "Напиши Олегу коротко (2-3 речення): похвали конкретно за це і одним "
            "рядком — що допоможе закріпити результат. Без вступів.") for k, l in good]
    ):
        if not _due(key, hours):
            continue

        prompt = f"{_STYLE}\n\nДАНІ:\n{facts_block(a)}\n\nПРИВІД: {line}\n\n{tone}"
        ai = (K.gemini_text(prompt, max_tokens=450, temperature=0.6, tag=TAG) or "").strip()
        text = f"{header}\n\n{line}"
        if ai:
            text += f"\n\n{ai}"
        if K.send_card(text, _kb(), tag=TAG):
            seen[key] = now.isoformat(timespec="seconds")
            sent += 1
            _journal("notify", f"сповіщення про здоров'я: {key}", line)

    state["seen"] = seen
    K.save(STATE_FILE, state)
    K.log(TAG, f"initiative: проблем {len(problems)}, похвал {len(good)}, надіслано {sent}")
    return sent


# ─── НАГАДУВАННЯ «НАДІШЛИ ДАНІ» ───────────────────────────────────────────────
# Олег сам оновлює дані ~3 рази на день, але може забути. Якщо пройшло довше
# REMINDER_STALE_HOURS без жодного нового запису (не день без даних узагалі —
# саме мовчання від останнього повідомлення) — коуч сам просить скинути цифри,
# поки день ще активний. Дедуп: не частіше REMINDER_GAP_HOURS, щоб не спамити.

def _hours_since_last_capture() -> float:
    items = load_journal()
    if not items:
        return 999.0
    try:
        last_ts = datetime.fromisoformat(str(items[-1].get("ts") or ""))
    except Exception:
        return 999.0
    return round((_now() - last_ts).total_seconds() / 3600.0, 1)


def data_reminder(force: bool = False) -> bool:
    """Сам нагадує надіслати дані, якщо довго мовчав. True — надіслано."""
    if not force and _muted():
        return False
    now = _now()
    if not force and not (HOURLY_START <= now.hour <= HOURLY_END):
        return False

    hours = _hours_since_last_capture()
    if hours < REMINDER_STALE_HOURS:
        return False

    state = K.load(STATE_FILE, default={}) or {}
    last = state.get("last_reminder")
    if not force and last:
        try:
            gap = (now - datetime.fromisoformat(last)).total_seconds() / 3600
            if gap < REMINDER_GAP_HOURS:
                return False
        except Exception:
            pass

    text = (
        "📵 <b>Давно тебе не бачив у даних здоров'я</b>\n\n"
        f"Останній запис — {hours:.0f} год тому. Ти зазвичай оновлюєш "
        "приблизно 3 рази на день — скинь актуальні цифри (вага, сон, кроки, "
        "пульс), щоб я не рахував аналіз і план дня на застарілому."
    )
    if K.send_card(text, _kb(), tag=TAG):
        state["last_reminder"] = now.isoformat(timespec="seconds")
        K.save(STATE_FILE, state)
        _journal("reminder", "нагадування надіслати дані", f"{hours:.0f} год мовчання")
        return True
    return False


# ─── ЛИСТИ ПРО ЗДОРОВ'Я ───────────────────────────────────────────────────────
# Повний доступ до пошти для здоров'я (запит Олега 21.09): лікар, аптека,
# страхова, результати аналізів — фільтр за темою/відправником, без витрати
# Gemini на кожен скан. Один лист = одне сповіщення (дедуп за uid).

_HEALTH_MAIL_KEYWORDS = (
    "лікар", "лікарн", "клінік", "аналіз", "результат", "лаборатор",
    "страхов", "аптек", "рецепт", "медичн", "мрт", "узі", "щеплен",
    "вакцин", "стоматолог",
    "doctor", "clinic", "pharmacy", "lab result", "blood test", "insurance",
    "prescription", "medical", "vaccination", "poistenie", "poistovna",
    "lekáreň", "lekar", "nemocnica",
)

MAIL_DEDUP_FILE = "healthai_mail_sent.json"
MAIL_SCAN_STATE = "healthai_mail_scan.json"
MAIL_SCAN_GAP_MIN = 45
_mail_dedup = None  # лінива інціалізація — щоб не тягнути K.Dedup при імпорті


def _get_mail_dedup():
    global _mail_dedup
    if _mail_dedup is None:
        _mail_dedup = K.Dedup(MAIL_DEDUP_FILE, ttl_days=30)
    return _mail_dedup


def _health_emails() -> list:
    """Останні листи, що стосуються здоров'я. [] якщо пошта недоступна."""
    try:
        import monitor as _m
        raw = _m.get_emails()
    except Exception as e:
        K.log(TAG, f"get_emails error: {e}")
        return []

    if isinstance(raw, dict):
        items = raw.get("items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        return []

    out = []
    for e in items:
        if not isinstance(e, dict):
            continue
        uid = str(e.get("uid") or "")
        sender = str(e.get("sender") or e.get("from") or "")
        subject = str(e.get("subject") or "")
        blob = f"{sender} {subject}".lower()
        if any(k in blob for k in _HEALTH_MAIL_KEYWORDS):
            out.append({"uid": uid, "sender": sender, "subject": subject})
    return out


def health_mail_check(force: bool = False) -> int:
    """Сповіщає про НОВИЙ лист про здоров'я (лікар/аптека/аналізи/страхова)."""
    if not force and _muted():
        return 0
    dedup = _get_mail_dedup()
    hits = _health_emails()
    sent = 0
    for h in hits[:5]:
        uid = h.get("uid") or ""
        if not uid or dedup.seen("mail", uid):
            continue
        text = (
            "📧 <b>Лист про здоров'я</b>\n\n"
            f"Від: {K.esc(h['sender'])}\n"
            f"Тема: {K.esc(h['subject'])}\n\n"
            "Схоже, це щось про лікаря, аптеку, аналізи чи страховку — "
            "перевір лист і скажи мені результат чи дату цифрою, якщо там "
            "щось варте фіксації для аналізу здоров'я."
        )
        if K.send_card(text, _kb(), tag=TAG):
            dedup.mark("mail", uid)
            sent += 1
            _journal("mail", "лист про здоров'я", h["subject"][:100])
    return sent


# ─── ЩОГОДИННИЙ ЧЕК-ІН ПРОТЯГОМ АКТИВНОГО ЧАСУ ───────────────────────────────
# Раніше було 3 фіксовані поради на день — Олег попросив 20.09: "мало видно",
# хоче майже щогодини протягом активного часу + AI сам вирішує коли актуально
# (враховуючи календар) + AI має ставити уточнюючі питання, коли даних не
# хватає, а не тільки констатувати факти.

HOURLY_START = 7             # активний час — з 07:00
HOURLY_END = 23               # до 23:00 (включно, останній чек-ін о 23:xx)

_MISSING_FIELDS = [("вагу", "weight"), ("сон", "sleep"), ("кроки", "steps"),
                   ("пульс", "hr")]


def _missing_today(a: dict) -> list:
    today = _now().strftime("%Y-%m-%d")
    out = []
    for label, key in _MISSING_FIELDS:
        m = a.get(key) or {}
        if m.get("last_day") != today:
            out.append(label)
    return out


def _upcoming_events_text() -> str:
    """Події сьогодні з календаря — щоб AI бачив контекст дня (зустрічі, зміна)."""
    try:
        events = K.events_for_day(0) or []
    except Exception:
        return "немає доступу до календаря"
    if not events:
        return "нічого не заплановано на сьогодні"
    out = [ev.get("summary", "(без назви)") for ev in events if ev.get("summary")]
    return ", ".join(out[:6]) if out else "нічого конкретного не видно"


def hourly_checkin(send: bool = True) -> str:
    """
    Один чек-ін протягом активного часу (викликається з tick() щогодини).
    AI сам вирішує, що написати: коротку пораду, конкретну дію, ПИТАННЯ якщо
    даних не хватає, або щось прив'язане до найближчих подій з календаря.
    Це НЕ звіт із фактами — це один живий рядок, як від людини-коуча.
    """
    a = analytics(14)
    missing = _missing_today(a)
    events_txt = _upcoming_events_text()

    ctx = (facts_block(a) + "\n"
           + "Зміна сьогодні: " + _SHIFT_UA.get(a.get("shift_today"), "—")
           + " | завтра: " + _SHIFT_UA.get(a.get("shift_tomorrow"), "—") + "\n"
           + "Сьогодні в календарі: " + events_txt + "\n"
           + "Дані, яких СЬОГОДНІ ще немає: " + (", ".join(missing) if missing else "усе є"))

    prompt = (
        f"{_STYLE}\n\nДАНІ:\n{ctx}\n\n"
        "Це живий проактивний чек-ін протягом дня (не звіт, не список). Напиши "
        "РІВНО ОДНЕ повідомлення (2-4 речення, з емодзі на початку), обравши "
        "САМ найдоцільніший ЗАРАЗ підхід:\n"
        "А) якщо є дані, яких сьогодні ще немає — прямо ЗАПИТАЙ конкретне "
        "('А скільки годин ти спав сьогодні?', 'Скільки кроків уже є?') — не "
        "просто згадай, а поставте питання і поясни навіщо воно тобі для аналізу;\n"
        "Б) якщо в календарі є щось найближче — прив'яжи коротку пораду чи "
        "запитання саме до цієї події (як вона вплине на сон/їжу/рух);\n"
        "В) інакше — дай одну конкретну дію чи спостереження з цифр вище, або "
        "запитай щось саме про самопочуття/енергію зараз, якщо це логічно.\n"
        "Пиши як активний партнер, який щиро цікавиться, а не як бот-нагадувалка. "
        "Без вступу, без списку, без повторів попередніх повідомлень."
    )
    tip = (K.gemini_text(prompt, max_tokens=320, temperature=0.75, tag=TAG) or "").strip()
    if not tip:
        return ""
    text = tip
    if send:
        # Якщо AI поставив тут ЖИВЕ питання ("а скільки годин ти спав?") —
        # кнопки мають бути варіантами відповіді САМЕ на нього (autokb/askme),
        # а не завжди однаковий рядок «Аналітика/Рекомендації». Fuzzy-дедуп
        # у autokb також не дає перепитувати те саме іншими словами.
        try:
            import autokb as _akb
            if not _akb.should_send(text, tag=TAG):
                K.log(TAG, "hourly: питання вже закрито — не надсилаю повторно")
                return text
            rows = _akb.build(text, tag=TAG) or _kb()
            K.send_card(text, rows, tag=TAG)
        except Exception as e:
            K.log(TAG, "autokb error: " + str(e))
            K.send_card(text, _kb(), tag=TAG)
        _journal("hourly", "щогодинний чек-ін здоров'я")
    return text


# ─── ПЛАНУВАЛЬНИК (викликається з monitor_loop щохвилини) ────────────────────

# Звіти перенесені у hcoach.py (AI-коуч 2.0): ранковий план 07:00, вечірній
# розбір + оцінка 21:20, сон, тижневий і місячний з графіками. Тут лишається
# захоплення даних, аналітика, щогодинний живий чек-ін протягом активного часу
# і ініціатива по аномаліях/хороших сигналах — без дублювання звітів.
_SLOTS = {}


def tick() -> str:
    """
    Один прохід: живий чек-ін щогодини з 07:00 до 23:00 (о хв 5-11, щоб не
    збігатись з іншими слотами), плюс ініціатива по аномаліях/хороших
    сигналах кожні 20 хв. Дедуп чек-іну — за годиною, не за днем.
    """
    now = _now()
    state = K.load(STATE_FILE, default={}) or {}
    hour_key = now.strftime("%Y-%m-%d %H")
    done = []
    muted = _muted()

    if (HOURLY_START <= now.hour <= HOURLY_END and 5 <= now.minute < 11
            and state.get("hourly_slot") != hour_key and not muted):
        try:
            if hourly_checkin(send=True):
                state["hourly_slot"] = hour_key
                done.append("hourly")
        except Exception as e:
            K.log(TAG, f"hourly error: {e}")

    day = now.strftime("%Y-%m-%d")
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

    try:
        if data_reminder():
            done.append("reminder")
    except Exception as e:
        K.log(TAG, f"reminder error: {e}")

    try:
        if K.rate_ok(MAIL_SCAN_STATE, MAIL_SCAN_GAP_MIN):
            K.rate_mark(MAIL_SCAN_STATE)
            n = health_mail_check()
            if n:
                done.append(f"mail:{n}")
    except Exception as e:
        K.log(TAG, f"mail check error: {e}")

    return ", ".join(done)


