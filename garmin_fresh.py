#!/usr/bin/env python3
"""
garmin_fresh.py — AI ЗАВЖДИ має доступ до Garmin і бачить лише СВІЖЕ як свіже.

Запит Олега (13.11): «зроби так щоб ai мав доступ до garmin даних завжди і давав
тільки свіжі дані».

ПРОБЛЕМА (видно в даних): живий запит до Garmin ішов раз на 3 год, а всі AI-модулі
читали кеш qwatch_data.json. Між синками AI працював зі старим кешем, а метрики без
свіжого заміру (HRV, сон) тихо підставлялися «з учора» і подавалися як поточні.
Ще: fetch_live брав date.today() за UTC, тож у першу годину-дві після півночі
(за Кошице) тягнув учорашній день.

ЩО РОБИТЬ:
  1. ensure_fresh(): якщо останній синк старший за STALE_MIN хв — сам робить живий
     запит (garmin_sync.fetch_live, день за часом Кошице). Обмеження: не частіше
     MIN_LIVE_GAP_MIN, після невдачі пауза FAIL_BACKOFF_MIN, жорсткий таймаут
     LIVE_TIMEOUT_S (AI-запит не висне), одночасно лише один запит.
  2. block(): текстовий блок для КОЖНОГО AI-запиту (через allctx): по кожній метриці
     значення + ЯКОГО дня замір + вік; стара метрика позначена ПРЯМО як застаріла.
     Правила: >1 дня — не подавати як поточну, лише «останній замір N дн тому»
     або мовчати; частковий день (кроки/стрес до кінця доби) — не порівнювати з
     нормою; синк старий/недоступний — сказати про це, не вигадувати.
  3. fresh_value(metric, max_age_days): для модулів, яким потрібне лише свіже.
  4. silent_refresh_tick(): тихе оновлення кешу (без повідомлення) для watcher-а.

API: ensure_fresh(force=False) -> dict, snapshot() -> dict, block() -> str,
     fresh_value(metric, max_age_days=0) -> value|None, silent_refresh_tick()
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TAG = "garmin_fresh"
STALE_MIN = 30            # кеш старший за це -> перед AI-запитом тягнемо живі дані
MIN_LIVE_GAP_MIN = 20     # не частіше (щоб не отримати rate-limit від Garmin)
FAIL_BACKOFF_MIN = 30     # після невдачі не пробуємо стільки хвилин
LIVE_TIMEOUT_S = 40       # AI не чекає довше
NIGHT_FROM, NIGHT_TO = 1, 5   # 01:00–05:00 за Кошице: без фонових запитів (Олег спить)

# метрика -> (підпис, одиниця, «частковий день»?, макс. вік свіжого в днях)
METRICS = [
    ("hrv",            "HRV (за ніч)",      "мс",    False, 1),
    ("hr_avg",         "Пульс спокою",      "уд/хв", False, 1),
    ("sleep_total_min", "Сон",              "_min",  False, 1),
    ("sleep_deep_min", "Глибокий сон",      "хв",    False, 1),
    ("sleep_rem_min",  "REM",               "хв",    False, 1),
    ("body_battery",   "Body Battery",      "/100",  True,  0),
    ("stress",         "Стрес (середній)",  "",      True,  0),
    ("spo2",           "SpO2",              "%",     False, 1),
    ("steps",          "Кроки",             "",      True,  0),
    ("vo2max",         "VO2max",            "",      False, 14),
    ("weight_kg",      "Вага (Garmin)",     "кг",    False, 7),
]

_lock = threading.Lock()
_st = {"last_try": 0.0, "last_ok": 0.0, "last_fail": 0.0, "last_err": ""}


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


def _local_now():
    # Кошице: узгоджено з qwsync._today (UTC+2, як у решті модулів)
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)


def _today():
    return _local_now().strftime("%Y-%m-%d")


def _db():
    try:
        import storage
        d = storage._load_github("qwatch_data.json") or {}
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log("qwatch: " + str(e))
        return {}


def _parse_saved(s):
    try:
        return datetime.strptime(str(s)[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return None


def sync_age_min(db=None):
    """Хвилин від останнього збереження з Garmin (за saved_at), або None."""
    db = db if db is not None else _db()
    best = None
    for day in sorted(db)[-3:]:
        e = db.get(day)
        if isinstance(e, dict):
            t = _parse_saved(e.get("saved_at"))
            if t and (best is None or t > best):
                best = t
    if not best:
        return None
    return max(0, int((_local_now() - best).total_seconds() // 60))


# ─── ЖИВИЙ ЗАПИТ ─────────────────────────────────────────────────────────────

def _live_call():
    import garmin_sync
    return garmin_sync.fetch_live(_today())


def ensure_fresh(force=False):
    """Гарантує свіжість кешу перед AI. Повертає {'refreshed','age_min','note'}.
    Ніколи не кидає виняток і не блокує довше LIVE_TIMEOUT_S."""
    out = {"refreshed": False, "age_min": sync_age_min(), "note": ""}
    now = time.time()
    age = out["age_min"]
    if not force:
        if age is not None and age < STALE_MIN:
            out["note"] = "кеш свіжий"
            return out
        if now - _st["last_try"] < MIN_LIVE_GAP_MIN * 60:
            out["note"] = "живий запит щойно був"
            return out
        if _st["last_fail"] and now - _st["last_fail"] < FAIL_BACKOFF_MIN * 60:
            out["note"] = "Garmin недавно не відповів: " + _st["last_err"][:80]
            return out
        if NIGHT_FROM <= _local_now().hour < NIGHT_TO and age is not None and age < 8 * 60:
            out["note"] = "ніч — без фонових запитів"
            return out
    if not _lock.acquire(blocking=False):
        out["note"] = "оновлення вже йде"
        return out
    try:
        _st["last_try"] = now
        box = {}

        def work():
            try:
                box["r"] = _live_call()
            except Exception as e:
                box["e"] = str(e)

        th = threading.Thread(target=work, daemon=True)
        th.start()
        th.join(LIVE_TIMEOUT_S)
        r = box.get("r")
        if r and r.get("ok"):
            _st["last_ok"] = time.time()
            _st["last_fail"] = 0.0
            out.update({"refreshed": True, "age_min": sync_age_min(), "note": "оновлено живим запитом"})
            _log("живий запит OK")
        else:
            err = (r or {}).get("error") or box.get("e") or "таймаут " + str(LIVE_TIMEOUT_S) + "с"
            _st["last_fail"] = time.time()
            _st["last_err"] = str(err)
            out["note"] = "живий запит не вдався: " + str(err)[:100]
            _log(out["note"])
    finally:
        _lock.release()
    return out


# ─── ЗРІЗ ТА СВІЖІСТЬ ────────────────────────────────────────────────────────

def _age_label(days):
    if days <= 0:
        return "сьогодні"
    if days == 1:
        return "вчора"
    return str(days) + " дн тому"


def snapshot(db=None):
    """{metric: {'value','day','age_days','fresh'}} — останній НЕпорожній замір."""
    db = db if db is not None else _db()
    today = datetime.strptime(_today(), "%Y-%m-%d")
    days = sorted(d for d in db if isinstance(db.get(d), dict))
    out = {}
    for key, label, unit, partial, max_age in METRICS:
        for d in reversed(days):
            v = db[d].get(key)
            if v is None or v == "" or (key in ("steps", "stress") and v == 0):
                continue
            try:
                age = (today - datetime.strptime(d[:10], "%Y-%m-%d")).days
            except Exception:
                continue
            out[key] = {"value": v, "day": d, "age_days": age, "fresh": age <= max_age,
                        "label": label, "unit": unit, "partial": bool(partial and age == 0)}
            break
    return out


def fresh_value(metric, max_age_days=0):
    """Значення метрики лише якщо замір не старший за max_age_days, інакше None."""
    s = snapshot().get(metric)
    if not s or s["age_days"] > max_age_days:
        return None
    return s["value"]


def _fmt(key, m):
    v, unit = m["value"], m["unit"]
    if unit == "_min":
        h, mm = divmod(int(v), 60)
        return str(h) + "г " + ("%02d" % mm) + "хв"
    if key == "steps":
        return str(int(v))
    return str(v) + (" " + unit if unit and unit != "/100" else unit)


def block():
    """Блок для AI-промпту: що з Garmin свіже, що застаріле, коли був синк."""
    snap = snapshot()
    if not snap:
        return "GARMIN: даних немає взагалі — не вигадуй показники здоров'я."
    age = sync_age_min()
    head = ("Останній синк Garmin: "
            + (("%d хв тому" % age) if age is not None and age < 120 else
               (("%.1f год тому" % (age / 60.0)) if age is not None else "невідомо"))
            + ". Зараз " + _local_now().strftime("%d.%m %H:%M") + " (Кошице).")
    if age is not None and age > 6 * 60:
        head += " ⚠️ СИНК СТАРИЙ — прямо скажи, що живих даних зараз нема."
    fresh, stale = [], []
    for key, label, unit, partial, max_age in METRICS:
        m = snap.get(key)
        if not m:
            continue
        line = label + ": " + _fmt(key, m) + " (" + _age_label(m["age_days"]) + ", " + m["day"][5:] + ")"
        if m["partial"]:
            line += " [доба не завершена — не порівнюй з нормою]"
        (fresh if m["fresh"] else stale).append(line)
    parts = [head]
    if fresh:
        parts.append("СВІЖЕ (можна подавати як поточне):\n- " + "\n- ".join(fresh))
    if stale:
        parts.append("ЗАСТАРІЛЕ (НЕ подавай як поточне; або «останній замір N дн тому», або мовчи):\n- "
                     + "\n- ".join(stale))
    missing = [lbl for k, lbl, _u, _p, _m in METRICS[:5] if k not in snap]
    if missing:
        parts.append("Немає взагалі: " + ", ".join(missing))
    parts.append("ПРАВИЛО СВІЖОСТІ: показники здоров'я бери ЛИШЕ з цього блоку. Якщо метрики "
                 "нема у «СВІЖЕ» — не кажи про неї в теперішньому часі. Кожне число супроводжуй "
                 "днем заміру, якщо це не сьогодні.")
    return "\n".join(parts)


# ─── ТИХЕ ФОНОВЕ ОНОВЛЕННЯ ───────────────────────────────────────────────────

def silent_refresh_tick():
    """Для watcher-а раз на ~час: оновити кеш без жодного повідомлення."""
    try:
        r = ensure_fresh()
        if r.get("refreshed"):
            _log("тихий синк: вік кешу " + str(r.get("age_min")) + " хв")
        return r
    except Exception as e:
        _log("tick: " + str(e))
        return {}


if __name__ == "__main__":
    print(block())
