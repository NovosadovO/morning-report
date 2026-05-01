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
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]
OFFSET_FILE    = "/tmp/bot_offset.json"

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
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"API error {method}: {e}")
        return {}


def send(chat_id, text):
    api("sendMessage", {
        "chat_id": chat_id,
        "text": text[:4090],
        "parse_mode": "HTML"
    })


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

    # ПЕРШИМ — підтверджуємо callback
    api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "Записано ✓"})

    # meds_yes_2026-04-27 або meds_no_2026-04-27 або meds_yes_today
    parts  = data.split("_", 2)
    answer = parts[1]  # yes / no
    date_raw = parts[2] if len(parts) > 2 else ""
    if date_raw == "today":
        from datetime import datetime, timezone, timedelta
        date = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    else:
        date = date_raw

    try:
        try:
            import sys as _sys; _sys.path.insert(0, os.path.dirname(__file__))
            from storage import load_meds as _lm, save_meds as _sm
            meds_db = _lm()
            meds_db[date] = (answer == "yes")
            _sm(meds_db)
        except Exception as _se:
            print(f"meds save error: {_se}")
            meds_file = "/tmp/meds_data.json"
            try:
                with open(meds_file) as f:
                    meds_db = _json.load(f)
            except:
                meds_db = {}
            meds_db[date] = (answer == "yes")
            with open(meds_file, "w") as f:
                _json.dump(meds_db, f)

        if answer == "yes":
            reply = "💊 <b>ARMOLOPID PLUS</b>\n\n✅ <b>Прийнято!</b> Молодець 💪\nПродовжуй в тому ж дусі."
        else:
            reply = "💊 <b>ARMOLOPID PLUS</b>\n\n❌ <b>Не прийнято.</b>\nНе забудь прийняти при першій нагоді!"

        api("editMessageText", {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": reply,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": []}
        })
    except Exception as e:
        print(f"meds callback error: {e}")


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


def handle_health_zip(chat_id, doc):
    """Обробляє ZIP файл від Health Auto Export."""
    try:
        send(chat_id, "⏳ Обробляю ZIP файл Health Auto Export...")

        file_id = doc["file_id"]
        # Отримуємо URL файлу
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

        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from health_webhook import analyze_hae_zip, format_hae_report

        stats = analyze_hae_zip(zip_bytes)
        if not stats:
            send(chat_id, "❌ Не вдалось розпарсити ZIP. Переконайся що це файл від <b>Health Auto Export</b> app.")
            return

        # Зберігаємо останній день в health.json на GitHub
        try:
            from storage import load_health, save_health
            health_db = load_health()
            last_date = stats.get("period_end") or stats.get("period_start")
            if last_date:
                entry = health_db.get(last_date, {})
                if stats.get("avg_steps"):   entry["steps"]         = int(stats["avg_steps"])
                if stats.get("avg_sleep"):   entry["sleep_hours"]   = round(stats["avg_sleep"], 1)
                if stats.get("avg_dist_km"): entry["distance_km"]   = round(stats["avg_dist_km"], 1)
                if stats.get("hrv_avg"):     entry["hrv"]           = int(stats["hrv_avg"])
                if stats.get("vo2_max"):     entry["vo2_max"]       = stats["vo2_max"]
                health_db[last_date] = entry
                save_health(health_db)
            saved = bool(last_date)
        except Exception as e:
            print(f"save health error: {e}")
            saved = False

        report = format_hae_report(stats)
        if saved:
            report += "\n\n✅ <b>Дані збережено в базу!</b>"
        send(chat_id, report)

    except Exception as e:
        print(f"handle_health_zip error: {e}", flush=True)
        send(chat_id, f"❌ Помилка обробки ZIP: {e}\n\nСпробуй ввести вручну:\n<code>/зд [кроки] [сон] [ЧСС] [кал] [score]</code>")


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


def load_offset():
    try:
        with open(OFFSET_FILE) as f:
            return json.load(f).get("offset", 0)
    except Exception:
        return 0


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": offset}, f)


