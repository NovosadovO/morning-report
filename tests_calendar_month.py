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
    return "AI-план №%d 💪 Тримай фокус на інвестиціях і бігу." % len(GEM)


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


def ev(t, dd, hh=12, loc=""):
    st = (N + timedelta(days=dd)).replace(hour=hh, minute=0, second=0,
                                          tzinfo=timezone(timedelta(hours=2)))
    return {"id": "e_%s_%d" % (t[:5], dd), "summary": t,
            "start": {"dateTime": st.isoformat()},
            "end": {"dateTime": (st + timedelta(hours=1)).isoformat()}, "location": loc}


RAW = [ev("Тренування", 1, 17), ev("Presov meeting", 2, 9, "Prešov"),
       ev("Хімчистка", 3, 12), ev("Оплата страховки Kooperativa", 6, 12),
       ev("Візит до лікаря", 12, 10, "Košice"), ev("Зустріч Maroš InterFin", 19, 15),
       ev("День народження мами", 27, 0), ev("🍵 Чай", 5, 8),
       ev("🌙 Нічна зміна", 4, 18), ev("Далеко за місяцем", 45, 12)]
C._raw_events = lambda hours_ahead=C.DEFAULT_HOURS: sorted(
    [x for x in (C._norm(e) for e in RAW) if x], key=lambda z: z["start"])

fails = []


def ck(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        fails.append(m)


print("=== 1. month_text ===")
t = C.month_text(31)
print(t[:900])
ck("МІСЯЦЬ ВПЕРЕД" in t, "заголовок є")
for name in ("Тренування", "Хімчистка", "Візит до лікаря", "Зустріч Maroš InterFin",
             "День народження мами"):
    ck(name in t, "подія у місяці: %s" % name)
ck("Далеко за місяцем" not in t, "події за межами 31 дня не показані")
ck("Чай" not in t, "рутина не в місяці")
ck("Нічна зміна" not in t, "зміна не в місяці")
ck("Найщільніший тиждень" in t, "є аналітика завантаження")
ck("🗓" in t, "групування по тижнях")

print("=== 2. month() 1x/місяць ===")
SENT.clear()
ok = C.month(force=True)
ck(ok, "month(force) надіслав")
ck("AI-план на місяць" in SENT[0][0], "AI-план присутній")
ck("cw_ack_" in json.dumps(SENT[0][1]), "кнопка cw_ack є")
ck(len(SENT[0][0]) <= 4096, "довжина в межах Telegram (%d)" % len(SENT[0][0]))

print("=== 3. дедуп місяця ===")
STORE.setdefault(C.MONTHLY_FILE, {})["month"] = N.strftime("%Y-%m")
ck(C._monthly_done(), "_monthly_done True після позначки")
ck(C.month(force=False) is False, "повторно за той самий місяць не шле")

print("=== 4. upcoming_text 31 день для AI ===")
u = C.upcoming_text(31, limit=40)
print("  ", u[:300])
ck("Візит до лікаря" in u and "День народження мами" in u, "AI бачить події місяця")
ck("Далеко за місяцем" not in u, "за 31 день не тягне")
ck("Чай" not in u, "рутина не в AI-контексті")

print("=== 5. немає лімітів AI ===")
before = len(GEM)
C.month(force=True)
ck(len(GEM) > before, "AI викликається без блокувань")

print("\n" + ("❌ FAIL: " + str(fails) if fails else "✅ ALL PASS"))
