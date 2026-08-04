#!/usr/bin/env python3
"""Регресійні тести на баги, знайдені реальними кліками в проді 04.08:
1) _ask_note NameError TELEGRAM_CHAT_ID
2) автонотатка зберігала все тіло "Привіт Олеже!..." 
3) calendar_watch писав "Подія: None (None)"
4) feedback_ctx тягнув це сміття в AI-промпт
"""
import sys, json
sys.path.insert(0, "/home/user/bot")

def ok(c, m):
    print(("✅ " if c else "❌ ") + m)

# ── 1. _ask_note: жодних невизначених імен ───────────────────────────────────
import ast
src = open("/home/user/bot/bot.py", encoding="utf-8").read()
tree = ast.parse(src)
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "_ask_note")
names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
ok("TELEGRAM_CHAT_ID" not in names, "_ask_note більше не звертається до TELEGRAM_CHAT_ID (NameError)")
ok("TELEGRAM_CHAT" in names, "_ask_note використовує існуючу TELEGRAM_CHAT")
ok("chat_id" in [a.arg for a in fn.args.args], "_ask_note приймає chat_id")
mod_names = {t.id for n in tree.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}
ok("TELEGRAM_CHAT" in mod_names, "TELEGRAM_CHAT реально визначена в модулі")
ok("TELEGRAM_CHAT_ID" not in mod_names, "TELEGRAM_CHAT_ID як константи не існує — підтверджує баг")

# ── фейковий storage ─────────────────────────────────────────────────────────
import ai_kit as K
MEM = {}
K.load = lambda f, default=None: json.loads(json.dumps(MEM.get(f, default)))
K.save = lambda f, d: MEM.__setitem__(f, json.loads(json.dumps(d))) or True
K.update_key = lambda f, k, v: MEM.setdefault(f, {}).__setitem__(k, json.loads(json.dumps(v))) or True

import ai_buttons as gx
import calendar_watch as cw
for m in (gx, cw):
    m.K.load, m.K.save, m.K.update_key = K.load, K.save, K.update_key

import ai_notes
SAVED = []
ai_notes.add_note = lambda text, source="manual": SAVED.append({"text": text, "source": source})
import response_log
response_log.log_response = lambda *a, **k: None

# ── 2. автонотатка gx: без вітання, без усього тіла ──────────────────────────
BIG = ("Привіт Олеже! 👋\n\nОлеже, який же сьогодні особливий вечір! Вівторок, 4 серпня, "
       "17:13, і я бачу, що ти на нічній зміні. BTC зараз 112400 USD. " + "х" * 900)
pid = gx._store.put({"topic": "crypto", "trigger": "deep_analysis", "text": BIG})
r = gx.do_note(pid)
note = SAVED[-1]["text"]
print("   auto-note:", repr(note[:120]))
ok(r.get("ok") and r.get("own") is False, "do_note без тексту працює і позначає own=False")
ok(not note.lower().startswith("привіт"), "автонотатка НЕ починається з 'Привіт Олеже'")
ok("Привіт Олеже" not in note, "вітання вирізано повністю")
ok(len(note) <= 220, f"автонотатка коротка, не все тіло (got {len(note)})")
ok("крипто" in note.lower() or "crypto" in note.lower(), "автонотатка позначена темою")

# власний текст Олега зберігається як є
pid2 = gx._store.put({"topic": "run", "trigger": "run", "text": BIG})
gx.do_note(pid2, note="коліно болить, тиждень без бігу")
ok(SAVED[-1]["text"] == "коліно болить, тиждень без бігу", "власний текст Олега зберігається дослівно")

# ── 3. calendar_watch: жодних "None (None)" ──────────────────────────────────
pidc = cw._store.put({"evid": "e1", "stage": "t30"})  # title/when відсутні
rc = cw.do_note(pidc)
cnote = SAVED[-1]["text"]
print("   cal auto-note:", repr(cnote))
ok(rc.get("ok"), "calendar do_note не падає без title")
ok("None" not in cnote, "немає 'Подія: None (None)'")

pidc2 = cw._store.put({"evid": "e2", "stage": "t30", "title": "Тренування", "when": "05.08 17:00"})
cw.do_note(pidc2)
ok("Тренування" in SAVED[-1]["text"] and "05.08" in SAVED[-1]["text"],
   "з нормальним payload автонотатка змістовна")
cw.do_note(pidc2, note="взяти нові кросівки")
ok("взяти нові кросівки" in SAVED[-1]["text"] and "Тренування" in SAVED[-1]["text"],
   "власна нотатка + назва події")

# ── 4. feedback_ctx фільтрує сміття ──────────────────────────────────────────
import feedback_ctx as fb
JUNK = [
    {"text": "Привіт Олеже! 👋\n\nОлеже, який же сьогодні особливий вечір!", "source": "qr_note"},
    {"text": "Подія: None (None)", "source": "calendar_watch"},
    {"text": "ок", "source": "manual"},
    {"text": "[крипто] відмічено без коментаря: BTC зараз 112400", "source": "gx_crypto"},
    {"text": "Олег має підписку Strava.", "source": "auto_extract"},
    {"text": "коліно болить, тиждень без бігу", "source": "gx_run"},
]
ai_notes.load_notes = lambda: JUNK
got = [n["text"] for n in fb._notes()]
print("   kept:", got)
ok(all(not t.lower().startswith("привіт") for t in got), "вітання відфільтровано")
ok(not any("None (None)" in t for t in got), "'Подія: None' відфільтровано")
ok(not any(t == "ок" for t in got), "надто коротка нотатка відфільтрована")
ok(not any("відмічено без коментаря" in t for t in got), "порожні автовідмітки не йдуть в промпт")
ok("Олег має підписку Strava." in got, "справжній факт залишився")
ok("коліно болить, тиждень без бігу" in got, "справжня нотатка Олега залишилась")

MEM[gx.ACK_FILE] = {}; MEM[cw.ACK_FILE] = {}
response_log.get_responses = lambda days=7, category=None: []
fb._CACHE.update({"ts": None})
txt = fb.build(7)
ok("Привіт Олеже" not in txt, "фінальний AI-контекст без сміття")
ok("коліно" in txt, "фінальний AI-контекст містить справжню нотатку")

print("\nDONE")
