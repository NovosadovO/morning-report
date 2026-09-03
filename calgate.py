# -*- coding: utf-8 -*-
"""calgate.py — у календар нічого не пишеться без дозволу Олега.

Правило: бот НЕ створює подію сам. Він питає — «📅 Так, запиши / 🕒 Інший час /
🚫 Не треба» — і пише лише після натискання. Виняток один: Олег сам попросив
(тоді force=True з обробника його ж повідомлення).

Сміття взагалі не доходить до питання: реклама, розсилки, галочки звичок,
рутина (зміна/сон/будильник) і надто дрібні записи відкидаються тихо.
"""

import threading

TAG = "calgate"
_local = threading.local()

# Рутина й побутовий шум — про це не питаємо і не пишемо.
_JUNK = ("зміна", "shift", "нічна", "рання", "сон", "будильник",
         "вода", "душ", "сауна", "вітамін", "зарядк", "розтяжк",
         "щоденник", "трекер", "чекліст", "рутин", "виконано",
         "перевірити невиконані", "нагадування про нагадування")
# Ознаки справді важливого — таке варто запитати.
_WORTH = ("зустріч", "meeting", "співбесід", "interview", "лікар", "doctor",
          "termin", "рахунок", "invoice", "оплат", "платіж", "дедлайн",
          "deadline", "договір", "виза", "віза", "паспорт", "техогляд",
          "страхов", "податк", "переговор", "дзвінок", "call", "презентац",
          "тренуванн", "пробіжк", "біг", "забіг", "старт", "переліт", "потяг",
          "рейс", "готель", "відпустк", "школ", "сад", "день народж",
          "інвест", "interfin", "банк", "нотар", "суд", "ремонт", "сервіс")


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


ALLOW_TTL = 30  # с — дозвіл живе секунди, щоб не «залипав» на потім


def allow_once() -> None:
    """Дозвіл на ОДИН запис — ставиться після натискання «📅 Так, запиши».

    Живе лише ALLOW_TTL секунд: якщо запис не дійшов до календаря, дозвіл
    згасає сам і наступну подію бот знову піде питати.
    """
    import time as _t
    _local.allow_at = _t.monotonic()


def _take_allow() -> bool:
    import time as _t
    at = getattr(_local, "allow_at", None)
    _local.allow_at = None
    if at is None:
        return False
    return (_t.monotonic() - at) <= ALLOW_TTL


ASK_STATE = "writegate_asks.json"
_MAX_ASK_PER_DAY = 8


def _ask_budget_ok() -> bool:
    """Ворота не мають перетворитись на спам: не більше _MAX_ASK_PER_DAY питань."""
    try:
        import ai_kit as K
        day = K.now().strftime("%Y-%m-%d")
        st = K.load(ASK_STATE, default={}) or {}
        if not isinstance(st, dict):
            st = {}
        n = int(st.get(day) or 0)
        if n >= _MAX_ASK_PER_DAY:
            return False
        K.update_key(ASK_STATE, day, n + 1)
        return True
    except Exception as e:
        _log("budget skip: " + str(e))
        return True


def worth_asking(summary: str, description: str = "") -> bool:
    """Чи варте це того, щоб узагалі питати Олега."""
    s = str(summary or "").strip()
    if len(s) < 4:
        return False
    low = (s + " " + str(description or "")).lower()
    try:
        import askme as A
        if A.is_promo(low) or A._is_tracker(s):
            return False
    except Exception:
        pass
    if any(w in low for w in _JUNK):
        return False
    # Усе інше — ПИТАЄМО. Вимога Олега: жодного запису без його «так»,
    # тому сумнівне не відкидаємо тихо, а показуємо йому кнопками.
    return True


