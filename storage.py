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

_TOKEN_CACHE = {"token": None, "exp": 0}

def _get_token():
    """JWT auth для service account — без зовнішніх залежностей."""
    now_ts = int(time.time())
    if _TOKEN_CACHE["token"] and now_ts < _TOKEN_CACHE["exp"] - 60:
        return _TOKEN_CACHE["token"]

    creds_json = os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", "")
    if not creds_json:
        print("storage: GOOGLE_CALENDAR_CREDENTIALS not set")
        return None
    try:
        import base64
        creds = json.loads(creds_json)

        def b64url(data):
            if isinstance(data, str):
                data = data.encode()
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

        header  = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}))
        payload = b64url(json.dumps({
            "iss": creds["client_email"],
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now_ts,
            "exp": now_ts + 3600,
        }))
        signing_input = f"{header}.{payload}".encode()

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        private_key = serialization.load_pem_private_key(
            creds["private_key"].encode(), password=None)
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

        jwt = f"{header}.{payload}.{b64url(signature)}"
        post_data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt
        }).encode()
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=post_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
        token = resp.get("access_token")
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["exp"] = now_ts + resp.get("expires_in", 3600)
        return token
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
