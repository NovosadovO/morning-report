#!/usr/bin/env python3
"""Тест «розумного» розуміння стану справ на реальних прикладах Олега:
1) поїздка на Корфу вже позаду → жодних нагадувань про неї;
2) страховка оплачена → бот сам каже період дії і пропонує записати
   наступну оплату в календар, а не нагадує «оплати страховку».
"""
import json
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, "/home/user/bot")

import ai_kit as K

# ── фейкові storage / telegram / calendar ────────────────────────────────────
_MEM = {}
K.load = lambda f, default=None: json.loads(json.dumps(_MEM.get(f, default if default is not None else {})))
K.save = lambda f, d: _MEM.__setitem__(f, json.loads(json.dumps(d)))
def _upd(f, k, v):
    _MEM.setdefault(f, {})[k] = json.loads(json.dumps(v))
K.update_key = _upd
K.remove_key = lambda f, k: _MEM.get(f, {}).pop(k, None)

CARDS = []
K.send_card = lambda text, keyboard=None, tag="", chat_id=None: (
    CARDS.append({"text": text, "kb": keyboard}) or True)

CAL = []
K.calendar_event = lambda summary, start_dt, end_dt=None, description="": (
    CAL.append({"summary": summary, "date": start_dt.date().isoformat()}) or {"ok": True})

# фіксована «сьогодні» — тест детермінований
TODAY = date(2026, 8, 20)
class _FakeNow(datetime):
    pass
K.now = lambda: datetime(2026, 8, 20, 12, 0)

import dismissed as D
import lifecycle as L
D._CACHE_TTL = 0

fails = 0
def ok(cond, name):
    global fails
    if cond:
        print(f"✅ {name}")
    else:
        fails += 1
        print(f"❌ {name}")

def kb_data(cards, idx=-1):
    return [b["callback_data"] for row in (cards[idx]["kb"] or []) for b in row]


# ── 1. ПОЇЗДКА НА КОРФУ ВЖЕ ПОЗАДУ ──────────────────────────────────────────
print("\n── 1. Поїздка на Корфу (прилетів сьогодні) ──")
ai_trip = {
    "description": "Дякуємо, що подорожували з нами. Сподіваємось, відпочинок на Корфу минув добре.",
    "state": "completed", "entity": "Поїздка на Корфу", "entity_kind": "trip",
    "valid_from": "2026-08-10", "valid_to": "2026-08-20",
    "next_due": None, "keyword": "Корфу",
    "action_type": "calendar", "action_title": "Підготуватись до поїздки на Корфу",
}
closed = L.from_email_ai(ai_trip, "uid1001", sender="Ryanair", subject="Your trip")
ok(closed is True, "поїздку розпізнано як завершену — звичайна пропозиція не піде")
ok(D.is_muted(title="Поїздка на Корфу"), "нагадування про поїздку заглушені")
ok(D.is_muted(title="🧳 Не забудь спакувати речі на Корфу"),
   "keyword «Корфу» глушить ЛЮБЕ нагадування про поїздку")
ok(not D.is_muted(title="Оплатити інтернет"), "чужі теми не зачепило")
ok(D.is_muted(title="Нагадування: рейс на Корфу"), "keyword ловить інші формулювання")
ok(CARDS and "вже позаду" in CARDS[-1]["text"], "бот сказав, що це вже позаду")
ok("10.08.2026" in CARDS[-1]["text"] and "20.08.2026" in CARDS[-1]["text"],
   "показані реальні дати поїздки з листа")

# ── 2. СТРАХОВКА ОПЛАЧЕНА ───────────────────────────────────────────────────
print("\n── 2. Страховка оплачена ──")
ai_ins = {
    "description": "Платіж отримано. Поліс ПЗВ діє з 01.09.2026 до 31.08.2027.",
    "state": "paid", "entity": "Страховка авто Allianz", "entity_kind": "insurance",
    "valid_from": "2026-09-01", "valid_to": "2027-08-31",
    "next_due": None, "keyword": "страховка авто",
    "action_type": "calendar", "action_title": "Оплатити страховку авто",
}
closed2 = L.from_email_ai(ai_ins, "uid1002", sender="Allianz", subject="Potvrdenie platby")
ok(closed2 is True, "страховку розпізнано як оплачену")
ok(D.is_muted(title="Оплатити страховку авто"),
   "нагадування «оплати страховку» більше не прийде")
ok(D.is_muted(title="Страховку авто продовжити"),
   "keyword працює у відмінках («страховку» ← «страховка авто»)")
ok(not D.is_muted(title="Страховка квартири"),
   "інша страховка НЕ заглушена — keyword вимагає всі слова")
card = CARDS[-1]["text"]
ok("вже оплачено" in card, "бот підтвердив: оплачено")
ok("01.09.2026" in card and "31.08.2027" in card,
   "написав З ЯКОГО ПО ЯКЕ число діє (як просив Олег)")
ok("01.09.2027" in card, "сам вивів дату наступної оплати (день після кінця дії)")

reg = L._reg()
rec = reg.get(L._slug("Страховка авто Allianz")) or {}
ok(rec.get("valid_to") == "2027-08-31" and rec.get("next_due") == "2027-09-01",
   "у реєстрі збережено період дії і наступну оплату")

# кнопка запису
datas = kb_data(CARDS)
ok(any(d.startswith("lc_add_") for d in datas), "є кнопка «Записати все»")
ok(any(d.startswith("lc_skip_") for d in datas), "є кнопка «Не треба»")

