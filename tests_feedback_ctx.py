#!/usr/bin/env python3
"""Тест feedback_ctx: збір реакцій з усіх джерел у AI-контекст."""
import os, sys, json
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/user/bot")

TZ = timezone(timedelta(hours=2))
now = datetime.now(TZ)

import ai_kit as K

# ── фейковий storage у пам'яті ────────────────────────────────────────────────
MEM = {}
def _load(f, default=None):
    return json.loads(json.dumps(MEM.get(f, default)))
def _save(f, d):
    MEM[f] = json.loads(json.dumps(d)); return True
def _upd(f, k, v):
    MEM.setdefault(f, {})[k] = json.loads(json.dumps(v)); return True
K.load, K.save, K.update_key = _load, _save, _upd

import ai_buttons as gx
import calendar_watch as cw
gx.K.load, gx.K.save, gx.K.update_key = _load, _save, _upd
cw.K.load, cw.K.save, cw.K.update_key = _load, _save, _upd

import feedback_ctx as fb
fb._CACHE.update({"ts": None})

def ok(c, m):
    print(("✅ " if c else "❌ ") + m)

# ── дані ──────────────────────────────────────────────────────────────────────
MEM[gx.ACK_FILE] = {
    "a1": {"answer": "done", "topic": "health", "preview": "вага 83.4", "ts": (now - timedelta(hours=3)).isoformat()},
    "a2": {"answer": "muted_7d", "topic": "crypto", "preview": "BTC -6%", "ts": (now - timedelta(hours=5)).isoformat()},
    "a3": {"answer": "noted", "topic": "run", "note": "коліно болить, тиждень без бігу", "ts": (now - timedelta(hours=8)).isoformat()},
    "a4": {"answer": "later_60", "topic": "email", "ts": (now - timedelta(days=30)).isoformat()},  # старий, не має попасти
}
MEM[cw.ACK_FILE] = {
    "c1": {"answer": "done", "title": "Тренування", "when": "05.08 17:00", "stage": "after", "ts": (now - timedelta(hours=2)).isoformat()},
    "c2": {"answer": "cancelled", "title": "Presov meeting", "when": "06.08 09:00", "stage": "t24h", "ts": (now - timedelta(hours=4)).isoformat()},
}

import response_log as rl
rl.get_responses = lambda days=7, category=None: [
    {"ts": now.isoformat(), "category": "day_energy", "question": "Як ти", "answer": "втомлений після нічної"},
]
import ai_notes
ai_notes.load_notes = lambda: [{"text": "купити нові кросівки", "ts": now.isoformat()}]

fb._CACHE.update({"ts": None, "days": None, "text": "", "stats": {}})
st = fb.stats(7)
print("STATS:", st)
ok(st["total"] == 5, f"старий запис (30 дн.) відкинуто, всього 5 (got {st['total']})")
ok(st["done"] == 2, f"done=2 з двох джерел (got {st['done']})")
ok(st["muted"] == 1, f"muted=1 (got {st['muted']})")
ok(st["noted"] == 1, f"noted=1 (got {st['noted']})")

mt = fb.muted_topics(3)
ok(mt == ["crypto"], f"приховані теми = crypto (got {mt})")

fb._CACHE.update({"ts": None})
txt = fb.build(7)
print("\n--- КОНТЕКСТ ДЛЯ AI ---\n" + txt + "\n-----------------------")
ok("РЕАКЦІЇ ОЛЕГА" in txt, "є блок статистики реакцій")
ok("НЕ ЦІКАВИТЬ" in txt, "є попередження про приховану тему")
ok("коліно" in txt, "нотатка з кнопки попала в контекст")
ok("Presov" in txt, "календарна відповідь попала в контекст")
ok("втомлений" in txt, "текстова відповідь попала в контекст")
ok("кросівки" in txt, "ai_notes попали в контекст")
ok(len(txt) <= fb.MAX_CHARS, f"довжина в межах {fb.MAX_CHARS} (got {len(txt)})")

# ── кеш ───────────────────────────────────────────────────────────────────────
calls = {"n": 0}
_orig = fb._buttons
def counted(c):
    calls["n"] += 1; return _orig(c)
fb._buttons = counted
fb.build(7); fb.build(7)
ok(calls["n"] == 0, f"повторний build з кешу — 0 читань (got {calls['n']})")
fb._buttons = _orig

# ── порожньо ─────────────────────────────────────────────────────────────────
MEM[gx.ACK_FILE] = {}; MEM[cw.ACK_FILE] = {}
rl.get_responses = lambda days=7, category=None: []
ai_notes.load_notes = lambda: []
fb._CACHE.update({"ts": None})
ok(fb.build(7) == "", "немає даних → порожній рядок (AI нічого не вигадує)")

# ── падіння джерела не ламає модуль ──────────────────────────────────────────
def boom(*a, **k):
    raise RuntimeError("storage down")
K.load = boom; gx.K.load = boom; cw.K.load = boom
fb._CACHE.update({"ts": None})
try:
    r = fb.build(7)
    ok(True, f"джерело впало → build не падає (got {r!r})")
except Exception as e:
    ok(False, f"build упав: {e}")

# ── ack пише в response_log ──────────────────────────────────────────────────
K.load, gx.K.load, cw.K.load = _load, _load, _load
logged = []
rl.log_response = lambda cat, q, a, extra=None: logged.append((cat, q, a))
pid = gx._store.put({"topic": "health", "trigger": "health", "text": "вага"})
gx.do_ack(pid, "seen")
ok(any(c == "ai_button" for c, _, _ in logged), f"ai_buttons пише в response_log (got {logged})")

print("\nDONE")
