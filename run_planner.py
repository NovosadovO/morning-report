#!/usr/bin/env python3
"""
ТРЕНУВАЛЬНИЙ ПЛАН ПІД ЗМІНИ  (Здоров'я/вага #2)

AI сам дивиться:
  📅 графік змін на 7 днів вперед (Google Calendar: «Рання зміна» / «Нічна зміна»)
  🏃 Strava — скільки бігав останні 2 тижні, темп, кілометраж
  ⚖️ вага (health/weight) — прогрес до цілі 78 кг

і САМ ставить пробіжки у вільні вікна між зміною і сном.

ЖОРСТКІ ПРАВИЛА ВІКОН (не порушуються навіть якщо AI попросить):
  • рання зміна 06:00–18:00 → біг о 19:00 (після душу, до сну)
  • нічна зміна 18:00–06:00 → біг о 15:00 (виспався, до виходу о 17:30);
    короткий, не важкий
  • ранок ПІСЛЯ нічної (06:00–14:00) → СОН, пробіжок не ставимо
  • вільний день → біг о 09:30
  • два дні підряд не ставимо важке навантаження

Кнопки: [📅 Поставити всі] [1️⃣ Тільки першу] [❌ Не треба]
Callback-префікси: rp_all_ / rp_one_ / rp_skip_
"""

import re
from datetime import datetime, timedelta

import ai_kit as K

TAG = "run_planner"

STORE_FILE = "run_plan_store.json"
SENT_FILE = "run_plan_sent.json"
STATE_FILE = "run_plan_state.json"

PLAN_MIN_GAP_MIN = 60 * 20     # не частіше ніж раз на ~20 годин
TARGET_RUNS_PER_WEEK = 3

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=3)

UA_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


# ─── ВІКНА ───────────────────────────────────────────────────────────────────

def _windows(days: int = 7) -> list:
    """Список {date, weekday, shift, slot, kind, why} — де фізично можливо бігти."""
    shifts = K.shift_map(days + 1)
    dates = sorted(shifts.keys())
    out = []
    for i, d in enumerate(dates[:days]):
        sh = shifts[d]
        prev = shifts.get(dates[i - 1]) if i > 0 else None
        dt = datetime.strptime(d, "%Y-%m-%d")
        wd = UA_DAYS[dt.weekday()]

        if sh == "early":
            out.append({"date": d, "weekday": wd, "shift": "рання 06-18",
                        "slot": "19:00", "dur_min": 50,
                        "why": "після зміни, є 2-3 години до сну"})
        elif sh == "night":
            out.append({"date": d, "weekday": wd, "shift": "нічна 18-06",
                        "slot": "15:00", "dur_min": 35,
                        "why": "виспався після ночі, до виходу о 17:30 — тільки легкий біг"})
        else:
            if prev == "night":
                # ранок = відсипається після нічної
                out.append({"date": d, "weekday": wd, "shift": "вільний (після нічної)",
                            "slot": "17:00", "dur_min": 40,
                            "why": "зранку сон після нічної — біг ближче до вечора"})
            else:
                out.append({"date": d, "weekday": wd, "shift": "вільний",
                            "slot": "09:30", "dur_min": 60,
                            "why": "вільний день — можна довгий біг"})
    return out


# ─── КОНТЕКСТ БІГУ ───────────────────────────────────────────────────────────

def _run_context() -> dict:
    ctx = {"ok": False, "text": "Strava недоступна", "runs_7d": 0, "km_7d": 0.0}
    try:
        import strava
        runs = strava.get_runs(days=14)
    except Exception as e:
        K.log(TAG, f"strava error: {e}")
        return ctx
    if not runs:
        ctx["text"] = "за 2 тижні пробіжок немає"
        ctx["ok"] = True
        return ctx
    now = datetime.now()
    last7 = [r for r in runs if (now - r["date"]).days < 7]
    prev7 = [r for r in runs if 7 <= (now - r["date"]).days < 14]
    km7 = round(sum(r["dist_km"] for r in last7), 1)
    kmp = round(sum(r["dist_km"] for r in prev7), 1)
    lines = [f"останні 7 днів: {len(last7)} пробіжок, {km7} км",
             f"попередні 7 днів: {len(prev7)} пробіжок, {kmp} км"]
    for r in runs[-4:]:
        lines.append(f"  {r['date_str']}: {r['dist_km']} км, темп {r['pace_str']}/км, "
                     f"{r['dur_min']} хв")
    ctx.update({"ok": True, "text": "\n".join(lines),
                "runs_7d": len(last7), "km_7d": km7})
    return ctx


