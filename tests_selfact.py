#!/usr/bin/env python3
"""Офлайн-тести selfact.py — детерміновані, без мережі."""
import os
import sys
import types
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = []


def ok(cond, name):
    if cond:
        print("  ok  " + name)
    else:
        print("  FAIL " + name)
        FAILS.append(name)


# ── заглушки залежностей ДО імпорту ─────────────────────────────
_dm = types.ModuleType("dismissed")
_dm.is_muted = lambda kind=None, key=None, title=None: "заглушено" in str(title or "").lower()
sys.modules["dismissed"] = _dm

_rx = types.ModuleType("react")
_rx.is_closed = lambda kind=None, key=None, title=None: "закрито" in str(title or "").lower()
_rx.keyboard = lambda kind="generic", key="", title="": [[{"text": "ok", "callback_data": "rx_ok"}]]
_rx.detect = lambda tag="", text="": "generic"
_rx._first_line = lambda text: str(text).split("\n")[0]
sys.modules["react"] = _rx

_an = types.ModuleType("ai_notes")
_NOTES = []
_an.add_note = lambda text, source="manual": _NOTES.append((text, source))
sys.modules["ai_notes"] = _an

_mon = types.ModuleType("monitor")
_MAILS = []
_mon.get_emails = lambda: list(_MAILS)
sys.modules["monitor"] = _mon

import selfact as S
import ai_kit as K

# ── підміна I/O ─────────────────────────────────────────────────
STORE = {}
SENT = []
EVENTS = []
GEM = {"out": []}


def _load(fn, default=None):
    v = STORE.get(fn)
    if v is None:
        return default
    import copy
    return copy.deepcopy(v)


def _save(fn, data):
    import copy
    STORE[fn] = copy.deepcopy(data)


def _update_key(fn, key, value):
    d = STORE.get(fn)
    if not isinstance(d, dict):
        d = {}
    d[key] = value
    STORE[fn] = d


def _remove_key(fn, key):
    d = STORE.get(fn)
    if isinstance(d, dict):
        d.pop(key, None)


S.K.load = _load
S.K.save = _save
S.K.update_key = _update_key
S.K.remove_key = _remove_key
S.K.send_card = lambda text, keyboard=None, tag="", chat_id=None: (SENT.append(text), True)[1]
S.K.calendar_event = lambda summary, start_dt, end_dt=None, description="": (
    EVENTS.append((summary, start_dt)), {"ok": True})[1]
S.K.events_for_day = lambda off=0: []
S.K.gemini_json = lambda prompt, max_tokens=1400, temperature=0.5, tag="", want="list": GEM["out"]
S.K.rate_ok = lambda f, m: True
S.K.rate_mark = lambda f: None

# dedup створюється на імпорті ДО підміни → перестворюємо
S._dedup = K.Dedup(S.DEDUP_FILE, ttl_days=5)
S._dedup.__dict__  # noqa
K.load = _load
K.save = _save
K.update_key = _update_key
K.remove_key = _remove_key


def reset():
    STORE.clear()
    SENT.clear()
    EVENTS.clear()
    _NOTES.clear()
    _MAILS.clear()
    GEM["out"] = []
    S._dedup = K.Dedup(S.DEDUP_FILE, ttl_days=5)


TOM = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

# ── 1. немає даних → жодної дії ──────────────────────────────────
print("1. порожній контекст")
reset()
ok(S.context() == "", "context порожній без даних")
ok(S.decide("") == [], "decide без даних → []")
ok(S.run(force=True) == 0, "run без даних → 0 дій")
ok(SENT == [], "нічого не надіслано")

# ── 2. контекст із реєстрів ─────────────────────────────────────
print("2. контекст із даних")
reset()
STORE["bills.json"] = {"b1": {"vendor": "Innogy", "amount": 62.4, "due": TOM}}
STORE["health.json"] = {"2026-08-25": {"weight": 83.4, "steps": 5100}}
ctx = S.context()
ok("Innogy" in ctx, "рахунок у контексті")
ok("83.4" in ctx, "здоров'я у контексті")
ok("СЬОГОДНІ" in ctx, "є дата/час")

# ── 3. нагадування пишеться у СПИСОК зі sent:false ──────────────
print("3. нагадування")
reset()
STORE["bills.json"] = {"b1": {"vendor": "Innogy", "amount": 62.4, "due": TOM}}
GEM["out"] = [{"type": "reminder", "title": "Оплатити Innogy",
               "text": "Рахунок 62.40 EUR", "date": TOM, "time": "10:00",
               "why": "bills.json"}]
n = S.run(force=True)
rem = STORE.get("reminders.json")
ok(n == 1, "виконана 1 дія")
ok(isinstance(rem, list) and len(rem) == 1, "reminders.json — СПИСОК з 1 запису")
ok(rem[0]["sent"] is False, "sent=False")
ok("datetime_utc" in rem[0] and rem[0]["id"].startswith("selfact_"), "формат запису")
ok(any("Зробив сам" in s for s in SENT), "Олега сповістили про дію")

