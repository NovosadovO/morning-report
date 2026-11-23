import os
os.environ.setdefault("TELEGRAM_TOKEN","x"); os.environ.setdefault("TELEGRAM_CHAT_ID","1")
from datetime import datetime, timedelta
import monitor, context

now = datetime(2026,11,23,6,15)
def si(today, start): return {"today": today, "today_start": start}

# вечірня нічна СЬОГОДНІ 18:00 — о 06:15 «завершена» НЕ можна
assert not monitor._night_really_ended(si("night", datetime(2026,11,23,18,0)), now, "sleeping")
# вільний день
assert not monitor._night_really_ended(si("free", None), now, "home")
# реальна нічна з учора 18:00
assert monitor._night_really_ended(si("after_night", datetime(2026,11,22,18,0)), now, "post_shift")
assert monitor._night_really_ended(si("night", datetime(2026,11,22,18,0)), now, "working_night")
# /дома вище календаря
assert not monitor._night_really_ended(si("after_night", datetime(2026,11,22,18,0)), now, "home")
# початок не вечір
assert not monitor._night_really_ended(si("after_night", datetime(2026,11,22,9,0)), now, "post_shift")

# get_status: вечірня нічна сьогодні, 06:15 -> не post_shift
class FakeDT(datetime):
    cur = now
    @classmethod
    def now(cls, tz=None): return cls.cur
import nowctx
context._now_local = lambda: now
nowctx._manual_fresh = lambda: None
nowctx.manual_shift = lambda: None
st = context.get_status(si("night", datetime(2026,11,23,18,0)))
assert st == "home", st
context._now_local = lambda: datetime(2026,11,23,9,0)
assert context.get_status(si("night", datetime(2026,11,23,18,0))) == "home"
context._now_local = lambda: datetime(2026,11,23,17,0)
assert context.get_status(si("night", datetime(2026,11,23,18,0))) == "pre_shift"
context._now_local = lambda: datetime(2026,11,23,3,0)
assert context.get_status(si("night", datetime(2026,11,23,18,0))) == "sleeping"
assert context.get_status(si("night", datetime(2026,11,22,18,0))) == "working_night"
context._now_local = lambda: datetime(2026,11,23,7,0)
assert context.get_status(si("night", datetime(2026,11,22,18,0))) == "post_shift"
print("OK")
