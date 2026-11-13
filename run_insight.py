#!/usr/bin/env python3
"""
run_insight.py — «тренер» з Garmin: аналіз пробіжок і готовність до бігу.

Запит Олега (13.11): «Особливо увагу я приділяю здоров'ю та бігу. AI по Garmin
даних — аналіз і рекомендації». Раніше біг = шаблонні нагадування («Час бігти!
25-35 хв», «6 днів без пробіжки»), що суперечили його реальним 10 км за ~64 хв.

Усі ЧИСЛА і ВЕРДИКТ готовності рахує код (детерміновано) — AI лише пише з них
текст і підбирає питання/кнопки. Нічого не вигадує і нічого не робить сам.

Два режими:
  A) ПІСЛЯ пробіжки (раз на кожну нову активність Garmin): порівняння з його ж
     медіаною (темп, пульс, метрів на удар), ефект тренування/каденс якщо Garmin
     їх віддав, час відновлення, коли наступний біг, питання «як відчуття».
  B) ПЕРЕД пробіжкою (раз на ~30 год, 07:30–19:00, якщо сьогодні не бігав і з
     останнього бігу ≥2 дні): готовність green/yellow/red з HRV, пульсу спокою,
     сну, Body Battery, навантаження 7д/28д, тип тренування, дистанція, стеля
     пульсу, слот під зміни/календар. Кнопки: Біжу / Не сьогодні (графік|втома).

Не використовує ai_kit.topic_ok (уроки зі старту: старі картки тримають мітки
і новий модуль не виходить). Власні інтервали + K.topic_mark після відправки.

Callback: ri_<pid>_<i>, ri_skip_<pid>.
"""
import io
import os
import re
import statistics
from datetime import datetime, timedelta

import ai_kit as K

TAG = "run_insight"
STATE_FILE = "run_insight_state.json"
STORE_FILE = "run_insight_store.json"
PRE_GAP_H = 30
PRE_WINDOW = (7.5, 19.0)
MIN_RUN_M = 2000          # коротше — це не пробіжка (ходьба/розминка)
FRESH_RUN_H = 36          # аналізуємо лише ті, що не старші за це

_store = K.PayloadStore(STORE_FILE)


def _log(m):
    K.log(TAG, m)


# ─── ДАНІ ────────────────────────────────────────────────────────────────────

def _fnum(x):
    try:
        f = float(x)
        return f if f > 0 else None
    except Exception:
        return None


def runs(days: int = 90, acts: list = None) -> list:
    """Лише пробіжки (Run/TrailRun/VirtualRun, ≥2 км), за зростанням дати."""
    if acts is None:
        try:
            import running
            acts = running.get_activities(days=days)
        except Exception as e:
            _log("running: " + str(e))
            return []
    out = []
    for a in acts:
        if not isinstance(a, dict) or a.get("type") not in ("Run", "TrailRun", "VirtualRun"):
            continue
        dist = _fnum(a.get("distance"))
        dur = _fnum(a.get("moving_time"))
        if not dist or not dur or dist < MIN_RUN_M:
            continue
        try:
            dt = datetime.fromisoformat(str(a.get("start_date_local", "")).replace("Z", ""))
        except Exception:
            continue
        km = dist / 1000.0
        pace = dur / km                       # с/км
        hr = _fnum(a.get("average_heartrate"))
        eff = ((dist / (dur / 60.0)) / hr) if hr else None   # метрів на удар
        out.append({
            "id": str(a.get("start_date_local")), "date": dt.replace(tzinfo=None),
            "km": round(km, 2), "dur": dur, "pace": round(pace, 1), "hr": hr,
            "eff": round(eff, 3) if eff else None,
            "max_hr": _fnum(a.get("max_hr")), "te": _fnum(a.get("aerobic_te")),
            "ate": _fnum(a.get("anaerobic_te")), "cad": _fnum(a.get("average_cadence")),
            "load": _fnum(a.get("training_load")), "cal": _fnum(a.get("calories")),
            "elev": _fnum(a.get("total_elevation_gain")), "name": a.get("name") or "",
        })
    out.sort(key=lambda r: r["date"])
    return out


