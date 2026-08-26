#!/usr/bin/env python3
"""Офлайн-тести recall.py — повна пам'ять переписки."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = []


def ok(cond, name):
    if cond:
        print("  ok  " + name)
    else:
        print("  FAIL " + name)
        FAILS.append(name)


_ab = types.ModuleType("ai_brain")
_ANS = []
_ab.remember_answer = lambda q, a, topic="", source="": _ANS.append((q, a, source))
sys.modules["ai_brain"] = _ab

import recall as R

STORE = {}
R.K.load = lambda fn, default=None: __import__("copy").deepcopy(STORE.get(fn, default))
R.K.save = lambda fn, data: STORE.__setitem__(fn, __import__("copy").deepcopy(data))


def reset():
    STORE.clear()
    _ANS.clear()


print("1. порожній журнал")
reset()
ok(R.load() == [], "load → []")
ok(R.block() == "", "block порожній")
ok("порожній" in R.report(), "report каже прямо")

print("2. повідомлення й команди")
reset()
R.log_message("Я на нічній зміні до 6 ранку")
R.log_message("/звіт", kind="cmd")
items = R.load()
ok(len(items) == 2, "два записи")
ok(items[0]["kind"] == "msg" and items[1]["kind"] == "cmd", "тип повідомлення розрізняється")
ok(R.log_message("") is None and len(R.load()) == 2, "порожнє не пишеться")

print("3. кнопки")
reset()
R.log_button("rx_done_1", "✅ Зроблено")
b = R.load()[0]
ok(b["t"] == "btn" and b["label"] == "✅ Зроблено", "кнопка з назвою збережена")
ok("[кнопка]" in R.block(), "кнопка видна в промпті")

print("4. відповіді + дубль у ai_brain")
reset()
R.log_answer("Як спалось?", "Погано, 4 години", topic="здоров'я")
ok(R.load()[0]["t"] == "ans", "відповідь збережена")
ok(len(_ANS) == 1 and _ANS[0][2] == "recall", "продубльовано в ai_brain")

print("5. блок для промпту")
reset()
for i in range(40):
    R.log_message("повідомлення номер " + str(i))
blk = R.block()
ok("повідомлення номер 39" in blk, "найновіше в блоці")
ok("РАНІШЕ ВІН ТАКОЖ КАЗАВ" in blk, "старіше зведено")
ok(len(blk) <= R.MAX_CHARS, "ліміт символів дотримано")

print("6. ліміт журналу")
reset()
for i in range(R.KEEP + 50):
    R.log_message("m" + str(i))
ok(len(R.load()) == R.KEEP, "тримає рівно KEEP записів")
ok(R.load()[-1]["text"] == "m" + str(R.KEEP + 49), "останнє не втрачено")

print("7. report")
reset()
R.log_message("текст")
R.log_button("rx_ok", "ок")
R.log_answer("q", "a")
rep = R.report()
ok("повідомлень 1" in rep and "кнопок 1" in rep and "відповідей 1" in rep, "підрахунок у report")

print()
print("FAILS:", len(FAILS))
if FAILS:
    for f in FAILS:
        print(" -", f)
    sys.exit(1)
