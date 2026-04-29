#!/usr/bin/env python3
"""
Health звіти: тижневий і місячний аналіз здоров'я.
"""
import os, sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from storage import load_health, save_health


def now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)


def _bar(value, max_val, length=7, fill="🟩", empty="⬜️"):
    filled = round(value / max_val * length) if max_val else 0
    filled = max(0, min(length, filled))
    return fill * filled + empty * (length - filled)


def _trend(values):
    """Повертає тренд: ↗️ ↘️ →"""
    if len(values) < 2:
        return ""
    diff = values[-1] - values[0]
    if diff > 0.5:
        return "↗️"
    elif diff < -0.5:
        return "↘️"
    return "→"


def get_health_week_report():
    """Тижневий health звіт — середні показники + тренди за 7 днів."""
    health = load_health()
    if not health:
        return "⚠️ Health даних немає. Введи: /здоров'я [кроки] [сон] [ЧСС] [калорії] [score]"

    now = now_local()
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    days_data = [health.get(d, {}) for d in days]
    filled = [d for d in days_data if d]

    if not filled:
        return "⚠️ Немає health даних за останній тиждень."

    def avg(key, mult=1):
        vals = [d[key] * mult for d in filled if key in d]
        return round(sum(vals) / len(vals), 1) if vals else None

    def vals(key):
        return [d[key] for d in days_data if key in d]

    steps_avg   = avg("steps")
    sleep_avg   = avg("sleep_hours")
    hr_avg      = avg("heart_rate")
    cal_avg     = avg("calories")
    hrv_avg     = avg("hrv")
    score_avg   = avg("health_score")
    stress_avg  = avg("stress_max")

    days_count = len(filled)
    date_from  = days[0][5:]   # MM-DD
    date_to    = days[-1][5:]

    lines = [
        f"💚 <b>Health звіт — тиждень</b>",
        f"<i>{date_from} → {date_to} ({days_count}/7 днів)</i>\n",
    ]

    if steps_avg:
        tr = _trend(vals("steps"))
        bar = _bar(steps_avg, 12000)
        lines.append(f"👟 <b>Кроки</b>: {int(steps_avg):,} / день {tr}")
        lines.append(f"   <code>{bar}</code> (ціль 12к)")

    if sleep_avg:
        tr = _trend(vals("sleep_hours"))
        bar = _bar(sleep_avg, 9)
        lines.append(f"\n😴 <b>Сон</b>: {sleep_avg} год / ніч {tr}")
        lines.append(f"   <code>{bar}</code> (ціль 8г)")

    if hr_avg:
        lines.append(f"\n❤️ <b>ЧСС</b>: {int(hr_avg)} bpm")

    if hrv_avg:
        tr = _trend(vals("hrv"))
        lines.append(f"💓 <b>HRV</b>: {int(hrv_avg)} ms {tr}")

    if stress_avg:
        tr = _trend(vals("stress_max"))
        lines.append(f"😤 <b>Стрес</b> (макс): {int(stress_avg)} {tr}")

    if cal_avg:
        lines.append(f"🔥 <b>Калорії</b>: {int(cal_avg):,} / день")

    if score_avg:
        tr = _trend(vals("health_score"))
        bar = _bar(score_avg, 100)
        lines.append(f"\n💚 <b>Health Score</b>: {int(score_avg)}/100 {tr}")
        lines.append(f"   <code>{bar}</code>")
        if score_avg >= 80:
            lines.append("   🏆 Відмінний стан!")
        elif score_avg >= 65:
            lines.append("   👍 Добре")
        elif score_avg >= 50:
            lines.append("   👌 Задовільно")
        else:
            lines.append("   ⚠️ Зверни увагу на відпочинок")

    # Таблиця по днях
    lines.append("\n<b>По днях:</b>")
    for i, (d, dd) in enumerate(zip(days, days_data)):
        d_short = d[5:]
        if not dd:
            lines.append(f"  {d_short}  —")
            continue
        score = f" | 💚{dd['health_score']}" if dd.get("health_score") else ""
        steps = f"👟{dd['steps']//1000}к" if dd.get("steps") else ""
        sleep = f"😴{dd.get('sleep_hours','')}г" if dd.get("sleep_hours") else ""
        hr    = f"❤️{dd['heart_rate']}" if dd.get("heart_rate") else ""
        parts = [x for x in [steps, sleep, hr] if x]
        lines.append(f"  {d_short}  {' '.join(parts)}{score}")

    return "\n".join(lines)


