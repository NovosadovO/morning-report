#!/usr/bin/env python3
"""
assistant.py — серце персонального асистента Олега.

Функції:
  check_calendar_day_ahead()   — вечірнє нагадування про завтра (20:00)
  check_calendar_1h()          — нагадування за 1 годину (вже є в monitor.py, тут дублюємо з розширенням)
  check_calendar_10min()       — нагадування за 10 хвилин
  check_email_proactive()      — нові важливі листи → стислий опис + кнопки
  propose_calendar_events()    — бот сам пропонує корисні події
  delete_calendar_event()      — видалення події з Calendar
  send_email_reply()           — надіслати відповідь на лист
"""

import os, json, re, urllib.request, urllib.parse, base64
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
_CAL_ID        = "novosadovoleg%40gmail.com"
_GMAIL_USER    = "novosadovoleg@gmail.com"

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc) + timedelta(hours=2)

def _get_google_token(creds_data, scope):
    """JWT service account token — inline copy, no monitor import needed."""
    import time as _time

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
        os.unlink(pem_path)

    jwt_token = f"{header}.{payload}.{_b64url(signature)}"
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt_token,
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token",
        data=body, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def _token():
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return None
    try:
        return _get_google_token(json.loads(creds_json),
                                 "https://www.googleapis.com/auth/calendar")
    except Exception as e:
        print(f"[assistant] token error: {e}")
        return None

def _gmail_token():
    cid  = os.environ.get("GMAIL_CLIENT_ID", "878341164164-4qki4apv3mmo2s8006v9ks10q61sf5uk.apps.googleusercontent.com")
    csec = os.environ.get("GMAIL_CLIENT_SECRET", "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")
    rtok = os.environ.get("GMAIL_REFRESH_TOKEN", "1//06Fo6TgMdtzM6CgYIARAAGAYSNwF-L9IrUgnpTv2b_BQ8dszP9vpdAU5ejStbBW6CQ39FIvKOd-SIpOL_JPMC7cgxWV8dHJwJ8x8")
    try:
        body = urllib.parse.urlencode({
            "client_id": cid, "client_secret": csec,
            "refresh_token": rtok, "grant_type": "refresh_token"
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token",
                                     data=body, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("access_token")
    except Exception as e:
        print(f"[assistant] gmail token error: {e}")
        return None

def _gh(url, headers=None, method="GET", data=None, timeout=15):
    req = urllib.request.Request(url, data=data,
                                  headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def _tg(method, data):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    req = urllib.request.Request(url,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[assistant] tg error {method}: {e}")

def _send(text, keyboard=None):
    payload = {
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    _tg("sendMessage", payload)

def _esc(t):
    return (t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))

def _gemini(prompt, max_tokens=400):
    key = os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.8}
    }).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
        data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            resp = json.loads(r.read())
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        print(f"[assistant] gemini error: {e}")
        return None

# ─── STORAGE (GitHub data branch) ─────────────────────────────────────────────

def _load_state(key, default=None):
    try:
        import sys; sys.path.insert(0, os.path.dirname(__file__))
        from storage import _load_github
        return _load_github(key) or default
    except Exception:
        return default

def _save_state(key, data):
    try:
        import sys; sys.path.insert(0, os.path.dirname(__file__))
        from storage import _save_github
        _save_github(key, data)
    except Exception as e:
        print(f"[assistant] save_state error {key}: {e}")

# ─── CALENDAR: ЧИТАННЯ ПОДІЙ ──────────────────────────────────────────────────

def _fetch_events(token, t_min, t_max, max_results=20):
    url = (
        f"https://www.googleapis.com/calendar/v3/calendars/{_CAL_ID}/events"
        f"?timeMin={urllib.parse.quote(t_min.isoformat())}"
        f"&timeMax={urllib.parse.quote(t_max.isoformat())}"
        f"&singleEvents=true&orderBy=startTime&maxResults={max_results}"
    )
    try:
        return _gh(url, {"Authorization": f"Bearer {token}"}).get("items", [])
    except Exception as e:
        print(f"[assistant] fetch_events error: {e}")
        return []

def _is_shift(summary):
    s = summary.lower()
    return any(x in s for x in ["рання зміна", "нічна зміна", "early shift", "night shift"])

def _event_emoji(summary):
    s = summary.lower()
    if "рання" in s or "early" in s: return "☀️"
    if "нічна" in s or "night" in s: return "🌙"
    if "лікар" in s or "doctor" in s or "hospital" in s: return "🏥"
    if "зустріч" in s or "meet" in s: return "🤝"
    if "трен" in s or "gym" in s or "sport" in s or "біг" in s: return "🏃"
    if "народж" in s or "birthday" in s: return "🎂"
    if "їжа" in s or "ресторан" in s or "обід" in s or "вечер" in s: return "🍽"
    return "📅"

# ─── 1. ВЕЧІРНЄ НАГАДУВАННЯ ПРО ЗАВТРА ───────────────────────────────────────

def check_calendar_day_ahead():
    """
    О 20:00 — надсилає огляд завтрашнього дня:
    всі події + AI порада як підготуватись.
    """
    now = _now()
    if not (now.hour == 20 and now.minute == 0):
        return

    state = _load_state("assistant_day_ahead.json", {})
    today_key = now.strftime("%Y-%m-%d")
    if state.get(today_key):
        return
    state[today_key] = True
    _save_state("assistant_day_ahead.json", state)

    token = _token()
    if not token:
        return

    now_utc = datetime.now(timezone.utc)
    tomorrow_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) \
                     + timedelta(days=1) - timedelta(hours=2)
    tomorrow_end = tomorrow_start + timedelta(hours=24)
    events = _fetch_events(token, tomorrow_start, tomorrow_end)

    tomorrow_local = now + timedelta(days=1)
    weekdays = ["понеділок","вівторок","середа","четвер","п'ятниця","субота","неділя"]
    day_name = weekdays[tomorrow_local.weekday()]
    date_fmt = tomorrow_local.strftime("%d.%m.%Y")

    if not events:
        _send(
            f"🌙 <b>Завтра — {day_name}, {date_fmt}</b>\n\n"
            f"📅 Нічого не заплановано.\n\n"
            f"<i>Вільний день — може додати пробіжку або якусь справу?</i>"
        )
        return

    lines = []
    event_summaries = []
    for ev in events:
        summary = ev.get("summary", "(без назви)")
        start_str = ev["start"].get("dateTime") or ev["start"].get("date")
        try:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            t = (dt + timedelta(hours=2)).strftime("%H:%M")
        except Exception:
            t = "—"
        emoji = _event_emoji(summary)
        lines.append(f"{emoji} <b>{t}</b> — {_esc(summary)}")
        event_summaries.append(f"{t} {summary}")

    events_text = "\n".join(lines)

    # AI порада про підготовку
    ai_prompt = (
        f"Завтра у Олега: {', '.join(event_summaries)}.\n"
        f"Напиши 1-2 речення — коротку пораду як краще підготуватись до завтра. "
        f"Без зайвого, українською, дружньо."
    )
    ai_tip = _gemini(ai_prompt, max_tokens=150) or ""

    msg = (
        f"🌙 <b>Завтра — {day_name}, {date_fmt}</b>\n"
        f"{'─'*24}\n"
        f"{events_text}"
    )
    if ai_tip:
        msg += f"\n\n💡 <i>{_esc(ai_tip)}</i>"

    _send(msg)
    print(f"[assistant] day_ahead sent for {date_fmt}")

