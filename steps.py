#!/usr/bin/env python3
"""
steps.py — обробка даних StepsApp + звіти/графіки/сповіщення.

Функції:
  parse_zip(zip_bytes)        — парсить ZIP від StepsApp, зберігає в GitHub
  get_steps_summary()         — підсумок за сьогодні
  get_weekly_report()         — тижневий звіт + графік
  get_monthly_report()        — місячний звіт + графік
  check_steps_notifications() — щоденні сповіщення (ранок, вечір, мотивація)
  detect_run_days(daily_data) — визначає дні коли бігав (висока активність)
"""

import os
import io
import json
import zipfile
import csv
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ─── НАЛАШТУВАННЯ ─────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO    = "NovosadovO/morning-report"

# Поріг для "день бігу" — >6км і >8000 кроків
RUN_DISTANCE_M = 6000
RUN_STEPS      = 8000

# ─── GITHUB STORAGE ──────────────────────────────────────────────────────────

def _gh_url(filename):
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{filename}"

def _gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "User-Agent": "morning-report-bot",
        "Content-Type": "application/json"
    }

def _gh_load(filename):
    import base64
    req = urllib.request.Request(_gh_url(filename), headers=_gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
            return json.loads(base64.b64decode(d["content"]).decode()), d["sha"]
    except Exception:
        return None, None

def _gh_save(filename, data, sha=None):
    import base64
    content = base64.b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
    body = {"message": f"steps: update {filename}", "content": content}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(
        _gh_url(filename),
        data=json.dumps(body).encode(),
        headers=_gh_headers(),
        method="PUT"
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"_gh_save {filename} error: {e}")
        return False

def load_steps_data():
    data, _ = _gh_load("steps_daily.json")
    return data or {}

def load_steps_data_merged():
    """Читає кроки тільки з qwatch_data.json.
    Повертає dict у форматі {date: {steps, calories, ...}} сумісний з steps_daily."""
    try:
        import sys as _sys_q, os as _os_q
        _sys_q.path.insert(0, _os_q.path.dirname(_os_q.path.abspath(__file__)))
        from storage import load as _sl
        qdata = _sl("qwatch_data.json", default={})
        result = {}
        for date, qrec in qdata.items():
            if not isinstance(qrec, dict):
                continue
            entry = {}
            if qrec.get("steps"):
                entry["steps"] = qrec["steps"]
            if qrec.get("calories"):
                entry["calories"] = qrec["calories"]
            if qrec.get("hr_avg"):
                entry["hr_avg"] = qrec["hr_avg"]
            if entry:
                result[date] = entry
        return result
    except Exception as _eq:
        print(f"[steps from qwatch] {_eq}")
        return {}

def save_steps_data(data):
    _, sha = _gh_load("steps_daily.json")
    return _gh_save("steps_daily.json", data, sha)

def load_steps_state():
    data, _ = _gh_load("steps_state.json")
    return data or {}

def save_steps_state(data):
    _, sha = _gh_load("steps_state.json")
    return _gh_save("steps_state.json", data, sha)

# ─── ПАРСИНГ ZIP ──────────────────────────────────────────────────────────────

def parse_zip(zip_bytes: bytes) -> dict:
    """
    Парсить ZIP від StepsApp.
    Повертає dict з daily, monthly, yearly, goals, streak даними.
    Зберігає daily в GitHub.
    """
    result = {"daily": {}, "monthly": {}, "yearly": {}, "goals": {}, "streak": {}}

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            with zf.open(name) as f:
                lines = f.read().decode("utf-8", errors="replace").splitlines()

            if not lines:
                continue

            # Пропускаємо перший рядок (заголовок) і другий (метадані типу "daily-activity-data;v1")
            rows = []
            for line in lines[2:]:
                if line.strip():
                    rows.append(line.strip().split(";"))

            if "daily" in name:
                for row in rows:
                    try:
                        date_str = row[0][:10]  # "2024-10-30"
                        result["daily"][date_str] = {
                            "steps":    int(float(row[1])),
                            "duration": int(float(row[2])),
                            "distance": int(float(row[3])),
                            "calories": int(float(row[4])),
                            "floors":   int(float(row[5])) if len(row) > 5 else 0
                        }
                    except Exception:
                        pass

            elif "monthly" in name:
                for row in rows:
                    try:
                        date_str = row[0][:7]  # "2024-10"
                        result["monthly"][date_str] = {
                            "steps": int(float(row[1])),
                            "distance": int(float(row[3])),
                            "calories": int(float(row[4]))
                        }
                    except Exception:
                        pass

            elif "yearly" in name:
                for row in rows:
                    try:
                        year = row[0][:4]
                        result["yearly"][year] = {
                            "steps": int(float(row[1])),
                            "distance": int(float(row[3])),
                            "calories": int(float(row[4]))
                        }
                    except Exception:
                        pass

            elif "goals" in name:
                for row in rows:
                    try:
                        result["goals"] = {
                            "steps_goal": int(float(row[1])),
                            "distance_goal": int(float(row[4])) if len(row) > 4 else 3000
                        }
                    except Exception:
                        pass

            elif "streak" in name:
                for row in rows:
                    try:
                        date_str = row[2][:10]
                        result["streak"][date_str] = row[0].strip().lower() == "true"
                    except Exception:
                        pass

    # Зберігаємо в GitHub
    if result["daily"]:
        existing = load_steps_data()
        existing.update(result["daily"])
        save_steps_data(existing)
        print(f"[steps] Saved {len(result['daily'])} daily records to GitHub")

    return result

# ─── УТИЛІТИ ─────────────────────────────────────────────────────────────────

def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)

