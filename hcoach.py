#!/usr/bin/env python3
"""
hcoach.py — AI-КОУЧ ПО ЗДОРОВ'Ю 2.0 (повна заміна старих звітів healthai).

Що робить:
  1. morning_plan()  — 07:00: план дня під зміну (сон, вода, їжа, рух, вікна).
  2. evening_review() — 21:20: розбір дня + ОЦІНКА 0-100 (рахується з даних,
                        не вигадується AI) + що завтра зробити інакше.
  3. sleep_report()   — аналіз сну окремо, з поправкою на нічні зміни.
  4. weekly_report()  — нд 19:30: тиждень фактами + AI + графік 2x2.
  5. monthly_report() — 1 числа 10:00: місяць фактами + AI + графік 2x2.

Дані бере з healthai.analytics() (qwatch/health.json) — жодних вигадок:
чого немає в даних, того немає у звіті. Оцінка дня зберігається в
hcoach_scores.json, щоб можна було будувати динаміку.
"""
import os
import sys
import json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K
import healthai as HA

TAG = "hcoach"

STATE_FILE = "hcoach_state.json"
SCORES_FILE = "hcoach_scores.json"

WEIGHT_GOAL = 78.0
SLEEP_GOAL_H = 7.5
STEPS_GOAL = 8000
HR_OK = 70
WATER_GOAL_L = 2.5


# ─── ДОПОМІЖНЕ ───────────────────────────────────────────────────────────────

def _now():
    return K.now()


def _log(msg):
    K.log(TAG, msg)


def _health():
    try:
        import storage
        return storage.load_health() or {}
    except Exception as e:
        _log("load_health error: " + str(e))
        return {}


def _day_rec(day: str) -> dict:
    rec = _health().get(day) or {}
    return rec if isinstance(rec, dict) else {}


def _shift_for(offset: int = 0) -> str:
    """'early' | 'night' | 'free' — зміна на день (offset у днях)."""
    try:
        return K.classify_shift(K.events_for_day(offset))
    except Exception as e:
        _log("shift error: " + str(e))
        return "free"


_SHIFT_UA = {"early": "☀️ рання 06:00–18:00",
             "night": "🌙 нічна 18:00–06:00",
             "free": "🏠 вихідний"}


def _muted() -> bool:
    try:
        import quiet
        if quiet.blocked("msg"):
            return True
    except Exception:
        pass
    try:
        import dismissed
        if dismissed.is_muted("hcoach") or dismissed.is_muted("healthai"):
            return True
    except Exception:
        pass
    return False


# ─── ОЦІНКА ДНЯ 0-100 ────────────────────────────────────────────────────────

