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

IGNORE_SENDERS = [
    "noreply", "no-reply", "newsletter", "notifications", "mailer",
    "support@", "info@", "marketing", "promo", "unsubscribe",
    "digest", "updates@", "news@", "alert@binance", "alert@coinbase",
    "donotreply", "do-not-reply", "notify.railway", "temu", "footshop",
    "unstoppabledomains", "startengine", "temuemail",
]
IGNORE_SUBJECTS = [
    "newsletter", "digest", "promo", "offer", "sale", "discount",
    "unsubscribe", "your daily", "weekly", "monthly", "referral",
    "new launch", "collecting", "portfolio", "managed by ai",
    "predtým", "teraz", "máš ich",
]

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

        # Якщо немає — беремо text/html
        if not body:
            for p in all_parts:
                if p.get("mimeType") == "text/html":
                    data = p.get("body", {}).get("data", "")
                    if data:
                        html_body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                        body = re.sub(r'<[^>]+>', ' ', html_body)
                        break

        body = _html.unescape(body)
        body = re.sub(r'https?://\S+', '', body)
        body = re.sub(r'\[.*?\]', '', body)
        body = re.sub(r'<.*?>', '', body)
        body = re.sub(r'(unsubscribe|відписатись|view in browser|view this post).{0,60}', '', body, flags=re.IGNORECASE)
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


def format_email_item(subject, sender, preview, is_unread=False):
    mark = "🔴 " if is_unread else "   "
    return (
        f"┌─────────────────────\n"
        f"{mark}📨 <b>{esc(subject[:55])}</b>\n"
        f"    👤 <code>{esc(sender[:40])}</code>\n"
        f"    💬 {esc(preview[:110])}\n"
        f"└─────────────────────"
    )


def get_emails():
    token = _gmail_access_token()
    if not token:
        return "📬 <b>Email</b>\n⚠️ Gmail API не налаштовано"

    seen = set(load_json_file(SEEN_EMAIL_FILE, default=[]))

    # Беремо останні 25 листів з INBOX
    all_msgs = _gmail_list(token, ["INBOX"], max_results=25)

    primary = []  # CATEGORY_PERSONAL або без категорії
    other   = []  # CATEGORY_PROMOTIONS, SOCIAL, UPDATES
    all_ids = []

    for m in all_msgs:
        msg_data = _gmail_get(token, m["id"], fmt="full")
        if not msg_data:
            continue
        all_ids.append(m["id"])
        labels  = msg_data.get("labelIds", [])
        subject, sender, preview, is_unread = _parse_gmail_msg(msg_data, full=True)
        if is_spam(sender, subject):
            continue

        is_promo  = "CATEGORY_PROMOTIONS" in labels
        is_social = "CATEGORY_SOCIAL"     in labels
        is_forum  = "CATEGORY_FORUMS"     in labels

        if is_promo or is_social or is_forum:
            if len(other) < 3:
                other.append((subject, sender, preview, is_unread))
        else:
            # CATEGORY_PERSONAL + CATEGORY_UPDATES = ОСНОВНІ
            if len(primary) < 5:
                primary.append((subject, sender, preview, is_unread))

        if len(primary) >= 5 and len(other) >= 3:
            break

    # Зберігаємо seen IDs
    new_seen = [mid for mid in all_ids if mid not in seen]
    save_json_file(SEEN_EMAIL_FILE, list(seen | set(new_seen))[-500:])

    lines = ["📩 <b>━━━ ЛИСТИ ━━━</b>\n"]

    if primary:
        unread_count = sum(1 for _, _, _, u in primary if u)
        header = "📥 <b>ОСНОВНІ</b>" + (f"  🔴 {unread_count} нових" if unread_count else "")
        lines.append(header)
        for s, snd, p, u in primary:
            lines.append(format_email_item(s, snd, p, u))
        lines.append("")

    if other:
        lines.append("📂 <b>ІНШІ</b>")
        for s, snd, p, u in other:
            lines.append(format_email_item(s, snd, p, u))

    if not primary and not other:
        lines.append("✅ Немає листів")

    return "\n".join(lines)


