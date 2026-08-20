#!/usr/bin/env python3
"""Офлайн-тест постійного блок-листа: підтвердив «не нагадуй» → тиша назавжди."""
import json, os, sys, tempfile
sys.path.insert(0, "/home/user/bot")

# ── фейковий storage: усе в пам'яті, без GitHub ──────────────────────────────
import ai_kit as K
_MEM = {}
K.load = lambda f, default=None: json.loads(json.dumps(_MEM.get(f, default if default is not None else {})))
def _save(f, d): _MEM[f] = json.loads(json.dumps(d))
K.save = _save
def _upd(f, k, v):
    d = _MEM.setdefault(f, {}); d[k] = json.loads(json.dumps(v))
K.update_key = _upd
def _rem(f, k):
    _MEM.get(f, {}).pop(k, None)
K.remove_key = _rem

import dismissed as D
D._CACHE_TTL = 0

fails = 0
def ok(cond, name):
    global fails
    if cond:
        print(f"✅ {name}")
    else:
        fails += 1
        print(f"❌ {name}")

# 1. чисто
ok(not D.is_muted(key="123", title="Faktúra Michaela"), "спочатку нічого не заглушено")

# 2. mute за ключем і назвою
D.mute("cal_skip", key="123", title="Faktúra Michaela")
ok(D.is_muted("cal_skip", key="123"), "заглушено за ключем")
ok(D.is_muted(key="123"), "ключ знаходиться без вказання kind")
ok(D.is_muted(title="Faktúra Michaela"), "заглушено за назвою")
ok(D.is_muted(title="Re: FAKTÚRA   michaela!!"), "назва матчиться попри Re:/регістр/пунктуацію")
ok(D.is_muted(key="999", title="🔔 «Faktúra Michaela»"), "інший id, та сама назва → все одно тиша")
ok(not D.is_muted(key="999", title="Зовсім інша тема"), "чужа тема НЕ заглушена")

# 3. службовий текст кнопки назвою не стає
D.mute("dm_skip", key=None, title="Не треба")
ok(not D.is_muted(title="Не треба"), "«Не треба» не заглушує все підряд")
ok(not D.is_muted(title="Інший лист"), "після сміттєвої назви нічого не поламалось")

# 4. хук з confirm.yes
D.remember_confirm("gate_cal_skip", "cal_skip_777", "Не треба",
                   "📅 АІ помітив дію:\n«Оплатити рахунок VSE»\nдо 25.08")
ok(D.is_muted("cal_skip", key="777"), "gate_*: ключ узято з хвоста callback_data")
ok(D.is_muted(title="Оплатити рахунок VSE"), "gate_*: назва взята з тексту повідомлення")

n_before = D.count()
ok(D.remember_confirm("gate_cal_add_", "x", "y", "") is False, "не-mute дія не пише в блок-лист")
ok(D.count() == n_before, "розмір блок-листа не змінився")

ok(D.remember_confirm("cw_cancel", "pid1", "Тренування у вівторок", "") is True,
   "cw_cancel закриває тему")
ok(D.is_muted(title="тренування у вівторок"), "подія заглушена за назвою")

# 5. повернення
D.unmute("cal_skip", key="123", title="Faktúra Michaela")
ok(not D.is_muted(key="123"), "unmute зняв ключ")
ok(not D.is_muted(title="Faktúra Michaela"), "unmute зняв назву")
n = D.unmute_all()
ok(n > 0 and D.count() == 0, f"unmute_all очистив ({n})")
ok(not D.is_muted(title="Оплатити рахунок VSE"), "після unmute_all нагадування повернулись")

# 6. звіт
ok("Порожньо" in D.report(), "звіт для порожнього блок-листа")
D.mute("event", key="e1", title="Візит до лікаря")
r = D.report()
ok("Візит до лікаря" in r and "ЗАКРИТІ ТЕМИ" in r, "звіт показує закриту тему")

# 7. відмовостійкість: storage падає → is_muted не блокує роботу
def _boom(*a, **k): raise RuntimeError("github down")
K.load = _boom
D._CACHE["data"] = None
ok(D.is_muted(key="e1", title="Візит до лікаря") is False, "збій storage → не ламає відправку")

# ── перевірка інтеграції в коді (без імпорту важких модулів) ─────────────────
src = {f: open(f"/home/user/bot/{f}").read() for f in
       ("confirm.py", "bot.py", "monitor.py", "calendar_watch.py", "followup_watcher.py")}
ok("remember_confirm" in src["confirm.py"], "confirm.yes() пише в блок-лист")
i = src["confirm.py"].find("_log(str(p.get(\"action\")), str(p.get(\"subject\") or \"\"), \"yes\")")
ok(0 < i < src["confirm.py"].find("import dismissed as _dm_y"), "хук стоїть ПІСЛЯ успішної дії")
ok("dismissed as _dm_act" in src["monitor.py"], "monitor: перевірка перед AI-пропозицією")
ok("dismissed as _dm_em" in src["monitor.py"], "monitor: перевірка перед followup листа")
ok("def is_blocked(evid: str, title: str = \"\")" in src["calendar_watch.py"],
   "calendar_watch: is_blocked знає назву")
ok("is_blocked(ev[\"id\"], ev.get(\"title\") or \"\")" in src["calendar_watch.py"],
   "calendar_watch: назва передається у перевірку")
ok("dismissed as _dm_fu" in src["followup_watcher.py"], "followup: перевірка перед відправкою")
ok("_drop_important_email" in src["bot.py"] and "extra={\"msg\": _msg_txt}" in src["bot.py"],
   "bot: email_keep чистить важливі + гейт передає текст")

print(f"\nfails: {fails}")
