import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TELEGRAM_TOKEN", "x"); os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
from datetime import datetime, timedelta
import storage
fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)
MEM = {}
storage.load = lambda f, default=None: MEM.get(f, default)
storage.save = lambda f, d: MEM.__setitem__(f, d) or True
storage.update_key = lambda f, k, v: MEM.setdefault(f, {}).__setitem__(k, v) or True
storage.remove_key = lambda f, k: MEM.get(f, {}).pop(k, None) and True
import ai_kit as K
import mailreply as MR, autoquiet as AQ, openloop as OL

JANA = "Jana Vasilková / Soft-Tech, s.r.o.<objednavky@soft-tech.sk>"
BODY = ("Dobrý deň prajem,\r\n\r\nmáte u nás objednávku na náhradné náplne do pera 5 kusov. "
        "Žiaľ, skladom sú posledné 4 kusy. Pošleme aspoň tieto a za 1.40€ Vám doplníme "
        "niečo do objednávky?\r\n\r\nĎakujeme za odpoveď")
print("1) евристики")
ok(MR.is_person(JANA), "Jana — людина")
ok(not MR.is_person("TradingView <noreply@tradingview.com>"), "noreply — не людина")
ok(not MR.is_person("Daily Crypto Digest <news@x.com>"), "розсилка — не людина")
ok(not MR.is_person("Reserved <info@reserved.com>"), "магазин — не людина")
ok(MR.needs_reply(JANA, "(без теми)", BODY), "питання Jany потребує відповіді")
ok(not MR.needs_reply(JANA, "Platba prijatá", "Vaša platba bola prijatá. Ďakujeme?"), "підтвердження оплати — ні")
ok(not MR.needs_reply(JANA, "Faktúra", "Posielame faktúru v prílohe."), "інформаційний лист без питання — ні")

print("2) run(): картка, відповідь, повтор")
SENT = []
K.send_card = lambda text, keyboard=None, tag="x", chat_id=None: SENT.append((text, keyboard)) or True
now = K.now().replace(tzinfo=None)
mail = {"uid": "84074", "sender": JANA, "subject": "(без теми)", "body": BODY, "dt": now - timedelta(hours=30)}
fresh = dict(mail, uid="1", dt=now - timedelta(minutes=30))
n = MR.run(force=True, fetched=([mail, fresh], {}))
ok(n == 1 and len(SENT) == 1, "одна картка (свіжий лист <2 год пропущено)")
ok("Jana" in SENT[0][0] and "4 kusy" in SENT[0][0], "у картці лист Jany")
cbs = [b["callback_data"] for row in SENT[0][1] for b in row]
ok(len(cbs) == 2 and cbs[0].startswith("mr_done_"), "кнопки Відповів/Не треба")
SENT.clear()
ok(MR.run(force=True, fetched=([mail], {})) == 0, "повтор одразу — тиша (12 год)")
MEM["mailreply_state.json"]["84074"]["last"] = (now - timedelta(hours=13)).isoformat()
ok(MR.run(force=True, fetched=([mail], {})) == 1, "через 13 год — друге нагадування")
MEM["mailreply_state.json"]["84074"]["last"] = (now - timedelta(hours=13)).isoformat()
MEM["mailreply_state.json"]["84074"]["n"] = 3
ok(MR.run(force=True, fetched=([mail], {})) == 0, "максимум 3 нагадування")
MEM["mailreply_state.json"].clear(); SENT.clear()
ok(MR.run(force=True, fetched=([mail], {"objednavky@soft-tech.sk": now - timedelta(hours=2)})) == 0,
   "є відповідь у Надісланих — мовчимо")
MEM["mailreply_state.json"].clear()
ok(MR.run(force=True, fetched=([mail], {"objednavky@soft-tech.sk": now - timedelta(days=2)})) == 1,
   "відповідь була ДО листа — нагадуємо")
pid = [b["callback_data"] for row in SENT[-1][1] for b in row][0][len("mr_done_"):]
r = MR.handle("mr_done_" + pid)
ok(MEM["mailreply_state.json"]["84074"]["state"] == "done" and r["keyboard"] == [], "«Відповів» гасить")
MEM["mailreply_state.json"]["84074"]["last"] = (now - timedelta(days=2)).isoformat()
SENT.clear()
ok(MR.run(force=True, fetched=([mail], {})) == 0, "після «Відповів» більше не нагадує")

print("3) autoquiet: дедуп і пріоритет")
MEM.clear()
AQ.hold("📩 Лист від <b>Jana</b> питання", kind="offer", keyboard=[[{"text": "x", "callback_data": "y"}]])
for i in range(80):
    AQ.hold(f"🔴▼ <b>SOL -8.{i}% за 24г</b> Зараз: 1{i}.5 $", kind="chunk")
    AQ.hold(f"🌅 <b>ЗВІТ · {i}:00</b> ранок", kind="chunk")
items = MEM["held_msgs.json"]["items"]
ok(len(items) == 3, f"повтори злито: {len(items)} запис(и)")
ok(any("Jana" in i["text"] for i in items), "лист Jany вцілів")
MEM.clear()
AQ.hold("📩 Лист від <b>Jana</b>", kind="offer")
for i in range(100):
    AQ.hold(f"різний текст {chr(97 + i % 26)}{chr(97 + i // 26)} слово", kind="chunk")
items = MEM["held_msgs.json"]["items"]
ok(len(items) == AQ.MAX_QUEUE and any("Jana" in i["text"] for i in items), "переповнення не витісняє лист")
print("3b) flush: лист живе довше за звичайне")
MEM.clear()
old = (K.now().replace(tzinfo=None) - timedelta(hours=20)).isoformat()
MEM["held_msgs.json"] = {"items": [
    {"ts": old, "kind": "offer", "text": "📩 Лист від Jana", "keyboard": [[{"text": "a", "callback_data": "b"}]]},
    {"ts": old, "kind": "chunk", "text": "звіт", "keyboard": None}]}
SENT.clear()
ok(AQ.flush(force=True) == 1 and any("Jana" in t for t, _ in SENT), "20-годинний лист віддано, звіт — ні")

print("4) openloop: цитата не стає обіцянкою Олега")
own = OL._own_text("Dobrý deň, pošlem to zajtra.\n\nOn Tue, 24 Nov 2026 Jana wrote:\n> pošleme 4 kusy\nďalší text")
ok("pošlem" in own and "Jana" not in own and "kusy" not in own, "цитату відрізано")
ok(OL._own_text("> лише цитата\n> ще") == "", "лише цитата → порожньо")
print("FAILS:", fails or "none"); sys.exit(1 if fails else 0)