def score_day(day: str = None) -> dict:
    """
    Детермінована оцінка 0-100 з реальних даних. AI сюди не лізе.
      сон      30 балів
      кроки    25
      вага     15 (тренд до цілі)
      пульс    10
      біг/актив 20
    Якщо метрики немає — її бали не враховуються, підсумок нормується
    на суму доступних ваг (щоб відсутність даних не «валила» оцінку).
    """
    day = day or _now().strftime("%Y-%m-%d")
    rec = _day_rec(day)
    parts = {}

    sleep_h = rec.get("sleep_hours")
    if sleep_h is None and rec.get("sleep_total_min") is not None:
        sleep_h = round(rec["sleep_total_min"] / 60.0, 2)
    if sleep_h is not None:
        if sleep_h >= SLEEP_GOAL_H:
            v = 30
        elif sleep_h >= 6.5:
            v = 24
        elif sleep_h >= 5.5:
            v = 15
        else:
            v = 7
        parts["сон"] = (v, 30, str(sleep_h) + " год")

    steps = rec.get("steps")
    if steps is not None:
        ratio = min(1.0, steps / float(STEPS_GOAL))
        parts["кроки"] = (round(25 * ratio), 25, str(steps))

    a = None
    w = rec.get("weight_kg")
    if w is not None:
        try:
            a = HA.analytics(14)
            delta = (a.get("weight") or {}).get("delta")
        except Exception:
            delta = None
        if delta is None:
            v = 8
            note = str(w) + " кг"
        elif delta <= -0.2:
            v = 15
            note = str(w) + " кг (вниз " + str(delta) + ")"
        elif delta < 0.2:
            v = 11
            note = str(w) + " кг (стабільно)"
        else:
            v = 5
            note = str(w) + " кг (вгору +" + str(delta) + ")"
        parts["вага"] = (v, 15, note)

    hr = rec.get("hr_avg")
    if hr is not None:
        if hr <= HR_OK:
            v = 10
        elif hr <= 80:
            v = 7
        elif hr <= 90:
            v = 4
        else:
            v = 1
        parts["пульс"] = (v, 10, str(hr) + " уд/хв")

    km = rec.get("distance_km")
    cal = rec.get("calories")
    if km is not None:
        v = 20 if km >= 5 else (14 if km >= 3 else (8 if km > 0 else 0))
        parts["актив"] = (v, 20, str(km) + " км")
    elif cal is not None:
        v = 20 if cal >= 600 else (13 if cal >= 350 else 6)
        parts["актив"] = (v, 20, str(cal) + " ккал")

    got = sum(p[0] for p in parts.values())
    total = sum(p[1] for p in parts.values())
    score = round(100.0 * got / total) if total else None

    out = {"day": day, "score": score, "parts": parts,
           "covered": total, "shift": _shift_for(0)}
    if score is not None:
        try:
            hist = K.load(SCORES_FILE, default={}) or {}
            if not isinstance(hist, dict):
                hist = {}
            hist[day] = score
            for k in sorted(hist.keys())[:-180]:
                hist.pop(k, None)
            K.save(SCORES_FILE, hist)
        except Exception as e:
            _log("scores save error: " + str(e))
    return out


def _score_bar(score) -> str:
    if score is None:
        return "оцінка: даних замало"
    full = round(score / 10)
    bar = "█" * full + "░" * (10 - full)
    if score >= 85:
        mark = "🟢 відмінно"
    elif score >= 70:
        mark = "🟢 добре"
    elif score >= 55:
        mark = "🟡 середньо"
    elif score >= 40:
        mark = "🟠 слабо"
    else:
        mark = "🔴 провал"
    return bar + "  <b>" + str(score) + "/100</b> — " + mark


def score_history(days: int = 30) -> list:
    hist = K.load(SCORES_FILE, default={}) or {}
    if not isinstance(hist, dict):
        return []
    out = []
    for i in range(days - 1, -1, -1):
        d = (_now().date() - timedelta(days=i)).strftime("%Y-%m-%d")
        if d in hist:
            out.append((d, hist[d]))
    return out


# ─── AI ──────────────────────────────────────────────────────────────────────

_STYLE = (
    "Ти особистий коуч Олега зі здоров'я (37 років, Кошице, змінний графік "
    "06:00-18:00 / 18:00-06:00, ціль — 78 кг). Пиши українською, тепло, "
    "по-людськи, конкретно. Використовуй ТІЛЬКИ факти з блоку даних. "
    "ЗАБОРОНЕНО вигадувати числа. Якщо якогось показника немає — так і скажи "
    "і попроси надіслати. Без води і банальностей."
)


def _ai(prompt: str, tokens: int = 900) -> str:
    try:
        txt = K.gemini_text(prompt, max_tokens=tokens, temperature=0.7, tag=TAG)
        return (txt or "").strip()
    except Exception as e:
        _log("gemini error: " + str(e))
        return ""


def _kb():
    return [
        [{"text": "📊 Цифри", "callback_data": "hc_stats"},
         {"text": "🧠 Поради", "callback_data": "hc_reco"}],
        [{"text": "😴 Сон", "callback_data": "hc_sleep"},
         {"text": "📈 Графік", "callback_data": "hc_chart"}],
        [{"text": "🧬 Повна аналітика", "callback_data": "hc_full"}],
    ]


# ─── 1. РАНКОВИЙ ПЛАН ────────────────────────────────────────────────────────

