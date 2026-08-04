#!/usr/bin/env python3
"""
РАНКОВЕ РІШЕННЯ «ЯК ТИ СЬОГОДНІ»  (Час/енергія #1)

Бот сам питає один раз на день, у правильне вікно під зміну:
      😴 Втома   ·   😐 Норма   ·   💪 Сила

Після натискання AI бере РЕАЛЬНІ дані (зміна сьогодні з календаря, події дня,
сон і кроки з health.json, вага, план бігу, неоплачені рахунки, найближчі
дедлайни) і перебудовує план дня під заявлену енергію:

  😴 Втома → мінімум обов'язкового, біг знімається або на прогулянку,
             акцент на сон і відновлення
  😐 Норма → базовий план + 1 корисна дія
  💪 Сила  → додає біг/силове, важливу задачу і крок до цілі

Кнопки під планом: [📅 Поставити блоки в календар] [📝 В нотатки] [❌]
Енергія пишеться в day_mode.json — з часом видно, як зміни впливають на стан.

Вікна питання (за зміною з календаря):
  рання зміна  → 04:55-05:45 (перед виїздом)
  нічна зміна  → 14:30-16:45 (після сну, до виїзду 17:30)
  вільний день → 08:00-10:59

Callback-префікси: dm_low_ / dm_ok_ / dm_high_ / dm_apply_ / dm_note_ / dm_skip_
"""

import re
import json
from datetime import datetime, timedelta

import ai_kit as K

TAG = "day_mode"

LOG_FILE = "day_mode.json"            # {YYYY-MM-DD: {energy, ts, shift}}
STORE_FILE = "day_mode_store.json"
ASK_FILE = "day_mode_asked.json"      # антидубль питання

_store = K.PayloadStore(STORE_FILE)

ENERGY = {
    "low": {"icon": "😴", "label": "Втома", "rule":
            "Енергії мало. Прибери все необов'язкове. Біг НЕ ставити (максимум "
            "20-30 хв спокійної прогулянки). Головне — сон і відновлення, "
            "1 обов'язкова справа, не більше."},
    "ok": {"icon": "😐", "label": "Норма", "rule":
           "Енергія звичайна. Базовий план дня + 1 корисна дія на ціль "
           "(біг за планом якщо вікно дозволяє, або 20 хв на інвестиції/навчання)."},
    "high": {"icon": "💪", "label": "Сила", "rule":
             "Енергії багато. Додай тренування (біг за планом, темпова робота), "
             "одну важку задачу, яку давно відкладав, і крок до великої цілі "
             "(інвестиції / нова робота)."},
}


# ─── КОНТЕКСТ ────────────────────────────────────────────────────────────────

def _shift_today() -> str:
    try:
        return K.classify_shift(K.events_for_day(0))
    except Exception:
        return "free"


def _events_text() -> str:
    try:
        evs = K.events_for_day(0)
    except Exception:
        return "календар недоступний"
    if not evs:
        return "подій на сьогодні немає"
    out = []
    for e in evs[:8]:
        t = str(e.get("start") or e.get("time") or "")
        m = re.search(r"(\d{2}:\d{2})", t)
        out.append(f"{m.group(1) if m else '—'} {str(e.get('summary'))[:60]}")
    return "; ".join(out)


def _sleep_steps() -> str:
    h = K.load("health.json", default={}) or {}
    days = sorted(k for k in h.keys() if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)))
    if not days:
        return "даних про сон і кроки немає"
    rec = h.get(days[-1]) or {}
    if not isinstance(rec, dict):
        return "даних про сон і кроки немає"
    parts = []
    if rec.get("sleep_hours"):
        parts.append(f"сон {rec['sleep_hours']} год")
    if rec.get("steps"):
        parts.append(f"кроки {rec['steps']}")
    return f"({days[-1]}) " + ", ".join(parts) if parts else "даних про сон і кроки немає"


def _weight_text() -> str:
    w = K.load("weight.json", default={}) or {}
    pts = sorted((k, v) for k, v in w.items()
                 if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)))
    for d, v in reversed(pts):
        val = v.get("weight") if isinstance(v, dict) else v
        try:
            f = float(val)
        except Exception:
            continue
        return f"{f} кг ({d}), до цілі 78 кг ще {round(f - 78, 1)} кг"
    return "вага не записана"


def _run_window() -> str:
    """Дозволене вікно бігу на сьогодні — за тими самими правилами, що run_planner."""
    sh = _shift_today()
    if sh == "early":
        return "після ранньої зміни — 19:00, 30-45 хв"
    if sh == "night":
        return "перед нічною — 15:00, тільки короткий 25-30 хв"
    return "вільний день — 09:30, можна довгий"


