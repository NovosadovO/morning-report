#!/usr/bin/env python3
"""Офлайн-тести autokb.py — кнопки під питанням + дедуп дублів.

Перевіряє фікс від 20.09/скарги Олега:
1. Довгий текст / тег health/hcoach/pulse/report тепер ТЕЖ отримує кнопки-
   відповіді саме на питання в кінці (раніше падало в generic react-кнопки).
2. Те саме питання, перефразоване AI іншими словами, вдруге НЕ надсилається
   (fuzzy-дедуп через askme.answered_similar).
3. dedup_key (стабільна тема тригера) дає точний, а не fuzzy, збіг.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K

FAILS = []


def ok(cond, name):
    print(("✅ " if cond else "❌ ") + name, flush=True)
    if not cond:
        FAILS.append(name)


# ─── детермінований storage у пам'яті (як у tests_react.py) ─────────────────
STORE = {}


def _load(fn, default=None):
    return STORE.get(fn, default if default is not None else {})


def _save(fn, data):
    STORE[fn] = data
    return True


def _upd(fn, key, val):
    STORE.setdefault(fn, {})[key] = val
    return True


def _rem(fn, key):
    STORE.get(fn, {}).pop(key, None)
    return True


K.load = _load
K.save = _save
K.update_key = _upd
K.remove_key = _rem

import askme as A
import autokb as AK
import react as R

A._store = K.PayloadStore(A.STORE_FILE)
R._store = K.PayloadStore(R.STORE_FILE)

# ─── 1) Довгий health-текст з питанням у кінці → askme-кнопки, не generic ───
long_health_text = (
    "📊 Аналітика за сьогодні\n" + ("Дані по кроках і сну. " * 60) +
    "\nБачу, що сьогодні ти зробив бота для трейдингу — це серйозний прогрес. "
    "Чи вдалося досягти бажаних результатів, чи потрібна допомога?"
)
ok(len(AK._clean(long_health_text)) > 900, "текст справді довгий (>900 симв)")

rows1 = AK.build(long_health_text, tag="health_pulse")
labels1 = [b["text"] for row in (rows1 or []) for b in row]
ok(bool(rows1), "довгий health_pulse текст З ПИТАННЯМ отримав кнопки")
ok(any("Вдалося" in l or "допомога" in l.lower() for l in labels1),
   "кнопки відповідають САМЕ питанню про результат/допомогу: " + str(labels1))
ok(not any(l in ("👌 Прийняв", "🙈 Не нагадуй") for l in labels1),
   "це НЕ узагальнені generic-кнопки")

# ─── 2) Те саме питання, інші слова → have NOT been asked, then answered,
#        потім AI перефразовує — має розпізнатись як те саме (fuzzy) ────────
should1 = AK.should_send(long_health_text, tag="health_pulse")
ok(should1, "перший раз питання ще не задавали — можна надсилати")

# Симулюємо, що бот РЕАЛЬНО надіслав і отримав кнопки (buttons() записує в
# askme.json факт "запитано", handle() записує факт "відповів").
pid_key = None
for k, v in (STORE.get(A.FILE, {}) or {}).items():
    pid_key = k
ok(pid_key is not None, "питання збереглось у пам'яті askme")

# знайдемо pid у сторі payload'ів і симулюємо натискання кнопки "done"
store_data = STORE.get(A.STORE_FILE, {}) or {}
pid = None
for k, v in store_data.items():
    if v.get("key") == pid_key:
        pid = k
        break
ok(pid is not None, "payload питання знайдено в askme_store")
if pid:
    res = A.handle("am_done_" + pid)
    ok(res.get("text"), "натискання кнопки обробилось: " + str(res.get("text")))

# Перефразований AI варіант ТОГО Ж питання (інші слова, той самий сенс)
reworded = (
    "📊 Ще один звіт про сьогодні. " + ("Трохи іншого тексту зверху. " * 40) +
    "\nЧи вдалося досягти бажаних результатів у боті для трейдингу?"
)
should2 = AK.should_send(reworded, tag="health_pulse")
ok(should2 is False,
   "перефразоване AI те саме питання вдруге НЕ надсилається (fuzzy-дедуп)")

# ─── 3) dedup_key: стабільна тема тригера дає гарантований, не fuzzy, збіг ──
STORE.clear()
q_a = "Як спав минулої ночі — вистачило годин?"
q_b = "Скільки годин вдалося поспати вночі, чи вистачило для бадьорості?"
rows_a = AK.build(q_a, tag="micro_checkin", dedup_key="micro_checkin:сон")
ok(bool(rows_a), "перше формулювання (dedup_key) отримало кнопки")
labels_a = [b["text"] for row in rows_a for b in row]
pid2 = None
for k, v in (STORE.get(A.STORE_FILE, {}) or {}).items():
    if v.get("key") == AK._key_for(q_a, "micro_checkin:сон"):
        pid2 = k
if pid2:
    A.handle("am_yes_" + pid2)
should_b = AK.should_send(q_b, tag="micro_checkin", dedup_key="micro_checkin:сон")
ok(should_b is False,
   "інше формулювання ТІЄЇ САМОЇ теми (dedup_key) не надсилається повторно")

# ─── 4) Звичайне сповіщення без питання → react-кнопки як і раніше ──────────
STORE.clear()
plain = "💸 Прийшов рахунок за електрику на 45€, оплатити до 25.09."
rows4 = AK.build(plain, tag="vip_email")
labels4 = [b["text"] for row in (rows4 or []) for b in row]
ok(bool(rows4), "сповіщення без питання все ще отримує react-кнопки: " + str(labels4))

print()
if FAILS:
    print(str(len(FAILS)) + " ПРОВАЛЕНО: " + "; ".join(FAILS))
    sys.exit(1)
print("Усі тести autokb пройшли ✅")
