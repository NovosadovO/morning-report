"""Тести run_insight: метрики, готовність, розбір після бігу, pre-run, кнопки.
Gemini/Telegram/Garmin замоковані."""
import sys, os
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_kit as K
import run_insight as R

fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

NOW = R._now_naive()
def act(days_ago, km, minutes, hr, **kw):
    d = (NOW - timedelta(days=days_ago)).replace(hour=18, minute=0, second=0, microsecond=0)
    a = {"type": "Run", "distance": km * 1000, "moving_time": minutes * 60,
         "start_date_local": d.isoformat(), "average_heartrate": hr, "name": "Run"}
    a.update(kw); return a

ACTS = [act(30, 10, 64, 150), act(24, 10, 65, 151), act(18, 10.2, 64, 149),
        act(12, 10, 64, 150), act(2, 10, 62, 158, aerobic_te=3.4, max_hr=171, average_cadence=168),
        {"type": "Walk", "distance": 4000, "moving_time": 3000, "start_date_local": NOW.isoformat()}]
rs = R.runs(acts=ACTS)
ok(len(rs) == 5, "ходьба відсіяна, 5 пробіжок")
ok(abs(rs[-1]["pace"] - 372) < 1, "темп 6:12")
c = R.compare_last(rs, rs[-1])
ok(c["n"] == 4 and c["pace_diff"] < 0 and c["hr_diff"] > 5, f"порівняння: швидше і вищий пульс {c}")
ok(R.fmt_pace(372) == "6:12" and R.fmt_dur(3720) == "1:02:00", "формати")
wk = R.weekly_km(rs, 8, NOW); ok(len(wk) == 8 and sum(w[1] for w in wk) > 40, "тижні")
lr = R.load_ratio(rs, NOW); ok(lr["acute"] == 10 and lr["ratio"], f"acwr {lr}")

# готовність
snap_g = {"sleep_total_min": {"value": 460, "fresh": True}, "body_battery": {"value": 70, "fresh": True}}
rows_g = [{"field": "hrv", "last": 55, "base": 55, "z": 0.1}, {"field": "hr_avg", "last": 52, "base": 52, "z": 0.0}]
rd = R.readiness(snap_g, rows_g, rs[:-1], NOW); ok(rd["level"] == "green", "green " + str(rd))
rd = R.readiness({"sleep_total_min": {"value": 300, "fresh": True}}, rows_g, rs[:-1], NOW); ok(rd["level"] == "red", "red: сон 5 год")
rows_y = [{"field": "hrv", "last": 45, "base": 55, "z": -1.0}]
rd = R.readiness(snap_g, rows_y, rs[:-1], NOW); ok(rd["level"] == "yellow", "yellow: HRV z -1.0")
rows_r = [{"field": "hrv", "last": 40, "base": 55, "z": -2.1}]
rd = R.readiness(snap_g, rows_r, rs[:-1], NOW); ok(rd["level"] == "yellow" and "HRV" in rd["reasons"][0], "лише HRV z -2.1, решта ок → yellow")
rd = R.readiness({"sleep_total_min": {"value": 380, "fresh": True}}, rows_r, rs[:-1], NOW); ok(rd["level"] == "red", "HRV z -2.1 + сон 6.3 → red")
rd = R.readiness({"sleep_total_min": {"value": 460, "fresh": False}}, [], rs[:-1], NOW); ok(rd["level"] == "unknown", "застарілі дані → unknown, не green")
rs_hard = R.runs(acts=ACTS[:3] + [act(0.5, 16, 100, 160)])
rd = R.readiness(snap_g, rows_g, rs_hard, NOW); ok(rd["level"] == "red", "учора жорсткий біг → red")
pl = R.plan({"level": "yellow"}, rs); ok(pl["type"] == "easy" and 4 <= pl["km"] < 10 and pl["hr_cap"] < 150, f"plan yellow {pl}")
pl = R.plan({"level": "red"}, rs); ok(pl["type"] == "rest", "plan red")
rec = R.recovery(rs[-1]); ok(rec["hours"] in (24, 36, 48), "recovery " + str(rec["hours"]))
png = R.chart(rs, NOW); ok(len(png) > 3000 and png[:4] == b"\x89PNG", "графік png")

