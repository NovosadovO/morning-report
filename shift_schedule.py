#!/usr/bin/env python3
"""
ГЕНЕРАТОР ГРАФІКА ЗМІН НА МІСЯЦЬ + АВТОБЛОКИ СНУ  (Час/зміни #1)

Олег пише одним повідомленням, як йому дали графік:
    /зміни нічні 5-9, рання 12-16, вільні 17-18
    графік на серпень: R 1-5, N 8-12
    зміни: рання 4,5,6,7 серпня і нічна 11-15

Бот:
  1. Парсить це (Gemini + локальний regex-фолбек) у список {date, type}.
  2. Показує ПРЕВʼЮ карточкою — Олег бачить, що саме буде створено.
  3. По кнопці створює в Google Calendar:
       ☀️ Рання зміна   06:00–18:00
       🌙 Нічна зміна   17:30–06:00 (наступного дня)
     і АВТОБЛОКИ СНУ:
       перед ранньою      😴 Сон 21:30 (день до) → 05:15
       після нічної       😴 Сон після нічної 07:00 → 14:00
     + 🚗 Виїзд на роботу за 30 хв до зміни.

Нічого не створюється без підтвердження кнопкою.
Callback-префікси: shf_ok_ / shf_only_ / shf_no_
"""

import re
from datetime import datetime, timedelta

import ai_kit as K

TAG = "shifts"

STORE_FILE = "shift_plan_store.json"
CREATED_FILE = "shift_created.json"   # {'YYYY-MM-DD': 'early'|'night'} що вже створено

_store = K.PayloadStore(STORE_FILE)

UA_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
UA_MONTHS = {
    "січ": 1, "лют": 2, "бер": 3, "квіт": 4, "трав": 5, "черв": 6,
    "лип": 7, "серп": 8, "верес": 9, "жовт": 10, "листоп": 11, "груд": 12,
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}

EARLY_START, EARLY_END = "06:00", "18:00"
NIGHT_START, NIGHT_END = "17:30", "06:00"


# ─── ПАРСИНГ ─────────────────────────────────────────────────────────────────

_PROMPT = """Розпарси графік змін Олега у JSON.

ЗАРАЗ: {now} (поточний місяць {month:02d}, рік {year})

ТЕКСТ ОЛЕГА:
{text}

ПРАВИЛА:
1. Типи: "early" (рання/ранкова/денна/R/д) і "night" (нічна/N/н).
2. "5-9" означає діапазон днів 5,6,7,8,9 включно.
3. Якщо місяць не вказаний — бери поточний ({month:02d}). Якщо число вже минуло
   більше ніж на 20 днів — це наступний місяць.
4. "вільні" / "вихідні" / "відпустка" — НЕ включай у результат.
5. Дати — YYYY-MM-DD.

Формат — ТІЛЬКИ JSON-масив без markdown, відсортований за датою:
[{{"date":"{year}-{month:02d}-05","type":"night"}}]
Якщо розпарсити неможливо — поверни []."""


def _norm_type(t: str) -> str:
    t = str(t or "").lower()
    if any(x in t for x in ("night", "нічн", "ночн", "n", "н")):
        if any(x in t for x in ("night", "нічн", "ночн")) or t.strip() in ("n", "н"):
            return "night"
    if any(x in t for x in ("early", "ранн", "ранк", "денн", "r", "д")):
        return "early"
    if "нічн" in t or "night" in t:
        return "night"
    return ""


def _local_parse(text: str) -> list:
    """Фолбек без AI: «нічні 5-9», «рання 12-16», «N 8-12», «рання 4,5,6»."""
    now = K.now()
    year, month = now.year, now.month

    m = re.search(r"(січ|лют|бер|квіт|трав|черв|лип|серп|верес|жовт|листоп|груд"
                  r"|январ|феврал|март|апрел|май|июн|июл|август|сентябр|октябр|ноябр|декабр)",
                  text.lower())
    if m:
        month = UA_MONTHS[m.group(1)]
        if month < now.month:
            year += 1

    out = {}
    pattern = re.compile(
        r"(нічн\w*|ночн\w*|night|\bn\b|\bн\b|ранн\w*|ранк\w*|денн\w*|early|\br\b|\bд\b)"
        r"\s*[:\-—]?\s*((?:\d{1,2}\s*(?:[-–—]\s*\d{1,2})?\s*[,;]?\s*)+)",
        re.IGNORECASE)
    for kind_raw, days_raw in pattern.findall(text):
        kind = _norm_type(kind_raw)
        if not kind:
            continue
        for part in re.split(r"[,;]", days_raw):
            part = part.strip()
            if not part:
                continue
            r = re.match(r"^(\d{1,2})\s*[-–—]\s*(\d{1,2})$", part)
            days = []
            if r:
                a, b = int(r.group(1)), int(r.group(2))
                if 1 <= a <= 31 and 1 <= b <= 31 and a <= b:
                    days = list(range(a, b + 1))
            elif part.isdigit() and 1 <= int(part) <= 31:
                days = [int(part)]
            for d in days:
                try:
                    date = datetime(year, month, d).strftime("%Y-%m-%d")
                except ValueError:
                    continue
                out[date] = kind
    return [{"date": d, "type": t} for d, t in sorted(out.items())]


