#!/usr/bin/env python3
"""
react.py — КЛАВІШІ ВІДПОВІДІ ПІД КОЖНИМ СПОВІЩЕННЯМ + ПАМ'ЯТЬ ПРО РЕАКЦІЇ.

Що просив Олег:
  1) під кожним сповіщенням — кнопки, доречні саме цьому сповіщенню;
  2) AI має ці натискання ЗАПИСУВАТИ і РОЗУМІТИ;
  3) про те, на що він уже відреагував, — більше не нагадувати і не тягнути
     в звіти як «нове»;
  4) тільки реальна, достовірна, актуальна інформація — нічого не вигадувати.

Як це працює
------------
KINDS — набір кнопок під вид сповіщення (лист, рахунок, дедлайн, підписка,
подія, здоров'я, біг, крипто, звичка, загальне). Вид визначається або прямо
(kind=...), або з тегу/тексту через detect().

Натискання пишеться у reactions.json НАЗАВЖДИ (гілка data):
    {kind, key, tkey, title, action, label, ts, state}
state:
    closed  — тема закрита (зробив/оплатив/не нагадуй/не піду) → тиша назавжди
    snoozed — «пізніше» → тиша до until
    open    — реакція записана, але тема жива (наприклад «спостерігаю»)

Закриття теми ДОДАТКОВО йде в dismissed.mute() — а його вже питають 50 місць
у коді перед кожною відправкою. Тому «не нагадуй» починає працювати одразу
в усьому боті, без правок кожного відправника.

AI-розуміння: block() віддає текст із реальними реакціями за 14 днів, а
inject() підмішує його в КОЖЕН не-JSON промпт Gemini (як nowctx/ai_brain).
Порожньої вигадки немає: немає реакцій — немає блоку.

API:
    keyboard(kind, key="", title="", extra=None) -> rows
    card(text, kind, key="", title="", tag="", extra=None) -> bool
    handle(data, cb) -> {text, alert, keyboard}
    is_closed(kind=None, key=None, title=None) -> bool
    last(kind=None, key=None, title=None) -> dict | None
    block() / inject(body_bytes, tag)
    report() -> текст для /реакції
"""
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K  # noqa: E402

TAG = "react"
FILE = "reactions.json"
STORE_FILE = "react_store.json"

MARK = "⁣REACT⁣"
KEEP_DAYS = 400          # історія реакцій — на рік з гаком
CTX_DAYS = 14            # у промпт AI — лише свіже
CTX_MAX = 12             # не більше 12 рядків, щоб не топити промпт
SNOOZE_HOURS = 6         # «пізніше» = тиша на 6 год

_store = K.PayloadStore(STORE_FILE)

# ─── КНОПКИ ПІД ВИД СПОВІЩЕННЯ ───────────────────────────────────────────────
# (action, підпис). Порядок = порядок у рядку.
KINDS = {
    "email": [("reply", "✍️ Відповім"), ("done", "✅ Опрацював"),
              ("later", "⏰ Пізніше"), ("mute", "🙈 Не нагадуй")],
    "bill": [("paid", "💸 Оплатив"), ("later", "⏰ Пізніше"),
             ("mute", "🙈 Не нагадуй")],
    "sub": [("keep", "👌 Лишаю"), ("cancelled", "🚫 Скасував"),
            ("mute", "🙈 Не нагадуй")],
    "deadline": [("done", "✅ Зробив"), ("later", "⏰ Пізніше"),
                 ("mute", "🙈 Не нагадуй")],
    "event": [("going", "👌 Буду"), ("declined", "❌ Не піду"),
              ("later", "⏰ Нагадай ближче")],
    "health": [("done", "✅ Зробив"), ("skipped", "❌ Не вийшло"),
               ("mute", "🙈 Не нагадуй")],
    "run": [("done", "🏃 Побігав"), ("skipped", "❌ Не бігав"),
            ("later", "⏰ Пізніше")],
    "crypto": [("bought", "📈 Купив"), ("sold", "📉 Продав"),
               ("watch", "👀 Спостерігаю"), ("mute", "🙈 Не про це")],
    "habit": [("done", "✅ Зробив"), ("skipped", "❌ Пропустив"),
              ("mute", "🙈 Не нагадуй")],
    "money": [("ok", "👌 Знаю"), ("wrong", "❗ Це не моє"),
              ("mute", "🙈 Не нагадуй")],
    "task": [("done", "✅ Зробив"), ("later", "⏰ Пізніше"),
             ("mute", "🙈 Не нагадуй")],
    "report": [("read", "👍 Прочитав"), ("useful", "💡 Корисно"),
               ("noise", "🗑 Це шум")],
    "generic": [("ok", "👌 Прийняв"), ("later", "⏰ Пізніше"),
                ("mute", "🙈 Не нагадуй")],
}

