#!/usr/bin/env python3
"""
Monitor — надсилає один зведений звіт кожні 3 години.
"""

import os
import re
import json
import base64
import imaplib
import email
import email.header
import urllib.request
import urllib.error
import urllib.parse
import time
from datetime import datetime, timezone, timedelta

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT   = os.environ["TELEGRAM_CHAT_ID"]
GMAIL_USER      = "novosadovoleg@gmail.com"
GMAIL_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")
_DATA_DIR       = os.path.dirname(os.path.abspath(__file__))
SEEN_EMAIL_FILE = os.path.join(_DATA_DIR, "monitor_seen_emails.json")
# Ціновий кеш в /tmp — зберігається між циклами але скидається при деплої
PRICE_CACHE     = "/tmp/monitor_prices_3h.json"

COINS = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "AVAX": "avalanche-2",
    "ONDO": "ondo-finance",
}

# ─── EMAIL CLASSIFICATION ─────────────────────────────────────────────────────
# Рівні: SPAM (викинути) → PROMO (показати в "Інші") → REAL (основні)

# Домени/ключові слова відправника — одразу в смітник
_SPAM_SENDERS = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "newsletter", "notifications", "mailer", "marketing", "unsubscribe",
    "digest", "updates@", "news@", "alert@binance", "alert@coinbase",
    "notify.railway", "temu", "footshop", "temuemail",
    "unstoppabledomains", "startengine",
    "linkedin", "jobvite", "greenhouse", "workday", "lever.co",
    "economist", "okx", "roundup", "dlnews", "coindesk", "cointelegraph",
    "decrypt.co", "theblock", "blockworks",
    "tripadvis", "booking.com", "sg.booking", "e.tripadvisor", "email.booking",
    "campaign@", "inspiration@", "aboutyou", "hello@news", "deals@", "offers@",
    "uniswap", "investing.com", "coinpoker", "novinky@",
    "sizeer", "pullandbear", "uefa", "store@", "streetguide@",
    "slovnaft", "kaufland", "loyalty", "mp1.", "em.", "info@",
    "finexity", "xtb.com", "zlavomat", "fox.com", "inbox.fox",
    "rondogo", "avax.network", "nft.", "airdrop", "binance.com",
    "support@", "promo@", "hello@", "team@", "hi@",
    "hotels.com", "eg.hotels", "airbnb.com", "expedia",
}

# Суб-домени відправника що означають bulk-mail
_SPAM_SUBDOMAINS = re.compile(
    r'^(news|mail|em|e\d*|m\d*|campaign|email|noreply|no-reply|'
    r'update|notification|send|go|sg|mp\d+|loyalty|kcard|alert|digest|'
    r'bulk|bounce|reply|auto|info|promo|marketing)\.'
)

# Ключові слова в темі листа
_SPAM_SUBJECTS = {
    "newsletter", "digest", "promo", "offer", "sale", "discount",
    "unsubscribe", "your daily", "weekly", "monthly", "referral",
    "new launch", "collecting", "portfolio", "managed by ai",
    "predtým", "teraz", "máš ich",
    "вакансі", "job alert", "new job", "recommended job", "hiring",
    "trading suite", "one step away",
    "national parks", "genius", "watchlist", "satellites",
    "vyberte", "dobierku", "zľava", "výpredaj",
    "% off", "limited time", "exclusive deal", "flash sale",
}

def _classify_email(sender: str, subject: str) -> str:
    """
    Повертає: 'spam' | 'promo' | 'real'
    Логіка базується на EMAIL ДОМЕНІ — не на display name (бо його підробляють).
    """
    s = sender.lower()
    sub = subject.lower()

    # Витягуємо email адресу відправника
    email_match = re.search(r'[\w.+%-]+@([\w.-]+\.[a-z]{2,})', s)
    if not email_match:
        return "spam"
    email_addr = email_match.group(0)
    domain = email_match.group(1)  # example.com
    # Верхній рівень домену (TLD): gmail.com → gmail, s-mania.com → s-mania
    domain_parts = domain.split('.')
    root = domain_parts[-2] if len(domain_parts) >= 2 else domain

    # 1. Явний спам по email/домену — drop
    if any(kw in s for kw in _SPAM_SENDERS):
        return "spam"
    if any(kw in sub for kw in _SPAM_SUBJECTS):
        return "spam"

    # 2. Промо піддомен — drop
    if _SPAM_SUBDOMAINS.match(domain):
        return "promo"

    # 3. ОСОБИСТИЙ EMAIL домен → реальна людина
    # gmail, outlook, hotmail, yahoo, ukr.net, icloud, proton, meta.ua тощо
    _PERSONAL_DOMAINS = {
        "gmail", "googlemail",
        "outlook", "hotmail", "live", "msn",
        "yahoo", "ymail",
        "icloud", "me", "mac",
        "ukr", "i", "meta", "ua",
        "proton", "protonmail",
        "tutanota", "tutamail",
        "seznam",
        "azet", "zoznam", "centrum",  # SK домени
        "post", "email",
    }
    if root in _PERSONAL_DOMAINS:
        return "real"

    # 4. Корпоративний домен — перевіряємо чи виглядає як особистий email
    # Ознаки масової розсилки в локальній частині (перед @):
    local = email_addr.split('@')[0]
    _BULK_LOCAL = {
        "noreply", "no-reply", "donotreply", "newsletter", "news",
        "notifications", "notify", "mailer", "marketing", "promo",
        "info", "hello", "hi", "team", "support", "admin", "updates",
        "deals", "offers", "digest", "alert", "alerts", "bulletin",
        "campaign", "email", "mail", "contact", "service", "sales",
        "billing", "reply", "bounce", "postmaster", "welcome",
        "notification", "automated", "system", "bot",
    }
    if any(kw == local or kw in local for kw in _BULK_LOCAL):
        return "spam"

    # 5. Відомі масові сервіси по root домену
    _BULK_ROOTS = {
        "facebook", "instagram", "twitter", "x", "linkedin", "youtube",
        "google", "apple", "amazon", "microsoft",
        "duolingo", "spotify", "netflix", "twitch",
        "substack", "beehiiv", "mailchimp", "sendgrid", "klaviyo",
        "tradingview", "coinmarketcap", "coingecko", "binance",
        "temu", "shopify", "etsy", "ebay",
        "booking", "airbnb", "expedia", "tripadvisor",
        "profesia", "indeed", "glassdoor",
        "s-mania", "smania", "lanet", "railway",
    }
    if root in _BULK_ROOTS:
        return "spam"

    # 6. Корпоративний домен без ознак розсилки → скоріш за все реальна людина
    return "real"

# Зворотна сумісність
IGNORE_SENDERS = list(_SPAM_SENDERS)
IGNORE_SUBJECTS = list(_SPAM_SUBJECTS)

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4090],
        "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        print(f"Telegram HTTP error: {e.code} {e.read().decode()}")
        return False
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def _send_telegram_photo(photo_url: str, caption: str) -> bool:
    # Шлемо як анімацію (GIF)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendAnimation"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "animation": "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2F84VzoRtuRjk0i6Ju6EUAd%2Fmail_alert.gif",
        "caption": caption[:1024],
        "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"sendAnimation error: {e}")
        return send_telegram(caption)


def fetch_json(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            if _HAS_REQUESTS:
                r = _requests.get(url, headers={"User-Agent": "monitor/1.0"}, timeout=20)
                r.raise_for_status()
                return r.json()
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "monitor/1.0"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read().decode())
        except Exception as e:
            print(f"fetch_json attempt {attempt}/{retries} error [{url[:60]}]: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_json_file(path, default=None):
    """Читає JSON. Якщо файл monitor_*.json — спочатку пробує GitHub (persistent)."""
    filename = os.path.basename(path)
    if filename.startswith("monitor_") and filename.endswith(".json"):
        try:
            import storage as _storage
            return _storage.load(filename, default=default if default is not None else {})
        except Exception:
            pass
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json_file(path, data):
    """Зберігає JSON. Якщо файл monitor_*.json — зберігає в GitHub (persistent)."""
    filename = os.path.basename(path)
    if filename.startswith("monitor_") and filename.endswith(".json"):
        try:
            import storage as _storage
            _storage.save(filename, data)
            return
        except Exception:
            pass
    with open(path, "w") as f:
        json.dump(data, f)


# ─── 1. ЦІНИ ──────────────────────────────────────────────────────────────────

def get_prices():
    ids = ",".join(COINS.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    data = fetch_json(url)

    # Fallback на Kraken якщо CoinGecko не відповів (rate limit)
    if not data:
        data = _get_prices_kraken()

    if not data:
        return "💰 <b>Ціни</b>\n⚠️ Недоступно"

    prev = load_json_file(PRICE_CACHE, default={})
    now_prices = {}
    lines = []

    for symbol, cg_id in COINS.items():
        price    = data.get(cg_id, {}).get("usd")
        change24 = data.get(cg_id, {}).get("usd_24h_change")
        if price is None:
            continue
        now_prices[cg_id] = price
        old = prev.get(cg_id)
        if old and old > 0:
            pct = (price - old) / old * 100
            arrow = "🟢" if pct > 0 else "🔴"
            sign = "+" if pct > 0 else ""
            ch = f"{sign}{pct:.2f}% за 3г"
        elif change24 is not None:
            arrow = "🟢" if change24 > 0 else "🔴"
            sign = "+" if change24 > 0 else ""
            ch = f"{sign}{change24:.2f}% за 24г"
        else:
            arrow = "⚪️"
            ch = "—"
        lines.append(f"{arrow} <b>{symbol}</b>  <code>${price:,.2f}</code>\n   <i>{ch}</i>")

    save_json_file(PRICE_CACHE, now_prices)
    return "💹 <b>ЦІНИ АКТИВІВ</b>\n\n" + "\n".join(lines)


def _get_prices_kraken():
    """Fallback: отримує ціни з Kraken (публічний API, без ключа, без блокування)."""
    # Kraken повертає власні назви пар (XXBTZUSD, XETHZUSD тощо)
    KRAKEN_MAP = {
        "bitcoin":      ("XBTUSD",  ["XXBTZUSD", "XBTUSD"]),
        "ethereum":     ("ETHUSD",  ["XETHZUSD", "ETHUSD"]),
        "avalanche-2":  ("AVAXUSD", ["AVAXUSD"]),
        "ondo-finance": ("ONDOUSD", ["ONDOUSD"]),
    }
    try:
        pairs = ",".join(v[0] for v in KRAKEN_MAP.values())
        raw = fetch_json(f"https://api.kraken.com/0/public/Ticker?pair={pairs}")
        if not raw or raw.get("error"):
            return None
        result_data = raw.get("result", {})
        out = {}
        for cg_id, (_, aliases) in KRAKEN_MAP.items():
            item = None
            for alias in aliases:
                if alias in result_data:
                    item = result_data[alias]
                    break
            if not item:
                continue
            price    = float(item["c"][0])
            open24   = float(item["o"])
            change24 = (price - open24) / open24 * 100 if open24 else 0
            out[cg_id] = {"usd": price, "usd_24h_change": round(change24, 2)}
        return out if out else None
    except Exception as e:
        print(f"Kraken fallback error: {e}")
        return None


# ─── 2. ПОГОДА ────────────────────────────────────────────────────────────────

def get_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=48.7163&longitude=21.2611"
        "&current=temperature_2m,apparent_temperature,weathercode,windspeed_10m,precipitation,relative_humidity_2m,surface_pressure"
        "&hourly=temperature_2m,precipitation,precipitation_probability,weathercode,windspeed_10m"
        "&daily=temperature_2m_max,temperature_2m_min,weathercode,sunrise,sunset,uv_index_max,precipitation_sum"
        "&forecast_days=2&timezone=Europe%2FPrague"
    )
    data = fetch_json(url)
    if not data:
        return "🌡 <b>Погода Košice</b>\n⚠️ Недоступно"

    WMO = {
        0: "☀️ Ясно", 1: "🌤 Перев. ясно", 2: "⛅️ Мінлива хмарність", 3: "☁️ Хмарно",
        45: "🌫 Туман", 48: "🌫 Туман",
        51: "🌦 Мряка", 53: "🌦 Мряка", 55: "🌦 Мряка",
        61: "🌧 Дощ", 63: "🌧 Дощ", 65: "🌧 Сильний дощ",
        71: "❄️ Сніг", 73: "❄️ Сніг", 75: "❄️ Сильний сніг",
        80: "🌦 Злива", 81: "🌦 Злива", 82: "⛈ Сильна злива",
        95: "⛈ Гроза", 96: "⛈ Гроза з градом", 99: "⛈ Сильна гроза",
    }
    RAIN  = {51, 53, 55, 61, 63, 65, 80, 81, 82}
    SNOW  = {71, 73, 75, 77, 85, 86}
    STORM = {95, 96, 99}

    current = data.get("current", {})
    temp  = current.get("temperature_2m")
    feel  = current.get("apparent_temperature")
    code  = current.get("weathercode", 0)
    wind  = current.get("windspeed_10m")
    hum   = current.get("relative_humidity_2m")
    desc  = WMO.get(code, "—")

    daily = data.get("daily", {})
    tmax = daily.get("temperature_2m_max", [None])[0]
    tmin = daily.get("temperature_2m_min", [None])[0]
    sunrise = daily.get("sunrise", [""])[0][11:16] if daily.get("sunrise") else "—"
    sunset  = daily.get("sunset",  [""])[0][11:16] if daily.get("sunset")  else "—"
    uv      = daily.get("uv_index_max", [None])[0]
    precip_sum = daily.get("precipitation_sum", [None])[0]

    uv_str = ""
    if uv is not None:
        uv_lvl = "🟢 Низький" if uv < 3 else ("🟡 Помірний" if uv < 6 else ("🟠 Високий" if uv < 8 else "🔴 Дуже високий"))
        uv_str = f"\n• УФ індекс: {uv:.0f} — {uv_lvl}"

    # Сон — додаємо в погодний блок
    sleep_line = ""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sleep import get_last_night_sleep
        _sl = get_last_night_sleep()
        if _sl:
            sleep_line = f"\n• {_sl}"
    except Exception as _se:
        print(f"sleep error: {_se}")

    result = (
        f"🌤 <b>ПОГОДА — Košice</b>\n"
        f"• {desc}  <b>{temp:.0f}°C</b>  <i>(відч. {feel:.0f}°C)</i>\n"
        f"• 🔻 {tmin:.0f}°C  /  🔺 {tmax:.0f}°C\n"
        f"• 💨 {wind:.0f} км/г   💧 {hum:.0f}%"
        f"{sleep_line}"
    )
    if precip_sum and precip_sum > 0:
        result += f"   🌧 {precip_sum:.1f} мм"
    result += f"\n• 🌅 {sunrise}   🌇 {sunset}"
    if uv_str:
        result += uv_str

    # Прогноз по годинах (наступні 6г)
    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    h_temps = hourly.get("temperature_2m", [])
    h_codes = hourly.get("weathercode", [])
    h_probs = hourly.get("precipitation_probability", [])
    h_winds = hourly.get("windspeed_10m", [])

    local_hour = (datetime.now(timezone.utc).hour + 2) % 24
    forecast_lines = []
    for i, t in enumerate(times):
        try:
            h = int(t[11:13])
        except:
            continue
        diff = (h - local_hour) % 24
        if 1 <= diff <= 6:
            c = h_codes[i] if i < len(h_codes) else 0
            tmp = h_temps[i] if i < len(h_temps) else "—"
            pr = h_probs[i] if i < len(h_probs) else 0
            wd = h_winds[i] if i < len(h_winds) else 0
            icon = WMO.get(c, "—").split()[0]
            rain_str = f"🌧{pr}%" if pr >= 30 else ""
            forecast_lines.append(f"<code>{t[11:16]}</code> {icon}{tmp:.0f}°{rain_str}")

    if forecast_lines:
        result += "\n\n<b>Прогноз:</b>  " + "  │  ".join(forecast_lines[:6])

    # Прогноз на завтра
    tmax_tmr   = daily.get("temperature_2m_max",  [None, None])[1]
    tmin_tmr   = daily.get("temperature_2m_min",  [None, None])[1]
    code_tmr   = daily.get("weathercode",          [0,    0])[1]
    precip_tmr = daily.get("precipitation_sum",   [None, None])[1]
    if tmax_tmr is not None and tmin_tmr is not None:
        desc_tmr = WMO.get(code_tmr, "—")
        rain_tmr = f"  🌧 {precip_tmr:.1f} мм" if precip_tmr and precip_tmr > 0 else ""
        result += (
            f"\n\n<b>Завтра:</b>  {desc_tmr}  "
            f"🔻{tmin_tmr:.0f}°  /  🔺{tmax_tmr:.0f}°{rain_tmr}"
        )

    # Попередження
    warnings = []
    for i, t in enumerate(times):
        try:
            h = int(t[11:13])
        except:
            continue
        diff = (h - local_hour) % 24
        if 0 < diff <= 3:
            c = h_codes[i] if i < len(h_codes) else 0
            pr = h_probs[i] if i < len(h_probs) else 0
            if pr >= 60 or c in RAIN | SNOW | STORM:
                kind = "❄️ Сніг" if c in SNOW else ("⛈ Гроза" if c in STORM else "🌧 Дощ")
                warnings.append(f"  {kind} о {t[11:16]} ({pr}%)")
    if warnings:
        result += "\n⚠️ <b>Найближчі 3г:</b>\n" + "\n".join(warnings)

    return result


# ─── 3. КАЛЕНДАР ──────────────────────────────────────────────────────────────

def _get_google_token(creds_data, scope):
    """Отримує access token для service account через JWT — без googleapiclient."""
    import base64, hashlib, hmac, struct, time as _time

    def _b64url(data):
        if isinstance(data, str):
            data = data.encode()
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    now_ts = int(_time.time())
    header  = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}))
    payload = _b64url(json.dumps({
        "iss": creds_data["client_email"],
        "scope": scope,
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now_ts,
        "exp": now_ts + 3600,
    }))
    signing_input = f"{header}.{payload}".encode()

    # Підпис через cryptography або fallback через subprocess openssl
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        private_key = serialization.load_pem_private_key(
            creds_data["private_key"].encode(), password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w") as f:
            f.write(creds_data["private_key"])
            pem_path = f.name
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", pem_path],
            input=signing_input, capture_output=True)
        signature = proc.stdout
        import os as _os; _os.unlink(pem_path)

    jwt_token = f"{header}.{payload}.{_b64url(signature)}"

    if _HAS_REQUESTS:
        resp = _requests.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token,
        }, timeout=15)
        resp.raise_for_status()
        return resp.json()["access_token"]
    else:
        import urllib.parse
        body = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt_token,
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token",
            data=body, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["access_token"]


def get_calendar():
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    now = datetime.now(timezone.utc)
    date_today    = (now + timedelta(hours=2)).strftime("%d.%m.%Y")
    date_tomorrow = (now + timedelta(hours=26)).strftime("%d.%m.%Y")

    if not creds_json:
        return "📅 <b>Календар</b>\n⚠️ Не налаштовано"

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(
            creds_data, "https://www.googleapis.com/auth/calendar.readonly")

        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        # Часові межі
        today_start = (now + timedelta(hours=2)).replace(
            hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        today_end      = today_start + timedelta(hours=24)
        tomorrow_start = today_end
        tomorrow_end   = tomorrow_start + timedelta(hours=24)

        def fetch_events(t_min, t_max):
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
                f"?timeMin={urllib.parse.quote(t_min.isoformat())}"
                f"&timeMax={urllib.parse.quote(t_max.isoformat())}"
                f"&singleEvents=true&orderBy=startTime&maxResults=20"
            )
            if _HAS_REQUESTS:
                r = _requests.get(url, headers=headers, timeout=15)
                r.raise_for_status()
                return r.json().get("items", [])
            else:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read()).get("items", [])

        today_events    = fetch_events(today_start, today_end)
        tomorrow_events = fetch_events(tomorrow_start, tomorrow_end)

        def format_events(events):
            lines = []
            for ev in events:
                start   = ev["start"].get("dateTime") or ev["start"].get("date")
                summary = ev.get("summary", "(без назви)")
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    t  = (dt + timedelta(hours=0)).strftime("%H:%M")
                except Exception:
                    t = start
                lines.append(f"• {t} — <b>{esc(summary)}</b>")
            return lines

        result  = "📅 <b>КАЛЕНДАР</b>\n"
        result += f"<b>Сьогодні {date_today}:</b>\n"
        today_lines = format_events(today_events)
        result += "\n".join(today_lines) if today_lines else "Нічого не заплановано"

        result += f"\n\n<b>Завтра {date_tomorrow}:</b>\n"
        tomorrow_lines = format_events(tomorrow_events)
        result += "\n".join(tomorrow_lines) if tomorrow_lines else "Нічого не заплановано"

        return result

    except Exception as e:
        return f"📅 <b>Календар</b>\n⚠️ Помилка: {esc(str(e)[:120])}"


