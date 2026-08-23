#!/usr/bin/env python3
"""
ДОКУМЕНТИ І ДЕДЛАЙНИ З ПОШТИ  (Документи #1)

Ловить у пошті НЕ рахунки, а обов'язки з датою:
  • страховка (авто, житло, життя) — кінець періоду / продовження
  • техогляд STK / emisná kontrola, реєстрація авто
  • договір / підписка — закінчення, авто-продовження, дата розірвання
  • термін дії документів (паспорт, ID, права, дозвіл)
  • «надати документи до», «підтвердити до», «записатись до»
  • гарантія / повернення товару / рекламація
  • візит до лікаря, техніка, службова дата

Що робить:
  1. scan() — фільтр листів по маркерах → Gemini витягує {тип, що, дедлайн, дія}
  2. Карточка з кнопками:
        [📅 В календар на дедлайн]  [⏰ Нагадати за 7 днів]
        [📝 В нотатки]              [❌ Не актуально]
  3. check_due_soon() — за 14 / 7 / 3 / 1 день і в день дедлайну — нагадування.
  4. upcoming() — /дедлайни: усе, що ще попереду, за датою.

Дані не вигадуються: немає дати в листі — рядок відкидається (без дати
дедлайн не має сенсу).

Callback-префікси: dl_cal_ / dl_rem_ / dl_note_ / dl_skip_ /
                   dl_due_done_ / dl_due_snooze_
"""

import re
import json
from datetime import datetime, timedelta

import ai_kit as K

TAG = "deadlines"

ITEMS_FILE = "deadlines.json"           # {did: {...}}
STORE_FILE = "deadlines_store.json"     # payload кнопок
SENT_FILE = "deadlines_sent.json"       # антидубль карточок
SCAN_STATE = "deadlines_scan.json"      # rate-limit скану
DUE_STATE = "deadlines_due_sent.json"   # антидубль нагадувань

SCAN_MIN_GAP_MIN = 180        # раз на 3 години
MAX_CARDS_PER_SCAN = 3
WARN_DAYS = (14, 7, 3, 1, 0)  # на скільких днях «до» нагадувати

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=45)
_due_dedup = K.Dedup(DUE_STATE, ttl_days=2)

# Маркери «тут є термін» (укр / словацька / чеська / англ / нім)
_HINTS = (
    "страхов", "поліс", "полiс", "договір", "договор", "угода", "продовж",
    "термін дії", "закінчується", "спливає", "дійсний до", "чинний до",
    "poistenie", "poistka", "poistn", "zmluva", "zmluvy", "platnost",
    "platnosť", "predlžen", "predlzen", "vypoved", "výpoveď", "ukončenie",
    "stk", "emisná", "emisna", "technická kontrola", "technicka kontrola",
    "prihlásenie", "prihlasenie", "registrácia", "registracia", "obnovenie",
    "termín", "termin", "do dňa", "najneskôr", "najneskor", "lehota",
    "insurance", "policy", "contract", "renewal", "renew", "expire",
    "expiry", "expires", "valid until", "deadline", "no later than",
    "subscription", "auto-renew", "cancel by", "warranty", "guarantee",
    "reklamác", "reklamac", "гарант", "повернення товару",
    "приймання", "запис на", "prihláška", "prihlaska",
    "vertrag", "kündigung", "kundigung", "versicherung",
    "паспорт", "посвідч", "права", "дозвіл", "povolenie", "vodičsk",
    "лікар", "medical", "vyšetrenie", "vysetrenie", "objednanie",
)
# Те, що майже завжди шум
_NOT = ("unsubscribe from our newsletter", "webinar", "black friday",
        "розсилка новин", "promo code", "промокод")


def _email_candidates(limit=10):
    """Листи, у яких МОЖЕ бути дедлайн: [{uid, sender, subject, body}] | None."""
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
        sender = str(e.get("sender") or e.get("from") or "")
        subject = str(e.get("subject") or "")
        body = str((bodies.get(uid) or {}).get("body") or "")
        blob = f"{sender} {subject} {body[:1800]}".lower()
        if not any(h in blob for h in _HINTS):
            continue
        if any(n in blob for n in _NOT):
            continue
        out.append({"uid": uid, "sender": sender, "subject": subject,
                    "body": body[:2500]})
        if len(out) >= limit:
            break
    return out


