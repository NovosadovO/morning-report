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

def is_spam(sender, subject):
    return _classify_email(sender, subject) == "spam"

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
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json_file(path, data):
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
    """Робить короткий summary через Gemini API."""
    api_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")
    if not api_key or not text or text == "—":
        return None
    try:
        text_trimmed = text[:max_input]
        prompt = (
            "Прочитай цей email і дай ДУЖЕ короткий опис (1-2 речення українською) "
            "— про що цей лист, що від тебе вимагається або що важливо знати. "
            "Без зайвих слів, тільки суть.\n\nЛист:\n" + text_trimmed
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        summary = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return summary[:300]
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

        # ── Стратегія: Gmail категорії через X-GM-RAW ──────────────────────────
        # Primary = листи від людей (Gmail сам класифікує)
        # Promotions/Updates/Social = решта
        # Беремо непрочитані Primary + останні 10 прочитані Primary

        _, p_unseen = mail.search(None, 'X-GM-RAW "category:primary is:unread"')
        _, p_all    = mail.search(None, 'X-GM-RAW "category:primary"')
        _, o_unseen = mail.search(None, 'X-GM-RAW "-category:primary is:unread"')

        primary_unread_ids = p_unseen[0].split()
        primary_all_ids    = p_all[0].split()
        other_unread_ids   = o_unseen[0].split()

        # Об'єднуємо: всі непрочитані primary + останні 10 прочитаних primary
        recent_primary = list(dict.fromkeys(
            primary_unread_ids + primary_all_ids[-10:]
        ))
        recent_primary = sorted(recent_primary, key=lambda x: int(x))[::-1]

        # Інші категорії — тільки непрочитані, останні 10
        recent_other = sorted(other_unread_ids, key=lambda x: int(x))[::-1][:10]

        seen = set(load_json_file(SEEN_EMAIL_FILE, default=[]))
        primary = []
        other   = []

        # ── Primary листи ──────────────────────────────────────────────────────
        for uid in recent_primary:
            if len(primary) >= 5:
                break
            _, msg_data = mail.fetch(uid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender  = _imap_decode_header(msg.get("From", ""))
            is_unread = uid in primary_unread_ids

            # Додатковий фільтр: YouTube, Duolingo, Google нотифікації — drop
            cls = _classify_email(sender, subject)
            if cls == "spam":
                continue

            body = _imap_get_body(msg)
            preview = body[:120].replace("\n", " ").strip()
            ai_sum = _gemini_summarize(body) if body else ""
            primary.append((subject, sender, preview, is_unread, ai_sum))

        # ── Інші категорії (Promotions/Updates/Social) ─────────────────────────
        for uid in recent_other:
            if len(other) >= 3:
                break
            _, msg_data = mail.fetch(uid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender  = _imap_decode_header(msg.get("From", ""))

            # Явний спам — skip
            if _classify_email(sender, subject) == "spam":
                continue

            other.append((subject, sender, "", True))

        # Зберігаємо seen
        save_json_file(SEEN_EMAIL_FILE, list(seen)[-500:])
        mail.logout()

        lines = ["📩 <b>━━━ ЛИСТИ ━━━</b>\n"]

        if primary:
            unread_count = sum(1 for _, _, _, u, _ in primary if u)
            header = "📥 <b>ОСНОВНІ</b>" + (f"  🔴 {unread_count} нових" if unread_count else "")
            lines.append(header)
            for s, snd, p, u, ai_sum in primary:
                lines.append(format_email_item(s, snd, p, u, ai_summary=ai_sum))
            lines.append("")

        if other:
            lines.append("📂 <b>ІНШІ</b>")
            for s, snd, p, u in other:
                lines.append(format_email_item(s, snd, p, u))

        if not primary and not other:
            lines.append("✅ Немає листів")

        return "\n".join(lines)

    except Exception as e:
        print(f"get_emails IMAP error: {e}")
        return f"📬 <b>Email</b>\n⚠️ Помилка: {e}"


# ─── 4b. МИТТЄВІ СПОВІЩЕННЯ ПРО НОВІ ЛИСТИ ───────────────────────────────────

ALERT_EMAIL_FILE = os.path.join(_DATA_DIR, "monitor_alert_emails.json")

def check_new_emails():
    """Перевіряє листи що прийшли за останні 12 хвилин — шле сповіщення."""
    try:
        mail = _imap_connect()
        mail.select("INBOX")

        # Шукаємо ТІЛЬКИ Primary листи за останні 12 хвилин
        since_dt = datetime.now(timezone.utc) - timedelta(minutes=12)
        since_str = since_dt.strftime("%d-%b-%Y")
        # Gmail X-GM-RAW: category:primary + unread + since
        _, data = mail.search(None, f'X-GM-RAW "category:primary is:unread after:{since_dt.strftime("%Y/%m/%d")}"')
        unseen_ids = data[0].split()

        if not unseen_ids:
            mail.logout()
            return

        new_alerts = []

        for uid in unseen_ids[-10:]:
            _, msg_data = mail.fetch(uid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            # Перевіряємо точний час отримання
            date_str = msg.get("Date", "")
            try:
                import email.utils as _eu
                msg_dt = datetime.fromtimestamp(_eu.parsedate_to_datetime(date_str).timestamp(), tz=timezone.utc)
                if msg_dt < since_dt:
                    continue
            except Exception:
                pass

            subject = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender  = _imap_decode_header(msg.get("From", ""))
            # Додатковий фільтр — YouTube, Duolingo тощо що Gmail кладе в Primary
            if _classify_email(sender, subject) != "spam":
                new_alerts.append((subject, sender))

        mail.logout()

        for subject, sender in new_alerts:
            caption = (
                f"📩 <b>━━ НОВИЙ ЛИСТ ━━</b>\n\n"
                f"📨 <b>{esc(subject[:70])}</b>\n"
                f"👤 <code>{esc(sender[:55])}</code>"
            )
            _send_telegram_photo(
                "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2FTPPUfhLfZwJmqMoezuxQM%2Fmail_banner_v2.png",
                caption
            )
            print(f"Alert sent: {subject[:50]}")

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

def main():
    now = datetime.now(timezone.utc)
    now_local = now + timedelta(hours=2)
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

    parts = [f"🕐 <b>Звіт {local_time}  ·  {local_date}</b>\n<i>3х годинний репорт</i>"]

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
    """О 7:00 у вихідні дні шле план дня з календаря."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    if not (h == 7 and 0 <= m < 5):
        return

    state = load_json_file(MORNING_BRIEF_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    # Перевіряємо чи є зміна сьогодні
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

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

        has_shift = any("зміна" in e.get("summary","").lower() for e in events)

        lines = [f"🌅 <b>Доброго ранку! План на {now_local.strftime('%d.%m')}:</b>\n"]

        if has_shift:
            shift_ev = next(e for e in events if "зміна" in e.get("summary","").lower())
            start = shift_ev["start"].get("dateTime","")
            try:
                dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                lines.append(f"💼 Робочий день — зміна о <b>{dt.strftime('%H:%M')}</b>")
            except:
                lines.append("💼 Сьогодні є зміна")
        else:
            lines.append("🏖 Вихідний день — відпочивай!")

        lines.append("")
        for ev in events:
            summary = ev.get("summary","")
            if "зміна" in summary.lower() or "нагадування" in summary.lower():
                continue
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            try:
                dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                t = dt.strftime("%H:%M")
            except:
                t = ""
            lines.append(f"• {t} {esc(summary)}")

        send_telegram("\n".join(lines))
        state["last"] = today
        save_json_file(MORNING_BRIEF_FILE, state)
        print(f"Morning brief sent for {today}")

    except Exception as e:
        print(f"check_morning_brief error: {e}")


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
    """Щопонеділка о 9:00 шле статистику звичок за минулий тиждень."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.weekday() == 0 and now_local.hour == 9 and now_local.minute < 5):
        return

    state = load_json_file(HABIT_STATS_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    # Читаємо дані з habits.py через storage (GitHub)
    try:
        import sys as _sys
        _sys.path.insert(0, _DIR)
        from storage import load_habits as _lh
        data = _lh()
        if not data:
            return

        # Останні 7 днів
        from datetime import date
        days = [(now_local - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 8)]
        days.reverse()

        lines = [f"📊 <b>Статистика звичок за тиждень</b>\n({days[0][5:]} — {days[-1][5:]})\n"]

        habit_names = data.get("habits", [])
        logs = data.get("logs", {})

        for habit in habit_names:
            count = sum(1 for d in days if logs.get(d, {}).get(habit) == True)
            bar   = "🟩" * count + "⬜" * (7 - count)
            lines.append(f"{bar} <b>{esc(habit)}</b> {count}/7")

        send_telegram("\n".join(lines))
        state["last"] = today
        save_json_file(HABIT_STATS_FILE, state)
        print("Weekly habit stats sent")

    except Exception as e:
        print(f"check_weekly_habit_stats error: {e}")


# ─── НАГАДУВАННЯ ПИТИ ВОДУ (кожні 2г, 8:00–20:00 у вихідні) ─────────────────

WATER_FILE = os.path.join(_DATA_DIR, "monitor_water.json")

def check_water_reminder():
    """Нагадування пити воду кожні 2г у вихідні між 8:00 і 20:00."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    if not (8 <= h <= 20 and 0 <= m < 5):
        return

    if h % 2 != 0:
        return

    # Перевіряємо чи є зміна сьогодні (тоді не нагадуємо)
    state = load_json_file(WATER_FILE, default={})
    key = now_local.strftime("%Y-%m-%d-%H")
    if state.get(key):
        return

    # Простий check — якщо є зміна пропускаємо
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
                return  # робочий день — не нагадуємо
        except:
            pass

    send_telegram("💧 <b>Час випити воду!</b>\nВипий склянку води зараз 🥤")
    state[key] = True
    save_json_file(WATER_FILE, state)
    print(f"Water reminder sent at {h}:00")


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
    """О 19:00 надсилає підсумок дня — події з календаря + звички."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    if not (h == 19 and m < 5):
        return

    state = load_json_file(DAY_SUMMARY_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id = "novosadovoleg%40gmail.com"

        # Всі події за сьогодні
        day_start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        day_end_utc   = now_local.replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(hours=2)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(day_start_utc.isoformat())}"
            f"&timeMax={urllib.parse.quote(day_end_utc.isoformat())}"
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

        # Статуси виконання подій (з кнопок ✅/❌)
        results_file = os.path.join(_DATA_DIR, "monitor_event_results.json")
        results = load_json_file(results_file, default={})

        # Звички через storage (GitHub)
        try:
            import sys as _sys; _sys.path.insert(0, _DIR)
            from storage import load_habits as _lh
            habits_db = _lh()
            today_habits = habits_db.get(today, {})
        except Exception as _e:
            print(f"habits load error in day summary: {_e}")
            today_habits = {}

        lines = []
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📋 <b>ПІДСУМОК ДНЯ</b>  {now_local.strftime('%d.%m.%Y')}")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")

        # ── КАЛЕНДАР ──
        # Фільтруємо — тільки реальні події (не звички з habits.py)
        HABIT_NAMES = {"холодний душ", "біг", "вода", "трав'яний чай", "сауна", "сон"}
        cal_events = [
            ev for ev in events
            if not any(h in ev.get("summary", "").lower() for h in HABIT_NAMES)
        ]

        if cal_events:
            lines.append("\n🗓 <b>Події дня</b>")
            for ev in cal_events:
                summary  = ev.get("summary", "(без назви)")
                start_raw = ev["start"].get("dateTime") or ev["start"].get("date")
                end_raw   = ev["end"].get("dateTime")   or ev["end"].get("date")
                ev_id     = ev.get("id", "")

                try:
                    start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                    end_dt   = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    t_start  = (start_dt + timedelta(hours=2)).strftime("%H:%M")
                    t_end    = (end_dt   + timedelta(hours=2)).strftime("%H:%M")
                    time_str = f"{t_start}–{t_end}"
                except Exception:
                    time_str = ""

                done_key_prefix = f"done_{ev_id}_"
                result = next((v for k, v in results.items() if done_key_prefix in k), None)

                s_lower = summary.lower()
                if "нічна" in s_lower:      ev_emoji = "🌙"
                elif "рання" in s_lower:    ev_emoji = "☀️"
                elif "birthday" in s_lower or "народження" in s_lower: ev_emoji = "🎂"
                elif "зустріч" in s_lower or "meet" in s_lower:        ev_emoji = "🤝"
                else:                       ev_emoji = "📅"

                if result == "yes":   status = "✅"
                elif result == "no":  status = "❌"
                else:                 status = "—"

                lines.append(f"\n{ev_emoji} <b>{esc(summary)}</b>  {status}")
                if time_str:
                    lines.append(f"    🕐 {time_str}")
        else:
            lines.append("\n🗓 <b>Події дня</b>\n    Вільний день")

        # ── ЗДОРОВ'Я ──
        HEALTH_HABITS = [
            ("shower", "🚿", "Холодний душ"),
            ("run",    "🏃", "Біг"),
            ("water",  "💧", "Вода 2л+"),
            ("tea",    "🍵", "Трав'яний чай"),
            ("sauna",  "🧖", "Сауна"),
        ]
        lines.append("\n\n💪 <b>Здоров'я сьогодні</b>")
        done_count = 0
        for hid, hemoji, hname in HEALTH_HABITS:
            val = today_habits.get(hid)
            if val is True:
                mark = "✅"
                done_count += 1
            elif val is False:
                mark = "❌"
            else:
                mark = "○"  # не відповів
            lines.append(f"    {hemoji} {hname}  {mark}")

        # Сон
        sleep_val = today_habits.get("sleep")
        if sleep_val:
            sleep_icon = "😊" if sleep_val >= 8 else ("🙂" if sleep_val >= 7 else ("😐" if sleep_val >= 6 else "😩"))
            lines.append(f"    😴 Сон  {sleep_val}г  {sleep_icon}")

        # Загальний рахунок — зірки
        total = len(HEALTH_HABITS)
        pct = int(done_count / total * 100) if total else 0
        stars_filled = done_count
        stars_empty  = total - done_count
        bar = "⭐️" * stars_filled + "☆" * stars_empty

        if pct == 100:
            grade = "🏆 Ідеальний день!"
        elif pct >= 80:
            grade = "💪 Відмінно!"
        elif pct >= 60:
            grade = "👍 Непогано"
        elif pct >= 40:
            grade = "😐 Є над чим працювати"
        else:
            grade = "💤 Слабкий день"

        lines.append(f"\n    {bar}  <b>{done_count}/{total}</b>")
        lines.append(f"    {grade}")

        # ── ЛІКИ ──
        try:
            from storage import load_meds as _lmeds
            meds_db = _lmeds()
            meds_taken = meds_db.get(today)
            if meds_taken is True:
                lines.append("\n\n💊 <b>Armolopid Plus</b>  ✅")
            elif meds_taken is False:
                lines.append("\n\n💊 <b>Armolopid Plus</b>  ❌ Не прийнято!")
            else:
                lines.append("\n\n💊 <b>Armolopid Plus</b>  ○ Не відмічено")
        except Exception as _me:
            print(f"meds in day summary error: {_me}")

        # ── ВАГА ──
        try:
            from storage import load_weight as _lw
            weight_db = _lw()
            if weight_db:
                last_w_date = max(weight_db.keys())
                last_w_val  = weight_db[last_w_date]
                days_ago = (now_local.date() - datetime.strptime(last_w_date, "%Y-%m-%d").date()).days
                if days_ago == 0:
                    lines.append(f"\n⚖️ <b>Вага сьогодні</b>  <b>{last_w_val} кг</b> ✅")
                else:
                    lines.append(f"\n⚖️ <b>Вага</b>  {last_w_val} кг  <i>({days_ago} дн. тому)</i>")
        except Exception as _we:
            print(f"weight in day summary error: {_we}")

        # ── HEALTH SCORE ──
        try:
            from storage import load_health as _lhealth
            health_db = _lhealth()
            today_health = health_db.get(today, {})
            if today_health:
                h_lines = []
                if today_health.get("steps"):
                    h_lines.append(f"👟 {today_health['steps']:,} кр.")
                if today_health.get("sleep_hours"):
                    h_lines.append(f"😴 {today_health['sleep_hours']}г")
                if today_health.get("heart_rate"):
                    h_lines.append(f"❤️ {today_health['heart_rate']} bpm")
                if today_health.get("hrv"):
                    h_lines.append(f"💓 HRV {today_health['hrv']}")
                if today_health.get("health_score"):
                    sc = today_health['health_score']
                    sc_emoji = "🟢" if sc >= 75 else ("🟡" if sc >= 55 else "🔴")
                    h_lines.append(f"{sc_emoji} Score {sc}/100")
                if h_lines:
                    lines.append(f"\n\n🍎 <b>Apple Health</b>")
                    lines.append(f"    {' · '.join(h_lines)}")
            else:
                lines.append(f"\n\n🍎 <b>Apple Health</b>  <i>немає даних — надішли /зд</i>")
        except Exception as _he:
            print(f"health in day summary error: {_he}")

        # ── ВОДА ──
        try:
            water_state = load_json_file(WATER_FILE, default={})
            water_count = water_state.get(today, 0)
            water_ml = water_count * 250
            water_bar = "💧" * water_count + "○" * max(0, 8 - water_count)
            lines.append(f"\n\n💧 <b>Вода</b>  {water_ml} мл  {water_bar}")
        except Exception as _wt:
            print(f"water in day summary error: {_wt}")

        lines.append(f"\n\n━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("Гарного вечора! 🌙")

        send_telegram("\n".join(lines))
        print("Day summary sent")

        state["last"] = today
        save_json_file(DAY_SUMMARY_FILE, state)

    except Exception as e:
        print(f"check_day_summary error: {e}")

# ─── ТРАФІК ПЕРЕД ЗМІНОЮ ─────────────────────────────────────────────────────

TRAFFIC_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_traffic_alert.json")

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

# Зворотна сумісність
IGNORE_SENDERS = list(_SPAM_SENDERS)
IGNORE_SUBJECTS = list(_SPAM_SUBJECTS)

def is_spam(sender, subject):
    return _classify_email(sender, subject) == "spam"

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
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json_file(path, data):
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
    """Робить короткий summary через Gemini API."""
    api_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")
    if not api_key or not text or text == "—":
        return None
    try:
        text_trimmed = text[:max_input]
        prompt = (
            "Прочитай цей email і дай ДУЖЕ короткий опис (1-2 речення українською) "
            "— про що цей лист, що від тебе вимагається або що важливо знати. "
            "Без зайвих слів, тільки суть.\n\nЛист:\n" + text_trimmed
        )
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        summary = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return summary[:300]
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

        # Непрочитані + останні 20 прочитані
        _, unseen_data = mail.search(None, "UNSEEN")
        _, all_data = mail.search(None, "ALL")
        unseen_ids = unseen_data[0].split()
        all_ids = all_data[0].split()
        # Об'єднуємо: всі непрочитані + останні 20
        recent_read = all_ids[-20:]
        combined = list(dict.fromkeys(unseen_ids + recent_read))  # без дублів
        recent_ids = sorted(combined, key=lambda x: int(x))[::-1]  # від нових до старих

        seen = set(load_json_file(SEEN_EMAIL_FILE, default=[]))
        primary = []
        other   = []

        for uid in recent_ids:
            _, msg_data = mail.fetch(uid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender  = _imap_decode_header(msg.get("From", ""))
            is_unread = uid.decode() not in seen

            cls = _classify_email(sender, subject)
            if cls == "spam":
                continue

            body = _imap_get_body(msg)
            preview = body[:120].replace("\n", " ").strip()

            if cls == "real":
                if len(primary) < 5:
                    ai_sum = _gemini_summarize(body) if body else ""
                    primary.append((subject, sender, preview, is_unread, ai_sum))
            else:  # promo
                if len(other) < 3:
                    other.append((subject, sender, preview, is_unread))

            if len(primary) >= 5 and len(other) >= 3:
                break

        # Зберігаємо seen (по Message-ID, не IMAP UID)
        save_json_file(SEEN_EMAIL_FILE, list(seen)[-500:])
        mail.logout()

        lines = ["📩 <b>━━━ ЛИСТИ ━━━</b>\n"]

        if primary:
            unread_count = sum(1 for _, _, _, u, _ in primary if u)
            header = "📥 <b>ОСНОВНІ</b>" + (f"  🔴 {unread_count} нових" if unread_count else "")
            lines.append(header)
            for s, snd, p, u, ai_sum in primary:
                lines.append(format_email_item(s, snd, p, u, ai_summary=ai_sum))
            lines.append("")

        if other:
            lines.append("📂 <b>ІНШІ</b>")
            for s, snd, p, u in other:
                lines.append(format_email_item(s, snd, p, u))

        if not primary and not other:
            lines.append("✅ Немає листів")

        return "\n".join(lines)

    except Exception as e:
        print(f"get_emails IMAP error: {e}")
        return f"📬 <b>Email</b>\n⚠️ Помилка: {e}"


# ─── 4b. МИТТЄВІ СПОВІЩЕННЯ ПРО НОВІ ЛИСТИ ───────────────────────────────────

ALERT_EMAIL_FILE = os.path.join(_DATA_DIR, "monitor_alert_emails.json")

def check_new_emails():
    """Перевіряє листи що прийшли за останні 12 хвилин — шле сповіщення."""
    try:
        mail = _imap_connect()
        mail.select("INBOX")

        # Шукаємо ТІЛЬКИ листи що прийшли за останні 12 хвилин по IMAP date
        since_dt = datetime.now(timezone.utc) - timedelta(minutes=12)
        since_str = since_dt.strftime("%d-%b-%Y")  # IMAP формат: 02-May-2026
        _, data = mail.search(None, f'(UNSEEN SINCE "{since_str}")')
        unseen_ids = data[0].split()

        if not unseen_ids:
            mail.logout()
            return

        new_alerts = []

        for uid in unseen_ids[-10:]:
            _, msg_data = mail.fetch(uid, "(RFC822.HEADER)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            # Перевіряємо точний час отримання
            date_str = msg.get("Date", "")
            try:
                import email.utils as _eu
                msg_dt = datetime.fromtimestamp(_eu.parsedate_to_datetime(date_str).timestamp(), tz=timezone.utc)
                if msg_dt < since_dt:
                    continue  # старіший ніж 12 хв
            except Exception:
                pass

            subject = _imap_decode_header(msg.get("Subject", "(без теми)"))
            sender  = _imap_decode_header(msg.get("From", ""))
            if _classify_email(sender, subject) == "real":
                new_alerts.append((subject, sender))

        mail.logout()

        for subject, sender in new_alerts:
            caption = (
                f"📩 <b>━━ НОВИЙ ЛИСТ ━━</b>\n\n"
                f"📨 <b>{esc(subject[:70])}</b>\n"
                f"👤 <code>{esc(sender[:55])}</code>"
            )
            _send_telegram_photo(
                "https://storage.googleapis.com/runable-templates/cli-uploads%2F1zsprqn6ymqOFgAJnNEK2HbTycMPBvLc%2FTPPUfhLfZwJmqMoezuxQM%2Fmail_banner_v2.png",
                caption
            )
            print(f"Alert sent: {subject[:50]}")

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

def main():
    now = datetime.now(timezone.utc)
    now_local = now + timedelta(hours=2)
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

    parts = [f"🕐 <b>Звіт {local_time}  ·  {local_date}</b>\n<i>3х годинний репорт</i>"]

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
    """О 7:00 у вихідні дні шле план дня з календаря."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    if not (h == 7 and 0 <= m < 5):
        return

    state = load_json_file(MORNING_BRIEF_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    # Перевіряємо чи є зміна сьогодні
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

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

        has_shift = any("зміна" in e.get("summary","").lower() for e in events)

        lines = [f"🌅 <b>Доброго ранку! План на {now_local.strftime('%d.%m')}:</b>\n"]

        if has_shift:
            shift_ev = next(e for e in events if "зміна" in e.get("summary","").lower())
            start = shift_ev["start"].get("dateTime","")
            try:
                dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                lines.append(f"💼 Робочий день — зміна о <b>{dt.strftime('%H:%M')}</b>")
            except:
                lines.append("💼 Сьогодні є зміна")
        else:
            lines.append("🏖 Вихідний день — відпочивай!")

        lines.append("")
        for ev in events:
            summary = ev.get("summary","")
            if "зміна" in summary.lower() or "нагадування" in summary.lower():
                continue
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            try:
                dt = datetime.fromisoformat(start.replace("Z","+00:00")) + timedelta(hours=2)
                t = dt.strftime("%H:%M")
            except:
                t = ""
            lines.append(f"• {t} {esc(summary)}")

        send_telegram("\n".join(lines))
        state["last"] = today
        save_json_file(MORNING_BRIEF_FILE, state)
        print(f"Morning brief sent for {today}")

    except Exception as e:
        print(f"check_morning_brief error: {e}")


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
    """Щопонеділка о 9:00 шле статистику звичок за минулий тиждень."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    if not (now_local.weekday() == 0 and now_local.hour == 9 and now_local.minute < 5):
        return

    state = load_json_file(HABIT_STATS_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    # Читаємо дані з habits.py через storage (GitHub)
    try:
        import sys as _sys
        _sys.path.insert(0, _DIR)
        from storage import load_habits as _lh
        data = _lh()
        if not data:
            return

        # Останні 7 днів
        from datetime import date
        days = [(now_local - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 8)]
        days.reverse()

        lines = [f"📊 <b>Статистика звичок за тиждень</b>\n({days[0][5:]} — {days[-1][5:]})\n"]

        habit_names = data.get("habits", [])
        logs = data.get("logs", {})

        for habit in habit_names:
            count = sum(1 for d in days if logs.get(d, {}).get(habit) == True)
            bar   = "🟩" * count + "⬜" * (7 - count)
            lines.append(f"{bar} <b>{esc(habit)}</b> {count}/7")

        send_telegram("\n".join(lines))
        state["last"] = today
        save_json_file(HABIT_STATS_FILE, state)
        print("Weekly habit stats sent")

    except Exception as e:
        print(f"check_weekly_habit_stats error: {e}")


# ─── НАГАДУВАННЯ ПИТИ ВОДУ (кожні 2г, 8:00–20:00 у вихідні) ─────────────────

WATER_FILE = os.path.join(_DATA_DIR, "monitor_water.json")

def check_water_reminder():
    """Нагадування пити воду кожні 2г у вихідні між 8:00 і 20:00."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute

    if not (8 <= h <= 20 and 0 <= m < 5):
        return

    if h % 2 != 0:
        return

    # Перевіряємо чи є зміна сьогодні (тоді не нагадуємо)
    state = load_json_file(WATER_FILE, default={})
    key = now_local.strftime("%Y-%m-%d-%H")
    if state.get(key):
        return

    # Простий check — якщо є зміна пропускаємо
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
                return  # робочий день — не нагадуємо
        except:
            pass

    send_telegram("💧 <b>Час випити воду!</b>\nВипий склянку води зараз 🥤")
    state[key] = True
    save_json_file(WATER_FILE, state)
    print(f"Water reminder sent at {h}:00")


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
    """О 19:00 надсилає підсумок дня — події з календаря + звички."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    if not (h == 19 and m < 5):
        return

    state = load_json_file(DAY_SUMMARY_FILE, default={})
    today = now_local.strftime("%Y-%m-%d")
    if state.get("last") == today:
        return

    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return

    try:
        creds_data = json.loads(creds_json)
        token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id = "novosadovoleg%40gmail.com"

        # Всі події за сьогодні
        day_start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        day_end_utc   = now_local.replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(hours=2)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(day_start_utc.isoformat())}"
            f"&timeMax={urllib.parse.quote(day_end_utc.isoformat())}"
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

        # Статуси виконання подій (з кнопок ✅/❌)
        results_file = os.path.join(_DATA_DIR, "monitor_event_results.json")
        results = load_json_file(results_file, default={})

        # Звички через storage (GitHub)
        try:
            import sys as _sys; _sys.path.insert(0, _DIR)
            from storage import load_habits as _lh
            habits_db = _lh()
            today_habits = habits_db.get(today, {})
        except Exception as _e:
            print(f"habits load error in day summary: {_e}")
            today_habits = {}

        lines = []
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📋 <b>ПІДСУМОК ДНЯ</b>  {now_local.strftime('%d.%m.%Y')}")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")

        # ── КАЛЕНДАР ──
        # Фільтруємо — тільки реальні події (не звички з habits.py)
        HABIT_NAMES = {"холодний душ", "біг", "вода", "трав'яний чай", "сауна", "сон"}
        cal_events = [
            ev for ev in events
            if not any(h in ev.get("summary", "").lower() for h in HABIT_NAMES)
        ]

        if cal_events:
            lines.append("\n🗓 <b>Події дня</b>")
            for ev in cal_events:
                summary  = ev.get("summary", "(без назви)")
                start_raw = ev["start"].get("dateTime") or ev["start"].get("date")
                end_raw   = ev["end"].get("dateTime")   or ev["end"].get("date")
                ev_id     = ev.get("id", "")

                try:
                    start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                    end_dt   = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    t_start  = (start_dt + timedelta(hours=2)).strftime("%H:%M")
                    t_end    = (end_dt   + timedelta(hours=2)).strftime("%H:%M")
                    time_str = f"{t_start}–{t_end}"
                except Exception:
                    time_str = ""

                done_key_prefix = f"done_{ev_id}_"
                result = next((v for k, v in results.items() if done_key_prefix in k), None)

                s_lower = summary.lower()
                if "нічна" in s_lower:      ev_emoji = "🌙"
                elif "рання" in s_lower:    ev_emoji = "☀️"
                elif "birthday" in s_lower or "народження" in s_lower: ev_emoji = "🎂"
                elif "зустріч" in s_lower or "meet" in s_lower:        ev_emoji = "🤝"
                else:                       ev_emoji = "📅"

                if result == "yes":   status = "✅"
                elif result == "no":  status = "❌"
                else:                 status = "—"

                lines.append(f"\n{ev_emoji} <b>{esc(summary)}</b>  {status}")
                if time_str:
                    lines.append(f"    🕐 {time_str}")
        else:
            lines.append("\n🗓 <b>Події дня</b>\n    Вільний день")

        # ── ЗДОРОВ'Я ──
        HEALTH_HABITS = [
            ("shower", "🚿", "Холодний душ"),
            ("run",    "🏃", "Біг"),
            ("water",  "💧", "Вода 2л+"),
            ("tea",    "🍵", "Трав'яний чай"),
            ("sauna",  "🧖", "Сауна"),
        ]
        lines.append("\n\n💪 <b>Здоров'я сьогодні</b>")
        done_count = 0
        for hid, hemoji, hname in HEALTH_HABITS:
            val = today_habits.get(hid)
            if val is True:
                mark = "✅"
                done_count += 1
            elif val is False:
                mark = "❌"
            else:
                mark = "○"  # не відповів
            lines.append(f"    {hemoji} {hname}  {mark}")

        # Сон
        sleep_val = today_habits.get("sleep")
        if sleep_val:
            sleep_icon = "😊" if sleep_val >= 8 else ("🙂" if sleep_val >= 7 else ("😐" if sleep_val >= 6 else "😩"))
            lines.append(f"    😴 Сон  {sleep_val}г  {sleep_icon}")

        # Загальний рахунок — зірки
        total = len(HEALTH_HABITS)
        pct = int(done_count / total * 100) if total else 0
        stars_filled = done_count
        stars_empty  = total - done_count
        bar = "⭐️" * stars_filled + "☆" * stars_empty

        if pct == 100:
            grade = "🏆 Ідеальний день!"
        elif pct >= 80:
            grade = "💪 Відмінно!"
        elif pct >= 60:
            grade = "👍 Непогано"
        elif pct >= 40:
            grade = "😐 Є над чим працювати"
        else:
            grade = "💤 Слабкий день"

        lines.append(f"\n    {bar}  <b>{done_count}/{total}</b>")
        lines.append(f"    {grade}")

        # ── ЛІКИ ──
        try:
            from storage import load_meds as _lmeds
            meds_db = _lmeds()
            meds_taken = meds_db.get(today)
            if meds_taken is True:
                lines.append("\n\n💊 <b>Armolopid Plus</b>  ✅")
            elif meds_taken is False:
                lines.append("\n\n💊 <b>Armolopid Plus</b>  ❌ Не прийнято!")
            else:
                lines.append("\n\n💊 <b>Armolopid Plus</b>  ○ Не відмічено")
        except Exception as _me:
            print(f"meds in day summary error: {_me}")

        # ── ВАГА ──
        try:
            from storage import load_weight as _lw
            weight_db = _lw()
            if weight_db:
                last_w_date = max(weight_db.keys())
                last_w_val  = weight_db[last_w_date]
                days_ago = (now_local.date() - datetime.strptime(last_w_date, "%Y-%m-%d").date()).days
                if days_ago == 0:
                    lines.append(f"\n⚖️ <b>Вага сьогодні</b>  <b>{last_w_val} кг</b> ✅")
                else:
                    lines.append(f"\n⚖️ <b>Вага</b>  {last_w_val} кг  <i>({days_ago} дн. тому)</i>")
        except Exception as _we:
            print(f"weight in day summary error: {_we}")

        # ── HEALTH SCORE ──
        try:
            from storage import load_health as _lhealth
            health_db = _lhealth()
            today_health = health_db.get(today, {})
            if today_health:
                h_lines = []
                if today_health.get("steps"):
                    h_lines.append(f"👟 {today_health['steps']:,} кр.")
                if today_health.get("sleep_hours"):
                    h_lines.append(f"😴 {today_health['sleep_hours']}г")
                if today_health.get("heart_rate"):
                    h_lines.append(f"❤️ {today_health['heart_rate']} bpm")
                if today_health.get("hrv"):
                    h_lines.append(f"💓 HRV {today_health['hrv']}")
                if today_health.get("health_score"):
                    sc = today_health['health_score']
                    sc_emoji = "🟢" if sc >= 75 else ("🟡" if sc >= 55 else "🔴")
                    h_lines.append(f"{sc_emoji} Score {sc}/100")
                if h_lines:
                    lines.append(f"\n\n🍎 <b>Apple Health</b>")
                    lines.append(f"    {' · '.join(h_lines)}")
            else:
                lines.append(f"\n\n🍎 <b>Apple Health</b>  <i>немає даних — надішли /зд</i>")
        except Exception as _he:
            print(f"health in day summary error: {_he}")

        # ── ВОДА ──
        try:
            water_state = load_json_file(WATER_FILE, default={})
            water_count = water_state.get(today, 0)
            water_ml = water_count * 250
            water_bar = "💧" * water_count + "○" * max(0, 8 - water_count)
            lines.append(f"\n\n💧 <b>Вода</b>  {water_ml} мл  {water_bar}")
        except Exception as _wt:
            print(f"water in day summary error: {_wt}")

        lines.append(f"\n\n━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("Гарного вечора! 🌙")

        send_telegram("\n".join(lines))
        print("Day summary sent")

        state["last"] = today
        save_json_file(DAY_SUMMARY_FILE, state)

    except Exception as e:
        print(f"check_day_summary error: {e}")

# ─── ТРАФІК ПЕРЕД ЗМІНОЮ ─────────────────────────────────────────────────────

TRAFFIC_ALERT_FILE = os.path.join(_DATA_DIR, "monitor_traffic_alert.json")

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
