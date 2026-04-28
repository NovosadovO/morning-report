#!/usr/bin/env python3
"""
Повний трекер Armolopid Plus.

Старт:  2026-04-27
Кінець: 2026-07-27 (рівно 3 місяці)

Розклад нагадувань:
  - Рання зміна (є в Calendar) → 12:30–13:30
  - Вихідний / нічна / немає змін → 09:00–12:00

Ключові дати:
  - 2026-07-13 (за 2 тижні) → нагадування: здай аналізи ДО відміни
  - 2026-07-20 (за 1 тиждень) → повторне нагадування аналізи
  - 2026-07-27 → СТОП таблетки
  - 2026-08-03 (через тиждень після) → нагадування здати аналізи ПІСЛЯ
  - 2026-08-10 (через 2 тижні після) → повторне нагадування
"""

import os, json, re, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT_ID"]

_DIR       = os.path.dirname(os.path.abspath(__file__))
MEDS_FILE_REPO = os.path.join(_DIR, "meds_data.json")
MEDS_FILE      = os.path.join("/tmp", "meds_data.json")
MEDS_SENT  = os.path.join("/tmp", "meds_sent.json")

MEDS_START = "2026-04-27"
MEDS_END   = "2026-07-27"
MEDS_NAME  = "Armolopid Plus"

# Ключові дати для нагадувань аналізів
ANALYSIS_ALERTS = {
    "2026-07-13": ("before_2w", "📋 <b>Аналізи ДО відміни</b>\nЗалишилось 2 тижні до відміни <b>Armolopid Plus</b> (27 липня).\n\n🩸 Здай аналізи <b>цього тижня</b>:\n• Загальний аналіз крові\n• Біохімія (печінка, нирки)\n• Ліпідний профіль\n\nЦе потрібно щоб порівняти з результатами після курсу."),
    "2026-07-20": ("before_1w", "📋 <b>Нагадування: аналізи!</b>\nЗалишився <b>1 тиждень</b> до відміни Armolopid Plus.\n\n⚠️ Якщо ще не здав аналізи — час зробити це ЗАРАЗ.\nПісля відміни результати будуть відрізнятись."),
    "2026-07-27": ("stop_day",  "🛑 <b>СЬОГОДНІ ОСТАННІЙ ДЕНЬ</b>\n\n💊 <b>Armolopid Plus — курс завершено!</b>\nПочаток: 27 квітня 2026\nКінець: 27 липня 2026\n\nПривітаю з завершенням 3-місячного курсу! 💪\n\n📋 Не забудь здати аналізи протягом 1–2 тижнів після відміни для контролю результату."),
    "2026-08-03": ("after_1w",  "📋 <b>Аналізи ПІСЛЯ відміни</b>\nМинув 1 тиждень після відміни Armolopid Plus.\n\n🩸 Час здати аналізи:\n• Загальний аналіз крові\n• Ліпідний профіль\n• Біохімія\n\nПорівняй з результатами ДО — побачиш ефект курсу! 🔬"),
    "2026-08-10": ("after_2w",  "📋 <b>Повторне нагадування: аналізи</b>\nВже 2 тижні після відміни Armolopid Plus.\n\n⚠️ Якщо ще не здав аналізи — зроби це на цьому тижні.\nРезультати будуть максимально показові зараз."),
}

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def api(method, data=None):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    payload = json.dumps(data or {}).encode()
    req     = urllib.request.Request(url, data=payload,
              headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"API error {method}: {e}")
        return {}

def now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)

def today_str():
    return now_local().strftime("%Y-%m-%d")

def load_meds():
    try:
        import sys as _sys; _sys.path.insert(0, _DIR)
        from storage import load_meds as _lm
        return _lm()
    except Exception as e:
        print(f"load_meds error: {e}")
        try:
            with open(MEDS_FILE) as f:
                return json.load(f)
        except:
            return {}

def save_meds(db):
    try:
        import sys as _sys; _sys.path.insert(0, _DIR)
        from storage import save_meds as _sm
        _sm(db)
    except Exception as e:
        print(f"save_meds error: {e}")
        with open(MEDS_FILE, "w") as f:
            json.dump(db, f)

def load_sent():
    try:
        with open(MEDS_SENT) as f:
            return json.load(f)
    except:
        return {}

def save_sent(s):
    with open(MEDS_SENT, "w") as f:
        json.dump(s, f)

def days_into_course():
    start = datetime.strptime(MEDS_START, "%Y-%m-%d")
    return (now_local().replace(tzinfo=None) - start).days + 1

def days_remaining():
    end = datetime.strptime(MEDS_END, "%Y-%m-%d")
    return (end - now_local().replace(tzinfo=None)).days

# ─── CALENDAR: перевірити тип зміни ───────────────────────────────────────────

