#!/usr/bin/env python3
"""
Week Planner 📅
Динамічний планер тижня з контролем перевантаження
- Дані з Google Calendar
- Аналіз вільного часу (таймбокс)
- Gemini-рекомендації для балансу
"""

import os
import json
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

try:
    from context import get_calendar_with_times
except ImportError:
    get_calendar_with_times = None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


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


def parse_calendar_events(cal_text):
    """Парсить текст календаря та витягує события на цей тиждень"""
    
    if not cal_text:
        return []
    
    events = []
    lines = cal_text.split('\n') if isinstance(cal_text, str) else []
    
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now_local.date()
    week_start = today - timedelta(days=today.weekday())  # Понеділок
    week_end = week_start + timedelta(days=6)  # Неділя
    
    for line in lines:
        line = line.strip()
        if not line or '🔔' not in line:
            continue
        
        # Спрощений парсинг
        # Очікуємо формат: "🔔 Назва события | Час | Дата"
        try:
            # Витягуємо час якщо є
            time_part = None
            if '|' in line:
                parts = line.split('|')
                name = parts[0].replace('🔔', '').strip()
                time_part = parts[1].strip() if len(parts) > 1 else None
            else:
                name = line.replace('🔔', '').strip()
            
            events.append({
                "name": name,
                "time": time_part,
                "duration_hours": 1  # Припущення
            })
        except Exception:
            pass
    
    return events


def calculate_week_load():
    """Рахує навантаженість тижня"""
    
    # Орієнтовна тривалість для різних типів подій
    event_durations = {
        "зміна": 12,  # 12 годин
        "нічна зміна": 12,
        "рання зміна": 12,
        "нічна": 12,
        "рання": 12,
        "робота": 8,
        "зустріч": 1,
        "дерматолог": 1.5,
        "зубна": 1,
        "тренування": 1.5,
        "біг": 1,
        "холодний душ": 0.25,
        "стка": 0.5,
    }
    
    try:
        if not get_calendar_with_times:
            return None
        
        cal_text = get_calendar_with_times()
        events = parse_calendar_events(cal_text)
        
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        today = now_local.date()
        week_start = today - timedelta(days=today.weekday())
        
        # Рахуємо на 7 днів
        total_work_hours = 0
        
        for event in events:
            name_lower = event['name'].lower()
            
            # Знайдемо тривалість
            duration = 1  # default
            for key, dur in event_durations.items():
                if key in name_lower:
                    duration = dur
                    break
            
            total_work_hours += duration
        
        # Доступний час на тиждень (без сну)
        # 7 днів * 24 години - 7 днів * 7 годин сну = 119 годин
        available_hours = 7 * 24 - 7 * 7  # 119 годин
        
        pct_occupied = int(total_work_hours / available_hours * 100)
        pct_free = 100 - pct_occupied
        
        return {
            "total_work_hours": total_work_hours,
            "available_hours": available_hours,
            "pct_occupied": min(pct_occupied, 100),
            "pct_free": max(pct_free, 0),
            "events_count": len(events)
        }
    
    except Exception as e:
        print(f"⚠️ Week load calc error: {e}")
        return None


def format_week_plan():
    """Форматує план тижня"""
    
    load = calculate_week_load()
    if not load:
        return "📅 <b>ПЛАН ТИЖНЯ</b>\n⚠️ Календар недоступний"
    
    lines = []
    lines.append(f"📅 <b>ПЛАН ТИЖНЯ</b>")
    lines.append(f"События: <b>{load['events_count']}</b>")
    lines.append(f"Робочих годин: <b>{load['total_work_hours']:.1f}</b> з {load['available_hours']}")
    lines.append("")
    
    # Прогрес-бар для зайнятості
    occupied = load['pct_occupied']
    free = load['pct_free']
    
    filled = int(occupied / 5)  # 100% = 20 chars
    bar = "🔴" * (occupied // 10) + "🟢" * (free // 10)
    
    lines.append(f"Зайнятість: {bar}")
    lines.append(f"<b>{occupied}%</b> робочо | <b>{free}%</b> вільно")
    lines.append("")
    
    # Рекомендація
    if occupied >= 80:
        lines.append("⚠️ <b>ПЕРЕГРУЖЕНО!</b> Залиш 20% на несподіване.")
        status = "CRITICAL"
    elif occupied >= 70:
        lines.append("🟠 Насичено. Будь готовий до непередбачених подій.")
        status = "HIGH"
    elif occupied >= 50:
        lines.append("🟡 Добре збалансовано.")
        status = "BALANCED"
    else:
        lines.append("🟢 Багато вільного часу! Час на розвиток.")
        status = "RELAXED"
    
    return "\n".join(lines), status


def generate_week_recommendations():
    """Генеріює рекомендації для балансу тижня через Gemini"""
    
    if not GEMINI_API_KEY:
        return None
    
    load = calculate_week_load()
    if not load:
        return None
    
    occupied = load['pct_occupied']
    free = load['pct_free']
    events = load['events_count']
    
    # Аналізуємо тип тижня
    if occupied >= 80:
        week_type = "ПЕРЕГРУЖЕНА"
        action = "потребує скорочення / делегування"
    elif occupied >= 70:
        week_type = "НАСИЧЕНА"
        action = "потребує уважного планування"
    elif occupied >= 50:
        week_type = "ЗБАЛАНСОВАНА"
        action = "хороша для вивчення нових речей"
    else:
        week_type = "ВІЛЬНА"
        action = "світ у твоїх руках, fokus на розвитку"
    
    prompt = f"""Ти — тайм-менеджер Олега. Дай рекомендацію на цей тиждень:

📊 АНАЛІЗ ТИЖНЯ:
- Тип: {week_type} ({occupied}% занято)
- События: {events}
- Вільний час: {free}% ({load['available_hours'] - load['total_work_hours']:.0f} годин)

💡 РЕКОМЕНДАЦІЯ:
[Одна конкретна стратегія на цей тиждень]

🎯 ТАЙМБОКС:
[Як розподілити вільний час на розвиток / відпочинок]

СТИЛЬ: Практичний, мотивуючий (2-3 пропозиції, емодзі)
МОВА: Українська"""

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
    
    text = _gem_post(url, body, "week_recommendations")
    
    if text:
        return text
    else:
        if occupied >= 80:
            return "⚠️ Тиждень перевантажена! Скуп час для себе. Делегуй что можна, откажись от ненужного."
        else:
            return "🎯 Тиждень в твоїх руках. Фокусуйся на найважливішому!"


def get_week_planner_block():
    """Повертає блок для звіту"""
    
    plan, status = format_week_plan()
    
    recommendations = generate_week_recommendations()
    
    block = f"""{plan}

🤖 <b>AI-рекомендація:</b>
{recommendations}"""
    
    return block


if __name__ == "__main__":
    print(get_week_planner_block())
