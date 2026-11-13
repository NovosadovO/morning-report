import outbox as O
from datetime import datetime, timedelta
now = datetime.now()
def row(topic, h): return {"topic": topic, "ts": (now - timedelta(hours=h)).isoformat(timespec="seconds")}
ok = fail = 0
def chk(c, m):
    global ok, fail
    if c: ok += 1
    else: fail += 1; print("FAIL", m)
chk(O._topic_reason("✨ СПРОБУЙ НОВЕ Після нічних змін", [row("trynew", 2)]), "trynew 2h blocked")
chk(not O._topic_reason("✨ СПРОБУЙ НОВЕ Після нічних змін", [row("trynew", 80)]), "trynew 80h passes")
chk(O._topic_reason("🏃 6 днів без пробіжки! Сьогодні вихідний", [row("run_nudge", 3)]), "run nudge repeat blocked")
chk(not O._topic_reason("Звичайний лист від банку про платіж", [row("run_nudge", 1)]), "other topic passes")
import foresight as F
chk(F._try_new_pick.__doc__ is not None, "foresight loads")
print("outbox topic: ok=%d fail=%d" % (ok, fail))
