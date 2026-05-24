#!/usr/bin/env python3
"""
Трекер ваги — зберігає дані через GitHub storage (persistent).
"""

import os, json
from datetime import datetime, timezone, timedelta
import storage


WEIGHT_FILE = "weight_data.json"


def load_data():
    data = storage.load(WEIGHT_FILE)
    if data and isinstance(data, dict):
        return data
    # fallback: локальний initial файл
    try:
        initial = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weight_data_initial.json")
        with open(initial) as f:
            d = json.load(f)
        if d:
            storage.save(WEIGHT_FILE, d)
            return d
    except Exception:
        pass
    return {}


def save_data(data):
    storage.save(WEIGHT_FILE, data)


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


def make_weight_chart(days=30) -> bytes | None:
    """Графік ваги за N днів."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import io

        data = load_data()
        now = datetime.now(timezone.utc) + timedelta(hours=2)

        dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
        weights = [data.get(d) for d in dates]
        labels = [(now - timedelta(days=i)).strftime("%d.%m") for i in range(days - 1, -1, -1)]

        # Фільтруємо тільки де є дані
        x_vals, y_vals, x_labels = [], [], []
        for i, (d, w) in enumerate(zip(dates, weights)):
            if w is not None:
                x_vals.append(i)
                y_vals.append(w)
                x_labels.append(labels[i])

        if len(y_vals) < 2:
            return None

        fig, ax = plt.subplots(figsize=(10, 5), facecolor="#1a1a2e")
        ax.set_facecolor("#16213e")

        ax.plot(x_vals, y_vals, color="#4ecdc4", linewidth=2.5,
                marker="o", markersize=6, markerfacecolor="white")
        ax.fill_between(x_vals, y_vals, min(y_vals) - 1, alpha=0.15, color="#4ecdc4")

        # Середня лінія
        avg = sum(y_vals) / len(y_vals)
        ax.axhline(y=avg, color="#ffd700", linestyle="--", alpha=0.7,
                   linewidth=1.5, label=f"Середня: {avg:.1f} кг")

        # Підписи точок
        for xi, yi in zip(x_vals, y_vals):
            ax.annotate(f"{yi}", (xi, yi), textcoords="offset points",
                        xytext=(0, 8), ha="center", color="white", fontsize=8)

        # Підписи осі X — кожні 3 дні
        tick_indices = x_vals[::3]
        tick_labels = [x_labels[x_vals.index(i)] for i in tick_indices]
        ax.set_xticks(tick_indices)
        ax.set_xticklabels(tick_labels, color="white", fontsize=8)

        ax.tick_params(colors="white")
        ax.spines[:].set_color("#333355")
        ax.set_ylabel("кг", color="white")
        title = f"Вага — останні {days} днів"
        ax.set_title(title, color="white", fontsize=13, fontweight="bold")
        ax.legend(facecolor="#16213e", labelcolor="white", fontsize=9)

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="#1a1a2e")
        plt.close(fig)
        return buf.getvalue()

    except Exception as e:
        print(f"weight chart error: {e}")
        return None
