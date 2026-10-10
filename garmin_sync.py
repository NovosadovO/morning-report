"""
garmin_sync.py — ЄДИНЕ джерело даних здоров'я бота (запит Олега, 08.10 і 09.10).

З 09.10 всі інші джерела (Apple Health ZIP/export, Health Auto Export,
StepsApp, OCR фото, QWatch Pro текст/команди) ВИДАЛЕНІ з коду — Олег прямо
попросив "дані... тільки із Garmin Connect, всі інші джерела видали".
Цей модуль — ЄДИНИЙ писач у канонічний qwatch_data.json (через qwsync.save):
steps, sleep, resting HR, HRV (hrvSummary), Body Battery, стрес
(avgStressLevel), SpO2, VO2max, вага (weigh-ins, якщо Олег її й там вносить —
пріоритет Garmin над ручним /вага, див. healthai._weight_series).

Два шляхи отримання даних (обидва через fetch_live — живий запит, не кеш):
  1. Фоновий синк — run_garmin_sync_watcher() у monitor_loop.py, раз на
     SYNC_EVERY_MIN (180 хв = 3 год). Викликає healthai.garmin_periodic_notify(),
     яка через fetch_live() бере свіжі дані, зберігає і ОДРАЗУ надсилає Олегу
     блок + короткі AI-рекомендації (запит Олега, 09.10 — "оновлює кожні 3 год
     і надсилає з AI рекомендаціями").
  2. Жива команда — /гармін у bot.py, викликає fetch_live() напряму, без AI
     (швидка відповідь, тільки блок даних) — не чекає 3-годинний цикл.

Офіційного Garmin Health API для одного користувача немає (програма
розробника Garmin зараз закрита для нових заявок) — використовуємо
неофіційну бібліотеку `garminconnect` (на базі `garth`), яка логіниться
звичайним email+паролем Олега (GARMIN_EMAIL/GARMIN_PASSWORD).

Сесія (OAuth-токени) кешується через storage.py (garmin_session.json,
той самий механізм синку з GitHub, що й усі інші *.json бота) — щоб
НЕ логінитись паролем щоразу (повторні логіни ризикують rate-limit/
блокуванням на боці Garmin). Пароль використовується лише коли кеш
відсутній або протух.
"""
import os
from datetime import date

TAG = "garmin_sync"

# як часто реально тягнути (watcher у monitor_loop.py викликає з таким кроком) —
# дані оновлюються протягом дня, але немає смислу бити Garmin API частіше.
# З 09.10 (запит Олега) — раз на 3 години, і кожен такий синк тепер ЖИВИЙ
# (fetch_live) і одразу надсилається Олегу з AI-рекомендаціями, а не тихо
# в кеш (див. healthai.garmin_periodic_notify, викликає run_garmin_sync_watcher).
SYNC_EVERY_MIN = 180


def _get_client():
    try:
        import garminconnect
    except Exception as e:
        print(f"[{TAG}] бібліотека garminconnect не встановлена: {e}", flush=True)
        return None

    import storage
    email = os.environ.get("GARMIN_EMAIL", "").strip()
    password = os.environ.get("GARMIN_PASSWORD", "").strip()
    if not email or not password:
        print(f"[{TAG}] GARMIN_EMAIL/GARMIN_PASSWORD не задані — пропускаю", flush=True)
        return None

    cache = storage.load("garmin_session.json", default={}) or {}
    token_json = cache.get("tokenstore") if isinstance(cache, dict) else None

    if token_json:
        try:
            client = garminconnect.Garmin()
            client.login(tokenstore=token_json)
            return client
        except Exception as e:
            print(f"[{TAG}] кеш-сесія не підійшла ({e}) — логінюсь паролем", flush=True)

    try:
        client = garminconnect.Garmin(email, password)
        client.login()
    except Exception as e:
        print(f"[{TAG}] логін провалився: {e}", flush=True)
        return None

    try:
        dumped = client.client.dumps()
        storage.save("garmin_session.json", {"tokenstore": dumped})
        print(f"[{TAG}] нова сесія збережена в кеш", flush=True)
    except Exception as e:
        print(f"[{TAG}] не зберіг нову сесію (не критично): {e}", flush=True)

    return client


