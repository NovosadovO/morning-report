#!/usr/bin/env python3
"""tests_nowctx.py — час, день, локація. Головне: «вдома» більше НЕ вигадується."""
import json
import os
import sys
import types
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = [0]


def _h(t):
    print("\n── " + t)


def _ck(name, cond):
    if cond:
        print("✅ " + name)
    else:
        print("❌ " + name)
        FAILS[0] += 1


# ─── заглушка storage ────────────────────────────────────────────────────────
_MEM = {}


class _FakeStorage(types.ModuleType):
    def load(self, name, default=None):
        return _MEM.get(name, default if default is not None else {})

    def save(self, name, data):
        _MEM[name] = data
        return True


sys.modules["storage"] = _FakeStorage("storage")

import nowctx as N  # noqa: E402


_h("БЛОК 1: час і дата")
n = N.now()
_ck("now() дає час із таймзоною", n.tzinfo is not None)
st = N.stamp()
_ck("stamp() містить рік", "2026" in st)
_ck("stamp() містить день тижня українською",
    any(w in st for w in N.WD))
_ck("stamp() містить місяць українською", any(m in st for m in N.MON))
_ck("stamp() містить час HH:MM", ":" in st.split(", ")[-1])
_ck("part_of_day: 3 год = глибока ніч", N.part_of_day(3) == "глибока ніч")
_ck("part_of_day: 10 год = ранок", N.part_of_day(10) == "ранок")
_ck("part_of_day: 20 год = вечір", N.part_of_day(20) == "вечір")


_h("БЛОК 2: ручна локація")
_MEM.clear()
_ck("set_manual('robota')", N.set_manual("robota") is True)
w = N.where()
_ck("після /робота state=work", w["state"] == "work")
_ck("label каже НА РОБОТІ", "НА РОБОТІ" in w["label"])
_ck("джерело — сам Олег", "сам" in w["source"])
_ck("set_manual('дома') приймає українською", N.set_manual("дома") is True)
_ck("після /дома state=home", N.where()["state"] == "home")
_ck("сміття не приймається", N.set_manual("хтозна") is False)


_h("БЛОК 3: протермінована ручна позначка не діє")
_MEM.clear()
old_ts = (N.now() - timedelta(hours=N.MANUAL_TTL_H + 2)).isoformat(timespec="seconds")
_MEM["whereami.json"] = {"loc": "doma", "ts": old_ts}
_ck("стара позначка ігнорується", N._manual_fresh() is None)


_h("БЛОК 4: ГОЛОВНЕ — без даних НЕ пишемо «вдома»")
_MEM.clear()
# калібруємо: календар недоступний (немає токена в тестах)
w = N.where()
_ck("без календаря і без позначки state=unknown", w["state"] == "unknown")
_ck("label не стверджує «вдома»", "вдома" not in w["label"].lower())
_ck("label каже НЕВІДОМО", "НЕВІДОМО" in w["label"])
_ck("sure=False", w["sure"] is False)
b = N.block()
_ck("у промпті заборона писати «ти вдома»", "ЗАБОРОНЕНО" in b and "вдома" in b)
_ck("у промпті пропозиція спитати", "на зміні чи вдома" in b)


_h("БЛОК 5: блок промпту завжди має час і місце")
_ck("блок містить дату й час", "Дата й час:" in b)
_ck("блок містить Кошице", "Кошице" in b)
_ck("блок містить «Де Олег»", "Де Олег:" in b)
_ck("блок містить джерело", "Джерело:" in b)
_ck("блок вимагає рахувати час буквально", "буквально" in b)


_h("БЛОК 6: на роботі — заборона про вільний час")
_MEM.clear()
N.set_manual("robota")
b2 = N.block()
_ck("блок каже ФІЗИЧНО НА РОБОТІ", "ФІЗИЧНО НА РОБОТІ" in b2)
_ck("заборонено «вільний ранок»", "вільний" in b2 and "ЗАБОРОНЕНО" in b2)
_ck("згадує перерву або після зміни", "перерв" in b2 or "після зміни" in b2)


_h("БЛОК 7: інжект у тіло запиту")
_MEM.clear()
body = json.dumps({"contents": [{"parts": [{"text": "Напиши аналіз дня"}]}]}).encode()
out = N.inject(body, "personal_ai")
txt = json.loads(out.decode())["contents"][0]["parts"][0]["text"]
_ck("інжект додав блок", "ЗАРАЗ (єдине джерело правди" in txt)
_ck("інжект поставив маркер", N.MARK in txt)
out2 = N.inject(out, "personal_ai")
txt2 = json.loads(out2.decode())["contents"][0]["parts"][0]["text"]
_ck("ідемпотентність: другий інжект нічого не додав", txt2 == txt)

jbody = json.dumps({"contents": [{"parts": [
    {"text": "Поверни тільки валідний JSON: {\"action_type\": \"x\"}"}]}]}).encode()
jout = N.inject(jbody, "action_detect")
_ck("JSON-промпт не чіпається", jout == jbody)

_ck("зламане тіло не валить інжект", N.inject(b"not json", "t") == b"not json")


_h("БЛОК 8: report() для /де")
_MEM.clear()
r = N.report()
_ck("report містить час", ":" in r)
_ck("report попереджає про невідому локацію", "вигадувати" in r)
N.set_manual("robota")
r2 = N.report()
_ck("report показує ручну позначку", "ручна позначка" in r2)


print("\n" + "=" * 50)
print("fails: " + str(FAILS[0]))
