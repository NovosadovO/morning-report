#!/usr/bin/env python3
"""
OCR парсинг скріну Apple Health через Google Vision API.
Використовує наявні Google credentials (GOOGLE_CALENDAR_CREDENTIALS).
"""

import os, sys, json, re, base64, urllib.request
sys.path.insert(0, os.path.dirname(__file__))


def _get_google_token(creds_data, scope):
    """JWT токен для Google API."""
    from monitor import _get_google_token as _gt
    return _gt(creds_data, scope)


def _vision_ocr(image_bytes):
    """Надсилає зображення в Google Vision API, повертає текст."""
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return None

    creds_data = json.loads(creds_json)
    token = _get_google_token(creds_data, "https://www.googleapis.com/auth/cloud-vision")

    img_b64 = base64.b64encode(image_bytes).decode()
    body = json.dumps({
        "requests": [{
            "image": {"content": img_b64},
            "features": [{"type": "TEXT_DETECTION", "maxResults": 1}]
        }]
    }).encode()

    req = urllib.request.Request(
        "https://vision.googleapis.com/v1/images:annotate",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        result = json.loads(r.read())

    annotations = result.get("responses", [{}])[0].get("textAnnotations", [])
    if annotations:
        return annotations[0].get("description", "")
    return ""


def _parse_health_text(text):
    """
    Парсить текст зі скріну Apple Health.
    Шукає: кроки, сон, ЧСС, калорії, HRV, стрес, health score.
    """
    data = {}
    text_lower = text.lower()
    lines = text.split("\n")

    def find_number_near(keyword, text, offset=80):
        """Знаходить перше число після ключового слова."""
        idx = text_lower.find(keyword)
        if idx == -1:
            return None
        snippet = text[idx:idx + offset]
        nums = re.findall(r'\d[\d,\.]*', snippet)
        if nums:
            n = nums[0].replace(",", "")
            try:
                return float(n) if "." in n else int(n)
            except:
                return None
        return None

    # Кроки
    for kw in ["steps", "кроки", "крок", "steps today"]:
        v = find_number_near(kw, text_lower, 100)
        if v and 100 < v < 100000:
            data["steps"] = int(v)
            break

    # Сон
    for kw in ["sleep", "сон", "time in bed"]:
        idx = text_lower.find(kw)
        if idx != -1:
            snippet = text[idx:idx + 100]
            # шукаємо "7h 30m" або "7:30" або "7.5"
            m = re.search(r'(\d+)\s*[hгч]\s*(\d+)?\s*[mмхв]?', snippet, re.IGNORECASE)
            if m:
                hours = int(m.group(1))
                mins = int(m.group(2)) if m.group(2) else 0
                data["sleep_hours"] = round(hours + mins / 60, 1)
                break
            m2 = re.search(r'(\d+)[:\.](\d+)', snippet)
            if m2:
                data["sleep_hours"] = round(int(m2.group(1)) + int(m2.group(2)) / 60, 1)
                break
            v = find_number_near(kw, text_lower)
            if v and 1 <= v <= 24:
                data["sleep_hours"] = float(v)
                break

    # ЧСС
    for kw in ["heart rate", "bpm", "чсс", "серце", "пульс"]:
        v = find_number_near(kw, text_lower, 60)
        if v and 30 <= v <= 220:
            data["heart_rate"] = int(v)
            break

    # Калорії
    for kw in ["calorie", "ккал", "calories", "active energy", "cal"]:
        v = find_number_near(kw, text_lower, 100)
        if v and 500 <= v <= 10000:
            data["calories"] = int(v)
            break

    # HRV
    for kw in ["hrv", "heart rate variability"]:
        v = find_number_near(kw, text_lower, 80)
        if v and 5 <= v <= 200:
            data["hrv"] = int(v)
            break

    # Стрес
    for kw in ["stress", "стрес"]:
        idx = text_lower.find(kw)
        if idx != -1:
            snippet = text[idx:idx + 150]
            nums = re.findall(r'\d+', snippet)
            stress_nums = [int(n) for n in nums if 0 <= int(n) <= 100]
            if len(stress_nums) >= 2:
                data["stress_min"] = min(stress_nums[:2])
                data["stress_max"] = max(stress_nums[:2])
            elif len(stress_nums) == 1:
                data["stress_max"] = stress_nums[0]
            break

    # Health Score — зазвичай велике число 0-100
    for kw in ["health score", "score", "бали здоров", "health"]:
        v = find_number_near(kw, text_lower, 60)
        if v and 1 <= v <= 100:
            # переконуємось що це не кроки/ЧСС
            if v != data.get("heart_rate") and v != data.get("steps", 0):
                data["health_score"] = int(v)
                break

    # Fallback: шукаємо велике число що схоже на score (поруч з %)
    if not data.get("health_score"):
        m = re.search(r'(\d{1,3})\s*[/%]', text)
        if m:
            v = int(m.group(1))
            if 1 <= v <= 100:
                data["health_score"] = v

    return data


def download_telegram_photo(file_id, bot_token):
    """Завантажує фото з Telegram по file_id."""
    # Отримуємо path
    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as r:
        result = json.loads(r.read())

    file_path = result["result"]["file_path"]
    photo_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

    req2 = urllib.request.Request(photo_url)
    with urllib.request.urlopen(req2, timeout=20) as r:
        return r.read()


def parse_health_photo(file_id, bot_token):
    """
    Основна функція: завантажує фото → OCR → парсить → повертає dict з даними.
    Повертає (data_dict, raw_text) або (None, error_msg).
    """
    try:
        image_bytes = download_telegram_photo(file_id, bot_token)
    except Exception as e:
        return None, f"Не вдалось завантажити фото: {e}"

    try:
        raw_text = _vision_ocr(image_bytes)
    except Exception as e:
        # Vision API недоступний — повертаємо None щоб бот попросив ввести вручну
        print(f"Vision OCR error: {e}")
        return None, f"OCR недоступний: {e}"

    if not raw_text:
        return None, "Текст на фото не знайдено"

    data = _parse_health_text(raw_text)
    return data, raw_text
