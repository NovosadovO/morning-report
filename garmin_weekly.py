#!/usr/bin/env python3
"""
garmin_weekly.py — тижневе Garmin-зведення: 7 днів проти попередніх 7.

Чому окремо від garmin_insight: той ловить ОДНУ аномалію дня, а тут — картина
тижня: сон, HRV, пульс спокою, стрес, кроки, біг (з run_insight.runs), вага.
Усі числа рахує код; Gemini лише пише висновок і ОДНЕ питання з кнопками, що
відповідають саме цьому питанню. Графік — 6 панелей за 14 днів із середніми
по тижнях.

Коли: неділя з 18:00 за Кошице; якщо пропущено — до понеділка 13:00. Раз на
ISO-тиждень. Нема свіжих даних (останній замір старший за вчора) або менше
4 днів у тижні — мовчить із причиною (/статус_ai). Тиша/mute поважаються.
Callback: gw_<pid>_<i>, gw_skip_<pid>.
"""
import io
import os
import re
import statistics
from datetime import datetime, timedelta

import ai_kit as K

TAG = "garmin_weekly"
STATE_FILE = "garmin_weekly_state.json"
STORE_FILE = "garmin_weekly_store.json"
MIN_DAYS = 4

_store = K.PayloadStore(STORE_FILE)

# поле -> (назва, одиниця, +1 вище=гірше, -1 нижче=гірше)
METRICS = {
    "sleep_hours": ("Сон", "год", -1),
    "hrv": ("HRV", "мс", -1),
    "hr_avg": ("Пульс спокою", "уд/хв", +1),
    "stress": ("Стрес", "", +1),
    "steps": ("Кроки", "", -1),
    "body_battery": ("Body Battery", "/100", -1),
}


def _log(m):
    K.log(TAG, m)


def _why(t):
    try:
        import watch_health
        watch_health.why(TAG, t)
    except Exception:
        pass


def _local_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Bratislava")).replace(tzinfo=None)
    except Exception:
        return K.now().replace(tzinfo=None) + timedelta(hours=1)


def due(now: datetime, last_week: str) -> str:
    """'' якщо можна слати, інакше причина. last_week — ISO-тиждень останньої відправки."""
    iso = now.isocalendar()
    wk = f"{iso[0]}-W{iso[1]:02d}"
    if last_week == wk:
        return "цього тижня вже було"
    if now.weekday() == 6 and now.hour >= 18:
        return ""
    if now.weekday() == 0 and now.hour < 13:
        # наздоганяємо неділю, яку було пропущено
        prev = (now - timedelta(days=1)).isocalendar()
        if last_week != f"{prev[0]}-W{prev[1]:02d}":
            return ""
        return "неділю вже відправлено"
    return "не час (неділя з 18:00)"


def _health() -> dict:
    try:
        import garmin_insight
        return garmin_insight._health()
    except Exception as e:
        _log("health: " + str(e))
        return {}


def _val(rec, field):
    v = rec.get(field)
    if v is None and field == "sleep_hours" and rec.get("sleep_total_min"):
        v = float(rec["sleep_total_min"]) / 60.0
    try:
        f = float(v)
    except Exception:
        return None
    return f if f > 0 else None


def _daily(health: dict, field: str, start, end) -> dict:
    out = {}
    d = start
    while d <= end:
        k = d.strftime("%Y-%m-%d")
        rec = health.get(k)
        if rec:
            v = _val(rec, field)
            if v is not None:
                out[k] = v
        d += timedelta(days=1)
    return out


def compute(health: dict, today=None) -> dict:
    """{'rows':[...], 'fresh_day':'YYYY-MM-DD', 'weight':{...}} — тільки реальні числа."""
    today = today or K.now().date()
    w_end = today
    w_start = today - timedelta(days=6)
    p_end = today - timedelta(days=7)
    p_start = today - timedelta(days=13)
    rows = []
    latest = ""
    for field, (name, unit, sign) in METRICS.items():
        cur = _daily(health, field, w_start, w_end)
        prev = _daily(health, field, p_start, p_end)
        if cur:
            latest = max(latest, max(cur))
        # сьогодні неповні кроки/стрес/BB — не рахуємо в середнє
        if field in ("steps", "stress", "body_battery"):
            cur.pop(today.strftime("%Y-%m-%d"), None)
        if len(cur) < MIN_DAYS or len(prev) < MIN_DAYS:
            continue
        a, b = statistics.mean(cur.values()), statistics.mean(prev.values())
        ch = ((a - b) / b * 100.0) if b else 0.0
        rows.append({"field": field, "name": name, "unit": unit, "cur": round(a, 2),
                     "prev": round(b, 2), "pct": round(ch, 1), "n": len(cur),
                     "worse": (ch * sign) > 0 and abs(ch) >= 3, "better": (ch * sign) < 0 and abs(ch) >= 3,
                     "series": _daily(health, field, p_start, w_end)})
    wts = _daily(health, "weight_kg", p_start, w_end)
    weight = {}
    if len(wts) >= 2:
        ks = sorted(wts)
        weight = {"first_day": ks[0], "first": wts[ks[0]], "last_day": ks[-1], "last": wts[ks[-1]]}
    return {"rows": rows, "fresh_day": latest, "weight": weight}


