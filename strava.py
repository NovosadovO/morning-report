import requests
import json
import os
from datetime import datetime, timedelta, timezone

CLIENT_ID = "228739"
CLIENT_SECRET = "48f5fe81c418ea39328fa88a1d4a82a37c3fc3fe"
REFRESH_TOKEN_ENV = "STRAVA_REFRESH_TOKEN"

# GitHub storage для refresh token
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")
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
            r = requests.get(url, headers=headers, timeout=10)
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


def get_last_activity():
    """Повертає dict з даними останнього тренування або None"""
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
            return None

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
        start_dt = datetime.fromisoformat(a["start_date_local"].replace("Z", ""))
        date_str = start_dt.strftime("%d.%m %H:%M")

        # Чи сьогодні
        now = datetime.now()
        is_today = start_dt.date() == now.date()
        is_yesterday = start_dt.date() == (now - timedelta(days=1)).date()

        if is_today:
            when = "сьогодні"
        elif is_yesterday:
            when = "вчора"
        else:
            days_ago = (now.date() - start_dt.date()).days
            when = f"{days_ago} дн. тому"

        return {
            "name": a.get("name", "Тренування"),
            "type": a.get("type", "Run"),
            "distance_km": round(distance_km, 2),
            "duration_min": duration_min,
            "pace": pace_str,
            "date": date_str,
            "when": when,
            "elevation": a.get("total_elevation_gain", 0),
            "hr": a.get("average_heartrate"),
            "kudos": a.get("kudos_count", 0),
        }
    except Exception as e:
        print(f"Strava get_last_activity error: {e}")
        return None


def get_week_stats():
    """Статистика за поточний тиждень (Пн-Нд)"""
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
            timeout=15
        )
        r.raise_for_status()
        activities = r.json()

        runs = [a for a in activities if a.get("type") in ("Run", "VirtualRun", "TrailRun")]

        total_km = sum(a["distance"] for a in runs) / 1000
        total_min = sum(a["moving_time"] for a in runs) // 60
        count = len(runs)

        return {
            "runs": count,
            "km": round(total_km, 1),
            "duration_min": total_min,
            "week_start": week_start.strftime("%d.%m"),
        }
    except Exception as e:
        print(f"Strava get_week_stats error: {e}")
        return None


def format_strava_block():
    """Форматований блок для Telegram звіту"""
    last = get_last_activity()
    week = get_week_stats()

    lines = ["🏃 <b>БІГОВИЙ ТРЕКЕР</b>"]

    if last:
        type_emoji = {"Run": "🏃", "TrailRun": "🏔", "VirtualRun": "💻"}.get(last["type"], "🏃")
        lines.append(f"\n<b>Остання пробіжка</b> ({last['when']}):")
        lines.append(f"  {type_emoji} {last['distance_km']} км · {last['duration_min']} хв · {last['pace']}")
        if last["elevation"]:
            lines.append(f"  ⛰ Набір висоти: {last['elevation']:.0f} м")
        if last["hr"]:
            lines.append(f"  ❤️ ЧСС: {last['hr']:.0f} уд/хв")
    else:
        lines.append("\n  Немає даних про тренування")

    if week:
        lines.append(f"\n<b>Цей тиждень</b> (з {week['week_start']}):")
        lines.append(f"  📅 Пробіжок: {week['runs']} · {week['km']} км · {week['duration_min']} хв")

        # Прогрес-бар (ціль 40 км/тиждень)
        goal_km = 40
        pct = min(week["km"] / goal_km, 1.0)
        filled = int(pct * 10)
        bar = "█" * filled + "░" * (10 - filled)
        lines.append(f"  [{bar}] {week['km']}/{goal_km} км")

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_strava_block())
