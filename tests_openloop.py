#!/usr/bin/env python3
"""Офлайн-тести openloop.py — чого Олег не зробив + ініціатива бота."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K

FAILS = []


def ok(cond, name):
    print(("✅ " if cond else "❌ ") + name, flush=True)
    if not cond:
        FAILS.append(name)


STORE = {}
SENT = []
EVENTS = []


def _load(fn, default=None):
    v = STORE.get(fn)
    if v is None:
        return default if default is not None else {}
    return v


def _save(fn, data):
    STORE[fn] = data
    return True


def _upd(fn, key, val):
    cur = STORE.get(fn)
    if not isinstance(cur, dict):
        cur = {}
    cur[key] = val
    STORE[fn] = cur
    return True


def _rem(fn, key):
    if isinstance(STORE.get(fn), dict):
        STORE[fn].pop(key, None)
    return True


def _send_card(text, keyboard=None, tag="", chat_id=None):
    SENT.append({"text": text, "kb": keyboard, "tag": tag})
    return True


def _cal(summary, start_dt, end_dt=None, description=""):
    EVENTS.append({"summary": summary, "start": start_dt})
    return {"ok": True, "id": "ev" + str(len(EVENTS))}


K.load = _load
K.save = _save
K.update_key = _upd
K.remove_key = _rem
K.send_card = _send_card
K.calendar_event = _cal
K.rate_ok = lambda *a, **k: True
K.rate_mark = lambda *a, **k: None

import react as R  # noqa: E402
import openloop as OL  # noqa: E402

R._store = K.PayloadStore(R.STORE_FILE)
OL._store = K.PayloadStore(OL.STORE_FILE)

MUTED = []


class _FakeDismissed:
    @staticmethod
    def mute(kind, key=None, title=None, note=""):
        MUTED.append({"kind": kind, "key": key, "title": title})
        return {"ok": True}

    @staticmethod
    def is_muted(kind=None, key=None, title=None):
        return False


sys.modules["dismissed"] = _FakeDismissed


def reset():
    STORE.clear()
    SENT.clear()
    EVENTS.clear()
    MUTED.clear()
    R._CACHE["data"] = None
    R._CACHE["ts"] = 0.0
    R._store = K.PayloadStore(R.STORE_FILE)
    OL._store = K.PayloadStore(OL.STORE_FILE)
    OL._sent_mail = lambda *a, **k: []
    OL._waiting = lambda sent: []
    OL._promises = lambda sent: []


def ago(days=0, hours=0):
    return (datetime.now() - timedelta(days=days, hours=hours)).isoformat(
        timespec="seconds")


# ─── 1. лист без відповіді ───────────────────────────────────────────────────
print("\n=== 1. лист без відповіді ===")
reset()
OL._sent_mail = lambda *a, **k: [
    {"to": "hr@firma.sk", "subject": "Dovolenka", "body": "ok", "date": ""}]
STORE["email_body_cache.json"] = {
    "u1": {"body": "Prosím o odpoveď", "date": ago(days=5)},
    "u2": {"body": "novy list", "date": ago(days=1)},
    "u3": {"body": "Dovolenka detail", "date": ago(days=6)},
}
import mailcal as MC  # noqa: E402
MC._emails = lambda limit=25: [
    {"uid": "u1", "sender": "Michaela <m@firma.sk>", "subject": "Faktúra 240",
     "body": "Prosím o odpoveď"},
    {"uid": "u2", "sender": "Peter <p@x.sk>", "subject": "Ahoj", "body": "hi"},
    {"uid": "u3", "sender": "HR <hr@firma.sk>", "subject": "Re: Dovolenka",
     "body": "detail"},
]
import importlib  # noqa: E402
importlib.reload(OL)          # повертаємо справжній _waiting після reset()
OL._store = K.PayloadStore(OL.STORE_FILE)
_sent = [{"to": "hr@firma.sk", "subject": "Dovolenka", "body": "ok",
          "date": ""}]
res = OL._waiting(_sent)
titles = [x["title"] for x in (res or [])]
ok("Faktúra 240" in titles, "лист старший 3 днів без відповіді — петля")
ok("Ahoj" not in titles, "свіжий лист (1 день) не петля")
ok(all("Dovolenka" not in t for t in titles),
   "тему, на яку вже відповідав, не піднімає")

# ─── 2. автовідповідачі не рахуються ─────────────────────────────────────────
print("\n=== 2. noreply ===")
MC._emails = lambda limit=25: [
    {"uid": "u1", "sender": "noreply@bank.sk", "subject": "Vypis",
     "body": "x"}]
STORE["email_body_cache.json"] = {"u1": {"body": "x", "date": ago(days=9)}}
ok(OL._waiting([]) == [], "від noreply відповіді не чекаємо")

# ─── 3. без дати листа — не вигадуємо давність ───────────────────────────────
print("\n=== 3. немає дати ===")
MC._emails = lambda limit=25: [
    {"uid": "u9", "sender": "Ivan <i@x.sk>", "subject": "Otazka", "body": "x"}]
STORE["email_body_cache.json"] = {"u9": {"body": "x"}}
ok(OL._waiting([]) == [], "без дати листа петлю не вигадуємо")

# ─── 4. пошта впала — модуль мовчить ─────────────────────────────────────────
print("\n=== 4. пошта впала ===")
MC._emails = lambda limit=25: None
ok(OL._waiting([]) is None, "пошта недоступна → None, а не порожній список")
reset()
OL._sent_mail = lambda *a, **k: None
n = OL.run(force=True)
ok(n == 0 and not SENT, "IMAP впав — жодного повідомлення не надсилаємо")

# ─── 5. прострочені нагадування ──────────────────────────────────────────────
print("\n=== 5. прострочені нагадування ===")
reset()
STORE["reminders.json"] = [
    {"id": "r1", "datetime_utc": ago(days=2), "text": "✍️ Написати Янці"},
    {"id": "r2", "datetime_utc": ago(hours=1), "text": "Свіже"},
    {"id": "r3", "datetime_utc": ago(days=2), "text": "Щоденне",
     "repeat": "daily"},
    {"id": "r4", "datetime_utc": ago(days=90), "text": "Древнє"},
]
od = OL._overdue()
ids = [x["key"] for x in od]
ok("r1" in ids, "прострочене нагадування — петля")
ok("r2" not in ids, "щойно прострочене (1 год) ще не турбує")
ok("r3" not in ids, "щоденні повторювані не рахуємо простроченими")
ok("r4" not in ids, "древнє (90 дн.) не піднімаємо")

# ─── 6. закрите кнопкою не піднімається ──────────────────────────────────────
print("\n=== 6. закрите реакцією ===")
R.mark("task", "done", key="r1", title="✍️ Написати Янці")
ok(OL._overdue() == [], "після «Зробив» петля зникає назавжди")

# ─── 7. ініціатива: створює подію з обіцянки ─────────────────────────────────
print("\n=== 7. бот сам створює подію ===")
reset()
soon = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
OL._sent_mail = lambda *a, **k: [{"to": "x@y.sk", "subject": "s", "body": "b"}]
OL._promises = lambda sent: [
    {"src": "promise", "key": "", "title": "надіслати документи Янці",
     "who": "janka@x.sk", "date": soon, "age": 0}]
n = OL.run(force=True)
ok(n == 1, "петлю надіслано")
ok(len(EVENTS) == 1, "бот САМ створив подію в календарі")
ok(soon in str(EVENTS[0]["start"]), "подія на обіцяну дату")
ok("календар" in SENT[0]["text"], "бот сказав постфактум, що створив")
ok(SENT[0]["kb"], "під петлею є кнопки відповіді")

# ─── 8. запис у реєстр ───────────────────────────────────────────────────────
print("\n=== 8. записує ===")
ok(len(STORE.get(OL.FILE, {})) == 1, "петля записана в реєстр")
rec = list(STORE[OL.FILE].values())[0]
ok(rec.get("src") == "promise" and rec.get("ts"), "джерело і час збережені")

# ─── 9. не питає двічі ───────────────────────────────────────────────────────
print("\n=== 9. без спаму ===")
before = len(SENT)
OL.run(force=True)
ok(len(SENT) == before, "про ту саму петлю вдруге не питає")

# ─── 10. обіцянка без дати — подію не вигадуємо ──────────────────────────────
print("\n=== 10. обіцянка без дати ===")
reset()
OL._sent_mail = lambda *a, **k: [{"to": "x@y.sk", "subject": "s", "body": "b"}]
OL._promises = lambda sent: [
    {"src": "promise", "key": "", "title": "подзвонити в банк", "who": "",
     "date": "", "age": 0}]
OL.run(force=True)
ok(len(EVENTS) == 0, "без конкретної дати подію не створюємо")
ok(len(SENT) == 1 and "подзвонити в банк" in SENT[0]["text"],
   "але питаємо про саму обіцянку")

# ─── 11. ліміт за прохід ─────────────────────────────────────────────────────
print("\n=== 11. ліміт ===")
reset()
STORE["reminders.json"] = [
    {"id": "x" + str(i), "datetime_utc": ago(days=2), "text": "Справа " + str(i)}
    for i in range(9)]
OL.run(force=True)
ok(len(SENT) == OL.MAX_ITEMS, "за прохід не більше MAX_ITEMS петель")

# ─── 12. нічого не знайшов — тиша ────────────────────────────────────────────
print("\n=== 12. тиша ===")
reset()
n = OL.run(force=True)
ok(n == 0 and not SENT, "немає петель — жодного повідомлення")
ok("не знайшов" in OL.report() or "Жодної" in OL.report(),
   "у звіті чесно: петель немає")

print("\n" + "=" * 50)
print("Падінь: " + str(len(FAILS)))
for f in FAILS:
    print(" - " + f)
sys.exit(1 if FAILS else 0)