_PROMPT = """Ти — асистент Олега (Кошице, Словаччина, працює в Minebea Mitsumi).
Знайди в листах нижче ОБОВ'ЯЗКИ З ДАТОЮ — те, що треба зробити або що
закінчується у конкретний день.

СЬОГОДНІ: {today}

ЛИСТИ:
{emails}

ЩО ШУКАЄМО (kind):
  "insurance"  — страховка: кінець періоду, продовження, зміна умов
  "vehicle"    — техогляд STK / emisná, реєстрація, сервіс авто
  "contract"   — договір/підписка: закінчення, авто-продовження, дата розірвання
  "document"   — термін дії документа (паспорт, ID, права, дозвіл)
  "submit"     — треба надати/підписати/підтвердити документ до дати
  "warranty"   — гарантія, повернення, рекламація
  "appointment"— запис/візит на конкретну дату
  "other"      — інший обов'язок з датою

ПРАВИЛА:
1. РАХУНКИ НА ОПЛАТУ пропускай — ними займається інший модуль.
2. Реклама, новини, промо — НЕ дедлайн.
3. Якщо конкретної дати в тексті НЕМАЄ — пропусти рядок повністю.
   Не здогадуйся і не вигадуй дату.
4. deadline — рівно YYYY-MM-DD. Якщо в листі «31.10.2026» → "2026-10-31".
   Якщо вказано лише місяць — візьми останній день того місяця.
5. title — коротко, 3-7 слів, конкретно (напр. «Страховка авто Allianz — кінець періоду»).
6. action — 1 речення: що саме Олегу зробити.
7. why — 1 живе речення українською: чому це важливо / що буде якщо пропустити.

Формат — ТІЛЬКИ валідний JSON-масив, без markdown:
[{{"uid":"12345","kind":"insurance","title":"Страховка авто — продовження",
   "org":"Allianz","deadline":"2026-10-31",
   "action":"Підтвердити продовження або знайти нову пропозицію",
   "why":"Без продовження авто залишиться без страховки з 1 листопада."}}]
Якщо дедлайнів немає — поверни []."""


_KIND_ICON = {
    "insurance": "🛡", "vehicle": "🚗", "contract": "📄", "document": "🪪",
    "submit": "📨", "warranty": "🧰", "appointment": "🩺", "other": "📌",
}
_KIND_NAME = {
    "insurance": "Страховка", "vehicle": "Авто", "contract": "Договір",
    "document": "Документ", "submit": "Подати документи",
    "warranty": "Гарантія", "appointment": "Візит", "other": "Термін",
}


def _extract(cands):
    lines = []
    for c in cands:
        # Чистимо пробіли ЗОВНІ f-string: Python 3.11 забороняє backslash у виразі.
        body_clean = re.sub(r"[ \t]+", " ", c["body"])[:1300]
        lines.append(
            f"--- uid={c['uid']}\nВІД: {c['sender'][:80]}\n"
            f"ТЕМА: {c['subject'][:120]}\nТЕКСТ: {body_clean}\n"
        )
    prompt = _PROMPT.format(today=K.now().strftime("%Y-%m-%d"),
                            emails="\n".join(lines)[:9000])
    return K.gemini_json(prompt, max_tokens=1600, temperature=0.2, tag=TAG)


# ─── СХОВИЩЕ ─────────────────────────────────────────────────────────────────

def load_items() -> dict:
    return K.load(ITEMS_FILE, default={}) or {}


def _did(title, deadline) -> str:
    return K.Dedup.key(title, deadline)[:48]


