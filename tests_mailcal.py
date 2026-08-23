#!/usr/bin/env python3
"""Офлайн-тести mailcal.py і tidy.py — без пошти, Gemini й календаря."""

import sys
import types
from datetime import datetime, timedelta, timezone

# ── Заглушка storage (як у tests_nowctx) ─────────────────────────────────────
_MEM = {}


class _FakeStorage(types.ModuleType):
    def load_json(self, name, default=None):
        return _MEM.get(name, default)

    def save_json(self, name, data):
        _MEM[name] = data
        return True


sys.modules.setdefault("storage", _FakeStorage("storage"))

import ai_kit as K            # noqa: E402
import mailcal as MC          # noqa: E402
import tidy as TD             # noqa: E402

FAILS = []


def ok(cond, msg):
    if cond:
        print(f"✅ {msg}")
    else:
        print(f"❌ {msg}")
        FAILS.append(msg)


# ── Детермінований шар замість storage/Telegram/Gemini/календаря ─────────────
_FILES = {}
_SENT = []
_CAL = []
_DELETED = []
_NOW = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)


def _load(filename, default=None):
    return _FILES.get(filename, default)


def _save(filename, data):
    _FILES[filename] = data
    return True


def _update_key(filename, key, value):
    d = _FILES.setdefault(filename, {})
    d[key] = value
    return True


def _remove_key(filename, key):
    d = _FILES.get(filename) or {}
    d.pop(key, None)
    return True


def _send_card(text, keyboard=None, tag="", chat_id=None):
    _SENT.append({"text": text, "kb": keyboard, "tag": tag})
    return True


def _calendar_event(summary, start_dt, end_dt=None, description=""):
    _CAL.append({"summary": summary, "start": start_dt, "desc": description})
    return {"ok": True, "event_id": f"ev{len(_CAL)}", "link": "http://x"}


K.load = _load
K.save = _save
K.update_key = _update_key
K.remove_key = _remove_key
K.send_card = _send_card
K.calendar_event = _calendar_event
K.now = lambda: _NOW
K.today_str = lambda: _NOW.strftime("%Y-%m-%d")
K.rate_ok = lambda *a, **k: True
K.rate_mark = lambda *a, **k: None

# _store / _dedup створені на імпорті до підміни → перестворюємо
MC._store = K.PayloadStore(MC.STORE_FILE)
MC._dedup = K.Dedup(MC.SENT_FILE, ttl_days=60)
MC._calendar_delete = lambda eid: (_DELETED.append(eid) or True)


def _reset():
    _FILES.clear()
    _SENT.clear()
    _CAL.clear()
    _DELETED.clear()
    MC._store = K.PayloadStore(MC.STORE_FILE)
    MC._dedup = K.Dedup(MC.SENT_FILE, ttl_days=60)


# ── 1. Нормалізація часу ─────────────────────────────────────────────────────
print("\n=== 1. час ===")
ok(MC._norm_time("9:30") == "09:30", "9:30 → 09:30")
ok(MC._norm_time("18:05") == "18:05", "18:05 без змін")
ok(MC._norm_time(None) == "", "None → порожньо")
ok(MC._norm_time("null") == "", "'null' → порожньо")
ok(MC._norm_time("99:99") == "", "неможливий час відкинуто")

# ── 2. Санітарна перевірка дати ──────────────────────────────────────────────
print("\n=== 2. дата ===")
ok(MC._sane_date("2026-09-01") == "2026-09-01", "майбутня дата ок")
ok(MC._sane_date("2026-08-23") == "2026-08-23", "сьогодні ок")
ok(MC._sane_date("2020-07-20") == "", "минуле відкинуто")
ok(MC._sane_date("2030-01-01") == "", "надто далеке майбутнє відкинуто")
ok(MC._sane_date("не дата") == "", "смітник відкинуто")

# ── 3. Порожній результат AI → нічого в календарі ────────────────────────────
print("\n=== 3. немає дат — молчимо ===")
_reset()
MC._emails = lambda limit=8: [{"uid": "1", "sender": "a@b", "subject": "Sale",
                               "body": "знижки"}]
MC._ask = lambda mails: []
n = MC.run(force=True)
ok(n == 0, "подій не створено")
ok(not _CAL, "календар не чіпали")
ok(not _SENT, "Олега не турбували")
ok(any(k.endswith("_none") for k in (_FILES.get(MC.ITEMS_FILE) or {})),
   "лист позначено як проглянутий")

