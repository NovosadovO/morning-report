#!/usr/bin/env python3
"""
Weight Coach 🏋️
AI-коуч для схуднення Олега
- Мета: 78 кг (зараз ~83-84 кг)
- Трекування тренду (вниз/вгору/стабільно)
- Дефіцит калорій + темп схуднення
- Gemini-мотивація щодня
"""

import os
import json
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

try:
    from storage import load as storage_load
except ImportError:
    storage_load = None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ─── КОНФІГ ──────────────────────────────────────────────────────────────────
WEIGHT_GOAL = 78.0  # кг
WEIGHT_CURRENT_APPROX = 83.0  # орієнтовна поточна
DEFICIT_PER_WEEK = 0.5  # кг/тиждень (здорова швидкість)


def _gem_post(url, body, tag):
    """Простий запит до Gemini"""
    import urllib.request
    import json as _json
    
    try:
        data = _json.dumps(body).encode('utf-8')
        req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            response = _json.loads(resp.read().decode())
            try:
                text = response['candidates'][0]['content']['parts'][0].get('text', '')
                return text if text else None
            except (KeyError, IndexError):
                return None
    except Exception as e:
        print(f"⚠️ Gemini error [{tag}]: {e}")
        return None


def load_weight_history():
    """Завантажує історію ваги з GitHub"""
    if not storage_load:
        return {}
    
    try:
        weight_data = storage_load("weight_data.json") or storage_load("weight.json") or {}
        return weight_data if isinstance(weight_data, dict) else {}
    except Exception:
        return {}


def get_current_weight():
    """Витягує поточну вагу"""
    history = load_weight_history()
    if not history:
        return None
    
    # Найновіша запис
    latest_date = max(history.keys()) if history else None
    if latest_date:
        return float(history[latest_date]), latest_date
    
    return None


def calculate_weight_progress():
    """Рахує прогрес схуднення"""
    
    history = load_weight_history()
    if not history or len(history) < 2:
        return None
    
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    today_str = now_local.strftime("%Y-%m-%d")
    
    # Останні записи (сортуємо по даті)
    sorted_dates = sorted(history.keys())
    latest_weight = float(history[sorted_dates[-1]])
    earliest_weight = float(history[sorted_dates[0]])
    
    # Тренд
    weight_loss = earliest_weight - latest_weight
    days_tracked = len(sorted_dates)
    weekly_loss = (weight_loss / days_tracked) * 7 if days_tracked > 0 else 0
    
    # До мети
    remaining = latest_weight - WEIGHT_GOAL
    weeks_to_goal = remaining / DEFICIT_PER_WEEK if remaining > 0 else 0
    
    # % прогресу
    total_to_lose = WEIGHT_CURRENT_APPROX - WEIGHT_GOAL
    lost_so_far = WEIGHT_CURRENT_APPROX - latest_weight
    pct_progress = int((lost_so_far / total_to_lose * 100)) if total_to_lose > 0 else 0
    pct_progress = min(pct_progress, 100)
    
    return {
        "current": latest_weight,
        "goal": WEIGHT_GOAL,
        "remaining": max(remaining, 0),
        "lost_total": lost_so_far,
        "weight_loss_weekly": weekly_loss,
        "days_tracked": days_tracked,
        "weeks_to_goal": max(int(weeks_to_goal), 0),
        "pct_progress": pct_progress,
        "trend": "📉 Вниз!" if weight_loss > 0 else ("📈 Вгору!" if weight_loss < 0 else "➡️ Стабільно"),
        "latest_date": sorted_dates[-1] if sorted_dates else None
    }


