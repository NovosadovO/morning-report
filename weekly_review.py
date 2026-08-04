#!/usr/bin/env python3
"""
ТИЖНЕВИЙ ОГЛЯД + 3 ЦІЛІ НА НАСТУПНИЙ ТИЖДЕНЬ  (Знання #2)

Раз на тиждень (нд ввечері) AI збирає РЕАЛЬНІ дані за 7 днів і порівнює
з попереднім тижнем:
   🏃 біг (Strava: кількість, км, темп — vs минулий тиждень)
   ⚖️ вага (тренд до цілі 78 кг)
   😴 сон / 👣 кроки (health)
   ✅ звички (habits.json — виконано з можливого)
   💸 рахунки (bills.json — прийшло / не оплачено)
   📈 крипто (BTC/ETH/AVAX/ONDO за 7 днів)
   📅 навантаження змін

Далі Gemini пише огляд + ставить 3 КОНКРЕТНІ цілі на наступний тиждень.
Кнопки: [📝 Зберегти цілі] [📅 Цілі в календар] [❌]
Callback-префікси: wr_goals_ / wr_cal_ / wr_skip_

Якщо Gemini недоступний — надсилається чесний локальний огляд на цифрах
(без вигаданих інтерпретацій) з тими самими кнопками.
"""

import re
import json
from datetime import datetime, timedelta

import ai_kit as K

TAG = "weekly_review"

STORE_FILE = "weekly_review_store.json"
STATE_FILE = "weekly_review_state.json"
GOALS_FILE = "weekly_goals.json"

MIN_GAP_MIN = 60 * 100     # ~4 доби, щоб не дублювати
_store = K.PayloadStore(STORE_FILE)


# ─── ЗБІР ДАНИХ ──────────────────────────────────────────────────────────────

def _running():
    try:
        import strava
        c = strava.compare_weeks()
        t, p = c["this_week"], c["prev_week"]

        def pace(sec):
            return f"{int(sec // 60)}:{int(sec % 60):02d}" if sec else "—"
        return {
            "ok": True,
            "text": (f"цей тиждень: {t['runs']} пробіжок, {t['km']} км, "
                     f"темп {pace(t['avg_pace_sec'])}/км\n"
                     f"минулий: {p['runs']} пробіжок, {p['km']} км, "
                     f"темп {pace(p['avg_pace_sec'])}/км\n"
                     f"різниця: {c['km_diff']:+} км, темп {c['pace_diff']:+.0f} с/км"),
            "runs": t["runs"], "km": t["km"], "km_diff": c["km_diff"],
            "prev_runs": p["runs"],
        }
    except Exception as e:
        K.log(TAG, f"strava error: {e}")
        return {"ok": False, "text": "Strava недоступна"}


