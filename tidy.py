#!/usr/bin/env python3
"""
САМОПРИБИРАННЯ  (tidy)

Реєстри бота накопичують мертвий непотріб: виконані дедлайни, скасовані
підписки, минулі дати з листів, «важливі» листи, на які вже відповіли.
Через це /дедлайни, /підписки й /дати з часом перестають бути корисними.

Раз на добу цей модуль прибирає ЛИШЕ те, що явно мертве, і пише Олегу
короткий звіт — що саме прибрано. Нічого не видаляється мовчки.

Принцип: критерій або чіткий, або запис лишається. Сумнівне не чіпаємо.
"""

from datetime import datetime, timedelta

import ai_kit as K

TAG = "tidy"

STATE = "tidy_state.json"      # {last: 'YYYY-MM-DD'}
LOG_FILE = "tidy_log.json"     # {date: [рядки]}

# Скільки днів після смерті запису тримаємо його «на всяк випадок»
KEEP_DONE_DAYS = 30       # виконані дедлайни
KEEP_CANCELLED_DAYS = 90  # скасовані/мертві підписки
KEEP_PAST_EVENT_DAYS = 120  # події з листів, які вже минули
KEEP_REMINDED_DAYS = 60   # дедлайни з листів, про які вже нагадали
STALE_SUB_DAYS = 180      # непідтверджена підписка, якої давно не видно


def _today():
    return K.now().date()


