#!/usr/bin/env python3
"""
health_parser.py — парсер текстового формату здоров'я від Олега.
Розпізнає: Кроки, Сон (глибокий+легкий), Пульс, HRV, Стрес, Калорії, Біг.
"""
import re
from datetime import datetime, timezone, timedelta

def parse_health_text(text: str) -> dict:
    """
    Парсить вільний текстовий формат здоров'я.
    Повертає dict з ключами: steps, sleep_hours, sleep_deep, sleep_light, 
    hr, hrv, stress, calories, running_km
    """
    result = {
        "steps": None,
        "sleep_hours": None,
        "sleep_deep": None,
        "sleep_light": None,
        "hr": None,
        "hrv": None,
        "stress": None,
        "calories": None,
        "running_km": None,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    
    text_lower = text.lower()
    
    # ── КРОКИ ──
    # Приклади: "23 063 кроки", "23063 кроки", "steps: 23063"
    match = re.search(r'(?:кроки|steps)[:\s]*[\(]*(\d[\d\s]*\d)(?:\s|к|шт)?', text_lower)
    if match:
        steps_str = match.group(1).replace(' ', '')
        try:
            result["steps"] = int(steps_str)
        except:
            pass
    
    # ── СОН ──
    # Приклади: "4 години 37 хвилин", "4:37", "4 год 37 хв"
    match = re.search(r'(?:сон)[:\s]*(\d+)\s*(?:год|h)[:\s]*(\d+)\s*(?:хв|м|min)', text_lower)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        result["sleep_hours"] = hours + minutes / 60
    else:
        # Спробувати формат "4:37"
        match = re.search(r'(?:сон)[:\s]*(\d+):(\d+)', text_lower)
        if match:
            result["sleep_hours"] = int(match.group(1)) + int(match.group(2)) / 60
    
    # ── ГЛИБОКИЙ СОН ──
    # Приклади: "2 год 31 хв", "2:31"
    match = re.search(r'(?:глибокий|deep)[:\s]*(?:сон)?[:\s]*(\d+)\s*(?:год|h)[:\s]*(\d+)', text_lower)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        result["sleep_deep"] = hours + minutes / 60
    
    # ── ЛЕГКИЙ СОН ──
    match = re.search(r'(?:легкий|light)[:\s]*(?:сон)?[:\s]*(\d+)\s*(?:год|h)[:\s]*(\d+)', text_lower)
    if match:
        hours = int(match.group(1))
        minutes = int(match.group(2))
        result["sleep_light"] = hours + minutes / 60
    
    # ── ПУЛЬС / HEART RATE ──
    # Приклади: "77 уд/хв", "пульс: 77", "hr: 77"
    match = re.search(r'(?:пульс|heart rate|hr)[:\s]*(\d+)', text_lower)
    if match:
        try:
            result["hr"] = int(match.group(1))
        except:
            pass
    
    # ── HRV ──
    # Приклади: "HRV: 39", "варіабельність: 39"
    match = re.search(r'(?:hrv|варіабельність)[:\s]*(\d+)', text_lower)
    if match:
        try:
            result["hrv"] = int(match.group(1))
        except:
            pass
    
    # ── СТРЕС ──
    # Приклади: "стрес: 30", "stress: 30"
    match = re.search(r'(?:стрес|stress)[:\s]*(\d+)', text_lower)
    if match:
        try:
            result["stress"] = int(match.group(1))
        except:
            pass
    
    # ── КАЛОРІЇ ──
    # Приклади: "980 калорій", "calories: 980"
    match = re.search(r'(?:калорії|calories)[:\s]*(\d+)', text_lower)
    if match:
        try:
            result["calories"] = int(match.group(1))
        except:
            pass
    
    # ── БІГ ──
    # Приклади: "5 км біг", "running: 5km"
    match = re.search(r'(?:біг|running)[:\s]*(\d+(?:[.,]\d+)?)\s*(?:км|km)', text_lower)
    if match:
        try:
            result["running_km"] = float(match.group(1).replace(',', '.'))
        except:
            pass
    
    # Фільтруємо None значення
    return {k: v for k, v in result.items() if v is not None}


def save_daily_health(data: dict, file_path: str = "daily_health.json"):
    """Зберігає дані здоров'я у JSON (дата → показники)"""
    import json
    import os
    
    # Читаємо існуючі дані
    existing = {}
    if os.path.exists(file_path):
        try:
            with open(file_path) as f:
                existing = json.load(f)
        except:
            pass
    
    # Додаємо/оновлюємо дані для дата
    date_key = data.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    existing[date_key] = data
    
    # Зберігаємо назад
    with open(file_path, 'w') as f:
        json.dump(existing, f, indent=2)
    
    return date_key


def load_daily_health(file_path: str = "daily_health.json") -> dict:
    """Читає всі дані здоров'я з JSON"""
    import json
    import os
    
    if not os.path.exists(file_path):
        return {}
    
    try:
        with open(file_path) as f:
            return json.load(f)
    except:
        return {}


if __name__ == "__main__":
    # ТЕСТ
    sample_text = """
    Фізична активність (Кроки): 23 063 кроки.
    Сон: 4 години 37 хвилин (Глибокий сон: 2 год 31 хв, легкий сон: 2 год 6 хв).
    Пульс (Heart Rate): Середній показник становить 77 уд/хв.
    Варіабельність серцевого ритму (HRV): 39.
    Рівень стресу: 30.
    Спалені калорії: 980
    """
    
    parsed = parse_health_text(sample_text)
    print("Parsed health data:")
    for k, v in parsed.items():
        print(f"  {k}: {v}")
