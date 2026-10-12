#!/usr/bin/env python3
"""
Самодіагностика бота — AI стежить за власним здоров'ям і проактивно
пише Олегу якщо щось зламалось: Gemini падає, пошта не перевіряється,
чи інші ключові фонові процеси зависли.

Легкий in-memory лічильник (не потребує GitHub — швидко і без зайвих записів),
періодичні перевірки шле через окремий watcher у monitor_loop.py.
"""
import os
import sys
import time
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TZ_OFFSET = timedelta(hours=2)

# In-memory лічильники (по процесу — досить, бо перевірка раз на N хв все одно
# читає "свіжий" стан цього ж процесу; переживати редеплой не критично для
# короткострокового моніторингу здоров'я)
_GEMINI_RESULTS = []   # список bool (True=успіх, False=провал), останні 30
_LAST_EMAIL_CHECK_OK = [None]  # timestamp останньої УСПІШНОЇ перевірки пошти
_LAST_ALERT_SENT = {}  # {"gemini": ts, "email": ts} — dedup щоб не спамити

# ── Лічильник викликів Gemini по днях (запит Олега 12.10, пункт 4 —
# самоконтроль витрат AI, щоб бачити чи скорочення звітів реально тримається,
# а не лише здається). In-memory за ПОТОЧНИЙ день (без GitHub-запису на
# КОЖЕН виклик — це саме те, від чого ми економимо), раз на день
# flush_daily_count() дописує підсумок в storage (ai_usage_daily.json)
# для історії за тиждень/місяць.
_TODAY_CALLS = {"day": None, "n": 0}
_LAST_FLUSHED_DAY = [None]


def _today_str():
    return (datetime.now(timezone.utc) + _TZ_OFFSET).strftime("%Y-%m-%d")


def record_gemini_result(ok: bool):
    _GEMINI_RESULTS.append(ok)
    if len(_GEMINI_RESULTS) > 30:
        _GEMINI_RESULTS.pop(0)

    today = _today_str()
    if _TODAY_CALLS["day"] != today:
        _TODAY_CALLS["day"] = today
        _TODAY_CALLS["n"] = 0
    _TODAY_CALLS["n"] += 1


def flush_daily_count(force: bool = False):
    """Раз на день (викликається з check_self_health) дописує сьогоднішній
    підсумок викликів Gemini в storage — щоб був історичний ряд для
    тижневого звіту витрат, без запису в GitHub на кожен окремий виклик."""
    now = datetime.now(timezone.utc) + _TZ_OFFSET
    today = now.strftime("%Y-%m-%d")
    if not force and not (now.hour == 23 and now.minute >= 50):
        return
    if _LAST_FLUSHED_DAY[0] == today:
        return
    if _TODAY_CALLS["day"] != today or _TODAY_CALLS["n"] == 0:
        return
    try:
        import storage
        hist = storage.load("ai_usage_daily.json", default={}) or {}
        if not isinstance(hist, dict):
            hist = {}
        hist[today] = _TODAY_CALLS["n"]
        # тримаємо лише останні 60 днів, щоб файл не ріс нескінченно
        cutoff = (now - timedelta(days=60)).strftime("%Y-%m-%d")
        hist = {d: v for d, v in hist.items() if d >= cutoff}
        storage.save("ai_usage_daily.json", hist)
        _LAST_FLUSHED_DAY[0] = today
        print(f"[self_diag] flushed AI usage {today}: {_TODAY_CALLS['n']} викликів", flush=True)
    except Exception as e:
        print(f"[self_diag] flush_daily_count error: {e}", flush=True)


def weekly_usage_report() -> str:
    """Короткий текст: скільки викликів Gemini за останні 7 днів + середнє/день.
    Для вставки в недільний підсумок дня (check_day_summary), не окремий звіт."""
    try:
        import storage
        hist = storage.load("ai_usage_daily.json", default={}) or {}
        if not isinstance(hist, dict):
            hist = {}
    except Exception:
        hist = {}
    now = datetime.now(timezone.utc) + _TZ_OFFSET
    today = now.strftime("%Y-%m-%d")
    hist = dict(hist)
    if _TODAY_CALLS["day"] == today and _TODAY_CALLS["n"] > 0:
        hist[today] = _TODAY_CALLS["n"]  # сьогоднішнє — ще не "флашене", беремо з пам'яті

    days7 = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    vals = [hist.get(d) for d in days7 if hist.get(d) is not None]
    if not vals:
        return ""
    total = sum(vals)
    avg = round(total / len(vals), 1)
    return f"🤖 <b>AI-виклики за тиждень:</b> {total} (середнє {avg}/день, {len(vals)} днів даних)"


def record_email_check_ok():
    _LAST_EMAIL_CHECK_OK[0] = time.time()


def _gemini_failure_rate() -> float:
    """Частка провалів серед останніх спроб (0.0-1.0). None якщо замало даних."""
    if len(_GEMINI_RESULTS) < 5:
        return 0.0
    fails = sum(1 for r in _GEMINI_RESULTS if not r)
    return fails / len(_GEMINI_RESULTS)


def _should_alert(key: str, min_hours: float = 3.0) -> bool:
    last = _LAST_ALERT_SENT.get(key, 0)
    if time.time() - last < min_hours * 3600:
        return False
    _LAST_ALERT_SENT[key] = time.time()
    return True


def check_self_health():
    """Головна перевірка — викликається періодично з monitor_loop.py.
    Якщо щось зламане — шле Олегу зрозуміле повідомлення (не технічний трейсбек)."""
    try:
        flush_daily_count()
    except Exception as e:
        print(f"[self_diag] flush_daily_count call error: {e}", flush=True)

    try:
        import monitor as _mon
    except Exception as e:
        print(f"[self_diag] cannot import monitor: {e}", flush=True)
        return

    alerts = []

    # 1) Gemini failure rate
    rate = _gemini_failure_rate()
    if rate >= 0.6 and _should_alert("gemini", min_hours=2.0):
        alerts.append(
            "🔴 <b>АІ помітив проблему з собою:</b> Gemini (мозок аналізу) падає "
            f"у {int(rate*100)}% останніх спроб. Можливо вичерпані кредити, невалідний "
            "ключ, або тимчасовий збій Google. Відповіді йдуть на локальних шаблонах "
            "(менш точні) поки це не виправиться."
        )

    # 2) Email checker — якщо давно не було успішної перевірки (>30 хв — цикл кожні 2 хв)
    last_ok = _LAST_EMAIL_CHECK_OK[0]
    if last_ok is not None:
        idle_min = (time.time() - last_ok) / 60
        if idle_min > 30 and _should_alert("email", min_hours=1.0):
            alerts.append(
                f"🟡 <b>АІ помітив проблему з собою:</b> перевірка пошти не спрацьовувала "
                f"успішно вже {int(idle_min)} хв (мало бути кожні 2 хв). Можлива проблема "
                "з Gmail IMAP з'єднанням."
            )

    if alerts:
        try:
            for a in alerts:
                _mon.send_telegram(f"🛠 <b>Самодіагностика бота</b>\n\n{a}")
        except Exception as e:
            print(f"[self_diag] send error: {e}", flush=True)
