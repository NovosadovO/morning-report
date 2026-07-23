#!/usr/bin/env python3
"""
Активне відстеження патернів поведінки Олега — наприклад, якщо він завжди
відкладає відповіді на листи від конкретної людини/компанії, AI це помітить
і запитає чому (може треба щось змінити: важливість, стосунки, пріоритет).

Зберігання: storage.py (persistent, GitHub data-гілка).
"""
import os
import sys
import re
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TIMING_FILE = "behavior_email_timing.json"     # {uid: {"sender": str, "seen_ts": iso}}
PATTERNS_FILE = "behavior_patterns_flagged.json"  # {sender_key: last_flagged_ts}


def _storage():
    import storage
    return storage


def _sender_key(sender: str) -> str:
    """Нормалізує відправника до стабільного ключа (email або домен)."""
    m = re.search(r'<([^>]+)>', sender or "")
    email = m.group(1).lower().strip() if m else (sender or "").lower().strip()
    return email or "unknown"


def record_email_seen(uid: str, sender: str):
    """Викликається коли бот вперше показав алерт про новий лист."""
    try:
        s = _storage()
        data = s.load(TIMING_FILE, default={})
        if not isinstance(data, dict):
            data = {}
        data[uid] = {
            "sender": _sender_key(sender),
            "sender_raw": sender[:100],
            "seen_ts": datetime.now(timezone.utc).isoformat(),
        }
        # Тримаємо тільки останні 300 записів щоб не роздувати файл
        if len(data) > 300:
            oldest_keys = sorted(data.keys(), key=lambda k: data[k].get("seen_ts", ""))[:len(data) - 300]
            for k in oldest_keys:
                data.pop(k, None)
        s.save(TIMING_FILE, data)
    except Exception as e:
        print(f"[behavior_patterns] record_email_seen error: {e}", flush=True)


def record_email_replied(uid: str):
    """Викликається коли Олег реально надіслав відповідь на лист (email_send_).
    Рахує затримку (год) і зберігає в історію затримок по відправнику."""
    try:
        s = _storage()
        timing = s.load(TIMING_FILE, default={})
        entry = timing.get(uid) if isinstance(timing, dict) else None
        if not entry:
            return
        seen_ts = datetime.fromisoformat(entry["seen_ts"])
        delay_hours = (datetime.now(timezone.utc) - seen_ts).total_seconds() / 3600
        sender = entry["sender"]

        delays = s.load("behavior_reply_delays.json", default={})
        if not isinstance(delays, dict):
            delays = {}
        delays.setdefault(sender, {"sender_raw": entry.get("sender_raw", sender), "delays": []})
        delays[sender]["delays"].append(round(delay_hours, 1))
        delays[sender]["delays"] = delays[sender]["delays"][-20:]  # останні 20 випадків
        s.save("behavior_reply_delays.json", delays)
    except Exception as e:
        print(f"[behavior_patterns] record_email_replied error: {e}", flush=True)


def analyze_patterns():
    """Періодична перевірка — якщо є відправник з хронічно повільними відповідями
    (≥3 випадки, середня затримка ≥48г) — АІ проактивно питає Олега чому,
    з дедуп щоб не питати про те саме частіше ніж раз на 2 тижні."""
    try:
        s = _storage()
        delays_data = s.load("behavior_reply_delays.json", default={})
        if not isinstance(delays_data, dict) or not delays_data:
            return
        flagged = s.load(PATTERNS_FILE, default={})
        if not isinstance(flagged, dict):
            flagged = {}

        import monitor as _mon
        now_ts = time.time()

        for sender_key, info in delays_data.items():
            delays = info.get("delays", [])
            if len(delays) < 3:
                continue
            avg_delay = sum(delays) / len(delays)
            if avg_delay < 48:
                continue
            last_flagged = flagged.get(sender_key, 0)
            if now_ts - last_flagged < 14 * 24 * 3600:  # раз на 2 тижні максимум
                continue

            sender_raw = info.get("sender_raw", sender_key)
            avg_days = avg_delay / 24
            msg = (
                "🔍 <b>АІ помітив патерн у твоїй поведінці:</b>\n\n"
                f"Ти постійно відкладаєш відповідь на листи від <b>{sender_raw}</b> — "
                f"в середньому на {avg_days:.1f} дні (за останні {len(delays)} листів). "
                "Це через низький пріоритет, незручний момент, чи щось інше? "
                "Якщо хочеш — можу позначати листи від цієї людини як менш термінові "
                "в майбутньому, або навпаки нагадувати частіше."
            )
            _mon.send_telegram(msg)
            flagged[sender_key] = now_ts

        s.save(PATTERNS_FILE, flagged)
    except Exception as e:
        print(f"[behavior_patterns] analyze error: {e}", flush=True)
