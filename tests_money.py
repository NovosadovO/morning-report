#!/usr/bin/env python3
"""Офлайн-тести money.py — без мережі, без Telegram, без Gemini."""
import sys
import types
from datetime import datetime, timedelta

import ai_kit as K

# ─── заглушки ────────────────────────────────────────────────────────────────
_FS = {}
_CARDS = []
_EVENTS = []
_RATE = {}

K.load = lambda f, default=None: _FS.get(f, default if default is not None else {})
K.save = lambda f, d: _FS.__setitem__(f, d)


def _upd(f, k, v):
    _FS.setdefault(f, {})[k] = v


def _rm(f, k):
    _FS.get(f, {}).pop(k, None)


K.update_key = _upd
K.remove_key = _rm
K.send_card = lambda text, kb=None, tag="", chat_id=None: (_CARDS.append((text, kb)) or True)
K.calendar_event = lambda s, sd, ed=None, description="": (_EVENTS.append((s, sd)) or {"ok": True})
K.rate_ok = lambda f, m: not _RATE.get(f)
K.rate_mark = lambda f: _RATE.__setitem__(f, True)

sys.modules["dismissed"] = types.SimpleNamespace(
    is_muted=lambda *a, **k: False, mute=lambda *a, **k: {"ok": True})
sys.modules["react"] = types.SimpleNamespace(
    is_closed=lambda *a, **k: False,
    keyboard=lambda kind="generic", key="", title="", **k: [[{"text": "ok",
                                                             "callback_data": "x"}]])

import money as M  # noqa: E402

M._store = K.PayloadStore(M.STORE_FILE)
M._dedup = K.Dedup(M.SENT_FILE, ttl_days=20)
M._ev_dedup = K.Dedup(M.EVENT_STATE, ttl_days=45)

fails = []


def ok(cond, name):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name}")
        fails.append(name)


def reset():
    _FS.clear()
    _CARDS.clear()
    _EVENTS.clear()
    _RATE.clear()


TODAY = K.now().date()
MONTH = TODAY.strftime("%Y-%m")


def d(offset):
    return (TODAY + timedelta(days=offset)).strftime("%Y-%m-%d")


print("\n1) Немає даних — модуль мовчить")
reset()
p = M.picture()
ok(not p["has_data"], "has_data=False на порожньому")
ok(M.line() == "", "line() порожній")
ok("Поки нічого достовірного" in M.report(), "report() каже прямо, що даних немає")

print("\n2) Запис фактів списання")
reset()
cid = M._record({"vendor": "Runable", "amount": "10.00", "currency": "USD",
                 "date": d(-1), "kind": "sub", "note": "тест"})
ok(bool(cid), "валідне списання записано")
ok(M._record({"vendor": "Runable", "amount": "10.00", "currency": "USD",
              "date": d(-1)}) == "", "дубль не пишеться вдруге")
ok(M._record({"vendor": "X", "amount": "5", "date": "хтозна"}) == "",
   "без достовірної дати не пишеться")
ok(M._record({"vendor": "", "amount": "5", "date": d(0)}) == "",
   "без vendor не пишеться")
ok(M._record({"vendor": "Y", "amount": "", "date": d(0)}) == "",
   "без суми не пишеться")

print("\n3) Валюти не змішуються")
reset()
M._record({"vendor": "A", "amount": "10", "currency": "EUR", "date": d(-2)})
M._record({"vendor": "B", "amount": "7.50", "currency": "USD", "date": d(-3)})
s = M.spent()
ok(s["eur"] == 10.0, f"EUR окремо: {s['eur']}")
ok(s["other"].get("USD") == 7.5, f"USD окремо: {s['other']}")
ok(s["count"] == 2, "обидва платежі в count")

print("\n4) Неоплачені рахунки й прострочка")
reset()
sys.modules["bills_watcher"] = types.SimpleNamespace(load_bills=lambda: {
    "b1": {"vendor": "Innogy", "amount": "95.00", "currency": "EUR", "due": d(3)},
    "b2": {"vendor": "Старий", "amount": "20.00", "currency": "EUR", "due": d(-5)},
    "b3": {"vendor": "Оплачений", "amount": "50.00", "due": d(1), "paid": True},
})
sys.modules["subs_watcher"] = types.SimpleNamespace(monthly_total=lambda: {
    "month": 24.0, "year": 288.0, "count": 2,
    "items": [{"vendor": "Netflix", "amount": 12.0, "cycle": "monthly",
               "currency": "EUR", "next_due": d(5)},
              {"vendor": "Spotify", "amount": 12.0, "cycle": "monthly",
               "currency": "EUR", "next_due": ""}]})
unpaid = M._unpaid_bills()
ok(len(unpaid) == 2, f"оплачений відкинуто (лишилось {len(unpaid)})")
ok(len(M.overdue()) == 1, "одна прострочка")
ups = M.upcoming()
names = [u["what"] for u in ups]
ok("Innogy" in names and "Netflix" in names, f"upcoming = {names}")
ok("Spotify" not in names, "підписка без дати не потрапила в upcoming")
ok(ups == sorted(ups, key=lambda x: x["left"]), "upcoming відсортовано за днями")

print("\n5) Одна лінія + звіт")
M._record({"vendor": "Runable", "amount": "9.00", "currency": "EUR", "date": d(-1)})
ln = M.line()
ok("витрачено" in ln and "чекає оплати" in ln and "підписки" in ln,
   "у лінії всі три частини")
