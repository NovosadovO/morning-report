"""
report_card.py — Красивий PNG-звіт для Telegram.
Надсилається двічі на день: 09:00 і 20:00 (UTC+2).
PIL-based: повна підтримка emoji через NotoColorEmoji.
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
_FONT_DIR = "/usr/share/fonts/truetype"
_SANS_BOLD   = f"{_FONT_DIR}/dejavu/DejaVuSans-Bold.ttf"
_SANS        = f"{_FONT_DIR}/dejavu/DejaVuSans.ttf"
_EMOJI_FONT  = f"{_FONT_DIR}/noto/NotoColorEmoji.ttf"

# ── Палітра ───────────────────────────────────────────────────────────────────
BG         = "#0D1117"
CARD       = "#161B22"
CARD2      = "#1C2128"
BORDER     = "#30363D"
TEXT       = "#E6EDF3"
MUTED      = "#8B949E"
GREEN      = "#3FB950"
RED        = "#F85149"
BLUE       = "#58A6FF"
PURPLE     = "#A371F7"
ORANGE     = "#D29922"
ACCENT     = "#58A6FF"

def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()

def _emoji_font(size=109):
    """NotoColorEmoji підтримує тільки розмір 109."""
    try:
        return ImageFont.truetype(_EMOJI_FONT, 109)
    except:
        return None

def _draw_emoji(canvas: Image.Image, draw: ImageDraw.Draw, emoji: str,
                x: int, y: int, size: int = 32):
    """Малює один emoji через PIL overlay (масштаб з 109→size)."""
    ef = _emoji_font()
    if not ef or not os.path.exists(_EMOJI_FONT):
        # fallback — нічого
        return
    tmp = Image.new("RGBA", (130, 130), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((5, 5), emoji, font=ef, embedded_color=True)
    tmp = tmp.resize((size, size), Image.LANCZOS)
    canvas.paste(tmp, (x, y), tmp)

def _streak(raw: dict, habit_key: str, today: date) -> int:
    """Рахує поточний стрік (послідовні дні ✓ включно з сьогодні)."""
    streak = 0
    d = today
    while True:
        entry = raw.get(d.isoformat(), {}) or {}
        v = entry.get(habit_key)
        if v is True:
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    return streak

def _month_pct(raw: dict, habit_key: str, today: date) -> float:
    """Відсоток виконання за поточний місяць."""
    start = today.replace(day=1)
    total, done = 0, 0
    d = start
    while d <= today:
        entry = raw.get(d.isoformat(), {}) or {}
        v = entry.get(habit_key)
        if v is not None:
            total += 1
            if v is True:
                done += 1
        d += timedelta(days=1)
    return done / total if total > 0 else 0.0

def _week_history(raw: dict, habit_key: str, today: date, days: int = 7):
    """Останні N днів: True/False/None."""
    result = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        entry = raw.get(d.isoformat(), {}) or {}
        v = entry.get(habit_key)
        result.append(v)  # True / False / None
    return result

def _last_weight(wdata: dict, today: date):
    """Остання відома вага + дата."""
    keys = sorted(wdata.keys(), reverse=True)
    for k in keys:
        try:
            d = date.fromisoformat(k)
            if d <= today:
                return float(wdata[k]), d
        except:
            pass
    return None, None

def _weight_trend(wdata: dict, today: date, days: int = 30):
    """Список (date, kg) за останні N днів."""
    result = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        v = wdata.get(d.isoformat())
        if v is not None:
            result.append((d, float(v)))
    return result


# ── ГОЛОВНА ФУНКЦІЯ ───────────────────────────────────────────────────────────

def generate_report_card(period: str = "evening") -> bytes | None:
    """
    period: "morning" (09:00) або "evening" (20:00)
    Повертає PNG bytes.
    """
    if not HAS_PIL:
        return None

    try:
        from storage import load_habits, load_weight
    except ImportError:
        return None

    now = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now.date()

    raw   = load_habits() or {}
    wdata = load_weight() or {}

    # ── Звички ───────────────────────────────────────────────────────────────
    HABITS = [
        {"id": "shower", "name": "Холодний душ",    "emoji": "🚿", "color": BLUE},
        {"id": "run",    "name": "Пробіжка",         "emoji": "🏃", "color": GREEN},
        {"id": "water",  "name": "Вода (2л+)",        "emoji": "💧", "color": "#1F6FEB"},
        {"id": "tea",    "name": "Трав'яний чай",    "emoji": "🍵", "color": ORANGE},
        {"id": "sauna",  "name": "Сауна",             "emoji": "🧖", "color": RED},
        {"id": "spray",  "name": "Спрей для волосся", "emoji": "💈", "color": PURPLE},
    ]

    today_entry = raw.get(today.isoformat(), {}) or {}

    # ── Вага ──────────────────────────────────────────────────────────────────
    last_kg, last_kg_date = _last_weight(wdata, today)
    weight_trend = _weight_trend(wdata, today, 30)
    target_kg = 75.0

    # ── Стрик ─────────────────────────────────────────────────────────────────
    streaks = {h["id"]: _streak(raw, h["id"], today) for h in HABITS}
    month_pcts = {h["id"]: _month_pct(raw, h["id"], today) for h in HABITS}
    histories = {h["id"]: _week_history(raw, h["id"], today, 7) for h in HABITS}

    # ── Біг (Strava) ──────────────────────────────────────────────────────────
    run_data = None
    try:
        from strava import get_month_stats
        run_data = get_month_stats(today.year, today.month)
    except:
        pass

    # ═══════════════════════════════════════════════════════════════════════════
    # РОЗМІРИ І КОМПОНУВАННЯ
    # ═══════════════════════════════════════════════════════════════════════════
    W = 900
    PAD = 28
    CARD_R = 14  # радіус кута карточки

    # Блоки:
    # 1. Заголовок           ~90px
    # 2. Звички (6 рядків)   ~420px
    # 3. Вага                ~160px
    # 4. Біг                 ~120px
    # 5. Footer              ~50px
    H = 90 + 420 + 170 + 130 + 60
    H += 20  # запас

    img = Image.new("RGB", (W, H), _hex(BG))
    draw = ImageDraw.Draw(img)

    # ── Шрифти ───────────────────────────────────────────────────────────────
    f_title   = _font(_SANS_BOLD, 28)
    f_sub     = _font(_SANS_BOLD, 17)
    f_label   = _font(_SANS_BOLD, 15)
    f_small   = _font(_SANS, 13)
    f_tiny    = _font(_SANS, 11)
    f_num     = _font(_SANS_BOLD, 22)
    f_pct     = _font(_SANS_BOLD, 13)

    UA_MONTHS = {1:"Січня",2:"Лютого",3:"Березня",4:"Квітня",
                 5:"Травня",6:"Червня",7:"Липня",8:"Серпня",
                 9:"Вересня",10:"Жовтня",11:"Листопада",12:"Грудня"}
    UA_DAYS = {0:"Понеділок",1:"Вівторок",2:"Середа",3:"Четвер",
               4:"П'ятниця",5:"Субота",6:"Неділя"}

    period_emoji  = "☀️" if period == "morning" else "🌙"
    period_text   = "Ранковий звіт" if period == "morning" else "Вечірній звіт"
    date_str = f"{UA_DAYS[today.weekday()]}, {today.day} {UA_MONTHS[today.month]} {today.year}"

    def draw_rounded_rect(d, x0, y0, x1, y1, r, fill, outline=None, width=1):
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill,
                             outline=outline, width=width)

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. ЗАГОЛОВОК
    # ═══════════════════════════════════════════════════════════════════════════
    y = PAD

    # Фон заголовка
    draw_rounded_rect(draw, PAD, y, W - PAD, y + 72, CARD_R,
                      fill=_hex(CARD), outline=_hex(BORDER), width=1)

    # Emoji заголовку через PIL overlay
    _draw_emoji(img, draw, period_emoji, PAD + 18, y + 12, size=32)
    draw.text((PAD + 56, y + 14), period_text, font=f_title, fill=_hex(TEXT))
    draw.text((PAD + 18, y + 44), date_str, font=f_small, fill=_hex(MUTED))

    # Done today count (права сторона)
    done_today = sum(1 for h in HABITS if today_entry.get(h["id"]) is True)
    total_habits = len(HABITS)
    done_str = f"{done_today}/{total_habits}"
    done_color = GREEN if done_today == total_habits else (ORANGE if done_today >= total_habits // 2 else RED)
    tw = draw.textlength(done_str, font=f_num)
    draw.text((W - PAD - 18 - tw, y + 14), done_str, font=f_num, fill=_hex(done_color))
    label_done = "виконано сьогодні"
    lw = draw.textlength(label_done, font=f_tiny)
    draw.text((W - PAD - 18 - lw, y + 46), label_done, font=f_tiny, fill=_hex(MUTED))

    y += 72 + 14

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. ЗВИЧКИ
    # ═══════════════════════════════════════════════════════════════════════════
    # Заголовок секції
    draw.text((PAD, y), "ЗВИЧКИ", font=f_pct, fill=_hex(MUTED))
    y += 20

    ROW_H = 62
    MINI_DOT = 10  # розмір кружечка в тижневій мині-картці

    for h in HABITS:
        hid     = h["id"]
        hname   = h["name"]
        hemoji  = h["emoji"]
        hcolor  = h["color"]
        status  = today_entry.get(hid)  # True / False / None
        streak  = streaks[hid]
        pct     = month_pcts[hid]
        hist    = histories[hid]

        # Фон рядку
        row_bg = _hex(CARD) if status is True else _hex(CARD2)
        draw_rounded_rect(draw, PAD, y, W - PAD, y + ROW_H - 4, 10,
                          fill=row_bg, outline=_hex(BORDER), width=1)

        # ── Статус кружечок (ліворуч) ────────────────────────────────────
        circle_x = PAD + 16
        circle_y = y + ROW_H // 2 - 2
        r_c = 14
        if status is True:
            draw.ellipse([circle_x - r_c, circle_y - r_c,
                          circle_x + r_c, circle_y + r_c],
                         fill=_hex(hcolor))
            # Галочка
            draw.line([(circle_x - 6, circle_y), (circle_x - 1, circle_y + 6),
                       (circle_x + 7, circle_y - 6)],
                      fill=_hex("#FFFFFF"), width=2)
        elif status is False:
            draw.ellipse([circle_x - r_c, circle_y - r_c,
                          circle_x + r_c, circle_y + r_c],
                         fill=_hex(RED))
            draw.line([(circle_x - 6, circle_y - 6), (circle_x + 6, circle_y + 6)],
                      fill=_hex("#FFFFFF"), width=2)
            draw.line([(circle_x + 6, circle_y - 6), (circle_x - 6, circle_y + 6)],
                      fill=_hex("#FFFFFF"), width=2)
        else:
            draw.ellipse([circle_x - r_c, circle_y - r_c,
                          circle_x + r_c, circle_y + r_c],
                         outline=_hex(BORDER), width=2)

        # ── Emoji + Назва ────────────────────────────────────────────────
        emoji_x = PAD + 36
        emoji_y = y + ROW_H // 2 - 16
        _draw_emoji(img, draw, hemoji, emoji_x, emoji_y, size=28)

        name_x = emoji_x + 34
        name_color = TEXT if status is True else (MUTED if status is None else RED)
        draw.text((name_x, y + 12), hname, font=f_label, fill=_hex(name_color))

        # ── Тижнева мини-карта (7 крапок) ───────────────────────────────
        dots_x = name_x
        dots_y = y + 36
        for di, dv in enumerate(hist):
            dx = dots_x + di * (MINI_DOT + 4)
            dy = dots_y
            if dv is True:
                draw.ellipse([dx, dy, dx + MINI_DOT, dy + MINI_DOT],
                             fill=_hex(hcolor))
            elif dv is False:
                draw.ellipse([dx, dy, dx + MINI_DOT, dy + MINI_DOT],
                             fill=_hex(RED))
            else:
                draw.ellipse([dx, dy, dx + MINI_DOT, dy + MINI_DOT],
                             outline=_hex(BORDER), width=1)

        # Підпис "7 днів"
        draw.text((dots_x + 7 * (MINI_DOT + 4) + 4, dots_y),
                  "7 днів", font=f_tiny, fill=_hex(MUTED))

        # ── Стрік ────────────────────────────────────────────────────────
        streak_x = W - PAD - 200
        if streak > 0:
            _draw_emoji(img, draw, "🔥", streak_x, y + 8, size=22)
            draw.text((streak_x + 26, y + 10), f"{streak}д", font=f_label, fill=_hex(ORANGE))
        else:
            draw.text((streak_x, y + 10), "—", font=f_label, fill=_hex(BORDER))

        # ── % місяця ─────────────────────────────────────────────────────
        pct_str = f"{int(pct * 100)}%"
        pct_color = GREEN if pct >= 0.8 else (ORANGE if pct >= 0.5 else RED)
        pct_x = W - PAD - 80
        draw.text((pct_x, y + 8), pct_str, font=f_sub, fill=_hex(pct_color))
        draw.text((pct_x, y + 32), "місяць", font=f_tiny, fill=_hex(MUTED))

        # ── Прогрес-бар % ────────────────────────────────────────────────
        bar_x0 = pct_x - 12
        bar_x1 = W - PAD - 16
        bar_y = y + 50
        draw.rounded_rectangle([bar_x0, bar_y, bar_x1, bar_y + 4],
                                radius=2, fill=_hex(BORDER))
        fill_w = int((bar_x1 - bar_x0) * pct)
        if fill_w > 0:
            draw.rounded_rectangle([bar_x0, bar_y, bar_x0 + fill_w, bar_y + 4],
                                    radius=2, fill=_hex(pct_color))

        y += ROW_H

    y += 12

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. ВАГА
    # ═══════════════════════════════════════════════════════════════════════════
    draw.text((PAD, y), "ВАГА", font=f_pct, fill=_hex(MUTED))
    y += 20

    draw_rounded_rect(draw, PAD, y, W - PAD, y + 140, CARD_R,
                      fill=_hex(CARD), outline=_hex(BORDER), width=1)

    inner_y = y + 16
    inner_x = PAD + 18

    if last_kg is not None:
        # Поточна вага
        kg_str = f"{last_kg:.1f} кг"
        _draw_emoji(img, draw, "⚖️", inner_x, inner_y, size=32)
        draw.text((inner_x + 38, inner_y + 2), kg_str, font=f_num, fill=_hex(TEXT))

        # Дата останнього виміру
        if last_kg_date:
            age_days = (today - last_kg_date).days
            age_str = "сьогодні" if age_days == 0 else (f"вчора" if age_days == 1 else f"{age_days}д тому")
            draw.text((inner_x + 38, inner_y + 32), age_str, font=f_tiny, fill=_hex(MUTED))

        # До цілі
        diff = last_kg - target_kg
        diff_str = f"До цілі ({target_kg:.0f} кг): {diff:+.1f} кг"
        diff_color = GREEN if diff <= 0 else (ORANGE if diff < 5 else RED)
        draw.text((inner_x, inner_y + 52), diff_str, font=f_small, fill=_hex(diff_color))

        # Мини-графік ваги (sparkline)
        if len(weight_trend) >= 2:
            spark_x0 = inner_x
            spark_x1 = W - PAD - 18
            spark_y0 = inner_y + 78
            spark_y1 = inner_y + 114
            spark_w = spark_x1 - spark_x0
            spark_h = spark_y1 - spark_y0

            kgs = [v for _, v in weight_trend]
            min_kg = min(kgs) - 0.5
            max_kg = max(kgs) + 0.5
            span = max_kg - min_kg or 1.0

            # Фон
            draw.rounded_rectangle([spark_x0, spark_y0, spark_x1, spark_y1],
                                    radius=6, fill=_hex(CARD2))

            # Лінія
            pts = []
            for i, (d_k, v_k) in enumerate(weight_trend):
                px = spark_x0 + int(i / (len(weight_trend) - 1) * spark_w)
                py = spark_y1 - int((v_k - min_kg) / span * spark_h)
                pts.append((px, py))

            if len(pts) >= 2:
                for i in range(len(pts) - 1):
                    draw.line([pts[i], pts[i + 1]], fill=_hex(BLUE), width=2)

            # Перший і останній підпис
            draw.text((pts[0][0], spark_y1 + 2),
                      f"{weight_trend[0][1]:.1f}", font=f_tiny, fill=_hex(MUTED))
            draw.text((pts[-1][0] - 20, spark_y1 + 2),
                      f"{weight_trend[-1][1]:.1f}", font=f_tiny, fill=_hex(TEXT))

            # Тренд (перша та остання точка)
            trend_diff = weight_trend[-1][1] - weight_trend[0][1]
            t_str = f"Тренд 30д: {trend_diff:+.1f} кг"
            t_color = GREEN if trend_diff < 0 else RED
            tw2 = draw.textlength(t_str, font=f_tiny)
            draw.text((W - PAD - 18 - tw2, inner_y + 2), t_str,
                      font=f_tiny, fill=_hex(t_color))
    else:
        draw.text((inner_x, inner_y + 30), "Немає даних про вагу",
                  font=f_small, fill=_hex(MUTED))

    y += 140 + 14

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. БІГ
    # ═══════════════════════════════════════════════════════════════════════════
    draw.text((PAD, y), "БІГ (STRAVA)", font=f_pct, fill=_hex(MUTED))
    y += 20

    draw_rounded_rect(draw, PAD, y, W - PAD, y + 100, CARD_R,
                      fill=_hex(CARD), outline=_hex(BORDER), width=1)

    run_x = PAD + 18
    run_y = y + 16

    _draw_emoji(img, draw, "🏃", run_x, run_y, size=32)

    if run_data and run_data.get("runs", 0) > 0:
        runs_cnt = run_data["runs"]
        km_total = run_data.get("km", 0)
        pace_str = run_data.get("avg_pace_str", "—")

        draw.text((run_x + 38, run_y + 2), f"{km_total:.1f} км", font=f_num, fill=_hex(GREEN))
        draw.text((run_x + 38, run_y + 32), f"{runs_cnt} пробіжок цього місяця", font=f_tiny, fill=_hex(MUTED))

        # Темп і найдовша
        details = []
        if pace_str and pace_str != "—":
            details.append(f"∅ темп: {pace_str} хв/км")
        best = run_data.get("best_run")
        if best:
            details.append(f"Найдовша: {best.get('dist_km', 0):.1f} км")
        if details:
            draw.text((run_x, run_y + 54), "  ·  ".join(details), font=f_small, fill=_hex(TEXT))
    else:
        UA_MONTHS2 = {1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",
                      5:"Травень",6:"Червень",7:"Липень",8:"Серпень",
                      9:"Вересень",10:"Жовтень",11:"Листопад",12:"Грудень"}
        draw.text((run_x + 38, run_y + 2),
                  f"0 пробіжок — {UA_MONTHS2[today.month]}",
                  font=f_sub, fill=_hex(MUTED))
        draw.text((run_x + 38, run_y + 30),
                  "Підключи Strava або запиши пробіжку ✍️",
                  font=f_tiny, fill=_hex(BORDER))

    y += 100 + 14

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. FOOTER
    # ═══════════════════════════════════════════════════════════════════════════
    time_str = now.strftime("%H:%M")
    footer = f"Оновлено о {time_str}  ·  Олег Новосадов  ·  Кошіце, Словаччина"
    fw = draw.textlength(footer, font=f_tiny)
    draw.text(((W - fw) // 2, y + 8), footer, font=f_tiny, fill=_hex(MUTED))

    # Тонка лінія зверху footer
    draw.line([(PAD, y + 4), (W - PAD, y + 4)], fill=_hex(BORDER), width=1)

    # ── Обрізаємо до реального контенту ──────────────────────────────────────
    final_h = y + 40
    img = img.crop((0, 0, W, final_h))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


if __name__ == "__main__":
    data = generate_report_card("evening")
    if data:
        with open("/tmp/report_card_test.png", "wb") as f:
            f.write(data)
        print(f"Saved: {len(data)} bytes → /tmp/report_card_test.png")
    else:
        print("Failed")
