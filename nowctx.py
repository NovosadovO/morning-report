#!/usr/bin/env python3
"""
nowctx.py — «зараз»: точна дата, день тижня, час і ДЕ Олег насправді.

Скарга Олега (23.08, зі скріншотом):
  «Налаштуй АІ так, щоб він завжди був у курсі, який день, що я маю на даний
   момент, день, годину. Знав де я. АІ сьогодні написав що я вдома, а насправді
   я на роботі.»

Реальний текст бота о 08:35: «З огляду на те, що ти зараз вдома і маєш вільний
ранок…» — а Олег був на зміні.

КОРІНЬ ПРОБЛЕМИ (знайдено в коді, не припущення):
  context.get_status() при today="free" і 7<=h<22 повертає "home", а
  STATUS_LABELS["home"] = «вдома, вільний час».
  Але today="free" виставляється У ДВОХ ЗОВСІМ РІЗНИХ ВИПАДКАХ:
    1) календар відповів і події зміни справді немає  → Олег дійсно вільний;
    2) немає токена / помилка API / подія названа інакше → МИ НЕ ЗНАЄМО.
  Другий випадок молча ставав «вдома, вільний час». Плюс
  message_generator._get_real_status() мав фолбек «вдома (невизначено)».
  Тобто «вдома» — це було ПРИПУЩЕННЯ, яке AI подавав як факт. Пряме
  порушення принципу «не вигадувати».

ЩО РОБИТЬ ЦЕЙ МОДУЛЬ
  1. Дає точний час: дата, день тижня, година:хвилина, Europe/Bratislava,
     плюс частину дня і номер тижня — щоб AI не гадав «ранок/вечір».
  2. Дає локацію з ТРЬОМА станами: work / home / unknown (+ after_night).
     unknown — це повноцінна відповідь, а не привід вигадати «вдома».
  3. Пріоритет джерел: ручне слово Олега (свіже) → Google Calendar →
     графік змін → unknown. Джерело завжди видно в тексті промпту.
  4. При unknown у промпт іде ЗАБОРОНА стверджувати локацію.

Інжектиться в monitor._gem_post → бачать УСІ модулі одразу.

API:
    block()            -> str    # блок для промпту
    stamp()            -> str    # «неділя, 23 серпня 2026, 09:14»
    where()            -> dict   # {state, label, source, sure}
    set_manual(loc)    -> bool   # 'robota' | 'doma'
    report()           -> str    # /де
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TAG = "nowctx"
MARK = "⁣NOWCTX⁣"          # невидимий маркер: захист від подвійного інжекту
WHERE_FILE = "whereami.json"          # ручна локація з таймстампом
MANUAL_TTL_H = 10                     # скільки годин слово Олега вважаємо свіжим

WD = ["понеділок", "вівторок", "середа", "четвер",
      "п'ятниця", "субота", "неділя"]
MON = ["січня", "лютого", "березня", "квітня", "травня", "червня", "липня",
       "серпня", "вересня", "жовтня", "листопада", "грудня"]


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


def now():
    """Локальний час Кошице. Europe/Bratislava = UTC+2 літом, UTC+1 зимою."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Bratislava"))
    except Exception:
        return datetime.now(timezone.utc) + timedelta(hours=2)


def part_of_day(h=None):
    h = now().hour if h is None else int(h)
    if h < 5:
        return "глибока ніч"
    if h < 9:
        return "ранній ранок"
    if h < 12:
        return "ранок"
    if h < 15:
        return "обідній час"
    if h < 18:
        return "друга половина дня"
    if h < 22:
        return "вечір"
    return "ніч"


def stamp():
    n = now()
    return (WD[n.weekday()] + ", " + str(n.day) + " " + MON[n.month - 1] + " "
            + str(n.year) + ", " + n.strftime("%H:%M"))


# ─── РУЧНА ЛОКАЦІЯ ───────────────────────────────────────────────────────────

def _load_manual():
    try:
        import storage
        d = storage.load(WHERE_FILE, default={}) or {}
        if isinstance(d, dict) and d.get("loc") in ("robota", "doma"):
            return d
    except Exception as e:
        _log("load manual error: " + str(e))
    return {}