# ─── 4. EMAIL (Gmail API) ────────────────────────────────────────────────────

def decode_header_str(h):
    parts = email.header.decode_header(h or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def is_spam(sender, subject):
    s, sub = sender.lower(), subject.lower()
    return any(x in s for x in IGNORE_SENDERS) or any(x in sub for x in IGNORE_SUBJECTS)


def _gmail_access_token():
    """Отримує Gmail access token через refresh token."""
    client_id     = os.environ.get("GMAIL_CLIENT_ID", "878341164164-4qki4apv3mmo2s8006v9ks10q61sf5uk.apps.googleusercontent.com")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "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")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "1//06Fo6TgMdtzM6CgYIARAAGAYSNwF-L9IrUgnpTv2b_BQ8dszP9vpdAU5ejStbBW6CQ39FIvKOd-SIpOL_JPMC7cgxWV8dHJwJ8x8")
    if not all([client_id, client_secret, refresh_token]):
        return None
    try:
        if _HAS_REQUESTS:
            r = _requests.post("https://oauth2.googleapis.com/token", data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            }, timeout=15)
            r.raise_for_status()
            return r.json().get("access_token")
        else:
            body = urllib.parse.urlencode({
                "client_id":     client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type":    "refresh_token",
            }).encode()
            req = urllib.request.Request("https://oauth2.googleapis.com/token",
                data=body, method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("access_token")
    except Exception as e:
        print(f"Gmail token error: {e}")
        return None


def _gmail_list(token, label_ids, max_results=10, q=""):
    """Повертає список {id, threadId} повідомлень."""
    params = {"maxResults": max_results, "labelIds": label_ids}
    if q:
        params["q"] = q
    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages?" + urllib.parse.urlencode(
        [("labelIds", lid) for lid in label_ids] +
        ([("q", q)] if q else []) +
        [("maxResults", max_results)]
    )
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            return r.json().get("messages", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read()).get("messages", [])
    except Exception as e:
        print(f"Gmail list error ({label_ids}): {e}")
        return []


def _gmail_get(token, msg_id, fmt="metadata"):
    """Отримує один лист. fmt='metadata' або 'full'."""
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format={fmt}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            return r.json()
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
    except Exception as e:
        print(f"Gmail get error ({msg_id}): {e}")
        return None


def _extract_header(msg_data, name):
    for h in msg_data.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _extract_body_preview(msg_data, max_chars=120):
    """Витягує preview з Gmail API повідомлення (format=full)."""
    try:
        import html as _html

        def get_parts(payload):
            parts = []
            if payload.get("mimeType", "").startswith("multipart"):
                for p in payload.get("parts", []):
                    parts.extend(get_parts(p))
            else:
                parts.append(payload)
            return parts

        payload = msg_data.get("payload", {})
        all_parts = get_parts(payload)

        body = ""
        # Спочатку шукаємо text/plain
        for p in all_parts:
            if p.get("mimeType") == "text/plain":
                data = p.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                    break

        # Якщо немає — беремо text/html і конвертуємо в текст
        if not body:
            for p in all_parts:
                if p.get("mimeType") == "text/html":
                    data = p.get("body", {}).get("data", "")
                    if data:
                        html_body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                        # Видаляємо style/script блоки повністю
                        html_body = re.sub(r'<style[^>]*>.*?</style>', ' ', html_body, flags=re.DOTALL | re.IGNORECASE)
                        html_body = re.sub(r'<script[^>]*>.*?</script>', ' ', html_body, flags=re.DOTALL | re.IGNORECASE)
                        # Заміняємо теги на пробіли
                        body = re.sub(r'<[^>]+>', ' ', html_body)
                        break

        body = _html.unescape(body)
        body = re.sub(r'https?://\S+', '', body)
        body = re.sub(r'\{[^}]*\}', '', body)   # CSS блоки типу {color: red}
        body = re.sub(r'@[a-zA-Z-]+\s*\{[^}]*\}', '', body)  # @media etc
        body = re.sub(r'\[.*?\]', '', body)
        body = re.sub(r'(unsubscribe|відписатись|view in browser|view this post|click here).{0,60}', '', body, flags=re.IGNORECASE)
        body = re.sub(r'\s+', ' ', body).strip()

        if len(body) > max_chars:
            body = body[:max_chars].rsplit(' ', 1)[0] + "…"
        return body if body else "—"
    except:
        return "—"


def _parse_gmail_msg(msg_data, full=False):
    """Повертає (subject, sender_clean, preview, is_unread)."""
    subject = decode_header_str(_extract_header(msg_data, "Subject")) or "(no subject)"
    sender  = decode_header_str(_extract_header(msg_data, "From")) or ""
    sender_clean = re.sub(r'<.*?>', '', sender).strip().strip('"') or sender
    is_unread = "UNREAD" in msg_data.get("labelIds", [])
    preview = _extract_body_preview(msg_data) if full else (msg_data.get("snippet", "") or "—")
    if len(preview) > 120:
        preview = preview[:120].rsplit(' ', 1)[0] + "…"
    return subject, sender_clean, preview, is_unread


def _gemini_summarize(text, max_input=3000):
    """Робить короткий actionable summary через Gemini API."""
    api_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")
    if not api_key or not text or text == "—":
        return None
    try:
        text_trimmed = text[:max_input]
        prompt = (
            "Прочитай цей email і дай ДУЖЕ короткий опис (1 речення українською, макс 120 символів). "
            "Формат: якщо потрібна дія — почни з емодзі дії (⚠️ помилка, 📋 інфо, 💰 фінанси, ✅ підтвердження, 📩 відповідь потрібна). "
            "Тільки суть і що робити. Без 'Лист про', без 'Повідомлення про'.\n\nЛист:\n" + text_trimmed
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        summary = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return summary[:200]
    except Exception as e:
        print(f"Gemini summary error: {e}")
        return None


def format_email_item(subject, sender, preview, is_unread=False, ai_summary=None):
    mark = "🔴 " if is_unread else "   "
    lines = [
        f"┌─────────────────────",
        f"{mark}📨 <b>{esc(subject[:55])}</b>",
        f"    👤 <code>{esc(sender[:40])}</code>",
    ]
    if ai_summary:
        lines.append(f"    🤖 {esc(ai_summary)}")
    else:
        lines.append(f"    💬 {esc(preview[:110])}")
    lines.append("└─────────────────────")
    return "\n".join(lines)


def _imap_connect():
    """Підключення до Gmail через IMAP."""
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "zbzlkvxjspuekbuk")
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(GMAIL_USER, app_password)
    return mail

def _imap_decode_header(raw):
    """Декодує email заголовок."""
    parts = email.header.decode_header(raw or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(enc or "utf-8", errors="replace"))
            except Exception:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)

def _imap_get_body(msg):
    """Витягує текст листа (plain або з HTML)."""
    import re as _re
    body = ""
    if msg.is_multipart():
        # Спочатку шукаємо text/plain
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                try:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace")
                    break
                except Exception:
                    pass
        # Якщо plain не знайшли — беремо HTML і стрипаємо теги
        if not body.strip():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition", ""))
                if ct == "text/html" and "attachment" not in cd:
                    try:
                        html = part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", errors="replace")
                        body = _re.sub(r'<[^>]+>', ' ', html)
                        body = _re.sub(r'\s+', ' ', body).strip()
                        break
                    except Exception:
                        pass
    else:
        try:
            raw = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                body = _re.sub(r'<[^>]+>', ' ', raw)
                body = _re.sub(r'\s+', ' ', body).strip()
            else:
                body = raw
        except Exception:
            pass
    return body[:3000]

def get_emails():
    try:
        mail = _imap_connect()
        mail.select("INBOX")

        # UID-based пошук (правильно — sequence numbers не persistent між сесіями)
        # Беремо: непрочитані primary + останні 15 прочитаних primary
        _, p_unseen = mail.uid('search', None, 'X-GM-RAW "category:primary is:unread"')
        _, p_all    = mail.uid('search', None, 'X-GM-RAW "category:primary"')

        primary_unread_uids = set(u.decode() for u in p_unseen[0].split())
        primary_all_uids    = [u.decode() for u in p_all[0].split()]

        # Якщо primary порожній — беремо всі UNSEEN як fallback
        if not primary_all_uids:
            _, fallback = mail.uid('search', None, 'UNSEEN')
            primary_all_uids = [u.decode() for u in fallback[0].split()]
            primary_unread_uids = set(primary_all_uids)

        # Об'єднуємо: всі непрочитані + останні 15 прочитаних, від нових до старих
        combined = list(dict.fromkeys(
            list(primary_unread_uids) + primary_all_uids[-15:]
        ))
        combined = sorted(combined, key=lambda x: int(x))[::-1]

        # Мінімальний чорний список — тільки явні системні нотифікації
        # (YouTube, Duolingo, Maps тощо що Gmail іноді кладе в Primary)
        _ALWAYS_SKIP = {
            "noreply@youtube.com", "no-reply@youtube.com",
            "no-reply@accounts.google.com", "noreply-maps-timeline@google.com",
            "hello@duolingo.com", "no-reply@duolingo.com",
            "no-reply@medium.com",
        }

        primary = []

        for uid in combined:
            if len(primary) >= 7:
                break
            _, msg_data = mail.uid('fetch', uid.encode(), "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject  = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender   = _imap_decode_header(msg.get("From", ""))
            is_unread = uid in primary_unread_uids

            # Пропускаємо тільки явні системні нотифікації
            email_match = re.search(r'[\w.+%-]+@[\w.-]+\.[a-z]{2,}', sender.lower())
            email_addr = email_match.group(0) if email_match else ""
            if email_addr in _ALWAYS_SKIP:
                continue

            body    = _imap_get_body(msg)
            preview = body[:120].replace("\n", " ").strip()
            # AI summary для всіх листів — читає і дає суть
            ai_sum  = _gemini_summarize(body) if body else ""
            primary.append((subject, sender, preview, is_unread, ai_sum))

        mail.logout()

        lines = ["📩 <b>━━━ ЛИСТИ ━━━</b>\n"]

        if primary:
            unread_count = sum(1 for _, _, _, u, _ in primary if u)
            header = "📥 <b>ОСНОВНІ</b>" + (f"  🔴 {unread_count} нових" if unread_count else "")
            lines.append(header)
            for s, snd, p, u, ai_sum in primary:
                lines.append(format_email_item(s, snd, p, u, ai_summary=ai_sum))
        else:
            lines.append("✅ Немає листів")

        return "\n".join(lines)

    except Exception as e:
        print(f"get_emails IMAP error: {e}")
        return f"📬 <b>Email</b>\n⚠️ Помилка: {e}"


# ─── 4b. МИТТЄВІ СПОВІЩЕННЯ ПРО НОВІ ЛИСТИ ───────────────────────────────────

ALERT_EMAIL_FILE = os.path.join(_DATA_DIR, "monitor_alert_emails.json")

_SKIP_EMAILS = {
    "noreply@youtube.com", "no-reply@youtube.com",
    "no-reply@accounts.google.com", "noreply-maps-timeline@google.com",
    "hello@duolingo.com", "no-reply@duolingo.com", "no-reply@medium.com"
}

def _email_sent_ids():
    """Повертає set вже надісланих IMAP UID (з GitHub — persistent)."""
    try:
        import storage as _st
        data = _st.load("monitor_alert_emails.json", default={})
        return set(str(x) for x in data.get("sent_ids", []))
    except Exception:
        return set()

def _email_save_ids(sent_ids: set):
    """Зберігає sent UID в GitHub. Тримає останні 1000."""
    try:
        import storage as _st
        lst = sorted(int(x) for x in sent_ids if str(x).isdigit())[-1000:]
        _st.save("monitor_alert_emails.json", {"sent_ids": [str(x) for x in lst]})
    except Exception as e:
        print(f"_email_save_ids error: {e}")

def check_new_emails():
    """Перевіряє непрочитані Primary листи — шле сповіщення ОДИН РАЗ на кожен лист (dedup по UID)."""
    try:
        mail = _imap_connect()
        mail.select("INBOX")

        # UID-based пошук (sequence numbers не persistent між IMAP сесіями!)
        _, data = mail.uid('search', None, 'X-GM-RAW "category:primary is:unread"')
        all_unread = data[0].split()

        if not all_unread:
            mail.logout()
            return

        # Завантажуємо вже надіслані з GitHub
        sent_ids = _email_sent_ids()

        # Фільтруємо тільки нові (не бачені) — беремо останні 20
        new_uids = [u for u in all_unread[-20:] if u.decode() not in sent_ids]

        if not new_uids:
            mail.logout()
            return

        to_alert = []
        newly_seen = set()

        for uid in new_uids:
            uid_str = uid.decode()
            _, msg_data = mail.uid('fetch', uid, "(RFC822.HEADER)")
            if not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])

            subject = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender  = _imap_decode_header(msg.get("From", ""))

            em = re.search(r'[\w.+%-]+@[\w.-]+\.[a-z]{2,}', sender.lower())
            ea = em.group(0) if em else ""

            newly_seen.add(uid_str)  # позначаємо як бачений незалежно від _SKIP
            if ea not in _SKIP_EMAILS:
                to_alert.append((subject, sender))

        mail.logout()

        # Надсилаємо і одразу зберігаємо stateв GitHub
        if newly_seen:
            sent_ids.update(newly_seen)
            _email_save_ids(sent_ids)

        for subject, sender in to_alert:
            _send_telegram_photo(
                "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2FTPPUfhLfZwJmqMoezuxQM%2Fmail_banner_v2.png",
                f"📩 <b>━━ НОВИЙ ЛИСТ ━━</b>\n\n"
                f"📨 <b>{esc(subject[:70])}</b>\n"
                f"👤 <code>{esc(sender[:55])}</code>"
            )
            print(f"Email alert sent: {subject[:50]}")

    except Exception as e:
        print(f"check_new_emails error: {e}")


# ─── 4c. ПОГОДНІ АЛЕРТИ ───────────────────────────────────────────────────────

WEATHER_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_weather_alert.json")

def check_weather_alert():
    """
    Щовечора (~20:00 місцевого) перевіряє погоду на завтра.
    Якщо очікується дощ/гроза/сніг — шле сповіщення.
    Також: якщо зараз різка зміна погоди (>5° за 3г) — миттєве сповіщення.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=48.7163&longitude=21.2611"
        "&current=temperature_2m,weathercode,precipitation"
        "&daily=weathercode,precipitation_sum,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&forecast_days=2&timezone=Europe%2FPrague"
    )
    data = fetch_json(url)
    if not data:
        return

    state = load_json_file(WEATHER_ALERT_FILE, default={})
    alerts = []

    WMO_BAD = {
        51, 53, 55, 61, 63, 65, 71, 73, 75, 77,
        80, 81, 82, 85, 86, 95, 96, 99
    }
    WMO_LABEL = {
        51: "🌦 Мряка", 53: "🌦 Мряка", 55: "🌦 Мряка",
        61: "🌧 Дощ", 63: "🌧 Дощ", 65: "🌧 Сильний дощ",
        71: "❄️ Сніг", 73: "❄️ Сніг", 75: "❄️ Сильний сніг",
        80: "🌦 Злива", 81: "🌦 Злива", 82: "⛈ Сильна злива",
        95: "⛈ Гроза", 96: "⛈ Гроза з градом", 99: "⛈ Сильна гроза",
    }

    # ── Вечірній алерт про завтра (шлемо між 19:00-21:00) ──
    if 19 <= now_local.hour < 21:
        today_str = now_local.strftime("%Y-%m-%d")
        last_evening = state.get("last_evening_alert", "")
        if last_evening != today_str:
            daily = data.get("daily", {})
            times = daily.get("time", [])
            codes = daily.get("weathercode", [])
            precip = daily.get("precipitation_sum", [])
            precip_prob = daily.get("precipitation_probability_max", [])
            tmax = daily.get("temperature_2m_max", [])
            tmin = daily.get("temperature_2m_min", [])

            # Завтра = індекс 1
            if len(times) > 1:
                code = codes[1] if len(codes) > 1 else 0
                pr   = precip[1] if len(precip) > 1 else 0
                prob = precip_prob[1] if len(precip_prob) > 1 else 0
                hi   = tmax[1] if len(tmax) > 1 else None
                lo   = tmin[1] if len(tmin) > 1 else None
                tomorrow_date = (now_local + timedelta(days=1)).strftime("%d.%m")

                if code in WMO_BAD or prob >= 50:
                    label = WMO_LABEL.get(code, "🌧 Опади")
                    temp_str = f"{lo:.0f}…{hi:.0f}°C" if hi and lo else ""
                    msg = (
                        f"🌦 <b>Погода на завтра ({tomorrow_date})</b>\n"
                        f"{label}"
                        + (f", {prob}% імовірність опадів" if prob else "")
                        + (f", {pr:.1f} мм" if pr > 0 else "")
                        + (f"\n🌡 {temp_str}" if temp_str else "")
                        + "\n\n☔ Не забудь парасольку!"
                    )
                    alerts.append(msg)
                    state["last_evening_alert"] = today_str

    # ── Різка зміна температури (>6° за останні 3г) ──
    current = data.get("current", {})
    temp_now = current.get("temperature_2m")
    if temp_now is not None:
        last_temp = state.get("last_temp")
        last_temp_time = state.get("last_temp_time", "")
        now_str = now_local.strftime("%Y-%m-%d %H")

        if last_temp is not None and last_temp_time != now_str:
            diff = temp_now - last_temp
            if abs(diff) >= 6:
                direction = "впала" if diff < 0 else "піднялась"
                alerts.append(
                    f"🌡 <b>Різка зміна температури!</b>\n"
                    f"Температура {direction} на {abs(diff):.0f}°C за 3г\n"
                    f"Зараз: {temp_now:.0f}°C"
                )

        state["last_temp"] = temp_now
        state["last_temp_time"] = now_str

    save_json_file(WEATHER_ALERT_FILE, state)

    for msg in alerts:
        send_telegram(msg)
        print(f"Weather alert sent: {msg[:60]}")


# ─── 4d. КРИПТО НОВИНИ ────────────────────────────────────────────────────────

CRYPTO_NEWS_FILE = os.path.join(_DATA_DIR, "monitor_crypto_news.json")

def _translate_ua(text):
    """Перекладає текст на українську через Google Translate (без ключа)."""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = "client=gtx&sl=en&tl=uk&dt=t&q=" + urllib.parse.quote(text)
        data = fetch_json(url + "?" + params)
        if data and data[0]:
            return "".join([s[0] for s in data[0] if s and s[0]])
    except Exception as e:
        print(f"translate error: {e}")
    return text  # fallback — оригінал


def check_crypto_news():
    """
    Раз на 4 години перевіряє топ новини з CoinGecko News.
    Шле нові важливі новини в Telegram.
    """
    state = load_json_file(CRYPTO_NEWS_FILE, default={"sent": [], "last_check": ""})

    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    now_str   = now_local.strftime("%Y-%m-%d %H")
    last      = state.get("last_check", "")

    # Не частіше ніж раз на 4г
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_dt).total_seconds() < 4 * 3600:
            return
    except Exception:
        pass

    # CoinGecko News API (безкоштовно, без ключа)
    data = fetch_json("https://api.coingecko.com/api/v3/news?page=1")

    sent     = set(state.get("sent", []))
    new_news = []

    if data:
        items = data.get("data", [])[:10]
        for item in items:
            nid   = str(item.get("id", ""))
            title = item.get("title", "")
            url_  = item.get("url", "")
            if not nid or nid in sent:
                continue
            sent.add(nid)
            new_news.append((title, url_))

    if new_news:
        lines = []
        for title, url_ in new_news[:5]:
            translated = _translate_ua(title)
            lines.append(f"• <a href='{url_}'>{esc(translated[:100])}</a>")
        msg = "📰 <b>Крипто новини</b>\n" + "\n".join(lines)
        send_telegram(msg)
        print(f"Crypto news sent: {len(new_news)} items")

    state["sent"]       = list(sent)[-300:]
    state["last_check"] = now_str
    save_json_file(CRYPTO_NEWS_FILE, state)

    _check_fear_greed()


def _check_fear_greed():
    """Шле Fear & Greed якщо екстремальне значення (< 20 або > 80)."""
    state = load_json_file(CRYPTO_NEWS_FILE, default={})
    last_fg = state.get("last_fg_date", "")
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    if last_fg == today:
        return

    data = fetch_json("https://api.alternative.me/fng/?limit=1")
    if not data:
        return

    try:
        value = int(data["data"][0]["value"])
        label = data["data"][0]["value_classification"]
    except Exception:
        return

    if value <= 20:
        emoji = "😱"
        msg = f"{emoji} <b>Fear &amp; Greed: {value} — {esc(label)}</b>\nРинок в екстремальному страху. Можливо час купувати?"
    elif value >= 80:
        emoji = "🤑"
        msg = f"{emoji} <b>Fear &amp; Greed: {value} — {esc(label)}</b>\nРинок в екстремальній жадібності. Будь обережний."
    else:
        return  # Нормальне значення — не шлемо

    send_telegram(msg)
    state["last_fg_date"] = today
    save_json_file(CRYPTO_NEWS_FILE, state)


# ─── 5. ПІДСУМОК ТА РЕКОМЕНДАЦІЇ ─────────────────────────────────────────────

def _get_run_recommendation(weather_text):
    """Рекомендація бігти сьогодні."""
    import re as _re
    XML = "/tmp/health_export/apple_health_export/export.xml"
    last_run_days = None
    try:
        with open(XML, "r", encoding="utf-8", errors="replace") as f:
            xml_content = f.read()
        now_utc = datetime.now(timezone.utc)
        run_dates = []
        for line in xml_content.split("\n"):
            if "HKWorkoutActivityTypeRunning" not in line:
                continue
            m2 = _re.search('startDate="([^"]+)"', line)
            if m2:
                s = m2.group(1).strip()
                # parse datetime safely
                import re as re2
                s2 = re2.sub(r" ([+-][0-9]{4})$", r"\1", s).replace(" ", "T", 1)
                try:
                    dt = datetime.fromisoformat(s2).astimezone(timezone.utc)
                    run_dates.append(dt)
                except Exception:
                    pass
        if run_dates:
            last_run_days = (now_utc - max(run_dates)).days
    except Exception as e:
        print(f"run recommendation error: {e}")

    bad_weather = any(x in weather_text.lower() for x in ["гроза", "сильний дощ", "сніг", "злива"])
    if last_run_days is None:
        return "🏃 Даних про пробіжки немає — саме час вийти!"
    elif last_run_days == 0:
        return "🏃 Сьогодні вже бігав — молодець! 💪"
    elif last_run_days <= 2:
        return f"🏃 {last_run_days} дн. без бігу — гарний момент вийти!"
    else:
        if bad_weather:
            return f"🏃 {last_run_days} днів без бігу... Погода не дуже, але ліньки гірше 😄"
        return f"🏃 <b>{last_run_days} днів без бігу!</b> Сьогодні — обов\'язково! 💨"


def get_summary(prices_text, weather_text, calendar_text):
    tips = []
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)

    if "дощ" in weather_text.lower() or "злива" in weather_text.lower():
        tips.append("☔ Візьми парасольку")
    if "гроза" in weather_text.lower():
        tips.append("⛈ Уникай відкритих місць — гроза")
    if "сніг" in weather_text.lower():
        tips.append("🧥 Одягнись тепліше — очікується сніг")
    if "туман" in weather_text.lower():
        tips.append("🚗 Обережно на дорозі — туман")

    run_rec = _get_run_recommendation(weather_text)
    if run_rec:
        tips.append(run_rec)

    if "🔻" in prices_text:
        tips.append("📉 Крипторинок падає — слідкуй за портфелем")
    if "🔺" in prices_text:
        tips.append("📈 Крипторинок росте")

    h = now_local.hour
    if 6 <= h < 10:
        tips.append("☕ Доброго ранку! Гарного дня")
    elif 12 <= h < 14:
        tips.append("🍽 Час обіду")
    elif 18 <= h < 21:
        tips.append("🌆 Гарного вечора")
    elif h >= 22 or h < 6:
        tips.append("😴 Пізно — час відпочивати")

    if "нічого не заплановано" not in calendar_text.lower():
        tips.append("📌 Перевір заплановані події на сьогодні")

    if not tips:
        tips.append("✅ Все спокійно")

    return "💡 <b>ПІДСУМОК</b>\n" + "\n".join(tips)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def get_city_traffic():
    """Ситуація на дорогах Košice через TomTom — інциденти."""
    try:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from traffic_kosice import format_traffic_report
        return format_traffic_report()
    except Exception as e:
        print(f"Traffic error: {e}")
        return None


# ─── MAIN ─────────────────────────────────────────────────────────────────────

MAIN_SENT_FILE = os.path.join(_DATA_DIR, "monitor_main_sent.json")

def _gh_get_sent():
    """Читає monitor_main_sent.json з GitHub (shared між усіма інстансами)."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        return None, None
    url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/monitor_main_sent.json"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {gh_token}",
        "User-Agent": "morning-report-bot"
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
            content = json.loads(base64.b64decode(d["content"]).decode())
            return content, d["sha"]
    except Exception:
        return {}, None

