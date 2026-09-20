"""Офлайн-тести: AI бачить календар на місяць вперед + щойно минуле
в КОЖНОМУ промпті (не тільки в спеціальних календарних тригерах).

Скарга Олега (20.09): «зроби так щоб AI бачив все в календарі на місяць
вперед ... і бачив те що зараз минуло».

Раніше allctx._src_calendar() (джерело, що йде в КОЖЕН AI-запит через
monitor._gem_post) викликало context.get_calendar_events(days=7), яка
насправді ІГНОРУВАЛА days і завжди повертала лише сьогодні+завтра. Тепер
джерело — calendar_watch.ai_context_text(): щойно минуле (6 год) + тиждень
детально + решта місяця вперед.
"""
import sys
import types
import json
from datetime import timedelta, timezone

sys.path.insert(0, "/home/user/repo")

FAIL = []


def ok(c, m):
    print(("  ✅ " if c else "  ❌ ") + m)
    if not c:
        FAIL.append(m)


# ── мок storage/telegram (як у tests_calendar_watch.py) ──────────────────────
MEM = {}
st = types.ModuleType("storage")
st.load = lambda f, default=None: json.loads(json.dumps(MEM.get(f, default if default is not None else {})))
st.save = lambda f, d: (MEM.__setitem__(f, json.loads(json.dumps(d))), True)[1]
st.update_key = lambda f, k, v, default=None: (MEM.setdefault(f, {}).__setitem__(k, json.loads(json.dumps(v))), True)[1]
st.remove_key = lambda f, k: MEM.setdefault(f, {}).pop(k, None)
sys.modules["storage"] = st

import ai_kit as K
K.TELEGRAM_TOKEN = "x"
K.TELEGRAM_CHAT = "1"
K.send_card = lambda text, keyboard=None, tag="", chat_id=None: True

NOW = K.now().replace(tzinfo=None)


def iso_utc(dt_local):
    return (dt_local - K.TZ).replace(tzinfo=timezone.utc).isoformat()


def mk(eid, title, start_local, dur=60, loc=""):
    return {"id": eid, "summary": title, "location": loc,
            "start": {"dateTime": iso_utc(start_local)},
            "end": {"dateTime": iso_utc(start_local + timedelta(minutes=dur))}}


EVENTS = []
mon = types.ModuleType("monitor")
mon._calendar_access_token = lambda: "tok"
mon._fetch_events_all_calendars = lambda h, a, b, max_per_cal=40: list(EVENTS)
sys.modules["monitor"] = mon

import calendar_watch as C

print("=== 1. recent_past_text(): щойно минула подія бачна ===")
EVENTS = [
    # завершилась 40 хв тому — має потрапити в "щойно минуло"
    mk("p1", "Зустріч з клієнтом", NOW - timedelta(hours=1, minutes=40), 60),
    # завершилась 3 дні тому — НЕ має потрапити (за межами 6-годинного вікна)
    mk("p2", "Стара зустріч", NOW - timedelta(days=3, hours=1), 60),
    # рутина — не має потрапити навіть у вікні
    mk("p3", "🏃 Біг 5 км", NOW - timedelta(hours=1)),
]
C._cache["ts"] = None
past = C.recent_past_text(hours=6)
ok("Зустріч з клієнтом" in past, "нещодавно завершена подія в тексті: " + past)
ok("завершилась" in past and "тому" in past, "формат «завершилась N хв/год тому»: " + past)
ok("Стара зустріч" not in past, "подія 3 дні тому НЕ в межах 6-годинного вікна")
ok("Біг 5 км" not in past, "рутина не потрапляє в «щойно минуло»")

print("=== 2. ai_context_text(): місяць вперед + щойно минуле одним блоком ===")
EVENTS = [
    mk("p1", "Зустріч з клієнтом", NOW - timedelta(hours=1, minutes=40), 60),
    mk("f1", "Дзвінок Michaela", NOW + timedelta(hours=2)),
    mk("f2", "Візит до лікаря", NOW + timedelta(days=3)),
    mk("f3", "День народження мами", NOW + timedelta(days=20)),
    mk("f4", "Технагляд авто", NOW + timedelta(days=29)),
    mk("far", "Занадто далеко", NOW + timedelta(days=45)),  # за межами 31 дня
    mk("shift1", "🌙 Нічна зміна", NOW + timedelta(days=1), 720),  # зміна — не показуємо
]
C._cache["ts"] = None
blk = C.ai_context_text(past_hours=6, ahead_days=31, limit=30)
print("BLOCK:", blk)
ok("ЩОЙНО МИНУЛО" in blk and "Зустріч з клієнтом" in blk, "блок містить щойно минуле")
ok("Дзвінок Michaela" in blk, "найближчі 7 днів: дзвінок Michaela")
ok("Візит до лікаря" in blk, "найближчі 7 днів: лікар")
ok("День народження мами" in blk, "решта місяця: день народження мами (20 днів)")
ok("Технагляд авто" in blk, "решта місяця: технагляд (29 днів)")
ok("Занадто далеко" not in blk, "подія за межами 31 дня НЕ в блоці")
ok("Нічна зміна" not in blk, "зміна не потрапляє як подія (є окреме джерело shift)")

print("=== 3. allctx._src_calendar() використовує calendar_watch, не лише 2 дні ===")
import allctx as A
A._CACHE.update({"at": 0, "text": "", "status": {}})
C._cache["ts"] = None
txt = A._src_calendar()
ok("День народження мами" in txt, "allctx-джерело бачить подію через 20 днів (місяць вперед)")
ok("Зустріч з клієнтом" in txt, "allctx-джерело бачить щойно минулу подію")

print()
if FAIL:
    print(str(len(FAIL)) + " ПРОВАЛЕНО: " + "; ".join(FAIL))
    sys.exit(1)
print("Усі тести ai_calendar_ctx пройшли ✅")
