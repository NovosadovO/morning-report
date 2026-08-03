#!/usr/bin/env python3
"""
Централізований лог УСІХ відповідей Олега боту — з будь-якої кнопки чи
вільного тексту (щоденник, мікро-опитування, настрій, звички, email-відповіді,
quick-reply, підтвердження календаря/покупок тощо).

Мета: мати єдине джерело даних для тижневих/місячних/річних звітів
"що і як часто Олег відповідав боту".

Зберігання: storage.py (GitHub data-гілка, persistent).
"""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOG_FILE = "response_log.json"
_TZ_OFFSET = timedelta(hours=2)


def _storage():
    import storage
    return storage


def log_response(category: str, question: str, answer: str, extra: dict = None):
    """Записує одну відповідь. category — тип (diary/micro_checkin/mood/habit/
    email_reply/quick_reply/calendar_confirm/shopping_confirm/chat тощо).

    НЕ блокує викликача: запис у GitHub (до 5 retry з backoff, ~30с) робиться
    у фоновому потоці. Раніше цей виклик стояв першим у кожному обробнику
    кнопки й міг вішати весь колбек.
    """
    entry = {
        "ts": (datetime.now(timezone.utc) + _TZ_OFFSET).isoformat(),
        "category": category,
        "question": (question or "")[:300],
        "answer": (answer or "")[:500],
    }
    if extra:
        entry["extra"] = extra

    def _writer():
        try:
            import storage as _st
            lock = _st._get_file_lock(LOG_FILE)
            with lock:  # RLock — вкладені _load/_save всередині безпечні
                data = _st._load_github(LOG_FILE) or {"entries": []}
                if not isinstance(data, dict):
                    data = {"entries": []}
                data.setdefault("entries", []).append(entry)
                data["entries"] = data["entries"][-2000:]
                _st._save_github(LOG_FILE, data)
        except Exception as e:
            print(f"[response_log] error: {e}", flush=True)

    try:
        import threading
        threading.Thread(target=_writer, daemon=True,
                         name=f"rlog-{category[:12]}").start()
    except Exception as e:
        print(f"[response_log] thread error: {e}", flush=True)


def get_responses(days: int = 7, category: str = None) -> list:
    """Повертає список відповідей за останні N днів (опційно фільтр по category)."""
    try:
        s = _storage()
        data = s.load(LOG_FILE, default={"entries": []})
        entries = data.get("entries", []) if isinstance(data, dict) else []
        cutoff = datetime.now(timezone.utc) + _TZ_OFFSET - timedelta(days=days)
        result = []
        for e in entries:
            try:
                ts = datetime.fromisoformat(e["ts"])
                if ts.replace(tzinfo=None) >= cutoff.replace(tzinfo=None):
                    if category is None or e.get("category") == category:
                        result.append(e)
            except Exception:
                continue
        return result
    except Exception as e:
        print(f"[response_log] get_responses error: {e}", flush=True)
        return []


def summarize_by_category(days: int = 7) -> dict:
    """Повертає {category: count} за період — швидка статистика для звітів."""
    responses = get_responses(days=days)
    summary = {}
    for r in responses:
        cat = r.get("category", "unknown")
        summary[cat] = summary.get(cat, 0) + 1
    return summary
