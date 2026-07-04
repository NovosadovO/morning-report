import requests
import json
import os
from datetime import datetime, timedelta, timezone

CLIENT_ID = "228739"
CLIENT_SECRET = "48f5fe81c418ea39328fa88a1d4a82a37c3fc3fe"
REFRESH_TOKEN_ENV = "STRAVA_REFRESH_TOKEN"

# GitHub storage для refresh token
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "NovosadovO/morning-report")
STRAVA_TOKEN_FILE = "strava_token.json"


def _get_refresh_token():
    """Отримати refresh token з env або GitHub"""
    # Спочатку env
    token = os.getenv(REFRESH_TOKEN_ENV)
    if token:
        return token

    # Потім GitHub
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STRAVA_TOKEN_FILE}"
            headers = {"Authorization": f"token {GITHUB_TOKEN}"}
            r = requests.get(url, headers=headers, params={"ref": "data"}, timeout=10)
            if r.status_code == 200:
                import base64
                content = base64.b64decode(r.json()["content"]).decode()
                data = json.loads(content)
                return data.get("refresh_token")
        except Exception:
            pass

    return None


def _save_refresh_token(new_token):
    """Зберегти оновлений refresh token у GitHub"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return

    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{STRAVA_TOKEN_FILE}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Content-Type": "application/json"
        }

        # Отримати SHA якщо файл існує
        sha = None
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            sha = r.json()["sha"]

        content = json.dumps({"refresh_token": new_token}, indent=2)
        encoded = base64.b64encode(content.encode()).decode()

        payload = {
            "message": "Update Strava refresh token",
            "content": encoded,
            "branch": "data"
        }
        if sha:
            payload["sha"] = sha

        requests.put(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"Strava: не вдалося зберегти token: {e}")


def _get_access_token():
    """Отримати свіжий access token через refresh token"""
    refresh_token = _get_refresh_token()
    if not refresh_token:
        raise Exception("STRAVA_REFRESH_TOKEN не знайдено")

    r = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15
    )
    r.raise_for_status()
    data = r.json()

    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        _save_refresh_token(new_refresh)

    return data["access_token"]


_STRAVA_CACHE_KEY = "strava_last_activity.json"

def _save_activity_cache(data: dict):
    """Зберегти останню активність в локальний кеш."""
    try:
        import storage as _st
        _st.save(_STRAVA_CACHE_KEY, data)
    except Exception:
        pass

def _load_activity_cache() -> dict | None:
    """Завантажити кешовану активність."""
    try:
        import storage as _st
        return _st.load(_STRAVA_CACHE_KEY)
    except Exception:
        return None

def _compute_when(start_date_local_iso: str) -> str:
    """Рахує 'сьогодні/вчора/N дн. тому' ЗАВЖДИ динамічно від поточного моменту —
    ніколи не бере застаріле значення з кешу."""
    try:
        start_dt = datetime.fromisoformat(start_date_local_iso.replace("Z", ""))
        now = datetime.now()
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
    """Повертає dict з даними останнього тренування або None.
    ЗАВЖДИ перераховує 'when' (сьогодні/вчора/N днів тому) від АКТУАЛЬНОГО часу,
    навіть якщо дані беруться зі старого кешу — щоб не показувати застарілу дату.
    Якщо API недоступне і використовується кеш — ставить прапор 'stale': True,
    щоб інші частини системи (AI-промпти) НЕ видавали ці дані за live-актуальні."""
    try:
        token = _get_access_token()
        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 1, "page": 1},
            timeout=15
        )
        r.raise_for_status()
        activities = r.json()

        if not activities:
            cached = _load_activity_cache()
            if cached and cached.get("start_date_local"):
                cached["when"] = _compute_when(cached["start_date_local"])
                cached["stale"] = False  # API відповіло успішно, просто немає активностей
            return cached

        a = activities[0]
        distance_km = a["distance"] / 1000
        duration_sec = a["moving_time"]
        duration_min = duration_sec // 60

        # Темп хв/км
        if distance_km > 0:
            pace_sec = duration_sec / distance_km
            pace_min = int(pace_sec // 60)
            pace_s = int(pace_sec % 60)
            pace_str = f"{pace_min}:{pace_s:02d} хв/км"
        else:
            pace_str = "—"

        # Дата
        start_date_local_iso = a["start_date_local"]
        start_dt = datetime.fromisoformat(start_date_local_iso.replace("Z", ""))
        date_str = start_dt.strftime("%d.%m %H:%M")
        when = _compute_when(start_date_local_iso)

        result = {
            "name": a.get("name", "Тренування"),
            "type": a.get("type", "Run"),
            "distance_km": round(distance_km, 2),
            "duration_min": duration_min,
            "pace": pace_str,
            "date": date_str,
            "when": when,
            "start_date_local": start_date_local_iso,
            "elevation": a.get("total_elevation_gain", 0),
            "hr": a.get("average_heartrate"),
            "kudos": a.get("kudos_count", 0),
            "stale": False,  # свіжі дані напряму з API
        }
        _save_activity_cache(result)
        return result
    except Exception as e:
        print(f"Strava get_last_activity error: {e}")
        # Fallback: кешовані дані — але 'when' ЗАВЖДИ перераховуємо, і СТАВИМО ПРАПОР stale
        cached = _load_activity_cache()
        if cached:
            print("Strava: using cached last activity (API failed — marking as STALE)")
            if cached.get("start_date_local"):
                cached["when"] = _compute_when(cached["start_date_local"])
            else:
                cached["when"] = "дата невідома (старий кеш)"
            cached["stale"] = True  # API недоступне — це НЕ гарантовано свіжі дані
        return cached


_WEEK_STATS_CACHE: dict = {"data": None, "ts": 0}
_WEEK_STATS_TTL = 600  # 10 хвилин — той самий burst-protect що і get_activities

def get_week_stats():
    """Статистика за поточний тиждень (Пн-Нд). Кешується на 10 хв."""
    import time as _time_cache
    now_ts = _time_cache.time()
    if _WEEK_STATS_CACHE["data"] is not None and (now_ts - _WEEK_STATS_CACHE["ts"]) < _WEEK_STATS_TTL:
        return _WEEK_STATS_CACHE["data"]

    try:
        token = _get_access_token()

        # Початок тижня (Понеділок)
        now = datetime.now()
        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        after_ts = int(week_start.timestamp())

        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 30, "after": after_ts},
            timeout=5  # Жорсткий timeout 5s, щоб не зависати
        )
        r.raise_for_status()
        activities = r.json()

        runs = [a for a in activities if a.get("type") in ("Run", "VirtualRun", "TrailRun")]

        total_km = sum(a["distance"] for a in runs) / 1000
        total_min = sum(a["moving_time"] for a in runs) // 60
        count = len(runs)

        result = {
            "runs": count,
            "km": round(total_km, 1),
            "duration_min": total_min,
            "week_start": week_start.strftime("%d.%m"),
        }
        _WEEK_STATS_CACHE["data"] = result
        _WEEK_STATS_CACHE["ts"] = now_ts
        return result
    except Exception as e:
        print(f"Strava get_week_stats error (timeout 5s): {e}")
        if _WEEK_STATS_CACHE["data"] is not None:
            print(f"Strava get_week_stats: returning STALE cache (age {now_ts - _WEEK_STATS_CACHE['ts']:.0f}s)")
            return _WEEK_STATS_CACHE["data"]
        return None


def _format_run_lines(last: dict) -> list:
    """Форматує рядки для одного тренування (без заголовку)."""
    type_emoji = {"Run": "🏃", "TrailRun": "🏔", "VirtualRun": "💻"}.get(last["type"], "🏃")
    lines = []
    lines.append(f"  {type_emoji} {last['distance_km']} км · {last['duration_min']} хв · {last['pace']}")
    if last.get("elevation"):
        lines.append(f"  ⛰ Набір висоти: {last['elevation']:.0f} м")
    if last.get("hr"):
        lines.append(f"  ❤️ ЧСС: {last['hr']:.0f} уд/хв")
    return lines


def format_strava_block():
    """Форматований блок для Telegram звіту"""
    last = get_last_activity()
    week = get_week_stats()

    lines = ["🏃 <b>БІГОВИЙ ТРЕКЕР</b>"]

    if last:
        is_today = last.get("when") == "сьогодні"
        is_stale = last.get("stale", False)
        if is_stale:
            lines.append(f"\n⚠️ <b>Strava API недоступне — показані ОСТАННІ ВІДОМІ дані (можуть бути неактуальні):</b>")
        elif is_today:
            lines.append(f"\n<b>Актуальне тренування:</b>")
        else:
            # Показуємо дату останнього тренування
            date_short = last.get("date", "").split(" ")[0]  # "DD.MM"
            lines.append(f"\n<b>Останнє тренування ({date_short}):</b>")
        lines.extend(_format_run_lines(last))
    else:
        lines.append("\n  Немає даних про тренування")

    if week:
        lines.append(f"\n<b>Цей тиждень</b> (з {week['week_start']}):")
        lines.append(f"  📅 Пробіжок: {week['runs']} · {week['km']} км · {week['duration_min']} хв")
        lines.append(f"  🎯 Ціль: {week['km']}/40 км")

    # Таблиця останніх 5 пробіжок
    try:
        recent = get_runs(days=60)[-5:]
        if recent:
            lines.append("\n<b>Останні пробіжки:</b>")
            for r in reversed(recent):
                lines.append(f"  {r['date_str']}  <b>{r['dist_km']} км</b>  {r['pace_str']}/км  {r['dur_min']} хв")
    except Exception:
        pass

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_strava_block())


# ─── РОЗШИРЕНІ ФУНКЦІЇ ────────────────────────────────────────────────────────

# In-process TTL кеш — уникає burst-запитів до Strava (кілька watcher-ів/report
# blocks дзвонять get_activities()/get_week_stats() майже одночасно з різними
# days= в межах одного циклу звіту → без кешу це N окремих HTTP запитів підряд
# → Strava rate-limit 429. TTL 10 хв: усі виклики в межах одного "сплеску"
# отримують один і той самий результат замість N запитів.
_ACT_CACHE: dict = {}
_ACT_CACHE_TTL = 600  # 10 хвилин

def get_activities(days: int = 30) -> list:
    """Повертає список активностей за останні N днів. Кешується на 10 хв per-days,
    щоб уникнути burst 429 коли кілька частин звіту дзвонять цю функцію одночасно."""
    import time as _time_cache
    now_ts = _time_cache.time()
    cached = _ACT_CACHE.get(days)
    if cached and (now_ts - cached["ts"]) < _ACT_CACHE_TTL:
        return cached["data"]

    try:
        token = _get_access_token()
        after_ts = int((datetime.now() - timedelta(days=days)).timestamp())
        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100, "after": after_ts},
            timeout=5  # Жорсткий timeout 5s
        )
        r.raise_for_status()
        data = r.json()
        _ACT_CACHE[days] = {"data": data, "ts": now_ts}
        return data
    except Exception as e:
        print(f"get_activities error (timeout 5s): {e}")
        # Якщо є хоч трохи протухлий кеш — краще повернути його, ніж порожньо
        if cached:
            print(f"get_activities: returning STALE cache for days={days} (age {now_ts - cached['ts']:.0f}s)")
            return cached["data"]
        return []


def get_runs(days: int = 30) -> list:
    """Тільки пробіжки за N днів з розрахованими полями."""
    acts = get_activities(days=days)
    result = []
    for a in acts:
        if a.get("type") not in ("Run", "VirtualRun", "TrailRun"):
            continue
        dist_km = round(a["distance"] / 1000, 2)
        dur_sec = a["moving_time"]
        pace_sec = (dur_sec / dist_km) if dist_km > 0 else 0
        dt = datetime.fromisoformat(a["start_date_local"].replace("Z", ""))
        result.append({
            "id":         a["id"],
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
            "calories":   a.get("calories") or a.get("kilojoules", 0),
            "type":       a.get("type", "Run"),
        })
    return sorted(result, key=lambda x: x["date"])


def get_month_stats(year: int = None, month: int = None) -> dict:
    """Повна статистика за місяць."""
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month

    # Дні в місяці
    import calendar
    days_in_month = calendar.monthrange(year, month)[1]
    month_start = datetime(year, month, 1)
    # Беремо з запасом — мінімум 90 днів щоб покрити попередній місяць
    days_ago = max((now - month_start).days + 5, 90)
    runs = get_runs(days=days_ago)
    # Фільтруємо тільки цей місяць
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
    """Річна статистика по місяцях."""
    now = datetime.now()
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
    """Порівняння поточного тижня з попереднім."""
    now = datetime.now()
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
        "pace_diff": pace_diff,  # від'ємне = швидший (добре)
    }


def compare_months() -> dict:
    """Порівняння поточного місяця з попереднім."""
    now = datetime.now()
    this_m = get_month_stats(now.year, now.month)
    # Попередній місяць
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


def format_run_analysis(short: bool = False) -> str:
    """
    Повний аналіз бігу для щоденного звіту або команди /біг.
    short=True — компактний блок для ранкового звіту
    """
    try:
        last  = get_last_activity()
        week  = get_week_stats()
        cw    = compare_weeks()

        lines = ["🏃 <b>БІГ</b>"]

        # Остання пробіжка
        if last and last.get("type") in ("Run", "VirtualRun", "TrailRun", None):
            type_emoji = {"Run": "🏃", "TrailRun": "🏔", "VirtualRun": "💻"}.get(last.get("type", "Run"), "🏃")
            lines.append(f"\n<b>Остання</b> ({last['when']}):")
            lines.append(f"  {type_emoji} {last['distance_km']} км · {last['duration_min']} хв · {last['pace']}")
            if last.get("elevation"):
                lines.append(f"  ⛰ Набір: {last['elevation']:.0f} м")
            if last.get("hr"):
                lines.append(f"  ❤️ ЧСС: {last['hr']:.0f} уд/хв")

        # Тиждень з порівнянням
        if week:
            prev_km = cw["prev_week"]["km"]
            km_diff = cw["km_diff"]
            diff_str = ""
            if prev_km > 0:
                sign = "+" if km_diff >= 0 else ""
                diff_str = f"  ({sign}{km_diff} vs минулий)"
            lines.append(f"\n<b>Тиждень:</b> {week['runs']} пробіжок · {week['km']} км{diff_str}")

            if not short:
                # Темп порівняння
                if cw["this_week"]["avg_pace_sec"] > 0 and cw["prev_week"]["avg_pace_sec"] > 0:
                    ps = cw["this_week"]["avg_pace_sec"]
                    pace_this = f"{int(ps//60)}:{int(ps%60):02d}"
                    pp = cw["prev_week"]["avg_pace_sec"]
                    pace_prev = f"{int(pp//60)}:{int(pp%60):02d}"
                    faster = "🔼" if cw["pace_diff"] > 0 else "🔽"
                    lines.append(f"  Темп: {pace_this} хв/км {faster} (було {pace_prev})")

        if short:
            return "\n".join(lines)

        # Місяць
        try:
            cm = compare_months()
            ms = cm["this_month"]
            pm = cm["prev_month"]
            import calendar as _cal
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
    """Повний звіт за тиждень (для недільного резюме)."""
    try:
        cw  = compare_weeks()
        this = cw["this_week"]
        prev = cw["prev_week"]
        runs  = get_runs(days=9)

        import calendar as _cal
        now = datetime.now()
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

        # Порівняння
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

        # Список пробіжок
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
    """Повний звіт за місяць."""
    try:
        now = datetime.now()
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

        # Порівняння з попереднім
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

        # Пробіжки по тижнях
        if ms.get("runs_list"):
            # Групуємо по тижнях
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