def _bills_text() -> str:
    try:
        import bills_watcher as B
        unpaid = [b for b in (B.load_bills() or {}).values() if not b.get("paid")]
        if not unpaid:
            return "неоплачених рахунків немає"
        s = sum(B._amount_f(b.get("amount")) for b in unpaid)
        soon = sorted((b for b in unpaid if b.get("due")), key=lambda x: str(x["due"]))
        head = f"{len(unpaid)} неоплачених на {s:.2f} EUR"
        if soon:
            head += f"; найближчий {soon[0].get('vendor')} до {soon[0].get('due')}"
        return head
    except Exception:
        return "рахунки недоступні"


def _deadlines_text() -> str:
    try:
        import deadlines_watcher as D
        items = [(D._days_left(v.get("deadline")), v)
                 for v in (D.load_items() or {}).values() if not v.get("done")]
        items = [(l, v) for l, v in items if l is not None and 0 <= l <= 21]
        if not items:
            return "найближчих дедлайнів немає"
        items.sort(key=lambda x: x[0])
        return "; ".join(f"{v.get('title')} через {l} дн." for l, v in items[:3])
    except Exception:
        return "дедлайни недоступні"


def collect() -> dict:
    return {
        "shift": _shift_today(),
        "events": _events_text(),
        "health": _sleep_steps(),
        "weight": _weight_text(),
        "run_window": _run_window(),
        "bills": _bills_text(),
        "deadlines": _deadlines_text(),
    }


# ─── ПИТАННЯ ─────────────────────────────────────────────────────────────────

def is_time() -> bool:
    """Правильне вікно під зміну сьогодні."""
    n = K.now()
    sh = _shift_today()
    if sh == "early":
        return (n.hour == 4 and n.minute >= 55) or (n.hour == 5 and n.minute <= 45)
    if sh == "night":
        return (n.hour == 14 and n.minute >= 30) or (15 <= n.hour < 16) or \
               (n.hour == 16 and n.minute <= 45)
    return 8 <= n.hour < 11


def asked_today() -> bool:
    log = K.load(ASK_FILE, default={}) or {}
    return log.get(K.today_str()) is not None


def answered_today() -> bool:
    log = K.load(LOG_FILE, default={}) or {}
    return isinstance(log.get(K.today_str()), dict)


def ask(force: bool = False) -> bool:
    """Надсилає питання про самопочуття (один раз на день)."""
    if not force and (asked_today() or answered_today()):
        return False
    if not force and not is_time():
        return False

    sh = _shift_today()
    pid = _store.put({"shift": sh, "date": K.today_str()})
    head = {"early": "☀️ Сьогодні рання зміна (06:00–18:00)",
            "night": "🌙 Сьогодні нічна зміна (17:30–06:00)",
            "free": "🏖 Сьогодні вільний день"}.get(sh, "")
    text = (f"⚡ <b>ЯК ТИ СЬОГОДНІ?</b>\n━━━━━━━━━━━━━━━━━━━━\n{head}\n\n"
            f"Скажи одним дотиком — і я перебудую план дня під твій реальний стан, "
            f"а не під ідеальний.")
    kb = [[{"text": "😴 Втома", "callback_data": f"dm_low_{pid}"},
           {"text": "😐 Норма", "callback_data": f"dm_ok_{pid}"},
           {"text": "💪 Сила", "callback_data": f"dm_high_{pid}"}]]
    ok = K.send_card(text, kb, tag=TAG)
    if ok:
        K.update_key(ASK_FILE, K.today_str(), K.now().isoformat())
        K.log(TAG, f"✅ питання надіслано (зміна={sh})")
        _store.gc(days=10)
    else:
        _store.drop(pid)
    return ok


# ─── ПЛАН ────────────────────────────────────────────────────────────────────

_PROMPT = """Ти — особистий коуч Олега (Кошице, Minebea Mitsumi, змінний графік;
цілі: вага 78 кг, регулярний біг, фінансова незалежність, робота в інвестиціях).

Олег щойно сказав, що його стан сьогодні: {energy_label} ({energy_icon}).
ПРАВИЛО ДЛЯ ЦЬОГО СТАНУ: {energy_rule}

РЕАЛЬНІ ДАНІ НА СЬОГОДНІ ({today}, {weekday}):
🏭 Зміна: {shift_h}
📅 Події в календарі: {events}
😴 Сон і кроки: {health}
⚖️ Вага: {weight}
🏃 Дозволене вікно бігу: {run_window}
💸 Рахунки: {bills}
📄 Дедлайни: {deadlines}

ПРАВИЛА:
1. Оперуй ТІЛЬКИ цими даними. Немає даних — так і скажи, не вигадуй.
2. НЕ порушуй графік зміни і вікно бігу. Після нічної зміни вранці — сон,
   ніяких тренувань. Не плануй нічого на години, коли Олег на роботі.
3. Тон: теплий, людяний, підтримуючий. Звертайся «Олеже». З емодзі.
4. Обсяг тексту: 200-300 слів. Живо, конкретно, без загальних фраз.
5. blocks — 2-4 конкретні блоки часу на СЬОГОДНІ у вільні години
   (час у форматі HH:MM, тривалість у хвилинах). Якщо стан «Втома» —
   не більше 2 блоків і без тренувань.

Формат — ТІЛЬКИ валідний JSON без markdown:
{{"text":"текст плану з емодзі-заголовками та переносами рядків",
  "blocks":[{{"time":"19:00","dur":40,"title":"🏃 Легкий біг 5 км",
              "why":"вікно після зміни, стан дозволяє"}}]}}"""