def _weight():
    try:
        w = K.load("weight.json", default={}) or {}
        pts = sorted((k, v) for k, v in w.items()
                     if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)))
        vals = []
        for d, v in pts:
            val = v.get("weight") if isinstance(v, dict) else v
            try:
                vals.append((d, float(val)))
            except Exception:
                continue
        if not vals:
            return {"ok": False, "text": "вага не записана"}
        last_d, last_v = vals[-1]
        week_ago = (K.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        prev = [v for d, v in vals if d <= week_ago]
        base = prev[-1] if prev else vals[0][1]
        diff = round(last_v - base, 1)
        return {"ok": True, "last": last_v, "diff": diff,
                "text": (f"{last_v} кг (замір {last_d}), за тиждень {diff:+} кг, "
                         f"до цілі 78 кг залишилось {round(last_v - 78, 1)} кг")}
    except Exception as e:
        return {"ok": False, "text": f"вага недоступна ({e})"}


def _health():
    try:
        h = K.load("health.json", default={}) or {}
        days = sorted(k for k in h.keys() if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)))
        cutoff = (K.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week = [h[d] for d in days if d > cutoff and isinstance(h[d], dict)]
        if not week:
            return {"ok": False, "text": "даних про сон/кроки за тиждень немає"}
        steps = [float(x["steps"]) for x in week if x.get("steps")]
        sleep = [float(x["sleep_hours"]) for x in week if x.get("sleep_hours")]
        parts = []
        if steps:
            parts.append(f"кроки: сер. {int(sum(steps) / len(steps))}/день "
                         f"({len(steps)} днів з даними)")
        if sleep:
            parts.append(f"сон: сер. {sum(sleep) / len(sleep):.1f} год")
        return {"ok": bool(parts), "text": "; ".join(parts) or "немає даних"}
    except Exception as e:
        return {"ok": False, "text": f"health недоступний ({e})"}


def _habits():
    try:
        h = K.load("habits.json", default={}) or {}
        days = sorted(k for k in h.keys() if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)))
        cutoff = (K.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        prev_cut = (K.now() - timedelta(days=14)).strftime("%Y-%m-%d")

        def score(sel):
            done = tot = 0
            for d in sel:
                rec = h.get(d) or {}
                if not isinstance(rec, dict):
                    continue
                for k, v in rec.items():
                    tot += 1
                    if v is True:
                        done += 1
            return done, tot

        d1, t1 = score([d for d in days if d > cutoff])
        d2, t2 = score([d for d in days if prev_cut < d <= cutoff])
        if not t1 and not t2:
            return {"ok": False, "text": "звички не відмічались"}
        p1 = round(100 * d1 / t1) if t1 else 0
        p2 = round(100 * d2 / t2) if t2 else 0
        return {"ok": True, "pct": p1,
                "text": f"виконано {d1}/{t1} ({p1}%), минулий тиждень {p2}%"}
    except Exception as e:
        return {"ok": False, "text": f"звички недоступні ({e})"}


def _bills():
    try:
        import bills_watcher as B
        b = B.load_bills()
        cutoff = (K.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        new = [x for x in b.values() if str(x.get("created", "")) > cutoff]
        unpaid = [x for x in b.values() if not x.get("paid")]
        s = sum(B._amount_f(x.get("amount")) for x in unpaid)
        if not new and not unpaid:
            return {"ok": True, "text": "рахунків немає, боргів немає"}
        return {"ok": True,
                "text": (f"за тиждень прийшло {len(new)} рахунків; "
                         f"не оплачено {len(unpaid)} на {s:.2f} EUR")}
    except Exception as e:
        return {"ok": False, "text": f"рахунки недоступні ({e})"}


def _crypto():
    ids = "bitcoin,ethereum,avalanche-2,ondo-finance"
    url = ("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
           f"&ids={ids}&price_change_percentage=7d")
    data = None
    try:
        import monitor as _m
        if hasattr(_m, "fetch_json_cached"):
            data = _m.fetch_json_cached(url, ttl=600)
        elif hasattr(_m, "fetch_json"):
            data = _m.fetch_json(url)
    except Exception as e:
        K.log(TAG, f"crypto error: {e}")
    if not isinstance(data, list) or not data:
        return {"ok": False, "text": "крипто-дані недоступні"}
    parts = []
    for c in data:
        ch = c.get("price_change_percentage_7d_in_currency")
        sym = str(c.get("symbol", "")).upper()
        price = c.get("current_price")
        if price is None:
            continue
        parts.append(f"{sym} ${price:,.2f} ({ch:+.1f}% за 7д)" if ch is not None
                     else f"{sym} ${price:,.2f}")
    return {"ok": bool(parts), "text": "; ".join(parts)}


def _shifts():
    try:
        sm = K.shift_map(7)
        early = sum(1 for v in sm.values() if v == "early")
        night = sum(1 for v in sm.values() if v == "night")
        free = sum(1 for v in sm.values() if v == "free")
        return {"ok": True,
                "text": f"наступні 7 днів: {early} ранніх, {night} нічних, {free} вільних"}
    except Exception:
        return {"ok": False, "text": "графік змін недоступний"}


def collect() -> dict:
    return {
        "running": _running(), "weight": _weight(), "health": _health(),
        "habits": _habits(), "bills": _bills(), "crypto": _crypto(),
        "shifts": _shifts(),
    }


# ─── AI ──────────────────────────────────────────────────────────────────────

_PROMPT = """Ти — особистий коуч Олега (Кошице, Minebea Mitsumi, змінний графік,
цілі: фінансова незалежність, вага 78 кг, робота в інвестиціях, регулярний біг).

Напиши ТИЖНЕВИЙ ОГЛЯД українською на РЕАЛЬНИХ даних нижче. Тиждень: {period}

🏃 БІГ: {running}
⚖️ ВАГА: {weight}
😴 ЗДОРОВ'Я: {health}
✅ ЗВИЧКИ: {habits}
💸 РАХУНКИ: {bills}
📈 КРИПТО: {crypto}
📅 ЗМІНИ: {shifts}

ПРАВИЛА:
1. Оперуй ТІЛЬКИ цими цифрами. Немає даних — скажи «даних немає», не вигадуй.
2. Тон: теплий, підтримуючий, як друг-коуч. Звертайся «Олеже».
3. Структура з емодзі-заголовками, ~350-450 слів:
   🏁 ЯК ПРОЙШОВ ТИЖДЕНЬ (порівняння з минулим — де +, де −)
   💡 ГОЛОВНИЙ ІНСАЙТ (одна думка, яку варто забрати)
   ⚠️ НА ЩО ЗВЕРНУТИ УВАГУ
4. В кінці — рівно 3 цілі на наступний тиждень, кожна вимірювана
   (з числом і врахуванням графіка змін).

Формат — ТІЛЬКИ валідний JSON без markdown:
{{"review":"текст огляду з емодзі-заголовками та переносами рядків",
  "goals":[{{"title":"3 пробіжки, разом 20 км","why":"минулого тижня було 2 і 14 км","date":"{goal_date}"}},
           {{"title":"...","why":"...","date":"{goal_date}"}},
           {{"title":"...","why":"...","date":"{goal_date}"}}]}}"""


def _local_review(d: dict, period: str) -> dict:
    """Чесний огляд без AI — тільки цифри."""
    lines = [f"🏁 <b>ТИЖДЕНЬ {period}</b> — факти без інтерпретацій",
             "━━━━━━━━━━━━━━━━━━━━",
             f"🏃 {K.esc(d['running']['text'])}",
             f"⚖️ {K.esc(d['weight']['text'])}",
             f"😴 {K.esc(d['health']['text'])}",
             f"✅ {K.esc(d['habits']['text'])}",
             f"💸 {K.esc(d['bills']['text'])}",
             f"📈 {K.esc(d['crypto']['text'])}",
             f"📅 {K.esc(d['shifts']['text'])}",
             "", "<i>Gemini був недоступний — інтерпретацію не додаю, "
                 "щоб не вигадувати.</i>"]
    goals = []
    r = d["running"]
    if r.get("ok"):
        target = max(3, int(r.get("runs", 0)) + 1)
        goals.append({"title": f"{target} пробіжки наступного тижня",
                      "why": f"цього тижня було {r.get('runs', 0)}"})
    w = d["weight"]
    if w.get("ok"):
        goals.append({"title": f"вага {round(w['last'] - 0.5, 1)} кг",
                      "why": f"зараз {w['last']} кг, ціль 78 кг"})
    h = d["habits"]
    if h.get("ok"):
        goals.append({"title": f"звички {min(100, h['pct'] + 10)}%",
                      "why": f"цього тижня {h['pct']}%"})
    return {"review": "\n".join(lines), "goals": goals[:3], "ai": False}


def build(period_days: int = 7) -> dict:
    d = collect()
    start = (K.now() - timedelta(days=period_days)).strftime("%d.%m")
    period = f"{start} – {K.now().strftime('%d.%m.%Y')}"
    goal_date = (K.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    live = sum(1 for k in ("running", "weight", "health", "habits") if d[k].get("ok"))
    if live == 0:
        return {"ok": False, "error": "no_live_data"}

    out = K.gemini_json(_PROMPT.format(
        period=period, running=d["running"]["text"], weight=d["weight"]["text"],
        health=d["health"]["text"], habits=d["habits"]["text"],
        bills=d["bills"]["text"], crypto=d["crypto"]["text"],
        shifts=d["shifts"]["text"], goal_date=goal_date),
        max_tokens=2000, temperature=0.7, tag=TAG, want="dict")

    if isinstance(out, dict) and out.get("review"):
        goals = [g for g in (out.get("goals") or []) if isinstance(g, dict) and g.get("title")]
        res = {"review": str(out["review"])[:3000], "goals": goals[:3], "ai": True}
    else:
        res = _local_review(d, period)

    res.update({"ok": True, "period": period, "goal_date": goal_date, "data": d})
    return res


# ─── КАРТОЧКА ────────────────────────────────────────────────────────────────

def _fmt(res) -> str:
    txt = res["review"]
    # AI іноді пише **жирним** — Telegram HTML такого не розуміє
    txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)
    txt = re.sub(r"(?<!<)\*(?!\*)", "•", txt)
    body = [f"📊 <b>ТИЖНЕВИЙ ОГЛЯД {res['period']}</b>", "━━━━━━━━━━━━━━━━━━━━", txt]
    if res.get("goals"):
        body.append("\n🎯 <b>3 ЦІЛІ НА НАСТУПНИЙ ТИЖДЕНЬ</b>")
        for i, g in enumerate(res["goals"], 1):
            body.append(f"<b>{i}. {K.esc(g.get('title'))}</b>")
            if g.get("why"):
                body.append(f"    <i>{K.esc(g['why'])}</i>")
    return "\n".join(body)[:3900]


def offer(force: bool = False) -> bool:
    if not force and not K.rate_ok(STATE_FILE, MIN_GAP_MIN):
        return False
    K.rate_mark(STATE_FILE)

    res = build()
    if not res.get("ok"):
        K.log(TAG, f"огляд не побудовано: {res.get('error')}")
        return False

    pid = _store.put({"goals": res.get("goals") or [],
                      "goal_date": res.get("goal_date"), "period": res["period"]})
    kb = []
    if res.get("goals"):
        kb.append([{"text": "📝 Зберегти цілі в нотатки", "callback_data": f"wr_goals_{pid}"}])
        kb.append([{"text": "📅 Цілі в календар", "callback_data": f"wr_cal_{pid}"},
                   {"text": "❌ Не треба", "callback_data": f"wr_skip_{pid}"}])
    ok = K.send_card(_fmt(res), kb or None, tag=TAG)
    if ok:
        K.save(GOALS_FILE, {"period": res["period"], "goals": res.get("goals") or [],
                            "created": K.today_str()})
        K.log(TAG, f"✅ огляд надіслано (ai={res.get('ai')}, "
                   f"{len(res.get('goals') or [])} цілей)")
    else:
        _store.drop(pid)
    return ok


def is_time() -> bool:
    """Нд 19:00-21:59 — час тижневого огляду."""
    n = K.now()
    return n.weekday() == 6 and 19 <= n.hour < 22


# ─── ДІЇ КНОПОК ──────────────────────────────────────────────────────────────

def do_goals(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    goals = p.get("goals") or []
    if not goals:
        return {"ok": False, "error": "no_goals"}
    try:
        import ai_notes
        for g in goals:
            ai_notes.add_note(f"🎯 Ціль тижня ({p.get('period')}): {g.get('title')}"
                              + (f" — {g.get('why')}" if g.get("why") else ""),
                              source="weekly_review")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    _store.drop(pid)
    return {"ok": True, "count": len(goals),
            "items": [g.get("title") for g in goals]}


def do_calendar(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    goals = p.get("goals") or []
    if not goals:
        return {"ok": False, "error": "no_goals"}
    date = K.valid_future_date(p.get("goal_date") or "") or \
        (K.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    created = []
    for i, g in enumerate(goals):
        start = K.parse_dt(date, f"{9 + i}:00")
        res = K.calendar_event(f"🎯 {g.get('title')}", start,
                               start + timedelta(minutes=30),
                               description=(g.get("why") or "") + "\n\n— ціль тижня від AI-коуча")
        if res.get("ok"):
            created.append(g.get("title"))
    if not created:
        return {"ok": False, "error": "calendar_error"}
    _store.drop(pid)
    return {"ok": True, "count": len(created), "date": date, "items": created}


def do_skip(pid: str) -> dict:
    _store.drop(pid)
    return {"ok": True}


if __name__ == "__main__":
    import sys
    if "--data" in sys.argv:
        print(json.dumps(collect(), ensure_ascii=False, indent=1))
    elif "--dry" in sys.argv:
        r = build()
        print(json.dumps(r, ensure_ascii=False, indent=1)[:5000])
    else:
        print("sent:", offer(force=True))
