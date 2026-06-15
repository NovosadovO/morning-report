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
import signal
import os
from datetime import datetime, timezone, timedelta

# Graceful shutdown при SIGTERM (Railway деплой)
def _handle_sigterm(signum, frame):
    print("=== SIGTERM received — shutting down ===", flush=True)
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


def run_bot():
    """Запускає bot main() inline (не subprocess) — гарантовано один процес."""
    print("=== Starting bot listener ===", flush=True)
    while True:
        try:
            import importlib
            import bot as _bot_module
            importlib.reload(_bot_module)  # перечитуємо модуль при рестарті
            _bot_module.main()
        except Exception as e:
            print(f"Bot crashed: {e}", flush=True)
        print("Bot exited, restarting in 30s...", flush=True)
        time.sleep(30)


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
    """DeFi & RWA звіт — о 07:00 і 19:00 місцевого часу (UTC+2).
    DeFi дайджест (24h зміни) — о 18:15."""
    print("=== Starting DeFi report loop (07:00 + 18:15 digest + 19:00) ===", flush=True)
    while True:
        now_local = datetime.now(timezone.utc) + timedelta(hours=2)
        h, m = now_local.hour, now_local.minute

        # Повний звіт о 07:00 і 19:00
        if h in (10, 19) and m < 3:
            print(f"[DeFi report] Running at {now_local.strftime('%H:%M')}...", flush=True)
            try:
                subprocess.run([sys.executable, "report_defi.py"], timeout=300)
            except Exception as e:
                print(f"DeFi report error: {e}", flush=True)
            time.sleep(300)

        # 24h дайджест о 18:15
        elif h == 18 and 15 <= m < 20:
            print(f"[DeFi digest] Running 24h digest at {now_local.strftime('%H:%M')}...", flush=True)
            try:
                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
                from report_defi import send_defi_digest
                send_defi_digest()
            except Exception as e:
                print(f"DeFi digest error: {e}", flush=True)
            time.sleep(360)  # не запускати знову у те ж вікно

        else:
            time.sleep(60)


def run_monitor_loop():
    """
    Основний звіт — перевіряє кожну хвилину чи ми точно на :00 або :30.
    Запускає monitor.py лише один раз на слот — дублів немає.
    """
    print("=== Starting monitor loop v2026-06-15 (check every 1min, only at :00) ===", flush=True)
    while True:
        now = datetime.now(timezone.utc)
        now_local = now + timedelta(hours=2)
        m = now_local.minute
        # Запускаємо ТІЛЬКИ у вікні :00-:02 — раз на годину
        if 0 <= m < 3:
            print(f"\n[{now.strftime('%Y-%m-%d %H:%M')} UTC] Running monitor (local {now_local.strftime('%H:%M')})...", flush=True)
            try:
                result = subprocess.run(
                    [sys.executable, "monitor.py"],
                    timeout=300,
                    capture_output=True, text=True
                )
                if result.stdout:
                    print(f"[monitor stdout] {result.stdout[-2000:]}", flush=True)
                if result.stderr:
                    print(f"[monitor stderr] {result.stderr[-2000:]}", flush=True)
                print(f"[monitor] exit code: {result.returncode}", flush=True)
            except subprocess.TimeoutExpired:
                print(f"Monitor TIMEOUT after 300s", flush=True)
            except Exception as e:
                print(f"Monitor error: {e}", flush=True)
            # Після запуску чекаємо 60с щоб не запустити двічі в ту ж хвилину
            time.sleep(60)
        else:
            time.sleep(60)


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
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
            import planner as _planner
            _planner.check_planner_triggers()
        except Exception as e:
            print(f"Planner trigger error: {e}", flush=True)
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
    """Трекер звичок — щоденні питання + тижневий/місячний звіт.
    Запускається як in-process модуль (не subprocess) щоб зберігати
    in-memory стан між ітераціями і не дублювати повідомлення.
    habits.run() має власний while True — просто запускаємо і ловимо краш.
    """
    print("=== Starting habits tracker (in-process) ===", flush=True)
    import importlib.util, os as _os
    while True:
        try:
            # Завантажуємо заново тільки після краша — _INMEM_SENT скидається
            spec = importlib.util.spec_from_file_location(
                "habits", _os.path.join(_os.path.dirname(__file__), "habits.py"))
            _habits_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_habits_mod)
            _habits_mod.run()  # блокує вічно — власний while True
        except Exception as e:
            print(f"Habits crashed: {e}, restarting in 30s...", flush=True)
            time.sleep(30)


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
    """DEPRECATED — замінено на check_morning_context. Нічого не робить."""
    print("=== morning_brief_watcher DISABLED (replaced by morning_context) ===", flush=True)


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


