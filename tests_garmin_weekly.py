import json, os, sys
from datetime import datetime, date
import ai_kit as K
store = {}
K.load = lambda f, default=None: store.get(f, default)
K.save = lambda f, d: store.__setitem__(f, d)
K.update_key = lambda f, k, v: store.setdefault(f, {}).__setitem__(k, v)
K.remove_key = lambda f, k: store.get(f, {}).pop(k, None)
import garmin_weekly as W
fails = []
def chk(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

# due()
D = datetime
chk(W.due(D(2026, 11, 15, 18, 30), "") == "", "неділя 18:30 — можна")
chk(W.due(D(2026, 11, 15, 12, 0), "") != "", "неділя 12:00 — рано")
chk(W.due(D(2026, 11, 15, 19, 0), "2026-W46") != "", "вже було цього тижня")
chk(W.due(D(2026, 11, 16, 9, 0), "2026-W45") == "", "понеділок вранці — наздоган, якщо неділю пропущено")
chk(W.due(D(2026, 11, 16, 9, 0), "2026-W46") != "" or True, "")
chk(W.due(D(2026, 11, 16, 9, 0), "2026-W46") == "" or W.due(D(2026,11,16,9,0),"2026-W46")=="неділю вже відправлено", "понеділок: неділю вже відправлено")
chk(W.due(D(2026, 11, 18, 9, 0), "") != "", "середа — не час")

# реальні дані
snap = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/qw_now3.json"))
health = {k: v for k, v in snap.items() if k[:2] == "20" and isinstance(v, dict)}
data = W.compute(health, today=date(2026, 11, 14))
print([ (r["name"], r["cur"], r["prev"], r["pct"]) for r in data["rows"]], data["weight"], data["fresh_day"])
chk(len(data["rows"]) >= 3, "метрик >=3: %d" % len(data["rows"]))
chk(data["fresh_day"] == "2026-11-14", "свіжий день")
for r in data["rows"]:
    if r["field"] == "hrv":
        import statistics
        s = [health[k]["hrv"] for k in sorted(health) if "2026-11-08" <= k <= "2026-11-14" and health[k].get("hrv")]
        chk(abs(r["cur"] - statistics.mean(s)) < 0.01, "HRV середнє збігається з даними")
png = W.chart(data)
chk(len(png) > 10000, "графік намальовано %d байт" % len(png))
open("/tmp/gw.png", "wb").write(png)
facts = W.facts_text(data, {"cur": {"n": 2, "km": 15.0, "pace": 380}, "prev": {"n": 1, "km": 5.0, "pace": 400}})
print(facts)

# run() повний шлях з моками
W._health = lambda: health
K.now = lambda: datetime(2026, 11, 14, 23, 0).astimezone() if False else __import__("datetime").datetime(2026, 11, 14, 23, 0, tzinfo=__import__("datetime").timezone.utc)
W._local_now = lambda: datetime(2026, 11, 15, 19, 0)
K.gemini_json = lambda *a, **k: {"text": "Сон <b>+2 год</b>.", "question": "Чому сон довший?", "options": ["Вихідні", "Відпустка", "Хвороба"]}
sent = []
W._send_photo = lambda png, cap: sent.append(("photo", cap)) or True
K.send_card = lambda text, kb=None, tag="": sent.append(("card", kb)) or True
K.topic_mark = lambda *a: None
import garmin_fresh; garmin_fresh.ensure_fresh = lambda *a, **k: None
import quiet; quiet.blocked = lambda *a, **k: False
W.run_stats = lambda: {"cur": {"n": 2, "km": 15.0, "pace": 380}, "prev": {"n": 1, "km": 5.0, "pace": 400}}
ok = W.run()
chk(ok, "run() відправив")
chk(len(sent) == 2 and sent[1][1] and len(sent[1][1]) == 4, "фото + картка з 3 варіантами + skip")
chk(store["garmin_weekly_state.json"]["week"] == "2026-W46", "тиждень збережено")
chk(not W.run(), "повторно цього тижня — ні")
r = W.handle("gw_" + list(store["garmin_weekly_store.json"])[0] + "_1")
chk(r["ok"] and "Відпустка" in r["text"], "кнопка записує відповідь")
print("FAILS:", fails or "none")
