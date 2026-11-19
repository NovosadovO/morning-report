import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import tzclock as T
fails = 0
def ok(c, m):
    global fails
    print(("OK   " if c else "FAIL ") + m)
    if not c: fails += 1
Z = ZoneInfo("Europe/Bratislava")
# порівняння з tzdata щопівгодини за 2025-2028
t = datetime(2025, 1, 1, tzinfo=timezone.utc); bad = 0
while t.year < 2029:
    if T.local_offset(t) != t.astimezone(Z).utcoffset(): bad += 1
    t += timedelta(minutes=30)
ok(bad == 0, "збігається з zoneinfo 2025-2028 (розбіжностей %d)" % bad)
ok(T.local_offset(datetime(2026, 10, 25, 0, 59)) == timedelta(hours=2), "25.10 00:59 UTC ще літо")
ok(T.local_offset(datetime(2026, 10, 25, 1, 0)) == timedelta(hours=1), "25.10 01:00 UTC уже зима")
ok(T.local_offset(datetime(2026, 3, 29, 1, 0)) == timedelta(hours=2), "29.03 01:00 UTC літо")
ok(T.local_offset(datetime(2026, 11, 19, 5, 0, tzinfo=timezone.utc)) == timedelta(hours=1), "19.11 = +1")
ok(T.local_offset(datetime(2026, 7, 1, 12, 0)) == timedelta(hours=2), "липень = +2")
ok(T.iso_offset(datetime(2026, 11, 19, 6, 0)) == "+01:00", "подія 19.11 06:00 -> +01:00")
ok(T.iso_offset(datetime(2026, 7, 1, 6, 0)) == "+02:00", "подія 01.07 06:00 -> +02:00")
ok(T.iso_offset(datetime(2026, 10, 25, 12, 0)) == "+01:00", "25.10 12:00 -> +01:00")
ok(T.iso_offset(datetime(2026, 10, 24, 23, 0)) == "+02:00", "24.10 23:00 -> +02:00")
ok(T.iso_offset(datetime(2026, 3, 29, 12, 0)) == "+02:00", "29.03 12:00 -> +02:00")
ok(T.iso_offset(datetime(2026, 3, 28, 12, 0)) == "+01:00", "28.03 12:00 -> +01:00")
print("FAILS", fails); sys.exit(1 if fails else 0)
