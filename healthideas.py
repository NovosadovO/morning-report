"""
healthideas.py — більше проактивних порад по здоров'ю (запит Олега 21.11).

Принципи:
  * ТІЛЬКИ дані Garmin Connect (analytics() читає qwatch_data.json). Жодних
    вигаданих чисел — у картці лише те, що є в даних, + одна конкретна дія.
  * Кожна ідея має свій ключ і cooldown, є денний ліміт та мінімальний
    проміжок між ідеями, щоб не повернути спам.
  * Враховує зміну (нічна/рання/вихідний) — не радить біг під час нічної.
  * Спільні ворота з healthai.initiative (insight_gate, 45 хв).
  * Без AI текст теж валідний (детермінована дія), AI лише розвиває його.
"""

from datetime import datetime, timedelta

import ai_kit as K
import healthai as H

TAG = "healthideas"
STATE_FILE = "healthideas_state.json"

DAILY_CAP = 8              # не більше стількох ідей на добу
MIN_GAP_MIN = 60           # мін. проміжок між двома ідеями
WINDOW = (8, 21)           # година, коли можна писати (включно з 8, до 21:59)
POST_NIGHT_HOUR = 7        # 06:30–07:59 — порада перед сном після нічної

# ключ -> cooldown, год
COOLDOWN_H = {
    "recovery_easy": 20, "run_ready": 20, "sleep_short": 20, "steps_pace": 20,
    "energy_low_now": 20, "rhr_up": 24, "weight_week": 24 * 6, "vo2_trend": 24 * 6,
    "steps_streak": 24 * 3, "post_night": 20, "early_tomorrow": 20,
    "sleep_good": 20, "stress_today": 20, "steps_done": 20, "hrv_good": 24 * 2,
    "weight_move": 24 * 3,
}


def _now():
    return K.now().replace(tzinfo=None)


def _fresh(m, max_days=1):
    return bool(m) and m.get("last") is not None and m.get("days_ago") is not None \
        and m["days_ago"] <= max_days


def _steps_streak() -> int:
    """Скільки днів ПІДРЯД (до вчора включно) кроки >= цілі, з Garmin."""
    try:
        import storage
        health = storage.load_health() or {}
        n = 0
        day = _now().date() - timedelta(days=1)
        while n < 30:
            v = (health.get(day.strftime("%Y-%m-%d")) or {}).get("steps")
            if v is None or float(v) < H.STEPS_GOAL:
                break
            n += 1
            day -= timedelta(days=1)
        return n
    except Exception as e:
        K.log(TAG, f"streak error: {e}")
        return 0


