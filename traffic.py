#!/usr/bin/env python3
"""
Трафік — перевіряє маршрут по календарю і надсилає сповіщення.
Використовує OpenRouteService API.
"""

import os, json, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

try:
    import requests as _req
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
ORS_KEY        = os.environ.get("ORS_API_KEY", "")

# Koordinати міст (звідки / куди)
LOCATIONS = {
    "košice":  (48.7163, 21.2611),
    "kosice":  (48.7163, 21.2611),
    "кошіце":  (48.7163, 21.2611),
    "prešov":  (48.9962, 21.2390),
    "presov":  (48.9962, 21.2390),
    "прешов":  (48.9962, 21.2390),
    "братислава": (48.1486, 17.1077),
    "bratislava": (48.1486, 17.1077),
    "ужгород": (48.6239, 22.2947),
    "uzhhorod": (48.6239, 22.2947),
    "львів":   (49.8397, 24.0297),
    "lviv":    (49.8397, 24.0297),
}

HOME = (48.7163, 21.2611)  # Košice за замовчуванням

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4090],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"Telegram error: {e}")

def get_route(origin, destination):
    """Повертає (км, хвилини) або None."""
    if not ORS_KEY:
        return None
    lon1, lat1 = origin[1], origin[0]
    lon2, lat2 = destination[1], destination[0]
    url = (
        f"https://api.openrouteservice.org/v2/directions/driving-car"
        f"?api_key={ORS_KEY}&start={lon1},{lat1}&end={lon2},{lat2}"
    )
    try:
        if _HAS_REQUESTS:
            r = _req.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
        else:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
        seg  = data["features"][0]["properties"]["segments"][0]
        km   = seg["distance"] / 1000
        mins = seg["duration"] / 60
        return km, mins
    except Exception as e:
        print(f"ORS error: {e}")
        return None

def detect_destination(text):
    """Знаходить місто призначення в тексті події."""
    t = text.lower()
    for key, coords in LOCATIONS.items():
        if key in t:
            return key.capitalize(), coords
    return None, None

def traffic_advice(mins):
    """Порада залежно від часу в дорозі."""
    if mins > 60:
        return "🔴 Затори! Виїжджай раніше на 20-30 хв"
    elif mins > 45:
        return "🟡 Невеликі затори, закладай +15 хв"
    else:
        return "🟢 Дорога вільна"

# ─── ПЕРЕВІРКА КАЛЕНДАРЯ ──────────────────────────────────────────────────────

def check_calendar_traffic():
    """
    Перевіряє події в Calendar наступні 2 години.
    Якщо знаходить подію з відомим містом — за 1 годину до виїзду надсилає маршрут.
    """
    import subprocess

    now   = datetime.now(timezone.utc)
    t_min = now.isoformat()
    t_max = (now + timedelta(hours=2)).isoformat()

    result = subprocess.run(
        ["connector", "run", "google_calendar", "google_calendar-list-events",
         json.dumps({"timeMin": t_min, "timeMax": t_max, "singleEvents": True, "maxResults": 10})],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        return

    try:
        events = json.loads(result.stdout)
        if isinstance(events, dict):
            events = events.get("items", [])
    except:
        return

    for ev in events:
        summary = ev.get("summary", "")
        start   = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
        if not start:
            continue

        dest_name, dest_coords = detect_destination(summary)
        if not dest_coords:
            # Перевіряємо також location поля
            location = ev.get("location", "")
            dest_name, dest_coords = detect_destination(location)

        if not dest_coords:
            continue

        try:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except:
            continue

        diff_min = (dt - now).total_seconds() / 60

        # Надсилаємо за 60 хв до події
        if 55 <= diff_min <= 65:
            route = get_route(HOME, dest_coords)
            local_dt = dt + timedelta(hours=2)
            t_str = local_dt.strftime("%H:%M")

            if route:
                km, mins = route
                advice = traffic_advice(mins)
                # Рекомендований час виїзду
                depart = dt - timedelta(minutes=mins + 10)
                depart_local = (depart + timedelta(hours=2)).strftime("%H:%M")

                msg = (
                    f"🚗 <b>Маршрут: Košice → {dest_name}</b>\n\n"
                    f"📍 Подія: <b>{summary}</b> о {t_str}\n\n"
                    f"🛣 Відстань: <b>{km:.1f} км</b>\n"
                    f"⏱ Час в дорозі: <b>{mins:.0f} хв</b>\n"
                    f"{advice}\n\n"
                    f"✅ Рекомендований виїзд: <b>{depart_local}</b>"
                )
            else:
                msg = (
                    f"🚗 <b>Нагадування</b>\n"
                    f"Подія <b>{summary}</b> о {t_str}\n"
                    f"Маршрут до {dest_name} — не вдалось отримати дані"
                )

            send_telegram(msg)
            print(f"Traffic alert sent for: {summary}")

# ─── КОМАНДА /маршрут ─────────────────────────────────────────────────────────

def handle_route_command(destination_text):
    """Обробляє команду /маршрут <місто>."""
    dest_name, dest_coords = detect_destination(destination_text.lower())
    if not dest_coords:
        return f"🗺 Не знаю це місто. Доступні: Прешов, Братислава, Ужгород, Львів"

    route = get_route(HOME, dest_coords)
    if not route:
        return "⚠️ Не вдалось отримати маршрут. Спробуй пізніше."

    km, mins = route
    advice = traffic_advice(mins)
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    arrive = (now_local + timedelta(minutes=mins)).strftime("%H:%M")

    return (
        f"🚗 <b>Košice → {dest_name}</b>\n\n"
        f"🛣 Відстань: <b>{km:.1f} км</b>\n"
        f"⏱ Час в дорозі: <b>{mins:.0f} хв</b>\n"
        f"{advice}\n\n"
        f"🏁 Прибуття орієнтовно о <b>{arrive}</b>"
    )

if __name__ == "__main__":
    # Тест
    print(handle_route_command("прешов"))
