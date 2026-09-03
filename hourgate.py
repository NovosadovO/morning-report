# -*- coding: utf-8 -*-
"""hourgate.py — один AI-блок на годину, не більше.

Навіщо: окремі AI-відправники (астро-алерти, здоров'я, теми, відкритість,
самодії) раніше стріляли кілька разів на годину кожен. Це і спам, і зайві
кредити Gemini. Тепер кожен із них має рівно ОДИН дозвіл на годинний слот.
Під воротами ЛИШЕ описові AI-блоки: здоров'я (hcoach) і теми/відкритість
(openmind). Нагадування, алерти, сповіщення та власні пропозиції AI під ворота
НЕ йдуть — вони спрацьовують тоді, коли є причина: подія, лист, рух ціни,
прострочене. Щогодинний звіт іде своїм шляхом і воротами не обмежений.
"""

import ai_kit as K

TAG = "hourgate"
FILE = "hourgate.json"


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


def _slot() -> str:
    return K.now().strftime("%Y-%m-%dT%H")


def allow(name: str, per_hour: int = 1) -> bool:
    """True — можна працювати цю годину. Далі до кінця години False."""
    name = str(name or "ai")[:40]
    slot = _slot()
    try:
        data = K.load(FILE, default={}) or {}
    except Exception:
        data = {}
    rec = data.get(name) or {}
    used = int(rec.get("used") or 0) if rec.get("slot") == slot else 0
    if used >= max(1, int(per_hour)):
        return False
    try:
        K.update_key(FILE, name, {"slot": slot, "used": used + 1})
    except Exception as e:
        _log("save error: " + str(e))
    return True


def used(name: str) -> int:
    try:
        rec = (K.load(FILE, default={}) or {}).get(str(name)[:40]) or {}
    except Exception:
        return 0
    return int(rec.get("used") or 0) if rec.get("slot") == _slot() else 0


def report() -> str:
    try:
        data = K.load(FILE, default={}) or {}
    except Exception:
        data = {}
    slot = _slot()
    out = ["⏱ <b>AI-блоки цієї години</b>", ""]
    live = [(k, v) for k, v in data.items()
            if isinstance(v, dict) and v.get("slot") == slot]
    if not live:
        out.append("Цієї години жодного окремого AI-блоку ще не було.")
    else:
        for k, v in sorted(live):
            out.append("• " + str(k) + " — використано " +
                       str(v.get("used")) + "/год")
    out.append("")
    out.append("Ліміт: один блок на годину кожному. Звіт іде окремо, щогодини.")
    return "\n".join(out)
