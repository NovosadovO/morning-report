#!/usr/bin/env python3
"""
sleep.py — звіти по сну. ЄДИНЕ джерело — Garmin Connect (запит Олега, 09-10.10:
"дані... тільки із Garmin Connect, всі інші джерела видали").

Раніше парсив Apple Health XML (/tmp/health_export/apple_health_export/export.xml,
джерело "Sleep Cycle") — цей код повністю видалений. Тепер читає qwatch_data.json
через storage.load_health() — єдиний писач туди це garmin_sync.py (живий запит до
Garmin Connect, фоновий синк кожні 3 год або команда /гармін).

Надає (сигнатури незмінні — щоб bot.py / monitor.py / weekly_report.py / allctx.py
працювали без правок):
  - get_last_night_sleep()   → рядок для ранкового звіту, або None
  - get_weekly_sleep_stats() → статистика за N днів для тижневого звіту, або None
  - format_sleep_week_block() → готовий HTML-блок
  - parse_sleep_records()    → всі дні з Garmin-даними по сну
"""
from datetime import datetime, timezone, timedelta


def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)


def parse_sleep_records():
    """
    Garmin-дані (qwatch_data.json) → список записів, тільки дні де Garmin
    реально віддав сон:
    [{
        'date': '2026-10-10',
        'total_min':  420,   # = asleep_min (Garmin sleepTimeSeconds — вже без "awake")
        'asleep_min': 420,
        'deep_min':   45,
        'rem_min':    90,
        'core_min':   245,   # light sleep
        'awake_min':  10,
    }]
    """
    try:
        import storage
        health = storage.load_health() or {}
    except Exception as e:
        print(f"sleep.py: storage.load_health error: {e}")
        return []
    if not isinstance(health, dict):
        return []

    records = []
    for date_str, rec in sorted(health.items()):
        if not isinstance(rec, dict):
            continue
        total = rec.get("sleep_total_min")
        if not total:
            continue
        deep  = rec.get("sleep_deep_min") or 0
        rem   = rec.get("sleep_rem_min") or 0
        core  = rec.get("sleep_light_min") or 0
        awake = rec.get("sleep_awake_min") or 0
        records.append({
            "date":       date_str,
            "total_min":  int(total),
            "asleep_min": int(total),
            "deep_min":   int(deep),
            "rem_min":    int(rem),
            "core_min":   int(core),
            "awake_min":  int(awake),
        })
    return records


def _fmt_dur(minutes):
    """420 → '7г 00хв'"""
    h = minutes // 60
    m = minutes % 60
    return f"{h}г {m:02d}хв"


def get_last_night_sleep():
    """
    Повертає рядок для ранкового звіту:
    '😴 Сон: 7г 15хв  (глибокий: 52хв, REM: 1г 30хв)'
    None якщо Garmin ще не віддав дані по сну за сьогодні/вчора.
    """
    records = parse_sleep_records()
    if not records:
        return None

    rec = records[-1]
    today = _now_local().strftime("%Y-%m-%d")
    yesterday = (_now_local() - timedelta(days=1)).strftime("%Y-%m-%d")

    if rec["date"] not in (today, yesterday):
        return None

    asleep = rec["asleep_min"]
    deep   = rec["deep_min"]
    rem    = rec["rem_min"]

    quality = ""
    if asleep >= 480:
        quality = " 😊"
    elif asleep >= 420:
        quality = " 🙂"
    elif asleep >= 360:
        quality = " 😐"
    else:
        quality = " 😩"

    parts = []
    if deep > 0:
        parts.append(f"глиб: {_fmt_dur(deep)}")
    if rem > 0:
        parts.append(f"REM: {_fmt_dur(rem)}")

    detail = f"  ({', '.join(parts)})" if parts else ""
    return f"😴 Сон: <b>{_fmt_dur(asleep)}</b>{quality}{detail}"


def get_weekly_sleep_stats(days=7):
    """
    Статистика сну за останні N днів (тільки дні з Garmin-даними):
    {
        'records': [...],
        'avg_min': 420,
        'avg_deep': 45,
        'avg_rem': 90,
        'days_tracked': 6,
        'best': {...},
        'worst': {...},
    }
    None якщо за період взагалі немає Garmin-даних по сну.
    """
    records = parse_sleep_records()
    now_local = _now_local()
    cutoff = (now_local - timedelta(days=days)).strftime("%Y-%m-%d")

    week_recs = [r for r in records if r["date"] >= cutoff]
    if not week_recs:
        return None

    avg_min  = sum(r["asleep_min"] for r in week_recs) // len(week_recs)
    avg_deep = sum(r["deep_min"]   for r in week_recs) // len(week_recs)
    avg_rem  = sum(r["rem_min"]    for r in week_recs) // len(week_recs)
    best     = max(week_recs, key=lambda r: r["asleep_min"])
    worst    = min(week_recs, key=lambda r: r["asleep_min"])

    return {
        "records":      week_recs,
        "avg_min":      avg_min,
        "avg_deep":     avg_deep,
        "avg_rem":      avg_rem,
        "days_tracked": len(week_recs),
        "best":         best,
        "worst":        worst,
    }


def format_sleep_week_block():
    """Готовий HTML-блок для тижневого звіту."""
    stats = get_weekly_sleep_stats(7)
    if not stats:
        return "😴 <b>Сон</b>\nДані відсутні (Garmin ще не синхронізував сон за цей тиждень)"

    lines = ["😴 <b>СОН — тиждень</b>\n"]

    avg = stats["avg_min"]
    quality = "😊" if avg >= 480 else ("🙂" if avg >= 420 else ("😐" if avg >= 360 else "😩"))
    lines.append(f"Середній сон: <b>{_fmt_dur(avg)}</b>  {quality}")

    if stats["avg_deep"] > 0:
        lines.append(f"Глибокий: <b>{_fmt_dur(stats['avg_deep'])}</b>  |  REM: <b>{_fmt_dur(stats['avg_rem'])}</b>")

    lines.append("\n<b>По днях:</b>")
    for r in stats["records"]:
        h = r["asleep_min"] // 60
        bars = min(h, 10)
        bar = "▓" * bars + "░" * (10 - bars)
        emoji = "😊" if r["asleep_min"] >= 480 else ("🙂" if r["asleep_min"] >= 420 else ("😐" if r["asleep_min"] >= 360 else "😩"))
        date_short = r["date"][5:]  # MM-DD
        lines.append(f"<code>{date_short} {bar}</code> {_fmt_dur(r['asleep_min'])} {emoji}")

    lines.append(f"\n🏆 Найкраще: {_fmt_dur(stats['best']['asleep_min'])} ({stats['best']['date'][5:]})")
    lines.append(f"😩 Найгірше: {_fmt_dur(stats['worst']['asleep_min'])} ({stats['worst']['date'][5:]})")

    return "\n".join(lines)


if __name__ == "__main__":
    print(get_last_night_sleep())
    print()
    print(format_sleep_week_block())
