#!/usr/bin/env python3
"""
АВТО-ПАРСЕР РАХУНКІВ З ПОШТИ  (Гроші/робота #1)

Що робить:
  1. Читає листи (той самий IMAP-кеш, що і звіт — email_body_cache.json,
     ніяких додаткових IMAP-сесій).
  2. Gemini витягує з листа: постачальник, сума, валюта, номер, ДЕДЛАЙН оплати.
  3. Кладе рахунок у bills.json (гілка data) і САМ пише карточку з кнопками:
        [📅 В календар на дедлайн]  [⏰ Нагадати за 2 дні]
        [✅ Вже оплачено]           [❌ Це не рахунок]
  4. check_due_soon() — щодня перевіряє неоплачені: за 2 дні до дедлайну
     (і в день дедлайну) пише нагадування з кнопкою «✅ Оплачено».
  5. monthly_report() — «скільки рахунків прийшло, на яку суму, що не оплачено».

Дані НЕ вигадуються: якщо суми/дедлайну в листі немає — поле пусте,
а якщо AI не знайшов жодного рахунку — модуль просто молчить.

Callback-префікси (роутинг у bot.py): bill_cal_ / bill_rem_ / bill_paid_ /
bill_skip_ / bill_due_paid_ / bill_due_snooze_
"""

import re
import json
from datetime import datetime, timedelta

import ai_kit as K

TAG = "bills"

BILLS_FILE = "bills.json"                  # {bill_id: {...}}
STORE_FILE = "bills_store.json"            # payload кнопок
SENT_FILE = "bills_sent.json"              # антидубль карточок
SCAN_STATE = "bills_scan.json"             # rate-limit скану
DUE_STATE = "bills_due_sent.json"          # антидубль нагадувань про дедлайн

SCAN_MIN_GAP_MIN = 120     # не частіше ніж раз на 2 години
MAX_CARDS_PER_SCAN = 3
DUE_WARN_DAYS = 2          # попереджати за 2 дні

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=30)
_due_dedup = K.Dedup(DUE_STATE, ttl_days=1)

# Слова-маркери рахунку (укр/словацька/англ/чеська)
_BILL_HINTS = (
    "рахунок", "рахунк", "оплат", "платіж", "заборгован",
    "faktúra", "faktura", "faktúru", "úhrada", "uhrada", "splatnos", "platba",
    "zaplat", "nedoplat", "predpis", "vyúčtovanie", "vyuctovanie", "upomienka",
    "invoice", "payment", "bill", "due", "amount due", "receipt", "subscription",
    "pošt", "poplat", "poplatok", "tarif",
)
_NOT_BILL = ("newsletter", "unsubscribe від новин", "webinar")


# ─── КОНТЕКСТ ────────────────────────────────────────────────────────────────

def _email_candidates(limit=8):
    """Листи, які МОЖУТЬ бути рахунком: (uid, sender, subject, body)."""
    try:
        import monitor as _m
        raw = _m.get_emails()
    except Exception as e:
        K.log(TAG, f"get_emails error: {e}")
        return None  # None = пошта недоступна (≠ порожньо)

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
        blob = f"{sender} {subject} {body[:1500]}".lower()
        if not any(h in blob for h in _BILL_HINTS):
            continue
        if any(n in blob for n in _NOT_BILL):
            continue
        out.append({"uid": uid, "sender": sender, "subject": subject,
                    "body": body[:2500]})
        if len(out) >= limit:
            break
    return out


