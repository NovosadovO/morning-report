#!/usr/bin/env python3
"""Офлайн-тести watchdog.py — без пошти, календаря, крипто й Strava."""

import sys
import types
from datetime import datetime, timedelta, timezone

_MEM = {}


class _FakeStorage(types.ModuleType):
    def load_json(self, name, default=None):
        return _MEM.get(name, default)

    def save_json(self, name, data):
        _MEM[name] = data
        return True


sys.modules.setdefault("storage", _FakeStorage("storage"))

import ai_kit as K        # noqa: E402
import watchdog as WD     # noqa: E402

FAILS = []


def ok(cond, msg):
    if cond:
        print(f"✅ {msg}")
    else:
        print(f"❌ {msg}")
        FAILS.append(msg)


_FILES = {}
_SENT = []
_NOW = datetime(2026, 8, 23, 21, 30, tzinfo=timezone.utc)

K.load = lambda f, default=None: _FILES.get(f, default)
K.save = lambda f, d: (_FILES.__setitem__(f, d), True)[1]
K.update_key = lambda f, k, v: (_FILES.setdefault(f, {}).__setitem__(k, v), True)[1]
K.send_card = lambda text, kb=None, tag="", chat_id=None: (
    _SENT.append({"text": text, "tag": tag}), True)[1]
K.now = lambda: _NOW
K.today_str = lambda: _NOW.strftime("%Y-%m-%d")
K.rate_ok = lambda *a, **k: True
K.rate_mark = lambda *a, **k: None
K.esc = lambda s: str(s)


def _reset():
    _FILES.clear()
    _SENT.clear()


def _sensors(mapping):
    """Підміняє живі датчики словником {назва: (ok, detail)}."""
    WD.LIVE = {name: (lambda v=val: v) for name, val in mapping.items()}


# ── 1. Свіжість state-файлів ────────────────────────────────────────────────
print("\n=== 1. свіжість ===")
_reset()
_FILES["fresh.json"] = {"last": (_NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")}
res, detail = WD._check_fresh("fresh.json", "last", 4)
ok(res is True, f"свіжий файл ок: {detail}")

_FILES["stale.json"] = {"last": (_NOW - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S")}
res, detail = WD._check_fresh("stale.json", "last", 4)
ok(res is False, f"застарілий файл спійманий: {detail}")

res, detail = WD._check_fresh("nonexistent.json", "last", 4)
ok(res is None, "відсутній файл → None, а не вигадка")

_FILES["nots.json"] = {"foo": "bar"}
res, detail = WD._check_fresh("nots.json", "", 4)
ok(res is None, "файл без часу → None")

# ── 2. Датчик зламався: перше падіння молчить, друге пише ────────────────────
print("\n=== 2. алерт після другого падіння ===")
_reset()
WD.FRESH = {}
_sensors({"пошта": (False, "IMAP не відповідає")})
n1 = WD.run(force=True)
ok(n1 == 0 and not _SENT, "одна осічка — ще не пишемо (не спам)")
n2 = WD.run(force=True)
ok(n2 == 1 and _SENT, "друга осічка — Олег дізнався")
ok("осліп" in _SENT[-1]["text"], "текст прямо каже, що бот осліп")
ok("IMAP" in _SENT[-1]["text"], "у тексті причина, а не абстракція")

# ── 3. Не спамить щогодини тим самим ─────────────────────────────────────────
print("\n=== 3. без спаму ===")
before = len(_SENT)
WD.run(force=True)
WD.run(force=True)
ok(len(_SENT) == before, "повторні перевірки того ж збою не пишуть знову")

# ── 4. Датчик ожив → «знову бачу» ────────────────────────────────────────────
print("\n=== 4. відновлення ===")
_sensors({"пошта": (True, "12 листів у вибірці")})
WD.run(force=True)
ok("Знову бачу" in _SENT[-1]["text"], "бот сам повідомив про відновлення")
ok("не бачив" in _SENT[-1]["text"] or "12 листів" in _SENT[-1]["text"],
   "у тексті є деталі відновлення")

# ── 5. Усе працює → тиші не порушуємо ────────────────────────────────────────
print("\n=== 5. коли все добре ===")
_reset()
_sensors({"пошта": (True, "ок"), "календар": (True, "ок")})
n = WD.run(force=True)
ok(n == 0 and not _SENT, "коли все працює — не турбуємо")

# ── 6. Щоденний підсумок ─────────────────────────────────────────────────────
print("\n=== 6. щоденна гарантія ===")
_reset()
_sensors({"пошта": (True, "8 листів"), "strava": (False, "403 — доступ треба поновити")})
sent = WD.digest(force=True)
ok(sent is True, "підсумок надіслано")
txt = _SENT[-1]["text"]
ok("Підсумок наглядача" in txt, "це саме підсумок")
ok("strava" in txt and "403" in txt, "сліпа зона названа прямо")
ok("працює 1 із 2" in txt, "чесний рахунок робочих датчиків")

# ── 7. Підсумок раз на добу ──────────────────────────────────────────────────
print("\n=== 7. один підсумок на добу ===")
before = len(_SENT)
ok(WD.digest(force=False) is False, "другий підсумок того ж дня не надсилається")
ok(len(_SENT) == before, "нічого не надіслано")

# ── 8. Немає сліпих зон — так і сказано ──────────────────────────────────────
print("\n=== 8. без сліпих зон ===")
_reset()
_sensors({"пошта": (True, "ок"), "календар": (True, "ок")})
WD.digest(force=True)
ok("сліпих зон немає" in _SENT[-1]["text"], "прямо сказано, що все видно")

# ── 9. Датчик кидає виняток — не валить наглядача ────────────────────────────
print("\n=== 9. виняток у датчику ===")
_reset()


def _boom():
    raise RuntimeError("токен здох")


WD.LIVE = {"календар": _boom}
rows = WD.check_all()
ok(rows and rows[0]["ok"] is False, "виняток = зламаний датчик, не крах")
ok("токен здох" in rows[0]["detail"], "причина винятку видна")

# ── 10. report() ─────────────────────────────────────────────────────────────
print("\n=== 10. report ===")
_reset()
_sensors({"пошта": (True, "ок"), "strava": (False, "403")})
WD.FRESH = {"невідоме": ("nope.json", "last", 4)}
r = WD.report()
ok("🟢" in r and "🔴" in r, "у звіті видно і робочі, і зламані")
ok("⚪" in r, "невідоме позначено окремо, без вигадок")

print("\n" + "=" * 50)
print(f"Падінь: {len(FAILS)}")
for f in FAILS:
    print(f"  ❌ {f}")
