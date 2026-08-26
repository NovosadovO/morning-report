"""Офлайн-тести healthai.py — без мережі, без Telegram, без Gemini."""

import sys
import types
from datetime import datetime, timedelta

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {extra}")
        FAILS.append(name)


# ── заглушки ДО імпорту модуля ───────────────────────────────────────────────
STORE = {}
SENT = []
JOURNALED = []


def _mk_ai_kit():
    m = types.ModuleType("ai_kit")

    def now():
        return datetime(2026, 8, 26, 21, 15)

    m.now = now
    m.today_str = lambda: "2026-08-26"
    m.log = lambda tag, msg: None
    m.load = lambda f, default=None: STORE.get(f, default if default is not None else {})
    m.save = lambda f, d: STORE.__setitem__(f, d)
    m.update_key = lambda f, k, v: None
    m.remove_key = lambda f, k: None
    m.send_card = lambda text, kb=None, tag="", chat_id=None: (SENT.append(text) or True)
    m.gemini_text = lambda p, max_tokens=1400, temperature=0.7, tag="": "AI-текст [тест]"
    m.gemini_json = lambda p, **kw: []
    m.rate_ok = lambda f, gap: True
    m.rate_mark = lambda f: None
    m.esc = lambda s: str(s)
    return m


def _mk_storage(health):
    m = types.ModuleType("storage")
    m.load_health = lambda: health
    m.load_weight = lambda: {}
    return m


def _mk_qwsync():
    m = types.ModuleType("qwsync")
    saved = []

    def normalize(payload):
        out = {}
        mapping = {"weight": "weight_kg", "steps": "steps", "hr": "hr_avg",
                   "sleep_min": "sleep_total_min", "hrv": "hrv"}
        for k, v in (payload or {}).items():
            key = mapping.get(k)
            if not key:
                continue
            try:
                out[key] = float(str(v).replace(",", ".").replace(" ", ""))
            except Exception:
                pass
        return out

    m.normalize = normalize
    m.save = lambda fields, notify=True: saved.append(fields) or fields
    m._saved = saved
    return m


def _mk_selfact():
    m = types.ModuleType("selfact")
    m.journal = lambda kind, what, detail="", module="": JOURNALED.append((kind, what))
    return m


def _mk_stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def build_health(days=20):
    """Синтетичні дані: вага росте, сон короткий, кроки низькі."""
    out = {}
    base = datetime(2026, 8, 26)
    for i in range(days):
        d = (base - timedelta(days=i)).strftime("%Y-%m-%d")
        out[d] = {
            "weight_kg": round(83.0 + (days - i) * 0.12, 1),
            "sleep_hours": 6.0,
            "steps": 4000,
            "hr_avg": 90,
        }
    return out


HEALTH = build_health()
sys.modules["ai_kit"] = _mk_ai_kit()
sys.modules["storage"] = _mk_storage(HEALTH)
sys.modules["qwsync"] = _mk_qwsync()
sys.modules["selfact"] = _mk_selfact()
sys.modules["dismissed"] = _mk_stub("dismissed", is_muted=lambda k: False,
                                    mute=lambda *a, **k: None)
sys.modules["quiet"] = _mk_stub("quiet", blocked=lambda kind: False)

import healthai as H  # noqa: E402

print("\n1. Розбір вільного тексту")
kv = H._kv_from_text("вага 83,4 сон 7г 20хв кроки 9 120 пульс 68")
check("вага", kv.get("weight") == "83,4", kv)
check("сон у хвилинах", kv.get("sleep_min") == 440, kv)
check("кроки", kv.get("steps") == "9120", kv)
check("пульс", kv.get("hr") == "68", kv)

print("\n2. capture зберігає сирий текст і числа")
STORE.clear()
f = H.capture("Вага 83.4, спав 7г, кроки 9120", source="test")
j = H.load_journal()
check("поля розібрані", bool(f), f)
check("журнал не порожній", len(j) == 1, len(j))
check("сирий текст цілий", "83.4" in j[0]["raw"], j[0]["raw"])
check("qwsync викликано", len(sys.modules["qwsync"]._saved) == 1)
check("selfact журнал", any(x[0] == "note" for x in JOURNALED), JOURNALED)