def _gh_save_sent(data, sha):
    """Зберігає monitor_main_sent.json на GitHub."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        return
    url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/monitor_main_sent.json"
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
    body = json.dumps({
        "message": "dedup: mark hour sent",
        "content": content,
        **({"sha": sha} if sha else {})
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"token {gh_token}",
        "Content-Type": "application/json",
        "User-Agent": "morning-report-bot"
    }, method="PUT")
    try:
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print(f"_gh_save_sent error: {e}")

def main():
    now = datetime.now(timezone.utc)
    now_local = now + timedelta(hours=2)
    hour_key = now_local.strftime("%Y-%m-%dT%H")

    # Захист від дублів — GitHub як shared state (працює при кількох інстансах)
    gh_sent, gh_sha = _gh_get_sent()
    if gh_sent is not None:
        if gh_sent.get("last_hour") == hour_key:
            print(f"=== Already sent this hour ({hour_key}) [GitHub], skipping ===")
            return
        gh_sent["last_hour"] = hour_key
        _gh_save_sent(gh_sent, gh_sha)
    else:
        # Fallback — local file
        _sent = load_json_file(MAIN_SENT_FILE, default={})
        if _sent.get("last_hour") == hour_key:
            print(f"=== Already sent this hour ({hour_key}) [local], skipping ===")
            return
        _sent["last_hour"] = hour_key
        save_json_file(MAIN_SENT_FILE, _sent)

    local_time = now_local.strftime("%H:%M")
    local_date = now_local.strftime("%d.%m.%Y")
    weekday = now_local.weekday()  # 0=Пн, 5=Сб, 6=Нд
    local_hour = now_local.hour

    # У вихідні (Сб/Нд) — навчання/крипто/пошта тільки з 11:00
    is_weekend = weekday >= 5
    include_learning_blocks = (not is_weekend) or (local_hour >= 11)

    print(f"=== Monitor run at {now.isoformat()} (weekend={is_weekend}, include_learning={include_learning_blocks}) ===")

    prices_text  = get_prices() if include_learning_blocks else None
    weather_text = get_weather()
    cal_text     = get_calendar()
    email_text   = get_emails() if include_learning_blocks else None
    traffic_text = get_city_traffic()
    summary_text = get_summary(prices_text or "", weather_text, cal_text)

    SEP = "\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"

    parts = [f"🕐 <b>Звіт {local_time}  ·  {local_date}</b>\n<i>Годинний звіт</i>"]

    if prices_text:
        parts.append(prices_text)

    parts.append(weather_text)

    if traffic_text:
        parts.append(traffic_text)

    parts.append(cal_text)

    if email_text:
        parts.append(email_text)

    if is_weekend and not include_learning_blocks:
        parts.append("💤 <i>Вихідний — крипто/пошта/навчання з 11:00</i>")

    parts.append(summary_text)

    report = SEP.join(parts)

    send_telegram(report)
    print("=== Report sent ===")


# ─── 4c. НАГАДУВАННЯ ПРО ПОДІЇ КАЛЕНДАРЯ (за 30 хв) ──────────────────────────

CALENDAR_REMINDED_FILE = os.path.join(_DATA_DIR, "monitor_calendar_reminded.json")

def check_calendar_reminders():
    """Шле нагадування за 1 годину до старту кожної події в Google Calendar."""
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    reminded = set(load_json_file(CALENDAR_REMINDED_FILE, default=[]))

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(
            creds_data, "https://www.googleapis.com/auth/calendar.readonly")

        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now = datetime.now(timezone.utc)
        window_start = now + timedelta(minutes=58)
        window_end   = now + timedelta(minutes=62)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(window_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(window_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=10"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        new_reminded = list(reminded)
        for ev in events:
            ev_id   = ev.get("id", "")
            summary = ev.get("summary", "(без назви)")
            start   = ev["start"].get("dateTime") or ev["start"].get("date")
            reminder_key = f"1h_{ev_id}_{start}"

            if reminder_key in reminded:
                continue

            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                local_dt = dt.astimezone(timezone(timedelta(hours=2)))
                t = local_dt.strftime("%H:%M")
            except Exception:
                t = start
                dt = None

            # Пропускаємо якщо подія вже минула (захист від повторів після redeploy)
            if dt is not None and dt < datetime.now(timezone.utc):
                print(f"Skipping past event: {summary} at {t}")
                new_reminded.append(reminder_key)
                continue

            s_lower = summary.lower()
            if "нічна" in s_lower:      emoji = "🌙"
            elif "рання" in s_lower:    emoji = "☀️"
            elif "birthday" in s_lower or "народження" in s_lower: emoji = "🎂"
            elif "зустріч" in s_lower or "meet" in s_lower:        emoji = "🤝"
            else:                       emoji = "📅"

            msg = f"{emoji} <b>Нагадування — через 1 годину:</b>\n<b>{esc(summary)}</b>\n🕐 Початок о <b>{t}</b>"
            send_telegram(msg)
            print(f"1h reminder sent: {summary} at {t}")
            new_reminded.append(reminder_key)

        save_json_file(CALENDAR_REMINDED_FILE, new_reminded[-500:])

    except Exception as e:
        print(f"check_calendar_reminders error: {e}")


if __name__ == "__main__":
    main()


# ─── НАГАДУВАННЯ ЗА 2Г ДО ЗМІНИ ─────────────────────────────────────────────

SHIFT_REMINDED_FILE = os.path.join(_DATA_DIR, "monitor_shift_reminded.json")

def check_shift_reminders():
    """Шле нагадування за 2 години до будь-якої події в Google Calendar."""
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    reminded = set(load_json_file(SHIFT_REMINDED_FILE, default=[]))

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now = datetime.now(timezone.utc)
        window_start = now + timedelta(hours=1, minutes=55)
        window_end   = now + timedelta(hours=2, minutes=5)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(window_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(window_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=20"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        new_reminded = list(reminded)
        for ev in events:
            summary = ev.get("summary", "(без назви)")
            ev_id = ev.get("id", "")
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            key   = f"2h_{ev_id}_{start}"
            if key in reminded:
                continue

            # визначаємо час
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                local_dt = dt.astimezone(timezone(timedelta(hours=2)))
                t = local_dt.strftime("%H:%M")
            except Exception:
                t = start

            # емодзі залежно від типу події
            s_lower = summary.lower()
            if "нічна" in s_lower:
                emoji = "🌙"
            elif "рання" in s_lower or "ранн" in s_lower:
                emoji = "☀️"
            elif "день народження" in s_lower or "birthday" in s_lower:
                emoji = "🎂"
            elif "зустріч" in s_lower or "meet" in s_lower:
                emoji = "🤝"
            else:
                emoji = "📅"

            msg = (
                f"{emoji} <b>Нагадування — через 2 години:</b>\n"
                f"<b>{esc(summary)}</b>\n"
                f"🕐 Початок о <b>{t}</b>"
            )
            send_telegram(msg)
            print(f"2h reminder sent: {summary} at {t}")
            new_reminded.append(key)

        save_json_file(SHIFT_REMINDED_FILE, new_reminded[-500:])

    except Exception as e:
        print(f"check_shift_reminders error: {e}")


# ─── РАНКОВИЙ БРИФІНГ (7:00 у вихідні) ───────────────────────────────────────

MORNING_BRIEF_FILE = os.path.join(_DATA_DIR, "monitor_morning_brief.json")

def check_morning_brief():
    """
    🌅 MEGA РАНКОВИЙ БРИФІНГ — о 07:00 щодня (адаптується до типу дня).
    Містить: привітання + тип дня, погода, крипто dashboard,
             статус звичок вчора, вага (графік 7 днів), AI порада.
    """
    import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")
    yesterday = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")

    state = load_json_file(MORNING_BRIEF_FILE, default={})
    if state.get("last") == today:
        return

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
        tomorrow_shift = shift_info.get("tomorrow", "free")
    except Exception:
        shift = "free"
        tomorrow_shift = "free"

    # Тригер: вихідний/нічна о 07:00, рання зміна о 05:00
    if shift == "early":
        trigger_h = 5
    else:
        trigger_h = 7

    if not (h == trigger_h and 0 <= m < 10):
        return

    DAY_UA = ["Понеділок","Вівторок","Середа","Четвер","П'ятниця","Субота","Неділя"]
    day_name = DAY_UA[now_local.weekday()]

    # ── Заголовок ───────────────────────────────────────────────────────────
    if shift == "early":
        header = f"☀️ <b>Доброго ранку, {day_name}!</b>\n💼 Рання зміна — виходити о 05:30 → Вихід вже скоро!"
        mood = "⚡️ Енергійного робочого дня!"
    elif shift == "night":
        header = f"🌙 <b>Доброго ранку, {day_name}!</b>\n🔴 Нічна зміна — виходити о 17:30 → є час відпочити"
        mood = "😴 Відпочинь перед ніччю — збережи сили!"
    else:
        header = f"🌅 <b>Доброго ранку, {day_name}!</b>\n🏖 Вихідний — твій день, використай добре!"
        mood = "💪 Продуктивного та приємного дня!"

    lines_out = [header, ""]

    # ── Погода ──────────────────────────────────────────────────────────────
    try:
        WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY","")
        CITY = "Kosice"
        if WEATHER_API_KEY:
            url_w = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={WEATHER_API_KEY}&units=metric&lang=uk"
            req_w = urllib.request.Request(url_w, headers={"User-Agent":"bot"})
            with urllib.request.urlopen(req_w, timeout=8) as r:
                wd = json.loads(r.read())
            temp = round(wd["main"]["temp"])
            feels = round(wd["main"]["feels_like"])
            desc = wd["weather"][0]["description"]
            wind = wd["wind"]["speed"]
            humidity = wd["main"]["humidity"]
            # Емодзі за описом
            if any(x in desc for x in ["дощ","злива"]): w_icon = "🌧"
            elif "гроза" in desc: w_icon = "⛈"
            elif any(x in desc for x in ["сніг","хурто"]): w_icon = "❄️"
            elif "хмар" in desc: w_icon = "☁️"
            elif "ясно" in desc or "сонячно" in desc: w_icon = "☀️"
            else: w_icon = "🌤"
            # Температурний рейтинг
            if temp >= 20: t_mood = "🔥 тепло"
            elif temp >= 10: t_mood = "😊 комфортно"
            elif temp >= 0: t_mood = "🧥 прохолодно"
            else: t_mood = "🥶 мороз"
            lines_out.append(f"{w_icon} <b>Погода</b> · {temp}°C ({t_mood}) · {desc}")
            lines_out.append(f"   💨 {wind} м/с · 💧 {humidity}% · відчувається {feels}°C")
            lines_out.append("")
    except Exception:
        pass

    # ── Крипто dashboard ────────────────────────────────────────────────────
    try:
        ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
        url_c = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&price_change_percentage=24h,7d"
        req_c = urllib.request.Request(url_c, headers={"User-Agent":"bot"})
        with urllib.request.urlopen(req_c, timeout=8) as r:
            raw_c = json.loads(r.read())
        data_c = {c["id"]: c for c in raw_c}
        sym_map = [("BTC","bitcoin"),("ETH","ethereum"),("AVAX","avalanche-2"),("ONDO","ondo-finance")]

        crypto_lines = []
        for sym, cid in sym_map:
            c = data_c.get(cid, {})
            price = c.get("current_price")
            ch24 = c.get("price_change_percentage_24h") or 0
            ch7  = c.get("price_change_percentage_7d_in_currency") or 0
            if price is None: continue
            arrow24 = "↑" if ch24 > 0 else "↓"
            arrow7  = "↑" if ch7  > 0 else "↓"
            sign24  = "+" if ch24 > 0 else ""
            sign7   = "+" if ch7  > 0 else ""
            icon    = "🟢" if ch24 > 0 else "🔴"
            # Мини-бар по 24h (від -5% до +5%)
            bar_val = max(-5, min(5, ch24))
            bar_pos = int((bar_val + 5) / 10 * 8)
            bar = "🟦" * bar_pos + "⬜" * (8 - bar_pos)
            crypto_lines.append(f"{icon} <b>{sym}</b> ${price:,.0f}  {arrow24}{sign24}{ch24:.1f}%  [{bar}]  7д:{sign7}{ch7:.1f}%")

        if crypto_lines:
            lines_out.append("💹 <b>Крипто</b>")
            lines_out.extend(crypto_lines)
            lines_out.append("")
    except Exception:
        pass

    # ── Звички вчора ────────────────────────────────────────────────────────
    try:
        from storage import load_habits as _lh
        habits_db = _lh()
        yest_habits = habits_db.get(yesterday, {})
        if yest_habits:
            HABIT_MAP = [("run","🏃","Біг"),("water","💧","Вода"),("shower","🚿","Душ"),("tea","🍵","Чай")]
            habit_parts = []
            for hid, hico, hname in HABIT_MAP:
                v = yest_habits.get(hid)
                mark = "✅" if v is True else ("❌" if v is False else "⬜")
                habit_parts.append(f"{hico}{mark}")
            lines_out.append(f"📊 <b>Вчора</b>  {'  '.join(habit_parts)}")
            lines_out.append("")
    except Exception:
        pass

    # ── Графік ваги (7 днів ASCII) ──────────────────────────────────────────
    try:
        from storage import load_weight as _lw
        wdata = _lw()
        if wdata:
            w_days = sorted(wdata.keys())[-7:]
            w_vals = [wdata[d] for d in w_days if wdata.get(d)]
            if len(w_vals) >= 2:
                w_min = min(w_vals) - 0.5
                w_max = max(w_vals) + 0.5
                w_range = w_max - w_min or 1
                bars = []
                for v in w_vals:
                    bar_h = int((v - w_min) / w_range * 5)
                    bar_h = max(1, min(5, bar_h))
                    blocks = ["⬜","🟦","🟦","🟩","🟩","🟨","🟧","🟥"]
                    bars.append(blocks[bar_h])
                trend = "↗️" if w_vals[-1] > w_vals[0] else ("↘️" if w_vals[-1] < w_vals[0] else "→")
                last_w = w_vals[-1]
                diff_goal = round(last_w - 78.0, 1)
                goal_str = f"до цілі: -{diff_goal} кг" if diff_goal > 0 else "✅ ЦІЛЬ ДОСЯГНУТА!"
                lines_out.append(f"⚖️ <b>Вага</b>  {last_w} кг  {trend}  ({goal_str})")
                lines_out.append(f"   <code>{''.join(bars)}</code>  7 днів")
                lines_out.append("")
    except Exception:
        pass

    # ── AI порада на день ────────────────────────────────────────────────────
    gemini_key = os.environ.get("GEMINI_API_KEY","")
    if gemini_key:
        try:
            shift_labels = {"early":"рання зміна 06:00–18:00","night":"нічна зміна 18:00–06:00","free":"вихідний"}
            prompt = (
                f"Сьогодні {day_name}, {shift_labels.get(shift,'вихідний')}. "
                f"Олег (Кошіце). Дай ОДНУ конкретну actionable пораду на цей день — "
                f"здоров'я/схуднення (ціль 78 кг)/фінанси/крипто/саморозвиток. "
                f"1-2 речення, бадьоро, без загальних слів. Тільки конкретика."
            )
            payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":120,"temperature":0.85}}).encode()
            req_ai = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                data=payload, headers={"Content-Type":"application/json"}, method="POST"
            )
            with urllib.request.urlopen(req_ai, timeout=15) as r:
                resp = json.loads(r.read())
            ai_tip = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            lines_out.append(f"🤖 <i>{ai_tip}</i>")
            lines_out.append("")
        except Exception as e:
            print(f"morning brief AI error: {e}")

    lines_out.append(f"<i>{mood}</i>")

    send_telegram("\n".join(lines_out))
    state["last"] = today
    save_json_file(MORNING_BRIEF_FILE, state)
    print(f"Morning brief sent: {today} shift={shift}")



PROACTIVE_FILE = os.path.join(_DATA_DIR, "monitor_proactive.json")

def check_proactive_insights():
    """
    Ініціативні повідомлення на основі профілю Олега:
    - Перед/після змін на роботі
    - Мотивація у вільні дні
    - Тижневий підсумок (бігу, ваги)
    - Крипто тренди
    - Нагадування про цілі
    """
    now_utc  = datetime.now(timezone.utc)
    now_local = now_utc + timedelta(hours=2)  # CEST UTC+2 (Кошіце)
    h, m  = now_local.hour, now_local.minute
    dow   = now_local.weekday()  # 0=пн, 6=нд
    today = now_local.strftime("%Y-%m-%d")

    if not (0 <= m < 5):  # тільки на початку кожної години
        return

    state = load_json_file(PROACTIVE_FILE, default={})

    def already_sent(key):
        return state.get(key) == today

    def mark_sent(key):
        state[key] = today
        save_json_file(PROACTIVE_FILE, state)

    # ── Отримуємо календар на сьогодні і завтра ───────────────────────────────
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    today_events = []
    tomorrow_events = []
    if creds_json:
        try:
            creds_data = json.loads(creds_json)
            token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
            headers = {"Authorization": f"Bearer {token}"}
            cal_id = "novosadovoleg%40gmail.com"

            for offset, store in [(0, "today_events"), (1, "tomorrow_events")]:
                day = now_local + timedelta(days=offset)
                tmin = day.replace(hour=0, minute=0, second=0, microsecond=0)
                tmax = tmin + timedelta(hours=24)
                url = (
                    f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
                    f"?timeMin={urllib.parse.quote(tmin.isoformat()+'Z'.replace('+01:00Z','Z'))}"
                    f"&timeMax={urllib.parse.quote(tmax.isoformat()+'Z'.replace('+01:00Z','Z'))}"
                    f"&singleEvents=true&orderBy=startTime&maxResults=20"
                )
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    evs = json.loads(r.read()).get("items", [])
                if offset == 0:
                    today_events = evs
                else:
                    tomorrow_events = evs
        except Exception as e:
            print(f"proactive calendar error: {e}")

    def get_shift(events):
        """Повертає ('early'/'night'/None, start_dt)"""
        for ev in events:
            s = ev.get("summary", "").lower()
            if "рання" in s:
                start = ev["start"].get("dateTime","")
                try:
                    dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                    return ("early", dt)
                except: return ("early", None)
            if "нічна" in s:
                start = ev["start"].get("dateTime","")
                try:
                    dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                    return ("night", dt)
                except: return ("night", None)
        return (None, None)

    today_shift, today_shift_dt   = get_shift(today_events)
    tomorrow_shift, tomorrow_shift_dt = get_shift(tomorrow_events)

    # ── 1. Ранкове привітання (08:00, вільний день) ───────────────────────────
    if h == 8 and not today_shift and not already_sent("morning_free"):
        tomorrow_note = ""
        if tomorrow_shift == "early":
            tomorrow_note = "\n\n⚡️ Завтра рання зміна — лягай спати вчасно!"
        elif tomorrow_shift == "night":
            tomorrow_note = "\n\n🌙 Завтра нічна зміна — відпочинь вдень."

        weekday_names = ["Понеділок","Вівторок","Середа","Четвер","П'ятниця","Субота","Неділя"]
        day_name = weekday_names[dow]

        # Мотивація залежно від дня
        if dow == 0:  # Понеділок
            motivation = "💪 Новий тиждень — нові можливості! Сьогодні:\n• 📈 Навчання інвестиціям\n• 💹 Чек крипто та портфель\n• 🏃 Пробіжка якщо дозволяє погода"
        elif dow == 4:  # П'ятниця
            motivation = "🎉 П'ятниця! Заплануй активний вікенд:\n• 🏃 Пробіжка або спорт\n• 📊 Аналіз тижня — крипто та інвестиції\n• 😴 Відпочинок — теж інвестиція"
        elif dow == 6:  # Неділя
            motivation = "🌿 Неділя — день відновлення та планування:\n• 📝 Плани на тиждень\n• ⚖️ Зважся та запиши результат\n• 🧘 Відпочинок і підзарядка"
        else:
            motivation = "✨ Гарний день для розвитку:\n• 📈 Навчання + крипто-аналіз\n• 🏃 Фізична активність\n• 📚 Читання або курси"

        send_telegram(f"🌅 <b>Доброго ранку, {day_name}!</b>\n\n{motivation}{tomorrow_note}")
        mark_sent("morning_free")

    # ── 2. Нагадування перед ранньою зміною (04:00) ───────────────────────────
    if h == 4 and today_shift == "early" and not already_sent("pre_early_shift"):
        send_telegram(
            "⏰ <b>Рання зміна через 1 годину!</b>\n\n"
            "☀️ Час прокидатись — зміна о 05:00\n"
            "☕ Випий воду та сніданок\n"
            "🎯 Гарної зміни!"
        )
        mark_sent("pre_early_shift")

    # ── 3. Нагадування перед нічною зміною (15:00) ────────────────────────────
    if h == 15 and today_shift == "night" and not already_sent("pre_night_shift"):
        send_telegram(
            "🌙 <b>Нічна зміна через 2 години!</b>\n\n"
            "• Постарайся трохи відпочити\n"
            "• Поїж нормально перед зміною\n"
            "• Візьми перекус на ніч\n"
            "💪 Гарної зміни!"
        )
        mark_sent("pre_night_shift")

    # ── 4. Після нічної зміни (06:00 — зміна закінчується о 05:00) ───────────
    # Перевіряємо чи вчора була нічна зміна
    yesterday = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
    if h == 6 and not already_sent("post_night_shift"):
        # Перевіряємо вчорашній календар
        try:
            creds_data = json.loads(creds_json) if creds_json else None
            if creds_data:
                token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
                yest = now_local - timedelta(days=1)
                tmin = yest.replace(hour=0,minute=0,second=0,microsecond=0)
                tmax = tmin + timedelta(hours=24)
                url = (
                    f"https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events"
                    f"?timeMin={urllib.parse.quote(tmin.isoformat())}"
                    f"&timeMax={urllib.parse.quote(tmax.isoformat())}"
                    f"&singleEvents=true&maxResults=10"
                )
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    yest_events = json.loads(r.read()).get("items", [])
                had_night = any("нічна" in e.get("summary","").lower() for e in yest_events)
                if had_night:
                    send_telegram(
                        "😴 <b>Нічна зміна завершена!</b>\n\n"
                        "Час додому і відпочивати 🛏\n"
                        "• Поїж і лягай спати\n"
                        "• Усі справи — після пробудження\n\n"
                        "Добре відпочинь! 💤"
                    )
                    mark_sent("post_night_shift")
        except Exception as e:
            print(f"post_night check error: {e}")

    # ── 5. Тижневий підсумок ваги (неділя 20:00) ─────────────────────────────
    if dow == 6 and h == 20 and not already_sent("weekly_weight"):
        try:
            weight_data = load_json_file(os.path.join(_DATA_DIR, "weight.json"), default={})
            if weight_data:
                sorted_w = sorted(weight_data.items())
                last_entries = sorted_w[-7:]  # останні 7 записів
                if last_entries:
                    last_date, last_w = last_entries[-1]
                    first_date, first_w = sorted_w[0]
                    total_change = last_w - first_w
                    recent_change = last_w - last_entries[0][1] if len(last_entries) > 1 else 0
                    target = 78.0  # ціль
                    to_goal = last_w - target

                    trend = "📉" if recent_change < -0.2 else "📈" if recent_change > 0.2 else "➡️"
                    msg = (
                        f"⚖️ <b>Тижневий підсумок ваги</b>\n\n"
                        f"Поточна: <b>{last_w} кг</b> ({last_date})\n"
                        f"{trend} За тиждень: {recent_change:+.1f} кг\n"
                        f"До цілі (78 кг): <b>{to_goal:.1f} кг</b>\n\n"
                    )
                    if to_goal > 5:
                        msg += "💪 Тримай режим харчування та активність!"
                    elif to_goal > 2:
                        msg += "🎯 Вже близько! Продовжуй у тому ж темпі."
                    else:
                        msg += "🏆 Майже ціль! Ти молодець!"
                    send_telegram(msg)
                    mark_sent("weekly_weight")
        except Exception as e:
            print(f"weekly weight error: {e}")

    # ── 6. Мотивація до бігу (вт/чт/сб о 09:00 якщо вільний) ────────────────
    if h == 9 and dow in (1, 3, 5) and not today_shift and not already_sent("run_motivation"):
        day_names = {1:"вівторок", 3:"четвер", 5:"субота"}
        send_telegram(
            f"🏃 <b>Час для пробіжки!</b>\n\n"
            f"Сьогодні {day_names[dow]} — ідеальний день для бігу.\n"
            f"• 20-30 хвилин легкого бігу\n"
            f"• Потом — сніданок і навчання\n\n"
            f"Вперед! 💨"
        )
        mark_sent("run_motivation")

    # ── 7. Щопонеділковий огляд цілей (пн 09:00, вільний) ────────────────────
    if h == 9 and dow == 0 and not today_shift and not already_sent("monday_goals"):
        send_telegram(
            "🎯 <b>Понеділок — огляд цілей тижня</b>\n\n"
            "Нагадую твої основні цілі:\n"
            "💰 Фінансова незалежність — вчись щодня\n"
            "⚖️ Схуднення до 78 кг — слідкуй за харчуванням\n"
            "💼 Нова робота в інвестиціях — нетворкінг та розвиток\n"
            "🏃 Активний спосіб життя — біг та спорт\n\n"
            "Що зробиш цього тижня для кожної цілі? 💪"
        )
        mark_sent("monday_goals")


# ─── КРИПТО АЛЕРТ >5% ЗА ГОДИНУ ──────────────────────────────────────────────

CRYPTO_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_crypto_alert.json")

def check_crypto_price_alert():
    """Шле сповіщення якщо BTC/ETH/AVAX/ONDO змінились >5% за годину."""
    state = load_json_file(CRYPTO_ALERT_FILE, default={})
    now_str = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H")

    ids = ",".join(COINS.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_1h_change=true"
    data = fetch_json(url)
    if not data:
        return

    alerts = []
    for symbol, cg_id in COINS.items():
        price   = data.get(cg_id, {}).get("usd")
        change1h = data.get(cg_id, {}).get("usd_1h_change")
        if price is None or change1h is None:
            continue

        key = f"{cg_id}_{now_str}"
        if state.get(key):
            continue

        if abs(change1h) >= 5:
            arrow = "🚀" if change1h > 0 else "💥"
            sign  = "+" if change1h > 0 else ""
            alerts.append(
                f"{arrow} <b>{symbol}</b> {sign}{change1h:.1f}% за годину\n"
                f"   Ціна: <code>${price:,.2f}</code>"
            )
            state[key] = True

    if alerts:
        msg = "⚡ <b>Крипто алерт!</b>\n\n" + "\n".join(alerts)
        send_telegram(msg)
        print(f"Crypto price alert sent: {len(alerts)} coins")

    save_json_file(CRYPTO_ALERT_FILE, state)


# ─── СТАТИСТИКА ЗВИЧОК ЗА ТИЖДЕНЬ (щопонеділка 9:00) ─────────────────────────

HABIT_STATS_FILE = os.path.join(_DATA_DIR, "monitor_habit_stats.json")

def check_weekly_habit_stats():
    """
    📊 WEEKLY HABIT DASHBOARD — щопонеділка о 09:00.
    Красивий ASCII дашборд: стрік, відсотки, тренди, AI аналіз.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.weekday() == 0 and now_local.hour == 9 and now_local.minute < 5):
        return

    state = load_json_file(HABIT_STATS_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from storage import load_habits as _lh, load_weight as _lw
        data = _lh()
        if not data:
            return

        days7 = [(now_local - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        days_short = [(now_local - timedelta(days=i)).strftime("%a") for i in range(6, -1, -1)]
        days_short_ua = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"]
        # яким день тижня була 6 днів тому
        start_dow = (now_local - timedelta(days=6)).weekday()
        day_labels = [days_short_ua[(start_dow + i) % 7] for i in range(7)]

        HABITS = [
            ("run",    "🏃", "Біг"),
            ("water",  "💧", "Вода"),
            ("shower", "🚿", "Хол.душ"),
            ("tea",    "🍵", "Чай"),
        ]

        logs = data if isinstance(data, dict) else {}

        header_row = "  " + " ".join(f"{d:>2}" for d in day_labels)
        lines_out = []
        lines_out.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines_out.append(f"📊 <b>ТИЖНЕВИЙ ДАШБОРД</b>")
        lines_out.append(f"<code>{header_row}</code>")
        lines_out.append("")

        total_score = 0
        habit_scores = {}

        for hid, hico, hname in HABITS:
            row_marks = []
            count = 0
            streak = 0
            current_streak = 0
            for d in days7:
                v = logs.get(d, {}).get(hid)
                if v is True:
                    row_marks.append("✅")
                    count += 1
                    current_streak += 1
                    streak = max(streak, current_streak)
                elif v is False:
                    row_marks.append("❌")
                    current_streak = 0
                else:
                    row_marks.append("⬜")
                    current_streak = 0
            pct = int(count / 7 * 100)
            total_score += pct
            habit_scores[hid] = pct

            # Рейтинг
            if pct >= 86: grade = "🏆"
            elif pct >= 57: grade = "⭐️"
            elif pct >= 29: grade = "👍"
            else: grade = "💤"

            marks_str = " ".join(row_marks)
            lines_out.append(f"{hico} <b>{hname}</b> {count}/7 {grade}")
            lines_out.append(f"<code>  {marks_str}</code>")

        lines_out.append("")

        # Загальний рейтинг тижня
        avg_pct = total_score // len(HABITS) if HABITS else 0
        if avg_pct >= 85: week_grade = "🏆 ІДЕАЛЬНИЙ ТИЖДЕНЬ!"
        elif avg_pct >= 65: week_grade = "⭐️ Відмінний тиждень!"
        elif avg_pct >= 45: week_grade = "👍 Непоганий тиждень"
        elif avg_pct >= 25: week_grade = "😐 Середній тиждень"
        else: week_grade = "💤 Слабкий тиждень — наступний кращий!"

        # Заповненість смужки
        filled = int(avg_pct / 100 * 10)
        progress_bar = "🟩" * filled + "⬜" * (10 - filled)
        lines_out.append(f"<code>[{progress_bar}]</code> {avg_pct}%  {week_grade}")
        lines_out.append("")

        # Тренд ваги за тиждень
        try:
            wdata = _lw()
            if wdata:
                w_days_data = {d: wdata[d] for d in days7 if d in wdata}
                if len(w_days_data) >= 2:
                    sorted_keys = sorted(w_days_data.keys())
                    w_start = w_days_data[sorted_keys[0]]
                    w_end   = w_days_data[sorted_keys[-1]]
                    diff = round(w_end - w_start, 1)
                    to_goal = round(w_end - 78.0, 1)
                    trend = "↗️ +{:.1f} кг".format(diff) if diff > 0 else "↘️ {:.1f} кг".format(diff)
                    lines_out.append(f"⚖️ <b>Вага за тиждень:</b> {w_start}→{w_end} кг  {trend}")
                    if to_goal > 0:
                        lines_out.append(f"   🎯 До цілі 78 кг: ще -{to_goal} кг")
                    else:
                        lines_out.append(f"   🏆 Ціль 78 кг ДОСЯГНУТА!")
                    lines_out.append("")
        except Exception:
            pass

        # AI аналіз тижня
        gemini_key = os.environ.get("GEMINI_API_KEY","")
        if gemini_key:
            try:
                habit_summary = ", ".join([f"{hname}: {habit_scores[hid]}%" for hid,_,hname in HABITS])
                prompt = (
                    f"Аналіз тижня Олега: {habit_summary}. "
                    f"Загальний результат: {avg_pct}%. "
                    f"Дай 1-2 речення: що вийшло добре і що покращити наступного тижня. "
                    f"Конкретно, без загальних слів."
                )
                payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":150,"temperature":0.7}}).encode()
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type":"application/json"}, method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_analysis = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                lines_out.append(f"🤖 <i>{ai_analysis}</i>")
                lines_out.append("")
            except Exception as e:
                print(f"habit stats AI error: {e}")

        lines_out.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines_out.append("💪 Новий тиждень — новий шанс!")

        send_telegram("\n".join(lines_out))
        state["last"] = today
        save_json_file(HABIT_STATS_FILE, state)
        print("Weekly habit stats sent")

    except Exception as e:
        print(f"check_weekly_habit_stats error: {e}")


def check_water_reminder():
    """Нагадування пити воду кожні 2г у вихідні між 8:00 і 20:00."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    if not (8 <= h <= 20 and 0 <= m < 5):
        return
    if h % 2 != 0:
        return

    key = now_local.strftime("%Y-%m-%d-%H")

    # GitHub-dedup (захист від кількох інстансів)
    gh_sent, gh_sha = _gh_get_sent()
    gh_water_key = f"water_{key}"
    if gh_sent is not None:
        if gh_sent.get(gh_water_key):
            return
    else:
        # fallback local
        state = load_json_file(WATER_FILE, default={})
        if state.get(key):
            return

    # Якщо є зміна сьогодні — пропускаємо (check_smart_notifications шле воду на зміні)
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if creds_json:
        try:
            creds_data = json.loads(creds_json)
            token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
            headers = {"Authorization": f"Bearer {token}"}
            cal_id  = "novosadovoleg%40gmail.com"
            now_utc = datetime.now(timezone.utc)
            day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end   = day_start + timedelta(hours=24)
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
                f"?timeMin={urllib.parse.quote(day_start.isoformat())}"
                f"&timeMax={urllib.parse.quote(day_end.isoformat())}"
                f"&singleEvents=true&maxResults=10"
            )
            if _HAS_REQUESTS:
                r = _requests.get(url, headers=headers, timeout=10)
                events = r.json().get("items", [])
            else:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    events = json.loads(r.read()).get("items", [])
            if any("зміна" in e.get("summary","").lower() for e in events):
                return
        except:
            pass

    send_telegram("💧 <b>Час випити воду!</b>\nВипий склянку води зараз 🥤")
    print(f"Water reminder sent at {h}:00")

    # Зберігаємо в GitHub
    if gh_sent is not None:
        gh_sent[gh_water_key] = True
        _gh_save_sent(gh_sent, gh_sha)
    else:
        state = load_json_file(WATER_FILE, default={})
        state[key] = True
        save_json_file(WATER_FILE, state)


# ─── ПЛАН ТИЖНЯ (щопонеділка 8:00) ───────────────────────────────────────────

WEEK_PLAN_FILE = os.path.join(_DATA_DIR, "monitor_week_plan.json")

def check_weekly_plan():
    """Щопонеділка о 8:00 і щонеділі о 18:00 шле план на тиждень з календаря."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    is_monday_8  = (now_local.weekday() == 0 and now_local.hour == 8  and now_local.minute < 5)
    is_sunday_18 = (now_local.weekday() == 6 and now_local.hour == 18 and now_local.minute < 5)
    if not (is_monday_8 or is_sunday_18):
        return

    state = load_json_file(WEEK_PLAN_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    key = f"{today}_{'sun18' if is_sunday_18 else 'mon8'}"
    if state.get(key):
        return

    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now_utc = datetime.now(timezone.utc)
        week_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        week_end   = week_start + timedelta(days=7)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(week_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(week_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=50"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        DAY_UA = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"]
        by_day = {}
        for ev in events:
            summary = ev.get("summary","")
            if "нагадування" in summary.lower():
                continue
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            try:
                dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                d  = dt.strftime("%Y-%m-%d")
                t  = dt.strftime("%H:%M")
            except:
                continue
            by_day.setdefault(d, []).append(f"{t} {esc(summary)}")

        lines = ["📅 <b>План на тиждень:</b>\n"]
        for i in range(7):
            day = (now_local + timedelta(days=i))
            d_str = day.strftime("%Y-%m-%d")
            d_label = f"{DAY_UA[day.weekday()]} {day.strftime('%d.%m')}"
            evs = by_day.get(d_str, [])
            if evs:
                lines.append(f"<b>{d_label}</b>")
                for e in evs[:5]:
                    lines.append(f"  • {e}")
            else:
                lines.append(f"<b>{d_label}</b> — вихідний")

        send_telegram("\n".join(lines))
        state[key] = True
        save_json_file(WEEK_PLAN_FILE, state)
        print("Weekly plan sent")

    except Exception as e:
        print(f"check_weekly_plan error: {e}")

# ─── ПЕРЕВІРКА ВИКОНАНИХ ПОДІЙ ────────────────────────────────────────────────

EVENT_DONE_FILE = os.path.join(_DATA_DIR, "monitor_event_done.json")

def check_event_done():
    """Після закінчення події питає 'Виконано?' з кнопками Так/Ні."""
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    asked = set(load_json_file(EVENT_DONE_FILE, default=[]))

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id = "novosadovoleg%40gmail.com"

        now = datetime.now(timezone.utc)
        # Вікно: події що закінчились 0-10 хвилин тому
        window_start = now - timedelta(minutes=10)
        window_end   = now

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(window_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(window_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=20"
            f"&timeZone=UTC"
        )
        # Нам потрібні події за END time — тому беремо всі за ширше вікно і фільтруємо
        # Розширюємо: беремо події що почались до now і закінчуються у вікні
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote((now - timedelta(days=1)).isoformat())}"
            f"&timeMax={urllib.parse.quote(now.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=50"
        )

        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        new_asked = list(asked)
        for ev in events:
            ev_id   = ev.get("id", "")
            summary = ev.get("summary", "(без назви)")
            end_raw = ev["end"].get("dateTime") or ev["end"].get("date")
            if not end_raw or "T" not in end_raw:
                continue  # пропускаємо цілоденні події

            try:
                end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
            except Exception:
                continue

            # Перевіряємо що подія закінчилась 0-10 хв тому
            diff = (now - end_dt).total_seconds()
            if not (0 <= diff <= 600):
                continue

            key = f"done_{ev_id}_{end_raw}"
            if key in asked:
                continue

            # Надсилаємо питання з кнопками через Telegram Bot API напряму
            local_end = end_dt + timedelta(hours=2)
            t = local_end.strftime("%H:%M")

            s_lower = summary.lower()
            if "нічна" in s_lower:
                emoji = "🌙"
            elif "рання" in s_lower or "ранн" in s_lower:
                emoji = "☀️"
            elif "день народження" in s_lower or "birthday" in s_lower:
                emoji = "🎂"
            elif "зустріч" in s_lower or "meet" in s_lower:
                emoji = "🤝"
            else:
                emoji = "📅"

            text = (
                f"{emoji} <b>{esc(summary)}</b> закінчилась о {t}\n"
                f"Виконано?"
            )

            import urllib.request as _ur
            import urllib.parse as _up
            bot_token = os.environ.get("TELEGRAM_TOKEN", "8374312425:AAHqrQCEqrgtVdl5Te5WhWblM2ESCnqhpfk")
            chat_id_tg = os.environ.get("TELEGRAM_CHAT", "2100366814")
            safe_key = key.replace("/", "_").replace("@", "_")[:60]

            payload = json.dumps({
                "chat_id": chat_id_tg,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "✅ Виконано", "callback_data": f"evdone_yes_{safe_key}"},
                        {"text": "❌ Не виконано", "callback_data": f"evdone_no_{safe_key}"},
                    ]]
                }
            }).encode()

            req2 = _ur.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with _ur.urlopen(req2, timeout=15) as resp:
                resp.read()

            print(f"Event done question sent: {summary}")
            new_asked.append(key)

        save_json_file(EVENT_DONE_FILE, new_asked[-500:])

    except Exception as e:
        print(f"check_event_done error: {e}")

