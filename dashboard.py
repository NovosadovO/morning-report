#!/usr/bin/env python3
"""
Dashboard — один великий дешборд для звіту
Секції: Здоров'я (вага, біг, кроки, сон, HRV) + Strava + Звички
Розмір: Ultra HD 2000x3000
Оновлюється щогодини
"""

import os
import io
from datetime import datetime, timedelta
from PIL import Image, ImageDraw, ImageFont
import json

# ═════════════════════════════════════════════════════════════════════════════
# COLORS & FONTS
# ═════════════════════════════════════════════════════════════════════════════

BG_COLOR = (13, 27, 42)          # Темно-синій фон
HEADER_COLOR = (255, 255, 255)   # Білі заголовки
TEXT_COLOR = (200, 200, 200)     # Світло-сірий текст
ACCENT_COLOR = (52, 152, 219)    # Блакитний акцент
SUCCESS_COLOR = (46, 204, 113)   # Зелений (досягнення)
WARNING_COLOR = (230, 126, 34)   # Помаранчевий (близько до цілі)
DANGER_COLOR = (231, 76, 60)     # Червоний (не на цілі)

TITLE_FONT_SIZE = 72
SECTION_FONT_SIZE = 48
VALUE_FONT_SIZE = 42
SMALL_FONT_SIZE = 32

# ═════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def get_fonts():
    """Завантажити шрифти або використовувати default"""
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", TITLE_FONT_SIZE)
        section_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", SECTION_FONT_SIZE)
        value_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", VALUE_FONT_SIZE)
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", SMALL_FONT_SIZE)
    except:
        title_font = ImageFont.load_default()
        section_font = ImageFont.load_default()
        value_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
    return title_font, section_font, value_font, small_font

def load_health_data():
    """Завантажити дані про здоров'я"""
    try:
        from storage import load_weight, load_running
        weight_data = load_weight() or {}
        running_data = load_running() or {}
        return weight_data, running_data
    except:
        return {}, {}

def load_habits_data():
    """Завантажити дані про звички"""
    try:
        from storage import load_habits
        habits_db = load_habits() or {}
        today_str = datetime.now().strftime("%Y-%m-%d")
        return habits_db.get(today_str, {})
    except:
        return {}

def load_strava_stats():
    """Завантажити Strava статистику (цей тиждень)"""
    try:
        from storage import load_strava_cache
        cache = load_strava_cache() or {}
        
        week_stats = cache.get("week_stats", {})
        month_stats = cache.get("month_stats", {})
        
        return week_stats, month_stats
    except:
        return {}, {}

def get_weight_color(current_weight, goal_weight=75):
    """Визначити колір для ваги (зелений/помаранчевий/червоний)"""
    if current_weight <= goal_weight:
        return SUCCESS_COLOR
    elif current_weight <= goal_weight + 3:
        return WARNING_COLOR
    else:
        return DANGER_COLOR

def get_progress_bar(current, goal, width=300, height=30):
    """Генерувати прогрес-бар як Image"""
    img = Image.new('RGB', (width, height), (50, 50, 50))
    draw = ImageDraw.Draw(img)
    
    if goal > 0:
        progress = min(current / goal, 1.0) * width
        draw.rectangle([(0, 0), (progress, height)], fill=SUCCESS_COLOR)
    
    draw.rectangle([(0, 0), (width, height)], outline=TEXT_COLOR, width=2)
    return img

# ═════════════════════════════════════════════════════════════════════════════
# SECTION RENDERERS
# ═════════════════════════════════════════════════════════════════════════════