def get_health_month_report():
    """Місячний health звіт — середні показники + тренди за поточний місяць."""
    health = load_health()
    if not health:
        return "⚠️ Health даних немає."

    now = now_local()
    month_days = now.day
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(month_days - 1, -1, -1)]
    days_data = [health.get(d, {}) for d in days]
    filled = [d for d in days_data if d]

    if not filled:
        return "⚠️ Немає health даних за цей місяць."

    def avg(key):
        vals = [d[key] for d in filled if key in d]
        return round(sum(vals) / len(vals), 1) if vals else None

    def vals(key):
        return [d[key] for d in days_data if key in d]

    month_name = now.strftime("%B %Y")
    days_count = len(filled)

    lines = [
        f"📅 <b>Health звіт — {month_name}</b>",
        f"<i>Даних за {days_count}/{month_days} дн.</i>\n",
    ]

    steps_avg  = avg("steps")
    sleep_avg  = avg("sleep_hours")
    hr_avg     = avg("heart_rate")
    hrv_avg    = avg("hrv")
    score_avg  = avg("health_score")
    cal_avg    = avg("calories")
    stress_avg = avg("stress_max")

    if steps_avg:
        tr = _trend(vals("steps"))
        lines.append(f"👟 <b>Кроки</b>: {int(steps_avg):,} / день  {tr}")
        total = sum(d["steps"] for d in filled if "steps" in d)
        lines.append(f"   Всього за місяць: <b>{total:,}</b>")

    if sleep_avg:
        tr = _trend(vals("sleep_hours"))
        lines.append(f"\n😴 <b>Сон</b>: {sleep_avg} год / ніч  {tr}")

    if hr_avg:
        lines.append(f"\n❤️ <b>ЧСС</b>: {int(hr_avg)} bpm")

    if hrv_avg:
        tr = _trend(vals("hrv"))
        lines.append(f"💓 <b>HRV</b>: {int(hrv_avg)} ms  {tr}")

    if stress_avg:
        tr = _trend(vals("stress_max"))
        lines.append(f"😤 <b>Стрес</b>: {int(stress_avg)} макс  {tr}")

    if cal_avg:
        lines.append(f"🔥 <b>Калорії</b>: {int(cal_avg):,} / день")

    if score_avg:
        tr = _trend(vals("health_score"))
        bar = _bar(score_avg, 100)
        lines.append(f"\n💚 <b>Health Score</b>: {int(score_avg)}/100  {tr}")
        lines.append(f"   <code>{bar}</code>")

        best_day  = max((d for d in days if health.get(d, {}).get("health_score")),
                        key=lambda d: health[d]["health_score"], default=None)
        worst_day = min((d for d in days if health.get(d, {}).get("health_score")),
                        key=lambda d: health[d]["health_score"], default=None)
        if best_day:
            lines.append(f"   🏆 Кращий: {best_day[5:]} ({health[best_day]['health_score']}/100)")
        if worst_day and worst_day != best_day:
            lines.append(f"   ⚠️ Найгірший: {worst_day[5:]} ({health[worst_day]['health_score']}/100)")

    # Тижні — середні по тижнях
    if len(filled) >= 7:
        lines.append("\n<b>Середнє по тижнях:</b>")
        week_size = 7
        for wk in range(0, min(4, (month_days + 6) // 7)):
            wk_days = days[wk * week_size: (wk + 1) * week_size]
            wk_data = [health.get(d, {}) for d in wk_days if health.get(d)]
            if not wk_data:
                continue
            sc = [d["health_score"] for d in wk_data if "health_score" in d]
            st = [d["steps"] for d in wk_data if "steps" in d]
            wk_score = f"💚{round(sum(sc)/len(sc))}" if sc else ""
            wk_steps = f"👟{round(sum(st)/len(st)/1000,1)}к" if st else ""
            lines.append(f"  Тиж {wk+1}: {wk_steps} {wk_score} ({len(wk_data)}/{week_size} дн.)")

    return "\n".join(lines)