def fmt_pace(sec) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_dur(sec) -> str:
    sec = int(round(sec))
    h, m = sec // 3600, (sec % 3600) // 60
    return f"{h}:{m:02d}:{sec % 60:02d}" if h else f"{m}:{sec % 60:02d}"


def _now_naive() -> datetime:
    return K.now().replace(tzinfo=None)


def weekly_km(rs: list, weeks: int = 8, now: datetime = None) -> list:
    """[(початок тижня пн, км)] за останні `weeks` тижнів, включно з поточним."""
    now = now or _now_naive()
    mon = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in range(weeks - 1, -1, -1):
        a = mon - timedelta(weeks=i)
        b = a + timedelta(days=7)
        out.append((a, round(sum(r["km"] for r in rs if a <= r["date"] < b), 1)))
    return out


def load_ratio(rs: list, now: datetime = None) -> dict:
    """Гостре (7д) проти хронічного (середній тиждень за попередні 28д)."""
    now = now or _now_naive()
    acute = sum(r["km"] for r in rs if now - timedelta(days=7) <= r["date"] <= now)
    prev = sum(r["km"] for r in rs if now - timedelta(days=35) <= r["date"] < now - timedelta(days=7))
    chronic = prev / 4.0
    ratio = (acute / chronic) if chronic > 0.5 else None
    return {"acute": round(acute, 1), "chronic": round(chronic, 1),
            "ratio": round(ratio, 2) if ratio else None}


def compare_last(rs: list, last: dict) -> dict:
    """Остання пробіжка проти медіани його ж попередніх (60 днів, 5–25 км)."""
    base = [r for r in rs if r["date"] < last["date"]
            and (last["date"] - r["date"]).days <= 60 and 5 <= r["km"] <= 25]
    out = {"n": len(base)}
    if len(base) < 2:
        return out
    mp = statistics.median(r["pace"] for r in base)
    out["pace_med"] = round(mp, 1)
    out["pace_diff"] = round(last["pace"] - mp, 1)          # + = повільніше
    hrs = [r["hr"] for r in base if r["hr"]]
    if hrs and last["hr"]:
        out["hr_med"] = round(statistics.median(hrs), 1)
        out["hr_diff"] = round(last["hr"] - out["hr_med"], 1)
    effs = [r["eff"] for r in base if r["eff"]]
    if effs and last["eff"]:
        em = statistics.median(effs)
        out["eff_med"] = round(em, 3)
        out["eff_pct"] = round((last["eff"] / em - 1) * 100, 1)   # + = економніше
    out["km_med"] = round(statistics.median(r["km"] for r in base), 1)
    return out


# ─── ГОТОВНІСТЬ (детерміновано) ──────────────────────────────────────────────

