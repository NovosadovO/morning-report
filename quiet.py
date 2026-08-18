#!/usr/bin/env python3
"""
quiet.py — РЕЖИМ СНУ.

Олег: «/сон → бот входить у режим сну: без сповіщень, без нагадувань, без
витрат кредитів AI. З 04:00 сам відновлює роботу».

Як працює:
  • /сон            → пишемо в quiet_mode.json {"until": <найближчі 04:00>}
  • поки now < until → усі ПРОАКТИВНІ повідомлення й усі виклики Gemini
                       блокуються (нічого не шлеться, кредити не паляться)
  • о 04:00           → until у минулому → is_quiet() = False. Ніякого крону не
                       треба: пробудження — це просто спливання дедлайну.
  • /прокинувся      → вихід раніше вручну.

ВАЖЛИВО: команди самого Олега працюють і під час сну. Якщо він о 02:00 напише
/звіт — отримає звіт. Тому потік, який обслуговує його повідомлення/кнопку,
помічається mark_user_thread() і для нього тиша не діє.

Що НЕ блокується взагалі: критичні технічні відповіді на його дії.
"""
import os
import sys
import threading
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STATE_FILE = "quiet_mode.json"
LOCAL_FILE = "/tmp/quiet_mode.json"   # першоджерело: спільний для всіх процесів контейнера
WAKE_HOUR = 4          # о 04:00 бот сам вертається до роботи
TAG = "quiet"
_CACHE_TTL = 10        # с — щоб кожен send не бив по файлу/GitHub

# Потік, що обслуговує запит Олега → для нього режим сну не діє
_local = threading.local()

# memcache стану: (дані, час)
_mem = {"data": None, "ts": 0.0}

# Скільки повідомлень/AI-викликів придушили за цю ніч (для звіту при пробудженні)
_muted_counter = {"msg": 0, "ai": 0}


def _log(msg):
    print(f"[{TAG}] {msg}", flush=True)


def _now():
    try:
        import ai_kit as K
        return K.now().replace(tzinfo=None)
    except Exception:
        return datetime.now()


def _load():
    """Читаємо ЛОКАЛЬНИЙ файл першим — він спільний для bot.py і для
    subprocess-ів (report2/report_defi/report_social), тому режим сну діє
    миттєво в усіх процесах. GitHub — тільки якщо локального ще немає
    (перший запуск після редеплою), бо його кеш живе 5 хв і давав дірку,
    через яку фонові сповіщення пролітали після /сон."""
    import json as _json
    import time as _t
    if _mem["data"] is not None and (_t.time() - _mem["ts"]) < _CACHE_TTL:
        return dict(_mem["data"])
    data = None
    try:
        with open(LOCAL_FILE) as f:
            data = _json.load(f)
    except Exception:
        data = None
    if data is None:
        try:
            import storage
            storage.invalidate_cache(STATE_FILE)
            data = storage.load(STATE_FILE, default={}) or {}
            try:
                with open(LOCAL_FILE, "w") as f:
                    _json.dump(data, f)
            except Exception:
                pass
        except Exception as e:
            _log(f"load error: {e}")
            data = {}
    _mem["data"] = dict(data)
    _mem["ts"] = _t.time()
    return dict(data)


def _save(data):
    """Локально — синхронно (щоб діяло негайно), у GitHub — для переживання
    редеплою."""
    import json as _json
    import time as _t
    _mem["data"] = dict(data)
    _mem["ts"] = _t.time()
    ok_local = False
    try:
        with open(LOCAL_FILE, "w") as f:
            _json.dump(data, f)
        ok_local = True
    except Exception as e:
        _log(f"local save error: {e}")
    try:
        import storage
        storage.save(STATE_FILE, data)
        storage.invalidate_cache(STATE_FILE)
    except Exception as e:
        _log(f"github save error: {e}")
    return ok_local


# ─── ПОЗНАЧКА «ЦЕ ПОТІК ОЛЕГА» ───────────────────────────────────────────────

def mark_user_thread():
    """Викликається на вході обробки повідомлення/кнопки від Олега."""
    _local.user = True


def clear_user_thread():
    _local.user = False


def is_user_thread() -> bool:
    return bool(getattr(_local, "user", False))


# Багато команд Олега (/звіт, /рахунки...) виконуються у фоновому потоці, а
# threading.local туди не наслідується — і його ж звіт було б заглушено.
# Тому додаємо вікно: якщо Олег писав/тиснув менш ніж ACTIVE_MIN хв тому,
# бот вважає, що він не спить, і не глушить нічого.
ACTIVE_MIN = 5
_last_user_action = {"ts": 0.0}


def touch_user():
    """Викликається на КОЖНЕ повідомлення/кнопку від Олега."""
    import time as _t
    _last_user_action["ts"] = _t.time()


def user_recently_active() -> bool:
    import time as _t
    return (_t.time() - _last_user_action["ts"]) < ACTIVE_MIN * 60


# ─── СТАН ────────────────────────────────────────────────────────────────────

def _next_wake(from_dt=None) -> datetime:
    """Найближчі WAKE_HOUR:00. Якщо зараз 23:10 → 04:00 наступного дня;
    якщо 02:30 → 04:00 сьогодні."""
    n = from_dt or _now()
    w = n.replace(hour=WAKE_HOUR, minute=0, second=0, microsecond=0)
    if w <= n:
        w += timedelta(days=1)
    return w


