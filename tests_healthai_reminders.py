"""
Офлайн-тест нових функцій healthai.py (запит Олега 21.09.2026):
  1. data_reminder() — нагадування "надішли дані", якщо мовчав довше
     REMINDER_STALE_HOURS, у активний час, не частіше REMINDER_GAP_HOURS.
  2. _health_emails()/health_mail_check() — фільтр листів про здоров'я
     (лікар/аптека/страхова/аналізи) і сповіщення про новий, з дедупом.

Усе мокається (K.load/K.save/K.send_card/monitor.get_emails) — жодних
реальних мереж/токенів не потрібно.
"""

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


import healthai as H  # noqa: E402
import ai_kit as K  # noqa: E402


# ─── helpers для мокання ─────────────────────────────────────────────────────

class FakeStore:
    def __init__(self):
        self.data = {}

    def load(self, name, default=None):
        return self.data.get(name, default)

    def save(self, name, val):
        self.data[name] = val


_store = FakeStore()
_sent_cards = []
_fixed_now = [datetime(2026, 9, 21, 15, 0, 0)]  # активний час за замовч.


def _fake_update_key(name, key, val):
    d = _store.data.get(name) or {}
    d[key] = val
    _store.data[name] = d


def _patch_common(monkeypatch=None):
    H._now = lambda: _fixed_now[0]
    H.K.load = lambda name, default=None: _store.load(name, default)
    H.K.save = lambda name, val: _store.save(name, val)
    H.K.update_key = _fake_update_key
    H.K.send_card = lambda text, kb=None, tag=None: (_sent_cards.append(text) or True)
    H._muted = lambda: False


_patch_common()


# ─── 1. data_reminder ───────────────────────────────────────────────────────

print("\n1. data_reminder() — мовчав менше REMINDER_STALE_HOURS → тиша")
_store.data = {}
_sent_cards.clear()
H.load_journal = lambda: [{"ts": (_fixed_now[0] - timedelta(hours=2)).isoformat()}]
check("не спрацьовує (2 год < 5)", H.data_reminder() is False)
check("картку не надіслано", len(_sent_cards) == 0)

print("\n2. data_reminder() — мовчав 6 год у активний час → нагадує")
_store.data = {}
_sent_cards.clear()
H.load_journal = lambda: [{"ts": (_fixed_now[0] - timedelta(hours=6)).isoformat()}]
check("спрацьовує", H.data_reminder() is True)
check("картку надіслано", len(_sent_cards) == 1)
check("текст згадує години", "6" in _sent_cards[0])

print("\n3. data_reminder() — дедуп: повторно не частіше REMINDER_GAP_HOURS")
check("повторний виклик одразу — тиша", H.data_reminder() is False)

print("\n4. data_reminder() — поза активним часом (03:00) → тиша навіть якщо давно мовчав")
_store.data = {}
_sent_cards.clear()
_fixed_now[0] = datetime(2026, 9, 21, 3, 0, 0)
H.load_journal = lambda: [{"ts": (_fixed_now[0] - timedelta(hours=10)).isoformat()}]
check("не спрацьовує вночі", H.data_reminder() is False)
_fixed_now[0] = datetime(2026, 9, 21, 15, 0, 0)

print("\n5. data_reminder() — жодного запису взагалі (порожній журнал) → все одно нагадує")
_store.data = {}
_sent_cards.clear()
H.load_journal = lambda: []
check("порожній журнал теж триггерить", H.data_reminder() is True)


# ─── 2. листи про здоров'я ───────────────────────────────────────────────────

print("\n6. _health_emails() — фільтрує за темою/відправником")
fake_monitor = types.ModuleType("monitor")
_EMAILS = [
    {"uid": "1", "sender": "clinic@lekar.sk", "subject": "Результати аналізів крові"},
    {"uid": "2", "sender": "shop@aliexpress.com", "subject": "-50% на все!"},
    {"uid": "3", "sender": "poistovna@dovera.sk", "subject": "Vaša poistná zmluva"},
    {"uid": "4", "sender": "boss@work.com", "subject": "Зустріч у понеділок"},
]
fake_monitor.get_emails = lambda: _EMAILS
sys.modules["monitor"] = fake_monitor

hits = H._health_emails()
uids = {h["uid"] for h in hits}
check("лист про аналізи знайдено", "1" in uids, uids)
check("реклама не потрапила", "2" not in uids, uids)
check("страхова (poistovna) знайдена", "3" in uids, uids)
check("робочий лист не потрапив", "4" not in uids, uids)

print("\n7. health_mail_check() — сповіщає про новий, дедуп за uid")
_store.data = {}
_sent_cards.clear()
H._mail_dedup = None
H.MAIL_DEDUP_FILE = "healthai_mail_sent_test.json"
n = H.health_mail_check()
check("надіслано рівно 2 (аналізи + страхова)", n == 2, n)
check("картки надіслано", len(_sent_cards) == 2)

n2 = H.health_mail_check()
check("повторний скан тих самих листів — 0 (дедуп)", n2 == 0, n2)


if FAILS:
    print(f"\n❌ FAILS: {FAILS}")
    raise SystemExit(1)
print("\nВСЕ ОК")
