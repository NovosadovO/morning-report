#!/usr/bin/env python3
"""tests_ai_brain.py — офлайн-тести єдиного мозку (пам'ять + свобода).

Нічого не пише в GitHub: storage.load/save підмінюються на локальний словник.
Запуск: TELEGRAM_TOKEN=x TELEGRAM_CHAT_ID=1 python3 -u tests_ai_brain.py
"""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

FAILS = []


def ok(cond, name, extra=""):
    if cond:
        print(f"✅ {name}")
    else:
        print(f"❌ {name} {extra}")
        FAILS.append(name)


# ─── ізолюємо storage ────────────────────────────────────────────────────────
import storage  # noqa: E402

_FAKE = {}


def _fake_load(name, default=None):
    return _FAKE.get(name, default if default is not None else {})


def _fake_save(name, data, *a, **k):
    _FAKE[name] = data
    return True


storage.load = _fake_load
storage.save = _fake_save

import ai_brain as B  # noqa: E402

B._FLUSH_GAP = 0  # у тестах пишемо одразу


def reset():
    _FAKE.clear()
    B._CACHE.update({"ts": None, "text": ""})
    B._BUF.clear()
    B._BUF_TS[0] = 0.0


# ─── 1. remember_answer ──────────────────────────────────────────────────────
reset()
ok(B.remember_answer("вільне повідомлення", "Я на нічній зміні, розбуди о 17:30",
                     topic="чат") is True, "remember_answer: записує")
ok(len(_FAKE.get(B.ANSWERS_FILE, {})) == 1, "remember_answer: 1 запис у storage")
ok(B.remember_answer("q", "") is False, "remember_answer: порожню відповідь не пише")
ok(B.remember_answer("q", "a") is False, "remember_answer: 1 символ не пише")

blk = B._answers_block()
ok(any("нічній зміні" in x for x in blk), "answers_block: містить відповідь Олега")

# обрізання до 120 записів
reset()
for i in range(130):
    B.remember_answer("q", f"відповідь номер {i}", topic="чат")
ok(len(_FAKE.get(B.ANSWERS_FILE, {})) <= 120, "remember_answer: тримає максимум 120",
   f"({len(_FAKE.get(B.ANSWERS_FILE, {}))})")

# ─── 2. note_topic + буферизація ─────────────────────────────────────────────
reset()
ok(B.note_topic("themes_ai", "про вагу і біг") is True, "note_topic: записує")
ok(len(_FAKE.get(B.TOPICS_FILE, {})) == 1, "note_topic: 1 запис")
tp = B._recent_topics()
ok(any("themes_ai" in x for x in tp), "recent_topics: тема видима")

reset()
B._FLUSH_GAP = 999
B.note_topic("a", "1")   # перший — пише (BUF_TS=0)
n_after_first = len(_FAKE.get(B.TOPICS_FILE, {}))
B.note_topic("b", "2")   # другий — тільки в буфер
B.note_topic("c", "3")
ok(n_after_first == 1 and len(_FAKE.get(B.TOPICS_FILE, {})) == 1,
   "note_topic: буферизує, не пише на кожен виклик",
   f"(after_first={n_after_first}, now={len(_FAKE.get(B.TOPICS_FILE, {}))})")
ok(len(B._BUF) == 2, "note_topic: буфер тримає незаписані", f"({len(B._BUF)})")
B.note_topic("d", "4", force=True)
ok(len(_FAKE.get(B.TOPICS_FILE, {})) == 4 and len(B._BUF) == 0,
   "note_topic: force=True скидає весь буфер",
   f"({len(_FAKE.get(B.TOPICS_FILE, {}))}, buf={len(B._BUF)})")
B._FLUSH_GAP = 0

reset()
for i in range(70):
    B.note_topic(f"t{i}", "g")
ok(len(_FAKE.get(B.TOPICS_FILE, {})) <= 60, "note_topic: тримає максимум 60",
   f"({len(_FAKE.get(B.TOPICS_FILE, {}))})")

# ─── 3. memory_block ─────────────────────────────────────────────────────────
reset()
m0 = B.memory_block()
ok(isinstance(m0, str), "memory_block: повертає рядок")

reset()
B.remember_answer("вільне повідомлення", "Хочу схуднути до 78 кг до жовтня", topic="чат")
B._CACHE.update({"ts": None, "text": ""})
m1 = B.memory_block()
ok("78 кг" in m1, "memory_block: містить свіжу відповідь Олега")
ok("ПАМ'ЯТЬ ПРО ОЛЕГА" in m1, "memory_block: має заголовок")
ok(len(m1) <= B.MEM_MAX, f"memory_block: ліміт MEM_MAX ({len(m1)})")

# ліміт при великому обсязі
reset()
for i in range(40):
    B.remember_answer("q", "довга відповідь Олега " * 20 + str(i), topic="чат")
B._CACHE.update({"ts": None, "text": ""})
m2 = B.memory_block()
ok(len(m2) <= B.MEM_MAX, f"memory_block: ліміт тримається на великому обсязі ({len(m2)})")

