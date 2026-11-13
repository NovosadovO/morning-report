#!/usr/bin/env python3
"""
outbox.py — єдиний фільтр ВСІХ проактивних повідомлень у Telegram.

Скарга Олега (06.11): «сповіщення дуже схожі за змістом, бот пише стандартними
шаблонами про одні й ті самі теми; приходять питання, на які я вже відповів».

Повідомлення шле ~35 різних місць коду (AI і шаблони), єдиної точки відправки
нема. Тому ставимо перехоплювач на urllib.request.urlopen: усе, що йде на
api.telegram.org/.../sendMessage, проходить тут. Це не коштує жодного AI-виклику.

Що робить для ПРОАКТИВНИХ повідомлень (відповіді на дії Олега не чіпає):
  1. Дубль по змісту за 20 год: ті самі слова/3-грами І (цифр нема або вони ті ж).
     Звіт зі зміненими цифрами проходить, шаблон «пий воду» вдруге — ні.
  2. Питання, на яке Олег уже відповів (текстом або кнопкою) — не надсилаємо.
  3. Термінове (🔴, 🚨, помилка, через годину…) і короткі тексти — проходять завжди.
Також запам'ятовує, на яке питання бота Олег відповів вільним текстом
(link_answer), щоб AI і фільтр це знали.

Стан: пам'ять + storage (outbox_log.json), запис не частіше разу на 2 хв.
API: install(), link_answer(text, reply_to_text=""), report()
"""
import io
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TAG = "outbox"
LOG_FILE = "outbox_log.json"
DUP_HOURS = 20
ANSWERED_DAYS = 10
Q_WAIT_HOURS = 8            # відповідь вільним текстом прив'язуємо до питання не старішого за це
MIN_CHECK_LEN = 60          # коротші тексти не перевіряємо на дубль
FLUSH_GAP = 120
SIM_FP = 0.55
SIM_WORDS = 0.60
NUMS_SAME = 0.60

_lock = threading.Lock()
_state = {"data": None, "ts": 0.0, "dirty": False, "flushed": 0.0}
_orig_urlopen = [None]


def _log(m):
    print("[outbox] " + str(m), flush=True)


def _now():
    return datetime.now()


def _load():
    if _state["data"] is not None and time.time() - _state["ts"] < 300:
        return _state["data"]
    data = {}
    try:
        import storage
        data = storage.load(LOG_FILE, default={}) or {}
    except Exception as e:
        _log("load error: " + str(e))
    if not isinstance(data, dict):
        data = {}
    for k in ("sent", "qs", "drops"):
        if not isinstance(data.get(k), list):
            data[k] = []
    if not isinstance(data.get("stats"), dict):
        data["stats"] = {}
    _state.update({"data": data, "ts": time.time()})
    return data


def _flush(force=False):
    if not _state["dirty"]:
        return
    if not force and time.time() - _state["flushed"] < FLUSH_GAP:
        return
    try:
        import storage
        d = _state["data"]
        d["sent"] = d["sent"][-40:]
        d["qs"] = d["qs"][-40:]
        d["drops"] = d["drops"][-30:]
        d["stats"] = dict(sorted(d["stats"].items())[-14:])
        storage.save(LOG_FILE, d)
        _state["dirty"] = False
        _state["flushed"] = time.time()
    except Exception as e:
        _log("flush error: " + str(e))


def _bump(key):
    day = _now().strftime("%Y-%m-%d")
    st = _load()["stats"].setdefault(day, {})
    st[key] = st.get(key, 0) + 1


def _clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(t or ""))).strip()


def _words(t):
    t = _clean(t).lower()
    return set(x for x in "".join(c if (c.isalnum() or c == " ") else " " for c in t).split()
               if len(x) > 2)


