#!/usr/bin/env python3
"""Офлайн-тести react.py — кнопки під сповіщеннями + пам'ять реакцій."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K
import react as R

FAILS = []


def ok(cond, name):
    print(("✅ " if cond else "❌ ") + name, flush=True)
    if not cond:
        FAILS.append(name)


# ─── детермінований storage у пам'яті ────────────────────────────────────────
STORE = {}


def _load(fn, default=None):
    return STORE.get(fn, default if default is not None else {})


def _save(fn, data):
    STORE[fn] = data
    return True


def _upd(fn, key, val):
    STORE.setdefault(fn, {})[key] = val
    return True


def _rem(fn, key):
    STORE.get(fn, {}).pop(key, None)
    return True


SENT = []


def _send_card(text, keyboard=None, tag="", chat_id=None):
    SENT.append({"text": text, "kb": keyboard, "tag": tag})
    return True


K.load = _load
K.save = _save
K.update_key = _upd
K.remove_key = _rem
K.send_card = _send_card
R._store = K.PayloadStore(R.STORE_FILE)

MUTED = []


class _FakeDismissed:
    @staticmethod
    def mute(kind, key=None, title=None, note=""):
        MUTED.append({"kind": kind, "key": key, "title": title})
        return {"ok": True}


sys.modules["dismissed"] = _FakeDismissed


def reset():
    STORE.clear()
    SENT.clear()
    MUTED.clear()
    R._CACHE["data"] = None
    R._CACHE["ts"] = 0.0
    R._store = K.PayloadStore(R.STORE_FILE)


def press(kind, action, key="", title=""):
    """Емуляція: сповіщення з кнопками → натискання action."""
    rows = R.keyboard(kind, key, title)
    want = "rx_" + action + "_"
    for row in rows:
        for b in row:
            if b["callback_data"].startswith(want):
                return R.handle(b["callback_data"], None)
    raise AssertionError("немає кнопки " + action + " для " + kind)


# ─── 1. кнопки під сповіщенням ───────────────────────────────────────────────
print("\n=== 1. доречні кнопки ===")
reset()
kb_mail = R.keyboard("email", key="uid42", title="Faktúra od Michaela")
labels = [b["text"] for row in kb_mail for b in row]
ok(any("Відповім" in x for x in labels), "під листом є «Відповім»")
ok(any("Не нагадуй" in x for x in labels), "під листом є «Не нагадуй»")

kb_bill = R.keyboard("bill", title="Оплата Runable $10")
lb = [b["text"] for row in kb_bill for b in row]
ok(any("Оплатив" in x for x in lb), "під рахунком є «Оплатив»")
ok(not any("Відповім" in x for x in lb), "під рахунком НЕ пропонує відповідати")

kb_run = R.keyboard("run", title="Пробіжка")
ok(any("Побігав" in b["text"] for row in kb_run for b in row),
   "під бігом свої кнопки")
ok(all(b["callback_data"].startswith("rx_") for row in kb_mail for b in row),
   "усі кнопки з префіксом rx_")

# ─── 2. вид визначається сам ─────────────────────────────────────────────────
print("\n=== 2. вид сповіщення ===")
reset()
ok(R.detect("email_ai", "Новий лист від банку") == "email", "лист розпізнано")
ok(R.detect("bills_watcher", "Рахунок на 30 EUR") == "bill", "рахунок розпізнано")
ok(R.detect("crypto_morning", "BTC +5%") == "crypto", "крипто розпізнано")
ok(R.detect("", "") == "generic", "невідоме → generic, без вигадок")

# ─── 3. натискання записується ───────────────────────────────────────────────
print("\n=== 3. запис реакції ===")
reset()
res = press("bill", "paid", key="bill7", title="Оплата Runable $10")
ok("Записав" in res["text"] or "записав" in res["text"], "бот підтвердив запис")
ok(res["alert"] is True, "справжнє випливаюче вікно")
data = STORE.get(R.FILE, {})
ok(len(data) == 1, "реакція збережена (1 запис)")
rec = list(data.values())[0]
ok(rec["action"] == "paid" and rec["state"] == "closed",
   "записано саме «оплатив» і тема закрита")
ok(res["keyboard"] and "✓" in res["keyboard"][0][0]["text"],
   "кнопки замінені на пломбу — двічі не натиснеш")

# ─── 4. про закрите більше не нагадуємо ──────────────────────────────────────
print("\n=== 4. закриту тему не нагадуємо ===")
ok(R.is_closed("bill", key="bill7") is True, "за ключем — тиша")
ok(R.is_closed("bill", title="Оплата Runable $10") is True, "за назвою — тиша")
ok(R.is_closed("bill", title="🔔 оплата runable $10  ") is True,
   "назва з емодзі/регістром — той самий матч")
ok(R.is_closed("bill", key="bill999") is False, "інший рахунок не заглушено")
ok(any(m["kind"] == "bill" for m in MUTED),
   "закриття продубльовано в глобальний блок-лист")
ok("оплатив" in R.why("bill", key="bill7"), "бот чесно каже, чому мовчить")

# ─── 5. «пізніше» = тиша тимчасово ───────────────────────────────────────────
print("\n=== 5. пізніше ===")
reset()
press("deadline", "later", key="dl1", title="Здати документи")
ok(R.is_closed("deadline", key="dl1") is True, "після «пізніше» одразу тиша")
d = STORE[R.FILE]
k = list(d.keys())[0]
d[k]["until"] = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
R._CACHE["ts"] = 0.0
ok(R.is_closed("deadline", key="dl1") is False,
   "коли час вийшов — нагадування повертається")

# ─── 6. реакція, що не закриває тему ─────────────────────────────────────────
print("\n=== 6. «спостерігаю» тему не закриває ===")
reset()
press("crypto", "watch", key="btc", title="BTC -6%")
ok(R.is_closed("crypto", key="btc") is False, "спостереження не глушить тему")
ok(STORE[R.FILE][list(STORE[R.FILE])[0]]["state"] == "open", "стан open")
ok(not MUTED, "у блок-лист нічого не пішло")

# ─── 7. AI бачить реакції ────────────────────────────────────────────────────
print("\n=== 7. AI розуміє реакції ===")
reset()
press("bill", "paid", key="b1", title="Faktúra O2 24 EUR")
press("email", "reply", key="e1", title="Лист від Michaela")
blk = R.block()
ok("Faktúra O2 24 EUR" in blk, "у промпті реальна назва")
ok("оплатив" in blk, "у промпті реальна дія")
ok("не тягнути в звіт" in blk or "не згадувати як нове" in blk,
   "AI прямо заборонено тягнути закрите в звіт")
ok("Не додумуй" in blk, "AI заборонено вигадувати реакції")

import json as _js
body = _js.dumps({"contents": [{"parts": [{"text": "ЗВІТ:"}]}]}).encode()
out = R.inject(body, "briefing")
txt = _js.loads(out.decode())["contents"][0]["parts"][0]["text"]
ok("Faktúra O2" in txt, "блок реакцій підмішано в промпт")
out2 = R.inject(out, "briefing")
ok(out2 == out, "інжект ідемпотентний — двічі не дублюється")

# ─── 8. немає реакцій — немає вигадок ────────────────────────────────────────
print("\n=== 8. без даних нічого не вигадуємо ===")
reset()
ok(R.block() == "", "порожня історія → порожній блок")
body3 = _js.dumps({"contents": [{"parts": [{"text": "ЗВІТ:"}]}]}).encode()
ok(R.inject(body3, "x") == body3, "промпт не змінено")
ok("нічого не вигадую" in R.report(), "у звіті чесно: реакцій ще немає")

# ─── 9. картка з кнопками ────────────────────────────────────────────────────
print("\n=== 9. card() ===")
reset()
R.card("💸 <b>Рахунок O2</b>\n24 EUR до 25.08", tag="bills_watcher")
ok(len(SENT) == 1, "картку надіслано")
ok(SENT[0]["kb"], "кнопки прикріплені автоматично")
ok(any("Оплатив" in b["text"] for row in SENT[0]["kb"] for b in row),
   "кнопки саме для рахунку")

# ─── 10. протухлий payload не ламає бота ─────────────────────────────────────
print("\n=== 10. протухлий payload ===")
reset()
r = R.handle("rx_done_deadbeef00", None)
ok(bool(r["text"]), "бот усе одно відповідає")
ok(len(STORE.get(R.FILE, {})) == 1, "реакція записана без вигаданої назви")
bad = R.handle("rx_", None)
ok("Не зрозумів" in bad["text"], "битий callback — чесна відповідь, не крах")

# ─── 11. звіт ────────────────────────────────────────────────────────────────
print("\n=== 11. /реакції ===")
reset()
press("bill", "paid", key="b1", title="Faktúra O2")
press("crypto", "watch", key="btc", title="BTC -6%")
rep = R.report()
ok("Faktúra O2" in rep and "BTC -6%" in rep, "у звіті обидві реакції")
ok("🔒" in rep, "закрита тема позначена")
ok("закритих тем: 1" in rep, "чесний рахунок закритих тем")

print("\n" + "=" * 50)
print("Падінь: " + str(len(FAILS)))
for f in FAILS:
    print(" - " + f)
sys.exit(1 if FAILS else 0)
