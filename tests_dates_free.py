import sys, os
sys.path.insert(0, "/home/user/bot")
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
import ai_kit as K

MEM = {}
K.load = lambda f, d=None, default=None: MEM.get(f, d if d is not None else (default if default is not None else {}))
def _save(f, data):
    MEM[f] = data
    return True
K.save = _save
def _upd(f, k, v):
    MEM.setdefault(f, {})[k] = v
    return True
K.update_key = _upd
K.remove_key = lambda f, k: MEM.get(f, {}).pop(k, None) is not None
K.send_card = lambda *a, **kw: True
from datetime import datetime
NOW = datetime(2026, 8, 21, 10, 0, 0)
K.now = lambda: NOW
K.today_str = lambda: "2026-08-21"

import dates_book as M
M._store = K.PayloadStore(M.STORE_FILE)

fails = 0
def ok(c, t):
    global fails
    print(("✅ " if c else "❌ ") + t)
    if not c:
        fails += 1

ok(M.looks_like_date_note("01.09. День народження Олі"), "ловить фразу Олега")
ok(M.looks_like_date_note("Річниця весілля 12.06"), "ловить річницю")
ok(not M.looks_like_date_note("Купити молоко 12.06"), "без маркера свята — не наше")
ok(not M.looks_like_date_note("День народження колись"), "без дати — не наше")
ok(not M.looks_like_date_note("/дата Мама 02.11"), "команду не перехоплюємо")

r = M.add_from_free_text("01.09. День народження Олі")
ok(r.get("ok"), f"додано: {r}")
ok(r["rec"]["name"] == "Олі", f"імʼя без службових слів (маємо {r['rec']['name']!r})")
ok(r["rec"]["md"] == "09-01", f"дата 09-01 (маємо {r['rec']['md']})")
ok(r["rec"]["kind"] == "birthday", "тип — день народження")
ok(r.get("when") == "2026-09-01", f"найближче 2026-09-01 (маємо {r.get('when')})")

r2 = M.add_from_free_text("Річниця весілля Міхаела 12.06")
ok(r2.get("ok") and r2["rec"]["kind"] == "anniversary", f"річниця: {r2.get('rec')}")
ok("Міхаела" in r2["rec"]["name"], f"імʼя в річниці (маємо {r2['rec']['name']!r})")

t, kb = M.added_card(r)
ok("Записав у реєстр дат" in t and "Олі" in t, "картка з підтвердженням")
flat = [b["callback_data"] for row in kb for b in row]
ok(any(x.startswith("db_cal_") for x in flat), "кнопка календаря")
ok(any(x.startswith("db_wish_") for x in flat), "кнопка привітання")
pid = [x for x in flat if x.startswith("db_cal_")][0][len("db_cal_"):]
ok(M._store.get(pid) is not None, "payload кнопки збережений")

src = open("bot.py").read()
ok("looks_like_date_note" in src, "перехоплення підключене в bot.py")
i_free = src.find("looks_like_date_note")
i_plan = src.find("from planner import handle_planner_reply")
ok(0 < i_free < i_plan, "перехоплення стоїть ДО планувальника")

print(f"\nfails: {fails}")
