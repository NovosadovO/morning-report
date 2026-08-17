#!/usr/bin/env python3
"""
ПРОАКТИВНІ ПРОПОЗИЦІЇ ДІЙ (AI сам ініціює).

Бот сам сканує:
  📅 Google Calendar (сьогодні + завтра + 7 днів)
  📧 Gmail (важливі/непрочитані листи)
  📝 нотатки (щоб не пропонувати те, що вже записано)

і САМ ПИШЕ ПЕРШИМ з конкретною пропозицією + кнопками, які реально виконують дію:
  [📅 В календар]  — створює подію в Google Calendar
  [⏰ Нагадати]     — створює нагадування (подія-нагадування) на вказану дату/час
  [📝 Занотувати]   — зберігає в ai_notes
  [✍️ Відповісти]   — відкриває AI-draft відповіді на лист (delegate у bot.py)
  [❌ Не треба]     — тихо закриває

ВАЖЛИВО: payload кнопок зберігається у storage (гілка data), а НЕ в пам'яті —
тому кнопки живі навіть після рестарту Railway.
"""

import os
import re
import json
import time
import uuid
import urllib.request
from datetime import datetime, timezone, timedelta

_DIR = os.path.dirname(os.path.abspath(__file__))
import sys
sys.path.insert(0, _DIR)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

_TZ = timedelta(hours=2)                       # Europe/Bratislava
STORE_FILE = "proactive_actions_store.json"    # payload кнопок (persistent)
SENT_FILE = "proactive_actions_sent.json"      # антидубль
SCAN_STATE = "proactive_actions_scan.json"     # коли останній скан

MAX_PER_SCAN = 3          # максимум карточок за один скан
MAX_PER_DAY = 8           # ліміт на добу, щоб не спамити
DEDUP_DAYS = 6            # не пропонувати те саме частіше ніж раз на 6 днів
SCAN_MIN_GAP_MIN = 90     # мінімум 90 хв між сканами


def _now():
    return datetime.now(timezone.utc) + _TZ


def _log(msg):
    print(f"[proactive_actions] {msg}", flush=True)


# ─── STORAGE ─────────────────────────────────────────────────────────────────

def _st():
    import storage
    return storage


def _save_payload(pid: str, payload: dict):
    try:
        _st().update_key(STORE_FILE, pid, payload)
    except Exception as e:
        _log(f"save_payload error: {e}")


def get_payload(pid: str):
    """Читає payload кнопки. Використовується з bot.py."""
    try:
        data = _st().load(STORE_FILE, default={}) or {}
        return data.get(pid)
    except Exception as e:
        _log(f"get_payload error: {e}")
        return None


def drop_payload(pid: str):
    try:
        _st().remove_key(STORE_FILE, pid)
    except Exception:
        pass


def _sent_key(kind: str, title: str, date: str) -> str:
    base = f"{kind}|{(title or '').lower().strip()[:60]}|{date or ''}"
    return re.sub(r"[^a-z0-9а-яіїєґ|\-]+", "_", base)


def _already_offered(kind: str, title: str, date: str) -> bool:
    try:
        data = _st().load(SENT_FILE, default={}) or {}
        ts = data.get(_sent_key(kind, title, date))
        if not ts:
            return False
        then = datetime.fromisoformat(ts).replace(tzinfo=None)
        return (_now().replace(tzinfo=None) - then).days < DEDUP_DAYS
    except Exception:
        return False


def _mark_offered(kind: str, title: str, date: str):
    try:
        _st().update_key(SENT_FILE, _sent_key(kind, title, date), _now().isoformat())
    except Exception as e:
        _log(f"mark_offered error: {e}")


def _sent_today_count() -> int:
    try:
        data = _st().load(SENT_FILE, default={}) or {}
        today = _now().strftime("%Y-%m-%d")
        return sum(1 for v in data.values() if isinstance(v, str) and v.startswith(today))
    except Exception:
        return 0


# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def _tg(method: str, body: dict):
    # quiet-guard: режим сну (/сон) — жодних сповіщень і нагадувань до 04:00
    try:
        import quiet as _q_g
        if _q_g.blocked("msg"):
            print("[quiet] 🌙 сон: _tg придушено", flush=True)
            return False
    except Exception:
        pass
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        try:
            err = e.read().decode()[:300]
        except Exception:
            err = str(e)
        _log(f"TG {method} error: {err}")
        return None


def _send_card(text: str, keyboard: list) -> bool:
    res = _tg("sendMessage", {
        "chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    })
    if not res:
        # HTML міг зламатись — шлемо як plain text, кнопки важливіші за форматування
        res = _tg("sendMessage", {
            "chat_id": TELEGRAM_CHAT, "text": re.sub(r"<[^>]+>", "", text),
            "reply_markup": {"inline_keyboard": keyboard},
        })
    return bool(res and res.get("ok"))


# ─── КОНТЕКСТ ────────────────────────────────────────────────────────────────

def _calendar_context() -> str:
    """Події на 7 днів вперед — реальні дані з Google Calendar."""
    try:
        import context as _ctx
        ev = _ctx.get_calendar_events(days=2)
        lines = []
        t = (ev.get("today_text") or "").strip()
        tm = (ev.get("tomorrow_text") or "").strip()
        if t:
            lines.append(f"СЬОГОДНІ ({_now().strftime('%Y-%m-%d %a')}): {t}")
        if tm:
            lines.append(f"ЗАВТРА ({(_now()+timedelta(days=1)).strftime('%Y-%m-%d %a')}): {tm}")
        return "\n".join(lines) or "календар порожній"
    except Exception as e:
        _log(f"calendar_context error: {e}")
        return "календар недоступний"


def _email_context() -> tuple:
    """Повертає (текст для AI, список листів [{uid, from, subject}]).

    monitor.get_emails() повертає dict {"__email_block__":True,"header":...,"items":[...]}
    (а при помилці — рядок). Раніше тут був emails[:12] по dict -> TypeError
    "unhashable type: 'slice'" і скан падав щосекунди.
    """
    try:
        import monitor as _m
        raw = _m.get_emails()
    except Exception as e:
        _log(f"email_context error: {e}")
        return ("пошта недоступна", [])

    if isinstance(raw, dict):
        emails = raw.get("items") or []
    elif isinstance(raw, list):
        emails = raw
    else:
        # рядок = або "Нових листів немає", або текст помилки
        txt = str(raw or "")
        if "Помилка" in txt or "error" in txt.lower():
            return ("пошта недоступна", [])
        return ("нових листів немає", [])

    if not emails:
        return ("нових листів немає", [])

    _JUNK = ("newsletter", "no-reply", "noreply", "notifications@", "info@news",
             "marketing", "promo", "digest", "unsubscribe")
    items, lines = [], []
    for e in emails[:12]:
        if not isinstance(e, dict):
            continue
        sender = str(e.get("sender") or e.get("from") or "")
        subject = str(e.get("subject") or "")
        low = (sender + " " + subject).lower()
        if any(j in low for j in _JUNK):
            continue
        uid = str(e.get("uid") or e.get("id") or "")
        unread = " [НЕПРОЧИТАНИЙ]" if e.get("unread") else ""
        items.append({"uid": uid, "from": sender, "subject": subject})
        lines.append(f"- uid={uid} | від {sender[:60]} | тема: {subject[:90]}{unread}")
        if len(items) >= 6:
            break
    return ("\n".join(lines) or "важливих листів немає", items)


def _notes_context() -> str:
    try:
        import ai_notes
        return ai_notes.get_notes_context(max_notes=12) or "нотаток немає"
    except Exception:
        return "нотаток немає"


# ─── AI: ГЕНЕРАЦІЯ ПРОПОЗИЦІЙ ────────────────────────────────────────────────

_PROMPT = """Ти — особистий AI-асистент Олега (Кошице, Словаччина, працює в Minebea Mitsumi,
змінний графік: рання 06:00-18:00 / нічна 18:00-06:00).

Твоє завдання: ПЕРШИМ запропонувати Олегу конкретні дії на основі РЕАЛЬНИХ даних нижче.
Ти НЕ пишеш звіт. Ти пропонуєш ДІЮ, яку можна виконати одним натисканням.

ЗАРАЗ: {now}

📅 КАЛЕНДАР:
{calendar}

📧 ПОШТА (важливі листи):
{emails}

📝 ВЖЕ ЗАПИСАНІ НОТАТКИ (не дублюй їх):
{notes}

ПРАВИЛА:
1. Тільки на основі даних вище. НЕ вигадуй подій, дат, листів, імен.
2. Максимум {maxn} пропозицій. Якщо реальної причини писати немає — поверни [].
3. Кожна пропозиція має бути КОНКРЕТНА і корисна саме зараз.
4. kind:
   - "calendar" — створити подію (є конкретна дата+час; підготовка, візит, дедлайн)
   - "reminder" — нагадування на дату (дія без точного часу: відповісти на лист, оплатити, подзвонити)
   - "note"     — важливий факт/деталь, яку варто зберегти в нотатки
   - "reply"    — лист потребує відповіді (тоді обовʼязково скопіюй ЧИСЛОВИЙ uid=... з блоку пошти в поле email_uid)
5. date — завжди YYYY-MM-DD, не в минулому. time — "HH:MM" або "" якщо весь день.
6. Врахуй зміни: під час зміни (06:00-18:00 рання / 18:00-06:00 нічна) Олег не може нічого робити.
7. message — 2-4 живих речення українською, звертайся "Олеже", поясни ЧОМУ це важливо.

Формат — ТІЛЬКИ валідний JSON-масив без markdown:
[
  {{"kind":"reminder","title":"Відповісти Michaela про графік","date":"2026-08-05","time":"09:00",
    "message":"Олеже, лист від Michaela висить без відповіді...","note_text":"","email_uid":""}}
]
"""


def _gemini_json(prompt: str, max_tokens: int = 1200):
    if not GEMINI_KEY:
        return []
    try:
        from monitor import _gem_post
    except Exception as e:
        _log(f"no _gem_post: {e}")
        return []

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.6,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash:generateContent?key={GEMINI_KEY}")
    try:
        resp = _gem_post(url, body, timeout=70, tag="proactive_actions", max_retries=3)
        raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        _log(f"gemini error: {e}")
        return []

    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, list) else []
    except Exception as e:
        _log(f"json parse error: {e} | raw={raw[:200]}")
        return []


# ─── ВІДПРАВКА КАРТОЧКИ ──────────────────────────────────────────────────────

_KIND_ICON = {"calendar": "📅", "reminder": "⏰", "note": "📝", "reply": "✉️"}
_KIND_LABEL = {
    "calendar": "Пропоную додати в календар",
    "reminder": "Пропоную поставити нагадування",
    "note": "Пропоную занотувати",
    "reply": "Лист чекає на відповідь",
}


def _valid_date(d: str) -> str:
    """Нормалізує дату; порожньо якщо невалідна або в минулому."""
    d = (d or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return ""
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return ""
    if dt < _now().date():
        return ""
    return d


def offer(item: dict) -> bool:
    """Надсилає одну проактивну карточку з робочими кнопками."""
    kind = (item.get("kind") or "").strip().lower()
    if kind not in _KIND_ICON:
        return False
    title = (item.get("title") or "").strip()
    if not title:
        return False
    date = _valid_date(item.get("date", ""))
    tm = (item.get("time") or "").strip()
    if not re.match(r"^\d{1,2}:\d{2}$", tm):
        tm = ""
    message = (item.get("message") or "").strip()
    email_uid = str(item.get("email_uid") or "").strip()
    # AI іноді пише email-адресу замість IMAP UID — така кнопка була б мертвою
    # (reply_manual_ очікує числовий UID). Краще без кнопки, ніж з битою.
    if not email_uid.isdigit():
        email_uid = ""
    note_text = (item.get("note_text") or "").strip() or title

    if kind in ("calendar", "reminder") and not date:
        # без дати подія не має сенсу — деградуємо до нотатки
        kind = "note"

    if _already_offered(kind, title, date):
        _log(f"skip duplicate: {kind}|{title[:40]}")
        return False

    pid = uuid.uuid4().hex[:10]
    _save_payload(pid, {
        "kind": kind, "title": title, "date": date, "time": tm,
        "message": message[:800], "note_text": note_text[:400],
        "email_uid": email_uid, "ts": _now().isoformat(),
    })

    when = ""
    if date:
        when = f"\n📆 <b>{date}</b>" + (f"  🕐 <b>{tm}</b>" if tm else "  (весь день)")

    text = (
        f"{_KIND_ICON[kind]} <b>{_KIND_LABEL[kind]}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{title}</b>{when}\n\n"
        f"{message}"
    )

    kb = []
    if kind == "calendar":
        kb.append([{"text": "📅 Додати в календар", "callback_data": f"pa_cal_{pid}"}])
        kb.append([{"text": "⏰ Нагадати", "callback_data": f"pa_rem_{pid}"},
                   {"text": "📝 Занотувати", "callback_data": f"pa_note_{pid}"}])
    elif kind == "reminder":
        kb.append([{"text": "⏰ Поставити нагадування", "callback_data": f"pa_rem_{pid}"}])
        kb.append([{"text": "📅 Як подію", "callback_data": f"pa_cal_{pid}"},
                   {"text": "📝 Занотувати", "callback_data": f"pa_note_{pid}"}])
    elif kind == "note":
        kb.append([{"text": "📝 Зберегти в нотатки", "callback_data": f"pa_note_{pid}"}])
        kb.append([{"text": "⏰ Ще й нагадати", "callback_data": f"pa_rem_{pid}"}])
    elif kind == "reply":
        if email_uid:
            kb.append([{"text": "🤖✍️ AI-Draft відповіді", "callback_data": f"reply_manual_{email_uid}"}])
        kb.append([{"text": "⏰ Нагадати відповісти", "callback_data": f"pa_rem_{pid}"},
                   {"text": "📝 Занотувати", "callback_data": f"pa_note_{pid}"}])

    kb.append([{"text": "❌ Не треба", "callback_data": f"pa_skip_{pid}"}])

    ok = _send_card(text, kb)
    if ok:
        _mark_offered(kind, title, date)
        _log(f"✅ offered {kind}: {title[:50]}")
        try:
            import response_log
            response_log.log_response("proactive_offer", _KIND_LABEL[kind], title[:120],
                                      {"kind": kind, "date": date})
        except Exception:
            pass
    else:
        drop_payload(pid)
    return ok


# ─── ГОЛОВНИЙ СКАН ───────────────────────────────────────────────────────────

def should_scan() -> bool:
    """Чи час сканувати: не частіше SCAN_MIN_GAP_MIN, не більше MAX_PER_DAY/добу."""
    if _sent_today_count() >= MAX_PER_DAY:
        return False
    try:
        state = _st().load(SCAN_STATE, default={}) or {}
        last = state.get("last_scan")
        if last:
            then = datetime.fromisoformat(last).replace(tzinfo=None)
            gap = (_now().replace(tzinfo=None) - then).total_seconds() / 60
            if gap < SCAN_MIN_GAP_MIN:
                return False
    except Exception:
        pass
    return True


def _mark_scanned():
    try:
        _st().update_key(SCAN_STATE, "last_scan", _now().isoformat())
    except Exception:
        pass


def scan_and_offer(force: bool = False) -> int:
    """
    Головна функція: читає РЕАЛЬНІ дані (календар + пошта + нотатки),
    просить AI сформувати конкретні пропозиції і надсилає карточки з кнопками.
    Повертає кількість надісланих карточок.
    """
    if not force and not should_scan():
        return 0

    # Позначаємо спробу ВІДРАЗУ: якщо далі щось упаде, listener не буде
    # запускати скан щосекунди (так було з TypeError у _email_context).
    _mark_scanned()

    cal = _calendar_context()
    emails_text, email_items = _email_context()
    notes = _notes_context()

    if ("недоступ" in cal and "недоступ" in emails_text):
        _log("немає жодних живих даних — скан скасовано (не вигадуємо)")
        return 0

    prompt = _PROMPT.format(
        now=_now().strftime("%Y-%m-%d %H:%M (%A)"),
        calendar=cal[:2000], emails=emails_text[:1800],
        notes=notes[:1200], maxn=MAX_PER_SCAN,
    )
    items = _gemini_json(prompt)

    if not items:
        _log("AI не знайшов причин писати — тиша (це нормально)")
        return 0

    # Підставляємо реальні uid листів, якщо AI вказав тему замість uid
    by_subject = {(i["subject"] or "").lower()[:40]: i["uid"] for i in email_items if i.get("uid")}
    sent = 0
    for it in items[:MAX_PER_SCAN]:
        if not isinstance(it, dict):
            continue
        if it.get("kind") == "reply" and not str(it.get("email_uid") or "").isdigit():
            guess = ""
            title_low = (it.get("title") or "").lower()
            for subj, uid in by_subject.items():
                if subj and subj[:20] in title_low:
                    guess = uid
                    break
            it["email_uid"] = guess or (email_items[0]["uid"] if email_items else "")
        if offer(it):
            sent += 1
            time.sleep(1.5)
    _log(f"scan завершено: {sent} карточок")
    return sent


# ─── ВИКОНАННЯ ДІЙ (викликається з bot.py) ───────────────────────────────────

def do_calendar(pid: str) -> dict:
    """Створює подію в Google Calendar з payload кнопки."""
    p = get_payload(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    date = p.get("date") or _now().strftime("%Y-%m-%d")
    tm = p.get("time") or ""
    try:
        import context as _ctx
        if tm:
            start = datetime.strptime(f"{date} {tm}", "%Y-%m-%d %H:%M")
        else:
            start = datetime.strptime(f"{date} 09:00", "%Y-%m-%d %H:%M")
        res = _ctx.create_calendar_event(
            summary=p["title"],
            start_dt=start,
            end_dt=start + timedelta(hours=1),
            description=(p.get("message") or "")[:600] + "\n\n— створено AI-асистентом",
        )
        if res.get("ok"):
            drop_payload(pid)
            return {"ok": True, "title": p["title"], "date": date, "time": tm or "09:00"}
        return {"ok": False, "error": res.get("error", "calendar_error")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def do_reminder(pid: str) -> dict:
    """Створює нагадування (подія з 🔔 у назві + попередження за 60 хв)."""
    p = get_payload(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    date = p.get("date") or (_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    tm = p.get("time") or "09:00"
    try:
        import context as _ctx
        start = datetime.strptime(f"{date} {tm}", "%Y-%m-%d %H:%M")
        res = _ctx.create_calendar_event(
            summary=f"🔔 {p['title']}",
            start_dt=start,
            end_dt=start + timedelta(minutes=30),
            description=(p.get("message") or "")[:600] + "\n\n— нагадування від AI-асистента",
        )
        if res.get("ok"):
            drop_payload(pid)
            return {"ok": True, "title": p["title"], "date": date, "time": tm}
        return {"ok": False, "error": res.get("error", "calendar_error")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def do_note(pid: str) -> dict:
    p = get_payload(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    try:
        import ai_notes
        txt = p.get("note_text") or p.get("title")
        if p.get("date"):
            txt = f"{txt} ({p['date']})"
        ai_notes.add_note(txt, source="proactive_offer")
        drop_payload(pid)
        return {"ok": True, "text": txt}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def do_skip(pid: str) -> dict:
    p = get_payload(pid) or {}
    drop_payload(pid)
    return {"ok": True, "title": p.get("title", "")}


# ─── CLI / тест ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys as _s
    if "--dry" in _s.argv:
        print("CALENDAR:\n", _calendar_context())
        et, ei = _email_context()
        print("EMAILS:\n", et)
        print("ITEMS:", ei)
    else:
        print("sent:", scan_and_offer(force=True))