def set_manual(loc):
    """Олег сказав прямо, де він. Це найвищий пріоритет на MANUAL_TTL_H годин."""
    loc = str(loc or "").strip().lower()
    if loc in ("robota", "робота", "work", "на роботі"):
        loc = "robota"
    elif loc in ("doma", "дома", "вдома", "home"):
        loc = "doma"
    else:
        return False
    try:
        import storage
        storage.save(WHERE_FILE, {"loc": loc, "ts": now().isoformat(timespec="seconds")})
        _log("локація вручну: " + loc)
        return True
    except Exception as e:
        _log("set_manual error: " + str(e))
        return False


def _manual_fresh():
    d = _load_manual()
    if not d:
        return None
    try:
        ts = datetime.fromisoformat(str(d.get("ts")))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=now().tzinfo)
        age_h = (now() - ts).total_seconds() / 3600.0
        if age_h <= MANUAL_TTL_H:
            return {"loc": d["loc"], "age_h": round(age_h, 1)}
    except Exception:
        return None
    return None


# ─── КАЛЕНДАР / ЗМІНА ────────────────────────────────────────────────────────

def _calendar_shift():
    """Повертає (shift, calendar_ok). shift: early|night|after_night|free|None.
    calendar_ok=False означає «ми НЕ знаємо», а не «вільний»."""
    try:
        import context as _c
        tok = None
        try:
            tok = _c._get_token()
        except Exception:
            tok = None
        info = _c.get_shift_from_calendar() or {}
        shift = info.get("today") or "free"
        # без токена «free» — це не факт, а відсутність даних
        return shift, bool(tok)
    except Exception as e:
        _log("calendar error: " + str(e))
        return None, False


def where():
    """{state, label, source, sure}
    state: 'work' | 'home' | 'after_night' | 'unknown'"""
    n = now()
    h = n.hour

    m = _manual_fresh()
    if m:
        if m["loc"] == "robota":
            return {"state": "work", "sure": True,
                    "source": "Олег сам сказав " + str(m["age_h"]) + " год тому",
                    "label": "НА РОБОТІ (сказав сам)"}
        return {"state": "home", "sure": True,
                "source": "Олег сам сказав " + str(m["age_h"]) + " год тому",
                "label": "вдома (сказав сам)"}

    shift, cal_ok = _calendar_shift()

    if shift == "night":
        if h >= 18 or h < 6:
            return {"state": "work", "sure": True, "source": "Google Calendar",
                    "label": "НА НІЧНІЙ ЗМІНІ (18:00-06:00) — фізично на роботі"}
        if 6 <= h < 10:
            return {"state": "after_night", "sure": True, "source": "Google Calendar",
                    "label": "щойно з нічної зміни — вдома, має спати"}
        return {"state": "home", "sure": True, "source": "Google Calendar",
                "label": "вдома, ввечері нічна зміна (початок ~17:30)"}

    if shift == "early":
        if 6 <= h < 18:
            return {"state": "work", "sure": True, "source": "Google Calendar",
                    "label": "НА РАННІЙ ЗМІНІ (06:00-18:00) — фізично на роботі"}
        if 4 <= h < 6:
            return {"state": "home", "sure": True, "source": "Google Calendar",
                    "label": "вдома, збирається на ранню зміну"}
        return {"state": "home", "sure": True, "source": "Google Calendar",
                "label": "вдома після ранньої зміни"}

    if shift == "after_night":
        return {"state": "after_night", "sure": True, "source": "Google Calendar",
                "label": "після нічної зміни — вдома, відновлюється"}

    # Календар відповів, події зміни немає. АЛЕ (скріншот Олега 23.08, 10:03):
    # він був НА РОБОТІ, а бот написав «вдома, вільний день». Причина — Олег
    # не додає зміни в календар. Тому «немає події» НЕ доказ, що він вдома:
    # це слабке припущення, і подавати його як факт заборонено.
    if shift == "free" and cal_ok:
        if 0 <= h < 7 or h >= 23:
            return {"state": "home", "sure": True, "source": "Google Calendar",
                    "label": "найпевніше вдома, нічний час (зміни в календарі немає)"}
        return {"state": "unknown", "sure": False,
                "source": "у календарі зміни немає, але Олег не завжди її додає",
                "label": ("НЕ ПІДТВЕРДЖЕНО: у календарі зміни немає, але це НЕ "
                          "означає, що Олег вдома — він міг просто не додати зміну")}

    # ГОЛОВНЕ: календар недоступний → НЕ вигадуємо «вдома»
    return {"state": "unknown", "sure": False,
            "source": "календар недоступний, ручної позначки немає",
            "label": "НЕВІДОМО, де саме Олег зараз"}