def _fmt_dist(m):
    """Метри → км"""
    return f"{m/1000:.1f} км"

def _fmt_dur(s):
    """Секунди → год/хв"""
    h = s // 3600
    m = (s % 3600) // 60
    if h:
        return f"{h}г {m}хв"
    return f"{m}хв"

def detect_run_days(daily_data: dict) -> list:
    """
    Визначає дні коли Олег бігав.
    Критерій: дистанція > 6км АБО (кроки > 8000 і тривалість > 3600с).
    Повертає список дат (str "YYYY-MM-DD").
    """
    run_days = []
    for date, d in daily_data.items():
        dist = d.get("distance", 0)
        steps = d.get("steps", 0)
        dur = d.get("duration", 0)
        if dist >= RUN_DISTANCE_M or (steps >= RUN_STEPS and dur >= 3600):
            run_days.append(date)
    return sorted(run_days)

def _days_since_last_run(daily_data: dict) -> int:
    run_days = detect_run_days(daily_data)
    if not run_days:
        return 999
    today = _now_local().strftime("%Y-%m-%d")
    last = run_days[-1]
    try:
        diff = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
        return diff
    except Exception:
        return 999

# ─── ПІДСУМОК СЬОГОДНІ ───────────────────────────────────────────────────────

def get_steps_summary() -> str:
    daily = load_steps_data()
    today = _now_local().strftime("%Y-%m-%d")
    d = daily.get(today)
    if not d:
        return "📊 Даних за сьогодні ще немає.\nНадішли ZIP з StepsApp щоб оновити!"

    steps = d["steps"]
    dist  = d["distance"]
    cal   = d["calories"]
    dur   = d["duration"]
    goal  = 20000  # дефолт

    pct = min(100, int(steps / goal * 100))
    bar_filled = pct // 10
    bar = "█" * bar_filled + "░" * (10 - bar_filled)

    is_run = dist >= RUN_DISTANCE_M or (steps >= RUN_STEPS and dur >= 3600)
    run_line = "\n🏃 Схоже що сьогодні бігав! 💪" if is_run else ""

    days_since = _days_since_last_run(daily)
    if days_since == 0:
        streak_line = "\n✅ Сьогодні активний день!"
    elif days_since == 1:
        streak_line = "\n💡 Вчора бігав — як самопочуття?"
    elif days_since >= 3:
        streak_line = f"\n⚠️ {days_since} днів без пробіжки — час вийти!"
    else:
        streak_line = ""

    return (
        f"👟 <b>Кроки сьогодні</b>\n\n"
        f"{bar} {pct}%\n"
        f"🦶 {steps:,} / {goal:,} кроків\n"
        f"📏 {_fmt_dist(dist)}\n"
        f"🔥 {cal} ккал\n"
        f"⏱ {_fmt_dur(dur)}"
        f"{run_line}"
        f"{streak_line}"
    )

