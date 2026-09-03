#!/usr/bin/env python3
"""
AI KIT — спільна інфраструктура для модулів автоматизації життя.

Використовується bills_watcher.py, run_planner.py, shift_schedule.py,
followup_watcher.py, weekly_review.py.

Чому окремий модуль: у кожному з них потрібне одне й те саме —
  • Telegram sendMessage з inline-кнопками (+ фолбек на plain text);
  • Gemini через monitor._gem_post (retry + model-fallback + rate-limit);
  • persistent payload кнопок у storage (гілка data) — щоб кнопки жили
    після рестарту Railway;
  • дедуп «це вже пропонував» з TTL у днях;
  • створення події в Google Calendar.

ПРАВИЛО ПРОЄКТУ: мертвих кнопок не буває. Якщо payload зник — кнопка
повертає {"ok": False, "error": "payload_missing"} і UI це показує.
"""

import os
import re
import json
import uuid
import urllib.request
from datetime import datetime, timezone, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
import sys
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

TZ = timedelta(hours=2)  # Europe/Bratislava


def now():
    """Локальний час Олега (UTC+2), naive-aware."""
    return datetime.now(timezone.utc) + TZ


def today_str():
    return now().strftime("%Y-%m-%d")


def log(tag, msg):
    print(f"[{tag}] {msg}", flush=True)


# ─── STORAGE ─────────────────────────────────────────────────────────────────

def st():
    import storage
    return storage


def load(filename, default=None):
    try:
        return st().load(filename, default=default)
    except Exception as e:
        log("ai_kit", f"load {filename} error: {e}")
        return default


def save(filename, data):
    try:
        st().save(filename, data)
        return True
    except Exception as e:
        log("ai_kit", f"save {filename} error: {e}")
        return False


def update_key(filename, key, value):
    try:
        st().update_key(filename, key, value)
        return True
    except Exception as e:
        log("ai_kit", f"update_key {filename}.{key} error: {e}")
        return False


def remove_key(filename, key):
    try:
        st().remove_key(filename, key)
        return True
    except Exception:
        return False


class PayloadStore:
    """Persistent payload кнопок: pid -> dict. Живе у гілці data."""

    def __init__(self, filename):
        self.filename = filename

    def put(self, payload: dict) -> str:
        pid = uuid.uuid4().hex[:10]
        payload = dict(payload)
        payload["ts"] = now().isoformat()
        update_key(self.filename, pid, payload)
        return pid

    def get(self, pid: str):
        data = load(self.filename, default={}) or {}
        return data.get(pid)

    def drop(self, pid: str):
        remove_key(self.filename, pid)

    def gc(self, days: int = 14):
        """Прибирає payload старші за N днів, щоб файл не ріс безмежно."""
        data = load(self.filename, default={}) or {}
        if not data:
            return 0
        cutoff = now().replace(tzinfo=None) - timedelta(days=days)
        dead = []
        for pid, p in data.items():
            try:
                ts = datetime.fromisoformat(str(p.get("ts", ""))).replace(tzinfo=None)
                if ts < cutoff:
                    dead.append(pid)
            except Exception:
                continue
        for pid in dead:
            remove_key(self.filename, pid)
        return len(dead)


class Dedup:
    """«Це я вже пропонував» — ключ -> ISO-час, з TTL у днях."""

    def __init__(self, filename, ttl_days=6):
        self.filename = filename
        self.ttl_days = ttl_days

    @staticmethod
    def key(*parts) -> str:
        base = "|".join(str(p or "").lower().strip()[:60] for p in parts)
        return re.sub(r"[^a-z0-9а-яіїєґ|\-]+", "_", base)

    def seen(self, *parts) -> bool:
        data = load(self.filename, default={}) or {}
        ts = data.get(self.key(*parts))
        if not ts:
            return False
        try:
            then = datetime.fromisoformat(str(ts)).replace(tzinfo=None)
        except Exception:
            return False
        return (now().replace(tzinfo=None) - then).days < self.ttl_days

    def mark(self, *parts):
        update_key(self.filename, self.key(*parts), now().isoformat())

    def count_today(self) -> int:
        data = load(self.filename, default={}) or {}
        t = today_str()
        return sum(1 for v in data.values() if isinstance(v, str) and v.startswith(t))


def rate_ok(state_file: str, min_gap_min: int) -> bool:
    """Чи пройшло достатньо часу від останнього запуску."""
    state = load(state_file, default={}) or {}
    last = state.get("last")
    if not last:
        return True
    try:
        then = datetime.fromisoformat(str(last)).replace(tzinfo=None)
    except Exception:
        return True
    gap = (now().replace(tzinfo=None) - then).total_seconds() / 60
    return gap >= min_gap_min


def rate_mark(state_file: str):
    update_key(state_file, "last", now().isoformat())


# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def tg(method: str, body: dict, tag="ai_kit"):
    # ЧЕРГА ПЕРЕД GUARD-ом: під час /тиша нічого не губиться — чекає до 04:00.
    try:
        import autoquiet as _aq
        if method == "sendMessage":
            _txt = str((body or {}).get("text") or "")
            if _aq.should_hold(_txt):
                _aq.hold(_txt, kind=tag)
                return False
    except Exception as _aqe:
        print(f"[autoquiet] tg skipped: {_aqe}", flush=True)
    # quiet-guard: режим тиші (/тиша) — жодних сповіщень до 04:00
    try:
        import quiet as _q_g
        if _q_g.blocked("msg"):
            print("[quiet] 🌙 тиша: tg придушено", flush=True)
            return False
    except Exception:
        pass
    if not TELEGRAM_TOKEN:
        log(tag, "TELEGRAM_TOKEN відсутній")
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except Exception as e:
        try:
            err = e.read().decode()[:300]
        except Exception:
            err = str(e)
        log(tag, f"TG {method} error: {err}")
        return None


