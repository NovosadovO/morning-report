"""
report_card.py — Великий PNG-звіт для Telegram (одне повідомлення).
Секції: заголовок, звички×6, вага+sparkline, біг (Strava), портфель (крипто).
PIL-based: повна підтримка кольорових emoji через NotoColorEmoji.
Надсилається двічі на день: 09:00 і 20:00 (UTC+2).
"""

import io
import os
from datetime import datetime, timedelta, date, timezone

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
BORDER  = "#30363D"
TEXT    = "#E6EDF3"
MUTED   = "#8B949E"
GREEN   = "#3FB950"
RED     = "#F85149"
BLUE    = "#58A6FF"
PURPLE  = "#A371F7"
ORANGE  = "#D29922"
YELLOW  = "#F0E040"

def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

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
    """Малює кольоровий emoji через PIL overlay (NotoColorEmoji)."""
    ef = _emoji_font()
    if not ef:
        return
    tmp = Image.new("RGBA", (130, 130), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((5, 5), emoji, font=ef, embedded_color=True)
    tmp = tmp.resize((size, size), Image.LANCZOS)
    canvas.paste(tmp, (x, y), tmp)

# ═══════════════════════════════════════════════════════════════════════════════
# Допоміжні функції
# ═══════════════════════════════════════════════════════════════════════════════

def _streak(raw: dict, habit_key: str, today: date) -> int:
    streak = 0
    d = today
    while True:
        v = (raw.get(d.isoformat()) or {}).get(habit_key)
        if v is True:
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    return streak

def _month_pct(raw: dict, habit_key: str, today: date) -> float:
    start = today.replace(day=1)
    total = done = 0
    d = start
    while d <= today:
        v = (raw.get(d.isoformat()) or {}).get(habit_key)
        if v is not None:
            total += 1
            if v is True:
                done += 1
        d += timedelta(days=1)
    return done / total if total > 0 else 0.0

def _week_history(raw: dict, habit_key: str, today: date, days: int = 7):
    return [(raw.get((today - timedelta(days=i)).isoformat()) or {}).get(habit_key)
            for i in range(days - 1, -1, -1)]

def _last_weight(wdata: dict, today: date):
    for i in range(120):
        d = (today - timedelta(days=i)).isoformat()
        v = wdata.get(d)
        if v:
            return float(v), (today - timedelta(days=i))
    return None, None

def _weight_trend(wdata: dict, today: date, days: int = 30):
    result = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        v = wdata.get(d.isoformat())
        if v:
            result.append((d, float(v)))
    return result

def _rounded_rect(draw: ImageDraw.Draw, x0, y0, x1, y1, r, fill, outline=None, lw=1):
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill, outline=outline, width=lw)

# ═══════════════════════════════════════════════════════════════════════════════
# ГОЛОВНА ФУНКЦІЯ
# ═══════════════════════════════════════════════════════════════════════════════

