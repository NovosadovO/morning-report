import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime
import storage, ai_kit as K
import bills_watcher as B

fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)

store = {}
storage.load = lambda f, default=None: store.get(f, default)
storage.save = lambda f, d: store.__setitem__(f, d) or True
def _uk(f, k, v):
    store.setdefault(f, {})[k] = v
def _rk(f, k):
    store.get(f, {}).pop(k, None)
storage.update_key = _uk
storage.remove_key = _rk

clock = {"t": datetime(2026, 11, 11, 23, 37)}
K.now = lambda: clock["t"]
K.today_str = lambda: clock["t"].strftime("%Y-%m-%d")
cards = []
K.send_card = lambda text, kb=None, tag="", chat_id=None: cards.append((text, kb)) or True

def reset():
    store.clear(); cards.clear()

def bill(vendor, amount, due, created, **kw):
    d = dict(vendor=vendor, amount=amount, currency="EUR", due=due, created=created,
             paid=False, invoice_no="", subject=kw.pop("subject", ""), note="")
    d.update(kw)
    return d

# 1. міграція: уже прострочені не стріляють знову
reset()
store["bills.json"] = {
    "a": bill("Google Play", "4.99", "2026-11-05", "2026-11-01"),
    "b": bill("Garmin Services", "8.99", "2026-11-06", "2026-11-05"),
}
n = B.check_due_soon()
ok(n == 0 and not cards, "після міграції прострочені не спамлять")
clock["t"] = datetime(2026, 11, 12, 9, 0)
ok(B.check_due_soon() == 0, "наступного дня теж тихо")

# 2. новий прострочений: один раз, не щодня, повтор через 7 днів, стоп після 14
reset()
clock["t"] = datetime(2026, 11, 11, 9, 0)
store["bills.json"] = {}
store["bills_overdue.json"] = {"_migrated": "2026-11-01"}
store["bills.json"]["c"] = bill("Acme", "10", "2026-11-10", "2026-11-01")
ok(B.check_due_soon() == 1, "перший раз прострочений — нагадали")
clock["t"] = datetime(2026, 11, 12, 9, 0)
ok(B.check_due_soon() == 0, "наступного дня — тихо")
clock["t"] = datetime(2026, 11, 17, 9, 0)
ok(B.check_due_soon() == 0, "через 6 днів — тихо")
clock["t"] = datetime(2026, 11, 18, 9, 0)
ok(B.check_due_soon() == 1, "через 7 днів — один повтор")
clock["t"] = datetime(2026, 11, 26, 9, 0)
ok(B.check_due_soon() == 0, "більше повторів немає")

# 3. кілька відразу = одне повідомлення
reset()
clock["t"] = datetime(2026, 11, 11, 9, 0)
store["bills_overdue.json"] = {"_migrated": "2026-11-01"}
store["bills.json"] = {
    "x1": bill("A One", "1", "2026-11-09", "2026-11-01"),
    "x2": bill("B Two", "2", "2026-11-08", "2026-11-01"),
    "x3": bill("C Three", "3", "2026-11-07", "2026-11-01"),
}
B.check_due_soon()
ok(len(cards) == 1 and len(cards[0][1]) == 3, "3 рахунки -> 1 повідомлення з 3 кнопками")

# 4. оплата з пошти закриває неоплачений того ж постачальника
reset()
clock["t"] = datetime(2026, 11, 11, 9, 0)
store["bills.json"] = {
    "g1": bill("Google Play", "4.99", "2026-11-05", "2026-11-01"),
    "g2": bill("Google Commerce Limited", "4.99", "", "2026-11-04", paid=True),
    "g3": bill("Google Cloud Platform", "", "", "2026-11-02"),
    "o": bill("Other", "4.99", "2026-11-05", "2026-11-01"),
}
store["bills_overdue.json"] = {"_migrated": "2026-11-01"}
B.reconcile_paid()
bl = store["bills.json"]
ok(bl["g1"]["paid"], "Google Play 4.99 закрито квитанцією Google 4.99")
ok(not bl["g3"]["paid"] and not bl["o"]["paid"], "інші постачальники/без суми не зачеплені")

# 4b. квитанція старіша за рахунок — не закриває
reset()
store["bills.json"] = {
    "n": bill("Garmin", "8.99", "2026-12-05", "2026-11-08"),
    "r": bill("Garmin", "8.99", "", "2026-11-05", paid=True),
}
B.reconcile_paid()
ok(not store["bills.json"]["n"]["paid"], "стара квитанція не закриває новіший рахунок")

# 5. auto_charge не нагадується
reset()
clock["t"] = datetime(2026, 11, 11, 9, 0)
store["bills_overdue.json"] = {"_migrated": "2026-11-01"}
store["bills.json"] = {"z": bill("Garmin", "8.99", "2026-11-06", "2026-11-05", auto_charge=True)}
ok(B.check_due_soon() == 0, "авто-списання не нагадуємо")

# 6. scan: auto_charge, дубль з іншим due, paid-confirmation
reset()
clock["t"] = datetime(2026, 11, 11, 9, 0)
K.rate_mark = lambda *a, **k: None
B._email_candidates = lambda limit=8: [{"uid": "1", "sender": "g", "subject": "s", "body": "b"}]
found = [
    {"uid": "1", "vendor": "Garmin Services", "amount": "8.99", "due": "2026-11-06", "auto_charge": True},
    {"uid": "1", "vendor": "Acme", "amount": "10", "due": "2026-11-20"},
]
B._extract = lambda c: found
ok(B.scan(force=True) == 1, "scan: авто-списання не картка, Acme — картка")
found[:] = [{"uid": "1", "vendor": "Acme", "amount": "10", "due": "2026-11-25"}]
cards.clear()
ok(B.scan(force=True) == 0 and not cards, "той самий Acme 10 з іншим due — не нова картка")
found[:] = [{"uid": "1", "vendor": "Acme Ltd", "amount": "10", "due": "", "already_paid": True}]
B.scan(force=True)
ok(all(b.get("paid") for b in store["bills.json"].values() if b["vendor"].startswith("Acme")),
   "scan: підтвердження оплати закрило Acme")

# 7. оплата з чату
reset()
clock["t"] = datetime(2026, 11, 11, 9, 0)
store["bills.json"] = {
    "a": bill("Google Play", "4.99", "2026-11-05", "2026-11-01", subject="ChatGPT відхилено"),
    "b": bill("Garmin Services", "8.99", "2026-11-06", "2026-11-05"),
    "c": bill("Acme", "9", "2026-12-20", "2026-11-05"),
}
r = B.paid_from_text("Garmin я вже оплатив")
ok("Garmin" in r and store["bills.json"]["b"]["paid"] and not store["bills.json"]["a"]["paid"],
   "чат: оплатив Garmin закриває лише Garmin")
r = B.paid_from_text("оплатив чатгпт")
ok(store["bills.json"]["a"]["paid"], "чат: оплатив чатгпт -> Google Play (ChatGPT)")
ok(B.paid_from_text("оплатив паркування") == "", "чат: невідоме — мовчимо")
store["bills.json"]["d"] = bill("Foo", "1", "2026-11-01", "2026-10-30")
r = B.paid_from_text("все оплатив")
ok(store["bills.json"]["d"]["paid"] and not store["bills.json"]["c"]["paid"],
   "чат: 'все оплатив' закриває прострочені, не майбутні")
ok(B.paid_from_text("привіт") == "", "чат: звичайний текст ігнорується")

print("FAILS:", fails or "none"); sys.exit(1 if fails else 0)
