"""21.11: здоров'я — тільки Garmin. Ручна вага ігнорується, вигадані числа ріжуться."""
import os, sys
os.environ.setdefault("TELEGRAM_TOKEN", "x"); os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage
FAILS = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: FAILS.append(m)

files = {
    "qwatch_data.json": {"2026-11-19": {"steps": 100}, "2026-11-20": {"weight_kg": 84.3},
                         "2026-11-17": {"weight_kg": 84.7}},
    "weight_data.json": {"2025-06-01": 85.0, "2026-11-18": 99.9, "2026-11-20": 70.0},
    "weight.json": {"2025-05-01": 86.0},
}
storage._load_github = lambda f: files.get(f)
w = storage.load_weight()
ok(w.get("2026-11-20") == 84.3, "Garmin виграє над ручним")
ok("2026-11-18" not in w, "ручний запис після старту Garmin ігнорується")
ok(w.get("2025-06-01") == 85.0 and w.get("2025-05-01") == 86.0, "архів до Garmin лишається")
ok(storage.load("weight_data.json") == w and storage.load("weight.json") == w, "storage.load(weight_*) = Garmin")
import weight
ok(weight.save_weight(80) is False, "weight.save_weight вимкнено")

import truth
prompt = "Вага 84.3 кг. Пульс у спокої 48 уд/хв. Сон 5 год 59 хв (359 хв). Кроків 7364. HRV 52 мс."
good = "Вага 84.3 кг, пульс 48 уд/хв. Сон 5 год 59 хв. Кроків 7364."
ok(truth.audit(prompt, good + " Тримай темп.") == [], "числа з Garmin проходять")
bad = "Вага 83 кг — молодець. Пульс 5 уд/хв у спокої. Ти виспався на 7:30, енергія 9 %."
iss = truth.audit(prompt, bad)
ok(any("числа" in i for i in iss), "вигадані виміряні числа (навіть <10 і з годиною) флагуються: %s" % iss)
txt, cut = truth.sanitize(prompt, bad + " Йди на пробіжку 8 км.")
ok("5 уд" not in txt and "9 %" not in txt and "83 кг" not in txt, "вигадане вирізано: " + txt)
ok("8 км" in txt, "план (км) не чіпаємо")
print("\nFAILS:", FAILS or "none"); sys.exit(1 if FAILS else 0)