def _get_today_shift_type():
    """
    Повертає:
      'early'   — є рання зміна сьогодні
      'night'   — нічна зміна
      'weekend' — вихідний або нема змін
    """
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return "weekend"

    try:
        import json as _json
        creds_data = _json.loads(creds_json)

        # Беремо JWT token — копіюємо логіку з monitor.py
        import sys
        sys.path.insert(0, _DIR)
        from monitor import _get_google_token

        token   = _get_google_token(creds_data, "https://www.googleapis.com/auth/calendar.readonly")
        headers = {"Authorization": f"Bearer {token}"}
        cal_id  = "novosadovoleg%40gmail.com"

        now_utc    = datetime.now(timezone.utc)
        day_start  = now_utc.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(hours=2)
        day_end    = day_start + timedelta(hours=24)

        url = (
            f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events"
            f"?timeMin={urllib.parse.quote(day_start.isoformat())}"
            f"&timeMax={urllib.parse.quote(day_end.isoformat())}"
            f"&singleEvents=true&orderBy=startTime&maxResults=20"
        )
        try:
            import requests as _req
            r = _req.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            events = r.json().get("items", [])
        except ImportError:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as r:
                events = json.loads(r.read()).get("items", [])

        for ev in events:
            summary = ev.get("summary", "").lower()
            if any(x in summary for x in ["рання", "ранн", "early"]):
                return "early"
            if any(x in summary for x in ["нічна", "нічн", "night"]):
                return "night"

        return "weekend"

    except Exception as e:
        print(f"meds calendar error: {e}")
        return "weekend"

# ─── НАГАДУВАННЯ ТАБЛЕТКИ ─────────────────────────────────────────────────────

def _progress_bar(day_n, total=92, width=10):
    """Прогрес-бар емодзі — нормально рендериться в Telegram."""
    filled = round(day_n / total * width)
    filled = max(0, min(width, filled))
    return "🟩" * filled + "⬜️" * (width - filled)

def _motivational_note(day_n, remaining):
    """Маленька мотивашка залежно від прогресу."""
    if day_n == 1:
        return "🚀 Перший день — відмінний початок!"
    elif day_n == 7:
        return "🎉 Тиждень позаду — так тримати!"
    elif day_n == 30:
        return "💪 Місяць пройдено — ти молодець!"
    elif day_n == 60:
        return "🔥 Два місяці! Залишився останній!"
    elif remaining <= 14:
        return f"🏁 Фінішна пряма — {remaining} дн. до кінця!"
    elif remaining <= 7:
        return f"⚡️ Майже фінiш — ще {remaining} дн.!"
    elif day_n % 10 == 0:
        return f"✨ {day_n} днів — ти крутий!"
    else:
        return "💊 Регулярний прийом — запорука результату"

def _send_meds_question(today):
    day_n     = days_into_course()
    remaining = days_remaining()

    bar = _progress_bar(day_n)
    pct = min(round(day_n / 92 * 100), 100)
    note = _motivational_note(day_n, remaining)

    if remaining > 0:
        progress_line = f"День <b>{day_n}</b> / 92  ·  ще <b>{remaining}</b> дн."
    else:
        progress_line = "🔚 Сьогодні <b>останній день</b> курсу!"

    text = (
        f"💊 <b>ARMOLOPID PLUS</b>\n\n"
        f"{bar}  {pct}%\n"
        f"{progress_line}\n\n"
        f"{note}\n\n"
        f"<i>Прийняв таблетку сьогодні?</i>"
    )

    api("sendMessage", {
        "chat_id": TELEGRAM_CHAT,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [[
                {"text": "✅ Прийняв", "callback_data": f"meds_yes_{today}"},
                {"text": "❌ Не прийняв", "callback_data": f"meds_no_{today}"},
            ]]
        }
    })
    print(f"Meds reminder sent for {today}")

