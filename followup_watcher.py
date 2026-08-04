#!/usr/bin/env python3
"""
FOLLOW-UP WATCHER — «ти відповів і чекаєш»  (Пошта/комунікація #2)

Логіка:
  1. Через Gmail API читає ВІДПРАВЛЕНІ листи за останні 21 день.
  2. Для кожного треду дивиться, хто написав ОСТАННІМ.
  3. Якщо останній у треді — Олег, і тиша вже 3+ дні → це залиплий діалог.
  4. Бот сам пише: «Олеже, Michaela не відповіла 4 дні» + кнопки:
        [✍️ Скласти пінг]  — AI пише короткий делікатний follow-up,
                             показує текст і лише ПОТІМ, окремою кнопкою,
                             реально надсилає через Gmail
        [⏰ Нагадати через 3 дні]
        [❌ Не треба]

Нічого не надсилається без явного підтвердження другою кнопкою.
Callback-префікси: fu_draft_ / fu_send_ / fu_rem_ / fu_skip_
"""

import re
import json
import urllib.request
from datetime import datetime, timedelta

import ai_kit as K

TAG = "followup"

STORE_FILE = "followup_store.json"
SENT_FILE = "followup_sent.json"
STATE_FILE = "followup_state.json"

CHECK_MIN_GAP_MIN = 60 * 12     # не частіше 2 разів на добу
SILENCE_DAYS = 3                # скільки днів тиші = залиплий діалог
MAX_CARDS = 3

MY_EMAIL = "novosadovoleg@gmail.com"

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=5)

_SKIP_TO = ("noreply", "no-reply", "notifications@", "support@github",
            "newsletter", "mailer", "donotreply", "info@news")


# ─── GMAIL ───────────────────────────────────────────────────────────────────

def _token():
    try:
        import monitor as _m
        return _m._gmail_access_token()
    except Exception as e:
        K.log(TAG, f"token error: {e}")
        return None


def _api(token, path):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception as e:
        K.log(TAG, f"gmail {path[:40]} error: {e}")
        return None


