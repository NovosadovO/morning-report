#!/usr/bin/env python3
"""
Persistent storage через Google Sheets.
Замінює /tmp файли — дані не зникають між редеплоями.

Структура таблиці (одна на весь проект):
  Аркуш "habits"  — {date: {shower: true, run: false, ...}}
  Аркуш "meds"    — {date: true/false}
  Аркуш "weight"  — {date: 82.5}
  Аркуш "sleep"   — {date: hours}

Кожен рядок: [key, value_json]
"""

import os, json, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))

# Кеш в пам'яті — щоб не робити запити кожні 30 сек
_CACHE = {}
_CACHE_TIME = {}
CACHE_TTL = 60  # секунд

def _get_token():
    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        return None
    try:
        import sys
        sys.path.insert(0, _DIR)
        from monitor import _get_google_token
        creds_data = json.loads(creds_json)
        return _get_google_token(
            creds_data,
            "https://www.googleapis.com/auth/spreadsheets"
        )
    except Exception as e:
        print(f"storage token error: {e}")
        return None

def _get_sheet_id():
    return os.environ.get("GOOGLE_SHEETS_ID", "")

def _sheets_request(method, path, body=None):
    """GET або PUT/POST до Sheets API."""
    token = _get_token()
    if not token:
        return None
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{_get_sheet_id()}/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    try:
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"sheets error [{method} {path[:60]}]: {e}")
        return None

def _load_sheet(sheet_name):
    """Завантажує аркуш як dict {key: value}."""
    cache_key = f"sheet_{sheet_name}"
    now = time.time()
    if cache_key in _CACHE and now - _CACHE_TIME.get(cache_key, 0) < CACHE_TTL:
        return _CACHE[cache_key]

    result = _sheets_request("GET", f"values/{urllib.parse.quote(sheet_name)}!A:B")
    if not result:
        return _load_local_fallback(sheet_name)

    rows = result.get("values", [])
    data = {}
    for row in rows:
        if len(row) >= 2:
            try:
                data[row[0]] = json.loads(row[1])
            except:
                data[row[0]] = row[1]

    _CACHE[cache_key] = data
    _CACHE_TIME[cache_key] = now
    return data

def _save_sheet(sheet_name, data):
    """Зберігає весь dict назад у аркуш."""
    _CACHE[f"sheet_{sheet_name}"] = data
    _CACHE_TIME[f"sheet_{sheet_name}"] = time.time()

    # Також зберігаємо локально як fallback
    _save_local_fallback(sheet_name, data)

    if not _get_sheet_id():
        return False

    values = [["key", "value"]]
    for k, v in data.items():
        values.append([k, json.dumps(v)])

    body = {
        "values": values,
        "majorDimension": "ROWS"
    }
    result = _sheets_request(
        "PUT",
        f"values/{urllib.parse.quote(sheet_name)}!A1?valueInputOption=RAW",
        body
    )
    return result is not None

def _load_local_fallback(sheet_name):
    """Fallback — читає з /tmp."""
    path = f"/tmp/{sheet_name}_data.json"
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return {}

def _save_local_fallback(sheet_name, data):
    """Backup в /tmp."""
    path = f"/tmp/{sheet_name}_data.json"
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except:
        pass

def invalidate_cache(sheet_name):
    _CACHE_TIME[f"sheet_{sheet_name}"] = 0

# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def load_habits():
    return _load_sheet("habits")

def save_habits(data):
    return _save_sheet("habits", data)

def load_meds():
    """Завантажує meds — fallback на repo meds_data.json."""
    data = _load_sheet("meds")
    if not data:
        # Початкові дані з репо
        repo_file = os.path.join(_DIR, "meds_data.json")
        try:
            with open(repo_file) as f:
                data = json.load(f)
            save_meds(data)
        except:
            pass
    return data

def save_meds(data):
    return _save_sheet("meds", data)

def load_weight():
    data = _load_sheet("weight")
    if not data:
        initial = os.path.join(_DIR, "weight_data_initial.json")
        try:
            with open(initial) as f:
                data = json.load(f)
            save_weight(data)
        except:
            pass
    return data

def save_weight(data):
    return _save_sheet("weight", data)
