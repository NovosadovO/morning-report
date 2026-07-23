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


def record_gemini_result(ok: bool):
    _GEMINI_RESULTS.append(ok)
    if len(_GEMINI_RESULTS) > 30:
        _GEMINI_RESULTS.pop(0)


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
