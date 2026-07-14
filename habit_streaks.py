#!/usr/bin/env python3
"""
habit_streaks.py — Комбінований стрік звичок + гейміфікація.

"Win day" = день, коли з усіх звичок, які того дня взагалі відмічались,
НЕМАЄ жодної відміченої як False (зрив). Дні без жодної відмітки не рахуються
ні в плюс, ні в мінус — просто пропускаються при підрахунку стріку.

Стрік рахується від СЬОГОДНІ (якщо сьогодні вже щось відмічено і без зривів)
або від ВЧОРА назад, якщо сьогодні ще нічого не відмічено.
"""

import os
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "2100366814")

_MILESTONES = [3, 5, 7, 10, 14, 21, 30, 50, 75, 100, 150, 200, 365]
_BADGES = {
    3: "🔥", 5: "⚡", 7: "🌟", 10: "💫", 14: "💪", 21: "🏆",
    30: "👑", 50: "🚀", 75: "💎", 100: "🏔", 150: "🌌", 200: "🛸", 365: "🎇",
}


def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)


def _day_result(day_data: dict) -> str:
    """'win' | 'break' | 'none' — результат конкретного дня."""
    if not day_data:
        return "none"
    # sleep/mood/steps тощо — не звички, ігноруємо; беремо тільки bool-значення
    bool_vals = [v for v in day_data.values() if isinstance(v, bool)]
    if not bool_vals:
        return "none"
    return "break" if any(v is False for v in bool_vals) else "win"


def compute_streak(habits_db: dict) -> dict:
    """Повертає {'current': int, 'best': int}."""
    now = _now_local()
    current = 0
    best = 0
    running = 0
    # Рахуємо current: йдемо назад від сьогодні, пропускаючи 'none' дні,
    # зупиняємось на першому 'break'.
    day = now
    started = False
    for _ in range(400):
        key = day.strftime("%Y-%m-%d")
        result = _day_result(habits_db.get(key, {}))
        if result == "win":
            current += 1
            started = True
        elif result == "break":
            break
        # 'none' — пропускаємо, йдемо далі назад (тільки якщо ще не почали рахувати,
        # інакше 'none' в середині теж не рве стрік, просто не додає)
        day -= timedelta(days=1)

    # Рахуємо best (найдовший стрік за всю історію)
    all_days = sorted(habits_db.keys())
    for key in all_days:
        result = _day_result(habits_db.get(key, {}))
        if result == "win":
            running += 1
            best = max(best, running)
        elif result == "break":
            running = 0
        # 'none' не рве running

    best = max(best, current)
    return {"current": current, "best": best}


def _load_state():
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import load
        return load("habit_streak_state.json", default={})
    except Exception:
        return {}


def _save_state(state):
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import save
        save("habit_streak_state.json", state)
    except Exception as e:
        print(f"[habit_streaks] save_state error: {e}")


def _send(text):
    if not TELEGRAM_TOKEN:
        return
    try:
        import json, urllib.request
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        body = json.dumps({"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[habit_streaks] send error: {e}")


def check_and_celebrate_milestone():
    """Викликати одразу після того як Олег відмітив звичку. Святкує НОВИЙ досягнутий мілстоун 1 раз."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import load_habits
        habits_db = load_habits()
        streak = compute_streak(habits_db)
        current = streak["current"]

        state = _load_state()
        last_celebrated = state.get("last_celebrated_milestone", 0)

        hit = None
        for ms in _MILESTONES:
            if current >= ms and ms > last_celebrated:
                hit = ms
        if hit:
            badge = _BADGES.get(hit, "🔥")
            _send(
                f"{badge} <b>СТРІК {hit} ДНІВ ПОСПІЛЬ!</b>\n\n"
                f"Ти тримаєш звички без зривів вже {hit} {'день' if hit == 1 else 'днів'} поспіль. "
                f"Рекорд: {streak['best']} днів. Це не випадковість — це дисципліна, яка вже стає звичкою. Так тримати! 💪"
            )
            state["last_celebrated_milestone"] = hit
            _save_state(state)
        elif current == 0 and last_celebrated > 0:
            # Стрік перервався — скидаємо, щоб наступний раз знову святкувати з 3
            state["last_celebrated_milestone"] = 0
            _save_state(state)
    except Exception as e:
        print(f"[habit_streaks] check_and_celebrate_milestone error: {e}")


def check_streak_risk():
    """Викликати ввечері (~21:00): якщо стрік ≥3 і сьогодні ще нічого не відмічено — попередити."""
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from storage import load_habits, load
        now = _now_local()
        if not (21 <= now.hour < 23):
            return

        state = _load_state()
        today_key = now.strftime("%Y-%m-%d")
        if state.get("last_risk_warned") == today_key:
            return

        habits_db = load_habits()
        today_result = _day_result(habits_db.get(today_key, {}))
        if today_result != "none":
            return  # вже щось відмітив сьогодні

        # Стрік станом на вчора (бо сьогодні ще 'none')
        streak = compute_streak(habits_db)
        if streak["current"] >= 3:
            _send(
                f"⚠️ <b>Стрік {streak['current']} днів під загрозою!</b>\n\n"
                f"Ти ще не відмітив жодної звички сьогодні. До кінця дня лишається небагато часу — "
                f"не дай стріку перерватись, відміть хоча б холодний душ/воду зараз 💧"
            )
        state["last_risk_warned"] = today_key
        _save_state(state)
    except Exception as e:
        print(f"[habit_streaks] check_streak_risk error: {e}")
