"""
running.py — РУЧНИЙ трекер бігу. Замінює Strava.

Strava з 2026 вимагає платну підписку API (403 Application/Inactive —
застосунок Олега деактивовано політикою Strava, не баг токена), тому
автоматичне підтягування пробіжок видалене. Замість цього Олег сам пише
в Telegram щось типу "Пробіжав 5.2 км за 28 хвилин, пульс 145" — і саме
Gemini (AI), а не крихкий regex, вирішує що там написано і зберігає це.
Regex лишається лише дешевою попередньою спробою й аварійним fallback-ом,
якщо AI недоступний — точно та ж філософія, що і в qwatch.py після фіксу
"AI — головне джерело правди для чисел" (26.09.2026, запит Олега: "тільки
аі слідкує за актуальністю даних").

Flow:
  1. Олег пише вільний текст про пробіжку в Telegram
  2. bot.py детектує (ключове слово біг/пробіжка/run + число+км) → parse_and_save(text)
  3. Gemini витягує структуровані поля, regex — фолбек
  4. Запис зберігається в data/running_data.json (ключ — ISO start_date_local,
     тому кілька пробіжок за один день не перезатирають одна одну)
  5. Усі звіти (день/тиждень/місяць/рік, графіки, AI-коучі) читають ЦЕЙ файл —
     жодного модуля більше НЕ ходить у Strava API.

Формат одного запису (навмисно ідентичний "сирій" активності Strava API —
щоб усю аналітику (get_runs/get_month_stats/compare_weeks/...) можна було
перенести з strava.py майже без змін):
  {
    "type": "Run" | "TrailRun" | "VirtualRun" | "Walk",
    "distance": метри (float),
    "moving_time": секунди (int),
    "start_date_local": "YYYY-MM-DDTHH:MM:SS",
    "average_heartrate": уд/хв або None,
    "total_elevation_gain": метри або None,
    "name": короткий підпис,
    "notes": вільний текст від Олега (як він описав пробіжку),
    "source": "manual" | "manual_ai",
    "saved_at": "YYYY-MM-DD HH:MM"
  }
"""

import os, re, json, urllib.request
from datetime import datetime, timezone, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8374312425:AAHqrQCEqrgtVdl5Te5WhWblM2ESCnqhpfk")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT",  "2100366814")
GEMINI_KEY     = os.environ.get("GEMINI_API_KEY", "")

_RUN_TYPES = ("Run", "TrailRun", "VirtualRun", "Walk")

# ─── STORAGE ──────────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import load as _l
        data = _l("running_data.json", default={})
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"running load error: {e}")
        return {}

def _save_key(key: str, record: dict):
    """Атомарний запис одного забігу під власним unique-ключем — уникає
    read-modify-write race, якщо два повідомлення про пробіжку прилетіли
    майже одночасно (той самий захист, що storage.update_key() дає
    draft_store.json)."""
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import update_key as _uk
        return _uk("running_data.json", key, record)
    except Exception as e:
        print(f"running save error: {e}")
        return False

# ─── AI-ПАРСЕР (головне джерело правди) ──────────────────────────────────────

def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)