# ── 4. Подія створюється сама + картка постфактум ────────────────────────────
print("\n=== 4. подія створюється автоматично ===")
_reset()
MC._emails = lambda limit=8: [{"uid": "77", "sender": "Ryanair <no@ryanair.com>",
                               "subject": "Your flight KSC-VIE",
                               "body": "Departure 2026-09-12 06:40"}]
MC._ask = lambda mails: [{"uid": "77", "title": "Рейс Кошице — Вієнна",
                          "date": "2026-09-12", "time": "06:40",
                          "kind": "flight", "why": "виліт з листа Ryanair"}]
n = MC.run(force=True)
ok(n == 1, "створено 1 подію")
ok(len(_CAL) == 1, "у календар пішов 1 запис")
ok("✈️" in _CAL[0]["summary"], "емодзі виду події в назві")
ok(_CAL[0]["start"].strftime("%Y-%m-%d %H:%M") == "2026-09-12 06:40",
   "дата й час правильні")
ok(len(_SENT) == 1, "надіслано одну картку")
ok("Додав у твій календар" in _SENT[0]["text"], "картка каже, що подію вже додано")
kb_flat = [b for row in (_SENT[0]["kb"] or []) for b in row]
ok(any(b["callback_data"].startswith("mc_del_") for b in kb_flat),
   "є кнопка прибрати")
ok(any(b["callback_data"].startswith("mc_ok_") for b in kb_flat),
   "є кнопка підтвердити")

# ── 5. Дедуп: повторний скан не дублює ───────────────────────────────────────
print("\n=== 5. дедуп ===")
n2 = MC.run(force=True)
ok(n2 == 0, "повторний скан подій не створив")
ok(len(_CAL) == 1, "у календарі так само 1 подія")

# ── 6. 🗑 лише перепитує попапом, видаляє тільки після «Так» ────────────────
print("\n=== 6. попап-підтвердження ===")
del_btn = [b for b in kb_flat if b["callback_data"].startswith("mc_del_")][0]
ask = MC.handle(del_btn["callback_data"])
ok(ask.get("alert") is True, "перше натискання дає випливаюче вікно")
ok("Прибрати з календаря?" in ask["text"], "вікно питає підтвердження")
ok("Рейс" in ask["text"] and "2026-09-12" in ask["text"],
   "у вікні назва й дата події")
ok(not _DELETED, "нічого НЕ видалено до підтвердження")
ask_kb = [b for row in (ask.get("keyboard") or []) for b in row]
ok(any(b["callback_data"].startswith("mc_yes_") for b in ask_kb), "є кнопка Так")
ok(any(b["callback_data"].startswith("mc_no_") for b in ask_kb), "є кнопка Ні")

yes_btn = [b for b in ask_kb if b["callback_data"].startswith("mc_yes_")][0]
res = MC.handle(yes_btn["callback_data"])
ok(res.get("alert") is True, "після «Так» теж випливаюче вікно")
ok("Прибрав з календаря" in res["text"], f"вікно підтверджує: {res['text'][:40]}")
ok(len(_DELETED) == 1, "подію видалено з календаря після «Так»")
recs = [r for r in (_FILES.get(MC.ITEMS_FILE) or {}).values()
        if isinstance(r, dict) and not r.get("empty")]
ok(recs and recs[0].get("state") == "deleted", "стан записано як deleted")
ok("Не знайшов" in MC.handle(yes_btn["callback_data"])["text"],
   "повторне «Так» не падає")

# ── 6b. «Ні» лишає подію ────────────────────────────────────────────────────
print("\n=== 6b. Ні, лишити ===")
_reset()
_FILES[MC.ITEMS_FILE] = {"k9": {"uid": "9", "title": "Візит", "date": "2026-09-09",
                                "time": "08:00", "kind": "appointment",
                                "event_id": "ev9", "state": "live"}}
pid = MC._store.put({"key": "k9"})
a = MC.handle(f"mc_del_{pid}")
ok(a.get("alert") is True, "вікно з питанням показано")
n_btn = [b for row in a["keyboard"] for b in row
         if b["callback_data"].startswith("mc_no_")][0]
r = MC.handle(n_btn["callback_data"])
ok("лишаю подію" in r["text"], "«Ні» лишає подію")
ok(not _DELETED, "з календаря нічого не зникло")
ok(_FILES[MC.ITEMS_FILE]["k9"]["state"] == "live", "стан лишився live")

# ── 7. Пошта недоступна → нічого не робимо ───────────────────────────────────
print("\n=== 7. пошта впала ===")
_reset()
MC._emails = lambda limit=8: None
ok(MC.run(force=True) == 0, "скан пропущено без вигадок")
ok(not _CAL and not _SENT, "ні календаря, ні повідомлень")