# Що саме означає натискання.
CLOSING = {"done", "paid", "cancelled", "mute", "declined", "skipped",
           "noise", "wrong", "keep"}
SNOOZING = {"later"}
# Решта (reply, watch, bought, sold, ok, read, useful, going) — записуємо,
# але тему не закриваємо.

ACK = {
    "done": "✅ Записав: зроблено. Більше не нагадую.",
    "paid": "💸 Записав: оплачено. Знімаю з нагадувань.",
    "cancelled": "🚫 Записав: скасовано. Більше не рахую.",
    "keep": "👌 Записав: лишаєш. Не пропоную скасувати.",
    "mute": "🙈 Все, ця тема закрита. Не нагадую і в звіти не тягну.",
    "declined": "❌ Записав: не йдеш. Нагадувати не буду.",
    "skipped": "Записав: не вийшло. Не докоряю, тема закрита.",
    "later": "⏰ Добре, повернусь до цього за " + str(SNOOZE_HOURS) + " год.",
    "reply": "✍️ Записав: відповідаєш сам. Нагадаю, якщо зависне.",
    "watch": "👀 Записав: спостерігаєш. Скажу, коли зміниться суттєво.",
    "bought": "📈 Записав покупку. Врахую в аналізі.",
    "sold": "📉 Записав продаж. Врахую в аналізі.",
    "going": "👌 Записав: будеш. Нагадаю ближче до часу.",
    "ok": "👌 Записав.",
    "read": "👍 Дякую, врахував.",
    "useful": "💡 Запам'ятав, що таке корисно — робитиму більше.",
    "noise": "🗑 Записав: це шум. Меньше такого.",
    "wrong": "❗ Записав: це не твоє. Не буду це приписувати тобі.",
}

# Людською мовою для промпта AI.
SAID = {
    "done": "зробив", "paid": "оплатив", "cancelled": "скасував",
    "keep": "лишає як є", "mute": "попросив більше не нагадувати",
    "declined": "не піде", "skipped": "пропустив", "later": "відклав",
    "reply": "відповідає сам", "watch": "спостерігає", "bought": "купив",
    "sold": "продав", "going": "буде", "ok": "прийняв до відома",
    "read": "прочитав", "useful": "назвав корисним", "noise": "назвав шумом",
    "wrong": "сказав, що це не його",
}

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿]+")
_TAGS = re.compile(r"<[^>]+>")
_NONWORD = re.compile(r"[^0-9a-zа-яіїєґ ]+", re.I)
_SPACES = re.compile(r"\s+")

_CACHE = {"data": None, "ts": 0.0}
_CACHE_TTL = 20


def _log(msg):
    print("[" + TAG + "] " + str(msg), flush=True)


def _norm(title) -> str:
    """Заголовок → стабільний ключ (без емодзі, тегів, регістру)."""
    t = _TAGS.sub(" ", str(title or ""))
    t = t.replace("«", " ").replace("»", " ")
    t = _EMOJI.sub(" ", t).strip().lower()
    t = _NONWORD.sub(" ", t)
    return _SPACES.sub(" ", t).strip()[:70]


def _now():
    return K.now().replace(tzinfo=None)