def _save_item(it: dict, sender: str = "") -> str:
    did = _did(it.get("title"), it.get("deadline"))
    rec = {
        "kind": (it.get("kind") or "other").strip().lower()[:20],
        "title": (it.get("title") or "").strip()[:90],
        "org": (it.get("org") or "").strip()[:60],
        "deadline": (it.get("deadline") or "").strip()[:10],
        "action": (it.get("action") or "").strip()[:200],
        "why": (it.get("why") or "").strip()[:300],
        "uid": str(it.get("uid") or ""),
        "sender": sender[:120],
        "done": False,
        "created": K.today_str(),
    }
    K.update_key(ITEMS_FILE, did, rec)
    return did


def _days_left(deadline: str):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(deadline or "")):
        return None
    try:
        return (datetime.strptime(deadline, "%Y-%m-%d").date() - K.now().date()).days
    except Exception:
        return None


def _urgency(left):
    if left is None:
        return ""
    if left < 0:
        return f"🔴 <b>ТЕРМІН ПРОЙШОВ {abs(left)} дн. тому</b>"
    if left == 0:
        return "🔴 <b>СЬОГОДНІ ОСТАННІЙ ДЕНЬ</b>"
    if left <= 3:
        return f"🔴 залишилось <b>{left} дн.</b>"
    if left <= 14:
        return f"🟠 залишилось <b>{left} дн.</b>"
    return f"🟢 ще <b>{left} дн.</b>"


# ─── КАРТОЧКА ────────────────────────────────────────────────────────────────

def _offer(it: dict, did: str) -> bool:
    title = it.get("title") or ""
    deadline = it.get("deadline") or ""
    if _dedup.seen("dl", title, deadline):
        K.log(TAG, f"skip duplicate: {title}")
        return False

    kind = (it.get("kind") or "other").lower()
    icon = _KIND_ICON.get(kind, "📌")
    left = _days_left(deadline)

    pid = _store.put({"did": did, "title": title, "deadline": deadline,
                      "kind": kind, "org": it.get("org"),
                      "action": it.get("action"), "why": it.get("why"),
                      "uid": it.get("uid")})

    text = (f"{icon} <b>ДЕДЛАЙН У ПОШТІ — {_KIND_NAME.get(kind, 'Термін').upper()}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 <b>{K.esc(title)}</b>\n")
    if it.get("org"):
        text += f"🏢 {K.esc(it['org'])}\n"
    text += f"📆 До <b>{K.esc(deadline)}</b>   {_urgency(left)}\n"
    if it.get("action"):
        text += f"\n➡️ <b>Що зробити:</b> {K.esc(it['action'])}"
    if it.get("why"):
        text += f"\n💭 {K.esc(it['why'])}"

    # хвіст: ставка + один крок (у ротації). Було: сухий перелік полів.
    try:
        import spice as _sp
        _t = _sp.tail("deadline", left, key=str(title or "") + str(deadline or ""))
        if _t:
            text += "\n\n" + _t
    except Exception:
        pass

    kb = [
        [{"text": "📅 В календар на дедлайн", "callback_data": f"dl_cal_{pid}"}],
        [{"text": "⏰ Нагадати заздалегідь", "callback_data": f"dl_rem_{pid}"},
         {"text": "📝 В нотатки", "callback_data": f"dl_note_{pid}"}],
    ]
    row = [{"text": "❌ Не актуально", "callback_data": f"dl_skip_{pid}"}]
    if str(it.get("uid") or "").isdigit():
        row.insert(0, {"text": "📖 Показати лист",
                       "callback_data": f"email_describe_{it['uid']}"})
    kb.append(row)

    ok = K.send_card(text, kb, tag=TAG)
    if ok:
        _dedup.mark("dl", title, deadline)
        K.log(TAG, f"✅ картка: {title} до {deadline}")
        try:
            import response_log
            response_log.log_response("deadline_found", title, deadline, {"kind": kind})
        except Exception:
            pass
    else:
        _store.drop(pid)
    return ok


# ─── СКАН ────────────────────────────────────────────────────────────────────

def should_scan() -> bool:
    return K.rate_ok(SCAN_STATE, SCAN_MIN_GAP_MIN)


