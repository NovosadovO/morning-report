"""
Proactive Scheduler v3.0 — Thread-based scheduler для 4 щоденних аналізів
Замість polling-based notifications, вико давати аналізи на РОЗКЛАДІ:
  - 6:00 UTC+2: Ранок (календар, здоров'я, крипто огляд)
  - 12:00 UTC+2: Обід (email VIP, крипто moves, здоров'я)
  - 15:00 UTC+2: Після обід (рекомендації, планування)
  - 20:00 UTC+2: Вечір (день summary, астро, слова мотивації)
"""

import os
import threading
import time
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# ============ CONFIG ============

TZ = ZoneInfo("Europe/Bratislava")  # UTC+2 (Kosice, Slovakia)
SCHEDULES = {
    "morning": 6,      # 6:00 UTC+2
    "lunch": 12,       # 12:00 UTC+2
    "afternoon": 15,   # 15:00 UTC+2
    "evening": 20,     # 20:00 UTC+2
}

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SCHEDULER_STATE_FILE = os.path.join(_DATA_DIR, "scheduler_state.json")

# ============ STATE ============

_SCHEDULER_RUNNING = False
_SCHEDULER_THREADS = {}
_CALLBACK_FUNCTIONS = {}

# ============ UTILS ============

def _load_scheduler_state():
    """Завантажити лист часів що вже запущені сьогодні"""
    if os.path.exists(SCHEDULER_STATE_FILE):
        try:
            with open(SCHEDULER_STATE_FILE, "r") as f:
                return json.load(f) or {}
        except:
            pass
    return {"last_run_date": None, "completed_schedules": []}

def _save_scheduler_state(state):
    """Зберегти лист часів"""
    try:
        os.makedirs(os.path.dirname(SCHEDULER_STATE_FILE), exist_ok=True)
        with open(SCHEDULER_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[SCHEDULER] Save state error: {e}")

def _get_current_time_tz():
    """Отримати поточний час у часовому поясі Олега"""
    return datetime.now(TZ)

def _get_hours_until_next_schedule(current_hour):
    """Скільки годин до наступного розкладу"""
    schedule_hours = sorted(SCHEDULES.values())
    
    for sh in schedule_hours:
        if current_hour < sh:
            return sh - current_hour
    
    # Якщо пропустили всі, наступний = перший завтра
    return 24 - current_hour + schedule_hours[0]

# ============ SCHEDULER ============

def _scheduler_worker():
    """Worker thread що перевіряє розклад кожну хвилину"""
    global _SCHEDULER_RUNNING
    
    print("[SCHEDULER] Worker started")
    
    while _SCHEDULER_RUNNING:
        try:
            now = _get_current_time_tz()
            current_hour = now.hour
            current_date = now.strftime("%Y-%m-%d")
            
            state = _load_scheduler_state()
            last_run_date = state.get("last_run_date", None)
            completed_schedules = state.get("completed_schedules", [])
            
            # Якщо день змінився, скидаємо completed
            if last_run_date != current_date:
                completed_schedules = []
                state["last_run_date"] = current_date
            
            # Перевіряємо кожен розклад
            for name, hour in SCHEDULES.items():
                if current_hour == hour and name not in completed_schedules:
                    print(f"[SCHEDULER] Triggered {name} at {now.strftime('%H:%M:%S')}")
                    
                    # Викликаємо callback якщо зареєстрований
                    if name in _CALLBACK_FUNCTIONS:
                        try:
                            callback = _CALLBACK_FUNCTIONS[name]
                            callback(name, now)  # Передаємо имя розкладу і час
                        except Exception as e:
                            print(f"[SCHEDULER] Callback error for {name}: {e}")
                    
                    # Позначаємо як виконано
                    completed_schedules.append(name)
                    state["completed_schedules"] = completed_schedules
                    _save_scheduler_state(state)
            
            # Чекаємо 60 сек перед наступною перевіркою
            time.sleep(60)
            
        except Exception as e:
            print(f"[SCHEDULER] Worker error: {e}")
            time.sleep(60)

def start_scheduler(callbacks: dict):
    """
    Запустити scheduler с callback-функціями
    
    Args:
        callbacks: {"morning": func_morning, "lunch": func_lunch, ...}
                  Кожна func(schedule_name, datetime_tz) або просто func()
    """
    global _SCHEDULER_RUNNING, _SCHEDULER_THREADS, _CALLBACK_FUNCTIONS
    
    if _SCHEDULER_RUNNING:
        print("[SCHEDULER] Already running")
        return
    
    _CALLBACK_FUNCTIONS = callbacks
    _SCHEDULER_RUNNING = True
    
    worker = threading.Thread(target=_scheduler_worker, daemon=True)
    worker.start()
    _SCHEDULER_THREADS["worker"] = worker
    
    print("[SCHEDULER] Started with schedules:", list(SCHEDULES.keys()))

def stop_scheduler():
    """Зупинити scheduler"""
    global _SCHEDULER_RUNNING
    _SCHEDULER_RUNNING = False
    print("[SCHEDULER] Stopped")

def is_running():
    """Перевірити чи scheduler запущений"""
    return _SCHEDULER_RUNNING

def get_next_schedule_in():
    """Повернути скільки хвилин до наступного розкладу"""
    now = _get_current_time_tz()
    current_hour = now.hour
    current_minute = now.minute
    
    hours_until_next = _get_hours_until_next_schedule(current_hour)
    minutes_until_next = hours_until_next * 60 - current_minute
    
    return max(0, minutes_until_next)

# ============ DEBUG ============

def get_scheduler_status():
    """Отримати статус scheduler для /diag"""
    now = _get_current_time_tz()
    next_min = get_next_schedule_in()
    
    return {
        "running": is_running(),
        "current_time": now.strftime("%H:%M:%S %Z"),
        "current_date": now.strftime("%Y-%m-%d"),
        "next_schedule_in_minutes": next_min,
        "schedules": list(SCHEDULES.items()),
        "completed_today": _load_scheduler_state().get("completed_schedules", []),
    }

if __name__ == "__main__":
    # TEST
    def test_callback(name, dt):
        print(f"[TEST] Callback {name} at {dt}")
    
    callbacks = {name: test_callback for name in SCHEDULES.keys()}
    start_scheduler(callbacks)
    
    print("Scheduler running for 5 min...")
    time.sleep(300)
    
    stop_scheduler()
    print("Done")
