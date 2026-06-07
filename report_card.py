"""
report_card.py — Великий фото-репорт для Telegram (album з 3 фото).
Фото 1: Заголовок + Звички (місячна теплова карта)
Фото 2: Вага (великий графік) + Біг (графік + статистика)
Фото 3: Портфель (монети + PnL)
"""

import io
import os
import math
from datetime import datetime, timedelta, date, timezone
import calendar as _calendar

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# ── Шляхи до шрифтів ─────────────────────────────────────────────────────────
_FONT_DIR   = "/usr/share/fonts/truetype"
_SANS_BOLD  = f"{_FONT_DIR}/dejavu/DejaVuSans-Bold.ttf"
_SANS       = f"{_FONT_DIR}/dejavu/DejaVuSans.ttf"
_EMOJI_FONT = f"{_FONT_DIR}/noto/NotoColorEmoji.ttf"

# ── Палітра ───────────────────────────────────────────────────────────────────
BG      = "#0D1117"
CARD    = "#161B22"
CARD2   = "#1C2128"
CARD3   = "#21262D"
BORDER  = "#30363D"
TEXT    = "#E6EDF3"
MUTED   = "#8B949E"
MUTED2  = "#6E7681"
GREEN   = "#3FB950"
GREEN2  = "#238636"
RED     = "#F85149"
BLUE    = "#58A6FF"
BLUE2   = "#1F6FEB"
PURPLE  = "#A371F7"
ORANGE  = "#D29922"
YELLOW  = "#F0E040"
TEAL    = "#39D353"

