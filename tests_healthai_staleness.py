#!/usr/bin/env python3
"""
tests_healthai_staleness.py — регрес-тест на баг "AI пише неправдиву
інформацію": сьогодні прийшли СВІЖІ дані (степс/пульс/HRV), але без сну —
і healthai мовчки брав сон з попереднього дня і подавав як сьогоднішній
("ти спав 2.0 год за 22 вересня", хоча ці 2.0 год були за 21-ше).

Перевіряємо:
1. analytics() позначає days_ago/last_day окремо для КОЖНОГО показника.
2. facts_block() явно маркує застарілий показник, коли інші сьогоднішні.
3. anomalies() генерує окрему картку "{field}_field_stale" саме для цього
   випадку (сьогодні є дані, але саме цього показника нема).
4. Коли всі показники сьогоднішні — жодних маркерів/аномалій.
"""
import sys, types, json
from datetime import datetime, timedelta

# ── мок ai_kit (без мережі, без Telegram) ──
_STATE = {}

class _FakeK(types.ModuleType):
    pass

fake_k = types.ModuleType("ai_kit")
def _now():
    return datetime(2026, 9, 22, 14, 5, 0)
fake_k.now = _now
fake_k.load = lambda f, default=None: _STATE.get(f, default if default is not None else {})
fake_k.save = lambda f, d: _STATE.__setitem__(f, d)
fake_k.log = lambda tag, msg: None
fake_k.rate_ok = lambda *a, **k: True
fake_k.rate_mark = lambda *a, **k: None
fake_k.send_card = lambda *a, **k: True
fake_k.gemini_text = lambda *a, **k: ""
fake_k.classify_shift = lambda *a, **k: "free"
fake_k.events_for_day = lambda *a, **k: []
sys.modules["ai_kit"] = fake_k

# ── мок storage.load_health() — точно як у реальному кейсі 21-22.09.2026 ──
_HEALTH_FIXTURE = {
    "2026-09-21": {"hr_avg": 85, "calories": 502, "weight_kg": 83.0,
                   "sleep_total_min": 120, "sleep_hours": 2.0, "steps": 9033,
                   "health_score": 95},
    "2026-09-22": {"steps": 12035, "hr_avg": 81, "calories": 576, "hrv": 30,
                   "weight_kg": 83.0, "spo2": 99},   # <- НЕМАЄ sleep_hours!
}

fake_storage = types.ModuleType("storage")
fake_storage.load_health = lambda: json.loads(json.dumps(_HEALTH_FIXTURE))
sys.modules["storage"] = fake_storage

import healthai

ok = True
def check(name, cond, extra=""):
    global ok
    print(("ok   " if cond else "FAIL ") + name + (f"  ({extra})" if extra and not cond else ""))
    if not cond:
        ok = False

a = healthai.analytics(30)

print("\n1. analytics() — sleep вказує на 21-ше з days_ago=1, steps на 22-ге з days_ago=0")
check("today встановлено правильно", a.get("today") == "2026-09-22")
check("sleep.last_day = 21-ше", a["sleep"]["last_day"] == "2026-09-21")
check("sleep.days_ago = 1", a["sleep"]["days_ago"] == 1, str(a["sleep"]))
check("sleep.last = 2.0 (правильне число, просто старе)", a["sleep"]["last"] == 2.0)
check("steps.last_day = 22-ге (сьогодні)", a["steps"]["last_day"] == "2026-09-22")
check("steps.days_ago = 0", a["steps"]["days_ago"] == 0)
check("hrv.days_ago = 0 (сьогодні є)", a["hrv"]["days_ago"] == 0)

print("\n2. facts_block() — сон явно позначений як старі дані, кроки без позначки")
fb = healthai.facts_block(a)
print(fb)
check("є явний маркер СТАРІ ДАНІ для сну", "СТАРІ ДАНІ за 2026-09-21" in fb and "Сон:" in fb)
check("сон з поміткою '1 дн. тому'", "1 дн. тому" in fb)
check("кроки НЕ позначені як старі (вони за сьогодні)",
      "Кроки: 12035" in fb and "Кроки: 12035 [!!СТАРІ" not in fb)

print("\n3. anomalies() — окрема картка sleep_field_stale, steps БЕЗ такої картки")
anomalies = healthai.anomalies(a)
keys = [k for k, _ in anomalies]
check("sleep_field_stale присутній", "sleep_field_stale" in keys, str(keys))
check("steps_field_stale ВІДСУТНІЙ (кроки свіжі)", "steps_field_stale" not in keys)
sleep_line = next((l for k, l in anomalies if k == "sleep_field_stale"), "")
check("текст картки згадує дату 2026-09-21 і що сьогоднішній сон ще не приходив",
      "2026-09-21" in sleep_line and "1 дн" in sleep_line, sleep_line)

print("\n4. Коли сьогодні ВСІ показники свіжі — жодних *_field_stale і жодних маркерів")
_HEALTH_FIXTURE_FRESH = {
    "2026-09-22": {"steps": 12035, "hr_avg": 81, "calories": 576, "hrv": 30,
                   "weight_kg": 83.0, "spo2": 99, "sleep_hours": 7.2},
}
fake_storage.load_health = lambda: json.loads(json.dumps(_HEALTH_FIXTURE_FRESH))
a2 = healthai.analytics(30)
fb2 = healthai.facts_block(a2)
check("немає маркера СТАРІ ДАНІ, коли все свіже", "СТАРІ ДАНІ" not in fb2)
an2 = healthai.anomalies(a2)
keys2 = [k for k, _ in an2]
check("жодного *_field_stale, коли все свіже", not any(k.endswith("_field_stale") for k in keys2), str(keys2))

print("\n" + ("ВСЕ ОК" if ok else "Є ПРОБЛЕМИ"))
sys.exit(0 if ok else 1)