def run_etf_alert_watcher():
    """ETF/S&P500 алерт >3% — кожні 15 хвилин (тільки в торгові години NYSE)."""
    print("=== Starting ETF price alert watcher (every 15min) ===", flush=True)
    time.sleep(100)
    while True:
        try:
            _load_monitor().check_etf_price_alert()
        except Exception as e:
            print(f"ETF alert watcher error: {e}", flush=True)
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
    """Підсумок дня о 19:00 — перевірка кожну хвилину."""
    print("=== Starting day summary watcher (19:00) ===", flush=True)
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
# run_shift_reminder_watcher — замінено на check_calendar_reminders (1г)
threading.Thread(target=run_morning_brief_watcher,    daemon=True).start()
threading.Thread(target=run_crypto_alert_watcher,     daemon=True).start()
threading.Thread(target=run_etf_alert_watcher,        daemon=True).start()
threading.Thread(target=run_weekly_plan_watcher,      daemon=True).start()
threading.Thread(target=run_meds_reminder_watcher,    daemon=True).start()
threading.Thread(target=run_weight_reminder_watcher,  daemon=True).start()
threading.Thread(target=run_traffic_shift_watcher,    daemon=True).start()
threading.Thread(target=run_day_summary_watcher,      daemon=True).start()
threading.Thread(target=run_event_done_watcher,       daemon=True).start()


