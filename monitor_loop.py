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
    """
    Основний звіт — перевіряє кожні 2 хвилини чи ми у вікні (:00, :20, :40).
    Дублі захищені в monitor.py через GitHub SHA claim.
    Запуск кожні 2хв гарантує що не пропустимо 2-хвилинне вікно
    навіть якщо Railway перезапустив процес в будь-який момент.
    """
    print("=== Starting monitor loop (check every 2min, windows :00/:20/:40) ===", flush=True)
    while True:
        now = datetime.now(timezone.utc)
        now_local = now + timedelta(hours=2)
        m = now_local.minute
        # Запускаємо тільки у вікнах: хвилини 0-2, 20-22, 40-42
        if m < 3 or (20 <= m < 23) or (40 <= m < 43):
            print(f"\n[{now.strftime('%Y-%m-%d %H:%M')} UTC] Running monitor (local {now_local.strftime('%H:%M')})...", flush=True)
            try:
                subprocess.run([sys.executable, "monitor.py"], timeout=120)
            except Exception as e:
                print(f"Monitor error: {e}", flush=True)
        time.sleep(120)


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
    """Астрологічний звіт — 2 рази на день:
       Ранок: рання зміна о 08:00, вихідний/нічна о 11:00
       Вечір: завжди о 20:00
    """
    print("=== Starting astro watcher (morning + 20:00 evening) ===", flush=True)
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
                if h == send_hour and m < 5:
                    _astro_gh_mark(morning_key)  # зберігаємо ДО надсилання
                    _send_astro(f"morning shift={shift}")
                    time.sleep(360)
                    continue

            # ── Вечірній о 20:00 ──
            evening_key = f"{today}_evening"
            if not _astro_gh_sent(evening_key) and h == 20 and m < 5:
                _astro_gh_mark(evening_key)  # зберігаємо ДО надсилання
                _send_astro("evening 20:00")
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

    _sent_weekly  = None   # дата (YYYY-Www) останнього тижневого
    _sent_monthly = None   # дата (YYYY-MM)  останнього місячного

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
                print(f"[Steps] Sending weekly report...", flush=True)
                _steps_mod.send_weekly_report()
                _sent_weekly = week_key
                time.sleep(360)
                continue

            # Місячний звіт — 1-го числа о 10:00
            month_key = now_local.strftime("%Y-%m")
            if now_local.day == 1 and h == 10 and m < 5 and _sent_monthly != month_key:
                print(f"[Steps] Sending monthly report...", flush=True)
                _steps_mod.send_monthly_report()
                _sent_monthly = month_key
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
            mod.check_important_emails_followup()
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

# Основний монітор в головному потоці
run_monitor_loop()