def check_meds_reminder():
    """
    Головна функція — викликається кожні 30 сек з habits.py.
    Визначає правильний час нагадування залежно від типу дня.
    """
    now   = now_local()
    today = today_str()

    # Тільки в межах курсу
    if today < MEDS_START or today > MEDS_END:
        _check_analysis_alerts(today)
        return

    sent  = load_sent()
    meds  = load_meds()

    # Вже відмітив сьогодні
    if meds.get(today) is True:
        return

    remind_key = f"meds_remind_{today}"
    if sent.get(remind_key):
        return

    # Визначаємо вікно залежно від типу дня
    shift = _get_today_shift_type()
    h, m  = now.hour, now.minute
    cur_min = h * 60 + m

    if shift == "early":
        # 12:30 – 13:30 → надсилаємо рівно о 12:30
        window_start = 12 * 60 + 30
        window_end   = 13 * 60 + 30
    else:
        # Вихідний / нічна / немає змін → 09:00 – 12:00
        window_start = 9 * 60
        window_end   = 12 * 60

    if window_start <= cur_min < window_end:
        _send_meds_question(today)
        sent[remind_key] = True
        save_sent(sent)

    # Повторне нагадування якщо не відмітив через 2г після першого вікна
    remind2_key = f"meds_remind2_{today}"
    if not sent.get(remind2_key) and cur_min >= window_end and not meds.get(today):
        # Надсилаємо повторне через 2г після закриття вікна
        repeat_start = window_end + 120
        if cur_min >= repeat_start:
            day_n2 = days_into_course()
            bar2   = _progress_bar(day_n2)
            pct2   = min(round(day_n2 / 92 * 100), 100)
            api("sendMessage", {
                "chat_id": TELEGRAM_CHAT,
                "text": (
                    f"⏰ <b>Ще не відмітив ліки!</b>\n\n"
                    f"{bar2}  {pct2}%\n"
                    f"День {day_n2} / 92\n\n"
                    f"💊 <b>{MEDS_NAME}</b> — прийняв сьогодні?"
                ),
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "✅ Прийняв", "callback_data": f"meds_yes_{today}"},
                        {"text": "❌ Не прийняв", "callback_data": f"meds_no_{today}"},
                    ]]
                }
            })
            sent[remind2_key] = True
            save_sent(sent)
            print(f"Meds repeat reminder sent for {today}")

    # Аналізи / ключові дати
    _check_analysis_alerts(today)

def _check_analysis_alerts(today):
    """Надсилає нагадування про аналізи у ключові дати."""
    if today not in ANALYSIS_ALERTS:
        return

    key_id, msg = ANALYSIS_ALERTS[today]
    sent = load_sent()
    alert_key = f"meds_alert_{key_id}"

    if sent.get(alert_key):
        return

    api("sendMessage", {
        "chat_id": TELEGRAM_CHAT,
        "text": msg,
        "parse_mode": "HTML"
    })
    sent[alert_key] = True
    save_sent(sent)
    print(f"Analysis alert sent: {key_id}")

# ─── ЗВІТИ ────────────────────────────────────────────────────────────────────

def get_meds_report_full(period="week"):
    """
    Повний звіт ліків: тиждень або місяць.
    Включає прогрес курсу.
    """
    db  = load_meds()
    now = now_local()

    if period == "week":
        days  = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
        title = "тиждень"
    elif period == "month":
        days  = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(now.day - 1, -1, -1)]
        title = now.strftime("%B %Y")
    else:  # course — весь курс
        start = datetime.strptime(MEDS_START, "%Y-%m-%d")
        n_days = (now.replace(tzinfo=None) - start).days + 1
        days  = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days - 1, -1, -1)]
        days  = [d for d in days if MEDS_START <= d <= MEDS_END]
        title = "весь курс"

    taken   = sum(1 for d in days if db.get(d) is True)
    missed  = sum(1 for d in days if db.get(d) is False)
    no_data = len(days) - taken - missed
    pct     = int(taken / len(days) * 100) if days else 0

    # Прогрес курсу
    day_n     = days_into_course()
    remaining = days_remaining()
    course_pct = min(round(day_n / 92 * 100), 100)
    course_bar = _progress_bar(day_n)

    # Звіт бар (по взятих/всього)
    report_filled = round(pct / 10)
    report_bar = "🟩" * report_filled + "⬜️" * (10 - report_filled)

    lines = [
        f"💊 <b>Armolopid Plus — {title}</b>\n",
        f"📅 Курс: день <b>{day_n}</b> / 92",
        f"{course_bar}  {course_pct}%",
        f"⏳ Залишилось: <b>{max(remaining, 0)}</b> дн.  (кінець: 27.07.2026)\n",
        f"✅ Прийнято:    <b>{taken}</b> дн.",
        f"❌ Пропущено:  <b>{missed}</b> дн.",
        f"○  Немає даних: <b>{no_data}</b> дн.",
        f"\n{report_bar}  <b>{pct}%</b>",
    ]

    if pct == 100:   lines.append("🏆 Ідеально!")
    elif pct >= 85:  lines.append("💪 Відмінно!")
    elif pct >= 70:  lines.append("👍 Непогано")
    else:            lines.append("⚠️ Намагайся не пропускати!")

    # Деталі по днях (тільки для тижня)
    if period == "week":
        lines.append("\n<b>По днях:</b>")
        for d in days:
            d_short = d[5:]
            v = db.get(d)
            if v is True:
                lines.append(f"  {d_short}  ✅")
            elif v is False:
                lines.append(f"  {d_short}  ❌")
            else:
                lines.append(f"  {d_short}  ○")

    return "\n".join(lines)

def format_meds_weekly_block():
    """Блок для недільного тижневого підсумку."""
    return get_meds_report_full("week")

# ─── ТЕСТ ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    os.environ.setdefault("TELEGRAM_TOKEN", "test")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "test")
    print(get_meds_report_full("week"))
    print()
    print(get_meds_report_full("month"))
    print()
    print(f"Shift type: {_get_today_shift_type()}")