def scan(force: bool = False) -> int:
    if not force and not should_scan():
        return 0
    K.rate_mark(SCAN_STATE)

    cands = _email_candidates()
    if cands is None:
        K.log(TAG, "пошта недоступна — скан скасовано (не вигадуємо)")
        return 0
    if not cands:
        K.log(TAG, "листів з можливими термінами немає")
        return 0

    K.log(TAG, f"кандидатів: {len(cands)}")
    found = _extract(cands)
    if not found:
        K.log(TAG, "AI не знайшов дедлайнів")
        return 0

    senders = {str(c["uid"]): c["sender"] for c in cands}
    existing = load_items()
    sent = 0
    for it in found:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        deadline = (it.get("deadline") or "").strip()
        if not title or not re.match(r"^\d{4}-\d{2}-\d{2}$", deadline):
            continue  # без реальної дати — не дедлайн
        left = _days_left(deadline)
        if left is not None and left < -60:
            continue  # дуже старе
        did = _did(title, deadline)
        if did in existing:
            continue
        _save_item(it, senders.get(str(it.get("uid") or ""), ""))
        if _offer(it, did):
            sent += 1
        if sent >= MAX_CARDS_PER_SCAN:
            break
    K.log(TAG, f"скан завершено: {sent} карточок")
    _store.gc(days=60)
    return sent


# ─── НАГАДУВАННЯ ─────────────────────────────────────────────────────────────

def check_due_soon() -> int:
    items = load_items()
    sent = 0
    for did, it in items.items():
        if it.get("done"):
            continue
        left = _days_left(it.get("deadline"))
        if left is None or left < 0 or left not in WARN_DAYS:
            continue
        if _due_dedup.seen("dlr", did, str(left)):
            continue

        kind = (it.get("kind") or "other").lower()
        icon = _KIND_ICON.get(kind, "📌")
        head = ("🔴 <b>СЬОГОДНІ ОСТАННІЙ ДЕНЬ</b>" if left == 0
                else f"⏰ <b>ДЕДЛАЙН ЧЕРЕЗ {left} дн.</b>")
        pid = _store.put({"did": did, **{k: it.get(k) for k in
                          ("title", "deadline", "kind", "org", "action", "why", "uid")}})
        text = (f"{head}\n━━━━━━━━━━━━━━━━━━━━\n"
                f"{icon} <b>{K.esc(it.get('title'))}</b>\n"
                f"📆 до <b>{it.get('deadline')}</b>")
        if it.get("org"):
            text += f"\n🏢 {K.esc(it['org'])}"
        if it.get("action"):
            text += f"\n\n➡️ {K.esc(it['action'])}"
        kb = [
            [{"text": "✅ Зроблено", "callback_data": f"dl_due_done_{pid}"}],
            [{"text": "📅 В календар", "callback_data": f"dl_cal_{pid}"},
             {"text": "⏰ Нагадати завтра", "callback_data": f"dl_due_snooze_{pid}"}],
        ]
        if K.send_card(text, kb, tag=TAG):
            _due_dedup.mark("dlr", did, str(left))
            sent += 1
    if sent:
        K.log(TAG, f"нагадувань про дедлайни: {sent}")
    return sent


# ─── СПИСОК ──────────────────────────────────────────────────────────────────

def upcoming(days: int = 180) -> str:
    items = load_items()
    rows = []
    for did, it in items.items():
        if it.get("done"):
            continue
        left = _days_left(it.get("deadline"))
        if left is None or left > days:
            continue
        rows.append((left, it))
    if not rows:
        return ("📄 <b>ДОКУМЕНТИ І ДЕДЛАЙНИ</b>\n\nАктивних термінів немає.\n\n"
                "<i>Я сам сканую пошту на страховки, техогляд, договори "
                "та терміни подачі документів.</i>")
    rows.sort(key=lambda x: x[0])
    out = ["📄 <b>ДОКУМЕНТИ І ДЕДЛАЙНИ</b>", "━━━━━━━━━━━━━━━━━━━━"]
    overdue = [r for r in rows if r[0] < 0]
    if overdue:
        out.append(f"🔴 Прострочено: <b>{len(overdue)}</b>")
    for left, it in rows:
        icon = _KIND_ICON.get((it.get("kind") or "other").lower(), "📌")
        mark = "🔴" if left <= 3 else ("🟠" if left <= 14 else "🟢")
        line = f"{mark} {icon} <b>{K.esc(it.get('title'))}</b> — {it.get('deadline')}"
        if left < 0:
            line += f" (−{abs(left)} дн.)"
        else:
            line += f" ({left} дн.)"
        out.append(line)
        if it.get("action"):
            out.append(f"    <i>{K.esc(it['action'])[:120]}</i>")
    return "\n".join(out)[:3900]