# ─── ТИЖНЕВИЙ ЗВІТ ───────────────────────────────────────────────────────────

def get_weekly_report() -> tuple:
    """Повертає (text, chart_bytes або None)"""
    daily = load_steps_data_merged()  # QWatch + StepsApp
    now = _now_local()
    today = now.strftime("%Y-%m-%d")

    # Останні 7 днів
    dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]

    steps_list = []
    dist_list  = []
    cal_total  = 0
    run_days   = []

    for d in dates:
        rec = daily.get(d, {})
        s = rec.get("steps", 0)
        dist = rec.get("distance", 0)
        steps_list.append(s)
        dist_list.append(dist)
        cal_total += rec.get("calories", 0)
        if dist >= RUN_DISTANCE_M or (s >= RUN_STEPS and rec.get("duration", 0) >= 3600):
            run_days.append(d)

    total_steps = sum(steps_list)
    total_dist  = sum(dist_list)
    avg_steps   = total_steps // 7
    days_active = sum(1 for s in steps_list if s >= 8000)

    # Дні тижня UA
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

    # Графік
    chart_bytes = _make_weekly_chart(dates, steps_list, dist_list, run_days)

    # Порівняння з попереднім тижнем
    prev_dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, 6, -1)]
    prev_total = sum(daily.get(d, {}).get("steps", 0) for d in prev_dates)
    diff = total_steps - prev_total
    diff_str = f"+{diff:,}" if diff >= 0 else f"{diff:,}"
    trend = "📈" if diff > 0 else "📉"

    text = (
        f"📊 <b>Тижневий звіт кроків</b>\n"
        f"<i>{dates[0]} — {dates[-1]}</i>\n\n"
        f"🦶 Всього: <b>{total_steps:,}</b> кроків\n"
        f"📏 Дистанція: <b>{_fmt_dist(total_dist)}</b>\n"
        f"🔥 Калорії: <b>{cal_total:,} ккал</b>\n"
        f"📅 Активних днів: <b>{days_active}/7</b>\n"
        f"🏃 Пробіжки: <b>{len(run_days)}</b> дні\n"
        f"⚡ Середнє/день: <b>{avg_steps:,}</b>\n\n"
        f"{trend} Порівняно з минулим тижнем: <b>{diff_str}</b>\n\n"
    )

    # Рекомендація
    if len(run_days) == 0:
        text += "💡 <i>Цього тижня без пробіжок — спробуй наступного!</i>"
    elif len(run_days) >= 4:
        text += "🔥 <i>Відмінний тиждень! Так тримати!</i>"
    elif avg_steps >= 15000:
        text += "💪 <i>Дуже активний тиждень — молодець!</i>"
    elif avg_steps < 8000:
        text += "⚠️ <i>Трохи мало руху — постарайся більше ходити/бігати!</i>"
    else:
        text += "✅ <i>Непогано! Є куди рости 💪</i>"

    return text, chart_bytes

# ─── МІСЯЧНИЙ ЗВІТ ───────────────────────────────────────────────────────────