# ─── ПІДСУМОК ДНЯ ────────────────────────────────────────────────────────────

DAY_SUMMARY_FILE = os.path.join(_DATA_DIR, "monitor_day_summary.json")

def check_day_summary():
    """
    🌙 RICH DAY SUMMARY — о 21:00 щодня.
    Містить: підсумок звичок + графік дня, ліки, вага, Apple Health,
             AI персональний підсумок з рекомендацією.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    if not (h == 21 and m < 5):
        return

    state = load_json_file(DAY_SUMMARY_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    DAY_UA = ["Понеділок","Вівторок","Середа","Четвер","П'ятниця","Субота","Неділя"]
    day_name = DAY_UA[now_local.weekday()]

    lines_out = []
    lines_out.append(f"🌙 <b>ПІДСУМОК ДНЯ — {day_name} {now_local.strftime('%d.%m')}</b>")
    lines_out.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines_out.append("")

    # ── Звички з візуалізацією ───────────────────────────────────────────────
    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from storage import load_habits as _lh
        habits_db = _lh()
        today_habits = habits_db.get(today, {})
    except Exception:
        today_habits = {}

    HEALTH_HABITS = [
        ("run",    "🏃", "Біг"),
        ("water",  "💧", "Вода 2л+"),
        ("shower", "🚿", "Хол.душ"),
        ("tea",    "🍵", "Трав.чай"),
        ("sauna",  "🧖", "Сауна"),
    ]

    done_count = 0
    habit_lines = []
    for hid, hico, hname in HEALTH_HABITS:
        v = today_habits.get(hid)
        if v is True:
            mark = "✅"; done_count += 1
        elif v is False:
            mark = "❌"
        else:
            mark = "⬜"
        habit_lines.append(f"{hico} {hname}  {mark}")

    # Прогрес-бар звичок
    total_h = len(HEALTH_HABITS)
    filled = int(done_count / total_h * 10) if total_h else 0
    bar = "🟩" * filled + "⬜" * (10 - filled)
    pct = int(done_count / total_h * 100) if total_h else 0

    if pct == 100: grade = "🏆 Ідеальний день!"
    elif pct >= 80: grade = "⭐️ Відмінно!"
    elif pct >= 60: grade = "👍 Непогано"
    elif pct >= 40: grade = "😐 Середньо"
    else: grade = "💤 Слабо"

    lines_out.append(f"💪 <b>Звички</b>  <code>[{bar}]</code> {done_count}/{total_h}  {grade}")
    for hl in habit_lines:
        lines_out.append(f"   {hl}")

    # Сон вчора
    sleep_v = today_habits.get("sleep")
    if sleep_v:
        s_ico = "😴✅" if sleep_v >= 7.5 else ("😴⚠️" if sleep_v >= 6 else "😴❌")
        lines_out.append(f"   😴 Сон  {sleep_v}г  {s_ico}")
    lines_out.append("")

    # ── Ліки ────────────────────────────────────────────────────────────────
    try:
        from storage import load_meds as _lmeds
        meds_db = _lmeds()
        meds_taken = meds_db.get(today)
        if meds_taken is True:
            lines_out.append("💊 <b>Armolopid Plus</b>  ✅ Прийнято")
        elif meds_taken is False:
            lines_out.append("💊 <b>Armolopid Plus</b>  ❌ <b>НЕ ПРИЙНЯТО!</b>")
        else:
            lines_out.append("💊 <b>Armolopid Plus</b>  ⬜ Не відмічено — прийняв?")
        lines_out.append("")
    except Exception:
        pass

    # ── Вага + мінітренд ────────────────────────────────────────────────────
    try:
        from storage import load_weight as _lw
        wdata = _lw()
        if wdata:
            recent = sorted(wdata.keys())[-7:]
            w_recent = [wdata[d] for d in recent if wdata.get(d)]
            last_w = wdata.get(today)
            if last_w:
                diff_goal = round(last_w - 78.0, 1)
                goal_str = f"до 78 кг: -{diff_goal}" if diff_goal > 0 else "🏆 ЦІЛЬ!"
                # Тренд
                if len(w_recent) >= 2:
                    delta = round(w_recent[-1] - w_recent[-2], 1)
                    trend = f"↗️+{delta}" if delta > 0 else f"↘️{delta}"
                else:
                    trend = ""
                lines_out.append(f"⚖️ <b>Вага сьогодні:</b> <b>{last_w} кг</b>  {trend}  ({goal_str})")
            elif w_recent:
                last_d = recent[-1]
                days_ago = (now_local.date() - datetime.strptime(last_d, "%Y-%m-%d").date()).days
                lines_out.append(f"⚖️ <b>Вага:</b> {w_recent[-1]} кг  <i>({days_ago} дн. тому — зважся!)</i>")
            lines_out.append("")
    except Exception:
        pass

    # ── Apple Health ─────────────────────────────────────────────────────────
    try:
        from storage import load_health as _lhealth
        health_db = _lhealth()
        td = health_db.get(today, {})
        if td:
            h_parts = []
            steps = td.get("steps")
            if steps:
                step_goal = 10000
                s_pct = int(steps / step_goal * 100)
                step_bar_f = int(s_pct / 100 * 8)
                step_bar = "🟩" * step_bar_f + "⬜" * (8 - step_bar_f)
                step_ico = "✅" if steps >= step_goal else ("⚠️" if steps >= 6000 else "❌")
                h_parts.append(f"👟 {steps:,} кроків {step_ico} {step_bar}")
            if td.get("sleep_hours"):
                sh = td["sleep_hours"]
                sh_ico = "✅" if sh >= 7.5 else ("⚠️" if sh >= 6 else "❌")
                h_parts.append(f"😴 Сон {sh}г {sh_ico}")
            if td.get("heart_rate"):
                h_parts.append(f"❤️ ЧСС {td['heart_rate']} bpm")
            if td.get("hrv"):
                h_parts.append(f"💓 HRV {td['hrv']}")
            if td.get("calories"):
                cal = td["calories"]
                cal_ico = "✅" if cal >= 400 else "📉"
                h_parts.append(f"🔥 {cal} ккал {cal_ico}")
            sc = td.get("health_score")
            if sc:
                sc_bar = "🟢" * int(sc/100*10) + "⬜" * (10 - int(sc/100*10))
                sc_ico = "🟢" if sc >= 75 else ("🟡" if sc >= 55 else "🔴")
                h_parts.append(f"{sc_ico} Score {sc}/100 [{sc_bar}]")

            if h_parts:
                lines_out.append("🍎 <b>Apple Health</b>")
                for hp in h_parts:
                    lines_out.append(f"   {hp}")
                lines_out.append("")
        else:
            lines_out.append("🍎 <b>Apple Health</b>  <i>немає даних — /зд для запису</i>")
            lines_out.append("")
    except Exception:
        pass

    # ── AI персональний підсумок ──────────────────────────────────────────────
    gemini_key = os.environ.get("GEMINI_API_KEY","")
    if gemini_key:
        try:
            habit_summary = f"звички {done_count}/{total_h}"
            w_info = ""
            try:
                if wdata:
                    lw_keys = sorted(wdata.keys())
                    if lw_keys:
                        w_info = f", вага {wdata[lw_keys[-1]]} кг (ціль 78)"
            except Exception: pass

            prompt = (
                f"Олег сьогодні ({day_name}): {habit_summary}{w_info}. "
                f"Дай ДУЖЕ короткий вечірній підсумок (1-2 речення) + одна порада на завтра. "
                f"Тон: підтримуючий і конкретний."
            )
            payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":150,"temperature":0.8}}).encode()
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                data=payload, headers={"Content-Type":"application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read())
            ai_text = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            lines_out.append(f"🤖 <i>{ai_text}</i>")
            lines_out.append("")
        except Exception as e:
            print(f"day summary AI error: {e}")

    lines_out.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines_out.append("🌙 Гарного відпочинку!")

    send_telegram("\n".join(lines_out))
    print(f"Day summary sent: {today}")
    state["last"] = today
    save_json_file(DAY_SUMMARY_FILE, state)


def check_traffic_before_shift():
    """За 1 год до зміни надсилає стан трафіку в Кошіце."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    # О 05:00 (перед ранньою 06:00) і о 17:00 (перед нічною 18:00)
    if not ((h == 5 and m < 5) or (h == 17 and m < 5)):
        return

    state = load_json_file(TRAFFIC_ALERT_FILE, default={})
    key = now_local.strftime("%Y-%m-%d-%H")
    if state.get(key):
        return

    try:
        from traffic_kosice import format_traffic_report
        report = format_traffic_report()

        shift = "☀️ Рання зміна (06:00)" if h == 5 else "🌙 Нічна зміна (18:00)"
        msg = f"🚗 <b>Трафік перед зміною</b>\n{shift}\n\n{report}"
        send_telegram(msg)
        print(f"Traffic before shift sent at {h}:00")

        state[key] = True
        save_json_file(TRAFFIC_ALERT_FILE, state)

    except Exception as e:
        print(f"check_traffic_before_shift error: {e}")

