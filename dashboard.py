#!/usr/bin/env python3
"""
Dashboard — один великий дешборд для звіту (Ultra HD 2000x3000)
Спрощена версія з надійними fallback'ами
"""

import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# COLORS
BG_COLOR = (13, 27, 42)          # Темно-синій фон
HEADER_COLOR = (255, 255, 255)   # Білі заголовки
TEXT_COLOR = (200, 200, 200)     # Світло-сірий текст
ACCENT_COLOR = (52, 152, 219)    # Блакитний акцент
SUCCESS_COLOR = (46, 204, 113)   # Зелений
WARNING_COLOR = (230, 126, 34)   # Помаранчевий
DANGER_COLOR = (231, 76, 60)     # Червоний

TITLE_FONT_SIZE = 72
SECTION_FONT_SIZE = 48
VALUE_FONT_SIZE = 42
SMALL_FONT_SIZE = 32

def get_fonts():
    """Завантажити шрифти"""
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", TITLE_FONT_SIZE)
        section_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", SECTION_FONT_SIZE)
        value_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", VALUE_FONT_SIZE)
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", SMALL_FONT_SIZE)
    except Exception as e:
        print(f"[dashboard] Font loading failed: {e}, using default", flush=True)
        title_font = section_font = value_font = small_font = ImageFont.load_default()
    return title_font, section_font, value_font, small_font

def generate_dashboard():
    """Генерувати простий дешборд"""
    try:
        width, height = 2000, 3000
        img = Image.new('RGB', (width, height), BG_COLOR)
        draw = ImageDraw.Draw(img)
        
        title_font, section_font, value_font, small_font = get_fonts()
        
        pos_y = 50
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        # HEADER
        draw.text((50, pos_y), "📈 ДЕШБОРД ОЛЕГА", fill=HEADER_COLOR, font=title_font)
        pos_y += 100
        draw.text((50, pos_y), f"Дата: {now}", fill=TEXT_COLOR, font=value_font)
        pos_y += 80
        
        # Лінія
        draw.line([(50, pos_y), (width - 50, pos_y)], fill=ACCENT_COLOR, width=3)
        pos_y += 60
        
        # СЕКЦІЯ: Здоров'я
        draw.text((50, pos_y), "📊 ЗДОРОВ'Я", fill=HEADER_COLOR, font=section_font)
        pos_y += 80
        
        try:
            from storage import load_weight
            wd = load_weight() or {}
            if wd:
                last_date = sorted(wd.keys())[-1]
                w = wd[last_date]
                draw.text((50, pos_y), f"⚖️  Вага: {w} кг (ціль 75 кг)", fill=SUCCESS_COLOR, font=value_font)
            else:
                draw.text((50, pos_y), "⚖️  Вага: немає даних", fill=TEXT_COLOR, font=value_font)
        except Exception as e:
            print(f"[dashboard] weight error: {e}", flush=True)
            draw.text((50, pos_y), "⚖️  Вага: —", fill=TEXT_COLOR, font=value_font)
        pos_y += 120
        
        # БІГ
        draw.text((50, pos_y), "🏃 Біг (всього): —", fill=ACCENT_COLOR, font=value_font)
        pos_y += 120
        
        # КРОКИ
        draw.text((50, pos_y), "👣 Кроки: —", fill=WARNING_COLOR, font=value_font)
        pos_y += 120
        
        # СОН
        draw.text((50, pos_y), "😴 Сон: —", fill=WARNING_COLOR, font=value_font)
        pos_y += 120
        
        # HRV
        draw.text((50, pos_y), "💚 HRV: —", fill=TEXT_COLOR, font=value_font)
        pos_y += 160
        
        # Лінія
        draw.line([(50, pos_y), (width - 50, pos_y)], fill=ACCENT_COLOR, width=3)
        pos_y += 60
        
        # СЕКЦІЯ: Strava
        draw.text((50, pos_y), "🏃 STRAVA", fill=HEADER_COLOR, font=section_font)
        pos_y += 80
        draw.text((50, pos_y), "📅 Тиждень: — км", fill=ACCENT_COLOR, font=value_font)
        pos_y += 120
        draw.text((50, pos_y), "📊 Місяць: — км", fill=ACCENT_COLOR, font=value_font)
        
        # Лінія
        draw.line([(50, pos_y), (width - 50, pos_y)], fill=ACCENT_COLOR, width=3)
        pos_y += 60
        
        # СЕКЦІЯ: Звички
        draw.text((50, pos_y), "🎯 ЗВИЧКИ", fill=HEADER_COLOR, font=section_font)
        pos_y += 80
        
        try:
            from storage import load_habits
            habits = load_habits() or {}
            today_str = datetime.now().strftime("%Y-%m-%d")
            today_habits = habits.get(today_str, {})
            
            habit_names = {
                "shower": "🚿 Душ",
                "run": "🏃 Біг",
                "water": "💧 Вода",
                "tea": "☕ Чай",
                "sauna": "🧖 Сауна",
                "spray": "💦 Спрей"
            }
            
            done = 0
            for key, name in habit_names.items():
                is_done = today_habits.get(key, False)
                color = SUCCESS_COLOR if is_done else DANGER_COLOR
                status = "✓" if is_done else "✗"
                draw.text((50, pos_y), f"{name}: {status}", fill=color, font=value_font)
                pos_y += 80
                if is_done:
                    done += 1
            
            draw.text((50, pos_y), f"Всього: {done}/6", fill=ACCENT_COLOR, font=section_font)
        except Exception as e:
            print(f"[dashboard] habits error: {e}", flush=True)
            draw.text((50, pos_y), "🎯 Звички: —", fill=TEXT_COLOR, font=value_font)
        
        # FOOTER
        draw.text((50, height - 80), "SmartAssistant Dashboard", fill=TEXT_COLOR, font=small_font)
        
        return img
    except Exception as e:
        print(f"[dashboard] generate error: {e}", flush=True)
        raise

def get_dashboard_bytes():
    """Генерувати і повернути як bytes"""
    try:
        img = generate_dashboard()
        import io
        buf = io.BytesIO()
        img.save(buf, format='PNG', quality=85)
        return buf.getvalue()
    except Exception as e:
        print(f"[dashboard] bytes error: {e}", flush=True)
        return None

def get_dashboard_path():
    """Генерувати і зберегти на диск"""
    try:
        img = generate_dashboard()
        path = "/tmp/dashboard.png"
        img.save(path, quality=85)
        print(f"[dashboard] saved to {path}", flush=True)
        return path
    except Exception as e:
        print(f"[dashboard] path error: {e}", flush=True)
        return None

if __name__ == "__main__":
    path = get_dashboard_path()
    print(f"Result: {path}")