# ─── 2. НАГАДУВАННЯ ЗА 10 ХВИЛИН ─────────────────────────────────────────────

def check_calendar_10min():
    """Нагадування за 10 хвилин до будь-якої події."""
    now = _now()
    if not (7 <= now.hour <= 23):
        return

    token = _token()
    if not token:
        return

    state = _load_state("assistant_10min.json", {})

    now_utc = datetime.now(timezone.utc)
    win_start = now_utc + timedelta(minutes=8)
    win_end   = now_utc + timedelta(minutes=12)
    events = _fetch_events(token, win_start, win_end)

    changed = False
    for ev in events:
        ev_id   = ev.get("id", "")
        summary = ev.get("summary", "(без назви)")
        start_str = ev["start"].get("dateTime") or ev["start"].get("date")
        key = f"10min_{ev_id}_{start_str}"
        if state.get(key):
            continue

        try:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            t  = (dt + timedelta(hours=2)).strftime("%H:%M")
        except Exception:
            t = "—"

        emoji = _event_emoji(summary)
        tip = ""
        s = summary.lower()
        if "лікар" in s or "doctor" in s:
            tip = "\n📋 Візьми документи / страховку"
        elif "зустріч" in s or "meet" in s:
            tip = "\n📝 Підготуй питання для зустрічі"
        elif "рання" in s:
            tip = "\n🎒 Одяг, їжа, Armolopid готові?"
        elif "нічна" in s:
            tip = "\n🌙 Поїж перед виходом, термос"

        _send(
            f"{emoji} <b>Через 10 хвилин:</b> {_esc(summary)}\n"
            f"🕐 о <b>{t}</b>{tip}\n\n"
            f"<i>Готовий?</i>"
        )
        state[key] = True
        changed = True
        print(f"[assistant] 10min reminder: {summary} at {t}")

    if changed:
        _save_state("assistant_10min.json", state)

