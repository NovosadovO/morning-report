#!/usr/bin/env python3
"""
garmin_news.py — «що нового в Garmin» замість щотригодинного однакового дампу.

Скарга Олега (13.11): «багато даних немає, текст старий і однаковий». Причини:
  • картка кожні 3 год показувала ВЕСЬ перелік полів, навіть «VO2max: немає даних»;
  • AI щоразу отримував той самий набір і видавав ті самі поради (вода, прогулянка).
Тепер:
  • дивимось, що ЗМІНИЛОСЬ з минулої картки (новий сон/HRV, поріг кроків, стрибок
    Body Battery/стресу, нова пробіжка, добрані пропущені дні);
  • нічого суттєвого — НЕ шлемо (тихий синк);
  • у картку йдуть лише наявні показники, кожен порівняно з ТВОЄЮ нормою за 14 днів;
  • AI бачить свої 4 попередні поради і не повторює їх.
"""
import re
import statistics

import ai_kit as K

STATE_FILE = "garmin_news_state.json"
STEP_MILESTONES = (3000, 5000, 8000, 10000, 15000, 20000)
BB_JUMP = 20
STRESS_JUMP = 10


def _hm(mins) -> str:
    h, m = divmod(int(mins), 60)
    return f"{h} год {m:02d} хв"


def _base(health: dict, field: str, day: str, n: int = 14):
    vals = []
    for d in sorted(health):
        if d >= day:
            continue
        v = (health[d] or {}).get(field)
        if isinstance(v, (int, float)) and v > 0:
            vals.append(float(v))
    vals = vals[-n:]
    if len(vals) < 5:
        return None
    return statistics.mean(vals), (statistics.pstdev(vals) or 1.0), len(vals)


def _vs(health, field, day, val, unit="") -> str:
    b = _base(health, field, day)
    if not b:
        return ""
    mean, std, n = b
    z = (val - mean) / std
    word = "в нормі" if abs(z) < 0.8 else (
        ("нижче" if z < 0 else "вище") + " за твою норму")
    return f" (норма за {n} дн: {mean:.0f}{unit}, {word})"


def detect(record: dict, health: dict, state: dict, today: str, new_runs=None,
           backfilled=None) -> tuple:
    """-> (news[list[str]], new_state). news — готові рядки з фактами."""
    news = []
    last = (state.get("last") or {}) if state.get("day") == today else {}
    seen = state.get("seen") or {}
    new_seen = dict(seen)

    sl = record.get("sleep_total_min")
    if sl and seen.get("sleep") != today:
        line = f"😴 Сон: <b>{_hm(sl)}</b>" + _vs(health, "sleep_total_min", today, sl, " хв")
        deep, rem = record.get("sleep_deep_min"), record.get("sleep_rem_min")
        if deep is not None and rem is not None:
            line += f"; глибокий {deep} хв, REM {rem} хв"
        news.append(line)
        new_seen["sleep"] = today
    hrv = record.get("hrv")
    if hrv and seen.get("hrv") != today:
        news.append(f"💓 HRV за ніч: <b>{hrv:g} мс</b>" + _vs(health, "hrv", today, hrv, " мс"))
        new_seen["hrv"] = today
    rhr = record.get("hr_avg")
    if rhr and seen.get("rhr") != today:
        news.append(f"❤️ Пульс спокою: <b>{rhr:g}</b>" + _vs(health, "hr_avg", today, rhr))
        new_seen["rhr"] = today

    steps = record.get("steps")
    if steps is not None:
        prev = last.get("steps", 0)
        crossed = [m for m in STEP_MILESTONES if prev < m <= steps]
        if crossed:
            news.append(f"👟 Кроки: <b>{steps:,}</b> — пройдено поріг {crossed[-1]:,}".replace(",", " "))
    bb, prev_bb = record.get("body_battery"), last.get("body_battery")
    if bb is not None and prev_bb is not None and abs(bb - prev_bb) >= BB_JUMP:
        arrow = "🔋↑" if bb > prev_bb else "🪫↓"
        news.append(f"{arrow} Body Battery: <b>{bb}</b> (було {prev_bb} кілька годин тому)")
    st, prev_st = record.get("stress"), last.get("stress")
    if st is not None and prev_st is not None and abs(st - prev_st) >= STRESS_JUMP:
        news.append(f"😤 Стрес (середній за день): <b>{st}</b> (було {prev_st})")
    for r in (new_runs or []):
        km = round((r.get("distance") or 0) / 1000.0, 2)
        if km >= 2:
            news.append(f"🏃 Нова активність у Garmin: <b>{km} км</b> за "
                        f"{int((r.get('moving_time') or 0) // 60)} хв")
    if backfilled:
        news.append("🗂 Добрано пропущені дні: " + ", ".join(d[8:10] + "." + d[5:7] for d in backfilled))

    new_state = {
        "day": today, "seen": new_seen,
        "last": {k: record.get(k) for k in ("steps", "body_battery", "stress")
                 if record.get(k) is not None} or last,
        "prev_ai": state.get("prev_ai") or [],
    }
    return news, new_state


def remember_ai(state: dict, text: str) -> dict:
    t = re.sub(r"<[^>]+>", "", text or "").strip().replace("\n", " ")[:220]
    if t:
        state["prev_ai"] = (state.get("prev_ai") or [])[-3:] + [t]
    return state
