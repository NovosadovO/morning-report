#!/usr/bin/env python3
"""
Persistent storage через GitHub repository.
Зберігає JSON файли в repo NovosadovO/morning-report/data/
Дані не зникають між редеплоями.
"""

import os, json, time, base64, urllib.request, urllib.parse, threading

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = "NovosadovO/morning-report"
GITHUB_API   = "https://api.github.com"

_DIR = os.path.dirname(os.path.abspath(__file__))

# Кеш в пам'яті
_CACHE = {}
_CACHE_TIME = {}
CACHE_TTL = 300  # секунд (5 хвилин — зменшує дублі при GitHub помилках)

# Глобальний лок для атомарного read-modify-write по файлах
_FILE_LOCKS: dict = {}
_FILE_LOCKS_LOCK = threading.Lock()

def _get_file_lock(filename: str):
    """Повертає RLock для конкретного файлу (singleton per filename).

    RLock, а НЕ Lock: раніше був звичайний Lock, і будь-який read-modify-write
    вигляду `with _get_file_lock(f): _load_github(f); _save_github(f, ...)`
    (response_log.log_response і подібні) намертво вішав потік — _load_github
    брав ТОЙ САМИЙ лок повторно. Через це кнопки під сповіщеннями "вмирали":
    потік висів у дедлоці, ні результату, ні помилки в логах.
    """
    with _FILE_LOCKS_LOCK:
        if filename not in _FILE_LOCKS:
            _FILE_LOCKS[filename] = threading.RLock()
        return _FILE_LOCKS[filename]

DATA_BRANCH = "data"  # окрема гілка для даних — не тригерить Railway редеплой

# ─── KEEP-ALIVE ────────────────────────────────────────────────────────────────
# Раніше кожен GET/PUT відкривав НОВИЙ TLS-конект до api.github.com (~0.3-0.5 с
# на рукостискання). Один клік по кнопці = GET + PUT, тобто секунда чистого
# очікування ще до того, як щось збережеться. Тримаємо одну сесію на процес.
_GH_SESSION = None


def _gh_sess():
    global _GH_SESSION
    if _GH_SESSION is None:
        try:
            import requests
            from requests.adapters import HTTPAdapter
            s = requests.Session()
            s.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=8,
                                            max_retries=0))
            _GH_SESSION = s
        except Exception:
            _GH_SESSION = False
    return _GH_SESSION or None


def _http(method, url, headers, data=None, timeout=15):
    """(status, text). Через keep-alive сесію, з відкатом на urllib."""
    sess = _gh_sess()
    if sess is not None:
        try:
            r = sess.request(method, url, headers=headers, data=data,
                             timeout=timeout)
            return r.status_code, r.text
        except Exception as e:
            print(f"GitHub sess {method} error: {e} — відкат на urllib")
    try:
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200, r.read().decode()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode()
        except Exception:
            return e.code, ""
    except Exception as e:
        return 0, str(e)


# SHA файлів, щоб не робити зайвий GET перед кожним PUT (економія одного кола)
_SHA = {}


def _gh_request(method, path, body=None):
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    if method == "GET":
        url += f"?ref={DATA_BRANCH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "morning-report-bot"
    }
    if body and method == "PUT":
        body["branch"] = DATA_BRANCH
    data = json.dumps(body).encode() if body else None
    code, text = _http(method, url, headers, data)
    if code == 404:
        return None
    if code != 200 and code != 201:
        print(f"GitHub {method} {path} error {code}: {str(text)[:200]}")
        return None
    try:
        return json.loads(text)
    except Exception as e:
        print(f"GitHub {method} {path} parse error: {e}")
        return None


def _gh_get(path, tries=3):
    """GET з розрізненням «файлу немає» і «GET впав».

    Повертає ("ok", json) | ("missing", None) | ("error", None).
    Було: будь-яка помилка GET -> None -> PUT без sha -> 422 «"sha" wasn't
    supplied» і перезапис/шум у логах. Тепер при transient-помилці ми НЕ
    робимо PUT без sha, а повторюємо спробу.
    """
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}?ref={DATA_BRANCH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "morning-report-bot",
    }
    for i in range(tries):
        code, text = _http("GET", url, headers)
        if code in (200, 201):
            try:
                js = json.loads(text)
            except Exception as e:
                print(f"GitHub GET {path} parse error: {e}")
                return "error", None
            try:
                _SHA[path.split("/")[-1]] = js.get("sha")
            except Exception:
                pass
            return "ok", js
        if code == 404:
            return "missing", None
        if code in (403, 408, 429) or code >= 500 or code == 0:
            time.sleep(0.4 * (2 ** i))
            continue
        print(f"GitHub GET {path} error {code}")
        return "error", None
    return "error", None


