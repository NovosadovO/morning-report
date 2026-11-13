"""garmin_news.detect, garmin_sync.backfill, qwsync.mirror_weight, ai_kit.md_to_html, briefing_ctx."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import garmin_news as N, garmin_sync as G, qwsync, storage, ai_kit as K, briefing_ctx as B
fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

# ── detect
T = "2026-11-13"
health = {f"2026-11-{d:02d}": {"sleep_total_min": 420 + d, "hrv": 60, "hr_avg": 50} for d in range(1, 13)}
news, st = N.detect({"steps": 1200}, health, {}, T)
ok(news == [], "нічого нового -> порожньо")
news, st = N.detect({"sleep_total_min": 300, "hrv": 40, "steps": 1200}, health, st, T)
ok(any("Сон" in n for n in news) and any("HRV" in n for n in news), "новий сон і HRV")
news2, st2 = N.detect({"sleep_total_min": 300, "hrv": 40, "steps": 1300}, health, st, T)
ok(news2 == [], "той самий сон вдруге не повторюється")
news3, st3 = N.detect({"steps": 5200}, health, st2, T)
ok(any("5 000" in n for n in news3), "поріг 5000 кроків: " + str(news3))
news4, _ = N.detect({"steps": 5300}, health, st3, T)
ok(news4 == [], "без нового порогу тихо")
news5, _ = N.detect({}, health, {}, T, backfilled=["2026-11-09", "2026-11-10"])
ok(any("09.11" in n for n in news5), "backfilled: " + str(news5))
s = N.remember_ai({}, "a"); [N.remember_ai(s, x) for x in "bcde"]
ok(len(s["prev_ai"]) == 4 and s["prev_ai"][-1] == "e", "remember_ai тримає 4")

# ── backfill з фейковим сховищем
store = {"qwatch_data.json": {
    "2026-11-12": {"steps": 8000, "hrv": 55, "sleep_total_min": 400, "saved_at": "2026-11-12T23:00:00"},
    "2026-11-08": {"steps": 3000},
}}
storage.load = lambda f, default=None: store.get(f, default)
storage.save = lambda f, d: store.__setitem__(f, d) or True
fetched = []
G._fetch_today = lambda c, d: (fetched.append(d) or {"date": d, "steps": 1000})
saved = []
qwsync.save = lambda p, notify=True: saved.append(p["date"]) or {"ok": True}
G._local_day = lambda: T
import time; time.sleep = lambda s: None
done = G.backfill(object(), T, days=7, max_days=3)
ok(len(done) == 3 and fetched[0] == "2026-11-12", "вчора (saved_at до півночі) першим, макс 3: " + str(fetched))
ok(store[G.BACKFILL_FILE]["2026-11-12"]["n"] == 1, "лічильник спроб")
fetched.clear()
G.backfill(object(), T, days=7, max_days=3)
ok("2026-11-12" not in fetched and len(fetched) <= 3, "не частіше 12 год: " + str(fetched))
for i in range(1, 8): store[G.BACKFILL_FILE]["2026-11-%02d" % (13 - i)] = {"ts": "2020-01-01T00:00:00", "n": 3}
fetched.clear()
G.backfill(object(), T, days=7, max_days=3)
ok(fetched == [], "3 спроби — більше не пробуємо")

# ── mirror_weight
w = {f"2026-10-{d:02d}": 82.0 for d in range(1, 9)}
w["2026-11-07"] = 83.0
wrote = {}
storage._load_github = lambda f: dict(w)
storage.save_weight = lambda d: wrote.update(d) or True
n = qwsync.mirror_weight({"2026-11-07": {"weight_kg": 90}, "2026-11-08": {"weight_kg": 84.34}})
ok(n == 1 and wrote.get("2026-11-08") == 84.3 and wrote["2026-11-07"] == 83.0, "ручна вага виграє, нова дописана")
wrote.clear()
storage._load_github = lambda f: None
ok(qwsync.mirror_weight({"2026-11-08": {"weight_kg": 84}}) == 0 and not wrote, "збій читання — не пишемо")
storage._load_github = lambda f: {"2026-11-01": 80}
ok(qwsync.mirror_weight({"2026-11-08": {"weight_kg": 84}}) == 0 and not wrote, "підозріло малий файл — не пишемо")

# ── md_to_html
h = K.md_to_html("### Заголовок\n**жирний** текст\n---\n* пункт")
ok("<b>жирний</b>" in h and "###" not in h and "---" not in h and "• пункт" in h, "md_to_html: " + repr(h))

# ── briefing_ctx
import datetime
p = B.build_prompt(datetime.datetime(2026, 11, 13, 9, 0), "x", "REP", "", "SIG", ["старий вступ"], "sd")
ok("500" not in p and "старий вступ" in p and "SIG" in p and "не вигадуй" in p, "промпт брифінгу")

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