_PROMPT = """Ти — фінансовий асистент Олега (Кошице, Словаччина).
Витягни з листів нижче ТІЛЬКИ реальні рахунки/платіжні вимоги.

ЗАРАЗ: {now}

ЛИСТИ:
{emails}

ПРАВИЛА:
1. Нічого не вигадуй. Якщо суми або дедлайну в тексті немає — залиш поле "".
2. Реклама, підтвердження оплати, виписки без вимоги оплати — НЕ рахунок, пропусти.
3. Якщо лист — підтвердження ВЖЕ здійсненої оплати, постав "already_paid": true.
4. due — дата оплати у форматі YYYY-MM-DD (шукай "splatnosť", "due date",
   "termín úhrady", "оплатити до"). Якщо є лише період — візьми останній день.
5. amount — тільки число (наприклад 47.90), без валюти. currency — "EUR"/"USD"/"CZK".
6. vendor — коротка назва постачальника (2-4 слова, як в листі).
7. note — 1 живе речення українською: що це за рахунок і на що звернути увагу.

Формат — ТІЛЬКИ валідний JSON-масив, без markdown:
[{{"uid":"12345","vendor":"GASTROMILA","amount":"47.90","currency":"EUR",
   "invoice_no":"2026/0812","due":"2026-08-12","already_paid":false,
   "note":"Рахунок за обіди за липень, оплатити до 12.08."}}]
Якщо рахунків немає — поверни []."""


def _extract(cands):
    lines = []
    for c in cands:
        # Пробіли чистимо ЗОВНІ f-string: Python 3.11 забороняє backslash
        # у виразі f-string ("f-string expression part cannot include a backslash").
        body_clean = re.sub(r"[ \t]+", " ", c["body"])[:1400]
        lines.append(
            f"--- uid={c['uid']}\nВІД: {c['sender'][:80]}\nТЕМА: {c['subject'][:120]}\n"
            f"ТЕКСТ: {body_clean}\n"
        )
    prompt = _PROMPT.format(now=K.now().strftime("%Y-%m-%d %H:%M"),
                            emails="\n".join(lines)[:9000])
    return K.gemini_json(prompt, max_tokens=1400, temperature=0.2, tag=TAG)


# ─── РАХУНКИ ─────────────────────────────────────────────────────────────────

def _bill_id(vendor, amount, due) -> str:
    return K.Dedup.key(vendor, amount, due)[:48]


def load_bills() -> dict:
    return K.load(BILLS_FILE, default={}) or {}


def _amount_f(v):
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except Exception:
        return 0.0


def _fmt_money(b) -> str:
    a = _amount_f(b.get("amount"))
    if not a:
        return "сума не вказана"
    return f"{a:.2f} {b.get('currency') or 'EUR'}"


def _save_bill(b: dict) -> str:
    bid = _bill_id(b.get("vendor"), b.get("amount"), b.get("due"))
    rec = {
        "vendor": (b.get("vendor") or "").strip()[:60],
        "amount": str(b.get("amount") or ""),
        "currency": (b.get("currency") or "EUR").strip()[:5],
        "invoice_no": str(b.get("invoice_no") or "")[:40],
        "due": K.valid_future_date(b.get("due") or "") or (b.get("due") or ""),
        "uid": str(b.get("uid") or ""),
        "sender": str(b.get("sender") or "")[:120],
        "subject": str(b.get("subject") or "")[:140],
        "note": (b.get("note") or "")[:300],
        "paid": bool(b.get("already_paid")),
        "paid_at": K.today_str() if b.get("already_paid") else "",
        "created": K.today_str(),
    }
    K.update_key(BILLS_FILE, bid, rec)
    return bid


# ─── КАРТОЧКА ────────────────────────────────────────────────────────────────

