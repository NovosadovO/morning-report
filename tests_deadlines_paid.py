import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime
import storage, ai_kit as K
import deadlines_watcher as D
import paidfacts as PF
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

clock = {"t": datetime(2026, 11, 21, 11, 0)}
K.now = lambda: clock["t"]
K.today_str = lambda: clock["t"].strftime("%Y-%m-%d")
cards = []
K.send_card = lambda text, kb=None, tag="", chat_id=None: cards.append((text, kb)) or True
K.rate_ok = lambda *a, **k: True
K.rate_mark = lambda *a, **k: None

SENDER = "Notifikácie KOOPERATIVA <notifikacie@ekooperativa.sk>"
AVIZO = ("Avízo na zaplatenie poistného PZ 6641172176 – povinného zmluvného poistenia MV, EČV LE066BI")
THANKS = "Poďakovanie za úhradu poistného - povinného zmluvného poistenia, EČV LE066BI"
HTML = "<meta charset='UTF-8'><table><tr><td style='x'>Platnosť poistenia končí 31.10.2026</td></tr></table>"

mails = {"items": []}
import types
mon = types.ModuleType("monitor"); mon.get_emails = lambda: mails
sys.modules["monitor"] = mon

extracted = {"v": []}
D._extract = lambda c: extracted["v"]
import ai_brain
ai_brain.remember_answer = lambda *a, **k: True

def reset():
    store.clear(); cards.clear()
    mails["items"] = []
    extracted["v"] = []

def item(title, deadline, uid="80711", org="KOOPERATIVA"):
    return {"uid": uid, "kind": "insurance", "title": title, "org": org,
            "deadline": deadline, "action": "Оплатити страховий поліс PZ 6641172176",
            "why": "x"}

# 1. той самий лист, інша назва/дата при повторному скані — другої картки НЕМАЄ
reset()
mails["items"] = [{"uid": "80711", "sender": SENDER, "subject": AVIZO}]
store["email_body_cache.json"] = {"80711": {"sender": SENDER, "subject": AVIZO, "body": HTML}}
D._migrate()
store["deadlines.json"] = {}  # після міграції нічого нема
store["deadlines_memory.json"]["migrations"]["kooperativa_paid_2026_11_22"] = "x"  # ізолюємо тест від міграції
extracted["v"] = [item("Страховка авто — оплата", "2026-11-04")]
n1 = D.scan(force=True)
extracted["v"] = [item("Страховка Kooperativa — оплата", "2026-11-20")]
n2 = D.scan(force=True)
extracted["v"] = [item("Страховка авто — новий термін", "2026-11-21")]
n3 = D.scan(force=True)
ok(n1 == 1 and n2 == 0 and n3 == 0, f"один лист = одна картка ({n1},{n2},{n3})")
ok(len(store["deadlines.json"]) == 1, "у сховищі одна картка")
ok(any("dl_paid_" in b["callback_data"] for r in cards[0][1] for b in r),
   "на картці є кнопка «Вже оплачено / зроблено»")

# 2. лист-підтвердження не стає дедлайном і закриває картку
reset()
store["email_body_cache.json"] = {
    "82462": {"sender": SENDER, "subject": THANKS, "body": HTML}}
mails["items"] = [{"uid": "82462", "sender": SENDER, "subject": THANKS}]
store["deadlines.json"] = {"k1": {**item("Страховка авто — оплата", "2026-11-21"),
                                  "done": False, "sender": SENDER}}
store["deadlines_memory.json"] = {"uids": {"80711": {"state": "offered"}},
                                  "migrations": {"seed_uids": "x", "kooperativa_paid_2026_11_22": "x"}}
extracted["v"] = [item("Оплата", "2026-11-22", uid="82462")]
n = D.scan(force=True)
ok(n == 0 and not cards, "з листа-подяки за оплату картки немає")
ok(store["deadlines.json"]["k1"]["done"] is True
   and store["deadlines.json"]["k1"].get("paid_by") == "mail",
   "лист-подяка закрив відкриту картку")

# 2b. підтвердження СТАРІШЕ за вимогу — не закриває
reset()
store["paid_facts.json"] = {"mail": {"70000": {"org": "kooperativa", "ids": [], "ts": "x"}}, "user": []}
store["deadlines.json"] = {"k1": {**item("Страховка", "2026-11-21"), "done": False}}
store["deadlines_memory.json"] = {"uids": {}, "migrations": {"seed_uids": "x", "kooperativa_paid_2026_11_22": "x"}}
ok(D.reconcile_paid() == 0, "старше підтвердження нову вимогу не закриває")

# 3. «оплатив страховку» в чаті
reset()
store["deadlines.json"] = {
    "k1": {**item("страховка авто kooperativa оплата", "2026-11-21"), "done": False},
    "k2": {**item("страховка авто оплата", "2026-11-22"), "done": False},
    "g1": {**item("Garmin Connect скасування підписки", "2026-11-25", uid="1", org="Garmin"),
           "kind": "contract", "done": False},
}
r = D.paid_from_text("оплатив страховку")
ok(r.startswith("✅") and store["deadlines.json"]["k1"]["done"] and store["deadlines.json"]["k2"]["done"],
   "чат «оплатив страховку» закрив обидві страхові картки")
ok(not store["deadlines.json"]["g1"]["done"], "Garmin не зачепило")
ok(D.paid_from_text("привіт як справи") == "", "без слова «оплатив» — мовчимо")
ok(D.paid_from_text("оплатив Netflix") == "", "нічого не знайшли — мовчимо")
ok(D._mem()["uids"]["80711"]["state"] == "paid", "лист позначено paid — не перечитається")