# ─── ДІЇ КНОПОК ──────────────────────────────────────────────────────────────

def _mark_done(did: str) -> bool:
    items = load_items()
    it = items.get(did)
    if not it:
        return False
    it["done"] = True
    it["done_at"] = K.today_str()
    K.update_key(ITEMS_FILE, did, it)
    return True


def do_calendar(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    deadline = p.get("deadline") or ""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", deadline):
        deadline = (K.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    icon = _KIND_ICON.get((p.get("kind") or "other").lower(), "📌")
    title = f"{icon} {p.get('title')}"
    start = K.parse_dt(deadline, "09:00")
    desc = "\n".join(x for x in [p.get("action"), p.get("why"),
                                 f"Джерело: {p.get('org') or 'лист у пошті'}",
                                 "— знайдено AI у пошті"] if x)
    res = K.calendar_event(title, start, start + timedelta(minutes=45), description=desc)
    if res.get("ok"):
        _store.drop(pid)
        return {"ok": True, "title": title, "date": deadline, "time": "09:00"}
    return {"ok": False, "error": res.get("error", "calendar_error")}


def do_reminder(pid: str) -> dict:
    """Нагадування заздалегідь: за 7 днів (або завтра, якщо дедлайн близько)."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    left = _days_left(p.get("deadline"))
    if left is None or left <= 1:
        d = K.now() + timedelta(days=1)
    elif left <= 8:
        d = K.now() + timedelta(days=1)
    else:
        d = datetime.strptime(p["deadline"], "%Y-%m-%d") - timedelta(days=7)
    date_s = d.strftime("%Y-%m-%d")
    icon = _KIND_ICON.get((p.get("kind") or "other").lower(), "📌")
    title = f"🔔 {icon} {p.get('title')}"
    start = K.parse_dt(date_s, "08:30")
    res = K.calendar_event(title, start, start + timedelta(minutes=30),
                           description=f"Дедлайн: {p.get('deadline')}\n"
                                       f"{p.get('action') or ''}\n\n— нагадування від AI")
    if res.get("ok"):
        _store.drop(pid)
        return {"ok": True, "title": title, "date": date_s, "time": "08:30",
                "deadline": p.get("deadline")}
    return {"ok": False, "error": res.get("error", "calendar_error")}


def do_note(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    try:
        import ai_notes
        txt = f"📄 Дедлайн {p.get('deadline')}: {p.get('title')}"
        if p.get("org"):
            txt += f" ({p['org']})"
        if p.get("action"):
            txt += f" — {p['action']}"
        ai_notes.add_note(txt, source="deadlines")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    _store.drop(pid)
    return {"ok": True, "title": p.get("title"), "deadline": p.get("deadline")}


def do_done(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    _mark_done(p.get("did", ""))
    _store.drop(pid)
    return {"ok": True, "title": p.get("title")}


def do_snooze(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    return {"ok": True, "title": p.get("title")}


def do_skip(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    did = p.get("did", "")
    if did:
        K.remove_key(ITEMS_FILE, did)
    _store.drop(pid)
    return {"ok": True, "title": p.get("title")}


if __name__ == "__main__":
    import sys
    if "--list" in sys.argv:
        print(upcoming())
    elif "--due" in sys.argv:
        print("нагадувань:", check_due_soon())
    elif "--cands" in sys.argv:
        c = _email_candidates()
        print(json.dumps(c, ensure_ascii=False, indent=1)[:4000] if c else c)
    else:
        print("карточок:", scan(force=True))
