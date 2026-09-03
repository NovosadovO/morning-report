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
    # Або явно важлива тема, або конкретика з часом/сумою/іменем
    if any(w in low for w in _WORTH):
        return True
    has_num = any(ch.isdigit() for ch in s)
    return bool(has_num and len(s) >= 8)


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