def _load(force: bool = False) -> dict:
    import time as _t
    if not force and _CACHE["data"] is not None and \
            (_t.time() - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]
    data = K.load(FILE, default={}) or {}
    _CACHE["data"] = data
    _CACHE["ts"] = _t.time()
    return data


def _rkey(kind: str, key: str = "", title: str = "") -> str:
    k = str(kind or "generic")
    if key:
        return k + "|k|" + str(key)[:60]
    n = _norm(title)
    return k + "|t|" + (n or "-")


def _dt(val):
    try:
        return datetime.fromisoformat(str(val)[:19].replace(" ", "T"))
    except Exception:
        return None


# ─── ВИЗНАЧЕННЯ ВИДУ ─────────────────────────────────────────────────────────

_HINTS = (
    ("email", ("email", "mail", "лист", "gmail", "inbox")),
    ("bill", ("bill", "рахун", "faktur", "платіж", "оплат", "invoice")),
    ("sub", ("sub", "підпис", "subscription")),
    ("deadline", ("deadline", "дедлайн", "термін")),
    ("event", ("event", "calendar", "календар", "подія", "зустріч", "cal_")),
    ("run", ("run", "strava", "біг", "пробіж")),
    ("health", ("health", "вага", "сон", "sleep", "weight", "крок", "step",
                "nutrition", "харч")),
    ("crypto", ("crypto", "btc", "eth", "ринок", "price", "крипто", "coin")),
    ("habit", ("habit", "звичк")),
    ("money", ("money", "гроші", "витрат", "spend")),
    ("report", ("report", "звіт", "briefing", "брифінг", "themes", "astro",
                "digest", "підсумок")),
    ("task", ("task", "нагадув", "remind", "todo", "справ")),
)


def detect(tag: str = "", text: str = "") -> str:
    """Вид сповіщення з тегу або тексту. Не вгадав — generic (безпечно)."""
    hay = (str(tag or "") + " " + str(text or "")[:400]).lower()
    for kind, words in _HINTS:
        for w in words:
            if w in hay:
                return kind
    return "generic"


# ─── КЛАВІАТУРА ──────────────────────────────────────────────────────────────

def keyboard(kind: str = "generic", key: str = "", title: str = "",
             extra=None) -> list:
    """Рядки кнопок під сповіщення. extra — власні кнопки модуля (йдуть вище)."""
    kind = kind if kind in KINDS else "generic"
    try:
        pid = _store.put({"kind": kind, "key": str(key or "")[:60],
                          "title": str(title or "")[:120]})
    except Exception as e:
        _log("store error: " + str(e))
        return list(extra or [])
    btns = [{"text": lbl, "callback_data": "rx_" + act + "_" + pid}
            for act, lbl in KINDS[kind]]
    rows = list(extra or [])
    # по 2 кнопки в рядку — читабельно на телефоні
    for i in range(0, len(btns), 2):
        rows.append(btns[i:i + 2])
    return rows


def card(text: str, kind: str = None, key: str = "", title: str = "",
         tag: str = "", extra=None) -> bool:
    """Надіслати сповіщення разом із доречними кнопками."""
    k = kind or detect(tag, text)
    return K.send_card(text, keyboard(k, key, title or _first_line(text),
                                      extra), tag=tag or TAG)


def _first_line(text: str) -> str:
    for line in str(text or "").split("\n"):
        s = _TAGS.sub("", line).strip()
        if len(s) > 3:
            return s[:120]
    return ""


# ─── ЗАПИС РЕАКЦІЇ ───────────────────────────────────────────────────────────

