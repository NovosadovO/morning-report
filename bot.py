#!/usr/bin/env python3
"""
Telegram bot — відповідає на команди користувача.
Команди:
  /start    — привітання
  /звіт     — повний звіт зараз
  /ціни     — ціни активів
  /погода   — погода Košice
  /календар — події на сьогодні
  /листи    — останні email
  /допомога — список команд
"""

import os
import json
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
OFFSET_FILE    = "/tmp/bot_offset.json"

# Унікальний ідентифікатор цього інстансу бота (leader election)
_INSTANCE_ID = str(uuid.uuid4())[:12]
_DRAFT_STORE: dict = {}  # uid_str -> {to, subject, body} — тимчасовий store для email drafts
_IMPORTANT_EMAILS_FILE = "data/important_emails.json"  # важливі листи (GitHub)
_GH_TOKEN    = os.environ.get("GITHUB_TOKEN", "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")
_GH_LOCK_URL = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/bot_lock.json"
_GH_OFF_URL  = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/bot_offset.json"

# Імпортуємо функції з monitor.py
import sys
sys.path.insert(0, os.path.dirname(__file__))
from monitor import get_prices, get_weather, get_calendar, get_emails

# ─── TELEGRAM API ─────────────────────────────────────────────────────────────

def api(method, data=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    payload = json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=payload,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
            if not resp.get("ok"):
                print(f"[API] {method} not ok: {resp.get('description','?')} | data={str(data)[:200]}")
            return resp
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[API] {method} HTTP {e.code}: {body[:300]}")
        return {}
    except Exception as e:
        print(f"[API] {method} error: {e}")
        return {}


def send(chat_id, text):
    api("sendMessage", {
        "chat_id": chat_id,
        "text": text[:4090],
        "parse_mode": "HTML"
    })

def send_reply(chat_id, reply_to_msg_id, text):
    api("sendMessage", {
        "chat_id": chat_id,
        "text": text[:4090],
        "parse_mode": "HTML",
        "reply_to_message_id": reply_to_msg_id
    })


def send_photo(chat_id, photo_bytes, caption=None):
    try:
        import requests as _rq
        import io as _io
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        r = _rq.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
            data=data,
            files={"photo": ("photo.png", _io.BytesIO(photo_bytes), "image/png")},
            timeout=30
        )
        if not r.ok:
            print(f"[send_photo] error {r.status_code}: {r.text[:200]}", flush=True)
    except Exception as e:
        print(f"[send_photo] error: {e}", flush=True)


def send_with_keyboard(chat_id, text, keyboard):
    api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": keyboard}
    })


def send_with_buttons(chat_id, text, habit_id):
    return api("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Так", "callback_data": f"habit_yes_{habit_id}"},
                {"text": "❌ Ні",  "callback_data": f"habit_no_{habit_id}"},
            ]]
        }
    })


def log_to_calendar(summary, date_str, hour, minute):
    """Додає подію-висновок в Google Calendar через API напряму."""
    try:
        import sys, json as _json, urllib.request, urllib.parse
        from datetime import datetime, timedelta
        sys.path.insert(0, os.path.dirname(__file__))
        from monitor import _get_google_token

        creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
        if not creds_json:
            print("Calendar log: no credentials")
            return

        creds_data = _json.loads(creds_json)
        token = _get_google_token(
            creds_data, "https://www.googleapis.com/auth/calendar.events")

        start_str = f"{date_str}T{hour:02d}:{minute:02d}:00+02:00"
        end_dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M") + timedelta(minutes=30)
        end_str = f"{date_str}T{end_dt.hour:02d}:{end_dt.minute:02d}:00+02:00"

        event = {
            "summary": summary,
            "start": {"dateTime": start_str, "timeZone": "Europe/Bratislava"},
            "end":   {"dateTime": end_str,   "timeZone": "Europe/Bratislava"},
        }
        body = _json.dumps(event).encode()
        req = urllib.request.Request(
            "https://www.googleapis.com/calendar/v3/calendars/novosadovoleg%40gmail.com/events",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        print(f"Calendar log OK: {summary}")
    except Exception as e:
        print(f"Calendar log error: {e}")


def handle_meds_callback(callback_query):
    """Обробляє ✅/❌ відповідь на питання про ліки."""
    import json as _json
    data    = callback_query.get("data", "")
    msg_id  = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]
    cb_id   = callback_query["id"]

    # ПЕРШИМ — підтверджуємо callback (завжди, незалежно від решти)
    try:
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Записано ✓"})
    except Exception as _ae:
        print(f"answerCallbackQuery error: {_ae}")

    # meds_yes_2026-04-27 або meds_no_2026-04-27
    parts    = data.split("_", 2)
    answer   = parts[1] if len(parts) > 1 else "yes"  # yes / no
    date_raw = parts[2] if len(parts) > 2 else ""
    from datetime import datetime, timezone, timedelta as _td
    _now = datetime.now(timezone.utc) + _td(hours=2)
    if not date_raw or date_raw == "today":
        if 0 <= _now.hour < 6:
            date = (_now - _td(days=1)).strftime("%Y-%m-%d")
        else:
            date = _now.strftime("%Y-%m-%d")
    else:
        date = date_raw

    # ДРУГИМ — редагуємо повідомлення (прибираємо кнопки) — до будь-яких важких операцій
    if answer == "yes":
        reply = "💊 <b>ARMOLOPID PLUS</b>\n\n✅ <b>Прийнято!</b> Молодець 💪\nПродовжуй в тому ж дусі."
    else:
        reply = "💊 <b>ARMOLOPID PLUS</b>\n\n❌ <b>Не прийнято</b> — прийми при першій нагоді!"

    try:
        api("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": reply,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []}
        })
    except Exception as _ee:
        print(f"editMessageText error: {_ee}")
        # Якщо edit не вдався — хоча б видалимо кнопки окремим запитом
        try:
            api("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "reply_markup": {"inline_keyboard": []}
            })
        except Exception as _re:
            print(f"editMessageReplyMarkup error: {_re}")

    # ТРЕТІМ — зберігаємо в storage (не критично якщо впаде)
    try:
        import sys as _sys; _sys.path.insert(0, os.path.dirname(__file__))
        from storage import load_meds as _lm, save_meds as _sm
        meds_db = _lm()
        meds_db[date] = (answer == "yes")
        _sm(meds_db)
        print(f"meds saved: {date} = {answer}")
    except Exception as _se:
        print(f"meds save error (storage): {_se}")
        # Fallback: локальний файл
        try:
            meds_file = "/tmp/meds_data.json"
            try:
                with open(meds_file) as f:
                    meds_db = _json.load(f)
            except Exception:
                meds_db = {}
            meds_db[date] = (answer == "yes")
            with open(meds_file, "w") as f:
                _json.dump(meds_db, f)
        except Exception as _fe:
            print(f"meds save error (file): {_fe}")

    # ЧЕТВЕРТИМ — логуємо в Google Calendar (не критично)
    try:
        now_l = datetime.now(timezone.utc) + _td(hours=2)
        mark = "✅" if answer == "yes" else "❌"
        log_to_calendar(f"💊 Armolopid Plus {mark}", date, now_l.hour, now_l.minute)
    except Exception as _ce:
        print(f"meds log_to_calendar error: {_ce}")


def get_meds_report(period="week"):
    """Звіт про прийом ліків за тиждень або місяць."""
    import json as _json
    from datetime import datetime, timezone, timedelta
    meds_file = "/tmp/meds_data.json"
    try:
        with open(meds_file) as f:
            db = _json.load(f)
    except:
        db = {}

    now = datetime.now(timezone.utc) + timedelta(hours=2)
    if period == "week":
        days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        title = "тиждень"
    else:
        days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(now.day - 1, -1, -1)]
        title = now.strftime("%B %Y")

    taken = sum(1 for d in days if db.get(d) is True)
    missed = sum(1 for d in days if db.get(d) is False)
    no_data = len(days) - taken - missed
    pct = int(taken / len(days) * 100) if days else 0

    stars = "⭐️" * min(taken, 7) + "☆" * (7 - min(taken, 7)) if period == "week" else ""

    lines = [
        f"💊 <b>Armolopid Plus — {title}</b>\n",
        f"✅ Прийнято:    <b>{taken}</b> дн.",
        f"❌ Пропущено:  <b>{missed}</b> дн.",
        f"○  Немає даних: <b>{no_data}</b> дн.",
    ]
    if stars:
        lines.append(f"\n{stars}  {pct}%")
    else:
        filled = int(pct / 10)
        bar = "🟩" * filled + "⬜️" * (10 - filled)
        lines.append(f"\n<code>[{bar}]</code>  {pct}%")

    if pct == 100:   lines.append("🏆 Ідеально!")
    elif pct >= 80:  lines.append("💪 Відмінно!")
    elif pct >= 60:  lines.append("👍 Непогано")
    else:            lines.append("⚠️ Намагайся не пропускати!")

    return "\n".join(lines)


