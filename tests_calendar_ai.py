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
K.update_key = lambda f, k, v: STORE.setdefault(f, {}).__setitem__(k, v)
K.remove_key = lambda f, k: STORE.get(f, {}).pop(k, None)
SENT = []
GEM = []
K.send_card = lambda text, kb=None, tag=None: (SENT.append((text, kb)) or True)


def fake_gem(prompt, max_tokens=0, temperature=0, tag=""):
    GEM.append(prompt)
    return "Тестова AI-порада №%d 💪 Підготуйся заздалегідь." % len(GEM)


K.gemini_text = fake_gem

import calendar_watch as C
C.K.GEMINI_KEY = "fake"


class PS:
    def __init__(s):
        s.d = {}

    def put(s, p):
        pid = K.Dedup.key(json.dumps(p, default=str))
        s.d[pid] = p
        return pid

    def get(s, pid):
        return s.d.get(pid)


C._store = PS()

N = K.now().replace(tzinfo=None, microsecond=0)


def ev(t, dm, dur=60, loc=""):
    st = (N + timedelta(minutes=dm)).replace(tzinfo=timezone(timedelta(hours=2)))
    return {"id": "e_" + t[:5] + str(dm), "summary": t,
            "start": {"dateTime": st.isoformat()},
            "end": {"dateTime": (st + timedelta(minutes=dur)).isoformat()},
            "location": loc}


RAW = [ev("Лікар", 3 * 24 * 60, loc="Košice"), ev("Зустріч Maroš", 24 * 60, loc="BA"),
       ev("🍵 Чай", 26 * 60), ev("🌙 Нічна зміна", 30 * 60), ev("Хімчистка", 4 * 24 * 60)]
C._raw_events = lambda hours_ahead=C.DEFAULT_HOURS: sorted(
    [x for x in (C._norm(e) for e in RAW) if x], key=lambda z: z["start"])

fails = []


def ck(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        fails.append(m)


print("=== 1. AI-коментар у нагадуванні ===")
n = C.tick()
txt = "\n--\n".join(t for t, _ in SENT)
ck(n >= 2, "надіслано %d" % n)
ck("🤖" in txt, "AI-коментар у карточці")
ck("Тестова AI-порада" in txt, "текст від Gemini підставлений")
ck(any("cw_ai_" in json.dumps(kb) for _, kb in SENT), "кнопка AI-підготовка є")
print("   AI-викликів:", len(GEM))

print("=== 2. AI не для рутини/змін ===")
prompts = " ".join(GEM)
ck("Чай" not in prompts and "Нічна зміна" not in prompts, "рутина/зміна не йдуть в AI")

print("=== 3. кеш per event|stage: різні тексти на різні етапи ===")
before = len(GEM)
e0 = C._raw_events()[0]
a = C._ai_note(e0, "t30")
b = C._ai_note(e0, "t2h")
ck(a != b, "різні етапи -> різні AI-коментарі")
ck(len(GEM) - before == 2, "2 нові етапи -> 2 виклики (got %d)" % (len(GEM) - before))
a2 = C._ai_note(e0, "t30")
ck(a2 == a and len(GEM) - before == 2, "той самий етап переюзує кеш (без нового виклику)")

print("=== 4. лімітів немає ===")
print("   згенеровано сьогодні:", C._ai_used_today())
before4 = len(GEM)
ev_new = dict(C._raw_events()[0])
for i in range(25):
    ev_new = dict(C._raw_events()[0]); ev_new["id"] = "many_%d" % i
    C._ai_note(ev_new, "t30")
ck(len(GEM) - before4 == 25, "25 подій -> 25 AI-викликів без блокування (got %d)" % (len(GEM) - before4))
ck(C._ai_used_today() >= 25, "лічильник статистики рахує (%d)" % C._ai_used_today())

print("=== 5. fallback без ключа ===")
C.K.GEMINI_KEY = ""
ev2 = dict(C._raw_events()[1])
ev2["id"] = "no_key_ev"
ck(C._ai_note(ev2, "t30") == "", "без ключа -> порожньо (локальний шаблон)")
SENT.clear()
C._send_event(ev2, "t30")
ck("<i>" in SENT[0][0], "карточка все одно надіслана з локальним hint")
C.K.GEMINI_KEY = "fake"

print("=== 6. AI-висновок в агенді і тижні ===")
SENT.clear()
C.agenda(force=True)
ck("AI-висновок" in SENT[0][0], "агенда має AI-висновок")
SENT.clear()
C.week(force=True)
ck("AI-стратегія тижня" in SENT[0][0], "тиждень має AI-стратегію")

print("=== 7. do_ai ===")
pid = C._store.put({"evid": C._raw_events()[0]["id"], "title": "Лікар",
                    "when": "12:00–13:00", "stage": "t30", "location": "Košice"})
r = C.do_ai(pid)
ck(bool(r.get("ok") and r.get("text")), "do_ai повернув текст: %s" % str(r.get("text"))[:40])
ck(any("ai_prep" in json.dumps(v, default=str) for v in STORE.get(C.ACK_FILE, {}).values()),
   "ai_prep збережено в ack")
ck(C.do_ai("no_such_pid").get("error") == "payload_missing", "мертвий payload -> payload_missing")

print("\n" + ("❌ FAIL: " + str(fails) if fails else "✅ ALL PASS"))
