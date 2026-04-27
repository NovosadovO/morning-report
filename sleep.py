#!/usr/bin/env python3
"""
Трекер сну — парсить Apple Health XML (Sleep Cycle дані).
Надає:
  - get_last_night_sleep()  → рядок для ранкового звіту
  - get_weekly_sleep_stats() → дані для тижневого підсумку
  - parse_sleep_records()   → всі записи [{date, total_min, deep_min, rem_min, awake_min}]
"""

import re
from datetime import datetime, timezone, timedelta

HEALTH_XML = "/tmp/health_export/apple_health_export/export.xml"

# Типи сну
ASLEEP_TYPES = {
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepREM",
    "HKCategoryValueSleepAnalysisAsleepDeep",
}
AWAKE_TYPE = "HKCategoryValueSleepAnalysisAwake"
INBED_TYPE = "HKCategoryValueSleepAnalysisInBed"

SLEEP_PATTERN = re.compile(
    r'type="HKCategoryTypeIdentifierSleepAnalysis"[^>]*'
    r'sourceName="Sleep Cycle"[^>]*'
    r'startDate="([^"]+)"[^>]*'
    r'endDate="([^"]+)"[^>]*'
    r'value="([^"]+)"'
)


def _parse_dt(s):
    """Парсить '2026-04-27 01:23:45 +0200' → datetime UTC."""
    s = s.strip()
    # Замінюємо пробіл перед timezone на +
    s = re.sub(r' ([+-]\d{4})$', r'\1', s)
    s = s.replace(' ', 'T', 1)
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def parse_sleep_records():
    """
    Парсить XML і повертає список записів:
    [{
        'date': '2026-04-27',   # дата засипання (local)
        'total_min': 420,       # загальний час у ліжку (хв)
        'asleep_min': 380,      # реально спав (Core+REM+Deep)
        'deep_min': 45,
        'rem_min': 90,
        'core_min': 245,
        'awake_min': 40,
        'bed_start': datetime,
        'bed_end': datetime,
    }]
    """
    try:
        with open(HEALTH_XML, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"sleep.py: cannot read XML: {e}")
        return []

    # Групуємо записи по ночах
    # Вважаємо "нова ніч" якщо > 3г перерва або дата змінилась після 12:00
    sessions = {}  # date_str → list of (start, end, value)

    for m in SLEEP_PATTERN.finditer(content):
        start_s, end_s, value = m.groups()
        try:
            start = _parse_dt(start_s)
            end   = _parse_dt(end_s)
        except Exception:
            continue

        # Визначаємо дату "ночі": якщо засинаємо після 12:00 — це ніч поточного дня
        # якщо до 12:00 — це продовження ночі попереднього дня
        local_start = start + timedelta(hours=2)
        if local_start.hour < 12:
            night_date = (local_start - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            night_date = local_start.strftime("%Y-%m-%d")

        if night_date not in sessions:
            sessions[night_date] = []
        sessions[night_date].append((start, end, value))

    records = []
    for date_str, segs in sorted(sessions.items()):
        if not segs:
            continue

        asleep_min = 0
        deep_min   = 0
        rem_min    = 0
        core_min   = 0
        awake_min  = 0

        bed_start = min(s for s, e, v in segs)
        bed_end   = max(e for s, e, v in segs)
        total_min = int((bed_end - bed_start).total_seconds() / 60)

        for start, end, value in segs:
            dur = int((end - start).total_seconds() / 60)
            if value == "HKCategoryValueSleepAnalysisAsleepCore":
                core_min   += dur
                asleep_min += dur
            elif value == "HKCategoryValueSleepAnalysisAsleepREM":
                rem_min    += dur
                asleep_min += dur
            elif value == "HKCategoryValueSleepAnalysisAsleepDeep":
                deep_min   += dur
                asleep_min += dur
            elif value == AWAKE_TYPE:
                awake_min  += dur

        records.append({
            "date":       date_str,
            "total_min":  total_min,
            "asleep_min": asleep_min,
            "deep_min":   deep_min,
            "rem_min":    rem_min,
            "core_min":   core_min,
            "awake_min":  awake_min,
            "bed_start":  bed_start,
            "bed_end":    bed_end,
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
    """
    records = parse_sleep_records()
    if not records:
        return None

    # Беремо найостанніший запис
    rec = records[-1]
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) + timedelta(hours=2) - timedelta(days=1)).strftime("%Y-%m-%d")

    # Беремо тільки якщо це вчорашня або сьогоднішня ніч
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
    Повертає статистику сну за останні N днів:
    {
        'records': [...],
        'avg_min': 420,
        'avg_deep': 45,
        'avg_rem': 90,
        'days_tracked': 6,
        'best': {...},
        'worst': {...},
    }
    """
    records = parse_sleep_records()
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
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
    """Повертає HTML-блок для тижневого звіту."""
    stats = get_weekly_sleep_stats(7)
    if not stats:
        return "😴 <b>Сон</b>\nДані відсутні"

    lines = ["😴 <b>СОН — тиждень</b>\n"]

    # Середнє
    avg = stats["avg_min"]
    quality = "😊" if avg >= 480 else ("🙂" if avg >= 420 else ("😐" if avg >= 360 else "😩"))
    lines.append(f"Середній сон: <b>{_fmt_dur(avg)}</b>  {quality}")

    if stats["avg_deep"] > 0:
        lines.append(f"Глибокий: <b>{_fmt_dur(stats['avg_deep'])}</b>  |  REM: <b>{_fmt_dur(stats['avg_rem'])}</b>")

    # Графік по днях
    lines.append("\n<b>По днях:</b>")
    for r in stats["records"]:
        h = r["asleep_min"] // 60
        bars = min(h, 10)
        bar = "▓" * bars + "░" * (10 - bars)
        emoji = "😊" if r["asleep_min"] >= 480 else ("🙂" if r["asleep_min"] >= 420 else ("😐" if r["asleep_min"] >= 360 else "😩"))
        date_short = r["date"][5:]  # MM-DD
        lines.append(f"<code>{date_short} {bar}</code> {_fmt_dur(r['asleep_min'])} {emoji}")

    # Найкращий/найгірший
    lines.append(f"\n🏆 Найкраще: {_fmt_dur(stats['best']['asleep_min'])} ({stats['best']['date'][5:]})")
    lines.append(f"😩 Найгірше: {_fmt_dur(stats['worst']['asleep_min'])} ({stats['worst']['date'][5:]})")

    return "\n".join(lines)


if __name__ == "__main__":
    # Тест
    print(get_last_night_sleep())
    print()
    print(format_sleep_week_block())