def render_health_section(draw, pos_y, title_font, section_font, value_font, small_font):
    """Рендеринг секції Здоров'я (вага, біг, кроки, сон, HRV)"""
    x_start = 50
    line_height = 120
    
    # Заголовок
    draw.text((x_start, pos_y), "📊 ЗДОРОВ'Я", fill=HEADER_COLOR, font=section_font)
    pos_y += 80
    
    # Завантажити дані
    weight_data, running_data = load_health_data()
    
    # ВАГА
    if weight_data:
        last_date = sorted(weight_data.keys())[-1]
        current_weight = weight_data[last_date]
        goal_weight = 75
        color = get_weight_color(current_weight, goal_weight)
        
        draw.text((x_start, pos_y), f"⚖️  Вага: {current_weight} кг (ціль {goal_weight} кг)", 
                 fill=color, font=value_font)
        pos_y += line_height
    else:
        draw.text((x_start, pos_y), "⚖️  Вага: немає даних", fill=TEXT_COLOR, font=value_font)
        pos_y += line_height
    
    # БІГ (за цей тиждень)
    if running_data:
        week_ago = datetime.now() - timedelta(days=7)
        week_runs = [v for d, v in running_data.items() 
                     if datetime.strptime(d, "%Y-%m-%d") >= week_ago]
        if week_runs:
            total_km = sum(week_runs)
            avg_speed = sum(week_runs) / len(week_runs)
            draw.text((x_start, pos_y), f"🏃 Біг тиждень: {total_km:.1f} км ({len(week_runs)} пробіжок)", 
                     fill=ACCENT_COLOR, font=value_font)
        else:
            draw.text((x_start, pos_y), "🏃 Біг тиждень: 0 км", fill=TEXT_COLOR, font=value_font)
    else:
        draw.text((x_start, pos_y), "🏃 Біг тиждень: немає даних", fill=TEXT_COLOR, font=value_font)
    pos_y += line_height
    
    # КРОКИ (сьогодні)
    try:
        from storage import load_steps
        steps_data = load_steps() or {}
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_steps = steps_data.get(today_str, 0)
        goal_steps = 10000
        
        if today_steps >= goal_steps:
            color = SUCCESS_COLOR
        elif today_steps >= 5000:
            color = WARNING_COLOR
        else:
            color = DANGER_COLOR
        
        draw.text((x_start, pos_y), f"👣 Кроки: {today_steps} / {goal_steps}", 
                 fill=color, font=value_font)
    except:
        draw.text((x_start, pos_y), "👣 Кроки: немає даних", fill=TEXT_COLOR, font=value_font)
    pos_y += line_height
    
    # СОН (сьогодні)
    try:
        from storage import load_sleep
        sleep_data = load_sleep() or {}
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_sleep = sleep_data.get(today_str, 0)
        goal_sleep = 8
        
        if today_sleep >= goal_sleep:
            color = SUCCESS_COLOR
        elif today_sleep >= 6:
            color = WARNING_COLOR
        else:
            color = DANGER_COLOR
        
        draw.text((x_start, pos_y), f"😴 Сон: {today_sleep} / {goal_sleep} годин", 
                 fill=color, font=value_font)
    except:
        draw.text((x_start, pos_y), "😴 Сон: немає даних", fill=TEXT_COLOR, font=value_font)
    pos_y += line_height
    
    # HRV (Heart Rate Variability)
    try:
        from storage import load_hrv
        hrv_data = load_hrv() or {}
        today_str = datetime.now().strftime("%Y-%m-%d")
        today_hrv = hrv_data.get(today_str, None)
        
        if today_hrv:
            if today_hrv >= 50:
                color = SUCCESS_COLOR
                status = "Відпочила"
            elif today_hrv >= 30:
                color = WARNING_COLOR
                status = "Норма"
            else:
                color = DANGER_COLOR
                status = "Стрес"
            
            draw.text((x_start, pos_y), f"💚 HRV: {today_hrv} мс ({status})", 
                     fill=color, font=value_font)
        else:
            draw.text((x_start, pos_y), "💚 HRV: немає даних", fill=TEXT_COLOR, font=value_font)
    except:
        draw.text((x_start, pos_y), "💚 HRV: немає даних", fill=TEXT_COLOR, font=value_font)
    pos_y += line_height
    
    return pos_y + 40

def render_strava_section(draw, pos_y, section_font, value_font, small_font):
    """Рендеринг секції Strava"""
    x_start = 50
    line_height = 100
    
    draw.text((x_start, pos_y), "🏃 STRAVA", fill=HEADER_COLOR, font=section_font)
    pos_y += 80
    
    week_stats, month_stats = load_strava_stats()
    
    # Статистика тижня
    week_km = week_stats.get("total_distance", 0) or 0
    week_runs = week_stats.get("count", 0) or 0
    
    draw.text((x_start, pos_y), f"📅 Цей тиждень: {week_km:.1f} км ({week_runs} пробіжок)", 
             fill=ACCENT_COLOR, font=value_font)
    pos_y += line_height
    
    # Статистика місяця
    month_km = month_stats.get("total_distance", 0) or 0
    month_runs = month_stats.get("count", 0) or 0
    
    draw.text((x_start, pos_y), f"📊 Цей місяць: {month_km:.1f} км ({month_runs} пробіжок)", 
             fill=ACCENT_COLOR, font=value_font)
    pos_y += line_height
    
    # Середня швидкість
    if week_runs > 0:
        avg_pace = week_km / week_runs
        draw.text((x_start, pos_y), f"⚡ Середня дистанція: {avg_pace:.2f} км/пробіжка", 
                 fill=TEXT_COLOR, font=value_font)
    else:
        draw.text((x_start, pos_y), f"⚡ Середня дистанція: —", fill=TEXT_COLOR, font=value_font)
    pos_y += line_height
    
    return pos_y + 40