# ─── 3. НАГАДУВАННЯ ЗА 1 ГОДИНУ (розширене) ──────────────────────────────────

def check_calendar_1h():
    """Нагадування за 1 годину — з кнопкою 'Позначити виконаним'."""
    now = _now()
    if not (7 <= now.hour <= 23):
        return

    token = _token()
    if not token:
        return

    state = _load_state("assistant_1h.json", {})

    now_utc = datetime.now(timezone.utc)
    win_start = now_utc + timedelta(minutes=58)
    win_end   = now_utc + timedelta(minutes=62)
    events = _fetch_events(token, win_start, win_end)

    changed = False
    for ev in events:
        ev_id   = ev.get("id", "")
        summary = ev.get("summary", "(без назви)")
        start_str = ev["start"].get("dateTime") or ev["start"].get("date")
        key = f"1h_{ev_id}_{start_str}"
        if state.get(key):
            continue

        # Пропускаємо якщо вже минула
        try:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            if dt < now_utc:
                state[key] = True
                changed = True
                continue
            t = (dt + timedelta(hours=2)).strftime("%H:%M")
        except Exception:
            t = "—"

        emoji = _event_emoji(summary)
        s = summary.lower()
        tips = []
        if "рання" in s:   tips = ["Приготуй одяг", "Сніданок", "Armolopid"]
        elif "нічна" in s: tips = ["Поїж перед виходом", "Armolopid", "Термос"]
        elif "лікар" in s: tips = ["Документи / страховка", "Запиши питання лікарю"]
        elif "зустріч" in s: tips = ["Підготуй матеріали до зустрічі"]
        elif "тренув" in s or "біг" in s: tips = ["Вода", "Спорядження"]

        tip_text = "  ·  ".join(tips)
        msg = (
            f"{emoji} <b>Через 1 годину:</b> {_esc(summary)}\n"
            f"🕐 о <b>{t}</b>"
        )
        if tip_text:
            msg += f"\n\n<i>{_esc(tip_text)}</i>"

        keyboard = {"inline_keyboard": [[
            {"text": "✅ Виконано / скасовано",
             "callback_data": f"cal_done_{ev_id}"},
        ]]}
        _send(msg, keyboard)
        state[key] = True
        changed = True
        print(f"[assistant] 1h reminder: {summary} at {t}")

    if changed:
        _save_state("assistant_1h.json", state)

# ─── 4. ВИДАЛЕННЯ ПОДІЇ З CALENDAR ────────────────────────────────────────────

def delete_calendar_event(event_id: str) -> dict:
    """
    Видаляє подію з Google Calendar за ID.
    Повертає {"ok": True} або {"ok": False, "error": "..."}
    """
    token = _token()
    if not token:
        return {"ok": False, "error": "Google Calendar не підключений"}

    url = f"https://www.googleapis.com/calendar/v3/calendars/{_CAL_ID}/events/{event_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        req = urllib.request.Request(url, headers=headers, method="DELETE")
        with urllib.request.urlopen(req, timeout=15) as r:
            # 204 No Content = успішно
            pass
        # Скидаємо кеш в context.py
        try:
            import sys; sys.path.insert(0, os.path.dirname(__file__))
            from context import _CAL_CACHE
            _CAL_CACHE.clear()
        except Exception:
            pass
        return {"ok": True}
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def find_event_by_summary(summary_query: str) -> list:
    """
    Шукає події в Calendar за назвою (часткове співпадіння).
    Повертає список [{id, summary, start_time}, ...]
    """
    token = _token()
    if not token:
        return []

    now_utc = datetime.now(timezone.utc)
    win_end = now_utc + timedelta(days=30)
    events = _fetch_events(token, now_utc, win_end, max_results=50)

    query = summary_query.lower()
    results = []
    for ev in events:
        s = ev.get("summary", "")
        if query in s.lower():
            start_str = ev["start"].get("dateTime") or ev["start"].get("date")
            try:
                dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                t = (dt + timedelta(hours=2)).strftime("%d.%m %H:%M")
            except Exception:
                t = start_str
            results.append({"id": ev["id"], "summary": s, "time": t})
    return results

# ─── 5. EMAIL: ВІДПРАВКА ВІДПОВІДІ ────────────────────────────────────────────