def _weight_context() -> str:
    try:
        w = K.load("weight.json", default={}) or {}
        if isinstance(w, dict) and w:
            keys = sorted(k for k in w.keys() if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)))
            if keys:
                last = w[keys[-1]]
                val = last.get("weight") if isinstance(last, dict) else last
                return f"вага {val} кг (ціль 78 кг), останнє зважування {keys[-1]}"
    except Exception:
        pass
    try:
        h = K.load("health.json", default={}) or {}
        keys = sorted(h.keys())
        if keys:
            last = h[keys[-1]] or {}
            if last.get("weight"):
                return f"вага {last['weight']} кг (ціль 78 кг), дата {keys[-1]}"
    except Exception:
        pass
    return "вага невідома (ціль 78 кг)"


_PROMPT = """Ти — тренер Олега з бігу. Він працює в Minebea Mitsumi змінами
(рання 06:00-18:00 / нічна 18:00-06:00), живе в Кошице, хоче схуднути до 78 кг.

ЗАРАЗ: {now}

🏃 ЙОГО РЕАЛЬНІ ДАНІ ЗІ STRAVA:
{strava}

⚖️ {weight}

📅 ДОСТУПНІ ВІКНА (вибирай ТІЛЬКИ з цього списку, час змінювати НЕ можна):
{windows}

Завдання: вибери {target} тренування на найближчі 7 днів.
ПРАВИЛА:
1. Використовуй тільки date+slot зі списку вікон, дослівно.
2. Не два важких тренування підряд. Після нічної зміни — тільки легкий біг.
3. Прогресія обережна: тижневий кілометраж не більше +10% до попереднього.
4. type: "легкий" | "темповий" | "інтервали" | "довгий"
5. dist_km — реальне число (наприклад 6.5), під його поточну форму.
6. why — 1 живе речення українською, чому саме це і саме в цей день.

Формат — ТІЛЬКИ JSON-масив без markdown:
[{{"date":"2026-08-05","time":"19:00","type":"легкий","dist_km":6,
   "why":"Після ранньої зміни — спокійний біг, щоб втягнутись у тиждень."}}]"""


# ─── ПЛАН ────────────────────────────────────────────────────────────────────

def _fallback_plan(windows, target) -> list:
    """Без AI: беремо найкращі вікна (вільні дні > рання > нічна), чергуючи дні."""
    prio = {"вільний": 0, "вільний (після нічної)": 1, "рання 06-18": 2, "нічна 18-06": 3}
    cand = sorted(windows, key=lambda w: (prio.get(w["shift"], 9), w["date"]))
    plan, used = [], []
    for w in cand:
        d = datetime.strptime(w["date"], "%Y-%m-%d").date()
        if any(abs((d - u).days) < 2 for u in used):
            continue
        hard = w["shift"] != "нічна 18-06"
        plan.append({"date": w["date"], "time": w["slot"],
                     "type": "довгий" if hard and len(plan) == 0 else "легкий",
                     "dist_km": 8 if hard and len(plan) == 0 else 6,
                     "why": w["why"]})
        used.append(d)
        if len(plan) >= target:
            break
    return sorted(plan, key=lambda x: x["date"])


def build_plan() -> dict:
    windows = _windows(7)
    if not windows:
        return {"ok": False, "error": "calendar_unavailable"}
    rc = _run_context()
    wtxt = _weight_context()

    # Цільова кількість: якщо цього тижня вже бігав — не перевантажуємо
    target = max(2, TARGET_RUNS_PER_WEEK - min(rc["runs_7d"], 1))

    wlines = "\n".join(
        f"- {w['date']} ({w['weekday']}) | зміна: {w['shift']} | час: {w['slot']} "
        f"| до {w['dur_min']} хв | {w['why']}" for w in windows)

    plan = []
    if rc["ok"]:
        prompt = _PROMPT.format(now=K.now().strftime("%Y-%m-%d %H:%M (%A)"),
                                strava=rc["text"], weight=wtxt,
                                windows=wlines, target=target)
        plan = K.gemini_json(prompt, max_tokens=1200, temperature=0.5, tag=TAG)

    # Валідація: тільки дозволені (date, time) пари
    allowed = {(w["date"], w["slot"]): w for w in windows}
    clean = []
    for p in plan or []:
        if not isinstance(p, dict):
            continue
        key = (str(p.get("date", "")), str(p.get("time", "")))
        if key not in allowed:
            K.log(TAG, f"AI дав недозволене вікно {key} — відкидаю")
            continue
        try:
            dist = float(str(p.get("dist_km", 0)).replace(",", "."))
        except Exception:
            dist = 0
        clean.append({"date": key[0], "time": key[1],
                      "type": str(p.get("type") or "легкий")[:20],
                      "dist_km": round(dist, 1) if dist else 6,
                      "why": str(p.get("why") or allowed[key]["why"])[:200],
                      "shift": allowed[key]["shift"]})
    if not clean:
        K.log(TAG, "AI не дав валідного плану — локальний фолбек")
        clean = _fallback_plan(windows, target)
        for p in clean:
            p["shift"] = allowed.get((p["date"], p["time"]), {}).get("shift", "")

    clean = sorted(clean, key=lambda x: (x["date"], x["time"]))[:4]
    return {"ok": True, "plan": clean, "runs_7d": rc["runs_7d"], "km_7d": rc["km_7d"],
            "ai": bool(plan), "weight": wtxt}


