#!/usr/bin/env python3
"""
tests_notrunc.py — повідомлення не обриваються посеред слова.

Скарга Олега (скріншот 20.08): «…використовувати його максимально ефективно, ад»
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

FAILS = 0


def ok(cond, label):
    global FAILS
    print(("✅ " if cond else "❌ ") + label)
    if not cond:
        FAILS += 1


import notrunc as nt  # noqa: E402


def resp(text, reason="MAX_TOKENS"):
    return {"candidates": [{"finishReason": reason,
                            "content": {"parts": [{"text": text}]}}]}


print("1) Виявлення обриву")
ok(nt.truncated(resp("текст")), "MAX_TOKENS = обрив")
ok(not nt.truncated(resp("текст", "STOP")), "STOP = все гаразд")
ok(not nt.truncated({}), "порожня відповідь не ламає")

print("\n2) Реальний випадок зі скріншота")
real = ("Привіт Олеже! 👋\n\nТи зараз заслужено відпочиваєш, дозволяючи організму "
        "повністю відновитися після насиченого періоду. Ніч — це твій час для "
        "глибокого спокою, і важливо використовувати його максимально ефективно, ад")
fixed = nt.tidy(real)
ok(not fixed.endswith("ад"), "огризок «ад» прибрано")
ok(fixed.endswith("."), f"закінчується цілим реченням: ...{fixed[-40:]!r}")
ok("глибокого спокою" in fixed, "корисний текст збережено")

print("\n3) Різні форми обриву")
ok(nt.tidy("Все добре.") == "Все добре.", "цілий текст не чіпаємо")
ok(nt.tidy("Питання? Далі обірвалось на сло").endswith("?"),
   "ріжемо по знаку питання")
ok(nt.tidy("Готово!") == "Готово!", "знак оклику — теж кінець")
no_dot = nt.tidy("Без жодної крапки цей текст просто обірвався на сло")
ok(no_dot.endswith("[…]"), "без речень — чесна позначка обриву")
ok(not no_dot.endswith("сло […]"), f"слово не розрізане: {no_dot!r}")
ok(nt.tidy("Коротко, і обрив після коми,").count(",") == 1,
   "висяча кома прибрана")
ok(nt.tidy("") == "", "порожній рядок не ламає")

print("\n4) Огризок не лишається замість тексту")
long_tail = "Перше речення. " + "далі йде дуже довгий шматок без крапок " * 8 + "обр"
r = nt.tidy(long_tail)
ok(len(r) > len(long_tail) * 0.5,
   f"не викидаємо більшу частину тексту ({len(r)} з {len(long_tail)})")

print("\n5) Добір токенів (bump)")
body = json.dumps({"contents": [{"parts": [{"text": "розкажи"}]}],
                   "generationConfig": {"maxOutputTokens": 1400,
                                        "temperature": 0.9}}).encode()
nb, cap = nt.bump(body)
ok(cap == 2800, f"стеля подвоєна ({cap})")
ok(json.loads(nb.decode())["generationConfig"]["temperature"] == 0.9,
   "решта конфігу не зачеплена")
big = json.dumps({"contents": [{"parts": [{"text": "x"}]}],
                  "generationConfig": {"maxOutputTokens": 8000}}).encode()
ok(nt.bump(big) == (None, 0), "вище стелі CAP не піднімаємо")
free = json.dumps({"contents": [{"parts": [{"text": "x"}]}],
                   "generationConfig": {"maxOutputTokens": 1000,
                                        "thinkingConfig": {"thinkingBudget": 4000}}}).encode()
nb2, _ = nt.bump(free)
ok(json.loads(nb2.decode())["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 512,
   "думанню теж ставимо стелю (інакше з'їсть добрані токени)")

print("\n6) fix_response править текст на місці")
r6 = resp(real)
ok(nt.fix_response(r6), "щось виправлено")
ok(not nt.text_of(r6).endswith("ад"), "у відповіді вже нема огризка")
r7 = resp("Все ціле.", "STOP")
ok(not nt.fix_response(r7), "цілу відповідь не чіпаємо")

print("\n7) Під'єднано до _gem_post")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")).read()
seg = src.split("def _gem_post(")[1][:9000]
ok("import notrunc" in seg, "notrunc імпортується в _gem_post")
ok("_nt.bump(body_bytes)" in seg, "спершу добираємо токени")
ok("_nt.fix_response(_resp)" in seg, "потім чистимо обрив")
ok("_retried_cap = False" in seg, "добір лише один раз (без нескінченного циклу)")
ok(seg.index("_nt.truncated") < seg.index("ai_brain as _brain_n"),
   "чистка ДО того, як текст піде далі")

print("\n8) Стеля токенів піднята під українську")
mg = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "message_generator.py")).read()
ok("max_tokens = 3500" in mg, "довгі повідомлення: 3500 (було 1400)")
ok("max_tokens = 1800" in mg, "середні: 1800 (було 700)")
ok("max_tokens = 900" in mg, "короткі: 900 (було 350)")
ok('cut = block.rfind(" "' in mg, "чанкер ріже по пробілу, не по слову")

print("\n9) Чанкер не розриває слова")
import message_generator as MG  # noqa: E402
one = "слово " * 2000
parts = MG._chunk_text(one, limit=3800)
ok(len(parts) > 1, f"текст розбито ({len(parts)} частин)")
ok(all(not p.endswith("сло") for p in parts), "жодна частина не рветься по слову")
ok("".join(p.replace("\n", " ") for p in parts).replace("  ", " ").strip().count("слово") == 2000,
   "жодного слова не загублено")

print(f"\nfails: {FAILS}")
sys.exit(1 if FAILS else 0)
