#!/usr/bin/env python3
"""Тест двокрокового підтвердження: кнопка питає «Точно?» і вимикає лише після «Так»."""
import sys, json, ast
from datetime import datetime, timedelta
sys.path.insert(0, "/home/user/bot")

def ok(c, m):
    print(("✅ " if c else "❌ ") + m)

import ai_kit as K
MEM = {}
K.load = lambda f, default=None: json.loads(json.dumps(MEM.get(f, default)))
K.save = lambda f, d: MEM.__setitem__(f, json.loads(json.dumps(d))) or True
K.update_key = lambda f, k, v: MEM.setdefault(f, {}).__setitem__(k, json.loads(json.dumps(v))) or True
K.remove_key = lambda f, k: MEM.get(f, {}).pop(k, None) or True

import calendar_watch as cw
import ai_buttons as gx
import confirm as cfm
for m in (cw, gx, cfm):
    m.K.load, m.K.save, m.K.update_key, m.K.remove_key = K.load, K.save, K.update_key, K.remove_key
import response_log
response_log.log_response = lambda *a, **k: None

SENT = []
cw.K.send_card = lambda text, kb=None, tag=None: SENT.append(text) or True

# ── 1. Кнопка "Скасовано" НЕ вимикає одразу ──────────────────────────────────
NOW = cw.K.now().replace(tzinfo=None)
ev = {"id": "ev1", "title": "Тренування", "start": NOW + timedelta(minutes=30),
      "end": NOW + timedelta(minutes=90), "location": "", "allday": False,
      "routine": False, "shift": False}
cw._raw_events = lambda hours_ahead=192: [ev]

pid = cw._store.put({"evid": "ev1", "title": "Тренування", "stage": "t30", "when": "17:00"})
q = cfm.ask("cw_cancel", pid, "Тренування")
ok(q.get("ok"), "confirm.ask повертає питання")
ok("Точно?" in q["text"], "у тексті є «Точно?»")
ok("Тренування" in q["text"], "у питанні названа саме ця подія")
ok("не нагадуватиму" in q["text"], "чітко сказано що станеться")
btns = [b["text"] for row in q["keyboard"] for b in row]
ok(len(btns) == 2 and any("Так" in b for b in btns) and any("Ні" in b for b in btns),
   f"дві кнопки Так/Ні (got {btns})")
ok(not cw.is_blocked("ev1"), "ПІСЛЯ ПИТАННЯ подія ще НЕ заблокована")

# ── 2. "Ні" — нічого не змінює ───────────────────────────────────────────────
r = cfm.no(q["cid"])
ok(r.get("ok") and r.get("cancelled"), "відповідь «Ні» обробляється")
ok(not cw.is_blocked("ev1"), "після «Ні» подія НЕ заблокована")
SENT.clear()
cw.tick()
ok(len(SENT) == 1, f"після «Ні» нагадування ПРИХОДИТЬ (got {len(SENT)})")

# ── 3. "Так" — реально вимикає ───────────────────────────────────────────────
MEM[cw.SENT_FILE] = {}
pid2 = cw._store.put({"evid": "ev1", "title": "Тренування", "stage": "t30", "when": "17:00"})
q2 = cfm.ask("cw_cancel", pid2, "Тренування")
r2 = cfm.yes(q2["cid"])
ok(r2.get("ok") and r2.get("confirmed"), "відповідь «Так» виконує дію")
ok(cw.is_blocked("ev1"), "після «Так» подія заблокована")
SENT.clear()
cw.tick()
ok(len(SENT) == 0, f"після «Так» нагадування НЕ приходить (got {len(SENT)})")

# ── 4. Блок переживає gc_sent (головний баг старої версії) ───────────────────
MEM[cw.SENT_FILE] = {"ev1|t30": (NOW - timedelta(days=30)).isoformat()}
cw.gc_sent(days=5)
ok(cw.is_blocked("ev1"), "блок ЖИВИЙ після gc_sent (раніше нагадування воскресали)")
SENT.clear(); cw.tick()
ok(len(SENT) == 0, "і нагадування далі мовчить")

# ── 5. Відкладені «+15 хв» теж знімаються ────────────────────────────────────
cw.unblock_all()
MEM[cw.SNOOZE_FILE] = {"k1": {"evid": "ev2", "title": "Зустріч", "when": "09:00",
                              "due": (NOW - timedelta(minutes=1)).isoformat()}}
