#!/usr/bin/env python3
"""
ТРЕНДИ ЗДОРОВ'Я  (healthtrend.py)

Не «сьогодні 8855 кроків», а КУДИ ВОНО ЙДЕ: вага, сон, кроки — 7 днів проти
попередніх 7, плюс зв'язок зі змінами на роботі (нічні vs решта).

Три речі, які модуль робить сам:
  • ЗАПИСУЄ  — щоденний зріз тренду в health_trend.json (щоб потім було з чим
    порівнювати, і щоб звіт не вигадував динаміку з повітря);
  • СТВОРЮЄ  — якщо ваги немає STALE_WEIGHT_DAYS днів, кладе в календар
    «⚖️ Зважитись» на завтрашній ранок і каже про це постфактум;
  • ЗАПИТУЄ  — при реальному зсуві (вага ±WEIGHT_JUMP кг, недосип
    SLEEP_DEBT год/ніч) пише пряме питання з кнопками react.py.

Джерела: health.json (steps, sleep_hours, weight_kg, hr_avg, hrv),
weight.json — запасне джерело ваги. Немає даних → модуль МОВЧИТЬ:
жодних нулів, жодних «схоже, ти...».

Команда: /тренди (/trend, /здоровя, /динаміка)
"""

import re
from datetime import datetime, timedelta

import ai_kit as K

TAG = "htrend"

SNAP_FILE = "health_trend.json"      # {YYYY-MM-DD: зріз}
STORE_FILE = "htrend_store.json"
SENT_FILE = "htrend_sent.json"
SCAN_STATE = "htrend_scan.json"

SCAN_GAP_MIN = 300           # раз на 5 годин достатньо
WINDOW = 7                   # вікно порівняння, днів
MIN_POINTS = 4               # менше точок у вікні — тренд не рахуємо
STALE_WEIGHT_DAYS = 10       # скільки днів без ваги — вже питання
WEIGHT_JUMP = 1.0            # кг зсуву між вікнами, від якого варто казати
SLEEP_DEBT = 6.5             # менше цього в середньому — недосип
STEPS_DROP_PCT = 25.0        # падіння активності, від якого питаємо

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=5)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ─── ДАНІ ────────────────────────────────────────────────────────────────────

def _health() -> dict:
    d = K.load("health.json", default={}) or {}
    return {k: v for k, v in d.items()
            if _DATE_RE.match(str(k)) and isinstance(v, dict)}


def _num(v):
    try:
        f = float(str(v).replace(",", "."))
        return f if f > 0 else None
    except Exception:
        return None