def mark(kind: str, action: str, key: str = "", title: str = "",
         label: str = "") -> dict:
    """Пише реакцію назавжди. Закриття теми дублює в dismissed (глобальна тиша)."""
    kind = kind or "generic"
    state = "closed" if action in CLOSING else (
        "snoozed" if action in SNOOZING else "open")
    rec = {
        "kind": kind,
        "key": str(key or "")[:60],
        "tkey": _norm(title),
        "title": str(title or "")[:120],
        "action": action,
        "label": label or action,
        "state": state,
        "ts": _now().isoformat(timespec="seconds"),
    }
    if state == "snoozed":
        rec["until"] = (_now() + timedelta(hours=SNOOZE_HOURS)).isoformat(
            timespec="seconds")
    try:
        K.update_key(FILE, _rkey(kind, key, title), rec)
        _CACHE["ts"] = 0.0
    except Exception as e:
        _log("save error: " + str(e))
    if state == "closed":
        try:
            import dismissed as D
            D.mute(kind, key=key or None, title=title or None,
                   note="реакція: " + str(SAID.get(action, action)))
        except Exception as e:
            _log("dismissed skip: " + str(e))
    _log("реакція: " + kind + " → " + str(SAID.get(action, action)) +
         " (" + (rec["title"] or rec["key"] or "-") + ")")
    return rec


# ─── ЧИ ТЕМА ВЖЕ ЗАКРИТА ─────────────────────────────────────────────────────

def last(kind=None, key=None, title=None):
    """Остання реакція на цю тему або None. Матч за ключем ТА за назвою."""
    data = _load()
    if not data:
        return None
    cands = []
    if key:
        r = data.get(_rkey(kind or "generic", str(key), ""))
        if r:
            cands.append(r)
    if title:
        n = _norm(title)
        if n:
            for rk, r in data.items():
                if r.get("tkey") == n and (not kind or r.get("kind") == kind):
                    cands.append(r)
    if not cands:
        return None
    cands.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
    return cands[0]


def is_closed(kind=None, key=None, title=None) -> bool:
    """True → не надсилати: Олег уже відреагував (закрив або відклав)."""
    r = last(kind, key, title)
    if not r:
        return False
    st = r.get("state")
    if st == "closed":
        return True
    if st == "snoozed":
        u = _dt(r.get("until"))
        return bool(u and _now() < u)
    return False


def why(kind=None, key=None, title=None) -> str:
    """Чесне пояснення, чому тиша. Немає даних — порожній рядок."""
    r = last(kind, key, title)
    if not r:
        return ""
    when = str(r.get("ts", ""))[:16].replace("T", " ")
    return (r.get("title") or r.get("key") or "тема") + " — ти " + \
        str(SAID.get(r.get("action"), r.get("action"))) + " (" + when + ")"


# ─── КНОПКИ ──────────────────────────────────────────────────────────────────

def handle(data: str, cb=None) -> dict:
    """Обробка rx_<action>_<pid>. Повертає {text, alert, keyboard}."""
    if not str(data or "").startswith("rx_"):
        return {"text": "", "alert": False, "keyboard": None}
    body = data[3:]
    action, _, pid = body.rpartition("_")
    if not action or not pid:
        return {"text": "⚠️ Не зрозумів кнопку", "alert": True, "keyboard": None}
    p = _store.get(pid) or {}
    kind = p.get("kind") or "generic"
    key = p.get("key") or ""
    title = p.get("title") or ""
    if not p:
        # payload протух (редеплой/gc) — записуємо хоч вид, не вигадуємо назву
        _log("payload " + pid + " не знайдено — пишу без назви")
    rec = mark(kind, action, key, title,
               label=dict(KINDS.get(kind, [])).get(action, action))
    txt = ACK.get(action, "Записав.")
    if title:
        txt = txt + "\n\n« " + title[:80] + " »"
    # кнопки замінюємо на одну «пломбу» — щоб не тиснути двічі
    lbl = dict(KINDS.get(kind, [])).get(action, action)
    kb = [[{"text": "✓ " + str(lbl), "callback_data": "rx_seen_" + pid}]]
    if action == "seen":
        return {"text": "Вже записано.", "alert": False, "keyboard": None}
    if rec.get("state") == "closed":
        try:
            _store.drop(pid)
        except Exception:
            pass
    return {"text": txt, "alert": True, "keyboard": kb}


# ─── AI: ПАМ'ЯТЬ ПРО РЕАКЦІЇ В ПРОМПТ ────────────────────────────────────────