def readiness(snap: dict = None, rows: list = None, rs: list = None,
              now: datetime = None) -> dict:
    """{'level': green|yellow|red, 'reasons': [...], 'facts': {...}}.
    Правила фіксовані, щоб AI їх не вигадував:
      red:    HRV z ≤ −1.5 | сон < 5.5 год | Body Battery < 20 | жорсткий біг ≤1 дня тому
      yellow: HRV z ≤ −0.8 | пульс спокою z ≥ +1.2 | сон < 6.5 год | BB < 35 | ACWR > 1.4
    Лише СВІЖІ заміри (≤1 дня; Body Battery — сьогоднішній)."""
    now = now or _now_naive()
    if snap is None:
        try:
            import garmin_fresh
            snap = garmin_fresh.snapshot()
        except Exception as e:
            _log("snapshot: " + str(e))
            snap = {}
    if rows is None:
        try:
            import garmin_insight
            rows = garmin_insight.analyse()
        except Exception as e:
            _log("analyse: " + str(e))
            rows = []
    byf = {r["field"]: r for r in rows}
    red, yellow, facts = [], [], {}
    hrv_strong = ""

    hrv = byf.get("hrv")
    if hrv:
        facts["hrv"] = hrv["last"]
        facts["hrv_base"] = hrv["base"]
        facts["hrv_z"] = hrv["z"]
        if hrv["z"] <= -1.5:
            hrv_strong = f"HRV {hrv['last']:g} мс — суттєво нижче твоєї норми {hrv['base']:g} ({hrv['z']:+.1f}σ)"
        elif hrv["z"] <= -0.8:
            yellow.append(f"HRV {hrv['last']:g} мс — нижче норми {hrv['base']:g} ({hrv['z']:+.1f}σ)")
    rhr = byf.get("hr_avg")
    if rhr:
        facts["rhr"] = rhr["last"]
        facts["rhr_base"] = rhr["base"]
        if rhr["z"] >= 1.2:
            yellow.append(f"пульс спокою {rhr['last']:g} при нормі {rhr['base']:g}")

    sl = snap.get("sleep_total_min") or {}
    if sl and sl.get("fresh"):
        try:
            hrs = float(sl["value"]) / 60.0
            facts["sleep_h"] = round(hrs, 1)
            if hrs < 5.5:
                red.append(f"сон лише {hrs:.1f} год")
            elif hrs < 6.5:
                yellow.append(f"сон {hrs:.1f} год")
        except Exception:
            pass
    bb = snap.get("body_battery") or {}
    if bb and bb.get("fresh"):
        try:
            v = float(bb["value"])
            facts["body_battery"] = round(v)
            if v < 20:
                red.append(f"Body Battery {v:.0f}/100")
            elif v < 35:
                yellow.append(f"Body Battery {v:.0f}/100")
        except Exception:
            pass
    st = snap.get("stress") or {}
    if st and st.get("fresh") and not st.get("partial"):
        facts["stress"] = st["value"]

    rs = rs if rs is not None else runs()
    lr = load_ratio(rs, now)
    facts["acwr"] = lr
    if lr["ratio"] and lr["ratio"] > 1.4:
        yellow.append(f"навантаження 7 днів {lr['acute']:g} км проти норми {lr['chronic']:g} км/тиж (×{lr['ratio']:g})")
    if rs:
        last = rs[-1]
        gap_h = (now - last["date"]).total_seconds() / 3600
        facts["hours_since_run"] = round(gap_h, 1)
        hard = (last["te"] or 0) >= 3.8 or last["km"] >= 15 or (last["km"] >= 10 and (last["hr"] or 0) >= 155)
        if hard and gap_h <= 30:
            red.append(f"учора був жорсткий біг ({last['km']:g} км"
                       + (f", ефект {last['te']:g}" if last["te"] else "") + ")")
        elif gap_h <= 24 and last["km"] >= 8:
            yellow.append(f"останній біг {last['km']:g} км був менше доби тому")

    if hrv_strong:
        # один лише низький HRV за ніч (решта показників у нормі) — це жовта,
        # а не червона: легко + слухати тіло. Червона — якщо є ще хоч один мінус.
        (red if (red or yellow) else yellow).append(hrv_strong)
    level = "red" if red else ("yellow" if yellow else "green")
    if not facts.get("hrv") and not facts.get("sleep_h") and level == "green":
        level = "unknown"
    return {"level": level, "reasons": red + yellow, "facts": facts}


def plan(rd: dict, rs: list) -> dict:
    """Детермінована рекомендація сесії з рівня готовності і його ж даних."""
    lvl = rd["level"]
    kms = [r["km"] for r in rs[-6:]] or [8.0]
    typical = statistics.median(kms)
    hrs = [r["hr"] for r in rs[-8:] if r["hr"]]
    med_hr = statistics.median(hrs) if hrs else None
    if lvl == "red":
        return {"type": "rest", "title": "відпочинок або легка ходьба 30–40 хв",
                "km": 0, "hr_cap": None}
    if lvl == "yellow":
        km = max(4.0, round(typical * 0.6 * 2) / 2)
        cap = round(med_hr * 0.9) if med_hr else None
        return {"type": "easy", "title": "легкий біг на розмові", "km": km, "hr_cap": cap}
    km = max(5.0, round(typical * 2) / 2)
    cap = round(med_hr * 0.93) if med_hr else None
    return {"type": "base", "title": "рівний аеробний біг", "km": km, "hr_cap": cap}


