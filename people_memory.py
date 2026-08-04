#!/usr/bin/env python3
"""
ПАМ'ЯТЬ ПРО ЛЮДЕЙ  (Комунікація #3)

AI веде картки контактів Олега (Michaela, Maroš Sivák, HR, клієнти):
  • коли був останній контакт і хто писав останнім
  • про що говорили (теми з листів за 60 днів)
  • що Олег ОБІЦЯВ і що обіцяли йому (AI витягує зобов'язання з тексту)
  • відкриті питання, на які немає відповіді
  • коли варто пінгнути

Джерела — тільки реальні: листи (IMAP-кеш email_body_cache.json + get_emails),
відправлені треди з followup_watcher, нотатки ai_notes.

Карточка з кнопками:
    [✍️ Написати йому/їй]  [⏰ Нагадати пінгнути]
    [📝 Зберегти в нотатки] [❌ Не треба]

Ніякого вигаданого: якщо пошта недоступна — модуль молчить.
Нічого не надсилається без другої кнопки «📤 Надіслати».

Callback-префікси: pm_draft_ / pm_send_ / pm_rem_ / pm_note_ / pm_skip_
"""

import re
import json
from datetime import datetime, timedelta

import ai_kit as K

TAG = "people"

PEOPLE_FILE = "people.json"              # {email: card}
STORE_FILE = "people_store.json"
SENT_FILE = "people_sent.json"
STATE_FILE = "people_state.json"

SCAN_MIN_GAP_MIN = 60 * 8      # оновлювати картки не частіше 3 разів на добу
OFFER_MIN_GAP_MIN = 60 * 20    # карточку-нагадування максимум раз на ~добу
SILENCE_PING_DAYS = 7          # тиша по важливому контакту
MAX_PEOPLE = 40

MY_EMAIL = "novosadovoleg@gmail.com"
MY_NAME = "Oleh Novosadov"

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=5)

# Хто точно не людина
_SKIP = ("noreply", "no-reply", "donotreply", "notifications@", "newsletter",
         "mailer", "bounce", "support@github", "info@news", "automated",
         "postmaster", "alerts@", "no_reply")

# Кого вважаємо важливим (VIP) — впливає на швидкість пінга
_VIP_HINTS = ("michaela", "sivak", "sivák", "interfin", "minebea", "mitsumi",
              "hr@", "personal", "banka", "bank", "notar", "advokat")


def _is_person(email: str) -> bool:
    e = (email or "").lower()
    if "@" not in e:
        return False
    return not any(s in e for s in _SKIP)


def _is_vip(email: str, name: str) -> bool:
    blob = f"{email} {name}".lower()
    return any(v in blob for v in _VIP_HINTS)


def _split_addr(raw: str):
    """'Michaela K. <m@x.sk>' -> ('Michaela K.', 'm@x.sk')"""
    raw = str(raw or "")
    m = re.search(r"[\w.\-+]+@[\w.\-]+\.\w+", raw)
    email = (m.group(0) if m else "").lower()
    name = re.sub(r"<[^>]*>", "", raw).strip().strip('"').strip()
    if not name or "@" in name:
        name = email.split("@")[0].replace(".", " ").title()
    return name[:60], email


# ─── ЗБІР ЛИСТІВ ─────────────────────────────────────────────────────────────

