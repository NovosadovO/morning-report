"""Офлайн-тест traffic_kosice: диф проти попереднього знімка — нові
інциденти показуються окремо, старі (довготривалі) згортаються в підсумок
'без змін', а не повторюються щоразу однаково."""
import os, sys, time

fails = []


def chk(cond, name):
    if not cond:
        fails.append(name)
        print(f"❌ {name}")
    else:
        print(f"✅ {name}")


_MEM = {}


class FakeStorage:
    @staticmethod
    def load(fn, default=None):
        return _MEM.get(fn, default)

    @staticmethod
    def save(fn, data):
        _MEM[fn] = data


sys.modules["storage"] = FakeStorage

import traffic_kosice as tk

INC_A = {"properties": {"iconCategory": 8, "magnitudeOfDelay": 4,
                          "events": [{"description": "Zatvorené"}],
                          "from": "Ipeľská (Popradská)", "to": "", "delay": 0}}
INC_B = {"properties": {"iconCategory": 8, "magnitudeOfDelay": 4,
                          "events": [{"description": "Zatvorené"}],
                          "from": "Trieda SNP (Popradská)", "to": "", "delay": 0}}
INC_C_NEW = {"properties": {"iconCategory": 1, "magnitudeOfDelay": 3,
                              "events": [{"description": "Nehoda"}],
                              "from": "Rooseveltova", "to": "", "delay": 300}}

# ── RUN 1: порожній baseline -> все нове ──
tk.get_incidents = lambda: [INC_A, INC_B]
r1 = tk.format_traffic_report()
chk("🆕 Нові" in r1, "перший запуск: всі інциденти позначені як нові")
chk("Ipeľská" in r1 and "Trieda SNP" in r1, "перший запуск показує обидва інциденти")

# ── RUN 2: та сама ситуація, baseline ще свіжий (в межах TTL) -> "без змін" ──
r2 = tk.format_traffic_report()
chk("🆕 Нові" not in r2, "другий запуск (без нових): немає розділу 'Нові'")
chk("без змін" in r2, "другий запуск: старі інциденти згорнуті в 'без змін'")
chk(r1 != r2, "звіт відрізняється між викликами замість повторення того самого")

# ── RUN 3: baseline застарів (TTL вийшов) + з'явився новий інцидент ──
state = _MEM.get("traffic_state.json", {})
state["ts"] = time.time() - 3600  # штучно "постарішав" знімок на годину
_MEM["traffic_state.json"] = state

tk.get_incidents = lambda: [INC_A, INC_B, INC_C_NEW]
r3 = tk.format_traffic_report()
chk("Rooseveltova" in r3, "після оновлення знімка новий інцидент показаний")
chk("🆕 Нові" in r3, "новий інцидент позначений як 'Нові'")

print()
if fails:
    print(f"ПРОВАЛЕНО: {len(fails)} — {fails}")
    sys.exit(1)
else:
    print("Усі тести пройшли ✅")