def send_card(text: str, keyboard=None, tag="ai_kit", chat_id=None) -> bool:
    """HTML-повідомлення з кнопками; при збої HTML — plain text (кнопки важливіші)."""
    body = {
        "chat_id": chat_id or TELEGRAM_CHAT,
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard is None:
        # Під КОЖНИМ повідомленням — кнопки, доречні саме його змісту:
        # питання → варіанти відповіді на це питання (пам'ять назавжди),
        # сповіщення → дії під його вид. Модуль зі своїми кнопками лишається
        # зі своїми. Питання, на яке Олег уже відповів, не надсилаємо взагалі.
        try:
            import autokb as _akb
            if not _akb.should_send(text, tag):
                return True
            keyboard = _akb.build(text, tag)
        except Exception as _e:
            log(tag, "autokb skip: " + str(_e))
            try:
                import react as _rx
                keyboard = _rx.keyboard(_rx.detect(tag, text),
                                        title=_rx._first_line(text))
            except Exception as _e2:
                log(tag, "react keyboard skip: " + str(_e2))
    if keyboard:
        body["reply_markup"] = {"inline_keyboard": keyboard}
    res = tg("sendMessage", body, tag=tag)
    if not res or not res.get("ok"):
        body.pop("parse_mode", None)
        body["text"] = re.sub(r"<[^>]+>", "", text)[:4000]
        res = tg("sendMessage", body, tag=tag)
    return bool(res and res.get("ok"))


# ─── GEMINI ──────────────────────────────────────────────────────────────────

def _gem_call(prompt: str, max_tokens: int, temperature: float, tag: str):
    if not GEMINI_KEY:
        log(tag, "GEMINI_API_KEY відсутній")
        return ""
    try:
        from monitor import _gem_post
    except Exception as e:
        log(tag, f"no _gem_post: {e}")
        return ""
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash:generateContent?key={GEMINI_KEY}")
    try:
        resp = _gem_post(url, body, timeout=90, tag=tag, max_retries=3)
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        log(tag, f"gemini error: {e}")
        return ""


def gemini_text(prompt: str, max_tokens: int = 1400, temperature: float = 0.7,
                tag: str = "ai_kit") -> str:
    return _gem_call(prompt, max_tokens, temperature, tag)


def gemini_json(prompt: str, max_tokens: int = 1400, temperature: float = 0.5,
                tag: str = "ai_kit", want="list"):
    """Просить Gemini JSON, чистить markdown-обгортку, парсить. [] / {} при збої."""
    raw = _gem_call(prompt, max_tokens, temperature, tag)
    empty = [] if want == "list" else {}
    if not raw:
        return empty
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
    pat = r"\[.*\]" if want == "list" else r"\{.*\}"
    m = re.search(pat, raw, re.DOTALL)
    if not m:
        return empty
    try:
        out = json.loads(m.group(0))
    except Exception as e:
        log(tag, f"json parse error: {e} | raw={raw[:200]}")
        return empty
    if want == "list" and not isinstance(out, list):
        return empty
    if want == "dict" and not isinstance(out, dict):
        return empty
    return out


# ─── CALENDAR ────────────────────────────────────────────────────────────────

def calendar_event(summary: str, start_dt: datetime, end_dt: datetime = None,
                   description: str = "", force: bool = False) -> dict:
    """Створює подію в Google Calendar Олега. Рекламу не пускає.
    force=True — Олег уже дав дозвіл кнопкою, ворота calgate пропускаємо."""
    try:
        import askme as _am_g
        if _am_g.is_promo(str(summary) + " " + str(description or "")):
            log("ai_kit", "календар: реклама відкинута — " + str(summary)[:70])
            return {"ok": False, "error": "promo blocked"}
    except Exception:
        pass
    try:
        import context as _ctx
        return _ctx.create_calendar_event(
            summary=summary, start_dt=start_dt, end_dt=end_dt,
            description=description, force=force)
    except Exception as e:
        return {"ok": False, "error": str(e)}


def events_for_day(offset: int = 0) -> list:
    """Події конкретного дня (offset у днях від сьогодні). [] якщо недоступно."""
    try:
        import context as _ctx
        token = _ctx._get_token()
        if not token:
            return []
        return _ctx._fetch_events_for_day(token, offset) or []
    except Exception as e:
        log("ai_kit", f"events_for_day({offset}) error: {e}")
        return []


_SHIFT_EARLY = ("рання", "ранкова", "early", "☀️", "denna", "denná")
_SHIFT_NIGHT = ("нічна", "ночна", "night", "🌙", "nocna", "nočná")


def classify_shift(events: list) -> str:
    """'early' | 'night' | 'free' — за назвами подій дня."""
    for ev in events or []:
        s = str(ev.get("summary", "")).lower()
        if any(x in s for x in _SHIFT_NIGHT):
            return "night"
        if any(x in s for x in _SHIFT_EARLY):
            return "early"
    return "free"


def shift_map(days: int = 7) -> dict:
    """{'YYYY-MM-DD': 'early'|'night'|'free'} на N днів вперед (з календаря)."""
    out = {}
    for off in range(days):
        day = (now() + timedelta(days=off)).strftime("%Y-%m-%d")
        out[day] = classify_shift(events_for_day(off))
    return out


def parse_dt(date_str: str, time_str: str = "09:00") -> datetime:
    time_str = (time_str or "09:00").strip()
    if not re.match(r"^\d{1,2}:\d{2}$", time_str):
        time_str = "09:00"
    return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")


def valid_future_date(d: str, allow_today=True) -> str:
    d = (d or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return ""
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return ""
    today = now().date()
    if dt < today or (dt == today and not allow_today):
        return ""
    return d


def esc(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
