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

def _load_monitor():
    import importlib.util, os
    spec = importlib.util.spec_from_file_location(
        "monitor", os.path.join(os.path.dirname(__file__), "monitor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def run_email_watcher():
    """Перевіряє нові важливі листи кожні 5 хвилин."""
    print("=== Starting email watcher (every 5min) ===", flush=True)
    time.sleep(30)
    while True:
        try:
            _load_monitor().check_new_emails()
        except Exception as e:
            print(f"Email watcher error: {e}", flush=True)
        time.sleep(300)

def run_weather_watcher():
    """Перевіряє погодні алерти кожні 30 хвилин."""
    print("=== Starting weather watcher (every 30min) ===", flush=True)
    time.sleep(60)
    while True:
        try:
            _load_monitor().check_weather_alert()
        except Exception as e:
            print(f"Weather watcher error: {e}", flush=True)
        time.sleep(1800)

def run_news_watcher():
    """Перевіряє крипто новини кожні 4 години."""
    print("=== Starting crypto news watcher (every 4h) ===", flush=True)
    time.sleep(90)
    while True:
        try:
            _load_monitor().check_crypto_news()
        except Exception as e:
            print(f"News watcher error: {e}", flush=True)
        time.sleep(14400)

def run_report2_loop():
    """Запускає report2.py кожні 3 години зі зсувом 1.5г від основного звіту."""
    print("=== Starting report2 loop (every 3h, offset 1.5h) ===", flush=True)
    time.sleep(5400)  # чекаємо 1.5г після старту
    while True:
        now = datetime.now(timezone.utc)
        print(f"\n[{now.strftime('%Y-%m-%d %H:%M')} UTC] Running report2...", flush=True)
        try:
            subprocess.run([sys.executable, "report2.py"], timeout=120)
        except Exception as e:
            print(f"Report2 error: {e}", flush=True)
        time.sleep(10800)  # кожні 3г



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

# Запускаємо всі сервіси в окремих потоках
threading.Thread(target=run_bot,             daemon=True).start()
threading.Thread(target=run_email_watcher,   daemon=True).start()
threading.Thread(target=run_weather_watcher, daemon=True).start()
threading.Thread(target=run_news_watcher,    daemon=True).start()
threading.Thread(target=run_report2_loop,    daemon=True).start()

# Основний монітор в головному потоці
run_monitor_loop()