def get_monthly_report() -> tuple:
    """Повертає (text, chart_bytes або None)"""
    daily = load_steps_data_merged()  # QWatch + StepsApp
    now = _now_local()

    # Поточний місяць
    year  = now.year
    month = now.month
    prefix = now.strftime("%Y-%m")

    month_data = {d: v for d, v in daily.items() if d.startswith(prefix)}

    if not month_data:
        return "📊 Даних за цей місяць немає.", None

    days_in_month = list(sorted(month_data.keys()))
    steps_list = [month_data[d]["steps"] for d in days_in_month]
    dist_list  = [month_data[d]["distance"] for d in days_in_month]

    total_steps = sum(steps_list)
    total_dist  = sum(d for d in dist_list)
    total_cal   = sum(month_data[d].get("calories", 0) for d in days_in_month)
    avg_steps   = total_steps // max(len(steps_list), 1)

    run_days = detect_run_days(month_data)
    active_days = sum(1 for s in steps_list if s >= 8000)
    best_day = max(days_in_month, key=lambda d: month_data[d]["steps"])
    best_steps = month_data[best_day]["steps"]

    # Місяці UA
    month_names = ["Січень","Лютий","Березень","Квітень","Травень","Червень",
                   "Липень","Серпень","Вересень","Жовтень","Листопад","Грудень"]

    chart_bytes = _make_monthly_chart(days_in_month, steps_list, dist_list, run_days)

    text = (
        f"📅 <b>Місячний звіт — {month_names[month-1]} {year}</b>\n\n"
        f"🦶 Всього кроків: <b>{total_steps:,}</b>\n"
        f"📏 Дистанція: <b>{_fmt_dist(total_dist)}</b>\n"
        f"🔥 Калорії: <b>{total_cal:,} ккал</b>\n"
        f"📅 Активних днів: <b>{active_days}/{len(days_in_month)}</b>\n"
        f"🏃 Пробіжки: <b>{len(run_days)} дні</b>\n"
        f"⚡ Середнє/день: <b>{avg_steps:,}</b>\n"
        f"🏆 Кращий день: <b>{best_day} — {best_steps:,} кроків</b>\n\n"
    )

    if len(run_days) >= 12:
        text += "🔥 <i>Неймовірний місяць! Більше 12 пробіжок!</i>"
    elif len(run_days) >= 8:
        text += "💪 <i>Відмінний місяць — стабільний біг!</i>"
    elif active_days >= 20:
        text += "✅ <i>Дуже активний місяць — так тримати!</i>"
    else:
        text += "💡 <i>Є куди рости — більше руху наступного місяця!</i>"

    return text, chart_bytes

# ─── ГРАФІКИ ─────────────────────────────────────────────────────────────────

def _make_weekly_chart(dates, steps_list, dist_list, run_days) -> bytes | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), facecolor="#1a1a2e")
        fig.suptitle("Тижнева активність", color="white", fontsize=14, fontweight="bold", y=0.98)

        day_labels = []
        for d in dates:
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
                day_labels.append(["Пн","Вт","Ср","Чт","Пт","Сб","Нд"][dt.weekday()])
            except Exception:
                day_labels.append(d[-5:])

        x = np.arange(len(dates))

        # Кроки
        ax1.set_facecolor("#16213e")
        colors = []
        for i, d in enumerate(dates):
            if d in run_days:
                colors.append("#ff6b6b")
            elif steps_list[i] >= 15000:
                colors.append("#4ecdc4")
            elif steps_list[i] >= 8000:
                colors.append("#45b7d1")
            else:
                colors.append("#404060")

        bars = ax1.bar(x, steps_list, color=colors, alpha=0.9, width=0.6)
        ax1.axhline(y=20000, color="#ffd700", linestyle="--", alpha=0.7, linewidth=1.5, label="Ціль 20k")
        ax1.set_ylabel("Кроки", color="white")
        ax1.set_xticks(x)
        ax1.set_xticklabels(day_labels, color="white")
        ax1.tick_params(colors="white")
        ax1.spines[:].set_color("#333355")
        ax1.set_title("Кроки по днях", color="white", fontsize=11)

        for bar, val in zip(bars, steps_list):
            if val > 0:
                ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 200,
                         f"{val//1000:.0f}k", ha="center", va="bottom",
                         color="white", fontsize=8)

        run_patch = mpatches.Patch(color="#ff6b6b", label="День бігу")
        ax1.legend(handles=[run_patch], loc="upper right",
                   facecolor="#16213e", labelcolor="white", fontsize=9)

        # Дистанція
        ax2.set_facecolor("#16213e")
        dist_km = [d/1000 for d in dist_list]
        ax2.plot(x, dist_km, color="#4ecdc4", linewidth=2.5, marker="o",
                 markersize=6, markerfacecolor="white")
        ax2.fill_between(x, dist_km, alpha=0.2, color="#4ecdc4")
        ax2.set_ylabel("Км", color="white")
        ax2.set_xticks(x)
        ax2.set_xticklabels(day_labels, color="white")
        ax2.tick_params(colors="white")
        ax2.spines[:].set_color("#333355")
        ax2.set_title("Дистанція (км)", color="white", fontsize=11)

        for xi, val in zip(x, dist_km):
            if val > 0:
                ax2.annotate(f"{val:.1f}", (xi, val), textcoords="offset points",
                             xytext=(0, 8), ha="center", color="white", fontsize=8)

        plt.tight_layout(rect=[0, 0, 1, 0.96])

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor="#1a1a2e")
        plt.close(fig)
        return buf.getvalue()

    except Exception as e:
        print(f"weekly chart error: {e}")
        return None