def handle_email_callback(callback_query):
    """Обробляє кнопки листів: Описати / Видалити / Залишити / В календар."""
    import json as _json
    data    = callback_query.get("data", "")
    msg_id  = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]
    cb_id   = callback_query["id"]

    if data.startswith("email_describe_"):
        uid_str = data[len("email_describe_"):]
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "📖 Читаю лист..."})

        _cid = chat_id
        _uid = uid_str
        _mid = msg_id
        GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")

        try:
            print(f"[email_describe] uid={_uid}", flush=True)

            import sys as _sys
            _sys.path.insert(0, os.path.dirname(__file__))
            import storage as _storage

            # ── Оригінальний текст повідомлення ──────────────────────────────
            orig_text = callback_query["message"].get("text", "")
            orig_kb   = callback_query["message"].get("reply_markup", {})

            # ── Читаємо з кешу ───────────────────────────────────────────────
            cache = _storage.load("email_body_cache.json") or {}
            entry = cache.get(_uid)
            print(f"[email_describe] cache entry={'found' if entry else 'MISS'}", flush=True)

            if not entry:
                try:
                    import imaplib, email as _email_lib, socket
                    socket.setdefaulttimeout(25)
                    _mail = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"), timeout=25)
                    _mail.login(os.environ.get("EMAIL_USER", ""), os.environ.get("EMAIL_PASS", ""))
                    _mail.select("INBOX")
                    _, _md = _mail.uid('fetch', _uid.encode(), "(RFC822)")
                    _mail.logout()
                    _msg = _email_lib.message_from_bytes(_md[0][1])
                    import email.header as _eh
                    def _dh(v):
                        if not v: return ""
                        parts = _eh.decode_header(v)
                        return " ".join(p.decode(e or "utf-8", errors="replace") if isinstance(p, bytes) else str(p) for p, e in parts)
                    subject = _dh(_msg.get("Subject", "(без теми)"))
                    sender  = _dh(_msg.get("From", ""))
                    body = ""
                    if _msg.is_multipart():
                        for _part in _msg.walk():
                            if _part.get_content_type() == "text/plain":
                                body = _part.get_payload(decode=True).decode(_part.get_content_charset() or "utf-8", errors="replace")
                                break
                    else:
                        body = _msg.get_payload(decode=True).decode(_msg.get_content_charset() or "utf-8", errors="replace")
                    entry = {"subject": subject, "sender": sender, "body": body[:2000]}
                except Exception as _fe:
                    api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"⚠️ Лист не знайдено"})
                    return

            subject = entry.get("subject", "(без теми)")
            sender  = entry.get("sender", "")
            body    = entry.get("body", "")

            # ── Gemini ───────────────────────────────────────────────────────
            prompt = (
                "Проаналізуй цей email і відповідай ТІЛЬКИ українською. Без зірочок, без markdown.\n"
                "Формат — 4 розділи. Пиши стисло але повно — кожен розділ завершуй повністю:\n\n"
                "ВІДПРАВНИК\n"
                "1-2 речення: хто це, компанія, роль.\n\n"
                "СУТЬ\n"
                "4-5 речень: що пропонують/повідомляють, контекст, головне послання.\n\n"
                "ДЕТАЛІ\n"
                "2-3 речення: факти, дати, суми, умови, посилання.\n\n"
                "ДІЇ\n"
                "2-3 речення: що конкретно зробити, в який термін, пріоритет.\n\n"
                f"Лист:\nВід: {sender}\nТема: {subject}\n\n{body[:3000]}"
            )
            req_body = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 1500, "temperature": 0.3}
            }).encode()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
            req = urllib.request.Request(url, data=req_body, headers={"Content-Type": "application/json"})

            ai_text = None
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    resp_data = json.loads(r.read())
                ai_text = resp_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                ai_text = ai_text.replace("**", "").replace("*", "")
                print(f"[email_describe] gemini ok, len={len(ai_text)}", flush=True)
            except Exception as _ge:
                print(f"[email_describe] gemini error: {_ge}", flush=True)

            import re as _re3
            sender_name = _re3.sub(r'<[^>]+>', '', sender).strip().strip('"').strip("'") or sender[:60]

            if ai_text:
                description = f"\n\n─────────────────\n📖 ОПИС ЛИСТА\n\n{ai_text}"
            else:
                preview = body[:600].strip() if body else "(порожній лист)"
                description = f"\n\n─────────────────\n📖 ТЕКСТ ЛИСТА\n\n{preview}"

            # ── Зберігаємо оригінал для відновлення ──────────────────────────
            orig_cache = _storage.load("email_orig_text.json") or {}
            orig_cache[f"{_cid}_{_mid}"] = {"text": orig_text, "keyboard": orig_kb}
            _storage.save("email_orig_text.json", orig_cache)

            # ── Нова клавіатура з кнопкою "Прибрати опис" ────────────────────
            new_kb = orig_kb.copy() if orig_kb else {"inline_keyboard": []}
            # Замінюємо кнопку "Описати лист" на "🗑 Прибрати опис"
            new_rows = []
            for row in new_kb.get("inline_keyboard", []):
                new_row = []
                for btn in row:
                    if btn.get("callback_data", "").startswith("email_describe_"):
                        new_row.append({"text": "🗑 Прибрати опис", "callback_data": f"email_undescribe_{_uid}"})
                    else:
                        new_row.append(btn)
                new_rows.append(new_row)
            new_kb["inline_keyboard"] = new_rows

            # ── Редагуємо повідомлення листа ─────────────────────────────────
            new_text = (orig_text + description)[:4096]
            api("editMessageText", {
                "chat_id": _cid,
                "message_id": _mid,
                "text": new_text,
                "reply_markup": new_kb
            })

        except Exception as _e:
            import traceback as _tb
            _tb.print_exc()
            api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"Помилка: {str(_e)[:100]}"})

    elif data.startswith("email_undescribe_"):
        uid_str = data[len("email_undescribe_"):]
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Опис прибрано"})

        import sys as _sys
        _sys.path.insert(0, os.path.dirname(__file__))
        import storage as _storage

        orig_cache = _storage.load("email_orig_text.json") or {}
        key = f"{chat_id}_{msg_id}"
        orig = orig_cache.get(key)
        if orig:
            api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": orig["text"],
                "reply_markup": orig["keyboard"]
            })
        else:
            api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "⚠️ Оригінал не знайдено"})

    elif data.startswith("email_delete_"):
        uid_str = data[len("email_delete_"):]
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(__file__))
            from monitor import _imap_delete_email
            ok = _imap_delete_email(uid_str)
            if ok:
                api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "🗑 Лист видалено"})
            else:
                api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "⚠️ Не вдалось видалити"})
            api("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "reply_markup": {"inline_keyboard": []}
            })
        except Exception as e:
            print(f"email_delete error: {e}")
            api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"Помилка: {e}"})

    elif data.startswith("email_keep_"):
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "📥 Залишено в скриньці"})
        api("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "reply_markup": {"inline_keyboard": []}
        })

    elif data.startswith("email_star_"):
        # Зберігаємо лист як важливий у GitHub
        uid_str = data[len("email_star_"):]
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "⭐ Збережено як важливий"})
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(__file__))
            from monitor import _imap_connect, _imap_get_body, _imap_decode_header
            import email as _email_lib, base64 as _b64, urllib.request as _ur2

            mail = _imap_connect()
            mail.select("INBOX")
            _, msg_data = mail.uid('fetch', uid_str.encode(), "(RFC822)")
            mail.logout()

            subject, sender, body = "(невідомо)", "(невідомо)", ""
            if msg_data and msg_data[0]:
                msg = _email_lib.message_from_bytes(msg_data[0][1])
                subject = _imap_decode_header(msg.get("Subject", ""))
                sender  = _imap_decode_header(msg.get("From", ""))
                body    = _imap_get_body(msg)[:500]

            # Завантажуємо поточний список важливих
            gh_url = f"https://api.github.com/repos/NovosadovO/morning-report/contents/data/important_emails.json"
            gh_headers = {"Authorization": f"token {_GH_TOKEN}", "User-Agent": "bot"}
            try:
                req = _ur2.Request(gh_url, headers=gh_headers)
                with _ur2.urlopen(req, timeout=10) as r:
                    gh_data = _json.loads(r.read())
                    existing = _json.loads(_b64.b64decode(gh_data["content"]).decode())
                    sha = gh_data["sha"]
            except Exception:
                existing, sha = [], None

            import datetime as _dt2
            existing.append({
                "uid": uid_str,
                "subject": subject,
                "sender": sender,
                "preview": body,
                "saved_at": _dt2.datetime.utcnow().isoformat()
            })

            content = _b64.b64encode(_json.dumps(existing, ensure_ascii=False, indent=2).encode()).decode()
            body_gh = {"message": "star email", "content": content}
            if sha:
                body_gh["sha"] = sha
            req2 = _ur2.Request(gh_url, data=_json.dumps(body_gh).encode(), headers={**gh_headers, "Content-Type": "application/json"}, method="PUT")
            _ur2.urlopen(req2, timeout=15)

            send(chat_id, f"⭐ <b>Збережено як важливий</b>\n<i>{subject[:80]}</i>\n\nНагадаю якщо не дав відповідь протягом 24 год.")
        except Exception as e:
            print(f"email_star error: {e}")
            send(chat_id, f"⚠️ Помилка збереження: {e}")

    elif data.startswith("email_cal_"):
        # AI витягує дату/подію з листа і пропонує додати в Google Calendar
        uid_str = data[len("email_cal_"):]
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "📅 Аналізую лист..."})
        try:
            import sys, os as _os, urllib.request as _ur3, re as _re3
            sys.path.insert(0, _os.path.dirname(__file__))
            from monitor import _imap_connect, _imap_get_body, _imap_decode_header
            import email as _email_lib, datetime as _dt3

            mail = _imap_connect()
            mail.select("INBOX")
            _, msg_data = mail.uid('fetch', uid_str.encode(), "(RFC822)")
            mail.logout()

            if not (msg_data and msg_data[0]):
                send(chat_id, "⚠️ Не вдалось завантажити лист")
                return

            msg = _email_lib.message_from_bytes(msg_data[0][1])
            subject = _imap_decode_header(msg.get("Subject", ""))
            sender  = _imap_decode_header(msg.get("From", ""))
            body    = _imap_get_body(msg)

            api_key = _os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")
            today = _dt3.date.today().isoformat()
            prompt = (
                f"Сьогодні {today}. Проаналізуй цей лист і знайди всі важливі дати, події, дедлайни, зустрічі.\n"
                f"Від: {sender}\nТема: {subject}\n\n{body[:3000]}\n\n"
                f"Відповідь ТІЛЬКИ у форматі JSON (без markdown):\n"
                f"{{\"events\": [{{\"title\": \"назва події\", \"date\": \"YYYY-MM-DD\", \"time\": \"HH:MM або null\", \"description\": \"деталі\"}}]}}\n"
                f"Якщо дат немає — {{\"events\": []}}\n"
                f"Мова полів: українська."
            )
            payload = _json.dumps({
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 500, "temperature": 0.3}
            }).encode()
            req = _ur3.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
                data=payload, headers={"Content-Type": "application/json"}
            )
            with _ur3.urlopen(req, timeout=25) as r:
                resp = _json.loads(r.read())
            raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            m = _re3.search(r'\{.*\}', raw, _re3.DOTALL)
            if not m:
                send(chat_id, "📅 У листі не знайдено жодних дат або подій.")
                return

            parsed = _json.loads(m.group(0))
            events = parsed.get("events", [])
            if not events:
                send(chat_id, "📅 У листі не знайдено жодних дат або подій.")
                return

            # Зберігаємо events для підтвердження
            _DRAFT_STORE[f"cal_{uid_str}"] = {"events": events, "subject": subject}

            lines = [f"📅 <b>Знайдено події в листі:</b>\n<i>{subject[:60]}</i>\n"]
            for i, ev in enumerate(events[:5]):
                t = ev.get("time") or "весь день"
                lines.append(f"{i+1}. <b>{ev['title']}</b>\n   📆 {ev['date']}  🕐 {t}\n   <i>{ev.get('description','')[:80]}</i>")

            send_with_keyboard(chat_id,
                "\n".join(lines) + "\n\nДодати ці події в Google Calendar?",
                [[
                    {"text": "✅ Додати всі", "callback_data": f"cal_add_{uid_str}"},
                    {"text": "❌ Скасувати",  "callback_data": f"cal_skip_{uid_str}"}
                ]]
            )
        except Exception as e:
            print(f"email_cal error: {e}")
            send(chat_id, f"⚠️ Помилка: {e}")

    elif data.startswith("cal_add_"):
        # Додаємо події в Google Calendar
        uid_str = data[len("cal_add_"):]
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "📅 Додаю в календар..."})
        try:
            import sys, os as _os, urllib.request as _ur4, urllib.parse as _up4, datetime as _dt4
            sys.path.insert(0, _os.path.dirname(__file__))
            from monitor import _get_google_token

            store_key = f"cal_{uid_str}"
            cal_data = _DRAFT_STORE.pop(store_key, None)
            if not cal_data:
                send(chat_id, "⚠️ Дані не знайдено. Спробуй ще раз.")
                return

            creds_json = _os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
            if not creds_json:
                send(chat_id, "⚠️ Google Calendar не налаштовано.")
                return

            import json as _j4
            creds_data = _j4.loads(creds_json)
            token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar")
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            cal_id = "novosadovoleg%40gmail.com"

            added = []
            for ev in cal_data["events"][:5]:
                try:
                    date_str = ev["date"]
                    time_str = ev.get("time")
                    if time_str and time_str != "null":
                        start = {"dateTime": f"{date_str}T{time_str}:00+02:00", "timeZone": "Europe/Prague"}
                        end_dt = _dt4.datetime.fromisoformat(f"{date_str}T{time_str}:00") + _dt4.timedelta(hours=1)
                        end = {"dateTime": end_dt.strftime(f"%Y-%m-%dT%H:%M:00+02:00"), "timeZone": "Europe/Prague"}
                    else:
                        start = {"date": date_str}
                        end = {"date": date_str}

                    body_ev = _j4.dumps({
                        "summary": ev["title"],
                        "description": f"{ev.get('description','')}\n\n📧 З листа: {cal_data['subject']}",
                        "start": start,
                        "end": end,
                    }).encode()
                    url = f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
                    req = _ur4.Request(url, data=body_ev, headers=headers, method="POST")
                    with _ur4.urlopen(req, timeout=15) as r:
                        r.read()
                    added.append(f"✅ {ev['title']} ({ev['date']})")
                except Exception as e_ev:
                    added.append(f"⚠️ {ev.get('title','?')}: {e_ev}")

            api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": {"inline_keyboard": []}})
            send(chat_id, "📅 <b>Додано в Google Calendar:</b>\n" + "\n".join(added))

            # ── Зберігаємо дедлайни в GitHub для нагадування -24г ──────────
            try:
                import base64 as _b64dl, urllib.request as _ur_dl, json as _jdl
                _dl_url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/email_deadlines.json"
                _dl_headers = {"Authorization": f"token {_GH_TOKEN}", "User-Agent": "bot"}

                # Читаємо поточний файл
                try:
                    _r = _ur_dl.Request(_dl_url, headers=_dl_headers)
                    with _ur_dl.urlopen(_r, timeout=10) as _resp:
                        _existing = _jdl.loads(_resp.read())
                    _dl_sha = _existing.get("sha", "")
                    _dl_data = _jdl.loads(_b64dl.b64decode(_existing["content"]).decode())
                    if not isinstance(_dl_data, list):
                        _dl_data = []
                except Exception:
                    _dl_sha = ""
                    _dl_data = []

                # Додаємо нові дедлайни (тільки успішно додані)
                _now_iso = _dt4.datetime.now(_dt4.timezone.utc).isoformat()
                for ev in cal_data["events"][:5]:
                    if ev.get("date"):
                        _dl_data.append({
                            "date": ev["date"],
                            "title": ev["title"],
                            "subject": cal_data.get("subject", ""),
                            "added_at": _now_iso,
                            "reminded": False
                        })

                _content_enc = _b64dl.b64encode(_jdl.dumps(_dl_data, ensure_ascii=False, indent=2).encode()).decode()
                _body_dl = {"message": "email deadlines update", "content": _content_enc}
                if _dl_sha:
                    _body_dl["sha"] = _dl_sha
                _req_dl = _ur_dl.Request(_dl_url, data=_jdl.dumps(_body_dl).encode(),
                                          headers={**_dl_headers, "Content-Type": "application/json"}, method="PUT")
                _ur_dl.urlopen(_req_dl, timeout=15)
                print(f"email_deadlines saved: {len(cal_data['events'])} events")
            except Exception as _dle:
                print(f"email_deadlines save error: {_dle}")

        except Exception as e:
            print(f"cal_add error: {e}")
            send(chat_id, f"⚠️ Помилка: {e}")

    elif data.startswith("cal_skip_"):
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Скасовано"})
        api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": {"inline_keyboard": []}})

    elif data.startswith("email_reply_"):
        # Генеруємо AI draft відповіді + кнопки [Надіслати] [Скасувати]
        uid_str = data[len("email_reply_"):]
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "✍️ Готую draft..."})
        try:
            import sys, os as _os, urllib.request as _ur
            sys.path.insert(0, _os.path.dirname(__file__))
            from monitor import _imap_connect, _imap_get_body, _imap_decode_header
            import email as _email_lib

            mail = _imap_connect()
            mail.select("INBOX")
            _, msg_data = mail.uid('fetch', uid_str.encode(), "(RFC822)")
            mail.logout()

            if msg_data and msg_data[0]:
                msg = _email_lib.message_from_bytes(msg_data[0][1])
                subject = _imap_decode_header(msg.get("Subject", ""))
                sender  = _imap_decode_header(msg.get("From", ""))
                body    = _imap_get_body(msg)

                # AI draft
                api_key = _os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")
                prompt = (
                    f"Ти пишеш від імені Олега Новосадова (novosadovoleg@gmail.com).\n"
                    f"Напиши КОРОТКИЙ і природній draft відповіді на цей лист.\n"
                    f"Від: {sender}\nТема: {subject}\n\n{body[:2000]}\n\n"
                    f"ВАЖЛИВО: відповідай ТІЄЮ САМОЮ МОВОЮ на якій написаний оригінальний лист. "
                    f"Якщо лист словацькою — відповідь словацькою. Якщо англійською — англійською. Якщо українською — українською. "
                    f"По-людськи, не формально. 3-6 речень максимум."
                )
                payload = _json.dumps({
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 300, "temperature": 0.85}
                }).encode()
                req = _ur.Request(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
                    data=payload, headers={"Content-Type": "application/json"}
                )
                with _ur.urlopen(req, timeout=20) as r:
                    resp = _json.loads(r.read())
                draft = resp["candidates"][0]["content"]["parts"][0]["text"].strip()

                # Зберігаємо draft + метадані для подальшого надсилання
                import re as _re
                to_addr = _re.search(r'<(.+?)>', sender)
                to_addr = to_addr.group(1) if to_addr else sender.strip()
                _DRAFT_STORE[uid_str] = {"to": to_addr, "subject": f"Re: {subject}", "body": draft}

                send_with_keyboard(chat_id,
                    f"✍️ <b>Draft відповіді</b>\n"
                    f"<i>Кому: {to_addr[:60]}\nТема: Re: {subject[:50]}</i>\n\n"
                    f"{draft}\n\n"
                    f"<i>Надіслати цей draft?</i>",
                    [[
                        {"text": "✉️ Надіслати", "callback_data": f"email_send_{uid_str}"},
                        {"text": "❌ Скасувати", "callback_data": f"email_cancel_{uid_str}"}
                    ]]
                )
            else:
                send(chat_id, "⚠️ Не вдалось завантажити лист")
        except Exception as e:
            print(f"email_reply error: {e}")
            send(chat_id, f"⚠️ Помилка генерації draft: {e}")

    elif data.startswith("email_send_"):
        uid_str = data[len("email_send_"):]
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "📤 Надсилаю..."})
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(__file__))
            draft_info = _DRAFT_STORE.pop(uid_str, None)
            if not draft_info:
                send(chat_id, "⚠️ Draft не знайдено. Спробуй ще раз через кнопку Відповісти.")
                return
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "assistant", _os.path.join(_os.path.dirname(__file__), "assistant.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            ok = mod.send_email_reply(draft_info["to"], draft_info["subject"], draft_info["body"])
            if ok.get("ok"):
                api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": {"inline_keyboard": []}})
                send(chat_id, f"✅ Лист надіслано → {draft_info['to']}")
            else:
                send(chat_id, f"⚠️ Помилка надсилання: {ok.get('error', 'невідома помилка')}")
        except Exception as e:
            print(f"email_send error: {e}")
            send(chat_id, f"⚠️ Помилка: {e}")

    elif data.startswith("email_cancel_"):
        api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Скасовано"})
        api("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": msg_id, "reply_markup": {"inline_keyboard": []}})


