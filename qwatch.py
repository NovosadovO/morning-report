"""
qwatch.py — QWatch Pro data parser, storage & reports.

Flow:
  1. User pastes QWatch text in Telegram
  2. bot.py detects it (contains "Health Score") → calls parse_and_save(text)
  3. Data saved to GitHub data/qwatch_data.json (keyed by date)
  4. Reports generated on demand or auto (daily 21:00, weekly Sunday, monthly 1st)
"""

import os, re, json, urllib.request
from datetime import datetime, timezone, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8374312425:AAHqrQCEqrgtVdl5Te5WhWblM2ESCnqhpfk")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT",  "2100366814")
GEMINI_KEY     = os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")

# ─── STORAGE ──────────────────────────────────────────────────────────────────

def _load():
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import load as _l
        return _l("qwatch_data.json", default={})
    except Exception as e:
        print(f"qwatch load error: {e}")
        return {}

def _save(data):
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import save as _s
        return _s("qwatch_data.json", data)
    except Exception as e:
        print(f"qwatch save error: {e}")
        return False

# ─── PARSER ───────────────────────────────────────────────────────────────────

def _extract_int(pattern, text, default=None):
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try: return int(m.group(1).replace(",", "").replace(" ", ""))
        except: pass
    return default

def _extract_float(pattern, text, default=None):
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try: return float(m.group(1).replace(",", "."))
        except: pass
    return default

def _extract_time_min(pattern, text, default=None):
    """Парсить '6 годин 45 хвилин' або '6h 45m' → хвилини."""
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        try:
            h = int(m.group(1))
            mn = int(m.group(2))
            return h * 60 + mn
        except: pass
    return default

