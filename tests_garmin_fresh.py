import json, time, os
from datetime import datetime, timedelta
import garmin_fresh as G
ok = fail = 0
def chk(c, m):
    global ok, fail
    if c: ok += 1
    else: fail += 1; print("FAIL", m)

now = G._local_now()
t = now.strftime("%Y-%m-%d"); y = (now - timedelta(days=1)).strftime("%Y-%m-%d"); o = (now - timedelta(days=4)).strftime("%Y-%m-%d")
def stamp(mins): return (now - timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M")
db = {o: {"hrv": 55, "weight_kg": 82.0, "saved_at": stamp(5000)},
      y: {"hrv": 40, "hr_avg": 49, "sleep_total_min": 419, "steps": 20707, "body_battery": 12, "saved_at": stamp(900)},
      t: {"hr_avg": 44, "body_battery": 100, "stress": 12, "steps": 43, "saved_at": stamp(50)}}
G._db = lambda: db
s = G.snapshot(db)
chk(s["hrv"]["age_days"] == 1 and s["hrv"]["fresh"], "hrv вчора = свіже")
chk(s["weight_kg"]["age_days"] == 4 and s["weight_kg"]["fresh"], "вага 4 дні, ліміт 7 = свіжа")
chk(s["steps"]["partial"] and s["steps"]["age_days"] == 0, "кроки сьогодні = частковий день")
chk(not s["sleep_total_min"]["fresh"] or s["sleep_total_min"]["age_days"] == 1, "сон вчора")
b = G.block()
chk("СВІЖЕ" in b and "ПРАВИЛО СВІЖОСТІ" in b, "блок має правила")
chk("не порівнюй з нормою" in b, "частковий день позначено")
print(b)

# ensure_fresh: свіжий кеш -> без запиту
calls = []
G._live_call = lambda: (calls.append(1), {"ok": True})[1]
db[t]["saved_at"] = stamp(5)
r = G.ensure_fresh(); chk(not calls and not r["refreshed"], "свіжий кеш не тягне")
# старий -> тягне
db[t]["saved_at"] = stamp(90); G._st.update(last_try=0, last_fail=0)
r = G.ensure_fresh(); chk(len(calls) == 1 and r["refreshed"], "старий кеш -> живий запит")
# rate limit
db[t]["saved_at"] = stamp(90)
r = G.ensure_fresh(); chk(len(calls) == 1, "не частіше за MIN_LIVE_GAP")
# невдача -> backoff
G._st.update(last_try=0, last_fail=0)
G._live_call = lambda: {"ok": False, "error": "login fail"}
r = G.ensure_fresh(); chk(not r["refreshed"] and "не вдався" in r["note"], "невдача зафіксована")
G._st["last_try"] = 0
r = G.ensure_fresh(); chk("недавно не відповів" in r["note"], "backoff після невдачі")
# таймаут не висить
G.LIVE_TIMEOUT_S = 1; G._st.update(last_try=0, last_fail=0)
G._live_call = lambda: time.sleep(5)
t0 = time.time(); G.ensure_fresh(); chk(time.time() - t0 < 3, "таймаут обмежує очікування")
# fresh_value
chk(G.fresh_value("hrv", 0) is None and G.fresh_value("hrv", 1) == 40, "fresh_value")
print("garmin_fresh: ok=%d fail=%d" % (ok, fail))
