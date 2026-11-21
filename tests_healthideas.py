"""Офлайн-тести healthideas.py: тригери лише з Garmin-даних, ліміти, зміна."""
import os, sys, types
from datetime import datetime
os.environ.setdefault("TELEGRAM_TOKEN", "x"); os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
import healthideas as HI

FAILS = []
def check(n, c, extra=""):
    print(("  ok   " if c else "  FAIL ") + n + (" " + str(extra) if not c else ""))
    if not c: FAILS.append(n)

def m(last, avg7=None, days_ago=0, day="2026-11-21", delta=None, n=7):
    return {"last": last, "avg7": avg7, "days_ago": days_ago, "last_day": day, "delta": delta, "n": n}

def base(**kw):
    a = {"today": "2026-11-21", "shift_today": "free", "shift_tomorrow": "free",
         "hrv": m(48, 50), "sleep": m(7.5, 7), "steps": m(5000), "energy": m(70),
         "hr": m(60, 60), "vo2max": m(None), "weight": {"last": 80.0, "days_ago": 1, "to_goal": 5.0}}
    a.update(kw); return a

def keys(a, h, wd_date=(2026, 11, 21), minute=0, streak=0):
    d = datetime(*wd_date, h, minute)
    return [c[0] for c in HI.candidates(a, d, streak=streak)]

# 1. HRV просів -> recovery_easy, і НЕ run_ready
k = keys(base(hrv=m(40, 50)), 11)
check("hrv drop -> recovery_easy", "recovery_easy" in k and "run_ready" not in k, k)
# 2. добре відновлення, вихідний -> run_ready
k = keys(base(hrv=m(51, 50)), 11)
check("good recovery free day -> run_ready", "run_ready" in k, k)
# 3. нічна -> без run_ready
k = keys(base(hrv=m(51, 50), shift_today="night"), 11)
check("night shift -> no run_ready", "run_ready" not in k, k)
# 4. застарілі дані -> нічого
k = keys(base(hrv=m(40, 50, days_ago=3)), 11)
check("stale hrv -> nothing", "recovery_easy" not in k, k)
# 5. короткий сон
k = keys(base(sleep=m(5.0, 7)), 10)
check("short sleep", "sleep_short" in k, k)
# 6. кроки відстають вдень
k = keys(base(steps=m(2000)), 17)
check("steps pace", "steps_pace" in k, k)
k = keys(base(steps=m(2000), shift_today="night"), 17)
check("steps pace not on night", "steps_pace" not in k, k)
k = keys(base(steps=m(2000, day="2026-11-20", days_ago=1)), 17)
check("steps pace needs today's data", "steps_pace" not in k, k)
# 7. пульс
k = keys(base(hr=m(70, 62)), 12)
check("rhr_up", "rhr_up" in k, k)
# 8. вага — тільки понеділок
k = keys(base(), 10, (2026, 11, 23))
check("weight_week monday", "weight_week" in k, k)
k = keys(base(), 10)
check("weight_week not saturday", "weight_week" not in k, k)
# 9. серія кроків
k = keys(base(), 12, streak=4)
check("steps_streak", "steps_streak" in k, k)
# 10. вікно: вночі нічого (крім post_night)
k = keys(base(hrv=m(40, 50), sleep=m(5, 7)), 3)
check("night window silent", k == [], k)
k = keys(base(shift_today="after_night"), 7, minute=40)
check("post_night 07:40", k == ["post_night"], k)
k = keys(base(shift_today="after_night"), 7, minute=10)
check("post_night not before 06:30-07:30", k == [], k)
# 11. завтра рання
k = keys(base(shift_tomorrow="early"), 20)
check("early_tomorrow", "early_tomorrow" in k, k)
# 12. VO2
k = keys(base(vo2max=m(50, delta=1.0)), 12)
check("vo2 up", "vo2_trend" in k, k)
# 13. нормальні дані -> тихо
check("all normal -> silent", keys(base(), 12) == [], keys(base(), 12))

# ── run(): ліміти, cooldown, без AI ──
STORE = {}; SENT = []
K = HI.K
K.load = lambda f, default=None: STORE.get(f, default if default is not None else {})
K.save = lambda f, d: STORE.__setitem__(f, d)
K.log = lambda *a, **k: None
K.send_card = lambda t, kb=None, tag="", chat_id=None: (SENT.append(t) or True)
K.gemini_text = lambda *a, **k: ""
K.rate_ok = lambda f, g: True
K.rate_mark = lambda f: None
K.now = lambda: datetime(2026, 11, 21, 11, 0)
HI.H._muted = lambda: False
HI.H._journal = lambda *a, **k: None
HI.H.analytics = lambda d=14: base(hrv=m(40, 50), hr=m(70, 62))
HI.H._shift_state = lambda: ""
HI.H.facts_block = lambda a: "facts"
HI._steps_streak = lambda: 0
n = HI.run()
check("run sends 1", n == 1 and len(SENT) == 1, SENT)
check("fallback text is deterministic action", "легший день" in SENT[0] and "HRV 40" in SENT[0], SENT)
n = HI.run()
check("min gap blocks second", n == 0 and len(SENT) == 1)
K.now = lambda: datetime(2026, 11, 21, 13, 0)
n = HI.run()
check("after gap sends next (other key)", n == 1 and "Пульс" in SENT[1], SENT)
K.now = lambda: datetime(2026, 11, 21, 15, 0)
n = HI.run()
check("cooldown: same keys not repeated", n == 0, SENT)
STORE["healthideas_state.json"]["count"] = 4
STORE["healthideas_state.json"]["seen"] = {}
K.now = lambda: datetime(2026, 11, 21, 18, 0)
check("daily cap", HI.run() == 0)
K.now = lambda: datetime(2026, 11, 22, 11, 0)
check("next day resets cap", HI.run() == 1)
print("FAILS:", FAILS); sys.exit(1 if FAILS else 0)
