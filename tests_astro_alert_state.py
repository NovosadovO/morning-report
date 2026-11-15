"""Астро-алерт: перший запуск (нема стану) мовчить; повтор без змін мовчить."""
import ai_kit, astro
store = {}
ai_kit.load = lambda f, default=None: store.get(f, default)
ai_kit.save = lambda f, d: store.__setitem__(f, d)
fails = []
def chk(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)
chk(astro._alert_allowed("🌙 Місяць", "☀️ Сонце"), "Місяць–Сонце дозволено")
chk(astro._alert_allowed("♂ Марс", "🌙 Місяць"), "Марс–Місяць дозволено")
chk(astro._alert_allowed("🌙 Місяць", "Асцендент (ASC)"), "Місяць–ASC дозволено")
chk(not astro._alert_allowed("🌙 Місяць", "♃ Юпітер"), "Місяць–Юпітер глушимо")
chk(not astro._alert_allowed("☿ Меркурій", "🌙 Місяць"), "Меркурій–Місяць глушимо")
chk(not astro._alert_allowed("🌙 Місяць"), "вхід Місяця в знак/дім глушимо")
chk(astro._alert_allowed("♄ Сатурн"), "вхід Сатурна дозволено")
chk(astro._alert_allowed("♄ Сатурн", "♃ Юпітер"), "повільні планети дозволено")

if not astro._KERYKEION_OK:
    print("kerykeion відсутній — пропуск"); raise SystemExit(0)
import os
lf = os.path.join(os.path.dirname(os.path.abspath(astro.__file__)), "data", "astro_last_aspects.json")
if os.path.exists(lf): os.remove(lf)
a1 = astro.get_astro_alerts()
chk(a1 == [], "перший запуск без стану — нічого не шле")
chk("astro_last_aspects.json" in store, "стан збережено через ai_kit")
if os.path.exists(lf): os.remove(lf)   # як після редеплою: локального файлу нема
a2 = astro.get_astro_alerts()
chk(a2 == [], "після редеплою стан береться з data — алертів нема")
print("FAILS:", fails or "none")