def recovery(last: dict) -> dict:
    """Скільки годин відновлюватись після пробіжки (за дистанцією/ефектом/пульсом)."""
    te = last["te"] or 0
    if te >= 4.0 or last["km"] >= 15:
        h = 48
    elif te >= 3.0 or last["km"] >= 8 or (last["hr"] or 0) >= 150:
        h = 36
    else:
        h = 24
    nxt = last["date"] + timedelta(hours=h + last["dur"] / 3600.0)
    return {"hours": h, "next": nxt}


# ─── ГРАФІК ──────────────────────────────────────────────────────────────────

def chart(rs: list, now: datetime = None) -> bytes:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        _log("matplotlib: " + str(e))
        return b""
    wk = weekly_km(rs, 8, now)
    last = rs[-10:]
    if not last:
        return b""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4), dpi=110,
                                 gridspec_kw={"width_ratios": [1, 1.2]})
    fig.patch.set_facecolor("#0D1117")
    for ax in (a1, a2):
        ax.set_facecolor("#161B22")
        ax.tick_params(colors="#8B949E", labelsize=8)
        for sp in ax.spines.values():
            sp.set_color("#30363D")
        ax.grid(color="#30363D", lw=0.5, alpha=0.6)
    xs = [w[0].strftime("%d.%m") for w in wk]
    vals = [w[1] for w in wk]
    cols = ["#58A6FF"] * (len(vals) - 1) + ["#3FB950"]
    a1.bar(xs, vals, color=cols)
    for i, v in enumerate(vals):
        if v:
            a1.text(i, v, f"{v:g}", ha="center", va="bottom", color="#E6EDF3", fontsize=8)
    a1.set_title("км за тиждень", color="#E6EDF3", fontsize=11, loc="left")
    a1.tick_params(axis="x", rotation=45)

    lab = [r["date"].strftime("%d.%m") for r in last]
    paces = [r["pace"] / 60.0 for r in last]
    a2.plot(lab, paces, color="#E3B341", marker="o", lw=1.8)
    a2.invert_yaxis()
    a2.set_ylabel("темп хв/км", color="#E3B341", fontsize=8)
    a2.set_title("темп і пульс, останні пробіжки", color="#E6EDF3", fontsize=11, loc="left")
    a2.tick_params(axis="x", rotation=45)
    hv = [(i, r["hr"]) for i, r in enumerate(last) if r["hr"]]
    if hv:
        b = a2.twinx()
        b.plot([lab[i] for i, _ in hv], [h for _, h in hv], color="#F85149", marker="s", lw=1.4)
        b.set_ylabel("пульс", color="#F85149", fontsize=8)
        b.tick_params(colors="#8B949E", labelsize=8)
        for sp in b.spines.values():
            sp.set_color("#30363D")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _send_photo(png: bytes, caption: str = "") -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat and png):
        return False
    try:
        try:
            import requests as _rq
        except ImportError:
            import httpreq as _rq
        data = {"chat_id": chat}
        if caption:
            data.update({"caption": caption[:1000], "parse_mode": "HTML"})
        r = _rq.post("https://api.telegram.org/bot" + token + "/sendPhoto", data=data,
                     files={"photo": ("run.png", png, "image/png")}, timeout=60)
        return getattr(r, "status_code", 0) == 200
    except Exception as e:
        _log("sendPhoto: " + str(e))
        return False


# ─── AI ──────────────────────────────────────────────────────────────────────