# ─── НАГАДУВАННЯ ЗВАЖИТИСЬ ────────────────────────────────────────────────────

WEIGHT_REMIND_FILE = os.path.join(_DATA_DIR, "monitor_weight_remind.json")

def check_weight_reminder():
    """
    Нагадує зважитись:
    - 04:55 — якщо сьогодні рання зміна (06:00)
    - 10:10 — якщо вихідний (немає змін)
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    # Перевіряємо тільки у потрібні вікна
    is_455  = (h == 4 and 55 <= m <= 59)
    is_1010 = (h == 10 and 10 <= m <= 14)
    if not (is_455 or is_1010):
        return

    state = load_json_file(WEIGHT_REMIND_FILE, default={})
    key = f"{today}_{h}"
    if state.get(key):
        return

    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    try:
        # Перевіряємо календар — є зміна сьогодні?
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id = "novosadovoleg%40gmail.com"

        day_start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        day_end_utc   = now_local.replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(hours=2)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(day_start_utc.isoformat())}"
            f"&timeMax={urllib.parse.quote(day_end_utc.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=10"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        has_early = any("рання" in ev.get("summary","").lower() for ev in events)
        has_shift = any("зміна" in ev.get("summary","").lower() for ev in events)
        is_day_off = not has_shift

        # 04:55 — тільки якщо є рання зміна
        if is_455 and not has_early:
            return

        # 10:10 — тільки якщо вихідний
        if is_1010 and not is_day_off:
            return

        msg = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚖️ <b>ЧАС ЗВАЖИТИСЬ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Зваж себе зараз і запиши в Apple Health.\n\n"
            "Потім надішли мені свою вагу, наприклад:\n"
            "<code>82.5</code>"
        )
        send_telegram(msg)
        print(f"Weight reminder sent at {h}:{m:02d}")

        state[key] = True
        save_json_file(WEIGHT_REMIND_FILE, state)

    except Exception as e:
        print(f"check_weight_reminder error: {e}")

# ─── ARMOLOPID PLUS — НАГАДУВАННЯ ────────────────────────────────────────────

MEDS_FILE = os.path.join(_DATA_DIR, "monitor_meds.json")

def check_meds_reminder():
    """
    Нагадує прийняти Armolopid Plus:
    - 11:00 — вихідний день
    - 13:15 — рання зміна
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    is_1100  = (h == 11 and 0  <= m <= 4)
    is_1315  = (h == 13 and 15 <= m <= 19)
    if not (is_1100 or is_1315):
        return

    state = load_json_file(MEDS_FILE, default={})
    remind_key = f"remind_{today}_{h}"
    if state.get(remind_key):
        return

    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id = "novosadovoleg%40gmail.com"

        day_start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        day_end_utc   = now_local.replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(hours=2)
        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(day_start_utc.isoformat())}"
            f"&timeMax={urllib.parse.quote(day_end_utc.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=10"
        )
        if _HAS_REQUESTS:
            r = _requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        has_early  = any("рання" in ev.get("summary","").lower() for ev in events)
        has_shift  = any("зміна" in ev.get("summary","").lower() for ev in events)
        is_day_off = not has_shift

        if is_1100 and not is_day_off:
            return
        if is_1315 and not has_early:
            return

        # Надсилаємо з кнопками
        bot_token  = os.environ.get("TELEGRAM_TOKEN", "8374312425:AAHqrQCEqrgtVdl5Te5WhWblM2ESCnqhpfk")
        chat_id_tg = os.environ.get("TELEGRAM_CHAT_ID", "2100366814")

        text = (
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💊 <b>ARMOLOPID PLUS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Час прийняти таблетку від холестерину!\n\n"
            "<i>Прийняв сьогодні?</i>"
        )
        payload = json.dumps({
            "chat_id": chat_id_tg,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ Прийняв", "callback_data": f"meds_yes_{today}"},
                    {"text": "❌ Не прийняв", "callback_data": f"meds_no_{today}"},
                ]]
            }
        }).encode()

        import urllib.request as _ur
        req2 = _ur.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with _ur.urlopen(req2, timeout=15) as resp:
            resp.read()

        print(f"Meds reminder sent at {h}:{m:02d}")
        state[remind_key] = True
        save_json_file(MEDS_FILE, state)

    except Exception as e:
        print(f"check_meds_reminder error: {e}")

# ─── HEALTH ALERT (HRV / СТРЕС) ──────────────────────────────────────────────

HEALTH_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_health_alert.json")