def _load_github(filename):
    """Читає JSON файл з GitHub repo. Thread-safe через file lock."""
    lock = _get_file_lock(filename)
    with lock:
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
    """Зберігає JSON файл в GitHub repo. Retry при 409 conflict. Thread-safe."""
    lock = _get_file_lock(filename)
    with lock:
        _CACHE[filename] = data
        _CACHE_TIME[filename] = time.time()

        # Також локально
        _save_local(filename, data)

    payload = data

    for attempt in range(5):  # 5 спроб з exponential backoff
        existing = None
        # Перша спроба: якщо sha цього файлу вже відомий з попередньої
        # операції — не витрачаємо ще одне коло на GET. Якщо sha застарів,
        # PUT віддасть 409/422 і наступна спроба піде звичайним шляхом.
        sha = _SHA.get(filename) if attempt == 0 else None
        if sha is None:
            state, existing = _gh_get(f"data/{filename}")
            if state == "error":
                # GET впав — PUT без sha перезаписав би файл / дав 422.
                time.sleep(0.5 * (2 ** attempt))
                continue
            sha = existing["sha"] if existing else None

        # ─── MERGE-ON-CONFLICT ────────────────────────────────────────────
        # Раніше при 409 (інший потік записав файл між нашим GET і PUT) ми
        # просто перезаписували файл своїм знімком — і ключі, які встиг
        # додати інший потік, ЗНИКАЛИ. Саме так губились відповіді на кнопки.
        # Тепер на повторній спробі беремо свіжий стан як базу і накладаємо
        # свої ключі поверх — нічиї дані не втрачаються.
        if attempt > 0 and isinstance(data, dict) and existing:
            try:
                remote = json.loads(base64.b64decode(existing["content"]).decode())
                if isinstance(remote, dict):
                    merged = dict(remote)
                    merged.update(data)
                    if merged != payload:
                        print(f"🔀 [storage] merge {filename}: "
                              f"+{len(set(remote) - set(data))} чужих ключів збережено")
                    payload = merged
                    lock2 = _get_file_lock(filename)
                    with lock2:
                        _CACHE[filename] = payload
                        _CACHE_TIME[filename] = time.time()
            except Exception as _me:
                print(f"[storage] merge {filename} skipped: {_me}")

        content = base64.b64encode(
            json.dumps(payload, ensure_ascii=False, indent=2).encode()).decode()

        body = {
            "message": f"update {filename}",
            "content": content,
        }
        if sha:
            body["sha"] = sha

        result = _gh_request("PUT", f"data/{filename}", body)
        if result:
            try:
                _SHA[filename] = (result.get("content") or {}).get("sha")
            except Exception:
                _SHA.pop(filename, None)
            print(f"✅ [storage] SAVED {filename} to GitHub (attempt {attempt+1}/5)")
            return True
        else:
            _SHA.pop(filename, None)
            wait_time = 0.5 * (2 ** attempt)  # 0.5s, 1s, 2s, 4s, 8s
            if attempt >= 2:
                print(f"⚠️ [storage] {filename}: спроба {attempt+1}/5, чекаю {wait_time}s")
            time.sleep(wait_time)

    print(f"❌ [storage] GAVE UP saving {filename} after 5 attempts")
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
    _SHA.pop(filename, None)

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

def load_meds_sent():
    return _load_github("meds_sent.json") or {}

def save_meds_sent(data):
    return _save_github("meds_sent.json", data)

def load_weight():
    """
    Канонічне джерело — weight_data.json (сюди пише weight.py при кожному /вазі
    від Олега, тут завжди АКТУАЛЬНІ дані). weight.json — старий/покинутий файл
    (востаннє оновлювався у квітні) і використовується лише як довіджерело для
    ДАТ, яких немає у weight_data.json (щоб не втратити стару історію).
    Раніше 14+ місць у monitor.py читали weight.json напряму → AI бачив вагу
    3-місячної давності. Фікс тут виправляє це одразу для всіх викликів.
    """
    canonical = _load_github("weight_data.json") or {}
    legacy = _load_github("weight.json") or {}
    if not canonical and not legacy:
        initial = os.path.join(_DIR, "weight_data_initial.json")
        try:
            with open(initial) as f:
                canonical = json.load(f)
            save_weight(canonical)
        except:
            pass
    merged = dict(legacy)
    merged.update(canonical)  # canonical (weight_data.json) виграє при перетині дат
    return merged

def save_weight(data):
    """Пише в weight_data.json — канонічний файл, який читає load_weight()."""
    return _save_github("weight_data.json", data)

