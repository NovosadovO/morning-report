#!/usr/bin/env python3
"""
qwsync.py — ПОВНА АВТОМАТИКА даних здоров'я з QWatch Pro.

Ланцюжок:
  QWatch Pro (годинник) → Apple Health (офіційна синхронізація додатку)
  → Apple Shortcuts «Команди» з автоматизацією за часом (безкоштовно)
  → POST JSON на /qw цього бота → qwatch_data.json.

Чому саме qwatch_data.json: `storage.load_health()` мерджить його ПОВЕРХ
health.json, тому дані одразу бачать усі звіти, AI-контексти, healthtrend,
графіки і selfact.

Приймає «як завгодно названі» поля (Shortcuts віддає рядки й різні ключі),
нормалізує, зберігає, підтверджує в Telegram і пише в журнал дій бота.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TAG = "qwsync"

# ── назви полів, які можуть прийти від Shortcuts / Health Auto Export ──
_ALIASES = {
    "steps": ("steps", "step", "stepcount", "step_count", "кроки", "kroky"),
    "sleep_total_min": ("sleep_min", "sleepminutes", "sleep_minutes", "sleepmin"),
    "sleep_hours": ("sleep_h", "sleep_hours", "sleephours", "sleep", "сон"),
    "hr_avg": ("hr", "hr_avg", "heart_rate", "heartrate", "bpm", "пульс"),
    "calories": ("calories", "kcal", "energy", "active_energy", "калорії"),
    "distance_km": ("distance_km", "distance", "km", "дистанція"),
    "weight_kg": ("weight_kg", "weight", "вага", "kg"),
    "hrv": ("hrv", "heart_rate_variability"),
    "spo2": ("spo2", "oxygen", "blood_oxygen", "кисень"),
    "health_score": ("health_score", "score", "оцінка"),
    "body_battery": ("body_battery", "battery", "енергія"),
    "stress": ("stress", "стрес"),
}


def _num(val):
    """Shortcuts часто віддає рядки на кшталт '4 989', '09h 08min', '83,4'."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    # '09h 08min' / '9 год 8 хв' → хвилини
    m = re.search(r"(\d+)\s*(?:h|г|год)\D+(\d+)\s*(?:m|хв|min)", s, re.IGNORECASE)
    if m:
        return float(int(m.group(1)) * 60 + int(m.group(2)))
    s = s.replace(" ", "").replace(" ", "").replace(",", ".")
    m2 = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m2:
        return None
    try:
        return float(m2.group(0))
    except Exception:
        return None


def _pick(payload: dict, names) -> object:
    low = {str(k).strip().lower(): v for k, v in payload.items()}
    for n in names:
        if n in low:
            return low[n]
    return None


def normalize(payload: dict) -> dict:
    """Сирий JSON → запис у форматі qwatch_data.json. {} якщо нічого корисного."""
    if not isinstance(payload, dict):
        return {}
    rec = {}
    for field, names in _ALIASES.items():
        v = _num(_pick(payload, names))
        if v is None:
            continue
        rec[field] = v

    # сон: години → хвилини (одне канонічне поле sleep_total_min)
    if "sleep_hours" in rec:
        h = rec.pop("sleep_hours")
        if h and h > 24:          # прийшли вже хвилини під виглядом годин
            rec.setdefault("sleep_total_min", h)
        elif h:
            rec.setdefault("sleep_total_min", round(h * 60))
    if "sleep_total_min" in rec:
        rec["sleep_total_min"] = int(round(rec["sleep_total_min"]))
        rec["sleep_hours"] = round(rec["sleep_total_min"] / 60.0, 2)

    for k in ("steps", "hr_avg", "calories", "hrv", "spo2", "health_score",
              "body_battery", "stress"):
        if k in rec and rec[k] is not None:
            rec[k] = int(round(rec[k]))
    for k in ("weight_kg", "distance_km"):
        if k in rec and rec[k] is not None:
            rec[k] = round(float(rec[k]), 2)

    # відсіюємо явне сміття, щоб не забруднити історію
    if rec.get("steps") is not None and not (0 <= rec["steps"] <= 100000):
        rec.pop("steps")
    if rec.get("hr_avg") is not None and not (25 <= rec["hr_avg"] <= 230):
        rec.pop("hr_avg")
    if rec.get("sleep_total_min") is not None and not (0 <= rec["sleep_total_min"] <= 1080):
        rec.pop("sleep_total_min", None)
        rec.pop("sleep_hours", None)
    if rec.get("weight_kg") is not None and not (35 <= rec["weight_kg"] <= 250):
        rec.pop("weight_kg")

    if not rec:
        return {}

    # дата
    d = _pick(payload, ("date", "day", "дата"))
    ds = str(d or "").strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", ds):
        m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", str(d or "").strip())
        ds = (m.group(3) + "-" + m.group(2) + "-" + m.group(1)) if m else _today()
    rec["date"] = ds
    rec["source"] = "qwatch_auto"
    rec["saved_at"] = _nowstr()
    return rec