def check_health_alert():
    """
    Після того як користувач вніс health дані — перевіряє:
    - HRV впав на 15+ від середнього за 7 днів → алерт
    - Стрес макс >= 60 → алерт
    - Стрес зріс на 15+ від середнього → алерт
    Надсилає не частіше 1 разу на день.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(HEALTH_ALERT_FILE, default={})
    if state.get(today):
        return  # вже надсилали сьогодні

    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from storage import load_health as _lh
        health = _lh()
    except Exception as e:
        print(f"health_alert load error: {e}")
        return

    today_data = health.get(today, {})
    if not today_data:
        return  # немає даних за сьогодні — нічого перевіряти

    # Середнє за останні 7 днів (без сьогодні)
    past_days = sorted([d for d in health.keys() if d < today], reverse=True)[:7]
    past_data = [health[d] for d in past_days]

    alerts = []

    # ── HRV ──
    hrv_today = today_data.get("hrv")
    if hrv_today and past_data:
        hrv_vals = [d["hrv"] for d in past_data if d.get("hrv")]
        if hrv_vals:
            hrv_avg = sum(hrv_vals) / len(hrv_vals)
            hrv_drop = hrv_avg - hrv_today
            if hrv_drop >= 15:
                alerts.append(
                    f"💓 <b>HRV впав!</b>  {int(hrv_today)} ms  (серед. {int(hrv_avg)} ms, -<b>{int(hrv_drop)}</b>)\n"
                    f"   → Можливо перевтома або погана ніч. Більше відпочинку!"
                )

    # ── СТРЕС ──
    stress_today = today_data.get("stress_max")
    if stress_today:
        if stress_today >= 60:
            alerts.append(
                f"😤 <b>Високий стрес!</b>  {stress_today}/100\n"
                f"   → Рекомендую: дихальні вправи, прогулянка, менше екранів"
            )
        elif past_data:
            stress_vals = [d["stress_max"] for d in past_data if d.get("stress_max")]
            if stress_vals:
                stress_avg = sum(stress_vals) / len(stress_vals)
                stress_rise = stress_today - stress_avg
                if stress_rise >= 15:
                    alerts.append(
                        f"😤 <b>Стрес зріс!</b>  {stress_today}  (серед. {int(stress_avg)}, +<b>{int(stress_rise)}</b>)\n"
                        f"   → Зверни увагу на відновлення"
                    )

    # ── КРОКИ ──
    steps_today = today_data.get("steps")
    if steps_today and steps_today < 5000:
        alerts.append(
            f"👟 <b>Мало кроків!</b>  {steps_today:,}  (ціль 10,000)\n"
            f"   → Невелика прогулянка ввечері?"
        )

    # ── HEALTH SCORE ──
    score_today = today_data.get("health_score")
    if score_today and past_data:
        score_vals = [d["health_score"] for d in past_data if d.get("health_score")]
        if score_vals:
            score_avg = sum(score_vals) / len(score_vals)
            score_drop = score_avg - score_today
            if score_drop >= 15:
                alerts.append(
                    f"💚 <b>Health Score впав!</b>  {score_today}/100  (серед. {int(score_avg)}, -<b>{int(score_drop)}</b>)\n"
                    f"   → Провів поганий день? Аналізуй сон і стрес"
                )

    if not alerts:
        return

    msg = f"⚠️ <b>Health Alert</b>  {now_local.strftime('%d.%m')}\n\n"
    msg += "\n\n".join(alerts)
    send_telegram(msg)
    print(f"Health alert sent: {len(alerts)} alerts")

    state[today] = True
    save_json_file(HEALTH_ALERT_FILE, state)

# ─── НАГАДУВАННЯ ВНЕСТИ HEALTH ДАНІ ──────────────────────────────────────────

HEALTH_REMIND_FILE = os.path.join(_DATA_DIR, "monitor_health_remind.json")

def check_health_data_reminder():
    """
    О 22:00 — якщо health дані за сьогодні не занесені → нагадування.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    if not (h == 22 and m < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(HEALTH_REMIND_FILE, default={})
    if state.get(today):
        return

    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from storage import load_health as _lh
        health = _lh()
        today_data = health.get(today, {})

        if today_data and today_data.get("steps"):
            return  # вже є дані

        msg = (
            "🍎 <b>Health дані за сьогодні!</b>\n\n"
            "Не забудь занести показники зі скріну Apple Health:\n\n"
            "Надішли фото скріну або вручну:\n"
            "<code>/зд [кроки] [сон] [ЧСС] [кал] [score]</code>"
        )
        send_telegram(msg)
        print("Health data reminder sent")

        state[today] = True
        save_json_file(HEALTH_REMIND_FILE, state)

    except Exception as e:
        print(f"check_health_data_reminder error: {e}")

    # 4. Все інше — promo
    return "promo"


def check_crypto_weekly_summary():
    """Щонеділі о 19:00: % зміна BTC/ETH/AVAX/ONDO за тиждень + AI коментар."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.weekday() == 6 and now_local.hour == 19 and now_local.minute < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(CRYPTO_WEEKLY_FILE, default={})
    if state.get("last") == today:
        return

    try:
        ids = ",".join(COINS.values())
        url = (
            f"https://api.coingecko.com/api/v3/coins/markets"
            f"?vs_currency=usd&ids={ids}&price_change_percentage=7d,24h"
        )
        raw = fetch_json(url)
        if not raw:
            return
        # convert list → dict by id
        data = {c["id"]: c for c in raw}

        # symbol order from COINS dict
        lines = []
        summary_parts = []
        for symbol, cg_id in COINS.items():
            coin = data.get(cg_id, {})
            price = coin.get("current_price")
            ch7d  = coin.get("price_change_percentage_7d_in_currency")
            ch24h = coin.get("price_change_percentage_24h")
            if price is None:
                continue

            arrow7 = "🟢" if (ch7d or 0) > 0 else "🔴"
            sign7  = "+" if (ch7d or 0) > 0 else ""
            lines.append(
                f"{arrow7} <b>{symbol}</b>: ${price:,.2f}  "
                f"7д: {sign7}{ch7d:.1f}%  24г: {'+' if (ch24h or 0)>0 else ''}{ch24h:.1f}%"
            )
            summary_parts.append(f"{symbol} {sign7}{ch7d:.1f}% за тиждень (${price:,.2f})")

        if not lines:
            return

        # AI коментар
        ai_comment = ""
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key and summary_parts:
            prompt = (
                "Ти фінансовий аналітик. Ось динаміка криптовалют за тиждень:\n"
                + "\n".join(summary_parts)
                + "\n\nДай короткий коментар (2-3 речення) українською: що відбулось на крипторинку цього тижня "
                  "і на що звернути увагу інвестору. Без зайвих слів, по суті."
            )
            try:
                payload = json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 400, "temperature": 0.7}
                }).encode()
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_comment = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                print(f"crypto weekly AI error: {e}")

        msg = f"📊 <b>Крипто підсумок тижня</b> ({today[5:]})\n\n"
        msg += "\n".join(lines)
        if ai_comment:
            msg += f"\n\n🤖 <i>{ai_comment}</i>"

        send_telegram(msg)
        print("Crypto weekly summary sent")
        state["last"] = today
        save_json_file(CRYPTO_WEEKLY_FILE, state)

    except Exception as e:
        print(f"check_crypto_weekly_summary error: {e}")


# ─── NET WORTH НАГАДУВАННЯ (1-е число місяця 10:00) ──────────────────────────

NET_WORTH_FILE = os.path.join(_DATA_DIR, "monitor_net_worth.json")

def check_net_worth_reminder():
    """1-го числа кожного місяця о 10:00 — нагадування оновити net worth."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.day == 1 and now_local.hour == 10 and now_local.minute < 5):
        return

    month_key = now_local.strftime("%Y-%m")
    state = load_json_file(NET_WORTH_FILE, default={})
    if state.get("last") == month_key:
        return

    month_names = {
        1:"Січень",2:"Лютий",3:"Березень",4:"Квітень",5:"Травень",
        6:"Червень",7:"Липень",8:"Серпень",9:"Вересень",10:"Жовтень",
        11:"Листопад",12:"Грудень"
    }
    month_name = month_names[now_local.month]

    send_telegram(
        f"📊 <b>Net Worth — {month_name} {now_local.year}</b>\n\n"
        f"Початок нового місяця — час підбити підсумки!\n\n"
        f"Перевір та запиши:\n"
        f"💹 <b>Крипто</b> — BTC, ETH, AVAX, ONDO\n"
        f"🏦 <b>Банк</b> — поточний рахунок + заощадження\n"
        f"📈 <b>Інвестиції</b> — InterFin портфель\n"
        f"💰 <b>Готівка</b> — якщо є\n\n"
        f"Відстеження = мотивація рости! 💪"
    )
    print("Net worth reminder sent")
    state["last"] = month_key
    save_json_file(NET_WORTH_FILE, state)


# ─── ІНВЕСТИЦІЙНИЙ ДАЙДЖЕСТ (вівторок 08:00) ─────────────────────────────────

INVEST_DIGEST_FILE = os.path.join(_DATA_DIR, "monitor_invest_digest.json")

def check_investment_news_digest():
    """Щовівторка о 08:00: AI дайджест новин по інвестиціях/ETF/крипто-регуляції."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.weekday() == 1 and now_local.hour == 8 and now_local.minute < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(INVEST_DIGEST_FILE, default={})
    if state.get("last") == today:
        return

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        return

    try:
        # Збираємо новини з Google News RSS
        import xml.etree.ElementTree as ET
        topics = [
            ("інвестиції ETF", "https://news.google.com/rss/search?q=investments+ETF+crypto&hl=uk&gl=UA&ceid=UA:uk"),
            ("crypto regulation", "https://news.google.com/rss/search?q=crypto+regulation+Bitcoin+ETF&hl=en&gl=US&ceid=US:en"),
            ("AVAX ONDO altcoin", "https://news.google.com/rss/search?q=Avalanche+AVAX+ONDO+altcoin&hl=en&gl=US&ceid=US:en"),
        ]

        all_titles = []
        for label, rss_url in topics:
            try:
                req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    tree = ET.fromstring(r.read())
                items = tree.findall(".//item")
                for item in items[:5]:
                    title_el = item.find("title")
                    if title_el is not None and title_el.text:
                        all_titles.append(title_el.text.strip())
            except Exception as e:
                print(f"RSS {label} error: {e}")

        if not all_titles:
            return

        news_block = "\n".join(f"- {t}" for t in all_titles[:15])
        prompt = (
            "Ти фінансовий аналітик. Ось заголовки новин за останні дні:\n\n"
            + news_block
            + "\n\nСклади короткий дайджест (3-4 речення) українською: що важливо знати "
              "приватному інвестору в крипто та ETF цього тижня. "
              "Виділи 1-2 ключові події. Без зайвих вступів, одразу по суті."
        )

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 400, "temperature": 0.6}
        }).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        digest = resp["candidates"][0]["content"]["parts"][0]["text"].strip()

        send_telegram(
            f"📰 <b>Інвестиційний дайджест</b> ({today[5:]})\n\n"
            f"{digest}\n\n"
            f"<i>🤖 AI підсумок по Google News</i>"
        )
        print("Investment news digest sent")
        state["last"] = today
        save_json_file(INVEST_DIGEST_FILE, state)

    except Exception as e:
        print(f"check_investment_news_digest error: {e}")


# ─── НАГАДУВАННЯ ПРО ІНТЕРВАЛЬНЕ ГОЛОДУВАННЯ (20:00 вільний день) ────────────

FASTING_FILE = os.path.join(_DATA_DIR, "monitor_fasting.json")

def check_fasting_reminder():
    """О 20:00 у вільний день: нагадування закінчити їсти (ціль — схуднення до 78 кг)."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.hour == 20 and now_local.minute < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(FASTING_FILE, default={})
    if state.get("last") == today:
        return

    # Перевіряємо чи є зміна сьогодні
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    has_shift = False
    if creds_json:
        try:
            creds_data = json.loads(creds_json)
            token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
            tmin = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            tmax = tmin + timedelta(hours=24)
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events"
                f"?timeMin={urllib.parse.quote(tmin.isoformat())}"
                f"&timeMax={urllib.parse.quote(tmax.isoformat())}"
                f"&singleEvents=true&maxResults=10"
            )
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                events = json.loads(r.read()).get("items", [])
            has_shift = any("рання" in e.get("summary","").lower() or "нічна" in e.get("summary","").lower() for e in events)
        except Exception as e:
            print(f"fasting calendar check error: {e}")

    if has_shift:
        return  # в робочий день режим інший

    # Поточна вага для мотивації
    weight_data = load_json_file(os.path.join(_DATA_DIR, "weight.json"), default={})
    weight_note = ""
    if weight_data:
        last_w = sorted(weight_data.items())[-1][1]
        to_goal = last_w - 78.0
        if to_goal > 0:
            weight_note = f"\n\n⚖️ До цілі 78 кг ще: <b>{to_goal:.1f} кг</b> — кожен день рахується!"

    send_telegram(
        "🕗 <b>Час зупинитись з їжею!</b>\n\n"
        "Якщо практикуєш <b>інтервальне голодування 16:8</b>:\n"
        "• Останній прийом їжі о 20:00\n"
        "• Наступний — о 12:00 завтра\n"
        "• Можна: вода, чай без цукру\n\n"
        "💪 Дотримання вікна — ключ до схуднення!"
        + weight_note
    )
    print("Fasting reminder sent")
    state["last"] = today
    save_json_file(FASTING_FILE, state)


# ─── ПОГОДА ПЕРЕД ЗМІНОЮ (за 1.5г до початку) ───────────────────────────────

PRE_SHIFT_WEATHER_FILE = os.path.join(_DATA_DIR, "monitor_pre_shift_weather.json")

def check_pre_shift_weather():
    """За 1.5 години до зміни: погода на час дороги + чи потрібна куртка/парасоля."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    # Рання зміна о 05:00 → нагадування о 03:30
    # Нічна зміна о 17:00 → нагадування о 15:30
    is_pre_early = (h == 3 and 28 <= m <= 35)
    is_pre_night = (h == 15 and 28 <= m <= 35)

    if not (is_pre_early or is_pre_night):
        return

    key = "pre_early" if is_pre_early else "pre_night"
    shift_time = "05:00" if is_pre_early else "17:00"

    state = load_json_file(PRE_SHIFT_WEATHER_FILE, default={})
    if state.get(key) == today:
        return

    # Перевіряємо чи є відповідна зміна сьогодні
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    has_shift = False
    if creds_json:
        try:
            creds_data = json.loads(creds_json)
            token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
            tmin = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            tmax = tmin + timedelta(hours=24)
            url = (
                f"https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events"
                f"?timeMin={urllib.parse.quote(tmin.isoformat())}"
                f"&timeMax={urllib.parse.quote(tmax.isoformat())}"
                f"&singleEvents=true&maxResults=10"
            )
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                events = json.loads(r.read()).get("items", [])

            shift_word = "рання" if is_pre_early else "нічна"
            has_shift = any(shift_word in e.get("summary","").lower() for e in events)
        except Exception as e:
            print(f"pre_shift_weather calendar error: {e}")

    if not has_shift:
        return

    try:
        # Погода на конкретну годину через open-meteo hourly
        shift_hour = 5 if is_pre_early else 17
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=48.7163&longitude=21.2611"
            f"&hourly=temperature_2m,apparent_temperature,precipitation_probability,weathercode,windspeed_10m"
            f"&forecast_days=1&timezone=Europe%2FPrague"
        )
        data = fetch_json(url)
        if not data:
            return

        hourly = data.get("hourly", {})
        times  = hourly.get("time", [])
        temps  = hourly.get("temperature_2m", [])
        feels  = hourly.get("apparent_temperature", [])
        precip = hourly.get("precipitation_probability", [])
        codes  = hourly.get("weathercode", [])
        winds  = hourly.get("windspeed_10m", [])

        # Знаходимо потрібний час
        idx = None
        for i, t in enumerate(times):
            if t.endswith(f"T{shift_hour:02d}:00"):
                idx = i
                break

        if idx is None or idx >= len(temps):
            return

        WMO = {
            0:"☀️ Ясно",1:"🌤 Перев.ясно",2:"⛅️ Хмарно",3:"☁️ Похмуро",
            45:"🌫 Туман",51:"🌦 Мряка",61:"🌧 Дощ",63:"🌧 Дощ",65:"🌧 Сильний дощ",
            71:"❄️ Сніг",80:"🌦 Злива",81:"🌦 Злива",95:"⛈ Гроза",96:"⛈ Гроза"
        }

        temp   = temps[idx]
        feel   = feels[idx] if idx < len(feels) else temp
        rain_p = precip[idx] if idx < len(precip) else 0
        code   = codes[idx] if idx < len(codes) else 0
        wind   = winds[idx] if idx < len(winds) else 0
        desc   = WMO.get(code, "—")

        # Рекомендації
        tips = []
        if rain_p >= 50 or code in {51,53,55,61,63,65,80,81,82,95,96,99}:
            tips.append("☂️ Візьми парасолю!")
        if feel < 10:
            tips.append("🧥 Тепла куртка — на вулиці холодно")
        elif feel < 16:
            tips.append("🧥 Легка куртка не завадить")
        if wind >= 30:
            tips.append("💨 Сильний вітер")

        tips_text = "\n".join(tips) if tips else "✅ Погода нормальна — нічого особливого"

        send_telegram(
            f"🌤 <b>Погода на дорогу до роботи</b> ({shift_time})\n\n"
            f"{desc}  {temp:.0f}°C (відчувається {feel:.0f}°C)\n"
            f"💧 Дощ: {rain_p}%  💨 Вітер: {wind:.0f} км/г\n\n"
            f"{tips_text}"
        )
        print(f"Pre-shift weather sent for {shift_time}")
        state[key] = today
        save_json_file(PRE_SHIFT_WEATHER_FILE, state)

    except Exception as e:
        print(f"check_pre_shift_weather error: {e}")


# ─── СТРІК НАВЧАННЯ ІНВЕСТИЦІЯМ ──────────────────────────────────────────────

LEARNING_STREAK_FILE = os.path.join(_DATA_DIR, "monitor_learning_streak.json")

def check_learning_streak():
    """
    Якщо 2+ дні підряд немає запису в habits про навчання → нагадування.
    Перевіряємо щодня о 18:00.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.hour == 18 and now_local.minute < 5):
        return

    today = now_local.strftime("%Y-%m-%d")
    state = load_json_file(LEARNING_STREAK_FILE, default={})
    if state.get("last") == today:
        return

    try:
        import sys as _sys
        _sys.path.insert(0, _DIR)
        from storage import load_habits as _lh
        habits = _lh()
        if not habits:
            return

        # Шукаємо кількість днів без навчання підряд
        days_without = 0
        for i in range(1, 8):  # перевіряємо до 7 днів назад
            day = (now_local - timedelta(days=i)).strftime("%Y-%m-%d")
            day_data = habits.get(day, {})

            # Перевіряємо наявність запису про навчання
            # Habits зазвичай мають поля: learning, study, навчання тощо
            has_learning = (
                day_data.get("learning") or
                day_data.get("study") or
                day_data.get("навчання") or
                day_data.get("invest_study") or
                day_data.get("education")
            )
            if has_learning:
                break
            days_without += 1

        if days_without >= 2:
            msg = (
                f"📚 <b>Навчання інвестиціям — {days_without} дні без занять!</b>\n\n"
                f"⚠️ Не переривай streak!\n\n"
                f"Навіть 15-20 хвилин на день:\n"
                f"• Курс від Maroš Sivák / InterFin\n"
                f"• Читання статті про ETF або крипто\n"
                f"• Перегляд відео по фінансах\n\n"
                f"💡 <i>Консистентність > інтенсивність</i>"
            )
            send_telegram(msg)
            print(f"Learning streak reminder sent: {days_without} days without study")

        state["last"] = today
        save_json_file(LEARNING_STREAK_FILE, state)

    except Exception as e:
        print(f"check_learning_streak error: {e}")


# ─── SMART CONTEXT-AWARE NOTIFICATIONS ───────────────────────────────────────

SMART_NOTIF_FILE = os.path.join(_DATA_DIR, "monitor_smart_notif.json")

