#!/usr/bin/env python3
"""
tests_inline_confirm.py — питання «Точно?» і результат мають з'являтись ПІД тим
сповіщенням, якого стосуються (а не новим повідомленням у кінці чату).

Перевіряємо:
  1. клік по «🚫 Не треба» редагує кнопки ТОГО САМОГО повідомлення
  2. у нових кнопках є рядок-питання і [Так]/[Ні]
  3. початкові кнопки збережені в payload
  4. «Ні» повертає початкові кнопки на місце
  5. «Так» лишає підпис-результат там само
  6. жодного нового sendMessage у процесі
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

FAILS = 0


def ok(cond, label):
    global FAILS
    print(("✅ " if cond else "❌ ") + label)
    if not cond:
        FAILS += 1


import ai_kit as K  # noqa: E402

MEM = {}
K.load = lambda f, default=None: MEM.get(f, default if default is not None else {})
K.save = lambda f, d: MEM.__setitem__(f, d)


def _upd(f, k, v):
    d = MEM.setdefault(f, {})
    d[k] = v
    return True


K.update_key = _upd

import confirm as C  # noqa: E402
import bot  # noqa: E402

CALLS = []


def fake_api(method, data=None):
    CALLS.append((method, data or {}))
    return {"ok": True, "result": {}}


bot.api = fake_api
bot.send = lambda chat_id, text: CALLS.append(("sendMessage", {"text": text})) or True
bot.send_with_keyboard = lambda c, t, k: CALLS.append(("sendMessage", {"text": t})) or True

ORIG_KB = [[{"text": "✅ Додати", "callback_data": "cal_add_77"},
            {"text": "🚫 Не треба", "callback_data": "cal_skip_77"}]]


def make_cb(data, kb=None):
    return {"id": "q1", "data": data,
            "message": {"message_id": 555, "chat": {"id": 2100366814},
                        "text": "📅 Оплатити страховку — додати в календар?",
                        "reply_markup": {"inline_keyboard": kb if kb is not None else ORIG_KB}}}


def edits():
    return [d for m, d in CALLS if m == "editMessageReplyMarkup"]


def sends():
    return [d for m, d in CALLS if m == "sendMessage"]


print("1) Питання стає під тим самим сповіщенням")
CALLS.clear()
cb = make_cb("cal_skip_77")
held = bot._confirm_gate(cb, "cal_skip_77")
ok(held, "гейт спрацював — дію відкладено до підтвердження")
e = edits()
ok(len(e) == 1, f"рівно одне редагування кнопок ({len(e)})")
ok(e and e[0].get("message_id") == 555 and e[0].get("chat_id") == 2100366814,
   "редагується САМЕ те повідомлення (555)")
ok(not sends(), f"жодного нового повідомлення в чат ({len(sends())})")

kb = e[0]["reply_markup"]["inline_keyboard"]
ok("Точно?" in kb[0][0]["text"], f"перший рядок — питання: {kb[0][0]['text']!r}")
ok(kb[0][0]["callback_data"] == "noop", "рядок-питання не є дією")
cds = [b["callback_data"] for row in kb for b in row]
yes_cd = [c for c in cds if c.startswith("cfm_y_")]
no_cd = [c for c in cds if c.startswith("cfm_n_")]
ok(len(yes_cd) == 1 and len(no_cd) == 1, "є [Так] і [Ні]")
ok(all(len(b["text"]) <= 64 for row in kb for b in row), "текст кнопок ≤64 симв")

cid = yes_cd[0][len("cfm_y_"):]
pl = C._store.get(cid)
ok(bool(pl), "payload збережено")
orig = ((pl or {}).get("extra") or {}).get("origin") or {}
ok(orig.get("msg_id") == 555, "запам'ятано, під яким сповіщенням стоїть питання")
ok(orig.get("kb") == ORIG_KB, "початкові кнопки збережено для відкату")

print("\n2) «Ні» повертає початкові кнопки на місце")
CALLS.clear()
r = C.no(cid)
ok(r.get("ok"), "confirm.no ок")
_, _, back = bot._cfm_origin(r)
ok(back == ORIG_KB, "з payload дістали початкові кнопки")
done = bot._cfm_close_inline(make_cb("cfm_n_" + cid, kb), r,
                             "👍 Залишив як було — без змін", keep_kb=back)
ok(done, "результат поставлено на місце")
e2 = edits()
ok(e2 and e2[-1]["message_id"] == 555, "редаговано те саме повідомлення")
kb2 = e2[-1]["reply_markup"]["inline_keyboard"]
ok(kb2[1:] == ORIG_KB, "початкові кнопки повернулись")
ok("Залишив як було" in kb2[0][0]["text"], "видно, що нічого не змінено")
ok(not sends(), "жодного нового повідомлення")

print("\n3) «Так» — результат теж на місці")
CALLS.clear()
cb3 = make_cb("cal_skip_88")
bot._confirm_gate(cb3, "cal_skip_88")
kb3 = edits()[0]["reply_markup"]["inline_keyboard"]
cid3 = [b["callback_data"] for row in kb3 for b in row
        if b["callback_data"].startswith("cfm_y_")][0][len("cfm_y_"):]
CALLS.clear()
r3 = C.yes(cid3)
ok(r3.get("ok"), "confirm.yes ок")
ok(r3.get("redispatch") == "cal_skip_88", "дія виконується тільки після «Так»")
ok(bot._cfm_close_inline(make_cb("cfm_y_" + cid3, kb3), r3,
                         str(r3.get("done_text") or "")), "підпис-результат поставлено")
kb4 = edits()[-1]["reply_markup"]["inline_keyboard"]
ok(len(kb4) == 1 and kb4[0][0]["callback_data"] == "noop",
   "лишився тільки підпис, кнопок-дій нема")
ok("Не нагадуватиму" in kb4[0][0]["text"] or "🚫" in kb4[0][0]["text"],
   f"підпис пояснює що сталось: {kb4[0][0]['text']!r}")

print("\n4) Не вдалось відредагувати → відповідь на сповіщення, не в кінець чату")
CALLS.clear()
bot.api = lambda m, d=None: (CALLS.append((m, d or {})),
                             {"ok": False} if m == "editMessageReplyMarkup"
                             else {"ok": True})[1]
cb5 = make_cb("cal_skip_99")
bot._confirm_gate(cb5, "cal_skip_99")
new_msgs = [d for m, d in CALLS if m == "sendMessage"]
ok(bool(new_msgs), "запасний варіант спрацював")
ok(new_msgs[0].get("reply_to_message_id") == 555,
   "повідомлення прив'язане до того сповіщення (reply)")
bot.api = fake_api

print("\n5) Код: питання ставиться інлайн і для видалення листа")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
ok(src.count("_ask_confirm_inline(") >= 4, "інлайн-питання під усіма гілками")
seg = src.split('elif data.startswith("email_delete_"):')[1][:900]
ok("_ask_confirm_inline" in seg, "видалення листа теж питає під листом")
seg2 = src.split('elif data.startswith("calrem_skip_"):')[1][:900]
ok("_ask_confirm_inline" in seg2, "скасування нагадування — теж")

print(f"\nfails: {FAILS}")
sys.exit(1 if FAILS else 0)
