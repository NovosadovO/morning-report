#!/usr/bin/env python3
"""
Головний процес. Запускає:
- bot.py               — Telegram bot polling
- monitor.py           — основний звіт кожні 3г
- report2.py           — дайджест кожні 3г (зсув 1.5г)
- report_defi.py       — DeFi & RWA звіт о 07:00 і 19:00
- report_social.py     — соц пост (крипто/акції/DeFi) раз на 3 дні о 10:00
- check_new_emails()   — миттєві сповіщення про листи кожні 5хв
- check_weather_alert()— погодні алерти кожні 30хв
- check_crypto_news()  — крипто новини кожні 4г
- check_calendar_reminders() — нагадування за 30хв до подій кожні 5хв
"""

import time
import subprocess
import sys
import threading
from datetime import datetime, timezone, timedelta


def run_bot():
    print("=== Starting bot listener ===", flush=True)
    while True:
        try:
            subprocess.run([sys.executable, "bot.py"])
        except Exception as e:
            print(f"Bot crashed: {e}, restarting in 10s...", flush=True)
            time.sleep(10)


def _load_monitor():
    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "monitor", os.path.join(os.path.dirname(__file__), "monitor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_email_watcher():
    """Нові листи — кожні 5 хвилин."""
    print("=== Starting email watcher (every 5min) ===", flush=True)
    time.sleep(30)
    while True:
        try:
            _load_monitor().check_new_emails()
        except Exception as e:
            print(f"Email watcher error: {e}", flush=True)
        time.sleep(300)


def run_weather_watcher():
    """Погодні алерти — кожні 30 хвилин."""
    print("=== Starting weather watcher (every 30min) ===", flush=True)
    time.sleep(60)
    while True:
        try:
            _load_monitor().check_weather_alert()
        except Exception as e:
            print(f"Weather watcher error: {e}", flush=True)
        time.sleep(1800)


def run_news_watcher():
    """Крипто новини — щодня о 19:30."""
    print("=== Starting crypto news watcher (daily 19:30) ===", flush=True)
    time.sleep(90)
    while True:
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        h, m = now_local.hour, now_local.minute
        if h == 19 and 30 <= m < 35:
            print(f"[Crypto news] Running at {now_local.strftime('%H:%M')}...", flush=True)
            try:
                _load_monitor().check_crypto_news()
            except Exception as e:
                print(f"News watcher error: {e}", flush=True)
            time.sleep(360)  # щоб не запустити двічі
        else:
            time.sleep(60)


def run_report2_loop():
    """Дайджест (новини світу, трафік, курси, AQI) — кожні 3г зі зсувом 1.5г."""
    print("=== Starting report2 loop (every 3h, offset 1.5h) ===", flush=True)
    time.sleep(5400)  # зсув 1.5г
    while True:
        now = datetime.now(timezone.utc)
        print(f"\n[{now.strftime('%Y-%m-%d %H:%M')} UTC] Running report2...", flush=True)
        try:
            subprocess.run([sys.executable, "report2.py"], timeout=120)
        except Exception as e:
            print(f"Report2 error: {e}", flush=True)
        time.sleep(10800)


def run_defi_report_loop():
    """DeFi & RWA звіт — о 07:00 і 19:00 місцевого часу (UTC+2)."""
    print("=== Starting DeFi report loop (07:00 + 19:00) ===", flush=True)
    while True:
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        h, m = now_local.hour, now_local.minute
        if h in (10, 19) and m < 3:
            print(f"[DeFi report] Running at {now_local.strftime('%H:%M')}...", flush=True)
            try:
                subprocess.run([sys.executable, "report_defi.py"], timeout=300)
            except Exception as e:
                print(f"DeFi report error: {e}", flush=True)
            time.sleep(300)  # щоб не запустити двічі у те саме вікно
        else:
            time.sleep(60)


def run_monitor_loop():
    """Основний звіт — кожні 3 години."""
    print("=== Starting monitor loop (every 3h) ===", flush=True)
    while True:
        now = datetime.now(timezone.utc)
        print(f"\n[{now.strftime('%Y-%m-%d %H:%M')} UTC] Running monitor...", flush=True)
        try:
            subprocess.run([sys.executable, "monitor.py"], timeout=120)
        except Exception as e:
            print(f"Monitor error: {e}", flush=True)
        print("Sleeping 3 hours...", flush=True)
        time.sleep(10800)


# ─── ЗАПУСК ───────────────────────────────────────────────────────────────────

def run_calendar_reminder_watcher():
    """Нагадування за 30хв до подій — кожні 5 хвилин."""
    print("=== Starting calendar reminder watcher (every 5min) ===", flush=True)
    time.sleep(45)
    while True:
        try:
            _load_monitor().check_calendar_reminders()
        except Exception as e:
            print(f"Calendar reminder watcher error: {e}", flush=True)
        time.sleep(300)


def run_social_post_loop():
    """Соціальний пост (крипто/акції/DeFi) — раз на 3 дні о 10:00 місцевого часу."""
    print("=== Starting social post loop (every 3 days at 10:00) ===", flush=True)
    time.sleep(120)  # затримка старту
    while True:
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        h, m = now_local.hour, now_local.minute
        if h == 10 and m < 5:
            print(f"[Social post] Checking at {now_local.strftime('%H:%M')}...", flush=True)
            try:
                subprocess.run([sys.executable, "report_social.py"], timeout=120)
            except Exception as e:
                print(f"Social post error: {e}", flush=True)
            time.sleep(360)  # щоб не запустити двічі у те саме вікно
        else:
            time.sleep(60)


def run_traffic_watcher():
    """Трафік — перевіряє події в календарі кожні 5 хв."""
    print("=== Starting traffic watcher (every 5min) ===", flush=True)
    time.sleep(60)
    while True:
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(__file__))
            from traffic import check_calendar_traffic
            check_calendar_traffic()
        except Exception as e:
            print(f"Traffic watcher error: {e}", flush=True)
        time.sleep(300)


