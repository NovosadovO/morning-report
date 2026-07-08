#!/usr/bin/env python3
"""
Недільний підсумок о 18:45 — повний звіт тижня.
Включає: сон, звички, ліки, вага, біг + рекомендації.
"""

import os, json, urllib.request
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]

_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def api(method, data=None):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    payload = json.dumps(data or {}).encode()
    req     = urllib.request.Request(url, data=payload,
              headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"API error {method}: {e}")
        return {}

def send(text):
    return api("sendMessage", {
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4090],
        "parse_mode": "HTML"
    })

def now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def _fmt_dur(minutes):
    h = minutes // 60
    m = minutes % 60
    return f"{h}г {m:02d}хв"

def bar(value, max_val, width=8):
    """Прогрес-бар: bar(5, 7) → '▓▓▓▓▓░░'"""
    filled = round(value / max_val * width) if max_val > 0 else 0
    filled = max(0, min(width, filled))
    return "🟩" * filled + "⬜️" * (width - filled)

# ─── ДАНІ ─────────────────────────────────────────────────────────────────────

def get_week_dates():
    """Повертає список 7 дат за минулий тиждень (пн–нд)."""
    now = now_local()
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]

def get_habits_data():
    """Завантажує дані звичок через storage (Google Sheets)."""
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import load_habits
        return load_habits()
    except Exception as e:
        print(f"get_habits_data error: {e}")
        return load_json("/tmp/habits_data.json", {})

def get_weight_data():
    """Завантажує дані ваги."""
    path = "/tmp/weight_data.json"
    data = load_json(path, {})
    if not data:
        initial = os.path.join(_DIR, "weight_data_initial.json")
        data = load_json(initial, {})
    return data

def get_meds_data():
    """Завантажує дані ліків через storage (Google Sheets)."""
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import load_meds
        return load_meds()
    except Exception as e:
        print(f"get_meds_data error: {e}")
        return load_json(os.path.join(_DIR, "meds_data.json"), {})