# ─── КОМАНДИ ──────────────────────────────────────────────────────────────────

HELP_TEXT = """
🤖 <b>Команди бота:</b>

/звички — відмітити звички вручну
/статус — детальний звіт по кожній звичці (7 днів)
/звіт — повний звіт зараз
/тиждень — тижневий підсумок
/сон — аналіз сну
/ціни — ціни BTC/ETH/AVAX/ONDO
/погода — погода Košice
/календар — події на сьогодні
/листи — останні email
/вага — динаміка ваги
/ліки — таблетки за тиждень
/ліки місяць — за місяць
/ліки курс — весь курс (27.04–27.07)
/зд — health дані (останні 7 днів)
/зд т — тижневий health звіт
/зд м — місячний health звіт
/зд [кроки] [сон] [ЧСС] [кал] [score] — записати дані
/допомога — цей список
"""


def handle_command(chat_id, text):
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
        report = f"🕐 <b>Звіт {local_time} · {local_date}</b>\n\n" + "\n\n".join(sections)
        send(chat_id, report)

    elif text in ["/ціни", "ціни"]:
        try:
            send(chat_id, get_prices())
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
            send(chat_id, get_meds_report("week"))

    elif text in ["/ліки місяць", "ліки місяць"]:
        try:
            from meds import get_meds_report_full
            send(chat_id, get_meds_report_full("month"))
        except Exception as e:
            send(chat_id, get_meds_report("month"))

    elif text in ["/ліки курс", "ліки курс"]:
        try:
            from meds import get_meds_report_full
            send(chat_id, get_meds_report_full("course"))
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif text in ["/вага", "вага"]:
        try:
            from weight import format_weekly_weight_report
            send(chat_id, format_weekly_weight_report())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif any(x in text for x in ["/здоров'я тиждень", "здоров'я тиждень", "/health week", "здоров'я тиждень"]) or text in ["/здоровя тиждень", "здоровя тиждень", "/зд т", "зд т", "/здт"]:
        send(chat_id, "⏳ Готую тижневий health звіт...")
        try:
            from health_report import get_health_week_report
            send(chat_id, get_health_week_report())
        except Exception as e:
            send(chat_id, f"⚠️ Помилка: {e}")

    elif any(x in text for x in ["/здоров'я місяць", "здоров'я місяць", "/health month", "здоров'я місяць"]) or text in ["/здоровя місяць", "здоровя місяць", "/зд м", "зд м", "/здм"]:
        send(chat_id, "⏳ Готую місячний health звіт...")
        try:
            from health_report import get_health_month_report
            send(chat_id, get_health_month_report())
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

    else:
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
        send(chat_id, f"Не розумію команду. Напиши /допомога щоб побачити список команд.")


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    print("=== Bot started, listening for messages ===", flush=True)
    # Print service account email for Google Sheets setup
    try:
        import json as _json
        _creds = _json.loads(os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "{}"))
        _email = _creds.get("client_email", "not found")
        print(f"=== SERVICE ACCOUNT EMAIL: {_email} ===", flush=True)
        _sheets_id = os.environ.get("GOOGLE_SHEETS_ID", "NOT SET")
        print(f"=== GOOGLE_SHEETS_ID: {_sheets_id} ===", flush=True)
    except Exception as _e:
        print(f"=== Could not read service account: {_e} ===", flush=True)
    offset = load_offset()

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                save_offset(offset)

                # Обробка кнопок (callback_query)
                cb = update.get("callback_query")
                if cb:
                    if str(cb["message"]["chat"]["id"]) == str(TELEGRAM_CHAT):
                        data = cb.get("data", "")
                        if data.startswith("evdone_"):
                            handle_event_done_callback(cb)
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
                        elif data.startswith("reminder_"):
                            handle_reminder_callback(cb)
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
                handle_command(chat_id, text)

        except Exception as e:
            print(f"Loop error: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
