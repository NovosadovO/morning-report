"""Перевірка merge-on-conflict у storage._save_github.

Сценарій справжнього бага: два потоки пишуть той самий файл.
Потік A читає {a:1}, потік B встигає записати {a:1, b:2},
потім A робить PUT -> 409. Раніше A перезаписував файл своїм {a:1, c:3}
і ключ b:2 ЗНИКАВ. Тепер має бути {a:1, b:2, c:3}.
"""
import base64
import json
import os
import sys

sys.path.insert(0, "/home/user/bot")
os.environ.setdefault("GITHUB_TOKEN", "fake")
import storage as S

FAIL = []


def ck(cond, name):
    print(("✅ " if cond else "❌ ") + name)
    if not cond:
        FAIL.append(name)


# ── фейковий "GitHub" ────────────────────────────────────────────────────────
REMOTE = {"content": {"a": 1, "b": 2}, "sha": "sha1"}
PUTS = []
STATE = {"conflicts": 1}


def fake_gh_get(path, tries=3):
    enc = base64.b64encode(
        json.dumps(REMOTE["content"], ensure_ascii=False).encode()).decode()
    return "ok", {"content": enc, "sha": REMOTE["sha"]}


def fake_gh_request(method, path, body=None):
    if method != "PUT":
        return None
    if STATE["conflicts"] > 0:          # перший PUT -> 409 conflict
        STATE["conflicts"] -= 1
        print("GitHub PUT (fake) 409 conflict")
        return None
    got = json.loads(base64.b64decode(body["content"]).decode())
    PUTS.append(got)
    REMOTE["content"] = got
    return {"content": {"sha": "sha2"}}


S._gh_get = fake_gh_get
S._gh_request = fake_gh_request
S._save_local = lambda *a, **k: None
S.time.sleep = lambda *a, **k: None

print("=== merge-on-conflict ===")
ok = S._save_github("t.json", {"a": 1, "c": 3})
ck(ok is True, "save повернув True після retry")
ck(len(PUTS) == 1, "успішний PUT був один")
saved = PUTS[0] if PUTS else {}
ck(saved.get("b") == 2, "чужий ключ b=2 НЕ втрачено (був би баг)")
ck(saved.get("c") == 3, "свій ключ c=3 записано")
ck(saved.get("a") == 1, "спільний ключ a=1 на місці")

print("\n=== без конфлікту merge не втручається ===")
PUTS.clear()
STATE["conflicts"] = 0
REMOTE["content"] = {"x": 9}
S._save_github("t2.json", {"only": "mine"})
ck(PUTS and PUTS[0] == {"only": "mine"}, "перша спроба пише рівно наші дані")

print("\n=== конфлікт для не-dict (список) не ламає збереження ===")
PUTS.clear()
STATE["conflicts"] = 1
S._save_github("t3.json", [1, 2, 3])
ck(PUTS and PUTS[0] == [1, 2, 3], "список збережено як є")

print("\nПОМИЛОК:", len(FAIL))