# кеш
reset()
B.remember_answer("q", "перша відповідь для кешу", topic="чат")
B._CACHE.update({"ts": None, "text": ""})
c1 = B.memory_block()
_FAKE.clear()  # дані зникли — але кеш ще живий
c2 = B.memory_block()
ok(c1 == c2 and c2 != "", "memory_block: кеш працює 120 с")
ok(B.remember_answer("q", "нова відповідь скидає кеш", topic="чат") is True
   and B._CACHE["ts"] is None, "remember_answer: інвалідує кеш")

# ─── 4. wrap / ідемпотентність ───────────────────────────────────────────────
reset()
B.remember_answer("q", "тестова відповідь для wrap", topic="чат")
B._CACHE.update({"ts": None, "text": ""})
p = "Напиши звіт для Олега."
w1 = B.wrap(p)
ok(w1.startswith(p), "wrap: оригінальний промпт на початку")
ok(B.MARK in w1, "wrap: ставить маркер")
ok("ТВОЯ СВОБОДА" in w1, "wrap: додає блок свободи")
ok("тестова відповідь для wrap" in w1, "wrap: додає пам'ять")
w2 = B.wrap(w1)
ok(w2 == w1, "wrap: ІДЕМПОТЕНТНО (двічі не інжектить)")
ok(w1.count(B.MARK) == 1, "wrap: маркер один раз")
ok(w1.count("ТВОЯ СВОБОДА") == 1, "wrap: свобода один раз")

w3 = B.wrap(p, freedom=False)
ok("ТВОЯ СВОБОДА" not in w3, "wrap: freedom=False не додає свободу")
w4 = B.wrap(p, memory=False)
ok("ПАМ'ЯТЬ ПРО ОЛЕГА" not in w4, "wrap: memory=False не додає пам'ять")

reset()  # пусто + без свободи → промпт не змінюється
ok(B.wrap(p, freedom=False) == p, "wrap: без даних і свободи промпт не змінений")

# ─── 5. is_json_prompt ───────────────────────────────────────────────────────
for s in ["Поверни ТІЛЬКИ валідний JSON", "відповідь — json", "JSON-масив об'єктів",
          "Тільки JSON, без пояснень"]:
    ok(B.is_json_prompt(s) is True, f"is_json_prompt: True для «{s[:28]}»")
for s in ["Напиши теплий звіт українською", "Проаналізуй лист від Michaela",
          "Дай пораду про біг"]:
    ok(B.is_json_prompt(s) is False, f"is_json_prompt: False для «{s[:28]}»")

reset()
jp = "Поверни ТІЛЬКИ валідний JSON зі списком подій."
wj = B.wrap(jp, freedom=not B.is_json_prompt(jp))
ok("ТВОЯ СВОБОДА" not in wj, "JSON-промпт не отримує блок свободи")

# ─── 6. report ───────────────────────────────────────────────────────────────
reset()
r0 = B.report()
ok("ПАМ'ЯТЬ AI" in r0, "report: працює на порожній пам'яті")
B.remember_answer("q", "звітна відповідь Олега", topic="чат")
B._CACHE.update({"ts": None, "text": ""})
r1 = B.report()
ok("звітна відповідь Олега" in r1, "report: показує відповідь")
ok("<b>" in r1, "report: HTML-розмітка")
ok(len(r1) < 4200, f"report: не перевищує ліміт Telegram ({len(r1)})")

# ─── 7. інжект у monitor._gem_post ───────────────────────────────────────────
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")).read()
seg = src.split("def _gem_post(")[1].split("\ndef ")[0]
ok("import ai_brain as _brain" in seg, "monitor._gem_post: інжект мозку присутній")
ok("_brain.wrap(" in seg, "monitor._gem_post: кличе wrap()")
ok("is_json_prompt" in seg, "monitor._gem_post: враховує JSON-промпти")
ok("_brain.MARK not in _pp" in seg, "monitor._gem_post: перевірка ідемпотентності")
ok("note_topic(tag" in seg, "monitor._gem_post: фіксує тему після відповіді")
i_bill = seg.find("_gem_billing_dead()")
i_inj = seg.find("import ai_brain as _brain")
ok(0 < i_bill < i_inj, "monitor._gem_post: інжект ПІСЛЯ перевірки білінгу")
ok(seg.find("режим сну") < i_inj, "monitor._gem_post: інжект ПІСЛЯ quiet-guard")

# ─── 8. хуки в bot.py ────────────────────────────────────────────────────────
bsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
ok("remember_answer(" in bsrc, "bot.py: записує відповіді Олега")
ok("ai_brain" in bsrc and "report()" in bsrc, "bot.py: команда /мозок")
ok(re.search(r'"/?мозок"|мозок', bsrc) is not None, "bot.py: тригер «мозок» є")

print(f"\nfails: {len(FAILS)}")
if FAILS:
    for f in FAILS:
        print(f"  - {f}")
