#!/usr/bin/env python3
"""tests_variety.py — офлайн-тести варіативності повідомлень.

Перевіряємо саме те, на що скаржився Олег:
 • однакові повідомлення ловляться і перепитуються;
 • мертві шаблонні фрази ловляться;
 • блок варіативності реально потрапляє в промпт КОЖНОГО модуля (бо інжект
   стоїть у monitor._gem_post, через який ходять усі);
 • JSON-промпти НЕ чіпаються (інакше зламався б парсер);
 • temperature піднімається для живого тексту.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

fails = []


def ok(cond, name, extra=""):
    if cond:
        print("✅ " + name)
    else:
        print("❌ " + name + ((" — " + str(extra)) if extra else ""))
        fails.append(name)


import variety as V

# storage-заглушка: тести не мають ходити в GitHub
_MEM = {}


class _FakeStorage:
    @staticmethod
    def load(fn, default=None):
        return json.loads(json.dumps(_MEM.get(fn, default)))

    @staticmethod
    def save(fn, data):
        _MEM[fn] = json.loads(json.dumps(data))
        return True


sys.modules["storage"] = _FakeStorage
V._CACHE["ts"] = 0.0
V._CACHE["data"] = None

print("\n─── 1. відпечатки й схожість ───")
t1 = ("БТС сьогодні 64200 доларів, це мінус три відсотки за добу, обсяги теж просіли. "
      "Вага 83.4 кілограма, тримається другий тиждень. Пробіжки не було 19 днів, "
      "це видно по пульсі спокою. Найближча подія у календарі — нічна зміна.")
t2 = t1  # дослівний повтор
t3 = ("Ондо просів на пять відсотків, тримай на оці позицію. "
      "Через дві години нічна зміна, поспи зараз хоча б годину. "
      "Лист від Мароша чекає відповіді третій день.")
f1, f3 = V.fingerprint(t1), V.fingerprint(t3)
ok(len(f1) > 5, "fingerprint непорожній", len(f1))
ok(V.similarity(f1, V.fingerprint(t2)) > 0.9, "дослівний повтор → схожість >0.9")
ok(V.similarity(f1, f3) < 0.2, "різні тексти → схожість <0.2",
   V.similarity(f1, f3))
ok(V.fingerprint("два слова") == [], "надкороткий текст → пустий відпечаток")

print("\n─── 2. зачин ───")
ok(V.opening("Привіт, Олеже! Далі другий рядок.") == "Привіт, Олеже!",
   "opening бере перше речення", V.opening("Привіт, Олеже! Далі другий рядок."))
ok("<b>" not in V.opening("<b>Жирний</b> текст. Ще."), "opening чистить HTML")

print("\n─── 3. банліст мертвих фраз ───")
dead = "💰 ФІНАНСИ\nЗараз немає даних для аналізу.\n🏃 БІГ\nОстання пробіжка була 19 днів тому."
why = V.check("themes_ai", dead)
ok(bool(why), "мертва фраза з прода ловиться", why)
ok("немає даних для аналізу" in why, "причина називає саму фразу", why)
ok(V.check("themes_ai", "Бажаю продуктивного дня!"), "порожнє побажання ловиться")
ok(not V.check("themes_ai", t3), "жива конкретика проходить", V.check("themes_ai", t3))

print("\n─── 4. повтор проти історії ───")
_MEM.clear()
V._BUF.clear()
V._BUF_TS[0] = 0.0
V._CACHE["ts"] = 0.0
ok(V.note("report", t1, force=True), "note записав у storage")
ok("variety_log.json" in _MEM, "файл журналу створено", list(_MEM))
ok(len(_MEM["variety_log.json"].get("report") or []) == 1, "один запис на тег")
V._CACHE["ts"] = 0.0
why2 = V.check("report", t1)
ok(bool(why2), "той самий текст → повтор", why2)
ok(("повтор" in why2) or ("зачин" in why2), "причина каже про повтор/зачин", why2)
V._CACHE["ts"] = 0.0
ok(not V.check("report", t3), "інший текст на тому ж тегу → ок")
V._CACHE["ts"] = 0.0
ok(not V.check("astro_ai", "Коротко."), "надкороткий текст не перевіряємо на схожість")

print("\n─── 5. обмеження журналу ───")
_MEM.clear()
V._BUF.clear()
V._CACHE["ts"] = 0.0
for i in range(KEEP := V.KEEP_PER_TAG + 5):
    V.note("report", "Текст номер " + str(i) + " з унікальними словами " + ("абв" * i)
           + " і ще трохи наповнення щоб пройшов мінімум довжини журналу.",
           force=True)
V._CACHE["ts"] = 0.0
ok(len(_MEM["variety_log.json"]["report"]) == V.KEEP_PER_TAG,
   "тримаємо тільки останні KEEP_PER_TAG", len(_MEM["variety_log.json"]["report"]))

print("\n─── 6. блок у промпті ───")
b = V.block("report")
ok("КУТ ПОДАЧІ" in b, "блок містить кут подачі")
ok("ТОН" in b, "блок містить тон")
ok("ФОРМА" in b, "блок містить форму")
ok("НІКОЛИ не пиши цих мертвих фраз" in b, "блок містить банліст")
ok("нічого не вигадуй" in b, "блок вимагає не вигадувати (відповідність дійсності)")
ok("ТАК ТИ ВЖЕ ПОЧИНАВ" in b, "блок показує попередні зачини")

print("\n─── 7. ротація дає різні комбінації ───")
combos = set()
for t in ["report", "themes_ai", "astro_ai", "MSG_RWA", "email_ai", "day_mode",
          "weekly_coach", "dates", "subs", "rwa"]:
    combos.add((V._pick(V.ANGLES, t), V._pick(V.TONES, t + "t"),
                V._pick(V.SHAPES, t + "s")))
ok(len(combos) >= 7, "різні теги → різні комбінації кут/тон/форма", len(combos))

print("\n─── 8. inject у тіло запиту ───")


def body(prompt, temp=0.3):
    return json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 900, "temperature": temp},
    }).encode()


out = V.inject(body("Напиши Олегу живий звіт про день."), "report")
d = json.loads(out.decode())
p = d["contents"][0]["parts"][0]["text"]
ok(V.MARK in p, "маркер вставлено")
ok("КУТ ПОДАЧІ" in p, "блок варіативності в промпті")
ok(d["generationConfig"]["temperature"] >= V.TEMP_MIN,
   "temperature піднято для живого тексту", d["generationConfig"]["temperature"])
ok(d["generationConfig"]["temperature"] <= V.TEMP_MAX, "temperature не вище стелі")
again = V.inject(out, "report")
ok(again is out or json.loads(again.decode())["contents"][0]["parts"][0]["text"].count(V.MARK) == 1,
   "інжект ідемпотентний (немає подвійного блоку)")

jb = body("Проаналізуй листи. Поверни ТІЛЬКИ валідний JSON масив об'єктів.", 0.2)
jout = V.inject(jb, "email_parse")
ok(jout is jb, "JSON-промпт не чіпаємо")
ok(json.loads(jout.decode())["generationConfig"]["temperature"] == 0.2,
   "temperature JSON-промпту не змінено")

hi = V.inject(body("Живий текст", 1.05), "x")
ok(json.loads(hi.decode())["generationConfig"]["temperature"] >= 1.0,
   "високу temperature не знижуємо")

print("\n─── 9. escalate ───")
esc = V.escalate(out, "майже дослівний повтор попереднього")
ok(esc is not None, "escalate повернув нове тіло")
ep = json.loads(esc.decode())["contents"][0]["parts"][0]["text"]
ok("УВАГА: попередня твоя спроба" in ep, "жорсткіша вимога додана")
ok("майже дослівний повтор" in ep, "причина передана в промпт")
ok(json.loads(esc.decode())["generationConfig"]["temperature"] == V.TEMP_MAX,
   "temperature на максимум при перепиті")
ok(V.escalate(esc, "ще раз") is None, "двічі не ескалюємо (без нескінченних перепитів)")

print("\n─── 10. інтеграція в monitor._gem_post ───")
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")).read()
i_post = src.find("def _gem_post(")
i_end = src.find("\ndef ", i_post + 10)
seg = src[i_post:i_end]
ok("import variety as _var" in seg, "inject викликається всередині _gem_post")
ok("_var.inject(body_bytes, tag)" in seg, "inject отримує tag")
ok("_var2.check(tag" in seg, "check викликається на відповіді")
ok("_var2.escalate(" in seg, "escalate викликається при повторі")
ok("_retried_var = False" in seg, "прапорець перепиту оголошено")
ok(seg.count("_retried_var = True") == 1, "перепит рівно один раз")
ok("_var3.note(" in seg, "note фіксує відправлене")
ok(seg.find("_var.inject") < seg.find("_gr.inject") if "_gr.inject" in seg else True,
   "variety інжектиться до grounding")

print("\n─── 11. звіт ───")
_MEM.clear()
V._BUF.clear()
V._CACHE["ts"] = 0.0
r0 = V.report()
ok("порожній" in r0, "порожній журнал → зрозуміле повідомлення")
V.note("report", t1, force=True)
V._CACHE["ts"] = 0.0
r1 = V.report()
ok("РІЗНОМАНІТНІСТЬ" in r1 and "Записів у журналі: 1" in r1, "звіт показує статистику")

print("\n─── 12. стійкість до сміття ───")
ok(V.check("t", None) == "", "check(None) не падає")
ok(V.check("t", "") == "", "check('') не падає")
ok(V.inject(b"not json", "t") == b"not json", "inject на не-JSON тілі не падає")
ok(V.escalate(b"not json", "x") is None, "escalate на не-JSON тілі не падає")
ok(V.fingerprint(None) == [], "fingerprint(None) не падає")
ok(V.similarity(None, None) == 0.0, "similarity(None) не падає")
ok(not V.note("t", "коротко"), "надкороткий текст не пишеться в журнал")


print("\n─── 13. applies(): JSON-відповіді не перевіряємо ───")
jb2 = body("Проаналізуй лист. Поверни ТІЛЬКИ валідний JSON.", 0.2)
ok(not V.applies(jb2), "JSON-промпт (без MARK) -> applies=False")
lb = V.inject(body("Напиши живий коментар про день."), "report")
ok(V.applies(lb), "живий промпт (з MARK) -> applies=True")
ok(not V.applies(b"not json"), "сміття -> applies=False без падіння")
src2 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")).read()
i2 = src2.find("def _gem_post(")
seg2 = src2[i2:src2.find("\ndef ", i2 + 10)]
ok("_var2.applies(body_bytes)" in seg2, "monitor перевіряє applies перед check")
# те, що ловилось помилково: JSON action_detect із однаковим зачином
jresp = '{"action_type": "calendar", "action_title": "Оплатити страховку", "action_date": "2026-08-25"}'
_MEM.clear(); V._BUF.clear(); V._CACHE["ts"] = 0.0
V.note("action_detect", jresp, force=True)
V._CACHE["ts"] = 0.0
ok(bool(V.check("action_detect", jresp)), "сам check ще ловить JSON (тому й потрібен applies)")
ok(not V.applies(jb2), "але applies відсікає його до перевірки")

print("\n" + "=" * 40)
print("fails: " + str(len(fails)))
for f in fails:
    print("  ❌ " + f)


# ── БЛОК 14: базові правила поведінки (промт Олега 23.08) ────────────────────
_h("БЛОК 14: CORE — проактивність, незгода, без «Хочеш, я…»")
_b = V.block("personal_ai")
_ck("CORE у промпті: не погоджуватись автоматично", "НЕ погоджуйся автоматично" in _b)
_ck("CORE у промпті: думати на 1-2 кроки вперед", "1-2 кроки вперед" in _b)
_ck("CORE у промпті: проактивні пропозиції", "нагадування" in _b and "відстежувати" in _b)
_ck("CORE у промпті: заборона кінцівки «Хочеш, я…»", "Хочеш, я" in _b)
_ck("CORE у промпті: ротація ролі", "стратег" in _b)
_ck("ОБСЯГ ротується", "ОБСЯГ саме цього разу" in _b)
_ck("ФОКУС ротується", "ЗМІСТОВИЙ ФОКУС" in _b)
_ck("ЩІЛЬНІСТЬ є", "ЩІЛЬНІСТЬ" in _b)
_ck("LENGTHS/FOCUS непорожні", len(V.LENGTHS) >= 5 and len(V.FOCUS) >= 5)
_ck("кінцівка «Хочеш, я» ловиться як шаблон",
    "Хочеш" in V.check("t14", "Вага 83.4 кг, мінус 200 г. Хочеш, я поставлю нагадування?"))
_ck("кінцівка «Хочеш щоб я» ловиться",
    "Хочеш" in V.check("t14b", "БТС 61200, третій день падіння. Хочеш щоб я відстежував рівень 60к?"))
_ck("нормальний текст із конкретною дією проходить",
    V.check("t14c", "БТС 61200, третій день падіння. Ставлю алерт на 60к — скажу, коли пробє.") == "")