# ── 8. Битий вивід AI не ламає модуль ────────────────────────────────────────
print("\n=== 8. смітник від AI ===")
_reset()
MC._emails = lambda limit=8: [{"uid": "9", "sender": "x", "subject": "s", "body": "b"}]
MC._ask = lambda mails: ["рядок", {"uid": "9"}, {"uid": "9", "title": "Без дати"},
                         {"uid": "9", "title": "Минуле", "date": "2001-01-01"}]
ok(MC.run(force=True) == 0, "жодної події з битого виводу")
ok(not _CAL, "у календар нічого не пішло")

# ── 9. report() ──────────────────────────────────────────────────────────────
print("\n=== 9. report ===")
_reset()
ok("ще нічого не створював" in MC.report(), "порожній звіт говорить прямо")
_FILES[MC.ITEMS_FILE] = {"k1": {"uid": "1", "title": "Візит до лікаря",
                                "date": "2026-09-05", "time": "08:00",
                                "kind": "appointment", "state": "live"}}
r = MC.report()
ok("Візит до лікаря" in r and "2026-09-05" in r, "подія видна у звіті")

# ── 10. tidy: прибирає лише мертве ───────────────────────────────────────────
print("\n=== 10. tidy ===")
TD.K.load = _load
TD.K.save = _save
TD.K.remove_key = _remove_key
TD.K.send_card = _send_card
TD.K.now = lambda: _NOW
_reset()

_FILES["deadlines.json"] = {
    "d1": {"title": "Старе виконане", "deadline": "2026-05-01", "done": True},
    "d2": {"title": "Свіже виконане", "deadline": "2026-08-20", "done": True},
    "d3": {"title": "Живе", "deadline": "2026-09-30", "done": False},
}
_FILES["subs.json"] = {
    "s1": {"name": "Netflix", "cancelled": True, "last_seen": "2026-01-05",
           "confirmed": True},
    "s2": {"name": "Spotify", "active": True, "confirmed": True,
           "last_seen": "2026-08-20"},
}
_FILES["email_deadlines.json"] = [
    {"date": "2024-07-20", "title": "Баня", "reminded": False},
    {"date": "2026-09-10", "title": "Оплата", "reminded": False},
    {"date": "2026-01-10", "title": "Старе нагадане", "reminded": True},
]
_FILES["dates.json"] = {
    "b1": {"name": "Олі", "md": "09-01", "kind": "birthday", "year": "1990"},
    "w1": {"name": "Разова робоча", "md": "03-01", "kind": "work", "year": "2024"},
}
_FILES[MC.ITEMS_FILE] = {
    "old": {"title": "Минулий рейс", "date": "2026-01-01", "state": "live"},
    "new": {"title": "Майбутній рейс", "date": "2026-09-12", "state": "live"},
}

n = TD.run(force=True)
dl = _FILES["deadlines.json"]
ok("d1" not in dl, "старий виконаний дедлайн прибрано")
ok("d2" in dl, "свіжий виконаний лишився")
ok("d3" in dl, "живий дедлайн не чіпали")
ok("s1" not in _FILES["subs.json"], "мертву підписку прибрано")
ok("s2" in _FILES["subs.json"], "активну підписку не чіпали")
eds = _FILES["email_deadlines.json"]
titles = [x["title"] for x in eds]
ok("Оплата" in titles, "майбутній дедлайн з листа лишився")
ok("Баня" not in titles, "битий рік прибрано")
ok("Старе нагадане" not in titles, "нагадане й минуле прибрано")
ok("b1" in _FILES["dates.json"], "день народження НЕ прибрано")
ok("w1" not in _FILES["dates.json"], "разова минула дата прибрана")
ok("old" not in _FILES[MC.ITEMS_FILE], "минула подія з листа прибрана з реєстру")
ok("new" in _FILES[MC.ITEMS_FILE], "майбутня подія лишилась")
ok(n > 0 and _SENT, "звіт про прибирання надіслано")
ok("Прибрав" in _SENT[-1]["text"], "у звіті сказано, що саме прибрано")

# ── 11. tidy раз на добу ─────────────────────────────────────────────────────
print("\n=== 11. tidy не бігає двічі на день ===")
ok(TD.run(force=False) == 0, "повторний запуск того ж дня пропущено")

# ── 12. tidy на порожніх реєстрах молчить ────────────────────────────────────
print("\n=== 12. порожні реєстри ===")
_reset()
ok(TD.run(force=True) == 0, "прибирати нічого")
ok(not _SENT, "порожнього звіту не надсилаємо")

print("\n" + "=" * 50)
print(f"Падінь: {len(FAILS)}")
for f in FAILS:
    print(f"  ❌ {f}")