def run_habits_loop():
    """Трекер звичок — щоденні питання + тижневий/місячний звіт."""
    print("=== Starting habits tracker ===", flush=True)
    import subprocess
    while True:
        try:
            subprocess.run([sys.executable, "habits.py"])
        except Exception as e:
            print(f"Habits crashed: {e}, restarting in 10s...", flush=True)
            time.sleep(10)


def run_shift_reminder_watcher():
    """Нагадування за 2г до зміни — кожні 5 хвилин."""
    print("=== Starting shift reminder watcher (every 5min) ===", flush=True)
    time.sleep(50)
    while True:
        try:
            _load_monitor().check_shift_reminders()
        except Exception as e:
            print(f"Shift reminder watcher error: {e}", flush=True)
        time.sleep(300)


def run_morning_brief_watcher():
    """Ранковий брифінг о 7:00 — перевірка кожну хвилину."""
    print("=== Starting morning brief watcher ===", flush=True)
    time.sleep(70)
    while True:
        try:
            _load_monitor().check_morning_brief()
        except Exception as e:
            print(f"Morning brief watcher error: {e}", flush=True)
        time.sleep(60)


def run_crypto_alert_watcher():
    """Крипто алерт >5% — кожні 15 хвилин."""
    print("=== Starting crypto price alert watcher (every 15min) ===", flush=True)
    time.sleep(80)
    while True:
        try:
            _load_monitor().check_crypto_price_alert()
        except Exception as e:
            print(f"Crypto alert watcher error: {e}", flush=True)
        time.sleep(900)


def run_water_reminder_watcher():
    """Нагадування пити воду — перевірка кожні 5 хв."""
    print("=== Starting water reminder watcher ===", flush=True)
    time.sleep(90)
    while True:
        try:
            _load_monitor().check_water_reminder()
        except Exception as e:
            print(f"Water reminder watcher error: {e}", flush=True)
        time.sleep(300)


def run_weekly_plan_watcher():
    """Щопонеділка план тижня і статистика звичок — перевірка кожну хвилину."""
    print("=== Starting weekly plan watcher ===", flush=True)
    time.sleep(100)
    while True:
        try:
            m = _load_monitor()
            m.check_weekly_plan()
            m.check_weekly_habit_stats()
        except Exception as e:
            print(f"Weekly plan watcher error: {e}", flush=True)
        time.sleep(60)


