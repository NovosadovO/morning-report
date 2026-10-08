"""
garmin_sync.py — пряме підключення бота до Garmin Connect (запит Олега, 08.10).

Навіщо, якщо дані вже йдуть через Apple Health → Health Auto Export → /upload:
Garmin Connect має деталі, яких немає в Apple Health export — HRV за ніч
(hrvSummary), Body Battery у реальному часі, стрес (avgStressLevel), SpO2
за ніч, VO2max. Цей модуль ЛИШЕ ДОДАЄ ці поля в той самий канонічний
qwatch_data.json (через qwsync.save), нічого не дублює і не замінює
Apple Health як основне джерело кроків/сну/ваги.

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
SYNC_EVERY_MIN = 120


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
    except Exception as e:
        print(f"[{TAG}] sleep error: {e}", flush=True)

    return payload


def sync_once() -> bool:
    """Один прохід: тягне сьогоднішні дані з Garmin Connect, мерджить у
    qwatch_data.json (той самий файл, що й Apple Health/вручну). Повертає
    True якщо хоч щось нове збережено."""
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
