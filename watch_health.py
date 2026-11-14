#!/usr/bin/env python3
"""
watch_health.py — видимість роботи фонових AI-модулів.

14.11: garmin_insight / run_insight / market_insight жодного разу не залишили сліду на гілці
`data`, і не було як дізнатись чому (Railway-логи недоступні). Тепер кожен запуск
записує в watcher_status.json: статус (sent|skipped|error) і ПРИЧИНУ мовчання.
Команда /статус_ai показує це в чаті.

  why(module, text)      — модуль зазначає причину, перед тим як повернути False
  track(module, fn)      — виклик fn() з записом результату (ніколи не кидає виняток)
  report()               — HTML-текст для чату
Запис у сховище — лише при зміні (status, reason) або раз на 30 хв, щоб не сміти комітами.
"""
import threading
import time
from datetime import datetime, timezone

FILE = "watcher_status.json"
REFRESH_S = 30 * 60

_lock = threading.Lock()
_reasons = {}
_mem = {}          # module -> {status, reason, ts_s(float), saved_s(float)}
_last_sent = {}


def _log(m):
    print(f"[watch_health] {m}", flush=True)


def why(module: str, text: str) -> None:
    _reasons[module] = str(text)[:160]


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save(module, rec):
    try:
        import storage
        db = storage.load(FILE, default={}) or {}
        if not isinstance(db, dict):
            db = {}
        db[module] = rec
        storage.save(FILE, db)
    except Exception as e:
        _log("save: " + str(e))


def record(module: str, status: str, reason: str = "") -> None:
    now = time.time()
    with _lock:
        prev = _mem.get(module) or {}
        if status == "sent":
            _last_sent[module] = _now_iso()
        changed = (prev.get("status"), prev.get("reason")) != (status, reason)
        due = now - prev.get("saved_s", 0) >= REFRESH_S
        cur = {"status": status, "reason": reason, "ts_s": now,
               "saved_s": prev.get("saved_s", 0)}
        if changed or due or status == "sent":
            cur["saved_s"] = now
            rec = {"ts": _now_iso(), "status": status, "reason": reason,
                   "last_sent": _last_sent.get(module, "")}
            _mem[module] = cur
        else:
            _mem[module] = cur
            return
    _save(module, rec)


def track(module: str, fn, *a, **k):
    """Виконує fn, фіксує результат. Повертає результат або False."""
    _reasons.pop(module, None)
    try:
        r = fn(*a, **k)
    except Exception as e:
        record(module, "error", f"{type(e).__name__}: {str(e)[:140]}")
        _log(f"{module}: {e}")
        return False
    if r:
        record(module, "sent", "")
    else:
        record(module, "skipped", _reasons.pop(module, "причину не вказано"))
    return r


def report() -> str:
    try:
        import storage
        db = storage.load(FILE, default={}) or {}
    except Exception:
        db = {}
    if not db:
        return "🩺 <b>Статус AI-модулів</b>\nЗаписів ще немає (модулі ще не запускались після деплою)."
    icon = {"sent": "✅", "skipped": "💤", "error": "❌"}
    rows = ["🩺 <b>Статус AI-модулів</b>"]
    for m, r in sorted(db.items()):
        if not isinstance(r, dict):
            continue
        ts = str(r.get("ts", ""))[5:16].replace("T", " ")
        line = f"{icon.get(r.get('status'), '•')} <b>{m}</b> · {r.get('status')} · {ts} UTC"
        if r.get("reason"):
            line += f"\n   {r['reason']}"
        if r.get("last_sent"):
            line += f"\n   востаннє надіслав: {str(r['last_sent'])[5:16].replace('T', ' ')} UTC"
        rows.append(line)
    return "\n".join(rows)
