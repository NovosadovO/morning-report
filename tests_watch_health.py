"""watch_health: статуси sent/skipped/error, причина мовчання, звіт; why() у модулях."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage, watch_health as W
fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

store = {}
storage.load = lambda f, default=None: store.get(f, default)
storage.save = lambda f, d: store.__setitem__(f, d) or True

def silent():
    W.why("m1", "відхилень нема")
    return False
ok(W.track("m1", silent) is False and store[W.FILE]["m1"]["reason"] == "відхилень нема"
   and store[W.FILE]["m1"]["status"] == "skipped", "skipped з причиною")
n0 = dict(store[W.FILE]["m1"])
store.clear()
W.track("m1", silent)
ok(W.FILE not in store, "те саме вдруге — без нового запису (не сміти комітами)")
ok(W.track("m2", lambda: True) is True and store[W.FILE]["m2"]["status"] == "sent"
   and store[W.FILE]["m2"]["last_sent"], "sent")
def boom(): raise ValueError("x")
ok(W.track("m3", boom) is False and store[W.FILE]["m3"]["status"] == "error"
   and "ValueError" in store[W.FILE]["m3"]["reason"], "error не кидає виняток")
r = W.report()
ok("m2" in r and "m3" in r and "ValueError" in r, "report містить усе")

# модулі повертають причину
import garmin_insight as G, ai_kit as K
K.rate_ok = lambda *a, **k: False
import quiet
quiet.blocked = lambda kind="msg": False
W._reasons.clear()
ok(G.run() is False and "інтервал" in W._reasons.get("garmin_insight", ""), "garmin_insight: причина — інтервал")
import market_insight as M
W._reasons.clear()
ok(M.run() is False and "інтервал" in W._reasons.get("market_insight", ""), "market_insight: причина — інтервал")
import run_insight as R
W._reasons.clear()
R._gates = lambda f: True
R.runs = lambda *a, **k: []
R.K.rate_ok = lambda *a, **k: True
ok(R.run_post() is False and "пробіжок" in W._reasons.get("run_insight", ""), "run_insight: нема пробіжок")
print("FAILS:", fails or "none"); sys.exit(1 if fails else 0)
