#!/usr/bin/env python3
"""
Запускає:
- bot.py — слухає команди в Telegram (в окремому потоці)
- monitor.py — надсилає звіт кожні 3 години
"""

import time
import subprocess
import sys
import threading
from datetime import datetime, timezone

def run_bot():
    """Запускає Telegram bot polling в окремому процесі"""
    print("=== Starting bot listener ===", flush=True)
    while True:
        try:
            subprocess.run([sys.executable, "bot.py"])
        except Exception as e:
            print(f"Bot crashed: {e}, restarting in 10s...", flush=True)
            time.sleep(10)

def run_email_watcher():
    """Перевіряє нові важливі листи кожні 5 хвилин — шле миттєве сповіщення."""
    print("=== Starting email watcher (every 5min) ===", flush=True)
    # Пауза при старті щоб monitor.py встиг відпрацювати першим
    time.sleep(30)
    while True:
        try:
            import importlib.util, os
            spec = importlib.util.spec_from_file_location(
                "monitor", os.path.join(os.path.dirname(__file__), "monitor.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.check_new_emails()
        except Exception as e:
            print(f"Email watcher error: {e}", flush=True)
        time.sleep(300)  # 5 хвилин

def run_monitor_loop():
    """Запускає monitor.py кожні 3 години"""
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

# Запускаємо бота в окремому потоці
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

# Email watcher в окремому потоці
email_thread = threading.Thread(target=run_email_watcher, daemon=True)
email_thread.start()

# Монітор в головному потоці
run_monitor_loop()