def _regex_parse(text: str) -> dict:
    """Дешева попередня спроба / аварійний fallback, якщо Gemini впав.
    Ловить лише найпростіші, найпоширеніші формулювання."""
    r = {}
    low = text.lower()

    m = re.search(r'(\d+(?:[.,]\d+)?)\s*км', low)
    if m:
        r["distance_km"] = float(m.group(1).replace(",", "."))

    m = re.search(r'за\s*(\d+)\s*(?:хв|хвилин)', low)
    if not m:
        m = re.search(r'(\d+)\s*(?:хв|хвилин)', low)
    if m:
        r["duration_min"] = int(m.group(1))
    else:
        m = re.search(r'(\d+)\s*г(?:од)?[а-я]*\s*(\d+)\s*хв', low)
        if m:
            r["duration_min"] = int(m.group(1)) * 60 + int(m.group(2))

    m = re.search(r'пульс[^\d]*(\d+)', low)
    if not m:
        m = re.search(r'(\d+)\s*(?:уд|bpm)', low)
    if m:
        r["hr_avg"] = int(m.group(1))

    m = re.search(r'набір[^\d]*(\d+)\s*м', low)
    if m:
        r["elevation_m"] = int(m.group(1))

    if "трейл" in low:
        r["type"] = "TrailRun"
    elif "доріжц" in low or "тренажер" in low:
        r["type"] = "VirtualRun"
    elif "пішо" in low or "прогулянк" in low or "хож" in low:
        r["type"] = "Walk"
    else:
        r["type"] = "Run"

    if "сьогодні" in low or not any(k in low for k in ("вчора", "позавчора")):
        r["date"] = _now_local().strftime("%Y-%m-%d")
    if "вчора" in low:
        r["date"] = (_now_local() - timedelta(days=1)).strftime("%Y-%m-%d")

    return r

