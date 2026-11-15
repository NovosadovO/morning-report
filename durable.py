"""durable.py — стан дедупу, що переживає редеплой.

Локальні файли data/*.json на Railway зникають при кожному деплої, і одразу
після нього йшли дублі (звіт-картки, нагадування про події, проактивні).
Тут: пишемо і локально, і в гілку `data` (ai_kit/storage); читаємо спершу
гілку data, якщо там нема — локальний файл.
"""
import json
import os

import ai_kit as K

TAG = "durable"


def _read_local(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def load(name, local_path=None):
    """dict зі стану. name — ім'я файлу в гілці data (напр. 'report_card_sent.json')."""
    try:
        d = K.load(name, default=None)
        if isinstance(d, dict) and d:
            return d
    except Exception:
        pass
    return _read_local(local_path) if local_path else {}


def save(name, data, local_path=None, keep=400):
    """Пише в обидва місця. Великі словники підрізає до останніх `keep` ключів."""
    try:
        if isinstance(data, dict) and len(data) > keep:
            data = dict(list(data.items())[-keep:])
    except Exception:
        pass
    if local_path:
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            K.log(TAG, f"local save {name}: {e}")
    try:
        return bool(K.save(name, data))
    except Exception as e:
        K.log(TAG, f"save {name}: {e}")
        return False