def _make_monthly_chart(days, steps_list, dist_list, run_days) -> bytes | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots(figsize=(12, 5), facecolor="#1a1a2e")
        ax.set_facecolor("#16213e")

        x = np.arange(len(days))
        day_nums = [d[-2:] for d in days]

        colors = []
        for i, d in enumerate(days):
            if d in run_days:
                colors.append("#ff6b6b")
            elif steps_list[i] >= 15000:
                colors.append("#4ecdc4")
            elif steps_list[i] >= 8000:
                colors.append("#45b7d1")
            else:
                colors.append("#404060")

        ax.bar(x, steps_list, color=colors, alpha=0.85, width=0.7)
        ax.axhline(y=20000, color="#ffd700", linestyle="--", alpha=0.6, linewidth=1.5)

        ax.set_xticks(x[::3])
        ax.set_xticklabels(day_nums[::3], color="white", fontsize=8)
        ax.tick_params(colors="white")
        ax.spines[:].set_color("#333355")
        ax.set_ylabel("Кроки", color="white")
        ax.set_title("Місячна активність — кроки по днях", color="white",
                     fontsize=13, fontweight="bold")

        # Середня лінія
        avg = sum(steps_list) // max(len(steps_list), 1)
        ax.axhline(y=avg, color="#a0a0ff", linestyle=":", alpha=0.8, linewidth=1.5,
                   label=f"Середнє: {avg:,}")
        ax.legend(facecolor="#16213e", labelcolor="white", fontsize=9)

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor="#1a1a2e")
        plt.close(fig)
        return buf.getvalue()

    except Exception as e:
        print(f"monthly chart error: {e}")
        return None