def _schedule_text() -> str:
    lines = []
    try:
        sm = K.shift_map(2)
        names = {"early": "рання зміна", "night": "нічна зміна", "free": "вільний день"}
        for i, (d, s) in enumerate(sm.items()):
            lines.append(("сьогодні" if i == 0 else "завтра") + f" ({d}): {names.get(s, s)}")
    except Exception as e:
        _log("shift_map: " + str(e))
    try:
        import calendar_watch
        c = calendar_watch.ai_context_text(past_hours=0, ahead_days=2, limit=8)
        if c:
            lines.append("Календар: " + c.replace("\n", "; ")[:500])
    except Exception as e:
        _log("calendar: " + str(e))
    return "\n".join(lines) or "немає даних про графік"


def _answers_text() -> str:
    try:
        import ai_brain
        a = ai_brain._answers_block(days=14, limit=10)
        if isinstance(a, (list, tuple)):
            a = "; ".join(str(x) for x in a)
        return str(a)[:900]
    except Exception:
        return ""


def _run_line(r: dict) -> str:
    s = f"{r['date'].strftime('%d.%m')} {r['km']:g} км, {fmt_dur(r['dur'])}, темп {fmt_pace(r['pace'])}/км"
    if r["hr"]:
        s += f", пульс {r['hr']:.0f}"
    if r["max_hr"]:
        s += f" (макс {r['max_hr']:.0f})"
    if r["te"]:
        s += f", аеробний ефект {r['te']:g}"
    if r["cad"]:
        s += f", каденс {r['cad']:.0f}"
    return s


_PROMPT_POST = """Ти біговий тренер Олега (Кошице, зміни, ціль 75 кг). Нижче РЕАЛЬНІ дані Garmin і
ПОРАХОВАНІ висновки. Пиши лише з них, нічого не вигадуй.

ПРОБІЖКА: {run}
ПОРІВНЯННЯ З ЙОГО ЖЕ МЕДІАНОЮ ({n} попередніх): {cmp}
ОСТАННІ ПРОБІЖКИ: {history}
ТИЖНІ (км): {weeks}. Навантаження 7д/норма: {acwr}
ВІДНОВЛЕННЯ (порахував код): мінімум {rec_h} год, наступний біг не раніше {nxt}.
СТАН ЗАРАЗ: {ready}
ГРАФІК: {sched}
ЙОГО ВІДПОВІДІ: {ans}

Завдання (українською, без «Привіт», без загальних фраз, одразу до суті):
"text": 4-6 коротких речень HTML (<b> для 2-3 ключових чисел). Що було добре/погано
  проти його норми (темп, пульс, економність), що означає для форми, що робити
  сьогодні для відновлення (їжа/вода/сон/розтяжка — конкретно), коли і який наступний біг.
"question": одне коротке питання про самопочуття після бігу (ноги, дихання, біль).
"options": 3-4 варіанти, що ПРЯМО відповідають питанню (до 28 символів кожен;
  один має сигналізувати про біль/проблему). Це лише запис, не обіцяй нагадувань.
Формат — ТІЛЬКИ JSON: {{"text":"...","question":"...","options":["..",".."]}}"""

_PROMPT_PRE = """Ти біговий тренер Олега (Кошице, зміни, ціль 75 кг). Нижче РЕАЛЬНІ дані Garmin і
ПОРАХОВАНИЙ вердикт. Пиши лише з них, нічого не вигадуй і не змінюй вердикт.

ГОТОВНІСТЬ: {level_ua}. Причини: {reasons}
ПОКАЗНИКИ: {facts}
РЕКОМЕНДАЦІЯ (порахував код): {rec}
Днів без бігу: {gap_days}. Останні пробіжки: {history}
ТИЖНІ (км): {weeks}. Навантаження 7д/норма: {acwr}
ГРАФІК: {sched}
ЙОГО ВІДПОВІДІ: {ans}

Завдання (українською, без «Привіт», одразу до суті):
"text": 4-6 коротких речень HTML (<b> для 2-3 ключових чисел). Чесно: чи варто бігти
  сьогодні і чому (з причин вище); якщо так — тип, дистанція, стеля пульсу з РЕКОМЕНДАЦІЇ;
  слот, що вміщається в зміни/календар (нічна зміна — не пізно ввечері перед нею;
  рання завтра — закінчити до 19:00); 1 порада про розминку/їжу/воду. Якщо red — прямо
  скажи не бігти і чим замінити.
"question": «Плануєш бігти сьогодні?» або близьке за змістом.
Формат — ТІЛЬКИ JSON: {{"text":"...","question":"..."}}"""