# ─── 4b. МИТТЄВІ СПОВІЩЕННЯ ПРО НОВІ ЛИСТИ ───────────────────────────────────

ALERT_EMAIL_FILE = os.path.join(_DATA_DIR, "monitor_alert_emails.json")

def check_new_emails():
    """Перевіряє нові листи в INBOX за останні 10 хвилин — шле сповіщення для Primary."""
    token = _gmail_access_token()
    if not token:
        return

    alerted = set(load_json_file(ALERT_EMAIL_FILE, default=[]))

    try:
        # Шукаємо листи що прийшли за останні 10 хвилин (незалежно від прочитано чи ні)
        import time as _time
        after_ts = int(_time.time()) - 600  # 10 хвилин тому
        msgs = _gmail_list(token, ["INBOX"], max_results=20, q=f"after:{after_ts}")

        new_alerts = []
        new_alerted = list(alerted)

        for m in msgs:
            mid = m["id"]
            if mid in alerted:
                continue
            msg_data = _gmail_get(token, mid, fmt="metadata")
            if not msg_data:
                continue

            labels = msg_data.get("labelIds", [])
            # Сповіщення тільки для Primary + Updates (не промо, не соцмережі)
            skip_labels = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
            if any(l in labels for l in skip_labels):
                new_alerted.append(mid)
                continue

            subject, sender, _, _ = _parse_gmail_msg(msg_data)
            new_alerted.append(mid)
            if not is_spam(sender, subject):
                new_alerts.append((subject, sender))

        save_json_file(ALERT_EMAIL_FILE, new_alerted[-1000:])

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
    local_time = (now + timedelta(hours=2)).strftime("%H:%M")
    local_date = (now + timedelta(hours=2)).strftime("%d.%m.%Y")

    print(f"=== Monitor run at {now.isoformat()} ===")

    prices_text  = get_prices()
    weather_text = get_weather()
    cal_text     = get_calendar()
    email_text   = get_emails()
    traffic_text = get_city_traffic()
    summary_text = get_summary(prices_text, weather_text, cal_text)

    SEP = "\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    report = (
        f"🕐 <b>Звіт {local_time}  ·  {local_date}</b>\n"
        f"<i>3х годинний репорт</i>"
        f"{SEP}"
        f"{prices_text}"
        f"{SEP}"
        f"{weather_text}"
        f"{SEP}"
        + (f"{traffic_text}{SEP}" if traffic_text else "")
        + f"{cal_text}"
        f"{SEP}"
        f"{email_text}"
        f"{SEP}"
        f"{summary_text}"
    )

    send_telegram(report)
    print("=== Report sent ===")


# ─── 4c. НАГАДУВАННЯ ПРО ПОДІЇ КАЛЕНДАРЯ (за 30 хв) ──────────────────────────

CALENDAR_REMINDED_FILE = os.path.join(_DATA_DIR, "monitor_calendar_reminded.json")

def check_calendar_reminders():
    """Шле нагадування за 30 хвилин до старту кожної події в Google Calendar."""
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
        window_start = now + timedelta(minutes=28)
        window_end   = now + timedelta(minutes=32)

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
            reminder_key = f"{ev_id}_{start}"

            if reminder_key in reminded:
                continue

            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                local_dt = dt + timedelta(hours=2)
                t = local_dt.strftime("%H:%M")
            except Exception:
                t = start

            msg = f"⏰ <b>Нагадування</b>\nЧерез 30 хв: <b>{esc(summary)}</b>\n🕐 Початок о {t}"
            send_telegram(msg)
            print(f"Calendar reminder sent: {summary} at {t}")
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
                local_dt = dt + timedelta(hours=2)
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
    """О 21:00 надсилає підсумок дня — події з календаря + звички."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now_local.hour, now_local.minute
    if not (h == 21 and m < 5):
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

        lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━")
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