def get_runs_from_health():
    """Витягує пробіжки з Apple Health XML за останні 7 днів."""
    import re
    XML = "/tmp/health_export/apple_health_export/export.xml"
    try:
        with open(XML, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except:
        return []

    # Шукаємо WorkoutActivityTypeRunning
    pattern = re.compile(
        r'workoutActivityType="HKWorkoutActivityTypeRunning"[^>]*'
        r'startDate="([^"]+)"[^>]*'
        r'endDate="([^"]+)"[^>]*'
        r'totalDistance="([^"]+)"[^>]*'
        r'(?:totalEnergyBurned="([^"]*)"[^>]*)?'
        r'duration="([^"]+)"'
    )
    # Alternate pattern
    pattern2 = re.compile(
        r'<Workout workoutActivityType="HKWorkoutActivityTypeRunning"[^>]+'
        r'duration="([^"]+)"[^>]+'
        r'totalDistance="([^"]+)"[^>]+'
        r'startDate="([^"]+)"'
    )

    week_dates = get_week_dates()
    runs = []

    for line in content.split('\n'):
        if 'HKWorkoutActivityTypeRunning' not in line:
            continue
        m = re.search(
            r'startDate="([^"]+)".*?duration="([^"]+)".*?totalDistance="([^"]+)"',
            line
        )
        if not m:
            m = re.search(
                r'duration="([^"]+)".*?totalDistance="([^"]+)".*?startDate="([^"]+)"',
                line
            )
            if m:
                dur_s, dist_s, start_s = m.groups()
            else:
                continue
        else:
            start_s, dur_s, dist_s = m.groups()

        try:
            # Парсимо дату
            start_s_clean = re.sub(r' ([+-]\d{4})$', r'\1', start_s.strip()).replace(' ', 'T', 1)
            dt = datetime.fromisoformat(start_s_clean).astimezone(timezone.utc)
            local_dt = dt + timedelta(hours=2)
            date_str = local_dt.strftime("%Y-%m-%d")

            if date_str not in week_dates:
                continue

            dist_km = float(dist_s)
            dur_min = float(dur_s)

            runs.append({
                "date": date_str,
                "km":   round(dist_km, 2),
                "min":  round(dur_min, 1),
            })
        except Exception as e:
            continue

    return runs

# ─── БЛОКИ ЗВІТУ ──────────────────────────────────────────────────────────────

def block_header():
    now = now_local()
    week_start = (now - timedelta(days=6)).strftime("%d.%m")
    week_end   = now.strftime("%d.%m.%Y")
    return (
        f"📋 <b>ТИЖНЕВИЙ ПІДСУМОК</b>\n"
        f"<i>{week_start} – {week_end}</i>\n"
        f"{'━'*22}"
    )

def block_sleep():
    """Блок сну."""
    try:
        from sleep import get_weekly_sleep_stats, _fmt_dur as fmt
        stats = get_weekly_sleep_stats(7)
    except Exception as e:
        return f"😴 <b>СОН</b>\n⚠️ Помилка: {e}"

    if not stats:
        return "😴 <b>СОН</b>\nДані відсутні"

    lines = ["😴 <b>СОН</b>"]

    avg = stats["avg_min"]
    quality = "😊" if avg >= 480 else ("🙂" if avg >= 420 else ("😐" if avg >= 360 else "😩"))
    lines.append(f"Середній: <b>{_fmt_dur(avg)}</b>  {quality}")

    if stats["avg_deep"] > 0:
        lines.append(f"Глибокий: <b>{_fmt_dur(stats['avg_deep'])}</b>  │  REM: <b>{_fmt_dur(stats['avg_rem'])}</b>")

    lines.append("")
    for r in stats["records"]:
        h = r["asleep_min"] // 60
        b = bar(r["asleep_min"], 600, 8)  # макс 10г
        e = "😊" if r["asleep_min"] >= 480 else ("🙂" if r["asleep_min"] >= 420 else ("😐" if r["asleep_min"] >= 360 else "😩"))
        d = r["date"][5:]
        lines.append(f"<code>{d} {b}</code> {_fmt_dur(r['asleep_min'])} {e}")

    lines.append(f"\n🏆 {_fmt_dur(stats['best']['asleep_min'])} ({stats['best']['date'][5:]})")
    lines.append(f"😩 {_fmt_dur(stats['worst']['asleep_min'])} ({stats['worst']['date'][5:]})")

    return "\n".join(lines)

def block_habits():
    """Блок звичок."""
    HABITS_META = [
        {"id": "shower", "name": "Холодний душ", "emoji": "🚿"},
        {"id": "run",    "name": "Біг",           "emoji": "🏃"},
        {"id": "water",  "name": "Вода",           "emoji": "💧"},
        {"id": "tea",    "name": "Чай",            "emoji": "🍵"},
        {"id": "sauna",  "name": "Сауна",          "emoji": "🧖"},
    ]

    db    = get_habits_data()
    dates = get_week_dates()

    lines = ["✅ <b>ЗВИЧКИ</b>"]

    total_score = 0
    max_score   = len(HABITS_META) * 7

    for h in HABITS_META:
        done  = sum(1 for d in dates if db.get(d, {}).get(h["id"]) is True)
        pct   = done / 7 * 100
        b     = bar(done, 7, 7)
        medal = "🥇" if done == 7 else ("🥈" if done >= 5 else ("🥉" if done >= 3 else "💤"))
        total_score += done
        lines.append(
            f"{h['emoji']} {h['name']}\n"
            f"<code>{b}</code> {done}/7 {pct:.0f}% {medal}"
        )

    overall_pct = total_score / max_score * 100 if max_score > 0 else 0
    overall_bar = bar(int(overall_pct), 100, 10)
    rating = "🏆" if overall_pct >= 90 else ("🥇" if overall_pct >= 70 else ("🥈" if overall_pct >= 50 else "💪"))
    lines.append(f"\n<b>Загалом:</b> <code>{overall_bar}</code> {overall_pct:.0f}% {rating}")

    return "\n".join(lines)

def block_meds():
    """Блок ліків — використовує meds.py."""
    try:
        import sys, os as _os
        sys.path.insert(0, _DIR)
        from meds import get_meds_report_full
        return get_meds_report_full("week")
    except Exception as e:
        # fallback
        db    = get_meds_data()
        dates = get_week_dates()
        taken = sum(1 for d in dates if db.get(d) is True)
        missed = 7 - taken
        pct = taken / 7 * 100
        b = bar(taken, 7, 7)
        medal = "✅" if taken == 7 else ("⚠️" if taken >= 5 else "❌")
        lines = ["💊 <b>ЛІКИ (Armolopid Plus)</b>", f"<code>{b}</code> {taken}/7 {medal}"]
        if missed > 0:
            missed_dates = [d[5:] for d in dates if not db.get(d)]
            lines.append(f"Пропущено: {', '.join(missed_dates)}")
        return "\n".join(lines)

def block_weight():
    """Блок ваги."""
    db    = get_weight_data()
    dates = get_week_dates()

    week_vals = [(d, db.get(d)) for d in dates]
    present   = [(d, v) for d, v in week_vals if v is not None]

    lines = ["⚖️ <b>ВАГА</b>"]

    if not present:
        lines.append("Даних немає")
        return "\n".join(lines)

    avg = sum(v for _, v in present) / len(present)

    for d, v in week_vals:
        d_short = d[5:]
        if v:
            lines.append(f"  {d_short}  <b>{v} кг</b>")
        else:
            lines.append(f"  {d_short}  —")

    lines.append(f"\nСередня: <b>{avg:.1f} кг</b>")

    # Тренд
    if len(present) >= 2:
        first_v = present[0][1]
        last_v  = present[-1][1]
        diff = last_v - first_v
        if diff < -0.2:
            lines.append(f"📉 Тренд: -{abs(diff):.1f} кг за тиждень")
        elif diff > 0.2:
            lines.append(f"📈 Тренд: +{diff:.1f} кг за тиждень")
        else:
            lines.append("➡️ Вага стабільна")

    # Порівняння з місяцем тому
    now = now_local()
    month_ago_key = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    month_ago = db.get(month_ago_key)
    current = present[-1][1] if present else None
    if current and month_ago:
        diff_month = current - month_ago
        sign = "+" if diff_month > 0 else ""
        lines.append(f"📅 За місяць: {sign}{diff_month:.1f} кг")

    return "\n".join(lines)

def block_running():
    """Блок бігу."""
    runs = get_runs_from_health()
    db   = get_habits_data()
    dates = get_week_dates()

    lines = ["🏃 <b>БІГ</b>"]

    if runs:
        total_km  = sum(r["km"] for r in runs)
        total_min = sum(r["min"] for r in runs)
        for r in runs:
            pace = r["min"] / r["km"] if r["km"] > 0 else 0
            pace_str = f"{int(pace)}:{int((pace%1)*60):02d}/км"
            lines.append(f"  {r['date'][5:]}  {r['km']:.1f}км  {int(r['min'])}хв  {pace_str}")

        lines.append(f"\nВсього: <b>{total_km:.1f} км</b>  за  <b>{int(total_min)} хв</b>")
        lines.append(f"Пробіжок: <b>{len(runs)}</b> цього тижня")
    else:
        # Перевіряємо дані звичок (ручне відмічання)
        run_days = sum(1 for d in dates if db.get(d, {}).get("run") is True)
        if run_days > 0:
            lines.append(f"Відмічено: <b>{run_days}/7</b> днів")
            b = bar(run_days, 7, 7)
            lines.append(f"<code>{b}</code>")
        else:
            lines.append("Цього тижня без пробіжок 🦥")
            lines.append("Наступного тижня — обов'язково! 💪")

    return "\n".join(lines)

def block_recommendations():
    """Блок рекомендацій на наступний тиждень."""
    db    = get_habits_data()
    dates = get_week_dates()

    try:
        from sleep import get_weekly_sleep_stats
        sleep_stats = get_weekly_sleep_stats(7)
    except:
        sleep_stats = None

    meds_db = get_meds_data()
    weight_db = get_weight_data()

    recs = []

    # Сон
    if sleep_stats:
        avg_sleep = sleep_stats["avg_min"]
        if avg_sleep < 360:
            recs.append("😴 Сон менше 6г — це серйозно. Спробуй лягати о 22:00")
        elif avg_sleep < 420:
            recs.append("😴 Сон < 7г. Додай 30 хв — ляж трохи раніше")
        elif avg_sleep >= 480:
            recs.append("😊 Відмінний сон цього тижня! Тримай режим")

    # Біг
    run_days = sum(1 for d in dates if db.get(d, {}).get("run") is True)
    runs = get_runs_from_health()
    if len(runs) == 0 and run_days == 0:
        recs.append("🏃 Тиждень без бігу. Хоч одна коротка пробіжка наступного тижня!")
    elif len(runs) >= 3 or run_days >= 3:
        recs.append("🏃 Гарна активність! Ціль — підтримай темп наступного тижня")

    # Ліки
    meds_taken = sum(1 for d in dates if meds_db.get(d) is True)
    if meds_taken < 7:
        missed = 7 - meds_taken
        recs.append(f"💊 Пропущено {missed} дні ліків — постав щоденне нагадування")
    else:
        recs.append("💊 Ліки — ідеально! Так тримати")

    # Вода
    water_days = sum(1 for d in dates if db.get(d, {}).get("water") is True)
    if water_days < 4:
        recs.append("💧 Менше половини тижня з водою. Постав пляшку перед очима")

    # Душ
    shower_days = sum(1 for d in dates if db.get(d, {}).get("shower") is True)
    if shower_days == 7:
        recs.append("🚿 Холодний душ 7/7 — залізна дисципліна!")
    elif shower_days < 3:
        recs.append("🚿 Холодний душ < 3 рази. Спробуй хоч 30 секунд холодної наприкінці")

    # Вага — тренд
    week_vals = [(d, weight_db.get(d)) for d in dates]
    present   = [(d, v) for d, v in week_vals if v is not None]
    if len(present) >= 2:
        diff = present[-1][1] - present[0][1]
        if diff > 1.0:
            recs.append("⚖️ Вага зросла на > 1 кг — перевір харчування")
        elif diff < -1.0:
            recs.append("⚖️ Вага впала на > 1 кг — молодець! Продовжуй")

    if not recs:
        recs.append("✅ Відмінний тиждень — всі показники в нормі!")

    lines = ["💡 <b>РЕКОМЕНДАЦІЇ</b>"]
    lines.extend(f"• {r}" for r in recs)
    return "\n".join(lines)

def block_health_score():
    """Загальний Health Score тижня."""
    db    = get_habits_data()
    dates = get_week_dates()
    meds_db  = get_meds_data()
    weight_db = get_weight_data()

    try:
        from sleep import get_weekly_sleep_stats
        sleep_stats = get_weekly_sleep_stats(7)
    except:
        sleep_stats = None

    score = 0
    max_score = 0

    # Сон (25 балів)
    max_score += 25
    if sleep_stats:
        avg = sleep_stats["avg_min"]
        if avg >= 480: score += 25
        elif avg >= 420: score += 20
        elif avg >= 360: score += 12
        else: score += 5

    # Звички (40 балів: по 8 за кожну)
    HABIT_IDS = ["shower", "run", "water", "tea", "sauna"]
    for hid in HABIT_IDS:
        max_score += 8
        done = sum(1 for d in dates if db.get(d, {}).get(hid) is True)
        score += int(done / 7 * 8)

    # Ліки (20 балів)
    max_score += 20
    meds_taken = sum(1 for d in dates if meds_db.get(d) is True)
    score += int(meds_taken / 7 * 20)

    # Вага — відстежував (15 балів)
    max_score += 15
    weight_days = sum(1 for d in dates if weight_db.get(d) is not None)
    score += int(weight_days / 7 * 15)

    pct = score / max_score * 100 if max_score > 0 else 0
    b   = bar(int(pct), 100, 10)

    if pct >= 90: stars = "⭐️⭐️⭐️⭐️⭐️"
    elif pct >= 75: stars = "⭐️⭐️⭐️⭐️"
    elif pct >= 60: stars = "⭐️⭐️⭐️"
    elif pct >= 40: stars = "⭐️⭐️"
    else: stars = "⭐️"

    return (
        f"🏅 <b>HEALTH SCORE</b>\n"
        f"<code>{b}</code>  <b>{pct:.0f}%</b>  {stars}"
    )

# ─── ГОЛОВНА ФУНКЦІЯ ──────────────────────────────────────────────────────────

def _send_photo(photo_bytes: bytes, caption: str = "") -> bool:
    """Відправляє PNG bytes як фото в Telegram (multipart)."""
    try:
        import urllib.request, io
        boundary = b"----TGBoundary"
        def _part(name, value, fname=None, ctype=None):
            cd = f'Content-Disposition: form-data; name="{name}"'
            if fname:
                cd += f'; filename="{fname}"'
            ct = f"\r\nContent-Type: {ctype}" if ctype else ""
            return (f"--{boundary.decode()}\r\n{cd}{ct}\r\n\r\n").encode() + (value if isinstance(value, bytes) else value.encode()) + b"\r\n"
        body = (
            _part("chat_id",    str(TELEGRAM_CHAT)) +
            _part("caption",    caption[:1024]) +
            _part("parse_mode", "HTML") +
            _part("photo",      photo_bytes, fname="chart.png", ctype="image/png") +
            f"--{boundary.decode()}--\r\n".encode()
        )
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"}
        )
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status == 200
    except Exception as e:
        print(f"_send_photo error: {e}")
        return False