_LEVEL_UA = {"green": "🟢 зелена — можна бігти за планом", "yellow": "🟡 жовта — тільки легко",
             "red": "🔴 червона — краще відпочити", "unknown": "⚪ мало свіжих даних"}


def _ctx_common(rs: list, now: datetime) -> dict:
    lr = load_ratio(rs, now)
    return {
        "history": "; ".join(_run_line(r) for r in rs[-6:]) or "—",
        "weeks": ", ".join(f"{w[0].strftime('%d.%m')}={w[1]:g}" for w in weekly_km(rs, 6, now)),
        "acwr": (f"{lr['acute']:g} / {lr['chronic']:g} км (×{lr['ratio']:g})" if lr["ratio"]
                 else f"{lr['acute']:g} км за 7д, бази для порівняння замало"),
        "sched": _schedule_text(), "ans": _answers_text() or "—",
    }


def _clean_opts(opts) -> list:
    return [str(o).strip()[:28] for o in (opts or []) if str(o).strip()][:4]


def _cmp_text(c: dict) -> str:
    if c.get("n", 0) < 2:
        return "замало попередніх пробіжок для порівняння"
    parts = []
    if "pace_diff" in c:
        d = c["pace_diff"]
        parts.append(f"темп {'повільніше' if d > 0 else 'швидше'} на {abs(d):.0f} с/км (медіана {fmt_pace(c['pace_med'])})")
    if "hr_diff" in c:
        parts.append(f"пульс {c['hr_diff']:+.0f} уд/хв до медіани {c['hr_med']:.0f}")
    if "eff_pct" in c:
        parts.append(f"економність (метрів на удар) {c['eff_pct']:+.1f}%")
    parts.append(f"типова дистанція {c['km_med']:g} км")
    return "; ".join(parts)


def build_post(rs: list, last: dict, now: datetime = None) -> dict:
    now = now or _now_naive()
    rd = readiness(rs=rs, now=now)
    rec = recovery(last)
    ctx = _ctx_common(rs, now)
    prompt = _PROMPT_POST.format(
        run=_run_line(last), n=compare_last(rs, last).get("n", 0),
        cmp=_cmp_text(compare_last(rs, last)), rec_h=rec["hours"],
        nxt=rec["next"].strftime("%d.%m %H:%M"),
        ready=_LEVEL_UA[rd["level"]] + ("; " + "; ".join(rd["reasons"]) if rd["reasons"] else ""),
        **ctx)
    data = K.gemini_json(prompt, max_tokens=1000, temperature=0.8, tag=TAG, want="dict")
    if not isinstance(data, dict) or not data.get("text"):
        return {}
    opts = _clean_opts(data.get("options"))
    q = str(data.get("question") or "").strip()
    if not q or len(opts) < 2:
        q, opts = "Як ноги і самопочуття після бігу?", [
            "Легко, ноги свіжі", "Нормально", "Важко, ноги забиті", "Щось болить"]
    return {"text": str(data["text"]).strip(), "question": q, "options": opts}