def _offer(b: dict, bid: str) -> bool:
    vendor = b.get("vendor") or "Невідомий постачальник"
    due = b.get("due") or ""
    if _dedup.seen("bill", vendor, b.get("amount"), due):
        K.log(TAG, f"skip duplicate: {vendor} {b.get('amount')}")
        return False

    days_left = None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", due):
        try:
            days_left = (datetime.strptime(due, "%Y-%m-%d").date() - K.now().date()).days
        except Exception:
            days_left = None

    urgency = ""
    if days_left is not None:
        if days_left < 0:
            urgency = f"🔴 <b>ПРОСТРОЧЕНО на {abs(days_left)} дн.</b>"
        elif days_left == 0:
            urgency = "🔴 <b>ДЕДЛАЙН СЬОГОДНІ</b>"
        elif days_left <= 3:
            urgency = f"🟠 залишилось <b>{days_left} дн.</b>"
        else:
            urgency = f"🟢 ще <b>{days_left} дн.</b>"

    pid = _store.put({"bid": bid, "vendor": vendor, "amount": b.get("amount"),
                      "currency": b.get("currency"), "due": due,
                      "invoice_no": b.get("invoice_no"),
                      "sender": b.get("sender"), "subject": b.get("subject"),
                      "note": b.get("note"), "uid": b.get("uid")})

    text = (
        f"💸 <b>НОВИЙ РАХУНОК У ПОШТІ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏢 <b>{K.esc(vendor)}</b>\n"
        f"💰 {K.esc(_fmt_money(b))}\n"
    )
    if b.get("invoice_no"):
        text += f"🧾 № {K.esc(b['invoice_no'])}\n"
    if due:
        text += f"📆 Оплатити до <b>{K.esc(due)}</b>   {urgency}\n"
    else:
        text += "📆 Дедлайн у листі не вказаний\n"
    if b.get("note"):
        text += f"\n{K.esc(b['note'])}"

    # хвіст: що на кону + один крок. Раніше картка була сухим переліком полів.
    try:
        import spice as _sp
        _t = _sp.tail("bill", days_left,
                      key=str(vendor or "") + str(b.get("invoice_no") or ""))
        if _t:
            text += "\n\n" + _t
    except Exception:
        pass

    kb = []
    if due:
        kb.append([{"text": "📅 В календар на дедлайн", "callback_data": f"bill_cal_{pid}"}])
        kb.append([{"text": f"⏰ Нагадати за {DUE_WARN_DAYS} дні", "callback_data": f"bill_rem_{pid}"}])
    else:
        kb.append([{"text": "⏰ Нагадати завтра", "callback_data": f"bill_rem_{pid}"}])
    row = [{"text": "✅ Вже оплачено", "callback_data": f"bill_paid_{pid}"}]
    if str(b.get("uid") or "").isdigit():
        row.append({"text": "📖 Показати лист", "callback_data": f"email_describe_{b['uid']}"})
    kb.append(row)
    kb.append([{"text": "✍️ Написати постачальнику", "callback_data": f"vr_menu_{pid}"}])
    kb.append([{"text": "❌ Це не рахунок", "callback_data": f"bill_skip_{pid}"}])

    ok = K.send_card(text, kb, tag=TAG)
    if ok:
        _dedup.mark("bill", vendor, b.get("amount"), due)
        K.log(TAG, f"✅ картка: {vendor} {_fmt_money(b)} due={due}")
        try:
            import response_log
            response_log.log_response("bill_found", vendor, _fmt_money(b), {"due": due})
        except Exception:
            pass
    else:
        _store.drop(pid)
    return ok


# ─── ГОЛОВНИЙ СКАН ───────────────────────────────────────────────────────────

def should_scan() -> bool:
    return K.rate_ok(SCAN_STATE, SCAN_MIN_GAP_MIN)


def scan(force: bool = False) -> int:
    """Шукає нові рахунки в пошті. Повертає кількість надісланих карточок."""
    if not force and not should_scan():
        return 0
    K.rate_mark(SCAN_STATE)  # позначаємо ВІДРАЗУ, щоб падіння не крутило цикл

    cands = _email_candidates()
    if cands is None:
        K.log(TAG, "пошта недоступна — скан скасовано (не вигадуємо)")
        return 0
    if not cands:
        K.log(TAG, "листів схожих на рахунок немає")
        return 0

    K.log(TAG, f"кандидатів: {len(cands)}")
    found = _extract(cands)
    if not found:
        K.log(TAG, "AI не знайшов рахунків")
        return 0

    existing = load_bills()
    by_uid = {str(c["uid"]): c for c in cands}
    sent = 0
    for b in found:
        if not isinstance(b, dict):
            continue
        vendor = (b.get("vendor") or "").strip()
        if not vendor:
            continue
        src = by_uid.get(str(b.get("uid") or "")) or {}
        b.setdefault("sender", src.get("sender") or "")
        b.setdefault("subject", src.get("subject") or "")
        bid = _bill_id(vendor, b.get("amount"), b.get("due"))
        if bid in existing:
            continue
        _save_bill(b)
        if b.get("already_paid"):
            K.log(TAG, f"вже оплачено, тільки записав: {vendor}")
            continue
        if _offer(b, bid):
            sent += 1
        if sent >= MAX_CARDS_PER_SCAN:
            break
    K.log(TAG, f"скан завершено: {sent} карточок")
    _store.gc(days=45)
    return sent


