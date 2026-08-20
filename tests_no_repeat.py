#!/usr/bin/env python3
"""
tests_no_repeat.py — пропозиція не приходить двічі.

Скарга Олега (20.08): «Я вже дав команду не нагадувати про лист від Вероніки і
підтвердив страховку — чому знову приходить???». Три причини були:
  1) блок-лист матчив тільки точну назву, а AI щоразу формулює інакше
     («Відповісти на лист від Вероні» / «...від Вероні щодо весілля»);
  2) не було журналу вже показаних пропозицій — той самий лист давав нову
     картку з новим id;
  3) пропонувались дати в МИНУЛОМУ (страховка «до 15.08», коли вже 20.08).
Тест закриває всі три.
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import ai_kit as K  # noqa: E402
import dismissed as dm  # noqa: E402

FAILS = 0


def ok(cond, msg):
    global FAILS
    if cond:
        print(f"✅ {msg}")
    else:
        FAILS += 1
        print(f"❌ {msg}")


# ── фейковий storage у пам'яті ───────────────────────────────────────────────
MEM = {}
K.load = lambda f, default=None: MEM.get(f, default if default is not None else {})
K.save = lambda f, d: MEM.__setitem__(f, d)


def _upd(f, k, v):
    MEM.setdefault(f, {})[k] = v


K.update_key = _upd
K.remove_key = lambda f, k: MEM.get(f, {}).pop(k, None)


def reset():
    MEM.clear()
    dm._CACHE["data"] = None
    dm._CACHE["ts"] = 0.0
    dm._OCACHE["data"] = None
    dm._OCACHE["ts"] = 0.0


# ── 1. нечіткий збіг назви ───────────────────────────────────────────────────
print("\n1) Блок-лист ловить ту саму тему, названу інакше")
reset()
dm.mute("cal_skip", key="77770", title="Відповісти на лист від Вероні")
ok(dm.is_muted(title="Відповісти на лист від Вероні щодо весілля"),
   "«...щодо весілля» — та сама тема, заглушено")
ok(dm.is_muted(key="77770"), "за ключем також заглушено")
ok(not dm.is_muted(title="Оплатити страховку Kooperativa"),
   "чужа тема НЕ заглушена")

print("\n1b) Схожі, але різні теми не злипаються")
reset()
dm.mute("cal_skip", key="1", title="Оплатити страховку Kooperativa")
ok(dm.is_muted(title="Оплатити страховку Kooperativa о 13:00"),
   "та сама страховка — заглушено")
ok(not dm.is_muted(title="Оплатити страховку квартири Allianz"),
   "інша страховка — НЕ заглушена")

# ── 2. журнал пропозицій ─────────────────────────────────────────────────────
print("\n2) Журнал пропозицій: двічі те саме не показуємо")
reset()
ok(dm.already_offered("Оплатити страховку Kooperativa") == "",
   "перший раз — можна пропонувати")
dm.mark_offered("calendar", key="77761", title="Оплатити страховку Kooperativa")
ok(dm.already_offered("Оплатити страховку Kooperativa") != "",
   "другий раз — не дублюємо")
ok(dm.already_offered("Оплатити страховку Kooperativa до 15.08") != "",
   "інше формулювання тієї ж теми — теж не дублюємо")
ok(dm.already_offered("Записатись до лікаря") == "",
   "інша тема — пропонуємо нормально")
ok(dm.already_offered("щось нове", key="77761") != "",
   "той самий source_id — не дублюємо")

print("\n2b) Натиснута кнопка = вирішено (60 днів)")
reset()
dm.mark_offered("cal_add", key="5", title="Оплатити страховку Kooperativa", decided=True)
r = dm.already_offered("Оплатити страховку Kooperativa")
ok("вирішено" in r, f"позначено як вирішене ({r})")

print("\n2c) Прострочений запис журналу не блокує")
reset()
dm.mark_offered("calendar", key="9", title="Купити квитки на Корфу")
key = dm._okey(dm._norm("Купити квитки на Корфу"))
rec = dict(MEM["offer_log.json"][key])
rec["until"] = "2020-01-01T00:00:00+00:00"
MEM["offer_log.json"][key] = rec
MEM["offer_log.json"].pop(dm._okey("key:9"), None)
dm._OCACHE["data"] = None
ok(dm.already_offered("Купити квитки на Корфу") == "",
   "термін вийшов — можна пропонувати знову")

# ── 3. код: минулі дати + дедуп + фіксація ───────────────────────────────────
print("\n3) monitor.apply_action_suggestion")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")).read()
i = src.find("def apply_action_suggestion")
_end = src.find("\ndef ", i + 10)
body = src[i:_end if _end > 0 else i + 8000]
ok("date.today().isoformat()" in body, "є фільтр дат у минулому")
ok("already_offered" in body, "є перевірка журналу пропозицій")
ok("mark_offered" in body, "після відправки фіксує пропозицію")
ok(body.find("already_offered") < body.find("_send_telegram_text_with_keyboard"),
   "перевірка ДО відправки")

print("\n3b) bot.py: кожне натискання закриває пропозицію")
bsrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
ast.parse(bsrc)
ok("def _close_offer(" in bsrc, "є _close_offer()")
for cb, mute_expected in (("cal_skip", True), ("shop_skip", True),
                          ("calrem_skip", False), ("cal_add", False),
                          ("calrem_add", False), ("shop_add", False)):
    line = [l for l in bsrc.split("\n") if f'_close_offer("{cb}"' in l]
    ok(bool(line), f"{cb} → _close_offer")
    if line:
        ok(("mute=True" in line[0]) == mute_expected,
           f"{cb}: mute={mute_expected}")

print(f"\nfails: {FAILS}")
sys.exit(1 if FAILS else 0)
