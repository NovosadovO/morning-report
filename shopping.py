#!/usr/bin/env python3
"""
Список покупок.
Дані зберігаються через storage.py → GitHub.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SHOPPING_FILE = "shopping_list.json"


def _storage():
    import storage
    return storage


def load_shopping() -> dict:
    """Повертає {'items': [{'text': str, 'done': bool}], 'date': str}"""
    s = _storage()
    data = s.load(SHOPPING_FILE)
    if not data or not isinstance(data, dict):
        return {"items": [], "date": ""}
    if "items" not in data:
        data["items"] = []
    return data


def save_shopping(data: dict):
    _storage().save(SHOPPING_FILE, data)


def add_items(text: str) -> list:
    """
    Парсить текст через кому або крапку з комою,
    додає нові пункти в список (якщо ще немає).
    Повертає список доданих назв.
    """
    import re
    from datetime import date

    parts = re.split(r"[,;]+", text)
    cleaned = [p.strip().strip(".").strip() for p in parts if p.strip()]
    cleaned = [c for c in cleaned if c]

    data = load_shopping()
    existing = {i["text"].lower() for i in data["items"]}

    added = []
    for name in cleaned:
        if name.lower() not in existing:
            data["items"].append({"text": name, "done": False})
            existing.add(name.lower())
            added.append(name)

    if not data.get("date"):
        data["date"] = date.today().isoformat()

    save_shopping(data)
    return added


def get_items() -> list:
    """Повертає [{'text': str, 'done': bool}]"""
    return load_shopping()["items"]


def mark_all_done():
    data = load_shopping()
    for item in data["items"]:
        item["done"] = True
    save_shopping(data)


def mark_item(index: int, done: bool):
    data = load_shopping()
    if 0 <= index < len(data["items"]):
        data["items"][index]["done"] = done
    save_shopping(data)


def get_uncompleted() -> list:
    """Повертає список тексту незавершених пунктів."""
    return [i["text"] for i in get_items() if not i["done"]]


def clear_list():
    """Очищає список повністю."""
    from datetime import date
    save_shopping({"items": [], "date": date.today().isoformat()})


def format_list(items: list) -> str:
    """Форматує список для показу в Telegram."""
    if not items:
        return "Список порожній."
    lines = []
    for i, item in enumerate(items):
        mark = "✅" if item["done"] else "⬜"
        lines.append(f"{mark} {item['text']}")
    return "\n".join(lines)
