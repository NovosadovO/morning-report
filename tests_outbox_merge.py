"""outbox._flush зливає журнал з іншого процесу замість затирання; report-скрипти ставлять фільтр."""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outbox, storage
fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

remote = {"sent": [{"ts": "2026-11-14T10:00:00", "key": "report2"}], "qs": [], "drops": [], "stats": {"2026-11-14": {"sent": 5}}}
saved = {}
storage.load = lambda f, default=None: remote if f == outbox.LOG_FILE else default
storage.save = lambda f, d: saved.update(d) or True
outbox._state.update({"data": {"sent": [{"ts": "2026-11-14T11:00:00", "key": "main"}], "qs": [], "drops": [],
                               "stats": {"2026-11-14": {"sent": 3, "dropped": 2}}}, "dirty": True, "flushed": 0})
outbox._flush(force=True)
ok([x["key"] for x in saved["sent"]] == ["report2", "main"], "записи двох процесів збережені по порядку")
ok(saved["stats"]["2026-11-14"] == {"sent": 5, "dropped": 2}, "лічильники: максимум з обох")
for f in ("report2.py", "report_defi.py", "report_social.py", "report_etf.py"):
    s = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f)).read()
    ok("outbox" in s.split('if __name__ == "__main__":')[-1], f + ": outbox.install у __main__")
print("FAILS:", fails or "none"); sys.exit(1 if fails else 0)
