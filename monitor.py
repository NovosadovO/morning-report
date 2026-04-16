#!/usr/bin/env python3
"""
Monitor script — runs every 15 min via GitHub Actions.
Sends Telegram alerts for:
- Crypto price changes 5%+ (BTC/ETH/AVAX/ONDO)
- Important new emails (Gmail IMAP)
- Rain/snow in Košice
- Google Calendar events in next 2 hours
"""

import os
import json
import time
import imaplib
import email
import email.header
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

# ─── CONFIG ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT   = os.environ["TELEGRAM_CHAT_ID"]
GMAIL_USER      = "novosadovoleg@gmail.com"
GMAIL_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")
PRICE_FILE      = "/tmp/monitor_prices.json"
SEEN_EMAIL_FILE = "/tmp/monitor_seen_emails.json"

COINS = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "AVAX": "avalanche-2",
    "ONDO": "ondo-finance",
}

PRICE_THRESHOLD = 0.05  # 5%

# Spam / newsletter senders to ignore (lowercase fragments)
IGNORE_SENDERS = [
    "noreply", "no-reply", "newsletter", "notifications", "mailer",
    "support@", "info@", "marketing", "promo", "unsubscribe",
    "digest", "updates@", "news@", "alert@binance", "alert@coinbase",
    "donotreply", "do-not-reply",
]
IGNORE_SUBJECTS = [
    "newsletter", "digest", "promo", "offer", "sale", "discount",
    "unsubscribe", "your daily", "weekly", "monthly",
]

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def fetch_json(url: str) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "monitor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"fetch_json error {url}: {e}")
        return None