def check_smart_notifications():
    """
    🧠 SMART NOTIFICATIONS — щохвилинна перевірка.
    Ситуативні сповіщення прив'язані до зміни + прогрес до цілей.
    """
    import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    try:
        from context import get_context, get_shift_from_calendar, should_notify, should_notify_low_priority
    except Exception as e:
        print(f"context import error: {e}"); return

    try:
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        h = now_local.hour
        m = now_local.minute
        today = now_local.strftime("%Y-%m-%d")
        state = load_json_file(SMART_NOTIF_FILE, default={})

        def sent(key): return state.get(key) == today
        def mark(key):
            state[key] = today
            save_json_file(SMART_NOTIF_FILE, state)

        shift_info     = get_shift_from_calendar()
        today_shift    = shift_info.get("today", "free")
        tomorrow_shift = shift_info.get("tomorrow", "free")

        # ── 1. ПІДЙОМ ПЕРЕД РАННЬОЮ (04:30) ───────────────────────────────
        if today_shift == "early" and h == 4 and 30 <= m < 35 and not sent("pre_early"):
            # Погода швидко
            weather_line = ""
            try:
                wkey = os.environ.get("WEATHER_API_KEY","")
                if wkey:
                    url_w = f"https://api.openweathermap.org/data/2.5/weather?q=Kosice&appid={wkey}&units=metric&lang=uk"
                    req_w = urllib.request.Request(url_w, headers={"User-Agent":"bot"})
                    with urllib.request.urlopen(req_w, timeout=5) as r:
                        wd = json.loads(r.read())
                    temp = round(wd["main"]["temp"])
                    desc = wd["weather"][0]["description"]
                    weather_line = f"\n🌤 Надворі {temp}°C, {desc}"
            except Exception: pass

            send_telegram(
                f"⏰ <b>ПІДЙОМ! Рання зміна через 1.5г</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"✅ Сніданок — не пропускай!\n"
                f"💊 Armolopid Plus\n"
                f"👟 Зручне взуття{weather_line}\n\n"
                f"<i>💪 Ти можеш — до роботи!</i>"
            )
            mark("pre_early")

        # ── 2. ПІСЛЯ РАННЬОЇ (18:15) ───────────────────────────────────────
        elif today_shift == "early" and h == 18 and 15 <= m < 20 and not sent("post_early"):
            # Прогрес по звичках
            habit_hint = ""
            try:
                from storage import load_habits as _lh
                db = _lh()
                td = db.get(today, {})
                done = sum(1 for k in ["run","water","shower","tea"] if td.get(k) is True)
                habit_hint = f"\n\n📊 Звички сьогодні: {done}/4 — відміть решту!"
            except Exception: pass

            send_telegram(
                f"🏠 <b>Рання зміна завершена! Молодець 💪</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"⚖️ Зважся — запиши вагу\n"
                f"🏃 Є сили на пробіжку?\n"
                f"✅ Відміть звички /звички{habit_hint}\n\n"
                f"<i>🌙 Вечір твій — використай добре!</i>"
            )
            mark("post_early")

        # ── 3. ПІДГОТОВКА ДО НІЧНОЇ (16:30) ──────────────────────────────
        elif today_shift == "night" and h == 16 and 30 <= m < 35 and not sent("pre_night"):
            send_telegram(
                f"🌙 <b>Нічна зміна через 1.5г</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🍽 Поїж зараз — наступний раз о 06:00\n"
                f"💊 Armolopid Plus\n"
                f"☕️ Кава або чай — заряд на ніч\n"
                f"😴 Хоча б 20 хв відпочинку\n\n"
                f"<i>⚡️ Хорошої зміни!</i>"
            )
            mark("pre_night")

        # ── 4. ПІСЛЯ НІЧНОЇ (06:15) ───────────────────────────────────────
        elif today_shift == "night" and h == 6 and 15 <= m < 20 and not sent("post_night"):
            send_telegram(
                f"😴 <b>Нічна завершена — ДОДОМУ!</b>\n"
                f"━━━━━━━━━━━━━━━━\n"
                f"🍳 Поїж щось легке перед сном\n"
                f"📵 Телефон в сторону — сон критичний\n"
                f"✅ Звички запишеш після сну\n\n"
                f"<i>💪 Ти відробив ніч — заслужений відпочинок!</i>"
            )
            mark("post_night")

        # ── 5. AI ПОРАДА (10:00, 14:00, 19:00 — вільний день) ─────────────
        ai_slots = {
            10: ("🌅 Ранкова порада", "Олег щойно прокинувся у вільний день. Дай ОДНУ конкретну дію на ранок — для схуднення (ціль 78 кг) або здоров'я. 1-2 речення, конкретно."),
            14: ("☀️ Порада на середину дня", "Олег вдома в середині дня. Дай одну ідею — що зробити для здоров'я або продуктивності наступні 2 години. Коротко і конкретно."),
            19: ("🌙 Вечірня порада", "Вечір вільного дня Олега. 1-2 речення: коротка оцінка дня і одна порада перед сном (схуднення/здоров'я/фінанси). По суті, без загальних слів."),
        }
        if today_shift == "free" and h in ai_slots and 0 <= m < 5:
            akey = f"ai_tip_{h}"
            if not sent(akey):
                label, prompt_text = ai_slots[h]
                gemini_key = os.environ.get("GEMINI_API_KEY","")
                if gemini_key:
                    try:
                        # Додаємо контекст ваги
                        w_context = ""
                        try:
                            from storage import load_weight as _lw
                            wdata = _lw()
                            if wdata:
                                last_k = sorted(wdata.keys())[-1]
                                w_context = f" Остання вага: {wdata[last_k]} кг."
                        except Exception: pass

                        full_prompt = f"{prompt_text}{w_context}"
                        payload = json.dumps({"contents":[{"parts":[{"text":full_prompt}]}],"generationConfig":{"maxOutputTokens":150,"temperature":0.9}}).encode()
                        req_ai = urllib.request.Request(
                            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                            data=payload, headers={"Content-Type":"application/json"}, method="POST"
                        )
                        with urllib.request.urlopen(req_ai, timeout=20) as r:
                            resp = json.loads(r.read())
                        tip = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                        send_telegram(f"{label}\n\n{tip}")
                        mark(akey)
                    except Exception as e:
                        print(f"smart notif AI error: {e}")

        # ── 6. ПЛАН НА ЗАВТРА (22:00 якщо є зміна) ───────────────────────
        if tomorrow_shift in ("early", "night") and h == 22 and 0 <= m < 5 and not sent("tomorrow_plan"):
            if tomorrow_shift == "early":
                send_telegram(
                    f"📋 <b>Завтра РАННЯ зміна (06:00)</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"😴 Лягай спати до 22:30\n"
                    f"⏰ Будильник на 04:30\n"
                    f"👕 Приготуй одяг і їжу зараз\n\n"
                    f"<i>Гарного відпочинку!</i>"
                )
            else:
                send_telegram(
                    f"📋 <b>Завтра НІЧНА зміна (18:00)</b>\n"
                    f"━━━━━━━━━━━━━━━━\n"
                    f"😴 Поспи вдень якщо зможеш\n"
                    f"🍽 Поїж о 16:30–17:00 (останній раз до 06:00)\n"
                    f"☕️ Підготуй термос з чаєм\n\n"
                    f"<i>Ти впораєшся 💪</i>"
                )
            mark("tomorrow_plan")

        # ── 7. ПРОГРЕС ДО 78 КГ (щосереди о 12:00) ───────────────────────
        if today_shift == "free" and now_local.weekday() == 2 and h == 12 and 0 <= m < 5 and not sent("weight_progress"):
            try:
                from storage import load_weight as _lw
                wdata = _lw()
                if wdata:
                    sorted_keys = sorted(wdata.keys())
                    if len(sorted_keys) >= 2:
                        last_w = wdata[sorted_keys[-1]]
                        first_w = wdata[sorted_keys[0]]
                        to_goal = round(last_w - 78.0, 1)
                        total_lost = round(first_w - last_w, 1)
                        if to_goal > 0:
                            # Графік останніх 5 вимірювань
                            recent_5 = sorted_keys[-5:]
                            w_vals = [wdata[d] for d in recent_5]
                            w_min = min(w_vals) - 0.3
                            w_max = max(w_vals) + 0.3
                            blocks = ["⬜","🟦","🟦","🟩","🟩","🟨","🟧","🟥"]
                            bars = []
                            for v in w_vals:
                                b = int((v - w_min) / max(w_max - w_min, 0.1) * 7)
                                bars.append(blocks[max(0, min(7, b))])
                            trend = "↗️" if w_vals[-1] > w_vals[-2] else "↘️" if w_vals[-1] < w_vals[-2] else "→"
                            send_telegram(
                                f"⚖️ <b>Прогрес до цілі 78 кг</b>\n\n"
                                f"Зараз: <b>{last_w} кг</b>  {trend}\n"
                                f"До цілі: <b>{to_goal} кг</b>\n"
                                f"Всього скинуто: {total_lost} кг\n\n"
                                f"<code>{''.join(bars)}</code>  (останні 5 вимірювань)\n\n"
                                f"{'🎯 Ще трохи!' if to_goal < 2 else ('💪 Продовжуй!' if to_goal < 5 else '🔥 Ти на шляху!')}"
                            )
                            mark("weight_progress")
            except Exception as e:
                print(f"weight progress error: {e}")

    except Exception as e:
        print(f"check_smart_notifications error: {e}")


def check_morning_context():
    """
    Розумний ранковий брифінг — знає тип дня і адаптує зміст + час:
      рання зміна  → о 05:00 (перед виходом)
      нічна зміна  → о 10:00 (після сну)
      вихідний     → о 08:30
    Містить: привітання, що сьогодні заплановано, погода, крипто, мотивація.
    """
    import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(MORNING_CTX_FILE, default={})
    if state.get("last") == today:
        return

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
    except Exception:
        shift = "free"

    # Час відправки залежно від типу дня
    trigger = {"early": 5, "night": 10, "free": 8}.get(shift, 8)
    if not (h == trigger and 0 <= m < 10):
        return

    try:
        # Календар на сьогодні
        cal = get_calendar()

        # Погода коротко
        try:
            weather = get_weather()
            # Беремо тільки першу строчку
            weather_short = weather.split("\n")[0] if weather else ""
        except Exception:
            weather_short = ""

        # Крипто топ
        try:
            ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
            url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&price_change_percentage=24h"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = json.loads(r.read())
            crypto_lines = []
            for c in raw:
                sym = c["symbol"].upper()
                price = c["current_price"]
                ch = c.get("price_change_percentage_24h") or 0
                icon = "🟢" if ch > 0 else "🔴"
                sign = "+" if ch > 0 else ""
                crypto_lines.append(f"{icon} {sym} ${price:,.0f} ({sign}{ch:.1f}%)")
            crypto_text = "  ".join(crypto_lines)
        except Exception:
            crypto_text = ""

        # AI мотивація на день
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        ai_tip = ""
        if gemini_key:
            try:
                shift_labels = {"early": "рання зміна (06:00–18:00)", "night": "нічна зміна (18:00–06:00)", "free": "вихідний день"}
                prompt = (
                    f"Сьогодні {['Пн','Вт','Ср','Чт','Пт','Сб','Нд'][now_local.weekday()]}, "
                    f"{shift_labels.get(shift,'вихідний')}. "
                    f"Дай Олегу одну конкретну мотиваційну пораду на сьогодні (1-2 речення) українською. "
                    f"Враховуй: ціль схуднення 78 кг, інвестиції/крипто, біг. "
                    f"Коротко, бадьоро, по суті."
                )
                payload = json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 120, "temperature": 0.9}
                }).encode()
                req2 = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type": "application/json"}, method="POST"
                )
                with urllib.request.urlopen(req2, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_tip = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception as e:
                print(f"morning context AI error: {e}")

        greetings = {"early": "☀️ Доброго ранку!", "night": "🌞 З добрим ранком!", "free": "🌅 Доброго ранку!"}
        greeting = greetings.get(shift, "🌅 Доброго ранку!")

        shift_info_text = {
            "early": "💼 Сьогодні рання зміна — виходити о 05:30",
            "night": "🌙 Сьогодні нічна зміна — виходити о 17:30",
            "free":  "🏖 Сьогодні вихідний — твій день!"
        }.get(shift, "")

        msg = f"{greeting} Олеже!\n\n{shift_info_text}\n\n"
        if weather_short:
            msg += f"🌤 {weather_short}\n\n"
        msg += f"📅 <b>План дня:</b>\n{cal}\n\n"
        if crypto_text:
            msg += f"💹 {crypto_text}\n\n"
        if ai_tip:
            msg += f"💡 <i>{ai_tip}</i>"

        send_telegram(msg)
        print(f"Morning context sent: shift={shift}, hour={h}")
        state["last"] = today
        save_json_file(MORNING_CTX_FILE, state)

    except Exception as e:
        print(f"check_morning_context error: {e}")


# ─── ТРЕКЕР БІГ / RUN COACH ──────────────────────────────────────────────────

RUN_COACH_FILE = os.path.join(_DATA_DIR, "monitor_run_coach.json")

def check_run_coach():
    """
    Тренер бігу — нагадує бігати 3 рази на тиждень.
    - Пн/Ср/Пт вихідного дня о 09:30: нагадування + план тренування
    - Якщо не бігав 3+ дні — нагадування будь-якого дня о 17:00
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")
    dow = now_local.weekday()  # 0=Пн

    state = load_json_file(RUN_COACH_FILE, default={})

    # Перевіримо скільки днів без бігу
    days_without = 0
    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from habits import load_data as _load_habits
        db = _load_habits()
        for i in range(1, 8):
            d = (now_local - timedelta(days=i)).strftime("%Y-%m-%d")
            if db.get(d, {}).get("run") is True:
                break
            days_without += 1
    except Exception:
        days_without = 0

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        today_shift = shift_info.get("today", "free")
    except Exception:
        today_shift = "free"

    is_free_day = today_shift == "free"

    # Нагадування Пн/Ср/Пт о 09:30 якщо вихідний
    run_key_day = f"run_coach_{today}"
    if is_free_day and dow in (0, 2, 4) and h == 9 and 30 <= m < 35 and not state.get(run_key_day):
        plans = [
            "🏃 <b>День бігу!</b>\n\nПлан: 20-30 хв легкий біг.\n• Розминка 5 хв ходьба\n• Темп розмовний (можеш говорити)\n• Заминка 5 хв ходьба\n\n💪 Навіть 2 км — це прогрес!",
            "🏃 <b>Час бігти!</b>\n\nСьогодні: 25-35 хв.\n• Перші 10 хв повільно\n• Середина — комфортний темп\n• Останні 5 хв — трохи швидше\n\n🔥 Кожне тренування = -калорії = ближче до 78 кг!",
            "🏃 <b>Пробіжка!</b>\n\nЦього тижня скільки разів бігав? Якщо 0-1 — сьогодні обов'язково!\n• 20 хв — мінімум\n• Повітря + рух = настрій на весь день\n\n🎯 Ціль: 3 тренування/тиждень",
        ]
        import random
        send_telegram(plans[dow % 3])
        state[run_key_day] = True
        save_json_file(RUN_COACH_FILE, state)
        return

    # Якщо 3+ дні без бігу — нагадування о 17:00 будь-якого вільного дня
    run_alert_key = f"run_alert_{today}"
    if days_without >= 3 and is_free_day and h == 17 and 0 <= m < 5 and not state.get(run_alert_key):
        send_telegram(
            f"🏃 <b>{days_without} днів без пробіжки!</b>\n\n"
            f"Ще не пізно — 20 хв бігу сьогодні ввечері?\n"
            f"Настрій гарантований 💪\n\n"
            f"<i>Ціль 78 кг — кожне тренування рахується!</i>"
        )
        state[run_alert_key] = True
        save_json_file(RUN_COACH_FILE, state)


# ─── НАГАДУВАННЯ ПРО ЇЖУ (дієтолог) ─────────────────────────────────────────

NUTRITION_FILE = os.path.join(_DATA_DIR, "monitor_nutrition.json")

def check_nutrition_reminder():
    """
    Дієтолог — нагадування про їжу з прив'язкою до графіку:
      Рання зміна:
        05:00 — сніданок перед виходом
        12:00 — обід на зміні
        19:00 — вечеря після зміни
      Нічна зміна:
        09:30 — сніданок після сну
        15:30 — обід/перекус перед зміною (ВАЖЛИВО — більше не поїсти)
        21:00 — легкий перекус на зміні якщо потрібно
      Вихідний:
        09:00 — сніданок
        13:00 — обід
        19:00 — вечеря (і нагадування про 16:8)
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(NUTRITION_FILE, default={})

    def already(key):
        return state.get(f"{today}_{key}")

    def mark(key):
        state[f"{today}_{key}"] = True
        save_json_file(NUTRITION_FILE, state)

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
    except Exception:
        shift = "free"

    # Рання зміна
    if shift == "early":
        if h == 5 and 0 <= m < 10 and not already("breakfast"):
            send_telegram(
                "🍳 <b>Сніданок!</b>\n\n"
                "Перед ранньою зміною важливо поїсти — дасть енергію на всі 12г.\n"
                "• Вівсянка / яйця / бутерброд\n"
                "• Вода або кава\n\n"
                "<i>Не виходь голодним!</i>"
            )
            mark("breakfast")
        elif h == 12 and 0 <= m < 10 and not already("lunch"):
            send_telegram(
                "🥗 <b>Обід на зміні!</b>\n\n"
                "Час поїсти — середина зміни.\n"
                "Намагайся уникати фастфуду:\n"
                "• Щось з собою > з кафетерію\n"
                "• Не забувай про воду 💧"
            )
            mark("lunch")
        elif h == 19 and 0 <= m < 10 and not already("dinner"):
            send_telegram(
                "🍽 <b>Вечеря!</b>\n\n"
                "Зміна позаду — час поїсти.\n"
                "💡 Порада: якщо практикуєш 16:8 —\n"
                "останній прийом їжі до 20:00\n\n"
                "<i>Легке і поживне 🥦</i>"
            )
            mark("dinner")

    # Нічна зміна
    elif shift == "night":
        if h == 9 and 30 <= m < 40 and not already("breakfast"):
            send_telegram(
                "🍳 <b>Сніданок!</b>\n\n"
                "Добрий ранок після нічної!\n"
                "Поїж щось легке перед сном:\n"
                "• Йогурт, фрукти, каша\n"
                "• Не їж важке — важче засинати"
            )
            mark("breakfast")
        elif h == 15 and 30 <= m < 40 and not already("lunch"):
            send_telegram(
                "🍽 <b>Важливо: обід перед нічною!</b>\n\n"
                "Це твій основний прийом їжі сьогодні.\n"
                "Через 2.5г виходиш на зміну — поїж добре:\n"
                "• Білок + вуглеводи + овочі\n"
                "• Уникай важкого — будеш на зміні\n\n"
                "<i>Це остання нормальна їжа до 06:00!</i>"
            )
            mark("lunch")

    # Вихідний
    else:
        if h == 9 and 0 <= m < 10 and not already("breakfast"):
            send_telegram(
                "🌅 <b>Сніданок!</b>\n\n"
                "Починаємо день правильно 💪\n"
                "• Повноцінний сніданок = енергія на весь ранок\n"
                "• Не пропускай — особливо якщо плануєш біг!\n\n"
                "<i>Ціль 78 кг: важливо що і коли їсти</i>"
            )
            mark("breakfast")
        elif h == 13 and 0 <= m < 10 and not already("lunch"):
            send_telegram(
                "🥗 <b>Обід!</b>\n\n"
                "Час заправитись 🍽\n"
                "• Тарілка: ½ овочі, ¼ білок, ¼ крупи\n"
                "• Не переїдай — вечеря ще буде\n\n"
                "<i>Слідкуй за порціями → 78 кг реальні!</i>"
            )
            mark("lunch")
        elif h == 19 and 0 <= m < 10 and not already("dinner"):
            send_telegram(
                "🌙 <b>Вечеря!</b>\n\n"
                "Якщо практикуєш 16:8 — це останній прийом їжі.\n"
                "• Їж до 20:00\n"
                "• Легке: риба, овочі, яйця\n"
                "• Уникай солодкого та важкого\n\n"
                "💪 <i>Ціль 78 кг: дисципліна ввечері — результат вранці!</i>"
            )
            mark("dinner")


# ─── ЯКІСТЬ СНУ — РАНКОВЕ ПИТАННЯ ────────────────────────────────────────────

SLEEP_Q_FILE = os.path.join(_DATA_DIR, "monitor_sleep_q.json")

def check_sleep_quality():
    """
    Вранці питає про якість сну — адаптивний час:
      Після ранньої (о 18:30): як спалось перед зміною?
      Після нічної (о 07:00): як перенесли нічну?
      Вихідний (о 08:00): як спалось?
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(SLEEP_Q_FILE, default={})
    if state.get(f"asked_{today}"):
        return

    try:
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
        yesterday_shift = shift_info.get("tomorrow", "free")  # використаємо як proxy
    except Exception:
        shift = "free"

    trigger = None
    if shift == "free" and h == 8 and 0 <= m < 10:
        trigger = "free"
    elif shift == "night" and h == 7 and 0 <= m < 10:
        trigger = "night"

    if not trigger:
        return

    questions = {
        "free":  "😴 <b>Як спалось?</b>\n\nОціни якість сну минулої ночі:",
        "night": "😴 <b>Як перенесли нічну?</b>\n\nЯкість сну після зміни:"
    }

    try:
        import urllib.request as _ur
        tg_token = os.environ.get("TELEGRAM_TOKEN", "")
        tg_chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
        payload  = json.dumps({
            "chat_id": tg_chat,
            "text": questions[trigger],
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[
                {"text": "😩 Погано",    "callback_data": "sleep_q_1"},
                {"text": "😐 Нормально","callback_data": "sleep_q_2"},
                {"text": "😊 Добре",    "callback_data": "sleep_q_3"},
                {"text": "🌟 Відмінно", "callback_data": "sleep_q_4"},
            ]]}
        }).encode()
        req = _ur.Request(
            f"https://api.telegram.org/bot{tg_token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"}
        )
        with _ur.urlopen(req, timeout=10) as r:
            pass
        state[f"asked_{today}"] = True
        save_json_file(SLEEP_Q_FILE, state)
        print("Sleep quality question sent")
    except Exception as e:
        print(f"sleep quality error: {e}")


# ─── КРИПТО РАНОК (щоденно при пробудженні) ──────────────────────────────────

CRYPTO_MORNING_FILE = os.path.join(_DATA_DIR, "monitor_crypto_morning.json")

def check_crypto_morning():
    """
    💹 CRYPTO DASHBOARD ЗРАНКУ — рання о 05:10, решта о 09:10.
    Ціни + міні-бар графік + Fear&Greed + AI сигнал.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    state = load_json_file(CRYPTO_MORNING_FILE, default={})
    if state.get("last") == today:
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        shift = shift_info.get("today", "free")
    except Exception:
        shift = "free"

    trigger = 5 if shift == "early" else 9
    if not (h == trigger and 10 <= m < 20):
        return

    try:
        ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
        url = f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}&price_change_percentage=24h,7d"
        req = urllib.request.Request(url, headers={"User-Agent": "bot"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = json.loads(r.read())
        data = {c["id"]: c for c in raw}

        coins_map = [("BTC","bitcoin"),("ETH","ethereum"),("AVAX","avalanche-2"),("ONDO","ondo-finance")]

        lines_out = []
        lines_out.append(f"💹 <b>КРИПТО ДАШБОРД</b> · {today[5:]}")
        lines_out.append("━━━━━━━━━━━━━━━━━━━━━━")

        summary_parts = []
        for sym, cg_id in coins_map:
            c = data.get(cg_id, {})
            price = c.get("current_price")
            ch24  = c.get("price_change_percentage_24h") or 0
            ch7   = c.get("price_change_percentage_7d_in_currency") or 0
            if price is None: continue

            icon = "🟢" if ch24 > 0 else "🔴"
            sign24 = "+" if ch24 > 0 else ""
            sign7  = "+" if ch7  > 0 else ""

            # Бар від -5% до +5%
            bar_pos = int(max(0, min(10, (ch24 + 5) / 10 * 10)))
            bar = "🔴" * bar_pos + "⬜" * (10 - bar_pos) if ch24 < 0 else "🟢" * bar_pos + "⬜" * (10 - bar_pos)
            bar = bar[:10]

            lines_out.append(f"")
            lines_out.append(f"{icon} <b>{sym}</b>  <b>${price:,.2f}</b>")
            lines_out.append(f"   24г: {sign24}{ch24:.2f}%  7д: {sign7}{ch7:.1f}%")
            lines_out.append(f"   <code>[{bar}]</code>")

            summary_parts.append(f"{sym}{sign24}{ch24:.1f}%")

        # Fear & Greed
        try:
            fg_data = fetch_json("https://api.alternative.me/fng/?limit=1")
            if fg_data:
                fg_val = int(fg_data["data"][0]["value"])
                fg_label = fg_data["data"][0]["value_classification"]
                fg_bar_f = int(fg_val / 100 * 10)
                fg_bar = "🟢" * fg_bar_f + "⬜" * (10 - fg_bar_f)
                if fg_val <= 25: fg_ico = "😱"
                elif fg_val <= 45: fg_ico = "😟"
                elif fg_val <= 55: fg_ico = "😐"
                elif fg_val <= 75: fg_ico = "😊"
                else: fg_ico = "🤑"
                lines_out.append("")
                lines_out.append(f"{fg_ico} <b>Fear &amp; Greed:</b> {fg_val}/100 — {esc(fg_label)}")
                lines_out.append(f"   <code>{fg_bar}</code>")
        except Exception:
            pass

        # AI сигнал
        gemini_key = os.environ.get("GEMINI_API_KEY","")
        if gemini_key and summary_parts:
            try:
                prompt = (
                    f"Крипто зміни за 24г: {', '.join(summary_parts)}. "
                    f"Дай 1-2 речення аналіз для довгострокового HODLera: "
                    f"що це означає, чи варто щось робити? Без фінансових порад, просто аналіз."
                )
                payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":150,"temperature":0.7}}).encode()
                req2 = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type":"application/json"}, method="POST"
                )
                with urllib.request.urlopen(req2, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_signal = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                lines_out.append("")
                lines_out.append(f"🤖 <i>{ai_signal}</i>")
            except Exception as e:
                print(f"crypto morning AI error: {e}")

        lines_out.append("")
        lines_out.append("━━━━━━━━━━━━━━━━━━━━━━")

        send_telegram("\n".join(lines_out))
        print(f"Crypto morning dashboard sent")
        state["last"] = today
        save_json_file(CRYPTO_MORNING_FILE, state)

    except Exception as e:
        print(f"check_crypto_morning error: {e}")


def check_week_goals():
    """
    Неділя о 20:30 — підсумок + цілі на наступний тиждень.
    AI аналізує тиждень і пропонує 3 конкретні цілі.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    dow = now_local.weekday()
    today = now_local.strftime("%Y-%m-%d")

    if not (dow == 6 and h == 20 and 30 <= m < 40):
        return

    state = load_json_file(WEEK_GOALS_FILE, default={})
    if state.get("last") == today:
        return

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        return

    try:
        # Збираємо дані за тиждень
        from habits import load_data as _load_habits
        db = _load_habits()
        week_days = [(now_local - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        habit_ids = ["run", "water", "tea", "shower"]
        habit_stats = {}
        for hid in habit_ids:
            done = sum(1 for d in week_days if db.get(d, {}).get(hid) is True)
            habit_stats[hid] = done

        # Вага
        try:
            from weight import load_weight_data
            wdata = load_weight_data()
            last_weight = None
            if wdata:
                last_key = sorted(wdata.keys())[-1]
                last_weight = wdata[last_key]["weight"]
        except Exception:
            last_weight = None

        prompt = (
            f"Тиждень Олега (Кошіце, Словаччина):\n"
            f"• Біг: {habit_stats.get('run',0)}/7 днів\n"
            f"• Вода: {habit_stats.get('water',0)}/7 днів\n"
            f"• Холодний душ: {habit_stats.get('shower',0)}/7 днів\n"
        )
        if last_weight:
            prompt += f"• Вага: {last_weight} кг (ціль 78 кг)\n"
        prompt += (
            f"\nСформулюй 3 конкретні цілі на наступний тиждень українською. "
            f"Враховуй слабкі місця цього тижня. Кожна ціль — одне речення, конкретна і досяжна. "
            f"Формат: '1. ... 2. ... 3. ...'"
        )

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 250, "temperature": 0.8}
        }).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
            data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        goals_text = resp["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Підсумок тижня
        run_count = habit_stats.get("run", 0)
        run_emoji = "🏆" if run_count >= 3 else ("👍" if run_count >= 1 else "😔")

        msg = (
            f"📅 <b>Підсумок тижня</b>\n\n"
            f"{run_emoji} Біг: {run_count}/7 днів\n"
            f"💧 Вода: {habit_stats.get('water',0)}/7 днів\n"
            f"🚿 Душ: {habit_stats.get('shower',0)}/7 днів\n"
        )
        if last_weight:
            diff = round(last_weight - 78.0, 1)
            msg += f"⚖️ Вага: {last_weight} кг (до цілі: -{diff} кг)\n"

        msg += f"\n🎯 <b>Цілі на наступний тиждень:</b>\n{goals_text}"

        send_telegram(msg)
        print("Week goals sent")
        state["last"] = today
        save_json_file(WEEK_GOALS_FILE, state)

    except Exception as e:
        print(f"check_week_goals error: {e}")


# ─── СЛІДКУВАННЯ ЗА КАЛЕНДАРЕМ — ЩО ЗАРАЗ ВІДБУВАЄТЬСЯ ──────────────────────

CALENDAR_CONTEXT_FILE = os.path.join(_DATA_DIR, "monitor_calendar_context.json")

def check_calendar_live():
    """
    Відстежує поточні події в календарі — що відбувається прямо зараз.
    Кожні 5 хвилин перевіряє:
    - Якщо подія почалась — "🔔 Почалась: [назва]"
    - За 15 хв до події — "⏰ Через 15 хв: [назва]"
    - Нагадування про незаплановані вихідні (нічого в календарі — пропонує щось корисне)
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    # Тільки в активний час (07:00–23:00)
    if not (7 <= h <= 23):
        return

    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    state = load_json_file(CALENDAR_CONTEXT_FILE, default={})

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now_utc = datetime.now(timezone.utc)
        # Вікно: наступні 20 хвилин
        window_end = now_utc + timedelta(minutes=20)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(now_utc.isoformat())}"
            f"&timeMax={urllib.parse.quote(window_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=5"
        )
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            events = json.loads(r.read()).get("items", [])

        for ev in events:
            summary = ev.get("summary", "(без назви)")
            # Пропускаємо зміни та автоматичні події
            s_lower = summary.lower()
            if any(x in s_lower for x in ["зміна", "shift", "нагадування"]):
                continue

            start_str = ev["start"].get("dateTime") or ev["start"].get("date")
            try:
                dt_start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                dt_local = dt_start + timedelta(hours=2)
                mins_until = int((dt_start - now_utc).total_seconds() / 60)
            except Exception:
                continue

            ev_key_15 = f"cal_15min_{ev['id']}_{dt_local.strftime('%Y%m%d%H%M')}"
            ev_key_now = f"cal_now_{ev['id']}_{dt_local.strftime('%Y%m%d%H%M')}"

            # За 15 хв
            if 12 <= mins_until <= 17 and not state.get(ev_key_15):
                send_telegram(
                    f"⏰ <b>Через 15 хв:</b> {esc(summary)}\n"
                    f"🕐 Початок о {dt_local.strftime('%H:%M')}"
                )
                state[ev_key_15] = True
                save_json_file(CALENDAR_CONTEXT_FILE, state)

            # Тільки що почалась (0–3 хв)
            elif 0 <= mins_until <= 3 and not state.get(ev_key_now):
                send_telegram(
                    f"🔔 <b>Починається зараз:</b> {esc(summary)}\n"
                    f"🕐 {dt_local.strftime('%H:%M')}"
                )
                state[ev_key_now] = True
                save_json_file(CALENDAR_CONTEXT_FILE, state)

    except Exception as e:
        print(f"check_calendar_live error: {e}")