def gate(summary: str, start_dt=None, end_dt=None, description: str = "",
         force: bool = False, source: str = "") -> dict:
    """None → писати можна. dict → писати НЕ можна (питання вже надіслано)."""
    if force or _take_allow():
        return None
    s = str(summary or "")
    if not worth_asking(s, description):
        _log("не вартує календаря — тихо відкидаю: " + s[:70])
        return {"ok": False, "error": "calgate: not worth"}
    when = ""
    try:
        when = start_dt.strftime("%d.%m о %H:%M")
    except Exception:
        pass
    q = ("📅 Хочу записати в календар:\n\n« " + s[:110] + " »" +
         (("\n🕐 " + when) if when else "") +
         (("\n\n" + str(description)[:200]) if description else "") +
         "\n\nЗаписати?")
    meta = {"summary": s[:110], "desc": str(description or "")[:300],
            "minutes": 60}
    try:
        meta["start"] = start_dt.isoformat()
        if end_dt:
            meta["minutes"] = max(
                15, int((end_dt - start_dt).total_seconds() / 60))
    except Exception:
        pass
    try:
        import askme as A
        key = "cal|" + "".join(ch for ch in s.lower()
                               if ch.isalnum() or ch == " ")[:60].strip()
        if A.answered(key):
            _log("уже відповідав про цю подію — не питаю вдруге: " + s[:60])
            return {"ok": False, "error": "calgate: already answered"}
        A.ask(q, kind="plan", key=key, meta=meta,
              tag="MSG_CAL_ASK_" + str(source or "bot"))
        _log("запитав дозвіл на запис: " + s[:70])
    except Exception as e:
        _log("ask error: " + str(e))
    return {"ok": False, "pending": True,
            "error": "calgate: чекаю підтвердження Олега"}


# ─── ЗАПИС КУДИ-ЗАВГОДНО, НЕ ТІЛЬКИ В КАЛЕНДАР ───────────────────────────────
# Вимога Олега: питати ЗАВЖДИ перед будь-яким записом — нагадування, нотатка,
# список покупок, задача. Дія виконується лише після натискання кнопки.

_WRITE_LABEL = {
    "reminder": "🔔 Хочу поставити нагадування",
    "note": "📝 Хочу записати нотатку",
    "shopping": "🛒 Хочу додати в список покупок",
    "task": "✅ Хочу поставити задачу",
    "write": "✍️ Хочу записати",
}


def gate_write(kind: str, title: str, start_dt=None, detail: str = "",
               force: bool = False, source: str = "",
               calendar: bool = True) -> dict:
    """None → писати можна. dict → писати НЕ можна (питання вже надіслано).

    kind: reminder | note | shopping | task | write
    """
    if force or _take_allow():
        return None
    s = str(title or "").strip()
    if not worth_asking(s, detail):
        _log("не вартує запису — тихо відкидаю: " + s[:70])
        return {"ok": False, "error": "calgate: not worth"}
    when = ""
    try:
        when = start_dt.strftime("%d.%m о %H:%M")
    except Exception:
        pass
    head = _WRITE_LABEL.get(str(kind), _WRITE_LABEL["write"])
    q = (head + ":\n\n« " + s[:110] + " »" +
         (("\n🕐 " + when) if when else "") +
         (("\n\n" + str(detail)[:200]) if detail else "") +
         "\n\nПоставити?")
    meta = {"summary": s[:110], "desc": str(detail or "")[:300],
            "minutes": 30, "calendar": bool(calendar), "kind": str(kind)}
    try:
        meta["start"] = start_dt.isoformat()
    except Exception:
        pass
    try:
        import askme as A
        key = (str(kind) + "|" + "".join(ch for ch in s.lower()
               if ch.isalnum() or ch == " ")[:60].strip())
        if A.answered(key):
            _log("уже відповідав про це — не питаю вдруге: " + s[:60])
            return {"ok": False, "error": "calgate: already answered"}
        if not _ask_budget_ok():
            _log("ліміт питань на добу — тихо відкидаю: " + s[:60])
            return {"ok": False, "error": "calgate: ask budget"}
        _ask_kind = "remind" if str(kind) == "reminder" else "write"
        A.ask(q, kind=_ask_kind, key=key, meta=meta,
              tag="MSG_WRITE_ASK_" + str(source or kind or "bot"))
        _log("запитав дозвіл на запис (" + str(kind) + "): " + s[:70])
    except Exception as e:
        _log("ask error: " + str(e))
    return {"ok": False, "pending": True,
            "error": "calgate: чекаю підтвердження Олега"}