def handle_reminder_callback(callback_query):
    """Обробляє ✅/❌ відповідь на одноразове нагадування."""
    import json as _json
    data    = callback_query.get("data", "")
    msg_id  = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]
    cb_id   = callback_query["id"]
    orig    = callback_query["message"].get("text", "")

    api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Записано ✓"})

    # reminder_yes_<id> або reminder_no_<id>
    parts  = data.split("_", 2)
    answer = parts[1] if len(parts) > 1 else "?"

    if answer == "yes":
        reply = orig + "\n\n✅ <b>Зроблено!</b>"
    else:
        reply = orig + "\n\n❌ <b>Не зроблено.</b>"

    try:
        api("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": reply,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []}
        })
    except Exception as e:
        print(f"reminder editMessage error: {e}")


def handle_event_done_callback(callback_query):
    """Обробляє ✅/❌ відповідь на питання 'Виконано?'."""
    import json as _json
    data    = callback_query.get("data", "")
    msg_id  = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]
    cb_id   = callback_query["id"]
    orig    = callback_query["message"].get("text", "")

    # ПЕРШИМ — відповідаємо Telegram
    api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Записано ✓"})

    # evdone_yes_<key> або evdone_no_<key>
    parts  = data.split("_", 2)
    answer = parts[1] if len(parts) > 1 else "?"
    key    = parts[2] if len(parts) > 2 else ""

    if answer == "yes":
        reply = orig.split("\n")[0] + "\n✅ <b>Виконано!</b>"
    elif answer == "skip":
        reply = orig.split("\n")[0] + "\n⏭ <b>Перенесено.</b>"
    else:
        reply = orig.split("\n")[0] + "\n❌ <b>Не виконано.</b>"

    try:
        api("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": reply,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []}
        })
    except Exception as e:
        print(f"event_done editMessage error: {e}")

    # Зберігаємо статус у файл для підсумку дня
    try:
        results_file = os.path.join(os.path.dirname(__file__), "monitor_event_results.json")
        if os.path.exists(results_file):
            with open(results_file) as f:
                results = _json.load(f)
        else:
            results = {}
        results[key] = answer
        with open(results_file, "w") as f:
            _json.dump(results, f)
    except Exception as e:
        print(f"event results save error: {e}")

    # Якщо ✅ — оновлюємо подію в Google Calendar (зелений + ✅ в назві)
    if answer == "yes":
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from monitor import _get_google_token
            import json as _j, urllib.request as _ur, urllib.parse as _up

            creds_json = _os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
            if creds_json:
                creds_data = _j.loads(creds_json)
                token = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar")
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
                cal_id = "novosadovoleg%40gmail.com"

                # Витягуємо ev_id з key: "done_{ev_id}_{end_raw}" або safe_key версія
                # key виглядає як: done_abc123_2026-04-29T14:15:00+02:00
                key_parts = key.split("_", 2)
                ev_id = key_parts[1] if len(key_parts) > 1 else ""

                if ev_id:
                    # Отримуємо поточну подію
                    get_url = f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events/{ev_id}"
                    req_get = _ur.Request(get_url, headers=headers)
                    with _ur.urlopen(req_get, timeout=10) as r:
                        ev = _j.loads(r.read())

                    # Додаємо ✅ в назву якщо ще немає
                    summary = ev.get("summary", "")
                    if not summary.startswith("✅"):
                        ev["summary"] = "✅ " + summary

                    # Зелений колір (sage=10 або basil=9 або green)
                    ev["colorId"] = "10"  # sage (зелений)

                    # Оновлюємо
                    patch_data = _j.dumps({
                        "summary": ev["summary"],
                        "colorId": ev["colorId"]
                    }).encode()
                    patch_url = f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events/{ev_id}"
                    req_patch = _ur.Request(patch_url, data=patch_data, headers=headers, method="PATCH")
                    _ur.urlopen(req_patch, timeout=10)
                    print(f"Calendar event updated: {ev['summary']}")
        except Exception as e:
            print(f"Calendar update error: {e}")


