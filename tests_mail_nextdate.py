import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime
import storage, ai_kit as K
import mailcal as M

fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

store = {}
storage.load = lambda f, default=None: store.get(f, default)
storage.save = lambda f, d: store.__setitem__(f, d) or True
storage.update_key = lambda f, k, v: store.setdefault(f, {}).__setitem__(k, v)
storage.remove_key = lambda f, k: store.get(f, {}).pop(k, None)
K.now = lambda: datetime(2026, 11, 15, 10, 0)
K.today_str = lambda: "2026-11-15"
cards, events = [], []
K.send_card = lambda text, kb=None, tag="", chat_id=None: cards.append((text, kb)) or True
K.calendar_event = lambda summary, start, end_dt=None, description="": \
    events.append((summary, start)) or {"ok": True, "event_id": "e1", "link": "l"}
K.rate_mark = lambda *a, **k: None

mails = [
    {"uid": "10", "sender": "kooperativa", "subject": "Potvrdenie transakcie (6641172176)", "body": "x"},
    {"uid": "11", "sender": "air", "subject": "Let", "body": "y"},
    {"uid": "12", "sender": "bill", "subject": "Avízo", "body": "z"},
]
M._emails = lambda limit=8: mails
found = [
    {"uid": "10", "title": "Kooperativa: наступний платіж 67,22 €", "date": "2026-12-12",
     "time": None, "kind": "next_payment", "why": "Najbližšia splátka 12.12.2026"},
    {"uid": "11", "title": "Рейс KSC-LTN", "date": "2026-12-01", "time": "08:30",
     "kind": "flight", "why": "рейс"},
    {"uid": "12", "title": "Оплатити Avízo", "date": "2026-11-30", "time": None,
     "kind": "payment", "why": "рахунок"},
    {"uid": "10", "title": "Минула дата", "date": "2026-10-01", "time": None,
     "kind": "next_payment", "why": ""},
]
M._ask = lambda emails: found
store["bills.json"] = {"b": {"uid": "12", "due": "2026-11-30", "paid": False, "vendor": "K"}}

n = M.run(force=True)
prop = [r for r in store["mailcal.json"].values() if r.get("state") == "proposed"]
ok(len(prop) == 1 and prop[0]["uid"] == "10", "next_payment -> лише пропозиція (1)")
ok(len(events) == 1 and "Рейс" in events[0][0], "рейс створено автоматично, платіж — ні")
ok(not any(r["uid"] == "12" for r in store["mailcal.json"].values()),
   "лист з карткою рахунку не дублюється")
prop_cards = [c for c in cards if "Наступний платіж" in c[0]]
ok(len(prop_cards) == 1, "є картка-пропозиція")
kb = prop_cards[0][1]
datas = [b["callback_data"] for row in kb for b in row]
ok(any(d.startswith("mc_pyes_") for d in datas) and any(d.startswith("mc_pno_") for d in datas)
   and any(d.startswith("mc_pyes2_") for d in datas), "кнопки: на дату / за 2 дні / не треба")
ok("2026-10-01" not in str(store["mailcal.json"]), "минула дата відкинута")

ev_before = len(events)
pyes = [d for d in datas if d.startswith("mc_pyes_")][0]
r = M.handle(pyes)
ok(len(events) == ev_before + 1 and events[-1][1] == datetime(2026, 12, 12, 9, 0), "після кнопки — подія на дату з листа")
ok(r["keyboard"] == [] , "кнопки прибрано")
r2 = M.handle(pyes)
ok(len(events) == ev_before + 1, "повторне натискання не дублює")

# відмова
store.clear(); cards.clear(); events.clear()
found[:] = [{"uid": "10", "title": "T", "date": "2026-12-12", "time": None, "kind": "renewal", "why": "w"}]
M.run(force=True)
kb = [c for c in cards][0][1]
pno = [b["callback_data"] for row in kb for b in row if b["callback_data"].startswith("mc_pno_")][0]
M.handle(pno)
ok(not events and any(r.get("state") == "declined" for r in store["mailcal.json"].values()), "«Не треба» — подію не створено")
cards.clear()
M.run(force=True)
ok(not cards, "після відмови той самий лист не пропонується знову")

# за 2 дні
store.clear(); cards.clear(); events.clear()
M.run(force=True)
kb = cards[0][1]
p2 = [b["callback_data"] for row in kb for b in row if b["callback_data"].startswith("mc_pyes2_")][0]
M.handle(p2)
ok(events and events[-1][1] == datetime(2026, 12, 10, 9, 0), "за 2 дні -> 10.12")

print("FAILS:", fails or "none"); sys.exit(1 if fails else 0)