def _local_plan(energy: str, d: dict) -> dict:
    """Чесний план без AI — на самих даних."""
    e = ENERGY[energy]
    lines = [f"{e['icon']} <b>ПЛАН ДНЯ — {e['label'].upper()}</b>",
             "━━━━━━━━━━━━━━━━━━━━",
             f"🏭 Зміна: {K.esc({'early': 'рання 06:00–18:00', 'night': 'нічна 17:30–06:00', 'free': 'вільний день'}.get(d['shift'], d['shift']))}",
             f"📅 {K.esc(d['events'])}",
             f"😴 {K.esc(d['health'])}",
             f"⚖️ {K.esc(d['weight'])}",
             f"🏃 {K.esc(d['run_window'])}",
             f"💸 {K.esc(d['bills'])}",
             f"📄 {K.esc(d['deadlines'])}",
             "", "<i>Gemini недоступний — даю факти без інтерпретації, "
                 "щоб нічого не вигадати.</i>"]
    blocks = []
    if energy == "high" and d["shift"] != "night":
        t = "19:00" if d["shift"] == "early" else "09:30"
        blocks.append({"time": t, "dur": 45, "title": "🏃 Біг за планом",
                       "why": "стан дозволяє, вікно вільне"})
    if energy == "ok" and d["shift"] == "free":
        blocks.append({"time": "09:30", "dur": 40, "title": "🏃 Спокійний біг",
                       "why": "вільний день, базовий обсяг"})
    if energy == "low":
        blocks.append({"time": "21:00" if d["shift"] != "night" else "14:00",
                       "dur": 30, "title": "🚶 Прогулянка замість тренування",
                       "why": "енергії мало — відновлення важливіше"})
    return {"text": "\n".join(lines), "blocks": blocks, "ai": False}


def build(energy: str) -> dict:
    if energy not in ENERGY:
        return {"ok": False, "error": "bad_energy"}
    d = collect()
    e = ENERGY[energy]
    wd = ["понеділок", "вівторок", "середа", "четвер", "п'ятниця",
          "субота", "неділя"][K.now().weekday()]
    shift_h = {"early": "рання 06:00–18:00", "night": "нічна 17:30–06:00",
               "free": "вільний день"}.get(d["shift"], d["shift"])

    out = K.gemini_json(_PROMPT.format(
        energy_label=e["label"], energy_icon=e["icon"], energy_rule=e["rule"],
        today=K.today_str(), weekday=wd, shift_h=shift_h, events=d["events"],
        health=d["health"], weight=d["weight"], run_window=d["run_window"],
        bills=d["bills"], deadlines=d["deadlines"]),
        max_tokens=1800, temperature=0.7, tag=TAG, want="dict")

    if isinstance(out, dict) and out.get("text"):
        blocks = [b for b in (out.get("blocks") or [])
                  if isinstance(b, dict) and b.get("title")
                  and re.match(r"^\d{1,2}:\d{2}$", str(b.get("time") or ""))]
        res = {"text": str(out["text"])[:3000], "blocks": blocks[:4], "ai": True}
    else:
        res = _local_plan(energy, d)

    res.update({"ok": True, "energy": energy, "shift": d["shift"]})
    return res


def _fmt(res) -> str:
    e = ENERGY[res["energy"]]
    txt = res["text"]
    txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)
    txt = re.sub(r"(?<!<)\*(?!\*)", "•", txt)
    body = [f"{e['icon']} <b>ПЛАН ДНЯ · {e['label'].upper()}</b>",
            "━━━━━━━━━━━━━━━━━━━━", txt]
    if res.get("blocks"):
        body.append("\n⏱ <b>БЛОКИ НА СЬОГОДНІ</b>")
        for b in res["blocks"]:
            line = f"🕐 <b>{K.esc(b.get('time'))}</b> — {K.esc(b.get('title'))}"
            if b.get("dur"):
                line += f" ({b['dur']} хв)"
            body.append(line)
            if b.get("why"):
                body.append(f"    <i>{K.esc(b['why'])}</i>")
    return "\n".join(body)[:3900]