# ─── НАГАДУВАННЯ ПРО ДЕДЛАЙН ─────────────────────────────────────────────────

def check_due_soon() -> int:
    """За DUE_WARN_DAYS днів до дедлайну (і в день дедлайну/просрочки) — нагадати."""
    bills = load_bills()
    today = K.now().date()
    sent = 0
    for bid, b in bills.items():
        if b.get("paid"):
            continue
        due = b.get("due") or ""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", due):
            continue
        try:
            d = datetime.strptime(due, "%Y-%m-%d").date()
        except Exception:
            continue
        left = (d - today).days
        if left > DUE_WARN_DAYS:
            continue
        if left < -30:
            continue  # дуже старе — не спамимо
        if _due_dedup.seen("due", bid, str(left)):
            continue

        if left < 0:
            head = f"🔴 <b>ПРОСТРОЧЕНИЙ РАХУНОК</b> ({abs(left)} дн.)"
        elif left == 0:
            head = "🔴 <b>ОПЛАТИТИ СЬОГОДНІ</b>"
        else:
            head = f"🟠 <b>ОПЛАТА ЧЕРЕЗ {left} дн.</b>"

        pid = _store.put({"bid": bid, "vendor": b.get("vendor"),
                          "amount": b.get("amount"), "currency": b.get("currency"),
                          "due": due, "invoice_no": b.get("invoice_no"),
                          "sender": b.get("sender"), "subject": b.get("subject"),
                          "note": b.get("note"), "uid": b.get("uid")})
        text = (f"{head}\n━━━━━━━━━━━━━━━━━━━━\n"
                f"🏢 <b>{K.esc(b.get('vendor'))}</b>\n"
                f"💰 {K.esc(_fmt_money(b))}\n📆 до <b>{due}</b>")
        if b.get("note"):
            text += f"\n\n{K.esc(b['note'])}"
        kb = [
            [{"text": "✅ Оплатив", "callback_data": f"bill_due_paid_{pid}"}],
            [{"text": "⏰ Нагадати завтра", "callback_data": f"bill_due_snooze_{pid}"},
             {"text": "📅 В календар", "callback_data": f"bill_cal_{pid}"}],
            [{"text": "✍️ Написати постачальнику", "callback_data": f"vr_menu_{pid}"}],
        ]
        if K.send_card(text, kb, tag=TAG):
            _due_dedup.mark("due", bid, str(left))
            sent += 1
    if sent:
        K.log(TAG, f"нагадувань про дедлайн: {sent}")
    return sent


# ─── МІСЯЧНИЙ ЗВІТ ───────────────────────────────────────────────────────────

