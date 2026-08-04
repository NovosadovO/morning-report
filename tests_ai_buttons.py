"""Офлайн-тест нових кнопок: ai_buttons (gx_*) + нові дії calendar_watch (cw_*)."""
import os, sys, json
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/user/bot")
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
os.environ.setdefault("GEMINI_API_KEY", "fake")

STORE = {}
import ai_kit as K
K.GEMINI_KEY = "fake"
K.load = lambda f, default=None: STORE.get(f, default if default is not None else {})
K.save = lambda f, d: STORE.__setitem__(f, d)
K.update_key = lambda f, k, v: STORE.setdefault(f, {}).__setitem__(k, v)
K.remove_key = lambda f, k: STORE.get(f, {}).pop(k, None)
SENT = []
GEM = []
K.send_card = lambda text, kb=None, tag=None: (SENT.append((text, kb)) or True)


def fake_gem(prompt, max_tokens=0, temperature=0, tag=""):
    GEM.append(prompt)
    return "AI-відповідь №%d 💪 Конкретні кроки." % (len(GEM))


K.gemini_text = fake_gem

NOTES = []
import ai_notes
ai_notes.add_note = lambda text, source="": NOTES.append((source, text))

import ai_buttons as G
import calendar_watch as C
G.K.GEMINI_KEY = "fake"
C.K.GEMINI_KEY = "fake"

fails = []


def chk(cond, name):
    print(("✅ " if cond else "❌ ") + name)
    if not cond:
        fails.append(name)


N = K.now().replace(tzinfo=None, microsecond=0)

print("\n=== 1. ТЕМИ ВИЗНАЧАЮТЬСЯ ===")
chk(G.detect_topic("BTC зріс до $118 000, +5%") == "crypto", "крипто-текст → тема crypto")
chk(G.detect_topic("Вага 83.4 кг, сон 6.5 год") == "health", "вага/сон → тема health")
chk(G.detect_topic("Пробіжка 8 км, темп 5:40, Strava") == "run", "біг → тема run")
chk(G.detect_topic("будь-що", "vip_email") == "email", "trigger vip_email → тема email")
chk(G.detect_topic("Просто привіт") == "general", "нейтральний текст → general")

print("\n=== 2. КЛАВІАТУРА ПІД КОЖНОЮ ТЕМОЮ ===")
for topic in G.TOPIC_LABEL:
    pid, kb = G.keyboard("текст про " + topic, topic=topic)
    flat = [b for row in kb for b in row]
    cds = [b["callback_data"] for b in flat]
    chk(len(flat) >= 6, f"{topic}: щонайменше 6 кнопок ({len(flat)})")
    chk(all(c.endswith(pid) for c in cds), f"{topic}: всі кнопки з живим payload")
    chk(any(c.startswith("gx_more_") for c in cds), f"{topic}: є «Поясни детальніше»")
    chk(any(c.startswith("gx_note_") for c in cds), f"{topic}: є «Нотатка»")
    chk(any(c.startswith("gx_later_") for c in cds), f"{topic}: є «Нагадай пізніше»")
    chk(any(c.startswith("gx_mute_") for c in cds), f"{topic}: є «Не цікавить»")

print("\n=== 3. ДІЇ gx_ ПРАЦЮЮТЬ ===")
pid, kb = G.keyboard("BTC $118 000 (+5.2%). Вага 83.4 кг.", trigger_type="crypto_move")
r = G.do_more(pid)
chk(r.get("ok") and "AI-відповідь" in r["text"], "gx_more → AI-текст")
chk("НЕ вигадуй" in GEM[-1] and "BTC $118 000" in GEM[-1], "gx_more: промпт з реальним текстом і забороною вигадок")

r = G.do_note(pid, note="купити ще ETH на просадці")
chk(r.get("ok") and r.get("own") and NOTES and "ETH" in NOTES[-1][1], "gx_note з моїм текстом → в нотатки")
r = G.do_note(pid)
chk(r.get("ok") and not r.get("own"), "gx_note без тексту → автозапис (кнопка не мертва)")

r = G.do_later(pid, minutes=1)
chk(r.get("ok") and r.get("at"), "gx_later → час повтору")
chk("Нічого не відкладено" not in G.pending(), "відкладене видно в /відкладені")