def format_weight_report():
    """Форматує красивий звіт про вагу"""
    
    progress = calculate_weight_progress()
    if not progress:
        current = get_current_weight()
        if current:
            weight, date = current
            return f"""🏋️ <b>ВАГА</b>
<b>{weight} кг</b> (записано {date})
Мета: 78 кг
Добавте більше записів для аналізу"""
        return "⚠️ Дані про вагу недоступні"
    
    lines = []
    lines.append(f"🏋️ <b>СХУДНЕННЯ</b>")
    lines.append(f"Поточна: <b>{progress['current']} кг</b>")
    lines.append(f"Мета: <b>{progress['goal']} кг</b>")
    lines.append("")
    
    # Прогрес-бар
    pct = progress['pct_progress']
    filled = int(pct / 5)  # 100% = 20 chars
    bar = "█" * filled + "░" * (20 - filled)
    lines.append(f"{bar} {pct}%")
    lines.append(f"Втрачено: <b>{progress['lost_total']:+.1f} кг</b>")
    lines.append("")
    
    lines.append(f"{progress['trend']}")
    lines.append(f"Темп: <b>{progress['weight_loss_weekly']:+.2f} кг/тиждень</b>")
    
    if progress['remaining'] > 0:
        lines.append(f"Залишилось: <b>{progress['remaining']:.1f} кг</b>")
        lines.append(f"Темп: <b>~{progress['weeks_to_goal']} тижнів</b> до мети")
    else:
        lines.append(f"✅ <b>МЕТА ДОСЯГНУТА!</b> 🎉")
    
    lines.append(f"<i>Відслідковано: {progress['days_tracked']} днів</i>")
    
    return "\n".join(lines)


def get_3day_trend():
    """Чи 3 дні вага не зменшувалась? (потребує мотивації)"""
    history = load_weight_history()
    if len(history) < 3:
        return False
    
    sorted_dates = sorted(history.keys())
    last_3_weights = [float(history[d]) for d in sorted_dates[-3:]]
    
    # Якщо всі 3 дні вага однакова або вгору
    return all(last_3_weights[i] >= last_3_weights[i-1] for i in range(1, 3))


def generate_daily_motivation():
    """Генеріює щоденну мотивацію через Gemini"""
    
    if not GEMINI_API_KEY:
        return None
    
    progress = calculate_weight_progress()
    if not progress:
        return None
    
    current = progress['current']
    goal = progress['goal']
    pct = progress['pct_progress']
    trend_text = progress['trend']
    weekly_loss = progress['weight_loss_weekly']
    
    # Контекст
    is_stagnation = get_3day_trend()
    stagnation_context = "\n⚠️ КОНТЕКСТ: 3 дні вага не зменшувалась — потребує мотивації!" if is_stagnation else ""
    
    prompt = f"""Ти — персональний коуч Олега по схуднену. Мотивуй його сьогодні:

📊 ЙОГО ДАНІ:
- Поточна вага: {current} кг
- Мета: {goal} кг
- Прогрес: {pct}%
- Темп: {weekly_loss:.2f} кг/тиждень
- Тренд: {trend_text}{stagnation_context}

🎯 НАПИШИ (3-4 рядки, теплий тон):
1. Что вышло отлично сегодня (хвали)
2. Одна конкретная дія для дня
3. Мотивуючий висновок

СТИЛЬ: Теплий, реалістичний, підтримуючий
МОВА: Українська
ЕМОДЗІ: Добавити кілька"""

    body = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 1,
            "maxOutputTokens": 400,
            "thinkingConfig": {"thinkingBudget": 0}
        }
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    text = _gem_post(url, body, "weight_motivation")
    
    if text:
        return text
    elif is_stagnation:
        return """💪 Ты на правильном пути! Плато — нормально.
Даже без изменений на весах твоё тело трансформируется.
Пей воду, спи 7-8 часов, продолжай. Ты сделаешь это! 🚀"""
    else:
        return "💪 Молодець за дисципліну! Продовжуй в тому ж дусі 🔥"


def get_weight_coach_block():
    """Повертає блок для звіту"""
    
    report = format_weight_report()
    
    motivation = generate_daily_motivation()
    
    block = f"""{report}

🤖 <b>Gemini-мотивація:</b>
{motivation}"""
    
    return block


if __name__ == "__main__":
    print(get_weight_coach_block())
