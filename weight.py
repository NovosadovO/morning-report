#!/usr/bin/env python3
"""
Трекер ваги — зберігає дані і показує динаміку.
"""

import os, json
from datetime import datetime, timezone, timedelta

WEIGHT_FILE = os.path.join("/tmp", "weight_data.json")
INITIAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weight_data_initial.json")


def load_data():
    # Спочатку /tmp, якщо немає — беремо initial з репозиторію
    try:
        with open(WEIGHT_FILE) as f:
            data = json.load(f)
        if data:
            return data
    except:
        pass
    try:
        with open(INITIAL_FILE) as f:
            data = json.load(f)
        # Копіюємо в /tmp для подальших записів
        save_data(data)
        return data
    except:
        return {}


def save_data(data):
    with open(WEIGHT_FILE, "w") as f:
        json.dump(data, f)


def today_key():
    return (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")


def save_weight(kg: float):
    data = load_data()
    data[today_key()] = kg
    save_data(data)


def get_trend():
    """Повертає рядок з динамікою ваги за останні 7 та 30 днів."""
    data = load_data()
    now = datetime.now(timezone.utc) + timedelta(hours=2)

    def avg_days(n):
        vals = []
        for i in range(n):
            k = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            if k in data:
                vals.append(data[k])
        return sum(vals) / len(vals) if vals else None

    today = data.get(today_key())
    avg7  = avg_days(7)
    avg30 = avg_days(30)

    lines = []
    if today:
        lines.append(f"⚖️ Сьогодні: <b>{today} кг</b>")
    if avg7:
        lines.append(f"📊 Середня за 7 днів: <b>{avg7:.1f} кг</b>")
    if avg30:
        lines.append(f"📅 Середня за 30 днів: <b>{avg30:.1f} кг</b>")

    # Тренд: порівнюємо тиждень тому з сьогодні
    week_ago_key = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    week_ago = data.get(week_ago_key)
    if today and week_ago:
        diff = today - week_ago
        if diff < -0.2:
            trend = f"📉 -{abs(diff):.1f} кг за тиждень"
        elif diff > 0.2:
            trend = f"📈 +{diff:.1f} кг за тиждень"
        else:
            trend = "➡️ Вага стабільна"
        lines.append(trend)

    return "\n".join(lines) if lines else None


def format_weekly_weight_report():
    """Тижневий звіт ваги."""
    data = load_data()
    now = datetime.now(timezone.utc) + timedelta(hours=2)

    days = []
    for i in range(6, -1, -1):
        d = now - timedelta(days=i)
        k = d.strftime("%Y-%m-%d")
        label = d.strftime("%d.%m")
        val = data.get(k)
        days.append((label, val))

    lines = ["⚖️ <b>Вага за тиждень</b>\n"]
    for label, val in days:
        if val:
            lines.append(f"  {label}  —  <b>{val} кг</b>")
        else:
            lines.append(f"  {label}  —  —")

    trend = get_trend()
    if trend:
        lines.append(f"\n{trend}")

    return "\n".join(lines)