def _overlap(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(min(len(a), len(b)))


def _question_line(text):
    try:
        import autokb
        return autokb._question_line(text)
    except Exception:
        for line in reversed([l.strip() for l in _clean(text).split("\n") if l.strip()]):
            if "?" in line and len(line) > 8:
                return line[:300]
    return ""


def _is_urgent(text):
    low = _clean(text).lower()
    try:
        import autoquiet
        hints = autoquiet.URGENT_HINTS
    except Exception:
        hints = ("🔴", "🚨", "термінов", "помилка")
    return any(h in str(text).lower() or h in low for h in hints)


def _is_user_context():
    try:
        import quiet
        return bool(quiet.is_user_thread() or quiet.user_recently_active())
    except Exception:
        return False


# ─── ВИТЯГ ТЕКСТУ ІЗ ЗАПИТУ ──────────────────────────────────────────────────

def _extract(url, data):
    """-> (text, is_reply) для sendMessage; (None, False) для всього іншого."""
    if "api.telegram.org" not in url or "/sendMessage" not in url:
        return None, False
    body = {}
    try:
        if isinstance(data, (bytes, bytearray)) and data:
            raw = bytes(data).decode("utf-8", "ignore")
            if raw.lstrip().startswith("{"):
                body = json.loads(raw)
            else:
                body = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
        elif "?" in url:
            body = {k: v[0] for k, v in urllib.parse.parse_qs(url.split("?", 1)[1]).items()}
    except Exception:
        return None, False
    text = body.get("text")
    if not text:
        return None, False
    return str(text), bool(body.get("reply_to_message_id"))


# ─── РІШЕННЯ ──────────────────────────────────────────────────────────────────

def _facts(text):
    import variety
    nums, ws = variety.facts_of(text)
    return variety.fingerprint(text), nums, ws


def _dup_reason(text, rows):
    low = _clean(text)
    if len(low) < MIN_CHECK_LEN:
        return ""
    try:
        import variety
    except Exception:
        return ""
    fp, nums, ws = _facts(text)
    cut = (_now() - timedelta(hours=DUP_HOURS)).isoformat(timespec="seconds")
    op = variety._norm(variety.opening(text))[:40]
    for r in rows:
        if str(r.get("ts") or "") < cut:
            continue
        rn = set(r.get("nums") or [])
        if len(nums) >= 2 and len(rn) >= 2:
            same_nums = len(set(nums) & rn) / float(max(len(nums), len(rn))) >= NUMS_SAME
        else:
            same_nums = True
        if not same_nums:
            continue
        s_fp = variety.similarity(fp, r.get("fp"))
        s_w = variety.similarity(ws, r.get("w"))
        if s_fp >= SIM_FP or s_w >= SIM_WORDS:
            return "той самий зміст (" + str(int(max(s_fp, s_w) * 100)) + "%)"
        if op and len(op) > 20 and op == str(r.get("op") or "") and s_w >= 0.35:
            return "той самий зачин і схожі слова"
    return ""


# Теми-«ворота» поверх змісту: різні модулі шлють одне й те саме іншими словами
# (13.11: «СПРОБУЙ НОВЕ» ×6 за 4 год, «6 днів без пробіжки» ×3, HRV-картка ×2).
# Збіг по змісту їх не ловить, тому — кулдаун на ТЕМУ незалежно від джерела.
TOPIC_RULES = [
    ("trynew", r"спробуй нове", 72),
    ("run_ai", r"^[^\w]*біг\s*[·•]", 3),
    ("run_nudge", r"^[^\w]*(час бігти|\d+ дн\w* без пробіжк|олеже, біговий трекер мовчить)", 10),
    ("hrv_card", r"^[💡🫀❤️\s]*(твій )?(hrv|порада на день)", 6),
    ("weigh", r"^[^\w]*(час зважитись|зваж себе|дані про вагу)", 12),
    ("garmin_sync", r"garmin connect\s*[—-]", 3),
    ("cal_prompt", r"додати в календар\?", 20),
    ("free_day", r"вільний день\s*[—-]\s*що", 24),
    ("plan_today", r"що плануєш на (<b>)?сьогодні|що плануєш на завтра|щось ще плануєш", 14),
    ("cal_today_list", r"події з календаря сьогодні", 24),
    ("nowexp", r"досить сидіти|розім.яти|вип.й води|час випити воду", 4),
]


def _topic(text):
    low = _clean(text).lower()
    for name, rx, hrs in TOPIC_RULES:
        try:
            if re.search(rx, low[:400]):
                return name, hrs
        except Exception:
            continue
    return "", 0


# тема -> (джерело, годин): шаблонні нагадування мовчать після AI-розбору того ж
CROSS_BLOCK = {"run_nudge": ("run_ai", 24)}


def _topic_reason(text, rows):
    name, hrs = _topic(text)
    if not name:
        return ""
    src = CROSS_BLOCK.get(name)
    if src:
        cut2 = (_now() - timedelta(hours=src[1])).isoformat(timespec="seconds")
        for r in rows:
            if r.get("topic") == src[0] and str(r.get("ts") or "") >= cut2:
                return ("уже був AI-розбір бігу о " + str(r.get("ts"))[11:16]
                        + " — шаблонне нагадування зайве")
    cut = (_now() - timedelta(hours=hrs)).isoformat(timespec="seconds")
    for r in rows:
        if r.get("topic") == name and str(r.get("ts") or "") >= cut:
            return "тема «" + name + "» вже була о " + str(r.get("ts"))[11:16] + " (пауза " + str(hrs) + " год)"
    return ""


def _answered_reason(text, qs):
    q = _question_line(text)
    if not q:
        return ""
    qw = _words(q)
    if not qw:
        return ""
    cut = (_now() - timedelta(days=ANSWERED_DAYS)).isoformat(timespec="seconds")
    for r in qs:
        if not r.get("ans") or str(r.get("ts") or "") < cut:
            continue
        if _overlap(qw, set(r.get("qw") or [])) >= 0.6:
            return "Олег уже відповів: «" + str(r.get("ans"))[:40] + "»"
    try:
        import askme
        key = askme.answered_similar(q)
        if key:
            return "Олег уже відповів кнопкою"
    except Exception:
        pass
    return ""


def _record(text):
    d = _load()
    fp, nums, ws = _facts(text)
    try:
        import variety
        op = variety._norm(variety.opening(text))[:40]
    except Exception:
        op = ""
    d["sent"].append({"ts": _now().isoformat(timespec="seconds"), "open": _clean(text)[:70],
                      "op": op, "fp": fp, "nums": nums, "w": ws,
                      "topic": _topic(text)[0]})
    q = _question_line(text)
    if q:
        d["qs"].append({"ts": _now().isoformat(timespec="seconds"), "q": q[:200],
                        "qw": sorted(_words(q))[:30], "ans": ""})
    _state["dirty"] = True


def check_request(url, data):
    """True -> повідомлення треба проковтнути."""
    text, is_reply = _extract(url, data)
    if text is None:
        return False
    if is_reply or _is_user_context():
        return False          # відповідь на дію Олега
    with _lock:
        d = _load()
        why = ""
        if not _is_urgent(text):
            why = (_answered_reason(text, d["qs"]) or _topic_reason(text, d["sent"])
                   or _dup_reason(text, d["sent"]))
        if why:
            _bump("dropped")
            d["drops"].append({"ts": _now().isoformat(timespec="seconds"),
                               "why": why, "open": _clean(text)[:80]})
            _state["dirty"] = True
            _log("🚫 не надсилаю: " + why + " | " + _clean(text)[:60])
            _flush()
            return True
        _bump("sent")
        _record(text)
        _flush()
    return False


# ─── ВІДПОВІДІ ОЛЕГА ──────────────────────────────────────────────────────────

def link_answer(text, reply_to_text=""):
    """Олег написав вільний текст: до якого питання бота це відповідь?
    Повертає текст питання або ''. Питання позначається закритим."""
    try:
        t = _clean(text)
        if len(t) < 2 or t.startswith("/"):
            return ""
        with _lock:
            d = _load()
            q = ""
            if reply_to_text:
                q = _question_line(reply_to_text)
            if q:
                qw = _words(q)
                d["qs"].append({"ts": _now().isoformat(timespec="seconds"), "q": q[:200],
                                "qw": sorted(qw)[:30], "ans": t[:120]})
                _state["dirty"] = True
                _flush()
                return q
            cut = (_now() - timedelta(hours=Q_WAIT_HOURS)).isoformat(timespec="seconds")
            for r in reversed(d["qs"]):
                if r.get("ans") or str(r.get("ts") or "") < cut:
                    continue
                r["ans"] = t[:120]
                _state["dirty"] = True
                _flush()
                return str(r.get("q") or "")
    except Exception as e:
        _log("link_answer error: " + str(e))
    return ""


# ─── ПЕРЕХОПЛЮВАЧ ─────────────────────────────────────────────────────────────

class _Fake(io.BytesIO):
    status = 200
    code = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return 200


def _patched(url, data=None, *a, **k):
    try:
        u = url.full_url if hasattr(url, "full_url") else str(url)
        d = data if data is not None else getattr(url, "data", None)
        if check_request(u, d):
            return _Fake(json.dumps({"ok": True, "result": {"message_id": 0}}).encode())
    except Exception as e:
        _log("hook error (fail-open): " + str(e))
    return _orig_urlopen[0](url, data, *a, **k)


def install():
    if _orig_urlopen[0] is not None:
        return False
    _orig_urlopen[0] = urllib.request.urlopen
    urllib.request.urlopen = _patched
    _log("перехоплювач проактивних повідомлень увімкнено")
    return True


def report():
    d = _load()
    rows = ["📬 <b>ФІЛЬТР СПОВІЩЕНЬ</b>"]
    for day, st in sorted(d["stats"].items())[-5:]:
        rows.append(f"{day}: надіслано {st.get('sent', 0)}, відсіяно {st.get('dropped', 0)}")
    if d["drops"]:
        rows.append("\nОстанні відсіяні:")
        for r in d["drops"][-8:]:
            rows.append("• " + str(r.get("ts"))[5:16].replace("T", " ") + " — " +
                        str(r.get("why")) + ": " + str(r.get("open"))[:50])
    return "\n".join(rows)
