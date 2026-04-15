#!/usr/bin/env python3
"""
monitor.py — runs every 15 min via GitHub Actions
Sends Telegram alerts for:
  - Crypto price changes 5%+ (BTC, ETH, AVAX, ONDO)
  - Important new emails (Gmail IMAP)
  - Košice weather (rain/snow)
  - Google Calendar events in next 2 hours
"""

import os
import json
import time
import imaplib
import email
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

# ─── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT   = os.environ["TELEGRAM_CHAT_ID"]
GMAIL_USER      = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS  = os.environ.get("GMAIL_APP_PASSWORD", "")

# Price state file (persisted via Actions cache between runs)
PRICE_STATE_FILE = "/tmp/crypto_prices.json"

COINS = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "AVAX": "avalanche-2",
    "ONDO": "ondo-finance",
}

THRESHOLD = 0.05  # 5%

# ─── Telegram ────────────────────────────────────────────────────────────────
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[Telegram error] {e}")

# ─── Crypto prices ───────────────────────────────────────────────────────────
def fetch_prices() -> dict:
    ids = ",".join(COINS.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
    req = urllib.request.Request(url, headers={"User-Agent": "monitor/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    result = {}
    for sym, cg_id in COINS.items():
        result[sym] = data.get(cg_id, {}).get("usd", 0)
    return result

def load_previous_prices() -> dict:
    if os.path.exists(PRICE_STATE_FILE):
        with open(PRICE_STATE_FILE) as f:
            return json.load(f)
    return {}

def save_prices(prices: dict):
    with open(PRICE_STATE_FILE, "w") as f:
        json.dump(prices, f)

def check_prices():
    try:
        current = fetch_prices()
        previous = load_previous_prices()
        alerts = []

        for sym, price in current.items():
            prev = previous.get(sym)
            if prev and prev > 0:
                change = (price - prev) / prev
                if abs(change) >= THRESHOLD:
                    arrow = "🚀" if change > 0 else "🔴"
                    alerts.append(
                        f"{arrow} <b>{sym}</b>: ${price:,.2f} "
                        f"({'+' if change > 0 else ''}{change*100:.1f}% за 15хв)"
                    )

        save_prices(current)

        if alerts:
            msg = "⚡ <b>Ціновий алерт</b>\n\n" + "\n".join(alerts)
            send_telegram(msg)
            print(f"[Prices] Sent {len(alerts)} alerts")
        else:
            print(f"[Prices] No significant changes. {json.dumps(current)}")
    except Exception as e:
        print(f"[Prices error] {e}")

# ─── Gmail IMAP ──────────────────────────────────────────────────────────────
SPAM_KEYWORDS = [
    "unsubscribe", "newsletter", "promotion", "offer", "sale",
    "discount", "no-reply", "noreply", "marketing", "digest",
    "weekly", "monthly", "notifications@", "updates@", "news@",
    "info@", "do-not-reply", "donotreply",
]

def is_important(msg_from: str, subject: str) -> bool:
    combined = (msg_from + " " + subject).lower()
    for kw in SPAM_KEYWORDS:
        if kw in combined:
            return False
    return True

def check_email():
    if not GMAIL_USER or not GMAIL_APP_PASS:
        print("[Email] Credentials not set, skipping")
        return
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASS)
        mail.select("inbox")

        # Unseen emails in last 20 minutes
        since_dt = (datetime.now(timezone.utc) - timedelta(minutes=20))
        since_str = since_dt.strftime("%d-%b-%Y")
        _, data = mail.search(None, f'(UNSEEN SINCE "{since_str}")')
        ids = data[0].split()

        alerts = []
        for eid in ids[-10:]:  # max 10
            _, msg_data = mail.fetch(eid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            from_addr = msg.get("From", "")
            subject = msg.get("Subject", "(без теми)")
            date = msg.get("Date", "")

            if is_important(from_addr, subject):
                alerts.append(f"• <b>{subject[:60]}</b>\n  від: {from_addr[:50]}")

        mail.logout()

        if alerts:
            msg = f"📧 <b>Нові важливі листи ({len(alerts)})</b>\n\n" + "\n\n".join(alerts)
            send_telegram(msg)
            print(f"[Email] Sent {len(alerts)} alerts")
        else:
            print("[Email] No important new emails")
    except Exception as e:
        print(f"[Email error] {e}")

# ─── Weather Košice ──────────────────────────────────────────────────────────
PRECIPITATION_CODES = {
    51, 53, 55,          # Drizzle
    61, 63, 65,          # Rain
    71, 73, 75, 77,      # Snow
    80, 81, 82,          # Rain showers
    85, 86,              # Snow showers
    95, 96, 99,          # Thunderstorm
}

WMO_LABELS = {
    51: "мряка", 53: "мряка", 55: "сильна мряка",
    61: "дощ", 63: "помірний дощ", 65: "сильний дощ",
    71: "сніг", 73: "помірний сніг", 75: "сильний сніг", 77: "крупа",
    80: "зливи", 81: "сильні зливи", 82: "дуже сильні зливи",
    85: "снігові зливи", 86: "сильні снігові зливи",
    95: "гроза", 96: "гроза з градом", 99: "гроза з сильним градом",
}

def check_weather():
    try:
        # Košice coords: 48.7163, 21.2611
        url = (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=48.7163&longitude=21.2611"
            "&hourly=weathercode,precipitation"
            "&forecast_days=1&timezone=Europe/Bratislava"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "monitor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())

        times = data["hourly"]["time"]
        codes = data["hourly"]["weathercode"]
        precip = data["hourly"]["precipitation"]

        # Check next 3 hours
        now = datetime.now(timezone(timedelta(hours=2)))  # CEST
        alerts = []
        for i, t_str in enumerate(times):
            t = datetime.fromisoformat(t_str)
            # Make aware
            t = t.replace(tzinfo=timezone(timedelta(hours=2)))
            if now <= t <= now + timedelta(hours=3):
                code = codes[i]
                rain = precip[i]
                if code in PRECIPITATION_CODES:
                    label = WMO_LABELS.get(code, f"код {code}")
                    alerts.append(f"• {t.strftime('%H:%M')} — {label} ({rain}мм)")

        if alerts:
            msg = "🌧 <b>Погода Košice — опади!</b>\n\n" + "\n".join(alerts)
            send_telegram(msg)
            print(f"[Weather] Precipitation alert sent")
        else:
            print("[Weather] No precipitation in next 3h")
    except Exception as e:
        print(f"[Weather error] {e}")

# ─── Google Calendar ─────────────────────────────────────────────────────────
def check_calendar():
    """
    Reads GOOGLE_CALENDAR_CREDENTIALS env var (service account JSON or OAuth token).
    Falls back gracefully if not configured.
    """
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    if not creds_json:
        print("[Calendar] No credentials, skipping")
        return

    try:
        import sys
        # Install google-auth if needed (done in workflow)
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_data = json.loads(creds_json)

        # Support both service account and OAuth token JSON
        if creds_data.get("type") == "service_account":
            creds = service_account.Credentials.from_service_account_info(
                creds_data,
                scopes=["https://www.googleapis.com/auth/calendar.readonly"]
            )
        else:
            from google.oauth2.credentials import Credentials
            creds = Credentials.from_authorized_user_info(creds_data)

        service = build("googleapiclient", "v3", credentials=creds)

        now_utc = datetime.now(timezone.utc)
        window_end = now_utc + timedelta(hours=2, minutes=15)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now_utc.isoformat(),
            timeMax=window_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=5,
        ).execute()

        events = events_result.get("items", [])
        if not events:
            print("[Calendar] No upcoming events")
            return

        alerts = []
        for ev in events:
            start = ev.get("start", {})
            dt_str = start.get("dateTime") or start.get("date")
            summary = ev.get("summary", "(без назви)")
            location = ev.get("location", "")

            if dt_str:
                try:
                    dt = datetime.fromisoformat(dt_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    mins_left = int((dt - now_utc).total_seconds() / 60)
                    time_str = dt.astimezone(timezone(timedelta(hours=2))).strftime("%H:%M")
                    loc_str = f"\n  📍 {location}" if location else ""
                    alerts.append(f"• <b>{summary}</b> о {time_str} (за {mins_left} хв){loc_str}")
                except Exception:
                    alerts.append(f"• <b>{summary}</b>")

        if alerts:
            msg = "📅 <b>Найближчі події (2 год)</b>\n\n" + "\n".join(alerts)
            send_telegram(msg)
            print(f"[Calendar] Sent {len(alerts)} event reminders")
    except ImportError:
        print("[Calendar] google-api-python-client not installed")
    except Exception as e:
        print(f"[Calendar error] {e}")

# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[Monitor] Starting at {datetime.now(timezone.utc).isoformat()}")
    check_prices()
    check_weather()
    check_email()
    check_calendar()
    print("[Monitor] Done")