# ─── БЛОК ДЛЯ ПРОМПТУ ────────────────────────────────────────────────────────

def block():
    n = now()
    w = where()
    lines = [
        "\n\n━━━ ЗАРАЗ (єдине джерело правди про час і місце) ━━━",
        "• Дата й час: " + stamp() + " (" + part_of_day(n.hour)
        + "), Кошице, Словаччина. Тиждень " + n.strftime("%V") + " року.",
        "• Де Олег: " + w["label"] + ". Джерело: " + w["source"] + ".",
    ]
    if w["state"] == "unknown":
        lines.append(
            "⛔ КРИТИЧНО: локація НЕ підтверджена. ЗАБОРОНЕНО писати «ти вдома», "
            "«вільний ранок», «відпочиваєш», «ти на роботі», «ти спиш» — це буде "
            "вигадка. Або взагалі не згадуй, де він, і пиши по суті даних, або "
            "одним коротким рядком спитай: «Ти зараз на зміні чи вдома?». "
            "Порада мусить працювати в обох випадках.")
    elif w["state"] == "work":
        lines.append(
            "⛔ Олег ФІЗИЧНО НА РОБОТІ. ЗАБОРОНЕНО писати «ти вдома», «вільний "
            "ранок/вечір», «відпочиваєш», «маєш вільний час», радити пробіжку, "
            "готування чи справи, які на зміні неможливі. Пиши як людині на "
            "зміні: коротко, те, що реально можна зробити в перерві або після "
            "зміни (і скажи прямо «після зміни»).")
    elif w["state"] == "after_night":
        lines.append(
            "⛔ Олег щойно з нічної зміни. Головне зараз — сон і відновлення. "
            "ЗАБОРОНЕНО бадьорити на активність і навантаження.")
    lines.append(
        "• Час використовуй буквально: «сьогодні», «зараз», «через 2 години» "
        "рахуй від часу вище, а не від абстрактного дня. Не пиши «доброго "
        "ранку» о " + n.strftime("%H:%M") + ", якщо це не ранок.")
    return "\n".join(lines)


def report():
    """Для команди /де — що бот думає про час і місце ПРЯМО зараз."""
    w = where()
    m = _manual_fresh()
    out = ["📍 <b>ДЕ Я ЗАРАЗ (як це бачить бот)</b>\n",
           "🕒 " + stamp() + " · " + part_of_day(),
           "📌 " + w["label"],
           "🔎 джерело: " + w["source"]]
    if w["state"] == "unknown":
        out.append("\n⚠️ Локація не підтверджена — AI НЕ буде вигадувати, "
                   "що ти вдома. Скажи /робота або /дома, і він знатиме точно.")
    if m:
        out.append("\n✍️ ручна позначка: " + m["loc"] + " (" + str(m["age_h"])
                   + " год тому, діє " + str(MANUAL_TTL_H) + " год)")
    return "\n".join(out)


# ─── ІНЖЕКТ ──────────────────────────────────────────────────────────────────

def inject(body_bytes, tag=""):
    """Додає блок «зараз» у не-JSON промпт. Ідемпотентно."""
    try:
        import json as _js
        b = _js.loads(body_bytes.decode())
        p = b["contents"][0]["parts"][0]["text"]
        if MARK in p:
            return body_bytes
        try:
            import ai_brain
            if ai_brain.is_json_prompt(p):
                return body_bytes
        except Exception:
            pass
        b["contents"][0]["parts"][0]["text"] = p + block() + "\n" + MARK
        _log("🕒 час і локація додані → " + str(tag))
        return _js.dumps(b).encode()
    except Exception as e:
        _log("inject error: " + str(e))
        return body_bytes