# ── повний прогін з моками ──
mem = {}
K.load = lambda f, default=None: mem.get(f, default)
K.save = lambda f, d: mem.__setitem__(f, d)
K.update_key = lambda f, k, v: mem.setdefault(f, {}).__setitem__(k, v)
K.rate_mark = lambda f: mem.__setitem__(f, {"last": K.now().isoformat()})
class FS:
    def __init__(s): s.d = {}
    def put(s, p): s.d["p1"] = p; return "p1"
    def get(s, i): return s.d.get(i)
    def drop(s, i): s.d.pop(i, None)
R._store = FS()
sent, photos, gem = [], [], []
K.send_card = lambda t, kb=None, tag="x": sent.append((t, kb)) or True
R._send_photo = lambda png, cap="": photos.append(len(png)) or True
K.topic_mark = lambda *a, **k: None
K.shift_map = lambda n=2: {K.today_str(): "free"}
K.gemini_json = lambda p, **k: (gem.append(p) or {"text": "<b>6:12/км</b> швидше за медіану, пульс вищий. Відновись.",
    "question": "Як ноги?", "options": ["Легко", "Нормально", "Важко", "Болить"]})
import running, garmin_fresh, garmin_insight, quiet, dismissed, ai_brain
running.get_activities = lambda days=30: ACTS
garmin_fresh.ensure_fresh = lambda *a, **k: {}
garmin_fresh.snapshot = lambda *a, **k: snap_g
garmin_insight.analyse = lambda *a, **k: rows_g
quiet.blocked = lambda *a, **k: False
dismissed.is_muted = lambda *a, **k: False
ai_brain.remember_answer = lambda *a, **k: mem.setdefault("ans", []).append((a, k))
ai_brain._answers_block = lambda **k: ""

# перший запуск: пробіжка 2 дні тому (48 год > 36) → тихо закрита; тут pre-run
mem.clear()
res = R.run_post()
ok(res is False and not sent, "стару (48г) пробіжку не розбираємо")
# свіжа пробіжка (3 год тому)
ACTS.append(act(0.1, 8, 50, 148, aerobic_te=2.9))
ACTS[-1]["start_date_local"] = (NOW - timedelta(hours=3)).isoformat()
mem.clear(); R._store = FS()
res = R.run_post()
ok(res is True and len(sent) == 1 and photos, "розбір свіжої пробіжки надіслано (+графік)")
t, kb = sent[0]
ok(t.startswith("🏃 <b>Біг ·"), "маркер «🏃 Біг ·» на початку")
ok(len(kb) == 5 and kb[0][0]["callback_data"] == "ri_p1_0" and kb[-1][0]["callback_data"] == "ri_skip_p1", "кнопки під питання")
ok("❓" in t and "Як ноги?" in t, "питання в тексті")
ok("не вигадуй" in gem[0].lower() or "нічого не вигадуй" in gem[0].lower(), "промпт забороняє вигадувати")
ok("6:" in gem[0] and "ПОРІВНЯННЯ" in gem[0], "в промпті порахункові числа")
sent.clear()
ok(R.run_post() is False and not sent, "та сама пробіжка вдруге не розбирається")
r = R.handle("ri_p1_3"); ok(r["ok"] and "Болить" in r["text"] and mem["ans"][0][1]["topic"] == "run", "відповідь записана в ai_brain")
ok(R.handle("ri_p1_0")["ok"] is False, "повторне натискання — закрите")

# pre-run: сьогодні біг був (3 год тому) → мовчимо
mem.clear(); sent.clear()
ok(R.run_pre(force=False) is False, "сьогодні вже бігав → не питаємо")
# pre-run: прибрати сьогоднішній біг, останній — 3 дні тому
ACTS.pop(); ACTS[-2] = act(3, 10, 62, 158)
R._store = FS()
class FixedNow:
    pass
orig_now = R._now_naive
R._now_naive = lambda: NOW.replace(hour=10, minute=0)
mem.clear(); sent.clear()
ok(R.run_pre() is True, "pre-run надіслано")
t, kb = sent[-1]
ok("готовність" in t and t.startswith("🏃"), "заголовок pre-run")
ok([b[0]["text"] for b in kb[:3]] == R.PRE_OPTS, "кнопки pre-run = Біжу/Не сьогодні…")
ok(R.run_pre() is False, "вдруге в межах 30 год — ні")
R._now_naive = lambda: NOW.replace(hour=23, minute=0)
mem.clear()
ok(R.run_pre() is False, "вночі (поза вікном) — ні")
R._now_naive = orig_now
# квайет
quiet.blocked = lambda *a, **k: True
mem.clear(); ok(R.run_post() is False and R.run_pre() is False, "тиша блокує")
print("FAILS:", len(fails))
sys.exit(1 if fails else 0)