def handle_habit_callback(callback_query):
    """Обробляє натискання ✅/❌ на звичках."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from habits import HABITS, load_data, save_data, today_key

    data    = callback_query.get("data", "")
    msg_id  = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]
    cb_id   = callback_query["id"]

    # ПЕРШИМ — підтверджуємо callback, щоб Telegram прибрав годинник
    api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Збережено ✓"})

    # Обробка сну
    if data.startswith("sleep_"):
        try:
            hours = int(data.split("_")[1])
            today    = today_key()
            db       = load_data()
            db.setdefault(today, {})["sleep"] = hours
            save_data(db)
            icons = {5: "😩", 6: "😐", 7: "🙂", 8: "😊"}
            icon  = icons.get(hours, "😴")
            label = f"{hours}г+" if hours == 8 else f"{hours}г"
            api("editMessageText", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "text": f"😴 <b>Сон</b> — {label} записано  {icon}",
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": []}
            })
        except Exception as e:
            print(f"sleep callback error: {e}")
        return True

    if not data.startswith("habit_"):
        return False

    parts  = data.split("_")   # habit_yes_shower або habit_toggle_shower
    action = parts[1]           # yes / no / toggle
    hab_id = parts[2]           # shower / run / water

    # habit_toggle_ — з команди /звички (перемикає стан)
    if action == "toggle":
        all_habits = [{"id": "shower", "name": "Холодний душ", "emoji": "🚿"}] + HABITS
        habit = next((h for h in all_habits if h["id"] == hab_id), None)
        if not habit:
            return False
        try:
            today = today_key()
            db = load_data()
            day_data = db.setdefault(today, {})
            current = day_data.get(hab_id)
            # toggle: None→True→False→True
            day_data[hab_id] = False if current is True else True
            save_data(db)

            # Оновлюємо весь список кнопок
            keyboard = []
            for h in all_habits:
                done = db[today].get(h["id"])
                status = "✅" if done is True else ("❌" if done is False else "⬜️")
                keyboard.append([
                    {"text": f"{h['emoji']} {h['name']} {status}", "callback_data": f"habit_toggle_{h['id']}"},
                ])
            api("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": msg_id,
                "reply_markup": {"inline_keyboard": keyboard}
            })
        except Exception as e:
            print(f"habit toggle error: {e}")
        return True

    answer = action  # yes / no

    all_h = [{"id": "shower", "name": "Холодний душ", "emoji": "🚿"}] + HABITS
    habit = next((h for h in all_h if h["id"] == hab_id), None)
    if not habit:
        return False

    try:
        # Зберігаємо результат
        today    = today_key()
        db       = load_data()
        db.setdefault(today, {})[hab_id] = (answer == "yes")
        save_data(db)

        if answer == "yes":
            reply = f"✅ <b>{habit['name']}</b> — зараховано! 💪"
        else:
            reply = f"❌ <b>{habit['name']}</b> — не зараховано. Завтра краще!"

        api("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": reply,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []}
        })
    except Exception as e:
        print(f"habit callback error: {e}")

    # Логуємо в Calendar — окремо, помилка не критична
    try:
        from datetime import datetime, timezone, timedelta
        now_l = datetime.now(timezone.utc) + timedelta(hours=2)
        mark = "✅" if answer == "yes" else "❌"
        log_to_calendar(f"{habit['emoji']} {habit['name']} {mark}", now_l.strftime("%Y-%m-%d"), habit["hour"], habit["minute"])
    except Exception as e:
        print(f"habit log_to_calendar error: {e}")

    return True


def _parse_apple_health_xml(zip_bytes):
    """Парсить Apple Health export.zip — повертає dict {date: {steps, sleep_hours, heart_rate, ...}}"""
    import zipfile, io, re as _re, xml.etree.ElementTree as ET
    from datetime import datetime, timezone, timedelta
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_name = next((n for n in zf.namelist() if n.endswith("export.xml")), None)
            if not xml_name:
                return None
            xml_bytes = zf.read(xml_name)

        # Парсимо XML потоково щоб не вантажити весь в пам'ять
        daily = {}

        def add(date, key, val):
            if date not in daily:
                daily[date] = {}
            if key not in daily[date]:
                daily[date][key] = []
            daily[date][key].append(val)

        context = ET.iterparse(io.BytesIO(xml_bytes), events=("end",))
        for event, elem in context:
            if elem.tag != "Record":
                elem.clear()
                continue
            rtype = elem.get("type", "")
            start = elem.get("startDate", "")[:10]
            val_str = elem.get("value", "")
            try:
                val = float(val_str)
            except:
                elem.clear()
                continue

            if "StepCount" in rtype:
                add(start, "steps", val)
            elif "HeartRate" in rtype and "Variability" not in rtype:
                add(start, "heart_rate", val)
            elif "ActiveEnergyBurned" in rtype:
                add(start, "calories_active", val)
            elif "BasalEnergyBurned" in rtype:
                add(start, "calories_basal", val)
            elif "DistanceWalkingRunning" in rtype:
                add(start, "distance_km", val)
            elif "SleepAnalysis" in rtype and elem.get("value","") == "HKCategoryValueSleepAnalysisAsleepUnspecified":
                # Розрахунок тривалості сну
                try:
                    s = datetime.fromisoformat(elem.get("startDate","").replace(" ", "T")[:19])
                    e = datetime.fromisoformat(elem.get("endDate","").replace(" ", "T")[:19])
                    hours = (e - s).total_seconds() / 3600
                    add(start, "sleep_hours", hours)
                except:
                    pass
            elif "HeartRateVariability" in rtype:
                add(start, "hrv", val)
            elif "FlightsClimbed" in rtype:
                add(start, "flights_climbed", val)
            elif "DietaryWater" in rtype:
                # Apple Health зберігає в літрах або мл — перевіряємо
                add(start, "water_ml", val * 1000 if val < 20 else val)

            elem.clear()

        # Агрегуємо
        result = {}
        for date, vals in daily.items():
            entry = {}
            if "steps" in vals:          entry["steps"]           = int(sum(vals["steps"]))
            if "heart_rate" in vals:     entry["heart_rate"]      = int(sum(vals["heart_rate"]) / len(vals["heart_rate"]))
            if "calories_active" in vals:entry["calories_active"] = int(sum(vals["calories_active"]))
            if "calories_basal" in vals: entry["calories"]        = int(sum(vals.get("calories_active",[0])) + sum(vals["calories_basal"]))
            if "distance_km" in vals:    entry["distance_km"]     = round(sum(vals["distance_km"]) / 1000, 2)  # метри -> км
            if "sleep_hours" in vals:    entry["sleep_hours"]     = round(sum(vals["sleep_hours"]), 1)
            if "hrv" in vals:            entry["hrv"]             = round(sum(vals["hrv"]) / len(vals["hrv"]), 1)
            if "flights_climbed" in vals:entry["flights_climbed"] = int(sum(vals["flights_climbed"]))
            if "water_ml" in vals:       entry["water_ml"]        = int(sum(vals["water_ml"]))
            if entry:
                result[date] = entry

        return result if result else None
    except Exception as e:
        print(f"_parse_apple_health_xml error: {e}")
        return None


def handle_health_zip(chat_id, doc):
    """Обробляє ZIP файл — Apple Health export або Health Auto Export."""
    try:
        send(chat_id, "⏳ Обробляю ZIP файл...")

        file_id = doc["file_id"]
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}"
        import urllib.request as _ur
        req = _ur.Request(url)
        with _ur.urlopen(req, timeout=15) as r:
            file_info = json.loads(r.read())

        file_path = file_info["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"

        req2 = _ur.Request(file_url)
        with _ur.urlopen(req2, timeout=60) as r:
            zip_bytes = r.read()

        import zipfile, io as _io
        # Визначаємо тип ZIP
        with zipfile.ZipFile(_io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()

        is_apple_health = any("export.xml" in n for n in names)
        is_hae = any(n.startswith("HealthAutoExport-") and n.endswith(".csv") for n in names)
        # StepsApp ZIP: містить CSV файли з крапкою з комою, без export.xml
        is_stepsapp = (
            not is_apple_health and not is_hae and
            any(n.endswith(".csv") for n in names) and
            not any("HealthAutoExport" in n for n in names)
        )

        if is_stepsapp:
            # StepsApp export
            send(chat_id, "👟 Знайдено StepsApp export — парсю...")
            try:
                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
                import steps as _steps
                result = _steps.parse_zip(zip_bytes)
                send(chat_id, result)
            except Exception as _se:
                send(chat_id, f"❌ Помилка парсингу StepsApp: {_se}")
            return

        if is_apple_health:
            # Apple Health export
            send(chat_id, "🍎 Знайдено Apple Health export.xml — парсю...")
            daily_data = _parse_apple_health_xml(zip_bytes)
            if not daily_data:
                send(chat_id, "❌ Не вдалось розпарсити XML.")
                return

            from storage import load_health, save_health
            health_db = load_health()
            new_days = 0
            for date, entry in daily_data.items():
                if entry:
                    existing = health_db.get(date, {})
                    existing.update(entry)
                    health_db[date] = existing
                    new_days += 1

            save_health(health_db)

            # Показуємо дані за сьогодні або вчора
            today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
            yesterday = (datetime.now(timezone.utc) + timedelta(hours=2) - timedelta(days=1)).strftime("%Y-%m-%d")
            show_date = today if today in daily_data else yesterday
            d = daily_data.get(show_date, {})

            lines = [
                f"✅ <b>Apple Health — оновлено {new_days} днів!</b>\n",
                f"📅 Останні дані ({show_date[5:].replace('-', '.')}):",
            ]
            if d.get("steps"):        lines.append(f"  👟 Кроки: <b>{d['steps']:,}</b>".replace(",", " "))
            if d.get("distance_km"):  lines.append(f"  📏 Дистанція: <b>{d['distance_km']} км</b>")
            if d.get("heart_rate"):   lines.append(f"  ❤️ Пульс: <b>{d['heart_rate']} уд/хв</b>")
            if d.get("calories_active"): lines.append(f"  🔥 Калорії: <b>{d['calories_active']} ккал</b>")
            if d.get("sleep_hours"):  lines.append(f"  😴 Сон: <b>{d['sleep_hours']} год</b>")
            if d.get("hrv"):          lines.append(f"  💓 HRV: <b>{d['hrv']}</b>")
            send(chat_id, "\n".join(lines))
            try:
                from health_report import generate_health_trend_chart
                _chart = generate_health_trend_chart(14)
                if _chart:
                    send_photo(chat_id, _chart, caption="📊 Тренди здоров'я — 14 днів")
            except Exception as _ce:
                print(f"[health chart] {_ce}", flush=True)

        elif is_hae:
            # Health Auto Export
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from health_webhook import analyze_hae_zip, format_hae_report

            stats = analyze_hae_zip(zip_bytes)
            if not stats:
                send(chat_id, "❌ Не вдалось розпарсити HAE ZIP.")
                return

            from storage import load_health, save_health
            health_db = load_health()
            last_date = stats.get("period_end") or stats.get("period_start")
            if last_date:
                entry = health_db.get(last_date, {})
                if stats.get("avg_steps"):   entry["steps"]       = int(stats["avg_steps"])
                if stats.get("avg_sleep"):   entry["sleep_hours"] = round(stats["avg_sleep"], 1)
                if stats.get("avg_dist_km"): entry["distance_km"] = round(stats["avg_dist_km"], 1)
                if stats.get("hrv_avg"):     entry["hrv"]         = int(stats["hrv_avg"])
                health_db[last_date] = entry
                save_health(health_db)

            report = format_hae_report(stats) + "\n\n✅ <b>Дані збережено!</b>"
            send(chat_id, report)
            try:
                from health_report import generate_health_trend_chart
                _chart = generate_health_trend_chart(14)
                if _chart:
                    send_photo(chat_id, _chart, caption="📊 Тренди здоров'я — 14 днів")
            except Exception as _ce:
                print(f"[health chart] {_ce}", flush=True)
        else:
            send(chat_id, "❌ Невідомий формат ZIP.\n\nОчікується:\n• <b>Apple Health</b> export (export.zip з iPhone)\n• <b>Health Auto Export</b> app")

    except Exception as e:
        print(f"handle_health_zip error: {e}", flush=True)
        send(chat_id, f"❌ Помилка: {e}\n\nВведи вручну:\n<code>/зд [кроки] [сон] [ЧСС] [кал]</code>")


def handle_health_photo(chat_id, msg):
    """Обробляє фото з Apple Health скріну — OCR через Google Vision API."""
    caption = msg.get("caption", "").strip()
    today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")

    # Якщо caption містить числа — парсимо вручну (старий формат)
    parts = caption.split() if caption else []
    if len(parts) >= 4:
        try:
            from storage import load_health, save_health
            health = load_health()
            entry = health.get(today, {})
            entry["steps"]       = int(parts[0])
            entry["sleep_hours"] = float(parts[1])
            entry["heart_rate"]  = int(parts[2])
            entry["calories"]    = int(parts[3])
            if len(parts) >= 5:
                entry["health_score"] = int(parts[4])
            health[today] = entry
            save_health(health)
            reply = f"✅ <b>Health дані {today} збережено!</b>\n\n"
            reply += f"👟 Кроки: {entry.get('steps','—')}\n"
            reply += f"😴 Сон: {entry.get('sleep_hours','—')} год\n"
            reply += f"❤️ ЧСС: {entry.get('heart_rate','—')} bpm\n"
            if entry.get("health_score"):
                reply += f"💚 Health Score: {entry['health_score']}/100"
            send(chat_id, reply)
            try:
                from health_report import generate_health_trend_chart
                _chart = generate_health_trend_chart(14)
                if _chart:
                    send_photo(chat_id, _chart, caption="📊 Тренди здоров'я — 14 днів")
            except Exception as _ce:
                print(f"[health chart] {_ce}", flush=True)
            return
        except (ValueError, IndexError):
            pass

    # OCR через Google Vision
    send(chat_id, "🔍 Читаю скрін...")
    try:
        from health_ocr import parse_health_photo
        # Беремо найбільше фото
        photos = msg.get("photo", [])
        if not photos:
            send(chat_id, "⚠️ Фото не знайдено")
            return
        file_id = photos[-1]["file_id"]

        data, raw = parse_health_photo(file_id, TELEGRAM_TOKEN)

        if data and len(data) >= 2:
            from storage import load_health, save_health
            health = load_health()
            entry = health.get(today, {})
            entry.update(data)
            health[today] = entry
            save_health(health)

            reply = f"✅ <b>Health дані {today} зчитано автоматично!</b>\n\n"
            if entry.get("steps"):       reply += f"👟 Кроки: <b>{entry['steps']:,}</b>\n"
            if entry.get("sleep_hours"): reply += f"😴 Сон: <b>{entry['sleep_hours']}г</b>\n"
            if entry.get("heart_rate"):  reply += f"❤️ ЧСС: <b>{entry['heart_rate']} bpm</b>\n"
            if entry.get("calories"):    reply += f"🔥 Калорії: <b>{entry['calories']:,}</b>\n"
            if entry.get("hrv"):         reply += f"💓 HRV: <b>{entry['hrv']} ms</b>\n"
            if entry.get("stress_max"):  reply += f"😤 Стрес: <b>{entry.get('stress_min','?')}–{entry['stress_max']}</b>\n"
            if entry.get("health_score"):reply += f"💚 Health Score: <b>{entry['health_score']}/100</b>\n"

            missing = []
            for k, label in [("steps","кроки"),("sleep_hours","сон"),("heart_rate","ЧСС"),("health_score","score")]:
                if not entry.get(k):
                    missing.append(label)
            if missing:
                reply += f"\n<i>Не знайдено: {', '.join(missing)}</i>\n"
                reply += f"Доповни: <code>/зд [кроки] [сон] [ЧСС] [кал] [score]</code>"

            send(chat_id, reply)
            try:
                from health_report import generate_health_trend_chart
                _chart = generate_health_trend_chart(14)
                if _chart:
                    send_photo(chat_id, _chart, caption="📊 Тренди здоров'я — 14 днів")
            except Exception as _ce:
                print(f"[health chart] {_ce}", flush=True)
        else:
            # OCR не спрацював — просимо вручну
            send(chat_id, (
                f"📸 Фото отримано, але не вдалось прочитати дані автоматично.\n\n"
                f"Введи вручну:\n<code>/зд [кроки] [сон] [ЧСС] [кал] [score]</code>\n\n"
                f"Наприклад:\n<code>/зд 10476 7.5 85 2500 75</code>"
            ))
    except Exception as e:
        print(f"handle_health_photo error: {e}", flush=True)
        send(chat_id, (
            f"⚠️ OCR помилка. Введи вручну:\n"
            f"<code>/зд [кроки] [сон] [ЧСС] [кал] [score]</code>"
        ))


def get_updates(offset=0):
    result = api("getUpdates", {"offset": offset, "timeout": 30, "limit": 10,
                                "allowed_updates": ["message", "callback_query"]})
    return result.get("result", [])


_GH_DATA_BRANCH = "data"  # окрема гілка для lock/offset — не тригерить Railway

def _gh_read(url):
    """Читає файл з GitHub гілки data. Повертає (data_dict, sha) або ({}, None)."""
    import base64 as _b64
    try:
        read_url = url + f"?ref={_GH_DATA_BRANCH}"
        req = urllib.request.Request(read_url, headers={
            "Authorization": f"token {_GH_TOKEN}",
            "User-Agent": "morning-report-bot"
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
            content = json.loads(_b64.b64decode(d["content"]))
            return content, d["sha"]
    except Exception:
        return {}, None


def _gh_write(url, data, sha, message="update"):
    """Записує файл у GitHub гілку data. Повертає True при успіху, False при конфлікті."""
    import base64 as _b64
    try:
        body_dict = {
            "message": message,
            "content": _b64.b64encode(json.dumps(data).encode()).decode(),
            "branch": _GH_DATA_BRANCH,
        }
        if sha:
            body_dict["sha"] = sha
        body = json.dumps(body_dict).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"token {_GH_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "morning-report-bot"
        }, method="PUT")
        with urllib.request.urlopen(req, timeout=8) as r:
            r.read()
        return True
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            return False  # conflict — інший процес записав першим
        return False
    except Exception:
        return False


# ─── LEADER ELECTION ─────────────────────────────────────────────────────────

_is_leader = False


def _try_become_leader():
    """
    Намагається стати лідером (єдиним активним ботом).
    Записує bot_lock.json з нашим instance_id + timestamp.
    Якщо там вже є свіжий lock від іншого instance — повертає False.
    """
    global _is_leader
    lock, sha = _gh_read(_GH_LOCK_URL)
    now_ts = time.time()

    # Перевіряємо чи є живий лідер (не старший 30с)
    existing_id = lock.get("instance", "")
    existing_ts = lock.get("ts", 0)
    if existing_id and existing_id != _INSTANCE_ID and (now_ts - existing_ts) < 60:
        print(f"[Leader] Another leader alive: {existing_id} (age {int(now_ts-existing_ts)}s)", flush=True)
        _is_leader = False
        return False

    # Записуємо себе як лідера
    new_lock = {"instance": _INSTANCE_ID, "ts": now_ts}
    if sha:
        ok = _gh_write(_GH_LOCK_URL, new_lock, sha, f"lock {_INSTANCE_ID}")
    else:
        # Файл не існує — створюємо (sha=None не підходить для PUT, потрібен POST)
        ok = _gh_write(_GH_LOCK_URL, new_lock, "", f"lock {_INSTANCE_ID}")

    if ok:
        print(f"[Leader] I am leader: {_INSTANCE_ID}", flush=True)
        _is_leader = True
    else:
        print(f"[Leader] Failed to claim lock", flush=True)
        _is_leader = False
    return _is_leader


def _heartbeat_leader():
    """Оновлює lock кожні 45с. Якщо хтось перехопив — зупиняємо бота."""
    global _is_leader
    while True:
        time.sleep(45)
        try:
            lock, sha = _gh_read(_GH_LOCK_URL)
            if lock.get("instance") != _INSTANCE_ID:
                print(f"[Leader] Lock taken by {lock.get('instance')} — stepping down", flush=True)
                _is_leader = False
                return  # thread exits — main loop перевірить _is_leader
            # Оновлюємо timestamp (без git commit — просто API)
            _gh_write(_GH_LOCK_URL, {"instance": _INSTANCE_ID, "ts": time.time()}, sha, "hb")
        except Exception as e:
            print(f"[Leader] Heartbeat error: {e}", flush=True)


def load_offset():
    """Читає offset з GitHub, fallback до /tmp."""
    data, _ = _gh_read(_GH_OFF_URL)
    if data.get("offset"):
        return data["offset"]
    try:
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    except Exception:
        return 0


def save_offset(offset):
    """Зберігає offset локально (швидко) + в GitHub (async не потрібен)."""
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"offset": offset}, f)
    except Exception:
        pass
    # GitHub запис — окремий потік щоб не блокувати polling
    import threading
    def _bg():
        try:
            data, sha = _gh_read(_GH_OFF_URL)
            if sha:
                _gh_write(_GH_OFF_URL, {"offset": offset}, sha, f"offset {offset}")
        except Exception as e:
            print(f"save_offset GH error: {e}", flush=True)
    threading.Thread(target=_bg, daemon=True).start()


# ─── КОМАНДИ ──────────────────────────────────────────────────────────────────

HELP_TEXT = """
🤖 <b>Команди бота:</b>

