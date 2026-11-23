import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timedelta
import storage, mail_seen as M
fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)
store = {}
storage.load = lambda f, default=None: store.get(f, default)
storage.save = lambda f, d: store.__setitem__(f, d) or True
t0 = datetime(2026, 11, 14, 10, 0)
items = [{"uid": i} for i in (1, 2, 3)]
new, old = M.split(items, t0)
ok(len(new) == 3 and old == [], "перший звіт: усі нові")
M.mark(new, t0)
new, old = M.split(items + [{"uid": 4}], t0 + timedelta(hours=3))
ok([i["uid"] for i in new] == [4] and len(old) == 3, "другий звіт: лише новий лист")
new, old = M.split(items, t0 + timedelta(days=15))
ok(len(new) == 3, "через 15 діб лист показується знову (нагадування)")
print("FAILS:", fails or "none"); sys.exit(1 if fails else 0)
