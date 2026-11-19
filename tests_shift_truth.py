"""Тест: бот не каже «зміна завершилась», поки зміна триває (19.11, скріншот Олега)."""
import sys; sys.path.insert(0, '.')
from datetime import datetime, timezone
import nowctx, context, truth, tzclock

CLOCK = {"t": None}
class FD(datetime):
    @classmethod
    def now(cls, tz=None):
        b = CLOCK["t"]
        return b.astimezone(tz) if tz else b
for m in (context, nowctx, tzclock):
    m.datetime = FD

def setup(kind, utc):
    CLOCK["t"] = utc
    day = utc.strftime("%Y-%m-%d")
    name, s, e = {"early": ("Рання зміна", "06:00", "18:00"),
                  "night": ("Нічна зміна", "18:00", "06:00")}.get(kind, (None,) * 3)
    ev = []
    if name:
        ev = [{"summary": name, "start": {"dateTime": f"{day}T{s}:00+01:00"},
               "end": {"dateTime": f"{day}T{e}:00+01:00"}}]
    context._get_token = lambda: "x"
    context._fetch_events_for_day = lambda tok, off: ev if off == 0 else []
    context._SHIFT_CACHE.clear()
    nowctx._manual_fresh = lambda: None
    nowctx.manual_shift = lambda: None

fails = []
def check(name, cond):
    print(("ok  " if cond else "FAIL"), name)
    if not cond: fails.append(name)

BAD = "Олеже, завершилася твоя ранкова зміна, тож час розібратися з тим, що відбулося."
GOOD = "Сьогодні вода: 2 л рівномірно. Після зміни — легка прогулянка."

# 1. рання зміна триває: 07:28 CET = 06:28 UTC
setup("early", datetime(2026, 11, 19, 6, 28, tzinfo=timezone.utc))
w = nowctx.where()
check("early in progress: state work", w["state"] == "work")
check("early in progress: phase", w["phase"] == "in_progress" and w["shift"] == "early")
check("shift_line says ТРИВАЄ", "ТРИВАЄ" in nowctx.shift_line() and "07:28" in nowctx.shift_line())
check("lie detected", bool(truth.audit("дані", BAD + " " + GOOD, "t")))
cl, cut = truth.sanitize("дані", BAD + " " + GOOD)
check("sanitize cuts lie", cut >= 1 and "завершилася" not in cl and "Сьогодні вода" in cl)
check("good text passes", not [i for i in truth.audit("дані 2 л", GOOD + " Фокус: сон і вода.", "t") if "ТРИВАЄ" in i])
check("other shift yesterday ok", not truth._shift_lie("Вчора нічна зміна завершилась пізно.", w))
check("night shift mention ok", not truth._shift_lie("Нічна зміна завершилась о 6, тепер відпочивай.", w))
check("future ok", not truth._shift_lie("Рання зміна завершиться о 18:00.", w))
check("ти вдома lie", truth._shift_lie("Ти вдома, тож пробіжка.", w))

# 2. після ранньої: 19:00 CET = 18:00 UTC
setup("early", datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc))
w = nowctx.where()
check("after early: ended", w["phase"] == "ended")
check("after early: ended text allowed", not truth._shift_lie(BAD, w))

# 3. нічна триває: 23:00 CET
setup("night", datetime(2026, 11, 19, 22, 0, tzinfo=timezone.utc))
w = nowctx.where()
check("night in progress", w["phase"] == "in_progress" and w["state"] == "work")
check("night lie detected", truth._shift_lie("Нічна зміна завершилась, час спати.", w))

# 4. DST: час у Кошице
CLOCK["t"] = datetime(2026, 11, 19, 5, 0, 48, tzinfo=timezone.utc)
check("local_offset winter = +1", tzclock.local_offset() .total_seconds() == 3600)
check("context._now_local 06:00", context._now_local().hour == 6)

print("FAILS:", fails); sys.exit(1 if fails else 0)