def parse_schedule(text: str) -> list:
    """Повертає [{date, type}] — спершu AI, потім локальний фолбек."""
    now = K.now()
    items = []
    if K.GEMINI_KEY:
        prompt = _PROMPT.format(now=now.strftime("%Y-%m-%d"), month=now.month,
                                year=now.year, text=text[:900])
        items = K.gemini_json(prompt, max_tokens=1400, temperature=0.1, tag=TAG)

    # Горизонт: від сьогодні до +75 днів. AI іноді дає дурні роки (2022) —
    # такі дати НЕ викидаємо, а перебудовуємо з номера дня в найближчий
    # відповідний місяць; якщо все одно поза горизонтом — відкидаємо.
    today = now.replace(tzinfo=None).date()
    horizon = today + timedelta(days=75)

    def _fix(d: str):
        try:
            y, mo, dd = (int(x) for x in d.split("-"))
        except Exception:
            return None
        from datetime import date as _date
        try:
            cand = _date(y, mo, dd)
        except Exception:
            cand = None
        if cand and today <= cand <= horizon:
            return cand.strftime("%Y-%m-%d")
        # рік/місяць зламані → шукаємо цей номер дня у поточному або наступних місяцях
        ym = (today.year, today.month)
        for _ in range(3):
            try:
                c2 = _date(ym[0], ym[1], dd)
            except Exception:
                c2 = None
            if c2 and today <= c2 <= horizon:
                return c2.strftime("%Y-%m-%d")
            ym = (ym[0] + 1, 1) if ym[1] == 12 else (ym[0], ym[1] + 1)
        return None

    clean = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        d = str(it.get("date", "")).strip()
        t = _norm_type(it.get("type"))
        if not (re.match(r"^\d{4}-\d{2}-\d{2}$", d) and t):
            continue
        fixed = _fix(d)
        if not fixed:
            K.log(TAG, f"дата поза горизонтом, відкинуто: {d}")
            continue
        if fixed != d:
            K.log(TAG, f"виправив дату від AI: {d} -> {fixed}")
        clean[fixed] = t

    if not clean:
        K.log(TAG, "AI не розпарсив — локальний regex")
        for it in _local_parse(text):
            clean[it["date"]] = it["type"]

    return [{"date": d, "type": t} for d, t in sorted(clean.items())]


# ─── ПОДІЇ ───────────────────────────────────────────────────────────────────

def _events_for(shift: dict, with_sleep: bool) -> list:
    """Список подій для однієї зміни: (title, start, end)."""
    d = shift["date"]
    out = []
    if shift["type"] == "early":
        start = K.parse_dt(d, EARLY_START)
        end = K.parse_dt(d, EARLY_END)
        out.append(("☀️ Рання зміна", start, end))
        out.append(("🚗 Виїзд на роботу", start - timedelta(minutes=30), start))
        if with_sleep:
            prev = (start - timedelta(days=1)).strftime("%Y-%m-%d")
            out.append(("😴 Сон (перед ранньою)",
                        K.parse_dt(prev, "21:30"), K.parse_dt(d, "05:15")))
    else:
        start = K.parse_dt(d, NIGHT_START)
        nxt = (start + timedelta(days=1)).strftime("%Y-%m-%d")
        end = K.parse_dt(nxt, NIGHT_END)
        out.append(("🌙 Нічна зміна", start, end))
        out.append(("🚗 Виїзд на роботу", start - timedelta(minutes=30), start))
        if with_sleep:
            out.append(("😴 Сон після нічної",
                        K.parse_dt(nxt, "07:00"), K.parse_dt(nxt, "14:00")))
    return out


