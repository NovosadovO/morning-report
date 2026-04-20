#!/usr/bin/env python3
"""
Звіт 2 — надсилається кожні 3г зі зсувом 1.5г від основного звіту.
Містить: 🌍 Світові новини | 🚗 Трафік Košice | 💶 Курси валют | 🌬 Якість повітря
"""

import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
_DATA_DIR      = os.path.dirname(os.path.abspath(__file__))
NEWS_SEEN_FILE = os.path.join(_DATA_DIR, "report2_news_seen.json")


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
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
            return r.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def fetch_json(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            if _HAS_REQUESTS:
                r = _requests.get(url, headers={"User-Agent": "report2/1.0"}, timeout=15)
                r.raise_for_status()
                return r.json()
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "report2/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read().decode())
        except Exception as e:
            print(f"fetch_json attempt {attempt}/{retries} [{url[:55]}]: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


def fetch_text(url, retries=2):
    for attempt in range(1, retries + 1):
        try:
            if _HAS_REQUESTS:
                r = _requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                r.raise_for_status()
                return r.text
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"fetch_text attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(2)
    return None


def load_json_file(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json_file(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def translate_ua(text):
    """Перекладає текст на українську через Google Translate."""
    try:
        url = ("https://translate.googleapis.com/translate_a/single"
               "?client=gtx&sl=en&tl=uk&dt=t&q=" + urllib.parse.quote(text))
        data = fetch_json(url)
        if data and data[0]:
            return "".join([s[0] for s in data[0] if s and s[0]])
    except Exception as e:
        print(f"translate error: {e}")
    return text


# ─── 1. СВІТОВІ НОВИНИ ────────────────────────────────────────────────────────

def get_world_news():
    """Топ-5 світових новин з BBC RSS → переклад UA."""
    rss = fetch_text("https://feeds.bbci.co.uk/news/world/rss.xml")
    if not rss:
        # Fallback — Reuters
        rss = fetch_text("https://feeds.reuters.com/reuters/worldNews")
    if not rss:
        return "🌍 <b>Новини світу</b>\n⚠️ Недоступно"

    import re
    titles = re.findall(r"<title><!\[CDATA\[(.+?)\]", rss)
    links  = re.findall(r"<link>(?!https://www\.bbc)(.+?)</link>", rss)

    # Перший title — назва каналу, пропускаємо
    titles = [t for t in titles if t not in ("BBC News", "Reuters")][:5]

    seen  = set(load_json_file(NEWS_SEEN_FILE, default=[]))
    new_seen = list(seen)
    lines = []

    for i, title in enumerate(titles[:5]):
        if title in seen:
            continue
        translated = translate_ua(title)
        link = links[i] if i < len(links) else ""
        if link:
            lines.append(f"• <a href='{link}'>{esc(translated[:120])}</a>")
        else:
            lines.append(f"• {esc(translated[:120])}")
        new_seen.append(title)

    save_json_file(NEWS_SEEN_FILE, new_seen[-200:])

    if not lines:
        return "🌍 <b>Новини світу</b>\nНових новин немає"

    return "🌍 <b>Новини світу</b>\n" + "\n".join(lines)


# ─── 2. ТРАФІК KOŠICE ─────────────────────────────────────────────────────────

# Ключові маршрути міста: (назва, старт lon,lat, кінець lon,lat, норма хв)
ROUTES = [
    ("Центр → Північ", "21.2390,48.7100", "21.2390,48.7350", 8),
    ("Захід → Центр",  "21.2100,48.7163", "21.2611,48.7163", 7),
    ("Південь → Центр","21.2390,48.6950", "21.2390,48.7163", 9),
]

def get_traffic():
    """Оцінює трафік через OSRM — порівнює з нормою."""
    lines = []
    any_data = False

    for name, start, end, normal_min in ROUTES:
        url = (f"https://router.project-osrm.org/route/v1/driving/{start};{end}"
               "?overview=false")
        data = fetch_json(url)
        if not data or data.get("code") != "Ok":
            continue
        any_data = True
        duration = data["routes"][0]["legs"][0]["duration"]
        actual_min = duration / 60

        ratio = actual_min / normal_min
        if ratio < 1.15:
            status = "🟢 Вільно"
        elif ratio < 1.4:
            status = "🟡 Помірно"
        elif ratio < 1.7:
            status = "🟠 Затори"
        else:
            status = "🔴 Пробки"

        delay = int(actual_min - normal_min)
        delay_str = f"+{delay} хв" if delay > 1 else ""
        lines.append(f"{status} {name}{' ' + delay_str if delay_str else ''}")

    if not any_data:
        return "🚗 <b>Трафік Košice</b>\n⚠️ Недоступно"

    # Загальна оцінка
    red   = sum(1 for l in lines if "🔴" in l)
    orange = sum(1 for l in lines if "🟠" in l)
    if red >= 2:
        summary = "🔴 Місто стоїть"
    elif red + orange >= 2:
        summary = "🟠 Є затори"
    elif orange >= 1:
        summary = "🟡 Помірний рух"
    else:
        summary = "🟢 Дороги вільні"

    return "🚗 <b>Трафік Košice</b> — " + summary + "\n" + "\n".join(lines)


# ─── 3. КУРСИ ВАЛЮТ ───────────────────────────────────────────────────────────

def get_currency():
    """Курси EUR, USD, CZK до UAH (НБУ) + EUR/USD (Frankfurter)."""
    lines = []

    # НБУ — гривневі курси
    nbu = fetch_json("https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json")
    if nbu:
        nbu_map = {item["cc"]: item["rate"] for item in nbu}
        pairs = [("EUR", "€"), ("USD", "$"), ("CZK", "Kč")]
        for code, sym in pairs:
            rate = nbu_map.get(code)
            if rate:
                lines.append(f"  {sym} {code}: <b>{rate:.2f} ₴</b>")

    # EUR/USD через Frankfurter
    fx = fetch_json("https://api.frankfurter.app/latest?from=EUR&to=USD,CZK,GBP")
    if fx:
        usd = fx.get("rates", {}).get("USD")
        czk = fx.get("rates", {}).get("CZK")
        if usd:
            lines.append(f"  € EUR/USD: <b>{usd:.4f}</b>")
        if czk:
            lines.append(f"  € EUR/CZK: <b>{czk:.3f}</b>")

    if not lines:
        return "💶 <b>Курси валют</b>\n⚠️ Недоступно"

    return "💶 <b>Курси валют</b>\n" + "\n".join(lines)


# ─── 4. ЯКІСТЬ ПОВІТРЯ ────────────────────────────────────────────────────────

def get_air_quality():
    """Індекс якості повітря для Košice (Open-Meteo AQI)."""
    url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        "?latitude=48.7163&longitude=21.2611"
        "&current=european_aqi,pm10,pm2_5,nitrogen_dioxide,ozone"
        "&timezone=Europe%2FPrague"
    )
    data = fetch_json(url)
    if not data:
        return "🌬 <b>Якість повітря</b>\n⚠️ Недоступно"

    current = data.get("current", {})
    aqi     = current.get("european_aqi")
    pm25    = current.get("pm2_5")
    pm10    = current.get("pm10")
    no2     = current.get("nitrogen_dioxide")

    if aqi is None:
        return "🌬 <b>Якість повітря</b>\n⚠️ Недоступно"

    if aqi <= 20:
        level = "🟢 Відмінна"
    elif aqi <= 40:
        level = "🟢 Добра"
    elif aqi <= 60:
        level = "🟡 Помірна"
    elif aqi <= 80:
        level = "🟠 Погана"
    elif aqi <= 100:
        level = "🔴 Дуже погана"
    else:
        level = "🟣 Небезпечна"

    result = f"🌬 <b>Якість повітря Košice</b>\n{level} (AQI {aqi:.0f})"
    details = []
    if pm25 is not None: details.append(f"PM2.5: {pm25:.1f}")
    if pm10 is not None: details.append(f"PM10: {pm10:.1f}")
    if no2  is not None: details.append(f"NO₂: {no2:.1f}")
    if details:
        result += "\n  " + " · ".join(details) + " μg/m³"

    if aqi > 60:
        result += "\n⚠️ Рекомендуємо обмежити час на вулиці"

    return result


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    now        = datetime.now(timezone.utc)
    local_time = (now + timedelta(hours=2)).strftime("%H:%M")
    local_date = (now + timedelta(hours=2)).strftime("%d.%m.%Y")

    print(f"=== Report2 run at {now.isoformat()} ===")

    news_text    = get_world_news()
    traffic_text = get_traffic()
    currency_text = get_currency()
    aqi_text     = get_air_quality()

    report = (
        f"🕐 <b>Дайджест {local_time} · {local_date}</b>\n\n"
        f"{news_text}\n\n"
        f"{traffic_text}\n\n"
        f"{currency_text}\n\n"
        f"{aqi_text}"
    )

    print(report)
    ok = send_telegram(report)
    print("Sent:", ok)


if __name__ == "__main__":
    main()