<b>💬 AI Асистент</b>
Просто напиши будь-що — я відповім як тренер, дієтолог, фін. радник або просто друг.
/я — твій поточний статус (де ти, зміна, вага, звички)
/забути — очистити пам'ять розмови

<b>📊 Звіти</b>
/звіт — повний звіт зараз
/тиждень — тижневий підсумок
/ціни — ціни BTC/ETH/AVAX/ONDO
/погода — погода Košice
/календар — події на сьогодні
/листи — останні email
/астро — астрологічний прогноз
/dd — DeFi дайджест 24h (зміни TVL, DEX, yields, stables)

<b>💪 Здоров'я</b>
/звички — відмітити звички
/статус — статус звичок (7 днів)
/вага — динаміка ваги
/сон — аналіз сну
/зд — health дані (7 днів)
/зд т — тижневий health звіт
/зд м — місячний health звіт
/зд [кроки] [сон] [ЧСС] [кал] [score] — записати

<b>👟 Кроки (StepsApp)</b>
Надішли ZIP з StepsApp — збережу автоматично
/кроки — підсумок кроків сьогодні
/кроки тиждень — тижневий звіт з графіком
/кроки місяць — місячний звіт з графіком
/пробіжки — історія пробіжок

<b>⌚ QWatch Pro</b>
Надішли текст з QWatch Pro — збережу автоматично
/qwatch — дані за сьогодні
/qwatch тиждень — тижневий звіт
/qwatch місяць — місячний звіт