def offer(text: str, chat_id=None) -> bool:
    """Показує превʼю розпарсеного графіка з кнопками підтвердження."""
    shifts = parse_schedule(text)
    if not shifts:
        K.send_card(
            "🤔 <b>Не зрозумів графік</b>\n\nНапиши так:\n"
            "<code>/зміни нічні 5-9, рання 12-16</code>\n"
            "<code>/зміни рання 1-5, нічна 8-12 серпня</code>",
            tag=TAG, chat_id=chat_id)
        return False

    already = K.load(CREATED_FILE, default={}) or {}
    dupes = [s for s in shifts if already.get(s["date"]) == s["type"]]
    fresh = [s for s in shifts if already.get(s["date"]) != s["type"]]
    if not fresh:
        K.send_card(f"✅ Усі {len(shifts)} змін уже є в календарі — нічого додавати.",
                    tag=TAG, chat_id=chat_id)
        return False

    pid = _store.put({"shifts": fresh})

    early = [s for s in fresh if s["type"] == "early"]
    night = [s for s in fresh if s["type"] == "night"]

    def _fmt(items):
        return ", ".join(
            f"{datetime.strptime(s['date'], '%Y-%m-%d').strftime('%d.%m')}"
            f"({UA_DAYS[datetime.strptime(s['date'], '%Y-%m-%d').weekday()]})"
            for s in items)

    lines = ["📋 <b>ГРАФІК ЗМІН — ПЕРЕВІР ПЕРЕД СТВОРЕННЯМ</b>",
             "━━━━━━━━━━━━━━━━━━━━"]
    if early:
        lines.append(f"☀️ <b>Рання</b> ({len(early)}): {_fmt(early)}")
        lines.append(f"    <i>{EARLY_START}–{EARLY_END}</i>")
    if night:
        lines.append(f"🌙 <b>Нічна</b> ({len(night)}): {_fmt(night)}")
        lines.append(f"    <i>{NIGHT_START}–{NIGHT_END}</i>")
    if dupes:
        lines.append(f"\n⏭ Пропущу {len(dupes)} — вже створені раніше")
    n_ev = sum(len(_events_for(s, True)) for s in fresh)
    lines.append("")
    lines.append("😴 <b>Блоки сну</b>: перед ранньою 21:30–05:15, "
                 "після нічної 07:00–14:00")
    lines.append("🚗 Плюс «виїзд на роботу» за 30 хв до кожної зміни")
    lines.append(f"\n📌 Разом буде створено <b>{n_ev}</b> подій "
                 f"({len(fresh)} змін)")

    kb = [
        [{"text": "✅ Створити всі + сон", "callback_data": f"shf_ok_{pid}"}],
        [{"text": "📋 Тільки зміни (без сну)", "callback_data": f"shf_only_{pid}"}],
        [{"text": "❌ Скасувати", "callback_data": f"shf_no_{pid}"}],
    ]
    return K.send_card("\n".join(lines), kb, tag=TAG, chat_id=chat_id)


def _apply(pid: str, with_sleep: bool) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    shifts = p.get("shifts") or []
    created, failed = 0, 0
    done_dates = []
    for s in shifts:
        ok_any = False
        for title, start, end in _events_for(s, with_sleep):
            res = K.calendar_event(title, start, end,
                                   description="— графік створено AI-асистентом",
                                   force=True)
            if res.get("ok"):
                created += 1
                ok_any = True
            else:
                failed += 1
                K.log(TAG, f"fail {title} {s['date']}: {res.get('error')}")
        if ok_any:
            K.update_key(CREATED_FILE, s["date"], s["type"])
            done_dates.append(s["date"])
    if not created:
        return {"ok": False, "error": "calendar_error"}
    _store.drop(pid)
    return {"ok": True, "created": created, "failed": failed,
            "shifts": len(done_dates), "sleep": with_sleep}


def do_ok(pid: str) -> dict:
    return _apply(pid, with_sleep=True)


def do_only(pid: str) -> dict:
    return _apply(pid, with_sleep=False)


def do_no(pid: str) -> dict:
    _store.drop(pid)
    return {"ok": True}


if __name__ == "__main__":
    import sys, json as _j
    txt = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or "нічні 5-9, рання 12-16"
    if "--local" in sys.argv:
        print(_j.dumps(_local_parse(txt), ensure_ascii=False, indent=1))
    elif "--parse" in sys.argv:
        print(_j.dumps(parse_schedule(txt), ensure_ascii=False, indent=1))
    else:
        print("sent:", offer(txt))