def build_pre(rs: list, rd: dict, pl: dict, now: datetime = None) -> dict:
    now = now or _now_naive()
    ctx = _ctx_common(rs, now)
    gap_days = (now - rs[-1]["date"]).days if rs else "—"
    rec = pl["title"] + (f", ~{pl['km']:g} км" if pl["km"] else "") + \
        (f", пульс до {pl['hr_cap']} (орієнтир від твого типового)" if pl["hr_cap"] else "")
    facts = ", ".join(f"{k}={v}" for k, v in rd["facts"].items() if k != "acwr") or "—"
    prompt = _PROMPT_PRE.format(
        level_ua=_LEVEL_UA[rd["level"]], reasons="; ".join(rd["reasons"]) or "жодних тривожних",
        facts=facts, rec=rec, gap_days=gap_days, **ctx)
    data = K.gemini_json(prompt, max_tokens=900, temperature=0.8, tag=TAG, want="dict")
    if not isinstance(data, dict) or not data.get("text"):
        return {}
    q = str(data.get("question") or "").strip() or "Плануєш бігти сьогодні?"
    return {"text": str(data["text"]).strip(), "question": q}


# ─── ВІДПРАВКА ───────────────────────────────────────────────────────────────

def _state() -> dict:
    st = K.load(STATE_FILE, default={}) or {}
    return st if isinstance(st, dict) else {}


def _gates(force: bool) -> bool:
    if force:
        return True
    try:
        import quiet
        if quiet.blocked("msg"):
            return False
    except Exception:
        pass
    try:
        import dismissed
        if dismissed.is_muted("run_insight", key="run"):
            return False
    except Exception:
        pass
    return True


def _deliver(head: str, body: str, question: str, kb: list, png: bytes, pid: str) -> bool:
    txt = head + "\n\n" + body + (("\n\n❓ <b>" + K.esc(question) + "</b>") if question else "")
    ok = K.send_card(txt, kb, tag=TAG)
    if not ok:
        _store.drop(pid)
        return False
    if png:
        _send_photo(png)      # графік — після картки (картка проходить дедуп outbox)
    K.topic_mark("strava")    # тема «run»: старі біг-картки відступають
    return True


def run_post(force: bool = False) -> bool:
    """Режим A: розбір нової пробіжки."""
    if not _gates(force):
        return False
    try:
        import garmin_fresh
        garmin_fresh.ensure_fresh()
    except Exception as e:
        _log("ensure_fresh: " + str(e))
    rs = runs()
    if not rs:
        return False
    st = _state()
    done = set(st.get("analysed") or [])
    if not st.get("init"):
        # перший запуск: старе не розбираємо, щоб не засипати бекплогом
        old = [r["id"] for r in rs if (_now_naive() - r["date"]).total_seconds() / 3600 > FRESH_RUN_H]
        done |= set(old)
        st["init"] = True
    now = _now_naive()
    todo = [r for r in rs if r["id"] not in done]
    for r in todo:                                   # застарілі — тихо закриваємо
        if (now - r["date"]).total_seconds() / 3600 > FRESH_RUN_H:
            done.add(r["id"])
    todo = [r for r in todo if r["id"] not in done]
    if not todo:
        st["analysed"] = sorted(done)[-60:]
        K.save(STATE_FILE, st)
        return False
    last = todo[-1]
    built = build_post(rs, last, now)
    if not built:
        _log("AI не склав розбір пробіжки")
        st["analysed"] = sorted(done)[-60:]
        K.save(STATE_FILE, st)
        return False
    rec = recovery(last)
    head = (f"🏃 <b>Біг · {last['date'].strftime('%d.%m')}: {last['km']:g} км за {fmt_dur(last['dur'])}</b>"
            f" · {fmt_pace(last['pace'])}/км" + (f" · пульс {last['hr']:.0f}" if last["hr"] else ""))
    pid = _store.put({"mode": "post", "q": built["question"], "opts": built["options"],
                      "run": last["id"], "title": f"Біг {last['date'].strftime('%d.%m')} {last['km']:g} км"})
    kb = [[{"text": o, "callback_data": f"ri_{pid}_{i}"}] for i, o in enumerate(built["options"])]
    kb.append([{"text": "🙈 Не розбирай пробіжки", "callback_data": f"ri_skip_{pid}"}])
    ok = _deliver(head, built["text"], built["question"], kb, chart(rs, now), pid)
    if ok:
        done.add(last["id"])
        st["analysed"] = sorted(done)[-60:]
        st["last_post"] = {"ts": K.now().isoformat(), "run": last["id"],
                           "recover_until": rec["next"].isoformat()}
        K.save(STATE_FILE, st)
        _log(f"✅ розбір пробіжки {last['id']}")
    return ok