def run_stats() -> dict:
    try:
        import run_insight
        rs = run_insight.runs(days=30)
    except Exception as e:
        _log("runs: " + str(e))
        return {}
    now = K.now().replace(tzinfo=None)
    cur = [r for r in rs if r["date"] > now - timedelta(days=7)]
    prev = [r for r in rs if now - timedelta(days=14) < r["date"] <= now - timedelta(days=7)]

    def agg(L):
        if not L:
            return {"n": 0, "km": 0.0, "pace": None}
        km = sum(r["km"] for r in L)
        sec = sum(r["dur"] for r in L)
        return {"n": len(L), "km": round(km, 1), "pace": round(sec / km) if km else None}
    return {"cur": agg(cur), "prev": agg(prev)}


def facts_text(data: dict, runs: dict) -> str:
    L = []
    for r in data["rows"]:
        u = (" " + r["unit"]) if r["unit"] else ""
        L.append(f"{r['name']}: цей тиждень {r['cur']:g}{u} (n={r['n']}), минулий {r['prev']:g}{u}, {r['pct']:+.1f}%"
                 + (" — гірше" if r["worse"] else " — краще" if r["better"] else " — без змін"))
    if data["weight"]:
        w = data["weight"]
        L.append(f"Вага: {w['first']:g} кг ({w['first_day'][5:]}) → {w['last']:g} кг ({w['last_day'][5:]}); ціль 75")
    if runs:
        c, p = runs["cur"], runs["prev"]
        from run_insight import fmt_pace
        L.append(f"Біг: {c['n']} пробіжок, {c['km']:g} км" + (f", темп {fmt_pace(c['pace'])}/км" if c["pace"] else "")
                 + f"; минулий тиждень {p['n']} пробіжок, {p['km']:g} км"
                 + (f", темп {fmt_pace(p['pace'])}/км" if p["pace"] else ""))
    return "\n".join(L)