def load_health():
    """
    Завантажує щоденні health дані. Структура: {"2026-04-29": {steps, sleep_hours, ...}}

    Канонічне джерело кроків/сну — qwatch_data.json (пише годинник, оновлюється
    щодня). health.json — старий Apple Health формат, востаннє оновлювався
    2026-05-11 і більше не пишеться, тому раніше steps/sleep завжди виходили
    None у звітах/AI-контекстах. Тут мерджимо qwatch поверх health.json
    (qwatch виграє при перетині дат), конвертуючи поля під очікувану схему
    (sleep_total_min -> sleep_hours, weight_kg лишається як є).
    """
    legacy = _load_github("health.json") or {}
    qwatch = _load_github("qwatch_data.json") or {}
    merged = dict(legacy)
    for day, e in qwatch.items():
        if not isinstance(e, dict):
            continue
        conv = dict(merged.get(day) or {})
        if e.get("steps"):
            conv["steps"] = e["steps"]
        if e.get("sleep_total_min"):
            conv["sleep_hours"] = round(e["sleep_total_min"] / 60, 1)
        if e.get("weight_kg"):
            conv["weight_kg"] = e["weight_kg"]
        if e.get("hr_avg"):
            conv["hr_avg"] = e["hr_avg"]
        if e.get("hrv"):
            conv["hrv"] = e["hrv"]
        if e.get("spo2"):
            conv["spo2"] = e["spo2"]
        merged[day] = conv
    return merged

def save_health(data):
    """Зберігає щоденні health дані."""
    return _save_github("health.json", data)

def load_price_history():
    """Завантажує price history для крипто графіка. Структура: {cg_id: [[ts, price], ...]}"""
    data = _load_github("price_history_30d.json")
    return data if data else {}

def save_price_history(data):
    """Зберігає price history для крипто графіка."""
    return _save_github("price_history_30d.json", data)

def load(filename, default=None):
    """Generic load — читає будь-який JSON файл з GitHub data/."""
    data = _load_github(filename)
    if data is None:
        return default if default is not None else {}
    return data

def save(filename, data):
    """Generic save — зберігає будь-який JSON файл в GitHub data/."""
    return _save_github(filename, data)

def update_key(filename, key, value, default=None):
    """Атомарно ставить data[key] = value для JSON-файлу (load+modify+save під
    ОДНИМ file lock, з re-fetch+re-apply на кожній спробі — без цього кілька
    фонових потоків, що одночасно додають СВОЇ ключі в один файл (напр.
    draft_store.json — кнопки календаря/покупок), могли губити записи одне
    одного (read-modify-write race: A і B обидва читають стару версію, A
    зберігає, B зберігає БЕЗ бачення зміни A — зміна A губиться)."""
    lock = _get_file_lock(filename)
    with lock:
        for attempt in range(5):
            state, existing = _gh_get(f"data/{filename}")
            if state == "error":
                time.sleep(0.5 * (2 ** attempt))
                continue
            if existing:
                try:
                    data = json.loads(base64.b64decode(existing["content"]).decode())
                except Exception:
                    data = {}
                sha = existing["sha"]
            else:
                data = _load_local(filename) or {}
                sha = None
            if not isinstance(data, dict):
                data = {}
            data[key] = value

            _CACHE[filename] = data
            _CACHE_TIME[filename] = time.time()
            _save_local(filename, data)

            body = {
                "message": f"update {filename}",
                "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode(),
            }
            if sha:
                body["sha"] = sha
            result = _gh_request("PUT", f"data/{filename}", body)
            if result:
                return True
            time.sleep(0.5 * (2 ** attempt))
        return False

def remove_key(filename, key):
    """Атомарно видаляє data[key] (симетрично до update_key) — той самий
    захист від гонки при видаленні ключа одночасно з додаванням іншого."""
    lock = _get_file_lock(filename)
    with lock:
        for attempt in range(5):
            state, existing = _gh_get(f"data/{filename}")
            if state == "error":
                time.sleep(0.5 * (2 ** attempt))
                continue
            if existing:
                try:
                    data = json.loads(base64.b64decode(existing["content"]).decode())
                except Exception:
                    data = {}
                sha = existing["sha"]
            else:
                data = _load_local(filename) or {}
                sha = None
            if not isinstance(data, dict):
                data = {}
            removed = data.pop(key, None)

            _CACHE[filename] = data
            _CACHE_TIME[filename] = time.time()
            _save_local(filename, data)

            body = {
                "message": f"update {filename}",
                "content": base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode()).decode(),
            }
            if sha:
                body["sha"] = sha
            result = _gh_request("PUT", f"data/{filename}", body)
            if result:
                return removed
            time.sleep(0.5 * (2 ** attempt))
        return None
