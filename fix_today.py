#!/usr/bin/env python3
"""
Одноразовий скрипт: запускається на Railway.
- Записує ліки ✅ в Google Calendar за сьогодні
- Видаляє події "сауна" за сьогодні
Запустить себе і вийде.
"""
import os, json, urllib.request, urllib.parse, sys
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")

def tg(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    d = json.dumps({"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"}).encode()
    urllib.request.urlopen(urllib.request.Request(url, data=d, headers={"Content-Type":"application/json"}), timeout=10)

def run():
    from monitor import _get_google_token
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        tg("❌ No GOOGLE_CALENDAR_CREDENTIALS")
        return

    creds_data = json.loads(creds_json)
    token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    now_l = datetime.now(timezone.utc) + timedelta(hours=2)
    today = now_l.strftime("%Y-%m-%d")

    # 1. Шукаємо всі події сьогодні
    params = urllib.parse.urlencode({
        "timeMin": today + "T00:00:00+02:00",
        "timeMax": today + "T23:59:59+02:00",
        "singleEvents": "true",
        "orderBy": "startTime"
    })
    url = f"https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events?{params}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    events = data.get("items", [])
    print(f"Events today ({len(events)}):")
    for e in events:
        print(f"  [{e['id']}] {e.get('summary','?')}")

    # 2. Видаляємо "сауна" (будь-яка подія з "сауна" в назві, case-insensitive)
    deleted = []
    for e in events:
        summary = e.get("summary", "").lower()
        if "сауна" in summary or "sauna" in summary:
            ev_id = e["id"]
            del_url = f"https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events/{ev_id}"
            req_del = urllib.request.Request(del_url, headers=headers, method="DELETE")
            try:
                urllib.request.urlopen(req_del, timeout=10)
                deleted.append(e.get("summary", ev_id))
                print(f"Deleted: {e.get('summary')}")
            except Exception as ex:
                print(f"Delete error: {ex}")

    # 3. Записуємо ліки в Calendar
    start_str = f"{today}T{now_l.hour:02d}:{now_l.minute:02d}:00+02:00"
    end_l = now_l + timedelta(minutes=15)
    end_str = f"{today}T{end_l.hour:02d}:{end_l.minute:02d}:00+02:00"
    event = {
        "summary": "💊 Armolopid Plus ✅",
        "start": {"dateTime": start_str, "timeZone": "Europe/Bratislava"},
        "end":   {"dateTime": end_str,   "timeZone": "Europe/Bratislava"},
        "colorId": "10"  # зелений
    }
    body = json.dumps(event).encode()
    req_cal = urllib.request.Request(
        "https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events",
        data=body, headers=headers, method="POST"
    )
    try:
        urllib.request.urlopen(req_cal, timeout=10)
        print("Meds event created in Calendar")
        meds_ok = True
    except Exception as ex:
        print(f"Meds calendar error: {ex}")
        meds_ok = False

    # 4. Звіт в Telegram
    msg = f"✅ <b>Виконано:</b>\n"
    if meds_ok:
        msg += f"💊 Armolopid Plus — записано в Calendar\n"
    if deleted:
        msg += f"🗑 Видалено: {', '.join(deleted)}\n"
    else:
        msg += "⚠️ Сауну не знайдено в Calendar на сьогодні\n"
    tg(msg)

if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"fix_today error: {e}")
        import urllib.request as _ur, json as _j, os as _o
        TOKEN = _o.environ.get("TELEGRAM_TOKEN","")
        CHAT  = _o.environ.get("TELEGRAM_CHAT_ID","")
        if TOKEN:
            _ur.urlopen(_ur.Request(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data=_j.dumps({"chat_id":CHAT,"text":f"❌ fix_today error: {e}"}).encode(),
                headers={"Content-Type":"application/json"}
            ), timeout=10)