before = len(SENT)
STORE[G.FOLLOW_FILE] = {k: dict(v, due=(N - timedelta(minutes=1)).isoformat())
                        for k, v in STORE[G.FOLLOW_FILE].items()}
n = G.tick()
chk(n == 1 and len(SENT) == before + 1, "tick надсилає прострочене відкладене")
chk("ТИ ПРОСИВ НАГАДАТИ" in SENT[-1][0], "відкладене має явний заголовок")
chk(SENT[-1][1] and any(b["callback_data"].startswith("gx_") for b in SENT[-1][1][0]),
    "відкладене приходить зі своїми живими кнопками")
chk(G.tick() == 0, "повторний tick не дублює")

print("\n=== 4. MUTE ===")
pid2, _ = G.keyboard("Місяць у Скорпіоні, транзит Сатурна", trigger_type="astro")
r = G.do_mute(pid2, days=7)
chk(r.get("ok") and r.get("topic") == "astro", "gx_mute → тема astro прихована")
chk(G.is_muted("astro") and not G.is_muted("crypto"), "прихована тільки та тема")
chk("астрологія" in G.mute_status(), "/приховані_теми показує тему")
G.unmute_all()
chk(not G.is_muted("astro"), "/увімкни_теми повертає тему")

print("\n=== 5. МЕРТВИХ КНОПОК НЕМА ===")
for fn in (G.do_more, G.do_note, G.do_later, G.do_mute, G.do_done, G.ask_note_text):
    r = fn("zzzz_no_such")
    chk(r.get("error") == "payload_missing", f"{fn.__name__}: зник payload → payload_missing")

print("\n=== 6. БЕЗ AI — БЕЗ ВИГАДОК ===")
G.K.GEMINI_KEY = ""
K.gemini_text = lambda *a, **kw: ""
r = G.do_more(pid)
chk(not r.get("ok") and r.get("error") == "ai_failed" and r.get("source"),
    "AI впав → віддає оригінал, а не вигадку")
K.gemini_text = fake_gem
G.K.GEMINI_KEY = "fake"

print("\n=== 7. ЗВІТ /кнопки ===")
rep = G.report(7)
chk("НАТИСКАННЯ КНОПОК" in rep and "нотатка" in rep, "звіт показує натискання і нотатки")

print("\n=== 8. КАЛЕНДАРНІ КНОПКИ ===")


class PS:
    def __init__(s):
        s.d = {}

    def put(s, p):
        pid = K.Dedup.key(json.dumps(p, default=str) + str(len(s.d)))
        s.d[pid] = p
        return pid

    def get(s, pid):
        return s.d.get(pid)

    def gc(s, days=14):
        return 0


C._store = PS()


def ev(t, dm, dur=60, loc=""):
    st = (N + timedelta(minutes=dm)).replace(tzinfo=timezone(timedelta(hours=2)))
    return {"id": "e_" + str(dm), "summary": t,
            "start": {"dateTime": st.isoformat()},
            "end": {"dateTime": (st + timedelta(minutes=dur)).isoformat()},
            "location": loc}


RAW = [ev("Тренування", 120, loc="Košice, Aupark"), ev("Зустріч Maroš", 24 * 60, loc="Prešov"),
       ev("🌙 Нічна зміна", 300)]
C._raw_events = lambda hours_ahead=C.DEFAULT_HOURS: sorted(
    [x for x in (C._norm(e) for e in RAW) if x], key=lambda z: z["start"])

evs = C._raw_events()
target = [e for e in evs if e["title"] == "Тренування"][0]

kb = C._kb_event("PID", "t30")
cds = [b["callback_data"] for row in kb for b in row]
chk(any(c.startswith("cw_sn60_") for c in cds), "t30: є «⏰ +1 год»")
chk(any(c.startswith("cw_go_") for c in cds), "t30: є «🚗 Виїжджаю»")
chk(any(c.startswith("cw_map_") for c in cds), "t30: є «📍 Деталі/маршрут»")
chk(any(c.startswith("cw_ai_") for c in cds), "t30: лишилась «🤖 AI-підготовка»")
kb3 = [b["callback_data"] for row in C._kb_event("PID", "t3d") for b in row]
chk(not any(c.startswith("cw_go_") for c in kb3) and any(c.startswith("cw_day_") for c in kb3),
    "t3d (далеко): без «Виїжджаю», але з «План дня»")