pid3 = cw._store.put({"evid": "ev2", "title": "Зустріч", "stage": "snooze", "when": "09:00"})
q3 = cfm.ask("cw_cancel", pid3, "Зустріч")
cfm.yes(q3["cid"])
ok(not MEM.get(cw.SNOOZE_FILE), "після підтвердження відкладені нагадування прибрані")
SENT.clear(); cw._fire_snoozed()
ok(len(SENT) == 0, "відкладене не спрацьовує після скасування")

# ── 6. Те саме для «Не цікавить» по темі ─────────────────────────────────────
gpid = gx._store.put({"topic": "crypto", "trigger": "crypto_move", "text": "BTC -6%"})
qg = cfm.ask("gx_mute", gpid, "крипто")
ok("7 днів" in qg["text"], "у питанні названо термін тиші")
ok(not gx.is_muted("crypto"), "до підтвердження тема НЕ прихована")
cfm.no(qg["cid"])
ok(not gx.is_muted("crypto"), "після «Ні» тема НЕ прихована")
qg2 = cfm.ask("gx_mute", gpid, "крипто")
rg = cfm.yes(qg2["cid"])
ok(rg.get("ok") and gx.is_muted("crypto"), "після «Так» тема прихована")

# ── 7. Протухле питання нічого не ламає ──────────────────────────────────────
gx.unmute_all()
pid4 = cw._store.put({"evid": "ev9", "title": "Стара", "stage": "t30"})
q4 = cfm.ask("cw_cancel", pid4, "Стара")
p = cfm._store.get(q4["cid"]); p["ts"] = (NOW - timedelta(hours=48)).isoformat()
cfm._store.put_at(q4["cid"], p) if hasattr(cfm._store, "put_at") else MEM.setdefault(
    cfm.STORE_FILE, {}).__setitem__(q4["cid"], {"payload": p, "ts": p["ts"]})
r4 = cfm.yes(q4["cid"])
ok(r4.get("error") in ("expired", "payload_missing"),
   f"протухле питання не виконується (got {r4})")
ok(not cw.is_blocked("ev9"), "і подія НЕ заблокована через протухле питання")

# ── 8. Мертвий payload ───────────────────────────────────────────────────────
ok(cfm.yes("neexistuje").get("error") == "payload_missing", "мертвий cid → payload_missing")
ok(cfm.no("neexistuje").get("error") == "payload_missing", "мертвий cid на «Ні» теж")

# ── 9. Розблокування повертає нагадування ────────────────────────────────────
pid5 = cw._store.put({"evid": "ev1", "title": "Тренування", "stage": "t30", "when": "17:00"})
cfm.yes(cfm.ask("cw_cancel", pid5, "Тренування")["cid"])
ok(cw.is_blocked("ev1"), "заблоковано")
n = cw.unblock_all()
ok(n >= 1 and not cw.is_blocked("ev1"), f"/увімкни_нагадування повертає (розблоковано {n})")
MEM[cw.SENT_FILE] = {}
SENT.clear(); cw.tick()
ok(len(SENT) == 1, "після розблокування нагадування знову приходить")

# ── 10. bot.py: cfm_ реально зароутений і не викликає do_cancel напряму ──────
src = open("/home/user/bot/bot.py", encoding="utf-8").read()
ok('data.startswith("cfm_")' in src, "cfm_ доданий у роутер callback")
ok('if data.startswith("cfm_y_")' in src, "є обробник cfm_y_")
ok('elif data.startswith("cfm_n_")' in src, "є обробник cfm_n_")
i = src.find('elif data.startswith("cw_cancel_"):')
seg = src[i:i+700]
ok("_cfm.ask(" in seg, "cw_cancel_ тепер ПИТАЄ, а не виконує")
ok("do_cancel(" not in seg, "cw_cancel_ більше не викликає do_cancel напряму")
j = src.find('elif data.startswith("gx_mute_"):')
seg2 = src[j:j+700]
ok("_cfm2.ask(" in seg2, "gx_mute_ тепер ПИТАЄ")
ok("do_mute(" not in seg2, "gx_mute_ більше не викликає do_mute напряму")

print("\nDONE")
