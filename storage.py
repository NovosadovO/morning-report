#!/usr/bin/env python3
"""
Persistent storage через GitHub repository.
Зберігає JSON файли в repo NovosadovO/morning-report/data/
Дані не зникають між редеплоями.
"""

import os, json, time, base64, urllib.request, urllib.parse

GITHUB_TOKEN = "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"
GITHUB_REPO  = "NovosadovO/morning-report"
GITHUB_API   = "https://api.github.com"

_DIR = os.path.dirname(os.path.abspath(__file__))

# Кеш в пам'яті
_CACHE = {}
_CACHE_TIME = {}
CACHE_TTL = 30  # секунд

def _gh_request(method, path, body=None):
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "morning-report-bot"
    }
    try:
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        body_err = e.read().decode()
        print(f"GitHub {method} {path} error {e.code}: {body_err[:200]}")
        return None
    except Exception as e:
        print(f"GitHub error [{method} {path}]: {e}")
        return None

def _load_github(filename):
    """Читає JSON файл з GitHub repo."""
    cache_key = filename
    now = time.time()
    if cache_key in _CACHE and now - _CACHE_TIME.get(cache_key, 0) < CACHE_TTL:
        return _CACHE[cache_key]

    result = _gh_request("GET", f"data/{filename}")
    if not result:
        return _load_local(filename)

    try:
        content = base64.b64decode(result["content"]).decode()
        data = json.loads(content)
        _CACHE[cache_key] = data
        _CACHE_TIME[cache_key] = now
        print(f"storage: loaded {filename} from GitHub ({len(data)} keys)")
        return data
    except Exception as e:
        print(f"storage parse error {filename}: {e}")
        return _load_local(filename)

def _save_github(filename, data):
    """Зберігає JSON файл в GitHub repo."""
    _CACHE[filename] = data
    _CACHE_TIME[filename] = time.time()

    # Також локально
    _save_local(filename, data)

    content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode()

    # Отримуємо поточний SHA (потрібен для update)
    existing = _gh_request("GET", f"data/{filename}")
    sha = existing["sha"] if existing else None

    body = {
        "message": f"update {filename}",
        "content": content,
    }
    if sha:
        body["sha"] = sha

    result = _gh_request("PUT", f"data/{filename}", body)
    if result:
        print(f"storage: saved {filename} to GitHub")
        return True
    else:
        print(f"storage: failed to save {filename} to GitHub")
        return False

def _load_local(filename):
    try:
        with open(f"/tmp/{filename}") as f:
            return json.load(f)
    except:
        return {}

def _save_local(filename, data):
    try:
        with open(f"/tmp/{filename}", "w") as f:
            json.dump(data, f)
    except:
        pass

def invalidate_cache(filename):
    _CACHE_TIME[filename] = 0

# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def load_habits():
    return _load_github("habits.json")

def save_habits(data):
    return _save_github("habits.json", data)

def load_meds():
    data = _load_github("meds.json")
    if not data:
        repo_file = os.path.join(_DIR, "meds_data.json")
        try:
            with open(repo_file) as f:
                data = json.load(f)
            save_meds(data)
        except:
            pass
    return data

def save_meds(data):
    return _save_github("meds.json", data)

def load_weight():
    data = _load_github("weight.json")
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
    return _save_github("weight.json", data)

def load_health():
    """Завантажує щоденні health дані. Структура: {"2026-04-29": {steps, sleep_hours, ...}}"""
    data = _load_github("health.json")
    if not data:
        return {}
    return data

def save_health(data):
    """Зберігає щоденні health дані."""
    return _save_github("health.json", data)

def load(filename, default=None):
    """Generic load — читає будь-який JSON файл з GitHub data/."""
    data = _load_github(filename)
    if data is None:
        return default if default is not None else {}
    return data

def save(filename, data):
    """Generic save — зберігає будь-який JSON файл в GitHub data/."""
    return _save_github(filename, data)