print("\n3. capture ігнорує порожнє")
before = len(H.load_journal())
H.capture("   ")
check("нічого не додано", len(H.load_journal()) == before)

print("\n4. analytics на синтетичних даних")
a = H.analytics(30)
check("вага є", a["weight"]["last"] is not None, a["weight"])
check("сон середній 6.0", a["sleep"]["avg7"] == 6.0, a["sleep"])
check("кроки середні 4000", a["steps"]["avg7"] == 4000.0, a["steps"])
check("тренд ваги визначено", a["weight"]["trend"] in ("зростає", "падає", "стабільно"),
      a["weight"]["trend"])
check("до цілі порахувано", a["weight"]["to_goal"] is not None, a["weight"])
check("streak > 0", a["streak"] > 0, a["streak"])
check("stale_hours число", isinstance(a["stale_hours"], int), a["stale_hours"])

print("\n5. facts_block — без падінь і з числами")
fb = H.facts_block(a)
check("вага в тексті", "Вага" in fb, fb[:80])
check("сон в тексті", "Сон" in fb)
check("streak в тексті", "Днів підряд" in fb)

print("\n6. analytics на ПОРОЖНІХ даних не падає")
sys.modules["storage"].load_health = lambda: {}
a0 = H.analytics(30)
fb0 = H.facts_block(a0)
check("немає даних — не падає", "немає даних" in fb0, fb0[:120])
check("streak = 0", a0["streak"] == 0, a0["streak"])
sys.modules["storage"].load_health = lambda: HEALTH

print("\n7. anomalies знаходить проблеми")
an = H.anomalies(H.analytics(30))
keys = [k for k, _ in an]
check("вага вгору", "weight_up" in keys, keys)
check("сон низький", "sleep_low" in keys, keys)
check("кроки низькі", "steps_low" in keys, keys)
check("пульс високий", "hr_high" in keys, keys)

print("\n8. ідеальні дані — жодної аномалії")
good = {}
for i in range(20):
    d = (datetime(2026, 8, 26) - timedelta(days=i)).strftime("%Y-%m-%d")
    good[d] = {"weight_kg": 78.0, "sleep_hours": 8.0, "steps": 11000, "hr_avg": 60}
sys.modules["storage"].load_health = lambda: good
check("чисто", H.anomalies(H.analytics(30)) == [], H.anomalies(H.analytics(30)))
sys.modules["storage"].load_health = lambda: HEALTH

print("\n9. звіти генеруються")
SENT.clear()
c = H.coach_report(send=True)
check("коуч надіслано", len(SENT) == 1, len(SENT))
check("коуч має AI", "AI-текст" in c)
t = H.tracker_report(send=True)
check("трекер надіслано", len(SENT) == 2, len(SENT))
check("трекер має факти", "AI-ТРЕКЕР" in t)
w = H.weekly_report(send=True)
check("тижневий надіслано", len(SENT) == 3, len(SENT))
check("stats без AI", "Здоров'я — факти" in H.stats_report())

print("\n10. initiative — сповіщення + дедуп 12 год")
STORE.clear()
SENT.clear()
n1 = H.initiative(force=True)
check("перший раз надіслав", n1 >= 3, n1)
n2 = H.initiative()
check("вдруге придушено дедупом", n2 == 0, n2)

print("\n11. mute глушить ініціативу")
STORE.clear()
sys.modules["dismissed"].is_muted = lambda k: True
check("mute працює", H.initiative() == 0)
sys.modules["dismissed"].is_muted = lambda k: False

print("\n12. tick о 21:15 запускає трекер один раз")
STORE.clear()
SENT.clear()
r1 = H.tick()
check("трекер у слоті", "tracker" in r1, r1)
r2 = H.tick()
check("двічі не повторює", "tracker" not in r2, r2)

print("\n13. tick поза слотом нічого не ламає")
sys.modules["ai_kit"].now = lambda: datetime(2026, 8, 26, 14, 3)
STORE.clear()
out = H.tick()
check("без слотів — тільки ініціатива", "tracker" not in out and "coach" not in out, out)

print("\n" + ("ВСЕ ОК" if not FAILS else f"ПАДІНЬ: {len(FAILS)} -> {FAILS}"))
sys.exit(1 if FAILS else 0)
