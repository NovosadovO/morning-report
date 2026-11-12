#!/usr/bin/env python3
"""
garmin_insight.py — AI-розбір Garmin з ГРАФІКОМ і питанням-кнопками.

Навіщо: здоров'я — головна тема, але повідомлення про неї були одноманітні
(сон/вага/кроки по колу). Тут тема дня обирається НЕ за шаблоном, а за
реальним відхиленням від ТВОЄЇ норми:

  1. Беремо реальні ряди з Garmin (storage.load_health): HRV, пульс спокою,
     стрес, SpO2, години сну, глибокий/REM сон, кроки.
  2. Для кожного — z-score останнього значення проти 14 попередніх днів.
  3. Береться метрика з найбільшим відхиленням, яку НЕ розбирали в останніх
     3 розборах (ротація). Нема відхилень ≥ порога → мовчимо (ніякого «все ок»).
  4. Малюється графік 21 день + смуга норми + остання точка.
  5. Gemini отримує ТІЛЬКИ ці числа, пише розбір (що це означає, найімовірніша
     причина з даних, 2 конкретні дії) і ОДНЕ питання з 2-4 варіантами відповіді,
     що відповідають саме питанню. Відповідь запам'ятовується в ai_brain.
  6. Не частіше 1 разу на 20 год, поважає тему health (ai_kit.topic_ok),
     тишу (/тиша) і mute hcoach/healthai. Нічого не робить сам — тільки кнопки.

Callback-префікс: gi_<pid>_<i> (відповідь), gi_skip_<pid>.
"""

import io
import os
import re
import json
import statistics
from datetime import timedelta

import ai_kit as K

TAG = "garmin_insight"
STATE_FILE = "garmin_insight_state.json"
STORE_FILE = "garmin_insight_store.json"
MIN_GAP_H = 20
BASE_DAYS = 14
MIN_BASE = 6
Z_MIN = 1.2                     # менше — це шум, мовчимо
RECENT_SKIP = 3                 # ту саму метрику не частіше ніж раз на 3 розбори

_store = K.PayloadStore(STORE_FILE)

# поле -> (назва, одиниця, «що вважається поганим»: +1 коли вище = гірше,
#          -1 коли нижче = гірше, 0 нейтрально)
METRICS = {
    "hrv":            ("HRV", "мс", -1),
    "hr_avg":         ("Пульс спокою", "уд/хв", +1),
    "stress":         ("Стрес", "", +1),
    "spo2":           ("SpO2", "%", -1),
    "sleep_hours":    ("Сон", "год", -1),
    "sleep_deep_min": ("Глибокий сон", "хв", -1),
    "sleep_rem_min":  ("REM-сон", "хв", -1),
    "steps":          ("Кроки", "", -1),
}
_PARTIAL_TODAY = ("steps", "stress")      # сьогодні ще неповні — беремо вчора


def _log(m):
    K.log(TAG, m)


def _health() -> dict:
    try:
        import storage
        d = storage.load_health() or {}
    except Exception as e:
        _log("load_health: " + str(e))
        return {}
    return {k: v for k, v in d.items()
            if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)) and isinstance(v, dict)}


def _val(rec: dict, field: str):
    v = rec.get(field)
    if v is None and field == "sleep_hours" and rec.get("sleep_total_min"):
        v = float(rec["sleep_total_min"]) / 60.0
    try:
        f = float(v)
    except Exception:
        return None
    return f if f > 0 else None