pid = [d for d in datas if d.startswith("lc_add_")][0][len("lc_add_"):]
r = L.do_add(pid)
ok(r.get("ok") and r.get("added") == 2, f"записано 2 події (got {r.get('added')})")
dates = sorted(c["date"] for c in CAL)
ok("2027-08-31" in dates, "у календарі: останній день дії")
ok("2027-08-18" in dates, "у календарі: нагадування про оплату за 14 днів до кінця")
ok(any("Оплатити" in c["summary"] for c in CAL), "нагадування названо зрозуміло")
ok(L.do_add(pid).get("error") == "payload_missing", "повторний клік нічого не дублює")

# ── 3. НІЧОГО НЕ ВИГАДУЄМО ──────────────────────────────────────────────────
print("\n── 3. Без дат у листі — жодних вигадок ──")
CARDS.clear()
ai_nodate = {"state": "paid", "entity": "Підписка Netflix", "entity_kind": "subscription",
             "valid_from": None, "valid_to": None, "next_due": None, "keyword": "netflix"}
L.from_email_ai(ai_nodate, "uid1003")
ok("нічого не вигадую" in CARDS[-1]["text"], "прямо сказав, що дат у листі немає")
ok(not any(d.startswith("lc_add_") for d in kb_data(CARDS)),
   "кнопки запису в календар немає — писати нічого")

# ── 4. МАЙБУТНЄ НЕ ЗАКРИВАЄМО ───────────────────────────────────────────────
print("\n── 4. Захист від поспішного AI ──")
ai_future = {"state": "completed", "entity": "Поїздка в Прагу", "entity_kind": "trip",
             "valid_to": "2026-12-01", "keyword": "прага"}
ok(L.from_email_ai(ai_future, "uid1004") is False,
   "подія в майбутньому НЕ закривається, навіть якщо AI сказав completed")
ok(not D.is_muted(title="Поїздка в Прагу"), "нагадування про майбутню поїздку живі")

ai_todo = {"state": "todo", "entity": "Рахунок за газ", "entity_kind": "bill",
           "next_due": "2026-09-05"}
ok(L.from_email_ai(ai_todo, "uid1005") is False, "state=todo не закриває тему")
ok(not D.is_muted(title="Рахунок за газ"), "по неоплаченому нагадування працює")

ok(L.from_email_ai({"state": "paid", "entity": ""}, "uid1006") is False,
   "без назви справи нічого не робимо")
ok(L.from_email_ai({}, "uid1007") is False, "порожній AI-розбір не ламає модуль")

# ── 5. ПОПЕРЕДЖЕННЯ ПРО ЗАКІНЧЕННЯ ──────────────────────────────────────────
print("\n── 5. Попередження заздалегідь ──")
CARDS.clear()
_MEM["lifecycle.json"] = {}
L.remember("Страховка авто Allianz", "insurance", "paid",
           valid_from="2025-09-01", valid_to="2026-08-31", next_due="2026-09-01",
           keyword="страховка авто")
L.remember("Далека справа", "subscription", "paid", valid_to="2027-05-01",
           next_due="2027-05-02")
n = L.check_expiring()
ok(n == 1, f"попередив тільки про те, що близько (got {n})")
ok("Страховка авто Allianz" in CARDS[-1]["text"], "попередження саме про страховку")
ok("31.08.2026" in CARDS[-1]["text"], "у попередженні є дата закінчення")
n2 = L.check_expiring()
ok(n2 == 0, "вдруге те саме не спамить")

# ── 6. ЗВІТ /справи ─────────────────────────────────────────────────────────
print("\n── 6. /справи ──")
rep = L.report()
ok("Страховка авто Allianz" in rep and "31.08.2026" in rep, "звіт показує строк дії")
ok("наступна оплата" in rep, "звіт показує наступну оплату")
_MEM["lifecycle.json"] = {}
ok("Порожньо" in L.report(), "порожній реєстр не падає")

# ── 7. ІНТЕГРАЦІЯ В КОДІ ────────────────────────────────────────────────────
print("\n── 7. Підключення ──")
mon = open("/home/user/bot/monitor.py").read()
bot = open("/home/user/bot/bot.py").read()
loop = open("/home/user/bot/monitor_loop.py").read()
ok('"state": "todo|paid|completed|info"' in mon, "AI-промпт листа питає стан справи")
ok('"valid_to": "YYYY-MM-DD або null"' in mon, "AI-промпт питає період дії")
ok("ЖОДНИХ ВИГАДАНИХ ДАТ" in mon, "промпт забороняє вигадувати дати")
ok("import lifecycle as _lc_em" in mon, "monitor кличе lifecycle на кожен лист")
ok("if not _closed:" in mon, "закрита тема не породжує нагадування «оплати»")
# головний баг: виклик мусить бути В ЦИКЛІ по листах, а не в except-і
import ast as _ast
_tree = _ast.parse(mon)
_in_loop = False
for _fn in _ast.walk(_tree):
    if isinstance(_fn, _ast.FunctionDef) and _fn.name == "check_new_emails":
        for _n in _ast.walk(_fn):
            if not isinstance(_n, _ast.For):
                continue
            for _c in _ast.walk(_n):
                if (isinstance(_c, _ast.Call)
                        and getattr(_c.func, "id", "") == "apply_action_suggestion"):
                    # і не всередині except-обробника
                    _bad = any(isinstance(_h, _ast.ExceptHandler)
                               and _h.lineno <= _c.lineno <= (_h.end_lineno or 0)
                               for _h in _ast.walk(_n))
                    _in_loop = not _bad
ok(_in_loop, "apply_action_suggestion тепер у циклі листів, а не в except")
ok('data.startswith("lc_")' in bot, "кнопки lc_* маршрутизуються")
ok('"lc_add_":' in bot, "запис у календар під підтвердженням")
ok("/справи" in bot, "команда /справи є")
ok("_lc_w.check_expiring()" in loop, "щоденна перевірка термінів у воркері")

print(f"\nfails: {fails}")