def _gemini_parse(text: str) -> dict:
    """
    AI — головне джерело правди. Читає вільний текст про пробіжку як людина
    (без залежності від точного порядку слів чи формулювання) і повертає
    структуровані поля. Викликається на КОЖЕН текст про біг, не тільки коли
    regex щось не зловив — саме так Олег попросив тримати актуальність даних
    під контролем AI, а не крихкого коду.
    """
    prompt = (
        "Витягни з тексту про пробіжку/тренування наступні поля і поверни "
        "ТІЛЬКИ JSON без markdown, без пояснень. Якщо значення немає в тексті "
        "— null. Числа без одиниць вимірювання.\n"
        "{\n"
        "  \"distance_km\": дистанція в км (число, може бути дробове) або null,\n"
        "  \"duration_min\": тривалість в ХВИЛИНАХ (переведи з годин/секунд якщо треба) або null,\n"
        "  \"hr_avg\": середній пульс уд/хв або null,\n"
        "  \"elevation_m\": набір висоти в метрах або null,\n"
        "  \"type\": одне з \"Run\", \"TrailRun\", \"VirtualRun\", \"Walk\" (біг доріжкою/треком що імітує зал → VirtualRun, трейл/гори → TrailRun, звичайна пробіжка → Run, ходьба → Walk) або null,\n"
        "  \"date\": \"YYYY-MM-DD\" — якщо сказано 'вчора'/'сьогодні'/конкретна дата, інакше null,\n"
        "  \"time\": \"HH:MM\" якщо вказано час тренування, інакше null,\n"
        "  \"notes\": короткий опис (як минула пробіжка, самопочуття, погода — 1 речення) або null\n"
        "}\n\n"
        f"Текст:\n{text[:3000]}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 400, "temperature": 0, "thinkingConfig": {"thinkingBudget": 0}}
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
    try:
        from monitor import _gem_post
        resp = _gem_post(url, body, timeout=30, tag="running_parse", max_retries=3)
        if isinstance(resp, dict) and resp.get("candidates"):
            parts = resp["candidates"][0].get("content", {}).get("parts", [])
            if parts and parts[0].get("text"):
                raw = parts[0]["text"].strip()
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    return json.loads(m.group(0))
    except Exception as e:
        print(f"running gemini parse error: {e}")
    return {}

def parse_run_text(text: str) -> dict:
    """AI (Gemini) вирішує — regex лише підстраховка, коли AI не відповів."""
    fallback = _regex_parse(text)
    ai = {}
    try:
        ai = _gemini_parse(text)
    except Exception as e:
        print(f"running: gemini failed, using regex-only: {e}")

    merged = dict(fallback)
    for k, v in ai.items():
        if v is not None:
            merged[k] = v
    return merged

def parse_and_save(text: str) -> dict:
    """
    Основна функція: парсить текст про пробіжку → зберігає як нову активність
    → повертає збережений запис (у "сирому" Strava-подібному форматі).
    """
    fields = parse_run_text(text)

    date_str = fields.get("date") or _now_local().strftime("%Y-%m-%d")
    time_str = fields.get("time") or _now_local().strftime("%H:%M:%S")
    if len(time_str) == 5:
        time_str += ":00"
    start_date_local = f"{date_str}T{time_str}"

    distance_km = fields.get("distance_km")
    duration_min = fields.get("duration_min")

    record = {
        "type": fields.get("type") or "Run",
        "distance": round(distance_km * 1000, 1) if distance_km else 0,
        "moving_time": int(duration_min * 60) if duration_min else 0,
        "start_date_local": start_date_local,
        "start_date": start_date_local,  # alias для strava_charts.py, який раніше читав це поле зі Strava API
        "average_heartrate": fields.get("hr_avg"),
        "total_elevation_gain": fields.get("elevation_m") or 0,
        "name": "Пробіжка" if (fields.get("type") or "Run") == "Run" else fields.get("type"),
        "notes": fields.get("notes"),
        "source": "manual_ai" if distance_km or duration_min else "manual",
        "saved_at": _now_local().strftime("%Y-%m-%d %H:%M"),
    }
    _save_key(start_date_local, record)
    print(f"running: saved manual run {start_date_local} ({distance_km} km)")
    return record

# ─── ЧИТАННЯ / АНАЛІТИКА (перенесено зі strava.py, тепер без HTTP/токенів) ───

def api_blocked() -> bool:
    """Сумісність зі старим викликами — ручні дані завжди 'доступні'."""
    return False

def app_inactive_reason() -> str:
    return ""

def get_activities(days: int = 30) -> list:
    """Повертає список 'сирих' активностей за останні N днів (Strava-подібний
    формат) з ручно введених даних. Без HTTP, без кешу — просто фільтр по даті."""
    db = _load()
    cutoff = _now_local() - timedelta(days=days)
    out = []
    for key, a in db.items():
        if not isinstance(a, dict):
            continue
        try:
            dt = datetime.fromisoformat(a.get("start_date_local", key).replace("Z", ""))
        except Exception:
            continue
        if dt.replace(tzinfo=None) >= cutoff.replace(tzinfo=None):
            out.append(a)
    # Найновіші спочатку — та сама конвенція, що й Strava API (деякі модулі,
    # напр. message_generator._get_live_strava(), беруть acts[0] як останню).
    out.sort(key=lambda a: a.get("start_date_local", ""), reverse=True)
    return out

def get_runs(days: int = 30) -> list:
    """Тільки пробіжки (Run/TrailRun/VirtualRun) за N днів з розрахованими полями."""
    acts = get_activities(days=days)
    result = []
    for a in acts:
        if a.get("type") not in ("Run", "VirtualRun", "TrailRun"):
            continue
        dist_km = round((a.get("distance") or 0) / 1000, 2)
        dur_sec = a.get("moving_time") or 0
        pace_sec = (dur_sec / dist_km) if dist_km > 0 else 0
        try:
            dt = datetime.fromisoformat(a["start_date_local"].replace("Z", ""))
        except Exception:
            dt = _now_local().replace(tzinfo=None)
        result.append({
            "id":         a.get("start_date_local"),
            "name":       a.get("name", "Пробіжка"),
            "date":       dt,
            "date_str":   dt.strftime("%d.%m"),
            "dist_km":    dist_km,
            "dur_sec":    dur_sec,
            "dur_min":    dur_sec // 60,
            "pace_sec":   round(pace_sec, 1),
            "pace_str":   f"{int(pace_sec//60)}:{int(pace_sec%60):02d}" if pace_sec > 0 else "—",
            "elev":       a.get("total_elevation_gain", 0),
            "hr":         a.get("average_heartrate"),
            "cadence":    a.get("average_cadence"),
            "watts":      a.get("average_watts"),
            "calories":   a.get("calories") or 0,
            "type":       a.get("type", "Run"),
            "notes":      a.get("notes"),
        })
    return sorted(result, key=lambda x: x["date"])

def _compute_when(start_date_local_iso: str) -> str:
    try:
        start_dt = datetime.fromisoformat(start_date_local_iso.replace("Z", ""))
        now = _now_local().replace(tzinfo=None)
        is_today = start_dt.date() == now.date()
        is_yesterday = start_dt.date() == (now - timedelta(days=1)).date()
        if is_today:
            return "сьогодні"
        elif is_yesterday:
            return "вчора"
        else:
            days_ago = (now.date() - start_dt.date()).days
            return f"{days_ago} дн. тому"
    except Exception:
        return "невідомо коли"

def get_last_activity():
    """Останній записаний забіг (Strava-подібний формат для звітів)."""
    acts = get_activities(days=3650)
    runs = [a for a in acts if a.get("type") in ("Run", "VirtualRun", "TrailRun")]
    if not runs:
        return None
    a = max(runs, key=lambda x: x.get("start_date_local", ""))

    distance_km = (a.get("distance") or 0) / 1000
    duration_sec = a.get("moving_time") or 0
    duration_min = duration_sec // 60
    if distance_km > 0:
        pace_sec = duration_sec / distance_km
        pace_str = f"{int(pace_sec//60)}:{int(pace_sec%60):02d} хв/км"
    else:
        pace_str = "—"

    start_date_local_iso = a["start_date_local"]
    try:
        start_dt = datetime.fromisoformat(start_date_local_iso.replace("Z", ""))
        date_str = start_dt.strftime("%d.%m %H:%M")
    except Exception:
        date_str = "?"

    return {
        "name": a.get("name", "Пробіжка"),
        "type": a.get("type", "Run"),
        "distance_km": round(distance_km, 2),
        "duration_min": duration_min,
        "pace": pace_str,
        "date": date_str,
        "when": _compute_when(start_date_local_iso),
        "start_date_local": start_date_local_iso,
        "elevation": a.get("total_elevation_gain", 0),
        "hr": a.get("average_heartrate"),
        "notes": a.get("notes"),
        "kudos": 0,
        "stale": False,  # ручні дані — завжди "актуальні" на момент запису
    }

def get_week_stats():
    """Статистика за поточний тиждень (Пн-Нд)."""
    now = _now_local().replace(tzinfo=None)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    runs = [r for r in get_runs(days=14) if r["date"] >= week_start]
    total_km = sum(r["dist_km"] for r in runs)
    total_min = sum(r["dur_min"] for r in runs)

    return {
        "runs": len(runs),
        "km": round(total_km, 1),
        "duration_min": total_min,
        "week_start": week_start.strftime("%d.%m"),
    }

def get_month_stats(year: int = None, month: int = None) -> dict:
    now = _now_local().replace(tzinfo=None)
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    month_start = datetime(year, month, 1)
    days_ago = max((now - month_start).days + 5, 90)
    runs = get_runs(days=days_ago)
    runs = [r for r in runs if r["date"].year == year and r["date"].month == month]

    if not runs:
        return {"runs": 0, "km": 0, "duration_min": 0, "year": year, "month": month, "runs_list": []}

    total_km   = round(sum(r["dist_km"] for r in runs), 1)
    total_min  = sum(r["dur_min"] for r in runs)
    avg_pace_sec = sum(r["pace_sec"] * r["dist_km"] for r in runs if r["pace_sec"] > 0) / max(total_km, 0.01)
    best_run   = max(runs, key=lambda x: x["dist_km"])
    fastest    = min((r for r in runs if r["pace_sec"] > 0), key=lambda x: x["pace_sec"], default=None)
    avg_hr     = None
    hr_runs    = [r["hr"] for r in runs if r["hr"]]
    if hr_runs:
        avg_hr = round(sum(hr_runs) / len(hr_runs), 0)

    return {
        "year":       year,
        "month":      month,
        "runs":       len(runs),
        "km":         total_km,
        "duration_min": total_min,
        "avg_pace_sec": round(avg_pace_sec, 1),
        "avg_pace_str": f"{int(avg_pace_sec//60)}:{int(avg_pace_sec%60):02d}" if avg_pace_sec > 0 else "—",
        "best_run":   best_run,
        "fastest":    fastest,
        "avg_hr":     avg_hr,
        "runs_list":  runs,
    }

def get_year_stats(year: int = None) -> dict:
    now = _now_local().replace(tzinfo=None)
    if year is None:
        year = now.year

    runs = get_runs(days=400)
    runs = [r for r in runs if r["date"].year == year]

    monthly = {}
    for r in runs:
        m = r["date"].month
        if m not in monthly:
            monthly[m] = {"runs": 0, "km": 0.0, "dur_min": 0}
        monthly[m]["runs"] += 1
        monthly[m]["km"]   += r["dist_km"]
        monthly[m]["dur_min"] += r["dur_min"]

    for m in monthly:
        monthly[m]["km"] = round(monthly[m]["km"], 1)

    total_km  = round(sum(r["dist_km"] for r in runs), 1)
    total_min = sum(r["dur_min"] for r in runs)

    return {
        "year":       year,
        "runs":       len(runs),
        "km":         total_km,
        "duration_min": total_min,
        "monthly":    monthly,
        "runs_list":  runs,
    }

def compare_weeks() -> dict:
    now = _now_local().replace(tzinfo=None)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    prev_start = week_start - timedelta(days=7)

    runs = get_runs(days=16)

    this_week = [r for r in runs if r["date"] >= week_start]
    prev_week = [r for r in runs if prev_start <= r["date"] < week_start]

    def week_summary(week_runs):
        if not week_runs:
            return {"runs": 0, "km": 0, "dur_min": 0, "avg_pace_sec": 0}
        km = round(sum(r["dist_km"] for r in week_runs), 1)
        dur = sum(r["dur_min"] for r in week_runs)
        total_km_for_pace = sum(r["dist_km"] for r in week_runs if r["pace_sec"] > 0)
        avg_pace = (
            sum(r["pace_sec"] * r["dist_km"] for r in week_runs if r["pace_sec"] > 0) / total_km_for_pace
            if total_km_for_pace > 0 else 0
        )
        return {"runs": len(week_runs), "km": km, "dur_min": dur, "avg_pace_sec": round(avg_pace, 1)}

    this = week_summary(this_week)
    prev = week_summary(prev_week)

    km_diff   = round(this["km"] - prev["km"], 1)
    pace_diff = round(this["avg_pace_sec"] - prev["avg_pace_sec"], 1)

    return {
        "this_week": this,
        "prev_week": prev,
        "km_diff":   km_diff,
        "pace_diff": pace_diff,
    }

def compare_months() -> dict:
    now = _now_local().replace(tzinfo=None)
    this_m = get_month_stats(now.year, now.month)
    if now.month == 1:
        prev_m = get_month_stats(now.year - 1, 12)
    else:
        prev_m = get_month_stats(now.year, now.month - 1)

    km_diff   = round(this_m["km"] - prev_m["km"], 1)
    runs_diff = this_m["runs"] - prev_m["runs"]

    return {
        "this_month": this_m,
        "prev_month": prev_m,
        "km_diff":    km_diff,
        "runs_diff":  runs_diff,
    }

# ─── ФОРМАТОВАНІ ЗВІТИ ────────────────────────────────────────────────────────

def _format_run_lines(last: dict) -> list:
    type_emoji = {"Run": "🏃", "TrailRun": "🏔", "VirtualRun": "💻", "Walk": "🚶"}.get(last["type"], "🏃")
    lines = []
    lines.append(f"  {type_emoji} {last['distance_km']} км · {last['duration_min']} хв · {last['pace']}")
    if last.get("elevation"):
        lines.append(f"  ⛰ Набір висоти: {last['elevation']:.0f} м")
    if last.get("hr"):
        lines.append(f"  ❤️ ЧСС: {last['hr']:.0f} уд/хв")
    if last.get("notes"):
        lines.append(f"  💬 {last['notes']}")
    return lines

def format_run_block():
    """Форматований блок для Telegram звіту (день/дашборд)."""
    last = get_last_activity()
    week = get_week_stats()

    lines = ["🏃 <b>БІГОВИЙ ТРЕКЕР</b>"]

    if last:
        is_today = last.get("when") == "сьогодні"
        if is_today:
            lines.append(f"\n<b>Актуальне тренування:</b>")
        else:
            date_short = last.get("date", "").split(" ")[0]
            lines.append(f"\n<b>Останнє тренування ({date_short}):</b>")
        lines.extend(_format_run_lines(last))
    else:
        lines.append("\n  Немає даних про тренування — напиши мені про пробіжку вільним текстом")

    if week:
        lines.append(f"\n<b>Цей тиждень</b> (з {week['week_start']}):")
        lines.append(f"  📅 Пробіжок: {week['runs']} · {week['km']} км · {week['duration_min']} хв")
        lines.append(f"  🎯 Ціль: {week['km']}/40 км")

    try:
        recent = get_runs(days=60)[-5:]
        if recent:
            lines.append("\n<b>Останні пробіжки:</b>")
            for r in reversed(recent):
                lines.append(f"  {r['date_str']}  <b>{r['dist_km']} км</b>  {r['pace_str']}/км  {r['dur_min']} хв")
    except Exception:
        pass

    return "\n".join(lines)

def format_run_analysis(short: bool = False) -> str:
    """Повний аналіз бігу для звіту або команди /біг. short=True — компактно."""
    try:
        last  = get_last_activity()
        week  = get_week_stats()
        cw    = compare_weeks()

        lines = ["🏃 <b>БІГ</b>"]

        if last and last.get("type") in ("Run", "VirtualRun", "TrailRun", None):
            type_emoji = {"Run": "🏃", "TrailRun": "🏔", "VirtualRun": "💻"}.get(last.get("type", "Run"), "🏃")
            lines.append(f"\n<b>Остання</b> ({last['when']}):")
            lines.append(f"  {type_emoji} {last['distance_km']} км · {last['duration_min']} хв · {last['pace']}")
            if last.get("elevation"):
                lines.append(f"  ⛰ Набір: {last['elevation']:.0f} м")
            if last.get("hr"):
                lines.append(f"  ❤️ ЧСС: {last['hr']:.0f} уд/хв")
            if last.get("notes"):
                lines.append(f"  💬 {last['notes']}")

        if week:
            prev_km = cw["prev_week"]["km"]
            km_diff = cw["km_diff"]
            diff_str = ""
            if prev_km > 0:
                sign = "+" if km_diff >= 0 else ""
                diff_str = f"  ({sign}{km_diff} vs минулий)"
            lines.append(f"\n<b>Тиждень:</b> {week['runs']} пробіжок · {week['km']} км{diff_str}")

            if not short:
                if cw["this_week"]["avg_pace_sec"] > 0 and cw["prev_week"]["avg_pace_sec"] > 0:
                    ps = cw["this_week"]["avg_pace_sec"]
                    pace_this = f"{int(ps//60)}:{int(ps%60):02d}"
                    pp = cw["prev_week"]["avg_pace_sec"]
                    pace_prev = f"{int(pp//60)}:{int(pp%60):02d}"
                    faster = "🔼" if cw["pace_diff"] > 0 else "🔽"
                    lines.append(f"  Темп: {pace_this} хв/км {faster} (було {pace_prev})")

        if short:
            return "\n".join(lines)

        try:
            cm = compare_months()
            ms = cm["this_month"]
            pm = cm["prev_month"]
            month_names = ["", "Січ", "Лют", "Бер", "Квіт", "Трав", "Черв",
                           "Лип", "Серп", "Вер", "Жовт", "Лист", "Груд"]
            mname = month_names[ms["month"]]
            lines.append(f"\n<b>{mname}:</b> {ms['runs']} пробіжок · {ms['km']} км")
            if ms.get("avg_pace_str") and ms["avg_pace_str"] != "—":
                lines.append(f"  Середній темп: {ms['avg_pace_str']} хв/км")
            if ms.get("avg_hr"):
                lines.append(f"  Середній ЧСС: {ms['avg_hr']:.0f} уд/хв")
            if ms.get("best_run"):
                br = ms["best_run"]
                lines.append(f"  🏆 Найдовша: {br['dist_km']} км ({br['date_str']})")
            if pm["km"] > 0:
                km_diff = cm["km_diff"]
                sign = "+" if km_diff >= 0 else ""
                lines.append(f"  vs {month_names[pm['month']]}: {sign}{km_diff} км")
        except Exception:
            pass

        return "\n".join(lines)

    except Exception as e:
        return f"🏃 <b>БІГ</b>\n⚠️ Помилка: {e}"

def format_weekly_run_report() -> str:
    try:
        cw  = compare_weeks()
        this = cw["this_week"]
        prev = cw["prev_week"]
        runs  = get_runs(days=9)

        now = _now_local().replace(tzinfo=None)
        week_start = now - timedelta(days=now.weekday())

        month_names = ["", "Січня", "Лютого", "Березня", "Квітня", "Травня", "Червня",
                       "Липня", "Серпня", "Вересня", "Жовтня", "Листопада", "Грудня"]

        lines = [
            f"🏃 <b>ТИЖНЕВИЙ ЗВІТ БІГУ</b>",
            f"<i>{week_start.strftime('%d')} {month_names[week_start.month]}</i>",
            "",
            f"📊 <b>Підсумок тижня:</b>",
            f"  Пробіжок:  {this['runs']}",
            f"  Дистанція: {this['km']} км",
            f"  Час:       {this['dur_min']} хв",
        ]

        if this["avg_pace_sec"] > 0:
            ps = this["avg_pace_sec"]
            lines.append(f"  Темп:      {int(ps//60)}:{int(ps%60):02d} хв/км")

        lines.append("")
        lines.append(f"📈 <b>vs минулий тиждень:</b>")
        km_sign = "+" if cw["km_diff"] >= 0 else ""
        km_emoji = "📈" if cw["km_diff"] >= 0 else "📉"
        lines.append(f"  {km_emoji} Дистанція: {km_sign}{cw['km_diff']} км")

        if cw["pace_diff"] != 0 and this["avg_pace_sec"] > 0 and prev["avg_pace_sec"] > 0:
            pace_emoji = "🔽" if cw["pace_diff"] < 0 else "🔼"
            faster = "швидше" if cw["pace_diff"] < 0 else "повільніше"
            abs_diff = abs(cw["pace_diff"])
            lines.append(f"  {pace_emoji} Темп: на {int(abs_diff//60)}:{int(abs_diff%60):02d} {faster}")

        this_runs = [r for r in runs if r["date"] >= week_start.replace(hour=0, minute=0, second=0)]
        if this_runs:
            lines.append("")
            lines.append(f"📋 <b>Пробіжки:</b>")
            for r in this_runs:
                hr_str = f" ❤️{r['hr']:.0f}" if r["hr"] else ""
                lines.append(f"  {r['date_str']} — {r['dist_km']} км · {r['pace_str']} хв/км{hr_str}")

        return "\n".join(lines)

    except Exception as e:
        return f"🏃 <b>ТИЖНЕВИЙ ЗВІТ БІГУ</b>\n⚠️ Помилка: {e}"

def format_monthly_run_report(year: int = None, month: int = None) -> str:
    try:
        now = _now_local().replace(tzinfo=None)
        if year is None: year = now.year
        if month is None: month = now.month

        ms = get_month_stats(year, month)
        month_names = ["", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                       "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]
        mname = month_names[month]

        lines = [
            f"🏃 <b>МІСЯЧНИЙ ЗВІТ БІГУ — {mname} {year}</b>",
            "",
            f"📊 <b>Підсумок:</b>",
            f"  Пробіжок:  {ms['runs']}",
            f"  Дистанція: {ms['km']} км",
            f"  Час:       {ms['duration_min']} хв ({ms['duration_min']//60}г {ms['duration_min']%60}хв)",
        ]

        if ms.get("avg_pace_str") and ms["avg_pace_str"] != "—":
            lines.append(f"  Темп:      {ms['avg_pace_str']} хв/км")
        if ms.get("avg_hr"):
            lines.append(f"  Середній ЧСС: {ms['avg_hr']:.0f} уд/хв")

        if ms.get("best_run"):
            br = ms["best_run"]
            lines.append(f"\n🏆 <b>Найдовша:</b> {br['dist_km']} км ({br['date_str']}, {br['pace_str']} хв/км)")

        if ms.get("fastest"):
            fr = ms["fastest"]
            lines.append(f"⚡️ <b>Найшвидша:</b> {fr['dist_km']} км — темп {fr['pace_str']} хв/км ({fr['date_str']})")

        try:
            cm = compare_months()
            pm = cm["prev_month"]
            if pm["km"] > 0:
                km_diff = cm["km_diff"]
                sign = "+" if km_diff >= 0 else ""
                emoji = "📈" if km_diff >= 0 else "📉"
                prev_mname = month_names[pm["month"]]
                lines.append(f"\n{emoji} <b>vs {prev_mname}:</b> {sign}{km_diff} км, {sign}{cm['runs_diff']} пробіжок")
        except Exception:
            pass

        if ms.get("runs_list"):
            weeks = {}
            for r in ms["runs_list"]:
                wk = r["date"].isocalendar()[1]
                if wk not in weeks:
                    weeks[wk] = []
                weeks[wk].append(r)
            lines.append(f"\n📅 <b>По тижнях:</b>")
            for wk_num in sorted(weeks):
                wk_runs = weeks[wk_num]
                wk_km = round(sum(r["dist_km"] for r in wk_runs), 1)
                lines.append(f"  Тиждень {wk_num}: {len(wk_runs)} пробіжок · {wk_km} км")

        return "\n".join(lines)

    except Exception as e:
        return f"🏃 <b>МІСЯЧНИЙ ЗВІТ БІГУ</b>\n⚠️ Помилка: {e}"

# ─── ПІДТВЕРДЖЕННЯ В TELEGRAM ─────────────────────────────────────────────────

def _send(text):
    try:
        import quiet as _q_g
        if _q_g.blocked("msg"):
            print("[quiet] 🌙 сон: running._send придушено", flush=True)
            return False
    except Exception:
        pass
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"running _send error: {e}")
        return False

format_strava_block = format_run_block  # сумісність зі старими викликами (monitor.py, allctx.py)


def send_confirmation(record: dict):
    """Підтвердження, що пробіжку записано."""
    dist_km = round((record.get("distance") or 0) / 1000, 2)
    dur_min = (record.get("moving_time") or 0) // 60
    hr = record.get("average_heartrate")
    elev = record.get("total_elevation_gain")
    notes = record.get("notes")
    try:
        date_short = record["start_date_local"].split("T")[0]
    except Exception:
        date_short = "?"

    lines = [f"✅ <b>Пробіжку записано</b> ({date_short})\n"]
    if dist_km:  lines.append(f"  🏃 Дистанція: {dist_km} км")
    if dur_min:  lines.append(f"  ⏱ Час: {dur_min} хв")
    if dist_km and dur_min:
        pace_sec = (dur_min * 60) / dist_km
        lines.append(f"  ⚡️ Темп: {int(pace_sec//60)}:{int(pace_sec%60):02d} хв/км")
    if hr:       lines.append(f"  ❤️ Пульс: {hr} уд/хв")
    if elev:     lines.append(f"  ⛰ Набір висоти: {elev:.0f} м")
    if notes:    lines.append(f"  💬 {notes}")
    lines.append("\n<i>Включено в звіти про біг (день/тиждень/місяць)</i>")
    _send("\n".join(lines))