def series(health: dict, field: str, days: int = 21) -> dict:
    cutoff = (K.now().date() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = {}
    for d, rec in health.items():
        if d >= cutoff:
            v = _val(rec, field)
            if v is not None:
                out[d] = v
    return dict(sorted(out.items()))


def analyse(health: dict = None) -> list:
    """[{field,name,unit,last,last_day,base,std,z,worse,n}] відсортовано за |z|."""
    health = health if health is not None else _health()
    today = K.today_str()
    res = []
    for field, (name, unit, sign) in METRICS.items():
        s = series(health, field, BASE_DAYS + 7)
        if field in _PARTIAL_TODAY:
            s.pop(today, None)
        if len(s) < MIN_BASE + 1:
            continue
        days = list(s)
        last_day = days[-1]
        # свіжість: останнє значення не старше 2 днів
        try:
            age = (K.now().date() - K.now().date().fromisoformat(last_day)).days
        except Exception:
            continue
        if age > 2:
            continue
        base_vals = [s[d] for d in days[:-1]][-BASE_DAYS:]
        if len(base_vals) < MIN_BASE:
            continue
        base = statistics.mean(base_vals)
        std = statistics.pstdev(base_vals) or max(abs(base) * 0.05, 0.5)
        last = s[last_day]
        z = (last - base) / std
        res.append({"field": field, "name": name, "unit": unit,
                    "last": round(last, 2), "last_day": last_day,
                    "base": round(base, 2), "std": round(std, 2),
                    "z": round(z, 2), "n": len(base_vals),
                    "worse": (z * sign) > 0 if sign else False})
    res.sort(key=lambda r: -abs(r["z"]))
    return res


def _state() -> dict:
    st = K.load(STATE_FILE, default={}) or {}
    return st if isinstance(st, dict) else {}


def pick(rows: list, state: dict):
    recent = (state.get("recent") or [])[-RECENT_SKIP:]
    for r in rows:
        if abs(r["z"]) < Z_MIN:
            break
        if r["field"] in recent:
            continue
        return r
    return None


# ─── ГРАФІК ──────────────────────────────────────────────────────────────────

def chart(health: dict, row: dict) -> bytes:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime
    except Exception as e:
        _log("matplotlib: " + str(e))
        return b""
    s = series(health, row["field"], 21)
    if len(s) < 5:
        return b""
    xs = [datetime.strptime(d, "%Y-%m-%d") for d in s]
    ys = list(s.values())
    bad = "#F85149" if row["worse"] else "#3FB950"
    fig, ax = plt.subplots(figsize=(8, 4), dpi=110)
    fig.patch.set_facecolor("#0D1117")
    ax.set_facecolor("#161B22")
    ax.axhspan(row["base"] - row["std"], row["base"] + row["std"],
               color="#58A6FF", alpha=0.15, label="твоя норма (±1σ)")
    ax.axhline(row["base"], color="#58A6FF", lw=1, ls="--")
    ax.plot(xs, ys, color="#8B949E", lw=1.6, marker="o", ms=4)
    ax.plot(xs[-1:], ys[-1:], marker="o", ms=11, color=bad, zorder=5)
    ax.annotate(f"{row['last']:g}", (xs[-1], ys[-1]), textcoords="offset points",
                xytext=(0, 12), ha="center", color=bad, fontsize=11,
                fontweight="bold")
    unit = (" · " + row["unit"]) if row["unit"] else ""
    ax.set_title(f"{row['name']}{unit} — 21 день", color="#E6EDF3", fontsize=13,
                 loc="left")
    ax.tick_params(colors="#8B949E")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    for sp in ax.spines.values():
        sp.set_color("#30363D")
    ax.grid(color="#30363D", lw=0.5, alpha=0.6)
    ax.legend(facecolor="#161B22", edgecolor="#30363D", labelcolor="#8B949E",
              loc="upper left", fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _send_photo(png: bytes, caption: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat and png):
        return False
    try:
        try:
            import requests as _rq
        except ImportError:
            import httpreq as _rq
        r = _rq.post("https://api.telegram.org/bot" + token + "/sendPhoto",
                     data={"chat_id": chat, "caption": caption[:1000],
                           "parse_mode": "HTML"},
                     files={"photo": ("garmin.png", png, "image/png")},
                     timeout=60)
        return getattr(r, "status_code", 0) == 200
    except Exception as e:
        _log("sendPhoto: " + str(e))
        return False


# ─── AI ──────────────────────────────────────────────────────────────────────

def _context_facts(health: dict, row: dict) -> str:
    """Реальні сусідні числа за останні 7 днів — AI шукає причину ТІЛЬКИ в них."""
    lines = []
    for f, (name, unit, _s) in METRICS.items():
        if f == row["field"]:
            continue
        s = series(health, f, 7)
        if s:
            lines.append(f"{name}: " + ", ".join(f"{d[5:]}={v:g}" for d, v in s.items()))
    return "\n".join(lines)


_PROMPT = """Ти особистий асистент Олега (Кошице, нічні/ранні зміни, ціль — 75 кг,
біг, фінансова незалежність). Нижче РЕАЛЬНІ дані з його годинника Garmin.

ГОЛОВНА МЕТРИКА: {name} ({unit})
Остання ({day}): {last}. Його норма за {n} попередніх днів: {base} (±{std}).
Відхилення: {z:+.1f}σ — {direction}.
Ряд за 21 день: {series}

СУСІДНІ ПОКАЗНИКИ (7 днів), де можна шукати причину:
{ctx}

Завдання (українською, без вступу «Привіт», без кліше, одразу до суті):
1. "text": 3-5 коротких речень HTML (<b> для 1-2 ключових чисел). Що це означає
   для самопочуття/відновлення, найімовірніша причина ТІЛЬКИ з даних вище
   (якщо причини в даних нема — так і скажи), і 2 конкретні дії на сьогодні/завтра.
2. "question": одне коротке питання до Олега, яке допоможе уточнити причину
   (напр. про нічну зміну, каву, алкоголь, тренування, хворобу — що доречно).
3. "options": 2-4 варіанти відповіді, які ПРЯМО відповідають на це питання
   (не «Так/Ні», якщо питання не закрите; кожен до 28 символів; це лише запис відповіді — не обіцяй, що бот щось нагадає чи поставить).
НЕ вигадуй чисел, яких немає вище. Не пиши про кеш/застарілі дані.
Формат — ТІЛЬКИ JSON-об'єкт: {{"text":"...","question":"...","options":["..",".."]}}"""


def build(row: dict, health: dict) -> dict:
    s = series(health, row["field"], 21)
    prompt = _PROMPT.format(
        name=row["name"], unit=row["unit"] or "—", day=row["last_day"],
        last=row["last"], n=row["n"], base=row["base"], std=row["std"],
        z=row["z"], direction=("гірше за норму" if row["worse"] else "краще за норму"),
        series=", ".join(f"{d[5:]}={v:g}" for d, v in s.items()),
        ctx=_context_facts(health, row) or "—")
    data = K.gemini_json(prompt, max_tokens=900, temperature=0.85,
                         tag="garmin_insight", want="dict")
    if not isinstance(data, dict) or not data.get("text"):
        return {}
    opts = [str(o).strip()[:28] for o in (data.get("options") or []) if str(o).strip()][:4]
    return {"text": str(data["text"]).strip(), "question": str(data.get("question") or "").strip(),
            "options": opts}


def run(force: bool = False) -> bool:
    try:
        import quiet
        if not force and quiet.blocked("msg"):
            return False
    except Exception:
        pass
    if not force:
        try:
            import dismissed
            if dismissed.is_muted("hcoach") or dismissed.is_muted("healthai"):
                return False
        except Exception:
            pass
        if not K.rate_ok("garmin_insight_gap.json", MIN_GAP_H * 60):
            return False
        if not K.topic_ok("health", 4):
            return False
    health = _health()
    rows = analyse(health)
    state = _state()
    row = pick(rows, state)
    if not row:
        _log("відхилень ≥ порога нема (або вже розбирали) — мовчу")
        return False
    K.rate_mark("garmin_insight_gap.json")

    built = build(row, health)
    if not built:
        _log("AI не склав розбір")
        return False
    png = chart(health, row)

    arrow = "🔻" if row["worse"] else "🔺"
    head = f"{arrow} <b>{K.esc(row['name'])}: {row['last']:g}{(' ' + row['unit']) if row['unit'] else ''}</b> " \
           f"(норма {row['base']:g}, {row['z']:+.1f}σ)"
    caption = head + "\n\n" + built["text"]
    pid = _store.put({"field": row["field"], "name": row["name"],
                      "q": built["question"], "opts": built["options"],
                      "last": row["last"], "day": row["last_day"]})
    kb = None
    if built["question"] and built["options"]:
        kb = [[{"text": o, "callback_data": f"gi_{pid}_{i}"}]
              for i, o in enumerate(built["options"])]
        kb.append([{"text": "🙈 Не питай про це", "callback_data": f"gi_skip_{pid}"}])

    if png and len(caption) <= 1000 and not kb:
        ok = _send_photo(png, caption)
    elif png:
        ok = _send_photo(png, head)
        if ok:
            txt = built["text"] + (("\n\n❓ <b>" + K.esc(built["question"]) + "</b>")
                                   if built["question"] else "")
            ok = K.send_card(txt, kb, tag=TAG)
    else:
        txt = caption + (("\n\n❓ <b>" + K.esc(built["question"]) + "</b>")
                         if built["question"] else "")
        ok = K.send_card(txt, kb, tag=TAG)
    if not ok:
        _store.drop(pid)
        return False
    K.topic_mark("health")
    state["recent"] = ((state.get("recent") or []) + [row["field"]])[-6:]
    state["last"] = {"field": row["field"], "day": K.today_str(), "z": row["z"]}
    K.save(STATE_FILE, state)
    _log(f"✅ розбір {row['field']} z={row['z']}")
    return True


def handle(data: str) -> dict:
    """gi_<pid>_<i> | gi_skip_<pid>  →  {'ok','text'}"""
    m = re.match(r"^gi_skip_(\w+)$", data)
    if m:
        p = _store.get(m.group(1)) or {}
        _store.drop(m.group(1))
        if p.get("field"):
            try:
                import dismissed
                dismissed.mute("garmin_insight", key=p["field"], title=p.get("name"))
            except Exception:
                pass
            st = _state()
            st["recent"] = ((st.get("recent") or []) + [p["field"]] * RECENT_SKIP)[-9:]
            K.save(STATE_FILE, st)
        return {"ok": True, "text": "Ок, цю метрику не чіпаю кілька розборів."}
    m = re.match(r"^gi_(\w+)_(\d)$", data)
    if not m:
        return {"ok": False, "text": "Невідома дія"}
    p = _store.get(m.group(1))
    if not p:
        return {"ok": False, "text": "Це питання вже закрите."}
    i = int(m.group(2))
    opts = p.get("opts") or []
    if i >= len(opts):
        return {"ok": False, "text": "Немає такого варіанта."}
    try:
        import ai_brain
        ai_brain.remember_answer(
            f"{p.get('name')} {p.get('last')} ({p.get('day')}): {p.get('q')}",
            opts[i], topic="health", source="button")
    except Exception as e:
        _log("remember: " + str(e))
    _store.drop(m.group(1))
    return {"ok": True, "text": f"Записав: {opts[i]}"}


if __name__ == "__main__":
    import sys
    if "--dry" in sys.argv:
        h = _health()
        for r in analyse(h):
            print(r)
        print("pick:", pick(analyse(h), _state()))
    else:
        print("надіслано:", run(force=True))