def _hex(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = h[0]*2 + h[1]*2 + h[2]*2
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _hex_alpha(h, a=255):
    r, g, b = _hex(h)
    return (r, g, b, a)

def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def _emoji_font():
    try:
        return ImageFont.truetype(_EMOJI_FONT, 109)
    except Exception:
        return None

def _draw_emoji(canvas: Image.Image, emoji: str, x: int, y: int, size: int = 32):
    ef = _emoji_font()
    if not ef:
        return
    tmp = Image.new("RGBA", (130, 130), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((5, 5), emoji, font=ef, embedded_color=True)
    tmp = tmp.resize((size, size), Image.LANCZOS)
    canvas.paste(tmp, (x, y), tmp)

def _rr(draw, x0, y0, x1, y1, r, fill, outline=None, lw=1):
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill, outline=outline, width=lw)

# ── Допоміжні дані ─────────────────────────────────────────────────────────────

def _streak(raw, hid, today):
    s, d = 0, today
    while (raw.get(d.isoformat()) or {}).get(hid) is True:
        s += 1; d -= timedelta(days=1)
    return s

def _month_pct(raw, hid, today):
    d = today.replace(day=1)
    total = done = 0
    while d <= today:
        v = (raw.get(d.isoformat()) or {}).get(hid)
        if v is not None:
            total += 1
            if v is True: done += 1
        d += timedelta(days=1)
    return done / total if total else 0.0

def _last_weight(wdata, today):
    for i in range(180):
        d = today - timedelta(days=i)
        v = wdata.get(d.isoformat())
        if v: return float(v), d
    return None, None

def _weight_series(wdata, today, days=60):
    """Повертає реальні записи (без пропуску порожніх днів) за останні N днів."""
    cutoff = today - timedelta(days=days)
    result = []
    for k, v in sorted(wdata.items()):
        try:
            from datetime import date as _date
            d = _date.fromisoformat(k)
        except Exception:
            continue
        if d < cutoff or d > today:
            continue
        if v:
            result.append((d, float(v)))
    return result

def _new_img(w, h):
    img = Image.new("RGB", (w, h), _hex(BG))
    return img, ImageDraw.Draw(img)

def _save(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()

def _section_header(img, draw, x, y, title, f_sec):
    draw.text((x, y), title, font=f_sec, fill=_hex(MUTED))
    draw.line([(x, y + 18), (img.width - x, y + 18)], fill=_hex(BORDER), width=2)
    return y + 28

# ═══════════════════════════════════════════════════════════════════════════════
# ФОТО 1 — Заголовок + Звички
# ═══════════════════════════════════════════════════════════════════════════════

def _make_habits_photo(period, now, today, raw, HABITS):
    W = 1080
    PAD = 32

    UA_MONTHS = {1:"Січня",2:"Лютого",3:"Березня",4:"Квітня",
                 5:"Травня",6:"Червня",7:"Липня",8:"Серпня",
                 9:"Вересня",10:"Жовтня",11:"Листопада",12:"Грудня"}
    UA_DAYS   = {0:"Понеділок",1:"Вівторок",2:"Середа",3:"Четвер",
                 4:"П'ятниця",5:"Субота",6:"Неділя"}
    UA_MON_SHORT = {1:"Січ",2:"Лют",3:"Бер",4:"Кві",5:"Тра",6:"Чер",
                    7:"Лип",8:"Сер",9:"Вер",10:"Жов",11:"Лис",12:"Гру"}

    # Шрифти
    f_h1    = _font(_SANS_BOLD, 52)
    f_h2    = _font(_SANS_BOLD, 34)
    f_h3    = _font(_SANS_BOLD, 28)
    f_label = _font(_SANS_BOLD, 24)
    f_body  = _font(_SANS, 22)
    f_small = _font(_SANS, 20)
    f_tiny  = _font(_SANS, 18)
    f_sec   = _font(_SANS_BOLD, 18)
    f_num   = _font(_SANS_BOLD, 64)

    period_emoji = "☀️" if period == "morning" else "🌙"
    period_text  = "Ранковий звіт" if period == "morning" else "Вечірній звіт"
    date_str = f"{UA_DAYS[today.weekday()]}, {today.day} {UA_MONTHS[today.month]} {today.year}"
    time_str = now.strftime("%H:%M")

    today_entry = raw.get(today.isoformat()) or {}
    done_today  = sum(1 for h in HABITS if today_entry.get(h["id"]) is True)

    # Теплова карта: скільки місяців показувати
    # Показуємо поточний місяць + 2 попередніх (3 місяці)
    HEATMAP_MONTHS = 3
    # Дні для теплової карти
    heatmap_start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    heatmap_start = (heatmap_start.replace(day=1) - timedelta(days=1)).replace(day=1)

    # Оцінка висоти
    # Заголовок: 180px
    # Зведення звичок (6 рядків): 6 * 54 + 40 = 364px
    # Теплова карта (6 звичок × місяць): 6 * 80 + 60 = 540px
    # Footer: 60px
    # Виділяємо з запасом, кропнемо в кінці
    H = 180 + 40 + len(HABITS) * 56 + 50 + len(HABITS) * 200 + 120 + 60
    img, draw = _new_img(W, H)

    y = PAD

    # ── ЗАГОЛОВОК ─────────────────────────────────────────────────────────────
    # Градієнтна смуга зверху
    for i in range(6):
        alpha = int(255 * (1 - i / 6))
        grad_col = (*_hex(BLUE if period == "morning" else PURPLE), alpha)
        draw.rectangle([0, i, W, i+1], fill=grad_col[:3])

    _rr(draw, 0, 0, W, 140, 0, fill=_hex(CARD))
    # Тонка кольорова лінія зверху
    accent = BLUE if period == "morning" else PURPLE
    draw.line([(0, 0), (W, 0)], fill=_hex(accent), width=4)

    _draw_emoji(img, period_emoji, PAD, 28, size=52)
    draw.text((PAD + 66, 26), period_text, font=f_h1, fill=_hex(TEXT))
    draw.text((PAD + 66, 80), date_str + f"  ·  {time_str}", font=f_body, fill=_hex(MUTED))

    # Велике число виконаних справа
    done_s = f"{done_today}/{len(HABITS)}"
    done_col = GREEN if done_today == len(HABITS) else (ORANGE if done_today >= len(HABITS)//2 else RED)
    dw = draw.textlength(done_s, font=f_num)
    draw.text((W - PAD - dw, 24), done_s, font=f_num, fill=_hex(done_col))
    lbl = "сьогодні"
    lw = draw.textlength(lbl, font=f_small)
    draw.text((W - PAD - lw, 94), lbl, font=f_small, fill=_hex(MUTED))

    y = 152

    # ── ЗВЕДЕННЯ ЗВИЧОК ───────────────────────────────────────────────────────
    y = _section_header(img, draw, PAD, y, "ЗВИЧКИ — СЬОГОДНІ", f_sec)

    ROW_H = 54
    for h in HABITS:
        hid    = h["id"]
        hcolor = h["color"]
        status = today_entry.get(hid)
        streak = _streak(raw, hid, today)
        pct    = _month_pct(raw, hid, today)

        # Фон
        bg = _hex(CARD) if status is True else _hex(CARD2)
        _rr(draw, PAD, y, W - PAD, y + ROW_H - 4, 12, fill=bg, outline=_hex(BORDER))

        # Статус
        cx, cy, rc = PAD + 22, y + ROW_H//2 - 2, 16
        if status is True:
            draw.ellipse([cx-rc,cy-rc,cx+rc,cy+rc], fill=_hex(hcolor))
            draw.line([(cx-8,cy),(cx-2,cy+8),(cx+9,cy-8)], fill=_hex("#FFF"), width=3)
        elif status is False:
            draw.ellipse([cx-rc,cy-rc,cx+rc,cy+rc], fill=_hex(RED))
            draw.line([(cx-7,cy-7),(cx+7,cy+7)], fill=_hex("#FFF"), width=3)
            draw.line([(cx+7,cy-7),(cx-7,cy+7)], fill=_hex("#FFF"), width=3)
        else:
            draw.ellipse([cx-rc,cy-rc,cx+rc,cy+rc], outline=_hex(BORDER), width=2)

        # Emoji + назва
        _draw_emoji(img, h["emoji"], PAD + 46, y + ROW_H//2 - 16, size=30)
        nc = TEXT if status is True else (MUTED if status is None else RED)
        draw.text((PAD + 84, y + ROW_H//2 - 10), h["name"], font=f_label, fill=_hex(nc))

        # Стрік
        if streak > 0:
            _draw_emoji(img, "🔥", W - PAD - 240, y + 10, size=24)
            draw.text((W - PAD - 212, y + 13), f"{streak}д", font=f_label, fill=_hex(ORANGE))

        # % місяця + бар
        pct_s   = f"{int(pct*100)}%"
        pct_col = GREEN if pct >= 0.8 else (ORANGE if pct >= 0.5 else RED)
        pw = draw.textlength(pct_s, font=f_h3)
        draw.text((W - PAD - 20 - pw, y + 10), pct_s, font=f_h3, fill=_hex(pct_col))
        draw.text((W - PAD - 20 - pw, y + 36), "місяць", font=f_tiny, fill=_hex(MUTED))

        # Прогрес бар
        bx0, bx1 = W - PAD - 190, W - PAD - 80
        by = y + 24
        draw.rounded_rectangle([bx0, by, bx1, by+6], radius=3, fill=_hex(BORDER))
        fw = int((bx1-bx0)*pct)
        if fw > 0:
            draw.rounded_rectangle([bx0, by, bx0+fw, by+6], radius=3, fill=_hex(pct_col))

        y += ROW_H

    y += 20

    # ── ТЕПЛОВА КАРТА — єдина таблиця ─────────────────────────────────────────
    # Рядки = звички, стовпці = дні поточного місяця
    y = _section_header(img, draw, PAD, y, f"ЗВИЧКИ — {UA_MON_SHORT[today.month].upper()} {today.year}", f_sec)

    days_in_month = _calendar.monthrange(today.year, today.month)[1]
    month_start   = today.replace(day=1)

    # Геометрія
    LABEL_W  = 210          # ширина колонки з назвою звички
    grid_w   = W - PAD * 2 - LABEL_W - 8
    CELL_W   = max(22, grid_w // days_in_month - 2)
    CELL_H   = 42           # висота рядка (звички)
    CELL_GAP = 2
    # підганяємо під ширину
    total_cell_w = days_in_month * (CELL_W + CELL_GAP)
    if total_cell_w > grid_w:
        CELL_W = max(16, (grid_w - days_in_month * CELL_GAP) // days_in_month)

    DAY_HDR_H = 30   # висота рядка з номерами днів

    # Фон всієї таблиці
    table_h = DAY_HDR_H + len(HABITS) * (CELL_H + CELL_GAP) + 12
    _rr(draw, PAD, y, W - PAD, y + table_h, 14, fill=_hex(CARD2), outline=_hex(BORDER))

    gx0 = PAD + LABEL_W  # початок колонок з клітинками

    # ── Заголовок: номери днів ──
    for d_num in range(1, days_in_month + 1):
        cx = gx0 + (d_num - 1) * (CELL_W + CELL_GAP)
        d_date = month_start.replace(day=d_num)
        is_weekend = d_date.weekday() >= 5
        is_today   = d_date == today
        col = TEXT if is_today else (MUTED if not is_weekend else ORANGE)
        lbl = str(d_num)
        lw  = draw.textlength(lbl, font=f_tiny)
        draw.text((cx + CELL_W//2 - lw//2, y + 6), lbl, font=f_tiny, fill=_hex(col))
        if is_today:
            draw.line([(cx + 2, y + DAY_HDR_H - 3), (cx + CELL_W - 2, y + DAY_HDR_H - 3)],
                      fill=_hex(BLUE), width=2)

    ry = y + DAY_HDR_H

    # ── Рядки звичок ──
    for hi, h in enumerate(HABITS):
        hid     = h["id"]
        hcolor  = h["color"]
        pct     = _month_pct(raw, hid, today)
        pct_col = GREEN if pct >= 0.8 else (ORANGE if pct >= 0.5 else RED)
        cy_row  = ry + hi * (CELL_H + CELL_GAP)

        # Назва звички зліва
        _draw_emoji(img, h["emoji"], PAD + 6, cy_row + CELL_H//2 - 14, size=26)
        nm = h["name"]
        # Скорочуємо якщо не вміщується
        max_label_chars = 12
        if len(nm) > max_label_chars:
            nm = nm[:max_label_chars - 1] + "…"
        draw.text((PAD + 38, cy_row + 4), nm, font=f_small, fill=_hex(TEXT))
        pct_s = f"{int(pct*100)}%"
        draw.text((PAD + 38, cy_row + 24), pct_s, font=f_tiny, fill=_hex(pct_col))

        # Клітинки по днях
        for d_num in range(1, days_in_month + 1):
            d_date = month_start.replace(day=d_num)
            cx = gx0 + (d_num - 1) * (CELL_W + CELL_GAP)
            v  = (raw.get(d_date.isoformat()) or {}).get(hid)
            is_future = d_date > today

            if is_future:
                fill = _hex(CARD3)
                outl = _hex(BORDER)
            elif v is True:
                fill = _hex(hcolor)
                outl = None
            elif v is False:
                fill = _hex("#4A1515") if (today - d_date).days > 3 else _hex(RED)
                outl = None
            else:
                fill = _hex(CARD3)
                outl = _hex(BORDER)

            _rr(draw, cx + 1, cy_row + 2, cx + CELL_W - 1, cy_row + CELL_H - 2,
                4, fill=fill, outline=outl, lw=1)

            # Обводка сьогодні
            if d_date == today:
                draw.rounded_rectangle(
                    [cx + 1, cy_row + 2, cx + CELL_W - 1, cy_row + CELL_H - 2],
                    radius=4, outline=_hex(TEXT), width=2)

    y += table_h + 14

    # Легенда
    leg_items = [("Виконано", GREEN), ("Пропущено", RED), ("Немає даних", MUTED2)]
    lx = PAD
    for leg_text, leg_col in leg_items:
        draw.rounded_rectangle([lx, y + 4, lx + 16, y + 16], radius=3, fill=_hex(leg_col))
        draw.text((lx + 22, y), leg_text, font=f_tiny, fill=_hex(MUTED))
        lx += int(draw.textlength(leg_text, font=f_tiny)) + 44

    y += 30

    # Footer
    draw.line([(PAD, y+4),(W-PAD, y+4)], fill=_hex(BORDER), width=2)
    ft = f"1/3  ·  {time_str}  ·  Олег Новосадов"
    ftw = draw.textlength(ft, font=f_tiny)
    draw.text(((W-ftw)//2, y+10), ft, font=f_tiny, fill=_hex(MUTED2))

    img = img.crop((0, 0, W, y + 36))
    return _save(img)


# ═══════════════════════════════════════════════════════════════════════════════
# ФОТО 2 — Вага + Біг
# ═══════════════════════════════════════════════════════════════════════════════

def _make_run_weight_photo(now, today, wdata, run_data, last_run):
    W = 1080
    PAD = 32

    UA_MONTHS2 = {1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",
                  5:"Травень",6:"Червень",7:"Липень",8:"Серпень",
                  9:"Вересень",10:"Жовтень",11:"Листопад",12:"Грудень"}
    UA_MONTHS = {1:"Січня",2:"Лютого",3:"Березня",4:"Квітня",
                 5:"Травня",6:"Червня",7:"Липня",8:"Серпня",
                 9:"Вересня",10:"Жовтня",11:"Листопада",12:"Грудня"}

    f_h1    = _font(_SANS_BOLD, 48)
    f_h2    = _font(_SANS_BOLD, 36)
    f_h3    = _font(_SANS_BOLD, 28)
    f_label = _font(_SANS_BOLD, 24)
    f_body  = _font(_SANS, 22)
    f_small = _font(_SANS, 20)
    f_tiny  = _font(_SANS, 18)
    f_sec   = _font(_SANS_BOLD, 18)
    f_num   = _font(_SANS_BOLD, 68)
    f_num2  = _font(_SANS_BOLD, 42)

    time_str = now.strftime("%H:%M")
    target_kg = 75.0
    last_kg, last_kg_date = _last_weight(wdata, today)
    weight_series = _weight_series(wdata, today, 60)

    # Висота
    H_TITLE   = 80
    H_WEIGHT  = 420
    H_RUN     = 480
    H_FOOTER  = 60
    H = H_TITLE + H_WEIGHT + H_RUN + H_FOOTER + 80
    img, draw = _new_img(W, H)

    y = PAD

    # ── Акцент лінія ──────────────────────────────────────────────────────────
    draw.line([(0,0),(W,0)], fill=_hex(GREEN), width=4)
    _rr(draw, 0, 0, W, 70, 0, fill=_hex(CARD))
    _draw_emoji(img, "⚖️", PAD, 14, size=42)
    _draw_emoji(img, "🏃", PAD + 300, 14, size=42)
    draw.text((PAD + 50, 18), "Вага та біг", font=f_h1, fill=_hex(TEXT))
    draw.text((W - PAD - draw.textlength("2 / 3", font=f_small) - 4, 26), "2 / 3", font=f_small, fill=_hex(MUTED))
    y = 82

    # ══════════════════════════════════════════
    # СЕКЦІЯ ВАГА
    # ══════════════════════════════════════════
    y = _section_header(img, draw, PAD, y, "ВАГА", f_sec)

    if last_kg is not None:
        # Великі цифри
        kg_s = f"{last_kg:.1f}"
        kgw = draw.textlength(kg_s, font=f_num)
        draw.text((PAD, y), kg_s, font=f_num, fill=_hex(TEXT))
        draw.text((PAD + kgw + 8, y + 30), "кг", font=f_h2, fill=_hex(MUTED))

        diff = last_kg - target_kg
        diff_col = GREEN if diff <= 0 else (ORANGE if diff < 3 else RED)
        diff_s = f"{diff:+.1f} кг від цілі {target_kg:.0f} кг"
        draw.text((PAD, y + 60), diff_s, font=f_body, fill=_hex(diff_col))

        if last_kg_date:
            age = (today - last_kg_date).days
            age_s = "сьогодні" if age == 0 else ("вчора" if age == 1 else f"{age}д тому")
            draw.text((PAD + kgw + draw.textlength("  кг", font=f_h2) + 8, y + 30),
                      age_s, font=f_small, fill=_hex(MUTED))

        # Статистика справа
        if len(weight_series) >= 5:
            kgs = [v for _, v in weight_series]
            mn_kg = min(kgs)
            mx_kg = max(kgs)
            avg_kg = sum(kgs) / len(kgs)
            td = weight_series[-1][1] - weight_series[0][1]
            td_col = GREEN if td < 0 else RED

            stats = [
                (f"{mn_kg:.1f} кг", "Мін за 60д", BLUE),
                (f"{mx_kg:.1f} кг", "Макс за 60д", RED),
                (f"{avg_kg:.1f} кг", "Середнє", MUTED),
                (f"{td:+.1f} кг", "Тренд 60д", td_col),
            ]
            sx = W - PAD - 280
            for si, (val, lbl, col) in enumerate(stats):
                bx = sx + (si % 2) * 140
                by = y + (si // 2) * 56
                _rr(draw, bx, by, bx+130, by+50, 8, fill=_hex(CARD2), outline=_hex(BORDER))
                draw.text((bx+10, by+6), val, font=f_label, fill=_hex(col))
                draw.text((bx+10, by+28), lbl, font=f_tiny, fill=_hex(MUTED))

        y += 90

        # Великий графік ваги
        if len(weight_series) >= 2:
            gx0, gx1 = PAD, W - PAD
            gy0, gy1 = y, y + 240
            gw, gh = gx1 - gx0, gy1 - gy0

            kgs = [v for _, v in weight_series]
            mn = min(kgs) - 0.8
            mx = max(kgs) + 0.8
            span = mx - mn or 1.0

            # Фон графіка
            _rr(draw, gx0, gy0, gx1, gy1, 12, fill=_hex(CARD), outline=_hex(BORDER))

            # Сітка горизонтальна
            grid_vals = [mn + (mx - mn) * i / 4 for i in range(5)]
            for gv in grid_vals:
                gy_line = gy1 - int((gv - mn) / span * gh)
                if gy0 < gy_line < gy1:
                    draw.line([(gx0+8, gy_line), (gx1-8, gy_line)], fill=_hex(BORDER), width=2)
                    draw.text((gx0+10, gy_line - 14), f"{gv:.1f}", font=f_tiny, fill=_hex(MUTED2))

            # Лінія цілі
            target_y = gy1 - int((target_kg - mn) / span * gh)
            if gy0 < target_y < gy1:
                for x_dash in range(gx0+8, gx1-8, 14):
                    draw.line([(x_dash, target_y), (x_dash+8, target_y)], fill=_hex(GREEN2), width=4)
                draw.text((gx1 - 80, target_y - 16), f"Ціль {target_kg:.0f}кг", font=f_tiny, fill=_hex(GREEN))

            # Точки і лінія
            pts = []
            for i, (_, v) in enumerate(weight_series):
                px = gx0 + int(i / (len(weight_series)-1) * gw)
                py = gy1 - int((v - mn) / span * gh)
                pts.append((px, py))

            # Заливка під лінією
            if len(pts) >= 2:
                fill_pts = [(gx0+8, gy1-2)] + pts + [(gx1-8, gy1-2)]
                overlay = Image.new("RGBA", img.size, (0,0,0,0))
                od = ImageDraw.Draw(overlay)
                od.polygon(fill_pts, fill=_hex_alpha(BLUE, 35))
                img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))
                draw = ImageDraw.Draw(img)

            # Лінія
            for i in range(len(pts)-1):
                draw.line([pts[i], pts[i+1]], fill=_hex(BLUE), width=5)

            # Точки
            for i, (px, py) in enumerate(pts):
                if i % max(1, len(pts)//12) == 0 or i == len(pts)-1:
                    draw.ellipse([px-5, py-5, px+5, py+5], fill=_hex(BLUE))
                    if i == len(pts)-1:
                        draw.ellipse([px-7, py-7, px+7, py+7], outline=_hex(TEXT), width=2)

            # Підписи дат (кожні 10 днів)
            for i, (d, _) in enumerate(weight_series):
                if i % max(1, len(weight_series)//6) == 0 or i == len(weight_series)-1:
                    lbl = f"{d.day} {UA_MONTHS[d.month][:3]}"
                    lw2 = draw.textlength(lbl, font=f_tiny)
                    px = gx0 + int(i / (len(weight_series)-1) * gw)
                    draw.text((px - lw2//2, gy1 + 4), lbl, font=f_tiny, fill=_hex(MUTED))

            y = gy1 + 22
        else:
            draw.text((PAD, y+20), "Недостатньо даних для графіка", font=f_body, fill=_hex(MUTED))
            y += 60
    else:
        draw.text((PAD, y+20), "Немає даних про вагу", font=f_body, fill=_hex(MUTED))
        y += 80

    y += 20

    # ══════════════════════════════════════════
    # СЕКЦІЯ БІГ
    # ══════════════════════════════════════════
    y = _section_header(img, draw, PAD, y, "БІГ — STRAVA", f_sec)

    # Місячні бари якщо є дані за кілька місяців
    try:
        from strava import get_year_stats, get_runs
        year_data = get_year_stats(today.year)
        monthly = year_data.get("monthly", {})
        all_runs = get_runs(days=120)
    except Exception:
        year_data = None
        monthly = {}
        all_runs = []

    run_month = run_data or {}
    runs_cnt = run_month.get("runs", 0)
    km_total = run_month.get("km", 0)

    # Статистика місяця
    if last_run or runs_cnt > 0:
        # Топ метрики
        stats_run = []
        if runs_cnt > 0:
            stats_run.append((f"{km_total:.1f}", "км цього місяця", GREEN))
            stats_run.append((f"{runs_cnt}", "пробіжок", BLUE))
            if run_month.get("avg_pace_str"):
                stats_run.append((run_month["avg_pace_str"], "хв/км темп", ORANGE))
            if run_month.get("avg_hr"):
                stats_run.append((f"{int(run_month['avg_hr'])}", "уд/хв ЧСС", RED))
        elif last_run:
            lr_km = last_run.get("dist_km", 0)
            lr_ps = last_run.get("pace_sec", 0)
            lr_hr = last_run.get("hr")
            stats_run.append((f"{lr_km:.1f}", "км остання", GREEN))
            if lr_ps > 0:
                stats_run.append((f"{int(lr_ps//60)}:{int(lr_ps%60):02d}", "хв/км темп", ORANGE))
            if lr_hr:
                stats_run.append((f"{int(lr_hr)}", "уд/хв ЧСС", RED))

        # Карточки статистики
        STAT_W = 200
        for si, (val, lbl, col) in enumerate(stats_run[:4]):
            bx = PAD + si * (STAT_W + 12)
            _rr(draw, bx, y, bx+STAT_W, y+72, 10, fill=_hex(CARD), outline=_hex(BORDER))
            _rr(draw, bx, y, bx+STAT_W, y+5, 0, fill=_hex(col))
            draw.text((bx+12, y+12), val, font=f_num2, fill=_hex(col))
            draw.text((bx+12, y+50), lbl, font=f_small, fill=_hex(MUTED))

        y += 84

    # Графік пробіжок (барчарт по місяцях + точки)
    if monthly or all_runs:
        gx0, gx1 = PAD, W - PAD
        gy0, gy1 = y, y + 200
        gw, gh = gx1 - gx0, gy1 - gy0

        _rr(draw, gx0, gy0, gx1, gy1, 12, fill=_hex(CARD), outline=_hex(BORDER))

        if monthly:
            # Місячний барчарт
            months_shown = sorted(monthly.keys())[-6:]  # останні 6 місяців
            max_km = max(monthly[m]["km"] for m in months_shown) if months_shown else 1
            max_km = max(max_km, 1)

            bar_w = int(gw / (len(months_shown) + 1))
            for bi, m in enumerate(months_shown):
                km = monthly[m]["km"]
                bh = int((km / max_km) * (gh - 50))
                bx_bar = gx0 + int((bi + 0.5) * gw / len(months_shown)) - bar_w//3
                by_bar = gy1 - bh - 8

                is_current = (m == today.month)
                bar_col = GREEN if is_current else CARD3
                bar_outline = GREEN if is_current else MUTED2

                _rr(draw, bx_bar, by_bar, bx_bar + bar_w//1.5, gy1-8, 6,
                    fill=_hex(bar_col), outline=_hex(bar_outline))

                # Підпис
                m_lbl = UA_MONTHS2.get(m, str(m))[:3]
                lw2 = draw.textlength(m_lbl, font=f_tiny)
                draw.text((bx_bar + bar_w//3 - lw2//2, gy1-22), m_lbl, font=f_tiny, fill=_hex(MUTED))

                # км над баром
                if km > 0:
                    km_s = f"{km:.0f}"
                    kmw = draw.textlength(km_s, font=f_tiny)
                    draw.text((bx_bar + bar_w//3 - kmw//2, by_bar - 16), km_s, font=f_tiny,
                              fill=_hex(GREEN if is_current else MUTED))

            draw.text((gx0+12, gy0+8), "км/місяць", font=f_tiny, fill=_hex(MUTED))

        elif all_runs:
            # Точкова діаграма останніх пробіжок
            recent = sorted(all_runs, key=lambda r: r["date"])[-20:]
            max_km2 = max(r["dist_km"] for r in recent) if recent else 1
            for ri, r in enumerate(recent):
                px = gx0 + int(ri / max(len(recent)-1, 1) * gw)
                py = gy1 - int(r["dist_km"] / max_km2 * (gh - 40)) - 20
                draw.ellipse([px-6,py-6,px+6,py+6], fill=_hex(GREEN))
                if ri > 0:
                    prev_r = recent[ri-1]
                    px0 = gx0 + int((ri-1) / max(len(recent)-1,1) * gw)
                    py0 = gy1 - int(prev_r["dist_km"] / max_km2 * (gh-40)) - 20
                    draw.line([(px0,py0),(px,py)], fill=_hex(GREEN2), width=4)
            draw.text((gx0+12, gy0+8), "км/пробіжка", font=f_tiny, fill=_hex(MUTED))

        y = gy1 + 8

    # Список останніх пробіжок
    if all_runs:
        y += 12
        draw.text((PAD, y), "ОСТАННІ ПРОБІЖКИ", font=f_sec, fill=_hex(MUTED))
        draw.line([(PAD, y+18),(W-PAD, y+18)], fill=_hex(BORDER), width=2)
        y += 28

        recent_runs = sorted(all_runs, key=lambda r: r["date"], reverse=True)[:5]
        col_widths = [120, 110, 110, 110, 110]  # дата, км, темп, ЧСС, тривалість
        headers = ["Дата", "Відстань", "Темп", "ЧСС", "Тривалість"]
        hx = PAD

        # Заголовки
        for ci, (ch, cw) in enumerate(zip(headers, col_widths)):
            draw.text((hx, y), ch, font=f_tiny, fill=_hex(MUTED))
            hx += cw
        y += 18
        draw.line([(PAD, y), (W-PAD, y)], fill=_hex(BORDER), width=2)
        y += 6

        for ri, r in enumerate(recent_runs):
            row_bg = CARD if ri % 2 == 0 else CARD2
            _rr(draw, PAD, y, W-PAD, y+32, 6, fill=_hex(row_bg))

            rd = r["date"]
            date_s = f"{rd.day:02d}.{rd.month:02d}" if hasattr(rd, 'day') else str(rd)[:10]
            pace_sec = r.get("pace_sec", 0)
            pace_s = f"{int(pace_sec//60)}:{int(pace_sec%60):02d}" if pace_sec > 0 else "—"
            hr_s = f"{int(r['hr'])}" if r.get("hr") else "—"
            dur_s = f"{int(r.get('dur_min', 0))} хв"

            row_vals = [date_s, f"{r['dist_km']:.2f} км", f"{pace_s} /км", f"{hr_s} bpm", dur_s]
            row_cols = [MUTED, GREEN, ORANGE, RED, BLUE]

            rx = PAD + 8
            for ci, (rv, cw, rc2) in enumerate(zip(row_vals, col_widths, row_cols)):
                draw.text((rx, y + 8), rv, font=f_small, fill=_hex(rc2))
                rx += cw
            y += 34

    y += 16

    # Footer
    draw.line([(PAD, y+4),(W-PAD, y+4)], fill=_hex(BORDER), width=2)
    ft = f"2/3  ·  {time_str}  ·  Олег Новосадов"
    ftw = draw.textlength(ft, font=f_tiny)
    draw.text(((W-ftw)//2, y+10), ft, font=f_tiny, fill=_hex(MUTED2))

    img = img.crop((0, 0, W, y+36))
    return _save(img)


# ═══════════════════════════════════════════════════════════════════════════════
# ФОТО 3 — Портфель
# ═══════════════════════════════════════════════════════════════════════════════

def _make_portfolio_photo(now, today, portfolio):
    W = 1080
    PAD = 32

    f_h1    = _font(_SANS_BOLD, 36)
    f_h2    = _font(_SANS_BOLD, 26)
    f_h3    = _font(_SANS_BOLD, 20)
    f_label = _font(_SANS_BOLD, 17)
    f_body  = _font(_SANS, 16)
    f_small = _font(_SANS, 14)
    f_tiny  = _font(_SANS, 12)
    f_sec   = _font(_SANS_BOLD, 13)
    f_num   = _font(_SANS_BOLD, 56)
    f_num2  = _font(_SANS_BOLD, 30)

    time_str = now.strftime("%H:%M")

    positions = portfolio.get("positions", {})
    total_val  = portfolio.get("total_value", 0)
    change24   = portfolio.get("change_24h", 0)
    total_pnl  = portfolio.get("total_pnl")

    all_pos = sorted(
        [(sym, p) for sym, p in positions.items() if p.get("value", 0) > 0],
        key=lambda x: x[1].get("value", 0), reverse=True
    )

    H = 100 + 180 + len(all_pos) * 66 + 120
    img, draw = _new_img(W, H)

    y = 0

    # Акцент
    draw.line([(0,0),(W,0)], fill=_hex(PURPLE), width=4)
    _rr(draw, 0, 0, W, 70, 0, fill=_hex(CARD))
    _draw_emoji(img, "💼", PAD, 14, size=42)
    draw.text((PAD + 52, 18), "Портфель", font=f_h1, fill=_hex(TEXT))
    draw.text((W-PAD-draw.textlength("3/3",font=f_small)-4, 26), "3/3", font=f_small, fill=_hex(MUTED))
    y = 82

    # ── ПІДСУМОК ──────────────────────────────────────────────────────────────
    _rr(draw, PAD, y, W-PAD, y+120, 14, fill=_hex(CARD), outline=_hex(BORDER))

    tv_s = f"${total_val:,.0f}"
    tvw = draw.textlength(tv_s, font=f_num)
    draw.text((PAD+20, y+12), tv_s, font=f_num, fill=_hex(TEXT))
    draw.text((PAD+20+tvw+8, y+48), "USD", font=f_h2, fill=_hex(MUTED))

    ch_col = GREEN if change24 >= 0 else RED
    ch_s = f"{'+' if change24>=0 else ''}{change24:,.0f}$ за 24г"
    draw.text((PAD+20, y+80), ch_s, font=f_label, fill=_hex(ch_col))

    if total_pnl is not None:
        pnl_col = GREEN if total_pnl >= 0 else RED
        pnl_s = f"P&L: {'+' if total_pnl>=0 else ''}{total_pnl:,.0f}$"
        pnl_w = draw.textlength(pnl_s, font=f_h3)
        draw.text((W-PAD-20-pnl_w, y+40), pnl_s, font=f_h3, fill=_hex(pnl_col))

    # Доnut mini: розподіл портфелю (прямокутний бар)
    if all_pos:
        bx0, bx1 = PAD+20, W-PAD-20
        bar_y = y + 104
        bar_h = 10
        bx_cur = bx0
        COIN_COLORS = [BLUE, GREEN, ORANGE, PURPLE, RED, TEAL, YELLOW, BLUE2]
        for ci, (sym, pos) in enumerate(all_pos[:8]):
            share = pos.get("value", 0) / total_val if total_val > 0 else 0
            seg_w = int((bx1-bx0) * share)
            if seg_w > 0:
                col = COIN_COLORS[ci % len(COIN_COLORS)]
                _rr(draw, bx_cur, bar_y, bx_cur+seg_w, bar_y+bar_h, 0, fill=_hex(col))
                bx_cur += seg_w
        draw.rounded_rectangle([bx0, bar_y, bx1, bar_y+bar_h], radius=5, outline=_hex(BORDER), width=1)

    y += 130

    # ── СПИСОК МОНЕТ ──────────────────────────────────────────────────────────
    y = _section_header(img, draw, PAD, y, f"ПОЗИЦІЇ ({len(all_pos)} монет)", f_sec)

    # Заголовки колонок
    cols = [("Монета", PAD+8, TEXT),
            ("Ціна", PAD+200, MUTED),
            ("К-сть", PAD+340, MUTED),
            ("Вартість", PAD+490, MUTED),
            ("24г %", PAD+650, MUTED),
            ("Частка", PAD+780, MUTED)]

    for ch, cx, cc in cols:
        draw.text((cx, y), ch, font=f_tiny, fill=_hex(cc))
    y += 18
    draw.line([(PAD, y),(W-PAD,y)], fill=_hex(BORDER), width=2)
    y += 6

    COIN_COLORS2 = [BLUE, GREEN, ORANGE, PURPLE, RED, TEAL, "#F7CC44", BLUE2,
                    "#DB61A2", "#00CED1", "#FF6B6B", "#95E75C"]

    for ci, (sym, pos) in enumerate(all_pos):
        row_h = 60
        row_bg = CARD if ci % 2 == 0 else CARD2
        _rr(draw, PAD, y, W-PAD, y+row_h, 6, fill=_hex(row_bg))

        val    = pos.get("value", 0)
        price  = pos.get("price", 0)
        amount = pos.get("amount", 0)
        chg24  = pos.get("change24", pos.get("change_24h_pct", 0)) or 0
        share  = val / total_val * 100 if total_val else 0
        coin_col = COIN_COLORS2[ci % len(COIN_COLORS2)]

        chg_col = GREEN if chg24 >= 0 else RED
        chg_s   = f"{'+' if chg24>=0 else ''}{chg24:.1f}%"
        price_s = f"${price:,.2f}" if price < 1000 else f"${price:,.0f}"
        val_s   = f"${val:,.0f}"
        amt_s   = f"{amount:.4f}" if amount < 1 else (f"{amount:.2f}" if amount < 100 else f"{amount:,.0f}")
        share_s = f"{share:.1f}%"

        # Кольоровий маркер
        draw.rounded_rectangle([PAD+4, y+10, PAD+10, y+row_h-10], radius=3, fill=_hex(coin_col))

        # Назва + ціна
        draw.text((PAD+18, y+8), sym, font=f_label, fill=_hex(coin_col))
        draw.text((PAD+18, y+34), price_s, font=f_small, fill=_hex(MUTED))

        draw.text((PAD+200, y+18), price_s, font=f_small, fill=_hex(TEXT))
        draw.text((PAD+340, y+18), amt_s, font=f_small, fill=_hex(TEXT))
        draw.text((PAD+490, y+12), val_s, font=f_label, fill=_hex(TEXT))

        # 24г % з кольоровим фоном
        chg_bg = _hex(GREEN2) if chg24 >= 0 else _hex("#3D0D0D")
        chgw = draw.textlength(chg_s, font=f_label)
        _rr(draw, PAD+645, y+12, PAD+650+chgw+12, y+40, 6, fill=chg_bg)
        draw.text((PAD+652, y+15), chg_s, font=f_label, fill=_hex(chg_col))

        # Частка + мінібар
        draw.text((PAD+780, y+8), share_s, font=f_label, fill=_hex(TEXT))
        bar_w_max = W - PAD - 20 - (PAD+790+50)
        bar_w_max = max(bar_w_max, 100)
        bw = int(bar_w_max * min(share / 100, 1.0))
        _rr(draw, PAD+790+50, y+22, PAD+790+50+bar_w_max, y+34, 4, fill=_hex(BORDER))
        if bw > 0:
            _rr(draw, PAD+790+50, y+22, PAD+790+50+bw, y+34, 4, fill=_hex(coin_col))

        y += row_h + 4

    y += 12

    # Footer
    draw.line([(PAD, y+4),(W-PAD, y+4)], fill=_hex(BORDER), width=2)
    ft = f"3/3  ·  {time_str}  ·  Олег Новосадов  ·  Кошіце, Словаччина"
    ftw = draw.textlength(ft, font=f_tiny)
    draw.text(((W-ftw)//2, y+10), ft, font=f_tiny, fill=_hex(MUTED2))

    img = img.crop((0, 0, W, y+36))
    return _save(img)


# ═══════════════════════════════════════════════════════════════════════════════
# ГОЛОВНА ФУНКЦІЯ
# ═══════════════════════════════════════════════════════════════════════════════

HABITS_DEF = [
    {"id": "shower", "name": "Холодний душ",     "emoji": "🚿", "color": BLUE},
    {"id": "run",    "name": "Пробіжка",          "emoji": "🏃", "color": GREEN},
    {"id": "water",  "name": "Вода (2л+)",         "emoji": "💧", "color": BLUE2},
    {"id": "tea",    "name": "Трав'яний чай",     "emoji": "🍵", "color": ORANGE},
    {"id": "sauna",  "name": "Сауна",              "emoji": "🧖", "color": RED},
    {"id": "spray",  "name": "Спрей для волосся",  "emoji": "💈", "color": PURPLE},
]

def generate_report_card(period: str = "morning") -> bytes | None:
    """Генерує перше фото (сумісність зі старим кодом) — фото 1 зі звичками."""
    result = generate_report_album(period)
    return result[0] if result else None


def generate_report_album(period: str = "morning") -> list[bytes]:
    """Генерує album з 3 фото. Повертає список bytes."""
    if not HAS_PIL:
        return []

    try:
        from storage import load_habits, load_weight, load
    except ImportError:
        return []

    now   = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.date()
    raw   = load_habits() or {}
    wdata = load("weight_data.json") or load_weight() or {}

    # Strava
    run_data = None
    last_run = None
    try:
        from strava import get_month_stats, get_runs
        run_data = get_month_stats(today.year, today.month)
        runs_list = get_runs(days=90)
        if runs_list:
            last_run = sorted(runs_list, key=lambda r: r["date"], reverse=True)[0]
    except Exception as e:
        print(f"[report_card] strava: {e}")

    # Портфель
    portfolio = None
    try:
        from portfolio import get_portfolio_summary
        portfolio = get_portfolio_summary()
    except Exception as e:
        print(f"[report_card] portfolio: {e}")

    results = []

    # Фото 1: Звички
    try:
        p1 = _make_habits_photo(period, now, today, raw, HABITS_DEF)
        results.append(p1)
    except Exception as e:
        print(f"[report_card] photo1 error: {e}")
        import traceback; traceback.print_exc()

    # Фото 2: Вага + Біг
    try:
        p2 = _make_run_weight_photo(now, today, wdata, run_data, last_run)
        results.append(p2)
    except Exception as e:
        print(f"[report_card] photo2 error: {e}")
        import traceback; traceback.print_exc()

    return results


# ── Локальний тест ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    photos = generate_report_album("morning")
    for i, data in enumerate(photos, 1):
        path = f"/tmp/report_photo_{i}.png"
        with open(path, "wb") as f:
            f.write(data)
        print(f"Photo {i}: {len(data):,} bytes → {path}")