# 3b. після «оплатив» повторне витягування з того ж листа не дає картки
mails["items"] = [{"uid": "80711", "sender": SENDER, "subject": AVIZO}]
extracted["v"] = [item("Страховка нова назва", "2026-12-01")]
cards.clear()
ok(D.scan(force=True) == 0 and not cards, "після «оплатив» той самий лист не оживає")

# 4. «❌ Не актуально» тримає суто по листу
reset()
store["deadlines_memory.json"] = {"uids": {}, "migrations": {"seed_uids": "x", "kooperativa_paid_2026_11_22": "x"}}
mails["items"] = [{"uid": "5", "sender": "Acme <a@acme.com>", "subject": "Contract renewal policy"}]
store["email_body_cache.json"] = {"5": {"sender": "Acme <a@acme.com>", "subject": "Contract renewal policy", "body": "x"}}
extracted["v"] = [item("Договір Acme", "2026-12-10", uid="5", org="Acme")]
D.scan(force=True)
pid = list(store["deadlines_store.json"].keys())[0]
D.do_skip(pid)
extracted["v"] = [item("Договір Acme — продовження", "2026-12-11", uid="5", org="Acme")]
cards.clear()
ok(D.scan(force=True) == 0 and not cards, "skip не повертається з іншою назвою")

# 5. кнопка «Вже оплачено»
reset()
store["deadlines_memory.json"] = {"uids": {}, "migrations": {"seed_uids": "x", "kooperativa_paid_2026_11_22": "x"}}
mails["items"] = [{"uid": "80711", "sender": SENDER, "subject": AVIZO}]
store["email_body_cache.json"] = {"80711": {"sender": SENDER, "subject": AVIZO, "body": HTML}}
extracted["v"] = [item("Страховка авто — оплата", "2026-11-21")]
D.scan(force=True)
pid = [k for k, v in store["deadlines_store.json"].items()][0]
r = D.do_paid(pid)
ok(r.get("ok") and all(v["done"] for v in store["deadlines.json"].values()), "кнопка закрила картку")
ok(any(u.get("org") == "kooperativa" for u in store["paid_facts.json"]["user"]), "факт «оплачено» збережено")

# 6. нагадування: закрита не шле; кілька варіантів одного листа — одне на добу
reset()
store["deadlines_memory.json"] = {"uids": {}, "migrations": {"seed_uids": "x", "kooperativa_paid_2026_11_22": "x"}}
clock["t"] = datetime(2026, 11, 21, 11, 0)
store["deadlines.json"] = {
    "a": {**item("Google пароль", "2026-11-22", uid="82946", org="Google"), "kind": "other", "done": False},
    "b": {**item("Google пароль 2", "2026-11-24", uid="82946", org="Google"), "kind": "other", "done": False},
}
n = D.check_due_soon()
ok(n == 1, f"варіанти одного листа — одне нагадування ({n})")

# 7. міграція: Kooperativa закривається один раз
reset()
store["deadlines.json"] = {
    "k1": {**item("страховка авто оплата", "2026-11-21"), "done": False, "sender": SENDER},
    "g1": {**item("Garmin", "2026-12-04", uid="9", org="Garmin"), "done": False},
}
D._migrate()
ok(store["deadlines.json"]["k1"]["done"] and not store["deadlines.json"]["g1"]["done"],
   "міграція закрила тільки Kooperativa")
ok(D._mem()["uids"]["80711"]["state"] == "paid" and D._mem()["uids"]["9"]["state"] == "offered",
   "реєстр листів засіяно")
store["deadlines.json"]["k9"] = {**item("Страховка 2027", "2027-10-31", uid="99999"), "done": False}
D._migrate()
ok(not store["deadlines.json"]["k9"]["done"], "міграція одноразова — нове не чіпає")

# 8. bills: підтвердження в пошті закриває рахунок Kooperativa без суми
reset()
store["bills.json"] = {"kooperativa||": dict(vendor="KOOPERATIVA", amount="", currency="EUR",
    invoice_no="PZ 6641172176", due="", uid="80711", sender=SENDER, subject=AVIZO,
    note="", paid=False, paid_at="", created="2026-10-12")}
PF.learn_mail("82462", SENDER, THANKS, HTML)
B.reconcile_paid()
ok(store["bills.json"]["kooperativa||"]["paid"] is True, "bills: рахунок закрито листом-підтвердженням")

# 8b. спільний номер авто не звʼязує різні справи
reset()
store["deadlines.json"] = {"p1": {**item("Паркування Кошице", "2026-11-25", uid="82436", org="Ubian Eshop"),
                                  "kind": "other", "action": "Оплатити паркування LE066BI", "done": False}}
PF.learn_mail("82462", SENDER, THANKS, HTML)
ok(D.reconcile_paid() == 0 and not store["deadlines.json"]["p1"]["done"], "паркування з тим самим ЄЧВ не закрито")

# 9. регулярки: реклама/аві́зо не є підтвердженням
ok(not PF.is_paid_mail(AVIZO, HTML), "авізо НЕ підтвердження")
ok(PF.is_paid_mail(THANKS, ""), "«Poďakovanie za úhradu» — підтвердження")
ok(PF.is_paid_mail("Receipt", "Thank you for your payment of $10"), "EN підтвердження")
ok(PF.org_token(SENDER) == "kooperativa", "org_token відправника")

print("\nFAILS:", fails if fails else "none")
sys.exit(1 if fails else 0)