def parse_qwatch_text(text: str) -> dict:
    """
    Парсить вільний текст QWatch Pro → структурований dict.
    Повертає dict з полями або {} якщо не вдалось.
    """
    result = {}

    # Date — "Дата: 2026-05-16 19:02" або "Дата: 17.05.2026 21:58"
    date_m = re.search(r'(?:Date|Дата)[:\s]+(\d{4}-\d{2}-\d{2})', text, re.IGNORECASE)
    if date_m:
        result["date"] = date_m.group(1)
    else:
        # DD.MM.YYYY формат
        date_m2 = re.search(r'(?:Date|Дата)[:\s]+(\d{2})\.(\d{2})\.(\d{4})', text, re.IGNORECASE)
        if date_m2:
            result["date"] = f"{date_m2.group(3)}-{date_m2.group(2)}-{date_m2.group(1)}"
        else:
            now = datetime.now(timezone.utc) + timedelta(hours=2)
            result["date"] = now.strftime("%Y-%m-%d")

    # Health Score — "Health Score: 78" або "Оцінка здоров'я: 78%"
    hs = _extract_int(r'(?:Health Score|Оцінка здоров.я)[:\s]+(\d+)', text)
    if hs: result["health_score"] = hs

    # Steps — "19 498 кроків" або "становить 19 498 кроків"
    m = re.search(r'становить\s+([\d][\d\s\u00a0,]*)\s*крок', text, re.IGNORECASE)
    if m:
        result["steps"] = int(m.group(1).replace(",", "").replace(" ", "").replace("\u00a0", "").replace("\xa0", ""))
    else:
        m = re.search(r'(?:зробили?|зробив)[^\d]*([\d\s,]+)\s*крок', text, re.IGNORECASE)
        if m:
            result["steps"] = int(m.group(1).replace(",", "").replace(" ", "").replace("\u00a0", ""))
        else:
            m2 = re.search(r'([\d][\d\s,\u00a0]*)\s*крок', text, re.IGNORECASE)
            if m2:
                s = m2.group(1).replace(",", "").replace(" ", "").replace("\u00a0", "").replace("\xa0", "")
                result["steps"] = int(s)

    # Sleep total — "6 годин 45 хвилин"
    m = re.search(r'(\d+)\s*годин\s+(\d+)\s*хвилин', text, re.IGNORECASE)
    if m:
        result["sleep_total_min"] = int(m.group(1)) * 60 + int(m.group(2))

    # Sleep deep — "3 години 17 хвилин глибокого"
    m = re.search(r'(\d+)\s*годин[аи]?\s+(\d+)\s*хвилин\s+глибокого', text, re.IGNORECASE)
    if m:
        result["sleep_deep_min"] = int(m.group(1)) * 60 + int(m.group(2))

    # Sleep light — "3 години 28 хвилин легкого"
    m = re.search(r'(\d+)\s*годин[аи]?\s+(\d+)\s*хвилин\s+легкого', text, re.IGNORECASE)
    if m:
        result["sleep_light_min"] = int(m.group(1)) * 60 + int(m.group(2))

    # Sleep quality score — тільки в контексті сну
    m = re.search(r'(?:якість сну|sleep score|sleep quality)[^\d]*(\d+)', text, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if val <= 100:
            result["sleep_quality"] = val

    # Heart Rate avg — "пульс сьогодні — 62 удари/хв" або "62 уд/хв"
    m = re.search(r'пульс[^\d]*—?\s*(\d+)\s*удар', text, re.IGNORECASE)
    if m:
        result["hr_avg"] = int(m.group(1))
    else:
        m2 = re.search(r'(\d+)\s*уд(?:ар)?[и]?/хв', text, re.IGNORECASE)
        if m2:
            result["hr_avg"] = int(m2.group(1))

    # Calories — "508 620" після "споживання енергії" або просто ккал
    # QWatch Pro дає калорії як великі числа без "ккал" — шукаємо в контексті
    m = re.search(r'(?:споживання енергії|витрат|калорі)[^\d]*([\d\s]+)', text, re.IGNORECASE)
    if m:
        val_str = m.group(1).replace(" ", "").replace("\u00a0", "")[:8]
        try:
            val = int(val_str)
            if val > 0:
                result["calories"] = val
        except: pass
    else:
        m2 = re.search(r'(\d+)\s*ккал', text, re.IGNORECASE)
        if m2:
            result["calories"] = int(m2.group(1))

    # Stress — "Показник стресу сьогодні — 45"
    m = re.search(r'(?:показник стресу|стрес)[^\d]*—?\s*(\d+)', text, re.IGNORECASE)
    if m:
        val = int(m.group(1))
        if val <= 100:
            result["stress"] = val

    # HRV — "HRV сьогодні — 48" або "ВСР ... становить 40" або "варіабельність ... становить 40"
    m = re.search(r'HRV[^\d]*—?\s*(\d+)', text, re.IGNORECASE)
    if m:
        result["hrv"] = int(m.group(1))
    else:
        m = re.search(r'(?:ВСР|варіабельність серцевого ритму)[^\d]*становить\s+(\d+)', text, re.IGNORECASE)
        if m:
            result["hrv"] = int(m.group(1))

    # Weight — "Вага: 83 кг"
    m = re.search(r'Вага[:\s]+(\d+(?:[.,]\d+)?)\s*кг', text, re.IGNORECASE)
    if m:
        result["weight_kg"] = float(m.group(1).replace(",", "."))

    # Age — "Вік: 36 років"
    m = re.search(r'Вік[:\s]+(\d+)\s*рок', text, re.IGNORECASE)
    if m:
        result["age"] = int(m.group(1))

    # Height — "Зріст: 175 см"
    m = re.search(r'Зріст[:\s]+(\d+)\s*см', text, re.IGNORECASE)
    if m:
        result["height_cm"] = int(m.group(1))

    # Gender — "Стать: Чоловіча / Жіноча"
    m = re.search(r'Стать[:\s]+(Чоловіча|Жіноча|Male|Female)', text, re.IGNORECASE)
    if m:
        g = m.group(1).lower()
        result["gender"] = "male" if g in ("чоловіча", "male") else "female"

    # Blood pressure — "тиск: 48" або "тиск 120/80"
    m = re.search(r'тиск[:\s]+(\d+)/(\d+)', text, re.IGNORECASE)
    if m:
        result["bp_systolic"] = int(m.group(1))
        result["bp_diastolic"] = int(m.group(2))
    else:
        m = re.search(r'\(тиск[:\s]+(\d+)\)', text, re.IGNORECASE)
        if m:
            result["bp_raw"] = int(m.group(1))

    # SpO2
    m = re.search(r'(\d+)\s*%.*?кисн|SpO2[^\d]*(\d+)', text, re.IGNORECASE)
    if m:
        val = int(m.group(1) or m.group(2))
        if 80 <= val <= 100:
            result["spo2"] = val

    # Якщо SpO2 не зафіксовано — зберігаємо null
    if "spo2" not in result:
        result["spo2"] = None

    result["source"] = "qwatch"
    result["saved_at"] = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")

    return result

def _gemini_parse(text: str) -> dict:
    """
    Fallback — якщо regex не зловив всі поля, використовуємо Gemini.
    Повертає dict з числовими полями.
    """
    prompt = (
        "Витягни з тексту здоров'я наступні числові поля і поверни ТІЛЬКИ JSON без markdown:\n"
        "{\n"
        "  \"health_score\": число 0-100 або null,\n"
        "  \"steps\": ціле число або null,\n"
        "  \"sleep_total_min\": хвилини або null,\n"
        "  \"sleep_deep_min\": хвилини або null,\n"
        "  \"sleep_light_min\": хвилини або null,\n"
        "  \"sleep_quality\": число 0-100 або null,\n"
        "  \"hr_avg\": уд/хв або null,\n"
        "  \"calories\": ккал або null,\n"
        "  \"stress\": бали або null,\n"
        "  \"hrv\": мс або null,\n"
        "  \"spo2\": % або null,\n"
        "  \"date\": \"YYYY-MM-DD\" або null\n"
        "}\n\n"
        f"Текст:\n{text[:4000]}"
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 500, "temperature": 0}
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception as e:
        print(f"qwatch gemini parse error: {e}")
    return {}

def parse_and_save(text: str) -> dict:
    """
    Основна функція: парсить текст QWatch → зберігає → повертає збережений запис.
    """
    # Спочатку regex
    record = parse_qwatch_text(text)

    # Якщо мало полів — допарсуємо через Gemini
    fields = [record.get(f) for f in ["steps", "sleep_total_min", "hr_avg", "hrv"]]
    if fields.count(None) >= 2:
        print("qwatch: few fields from regex, trying Gemini...")
        gemini_data = _gemini_parse(text)
        # Merge — regex має пріоритет
        for k, v in gemini_data.items():
            if k not in record or record[k] is None:
                record[k] = v

    # Нормалізуємо дату
    if not record.get("date"):
        now = datetime.now(timezone.utc) + timedelta(hours=2)
        record["date"] = now.strftime("%Y-%m-%d")

    # Зберігаємо
    db = _load()
    db[record["date"]] = record
    _save(db)
    print(f"qwatch: saved record for {record['date']}")
    return record

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _fmt_sleep(total_min):
    if total_min is None: return "—"
    h, m = divmod(total_min, 60)
    return f"{h}г {m:02d}хв"

def _fmt_val(v, unit=""):
    if v is None: return "—"
    return f"{v}{unit}"

# ─── ЗВІТИ ────────────────────────────────────────────────────────────────────

def format_day_block(date: str) -> str:
    """Блок QWatch для денного підсумку (21:00)."""
    db = _load()
    r = db.get(date)
    if not r:
        return ""

    lines = ["⌚ <b>QWatch Pro</b>"]
    if r.get("health_score"):
        lines.append(f"  🏆 Health Score: <b>{r['health_score']}%</b>")
    if r.get("steps"):
        lines.append(f"  🚶 Кроки: <b>{r['steps']:,}</b>")
    if r.get("sleep_total_min"):
        deep = f", глибокий {_fmt_sleep(r.get('sleep_deep_min'))}" if r.get("sleep_deep_min") else ""
        lines.append(f"  🛌 Сон: <b>{_fmt_sleep(r['sleep_total_min'])}</b>{deep}")
    if r.get("hr_avg"):
        lines.append(f"  ❤️ ЧСС: <b>{r['hr_avg']} уд/хв</b>")
    if r.get("hrv"):
        lines.append(f"  🧘 HRV: <b>{r['hrv']} мс</b>")
    if r.get("calories"):
        lines.append(f"  🔥 Калорії: <b>{r['calories']} ккал</b>")
    if r.get("stress"):
        lines.append(f"  🤯 Стрес: <b>{r['stress']} балів</b>")
    if r.get("spo2"):
        lines.append(f"  🩸 SpO2: <b>{r['spo2']}%</b>")
    return "\n".join(lines)

def _get_week_records():
    """Останні 7 днів."""
    db = _load()
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    return [(d, db[d]) for d in dates if d in db]

def _get_month_records():
    """Поточний місяць."""
    db = _load()
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    month = now.strftime("%Y-%m")
    records = [(d, v) for d, v in sorted(db.items()) if d.startswith(month)]
    return records

def _avg(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None

def report_weekly() -> str:
    """Тижневий звіт QWatch."""
    records = _get_week_records()
    if not records:
        return "📊 QWatch: даних за тиждень немає."

    dates_str = f"{records[0][0][5:]} – {records[-1][0][5:]}"

    steps_list   = [r.get("steps") for _, r in records]
    sleep_list   = [r.get("sleep_total_min") for _, r in records]
    hr_list      = [r.get("hr_avg") for _, r in records]
    hrv_list     = [r.get("hrv") for _, r in records]
    stress_list  = [r.get("stress") for _, r in records]
    cal_list     = [r.get("calories") for _, r in records]
    hs_list      = [r.get("health_score") for _, r in records]

    lines = [
        f"📊 <b>QWatch — тижневий звіт</b> ({dates_str})",
        f"Днів з даними: {len(records)}/7\n",
    ]

    if any(v for v in hs_list):
        lines.append(f"🏆 Health Score: сер. <b>{_avg(hs_list)}%</b>")
    if any(v for v in steps_list):
        total = sum(v for v in steps_list if v)
        lines.append(f"🚶 Кроки: сер. <b>{int(_avg(steps_list)):,}</b> / всього {total:,}")
    if any(v for v in sleep_list):
        lines.append(f"🛌 Сон: сер. <b>{_fmt_sleep(int(_avg(sleep_list)))}</b>")
    if any(v for v in hr_list):
        lines.append(f"❤️ ЧСС: сер. <b>{_avg(hr_list)} уд/хв</b>")
    if any(v for v in hrv_list):
        lines.append(f"🧘 HRV: сер. <b>{_avg(hrv_list)} мс</b>  (min {min(v for v in hrv_list if v)} / max {max(v for v in hrv_list if v)})")
    if any(v for v in stress_list):
        lines.append(f"🤯 Стрес: сер. <b>{_avg(stress_list)}</b>")
    if any(v for v in cal_list):
        lines.append(f"🔥 Калорії: сер. <b>{int(_avg(cal_list))} ккал</b>")

    # Деталі по днях
    lines.append("\n<b>По днях:</b>")
    DAY_UA = ["Пн","Вт","Ср","Чт","Пт","Сб","Нд"]
    for d, r in records:
        dt = datetime.strptime(d, "%Y-%m-%d")
        day = DAY_UA[dt.weekday()]
        hs = f"{r['health_score']}%" if r.get("health_score") else "—"
        sl = _fmt_sleep(r.get("sleep_total_min"))
        st = str(r["steps"]) if r.get("steps") else "—"
        hrv = f"{r['hrv']}мс" if r.get("hrv") else "—"
        lines.append(f"  {day} {d[5:]}: HS {hs} | сон {sl} | кроки {st} | HRV {hrv}")

    return "\n".join(lines)

def report_monthly() -> str:
    """Місячний звіт QWatch."""
    records = _get_month_records()
    if not records:
        return "📈 QWatch: даних за місяць немає."

    now = datetime.now(timezone.utc) + timedelta(hours=2)
    month_name = now.strftime("%B %Y")

    steps_list  = [r.get("steps") for _, r in records]
    sleep_list  = [r.get("sleep_total_min") for _, r in records]
    hr_list     = [r.get("hr_avg") for _, r in records]
    hrv_list    = [r.get("hrv") for _, r in records]
    stress_list = [r.get("stress") for _, r in records]
    hs_list     = [r.get("health_score") for _, r in records]

    lines = [
        f"📈 <b>QWatch — місячний звіт</b> ({month_name})",
        f"Днів з даними: {len(records)}\n",
    ]

    if any(v for v in hs_list):
        lines.append(f"🏆 Health Score: сер. <b>{_avg(hs_list)}%</b>  (max {max(v for v in hs_list if v)}%)")
    if any(v for v in steps_list):
        total = sum(v for v in steps_list if v)
        lines.append(f"🚶 Кроки: сер. <b>{int(_avg(steps_list)):,}</b> / всього {total:,}")
    if any(v for v in sleep_list):
        lines.append(f"🛌 Сон: сер. <b>{_fmt_sleep(int(_avg(sleep_list)))}</b>")
    if any(v for v in hr_list):
        lines.append(f"❤️ ЧСС: сер. <b>{_avg(hr_list)} уд/хв</b>")
    if any(v for v in hrv_list):
        lines.append(f"🧘 HRV: сер. <b>{_avg(hrv_list)} мс</b>  (min {min(v for v in hrv_list if v)} / max {max(v for v in hrv_list if v)})")
    if any(v for v in stress_list):
        lines.append(f"🤯 Стрес: сер. <b>{_avg(stress_list)}</b>")

    return "\n".join(lines)

# ─── SEND ─────────────────────────────────────────────────────────────────────

def _send(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception as e:
        print(f"qwatch send error: {e}")
        return False

def send_confirmation(record: dict):
    """Підтвердження що дані збережено."""
    date = record.get("date", "?")
    hs   = record.get("health_score")
    sl   = _fmt_sleep(record.get("sleep_total_min"))
    st   = record.get("steps")
    hrv  = record.get("hrv")
    hr   = record.get("hr_avg")
    cal  = record.get("calories")
    stress = record.get("stress")
    spo2 = record.get("spo2")
    wt   = record.get("weight_kg")
    bp_s = record.get("bp_systolic")
    bp_d = record.get("bp_diastolic")
    bp_r = record.get("bp_raw")

    lines = [f"✅ <b>QWatch дані збережено</b> ({date})\n"]
    if hs:    lines.append(f"  🏆 Health Score: {hs}%")
    if st:    lines.append(f"  🚶 Кроки: {st:,}")
    if sl != "—": lines.append(f"  🛌 Сон: {sl}")
    if hr:    lines.append(f"  ❤️ Пульс: {hr} уд/хв")
    if hrv:   lines.append(f"  🧘 HRV: {hrv} мс")
    if spo2:  lines.append(f"  🩸 SpO2: {spo2}%")
    if stress is not None: lines.append(f"  😬 Стрес: {stress}")
    if cal:
        cal_show = round(cal / 1000, 1) if cal > 10000 else cal
        unit = "ккал" if cal <= 10000 else "ккал (×1000)"
        lines.append(f"  🔥 Калорії: {cal_show} {unit}")
    if wt:    lines.append(f"  ⚖️ Вага: {wt} кг")
    if bp_s and bp_d: lines.append(f"  🩺 Тиск: {bp_s}/{bp_d}")
    elif bp_r: lines.append(f"  🩺 Тиск: {bp_r}")
    lines.append("\n<i>Включено в денний підсумок о 21:00</i>")
    _send("\n".join(lines))

def send_weekly_report():
    _send(report_weekly())

def send_monthly_report():
    _send(report_monthly())

# ─── AUTO REMINDERS (викликається з monitor_loop.py) ─────────────────────────

def check_qwatch_reminder():
    """О 19:02 — нагадування надіслати QWatch дані."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    h, m = now.hour, now.minute
    if not (h == 19 and 2 <= m < 7):
        return

    today = now.strftime("%Y-%m-%d")
    db = _load()
    if today in db:
        return  # вже є дані сьогодні

    # Dedup
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import load as _l, save as _s
        sent = _l("qwatch_reminders.json", default={})
        if sent.get(today):
            return
        sent[today] = True
        _s("qwatch_reminders.json", sent)
    except Exception as e:
        print(f"qwatch reminder dedup error: {e}")

    _send(
        "⌚ <b>QWatch — надішли дані за сьогодні</b>\n\n"
        "Скопіюй і відправ текст з QWatch Pro додатку.\n"
        "<i>Health Score, сон, кроки, HRV — все збережу автоматично.</i>"
    )
    print(f"qwatch reminder sent for {today}")

def check_qwatch_weekly():
    """Неділя о 20:30 — тижневий звіт QWatch."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    if now.weekday() != 6:  # 6 = неділя
        return
    h, m = now.hour, now.minute
    if not (h == 20 and 30 <= m < 35):
        return

    today = now.strftime("%Y-%m-%d")
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import load as _l, save as _s
        sent = _l("qwatch_weekly_sent.json", default={})
        key = f"weekly_{today}"
        if sent.get(key):
            return
        sent[key] = True
        _s("qwatch_weekly_sent.json", sent)
    except Exception as e:
        print(f"qwatch weekly dedup error: {e}")

    send_weekly_report()
    print(f"qwatch weekly report sent for {today}")

def check_qwatch_monthly():
    """1-го числа о 09:05 — місячний звіт QWatch."""
    now = datetime.now(timezone.utc) + timedelta(hours=2)
    if now.day != 1:
        return
    h, m = now.hour, now.minute
    if not (h == 9 and 5 <= m < 10):
        return

    month = now.strftime("%Y-%m")
    try:
        import sys; sys.path.insert(0, _DIR)
        from storage import load as _l, save as _s
        sent = _l("qwatch_monthly_sent.json", default={})
        if sent.get(month):
            return
        sent[month] = True
        _s("qwatch_monthly_sent.json", sent)
    except Exception as e:
        print(f"qwatch monthly dedup error: {e}")

    send_monthly_report()
    print(f"qwatch monthly report sent for {month}")