def load_json_file(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json_file(path: str, data):
    with open(path, "w") as f:
        json.dump(data, f)


# ─── 1. CRYPTO PRICE MONITOR ─────────────────────────────────────────────────

def check_prices():
    ids = ",".join(COINS.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    data = fetch_json(url)
    if not data:
        print("CoinGecko unavailable")
        return

    now = datetime.now(timezone.utc)
    lines = []

    for symbol, cg_id in COINS.items():
        price = data.get(cg_id, {}).get("usd")
        change = data.get(cg_id, {}).get("usd_24h_change")
        if price is None:
            continue
        arrow = "🔺" if (change or 0) > 0 else "🔻"
        change_str = f"{'+' if change > 0 else ''}{change:.1f}%" if change is not None else ""
        lines.append(f"{arrow} <b>{symbol}</b>: ${price:,.2f}  <i>{change_str} за 24г</i>")

    if lines:
        time_str = (now + timedelta(hours=2)).strftime("%H:%M")
        msg = f"💰 <b>Ціни активів — {time_str}</b>\n\n" + "\n".join(lines)
        send_telegram(msg)
        print("Price update sent")


# ─── 2. GMAIL IMPORTANT EMAIL MONITOR ────────────────────────────────────────

def decode_header_str(h) -> str:
    parts = email.header.decode_header(h or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def is_spam(sender: str, subject: str) -> bool:
    s = sender.lower()
    sub = subject.lower()
    if any(x in s for x in IGNORE_SENDERS):
        return True
    if any(x in sub for x in IGNORE_SUBJECTS):
        return True
    return False


def check_emails():
    if not GMAIL_PASSWORD:
        print("No Gmail password, skipping email check")
        return

    seen = load_json_file(SEEN_EMAIL_FILE, default=[])
    seen_set = set(seen)

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_USER, GMAIL_PASSWORD.replace(" ", ""))
        mail.select("INBOX")

        # Fetch UNSEEN emails from last 30 min
        since = (datetime.now(timezone.utc) - timedelta(minutes=30)).strftime("%d-%b-%Y")
        _, data = mail.search(None, f'(UNSEEN SINCE "{since}")')
        ids = data[0].split() if data[0] else []

        new_alerts = []
        new_seen = []

        for uid in ids[-20:]:  # cap at last 20
            uid_str = uid.decode()
            if uid_str in seen_set:
                continue
            _, msg_data = mail.fetch(uid, "(RFC822)")
            raw = msg_data[0][1] if msg_data and msg_data[0] else None
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            sender  = decode_header_str(msg.get("From", ""))
            subject = decode_header_str(msg.get("Subject", "(no subject)"))
            date_str = msg.get("Date", "")

            new_seen.append(uid_str)

            if is_spam(sender, subject):
                continue

            def esc(s): return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            new_alerts.append(f"• <b>{esc(subject[:60])}</b>\n  від: {esc(sender[:50])}")

        mail.logout()

        # Update seen
        all_seen = list(seen_set | set(new_seen))
        save_json_file(SEEN_EMAIL_FILE, all_seen[-500:])

        if new_alerts:
            msg = f"📬 <b>Нові важливі листи ({len(new_alerts)})</b>\n\n" + "\n\n".join(new_alerts[:5])
            if len(new_alerts) > 5:
                msg += f"\n\n...і ще {len(new_alerts)-5}"
            send_telegram(msg)
            print(f"Email alert: {len(new_alerts)} new")
        else:
            print("No new important emails.")

    except Exception as e:
        print(f"Email check error: {e}")


# ─── 3. WEATHER KOŠICE ───────────────────────────────────────────────────────

def check_weather():
    # Open-Meteo — no API key needed
    # Košice coords: 48.7163, 21.2611
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=48.7163&longitude=21.2611"
        "&hourly=precipitation,precipitation_probability,weathercode"
        "&forecast_days=1"
        "&timezone=Europe%2FPrague"
    )
    data = fetch_json(url)
    if not data:
        print("Weather API unavailable")
        return

    hourly = data.get("hourly", {})
    times  = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    prob   = hourly.get("precipitation_probability", [])
    codes  = hourly.get("weathercode", [])

    # WMO codes for rain/snow
    RAIN_CODES  = {51,53,55,61,63,65,80,81,82}
    SNOW_CODES  = {71,73,75,77,85,86}
    STORM_CODES = {95,96,99}

    now_utc = datetime.now(timezone.utc)
    # Europe/Prague is UTC+2 in summer, UTC+1 in winter — approximate
    local_hour = (now_utc.hour + 2) % 24

    alerts = []
    for i, t in enumerate(times):
        # t is like "2024-01-15T14:00"
        try:
            h = int(t[11:13])
        except Exception:
            continue
        # next 3 hours
        diff = (h - local_hour) % 24
        if diff > 3:
            continue
        p = precip[i] if i < len(precip) else 0
        pr = prob[i] if i < len(prob) else 0
        code = codes[i] if i < len(codes) else 0
        if p > 0.3 or pr >= 60 or code in RAIN_CODES | SNOW_CODES | STORM_CODES:
            kind = "❄️ Сніг" if code in SNOW_CODES else ("⛈ Гроза" if code in STORM_CODES else "🌧 Дощ")
            alerts.append(f"{kind} о {t[11:16]}: {p}мм, ймовірність {pr}%")

    if alerts:
        msg = "☔ <b>Погода Košice — візьми парасольку!</b>\n\n" + "\n".join(alerts)
        send_telegram(msg)
        print("Weather alert sent")
    else:
        print("Weather OK, no precipitation.")


# ─── 4. GOOGLE CALENDAR ──────────────────────────────────────────────────────

def check_calendar():
    """
    Uses Google Calendar API with service account or OAuth credentials.
    Credentials JSON from env var GOOGLE_CALENDAR_CREDENTIALS.
    """
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        print("No Google Calendar credentials, skipping")
        return

    try:
        import google.oauth2.service_account as sa
        from googleapiclient.discovery import build
    except ImportError:
        print("google-auth not installed, skipping calendar")
        return

    try:
        creds_data = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/calendar.readonly"]

        if creds_data.get("type") == "service_account":
            creds = sa.Credentials.from_service_account_info(creds_data, scopes=scopes)
        else:
            # OAuth2 refresh token flow
            from google.oauth2.credentials import Credentials
            creds = Credentials(
                token=None,
                refresh_token=creds_data.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
            )

        service = build("calendar", "v3", credentials=creds)

        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(hours=2)).isoformat()

        events_result = service.events().list(
            calendarId="novosadovoleg@gmail.com",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        ).execute()

        events = events_result.get("items", [])
        if not events:
            print("No calendar events in next 2 hours.")
            return

        lines = []
        for ev in events:
            start = ev["start"].get("dateTime") or ev["start"].get("date")
            summary = ev.get("summary", "(без назви)")
            loc = ev.get("location", "")
            # Parse time
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                # Use time as-is from calendar (already in user's timezone)
                time_str = dt.strftime("%H:%M")
            except Exception:
                time_str = start
            lines.append(f"• <b>{summary}</b> о {time_str}" + (f"\n  📍 {loc}" if loc else ""))

        msg = f"📅 <b>Найближчі події ({len(lines)})</b>\n\n" + "\n\n".join(lines)
        send_telegram(msg)
        print(f"Calendar alert: {len(lines)} events")

    except Exception as e:
        print(f"Calendar error: {e}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"=== Monitor run at {datetime.now(timezone.utc).isoformat()} ===")

    # Run all checks — catch individual errors so one failure doesn't block others
    for check_fn in [check_prices, check_emails, check_weather, check_calendar]:
        try:
            check_fn()
        except Exception as e:
            print(f"ERROR in {check_fn.__name__}: {e}")

    print("=== Done ===")


if __name__ == "__main__":
    main()