def send_weekly_report():
    """Надсилає повний недільний підсумок + графіки + AI аналіз."""
    SEP = "\n" + "─" * 22 + "\n"
    now = now_local()

    msg1 = (
        block_header() + "\n\n" +
        block_sleep()
    )

    msg2 = (
        block_habits() + SEP +
        block_meds()
    )

    msg3 = (
        block_weight() + SEP +
        block_running()
    )

    msg4 = (
        block_health_score() + SEP +
        block_recommendations()
    )

    for msg in [msg1, msg2, msg3, msg4]:
        send(msg)

    # ── Графіки тижня ─────────────────────────────────────────────────────────
    try:
        import sys as _sys_wr; _sys_wr.path.insert(0, _DIR)
        from charts import plot_weekly_dashboard as _pwd
        chart = _pwd(days=7)
        if chart:
            _send_photo(chart, f"📊 Тижневий дашборд — {now.strftime('%d.%m.%Y')}")
    except Exception as _e_wc:
        print(f"weekly chart error: {_e_wc}")

    # ── AI аналіз тижня (Gemini) ──────────────────────────────────────────────
    try:
        import os as _os_wr, json as _json_wr, urllib.request as _ur_wr
        gemini_key = _os_wr.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            # Збираємо компактний контекст
            w_block = block_weight()
            r_block = block_running()
            h_block = block_habits()
            hs_block = block_health_score()
            summary_ctx = f"{w_block}\n{r_block}\n{h_block}\n{hs_block}"[:1200]

            prompt = (
                f"Ось результати Олега за тиждень:\n{summary_ctx}\n\n"
                f"Ти особистий коуч і друг. Зроби живий аналіз (3-4 речення): "
                f"що вдалось добре, де є слабке місце, і дай одну конкретну ціль на наступний тиждень. "
                f"Тон: дружній, без пафосу, конкретний. Українською."
            )
            payload = _json_wr.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 220, "temperature": 0.8, "thinkingConfig": {"thinkingBudget": 0}}
            }).encode()
            from monitor import _gem_post
            ai_data = _gem_post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                payload, timeout=25, tag="weekly_report", max_retries=3
            )
            ai_text = ""
            if isinstance(ai_data, dict) and ai_data.get("candidates"):
                _parts_wr = ai_data["candidates"][0].get("content", {}).get("parts", [])
                if _parts_wr:
                    ai_text = _parts_wr[0].get("text", "").strip()
            if ai_text:
                send(f"🤖 <b>AI-аналіз тижня:</b>\n<i>{ai_text}</i>")
    except Exception as _e_ai:
        print(f"weekly AI error: {_e_ai}")

    print("Weekly summary report sent.")


# ─── ТЕСТ ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== TEST WEEKLY REPORT ===\n")
    print(block_header())
    print()
    print(block_sleep())
    print()
    print(block_habits())
    print()
    print(block_meds())
    print()
    print(block_weight())
    print()
    print(block_running())
    print()
    print(block_health_score())
    print()
    print(block_recommendations())