def candidates(a: dict, now: datetime = None, streak: int = None) -> list:
    """Список (ключ, емодзі-заголовок, факт-рядок, дія). Чиста функція від даних —
    легко тестується. Порядок = пріоритет."""
    now = now or _now()
    h = now.hour
    out = []
    shift = a.get("shift_today")
    tomorrow = a.get("shift_tomorrow")
    hrv = a.get("hrv") or {}
    sleep = a.get("sleep") or {}
    steps = a.get("steps") or {}
    energy = a.get("energy") or {}
    hr = a.get("hr") or {}
    vo2 = a.get("vo2max") or {}
    w = a.get("weight") or {}

    in_window = WINDOW[0] <= h <= WINDOW[1]

    # 1. Після нічної — порада перед сном (єдина, що йде зранку)
    if shift == "after_night" and h == POST_NIGHT_HOUR and now.minute >= 30:
        fact = "Нічна зміна щойно закінчилась"
        if _fresh(sleep, 0):
            fact += f"; сон за останню ніч за Garmin — {sleep['last']} год"
        out.append(("post_night", "🛏 Перед сном після нічної", fact,
                    "Темні штори, прохолодна кімната, телефон на беззвучний. Без кави "
                    "та важкої їжі перед сном — так денний сон дасть більше відновлення."))

    if not in_window:
        return out

    # 2. HRV просів — полегшений день
    if _fresh(hrv) and hrv.get("avg7"):
        drop = (hrv["avg7"] - hrv["last"]) / hrv["avg7"] * 100
        if drop >= 10:
            out.append(("recovery_easy", "🫀 Тіло просить легший день",
                        f"HRV {hrv['last']} мс — нижче за тижневе середнє {hrv['avg7']} мс",
                        "Сьогодні без інтервалів і силових: легка ходьба або спокійний "
                        "біг у розмовному темпі, більше води, лягти раніше."))
        elif drop <= 2 and sleep.get("last") is not None and _fresh(sleep) \
                and sleep["last"] >= 7 and _fresh(energy) and energy["last"] >= 60:
            ok_shift = (shift == "free" and 9 <= h <= 19) or (shift == "early" and 18 <= h <= 20)
            if ok_shift:
                out.append(("run_ready", "🏃 Хороше вікно для тренування",
                            f"HRV {hrv['last']} мс (тижнева норма {hrv['avg7']} мс), сон "
                            f"{sleep['last']} год, body battery {energy['last']}",
                            "Відновлення в нормі — можна якісне тренування: темповий біг "
                            "або інтервали. Розминка 10 хв, воду візьми з собою."))

    # 3. Короткий сон за ніч
    if _fresh(sleep, 0) and sleep["last"] < 6 and shift in ("early", "free") and h <= 14:
        out.append(("sleep_short", "😴 Короткий сон — як вирівняти день",
                    f"Сон за ніч за Garmin — {sleep['last']} год",
                    "Кава лише до обіду, на обід — легка їжа, 20 хв денного сну до "
                    "15:00 якщо вийде, а сьогодні лягти на годину раніше."))

    # 4. Кроки відстають від темпу
    if steps.get("last_day") == a.get("today") and steps.get("last") is not None \
            and 15 <= h <= 20 and steps["last"] < H.STEPS_GOAL * 0.4 and shift != "night":
        out.append(("steps_pace", "🚶 Кроки відстають",
                    f"Зараз {int(steps['last'])} кроків при цілі {H.STEPS_GOAL}",
                    "Прогуляйся 20–30 хвилин у спокійному темпі — це добре додасть до "
                    "цілі й заодно розвантажить голову."))

    # 5. Низька енергія
    if energy.get("last_day") == a.get("today") and energy.get("last") is not None \
            and energy["last"] <= 25 and 14 <= h <= 21:
        out.append(("energy_low_now", "🔋 Енергії мало",
                    f"Body battery {energy['last']} зі 100",
                    "Не навантажуй вечір: легка вечеря, без кави, екрани вимкни "
                    "раніше й лягай вчасно — так батарея зарядиться."))

    # 6. Пульс вищий за норму
    if _fresh(hr) and hr.get("avg7") and hr["last"] - hr["avg7"] >= 6:
        out.append(("rhr_up", "❤️ Пульс вищий за звичний",
                    f"Пульс {hr['last']} уд/хв проти тижневої норми {hr['avg7']} уд/хв",
                    "Це може бути недосип, стрес або початок застуди: пий воду, без "
                    "алкоголю й важкого тренування, і стеж за сном. Якщо тримається "
                    "кілька днів — варто показатись лікарю."))

    # 7. Тиждень: вага до цілі (понеділок вранці)
    if now.weekday() == 0 and 9 <= h <= 12 and w.get("last") is not None \
            and w.get("days_ago") is not None and w["days_ago"] <= 7 and w.get("to_goal") is not None:
        out.append(("weight_week", "⚖️ Початок тижня: вага",
                    f"Остання вага з Garmin — {w['last']} кг, до цілі {H.WEIGHT_GOAL:g} кг "
                    f"лишилось {w['to_goal']} кг",
                    "Постав на цей тиждень одну конкретну ціль: вечеря без "
                    "швидких вуглеводів 4 дні з 7 — цього достатньо для руху вниз."))

    # 8. VO2max
    if vo2.get("last") is not None and vo2.get("n", 0) >= 3 and vo2.get("delta") not in (None, 0):
        up = vo2["delta"] > 0
        out.append(("vo2_trend", "📈 VO2max" if up else "📉 VO2max",
                    f"VO2max {vo2['last']} ({vo2['delta']:+} за період)",
                    "Росте — продовжуй 2–3 бігові тренування на тиждень, одне з них "
                    "довге й легке." if up else
                    "Просів — додай одне легке довге тренування на тиждень і не "
                    "скорочуй сон, бо саме він повертає форму."))

    # 9. Серія днів із виконаною ціллю по кроках
    st = _steps_streak() if streak is None else streak
    if st >= 3:
        out.append(("steps_streak", "🔥 Серія по кроках",
                    f"Ціль {H.STEPS_GOAL} кроків виконана {st} дні(в) підряд",
                    "Не ламай серію: навіть коротка вечірня прогулянка її збереже."))

    # 9b. Нові типи (кожен раз на добу максимум)
    if _fresh(sleep, 0) and sleep["last"] >= 7.5 and 8 <= h <= 12 and shift != "after_night":
        out.append(("sleep_good", "😴 Гарний сон",
                    f"Сон за ніч за Garmin — {sleep['last']} год",
                    "Використай цей запас: найважче на сьогодні роби зараз, поки голова "
                    "свіжа, а вечір залиш під легку прогулянку."))
    stress = a.get("stress") or {}
    if stress.get("last_day") == a.get("today") and stress.get("last") is not None \
            and stress["last"] >= H.STRESS_HIGH and 11 <= h <= 21 and shift != "night":
        out.append(("stress_today", "😣 Стрес сьогодні підвищений",
                    f"Стрес за Garmin — {stress['last']} зі 100",
                    "5 хвилин дихання (вдих 4 с, видих 6 с) або коротка прогулянка без "
                    "телефону — це швидко знижує навантаження."))
    if steps.get("last_day") == a.get("today") and steps.get("last") is not None \
            and steps["last"] >= H.STEPS_GOAL and 12 <= h <= 21:
        out.append(("steps_done", "✅ Ціль по кроках виконана",
                    f"Сьогодні {int(steps['last'])} кроків при цілі {H.STEPS_GOAL}",
                    "Чудово. Решту дня — розтяжка 5–10 хв і спокійний вечір, "
                    "додатково себе не навантажуй."))
    if _fresh(hrv) and hrv.get("avg7") and hrv["last"] > hrv["avg7"] * 1.1 and 9 <= h <= 19:
        out.append(("hrv_good", "🫀 HRV вище за норму",
                    f"HRV {hrv['last']} мс проти тижневої норми {hrv['avg7']} мс",
                    "Нервова система відновилась — гарний момент для запланованого "
                    "бігу чи силового."))
    if w.get("delta") is not None and w.get("n", 0) >= 4 and abs(w["delta"]) >= 0.4 \
            and w.get("days_ago") is not None and w["days_ago"] <= 3 and 10 <= h <= 19:
        dn = w["delta"] < 0
        out.append(("weight_move", "⚖️ Вага " + ("йде вниз" if dn else "йде вгору"),
                    f"Вага {w['last']} кг ({w['delta']:+} кг за період), до цілі "
                    f"{H.WEIGHT_GOAL:g} кг — {w.get('to_goal')} кг",
                    "Так тримати: не змінюй режим сну й харчування." if dn else
                    "Подивись на вечері й солодке цього тижня та додай 20 хв ходьби щодня."))

    # 10. Завтра рання — вечірнє налаштування
    if tomorrow == "early" and shift != "night" and 20 <= h <= 21:
        fact = "Завтра рання зміна (06:00)"
        if _fresh(sleep) and sleep.get("avg7") is not None:
            fact += f"; сон за тиждень у середньому {sleep['avg7']} год"
        out.append(("early_tomorrow", "⏰ Завтра рання", fact,
                    "Лягай на годину раніше за звичне, будильник підготуй зараз, "
                    "каву на вечір не пий."))
    return out