def _d(s):
    """'YYYY-MM-DD' → date | None."""
    s = str(s or "")[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _age(s):
    """Скільки днів тому була ця дата. None якщо дата некорректна."""
    d = _d(s)
    if not d:
        return None
    return (_today() - d).days


# ─── ПРИБИРАННЯ ПО РЕЄСТРАХ ──────────────────────────────────────────────────

def _tidy_deadlines():
    """Виконані дедлайни старші за KEEP_DONE_DAYS → геть."""
    removed = []
    try:
        import deadlines_watcher as DW
    except Exception as e:
        K.log(TAG, f"deadlines недоступні: {e}")
        return removed

    items = K.load(DW.ITEMS_FILE, default={}) or {}
    for did, r in list(items.items()):
        if not isinstance(r, dict):
            continue
        age = _age(r.get("deadline"))
        if r.get("done") and age is not None and age > KEEP_DONE_DAYS:
            K.remove_key(DW.ITEMS_FILE, did)
            removed.append(f"⏳ виконано й минуло: {r.get('title','')} ({r.get('deadline','')})")
    return removed


def _tidy_subs():
    """Скасовані підписки + непідтверджені, яких давно не видно в пошті."""
    removed = []
    try:
        import subs_watcher as SW
    except Exception as e:
        K.log(TAG, f"subs недоступні: {e}")
        return removed

    items = K.load(SW.SUBS_FILE, default={}) or {}
    for key, r in list(items.items()):
        if not isinstance(r, dict):
            continue
        dead = bool(r.get("cancelled")) or (r.get("active") is False)
        last = r.get("last_seen") or r.get("created")
        age = _age(last)

        if dead and age is not None and age > KEEP_CANCELLED_DAYS:
            K.remove_key(SW.SUBS_FILE, key)
            removed.append(f"🧹 мертва підписка: {r.get('name') or key}")
            continue

        if (not r.get("confirmed")) and age is not None and age > STALE_SUB_DAYS:
            K.remove_key(SW.SUBS_FILE, key)
            removed.append(f"🧹 так і не підтвердилась як підписка: {r.get('name') or key}")
    return removed


def _tidy_mailcal():
    """Події з листів, які давно минули (сам календар не чіпаємо)."""
    removed = []
    try:
        import mailcal as MC
    except Exception as e:
        K.log(TAG, f"mailcal недоступний: {e}")
        return removed

    items = K.load(MC.ITEMS_FILE, default={}) or {}
    for key, r in list(items.items()):
        if not isinstance(r, dict):
            continue
        if r.get("empty"):
            age = _age(r.get("at"))
            if age is not None and age > KEEP_REMINDED_DAYS:
                K.remove_key(MC.ITEMS_FILE, key)
            continue
        age = _age(r.get("date"))
        if age is not None and age > KEEP_PAST_EVENT_DAYS:
            K.remove_key(MC.ITEMS_FILE, key)
            removed.append(f"📅 подія з листа давно минула: {r.get('title','')} ({r.get('date','')})")
    return removed


def _tidy_email_deadlines():
    """data/email_deadlines.json: минулі й уже нагадані записи + битий рік."""
    removed = []
    data = K.load("email_deadlines.json", default=[])
    if not isinstance(data, list) or not data:
        return removed

    keep = []
    for it in data:
        if not isinstance(it, dict):
            continue
        age = _age(it.get("date"))
        if age is None:
            removed.append(f"⏰ запис без нормальної дати: {it.get('title','')}")
            continue
        if age > 365 * 2:
            removed.append(f"⏰ битий рік у даті: {it.get('title','')} ({it.get('date','')})")
            continue
        if it.get("reminded") and age > KEEP_REMINDED_DAYS:
            removed.append(f"⏰ нагадав і минуло: {it.get('title','')} ({it.get('date','')})")
            continue
        keep.append(it)

    if removed:
        K.save("email_deadlines.json", keep)
    return removed


def _tidy_dates():
    """Разові дати (не щорічні) з роком, які минули понад рік тому."""
    removed = []
    try:
        import dates_book as DB
    except Exception as e:
        K.log(TAG, f"dates недоступні: {e}")
        return removed

    items = K.load(DB.DATES_FILE, default={}) or {}
    recurring = ("birthday", "anniversary", "memorial")
    for did, r in list(items.items()):
        if not isinstance(r, dict):
            continue
        if (r.get("kind") or "other") in recurring:
            continue          # дні народження повторюються щороку — не чіпаємо
        year = str(r.get("year") or "")
        md = str(r.get("md") or "")
        if not (year and md):
            continue
        age = _age(f"{year}-{md}")
        if age is not None and age > 365:
            K.remove_key(DB.DATES_FILE, did)
            removed.append(f"📌 разова дата минула понад рік тому: {r.get('name','')} ({year}-{md})")
    return removed


# ─── ГОЛОВНЕ ─────────────────────────────────────────────────────────────────

def run(force=False) -> int:
    """Раз на добу. Повертає кількість прибраних записів."""
    today = _today().isoformat()
    state = K.load(STATE, default={}) or {}
    if not force and state.get("last") == today:
        return 0

    removed = []
    for fn in (_tidy_deadlines, _tidy_subs, _tidy_mailcal,
               _tidy_email_deadlines, _tidy_dates):
        try:
            removed += fn()
        except Exception as e:
            K.log(TAG, f"{fn.__name__} error: {e}")

    state["last"] = today
    K.save(STATE, state)

    if not removed:
        K.log(TAG, "прибирати нічого")
        return 0

    log = K.load(LOG_FILE, default={}) or {}
    log[today] = removed
    for k in sorted(log.keys())[:-14]:      # історія за 14 днів
        log.pop(k, None)
    K.save(LOG_FILE, log)

    head = f"🧹 <b>Прибрав {len(removed)} мертвих записів</b>"
    body = "\n".join(f"• {K.esc(x)}" for x in removed[:20])
    tail = ""
    if len(removed) > 20:
        tail = f"\n\n…і ще {len(removed) - 20}."
    K.send_card(f"{head}\n\n{body}{tail}\n\n<i>Реєстри чисті — /дедлайни, "
                f"/підписки й /дати тепер показують лише живе.</i>", tag=TAG)
    K.log(TAG, f"прибрано {len(removed)}")
    return len(removed)


def report() -> str:
    """Що прибиралось останнім часом — для /прибирання."""
    log = K.load(LOG_FILE, default={}) or {}
    if not log:
        return ("🧹 Я ще нічого не прибирав — або реєстри чисті, або ще не "
                "настав час першого прибирання.")
    out = ["🧹 <b>Останні прибирання</b>", ""]
    for day in sorted(log.keys(), reverse=True)[:5]:
        rows = log.get(day) or []
        out.append(f"<b>{day}</b> — {len(rows)}")
        for x in rows[:6]:
            out.append(f"• {K.esc(x)}")
        out.append("")
    return "\n".join(out).strip()
