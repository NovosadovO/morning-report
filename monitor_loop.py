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

# Монітор в головному потоці
run_monitor_loop()
