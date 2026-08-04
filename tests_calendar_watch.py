import sys, types, json
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/user/bot")

FAIL = []
def ok(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c: FAIL.append(m)

# ── мок storage ─────────────────────────────────────────────────────────────
MEM = {}
st = types.ModuleType("storage")
def _load(f, default=None): return json.loads(json.dumps(MEM.get(f, default if default is not None else {})))
def _save(f, d): MEM[f] = json.loads(json.dumps(d)); return True
def _upd(f, k, v, default=None):
    d = MEM.setdefault(f, {}); d[k] = json.loads(json.dumps(v)); return True
def _rem(f, k):
    return MEM.setdefault(f, {}).pop(k, None)
st.load, st.save, st.update_key, st.remove_key = _load, _save, _upd, _rem
sys.modules["storage"] = st

# ── мок telegram ────────────────────────────────────────────────────────────
SENT = []
import ai_kit as K
K.TELEGRAM_TOKEN = "x"; K.TELEGRAM_CHAT = "1"
def fake_send_card(text, keyboard=None, tag="", chat_id=None):
    SENT.append({"text": text, "kb": keyboard}); return True
K.send_card = fake_send_card

# ── мок календаря ───────────────────────────────────────────────────────────
NOW = K.now().replace(tzinfo=None)
def iso_utc(dt_local):
    return (dt_local - K.TZ).replace(tzinfo=timezone.utc).isoformat()

EVENTS = []
def mk(eid, title, start_local, dur=60, loc=""):
    return {"id": eid, "summary": title, "location": loc,
            "start": {"dateTime": iso_utc(start_local)},
            "end": {"dateTime": iso_utc(start_local + timedelta(minutes=dur))}}

mon = types.ModuleType("monitor")
mon._calendar_access_token = lambda: "tok"
mon._fetch_events_all_calendars = lambda h, a, b, max_per_cal=40: list(EVENTS)
sys.modules["monitor"] = mon
notes = types.ModuleType("ai_notes")
NOTES = []
notes.add_note = lambda t, source="": NOTES.append(t)
sys.modules["ai_notes"] = notes

import calendar_watch as C

print("=== 1. Парсинг і фільтр рутини ===")
EVENTS = [
    mk("e1", "Зустріч з Maroš — інвестиції", NOW + timedelta(minutes=120), 60, "Košice"),
    mk("e2", "🏃 Біг 5 км", NOW + timedelta(minutes=120)),
    mk("e3", "🌙 Нічна зміна", NOW + timedelta(minutes=125), 720),
    mk("e4", "Дзвінок Michaela", NOW + timedelta(minutes=30)),
    mk("e5", "Огляд авто STK", NOW - timedelta(minutes=90), 60),
]
C._cache["ts"] = None
evs = C._raw_events()
ok(len(evs) == 5, f"розпарсено 5 подій, got {len(evs)}")
byid = {e["id"]: e for e in evs}
ok(byid["e2"]["routine"] is True, "біг позначений як рутина")
ok(byid["e3"]["shift"] is True, "нічна зміна позначена як зміна")
ok(byid["e1"]["routine"] is False, "зустріч НЕ рутина")
ok(byid["e1"]["location"] == "Košice", "локація витягнута")
ok(abs((byid["e1"]["start"] - (NOW + timedelta(minutes=120))).total_seconds()) < 90,
   "час події у локальному часі (без зсуву TZ)")

print("=== 2. Прохід: кожна подія окремо ===")
SENT.clear()
n = C.tick()
ok(n == 3, f"надіслано 3 нагадування (t2h зустріч, t30 дзвінок, after STK), got {n}")
txt = "\n".join(s["text"] for s in SENT)
ok("Maroš" in txt, "є нагадування про зустріч")
ok("Michaela" in txt, "є нагадування про дзвінок")
ok("Як пройшло" in txt, "є питання «як пройшло» після події")
ok("Біг 5 км" not in txt, "рутина НЕ спамить")
ok("Нічна зміна" not in txt, "зміна не йде як подія")

print("=== 3. Дедуп — повторно не спамить ===")
SENT.clear()
n2 = C.tick()
ok(n2 == 0, f"повторний прохід нічого не надіслав, got {n2}")

print("=== 4. Нова подія все одно проходить (дедуп per-event, не глобальний) ===")
EVENTS.append(mk("e6", "Візит до лікаря", NOW + timedelta(minutes=31)))
C._cache["ts"] = None
SENT.clear()
n3 = C.tick()
ok(n3 == 1, f"нова подія отримала нагадування навіть після інших, got {n3}")
ok("лікаря" in SENT[0]["text"], "саме про нову подію")

print("=== 5. Кнопки під нагадуванням ===")
kb = SENT[0]["kb"]
flat = [b["callback_data"] for row in kb for b in row]
ok(any(d.startswith("cw_ok_") for d in flat), "є кнопка «Пам'ятаю»")
ok(any(d.startswith("cw_sn_") for d in flat), "є кнопка «+15 хв»")
ok(any(d.startswith("cw_note_") for d in flat), "є кнопка «Нотатка»")
ok(any(d.startswith("cw_cancel_") for d in flat), "є кнопка «Скасовано»")
pid = [d for d in flat if d.startswith("cw_ok_")][0][len("cw_ok_"):]

print("=== 6. Відповіді ЗБЕРІГАЮТЬСЯ ===")
r = C.do_ok(pid)
ok(r.get("ok"), "cw_ok відпрацював")
acks = MEM.get("calendar_ack.json", {})
ok(len(acks) == 1, f"відповідь збережена у calendar_ack.json, got {len(acks)}")
rec = list(acks.values())[0]
ok(rec["answer"] == "remembered", "answer=remembered")
ok("лікаря" in rec["title"], "заголовок події збережений разом з відповіддю")
ok(bool(rec.get("ts")), "час відповіді збережений")

print("=== 7. Snooze +15 хв реально стріляє ===")
sn_pid = [d for d in flat if d.startswith("cw_sn_")][0][len("cw_sn_"):]
r = C.do_snooze(sn_pid, minutes=15)
ok(r.get("ok"), "snooze прийнятий")
ok(len(MEM.get("calendar_snooze.json", {})) == 1, "snooze записаний у storage")
SENT.clear()
ok(C._fire_snoozed() == 0, "до часу не стріляє")
key = list(MEM["calendar_snooze.json"])[0]
MEM["calendar_snooze.json"][key]["due"] = (NOW - timedelta(minutes=1)).isoformat()
fired = C._fire_snoozed()
ok(fired == 1, f"після настання часу стріляє, got {fired}")
ok("Нагадую ще раз" in SENT[0]["text"], "текст повторного нагадування")
ok(len(MEM["calendar_snooze.json"]) == 0, "snooze прибраний після спрацювання")

print("=== 8. «Скасовано» глушить подію назавжди ===")
EVENTS.append(mk("e7", "Непотрібна зустріч", NOW + timedelta(minutes=29)))
C._cache["ts"] = None
SENT.clear(); C.tick()
kb7 = SENT[-1]["kb"]
flat7 = [b["callback_data"] for row in kb7 for b in row]
cpid = [d for d in flat7 if d.startswith("cw_cancel_")][0][len("cw_cancel_"):]
C.do_cancel(cpid)
EVENTS[-1] = mk("e7", "Непотрібна зустріч", NOW + timedelta(minutes=119))
C._cache["ts"] = None
SENT.clear()
C.tick()
ok(all("Непотрібна" not in s["text"] for s in SENT), "після «Скасовано» подія більше не нагадує")

print("=== 9. Нотатка ===")
npid = [d for d in flat if d.startswith("cw_note_")][0][len("cw_note_"):]
r = C.do_note(npid)
ok(r.get("ok"), "нотатка збережена")
ok(len(NOTES) == 1 and "лікаря" in NOTES[0], "нотатка містить подію")

print("=== 10. Агенда дня ===")
# ВАЖЛИВО: події з фіксованим часом СЬОГОДНІ (а не NOW+хвилини) — інакше пізно
# ввечері вони перетікають за полуніч і агенда справедливо їх не показує,
# і тест падав залежно від години запуску.
_D = NOW.replace(hour=9, minute=0, second=0, microsecond=0)
EVENTS.append(mk("a1", "🌙 Нічна зміна (агенда)", _D, 720))
EVENTS.append(mk("a2", "Зустріч Maroš — агенда", _D.replace(hour=10)))
EVENTS.append(mk("a3", "Дзвінок Michaela — агенда", _D.replace(hour=11)))
EVENTS.append(mk("a4", "🍵 Трав'яний чай", _D.replace(hour=8)))
C._cache["ts"] = None
SENT.clear()
ok(C.agenda(force=True), "агенда надіслана")
a = SENT[0]["text"]
ok("ПЛАН НА СЬОГОДНІ" in a, "заголовок агенди")
ok("Нічна зміна" in a, "зміна показана окремим рядком")
ok("Maroš" in a and "Michaela" in a, "реальні події перелічені")
ok("Рутини в календарі" in a, "рутина — лічильником, не спамом")
akb = [b["callback_data"] for row in SENT[0]["kb"] for b in row]
ok(any(d.startswith("cw_ack_") for d in akb), "кнопка «Прийняв план»")
apid = [d for d in akb if d.startswith("cw_ack_")][0][len("cw_ack_"):]
C.do_agenda_ack(apid)
ok(any(v["answer"] == "accepted" for v in MEM["calendar_ack.json"].values()), "ack агенди збережений")

print("=== 11. Агенда — 1 раз на день ===")
MEM["calendar_daily.json"] = {"agenda": K.today_str()}
SENT.clear()
ok(C.agenda(force=False) is False, "повторно за день не надсилає")

print("=== 12. Прев'ю на завтра ===")
EVENTS.append(mk("e8", "Весілля — фінальна зустріч",
                 (NOW + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)))
C._cache["ts"] = None
SENT.clear()
ok(C.tomorrow(force=True), "прев'ю надіслано")
ok("ЧЕКАЄ ЗАВТРА" in SENT[0]["text"], "заголовок прев'ю")
ok("Весілля" in SENT[0]["text"], "подія завтра показана")

print("=== 13. Календар недоступний → молчить, не вигадує ===")
mon._calendar_access_token = lambda: None
C._cache["ts"] = None
ok(C._raw_events() is None, "None замість вигадок")
ok(C.tick() == 0, "прохід 0 нагадувань")
ok(C.agenda(force=True) is False, "агенда не вигадується")
mon._calendar_access_token = lambda: "tok"

print("=== 14. Звіт відповідей ===")
rep = C.report()
ok("ВІДПОВІДІ НА НАГАДУВАННЯ" in rep, "звіт будується")
ok("лікаря" in rep, "у звіті є подія")

print("=== 15. Нуль AI-викликів ===")
calls = []
K.gemini_text = lambda *a, **k: calls.append(1) or ""
K.gemini_json = lambda *a, **k: calls.append(1) or None
C._cache["ts"] = None
MEM["calendar_sent.json"] = {}
MEM["calendar_daily.json"] = {}
C.tick(); C.agenda(force=True); C.tomorrow(force=True); C.report()
ok(len(calls) == 0, f"Gemini не викликався ані разу, got {len(calls)}")

print("\n" + "="*60)
print("❌ FAILED: " + str(len(FAIL)) if FAIL else "✅ ALL PASS")
for f in FAIL: print("   -", f)
sys.exit(1 if FAIL else 0)