# ─── НАСТРІЙ ВЕЧОРА (21:30) ───────────────────────────────────────────────────

MOOD_FILE = os.path.join(_DATA_DIR, "monitor_mood.json")

def check_mood_evening():
    """
    😊 О 21:30 питає про настрій дня — 1-5 зірок.
    Зберігає для тижневого аналізу + AI реакція.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    if not (h == 21 and 30 <= m < 35):
        return

    state = load_json_file(MOOD_FILE, default={})
    if state.get(today):
        return

    # Відправляємо з inline кнопками через bot API
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        keyboard = {
            "inline_keyboard": [[
                {"text": "😩 1", "callback_data": "mood_1"},
                {"text": "😕 2", "callback_data": "mood_2"},
                {"text": "😐 3", "callback_data": "mood_3"},
                {"text": "😊 4", "callback_data": "mood_4"},
                {"text": "🤩 5", "callback_data": "mood_5"},
            ]]
        }
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT,
            "text": f"✨ <b>Як пройшов день?</b>\n\nОціни свій день від 1 до 5:",
            "parse_mode": "HTML",
            "reply_markup": keyboard
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        state[today] = "asked"
        save_json_file(MOOD_FILE, state)
        print("Mood question sent")
    except Exception as e:
        print(f"check_mood_evening error: {e}")


# ─── ПРОГРЕС КРОКІВ (18:00) ───────────────────────────────────────────────────

STEPS_FILE = os.path.join(_DATA_DIR, "monitor_steps.json")

def check_step_goal():
    """
    👟 О 18:00 у вільний день — перевіряє кроки з Health даних.
    Якщо < 8000 — мотивує дійти. Якщо > 10000 — хвалить.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    if not (h == 18 and 0 <= m < 5):
        return

    state = load_json_file(STEPS_FILE, default={})
    if state.get(today):
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from context import get_shift_from_calendar
        shift_info = get_shift_from_calendar()
        if shift_info.get("today") != "free":
            return  # На зміні — не турбуємо
    except Exception:
        pass

    try:
        from storage import load_health as _lh
        health = _lh()
        steps = health.get(today, {}).get("steps")

        if not steps:
            send_telegram(
                "👟 <b>Кроки сьогодні</b>\n\n"
                "Не бачу даних Apple Health 😅\n"
                "Скільки пройшов? Надішли /зд щоб записати!\n\n"
                "<i>Ціль: 10 000 кроків на день</i>"
            )
            state[today] = True
            save_json_file(STEPS_FILE, state)
            print("Step goal check sent: no data")
        else:
            step_goal = 10000
            remaining = step_goal - steps
            bar_f = min(10, int(steps / step_goal * 10))
            bar = "🟩" * bar_f + "⬜" * (10 - bar_f)
            pct = int(steps / step_goal * 100)

            if steps >= 12000:
                msg = (
                    f"👟 <b>Кроки: {steps:,}</b> 🏆\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"Фантастично! Перевиконав ціль на {steps-step_goal:,} кроків!\n"
                    f"<i>💪 Так тримати!</i>"
                )
            elif steps >= 10000:
                msg = (
                    f"👟 <b>Кроки: {steps:,}</b> ✅\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"Ціль 10 000 виконана! Гарна робота! 🎯"
                )
            elif steps >= 7000:
                msg = (
                    f"👟 <b>Кроки: {steps:,}</b> ⚡️\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"До цілі ще {remaining:,} кроків — 20 хв прогулянки вирішить справу!\n"
                    f"<i>Майже там!</i>"
                )
            else:
                msg = (
                    f"👟 <b>Кроки: {steps:,}</b> 📉\n"
                    f"<code>[{bar}]</code> {pct}%\n\n"
                    f"До цілі ще {remaining:,} кроків.\n"
                    f"Час невеличкої прогулянки? 🚶‍♂️\n"
                    f"<i>Кожен крок → ближче до 78 кг!</i>"
                )
            send_telegram(msg)
            state[today] = True
            save_json_file(STEPS_FILE, state)
            print(f"Step goal check sent: {steps}")

    except Exception as e:
        print(f"check_step_goal error: {e}")


# ─── П'ЯТНИЧНИЙ ПІДСУМОК ТИЖНЯ (20:00) ──────────────────────────────────────

FRIDAY_RECAP_FILE = os.path.join(_DATA_DIR, "monitor_friday_recap.json")

def check_friday_recap():
    """
    🎉 П'ятниця 20:00 — підсумок робочого тижня + AI мотивація на вихідні.
    Статистика змін, звички, вага.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    if not (now_local.weekday() == 4 and h == 20 and 0 <= m < 10):
        return

    state = load_json_file(FRIDAY_RECAP_FILE, default={})
    if state.get(today):
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from storage import load_habits as _lh, load_weight as _lw

        # Дні цього тижня (Пн–Пт)
        week_days = [(now_local - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4, -1, -1)]
        habits_db = _lh()
        wdata = _lw()

        HABITS = [("run","🏃","Біг"),("water","💧","Вода"),("shower","🚿","Душ")]
        habit_stats = {}
        for hid, hico, hname in HABITS:
            habit_stats[hid] = sum(1 for d in week_days if habits_db.get(d, {}).get(hid) is True)

        # Вага за тиждень
        w_start = w_end = None
        if wdata:
            w_week = {d: wdata[d] for d in week_days if d in wdata}
            if len(w_week) >= 2:
                sk = sorted(w_week.keys())
                w_start = w_week[sk[0]]
                w_end   = w_week[sk[-1]]

        lines_out = [
            f"🎉 <b>КІНЕЦЬ ТИЖНЯ — П'ятниця!</b>",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"",
        ]

        # Звички за тиждень
        lines_out.append("💪 <b>Звички за тиждень (Пн–Пт)</b>")
        for hid, hico, hname in HABITS:
            count = habit_stats[hid]
            dots = "🟩" * count + "⬜" * (5 - count)
            grade = "🏆" if count == 5 else ("⭐️" if count >= 3 else ("👍" if count >= 1 else "💤"))
            lines_out.append(f"   {hico} {hname}: {count}/5  {dots}  {grade}")
        lines_out.append("")

        # Вага
        if w_start and w_end:
            diff = round(w_end - w_start, 1)
            to_goal = round(w_end - 78.0, 1)
            trend = f"↗️ +{diff} кг" if diff > 0 else f"↘️ {diff} кг"
            lines_out.append(f"⚖️ <b>Вага:</b> {w_start}→{w_end} кг  {trend}")
            if to_goal > 0:
                lines_out.append(f"   🎯 До цілі 78 кг: ще -{to_goal} кг")
            else:
                lines_out.append("   🏆 ЦІЛЬ ДОСЯГНУТА!")
            lines_out.append("")

        # AI підсумок + план вихідних
        gemini_key = os.environ.get("GEMINI_API_KEY","")
        if gemini_key:
            try:
                run_c = habit_stats.get("run",0)
                w_info = f", вага {w_end} кг (ціль 78)" if w_end else ""
                prompt = (
                    f"Тиждень Олега: біг {run_c}/5 днів, вода {habit_stats.get('water',0)}/5{w_info}. "
                    f"Сьогодні п'ятниця. Дай: 1) одне речення підсумку тижня; "
                    f"2) одна конкретна пропозиція чим зайнятись на вихідних для здоров'я. "
                    f"Коротко, по-дружньому."
                )
                payload = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"maxOutputTokens":150,"temperature":0.8}}).encode()
                req = urllib.request.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
                    data=payload, headers={"Content-Type":"application/json"}, method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    resp = json.loads(r.read())
                ai_text = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
                lines_out.append(f"🤖 <i>{ai_text}</i>")
                lines_out.append("")
            except Exception as e:
                print(f"friday recap AI error: {e}")

        lines_out.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines_out.append("🎊 Хороших вихідних, Олеже!")

        send_telegram("\n".join(lines_out))
        state[today] = True
        save_json_file(FRIDAY_RECAP_FILE, state)
        print("Friday recap sent")

    except Exception as e:
        print(f"check_friday_recap error: {e}")


# ─── ТРЕНД ВАГИ — АЛЕРТ ЯКЩО РОСТЕ 3 ДНІ ПОСПІЛЬ ────────────────────────────

WEIGHT_TREND_FILE = os.path.join(_DATA_DIR, "monitor_weight_trend.json")

def check_weight_trend_alert():
    """
    ⚠️ Якщо вага росте 3+ дні поспіль — проактивний алерт о 10:00.
    Мотивує скоригувати харчування/активність.
    """
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    today = now_local.strftime("%Y-%m-%d")

    if not (h == 10 and 0 <= m < 5):
        return

    state = load_json_file(WEIGHT_TREND_FILE, default={})
    week_key = now_local.strftime("%Y-W%W")
    if state.get(week_key):
        return

    try:
        import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from storage import load_weight as _lw
        wdata = _lw()
        if not wdata or len(wdata) < 3:
            return

        sorted_keys = sorted(wdata.keys())[-5:]
        w_vals = [wdata[k] for k in sorted_keys]

        # Перевіряємо: 3+ дні росту поспіль
        rising_days = 0
        for i in range(len(w_vals) - 1, 0, -1):
            if w_vals[i] > w_vals[i-1]:
                rising_days += 1
            else:
                break

        if rising_days < 3:
            return

        # Графік останніх 5 вимірювань
        w_min = min(w_vals) - 0.3
        w_max = max(w_vals) + 0.3
        blocks = ["⬜","🟦","🟦","🟩","🟩","🟨","🟧","🟥"]
        bars = []
        for v in w_vals:
            b = int((v - w_min) / max(w_max - w_min, 0.1) * 7)
            bars.append(blocks[max(0, min(7, b))])

        total_rise = round(w_vals[-1] - w_vals[-rising_days-1], 1)
        to_goal = round(w_vals[-1] - 78.0, 1)

        send_telegram(
            f"⚠️ <b>Вага зростає {rising_days} дні поспіль!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"<code>{''.join(bars)}</code>  ↗️ +{total_rise} кг\n"
            f"Зараз: <b>{w_vals[-1]} кг</b>  |  До 78 кг: -{to_goal}\n\n"
            f"🔍 Можливі причини:\n"
            f"• 💧 Недостатньо води\n"
            f"• 🍽 Пізня їжа або великі порції\n"
            f"• 🏃 Мало руху\n\n"
            f"<i>Маленькі корекції → великі результати!</i>"
        )
        state[week_key] = True
        save_json_file(WEIGHT_TREND_FILE, state)
        print(f"Weight trend alert: {rising_days} days rising")

    except Exception as e:
        print(f"check_weight_trend_alert error: {e}")
