#!/usr/bin/env python3
"""
mail_seen.py — кожен лист у звіті показується карткою ОДИН раз.

14.11: ті самі 6–7 непрочитаних листів приходили окремими «📩 НОВЕ» картками в кожному
3-годинному звіті (10:01, 13:02, …). Тепер uid, що вже був карткою за останні 3 доби,
у звіті не повторюється (лишається лічильник «ще N раніше показаних»).
"""
from datetime import datetime, timedelta

FILE = "mail_seen.json"
# 23.11: було 3 доби → 5 старих непрочитаних листів через 3 доби знову виїжджали
# як «НОВЕ». Тримаємо 14 діб.
TTL_DAYS = 14


def _load():
    try:
        import storage
        d = storage.load(FILE, default={}) or {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def split(items: list, now: datetime = None):
    """-> (нові[list], вже_показані[list]); item — dict з ключем 'uid'."""
    now = now or datetime.utcnow()
    cut = (now - timedelta(days=TTL_DAYS)).isoformat(timespec="seconds")
    seen = {k: v for k, v in _load().items() if str(v) >= cut}
    new, old = [], []
    for it in items or []:
        (old if str(it.get("uid")) in seen else new).append(it)
    return new, old


def mark(items: list, now: datetime = None) -> None:
    if not items:
        return
    now = now or datetime.utcnow()
    cut = (now - timedelta(days=TTL_DAYS)).isoformat(timespec="seconds")
    seen = {k: v for k, v in _load().items() if str(v) >= cut}
    for it in items:
        seen[str(it.get("uid"))] = now.isoformat(timespec="seconds")
    try:
        import storage
        storage.save(FILE, seen)
    except Exception as e:
        print(f"[mail_seen] save: {e}", flush=True)