def _due(state: dict, key: str, now: datetime) -> bool:
    prev = (state.get("seen") or {}).get(key)
    if not prev:
        return True
    try:
        return (now - datetime.fromisoformat(prev)).total_seconds() >= COOLDOWN_H.get(key, 24) * 3600
    except Exception:
        return True


def run(force: bool = False) -> int:
    """Один прохід (викликається з healthai.tick). Шле максимум одну ідею."""
    if not force and H._muted():
        return 0
    now = _now()
    state = K.load(STATE_FILE, default={}) or {}
    day = now.strftime("%Y-%m-%d")
    if state.get("day") != day:
        state["day"] = day
        state["count"] = 0
        state["sigs"] = []
    if not force:
        if state.get("count", 0) >= DAILY_CAP:
            return 0
        last = state.get("last_sent")
        if last:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds() < MIN_GAP_MIN * 60:
                    return 0
            except Exception:
                pass
        if not K.rate_ok(H._INSIGHT_GATE_FILE, 45):
            return 0

    a = H.analytics(14)
    sent = 0
    for key, header, fact, action in candidates(a, now):
        if not force and not _due(state, key, now):
            continue
        prompt = (f"{H._STYLE}\n\nДАНІ:\n{H.facts_block(a)}\n{H._shift_state()}\n\n"
                  f"ПРИВІД: {fact}\nБАЗОВА ПОРАДА: {action}\n\n"
                  "Перепиши порадою для Олега у 2-3 речення: чому саме це випливає "
                  "з цих чисел і ОДНА конкретна дія. Використовуй ЛИШЕ числа з ДАНИХ. "
                  "Без вступів, без нових чисел.")
        ai = (K.gemini_text(prompt, max_tokens=300, temperature=0.6, tag=TAG) or "").strip()
        text = f"<b>{header}</b>\n\n{fact}.\n\n{ai or action}"
        sig = " ".join((ai or action).lower().split())[:120]
        if not force and sig in (state.get("sigs") or []):
            continue
        if K.send_card(text, H._kb(), tag=TAG):
            state.setdefault("seen", {})[key] = now.isoformat(timespec="seconds")
            state["count"] = state.get("count", 0) + 1
            state.setdefault("sigs", []).append(sig)
            state["last_sent"] = now.isoformat(timespec="seconds")
            K.rate_mark(H._INSIGHT_GATE_FILE)
            H._journal("idea", f"ідея здоров'я: {key}", fact)
            sent = 1
        break          # одна ідея за прохід
    K.save(STATE_FILE, state)
    K.log(TAG, f"run: надіслано {sent}, за день {state.get('count', 0)}")
    return sent