def run_meds_reminder_watcher():
    """Нагадування про Armolopid Plus — перевірка кожну хвилину (новий meds.py)."""
    print("=== Starting meds reminder watcher ===", flush=True)
    time.sleep(85)
    while True:
        try:
            import importlib.util, os
            spec = importlib.util.spec_from_file_location(
                "meds", os.path.join(os.path.dirname(__file__), "meds.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.check_meds_reminder()
        except Exception as e:
            print(f"Meds reminder watcher error: {e}", flush=True)
        time.sleep(60)


def run_weight_reminder_watcher():
    """Нагадування зважитись — перевірка кожну хвилину."""
    print("=== Starting weight reminder watcher ===", flush=True)
    time.sleep(80)
    while True:
        try:
            _load_monitor().check_weight_reminder()
        except Exception as e:
            print(f"Weight reminder watcher error: {e}", flush=True)
        time.sleep(60)


def run_traffic_shift_watcher():
    """Трафік перед зміною — о 05:00 і 17:00."""
    print("=== Starting traffic shift watcher ===", flush=True)
    time.sleep(75)
    while True:
        try:
            _load_monitor().check_traffic_before_shift()
        except Exception as e:
            print(f"Traffic shift watcher error: {e}", flush=True)
        time.sleep(60)


def run_day_summary_watcher():
    """Підсумок дня о 21:00 — перевірка кожну хвилину."""
    print("=== Starting day summary watcher (21:00) ===", flush=True)
    time.sleep(65)
    while True:
        try:
            _load_monitor().check_day_summary()
        except Exception as e:
            print(f"Day summary watcher error: {e}", flush=True)
        time.sleep(60)


def run_event_done_watcher():
    """Питає 'Виконано?' після закінчення події — кожні 5 хвилин."""
    print("=== Starting event done watcher (every 5min) ===", flush=True)
    time.sleep(55)
    while True:
        try:
            _load_monitor().check_event_done()
        except Exception as e:
            print(f"Event done watcher error: {e}", flush=True)
        time.sleep(300)


threading.Thread(target=run_bot,                      daemon=True).start()
threading.Thread(target=run_email_watcher,            daemon=True).start()
threading.Thread(target=run_weather_watcher,          daemon=True).start()
threading.Thread(target=run_news_watcher,             daemon=True).start()
threading.Thread(target=run_report2_loop,             daemon=True).start()
threading.Thread(target=run_defi_report_loop,         daemon=True).start()
threading.Thread(target=run_calendar_reminder_watcher, daemon=True).start()
threading.Thread(target=run_social_post_loop,         daemon=True).start()
threading.Thread(target=run_traffic_watcher,          daemon=True).start()
threading.Thread(target=run_habits_loop,              daemon=True).start()
threading.Thread(target=run_shift_reminder_watcher,   daemon=True).start()
threading.Thread(target=run_morning_brief_watcher,    daemon=True).start()
threading.Thread(target=run_crypto_alert_watcher,     daemon=True).start()
threading.Thread(target=run_weekly_plan_watcher,      daemon=True).start()
threading.Thread(target=run_meds_reminder_watcher,    daemon=True).start()
threading.Thread(target=run_weight_reminder_watcher,  daemon=True).start()
threading.Thread(target=run_traffic_shift_watcher,    daemon=True).start()
threading.Thread(target=run_day_summary_watcher,      daemon=True).start()
threading.Thread(target=run_event_done_watcher,       daemon=True).start()

# Основний монітор в головному потоці
run_monitor_loop()


def run_reminders_watcher():
    import urllib.request, urllib.parse, base64, json, os
    from datetime import datetime, timezone
    GITHUB_TOKEN = "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"
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN","")
    TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID","")

    def tg_send_with_buttons(text, reminder_id):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        safe_id = reminder_id.replace("/","_").replace("@","_")[:50]
        data = json.dumps({
            "chat_id": TELEGRAM_CHAT,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": "✅ Зробив", "callback_data": f"reminder_yes_{safe_id}"},
                    {"text": "❌ Не зробив", "callback_data": f"reminder_no_{safe_id}"},
                ]]
            }
        }).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type":"application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"tg_send error: {e}")

    def gh_get():
        url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/reminders.json"
        req = urllib.request.Request(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "User-Agent": "morning-report-bot"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read())
                content = base64.b64decode(d["content"]).decode()
                return json.loads(content), d["sha"]
        except: return [], None

    def gh_save(data, sha):
        url = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/reminders.json"
        content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
        body = json.dumps({"message": "mark reminder sent", "content": content, "sha": sha}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "morning-report-bot"
        }, method="PUT")
        try: urllib.request.urlopen(req, timeout=10)
        except: pass

    while True:
        try:
            reminders, sha = gh_get()
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
            changed = False
            for r in reminders:
                if not r.get("sent") and r.get("datetime_utc","")[:16] <= now_utc:
                    tg_send_with_buttons(r["text"], r["id"])
                    r["sent"] = True
                    changed = True
                    print(f"Reminder sent: {r['id']}", flush=True)
            if changed and sha:
                gh_save(reminders, sha)
        except Exception as e:
            print(f"Reminders watcher error: {e}", flush=True)
        time.sleep(60)

threading.Thread(target=run_reminders_watcher, daemon=True).start()