def _mail_items():
    """[{uid, sender, subject, body, date}] | None (пошта недоступна)."""
    try:
        import monitor as _m
        raw = _m.get_emails()
    except Exception as e:
        K.log(TAG, f"get_emails error: {e}")
        return None
    if isinstance(raw, dict):
        items = raw.get("items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        txt = str(raw or "")
        if "Помилка" in txt or "error" in txt.lower():
            return None
        return []
    bodies = K.load("email_body_cache.json", default={}) or {}
    out = []
    for e in items:
        if not isinstance(e, dict):
            continue
        uid = str(e.get("uid") or "")
        body = str((bodies.get(uid) or {}).get("body") or "")
        out.append({
            "uid": uid,
            "sender": str(e.get("sender") or e.get("from") or ""),
            "subject": str(e.get("subject") or ""),
            "date": str(e.get("date") or e.get("received") or ""),
            "body": body[:1500],
        })
    return out


def _sent_threads():
    """Відправлені Олегом треди з followup_watcher (кому і коли він писав)."""
    try:
        import followup_watcher as F
        rows = F._stuck_threads()
    except Exception as e:
        K.log(TAG, f"sent threads error: {e}")
        return []
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({"to": r.get("to"), "name": r.get("name"),
                    "subject": r.get("subject"), "days": r.get("days"),
                    "sent_at": r.get("sent_at"), "snippet": r.get("snippet")})
    return out


def _parse_date(s: str) -> str:
    """Будь-який формат дати листа -> YYYY-MM-DD ('' якщо не вийшло)."""
    s = str(s or "").strip()
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except Exception:
        return ""


# ─── КАРТКИ ──────────────────────────────────────────────────────────────────

def load_people() -> dict:
    return K.load(PEOPLE_FILE, default={}) or {}


_PROMPT = """Ти ведеш CRM-пам'ять про людей для Олега Новосадова
(Кошице, Словаччина, Minebea Mitsumi; інтереси — інвестиції, крипто, біг).

Нижче реальне листування. Побудуй картку по КОЖНІЙ людині (не по кожному листу).

ЛИСТИ ВІД ЛЮДЕЙ:
{inbox}

ЛИСТИ, ЯКІ НАПИСАВ ОЛЕГ І ЩЕ БЕЗ ВІДПОВІДІ:
{sent}

ПРАВИЛА:
1. Тільки те, що є в тексті. НЕ вигадуй домовленостей, сум, дат, посад.
2 Роботів/розсилки/сервісні адреси пропускай.
3. topics — 1-3 короткі теми (2-5 слів кожна), про що реально йшла мова.
4. my_promises — що ОЛЕГ обіцяв зробити (якщо явно написано). Немає — [].
5. their_promises — що людина обіцяла Олегу. Немає — [].
6. open_question — питання, яке зависло без відповіді, або "".
7. next_step — 1 конкретне речення українською: що Олегу зробити далі.
8. ping_in_days — через скільки днів варто написати (0 = сьогодні,
   -1 = не потрібно). Ціле число.
9. relation — одне слово: "робота" / "інвестиції" / "сервіс" / "особисте" / "інше".

Формат — ТІЛЬКИ валідний JSON-масив, без markdown:
[{{"email":"michaela@firma.sk","name":"Michaela","relation":"робота",
   "topics":["графік змін","відпустка у серпні"],
   "my_promises":["надіслати підтвердження до п'ятниці"],
   "their_promises":[],
   "open_question":"Чи можна взяти зміну 12.08?",
   "next_step":"Надіслати підтвердження — вона чекає з 1 серпня.",
   "ping_in_days":0}}]
Якщо людей немає — поверни []."""


def _blob_inbox(items, days=60):
    cutoff = (K.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    lines = []
    for it in items:
        name, email = _split_addr(it["sender"])
        if not _is_person(email):
            continue
        d = _parse_date(it["date"])
        if d and d < cutoff:
            continue
        body_clean = re.sub(r"[ \t]+", " ", it["body"])[:600]
        lines.append(
            f"--- {name} <{email}> | {d or 'без дати'}\n"
            f"ТЕМА: {it['subject'][:110]}\nТЕКСТ: {body_clean}\n"
        )
    return "\n".join(lines)[:7000]


def _blob_sent(rows):
    lines = []
    for r in rows:
        snip = re.sub(r"[ \t]+", " ", str(r.get("snippet") or ""))[:300]
        lines.append(f"--- Олег -> {r.get('name')} <{r.get('to')}> | "
                     f"надіслано {r.get('sent_at')}, тиша {r.get('days')} дн.\n"
                     f"ТЕМА: {str(r.get('subject'))[:110]}\nУРИВОК: {snip}\n")
    return "\n".join(lines)[:2500] or "— немає"


def refresh(force: bool = False) -> int:
    """Оновлює people.json з реальної пошти. Повертає кількість карток."""
    if not force and not K.rate_ok(STATE_FILE, SCAN_MIN_GAP_MIN):
        return 0
    K.rate_mark(STATE_FILE)

    items = _mail_items()
    if items is None:
        K.log(TAG, "пошта недоступна — картки не оновлюю (не вигадуємо)")
        return 0
    inbox = _blob_inbox(items)
    if not inbox.strip():
        K.log(TAG, "листів від живих людей за 60 днів немає")
        return 0
    sent_rows = _sent_threads()

    cards = K.gemini_json(_PROMPT.format(inbox=inbox, sent=_blob_sent(sent_rows)),
                          max_tokens=2200, temperature=0.3, tag=TAG)
    if not cards:
        K.log(TAG, "AI не побудував карток")
        return 0

    old = load_people()
    # факт останнього контакту беремо з листів, а не з AI
    last_seen = {}
    last_subj = {}
    for it in items:
        _, email = _split_addr(it["sender"])
        d = _parse_date(it["date"])
        if email and d and d > last_seen.get(email, ""):
            last_seen[email] = d
            last_subj[email] = str(it.get("subject") or "")[:120]
    my_last = {}
    for r in sent_rows:
        e = str(r.get("to") or "").lower()
        d = _parse_date(str(r.get("sent_at") or ""))
        if e and d and d > my_last.get(e, ""):
            my_last[e] = d

    n = 0
    for c in cards:
        if not isinstance(c, dict):
            continue
        email = str(c.get("email") or "").lower().strip()
        if not _is_person(email):
            continue
        name = str(c.get("name") or "").strip() or email.split("@")[0].title()
        prev = old.get(email) or {}
        try:
            ping = int(c.get("ping_in_days"))
        except Exception:
            ping = -1
        rec = {
            "name": name[:60],
            "relation": str(c.get("relation") or "інше")[:20],
            "vip": _is_vip(email, name),
            "topics": [str(t)[:60] for t in (c.get("topics") or [])][:3],
            "my_promises": [str(t)[:140] for t in (c.get("my_promises") or [])][:3],
            "their_promises": [str(t)[:140] for t in (c.get("their_promises") or [])][:3],
            "open_question": str(c.get("open_question") or "")[:200],
            "next_step": str(c.get("next_step") or "")[:220],
            "ping_in_days": ping,
            "last_in": last_seen.get(email, prev.get("last_in", "")),
            "last_subject": last_subj.get(email, prev.get("last_subject", "")),
            "last_out": my_last.get(email, prev.get("last_out", "")),
            "updated": K.today_str(),
            "pinged_at": prev.get("pinged_at", ""),
        }
        K.update_key(PEOPLE_FILE, email, rec)
        n += 1
        if n >= MAX_PEOPLE:
            break
    K.log(TAG, f"✅ карток оновлено: {n}")
    _store.gc(days=20)
    return n


# ─── ХТО ПОТРЕБУЄ УВАГИ ──────────────────────────────────────────────────────

def _silence_days(rec) -> int:
    d = max(str(rec.get("last_in") or ""), str(rec.get("last_out") or ""))
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return 999
    try:
        return (K.now().date() - datetime.strptime(d, "%Y-%m-%d").date()).days
    except Exception:
        return 999


def needs_attention() -> list:
    """[(score, email, rec)] — кому варто написати, найважливіші зверху."""
    out = []
    for email, rec in load_people().items():
        if not isinstance(rec, dict):
            continue
        ping = rec.get("ping_in_days")
        sil = _silence_days(rec)
        score = 0
        why = []
        if rec.get("my_promises"):
            score += 40
            why.append("є твоя обіцянка")
        if rec.get("open_question"):
            score += 25
            why.append("зависло питання")
        if isinstance(ping, int) and 0 <= ping <= 1:
            score += 20
            why.append("час пінгувати")
        if rec.get("vip") and sil >= SILENCE_PING_DAYS:
            score += 15
            why.append(f"тиша {sil} дн.")
        if rec.get("their_promises") and sil >= 5:
            score += 12
            why.append("вони обіцяли і тихо")
        if score < 20:
            continue
        # вже пінгували сьогодні — не турбуємо
        if str(rec.get("pinged_at") or "") == K.today_str():
            continue
        out.append((score, email, {**rec, "silence": sil, "why": why}))
    out.sort(key=lambda x: -x[0])
    return out


def offer(force: bool = False) -> bool:
    """Карточка по одній людині, яка найбільше потребує увагу."""
    if not force and not K.rate_ok("people_offer.json", OFFER_MIN_GAP_MIN):
        return False
    rows = needs_attention()
    if not rows:
        K.log(TAG, "нікого пінгувати не треба")
        return False
    K.rate_mark("people_offer.json")

    score, email, rec = rows[0]
    if _dedup.seen("pm", email, K.today_str()):
        return False

    pid = _store.put({"email": email, "name": rec.get("name"),
                      "topics": rec.get("topics"), "next_step": rec.get("next_step"),
                      "open_question": rec.get("open_question"),
                      "my_promises": rec.get("my_promises"),
                      "their_promises": rec.get("their_promises"),
                      "relation": rec.get("relation"), "silence": rec.get("silence")})

    vip = "⭐ " if rec.get("vip") else ""
    text = [f"🧠 <b>ПАМ'ЯТЬ ПРО ЛЮДЕЙ</b>", "━━━━━━━━━━━━━━━━━━━━",
            f"{vip}👤 <b>{K.esc(rec.get('name'))}</b>  ·  {K.esc(rec.get('relation'))}",
            f"📧 {K.esc(email)}"]
    sil = rec.get("silence")
    if isinstance(sil, int) and sil < 999:
        text.append(f"🕐 останній контакт: <b>{sil} дн. тому</b>")
    if rec.get("topics"):
        text.append(f"💬 Про що говорили: {K.esc(', '.join(rec['topics']))}")
    if rec.get("my_promises"):
        text.append("\n🔴 <b>ТИ ОБІЦЯВ:</b>")
        for p in rec["my_promises"]:
            text.append(f"   • {K.esc(p)}")
    if rec.get("their_promises"):
        text.append("\n🟡 <b>ТОБІ ОБІЦЯЛИ:</b>")
        for p in rec["their_promises"]:
            text.append(f"   • {K.esc(p)}")
    if rec.get("open_question"):
        text.append(f"\n❓ Зависло: <i>{K.esc(rec['open_question'])}</i>")
    if rec.get("next_step"):
        text.append(f"\n➡️ <b>Далі:</b> {K.esc(rec['next_step'])}")
    if rec.get("why"):
        text.append(f"\n<i>Чому пишу: {K.esc(', '.join(rec['why']))}</i>")

    kb = [
        [{"text": "✍️ Написати листа", "callback_data": f"pm_draft_{pid}"}],
        [{"text": "⏰ Нагадати пінгнути", "callback_data": f"pm_rem_{pid}"},
         {"text": "📝 В нотатки", "callback_data": f"pm_note_{pid}"}],
        [{"text": "❌ Не треба", "callback_data": f"pm_skip_{pid}"}],
    ]
    ok = K.send_card("\n".join(text)[:3900], kb, tag=TAG)
    if ok:
        _dedup.mark("pm", email, K.today_str())
        K.log(TAG, f"✅ картка: {rec.get('name')} ({email}) score={score}")
    else:
        _store.drop(pid)
    return ok


def digest() -> str:
    """/люди — усі картки одним списком."""
    people = load_people()
    if not people:
        return ("🧠 <b>ПАМ'ЯТЬ ПРО ЛЮДЕЙ</b>\n\nКарток ще немає — я збираю їх "
                "з реального листування.\n\n<i>Онови вручну: /люди_онови</i>")
    rows = sorted(people.items(), key=lambda kv: -(
        40 * bool(kv[1].get("my_promises")) + 20 * bool(kv[1].get("open_question"))
        + 10 * bool(kv[1].get("vip"))))
    out = ["🧠 <b>ПАМ'ЯТЬ ПРО ЛЮДЕЙ</b>", "━━━━━━━━━━━━━━━━━━━━",
           f"👥 Карток: <b>{len(people)}</b>", ""]
    for email, r in rows[:15]:
        if not isinstance(r, dict):
            continue
        vip = "⭐" if r.get("vip") else "👤"
        sil = _silence_days(r)
        sil_s = f"{sil} дн. тому" if sil < 999 else "давно"
        out.append(f"{vip} <b>{K.esc(r.get('name'))}</b> · {K.esc(r.get('relation'))} · {sil_s}")
        if r.get("topics"):
            out.append(f"    💬 {K.esc(', '.join(r['topics']))}")
        if r.get("my_promises"):
            out.append(f"    🔴 ти обіцяв: {K.esc(r['my_promises'][0])}")
        if r.get("their_promises"):
            out.append(f"    🟡 тобі обіцяли: {K.esc(r['their_promises'][0])}")
        if r.get("open_question"):
            out.append(f"    ❓ {K.esc(r['open_question'])[:90]}")
    return "\n".join(out)[:3900]


# ─── ЧЕРНОВИК ЛИСТА ──────────────────────────────────────────────────────────

_DRAFT_PROMPT = """Напиши короткий email від Олега Новосадова до людини нижче.

КОМУ: {name} <{to}>
ТИП ЗВ'ЯЗКУ: {relation}
ПРО ЩО ГОВОРИЛИ РАНІШЕ: {topics}
ОЛЕГ ОБІЦЯВ: {mine}
ЙОМУ ОБІЦЯЛИ: {theirs}
ЗАВИСЛЕ ПИТАННЯ: {question}
ЩО ТРЕБА ЗРОБИТИ ЦИМ ЛИСТОМ: {step}
ТИША: {silence} днів

ПРАВИЛА:
1. Нічого не вигадуй: жодних дат, сум, домовленостей, яких немає вище.
   Якщо Олег обіцяв щось і ще не зробив — напиши, що працює над цим / уточни строк,
   але НЕ пиши, що вже зробив.
2. МОВА: та сама, що ймовірно у листуванні (словацька для .sk адрес,
   англійська для міжнародних, українська для українських). Визнач сам.
3. 70-120 слів. Тон: ввічливо, по-людськи, без канцеляриту.
4. Структура: звертання → зв'язок з попередньою темою → суть/питання → підпис.
5. Підпис: {me} ({my_email}).
6. Поверни ТІЛЬКИ текст листа, без теми і без markdown."""


def _subject_for(p) -> str:
    """Тема — МОВОЮ листування: «Re: <остання тема>», інакше нейтральна по домену.
    Раніше бралась перша тема з topics — а вони українською, тож словацький лист
    приходив з українською темою."""
    last = str(p.get("last_subject") or "").strip()
    if last:
        return last if re.match(r"^(re|odp|fwd)\s*:", last, re.I) else f"Re: {last[:66]}"
    dom = str(p.get("email") or "").split("@")[-1].lower()
    if dom.endswith(".sk") or dom.endswith(".cz"):
        return "Dobrý deň"
    if dom.endswith(".ua") or dom.endswith(".ru"):
        return "Доброго дня"
    if dom.endswith(".hu"):
        return "Hello"
    return "Hello"


def do_draft(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    body = K.gemini_text(_DRAFT_PROMPT.format(
        name=p.get("name"), to=p.get("email"), relation=p.get("relation") or "інше",
        topics=", ".join(p.get("topics") or []) or "—",
        mine="; ".join(p.get("my_promises") or []) or "—",
        theirs="; ".join(p.get("their_promises") or []) or "—",
        question=p.get("open_question") or "—",
        step=p.get("next_step") or "підтримати контакт і рухати питання далі",
        silence=p.get("silence"), me=MY_NAME, my_email=MY_EMAIL),
        max_tokens=800, temperature=0.6, tag=TAG)
    if not body:
        return {"ok": False, "error": "ai_unavailable"}
    body = re.sub(r"^```.*?\n|```$", "", body.strip(), flags=re.DOTALL).strip()
    body = re.sub(r"^(?:Subject|Тема|Predmet)\s*:.*\n+", "", body, flags=re.I)
    subject = _subject_for(p)
    pid2 = _store.put({**p, "draft": body[:2200], "subject_out": subject})
    return {"ok": True, "pid": pid2, "to": p.get("email"), "name": p.get("name"),
            "subject": subject, "draft": body[:2200]}


def do_send(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    if not p.get("draft"):
        return {"ok": False, "error": "no_draft"}
    try:
        import assistant
        res = assistant.send_email_reply(p["email"], p.get("subject_out") or "Ahoj",
                                        p["draft"])
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error", "send_error")}
    _mark_pinged(p["email"])
    _store.drop(pid)
    return {"ok": True, "to": p["email"], "name": p.get("name")}


def _mark_pinged(email: str):
    people = load_people()
    rec = people.get(email)
    if not rec:
        return
    rec["pinged_at"] = K.today_str()
    rec["last_out"] = K.today_str()
    K.update_key(PEOPLE_FILE, email, rec)


def do_remind(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    d = (K.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    start = K.parse_dt(d, "09:00")
    title = f"🔔 Написати: {p.get('name')}"
    desc = "\n".join(x for x in [
        p.get("next_step"),
        ("Ти обіцяв: " + "; ".join(p.get("my_promises") or [])) if p.get("my_promises") else "",
        p.get("email"), "— пам'ять про людей від AI"] if x)
    res = K.calendar_event(title, start, start + timedelta(minutes=30), description=desc)
    if res.get("ok"):
        _mark_pinged(p.get("email") or "")
        _store.drop(pid)
        return {"ok": True, "title": title, "date": d, "time": "09:00"}
    return {"ok": False, "error": res.get("error", "calendar_error")}


def do_note(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    try:
        import ai_notes
        parts = [f"🧠 {p.get('name')} ({p.get('email')})"]
        if p.get("topics"):
            parts.append("теми: " + ", ".join(p["topics"]))
        if p.get("my_promises"):
            parts.append("я обіцяв: " + "; ".join(p["my_promises"]))
        if p.get("their_promises"):
            parts.append("мені обіцяли: " + "; ".join(p["their_promises"]))
        if p.get("open_question"):
            parts.append("зависло: " + p["open_question"])
        ai_notes.add_note(" | ".join(parts), source="people_memory")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    _store.drop(pid)
    return {"ok": True, "name": p.get("name")}


def do_skip(pid: str) -> dict:
    p = _store.get(pid)
    if p and p.get("email"):
        _mark_pinged(p["email"])
    _store.drop(pid)
    return {"ok": True}


if __name__ == "__main__":
    import sys
    if "--refresh" in sys.argv:
        print("карток:", refresh(force=True))
    elif "--digest" in sys.argv:
        print(digest())
    elif "--attention" in sys.argv:
        print(json.dumps(needs_attention(), ensure_ascii=False, indent=1)[:4000])
    else:
        print("картка надіслана:", offer(force=True))