ok("прострочено" in ln, "прострочка згадана в лінії")
rep = M.report()
ok("Innogy" in rep and "Netflix" in rep, "звіт містить рахунок і підписку")
ok("115.00" in rep or "115" in rep, "сума неоплачених порахована (95+20)")

print("\n6) Ініціатива: календар на великий платіж")
reset()
sys.modules["bills_watcher"] = types.SimpleNamespace(load_bills=lambda: {
    "b1": {"vendor": "Innogy", "amount": "95.00", "currency": "EUR", "due": d(3)}})
sys.modules["subs_watcher"] = types.SimpleNamespace(monthly_total=lambda: {
    "month": 0.0, "year": 0.0, "count": 0, "items": []})
M._email_candidates = lambda limit=10: []
n = M.run(force=True)
ok(len(_EVENTS) == 1, f"подія створена ({_EVENTS})")
ok(_EVENTS and "Innogy" in _EVENTS[0][0], "у назві події постачальник")
ok(any("календар" in c[0] for c in _CARDS), "сказав постфактум карточкою")
before = len(_EVENTS)
M.run(force=True)
ok(len(_EVENTS) == before, "друга спроба не дублює подію")

print("\n7) Малий платіж — без події")
reset()
sys.modules["bills_watcher"] = types.SimpleNamespace(load_bills=lambda: {
    "b1": {"vendor": "Дрібниця", "amount": "9.00", "currency": "EUR", "due": d(2)}})
M._email_candidates = lambda limit=10: []
M.run(force=True)
ok(not _EVENTS, "подія не створена на 9€")

print("\n8) Питання про стрибок витрат")
reset()
sys.modules["bills_watcher"] = types.SimpleNamespace(load_bills=lambda: {})
prev = (K.now().replace(day=1) - timedelta(days=1))
M._record({"vendor": "Минулий", "amount": "100.00", "currency": "EUR",
           "date": prev.strftime("%Y-%m-15")})
M._record({"vendor": "Цей", "amount": "200.00", "currency": "EUR", "date": d(-1)})
p = M.picture()
ok(p["diff_pct"] == 100.0, f"diff_pct={p['diff_pct']}")
M._email_candidates = lambda limit=10: []
M.run(force=True)
ok(any("дорожче" in c[0] for c in _CARDS), "питання про стрибок надіслано")
ok(any("разові" in c[0] for c in _CARDS), "питання пряме, з вибором")
cnt = len(_CARDS)
M.run(force=True)
ok(len(_CARDS) == cnt, "питання не дублюється")

print("\n9) Мала база — відсотки не рахуємо")
reset()
sys.modules["bills_watcher"] = types.SimpleNamespace(load_bills=lambda: {})
M._record({"vendor": "A", "amount": "5.00", "currency": "EUR",
           "date": prev.strftime("%Y-%m-10")})
M._record({"vendor": "B", "amount": "30.00", "currency": "EUR", "date": d(-1)})
ok(M.picture()["diff_pct"] is None, "при базі < MIN_BASE_EUR відсотків немає")

print("\n10) Пошта недоступна — фактів не вигадуємо")
reset()
sys.modules["bills_watcher"] = types.SimpleNamespace(load_bills=lambda: {})
sys.modules["subs_watcher"] = types.SimpleNamespace(monthly_total=lambda: {
    "month": 0.0, "year": 0.0, "count": 0, "items": []})
M._email_candidates = lambda limit=10: None
ok(M.run(force=True) == 0, "run() = 0 і жодного запису")
ok(not _FS.get(M.CHARGES_FILE), "реєстр порожній")

print("\n11) Заглушено через react/dismissed")
reset()
sys.modules["bills_watcher"] = types.SimpleNamespace(load_bills=lambda: {
    "b1": {"vendor": "Прострочений", "amount": "30.00", "currency": "EUR",
           "due": d(-9)}})
M._email_candidates = lambda limit=10: []
sys.modules["dismissed"] = types.SimpleNamespace(is_muted=lambda *a, **k: True,
                                                 mute=lambda *a, **k: {})
M.run(force=True)
ok(not any("Прострочені" in c[0] for c in _CARDS), "заглушене питання не пішло")
sys.modules["dismissed"] = types.SimpleNamespace(is_muted=lambda *a, **k: False,
                                                 mute=lambda *a, **k: {})

print("\n12) Rate-limit")
reset()
sys.modules["bills_watcher"] = types.SimpleNamespace(load_bills=lambda: {})
M._email_candidates = lambda limit=10: []
M.run()
ok(bool(_RATE.get(M.SCAN_STATE)), "перший прохід відмітив rate")
M._email_candidates = lambda limit=10: (_ for _ in ()).throw(
    AssertionError("не мало викликатись"))
ok(M.run() == 0, "другий прохід одразу — пропущено")

print("\n13) Кнопка")
reset()
pid = M._store.put({"key": "k", "kind": "money"})
r = M.handle(pid)
ok(r["ok"], "handle приймає pid")
ok(not M.handle(pid)["ok"], "payload одноразовий")

print("\n" + ("=" * 46))
print(f"ПАДІНЬ: {len(fails)}" + ("  → " + ", ".join(fails) if fails else "  ✅"))
sys.exit(1 if fails else 0)