def _make_run_history_chart(run_days_data: dict) -> bytes | None:
    """Графік пробіжок — останні 30 днів."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        now = _now_local()
        last30 = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(29, -1, -1)]

        dists = [run_days_data.get(d, {}).get("distance", 0) / 1000 for d in last30]
        x = np.arange(30)
        labels = [(now - timedelta(days=i)).strftime("%d") for i in range(29, -1, -1)]

        fig, ax = plt.subplots(figsize=(12, 4), facecolor="#1a1a2e")
        ax.set_facecolor("#16213e")

        colors = ["#ff6b6b" if d > 0 else "#333355" for d in dists]
        ax.bar(x, dists, color=colors, alpha=0.9, width=0.7)

        ax.set_xticks(x[::3])
        ax.set_xticklabels(labels[::3], color="white", fontsize=8)
        ax.tick_params(colors="white")
        ax.spines[:].set_color("#333355")
        ax.set_ylabel("Км", color="white")
        ax.set_title("Пробіжки — останні 30 днів", color="white",
                     fontsize=13, fontweight="bold")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                    facecolor="#1a1a2e")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        print(f"run history chart error: {e}")
        return None

# ─── СПОВІЩЕННЯ ──────────────────────────────────────────────────────────────

def _tg_send_text(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"tg_send error: {e}")

def _tg_send_photo(photo_bytes: bytes, caption: str = ""):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT or not photo_bytes:
        return
    boundary = "----FormBoundary7Ma4YWxkTrZu0gW"

    def field(name, value):
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    body = (
        field("chat_id", TELEGRAM_CHAT) +
        field("parse_mode", "HTML") +
        field("caption", caption) +
        f"--{boundary}\r\n".encode() +
        f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'.encode() +
        f"Content-Type: image/png\r\n\r\n".encode() +
        photo_bytes +
        f"\r\n--{boundary}--\r\n".encode()
    )

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print(f"tg_send_photo error: {e}")


def check_steps_notifications():
    """
    Щоденні сповіщення про кроки — перевірка кожну хвилину.
    - Вечірній підсумок о 21:00 — скільки кроків за день
    - Мотивація о 10:00 якщо вихідний і мало кроків
    - Після пробіжки — аналіз якщо сьогодні є дані про біг
    """
    now = _now_local()
    h, m = now.hour, now.minute
    today = now.strftime("%Y-%m-%d")

    state = load_steps_state()

    daily = load_steps_data()
    today_data = daily.get(today, {})

    if not today_data:
        return  # немає даних — мовчимо

    # ── Вечірній підсумок о 21:00 ─────────────────────────────────────────────
    if h == 21 and 0 <= m < 5 and state.get("summary_date") != today:
        text = get_steps_summary()
        _tg_send_text(text)
        state["summary_date"] = today
        save_steps_state(state)

    # ── Виявлення пробіжки і відправка аналізу ────────────────────────────────
    dist = today_data.get("distance", 0)
    steps = today_data.get("steps", 0)
    dur = today_data.get("duration", 0)
    is_run = dist >= RUN_DISTANCE_M or (steps >= RUN_STEPS and dur >= 3600)

    if is_run and state.get("run_report_date") != today and h >= 12:
        cal = today_data.get("calories", 0)
        pace_min_km = (dur / 60) / (dist / 1000) if dist > 0 else 0
        pace_str = f"{int(pace_min_km)}:{int((pace_min_km % 1) * 60):02d} хв/км" if pace_min_km > 0 else "—"

        days_since = _days_since_last_run({k: v for k, v in daily.items() if k < today})
        if days_since == 0:
            rest_line = "Відпочинок вчора — відмінний старт!"
        elif days_since <= 2:
            rest_line = f"Перерва {days_since} дні — правильний режим!"
        else:
            rest_line = f"Перерва {days_since} днів — але ти повернувся! 💪"

        text = (
            f"🏃 <b>Пробіжка сьогодні!</b>\n\n"
            f"📏 Дистанція: <b>{_fmt_dist(dist)}</b>\n"
            f"⏱ Час: <b>{_fmt_dur(dur)}</b>\n"
            f"🦶 Кроки: <b>{steps:,}</b>\n"
            f"🔥 Калорії: <b>{cal} ккал</b>\n"
            f"⚡ Темп: <b>{pace_str}</b>\n\n"
            f"<i>{rest_line}</i>\n\n"
        )

        # Рекомендація
        if dist >= 10000:
            text += "🏆 <i>10+ км — серйозне тренування! Відновлення завтра обов'язкове.</i>"
        elif dist >= 7000:
            text += "💪 <i>Гарна пробіжка! Не забудь потягнутись.</i>"
        else:
            text += "✅ <i>Хороший старт! Поступово збільшуй дистанцію.</i>"

        _tg_send_text(text)
        state["run_report_date"] = today
        save_steps_state(state)


def send_weekly_report():
    """Тижневий звіт з графіком — відправляє в Telegram."""
    text, chart = get_weekly_report()
    if chart:
        _tg_send_photo(chart, text)
    else:
        _tg_send_text(text)


def send_monthly_report():
    """Місячний звіт з графіком — відправляє в Telegram."""
    text, chart = get_monthly_report()
    if chart:
        _tg_send_photo(chart, text)
    else:
        _tg_send_text(text)


def send_run_history():
    """Графік пробіжок за 30 днів."""
    daily = load_steps_data()
    chart = _make_run_history_chart(daily)
    run_days = detect_run_days(daily)

    now = _now_local()
    last30_start = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    recent_runs = [d for d in run_days if d >= last30_start]

    text = (
        f"🏃 <b>Пробіжки — останні 30 днів</b>\n\n"
        f"📅 Всього пробіжок: <b>{len(recent_runs)}</b>\n"
    )
    if recent_runs:
        total_dist = sum(daily.get(d, {}).get("distance", 0) for d in recent_runs)
        text += f"📏 Загальна дистанція: <b>{_fmt_dist(total_dist)}</b>\n"
        text += f"🗓 Остання: <b>{recent_runs[-1]}</b>\n"

    days_since = _days_since_last_run(daily)
    if days_since == 0:
        text += "\n✅ Сьогодні бігав!"
    elif days_since == 1:
        text += "\n💡 Вчора бігав."
    elif days_since >= 3:
        text += f"\n⚠️ {days_since} днів без пробіжки!"

    if chart:
        _tg_send_photo(chart, text)
    else:
        _tg_send_text(text)


if __name__ == "__main__":
    # Тест
    print("Testing steps.py...")
    daily = load_steps_data()
    print(f"Loaded {len(daily)} daily records")
    run_days = detect_run_days(daily)
    print(f"Detected {len(run_days)} run days")
    print(get_steps_summary())