_ICON = {"легкий": "🟢", "темповий": "🟠", "інтервали": "🔴", "довгий": "🔵"}


def offer(force: bool = False) -> bool:
    """Надсилає карточку з планом бігу під зміни."""
    if not force and not K.rate_ok(STATE_FILE, PLAN_MIN_GAP_MIN):
        return False
    K.rate_mark(STATE_FILE)

    res = build_plan()
    if not res.get("ok"):
        K.log(TAG, f"план не побудовано: {res.get('error')}")
        return False
    plan = res["plan"]
    if not plan:
        K.log(TAG, "вільних вікон немає — тиша")
        return False

    sig = "|".join(f"{p['date']}{p['time']}" for p in plan)
    if not force and _dedup.seen("plan", sig):
        K.log(TAG, "такий план вже пропонував")
        return False

    pid = _store.put({"plan": plan})
    total = round(sum(p["dist_km"] for p in plan), 1)

    text = ["🏃 <b>ПЛАН БІГУ ПІД ТВОЇ ЗМІНИ</b>", "━━━━━━━━━━━━━━━━━━━━",
            f"📊 За 7 днів: {res['runs_7d']} пробіжок, {res['km_7d']} км",
            f"⚖️ {K.esc(res['weight'])}", ""]
    for i, p in enumerate(plan, 1):
        dt = datetime.strptime(p["date"], "%Y-%m-%d")
        wd = UA_DAYS[dt.weekday()]
        text.append(f"{_ICON.get(p['type'], '🏃')} <b>{i}. {dt.strftime('%d.%m')} ({wd}) "
                    f"о {p['time']}</b> — {p['type']}, {p['dist_km']} км")
        if p.get("shift"):
            text.append(f"    <i>зміна: {K.esc(p['shift'])}</i>")
        text.append(f"    {K.esc(p['why'])}")
    text.append("")
    text.append(f"📌 Разом: <b>{total} км</b> за {len(plan)} тренування")
    if not res.get("ai"):
        text.append("<i>(складено локально — Gemini був недоступний)</i>")

    kb = [
        [{"text": f"📅 Поставити всі {len(plan)} в календар", "callback_data": f"rp_all_{pid}"}],
        [{"text": "1️⃣ Тільки першу", "callback_data": f"rp_one_{pid}"},
         {"text": "❌ Не треба", "callback_data": f"rp_skip_{pid}"}],
    ]
    ok = K.send_card("\n".join(text), kb, tag=TAG)
    if ok:
        _dedup.mark("plan", sig)
        K.log(TAG, f"✅ план запропоновано: {len(plan)} тренувань, {total} км")
    else:
        _store.drop(pid)
    return ok


# ─── ДІЇ КНОПОК ──────────────────────────────────────────────────────────────

def _create(p) -> bool:
    start = K.parse_dt(p["date"], p["time"])
    dur = 40 if p["type"] == "легкий" else 60
    title = f"🏃 Біг {p['dist_km']} км ({p['type']})"
    res = K.calendar_event(title, start, start + timedelta(minutes=dur),
                           description=f"{p.get('why', '')}\n\n— план від AI-тренера")
    return bool(res.get("ok"))


def do_all(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    plan = p.get("plan") or []
    done, fail = [], 0
    for s in plan:
        if _create(s):
            done.append(s)
        else:
            fail += 1
    if not done:
        return {"ok": False, "error": "calendar_error"}
    _store.drop(pid)
    return {"ok": True, "created": len(done), "failed": fail,
            "items": [f"{s['date']} {s['time']} — {s['dist_km']} км" for s in done]}


def do_one(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    plan = p.get("plan") or []
    if not plan:
        return {"ok": False, "error": "empty_plan"}
    s = plan[0]
    if not _create(s):
        return {"ok": False, "error": "calendar_error"}
    _store.drop(pid)
    return {"ok": True, "created": 1,
            "items": [f"{s['date']} {s['time']} — {s['dist_km']} км ({s['type']})"]}


def do_skip(pid: str) -> dict:
    _store.drop(pid)
    return {"ok": True}


if __name__ == "__main__":
    import sys, json as _j
    if "--dry" in sys.argv:
        print(_j.dumps(build_plan(), ensure_ascii=False, indent=1))
    elif "--windows" in sys.argv:
        print(_j.dumps(_windows(7), ensure_ascii=False, indent=1))
    else:
        print("sent:", offer(force=True))