def block() -> str:
    """Реальні реакції за CTX_DAYS для промпта. Немає — порожньо."""
    data = _load()
    if not data:
        return ""
    cut = _now() - timedelta(days=CTX_DAYS)
    rows = []
    for r in data.values():
        ts = _dt(r.get("ts"))
        if not ts or ts < cut:
            continue
        rows.append((ts, r))
    if not rows:
        return ""
    rows.sort(key=lambda x: x[0], reverse=True)
    lines = []
    closed = []
    for ts, r in rows[:CTX_MAX]:
        name = r.get("title") or r.get("key") or r.get("kind")
        lines.append("• " + str(ts.strftime("%d.%m %H:%M")) + " — «" +
                     str(name)[:70] + "»: " +
                     str(SAID.get(r.get("action"), r.get("action"))))
        if r.get("state") == "closed":
            closed.append(str(name)[:70])
    out = ["", "РЕАКЦІЇ ОЛЕГА (реальні натискання кнопок, не вигадка):"]
    out += lines
    if closed:
        out.append("ЗАКРИТІ ТЕМИ — не нагадувати, не згадувати як нове й не "
                   "тягнути в звіт: " + "; ".join(closed[:10]) + ".")
    out.append("Якщо Олег уже щось зробив/оплатив/скасував — вважай це "
               "фактом і не пропонуй те саме вдруге. Не додумуй реакцій, "
               "яких тут немає.")
    return "\n".join(out)


def inject(body_bytes, tag=""):
    """Додає блок реакцій у не-JSON промпт. Ідемпотентно."""
    try:
        import json as _js
        b = _js.loads(body_bytes.decode())
        p = b["contents"][0]["parts"][0]["text"]
        if MARK in p:
            return body_bytes
        try:
            import ai_brain
            if ai_brain.is_json_prompt(p):
                return body_bytes
        except Exception:
            pass
        blk = block()
        if not blk:
            return body_bytes
        b["contents"][0]["parts"][0]["text"] = p + "\n" + blk + "\n" + MARK
        _log("пам'ять реакцій додана → " + str(tag))
        return _js.dumps(b).encode()
    except Exception as e:
        _log("inject error: " + str(e))
        return body_bytes


# ─── ЗВІТ І ПРИБИРАННЯ ───────────────────────────────────────────────────────

def report(limit: int = 20) -> str:
    data = _load(force=True)
    if not data:
        return ("🔘 <b>Реакції</b>\n\nПоки жодної кнопки не натиснуто — "
                "нічого не вигадую.")
    rows = []
    for r in data.values():
        ts = _dt(r.get("ts"))
        if ts:
            rows.append((ts, r))
    rows.sort(key=lambda x: x[0], reverse=True)
    icon = {"closed": "🔒", "snoozed": "⏰", "open": "•"}
    out = ["🔘 <b>Реакції</b> — що я записав із твоїх натискань", ""]
    for ts, r in rows[:limit]:
        name = r.get("title") or r.get("key") or r.get("kind")
        out.append(icon.get(r.get("state"), "•") + " " +
                   ts.strftime("%d.%m %H:%M") + " — " + K.esc(str(name)[:60]) +
                   ": " + str(SAID.get(r.get("action"), r.get("action"))))
    n_closed = sum(1 for _, r in rows if r.get("state") == "closed")
    out.append("")
    out.append("Усього: " + str(len(rows)) + ", закритих тем: " +
               str(n_closed) + ". 🔒 = не нагадую і в звіти не тягну.")
    return "\n".join(out)


def gc() -> int:
    """Прибирає реакції старші KEEP_DAYS і протухлі payload."""
    n = 0
    try:
        data = _load(force=True)
        cut = _now() - timedelta(days=KEEP_DAYS)
        for rk, r in list(data.items()):
            ts = _dt(r.get("ts"))
            if ts and ts < cut:
                K.remove_key(FILE, rk)
                n += 1
        _store.gc(days=30)
        _CACHE["ts"] = 0.0
    except Exception as e:
        _log("gc error: " + str(e))
    return n
