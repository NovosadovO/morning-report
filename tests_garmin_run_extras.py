"""garmin_sync._fetch_run_activities (+3 дні назад, extras) і running.save_from_garmin (доповнення)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import garmin_sync as G, running
fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

class C:
    def get_activities(self, a, b):
        return [
            {"activityId": 1, "startTimeLocal": "2026-11-13 07:00:00", "activityType": {"typeKey": "running"},
             "distance": 10000, "duration": 3600, "averageHR": 150, "maxHR": 171,
             "aerobicTrainingEffect": 3.4, "averageRunningCadenceInStepsPerMinute": 168, "calories": 700},
            {"activityId": 2, "startTimeLocal": "2026-11-11 18:00:00", "activityType": {"typeKey": "running"},
             "distance": 8000, "duration": 3000, "averageHR": 145},
            {"activityId": 3, "startTimeLocal": "2026-11-05 18:00:00", "activityType": {"typeKey": "running"}, "distance": 5000, "duration": 2000},
            {"activityId": 4, "startTimeLocal": "2026-11-13 09:00:00", "activityType": {"typeKey": "yoga"}, "distance": 0, "duration": 1000},
        ]
acts = G._fetch_run_activities(C(), "2026-11-13", back_days=3)
ok({a["activity_id"] for a in acts} == {1, 2}, "3 дні назад: 13.11 і 11.11, не 05.11, не йога")
ok(acts[0]["max_hr"] == 171 and acts[0]["aerobic_te"] == 3.4 and acts[0]["average_cadence"] == 168, "extras")
ok("max_hr" not in acts[1], "нема даних — нема ключа")
acts0 = G._fetch_run_activities(C(), "2026-11-13")
ok(len(acts0) == 1, "без back_days — лише день")

db = {}
running._load = lambda: db
running._save_key = lambda k, rec: db.__setitem__(k, rec) or True
a1 = dict(acts[0])
ok(running.save_from_garmin(a1) is True and db["garmin_1"]["max_hr"] == 171, "новий запис з extras")
db["garmin_1"].pop("max_hr")
ok(running.save_from_garmin(a1) is False and db["garmin_1"]["max_hr"] == 171, "існуючий доповнено, але не «новий»")
print("FAILS:", len(fails)); sys.exit(1 if fails else 0)