def _fetch_today(client, day: str) -> dict:
    payload = {"date": day}

    try:
        stats = client.get_stats(day) or {}
        if stats.get("totalSteps") is not None:
            payload["steps"] = stats["totalSteps"]
        if stats.get("restingHeartRate") is not None:
            payload["hr_avg"] = stats["restingHeartRate"]
    except Exception as e:
        print(f"[{TAG}] stats error: {e}", flush=True)

    try:
        hrv = client.get_hrv_data(day) or {}
        summary = hrv.get("hrvSummary") or {}
        if summary.get("lastNightAvg") is not None:
            payload["hrv"] = summary["lastNightAvg"]
    except Exception as e:
        print(f"[{TAG}] hrv error: {e}", flush=True)

    try:
        bb = client.get_body_battery(day) or []
        if bb and isinstance(bb, list):
            arr = (bb[0] or {}).get("bodyBatteryValuesArray") or []
            # останнє реальне (не -1/-2 заглушка) значення за день
            vals = [v[1] for v in arr if isinstance(v, list) and len(v) > 1 and v[1] is not None and v[1] >= 0]
            if vals:
                payload["body_battery"] = vals[-1]
    except Exception as e:
        print(f"[{TAG}] body_battery error: {e}", flush=True)

    try:
        st = client.get_stress_data(day) or {}
        if st.get("avgStressLevel") is not None and st["avgStressLevel"] >= 0:
            payload["stress"] = st["avgStressLevel"]
    except Exception as e:
        print(f"[{TAG}] stress error: {e}", flush=True)

    try:
        sp = client.get_spo2_data(day) or {}
        if sp.get("averageSpO2") is not None:
            payload["spo2"] = sp["averageSpO2"]
    except Exception as e:
        print(f"[{TAG}] spo2 error: {e}", flush=True)

    try:
        mm = client.get_max_metrics(day) or []
        if mm and isinstance(mm, list):
            gen = (mm[0] or {}).get("generic") or {}
            vo2 = gen.get("vo2MaxPreciseValue") or gen.get("vo2MaxValue")
            if vo2:
                payload["vo2max"] = vo2
    except Exception as e:
        print(f"[{TAG}] max_metrics error: {e}", flush=True)

    try:
        sl = client.get_sleep_data(day) or {}
        dto = sl.get("dailySleepDTO") or {}
        secs = dto.get("sleepTimeSeconds")
        if secs:
            payload["sleep_total_min"] = round(secs / 60)
        deep_s  = dto.get("deepSleepSeconds")
        rem_s   = dto.get("remSleepSeconds")
        light_s = dto.get("lightSleepSeconds")
        awake_s = dto.get("awakeSleepSeconds")
        if deep_s is not None:
            payload["sleep_deep_min"] = round(deep_s / 60)
        if rem_s is not None:
            payload["sleep_rem_min"] = round(rem_s / 60)
        if light_s is not None:
            payload["sleep_light_min"] = round(light_s / 60)
        if awake_s is not None:
            payload["sleep_awake_min"] = round(awake_s / 60)
    except Exception as e:
        print(f"[{TAG}] sleep error: {e}", flush=True)

    try:
        wi = client.get_daily_weigh_ins(day) or {}
        avg = wi.get("totalAverage") or {}
        grams = avg.get("weight")
        if not grams:
            entries = wi.get("dateWeightList") or []
            if entries:
                grams = (entries[-1] or {}).get("weight")
        if grams:
            payload["weight_kg"] = round(grams / 1000.0, 1)
    except Exception as e:
        print(f"[{TAG}] weight error: {e}", flush=True)

    return payload


# ── поля, які реально вміє бачити Garmin (для /гармін — показуємо ВСІ,
# навіть якщо порожні, щоб Олег бачив що саме не прийшло) ──
FIELDS = [
    ("steps",           "👟 Кроки",          ""),
    ("hr_avg",          "❤️ Пульс спокою",   " уд/хв"),
    ("sleep_total_min", "😴 Сон",            "_mins"),
    ("hrv",             "💓 HRV (за ніч)",   " мс"),
    ("body_battery",    "🔋 Body Battery",   ""),
    ("stress",          "😤 Стрес",          ""),
    ("spo2",            "🫁 SpO2 (за ніч)",  "%"),
    ("vo2max",          "🫀 VO2max",         ""),
    ("weight_kg",       "⚖️ Вага",           " кг"),
]


def format_block(payload: dict, day: str, live: bool) -> str:
    """Текстовий блок усіх полів Garmin — навіть відсутні показуються як
    'немає даних' (Олег просив ВСІ дані, щоб було видно прогалини)."""
    from datetime import datetime, timezone, timedelta
    now = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%d.%m.%Y %H:%M")
    tag = "⚡ живий запит до Garmin саме зараз" if live else "з кешу (фоновий синк кожні 3г)"
    lines = [f"⌚ <b>Garmin Connect — {day}</b>", f"<i>{tag}, {now}</i>", ""]
    for key, label, unit in FIELDS:
        v = payload.get(key)
        if v is None:
            lines.append(f"{label}: <i>немає даних</i>")
            continue
        if unit == "_mins":
            h, m = divmod(int(v), 60)
            lines.append(f"{label}: <b>{h}г {m:02d}хв</b>")
        else:
            lines.append(f"{label}: <b>{v}{unit}</b>")
    return "\n".join(lines)


def fetch_live(day: str = None) -> dict:
    """Живий запит до Garmin Connect (не кеш) — для команди /гармін.
    Повертає {'ok', 'record', 'text'} або {'ok': False, 'error': ...}."""
    day = day or date.today().isoformat()
    client = _get_client()
    if client is None:
        return {"ok": False, "error": "Немає підключення до Garmin Connect "
                 "(перевір GARMIN_EMAIL/GARMIN_PASSWORD або бібліотеку garminconnect)"}

    payload = _fetch_today(client, day)
    try:
        import qwsync
        res = qwsync.save(payload, notify=False)
        record = res.get("record") or payload
    except Exception as e:
        print(f"[{TAG}] live save error: {e}", flush=True)
        record = payload

    text = format_block(record, day, live=True)
    return {"ok": True, "record": record, "text": text}


def sync_once() -> bool:
    """Один прохід (фоновий, кожні SYNC_EVERY_MIN хв): тягне сьогоднішні дані
    з Garmin Connect, мерджить у qwatch_data.json. Повертає True якщо хоч
    щось нове збережено."""
    client = _get_client()
    if client is None:
        return False

    today = date.today().isoformat()
    payload = _fetch_today(client, today)
    fields = [k for k in payload if k != "date"]
    if not fields:
        print(f"[{TAG}] нічого не отримав за {today}", flush=True)
        return False

    try:
        import qwsync
        res = qwsync.save(payload, notify=False)
        if res.get("ok"):
            print(f"[{TAG}] збережено {today}: {', '.join(fields)}", flush=True)
        else:
            print(f"[{TAG}] save не ok: {res}", flush=True)
        return bool(res.get("ok"))
    except Exception as e:
        print(f"[{TAG}] save error: {e}", flush=True)
        return False


if __name__ == "__main__":
    print(sync_once())