def monthly_report(month: str = None) -> str:
    """month = 'YYYY-MM'. За замовчуванням — поточний місяць."""
    month = month or K.now().strftime("%Y-%m")
    bills = load_bills()
    rows = [b for b in bills.values()
            if str(b.get("created", "")).startswith(month)
            or str(b.get("due", "")).startswith(month)]
    if not rows:
        return f"💸 <b>РАХУНКИ {month}</b>\n\nЗа цей місяць рахунків не зафіксовано."

    total = sum(_amount_f(b.get("amount")) for b in rows)
    unpaid = [b for b in rows if not b.get("paid")]
    unpaid_sum = sum(_amount_f(b.get("amount")) for b in unpaid)

    out = [f"💸 <b>РАХУНКИ ЗА {month}</b>", "━━━━━━━━━━━━━━━━━━━━",
           f"📥 Прийшло: <b>{len(rows)}</b> шт  |  💰 Разом: <b>{total:.2f} EUR</b>",
           f"✅ Оплачено: {len(rows) - len(unpaid)}  |  ⏳ Не оплачено: <b>{len(unpaid)}</b>"
           + (f" ({unpaid_sum:.2f} EUR)" if unpaid_sum else ""), ""]

    for b in sorted(rows, key=lambda x: str(x.get("due") or "9999")):
        mark = "✅" if b.get("paid") else "⏳"
        out.append(f"{mark} <b>{K.esc(b.get('vendor'))}</b> — {_fmt_money(b)}"
                   + (f"  📆 {b['due']}" if b.get("due") else ""))

    prev = (K.now().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    prev_rows = [b for b in bills.values() if str(b.get("created", "")).startswith(prev)]
    if prev_rows:
        prev_sum = sum(_amount_f(b.get("amount")) for b in prev_rows)
        diff = total - prev_sum
        arrow = "📈" if diff > 0 else "📉"
        out.append("")
        out.append(f"{arrow} Проти {prev}: {prev_sum:.2f} EUR ({diff:+.2f} EUR)")
    return "\n".join(out)[:3900]


# ─── ДІЇ КНОПОК (з bot.py) ───────────────────────────────────────────────────

def do_calendar(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    due = p.get("due") or (K.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    start = K.parse_dt(due, "09:00")
    title = f"💸 Оплатити: {p.get('vendor')}"
    amount = _fmt_money(p)
    res = K.calendar_event(title, start, start + timedelta(minutes=30),
                           description=f"{amount}\n{p.get('note') or ''}\n\n— знайдено AI у пошті")
    if res.get("ok"):
        _store.drop(pid)
        return {"ok": True, "title": title, "date": due, "time": "09:00"}
    return {"ok": False, "error": res.get("error", "calendar_error")}


def do_reminder(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    due = p.get("due") or ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", due):
        try:
            d = datetime.strptime(due, "%Y-%m-%d") - timedelta(days=DUE_WARN_DAYS)
            if d.date() < K.now().date():
                d = K.now() + timedelta(days=1)
        except Exception:
            d = K.now() + timedelta(days=1)
    else:
        d = K.now() + timedelta(days=1)
    date_s = d.strftime("%Y-%m-%d")
    start = K.parse_dt(date_s, "08:30")
    title = f"🔔 Рахунок {p.get('vendor')} — {_fmt_money(p)}"
    res = K.calendar_event(title, start, start + timedelta(minutes=30),
                           description=f"Дедлайн оплати: {due or 'не вказаний'}\n"
                                       f"{p.get('note') or ''}\n\n— нагадування від AI")
    if res.get("ok"):
        _store.drop(pid)
        return {"ok": True, "title": title, "date": date_s, "time": "08:30"}
    return {"ok": False, "error": res.get("error", "calendar_error")}


def _mark_paid(bid: str):
    bills = load_bills()
    b = bills.get(bid)
    if not b:
        return False
    b["paid"] = True
    b["paid_at"] = K.today_str()
    K.update_key(BILLS_FILE, bid, b)
    return True


def do_paid(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    _mark_paid(p.get("bid", ""))
    _store.drop(pid)
    return {"ok": True, "vendor": p.get("vendor"), "amount": _fmt_money(p)}


def do_snooze(pid: str) -> dict:
    """Нагадати завтра: просто чистить дедуп на завтра (нагадування прийде знову)."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    return {"ok": True, "vendor": p.get("vendor")}


def do_skip(pid: str) -> dict:
    """«Це не рахунок» — прибираємо з bills.json, щоб не заважав звіту."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    bid = p.get("bid", "")
    if bid:
        K.remove_key(BILLS_FILE, bid)
    _store.drop(pid)
    return {"ok": True, "vendor": p.get("vendor")}


if __name__ == "__main__":
    import sys
    if "--report" in sys.argv:
        print(monthly_report())
    elif "--due" in sys.argv:
        print("нагадувань:", check_due_soon())
    elif "--cands" in sys.argv:
        c = _email_candidates()
        print(json.dumps(c, ensure_ascii=False, indent=1)[:4000] if c else c)
    else:
        print("карточок:", scan(force=True))