<b>💊 Ліки</b>
/ліки — Armolopid Plus за тиждень
/ліки місяць — за місяць
/ліки курс — весь курс (27.04–27.07)

/допомога — цей список
"""


def handle_command(chat_id, text):
    # Зберігаємо оригінальний текст для парсерів (QWatch тощо)
    original_text = text.strip()
    # Нормалізуємо апострофи (Telegram може надсилати різні варіанти)
    text = text.strip().lower()
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'").replace("`", "'")

    if text in ["/start", "start"]:
        send(chat_id, "👋 Привіт! Я твій асистент.\n" + HELP_TEXT)

    elif text.startswith("/маршрут") or text.startswith("маршрут"):
        dest = text.replace("/маршрут", "").replace("маршрут", "").strip()
        if not dest:
            send(chat_id, "Вкажи місто: /маршрут Прешов")
        else:
            try:
                from traffic import handle_route_command
                send(chat_id, handle_route_command(dest))
            except Exception as e:
                send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/допомога", "/help", "допомога"]:
        send(chat_id, HELP_TEXT)

    elif text in ["/resetlock", "resetlock"]:
        try:
            lock, sha = _gh_read(_GH_LOCK_URL)
            if sha:
                _gh_write(_GH_LOCK_URL, {"instance": "", "ts": 0}, sha, "manual reset")
            send(chat_id, f"🔓 Lock скинуто. Був: {lock.get('instance', 'empty')}\nБот перезапуститься сам.")
        except Exception as _rle:
            send(chat_id, f"⚠️ Помилка: {_rle}")

    elif text in ["/звички", "звички"]:
        from habits import HABITS, load_data, today_key
        from meds import load_meds, save_meds, now_local, MEDS_NAME, MEDS_START, MEDS_END
        hab_data = load_data()
        today = today_key()
        day_data = hab_data.get(today, {})

        all_habits = [{"id": "shower", "name": "Холодний душ", "emoji": "🚿"}] + HABITS
        meds_db = load_meds()
        meds_today = meds_db.get(today)
        meds_status = "✅" if meds_today is True else ("❌" if meds_today is False else "⬜️")

        from datetime import datetime, timezone, timedelta
        date_str = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%d.%m")

        lines = [f"📋 <b>Звички {date_str}</b>\n"]
        for h in all_habits:
            done = day_data.get(h["id"])
            s = "✅" if done is True else ("❌" if done is False else "⬜️")
            lines.append(f"{s} {h['emoji']} {h['name']}")
        lines.append(f"{meds_status} 💊 {MEDS_NAME}")
        lines.append("\n<i>Натисни щоб змінити:</i>")

        keyboard = []
        for h in all_habits:
            done = day_data.get(h["id"])
            yes_mark = "·" if done is True else ""
            no_mark = "·" if done is False else ""
            keyboard.append([
                {"text": f"✅{yes_mark} {h['emoji']} {h['name']}", "callback_data": f"habit_yes_{h['id']}"},
                {"text": f"❌{no_mark}", "callback_data": f"habit_no_{h['id']}"},
            ])
        yes_mark = "·" if meds_today is True else ""
        no_mark = "·" if meds_today is False else ""
        keyboard.append([
            {"text": f"✅{yes_mark} 💊 {MEDS_NAME}", "callback_data": "meds_yes_today"},
            {"text": f"❌{no_mark}", "callback_data": "meds_no_today"},
        ])

        send_with_keyboard(chat_id, "\n".join(lines), keyboard)

    elif text in ["/статус", "статус"]:
        from habits import HABITS, load_data, now_local
        from datetime import datetime, timezone, timedelta
        db  = load_data()
        now = now_local()
        # Останні 7 днів
        days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]

        all_habits = [{"id": "shower", "name": "Холодний душ", "emoji": "🚿"}] + HABITS
        parts = []
        for h in all_habits:
            taken   = sum(1 for d in days if db.get(d, {}).get(h["id"]) is True)
            missed  = sum(1 for d in days if db.get(d, {}).get(h["id"]) is False)
            no_data = 7 - taken - missed
            pct     = int(taken / 7 * 100)
            bar     = "🟩" * taken + "⬜️" * (7 - taken)
            if pct == 100:   rating = "🏆 Ідеально!"
            elif pct >= 85:  rating = "💪 Відмінно!"
            elif pct >= 57:  rating = "👍 Непогано"
            else:            rating = "⚠️ Намагайся не пропускати!"

            lines_h = [
                f"{h['emoji']} <b>{h['name']}</b>",
                f"{bar}  {pct}%",
                f"✅ Виконано:    <b>{taken}</b> дн.",
                f"❌ Пропущено:  <b>{missed}</b> дн.",
                f"○  Немає даних: <b>{no_data}</b> дн.",
                rating,
                "<b>По днях:</b>",
            ]
            for d in days:
                d_short = d[5:]
                v = db.get(d, {}).get(h["id"])
                icon = "✅" if v is True else ("❌" if v is False else "○")
                lines_h.append(f"  {d_short}  {icon}")
            parts.append("\n".join(lines_h))

        send(chat_id, f"📊 <b>Статус звичок (7 днів)</b>\n\n" + "\n\n─────────────\n\n".join(parts))

    elif text in ["/dd", "/defi", "defi дайджест", "defi digest", "дефі дайджест"]:
        send(chat_id, "⏳ Збираю DeFi дайджест...")
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from report_defi import digest_24h
            digest_24h(force=True)
        except Exception as e:
            send(chat_id, f"⚠️ Помилка дайджесту: {e}")

    elif text in ["/звіт", "звіт"]:
        send(chat_id, "⏳ Збираю звіт...")
        now = datetime.now(timezone.utc)
        local_time = (now + timedelta(hours=2)).strftime("%H:%M")
        local_date = (now + timedelta(hours=2)).strftime("%d.%m.%Y")
        sections = []
        for fn in [get_prices, get_weather, get_calendar, get_emails]:
            try:
                sections.append(fn())
            except Exception as e:
                print(f"Error in {fn.__name__}: {e}")
        # Плани/нотатки — окрема секція
        try:
            from planner import format_planner_for_report
            sections.append(format_planner_for_report())
        except Exception as e:
            print(f"Error in format_planner_for_report: {e}")
        report = f"🕐 <b>Звіт {local_time} · {local_date}</b>\n\n" + "\n\n".join(sections)
        send(chat_id, report)
        try:
            from monitor import generate_crypto_trend_chart
            _cchart = generate_crypto_trend_chart(30)
            if _cchart:
                send_photo(chat_id, _cchart, caption="📈 Тренд цін — 30 днів")
        except Exception as _cce:
            print(f"[crypto chart] {_cce}", flush=True)

    elif text in ["/ціни", "ціни"]:
        try:
            send(chat_id, get_prices())
            from monitor import generate_crypto_trend_chart
            _cchart = generate_crypto_trend_chart(30)
            if _cchart:
                send_photo(chat_id, _cchart, caption="📈 Тренд цін — 30 днів")
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/погода", "погода"]:
        try:
            send(chat_id, get_weather())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/календар", "календар"]:
        try:
            send(chat_id, get_calendar())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/листи", "листи"]:
        try:
            send(chat_id, get_emails())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/астро", "астро"]:
        try:
            send(chat_id, "⏳ Будую карту...")
            # 1. Надсилаємо зображення натальної карти + транзитів
            try:
                from astro_chart import generate_natal_chart
                import tempfile, os as _os
                chart_path = generate_natal_chart()
                if chart_path and _os.path.exists(chart_path):
                    with open(chart_path, 'rb') as f:
                        img_bytes = f.read()
                    import urllib.request as _ur
                    boundary = b"----boundary"
                    body = (
                        b"------boundary\r\n"
                        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n' +
                        str(chat_id).encode() + b"\r\n"
                        b"------boundary\r\n"
                        b'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
                        b"Content-Type: image/png\r\n\r\n" +
                        img_bytes + b"\r\n"
                        b"------boundary--\r\n"
                    )
                    req = _ur.Request(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                        data=body,
                        headers={"Content-Type": "multipart/form-data; boundary=----boundary"}
                    )
                    with _ur.urlopen(req, timeout=30) as r:
                        pass
                    try: _os.unlink(chart_path)
                    except: pass
            except Exception as ce:
                import traceback
                print(f"[astro] chart send error: {ce}")
                traceback.print_exc()
                send(chat_id, f"⚠️ Не вдалося згенерувати карту: {ce}")
            # 2. Текстовий звіт
            from astro import get_astro_report
            send(chat_id, get_astro_report())
        except Exception as e:
            send(chat_id, f"⚠️ Астро помилка: {e}")

    elif text in ["/тиждень", "тиждень", "/підсумок", "підсумок"]:
        send(chat_id, "⏳ Готую тижневий підсумок...")
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            from weekly_report import send_weekly_report
            send_weekly_report()
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/сон", "сон"]:
        try:
            from sleep import get_last_night_sleep, format_sleep_week_block
            last = get_last_night_sleep()
            week = format_sleep_week_block()
            msg = ""
            if last:
                msg += f"<b>Минула ніч:</b>\n{last}\n\n"
            msg += week
            send(chat_id, msg)
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/ліки", "ліки", "/armolopid"]:
        try:
            from meds import get_meds_report_full
            send(chat_id, get_meds_report_full("week"))
        except Exception as e:
            print(f"/ліки (full) error: {e}")
            try:
                send(chat_id, get_meds_report("week"))
            except Exception as e2:
                send(chat_id, f"⚠️ Помилка звіту ліків: {e2}")

    elif text in ["/ліки місяць", "ліки місяць"]:
        try:
            from meds import get_meds_report_full
            send(chat_id, get_meds_report_full("month"))
        except Exception as e:
            print(f"/ліки місяць (full) error: {e}")
            try:
                send(chat_id, get_meds_report("month"))
            except Exception as e2:
                send(chat_id, f"⚠️ Помилка звіту ліків: {e2}")

    elif text in ["/ліки курс", "ліки курс"]:
        try:
            from meds import get_meds_report_full
            send(chat_id, get_meds_report_full("course"))
        except Exception as e:
            print(f"/ліки курс (full) error: {e}")
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/вага", "вага"]:
        try:
            from weight import format_weekly_weight_report, make_weight_chart
            text_msg = format_weekly_weight_report()
            chart = make_weight_chart(30)
            if chart:
                send_photo(chat_id, chart, text_msg)
            else:
                send(chat_id, text_msg)
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    # ─── КРОКИ (StepsApp) ──────────────────────────────────────────────────────
    elif text in ["/кроки", "кроки"]:
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            import steps as _steps
            send(chat_id, _steps.get_steps_summary())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/кроки тиждень", "кроки тиждень", "/кт"]:
        send(chat_id, "⏳ Готую тижневий звіт кроків...")
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            import steps as _steps
            _steps.send_weekly_report()
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/кроки місяць", "кроки місяць", "/км"]:
        send(chat_id, "⏳ Готую місячний звіт кроків...")
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            import steps as _steps
            _steps.send_monthly_report()
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/пробіжки", "пробіжки"]:
        try:
            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            import steps as _steps
            _steps.send_run_history()
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif any(x in text for x in ["/здоров'я тиждень", "здоров'я тиждень", "/health week", "здоров'я тиждень"]) or text in ["/здоровя тиждень", "здоровя тиждень", "/зд т", "зд т", "/здт"]:
        send(chat_id, "⏳ Готую тижневий health звіт...")
        try:
            from health_report import get_health_week_report, generate_health_trend_chart
            report_text = get_health_week_report()
            chart = generate_health_trend_chart(7)
            if chart:
                send_photo(chat_id, chart, report_text)
            else:
                send(chat_id, report_text)
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif any(x in text for x in ["/здоров'я місяць", "здоров'я місяць", "/health month", "здоров'я місяць"]) or text in ["/здоровя місяць", "здоровя місяць", "/зд м", "зд м", "/здм"]:
        send(chat_id, "⏳ Готую місячний health звіт...")
        try:
            from health_report import get_health_month_report, generate_health_trend_chart
            report_text = get_health_month_report()
            chart = generate_health_trend_chart(30)
            if chart:
                send_photo(chat_id, chart, report_text)
            else:
                send(chat_id, report_text)
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/зд", "зд"]:
        # Швидкий перегляд останніх 7 днів
        try:
            from storage import load_health
            health = load_health()
            if health:
                sorted_days = sorted(health.keys(), reverse=True)[:7]
                reply = "💚 <b>Health (7 днів)</b>\n\n"
                for d in sorted_days:
                    h = health[d]
                    score = f" 💚{h['health_score']}" if h.get("health_score") else ""
                    steps = f"👟{h['steps']//1000}к" if h.get("steps") else ""
                    sleep = f"😴{h.get('sleep_hours','')}г" if h.get("sleep_hours") else ""
                    hr = f"❤️{h['heart_rate']}" if h.get("heart_rate") else ""
                    parts = [x for x in [steps, sleep, hr] if x]
                    reply += f"<b>{d[5:]}</b>  {' '.join(parts)}{score}\n"
                send(chat_id, reply)
            else:
                send(chat_id, "Немає health даних.\n\nДодай: /зд [кроки] [сон] [ЧСС] [калорії] [score]")
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text.startswith("/здоров'я") or text.startswith("/health") or text.startswith("/здоровя") or text.startswith("/зд"):
        # /здоров'я [кроки] [сон] [ЧСС] [калорії]
        try:
            parts = text.split()[1:]
            today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
            from storage import load_health, save_health
            health = load_health()
            entry = health.get(today, {})
            if len(parts) >= 4:
                entry["steps"]       = int(parts[0])
                entry["sleep_hours"] = float(parts[1])
                entry["heart_rate"]  = int(parts[2])
                entry["calories"]    = int(parts[3])
                if len(parts) >= 5:
                    entry["health_score"] = int(parts[4])
                health[today] = entry
                save_health(health)
                reply = f"✅ <b>Health дані {today} збережено!</b>\n\n"
                reply += f"👟 Кроки: {entry.get('steps','—')}\n"
                reply += f"😴 Сон: {entry.get('sleep_hours','—')} год\n"
                reply += f"❤️ ЧСС: {entry.get('heart_rate','—')} bpm\n"
                reply += f"🔥 Калорії: {entry.get('calories','—')}\n"
                if entry.get("health_score"):
                    reply += f"💚 Health Score: {entry['health_score']}/100"
                send(chat_id, reply)
                try:
                    from health_report import generate_health_trend_chart
                    _chart = generate_health_trend_chart(14)
                    if _chart:
                        send_photo(chat_id, _chart, caption="📊 Тренди здоров'я — 14 днів")
                except Exception as _ce:
                    print(f"[health chart] {_ce}", flush=True)
            else:
                # Показати поточні дані
                if health:
                    sorted_days = sorted(health.keys(), reverse=True)[:7]
                    reply = "💚 <b>Health дані (останні 7 днів)</b>\n\n"
                    for d in sorted_days:
                        h = health[d]
                        score = f" | Score: {h['health_score']}/100" if h.get("health_score") else ""
                        reply += f"<b>{d}</b>{score}\n"
                        reply += f"  👟 {h.get('steps','—')} | 😴 {h.get('sleep_hours','—')}г | ❤️ {h.get('heart_rate','—')} bpm\n"
                    send(chat_id, reply)
                else:
                    send(chat_id, "Немає health даних. Введи: /здоров'я [кроки] [сон] [ЧСС] [калорії]")
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}\nФормат: /здоров'я [кроки] [сон] [ЧСС] [калорії]")

    elif text in ["/qwatch", "qwatch", "/qs"]:
        try:
            from qwatch import report_weekly, _load
            from datetime import datetime, timezone, timedelta
            today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
            db = _load()
            if today in db:
                from qwatch import format_day_block
                send(chat_id, format_day_block(today) or "QWatch: немає даних за сьогодні.")
            else:
                send(chat_id, "⌚ Даних QWatch за сьогодні ще немає.\nНадішли текст з QWatch Pro.")
        except Exception as e:
            send(chat_id, f"⚠️ {e}")

    elif text in ["/qwatch тиждень", "qwatch тиждень", "/qст", "qст"]:
        send(chat_id, "⏳ Готую тижневий QWatch звіт...")
        try:
            from qwatch import report_weekly
            rtext, rchart = report_weekly()
            if rchart:
                send_photo(chat_id, rchart, rtext)
            else:
                send(chat_id, rtext)
        except Exception as e:
            send(chat_id, f"⚠️ {e}")

    elif text in ["/qwatch місяць", "qwatch місяць", "/qм", "qм"]:
        send(chat_id, "⏳ Готую місячний QWatch звіт...")
        try:
            from qwatch import report_monthly
            rtext, rchart = report_monthly()
            if rchart:
                send_photo(chat_id, rchart, rtext)
            else:
                send(chat_id, rtext)
        except Exception as e:
            send(chat_id, f"⚠️ {e}")

    elif text in ["/забути", "забути", "/clear", "/скинути"]:
        from context import clear_history
        clear_history()
        send(chat_id, "🧹 Пам'ять розмови очищена. Починаємо з чистого аркуша!")

    elif text in ["/статус_зараз", "/я", "я зараз", "де я"]:
        try:
            from context import get_context, STATUS_LABELS
            ctx = get_context(include_crypto=True)
            shift_labels = {"early": "рання (06:00–18:00)", "night": "нічна (18:00–06:00)", "free": "вихідний"}
            msg = (
                f"📍 <b>Твій статус зараз</b>\n\n"
                f"🕐 {ctx['time_str']}, {ctx['weekday']} {ctx['date_str']}\n"
                f"👤 {ctx['status_label']}\n"
                f"📅 Зміна сьогодні: <b>{shift_labels.get(ctx['shift_today'], ctx['shift_today'])}</b>\n"
                f"📅 Зміна завтра: <b>{shift_labels.get(ctx['shift_tomorrow'], ctx['shift_tomorrow'])}</b>\n\n"
                f"⚖️ {ctx['weight']}\n"
                f"💚 {ctx['health']}\n"
                f"📋 Звички: {ctx['habits']}\n"
                f"💊 {ctx['meds']}\n"
            )
            if ctx.get("crypto"):
                msg += f"\n💹 {ctx['crypto']}"
            send(chat_id, msg)
        except Exception as e:
            send(chat_id, f"⚠️ {e}")

    else:
        # Розпізнавання тексту QWatch Pro
        raw_text = original_text
        if ("health score" in raw_text.lower() or "оцінка здоров" in raw_text.lower()
                or ("hrv" in raw_text.lower() and ("сон" in raw_text.lower() or "кроки" in raw_text.lower() or "пульс" in raw_text.lower()))):
            try:
                api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
                from qwatch import parse_and_save, send_confirmation
                record = parse_and_save(raw_text)
                send_confirmation(record)
                return
            except Exception as e:
                send(chat_id, f"⚠️ QWatch помилка: {e}")
                return

        # Спроба розпізнати вагу (число типу 82 або 82.5)
        try:
            kg = float(text.replace(",", "."))
            if 30 < kg < 250:
                from weight import save_weight, get_trend
                save_weight(kg)
                trend = get_trend()
                reply = f"⚖️ <b>{kg} кг</b> — збережено!\n\nНе забудь записати в Apple Health 🍎"
                if trend:
                    reply += f"\n\n{trend}"
                send(chat_id, reply)
                return
        except ValueError:
            pass

        # Зберігаємо контекст з відповіді Олега (локація, активність, настрій)
        try:
            from proactive import update_user_state_from_message
            update_user_state_from_message(text)
        except Exception:
            pass

        # Будь-який текст → AI асистент (Calendar завжди підключений)
        try:
            api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
            from context import ask_ai
            answer = ask_ai(text, include_calendar=True)
            send(chat_id, answer)
        except Exception as e:
            send(chat_id, f"⚠️ AI помилка: {e}")


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    import threading as _threading
    print(f"=== Bot started [{_INSTANCE_ID}] ===", flush=True)

    # Скидаємо webhook (якщо був) і очищуємо pending updates
    try:
        api("deleteWebhook", {"drop_pending_updates": True})
        print("[Bot] Webhook deleted, pending updates dropped", flush=True)
    except Exception as e:
        print(f"[Bot] deleteWebhook error: {e}", flush=True)

    # Чекаємо стати лідером (до 180с)
    waited = 0
    while not _try_become_leader():
        waited += 5
        if waited >= 180:
            print(f"[Leader] Could not become leader in 180s — exiting", flush=True)
            return
        print(f"[Leader] Waiting... ({waited}s)", flush=True)
        time.sleep(5)

    # Запускаємо heartbeat в background
    _threading.Thread(target=_heartbeat_leader, daemon=True).start()

    # Print service account email for Google Sheets setup
    try:
        _creds = json.loads(os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "{}"))
        _email = _creds.get("client_email", "not found")
        print(f"=== SERVICE ACCOUNT EMAIL: {_email} ===", flush=True)
        _sheets_id = os.environ.get("GOOGLE_SHEETS_ID", "NOT SET")
        print(f"=== GOOGLE_SHEETS_ID: {_sheets_id} ===", flush=True)
    except Exception as _e:
        print(f"=== Could not read service account: {_e} ===", flush=True)

    offset = load_offset()
    print(f"[Bot] Starting polling from offset {offset}", flush=True)

    while True:
        # Якщо ми більше не лідер — зупиняємось
        if not _is_leader:
            print(f"[Bot] Not leader anymore [{_INSTANCE_ID}] — stopping", flush=True)
            return

        try:
            updates = get_updates(offset)
            for update in updates:
                uid = update["update_id"]
                offset = uid + 1
                save_offset(offset)

                # Перевіряємо лідерство перед кожною обробкою
                if not _is_leader:
                    print(f"[Bot] Lost leadership mid-loop — stopping", flush=True)
                    return

                # Обробка кнопок (callback_query)
                cb = update.get("callback_query")
                if cb:
                    if str(cb["message"]["chat"]["id"]) == str(TELEGRAM_CHAT):
                        data = cb.get("data", "")
                        if data.startswith("evdone_"):
                            handle_event_done_callback(cb)
                        elif data.startswith("sleep_q_"):
                            scores = {
                                "sleep_q_1": "😩 Погано",
                                "sleep_q_2": "😐 Нормально",
                                "sleep_q_3": "😊 Добре",
                                "sleep_q_4": "🌟 Відмінно"
                            }
                            label = scores.get(data, data)
                            api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": f"Записано: {label}"})
                            # Зберегти в health data
                            try:
                                from datetime import date
                                today_str = date.today().isoformat()
                                try:
                                    from storage import load_health, save_health
                                    health = load_health()
                                    if today_str not in health:
                                        health[today_str] = {}
                                    health[today_str]["sleep_quality"] = data.replace("sleep_q_", "")
                                    health[today_str]["sleep_quality_label"] = label
                                    save_health(health)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            send(chat_id, f"✅ Сон записано: <b>{label}</b>")
                        elif data.startswith("meds_"):
                            handle_meds_callback(cb)
                        elif data == "reminder_health_photo":
                            api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Надішли фото 📸"})
                            send(chat_id, "📸 Надішли скрін Apple Health — прочитаю автоматично!")
                        elif data == "reminder_health_view":
                            api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": ""})
                            try:
                                from storage import load_health
                                health = load_health()
                                if health:
                                    sorted_days = sorted(health.keys(), reverse=True)[:7]
                                    reply = "💚 <b>Health (7 днів)</b>\n\n"
                                    for d in sorted_days:
                                        h = health[d]
                                        score = f" 💚{h['health_score']}" if h.get("health_score") else ""
                                        steps = f"👟{h['steps']//1000}к" if h.get("steps") else ""
                                        sleep = f"😴{h.get('sleep_hours','')}г" if h.get("sleep_hours") else ""
                                        hr = f"❤️{h['heart_rate']}" if h.get("heart_rate") else ""
                                        parts = [x for x in [steps, sleep, hr] if x]
                                        reply += f"<b>{d[5:]}</b>  {' '.join(parts)}{score}\n"
                                    send(chat_id, reply)
                                else:
                                    send(chat_id, "Немає даних. Введи /зд [кроки] [сон] [ЧСС] [кал] [score]")
                            except Exception as e:
                                send(chat_id, f"⚠️ {e}")
                        elif data == "cal_all_done_today":
                            # Persistent reminder — підтвердження що всі події виконано
                            api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "✅ Чудово!"})
                            try:
                                api("editMessageReplyMarkup", {
                                    "chat_id": chat_id, "message_id": cb["message"]["message_id"],
                                    "reply_markup": {"inline_keyboard": []}
                                })
                            except: pass
                            send(chat_id, "✅ Відмічено — всі події сьогодні виконані!")
                            # Записуємо в persist state щоб більше не нагадувало сьогодні
                            try:
                                import sys as _sys, os as _os
                                _sys.path.insert(0, _os.path.dirname(__file__))
                                from storage import load as _st_load, save as _st_save
                                from datetime import datetime, timezone, timedelta
                                today_str = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
                                ps = _st_load("habits_persist_remind.json") or {}
                                import time as _time
                                # Виставляємо timestamp далеко в майбутнє — щоб не нагадувало сьогодні
                                ps[f"persist_calendar_{today_str}_ts"] = int(_time.time()) + 86400
                                _st_save("habits_persist_remind.json", ps)
                            except Exception as _e:
                                print(f"cal_all_done_today state error: {_e}")

                        elif data.startswith("cal_done_"):
                            # Видалити подію з Google Calendar після натискання "✅ Зроблено"
                            ev_id = data[len("cal_done_"):]
                            api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "🗑 Видаляю подію..."})
                            try:
                                import importlib.util, os as _os
                                spec = importlib.util.spec_from_file_location(
                                    "assistant", _os.path.join(_os.path.dirname(__file__), "assistant.py"))
                                mod = importlib.util.module_from_spec(spec)
                                spec.loader.exec_module(mod)
                                ok = mod.delete_calendar_event(ev_id)
                                api("editMessageReplyMarkup", {
                                    "chat_id": chat_id, "message_id": cb["message"]["message_id"],
                                    "reply_markup": {"inline_keyboard": []}
                                })
                                if ok.get("ok"):
                                    send(chat_id, "✅ Подію видалено з Google Calendar")
                                else:
                                    send(chat_id, f"⚠️ Не вдалось видалити: {ok.get('error', 'невідома помилка')}")
                            except Exception as e:
                                print(f"cal_done error: {e}")
                                send(chat_id, f"⚠️ Помилка: {e}")
                        elif data.startswith("planner_"):
                            api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": ""})
                            try:
                                from planner import (
                                    handle_planner_confirm, handle_planner_cancel,
                                    handle_planner_edit, clear_state, _send_force_reply,
                                    set_state, get_state,
                                    handle_planner_hour, handle_planner_minute,
                                    handle_planner_time_back
                                )
                                if data == "planner_confirm":
                                    handle_planner_confirm()
                                elif data == "planner_cancel":
                                    handle_planner_cancel()
                                elif data == "planner_edit":
                                    handle_planner_edit()
                                elif data == "planner_time_back":
                                    handle_planner_time_back()
                                elif data.startswith("planner_hour_"):
                                    hour_val = data[len("planner_hour_"):]
                                    handle_planner_hour(hour_val)
                                elif data.startswith("planner_min_"):
                                    # planner_min_09_30
                                    parts = data.split("_")  # ['planner','min','09','30']
                                    if len(parts) == 4:
                                        handle_planner_minute(parts[2], parts[3])
                                elif data == "planner_write":
                                    # Якщо немає активного стану — ставимо awaiting_tomorrow
                                    st = get_state()
                                    if not st.get("mode"):
                                        from datetime import datetime, timezone, timedelta
                                        now = datetime.now(timezone.utc) + timedelta(hours=2)
                                        set_state("awaiting_tomorrow", {"base_date": now.strftime("%Y-%m-%d")})
                                    _send_force_reply("✏️ <b>Напиши свої плани:</b>\n\n<i>Наприклад: спортзал о 8, лікар о 14, зателефонувати Максиму</i>")
                                elif data == "planner_write_today":
                                    # Кнопка "записати" в денному нагадуванні — ставимо стан today
                                    from datetime import datetime, timezone, timedelta
                                    now = datetime.now(timezone.utc) + timedelta(hours=2)
                                    set_state("awaiting_today", {"base_date": now.strftime("%Y-%m-%d"), "context": "today"})
                                    _send_force_reply("✏️ <b>Що занотуємо?</b>\n\n<i>Зустріч, ідея, завдання — будь що</i>")
                                elif data == "planner_skip":
                                    clear_state()
                                    send(chat_id, "👍 Добре, нічого не записую.")
                            except Exception as _ple:
                                print(f"planner callback error: {_ple}")
                        elif data == "delete_self":
                            api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Закрито"})
                            api("deleteMessage", {"chat_id": chat_id, "message_id": cb["message"]["message_id"]})
                        elif (data.startswith("email_describe_") or data.startswith("email_undescribe_") or
                              data.startswith("email_delete_") or
                              data.startswith("email_keep_") or data.startswith("email_star_") or
                              data.startswith("email_cal_") or data.startswith("email_reply_") or
                              data.startswith("email_send_") or data.startswith("email_cancel_") or
                              data.startswith("cal_add_") or data.startswith("cal_skip_")):
                            handle_email_callback(cb)
                        elif data.startswith("reminder_"):
                            handle_reminder_callback(cb)
                        elif data.startswith("mood_"):
                            # Обробка оцінки настрою 1-5
                            try:
                                score = int(data.split("_")[1])
                                api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Записано ✓"})
                                labels = {1: "😩 Важкий день", 2: "😕 Нижче норми", 3: "😐 Нормально", 4: "😊 Добре", 5: "🤩 Чудово"}
                                label = labels.get(score, str(score))
                                # Зберігаємо в monitor_mood.json
                                try:
                                    from datetime import date
                                    today_str = date.today().isoformat()
                                    import sys; sys.path.insert(0, os.path.dirname(__file__))
                                    from monitor import load_json_file, save_json_file, MOOD_FILE
                                    state = load_json_file(MOOD_FILE, default={})
                                    state[today_str] = score
                                    save_json_file(MOOD_FILE, state)
                                except Exception as _e:
                                    print(f"mood save error: {_e}")
                                # AI реакція
                                reactions = {
                                    1: "Важкий день буває у кожного. Завтра буде краще 💙",
                                    2: "Нічого — відпочинь, завтра нова сторінка 🌙",
                                    3: "Стабільно — і це вже добре 👌",
                                    4: "Гарний день! Так тримати 💪",
                                    5: "Відмінно! Ось це день 🔥"
                                }
                                send(chat_id,
                                    f"✨ <b>Настрій: {label}</b>\n\n"
                                    f"{reactions.get(score, '')}\n\n"
                                    f"<i>Записано для тижневого аналізу</i>"
                                )
                            except Exception as _e:
                                print(f"mood callback error: {_e}")
                        else:
                            handle_habit_callback(cb)
                    continue

                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                text = msg.get("text", "")

                # Тільки від авторизованого користувача
                if str(chat_id) != str(TELEGRAM_CHAT):
                    send(chat_id, "⛔ Немає доступу.")
                    continue

                # Обробка фото (скрін Health Score)
                if msg.get("photo"):
                    handle_health_photo(chat_id, msg)
                    continue

                # Обробка ZIP файлу (Health Auto Export)
                if msg.get("document"):
                    doc = msg["document"]
                    fname = doc.get("file_name", "")
                    if fname.endswith(".zip") or "export" in fname.lower():
                        handle_health_zip(chat_id, doc)
                        continue

                print(f"Message: {text}", flush=True)

                # Planner — обробляємо першим якщо бот очікує відповідь
                try:
                    from planner import handle_planner_reply
                    if handle_planner_reply(text):
                        continue
                except Exception as _pe:
                    print(f"planner reply error: {_pe}")

                handle_command(chat_id, text)

        except Exception as e:
            print(f"Loop error: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