PRE_OPTS = ["Біжу сьогодні", "Не сьогодні — графік", "Не сьогодні — втома"]


def run_pre(force: bool = False) -> bool:
    """Режим B: готовність перед пробіжкою."""
    if not _gates(force):
        return False
    now = _now_naive()
    hr = now.hour + now.minute / 60.0
    st = _state()
    if not force:
        if not (PRE_WINDOW[0] <= hr < PRE_WINDOW[1]):
            return False
        if not K.rate_ok("run_insight_pre_gap.json", PRE_GAP_H * 60):
            return False
    try:
        import garmin_fresh
        garmin_fresh.ensure_fresh()
    except Exception as e:
        _log("ensure_fresh: " + str(e))
    rs = runs()
    if not rs:
        return False
    last = rs[-1]
    if last["date"].date() == now.date():
        return False                       # сьогодні вже бігав
    gap_days = (now.date() - last["date"].date()).days
    if gap_days < 2 and not force:
        return False
    rd = readiness(rs=rs, now=now)
    pl = plan(rd, rs)
    K.rate_mark("run_insight_pre_gap.json")
    built = build_pre(rs, rd, pl, now)
    if not built:
        _log("AI не склав попередній розбір")
        return False
    icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(rd["level"], "⚪")
    head = (f"🏃 <b>Біг · готовність {icon}</b> · без бігу {gap_days} дн."
            + (f" · план: {pl['km']:g} км" if pl["km"] else ""))
    pid = _store.put({"mode": "pre", "q": built["question"], "opts": PRE_OPTS,
                      "level": rd["level"], "title": "Біг сьогодні"})
    kb = [[{"text": o, "callback_data": f"ri_{pid}_{i}"}] for i, o in enumerate(PRE_OPTS)]
    kb.append([{"text": "🙈 Не питай про біг", "callback_data": f"ri_skip_{pid}"}])
    ok = _deliver(head, built["text"], built["question"], kb, chart(rs, now), pid)
    if ok:
        st["last_pre"] = {"ts": K.now().isoformat(), "level": rd["level"], "day": K.today_str()}
        K.save(STATE_FILE, st)
        _log(f"✅ готовність {rd['level']}")
    return ok


def run(force: bool = False) -> bool:
    """Точка входу для watcher-а: спершу розбір нової пробіжки, інакше готовність."""
    try:
        if run_post(force):
            return True
    except Exception as e:
        _log("run_post: " + str(e))
    return run_pre(force)


def handle(data: str) -> dict:
    """ri_<pid>_<i> | ri_skip_<pid>  →  {'ok','text'}"""
    m = re.match(r"^ri_skip_(\w+)$", data)
    if m:
        _store.drop(m.group(1))
        try:
            import dismissed
            dismissed.mute("run_insight", key="run", title="AI-розбір бігу")
        except Exception:
            pass
        return {"ok": True, "text": "Ок, розбори бігу вимкнув."}
    m = re.match(r"^ri_(\w+)_(\d)$", data)
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
        ai_brain.remember_answer(f"{p.get('title')}: {p.get('q')}", opts[i],
                                 topic="run", source="button")
    except Exception as e:
        _log("remember: " + str(e))
    _store.drop(m.group(1))
    return {"ok": True, "text": f"Записав: {opts[i]}"}


if __name__ == "__main__":
    import sys
    rs_ = runs()
    if "--dry" in sys.argv:
        for r in rs_[-6:]:
            print(_run_line(r))
        rd_ = readiness(rs=rs_)
        print(rd_)
        print(plan(rd_, rs_))
        print(weekly_km(rs_), load_ratio(rs_))
    else:
        print("надіслано:", run(force=True))