kba = [b["callback_data"] for row in C._kb_event("PID", "after") for b in row]
chk(any(c.startswith("cw_next_") for c in kba), "after: є «🤖 Що далі?»")

SENT.clear()
C._send_event(target, "t30", evs)
pid_ev = [b["callback_data"][len("cw_ok_"):] for row in SENT[-1][1] for b in row
          if b["callback_data"].startswith("cw_ok_")][0]

r = C.do_snooze(pid_ev, minutes=60)
chk(r.get("ok") and r.get("minutes") == 60, "cw_sn60 → відкладено на 60 хв")
r = C.do_go(pid_ev)
chk(r.get("ok") and isinstance(r.get("left"), int) and r["left"] > 0, "cw_go → скільки лишилось хвилин")
r = C.do_map(pid_ev)
chk(r.get("ok") and "google.com/maps" in r.get("url", "") and "Aupark" in r.get("location", ""),
    "cw_map → реальне місце + маршрут")
r = C.do_day(pid_ev)
chk(r.get("ok") and "Тренування" in r["text"], "cw_day → план дня з реальними подіями")
r = C.do_next(pid_ev)
chk(r.get("ok") and "AI-відповідь" in r["text"], "cw_next → AI наступні кроки")
r = C.do_note(pid_ev, note="взяти кросівки")
chk(r.get("ok") and r.get("own") and "кросівки" in NOTES[-1][1], "cw_note з моїм текстом")
r = C.ask_note_text(pid_ev)
chk(r.get("ok") and r.get("title") == "Тренування", "ask_note_text → назва події для запиту")

print("\n=== 9. КНОПКИ ПІД ОГЛЯДАМИ ===")
SENT.clear()
C._daily_done = lambda k: False
C._daily_mark = lambda k: None
C.agenda(force=True)
cds_a = [b["callback_data"] for row in SENT[-1][1] for b in row]
chk(any(c.startswith("cw_focus_") for c in cds_a), "агенда: є «🎯 Головне на день»")
chk(any(c.startswith("cw_run_") for c in cds_a), "агенда: є «🏃 Вписати біг»")
chk(any(c.startswith("cw_refresh_") for c in cds_a), "агенда: є «🔁 Оновити»")
pid_ag = cds_a[0][len("cw_ack_"):]
r = C.do_focus(pid_ag)
chk(r.get("ok") and "ГОЛОВНЕ НА ДЕНЬ" in r.get("head", ""), "cw_focus (день) → AI обирає головне")
chk("НЕ вигадуй" in GEM[-1], "cw_focus: у промпті заборона вигадок")
r = C.do_run(pid_ag)
chk(r.get("ok") and "AI-відповідь" in r["text"], "cw_run → вікно для бігу")
r = C.do_refresh(pid_ag)
_fresh = C._agenda_text(C._day_events(0, C._raw_events()), 0)
chk(r.get("ok") and r["text"] and r["text"][:200] == _fresh[:200],
    "cw_refresh → свіжий план дня прямо з календаря")

for fn in (C.do_go, C.do_map, C.do_day, C.do_next, C.do_focus, C.do_run, C.do_refresh, C.ask_note_text):
    r = fn("zzzz_no_such")
    chk(r.get("error") == "payload_missing", f"C.{fn.__name__}: зник payload → payload_missing")

print("\n=== 10. КАЛЕНДАР НЕДОСТУПНИЙ → МОЛЧИМО ===")
C._raw_events = lambda hours_ahead=C.DEFAULT_HOURS: None
r = C.do_day(pid_ev)
chk(r.get("error") == "no_calendar", "do_day: календар недоступний → no_calendar, без вигадок")
r = C.do_focus(pid_ag)
chk(r.get("error") == "no_calendar", "do_focus: календар недоступний → no_calendar")

print("\n" + "=" * 50)
print(f"ПОМИЛОК: {len(fails)}")
for f in fails:
    print("  ❌ " + f)