def until_dt():
    st = _load()
    raw = st.get("until")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).replace(tzinfo=None)
    except Exception:
        return None


def is_quiet() -> bool:
    """True → зараз режим сну (нічого не шлемо, AI не викликаємо)."""
    u = until_dt()
    if not u:
        return False
    if _now() >= u:
        # дедлайн сплив — авто-пробудження, чистимо стан один раз
        _auto_wake()
        return False
    return True


def blocked(kind: str = "msg") -> bool:
    """Головна перевірка для викликів у коді. kind: msg | ai."""
    if is_user_thread() or user_recently_active():
        return False
    if not is_quiet():
        return False
    try:
        _muted_counter[kind] = _muted_counter.get(kind, 0) + 1
    except Exception:
        pass
    return True


def _auto_wake():
    st = _load()
    if not st.get("until"):
        return
    since = st.get("since")
    st2 = {"until": None, "since": None,
           "last_wake": _now().isoformat(), "last_sleep_since": since}
    _save(st2)
    _log(f"☀️ авто-пробудження о {WAKE_HOUR:02d}:00 — режим сну знято")
    try:
        import storage
        storage.invalidate_cache(STATE_FILE)
    except Exception:
        pass


# ─── УВІМКНУТИ / ВИКЛЮЧИТИ ───────────────────────────────────────────────────

def sleep_on(wake_hour: int = None) -> dict:
    """/сон — вмикає тишу до найближчих 04:00 (або до вказаної години)."""
    global WAKE_HOUR
    if wake_hour is not None:
        try:
            wake_hour = int(wake_hour)
            if 0 <= wake_hour <= 23:
                WAKE_HOUR = wake_hour
        except Exception:
            pass
    n = _now()
    u = _next_wake(n)
    _muted_counter["msg"] = 0
    _muted_counter["ai"] = 0
    # /сон сам приходить від Олега → вікно активності щойно оновилось і
    # 5 хв пропускало б сповіщення. Скидаємо: тиша має початись НЕГАЙНО.
    _last_user_action["ts"] = 0.0
    ok = _save({"until": u.isoformat(), "since": n.isoformat(),
                "wake_hour": WAKE_HOUR})
    hrs = (u - n).total_seconds() / 3600.0
    _log(f"🌙 режим сну увімкнено до {u.strftime('%d.%m %H:%M')} ({hrs:.1f} год)")
    return {"ok": ok, "until": u, "hours": hrs}


def sleep_off() -> dict:
    """/прокинувся — ручний вихід із тиші."""
    was = is_quiet()
    u = until_dt()
    _save({"until": None, "since": None, "last_wake": _now().isoformat()})
    _log("☀️ режим сну знято вручну")
    return {"ok": True, "was_sleeping": was, "planned_until": u,
            "muted_msg": _muted_counter.get("msg", 0),
            "muted_ai": _muted_counter.get("ai", 0)}


# ─── ТЕКСТИ ДЛЯ TELEGRAM ─────────────────────────────────────────────────────

def sleep_text(r: dict) -> str:
    u = r.get("until")
    h = r.get("hours") or 0
    when = u.strftime("%H:%M") if hasattr(u, "strftime") else "04:00"
    return (
        "🌙 <b>РЕЖИМ СНУ УВІМКНЕНО</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"Тиша до <b>{when}</b> (≈{h:.1f} год).\n\n"
        "🔕 Не буде сповіщень і нагадувань\n"
        "🤖 AI вимкнений — кредити не витрачаються\n"
        f"☀️ О {when} я вернусь до роботи сам\n\n"
        "<i>Якщо напишеш мені вночі — відповім, команди працюють.\n"
        "Прокинувся раніше → /прокинувся</i>\n\n"
        "Добраніч, Олеже 💙"
    )


def wake_text(r: dict) -> str:
    if not r.get("was_sleeping"):
        return ("☀️ Режим сну і так був вимкнений — я на зв'язку.\n"
                "Щоб заснути: /сон")
    m = r.get("muted_msg", 0)
    a = r.get("muted_ai", 0)
    extra = ""
    if m or a:
        extra = (f"\n\nПоки ти спав, я стримав <b>{m}</b> сповіщень "
                 f"і {a} AI-викликів — кредити цілі.")
    return ("☀️ <b>З ДОБРИМ РАНКОМ!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Режим сну знято, я знову на зв'язку." + extra +
            "\n\nХочеш зведення за ніч — /звіт")


def status_text() -> str:
    u = until_dt()
    if not u or _now() >= u:
        st = _load()
        lw = st.get("last_wake")
        tail = ""
        if lw:
            try:
                tail = ("\nОстаннє пробудження: "
                        + datetime.fromisoformat(str(lw)).strftime("%d.%m %H:%M"))
            except Exception:
                pass
        return ("☀️ <b>Режим сну: ВИКЛ</b>\nСповіщення й AI працюють."
                + tail + "\n\nЗаснути: /сон")
    left = (u - _now()).total_seconds() / 3600.0
    return ("🌙 <b>Режим сну: УВІМК</b>\n"
            f"Тиша ще {left:.1f} год, до {u.strftime('%H:%M')}.\n"
            f"Стримано: {_muted_counter.get('msg', 0)} сповіщень, "
            f"{_muted_counter.get('ai', 0)} AI-викликів.\n\n"
            "Прокинувся → /прокинувся")


if __name__ == "__main__":
    print(status_text())
