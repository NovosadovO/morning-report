#!/usr/bin/env python3
"""
Monitor — надсилає один зведений звіт кожні 3 години.
"""

import os
import json
import imaplib
import email
import email.header
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT   = os.environ["TELEGRAM_CHAT_ID"]
GMAIL_USER      = "novosadovoleg@gmail.com"
GMAIL_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")
SEEN_EMAIL_FILE = "/tmp/monitor_seen_emails.json"

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
    "donotreply", "do-not-reply",
]
IGNORE_SUBJECTS = [
    "newsletter", "digest", "promo", "offer", "sale", "discount",
    "unsubscribe", "your daily", "weekly", "monthly",
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


def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "monitor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"fetch_json error: {e}")
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
    if not data:
        return "💰 <b>Ціни</b>\n⚠️ Недоступно"

    lines = []
    for symbol, cg_id in COINS.items():
        price  = data.get(cg_id, {}).get("usd")
        change = data.get(cg_id, {}).get("usd_24h_change")
        if price is None:
            continue
        arrow = "🔺" if (change or 0) > 0 else "🔻"
        ch = f"{'+' if change > 0 else ''}{change:.1f}%" if change is not None else ""
        lines.append(f"{arrow} <b>{symbol}</b>: ${price:,.2f} <i>({ch} 24г)</i>")

    return "💰 <b>Ціни активів</b>\n" + "\n".join(lines)


# ─── 2. ПОГОДА ────────────────────────────────────────────────────────────────

def get_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=48.7163&longitude=21.2611"
        "&hourly=precipitation,precipitation_probability,weathercode,"
        "temperature_2m,apparent_temperature,windspeed_10m"
        "&forecast_days=2&timezone=Europe%2FPrague"
    )
    data = fetch_json(url)
    if not data:
        return "🌡 <b>Погода Košice</b>\n⚠️ Недоступно"

    hourly  = data.get("hourly", {})
    times   = hourly.get("time", [])
    precip  = hourly.get("precipitation", [])
    prob    = hourly.get("precipitation_probability", [])
    codes   = hourly.get("weathercode", [])
    temps   = hourly.get("temperature_2m", [])
    feels   = hourly.get("apparent_temperature", [])
    winds   = hourly.get("windspeed_10m", [])

    WMO = {
        0: "☀️ Ясно", 1: "🌤 Переважно ясно", 2: "⛅️ Мінлива хмарність", 3: "☁️ Хмарно",
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

    local_hour = (datetime.now(timezone.utc).hour + 2) % 24
    current = ""
    warnings = []

    for i, t in enumerate(times):
        try:
            h = int(t[11:13])
        except Exception:
            continue
        diff = (h - local_hour) % 24
        code  = codes[i] if i < len(codes) else 0
        temp  = temps[i] if i < len(temps) else None
        feel  = feels[i] if i < len(feels) else None
        wind  = winds[i] if i < len(winds) else None
        p     = precip[i] if i < len(precip) else 0
        pr    = prob[i] if i < len(prob) else 0

        if diff == 0:
            desc   = WMO.get(code, "—")
            t_str  = f"{temp:.0f}°C" if temp is not None else "—"
            f_str  = f"відчувається {feel:.0f}°C" if feel is not None else ""
            w_str  = f"вітер {wind:.0f} км/г" if wind is not None else ""
            extras = ", ".join(filter(None, [f_str, w_str]))
            current = f"{desc}, {t_str}" + (f"\n  {extras}" if extras else "")

        if 0 < diff <= 3 and (p > 0.3 or pr >= 60 or code in RAIN | SNOW | STORM):
            kind = "❄️ Сніг" if code in SNOW else ("⛈ Гроза" if code in STORM else "🌧 Дощ")
            warnings.append(f"  {kind} о {t[11:16]} ({pr}%)")

    result = f"🌡 <b>Погода Košice</b>\n{current}"
    if warnings:
        result += "\n⚠️ Найближчі 3г:\n" + "\n".join(warnings)
    return result


# ─── 3. КАЛЕНДАР ──────────────────────────────────────────────────────────────

def get_calendar():
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    now = datetime.now(timezone.utc)
    date_today = (now + timedelta(hours=2)).strftime("%d.%m.%Y")
    date_tomorrow = (now + timedelta(hours=26)).strftime("%d.%m.%Y")

    if not creds_json:
        return f"📅 <b>Календар</b>\n⚠️ Не налаштовано"

    try:
        import google.oauth2.service_account as sa
        from googleapiclient.discovery import build

        creds_data = json.loads(creds_json)
        creds = sa.Credentials.from_service_account_info(
            creds_data, scopes=["https://www.googleapis.com/auth/calendar.readonly"])
        service = build("calendar", "v3", credentials=creds)

        # Сьогодні
        today_start = (now + timedelta(hours=2)).replace(
            hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        today_end = today_start + timedelta(hours=24)

        # Завтра
        tomorrow_start = today_start + timedelta(hours=24)
        tomorrow_end   = tomorrow_start + timedelta(hours=24)

        def fetch_events(t_min, t_max):
            r = service.events().list(
                calendarId="novosadovoleg@gmail.com",
                timeMin=t_min.isoformat(),
                timeMax=t_max.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=20
            ).execute()
            return r.get("items", [])

        today_events    = fetch_events(today_start, today_end)
        tomorrow_events = fetch_events(tomorrow_start, tomorrow_end)

        def format_events(events):
            lines = []
            for ev in events:
                start = ev["start"].get("dateTime") or ev["start"].get("date")
                summary = ev.get("summary", "(без назви)")
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    t = dt.strftime("%H:%M")
                except Exception:
                    t = start
                lines.append(f"• {t} — <b>{esc(summary)}</b>")
            return lines

        result = f"📅 <b>Календар</b>\n"
        result += f"<b>Сьогодні {date_today}:</b>\n"
        today_lines = format_events(today_events)
        result += ("\n".join(today_lines) if today_lines else "Нічого не заплановано")

        result += f"\n\n<b>Завтра {date_tomorrow}:</b>\n"
        tomorrow_lines = format_events(tomorrow_events)
        result += ("\n".join(tomorrow_lines) if tomorrow_lines else "Нічого не заплановано")

        return result

    except Exception as e:
        return f"📅 <b>Календар</b>\n⚠️ Помилка: {esc(str(e)[:80])}"


# ─── 4. EMAIL ─────────────────────────────────────────────────────────────────

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


def get_emails():
    if not GMAIL_PASSWORD:
        return "📬 <b>Email</b>\n⚠️ Не налаштовано"

    seen = set(load_json_file(SEEN_EMAIL_FILE, default=[]))

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_USER, GMAIL_PASSWORD.replace(" ", ""))
        mail.select("INBOX")

        since = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%d-%b-%Y")
        _, data = mail.search(None, f'(UNSEEN SINCE "{since}")')
        ids = data[0].split() if data[0] else []

        new_items = []
        new_seen = []

        for uid in ids[-20:]:
            uid_str = uid.decode()
            if uid_str in seen:
                continue
            _, msg_data = mail.fetch(uid, "(RFC822)")
            raw = msg_data[0][1] if msg_data and msg_data[0] else None
            if not raw:
                continue
            msg = email.message_from_bytes(raw)
            sender  = decode_header_str(msg.get("From", ""))
            subject = decode_header_str(msg.get("Subject", "(no subject)"))
            new_seen.append(uid_str)
            if not is_spam(sender, subject):
                new_items.append(f"• <b>{esc(subject[:55])}</b>\n  {esc(sender[:45])}")

        if not new_items:
            _, data2 = mail.search(None, "ALL")
            all_ids = data2[0].split() if data2[0] else []
            recent = []
            for uid in reversed(all_ids[-15:]):
                if len(recent) >= 5:
                    break
                _, msg_data = mail.fetch(uid, "(RFC822)")
                raw = msg_data[0][1] if msg_data and msg_data[0] else None
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                sender  = decode_header_str(msg.get("From", ""))
                subject = decode_header_str(msg.get("Subject", "(no subject)"))
                if not is_spam(sender, subject):
                    recent.append(f"• <b>{esc(subject[:55])}</b>\n  {esc(sender[:45])}")
            mail.logout()
            save_json_file(SEEN_EMAIL_FILE, list(seen | set(new_seen))[-500:])
            if recent:
                return "📬 <b>Останні листи</b>\n" + "\n".join(recent)
            return "📬 <b>Email</b>\nНових листів немає"

        mail.logout()
        save_json_file(SEEN_EMAIL_FILE, list(seen | set(new_seen))[-500:])
        return f"📬 <b>Нових листів: {len(new_items)}</b>\n" + "\n".join(new_items[:5])

    except Exception as e:
        return f"📬 <b>Email</b>\n⚠️ Помилка: {esc(str(e)[:80])}"


# ─── 5. ПІДСУМОК ТА РЕКОМЕНДАЦІЇ ─────────────────────────────────────────────

def get_summary(prices_text, weather_text, calendar_text):
    tips = []
    now_local = datetime.now(timezone.utc) + timedelta(hours=2)

    # Погода — рекомендації
    if "дощ" in weather_text.lower() or "злива" in weather_text.lower():
        tips.append("☔ Візьми парасольку")
    if "гроза" in weather_text.lower():
        tips.append("⛈ Уникай відкритих місць — гроза")
    if "сніг" in weather_text.lower():
        tips.append("🧥 Одягнись тепліше — очікується сніг")
    if "туман" in weather_text.lower():
        tips.append("🚗 Обережно на дорозі — туман")

    # Ціни — рекомендації
    if "🔻" in prices_text:
        tips.append("📉 Крипторинок падає — слідкуй за портфелем")
    if "🔺" in prices_text:
        tips.append("📈 Крипторинок росте")

    # Час доби
    h = now_local.hour
    if 6 <= h < 10:
        tips.append("☕ Доброго ранку! Гарного дня")
    elif 12 <= h < 14:
        tips.append("🍽 Час обіду")
    elif 18 <= h < 21:
        tips.append("🌆 Гарного вечора")
    elif h >= 22 or h < 6:
        tips.append("😴 Пізно — час відпочивати")

    # Календар
    if "нічого не заплановано" not in calendar_text.lower():
        tips.append("📌 Перевір заплановані події на сьогодні")

    if not tips:
        tips.append("✅ Все спокійно")

    return "💡 <b>Підсумок та рекомендації</b>\n" + "\n".join(tips)


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
    summary_text = get_summary(prices_text, weather_text, cal_text)

    report = (
        f"🕐 <b>Звіт {local_time} · {local_date}</b>\n\n"
        f"{prices_text}\n\n"
        f"{weather_text}\n\n"
        f"{cal_text}\n\n"
        f"{email_text}\n\n"
        f"{summary_text}"
    )

    send_telegram(report)
    print("=== Report sent ===")


if __name__ == "__main__":
    main()