def _today() -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")


def _nowstr() -> str:
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")


def _fmt(rec: dict) -> str:
    bits = []
    if rec.get("steps") is not None:
        bits.append("👟 Кроки: <b>" + str(rec["steps"]) + "</b>")
    if rec.get("sleep_total_min"):
        h, m = divmod(int(rec["sleep_total_min"]), 60)
        bits.append("😴 Сон: <b>" + str(h) + "г " + str(m).zfill(2) + "хв</b>")
    if rec.get("hr_avg") is not None:
        bits.append("❤️ Пульс: <b>" + str(rec["hr_avg"]) + "</b> bpm")
    if rec.get("calories") is not None:
        bits.append("🔥 Калорії: <b>" + str(rec["calories"]) + "</b>")
    if rec.get("distance_km") is not None:
        bits.append("📏 Дистанція: <b>" + str(rec["distance_km"]) + "</b> км")
    if rec.get("weight_kg") is not None:
        bits.append("⚖️ Вага: <b>" + str(rec["weight_kg"]) + "</b> кг")
    if rec.get("hrv") is not None:
        bits.append("💓 HRV: <b>" + str(rec["hrv"]) + "</b>")
    if rec.get("spo2") is not None:
        bits.append("🫁 SpO2: <b>" + str(rec["spo2"]) + "%</b>")
    if rec.get("body_battery") is not None:
        bits.append("🔋 Body Battery: <b>" + str(rec["body_battery"]) + "</b>")
    return "\n".join(bits)


def save(payload: dict, notify: bool = True) -> dict:
    """Головна точка входу з вебхука. Повертає {'ok','record','fields'}."""
    rec = normalize(payload)
    if not rec:
        print("[" + TAG + "] порожній/непридатний payload — не зберігаю", flush=True)
        return {"ok": False, "error": "no usable fields"}

    try:
        import storage
        db = storage.load("qwatch_data.json", default={}) or {}
        if not isinstance(db, dict):
            db = {}
        prev = db.get(rec["date"]) or {}
        merged = dict(prev)
        merged.update({k: v for k, v in rec.items() if v is not None})
        db[rec["date"]] = merged
        storage.save("qwatch_data.json", db)
        print("[" + TAG + "] збережено " + rec["date"] + ": "
              + ", ".join(k for k in rec if k not in ("date", "source", "saved_at")),
              flush=True)
    except Exception as e:
        print("[" + TAG + "] storage error: " + str(e), flush=True)
        return {"ok": False, "error": str(e)}

    fields = [k for k in rec if k not in ("date", "source", "saved_at", "sleep_hours")]

    if notify:
        try:
            import ai_kit as K
            body = _fmt(merged)
            txt = ("⌚️ <b>Дані з годинника прийшли автоматично</b>\n"
                   + rec["date"] + "\n\n" + body)
            K.send_card(txt, tag=TAG)
        except Exception as e:
            print("[" + TAG + "] notify error: " + str(e), flush=True)

    try:
        import selfact
        selfact.journal("note", "Записав дані здоров'я за " + rec["date"],
                        "автосинк з годинника: " + ", ".join(fields), module=TAG)
    except Exception as e:
        print("[" + TAG + "] journal error: " + str(e), flush=True)

    return {"ok": True, "record": merged, "fields": fields}


def secret_ok(given: str) -> bool:
    """Секрет для захисту ендпоінта. QW_SECRET, або той самий, що у Telegram."""
    want = (os.environ.get("QW_SECRET", "")
            or os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")).strip()
    if not want:
        return True          # секрет не заданий — не блокуємо (як у /telegram)
    return str(given or "").strip() == want


if __name__ == "__main__":
    print(normalize({"steps": "4 989", "sleep": "09h 08min", "hr": "71",
                     "weight": "83,4"}))