def render_habits_section(draw, pos_y, section_font, value_font, small_font):
    """Рендеринг секції Звички"""
    x_start = 50
    line_height = 100
    
    draw.text((x_start, pos_y), "🎯 ЗВИЧКИ", fill=HEADER_COLOR, font=section_font)
    pos_y += 80
    
    habits = load_habits_data()
    habit_names = {
        "shower": "🚿 Душ",
        "run": "🏃 Біг",
        "water": "💧 Вода",
        "tea": "☕ Чай",
        "sauna": "🧖 Сауна",
        "spray": "💦 Спрей"
    }
    
    done_count = 0
    total_count = len(habit_names)
    
    for key, emoji_name in habit_names.items():
        is_done = habits.get(key, False)
        color = SUCCESS_COLOR if is_done else DANGER_COLOR
        status = "✓" if is_done else "✗"
        
        draw.text((x_start, pos_y), f"{emoji_name}: {status}", fill=color, font=value_font)
        pos_y += 80
        
        if is_done:
            done_count += 1
    
    # Підсумок
    draw.text((x_start, pos_y), f"Всього: {done_count}/{total_count} виконано", 
             fill=ACCENT_COLOR, font=section_font)
    pos_y += line_height
    
    return pos_y + 40

# ═════════════════════════════════════════════════════════════════════════════
# MAIN DASHBOARD GENERATOR
# ═════════════════════════════════════════════════════════════════════════════

def generate_dashboard():
    """Генерувати один великий дешборд (2000x3000)"""
    width, height = 2000, 3000
    img = Image.new('RGB', (width, height), BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    # Завантажити шрифти
    title_font, section_font, value_font, small_font = get_fonts()
    
    # HEADER
    pos_y = 50
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    draw.text((50, pos_y), "📈 ДЕШБОРД ОЛЕГА", fill=HEADER_COLOR, font=title_font)
    pos_y += 100
    draw.text((50, pos_y), f"Дата: {now}", fill=TEXT_COLOR, font=value_font)
    pos_y += 80
    
    # Горизонтальна лінія
    draw.line([(50, pos_y), (width - 50, pos_y)], fill=ACCENT_COLOR, width=3)
    pos_y += 40
    
    # СЕКЦІЯ: Здоров'я
    pos_y = render_health_section(draw, pos_y, title_font, section_font, value_font, small_font)
    
    # Горизонтальна лінія
    draw.line([(50, pos_y), (width - 50, pos_y)], fill=ACCENT_COLOR, width=3)
    pos_y += 40
    
    # СЕКЦІЯ: Strava
    pos_y = render_strava_section(draw, pos_y, section_font, value_font, small_font)
    
    # Горизонтальна лінія
    draw.line([(50, pos_y), (width - 50, pos_y)], fill=ACCENT_COLOR, width=3)
    pos_y += 40
    
    # СЕКЦІЯ: Звички
    pos_y = render_habits_section(draw, pos_y, section_font, value_font, small_font)
    
    # Горизонтальна лінія
    draw.line([(50, pos_y), (width - 50, pos_y)], fill=ACCENT_COLOR, width=3)
    pos_y += 40
    
    # FOOTER
    draw.text((50, height - 80), "SmartAssistant v5.0 • Dashboard 2024", 
             fill=TEXT_COLOR, font=small_font)
    
    return img

def get_dashboard_bytes():
    """Генерувати дешборд і повернути як bytes для відправки в Telegram"""
    try:
        img = generate_dashboard()
        
        # Зберегти тимчасово
        img.save("/tmp/dashboard.png", quality=95)
        
        # Прочитати bytes
        with open("/tmp/dashboard.png", "rb") as f:
            return f.read()
    except Exception as e:
        print(f"[dashboard] error: {e}", flush=True)
        return None

def get_dashboard_path():
    """Генерувати дешборд і повернути шлях до файлу"""
    try:
        img = generate_dashboard()
        path = "/tmp/dashboard.png"
        img.save(path, quality=95)
        return path
    except Exception as e:
        print(f"[dashboard] error: {e}", flush=True)
        return None

# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    path = get_dashboard_path()
    if path:
        print(f"✅ Дешборд збережений: {path}")
        img = Image.open(path)
        print(f"   Розмір: {img.size}")
    else:
        print("❌ Помилка при генеруванні дешборду")