def generate_report_card(period: str = "morning") -> bytes | None:
    """Генерує великий PNG-банер. Повертає bytes."""
    if not HAS_PIL:
        return None

    try:
        from storage import load_habits, load_weight
    except ImportError:
        return None

    now   = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.date()

    raw   = load_habits() or {}
    wdata = load_weight() or {}

    # ── Звички ───────────────────────────────────────────────────────────────
    HABITS = [
        {"id": "shower", "name": "Холодний душ",     "emoji": "🚿", "color": BLUE},
        {"id": "run",    "name": "Пробіжка",          "emoji": "🏃", "color": GREEN},
        {"id": "water",  "name": "Вода (2л+)",         "emoji": "💧", "color": "#1F6FEB"},
        {"id": "tea",    "name": "Трав'яний чай",     "emoji": "🍵", "color": ORANGE},
        {"id": "sauna",  "name": "Сауна",              "emoji": "🧖", "color": RED},
        {"id": "spray",  "name": "Спрей для волосся",  "emoji": "💈", "color": PURPLE},
    ]

    today_entry = raw.get(today.isoformat()) or {}

    # ── Вага ─────────────────────────────────────────────────────────────────
    last_kg, last_kg_date = _last_weight(wdata, today)
    weight_trend = _weight_trend(wdata, today, 30)
    target_kg = 75.0

    # ── Стрік / % місяць / 7-денна ───────────────────────────────────────────
    streaks    = {h["id"]: _streak(raw, h["id"], today)      for h in HABITS}
    month_pcts = {h["id"]: _month_pct(raw, h["id"], today)   for h in HABITS}
    histories  = {h["id"]: _week_history(raw, h["id"], today) for h in HABITS}

    # ── Стрік ─────────────────────────────────────────────────────────────────
    # Загальний стрік: скільки днів поспіль виконано ВСІ звички
    def _total_streak():
        s = 0
        d = today
        while True:
            entry = raw.get(d.isoformat()) or {}
            if all(entry.get(h["id"]) is True for h in HABITS):
                s += 1
                d -= timedelta(days=1)
            else:
                break
        return s

    total_streak = _total_streak()

    # ── Біг (Strava) ─────────────────────────────────────────────────────────
    run_data = None
    last_run = None
    try:
        from strava import get_month_stats, get_runs
        run_data = get_month_stats(today.year, today.month)
        runs_list = get_runs(days=60)
        if runs_list:
            last_run = sorted(runs_list, key=lambda r: r["date"], reverse=True)[0]
    except Exception as e:
        print(f"[report_card] strava error: {e}")

    # ── Портфель ─────────────────────────────────────────────────────────────
    portfolio = None
    try:
        from portfolio import get_portfolio_summary
        portfolio = get_portfolio_summary()
    except Exception as e:
        print(f"[report_card] portfolio error: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # РОЗМІРИ
    # ═══════════════════════════════════════════════════════════════════════════
    W     = 900
    PAD   = 28
    R     = 14   # радіус карточки

    # Висота секцій (приблизна, потім кроп)
    H_HEADER  = 90
    H_HABITS  = 30 + len(HABITS) * 70 + 20    # заголовок + рядки
    H_WEIGHT  = 30 + 160 + 20
    H_RUN     = 30 + 130 + 20
    H_DIVIDER = 30
    H_PORTFOLIO = 30 + 60 + len(portfolio or {}) * 52 + 30 if portfolio else 0
    H_FOOTER  = 50
    H = H_HEADER + H_HABITS + H_WEIGHT + H_RUN + H_PORTFOLIO + H_FOOTER + 60

    img  = Image.new("RGB", (W, H), _hex(BG))
    draw = ImageDraw.Draw(img)

    # ── Шрифти ───────────────────────────────────────────────────────────────
    f_h1    = _font(_SANS_BOLD, 30)
    f_h2    = _font(_SANS_BOLD, 20)
    f_label = _font(_SANS_BOLD, 16)
    f_body  = _font(_SANS, 15)
    f_small = _font(_SANS, 13)
    f_tiny  = _font(_SANS, 11)
    f_num   = _font(_SANS_BOLD, 24)
    f_sec   = _font(_SANS_BOLD, 12)

    UA_MONTHS = {1:"Січня",2:"Лютого",3:"Березня",4:"Квітня",
                 5:"Травня",6:"Червня",7:"Липня",8:"Серпня",
                 9:"Вересня",10:"Жовтня",11:"Листопада",12:"Грудня"}
    UA_DAYS   = {0:"Понеділок",1:"Вівторок",2:"Середа",3:"Четвер",
                 4:"П'ятниця",5:"Субота",6:"Неділя"}
    UA_MONTHS2 = {1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",
                  5:"Травень",6:"Червень",7:"Липень",8:"Серпень",
                  9:"Вересень",10:"Жовтень",11:"Листопад",12:"Грудень"}

    period_emoji = "☀️" if period == "morning" else "🌙"
    period_text  = "Ранковий звіт" if period == "morning" else "Вечірній звіт"
    date_str     = f"{UA_DAYS[today.weekday()]}, {today.day} {UA_MONTHS[today.month]} {today.year}"
    time_str     = now.strftime("%H:%M")

    y = PAD

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. ЗАГОЛОВОК
    # ═══════════════════════════════════════════════════════════════════════════
    _rounded_rect(draw, PAD, y, W - PAD, y + 80, R, fill=_hex(CARD), outline=_hex(BORDER))

    _draw_emoji(img, period_emoji, PAD + 18, y + 14, size=36)
    draw.text((PAD + 62, y + 14), period_text, font=f_h1, fill=_hex(TEXT))
    draw.text((PAD + 18, y + 52), date_str + f"  ·  {time_str}", font=f_small, fill=_hex(MUTED))

    # Загальний стрік справа
    if total_streak > 0:
        ts_str = f"{total_streak}д"
        _draw_emoji(img, "🔥", W - PAD - 80, y + 12, size=32)
        draw.text((W - PAD - 44, y + 14), ts_str, font=f_num, fill=_hex(ORANGE))
        lbl = "загальний"
        lw = draw.textlength(lbl, font=f_tiny)
        draw.text((W - PAD - 44 - (lw - draw.textlength(ts_str, font=f_num))//2 + 4, y + 50),
                  lbl, font=f_tiny, fill=_hex(MUTED))

    # Виконано сьогодні
    done_today   = sum(1 for h in HABITS if today_entry.get(h["id"]) is True)
    total_habits = len(HABITS)
    done_str     = f"{done_today}/{total_habits}"
    done_color   = GREEN if done_today == total_habits else (ORANGE if done_today >= total_habits // 2 else RED)
    ds_w = draw.textlength(done_str, font=f_num)
    right_x = W - PAD - 100 - 60 if total_streak > 0 else W - PAD - 18
    draw.text((right_x - ds_w, y + 14), done_str, font=f_num, fill=_hex(done_color))
    lbl2 = "виконано"
    lbl2_w = draw.textlength(lbl2, font=f_tiny)
    draw.text((right_x - lbl2_w, y + 50), lbl2, font=f_tiny, fill=_hex(MUTED))

    y += 80 + 16

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. ЗВИЧКИ
    # ═══════════════════════════════════════════════════════════════════════════
    draw.text((PAD, y), "ЗВИЧКИ", font=f_sec, fill=_hex(MUTED))
    y += 20

    ROW_H  = 66
    DOT_R  = 7   # радіус мінікрапки

    for h in HABITS:
        hid    = h["id"]
        hcolor = h["color"]
        status = today_entry.get(hid)    # True / False / None
        streak = streaks[hid]
        pct    = month_pcts[hid]
        hist   = histories[hid]

        row_bg = _hex(CARD) if status is True else _hex(CARD2)
        _rounded_rect(draw, PAD, y, W - PAD, y + ROW_H - 4, 12,
                      fill=row_bg, outline=_hex(BORDER))

        # Статус кружечок
        cx = PAD + 20
        cy = y + ROW_H // 2 - 2
        rc = 15
        if status is True:
            draw.ellipse([cx - rc, cy - rc, cx + rc, cy + rc], fill=_hex(hcolor))
            draw.line([(cx-7, cy), (cx-2, cy+7), (cx+8, cy-7)], fill=_hex("#FFFFFF"), width=3)
        elif status is False:
            draw.ellipse([cx - rc, cy - rc, cx + rc, cy + rc], fill=_hex(RED))
            draw.line([(cx-7, cy-7), (cx+7, cy+7)], fill=_hex("#FFFFFF"), width=3)
            draw.line([(cx+7, cy-7), (cx-7, cy+7)], fill=_hex("#FFFFFF"), width=3)
        else:
            draw.ellipse([cx - rc, cy - rc, cx + rc, cy + rc], outline=_hex(BORDER), width=2)

        # Emoji + назва
        ex = PAD + 42
        ey = y + ROW_H // 2 - 18
        _draw_emoji(img, h["emoji"], ex, ey, size=30)

        nx = ex + 36
        nc = TEXT if status is True else (MUTED if status is None else RED)
        draw.text((nx, y + 10), h["name"], font=f_label, fill=_hex(nc))

        # 7-денна міні-карта
        dot_x0 = nx
        dot_y  = y + 38
        for di, dv in enumerate(hist):
            dx = dot_x0 + di * (DOT_R * 2 + 5)
            dy = dot_y
            if dv is True:
                draw.ellipse([dx, dy, dx + DOT_R*2, dy + DOT_R*2], fill=_hex(hcolor))
            elif dv is False:
                draw.ellipse([dx, dy, dx + DOT_R*2, dy + DOT_R*2], fill=_hex(RED))
            else:
                draw.ellipse([dx, dy, dx + DOT_R*2, dy + DOT_R*2], outline=_hex(BORDER), width=1)
        week_x = dot_x0 + 7 * (DOT_R * 2 + 5) + 6
        draw.text((week_x, dot_y + 1), "7д", font=f_tiny, fill=_hex(MUTED))

        # Стрік 🔥
        sk_x = W - PAD - 200
        if streak > 0:
            _draw_emoji(img, "🔥", sk_x, y + 6, size=22)
            draw.text((sk_x + 26, y + 10), f"{streak}д", font=f_label, fill=_hex(ORANGE))
        else:
            draw.text((sk_x + 4, y + 10), "—", font=f_label, fill=_hex(BORDER))

        # % місяця
        pct_str  = f"{int(pct * 100)}%"
        pct_col  = GREEN if pct >= 0.8 else (ORANGE if pct >= 0.5 else RED)
        px       = W - PAD - 72
        draw.text((px, y + 10), pct_str, font=f_h2, fill=_hex(pct_col))
        draw.text((px, y + 36), "місяць", font=f_tiny, fill=_hex(MUTED))

        # Progress bar
        bx0 = px - 8
        bx1 = W - PAD - 16
        by  = y + 54
        draw.rounded_rectangle([bx0, by, bx1, by + 4], radius=2, fill=_hex(BORDER))
        fw = int((bx1 - bx0) * pct)
        if fw > 0:
            draw.rounded_rectangle([bx0, by, bx0 + fw, by + 4], radius=2, fill=_hex(pct_col))

        y += ROW_H

    y += 16

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. ВАГА
    # ═══════════════════════════════════════════════════════════════════════════
    draw.text((PAD, y), "ВАГА", font=f_sec, fill=_hex(MUTED))
    y += 20

    card_h = 155
    _rounded_rect(draw, PAD, y, W - PAD, y + card_h, R, fill=_hex(CARD), outline=_hex(BORDER))

    iy = y + 18
    ix = PAD + 20

    if last_kg is not None:
        _draw_emoji(img, "⚖️", ix, iy, size=36)
        draw.text((ix + 44, iy + 2),  f"{last_kg:.1f} кг",  font=f_num, fill=_hex(TEXT))

        if last_kg_date:
            age = (today - last_kg_date).days
            age_s = "сьогодні" if age == 0 else ("вчора" if age == 1 else f"{age}д тому")
            draw.text((ix + 44, iy + 34), age_s, font=f_tiny, fill=_hex(MUTED))

        diff = last_kg - target_kg
        diff_col = GREEN if diff <= 0 else (ORANGE if diff < 5 else RED)
        draw.text((ix, iy + 58), f"До цілі ({target_kg:.0f} кг): {diff:+.1f} кг",
                  font=f_body, fill=_hex(diff_col))

        # Sparkline
        if len(weight_trend) >= 2:
            sx0 = ix
            sx1 = W - PAD - 20
            sy0 = iy + 84
            sy1 = iy + 126
            sw  = sx1 - sx0
            sh  = sy1 - sy0

            kgs   = [v for _, v in weight_trend]
            mn    = min(kgs) - 0.5
            mx    = max(kgs) + 0.5
            span  = mx - mn or 1.0

            draw.rounded_rectangle([sx0, sy0, sx1, sy1], radius=6, fill=_hex(CARD2))

            pts = []
            for i, (_, v) in enumerate(weight_trend):
                px_i = sx0 + int(i / (len(weight_trend) - 1) * sw)
                py_i = sy1 - int((v - mn) / span * sh)
                pts.append((px_i, py_i))

            # Тінь під лінією
            fill_pts = pts + [(pts[-1][0], sy1), (pts[0][0], sy1)]
            shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            sd = ImageDraw.Draw(shadow)
            sd.polygon(fill_pts, fill=(*_hex(BLUE), 40))
            img.paste(shadow, mask=shadow.split()[3])
            draw = ImageDraw.Draw(img)

            for i in range(len(pts) - 1):
                draw.line([pts[i], pts[i+1]], fill=_hex(BLUE), width=2)

            # Точки
            for px_i, py_i in pts[::max(1, len(pts)//8)]:
                draw.ellipse([px_i-3, py_i-3, px_i+3, py_i+3], fill=_hex(BLUE))

            draw.text((sx0, sy1 + 3), f"{weight_trend[0][1]:.1f}", font=f_tiny, fill=_hex(MUTED))
            draw.text((sx1 - 30, sy1 + 3), f"{weight_trend[-1][1]:.1f}", font=f_tiny, fill=_hex(TEXT))

            td = weight_trend[-1][1] - weight_trend[0][1]
            tc = GREEN if td < 0 else RED
            ts = f"30д: {td:+.1f} кг"
            tw = draw.textlength(ts, font=f_tiny)
            draw.text((sx1 - tw, iy + 2), ts, font=f_tiny, fill=_hex(tc))
    else:
        draw.text((ix, iy + 40), "Немає даних про вагу", font=f_body, fill=_hex(MUTED))

    y += card_h + 16

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. БІГ (STRAVA)
    # ═══════════════════════════════════════════════════════════════════════════
    draw.text((PAD, y), "БІГ — STRAVA", font=f_sec, fill=_hex(MUTED))
    y += 20

    run_h = 120
    _rounded_rect(draw, PAD, y, W - PAD, y + run_h, R, fill=_hex(CARD), outline=_hex(BORDER))

    rx = PAD + 20
    ry = y + 16

    _draw_emoji(img, "🏃", rx, ry, size=34)

    if run_data and run_data.get("runs", 0) > 0:
        runs_cnt = run_data["runs"]
        km_total = run_data.get("km", 0)
        pace_str = run_data.get("avg_pace_str", "—")
        avg_hr   = run_data.get("avg_hr")

        draw.text((rx + 42, ry + 2),  f"{km_total:.1f} км", font=f_num, fill=_hex(GREEN))
        draw.text((rx + 42, ry + 32), f"{runs_cnt} пробіжок — {UA_MONTHS2[today.month]}", font=f_small, fill=_hex(MUTED))

        parts = []
        if pace_str and pace_str != "—":
            parts.append(f"∅ темп {pace_str} хв/км")
        best = run_data.get("best_run")
        if best:
            parts.append(f"Найдовша {best.get('dist_km', 0):.1f} км")
        if avg_hr:
            parts.append(f"ЧСС {int(avg_hr)} уд/хв")
        if parts:
            draw.text((rx, ry + 56), "  ·  ".join(parts), font=f_small, fill=_hex(TEXT))

        # Остання пробіжка
        if last_run:
            lr_date = last_run["date"]
            age_d   = (today - lr_date.date() if hasattr(lr_date, "date") else today - lr_date).days
            age_s   = "сьогодні" if age_d == 0 else ("вчора" if age_d == 1 else f"{age_d}д тому")
            lr_km   = last_run.get("dist_km", 0)
            lr_pace = ""
            if last_run.get("pace_sec", 0) > 0:
                ps = last_run["pace_sec"]
                lr_pace = f"  {int(ps//60)}:{int(ps%60):02d}/км"
            lr_hr = f"  ❤️ {int(last_run['hr'])}" if last_run.get("hr") else ""
            draw.text((rx, ry + 82),
                      f"Остання: {lr_km:.1f} км ({age_s}){lr_pace}{lr_hr}",
                      font=f_small, fill=_hex(MUTED))
    else:
        draw.text((rx + 42, ry + 8), f"0 пробіжок — {UA_MONTHS2[today.month]}", font=f_h2, fill=_hex(MUTED))
        if last_run:
            lr_date = last_run["date"]
            age_d = (today - (lr_date.date() if hasattr(lr_date, "date") else lr_date)).days
            age_s = f"{age_d}д тому"
            lr_km = last_run.get("dist_km", 0)
            lr_pace = ""
            if last_run.get("pace_sec", 0) > 0:
                ps = last_run["pace_sec"]
                lr_pace = f"  {int(ps//60)}:{int(ps%60):02d}/км"
            draw.text((rx + 42, ry + 38), f"Остання: {lr_km:.1f} км ({age_s}){lr_pace}", font=f_small, fill=_hex(MUTED))
        else:
            draw.text((rx + 42, ry + 40), "Підключи Strava або запиши пробіжку", font=f_small, fill=_hex(BORDER))

    y += run_h + 16

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. ПОРТФЕЛЬ
    # ═══════════════════════════════════════════════════════════════════════════
    if portfolio:
        positions  = portfolio.get("positions", portfolio)  # dict sym→data
        # Якщо get_portfolio_summary повертає dict з positions або dict монет напряму
        if "positions" in portfolio:
            positions = portfolio["positions"]
        total_val   = portfolio.get("total_value", sum(p.get("value", 0) for p in positions.values()))
        change24    = portfolio.get("change_24h", portfolio.get("change_24h_usd", 0))
        total_pnl   = portfolio.get("total_pnl")

        draw.text((PAD, y), "ПОРТФЕЛЬ", font=f_sec, fill=_hex(MUTED))
        y += 20

        # Підсумковий заголовок
        ph = 64
        _rounded_rect(draw, PAD, y, W - PAD, y + ph, R, fill=_hex(CARD), outline=_hex(BORDER))

        _draw_emoji(img, "💼", PAD + 18, y + 14, size=32)
        draw.text((PAD + 58, y + 12), f"${total_val:,.0f}", font=f_num, fill=_hex(TEXT))

        # 24г зміна
        ch_col = GREEN if change24 >= 0 else RED
        ch_s   = f"24г: {'+' if change24>=0 else ''}{change24:,.0f}$"
        draw.text((PAD + 58, y + 42), ch_s, font=f_small, fill=_hex(ch_col))

        # P&L якщо є
        if total_pnl is not None:
            pnl_col = GREEN if total_pnl >= 0 else RED
            pnl_s   = f"P&L: {'+' if total_pnl>=0 else ''}{total_pnl:,.0f}$"
            pnl_w   = draw.textlength(pnl_s, font=f_body)
            draw.text((W - PAD - 20 - pnl_w, y + 20), pnl_s, font=f_body, fill=_hex(pnl_col))

        y += ph + 10

        # Монети
        all_pos = sorted(
            [(sym, p) for sym, p in positions.items() if p.get("value", 0) > 0],
            key=lambda x: x[1].get("value", 0), reverse=True
        )
        MAX_COINS = 8
        sorted_pos = all_pos[:MAX_COINS]
        hidden_cnt = len(all_pos) - MAX_COINS

        for sym, pos in sorted_pos:
            row_h = 50
            _rounded_rect(draw, PAD, y, W - PAD, y + row_h, 10, fill=_hex(CARD2), outline=_hex(BORDER))

            val    = pos.get("value", 0)
            price  = pos.get("price", 0)
            pct    = pos.get("pct_of_total", val / total_val * 100 if total_val else 0)
            chg24  = pos.get("change24", pos.get("change_24h_pct", 0)) or 0

            # Назва монети
            draw.text((PAD + 18, y + 10), sym, font=f_label, fill=_hex(TEXT))

            # Ціна
            price_s = f"${price:,.2f}" if price < 1000 else f"${price:,.0f}"
            draw.text((PAD + 18, y + 32), price_s, font=f_tiny, fill=_hex(MUTED))

            # % 24г
            chg_col = GREEN if chg24 >= 0 else RED
            chg_s   = f"{'+' if chg24>=0 else ''}{chg24:.1f}%"
            draw.text((PAD + 120, y + 16), chg_s, font=f_label, fill=_hex(chg_col))

            # Вартість
            val_s = f"${val:,.0f}"
            val_w = draw.textlength(val_s, font=f_label)
            draw.text((W - PAD - 20 - val_w, y + 10), val_s, font=f_label, fill=_hex(TEXT))

            # % портфелю
            pct_s = f"{pct:.0f}%"
            pct_w = draw.textlength(pct_s, font=f_small)
            draw.text((W - PAD - 20 - pct_w, y + 32), pct_s, font=f_small, fill=_hex(MUTED))

            # Мінібар частки
            bar_x0 = PAD + 200
            bar_x1 = W - PAD - 90
            bar_y  = y + 23
            draw.rounded_rectangle([bar_x0, bar_y, bar_x1, bar_y + 5], radius=2, fill=_hex(BORDER))
            fw_bar = int((bar_x1 - bar_x0) * min(pct / 100, 1.0))
            if fw_bar > 0:
                draw.rounded_rectangle([bar_x0, bar_y, bar_x0 + fw_bar, bar_y + 5], radius=2, fill=_hex(chg_col))

            y += row_h + 6

        # "та ще X монет"
        if hidden_cnt > 0:
            rest_val = sum(p.get("value", 0) for _, p in all_pos[MAX_COINS:])
            rest_s = f"+ ще {hidden_cnt} монет  (${rest_val:,.0f})"
            draw.text((PAD + 18, y + 4), rest_s, font=f_small, fill=_hex(MUTED))
            y += 24

        y += 10

    # ═══════════════════════════════════════════════════════════════════════════
    # 6. FOOTER
    # ═══════════════════════════════════════════════════════════════════════════
    draw.line([(PAD, y + 4), (W - PAD, y + 4)], fill=_hex(BORDER), width=1)
    footer = f"Оновлено {time_str}  ·  Олег Новосадов  ·  Кошіце, Словаччина"
    fw_txt = draw.textlength(footer, font=f_tiny)
    draw.text(((W - fw_txt) // 2, y + 12), footer, font=f_tiny, fill=_hex(MUTED))

    # ── Обрізаємо ────────────────────────────────────────────────────────────
    final_h = y + 40
    img     = img.crop((0, 0, W, final_h))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


# ── Локальний тест ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    data = generate_report_card("morning")
    if data:
        with open("/tmp/report_card_test.png", "wb") as f:
            f.write(data)
        print(f"Saved {len(data):,} bytes → /tmp/report_card_test.png")
    else:
        print("Failed (PIL not available?)")