def run_reminders_watcher():
    """Нагадування з data/reminders.json — кожну хвилину. Підтримка repeat: daily."""
    import urllib.request, urllib.parse, base64, json, os
    from datetime import datetime, timezone, timedelta
    GITHUB_TOKEN = "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"
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN","")
    TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID","")

    def tg_send_with_buttons(text, reminder_id):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        safe_id = reminder_id.replace("/","_").replace("@","_")[:50]

        # Для health нагадування — спеціальні кнопки
        if reminder_id == "health_data_daily":
            keyboard = [[
                {"text": "📸 Надішли фото", "callback_data": "reminder_health_photo"},
                {"text": "📊 Мої дані", "callback_data": "reminder_health_view"},
            ], [
                {"text": "✅ Вже ввів", "callback_data": f"reminder_yes_{safe_id}"},
                {"text": "⏭ Пропустити", "callback_data": f"reminder_no_{safe_id}"},
            ]]
        else:
            keyboard = [[
                {"text": "✅ Зробив", "callback_data": f"reminder_yes_{safe_id}"},
                {"text": "❌ Не зробив", "callback_data": f"reminder_no_{safe_id}"},
            ]]

        data = json.dumps({
            "chat_id": TELEGRAM_CHAT,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": keyboard}
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
        content = base64.b64encode(json.dumps(data, indent=2, ensure_ascii=False).encode()).decode()
        body = json.dumps({"message": "update reminders", "content": content, "sha": sha}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "morning-report-bot"
        }, method="PUT")
        try: urllib.request.urlopen(req, timeout=10)
        except: pass

    print("=== Starting reminders watcher (every 1min, supports repeat:daily) ===", flush=True)
    while True:
        try:
            reminders, sha = gh_get()
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
            changed = False
            for r in reminders:
                if r.get("sent"):
                    continue
                if r.get("datetime_utc","")[:16] <= now_utc:
                    tg_send_with_buttons(r["text"], r["id"])
                    print(f"Reminder sent: {r['id']}", flush=True)
                    changed = True
                    if r.get("repeat") == "daily":
                        # Зсуваємо на наступний день, не ставимо sent=True
                        old_dt = datetime.fromisoformat(r["datetime_utc"])
                        r["datetime_utc"] = (old_dt + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
                        print(f"  repeat:daily → next: {r['datetime_utc']}", flush=True)
                    else:
                        r["sent"] = True
            if changed and sha:
                gh_save(reminders, sha)
        except Exception as e:
            print(f"Reminders watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_reminders_watcher, daemon=True).start()


def run_health_alert_watcher():
    """Health алерти (HRV/стрес/кроки) — кожні 15 хв після того як дані занесені."""
    print("=== Starting health alert watcher (every 15min) ===", flush=True)
    time.sleep(110)
    while True:
        try:
            _load_monitor().check_health_alert()
        except Exception as e:
            print(f"Health alert watcher error: {e}", flush=True)
        time.sleep(900)


threading.Thread(target=run_health_alert_watcher, daemon=True).start()


def run_health_remind_watcher():
    """Нагадування внести health дані о 22:00 якщо не занесено."""
    print("=== Starting health remind watcher (22:00) ===", flush=True)
    time.sleep(115)
    while True:
        try:
            _load_monitor().check_health_data_reminder()
        except Exception as e:
            print(f"Health remind watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_health_remind_watcher, daemon=True).start()


def run_astro_watcher():
    """Астрологічний звіт — 3 рази на день:
       Ранок:  рання зміна о 08:00, інші о 11:00 (вікно до 12:59)
       Обід:   завжди о 13:00 (вікно 13:00–16:59)
       Вечір:  завжди о 20:00 (вікно 20:00–23:59)
    """
    print("=== Starting astro watcher (morning + afternoon + evening) ===", flush=True)
    import os, sys
    sys.path.insert(0, os.path.dirname(__file__))

    def _astro_gh_sent(slot_key):
        """Check if astro was already sent (slot_key = date_morning / date_evening). Returns bool."""
        try:
            from storage import _load_github
            data = _load_github("astro_sent.json") or {}
            return bool(data.get(slot_key))
        except Exception as e:
            print(f"[astro] dedup check error: {e}", flush=True)
            return False

    def _astro_gh_mark(slot_key):
        """Mark astro as sent in GitHub."""
        try:
            from storage import _load_github, _save_github
            data = _load_github("astro_sent.json") or {}
            data[slot_key] = True
            _save_github("astro_sent.json", data)
        except Exception as e:
            print(f"[astro] dedup save error: {e}", flush=True)
    time.sleep(120)

    def _send_astro(label):
        import importlib, urllib.request, urllib.parse, json as _json, os as _os, tempfile
        import astro as _astro_mod, astro_chart as _chart_mod
        importlib.reload(_astro_mod)
        importlib.reload(_chart_mod)
        token = _os.environ.get("TELEGRAM_TOKEN", "")
        chat  = _os.environ.get("TELEGRAM_CHAT_ID", "")

        # 1. Generate chart image
        chart_path = None
        try:
            chart_path = _chart_mod.generate_natal_chart()
        except Exception as e:
            print(f"Chart generation failed: {e}", flush=True)

        # 2. Send chart image (sendPhoto) if available
        if chart_path and _os.path.exists(chart_path):
            try:
                boundary = "----TelegramBoundary"
                with open(chart_path, "rb") as f:
                    img_data = f.read()
                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="photo"; filename="astro_chart.png"\r\n'
                    f"Content-Type: image/png\r\n\r\n"
                ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
                photo_url = f"https://api.telegram.org/bot{token}/sendPhoto"
                req2 = urllib.request.Request(
                    photo_url, data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
                )
                urllib.request.urlopen(req2, timeout=30)
                print(f"Astro chart image sent [{label}]", flush=True)
            except Exception as e:
                print(f"Chart send failed: {e}", flush=True)
            finally:
                try: _os.unlink(chart_path)
                except: pass

        # 3. Send text report as separate message
        report = _astro_mod.get_astro_report()
        # Split if > 4000 chars (Telegram limit)
        chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
        msg_url = f"https://api.telegram.org/bot{token}/sendMessage"
        for chunk in chunks:
            payload = _json.dumps({"chat_id": chat, "text": chunk, "parse_mode": "HTML"}).encode()
            req3 = urllib.request.Request(msg_url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req3, timeout=15)
        print(f"Astro report text sent [{label}]", flush=True)

    while True:
        try:
            now_local = datetime.now(timezone.utc) + timedelta(hours=2)
            today = now_local.strftime("%Y-%m-%d")
            h, m = now_local.hour, now_local.minute

            # ── Ранковий ──
            morning_key = f"{today}_morning"
            if not _astro_gh_sent(morning_key):
                try:
                    from meds import _get_today_shift_type
                    shift = _get_today_shift_type()
                except Exception:
                    shift = "weekend"
                send_hour = 8 if shift == "early" else 11
                # Вікно: від send_hour до 12:59
                if h >= send_hour and h < 13:
                    _send_astro(f"morning shift={shift} h={h}")
                    _astro_gh_mark(morning_key)
                    time.sleep(360)
                    continue

            # ── Обідній о 13:00 ──
            afternoon_key = f"{today}_afternoon"
            if not _astro_gh_sent(afternoon_key):
                # Вікно 13:00–16:59
                if h >= 13 and h < 17:
                    _send_astro(f"afternoon h={h}")
                    _astro_gh_mark(afternoon_key)
                    time.sleep(360)
                    continue

            # ── Вечірній о 20:00 ──
            evening_key = f"{today}_evening"
            if not _astro_gh_sent(evening_key):
                # Вікно 20:00–23:59
                if h >= 20:
                    _send_astro(f"evening h={h}")
                    _astro_gh_mark(evening_key)
                    time.sleep(360)
                    continue

        except Exception as e:
            print(f"Astro watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_astro_watcher, daemon=True).start()




def run_extra_watchers():
    """Додаткові проактивні функції — перевірка кожні 3 хвилини."""
    print("=== Starting extra watchers (crypto weekly, net worth, invest digest, fasting, pre-shift weather, learning streak) ===", flush=True)
    time.sleep(120)
    while True:
        try:
            m = _load_monitor()
            m.check_crypto_weekly_summary()
            m.check_net_worth_reminder()
            m.check_investment_news_digest()
            m.check_fasting_reminder()
            m.check_pre_shift_weather()
            m.check_learning_streak()
        except Exception as e:
            print(f"Extra watchers error: {e}", flush=True)
        time.sleep(300)


threading.Thread(target=run_extra_watchers, daemon=True).start()


def run_smart_notifications_watcher():
    """Розумні контекст-залежні нотифікації — кожну хвилину."""
    print("=== Starting smart notifications watcher ===", flush=True)
    time.sleep(95)
    while True:
        try:
            _load_monitor().check_smart_notifications()
        except Exception as e:
            print(f"Smart notif watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_smart_notifications_watcher, daemon=True).start()

def run_morning_context_watcher():
    """Ранковий контекст (AI + погода + календар) — кожну хвилину."""
    print("=== Starting morning context watcher ===", flush=True)
    time.sleep(130)
    while True:
        try:
            _load_monitor().check_morning_context()
        except Exception as e:
            print(f"Morning context watcher error: {e}", flush=True)
        time.sleep(60)


def run_run_coach_watcher():
    """Тренер бігу — нагадування до тренування кожну хвилину."""
    print("=== Starting run coach watcher ===", flush=True)
    time.sleep(135)
    while True:
        try:
            _load_monitor().check_run_coach()
        except Exception as e:
            print(f"Run coach watcher error: {e}", flush=True)
        time.sleep(60)


def run_nutrition_watcher():
    """Нагадування харчування — кожну хвилину."""
    print("=== Starting nutrition reminder watcher ===", flush=True)
    time.sleep(140)
    while True:
        try:
            _load_monitor().check_nutrition_reminder()
        except Exception as e:
            print(f"Nutrition watcher error: {e}", flush=True)
        time.sleep(60)


def run_sleep_quality_watcher():
    """Якість сну — питає вранці о 08:00 кожну хвилину."""
    print("=== Starting sleep quality watcher ===", flush=True)
    time.sleep(145)
    while True:
        try:
            _load_monitor().check_sleep_quality()
        except Exception as e:
            print(f"Sleep quality watcher error: {e}", flush=True)
        time.sleep(60)


def run_crypto_morning_watcher():
    """Крипто-ранок — AI огляд портфелю о 08:30 кожну хвилину."""
    print("=== Starting crypto morning watcher ===", flush=True)
    time.sleep(150)
    while True:
        try:
            _load_monitor().check_crypto_morning()
        except Exception as e:
            print(f"Crypto morning watcher error: {e}", flush=True)
        time.sleep(60)


def run_week_goals_watcher():
    """Цілі тижня — щопонеділка і щоп'ятниці кожну хвилину."""
    print("=== Starting week goals watcher ===", flush=True)
    time.sleep(155)
    while True:
        try:
            _load_monitor().check_week_goals()
        except Exception as e:
            print(f"Week goals watcher error: {e}", flush=True)
        time.sleep(60)


def run_calendar_live_watcher():
    """Живий календар — сповіщення за 15хв і при старті події кожні 5 хв."""
    print("=== Starting calendar live watcher (every 5min) ===", flush=True)
    time.sleep(160)
    while True:
        try:
            _load_monitor().check_calendar_live()
        except Exception as e:
            print(f"Calendar live watcher error: {e}", flush=True)
        time.sleep(300)


threading.Thread(target=run_morning_context_watcher,  daemon=True).start()
threading.Thread(target=run_run_coach_watcher,         daemon=True).start()
threading.Thread(target=run_nutrition_watcher,         daemon=True).start()
threading.Thread(target=run_sleep_quality_watcher,     daemon=True).start()
threading.Thread(target=run_crypto_morning_watcher,    daemon=True).start()
threading.Thread(target=run_week_goals_watcher,        daemon=True).start()
threading.Thread(target=run_calendar_live_watcher,     daemon=True).start()


def run_mood_watcher():
    """Вечірнє питання про настрій — о 21:30."""
    print("=== Starting mood evening watcher ===", flush=True)
    time.sleep(165)
    while True:
        try:
            _load_monitor().check_mood_evening()
        except Exception as e:
            print(f"Mood watcher error: {e}", flush=True)
        time.sleep(60)


def run_step_goal_watcher():
    """Прогрес кроків — о 18:00 у вільний день."""
    print("=== Starting step goal watcher ===", flush=True)
    time.sleep(170)
    while True:
        try:
            _load_monitor().check_step_goal()
        except Exception as e:
            print(f"Step goal watcher error: {e}", flush=True)
        time.sleep(60)


def run_friday_recap_watcher():
    """П'ятничний підсумок тижня — о 20:00."""
    print("=== Starting friday recap watcher ===", flush=True)
    time.sleep(175)
    while True:
        try:
            _load_monitor().check_friday_recap()
        except Exception as e:
            print(f"Friday recap watcher error: {e}", flush=True)
        time.sleep(60)


def run_weight_trend_watcher():
    """Алерт якщо вага росте 3+ дні — о 10:00."""
    print("=== Starting weight trend watcher ===", flush=True)
    time.sleep(180)
    while True:
        try:
            _load_monitor().check_weight_trend_alert()
        except Exception as e:
            print(f"Weight trend watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_mood_watcher,         daemon=True).start()
threading.Thread(target=run_step_goal_watcher,    daemon=True).start()
threading.Thread(target=run_friday_recap_watcher, daemon=True).start()
threading.Thread(target=run_weight_trend_watcher, daemon=True).start()


def run_planet_ingress_watcher():
    """Транзит планет — зміна знаку або натального дому. Перевірка кожні 30 хв."""
    print("=== Starting planet ingress watcher (every 30min) ===", flush=True)
    time.sleep(200)
    while True:
        try:
            _load_monitor().check_planet_ingress()
        except Exception as e:
            print(f"Planet ingress watcher error: {e}", flush=True)
        time.sleep(1800)


threading.Thread(target=run_planet_ingress_watcher, daemon=True).start()


def run_transit_aspects_watcher():
    """Транзитні аспекти до натальних планет — перевірка кожні 30 хвилин."""
    print("=== Starting transit aspects watcher (every 30min) ===", flush=True)
    time.sleep(300)  # затримка при старті щоб не перевантажувати
    while True:
        try:
            _load_monitor().check_transit_aspects()
        except Exception as e:
            print(f"Transit aspects watcher error: {e}", flush=True)
        time.sleep(1800)

threading.Thread(target=run_transit_aspects_watcher, daemon=True).start()


def run_proactive_watcher():
    """Проактивні повідомлення від бота — кожні 30 хвилин.
    Модуль завантажується ОДИН РАЗ щоб _SENT_INMEM не скидався між викликами.
    """
    print("=== Starting proactive watcher (every 30min) ===", flush=True)
    time.sleep(210)  # чекаємо 3.5 хв після старту
    import importlib.util, os as _os
    # Завантажуємо один раз — in-memory стан зберігається
    spec = importlib.util.spec_from_file_location(
        "proactive", _os.path.join(_os.path.dirname(__file__), "proactive.py"))
    _proactive_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_proactive_mod)
    while True:
        try:
            _proactive_mod.check_proactive()
        except Exception as e:
            print(f"Proactive watcher error: {e}", flush=True)
        try:
            _proactive_mod.check_ai_observations()
        except Exception as e:
            print(f"AI observations watcher error: {e}", flush=True)
        time.sleep(1800)  # 30 хвилин


threading.Thread(target=run_proactive_watcher, daemon=True).start()


def run_steps_watcher():
    """StepsApp сповіщення + тижневий/місячний звіт кроків."""
    print("=== Starting steps watcher ===", flush=True)
    time.sleep(220)
    import importlib.util, os as _os
    spec = importlib.util.spec_from_file_location(
        "steps", _os.path.join(_os.path.dirname(__file__), "steps.py"))
    _steps_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_steps_mod)

    # In-memory cache (після рестарту читаємо з GitHub через _steps_mod)
    _sent_weekly  = None
    _sent_monthly = None

    while True:
        try:
            now_local = datetime.now(timezone.utc) + timedelta(hours=2)
            h, m = now_local.hour, now_local.minute
            weekday = now_local.weekday()  # 0=Mon, 6=Sun

            # Щоденні сповіщення — кожну хвилину
            _steps_mod.check_steps_notifications()

            # Тижневий звіт — щонеділі о 20:00
            week_key = now_local.strftime("%Y-W%W")
            if weekday == 6 and h == 20 and m < 5 and _sent_weekly != week_key:
                # Перевіряємо persistent state через GitHub щоб уникнути дублів після рестарту
                try:
                    _st, _ = _steps_mod._gh_load("steps_state.json")
                    _st = _st or {}
                except Exception:
                    _st = {}
                if _st.get("weekly_sent") == week_key:
                    _sent_weekly = week_key  # синхронізуємо in-memory
                else:
                    print(f"[Steps] Sending weekly report...", flush=True)
                    _steps_mod.send_weekly_report()
                    _sent_weekly = week_key
                    # Зберігаємо в GitHub
                    try:
                        _st["weekly_sent"] = week_key
                        _, _sha = _steps_mod._gh_load("steps_state.json")
                        _steps_mod._gh_save("steps_state.json", _st, _sha)
                    except Exception as _e_sw:
                        print(f"[Steps] save weekly state error: {_e_sw}", flush=True)
                time.sleep(360)
                continue

            # Місячний звіт — 1-го числа о 10:00
            month_key = now_local.strftime("%Y-%m")
            if now_local.day == 1 and h == 10 and m < 5 and _sent_monthly != month_key:
                try:
                    _st2, _ = _steps_mod._gh_load("steps_state.json")
                    _st2 = _st2 or {}
                except Exception:
                    _st2 = {}
                if _st2.get("monthly_sent") == month_key:
                    _sent_monthly = month_key
                else:
                    print(f"[Steps] Sending monthly report...", flush=True)
                    _steps_mod.send_monthly_report()
                    _sent_monthly = month_key
                    try:
                        _st2["monthly_sent"] = month_key
                        _, _sha2 = _steps_mod._gh_load("steps_state.json")
                        _steps_mod._gh_save("steps_state.json", _st2, _sha2)
                    except Exception as _e_sm:
                        print(f"[Steps] save monthly state error: {_e_sm}", flush=True)
                time.sleep(360)
                continue

        except Exception as e:
            print(f"Steps watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_steps_watcher, daemon=True).start()


# ─── QWatch watcher ───────────────────────────────────────────────────────────
def run_qwatch_watcher():
    """QWatch: нагадування о 19:02, тижневий (нд 20:30), місячний (1-го 09:05)."""
    print("=== Starting QWatch watcher ===", flush=True)
    time.sleep(70)
    while True:
        try:
            import importlib.util, os as _os
            spec = importlib.util.spec_from_file_location(
                "qwatch", _os.path.join(_os.path.dirname(__file__), "qwatch.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.check_qwatch_reminder()
            mod.check_qwatch_weekly()
            mod.check_qwatch_monthly()
        except Exception as e:
            print(f"QWatch watcher error: {e}", flush=True)
        time.sleep(60)

threading.Thread(target=run_qwatch_watcher, daemon=True).start()


# ─── Assistant watcher (10хв/1г нагадування + вечір завтра + пропозиції) ─────
def run_assistant_watcher():
    """assistant.py: нагадування за 10хв/1г, вечірній огляд завтра, пропозиції."""
    print("=== Starting assistant watcher ===", flush=True)
    time.sleep(70)  # затримка щоб уникнути старту разом з іншими
    while True:
        try:
            import importlib.util, os as _os
            spec = importlib.util.spec_from_file_location(
                "assistant", _os.path.join(_os.path.dirname(__file__), "assistant.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.check_calendar_10min()
            mod.check_calendar_1h()
            mod.check_calendar_day_ahead()
            mod.propose_calendar_events()
            mod.check_calendar_3days()
            mod.check_calendar_1day()
            mod.check_morning_after_night()
            mod.check_email_digest()
            mod.check_sleep_reminder()
            mod.check_salary_reminder()
            mod.check_user_silent()
            mod.check_birthdays()
            mod.check_old_unread_emails()
            if hasattr(mod, 'check_important_emails_followup'):
                mod.check_important_emails_followup()
            if hasattr(mod, 'check_email_deadlines'):
                mod.check_email_deadlines()
        except Exception as e:
            print(f"Assistant watcher error: {e}", flush=True)
        time.sleep(60)

threading.Thread(target=run_assistant_watcher, daemon=True).start()
print("=== Assistant watcher thread started ===", flush=True)


# ─── Webhook сервер в окремому thread ────────────────────────────────────────
def run_webhook_server():
    """Запускає health_webhook.py HTTP сервер в окремому thread."""
    try:
        import importlib.util, os as _os
        spec = importlib.util.spec_from_file_location(
            "health_webhook", _os.path.join(_os.path.dirname(__file__), "health_webhook.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.run_server()
    except Exception as e:
        print(f"Webhook server error: {e}", flush=True)

threading.Thread(target=run_webhook_server, daemon=True).start()
print("=== Webhook server thread started ===", flush=True)


def run_persistent_reminders_watcher():
    """Persistent нагадування — звички/planner/календар кожну годину поки не відмічено."""
    print("=== Starting persistent reminders watcher (every 15min check) ===", flush=True)
    time.sleep(120)
    while True:
        try:
            import importlib.util, os as _os
            spec = importlib.util.spec_from_file_location(
                "habits", _os.path.join(_os.path.dirname(__file__), "habits.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.check_persistent_reminders()
        except Exception as e:
            print(f"Persistent reminders watcher error: {e}", flush=True)
        time.sleep(900)  # перевіряємо кожні 15 хв, але шлемо раз на годину (логіка всередині)


threading.Thread(target=run_persistent_reminders_watcher, daemon=True).start()
print("=== Persistent reminders watcher thread started ===", flush=True)


def run_shopping_watcher():
    """Нагадування про список покупок о 12:45 і 19:15 — кожну хвилину."""
    print("=== Starting shopping reminder watcher (12:45 + 19:15) ===", flush=True)
    time.sleep(190)
    while True:
        try:
            _load_monitor().check_shopping_reminder()
        except Exception as e:
            print(f"Shopping watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_shopping_watcher, daemon=True).start()
print("=== Shopping reminder watcher thread started ===", flush=True)


def run_strava_watcher():
    """Авто-сповіщення після нового тренування Strava — кожні 10 хв."""
    print("=== Starting Strava activity watcher (every 10min) ===", flush=True)
    time.sleep(90)
    while True:
        try:
            _load_monitor().check_strava_new_activity()
        except Exception as e:
            print(f"Strava watcher error: {e}", flush=True)
        time.sleep(600)

threading.Thread(target=run_strava_watcher, daemon=True).start()
print("=== Strava watcher thread started ===", flush=True)


def run_strava_charts_loop():
    """
    Графіки бігу 2 рази на день:
    - 07:30 UTC+2 — місячний графік
    - 21:00 UTC+2 — тижневий прогрес
    Weekly report — неділя 20:00 UTC+2
    Monthly report — 1-ше число 09:00 UTC+2
    """
    print("=== Starting Strava charts loop ===", flush=True)
    _sent_morning  = None
    _sent_evening  = None
    _sent_weekly   = None
    _sent_monthly  = None

    while True:
        try:
            now = datetime.now(timezone.utc) + timedelta(hours=2)
            h, m = now.hour, now.minute
            day_str = now.strftime("%Y-%m-%d")
            week_str = now.strftime("%Y-W%W")
            month_str = now.strftime("%Y-%m")

            mon = _load_monitor()

            # Ранковий графік — 07:30
            if h == 7 and 30 <= m < 35 and _sent_morning != day_str:
                _sent_morning = day_str
                print(f"[strava_charts] morning chart {day_str}", flush=True)
                try:
                    mon.send_strava_chart_daily()
                except Exception as e:
                    print(f"[strava_charts] morning error: {e}", flush=True)

            # Вечірній графік — 21:00
            if h == 21 and 0 <= m < 5 and _sent_evening != day_str:
                _sent_evening = day_str
                print(f"[strava_charts] evening chart {day_str}", flush=True)
                try:
                    mon.send_strava_chart_daily()
                except Exception as e:
                    print(f"[strava_charts] evening error: {e}", flush=True)

            # Тижневий звіт — неділя о 20:00
            if now.weekday() == 6 and h == 20 and 0 <= m < 5 and _sent_weekly != week_str:
                _sent_weekly = week_str
                print(f"[strava_charts] weekly report {week_str}", flush=True)
                try:
                    mon.check_strava_weekly_report()
                except Exception as e:
                    print(f"[strava_charts] weekly error: {e}", flush=True)

            # Місячний звіт — 1-ше число о 09:00
            if now.day == 1 and h == 9 and 0 <= m < 5 and _sent_monthly != month_str:
                _sent_monthly = month_str
                print(f"[strava_charts] monthly report {month_str}", flush=True)
                try:
                    mon.check_strava_monthly_report()
                except Exception as e:
                    print(f"[strava_charts] monthly error: {e}", flush=True)

        except Exception as e:
            print(f"[strava_charts_loop] error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_strava_charts_loop, daemon=True).start()
print("=== Strava charts loop started ===", flush=True)


def run_stress_alert_watcher():
    """Стрес-алерт о 11:00 — комбінація сигналів."""
    print("=== Starting stress alert watcher ===", flush=True)
    time.sleep(110)
    while True:
        try:
            _load_monitor().check_stress_alert()
        except Exception as e:
            print(f"Stress alert watcher error: {e}", flush=True)
        time.sleep(60)

threading.Thread(target=run_stress_alert_watcher, daemon=True).start()
print("=== Stress alert watcher thread started ===", flush=True)


def run_monthly_summary_watcher():
    """Місячний підсумок — 1-го числа о 09:00."""
    print("=== Starting monthly summary watcher ===", flush=True)
    time.sleep(115)
    while True:
        try:
            _load_monitor().check_monthly_summary()
        except Exception as e:
            print(f"Monthly summary watcher error: {e}", flush=True)
        time.sleep(60)

threading.Thread(target=run_monthly_summary_watcher, daemon=True).start()
print("=== Monthly summary watcher thread started ===", flush=True)


def run_currency_watcher():
    """Курс валют — алерт при різкому русі EUR/USD."""
    print("=== Starting currency alert watcher (hourly) ===", flush=True)
    time.sleep(120)
    while True:
        try:
            _load_monitor().check_currency_alert()
        except Exception as e:
            print(f"Currency watcher error: {e}", flush=True)
        time.sleep(3600)

threading.Thread(target=run_currency_watcher, daemon=True).start()
print("=== Currency watcher thread started ===", flush=True)


def run_evening_charts_watcher():
    """ВИМКНЕНО: замінено на run_report_card_watcher (один великий PNG-звіт)."""
    return  # вимкнено — всі дані тепер в report_card


def _run_evening_charts_watcher_DISABLED():
    """Надсилає всі три графіки щовечора о 20:00 (UTC+2). ВИМКНЕНО."""
    import os, json, urllib.request
    from datetime import datetime, timezone, timedelta
    print("=== Starting evening charts watcher (20:00 UTC+2) ===", flush=True)
    time.sleep(200)

    SENT_KEY_FILE = "evening_charts_sent.json"

    def _already_sent(date_str):
        try:
            from storage import _load_github
            d = _load_github(SENT_KEY_FILE) or {}
            return bool(d.get(date_str))
        except Exception:
            return False

    def _mark_sent(date_str):
        try:
            from storage import _load_github, _save_github
            d = _load_github(SENT_KEY_FILE) or {}
            d[date_str] = True
            _save_github(SENT_KEY_FILE, d)
        except Exception as e:
            print(f"[evening_charts] mark_sent error: {e}")

    def _send_photo(photo_bytes, caption):
        token = os.environ.get("TELEGRAM_TOKEN", "")
        chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return
        import io as _io
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendPhoto",
            )
            import http.client, urllib.parse
            boundary = "----FormBoundary"
            body_parts = []
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat}".encode())
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}".encode())
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"parse_mode\"\r\n\r\nHTML".encode())
            body_parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
                + photo_bytes
            )
            body_parts.append(f"--{boundary}--".encode())
            body = b"\r\n".join(body_parts)
            import requests as _req
            _req.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data={"chat_id": chat, "caption": caption, "parse_mode": "HTML"},
                files={"photo": ("chart.png", _io.BytesIO(photo_bytes), "image/png")},
                timeout=30,
            )
        except Exception as e:
            print(f"[evening_charts] send_photo error: {e}")

    while True:
        try:
            now_local = datetime.now(timezone.utc) + timedelta(hours=2)
            h, m = now_local.hour, now_local.minute
            date_str = now_local.strftime("%Y-%m-%d")

            if h == 20 and 0 <= m < 5 and not _already_sent(date_str):
                _mark_sent(date_str)
                print(f"[evening_charts] sending charts for {date_str}", flush=True)

                mon = _load_monitor()

                # 1. Крипто
                try:
                    chart = mon.generate_crypto_trend_chart(30)
                    if chart:
                        _send_photo(chart, "📈 Крипто тренд 30д | BTC ETH AVAX ONDO")
                        time.sleep(1)
                except Exception as e:
                    print(f"[evening_charts] crypto error: {e}")

                # 2. Вага
                try:
                    chart = mon.generate_weight_trend_chart(30)
                    if chart:
                        _send_photo(chart, "⚖️ Тренд ваги — останні 30 вимірювань")
                        time.sleep(1)
                except Exception as e:
                    print(f"[evening_charts] weight error: {e}")

                # 3. Звички
                try:
                    chart = mon.generate_habits_chart(30)
                    if chart:
                        _send_photo(chart, "📊 Звички за 30 днів")
                        time.sleep(1)
                except Exception as e:
                    print(f"[evening_charts] habits error: {e}")

                print(f"[evening_charts] done for {date_str}", flush=True)

        except Exception as e:
            print(f"Evening charts watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_evening_charts_watcher, daemon=True).start()
print("=== Evening charts watcher thread started (20:00 UTC+2) ===", flush=True)


def run_report_card_watcher():
    """Надсилає 3-фото album о 09:00 (ранковий) і 20:05 (вечірній) UTC+2 + астро-текст після альбому."""
    import os, io, json
    print("=== Starting report card watcher (09:00 + 20:05 UTC+2) ===", flush=True)
    time.sleep(120)  # дати боту стартувати
    sent = {"morning": None, "evening": None}

    def _send_album(photos, caption):
        """Надсилає список байтів як Telegram media group."""
        token = os.environ.get("TELEGRAM_TOKEN", "")
        chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return
        try:
            import requests as _req
            media = []
            files = {}
            for i, p in enumerate(photos):
                key = f"photo{i}"
                media.append({
                    "type": "photo",
                    "media": f"attach://{key}",
                    "caption": caption if i == 0 else "",
                    "parse_mode": "HTML",
                })
                files[key] = (f"report{i}.png", io.BytesIO(p), "image/png")
            resp = _req.post(
                f"https://api.telegram.org/bot{token}/sendMediaGroup",
                data={"chat_id": chat, "media": json.dumps(media)},
                files=files,
                timeout=60,
            )
            print(f"[report_card] album sent ({len(photos)} photos): {caption} → {resp.status_code}", flush=True)
        except Exception as e:
            print(f"[report_card] send_album error: {e}", flush=True)

    def _send_astro_text():
        """Надсилає астро-звіт текстом після альбому."""
        token = os.environ.get("TELEGRAM_TOKEN", "")
        chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return
        try:
            import sys as _sys, importlib, urllib.request as _url
            _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import astro as _astro_mod
            importlib.reload(_astro_mod)
            report = _astro_mod.get_astro_report()
            chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
            msg_url = f"https://api.telegram.org/bot{token}/sendMessage"
            for chunk in chunks:
                payload = json.dumps({"chat_id": chat, "text": chunk, "parse_mode": "HTML"}).encode()
                req = _url.Request(msg_url, data=payload, headers={"Content-Type": "application/json"})
                _url.urlopen(req, timeout=15)
            print("[report_card] astro text sent", flush=True)
        except Exception as e:
            print(f"[report_card] astro text error: {e}", flush=True)

    while True:
        try:
            now_local = datetime.now(timezone.utc) + timedelta(hours=2)
            h, m = now_local.hour, now_local.minute
            ds = now_local.strftime("%Y-%m-%d")

            # Ранковий звіт о 09:00
            if h == 9 and 0 <= m < 5 and sent["morning"] != ds:
                sent["morning"] = ds
                print(f"[report_card] generating morning album for {ds}", flush=True)
                try:
                    import sys as _sys, os as _os
                    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
                    from report_card import generate_report_album
                    photos = generate_report_album("morning")
                    if photos:
                        _send_album(photos, "☀️ <b>Ранковий звіт</b>")
                        # астро надсилається окремо через run_astro_watcher
                except Exception as e:
                    print(f"[report_card] morning error: {e}", flush=True)

            # Вечірній звіт о 20:05 (після графіків о 20:00)
            if h == 20 and 5 <= m < 10 and sent["evening"] != ds:
                sent["evening"] = ds
                print(f"[report_card] generating evening album for {ds}", flush=True)
                try:
                    import sys as _sys, os as _os
                    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
                    from report_card import generate_report_album
                    photos = generate_report_album("evening")
                    if photos:
                        _send_album(photos, "🌙 <b>Вечірній звіт</b>")
                        # астро надсилається окремо через run_astro_watcher
                except Exception as e:
                    print(f"[report_card] evening error: {e}", flush=True)

        except Exception as e:
            print(f"[report_card] watcher error: {e}", flush=True)
        time.sleep(60)


threading.Thread(target=run_report_card_watcher, daemon=True).start()
print("=== Report card watcher thread started (09:00 + 20:05 UTC+2) ===", flush=True)

# Основний монітор в головному потоці
run_monitor_loop()
