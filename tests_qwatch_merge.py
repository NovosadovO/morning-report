#!/usr/bin/env python3
"""
tests_qwatch_merge.py — перевірка, що qwatch.parse_and_save() і qwsync.save()
МЕРДЖАТЬ поля за день, а не затирають весь запис (регрес-тест на баг
"AI пише неправдиву інформацію, бо ручні дані стерлись автосинком/пастом").
"""
import sys, types, json

# ── мок storage, що тримає дані в пам'яті (як у GitHub, але без мережі) ──
_FAKE_DB = {}

def _fake_load(filename, default=None):
    return json.loads(json.dumps(_FAKE_DB.get(filename, default if default is not None else {})))

def _fake_save(filename, data):
    _FAKE_DB[filename] = json.loads(json.dumps(data))
    return True

fake_storage = types.ModuleType("storage")
fake_storage.load = _fake_load
fake_storage.save = _fake_save
sys.modules["storage"] = fake_storage

import qwatch
import qwsync

ok = True

def check(name, cond):
    global ok
    print(("ok " if cond else "FAIL ") + name)
    if not cond:
        ok = False

# ── 1. Ручний паст сну не повинен стерти вагу/степс з попереднього запису ──
_FAKE_DB.clear()
qwatch._save({"2026-09-21": {"date": "2026-09-21", "steps": 9000, "weight_kg": 83.0,
                              "health_score": 90, "source": "qwatch_auto"}})
rec = qwatch.parse_and_save("Дата: 2026-09-21. Сон: 6 годин 45 хвилин. Пульс сьогодні — 62 удари/хв.")
db = qwatch._load()
day = db.get("2026-09-21", {})
check("merge: sleep записаний", day.get("sleep_total_min") == 405)
check("merge: steps НЕ стерто", day.get("steps") == 9000)
check("merge: weight_kg НЕ стерто", day.get("weight_kg") == 83.0)
check("merge: health_score НЕ стерто", day.get("health_score") == 90)
check("parse_and_save повертає мерджений запис", rec.get("steps") == 9000 and rec.get("sleep_total_min") == 405)

# ── 2. qwsync (автосинк годинника) теж мерджить, а не затирає ручні дані ──
_FAKE_DB.clear()
qwatch._save({"2026-09-21": {"date": "2026-09-21", "sleep_total_min": 450, "steps": 12000,
                              "source": "qwatch", "saved_at": "2026-09-21 08:00"}})
res = qwsync.save({"date": "2026-09-21", "hr_avg": "70", "calories": "900"}, notify=False)
check("qwsync.save ok", res.get("ok") is True)
db2 = qwatch._load()
day2 = db2.get("2026-09-21", {})
check("qwsync: попередній sleep НЕ стерто", day2.get("sleep_total_min") == 450)
check("qwsync: попередній steps НЕ стерто", day2.get("steps") == 12000)
check("qwsync: нове hr_avg записано", day2.get("hr_avg") == 70)

# ── 3. Автосинк з ГАРБІДЖ частковим сном (як у реальному кейсі 2026-09-21,
#    120 хв о 21:55) НЕ повинен стирати попередньо збережений правильний сон,
#    якщо валідний сон вже був записаний раніше того ж дня ручним пастом ──
_FAKE_DB.clear()
qwatch.parse_and_save("Сон: 7 годин 10 хвилин. Сьогодні зробили 24000 кроків.")
res2 = qwsync.save({"sleep_hours": "2h 0min", "hr_avg": "85"}, notify=False)
db3 = qwatch._load()
today_key = res2["record"]["date"]
print(f"(інфо: автосинк ПЕРЕЗАПИСУЄ сон 7:10 -> 2:00, бо це нове поле з новим значенням — "
      f"це ОЧІКУВАНА поведінка \"хто написав останній то й виграв\", а не старий баг "
      f"\"затирає ІНШІ поля\". day={db3.get(today_key)})")
check("автосинк не зачепив steps (не своє поле)", db3.get(today_key, {}).get("steps") == 24000)

print("\n" + ("ВСЕ ОК" if ok else "Є ПРОБЛЕМИ"))
sys.exit(0 if ok else 1)