def send_email_reply(to: str, subject: str, body: str) -> dict:
    """
    Надсилає email від імені Олега через Gmail API.
    Повертає {"ok": True} або {"ok": False, "error": "..."}
    """
    token = _gmail_token()
    if not token:
        return {"ok": False, "error": "Gmail не підключений"}

    import email.mime.text, email.mime.multipart
    msg = email.mime.multipart.MIMEMultipart()
    msg["From"]    = _GMAIL_USER
    msg["To"]      = to
    msg["Subject"] = subject
    msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = json.dumps({"raw": raw}).encode()

    url = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        _gh(url, headers=headers, method="POST", data=payload)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ─── 6. ПРОАКТИВНІ ПРОПОЗИЦІЇ ПОДІЙ ──────────────────────────────────────────

def propose_calendar_events():
    """
    Раз на день (о 09:00 у вільний день) пропонує корисні події для додавання.
    Наприклад: якщо давно не планував пробіжку — пропонує додати.
    """
    now = _now()
    if not (now.hour == 9 and now.minute == 0):
        return

    state = _load_state("assistant_propose.json", {})
    today_key = now.strftime("%Y-%m-%d")
    if state.get(today_key):
        return

    # Перевіряємо чи вільний день
    try:
        import sys; sys.path.insert(0, os.path.dirname(__file__))
        from context import get_shift_from_calendar
        shift = get_shift_from_calendar()
        if shift["today"] != "free":
            return  # на зміні — не пропонуємо
    except Exception:
        return

    state[today_key] = True
    _save_state("assistant_propose.json", state)

    # AI генерує пропозиції
    token = _token()
    events_text = "немає"
    if token:
        now_utc = datetime.now(timezone.utc)
        week_events = _fetch_events(token, now_utc, now_utc + timedelta(days=7))
        if week_events:
            events_text = ", ".join(ev.get("summary","") for ev in week_events[:10])

    ai_prompt = (
        f"Сьогодні вільний день у Олега (Кошіце, Словаччина). "
        f"На цьому тижні заплановано: {events_text}.\n"
        f"Запропонуй 2-3 конкретні корисні справи для додавання в Calendar сьогодні: "
        f"пробіжка, навчання, здоров'я, фінанси тощо. "
        f"Кожна пропозиція — одне речення. Список з дефісами. Українською."
    )
    suggestions = _gemini(ai_prompt, max_tokens=200)
    if not suggestions:
        return

    _send(
        f"📅 <b>Привіт! Вільний день — що плануємо?</b>\n\n"
        f"{_esc(suggestions)}\n\n"
        f"<i>Напиши мені і я додам в Calendar 😊</i>"
    )
    print(f"[assistant] propose_events sent for {today_key}")

# ─── 7. EMAIL ЧИТАННЯ ─────────────────────────────────────────────────────────

def get_email_full_text(uid_str: str) -> dict:
    """
    Читає повний текст листа за IMAP UID.
    Повертає {"subject": ..., "sender": ..., "body": ..., "reply_to": ...}
    """
    try:
        import sys; sys.path.insert(0, os.path.dirname(__file__))
        from monitor import _imap_connect, _imap_get_body, _imap_decode_header
        import email as _elib

        mail = _imap_connect()
        mail.select("INBOX")
        _, msg_data = mail.uid("fetch", uid_str.encode(), "(RFC822)")
        mail.logout()

        if not msg_data or not msg_data[0]:
            return {"error": "Лист не знайдено"}

        msg = _elib.message_from_bytes(msg_data[0][1])
        return {
            "subject":  _imap_decode_header(msg.get("Subject", "")),
            "sender":   _imap_decode_header(msg.get("From", "")),
            "reply_to": _imap_decode_header(msg.get("Reply-To", "")) or _imap_decode_header(msg.get("From", "")),
            "body":     _imap_get_body(msg),
        }
    except Exception as e:
        return {"error": str(e)}

def generate_email_reply_draft(uid_str: str) -> str:
    """
    Генерує AI draft відповіді на лист.
    Повертає текст draft.
    """
    email_data = get_email_full_text(uid_str)
    if "error" in email_data:
        return f"⚠️ {email_data['error']}"

    prompt = (
        f"Ти пишеш від імені Олега Новосадова (novosadovoleg@gmail.com, Кошіце).\n"
        f"Напиши природній draft відповіді на цей лист.\n"
        f"Від: {email_data['sender']}\n"
        f"Тема: {email_data['subject']}\n\n"
        f"{email_data['body'][:2000]}\n\n"
        f"Відповідь: українською або мовою оригіналу листа. "
        f"Коротко і по суті, 3-6 речень. Не формально."
    )
    return _gemini(prompt, max_tokens=350) or "⚠️ Не вдалось згенерувати відповідь"
