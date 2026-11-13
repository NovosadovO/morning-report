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
chk(O._topic("🏃 <b>Біг · 13.11: 8 км за 50:00</b>")[0] == "run_ai", "run_ai тема")
chk(O._topic("🏃 Біг · готовність 🟢")[0] == "run_ai", "run_ai pre")
chk(O._topic("🏃 Час бігти! 25-35 хв")[0] == "run_nudge", "run_nudge ще ловиться")
chk(O._topic_reason("🏃 Час бігти! 25-35 хв", [row("run_ai", 5)]), "шаблон бігу блокується після AI-розбору")
chk(not O._topic_reason("🏃 Час бігти! 25-35 хв", [row("run_ai", 30)]), "через 30 год шаблон вже не блокується кросом")
import foresight as F
chk(F._try_new_pick.__doc__ is not None, "foresight loads")
print("outbox topic: ok=%d fail=%d" % (ok, fail))
