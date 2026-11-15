import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import durable, ai_kit
FAILS = []
def ok(m, c):
    print("ok  " if c else "FAIL", m)
    if not c: FAILS.append(m)
store = {}
ai_kit.load = lambda n, default=None: store.get(n, default)
ai_kit.save = lambda n, d: store.__setitem__(n, d) or True
tmp = os.path.join(tempfile.mkdtemp(), "data", "x.json")
durable.save("x.json", {"a": 1}, tmp)
ok("записано у гілку data", store.get("x.json") == {"a": 1})
ok("записано локально", os.path.exists(tmp))
os.remove(tmp)                      # імітація редеплою: локального файлу нема
ok("після 'редеплою' стан читається з data", durable.load("x.json", tmp) == {"a": 1})
store.clear()
durable.save("y.json", {"k": 1}, tmp)
store.clear()                       # data недоступна -> локальний
ok("fallback на локальний", durable.load("y.json", tmp) == {"k": 1})
big = {str(i): i for i in range(500)}
durable.save("z.json", big, None, keep=100)
ok("підрізає до keep", len(store["z.json"]) == 100 and "499" in store["z.json"])
print("FAILS:", FAILS or "none"); sys.exit(1 if FAILS else 0)