def morning_plan(send: bool = True) -> str:
    a = HA.analytics(30)
    today = _shift_for(0)
    tomorrow = _shift_for(1)
    yest = (_now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    yrec = _day_rec(yest)

    ctx = [
        "СЬОГОДНІ: " + _now().strftime("%d.%m.%Y (%a)"),
        "ЗМІНА сьогодні: " + _SHIFT_UA.get(today, today),
        "ЗМІНА завтра: " + _SHIFT_UA.get(tomorrow, tomorrow),
        "",
        HA.facts_block(a),
        "",
        "ВЧОРА: сон " + str(yrec.get("sleep_hours") or "—") + " год, кроки "
        + str(yrec.get("steps") or "—") + ", вага " + str(yrec.get("weight_kg") or "—") + " кг",
        "ЦІЛІ: вага " + str(WEIGHT_GOAL) + " кг, сон " + str(SLEEP_GOAL_H)
        + " год, кроки " + str(STEPS_GOAL) + ", вода " + str(WATER_GOAL_L) + " л",
    ]
    prompt = (_STYLE + "\n\nДАНІ:\n" + "\n".join(ctx) + "\n\n"
              "Склади ПЛАН ДНЯ по здоров'ю під цю зміну. Рівно 5 блоків, "
              "кожен 1-2 речення з конкретним часом:\n"
              "😴 СОН — коли лягти сьогодні (враховуй зміну завтра)\n"
              "💧 ВОДА — скільки і коли пити на зміні\n"
              "🍽 ЇЖА — що і о котрій, під ціль 78 кг\n"
              "🏃 РУХ — конкретне вікно для руху в цьому графіку\n"
              "🎯 ФОКУС ДНЯ — одна річ, головна на сьогодні\n"
              "Наприкінці один рядок мотивації. Без вступу.")
    body = _ai(prompt, 1000)
    if not body:
        body = ("😴 СОН — цілься на " + str(SLEEP_GOAL_H) + " год.\n"
                "💧 ВОДА — " + str(WATER_GOAL_L) + " л рівномірно.\n"
                "🍽 ЇЖА — без пізніх вуглеводів.\n"
                "🏃 РУХ — " + str(STEPS_GOAL) + " кроків.\n"
                "🎯 ФОКУС — записати вагу і сон увечері.\n"
                "(AI недоступний — базовий план за цілями)")

    txt = ("🌅 <b>ПЛАН ДНЯ — ЗДОРОВ'Я</b>\n"
           + _SHIFT_UA.get(today, today) + "\n\n" + body)
    if send:
        K.send_card(txt, _kb(), tag=TAG)
        _journal("morning_plan", "ранковий план здоров'я")
    return txt


# ─── 2. ВЕЧІРНІЙ РОЗБІР + ОЦІНКА ─────────────────────────────────────────────

def evening_review(send: bool = True) -> str:
    day = _now().strftime("%Y-%m-%d")
    sc = score_day(day)
    a = HA.analytics(30)

    lines = []
    for name, (got, mx, note) in sc["parts"].items():
        lines.append("• " + name + ": " + note + " → " + str(got) + "/" + str(mx))
    detail = "\n".join(lines) if lines else "• даних за сьогодні ще немає"

    hist = score_history(7)
    avg7 = round(sum(v for _, v in hist) / len(hist)) if hist else None

    ctx = ["ОЦІНКА ДНЯ: " + str(sc["score"]) + "/100" if sc["score"] is not None
           else "ОЦІНКА ДНЯ: даних замало",
           "Розклад балів:\n" + detail,
           "Середня за 7 днів: " + (str(avg7) if avg7 is not None else "—"),
           "Зміна сьогодні: " + _SHIFT_UA.get(sc["shift"], sc["shift"]),
           "Зміна завтра: " + _SHIFT_UA.get(_shift_for(1), "—"),
           "",
           HA.facts_block(a)]
    prompt = (_STYLE + "\n\nДАНІ:\n" + "\n".join(ctx) + "\n\n"
              "Зроби ВЕЧІРНІЙ РОЗБІР ДНЯ:\n"
              "1) Що сьогодні вийшло добре (по цифрах)\n"
              "2) Що просіло і чому саме (по цифрах)\n"
              "3) Рівно 3 конкретні дії на завтра під завтрашню зміну\n"
              "4) Один рядок про сон: о котрій лягати сьогодні\n"
              "Коротко, без вступу, без повтору самих чисел рядок за рядком.")
    body = _ai(prompt, 900) or "AI зараз недоступний — нижче тільки цифри."

    txt = ("🌙 <b>РОЗБІР ДНЯ — ЗДОРОВ'Я</b>\n" + _now().strftime("%d.%m.%Y") + "\n\n"
           + _score_bar(sc["score"]) + "\n\n" + detail + "\n\n" + body)
    if avg7 is not None:
        txt += "\n\n📈 Середня за 7 днів: <b>" + str(avg7) + "/100</b>"
    if send:
        K.send_card(txt, _kb(), tag=TAG)
        _journal("evening_review", "вечірній розбір, оцінка " + str(sc["score"]))
    return txt


# ─── 3. СОН ──────────────────────────────────────────────────────────────────

def sleep_report(send: bool = True) -> str:
    a = HA.analytics(30)
    s = a.get("sleep") or {}
    health = _health()
    rows = []
    for i in range(13, -1, -1):
        d = (_now().date() - timedelta(days=i)).strftime("%Y-%m-%d")
        rec = health.get(d) or {}
        h = rec.get("sleep_hours")
        if h is not None:
            rows.append(d[5:] + ": " + str(h) + " год")
    hist = "\n".join(rows[-10:]) if rows else "даних немає"

    shifts = []
    try:
        sm = K.shift_map(7)
        shifts = [d + " " + _SHIFT_UA.get(v, v) for d, v in sorted(sm.items())]
    except Exception:
        pass

    ctx = ["СОН: останній " + str(s.get("last") or "—") + " год | сер.7д "
           + str(s.get("avg7") or "—") + " | сер.30д " + str(s.get("avg") or "—")
           + " | тренд " + str(s.get("trend") or "—"),
           "Ціль сну: " + str(SLEEP_GOAL_H) + " год",
           "Історія (останні дні):\n" + hist,
           "Зміни на 7 днів вперед:\n" + ("\n".join(shifts) if shifts else "—")]
    prompt = (_STYLE + "\n\nДАНІ:\n" + "\n".join(ctx) + "\n\n"
              "Зроби АНАЛІЗ СНУ під змінний графік:\n"
              "1) Що показують цифри (недосип? нестабільність?)\n"
              "2) Схема сну на РАННЮ зміну: коли лягати/вставати\n"
              "3) Схема сну на НІЧНУ зміну: коли спати вдень, скільки, "
              "як не зламати ритм\n"
              "4) Перехід нічна→вихідний: як відсипатись правильно\n"
              "5) 3 конкретні правила саме для Олега\n"
              "Без загальних фраз про 'гігієну сну'.")
    body = _ai(prompt, 1100) or "AI недоступний."

    txt = ("😴 <b>СОН — АНАЛІЗ І СХЕМА ПІД ЗМІНИ</b>\n\n" + body
           + "\n\n<i>Останні дні:</i>\n" + hist)
    if send:
        K.send_card(txt, _kb(), tag=TAG)
        _journal("sleep_report", "аналіз сну")
    return txt


# ─── 4. ГРАФІКИ ──────────────────────────────────────────────────────────────

def chart(days: int = 30) -> bytes:
    """Графік 2x2: вага, сон, кроки, оцінка дня. b'' якщо нема даних."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        _log("matplotlib недоступний: " + str(e))
        return b""

    health = _health()
    xs, weight, sleep, steps = [], [], [], []
    for i in range(days - 1, -1, -1):
        d = (_now().date() - timedelta(days=i))
        key = d.strftime("%Y-%m-%d")
        rec = health.get(key) or {}
        if not isinstance(rec, dict):
            rec = {}
        xs.append(d)
        weight.append(rec.get("weight_kg"))
        sh = rec.get("sleep_hours")
        if sh is None and rec.get("sleep_total_min") is not None:
            sh = round(rec["sleep_total_min"] / 60.0, 2)
        sleep.append(sh)
        steps.append(rec.get("steps"))

    hist = dict(score_history(days))
    scores = [hist.get(d.strftime("%Y-%m-%d")) for d in xs]

    if not any(v is not None for v in weight + sleep + steps + scores):
        return b""

    def clean(vals):
        px = [x for x, v in zip(xs, vals) if v is not None]
        pv = [v for v in vals if v is not None]
        return px, pv

    fig, ax = plt.subplots(2, 2, figsize=(13, 8))
    fig.patch.set_facecolor("white")
    title = "Здоров'я Олега — " + str(days) + " днів"
    fig.suptitle(title, fontsize=16, fontweight="bold")

    specs = [
        (ax[0][0], weight, "⚖️ Вага, кг", "#e63946", WEIGHT_GOAL, "ціль 78"),
        (ax[0][1], sleep, "😴 Сон, год", "#457b9d", SLEEP_GOAL_H, "ціль 7.5"),
        (ax[1][0], steps, "👣 Кроки", "#2a9d8f", STEPS_GOAL, "ціль 8000"),
        (ax[1][1], scores, "🏅 Оцінка дня", "#f4a261", 70, "добре 70"),
    ]
    for axis, vals, ttl, color, goal, glabel in specs:
        px, pv = clean(vals)
        axis.set_facecolor("#fbfbfb")
        axis.set_title(ttl, fontsize=12, fontweight="bold")
        axis.grid(alpha=0.3)
        if pv:
            axis.plot(px, pv, marker="o", ms=4, lw=2, color=color)
            if len(pv) >= 4:
                win = min(7, len(pv))
                ma = [sum(pv[max(0, i - win + 1):i + 1])
                      / len(pv[max(0, i - win + 1):i + 1]) for i in range(len(pv))]
                axis.plot(px, ma, lw=2, ls="--", color="#ff9f1c", alpha=0.8)
            axis.axhline(goal, color="#888", ls=":", lw=1.5)
            axis.text(0.01, 0.02, glabel, transform=axis.transAxes,
                      fontsize=9, color="#666")
        else:
            axis.text(0.5, 0.5, "немає даних", ha="center", va="center",
                      transform=axis.transAxes, color="#999", fontsize=12)
        axis.tick_params(axis="x", labelrotation=45, labelsize=8)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    import io
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    return buf.getvalue()


def send_chart(days: int = 30, caption: str = "") -> bool:
    png = chart(days)
    if not png:
        K.send_card("📈 Графіка немає — замало даних по здоров'ю.", None, tag=TAG)
        return False
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    try:
        try:
            import requests as _rq
        except ImportError:
            import httpreq as _rq
        r = _rq.post("https://api.telegram.org/bot" + token + "/sendPhoto",
                     data={"chat_id": chat, "caption": caption[:900],
                           "parse_mode": "HTML"},
                     files={"photo": ("health.png", png, "image/png")},
                     timeout=60)
        ok = bool(getattr(r, "ok", False))
        if not ok:
            _log("sendPhoto: " + str(getattr(r, "status_code", "?")))
        return ok
    except Exception as e:
        _log("sendPhoto error: " + str(e))
        return False


# ─── 5. ТИЖНЕВИЙ / МІСЯЧНИЙ ЗВІТ ─────────────────────────────────────────────

def _period_report(days: int, title: str, tag: str, send: bool) -> str:
    a = HA.analytics(days)
    hist = score_history(days)
    avg = round(sum(v for _, v in hist) / len(hist)) if hist else None
    best = max(hist, key=lambda x: x[1]) if hist else None
    worst = min(hist, key=lambda x: x[1]) if hist else None

    extra = []
    if avg is not None:
        extra.append("Середня оцінка: " + str(avg) + "/100 (днів з оцінкою: "
                     + str(len(hist)) + ")")
    if best:
        extra.append("Найкращий день: " + best[0] + " — " + str(best[1]))
    if worst:
        extra.append("Найгірший день: " + worst[0] + " — " + str(worst[1]))
    try:
        sm = K.shift_map(7)
        n_night = sum(1 for v in sm.values() if v == "night")
        extra.append("Нічних змін попереду (7 днів): " + str(n_night))
    except Exception:
        pass

    facts = HA.facts_block(a) + ("\n" + "\n".join(extra) if extra else "")
    prompt = (_STYLE + "\n\nДАНІ ЗА " + str(days) + " ДНІВ:\n" + facts + "\n\n"
              "Напиши " + title + ":\n"
              "1) Головний висновок періоду одним абзацом\n"
              "2) Динаміка: що покращилось, що погіршилось (по цифрах)\n"
              "3) Що саме заважає дійти до 78 кг — конкретно\n"
              "4) План на наступний період: 3 пункти з числами\n"
              "5) Одне питання до Олега наприкінці")
    body = _ai(prompt, 1300) or "AI недоступний — нижче факти."

    txt = ("📊 <b>" + title.upper() + "</b>\n" + _now().strftime("%d.%m.%Y")
           + "\n\n" + body + "\n\n<b>ЦИФРИ</b>\n" + facts)
    if send:
        K.send_card(txt, _kb(), tag=TAG)
        send_chart(days, "📈 Здоров'я за " + str(days) + " днів")
        _journal(tag, title)
    return txt


def full_report(send: bool = True) -> str:
    """
    ПОВНА АНАЛІТИКА ТРЕКІНГУ: усі дані, що є в базі, за весь доступний період.
    Результати + динаміка + прогноз до цілі + AI-аналіз + рекомендації + графік.
    """
    health = _health()
    all_days = sorted(d for d in health.keys() if len(str(d)) == 10)
    span = len(all_days)
    depth = max(30, min(180, span or 30))
    a = HA.analytics(depth)

    # покриття по кожній метриці за весь період
    fields = [("weight_kg", "Вага"), ("sleep_hours", "Сон"), ("steps", "Кроки"),
              ("hr_avg", "Пульс"), ("hrv", "HRV"), ("calories", "Калорії"),
              ("distance_km", "Дистанція")]
    cover = []
    for key, label in fields:
        n = sum(1 for d in all_days
                if isinstance(health.get(d), dict) and health[d].get(key) is not None)
        pct = round(100.0 * n / span) if span else 0
        cover.append(label + ": " + str(n) + " з " + str(span) + " днів (" + str(pct) + "%)")

    # прогноз досягнення 78 кг за фактичним темпом
    forecast = "Прогноз по вазі: даних для темпу замало"
    w = a.get("weight") or {}
    if w.get("last") is not None and w.get("delta") is not None and w["delta"] < -0.05:
        per_day = abs(w["delta"]) / max(1, depth / 2.0)
        left = w["last"] - WEIGHT_GOAL
        if left > 0 and per_day > 0:
            days_left = int(left / per_day)
            eta = (_now().date() + timedelta(days=days_left)).strftime("%d.%m.%Y")
            forecast = ("Прогноз по вазі: темп " + str(round(per_day * 7, 2))
                        + " кг/тиждень → 78 кг близько " + eta
                        + " (" + str(days_left) + " днів)")
    elif w.get("last") is not None and w.get("delta") is not None:
        forecast = ("Прогноз по вазі: за поточним трендом (" + str(w["delta"])
                    + ") ціль 78 кг НЕ наближається")

    hist = score_history(depth)
    scores_line = "Оцінка дня: даних ще немає"
    if hist:
        vals = [v for _, v in hist]
        avg = round(sum(vals) / len(vals))
        scores_line = ("Оцінка дня: сер. " + str(avg) + "/100 | найкраща "
                       + str(max(vals)) + " | найгірша " + str(min(vals))
                       + " | днів з оцінкою " + str(len(vals)))

    anom = []
    try:
        anom = [t for _, t in HA.anomalies(a)]
    except Exception as e:
        _log("anomalies error: " + str(e))

    facts = "\n".join([
        "ПЕРІОД: " + (all_days[0] + " → " + all_days[-1] if all_days else "даних немає")
        + " (" + str(span) + " днів у базі, глибина аналізу " + str(depth) + ")",
        "",
        HA.facts_block(a),
        "",
        "ПОКРИТТЯ ДАНИХ:",
        "\n".join(cover),
        "",
        forecast,
        scores_line,
    ])
    if anom:
        facts += "\n\nВИЯВЛЕНІ ВІДХИЛЕННЯ:\n" + "\n".join("• " + x for x in anom)

    prompt = (_STYLE + "\n\nПОВНІ ДАНІ ТРЕКІНГУ:\n" + facts + "\n\n"
              "Зроби ПОВНИЙ АНАЛІТИЧНИЙ ЗВІТ як особистий коуч-аналітик:\n"
              "1) 📌 ГОЛОВНЕ — стан здоров'я одним абзацом, честно\n"
              "2) 📈 ЩО ПОКАЗУЮТЬ ЦИФРИ — по кожній метриці, де є дані: "
              "що добре, що погано, чому\n"
              "3) 🔗 ЗВ'ЯЗКИ — як сон/кроки/пульс впливають на вагу за цими даними\n"
              "4) ⚠️ РИЗИКИ — на що звернути увагу зараз\n"
              "5) 🎯 РЕКОМЕНДАЦІЇ — 5 конкретних дій з числами і термінами\n"
              "6) 🕳 ДІРИ В ДАНИХ — чого не хватає для точнішого аналізу\n"
              "Пиши предметно, з числами з даних. Без загальних порад.")
    body = _ai(prompt, 1800) or "AI недоступний — нижче лише факти."

    txt = ("🧬 <b>ПОВНА АНАЛІТИКА ЗДОРОВ'Я</b>\n" + _now().strftime("%d.%m.%Y %H:%M")
           + "\n\n" + body + "\n\n<b>ФАКТИ</b>\n" + facts)
    if send:
        K.send_card(txt, _kb(), tag=TAG)
        send_chart(depth, "📈 Трекінг здоров'я за " + str(depth) + " днів")
        _journal("full_report", "повна аналітика здоров'я")
    return txt


def weekly_report(send: bool = True) -> str:
    return _period_report(7, "Тижневий звіт по здоров'ю", "weekly", send)


def monthly_report(send: bool = True) -> str:
    return _period_report(30, "Місячний звіт по здоров'ю", "monthly", send)


# ─── ЖУРНАЛ ──────────────────────────────────────────────────────────────────

def _journal(kind, what, detail=""):
    try:
        import selfact
        selfact.journal(TAG, kind, what, detail)
    except Exception:
        pass


# ─── ПЛАНУВАЛЬНИК ────────────────────────────────────────────────────────────

_SLOTS = {
    "morning": (7, 0, morning_plan),
    "evening": (21, 20, evening_review),
    "sleep": (14, 40, sleep_report),
}


def tick() -> str:
    """Викликається щохвилини з monitor_loop. Дедуп — за днем у стані."""
    now = _now()
    day = now.strftime("%Y-%m-%d")
    state = K.load(STATE_FILE, default={}) or {}
    if not isinstance(state, dict):
        state = {}
    done = []

    # оцінка дня рахується постійно (щоб історія була навіть без звіту)
    try:
        if now.minute % 30 == 0:
            score_day(day)
    except Exception as e:
        _log("score_day error: " + str(e))

    for name, (h, m, fn) in _SLOTS.items():
        if now.hour == h and m <= now.minute < m + 6 and state.get(name) != day:
            if _muted():
                continue
            # аналіз сну — тільки по середах, щоб не набридати
            if name == "sleep" and now.weekday() != 2:
                continue
            try:
                fn(send=True)
                state[name] = day
                done.append(name)
            except Exception as e:
                _log(name + " error: " + str(e))

    if (now.weekday() == 6 and now.hour == 19 and 30 <= now.minute < 36
            and state.get("weekly") != day and not _muted()):
        try:
            weekly_report(send=True)
            state["weekly"] = day
            done.append("weekly")
        except Exception as e:
            _log("weekly error: " + str(e))

    if (now.day == 1 and now.hour == 10 and 0 <= now.minute < 6
            and state.get("monthly") != day and not _muted()):
        try:
            monthly_report(send=True)
            state["monthly"] = day
            done.append("monthly")
        except Exception as e:
            _log("monthly error: " + str(e))

    if done:
        K.save(STATE_FILE, state)
    return ", ".join(done)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    if cmd == "morning":
        print(morning_plan(send=False))
    elif cmd == "evening":
        print(evening_review(send=False))
    elif cmd == "sleep":
        print(sleep_report(send=False))
    elif cmd == "weekly":
        print(weekly_report(send=False))
    elif cmd == "monthly":
        print(monthly_report(send=False))
    else:
        print(json.dumps(score_day(), ensure_ascii=False, indent=2))
