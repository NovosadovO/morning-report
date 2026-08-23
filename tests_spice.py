#!/usr/bin/env python3
"""tests_spice.py — нагадування мусять бути цікаві й ефективні, а не шаблонні.

Скарга Олега: «Текст сповіщення звітів нагадування якийсь простий.
Зроби його цікавим, ефективнішим».
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []


def ok(cond, name, extra=""):
    if cond:
        print("✅ " + name)
    else:
        print("❌ " + name + ((" — " + str(extra)) if extra else ""))
        fails.append(name)


import spice as S

print("\n─── 1. хвіст має ставку і крок ───")
t = S.tail("date", 3, key="olya")
ok(bool(t), "хвіст непорожній")
ok("➡️" in t, "є конкретний крок")
ok(len(t.splitlines()) >= 3, "щонайменше 3 змістові рядки", len(t.splitlines()))
ok("3 дн" in t, "терміновість із реальним числом днів", t.splitlines()[0])

print("\n─── 2. різні пороги → різний текст ───")
texts = [S.tail("date", d, key="olya") for d in (7, 3, 1, 0)]
ok(len(set(texts)) == 4, "7/3/1/0 днів дають 4 різні тексти", len(set(texts)))
ok("Сьогодні" in texts[3], "нульовий день звучить терміново", texts[3][:40])
ok("Завтра" in texts[2], "один день — «завтра»", texts[2][:40])

print("\n─── 3. різні події → різні формулювання ───")
per_event = set()
for name in ["olya", "mama", "maros", "petro", "ivan", "kolya"]:
    per_event.add(S.tail("date", 7, key=name))
ok(len(per_event) >= 3, "ротація по події працює", len(per_event))

print("\n─── 4. типи нагадувань мають свій зміст ───")
for kind in ("date", "bill", "deadline", "sub"):
    tt = S.tail(kind, 3, {"amount": 10, "cycle": "monthly"}, key="k")
    ok(bool(tt) and "➡️" in tt, "тип " + kind + " дає повний хвіст")
ok(S.tail("невідомий_тип", 3) == "", "невідомий тип → порожньо, без сміття")

print("\n─── 5. гроші: рік замість місяця ───")
ml = S.money_line(12.99, "monthly")
ok("155" in ml or "156" in ml, "12.99/міс → ~156€ на рік", ml)
ok("на рік" in ml, "формулюємо через рік — так рішення очевидніше")
ok(S.money_line(None) == "", "немає суми → нічого не вигадуємо")
ok(S.money_line(0) == "", "нуль не малюємо")
ok(S.money_line("сміття") == "", "сміття не ламає")
sub = S.tail("sub", 2, {"amount": 12.99, "cycle": "monthly"}, key="netflix")
ok("€ на рік" in sub, "у картці підписки видно річну суму")
sub2 = S.tail("sub", 2, {}, key="netflix")
ok("€ на рік" not in sub2, "без суми річний рядок не з'являється")

print("\n─── 6. терміновість ───")
ok("Сьогодні" in S.urgency(0), "0 днів")
ok("Завтра" in S.urgency(1), "1 день")
ok("2 дн" in S.urgency(2), "2 дні показують число", S.urgency(2))
ok(S.urgency(None) == "", "None → порожньо")
ok(S.urgency("абв") == "", "сміття → порожньо")
ok(bool(S.urgency(30)), "далекий строк теж має рядок", S.urgency(30))

print("\n─── 7. стабільність у межах дня (антидубль) ───")
a = S.tail("bill", 3, key="orange")
b = S.tail("bill", 3, key="orange")
ok(a == b, "той самий виклик двічі → той самий текст (дубль не «мутує»)")

print("\n─── 8. без порожньої мотивації ───")
banned = ["тримай темп", "все під контролем", "гарного дня", "успіхів",
          "продовжуй у тому ж дусі", "не забувай про себе"]
allt = " ".join(S.tail(k, d, {"amount": 9.99, "cycle": "monthly"}, key=str(i))
                for i, k in enumerate(("date", "bill", "deadline", "sub"))
                for d in (7, 3, 1, 0)).lower()
for b_ in banned:
    ok(b_ not in allt, "немає порожньої фрази «" + b_ + "»")

print("\n─── 9. підключення в модулях ───")
here = os.path.dirname(os.path.abspath(__file__))
for mod, kind in (("dates_book.py", '"date"'), ("subs_watcher.py", '"sub"'),
                  ("bills_watcher.py", '"bill"'),
                  ("deadlines_watcher.py", '"deadline"')):
    src = open(os.path.join(here, mod)).read()
    ok("spice" in src, mod + ": spice імпортовано")
    ok(kind in src, mod + ": передає правильний тип " + kind)

ds = open(os.path.join(here, "dates_book.py")).read()
ok("Є час підготуватись без спіху" not in ds,
   "dates_book: статичний хвіст прибрано")
ok("Останній момент купити подарунок" not in ds,
   "dates_book: другий статичний хвіст прибрано")
ss = open(os.path.join(here, "subs_watcher.py")).read()
ok(ss.count("Якщо не користуєшся — скасувати треба ДО списання") <= 1,
   "subs: статичний рядок лишився лише як fallback")
bs = open(os.path.join(here, "bills_watcher.py")).read()
ok("_days_left(" not in bs, "bills: немає виклику неіснуючої функції")
ok('_sp.tail("bill", days_left' in bs, "bills: використана реальна змінна days_left")

print("\n─── 10. _head у dates_book збирається правильно ───")
import types


class _K:
    @staticmethod
    def esc(s):
        return str(s or "")

    @staticmethod
    def log(*a, **k):
        pass


import importlib
db = importlib.import_module("dates_book")
db.K = _K
from datetime import datetime as _dt

item = {"rec": {"name": "Оля", "kind": "birthday"}, "days_left": 3,
        "when": _dt(2026, 9, 1), "age": None}
h = db._head(item)
ok("Оля" in h, "ім'я в картці")
ok("ЧЕРЕЗ 3 ДН" in h, "заголовок із залишком днів")
ok("➡️" in h, "у картці є конкретний крок")
ok("Є час підготуватись без спіху" not in h, "мертвого рядка немає")
item0 = dict(item, days_left=0)
h0 = db._head(item0)
ok("СЬОГОДНІ" in h0, "нульовий день — свій заголовок")
ok(h0 != h, "тексти на різних порогах відрізняються")

print("\n" + "=" * 40)
print("fails: " + str(len(fails)))
for f in fails:
    print("  ❌ " + f)