def chart(data: dict) -> bytes:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except Exception as e:
        _log("matplotlib: " + str(e))
        return b""
    rows = data["rows"][:6]
    if len(rows) < 2:
        return b""
    ncol = 3
    nrow = (len(rows) + ncol - 1) // ncol
    fig, axes = plt.subplots(nrow, ncol, figsize=(11, 3.2 * nrow), dpi=100)
    fig.patch.set_facecolor("#0D1117")
    axes = [axes] if nrow * ncol == 1 else list(getattr(axes, "flat", axes))
    split = None
    for ax, r in zip(axes, rows):
        ax.set_facecolor("#161B22")
        s = r["series"]
        xs = [datetime.strptime(d, "%Y-%m-%d") for d in s]
        ys = list(s.values())
        col = "#F85149" if r["worse"] else "#3FB950" if r["better"] else "#58A6FF"
        ax.plot(xs, ys, color="#8B949E", lw=1.4, marker="o", ms=3)
        if xs:
            mid = K.now().replace(tzinfo=None).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6.5)
            ax.axvline(mid, color="#30363D", lw=1, ls=":")
            ax.hlines(r["prev"], xs[0], mid, color="#8B949E", lw=1.5, ls="--")
            ax.hlines(r["cur"], mid, xs[-1] + timedelta(hours=8), color=col, lw=2)
        u = (" " + r["unit"]) if r["unit"] else ""
        ax.set_title(f"{r['name']}: {r['cur']:g}{u} ({r['pct']:+.0f}%)", color=col, fontsize=11, loc="left")
        ax.tick_params(colors="#8B949E", labelsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
        for sp in ax.spines.values():
            sp.set_color("#30363D")
        ax.grid(color="#30363D", lw=0.5, alpha=0.6)
    for ax in axes[len(rows):]:
        ax.axis("off")
    fig.suptitle("Garmin: останні 7 днів проти попередніх 7 (пунктир — минулий тиждень, лінія — цей)",
                 color="#E6EDF3", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


_PROMPT = """Ти особистий асистент Олега (Кошице, нічні/ранні зміни, ціль 75 кг, біг).
Нижче РЕАЛЬНІ тижневі підсумки з його Garmin (цей тиждень проти минулого):
{facts}

Завдання (українською, без привітання і кліше, одразу до суті):
1. "text": 4-6 коротких речень HTML (<b> для 2-3 ключових чисел): головна зміна тижня,
   що з неї випливає для відновлення і бігу, 2 конкретні дії на наступний тиждень.
   Опирайся ТІЛЬКИ на числа вище; якщо причини в даних нема — так і скажи.
2. "question": одне коротке питання, яке допоможе зрозуміти причину головної зміни.
3. "options": 2-4 варіанти відповіді, що ПРЯМО відповідають на це питання
   (не «Так/Ні», якщо питання не закрите; до 28 символів; це лише запис відповіді).
Не вигадуй чисел. Формат — ТІЛЬКИ JSON: {{"text":"...","question":"...","options":["..",".."]}}"""


def build(facts: str) -> dict:
    data = K.gemini_json(_PROMPT.format(facts=facts), max_tokens=900, temperature=0.8,
                         tag="garmin_weekly", want="dict")
    if not isinstance(data, dict) or not data.get("text"):
        return {}
    opts = [str(o).strip()[:28] for o in (data.get("options") or []) if str(o).strip()][:4]
    return {"text": str(data["text"]).strip(), "question": str(data.get("question") or "").strip(),
            "options": opts}


def _send_photo(png: bytes, caption: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat and png):
        return False
    try:
        import requests as _rq
        r = _rq.post("https://api.telegram.org/bot" + token + "/sendPhoto",
                     data={"chat_id": chat, "caption": caption[:1000], "parse_mode": "HTML"},
                     files={"photo": ("week.png", png, "image/png")}, timeout=60)
        return getattr(r, "status_code", 0) == 200
    except Exception as e:
        _log("sendPhoto: " + str(e))
        return False


def run(force: bool = False) -> bool:
    now = _local_now()
    st = K.load(STATE_FILE, default={}) or {}
    if not force:
        try:
            import quiet
            if quiet.blocked("msg"):
                _why("тиха година / режим сну")
                return False
            import dismissed
            if dismissed.is_muted("hcoach") or dismissed.is_muted("healthai"):
                _why("заглушено (dismissed: hcoach/healthai)")
                return False
        except Exception:
            pass
        r = due(now, st.get("week", ""))
        if r:
            _why(r)
            return False
    try:
        import garmin_fresh
        garmin_fresh.ensure_fresh()
    except Exception as e:
        _log("ensure_fresh: " + str(e))
    health = _health()
    data = compute(health)
    if not data["fresh_day"]:
        _why("у Garmin-даних нема жодного заміру за 7 днів")
        return False
    try:
        age = (K.now().date() - datetime.strptime(data["fresh_day"], "%Y-%m-%d").date()).days
    except Exception:
        age = 99
    if age > 1:
        _why(f"останній замір {data['fresh_day']} — застарілий (свіжі дані важливіші за вчасність)")
        return False
    if len(data["rows"]) < 3:
        _why(f"замало метрик з ≥{MIN_DAYS} днями в обох тижнях ({len(data['rows'])})")
        return False
    runs = run_stats()
    facts = facts_text(data, runs)
    built = build(facts)
    if not built:
        _why("AI не склав зведення")
        return False
    png = chart(data)
    iso = now.isocalendar()
    head = f"📊 <b>Garmin за тиждень</b> · {(now - timedelta(days=6)).strftime('%d.%m')}–{now.strftime('%d.%m')}"
    pid = _store.put({"q": built["question"], "opts": built["options"], "facts": facts[:900],
                      "week": f"{iso[0]}-W{iso[1]:02d}"})
    kb = None
    if built["question"] and built["options"]:
        kb = [[{"text": o, "callback_data": f"gw_{pid}_{i}"}] for i, o in enumerate(built["options"])]
        kb.append([{"text": "🙈 Не питай про це", "callback_data": f"gw_skip_{pid}"}])
    body = built["text"] + (("\n\n❓ <b>" + K.esc(built["question"]) + "</b>") if built["question"] else "")
    ok = True
    if png:
        ok = _send_photo(png, head)
    if ok:
        ok = K.send_card((head + "\n\n" if not png else "") + body, kb, tag=TAG)
    if not ok:
        _store.drop(pid)
        _why("відправка не вдалась / відсіяна outbox")
        return False
    K.topic_mark("health")
    st["week"] = f"{iso[0]}-W{iso[1]:02d}"
    st["sent_at"] = K.now().isoformat(timespec="seconds")
    K.save(STATE_FILE, st)
    _log("✅ тижневе зведення " + st["week"])
    return True


def handle(data: str) -> dict:
    m = re.match(r"^gw_skip_(\w+)$", data)
    if m:
        _store.drop(m.group(1))
        return {"ok": True, "text": "Ок, це питання закрито."}
    m = re.match(r"^gw_(\w+)_(\d)$", data)
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
        ai_brain.remember_answer(f"Тиждень {p.get('week')}: {p.get('q')}", opts[i],
                                 topic="health", source="button")
    except Exception as e:
        _log("remember: " + str(e))
    _store.drop(m.group(1))
    return {"ok": True, "text": f"Записав: {opts[i]}"}


if __name__ == "__main__":
    import sys
    if "--dry" in sys.argv:
        d = compute(_health())
        print(facts_text(d, run_stats()))
    else:
        print("надіслано:", run(force=True))