def _hdr(msg, name):
    for h in (msg.get("payload") or {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _stuck_threads(limit=MAX_CARDS * 3):
    """Треди, де останній писав Олег і відповіді немає SILENCE_DAYS+ днів."""
    token = _token()
    if not token:
        return None  # None = Gmail недоступний

    try:
        import monitor as _m
        sent = _m._gmail_list(token, ["SENT"], max_results=30, q="newer_than:21d")
    except Exception as e:
        K.log(TAG, f"list sent error: {e}")
        return None
    if not sent:
        return []

    seen_threads, out = set(), []
    now = K.now().replace(tzinfo=None)

    for m in sent:
        tid = m.get("threadId")
        if not tid or tid in seen_threads:
            continue
        seen_threads.add(tid)

        th = _api(token, f"threads/{tid}?format=metadata")
        if not th:
            continue
        msgs = th.get("messages") or []
        if not msgs:
            continue
        last = msgs[-1]

        frm = _hdr(last, "From").lower()
        if MY_EMAIL not in frm:
            continue  # останнє слово не за Олегом — чекає він, не вони

        try:
            ts = datetime.fromtimestamp(int(last.get("internalDate", 0)) / 1000)
        except Exception:
            continue
        days = (now - ts).days
        if days < SILENCE_DAYS or days > 21:
            continue

        to = _hdr(last, "To") or _hdr(msgs[0], "To")
        subject = _hdr(last, "Subject") or _hdr(msgs[0], "Subject") or "(без теми)"
        low = f"{to} {subject}".lower()
        if any(s in low for s in _SKIP_TO):
            continue
        if len(msgs) == 1 and "re:" not in subject.lower():
            # односторонній лист теж рахуємо — але це саме те, що часто губиться
            pass

        addr = ""
        mm = re.search(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", to.lower())
        if mm:
            addr = mm.group(0)
        if not addr or addr == MY_EMAIL:
            continue

        name = re.sub(r"<[^>]+>", "", to).strip(' "') or addr.split("@")[0]
        out.append({
            "thread_id": tid, "to": addr, "name": name[:60],
            "subject": subject[:140], "days": days,
            "snippet": (last.get("snippet") or "")[:400],
            "sent_at": ts.strftime("%Y-%m-%d"),
        })
        if len(out) >= limit:
            break

    out.sort(key=lambda x: -x["days"])
    return out


# ─── КАРТОЧКА ────────────────────────────────────────────────────────────────

def check(force: bool = False) -> int:
    if not force and not K.rate_ok(STATE_FILE, CHECK_MIN_GAP_MIN):
        return 0
    K.rate_mark(STATE_FILE)

    threads = _stuck_threads()
    if threads is None:
        K.log(TAG, "Gmail недоступний — перевірку скасовано")
        return 0
    if not threads:
        K.log(TAG, "залиплих діалогів немає")
        return 0

    sent = 0
    for t in threads:
        if _dedup.seen("fu", t["thread_id"], str(t["days"] // 3)):
            continue
        pid = _store.put(t)
        text = (
            f"📭 <b>ЧЕКАЄШ ВІДПОВІДЬ {t['days']} дн.</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{K.esc(t['name'])}</b>\n"
            f"📋 {K.esc(t['subject'])}\n"
            f"📤 Твій лист від {t['sent_at']}\n\n"
            f"<i>{K.esc(t['snippet'][:200])}</i>\n\n"
            f"Олеже, тиша вже {t['days']} дні. Хочеш, складу короткий делікатний пінг?"
        )
        kb = [
            [{"text": "✍️ Скласти пінг", "callback_data": f"fu_draft_{pid}"}],
            [{"text": "⏰ Нагадати через 3 дні", "callback_data": f"fu_rem_{pid}"},
             {"text": "❌ Не треба", "callback_data": f"fu_skip_{pid}"}],
        ]
        if K.send_card(text, kb, tag=TAG):
            _dedup.mark("fu", t["thread_id"], str(t["days"] // 3))
            sent += 1
            K.log(TAG, f"✅ follow-up: {t['name']} ({t['days']} дн.)")
        else:
            _store.drop(pid)
        if sent >= MAX_CARDS:
            break
    _store.gc(days=20)
    return sent


# ─── ДІЇ КНОПОК ──────────────────────────────────────────────────────────────

_DRAFT_PROMPT = """Склади короткий follow-up лист від Олега Новосадова.

Кому: {name} <{to}>
Тема оригінального листа: {subject}
Олег написав {days} днів тому, відповіді немає.
Фрагмент його листа: {snippet}

ПРАВИЛА:
- Мова листа: та сама, що у фрагменті (словацька / англійська / українська).
- 3-5 речень максимум. Ввічливо, без тиску, без пасивної агресії.
- Нагадай контекст одним рядком і чітко спитай про статус.
- Без markdown, без теми листа, без підпису «Best regards» — тільки текст листа
  і в кінці окремим рядком: Oleh Novosadov
Поверни ТІЛЬКИ текст листа."""


def do_draft(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    body = K.gemini_text(_DRAFT_PROMPT.format(
        name=p["name"], to=p["to"], subject=p["subject"],
        days=p["days"], snippet=p.get("snippet", "")[:300]),
        max_tokens=700, temperature=0.6, tag=TAG)
    if not body:
        return {"ok": False, "error": "ai_unavailable"}
    body = re.sub(r"^```.*?\n|```$", "", body.strip(), flags=re.DOTALL).strip()
    subject = p["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    pid2 = _store.put({**p, "draft": body[:2000], "subject_re": subject})
    return {"ok": True, "pid": pid2, "to": p["to"], "name": p["name"],
            "subject": subject, "draft": body[:2000]}


def do_send(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    if not p.get("draft"):
        return {"ok": False, "error": "no_draft"}
    try:
        import assistant
        res = assistant.send_email_reply(p["to"], p.get("subject_re") or p["subject"],
                                         p["draft"])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if res.get("ok"):
        _store.drop(pid)
        _dedup.mark("fu_sent", p["thread_id"], K.today_str())
        return {"ok": True, "to": p["to"], "name": p["name"]}
    return {"ok": False, "error": res.get("error", "send_error")}


def do_remind(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    d = (K.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    start = K.parse_dt(d, "09:00")
    title = f"🔔 Пінг: {p['name']} — {p['subject'][:60]}"
    res = K.calendar_event(title, start, start + timedelta(minutes=30),
                           description=f"Лист від {p['sent_at']} без відповіді.\n"
                                       f"{p['to']}\n\n— нагадування від AI")
    if res.get("ok"):
        _store.drop(pid)
        return {"ok": True, "title": title, "date": d, "time": "09:00"}
    return {"ok": False, "error": res.get("error", "calendar_error")}


def do_skip(pid: str) -> dict:
    _store.drop(pid)
    return {"ok": True}


if __name__ == "__main__":
    import sys
    if "--dry" in sys.argv:
        print(json.dumps(_stuck_threads(), ensure_ascii=False, indent=1))
    else:
        print("cards:", check(force=True))