# ─── ДІЇ КНОПОК ──────────────────────────────────────────────────────────────

def answer(pid: str, energy: str) -> dict:
    """Натиснуто 😴/😐/💪 — записуємо стан і будуємо план."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    K.update_key(LOG_FILE, K.today_str(), {
        "energy": energy, "shift": p.get("shift"), "ts": K.now().isoformat()})
    try:
        import response_log
        response_log.log_response("day_energy", "Як ти сьогодні",
                                  ENERGY[energy]["label"], {"shift": p.get("shift")})
    except Exception:
        pass

    res = build(energy)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "build_failed")}
    _store.drop(pid)
    pid2 = _store.put({"blocks": res.get("blocks") or [], "energy": energy,
                       "text": res["text"][:1500]})
    return {"ok": True, "pid": pid2, "text": _fmt(res),
            "blocks": res.get("blocks") or [], "ai": res.get("ai"),
            "label": ENERGY[energy]["label"]}


def do_apply(pid: str) -> dict:
    """Ставить блоки плану в Google Calendar на сьогодні."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    blocks = p.get("blocks") or []
    if not blocks:
        return {"ok": False, "error": "no_blocks"}
    today = K.today_str()
    created, failed = [], 0
    for b in blocks:
        try:
            dur = int(b.get("dur") or 30)
        except Exception:
            dur = 30
        start = K.parse_dt(today, str(b.get("time")))
        res = K.calendar_event(str(b.get("title"))[:90], start,
                               start + timedelta(minutes=max(10, min(240, dur))),
                               description=(b.get("why") or "") +
                                           "\n\n— план дня від AI (стан: " +
                                           ENERGY.get(p.get("energy"), {}).get("label", "") + ")")
        if res.get("ok"):
            created.append(f"{b.get('time')} {b.get('title')}")
        else:
            failed += 1
    if not created:
        return {"ok": False, "error": "calendar_error"}
    _store.drop(pid)
    return {"ok": True, "created": len(created), "failed": failed, "items": created}


def do_note(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    try:
        import ai_notes
        lbl = ENERGY.get(p.get("energy"), {}).get("label", "")
        blocks = "; ".join(f"{b.get('time')} {b.get('title')}"
                           for b in (p.get("blocks") or []))
        ai_notes.add_note(f"⚡ План дня {K.today_str()} (стан: {lbl}): "
                          + (blocks or p.get("text", "")[:200]),
                          source="day_mode")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    _store.drop(pid)
    return {"ok": True}


def do_skip(pid: str) -> dict:
    _store.drop(pid)
    return {"ok": True}


# ─── ТРЕНД ───────────────────────────────────────────────────────────────────

def trend(days: int = 14) -> str:
    log = K.load(LOG_FILE, default={}) or {}
    cutoff = (K.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = sorted((k, v) for k, v in log.items()
                  if re.match(r"^\d{4}-\d{2}-\d{2}$", str(k)) and k > cutoff
                  and isinstance(v, dict))
    if not rows:
        return ("⚡ <b>ЕНЕРГІЯ ПО ДНЯХ</b>\n\nЩе немає відміток.\n\n"
                "<i>Я питаю раз на день у вікні під твою зміну. "
                "Спитати зараз: /день</i>")
    out = ["⚡ <b>ЕНЕРГІЯ ПО ДНЯХ</b>", "━━━━━━━━━━━━━━━━━━━━"]
    by_shift = {}
    for d, v in rows:
        e = ENERGY.get(v.get("energy"), {})
        sh = {"early": "☀️", "night": "🌙", "free": "🏖"}.get(v.get("shift"), "•")
        out.append(f"{e.get('icon', '•')} {d} {sh} — {e.get('label', '?')}")
        by_shift.setdefault(v.get("shift") or "?", []).append(v.get("energy"))
    out.append("")
    score = {"low": 1, "ok": 2, "high": 3}
    for sh, vals in by_shift.items():
        nums = [score.get(x, 2) for x in vals]
        name = {"early": "☀️ рання зміна", "night": "🌙 нічна зміна",
                "free": "🏖 вільний день"}.get(sh, sh)
        out.append(f"{name}: середня енергія <b>{sum(nums) / len(nums):.1f}/3</b> "
                   f"({len(nums)} дн.)")
    return "\n".join(out)[:3900]


if __name__ == "__main__":
    import sys
    if "--trend" in sys.argv:
        print(trend())
    elif "--ctx" in sys.argv:
        print(json.dumps(collect(), ensure_ascii=False, indent=1))
    elif "--dry" in sys.argv:
        e = sys.argv[sys.argv.index("--dry") + 1] if len(sys.argv) > sys.argv.index("--dry") + 1 else "ok"
        print(json.dumps(build(e), ensure_ascii=False, indent=1)[:5000])
    else:
        print("питання надіслано:", ask(force=True))
