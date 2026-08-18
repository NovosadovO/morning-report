#!/usr/bin/env python3
"""Тести режиму сну (/сон): тиша, нуль AI, авто-пробудження о 04:00."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = 0


def ok(cond, msg):
    global FAILS
    if cond:
        print(f"✅ {msg}")
    else:
        FAILS += 1
        print(f"❌ {msg}")


import quiet  # noqa: E402

# ── ізолюємо стан у пам'яті, щоб не чіпати прод-файл ─────────────────────────
_MEM = {}
quiet._load = lambda: dict(_MEM)


def _save(d):
    _MEM.clear()
    _MEM.update(d)
    return True


quiet._save = _save
quiet._last_user_action["ts"] = 0.0

NOW = datetime(2026, 8, 17, 23, 30)
quiet._now = lambda: NOW

# ── 1. розрахунок пробудження ────────────────────────────────────────────────
ok(quiet._next_wake(datetime(2026, 8, 17, 23, 30)) == datetime(2026, 8, 18, 4, 0),
   "23:30 → пробудження 04:00 наступного дня")
ok(quiet._next_wake(datetime(2026, 8, 18, 2, 15)) == datetime(2026, 8, 18, 4, 0),
   "02:15 → пробудження 04:00 того ж дня")
ok(quiet._next_wake(datetime(2026, 8, 18, 4, 0)) == datetime(2026, 8, 19, 4, 0),
   "рівно 04:00 → наступна доба (не нульовий сон)")

# ── 2. до /сон нічого не блокується ──────────────────────────────────────────
quiet.clear_user_thread()
ok(quiet.is_quiet() is False, "без /сон режим сну вимкнений")
ok(quiet.blocked("msg") is False, "без /сон повідомлення НЕ блокуються")
ok(quiet.blocked("ai") is False, "без /сон AI НЕ блокується")

# ── 3. /сон ──────────────────────────────────────────────────────────────────
r = quiet.sleep_on()
ok(r["ok"], "/сон зберіг стан")
ok(r["until"] == datetime(2026, 8, 18, 4, 0), "/сон о 23:30 → тиша до 04:00")
ok(4.4 < r["hours"] < 4.6, f"тривалість ≈4.5 год (отримано {r['hours']:.2f})")
ok(quiet.is_quiet() is True, "після /сон режим сну активний")
ok(quiet.blocked("msg") is True, "🔕 сповіщення блокуються")
ok(quiet.blocked("ai") is True, "🤖 AI-виклики блокуються (кредити цілі)")

# ── 4. команди Олега працюють і вночі ────────────────────────────────────────
quiet.mark_user_thread()
ok(quiet.blocked("msg") is False, "потік Олега: відповідь на команду НЕ глушиться")
ok(quiet.blocked("ai") is False, "потік Олега: AI на його запит дозволений")
quiet.clear_user_thread()
ok(quiet.blocked("msg") is True, "фоновий потік знову глушиться")

# ── 5. лічильник придушеного ─────────────────────────────────────────────────
before = quiet._muted_counter["msg"]
quiet.blocked("msg")
quiet.blocked("msg")
ok(quiet._muted_counter["msg"] == before + 2, "лічильник придушених сповіщень росте")

# ── 6. АВТО-ПРОБУДЖЕННЯ о 04:00 без крону ────────────────────────────────────
quiet._now = lambda: datetime(2026, 8, 18, 3, 59)
ok(quiet.is_quiet() is True, "03:59 — ще тиша")
quiet._now = lambda: datetime(2026, 8, 18, 4, 0)
ok(quiet.is_quiet() is False, "☀️ 04:00 — бот сам відновив роботу")
ok(quiet.blocked("msg") is False, "після 04:00 сповіщення знову йдуть")
ok(quiet.blocked("ai") is False, "після 04:00 AI знову працює")
ok(_MEM.get("until") in (None, ""), "стан очищено при авто-пробудженні")
ok(bool(_MEM.get("last_wake")), "записано час пробудження")

# ── 7. повторний виклик is_quiet стабільний ──────────────────────────────────
ok(quiet.is_quiet() is False and quiet.is_quiet() is False,
   "повторні перевірки після пробудження не ламаються")

# ── 8. ручний вихід /прокинувся ──────────────────────────────────────────────
quiet._now = lambda: datetime(2026, 8, 18, 1, 0)
quiet.sleep_on()
ok(quiet.is_quiet() is True, "знову заснув о 01:00")
w = quiet.sleep_off()
ok(w["was_sleeping"] is True, "/прокинувся бачить, що бот спав")
ok(quiet.is_quiet() is False, "/прокинувся вимкнув тишу")
w2 = quiet.sleep_off()
ok(w2["was_sleeping"] is False, "повторний /прокинувся — просто інформує")

# ── 9. кастомна година пробудження ───────────────────────────────────────────
quiet.WAKE_HOUR = 4
quiet._now = lambda: datetime(2026, 8, 18, 22, 0)
r6 = quiet.sleep_on(wake_hour=6)
ok(r6["until"] == datetime(2026, 8, 19, 6, 0), "sleep_on(wake_hour=6) → 06:00")
quiet.WAKE_HOUR = 4
quiet.sleep_off()

# ── 10. тексти ───────────────────────────────────────────────────────────────
quiet._now = lambda: datetime(2026, 8, 18, 23, 0)
t = quiet.sleep_text(quiet.sleep_on())
ok("04:00" in t and "РЕЖИМ СНУ" in t, "текст /сон містить годину пробудження")
ok("/прокинувся" in t, "текст /сон підказує ручний вихід")
st = quiet.status_text()
ok("УВІМК" in st, "статус показує активну тишу")
quiet.sleep_off()
ok("ВИКЛ" in quiet.status_text(), "статус показує вимкнену тишу")

# ── 11. guard реально стоїть у сендерах ──────────────────────────────────────
import ast  # noqa: E402

EXPECT = {
    "monitor.py": ["_send_telegram_chunk", "_send_telegram_photo",
                   "_send_telegram_text_with_keyboard", "_send_album"],
    "bot.py": ["send", "send_with_keyboard", "send_photo"],
    "monitor_loop.py": ["tg_send_with_buttons", "_send_astro"],
    "ai_kit.py": ["tg"],
    "proactive.py": ["_send_chunk"],
    "message_generator.py": ["_tg_api"],
    "smart_notifications_v3.py": [],
}
here = os.path.dirname(os.path.abspath(__file__))
for f, funcs in EXPECT.items():
    src = open(os.path.join(here, f), encoding="utf-8").read()
    tree = ast.parse(src)
    for fn in funcs:
        found = False
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name == fn:
                seg = ast.get_source_segment(src, n) or ""
                if "quiet-guard" in seg and '_q_g.blocked("msg")' in seg:
                    found = True
        ok(found, f"quiet-guard стоїть у {f}:{fn}()")

# AI-гейт у централізованому Gemini-виклику
msrc = open(os.path.join(here, "monitor.py"), encoding="utf-8").read()
gem = msrc.split("def _gem_post")[1][:1200]
ok('_q_gem.blocked("ai")' in gem, "quiet-guard стоїть у monitor._gem_post() (кредити AI)")

# команди зареєстровані
bsrc = open(os.path.join(here, "bot.py"), encoding="utf-8").read()
for cmd in ['"/сон"', '"/прокинувся"', '"/режим_сну"', '"/аналіз_сну"']:
    ok(cmd in bsrc, f"команда {cmd} є в bot.py")
ok("_q_cmd.mark_user_thread()" in bsrc, "handle_command позначає потік Олега")
ok("_q_cb.mark_user_thread()" in bsrc, "_route_callback позначає потік Олега")


# ── 12. дірки, через які сповіщення пролітали (фікс 18.08) ────────────────────
DIRECT = {
    "monitor.py": ["check_smart_notifications", "check_event_done",
                   "check_sleep_quality", "check_mood_evening"],
    "monitor_loop.py": ["run_astro_alert_watcher"],
    "proactive.py": ["_do_request"],
    "habits.py": ["api"],
    "meds.py": ["api"],
    "weekly_report.py": ["api"],
}
for f, funcs in DIRECT.items():
    src = open(os.path.join(here, f), encoding="utf-8").read()
    tree = ast.parse(src)
    for fn in funcs:
        segs = [ast.get_source_segment(src, n) or "" for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == fn]
        ok(bool(segs) and all("quiet-guard" in s for s in segs),
           f"quiet-guard закриває пряму відправку {f}:{fn}()")

# жодна функція з api.telegram.org не лишилась без guard (крім реакцій на Олега)
ALLOWED = {("bot.py", "api"), ("bot.py", "handle_command"),
           ("bot.py", "_download_telegram_file"), ("bot.py", "handle_health_zip"),
           ("health_ocr.py", "download_telegram_photo")}
import glob  # noqa: E402
leaks = []
for path in glob.glob(os.path.join(here, "*.py")):
    f = os.path.basename(path)
    if f.startswith("tests_"):
        continue
    src = open(path, encoding="utf-8").read()
    if "api.telegram.org" not in src:
        continue
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, n) or ""
            if "api.telegram.org" in seg and "quiet-guard" not in seg:
                if (f, n.name) not in ALLOWED:
                    leaks.append(f"{f}:{n.name}")
ok(not leaks, f"немає незакритих відправників (знайдено: {leaks})")

# ── 13. стан читається локально (щоб діяв у всіх процесах негайно) ───────────
qsrc = open(os.path.join(here, "quiet.py"), encoding="utf-8").read()
ok("LOCAL_FILE" in qsrc and "/tmp/quiet_mode.json" in qsrc,
   "стан дублюється в локальний файл (спільний для subprocess-ів)")
ok("invalidate_cache" in qsrc, "GitHub-кеш інвалідується (не 5-хвилинна дірка)")

# ── 14. вікно активності Олега ──────────────────────────────────────────────
import time as _tt
quiet._now = lambda: datetime(2026, 8, 18, 23, 0)
quiet._last_user_action["ts"] = 0.0
quiet.sleep_on()
quiet.clear_user_thread()
ok(quiet.blocked("msg") is True, "після /сон фонове сповіщення блокується одразу")
ok(quiet._last_user_action["ts"] == 0.0,
   "/сон скидає вікно активності (інакше 5 хв пролітали сповіщення)")
quiet.touch_user()
ok(quiet.blocked("msg") is False,
   "Олег написав → фоновий потік його команди (напр. /звіт) НЕ глушиться")
quiet._last_user_action["ts"] = _tt.time() - (quiet.ACTIVE_MIN * 60 + 10)
ok(quiet.blocked("msg") is True, "через 5 хв тишина знову діє")
quiet._last_user_action["ts"] = 0.0
quiet.sleep_off()

bsrc2 = open(os.path.join(here, "bot.py"), encoding="utf-8").read()
ok("_q_cmd.touch_user()" in bsrc2, "handle_command оновлює вікно активності")
ok("_q_cb.touch_user()" in bsrc2, "_route_callback оновлює вікно активності")

print(f"\nfails: {FAILS}")
sys.exit(1 if FAILS else 0)