def series(field: str, days: int = 60) -> dict:
    """{date: value} за останні N днів. Тільки реальні значення."""
    cutoff = (K.now().date() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = {}
    for date, rec in _health().items():
        if date < cutoff:
            continue
        v = _num(rec.get(field))
        if v is not None:
            out[date] = v
    if field == "weight_kg":
        for date, v in (K.load("weight.json", default={}) or {}).items():
            if _DATE_RE.match(str(date)) and date >= cutoff and date not in out:
                n = _num(v)
                if n is not None:
                    out[date] = n
    return dict(sorted(out.items()))


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def window_avg(field: str, back: int = 0):
    """Середнє за вікно: back=0 — останні 7 днів, back=1 — попередні 7."""
    s = series(field)
    if not s:
        return None, 0
    end = K.now().date() - timedelta(days=WINDOW * back)
    start = end - timedelta(days=WINDOW)
    vals = [v for d, v in s.items()
            if start.strftime("%Y-%m-%d") < d <= end.strftime("%Y-%m-%d")]
    return _avg(vals), len(vals)


def trend(field: str):
    """{'now','prev','diff','n'} або None, якщо даних мало."""
    cur, n1 = window_avg(field, 0)
    prev, n2 = window_avg(field, 1)
    if cur is None or n1 < MIN_POINTS:
        return None
    res = {"now": cur, "prev": prev, "n": n1, "diff": None}
    if prev is not None and n2 >= MIN_POINTS:
        res["diff"] = round(cur - prev, 2)
    return res


def last_point(field: str):
    s = series(field, days=120)
    if not s:
        return None, None
    d = max(s)
    return d, s[d]


def days_since(field: str):
    d, _ = last_point(field)
    if not d:
        return None
    try:
        return (K.now().date() - datetime.strptime(d, "%Y-%m-%d").date()).days
    except Exception:
        return None


# ─── ЗМІНИ vs РЕШТА ──────────────────────────────────────────────────────────

def shift_split(field: str, days: int = 30):
    """Середнє в дні нічних змін проти решти. None, якщо графіка немає."""
    try:
        import ai_kit as _K
        smap = _K.shift_map(days)
    except Exception as e:
        K.log(TAG, f"shift_map недоступна: {e}")
        return None
    if not smap:
        return None
    s = series(field, days)
    night, other = [], []
    for d, v in s.items():
        kind = str(smap.get(d) or "")
        if not kind:
            continue
        (night if "ніч" in kind or "night" in kind.lower() else other).append(v)
    if len(night) < 3 or len(other) < 3:
        return None
    return {"night": _avg(night), "other": _avg(other),
            "n_night": len(night), "n_other": len(other)}


# ─── ТЕКСТ ───────────────────────────────────────────────────────────────────

def line() -> str:
    """Один рядок для звіту. Порожньо = немає достовірних даних."""
    bits = []
    w = trend("weight_kg")
    if w:
        d = f" ({w['diff']:+.1f} кг)" if w["diff"] is not None else ""
        bits.append(f"вага <b>{w['now']:.1f} кг</b>{d}")
    sl = trend("sleep_hours")
    if sl:
        d = f" ({sl['diff']:+.1f} г)" if sl["diff"] is not None else ""
        bits.append(f"сон {sl['now']:.1f} г{d}")
    st = trend("steps")
    if st:
        d = f" ({st['diff']:+.0f})" if st["diff"] is not None else ""
        bits.append(f"кроки {st['now']:.0f}{d}")
    if not bits:
        return ""
    return "🩺 <b>7 днів</b>: " + " · ".join(bits)


def report() -> str:
    """/тренди — повна динаміка."""
    parts = ["🩺 <b>ТРЕНДИ ЗДОРОВ'Я</b> (7 днів проти попередніх 7)",
             "━━━━━━━━━━━━━━━━━━━━"]
    shown = 0

    spec = [("weight_kg", "⚖️ Вага", "кг", 1, True),
            ("sleep_hours", "😴 Сон", "г", 1, False),
            ("steps", "👟 Кроки", "", 0, False),
            ("hrv", "💓 HRV", "", 0, False),
            ("hr_avg", "❤️ Пульс спокою", "", 0, True)]
    for field, name, unit, dec, good_down in spec:
        t = trend(field)
        if not t:
            continue
        shown += 1
        u = (" " + unit) if unit else ""
        row = f"{name}: <b>{t['now']:.{dec}f}{u}</b>"
        if t["diff"] is not None:
            mark = "📉" if (t["diff"] < 0) == (not good_down) else "📈"
            if good_down:
                mark = "📉" if t["diff"] < 0 else "📈"
            row += f"  {mark} {t['diff']:+.{dec}f}{u} проти минулого тижня"
        else:
            row += "  <i>(нема з чим порівняти)</i>"
        row += f"  <i>· {t['n']} дн. з даними</i>"
        parts.append(row)

    if not shown:
        return ("🩺 <b>ТРЕНДИ ЗДОРОВ'Я</b>\n\nДостовірних даних за останні "
                f"{WINDOW * 2} днів замало — тренд рахувати нема з чого. "
                "Синхронізуй здоров'я або скажи вагу, і я почну вести динаміку.")

    dw = days_since("weight_kg")
    if dw is not None and dw >= STALE_WEIGHT_DAYS:
        parts.append(f"\n⚠️ Вагу востаннє бачив {dw} дн. тому — тренд ваги вже "
                     f"застарілий.")

    sp = shift_split("sleep_hours")
    if sp:
        parts.append(f"\n🌙 Сон у дні нічних: <b>{sp['night']:.1f} г</b> "
                     f"({sp['n_night']} дн.) проти {sp['other']:.1f} г "
                     f"в інші ({sp['n_other']} дн.)")
    sps = shift_split("steps")
    if sps:
        parts.append(f"👟 Кроки в нічні: <b>{sps['night']:.0f}</b> проти "
                     f"{sps['other']:.0f} в інші")

    return "\n".join(parts)[:3900]


# ─── ЗРІЗ У ФАЙЛ ─────────────────────────────────────────────────────────────

def snapshot() -> dict:
    snap = {}
    for f in ("weight_kg", "sleep_hours", "steps", "hrv", "hr_avg"):
        t = trend(f)
        if t:
            snap[f] = {"avg": t["now"], "diff": t["diff"], "n": t["n"]}
    if snap:
        K.update_key(SNAP_FILE, K.now().strftime("%Y-%m-%d"),
                     {"ts": K.now().isoformat(), **snap})
    return snap


# ─── ІНІЦІАТИВА ──────────────────────────────────────────────────────────────

def _kb(pid, kind="health"):
    try:
        import react as R
        return R.keyboard(kind)
    except Exception:
        return [[{"text": "👌 Прийняв", "callback_data": f"ht_ok_{pid}"}]]


def _muted(key) -> bool:
    for mod, fn in (("dismissed", "is_muted"), ("react", "is_closed")):
        try:
            m = __import__(mod)
            if getattr(m, fn)("health", key):
                return True
        except Exception:
            continue
    return False


def _ask(text, key, kind="health") -> bool:
    if _dedup.seen("ht", key) or _muted(key):
        return False
    pid = _store.put({"key": key, "kind": kind})
    if K.send_card(text, _kb(pid, kind), tag=TAG):
        _dedup.mark("ht", key)
        return True
    return False


def _weigh_event() -> bool:
    when = (K.now() + timedelta(days=1)).replace(hour=7, minute=0,
                                                 second=0, microsecond=0)
    try:
        res = K.calendar_event("⚖️ Зважитись", when, when + timedelta(minutes=15),
                               description="Тренд ваги застарів — бот нагадує.")
    except Exception as e:
        K.log(TAG, f"calendar error: {e}")
        return False
    return bool(res)


def run(force: bool = False) -> int:
    if not force and not K.rate_ok(SCAN_STATE, SCAN_GAP_MIN):
        return 0
    K.rate_mark(SCAN_STATE)

    snap = snapshot()
    if not snap:
        K.log(TAG, "даних для тренду немає — мовчу")
        return 0

    sent = 0

    # 1. Вага застаріла → подія в календар + питання
    dw = days_since("weight_kg")
    if dw is not None and dw >= STALE_WEIGHT_DAYS:
        key = f"weigh_{K.now().strftime('%Y-%W')}"
        if not _dedup.seen("ht", key) and not _muted(key):
            created = _weigh_event()
            txt = (f"⚖️ <b>Вага не оновлювалась {dw} дн.</b>\n"
                   f"Без свіжої цифри тренд ваги — це вигадка, тому я його "
                   f"більше не рахую.\n")
            txt += ("Поклав завтра на 7:00 в календар «Зважитись».\n"
                    if created else "")
            txt += "Скажи вагу цифрою — і динаміка знову жива."
            if _ask(txt, key):
                sent += 1

    # 2. Реальний зсув ваги
    w = trend("weight_kg")
    if w and w["diff"] is not None and abs(w["diff"]) >= WEIGHT_JUMP:
        key = f"wjump_{K.now().strftime('%Y-%W')}"
        direction = "вниз" if w["diff"] < 0 else "вгору"
        goal = " До 78 кг лишилось " + f"{w['now'] - 78:.1f} кг." if w["now"] > 78 else ""
        txt = (f"⚖️ <b>Вага пішла {direction}: {w['diff']:+.1f} кг за тиждень</b>\n"
               f"Зараз середнє {w['now']:.1f} кг проти {w['prev']:.1f} кг.{goal}\n\n"
               f"Що змінилось цього тижня — їжа, рух чи зміни на роботі? "
               f"Від цього залежить, що варто тримати, а що прибрати.")
        if _ask(txt, key):
            sent += 1

    # 3. Недосип
    sl = trend("sleep_hours")
    if sl and sl["now"] < SLEEP_DEBT:
        key = f"sleep_{K.now().strftime('%Y-%W')}"
        sp = shift_split("sleep_hours")
        extra = ""
        if sp and sp["night"] < sp["other"]:
            extra = (f"\nУ дні нічних спиш {sp['night']:.1f} г проти "
                     f"{sp['other']:.1f} г в інші — провал саме там.")
        txt = (f"😴 <b>Середній сон за тиждень: {sl['now']:.1f} г</b> — "
               f"нижче {SLEEP_DEBT} г.{extra}\n\n"
               f"Це через зміни чи ти сам лягаєш пізно? Якщо через зміни — "
               f"можу планувати денний сон у вільні дні.")
        if _ask(txt, key):
            sent += 1

    # 4. Провал активності
    st = trend("steps")
    if (st and st["prev"] and st["diff"] is not None
            and st["prev"] > 3000
            and (-st["diff"] / st["prev"] * 100) >= STEPS_DROP_PCT):
        key = f"steps_{K.now().strftime('%Y-%W')}"
        pct = -st["diff"] / st["prev"] * 100
        txt = (f"👟 <b>Активність просіла на {pct:.0f}%</b>\n"
               f"{st['now']:.0f} кроків/день проти {st['prev']:.0f} минулого "
               f"тижня.\n\nЦе зміни/погода чи просто тиждень випав? "
               f"Якщо друге — скажи, і поставлю вікно для бігу у вільний день.")
        if _ask(txt, key):
            sent += 1

    if sent:
        K.log(TAG, f"карточок надіслано: {sent}")
    return sent


def handle(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "text": "Дані кнопки застаріли."}
    _store.drop(pid)
    return {"ok": True, "text": "👌 Записав."}


if __name__ == "__main__":
    print(report())