# ── 4. не дублює ту саму дію ─────────────────────────────────────
print("4. дедуплікація")
before = len(STORE["reminders.json"])
n2 = S.run(force=True)
ok(n2 == 0, "повторний прохід → 0 дій")
ok(len(STORE["reminders.json"]) == before, "нагадування не задублювалось")

# ── 5. нагадування без достовірної дати відкидається ────────────
print("5. брехлива дата")
reset()
STORE["bills.json"] = {"b1": {"vendor": "X"}}
GEM["out"] = [{"type": "reminder", "title": "Колись", "text": "щось",
               "date": "2020-01-01", "time": "10:00"}]
ok(S.run(force=True) == 0, "минула дата → дія не виконана")
ok(STORE.get("reminders.json") is None, "у reminders нічого не додано")

# ── 6. подія в календарі ─────────────────────────────────────────
print("6. подія")
reset()
STORE["deadlines.json"] = {"d1": {"what": "техогляд", "due": TOM}}
GEM["out"] = [{"type": "event", "title": "Техогляд авто", "text": "СТК",
               "date": TOM, "time": "08:00", "why": "deadlines.json"}]
ok(S.run(force=True) == 1, "подія створена")
ok(len(EVENTS) == 1 and isinstance(EVENTS[0][1], datetime), "calendar_event з datetime")

# ── 7. нотатка ───────────────────────────────────────────────────
print("7. нотатка")
reset()
STORE["ai_notes.json"] = {"notes": [{"text": "старе", "source": "manual", "ts": "2026-01-01"}]}
GEM["out"] = [{"type": "note", "title": "Нічні зміни", "text": "Олег на нічній у вересні"}]
ok(S.run(force=True) == 1, "нотатка додана")
ok(_NOTES and _NOTES[0][1] == "selfact", "source=selfact")

# ── 8. повага до react / dismissed ───────────────────────────────
print("8. закрите й заглушене")
reset()
STORE["bills.json"] = {"b1": {"vendor": "Y"}}
GEM["out"] = [{"type": "notify", "title": "Тема закрито", "text": "щось"},
              {"type": "ask", "title": "Тема заглушено", "text": "щось"}]
ok(S.run(force=True) == 0, "закриті/заглушені теми пропущені")
ok(SENT == [], "нічого не надіслано")

# ── 9. журнал ────────────────────────────────────────────────────
print("9. журнал дій")
reset()
S.journal("event", "Техогляд авто", "з deadlines", module="deadlines_watcher")
recs = S.load_journal()
ok(len(recs) == 1 and recs[0]["module"] == "deadlines_watcher", "запис у журнал")
ok(len(S._today_actions()) == 1, "дія зарахована на сьогодні")

# ── 10. дайджест ─────────────────────────────────────────────────
print("10. міні-звіт")
reset()
d = S.digest(force=True)
ok("Нічого не створював" in d, "без дій каже прямо")
S.journal("reminder", "Оплатити Innogy", "bills")
S.journal("note", "Нічні зміни у вересні", "календар")
d2 = S.digest(force=True)
ok("Оплатити Innogy" in d2 and "Нічні зміни" in d2, "дії в звіті")
ok("Нагадування (1)" in d2 and "Нотатка (1)" in d2, "групування з підрахунком")
ok(SENT == [], "force=True не надсилає")

# ── 11. ліміт дій ────────────────────────────────────────────────
print("11. ліміт 2 дії")
reset()
STORE["bills.json"] = {"b1": {"vendor": "Z"}}
GEM["out"] = [{"type": "notify", "title": "A" + str(i), "text": "t"} for i in range(5)]
ok(len(S.decide(S.context())) <= S.MAX_ACTIONS, "decide ріже до MAX_ACTIONS")

# ── 12. смітник від AI відкидається ─────────────────────────────
print("12. валідація відповіді AI")
reset()
STORE["bills.json"] = {"b1": {"vendor": "Q"}}
GEM["out"] = ["сміття", {"type": "unknown", "title": "x"}, {"type": "note"},
              {"type": "note", "title": "ok", "text": "факт"}]
acts = S.decide(S.context())
ok(len(acts) == 1 and acts[0]["type"] == "note", "лишилась лише валідна дія")

# ── 13. report без побічних дій ──────────────────────────────────
print("13. report")
reset()
STORE["bills.json"] = {"b1": {"vendor": "W", "amount": 10}}
GEM["out"] = [{"type": "ask", "title": "Питання", "text": "чи оплатив?", "why": "bills"}]
r = S.report()
ok("Питання" in r, "report показує задум")
ok(SENT == [] and EVENTS == [] and _NOTES == [], "report нічого не виконує")

print()
print("FAILS:", len(FAILS))
if FAILS:
    for f in FAILS:
        print(" -", f)
    sys.exit(1)
