#!/usr/bin/env python3
"""
РЕГУЛЯРНІ ПЛАТЕЖІ І ПІДПИСКИ  (Гроші #новий)

bills_watcher ловить ОДИН рахунок. Цей модуль ловить те, що списується
ЩОМІСЯЦЯ і про що легко забути: Netflix, iCloud, спортзал, хостинг,
мобільний тариф, страховка, донат, платний API.

Що робить:
  1. scan() — читає ті самі листи (IMAP-кеш, без нових сесій), Gemini витягує
     списання: {постачальник, сума, валюта, цикл, дата списання}.
  2. Веде реєстр subs.json. Підписка «підтверджується», коли:
        • AI прямо бачить у листі регулярність (renew / predplatné / monthly), АБО
        • від того самого постачальника прийшло ≥2 списання в РІЗНІ місяці.
  3. Рахує реальну ціну: X €/міс і X €/рік по всіх активних.
  4. check_renewals() — за 3 дні до наступного списання (і в день) — попередження
     з кнопкою «скасувати» (поки не списали, а не після).
  5. Ловить ПІДВИЩЕННЯ ЦІНИ: нове списання дорожче за попереднє → окремий алерт.
  6. report_block() — рядок у щоденний звіт.

Нічого не вигадуємо: немає суми в листі — рядок не стає підпискою.

Callback-префікси: sb_ok_ / sb_rem_ / sb_cancel_ / sb_stop_ / sb_skip_
"""

import re
import json
from datetime import datetime, timedelta

import ai_kit as K

TAG = "subs"

SUBS_FILE = "subs.json"            # {key: {...}}
STORE_FILE = "subs_store.json"     # payload кнопок
SENT_FILE = "subs_sent.json"       # антидубль карточок
SCAN_STATE = "subs_scan.json"      # rate-limit скану
DUE_STATE = "subs_due_sent.json"   # антидубль попереджень про списання

SCAN_MIN_GAP_MIN = 180             # раз на 3 години достатньо
MAX_CARDS_PER_SCAN = 3
DUE_WARN_DAYS = 3                  # попереджати за 3 дні до списання
HIKE_MIN_PCT = 3.0                 # від якого росту ціни кричати

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=45)
_due_dedup = K.Dedup(DUE_STATE, ttl_days=2)

_SUB_HINTS = (
    "підписк", "передплат", "продовж", "списан", "автоплатіж", "щомісяч",
    "predplatn", "obnoven", "predĺžen", "predlzen", "mesačn", "mesacn",
    "subscription", "subscribed", "renew", "renewal", "recurring", "auto-renew",
    "monthly plan", "yearly plan", "your plan", "membership", "billed",
    "receipt", "payment received", "invoice", "charged", "abonn", "premium",
)
_NOT_SUB = ("unsubscribe from newsletter", "webinar", "вебінар")

_CYCLE_MONTHS = {"weekly": 0.23, "monthly": 1.0, "quarterly": 3.0,
                 "yearly": 12.0, "annual": 12.0}


# ─── КОНТЕКСТ ────────────────────────────────────────────────────────────────

def _email_candidates(limit=12):
    """Листи, які МОЖУТЬ бути регулярним списанням. None = пошта недоступна."""
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
        blob = f"{sender} {subject} {body[:1500]}".lower()
        if not any(h in blob for h in _SUB_HINTS):
            continue
        if any(n in blob for n in _NOT_SUB):
            continue
        out.append({"uid": uid, "sender": sender, "subject": subject,
                    "body": body[:2500]})
        if len(out) >= limit:
            break
    return out


_PROMPT = """Ти — фінансовий асистент Олега (Кошице, Словаччина).
Знайди в листах РЕГУЛЯРНІ платежі — те, що списується періодично
(підписки, тарифи, членства, хостинг, страховки з місячним платежем).

ЗАРАЗ: {now}

ЛИСТИ:
{emails}

ПРАВИЛА:
1. Нічого не вигадуй. Немає суми — не додавай запис узагалі.
2. Разовий рахунок (одна покупка, доставка, ремонт) — ПРОПУСТИ, це не підписка.
3. cycle: "monthly" | "yearly" | "quarterly" | "weekly" | "unknown".
4. recurring: true лише якщо в тексті реально видно регулярність
   (renew, predplatné, monthly, "наступне списання", "щомісяця").
5. charge_date — дата ЦЬОГО списання (YYYY-MM-DD), якщо є в листі.
6. next_due — дата НАСТУПНОГО списання (YYYY-MM-DD), якщо вона прямо вказана.
7. amount — тільки число (12.99). currency — "EUR"/"USD"/"CZK".
8. vendor — коротка назва сервісу як у листі (Netflix, iCloud, O2, Orange).
9. note — 1 живе речення українською: що це і на що звернути увагу.

Формат — ТІЛЬКИ валідний JSON-масив, без markdown:
[{{"uid":"123","vendor":"Netflix","amount":"13.49","currency":"EUR",
   "cycle":"monthly","charge_date":"2026-08-14","next_due":"2026-09-14",
   "recurring":true,"note":"Стандартна підписка, списується 14 числа."}}]
Якщо регулярних платежів немає — поверни []."""


def _extract(cands):
    lines = []
    for c in cands:
        body_clean = re.sub(r"[ \t]+", " ", c["body"])[:1300]
        lines.append(
            f"--- uid={c['uid']}\nВІД: {c['sender'][:80]}\nТЕМА: {c['subject'][:120]}\n"
            f"ТЕКСТ: {body_clean}\n"
        )
    prompt = _PROMPT.format(now=K.now().strftime("%Y-%m-%d %H:%M"),
                            emails="\n".join(lines)[:9000])
    return K.gemini_json(prompt, max_tokens=1800, temperature=0.2, tag=TAG)


# ─── РЕЄСТР ──────────────────────────────────────────────────────────────────

def _slug(vendor) -> str:
    s = re.sub(r"[^a-z0-9а-яіїєґ]+", "", str(vendor or "").lower())
    return s[:32] or "unknown"


def load_subs() -> dict:
    return K.load(SUBS_FILE, default={}) or {}


def _amount_f(v):
    try:
        return float(str(v).replace(",", ".").replace(" ", "").replace("€", ""))
    except Exception:
        return 0.0


def _cycle_months(cycle) -> float:
    return _CYCLE_MONTHS.get(str(cycle or "").lower().strip(), 1.0)


def _fmt_money(a, cur="EUR") -> str:
    a = _amount_f(a)
    if not a:
        return "сума невідома"
    sign = {"EUR": "€", "USD": "$"}.get((cur or "EUR").upper(), "")
    return f"{a:.2f}{sign}" if sign else f"{a:.2f} {cur}"


def _valid_date(d) -> str:
    d = str(d or "").strip()
    return d if re.match(r"^\d{4}-\d{2}-\d{2}$", d) else ""


def _add_months(d: datetime, months: float) -> datetime:
    """Для цілих місяців зберігає число (14.08 → 14.09), а не +30 днів."""
    whole = int(round(months))
    if months < 1 or abs(months - whole) > 0.01:
        return d + timedelta(days=int(round(months * 30.4)))
    total = (d.year * 12 + (d.month - 1)) + whole
    y, m = divmod(total, 12)
    m += 1
    day = d.day
    while day > 28:
        try:
            return d.replace(year=y, month=m, day=day)
        except ValueError:
            day -= 1
    return d.replace(year=y, month=m, day=day)


def _calc_next(rec) -> str:
    """Наступне списання: явна дата з листа, інакше остання дата + цикл."""
    nd = _valid_date(rec.get("next_due"))
    charges = rec.get("charges") or []
    last = ""
    for ch in charges:
        d = _valid_date(ch.get("date"))
        if d and d > last:
            last = d
    if nd and (not last or nd > last):
        return nd
    if not last:
        return ""
    try:
        base = datetime.strptime(last, "%Y-%m-%d")
    except Exception:
        return nd
    nxt = _add_months(base, _cycle_months(rec.get("cycle")))
    today = K.now().date()
    guard = 0
    while nxt.date() < today and guard < 36:
        nxt = _add_months(nxt, _cycle_months(rec.get("cycle")))
        guard += 1
    return nxt.strftime("%Y-%m-%d")


def _distinct_months(charges) -> int:
    return len({str(c.get("date") or "")[:7] for c in charges
                if _valid_date(c.get("date"))})


def _upsert(item, src) -> tuple:
    """Оновлює/створює запис. Повертає (key, rec, event) де event:
    'new_confirmed' | 'hike' | 'charge' | ''."""
    key = _slug(item.get("vendor"))
    subs = load_subs()
    rec = subs.get(key) or {
        "vendor": str(item.get("vendor") or "")[:60],
        "cycle": "unknown", "currency": (item.get("currency") or "EUR")[:5],
        "charges": [], "active": True, "cancelled": False,
        "confirmed": False, "skipped": False,
        "first_seen": K.today_str(), "note": "",
    }
    if rec.get("skipped"):
        return key, rec, ""

    amount = _amount_f(item.get("amount"))
    date = _valid_date(item.get("charge_date")) or K.today_str()
    uid = str(item.get("uid") or "")

    event = ""
    prev_amounts = [_amount_f(c.get("amount")) for c in rec["charges"]
                    if _amount_f(c.get("amount"))]
    already = any(str(c.get("uid")) == uid and uid for c in rec["charges"])
    same_day = any(_valid_date(c.get("date")) == date
                   and abs(_amount_f(c.get("amount")) - amount) < 0.01
                   for c in rec["charges"])
    if amount and not already and not same_day:
        rec["charges"].append({"date": date, "amount": f"{amount:.2f}", "uid": uid})
        rec["charges"] = sorted(rec["charges"],
                                key=lambda c: str(c.get("date") or ""))[-24:]
        event = "charge"
        if prev_amounts:
            prev = prev_amounts[-1]
            if prev and amount > prev * (1 + HIKE_MIN_PCT / 100.0):
                rec["hike_from"] = f"{prev:.2f}"
                rec["hike_to"] = f"{amount:.2f}"
                event = "hike"

    if item.get("cycle") and str(item["cycle"]).lower() != "unknown":
        rec["cycle"] = str(item["cycle"]).lower()
    if item.get("currency"):
        rec["currency"] = str(item["currency"])[:5]
    if item.get("note"):
        rec["note"] = str(item["note"])[:300]
    if _valid_date(item.get("next_due")):
        rec["next_due"] = _valid_date(item["next_due"])
    if amount:
        rec["amount"] = f"{amount:.2f}"
    rec["sender"] = str((src or {}).get("sender") or rec.get("sender") or "")[:120]
    rec["subject"] = str((src or {}).get("subject") or rec.get("subject") or "")[:140]
    rec["uid"] = uid or rec.get("uid", "")
    rec["last_seen"] = K.today_str()

    was_confirmed = bool(rec.get("confirmed"))
    if bool(item.get("recurring")) or _distinct_months(rec["charges"]) >= 2:
        rec["confirmed"] = True
        if rec.get("cycle") == "unknown" and _distinct_months(rec["charges"]) >= 2:
            rec["cycle"] = "monthly"
    rec["next_due"] = _calc_next(rec)

    K.update_key(SUBS_FILE, key, rec)
    if rec["confirmed"] and not was_confirmed:
        event = "new_confirmed"
    return key, rec, event


# ─── ГРОШІ ───────────────────────────────────────────────────────────────────

def active_subs() -> dict:
    return {k: r for k, r in load_subs().items()
            if r.get("confirmed") and r.get("active")
            and not r.get("cancelled") and not r.get("skipped")}


def monthly_total() -> dict:
    """{'month': X, 'year': Y, 'count': N, 'items': [...]} — тільки активні."""
    items = []
    total = 0.0
    for k, r in active_subs().items():
        a = _amount_f(r.get("amount"))
        if not a:
            continue
        per_month = a / max(_cycle_months(r.get("cycle")), 0.1)
        total += per_month
        items.append({"key": k, "vendor": r.get("vendor"), "amount": a,
                      "cycle": r.get("cycle"), "per_month": per_month,
                      "currency": r.get("currency") or "EUR",
                      "next_due": r.get("next_due") or ""})
    items.sort(key=lambda i: -i["per_month"])
    return {"month": total, "year": total * 12, "count": len(items), "items": items}


# ─── КАРТОЧКИ ────────────────────────────────────────────────────────────────

def _kb(pid, with_cancel=True):
    kb = [[{"text": "✅ Так, це підписка", "callback_data": f"sb_ok_{pid}"}],
          [{"text": f"⏰ Нагадати за {DUE_WARN_DAYS} дні до списання",
            "callback_data": f"sb_rem_{pid}"}]]
    if with_cancel:
        kb.append([{"text": "🚫 Хочу скасувати — нагадай",
                    "callback_data": f"sb_cancel_{pid}"}])
    kb.append([{"text": "🗑 Вже не користуюсь", "callback_data": f"sb_stop_{pid}"},
               {"text": "❌ Не підписка", "callback_data": f"sb_skip_{pid}"}])
    return kb


def _offer_new(key, rec) -> bool:
    if _dedup.seen("sub", key, rec.get("amount")):
        return False
    tot = monthly_total()
    pid = _store.put({"key": key, "vendor": rec.get("vendor"),
                      "amount": rec.get("amount"), "currency": rec.get("currency"),
                      "cycle": rec.get("cycle"), "next_due": rec.get("next_due"),
                      "uid": rec.get("uid")})
    cyc = {"monthly": "щомісяця", "yearly": "щороку",
           "quarterly": "щокварталу", "weekly": "щотижня"}.get(rec.get("cycle"), "регулярно")
    text = (f"🔄 <b>НОВА РЕГУЛЯРНА ПЛАТА</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏢 <b>{K.esc(rec.get('vendor'))}</b>\n"
            f"💰 {_fmt_money(rec.get('amount'), rec.get('currency'))} {cyc}\n")
    if rec.get("next_due"):
        text += f"📆 Наступне списання: <b>{K.esc(rec['next_due'])}</b>\n"
    if rec.get("note"):
        text += f"\n{K.esc(rec['note'])}\n"
    a = _amount_f(rec.get("amount"))
    if a:
        per_year = a * (12.0 / max(_cycle_months(rec.get("cycle")), 0.1))
        text += f"\n📉 Це <b>{per_year:.0f}€ на рік</b>."
    if tot["count"]:
        text += (f"\n🧾 Разом активних підписок: <b>{tot['count']}</b> — "
                 f"<b>{tot['month']:.2f}€/міс</b> ({tot['year']:.0f}€/рік).")
    ok = K.send_card(text, _kb(pid), tag=TAG)
    if ok:
        _dedup.mark("sub", key, rec.get("amount"))
        K.log(TAG, f"✅ картка: {rec.get('vendor')} {rec.get('amount')}")
    else:
        _store.drop(pid)
    return ok


def _offer_hike(key, rec) -> bool:
    if _dedup.seen("hike", key, rec.get("hike_to")):
        return False
    pid = _store.put({"key": key, "vendor": rec.get("vendor"),
                      "amount": rec.get("amount"), "currency": rec.get("currency"),
                      "cycle": rec.get("cycle"), "next_due": rec.get("next_due"),
                      "uid": rec.get("uid")})
    old = _amount_f(rec.get("hike_from"))
    new = _amount_f(rec.get("hike_to"))
    diff = new - old
    pct = (diff / old * 100.0) if old else 0.0
    per_year = diff * (12.0 / max(_cycle_months(rec.get("cycle")), 0.1))
    text = (f"📈 <b>ПІДПИСКА ПОДОРОЖЧАЛА</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🏢 <b>{K.esc(rec.get('vendor'))}</b>\n"
            f"💰 було {old:.2f} → стало <b>{new:.2f}</b> "
            f"{(rec.get('currency') or 'EUR')} ({pct:+.0f}%)\n"
            f"📉 Це <b>+{per_year:.0f}€ на рік</b> з твоєї кишені.\n")
    if rec.get("next_due"):
        text += f"📆 Наступне списання: <b>{K.esc(rec['next_due'])}</b>\n"
    text += "\nВарто вирішити зараз, поки не списали знову."
    ok = K.send_card(text, _kb(pid), tag=TAG)
    if ok:
        _dedup.mark("hike", key, rec.get("hike_to"))
        K.log(TAG, f"✅ подорожчання: {rec.get('vendor')} {old}→{new}")
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
        K.log(TAG, "листів схожих на підписку немає")
        return 0

    K.log(TAG, f"кандидатів: {len(cands)}")
    found = _extract(cands)
    if not found:
        K.log(TAG, "AI не знайшов регулярних платежів")
        return 0

    by_uid = {str(c["uid"]): c for c in cands}
    sent = 0
    for it in found:
        if not isinstance(it, dict) or not str(it.get("vendor") or "").strip():
            continue
        if not _amount_f(it.get("amount")):
            continue
        src = by_uid.get(str(it.get("uid") or "")) or {}
        key, rec, event = _upsert(it, src)
        if event == "hike":
            if _offer_hike(key, rec):
                sent += 1
        elif event == "new_confirmed":
            if _offer_new(key, rec):
                sent += 1
        if sent >= MAX_CARDS_PER_SCAN:
            break
    K.log(TAG, f"скан завершено: {sent} карточок")
    _store.gc(days=60)
    return sent


# ─── ПОПЕРЕДЖЕННЯ ПРО СПИСАННЯ ───────────────────────────────────────────────

def check_renewals() -> int:
    today = K.now().date()
    sent = 0
    for key, r in active_subs().items():
        nd = _valid_date(r.get("next_due"))
        if not nd:
            continue
        try:
            d = datetime.strptime(nd, "%Y-%m-%d").date()
        except Exception:
            continue
        left = (d - today).days
        if left < 0 or left > DUE_WARN_DAYS:
            continue
        if _due_dedup.seen("due", key, nd, str(left)):
            continue
        pid = _store.put({"key": key, "vendor": r.get("vendor"),
                          "amount": r.get("amount"), "currency": r.get("currency"),
                          "cycle": r.get("cycle"), "next_due": nd,
                          "uid": r.get("uid")})
        when = "СЬОГОДНІ" if left == 0 else f"через {left} дн."
        text = (f"🔄 <b>СКОРО СПИСАННЯ</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🏢 <b>{K.esc(r.get('vendor'))}</b>\n"
                f"💰 {_fmt_money(r.get('amount'), r.get('currency'))} — {when} ({nd})\n")
        if r.get("note"):
            text += f"\n{K.esc(r['note'])}\n"
        # хвіст через spice: терміновість + ставка + €/рік + один крок.
        # Раніше тут був один статичний рядок на всі випадки.
        try:
            import spice
            _t = spice.tail("sub", left,
                            {"amount": r.get("amount"), "cycle": r.get("cycle")},
                            key=str(r.get("vendor") or key))
        except Exception:
            _t = "Якщо не користуєшся — скасувати треба ДО списання."
        if _t:
            text += "\n" + _t
        kb = [[{"text": "👍 Все ок, хай списують", "callback_data": f"sb_ok_{pid}"}],
              [{"text": "🚫 Хочу скасувати — нагадай",
                "callback_data": f"sb_cancel_{pid}"}],
              [{"text": "🗑 Вже не користуюсь", "callback_data": f"sb_stop_{pid}"}]]
        if K.send_card(text, kb, tag=TAG):
            _due_dedup.mark("due", key, nd, str(left))
            sent += 1
        else:
            _store.drop(pid)
    if sent:
        K.log(TAG, f"попереджень про списання: {sent}")
    return sent


# ─── ЗВІТ ────────────────────────────────────────────────────────────────────

def report_block() -> str:
    tot = monthly_total()
    if not tot["count"]:
        return ""
    lines = [f"🔄 <b>ПІДПИСКИ</b>: {tot['count']} активних — "
             f"<b>{tot['month']:.2f}€/міс</b> ({tot['year']:.0f}€/рік)"]
    for it in tot["items"][:5]:
        nd = f" · {it['next_due']}" if it["next_due"] else ""
        lines.append(f"   • {K.esc(it['vendor'])} — {it['amount']:.2f}"
                     f"{'€' if (it['currency'] or 'EUR').upper() == 'EUR' else ' ' + it['currency']}"
                     f"{nd}")
    soon = [i for i in tot["items"] if i["next_due"]
            and 0 <= (datetime.strptime(i["next_due"], "%Y-%m-%d").date()
                      - K.now().date()).days <= 7]
    if soon:
        names = ", ".join(f"{s['vendor']} ({s['next_due'][5:]})" for s in soon[:4])
        lines.append(f"   ⏳ Цього тижня спишуть: {K.esc(names)}")
    return "\n".join(lines)


def overview_text() -> str:
    """/підписки — повний список з сумами."""
    subs = load_subs()
    tot = monthly_total()
    if not subs:
        return ("🔄 <b>ПІДПИСКИ</b>\n\nПоки нічого не знайшов у пошті. "
                "Модуль сам додасть, коли прийде лист про списання.")
    lines = [f"🔄 <b>ПІДПИСКИ</b>\n"
             f"Активних: <b>{tot['count']}</b> — <b>{tot['month']:.2f}€/міс</b> "
             f"({tot['year']:.0f}€/рік)\n━━━━━━━━━━━━━━━━━━━━"]
    for it in tot["items"]:
        cyc = {"monthly": "міс", "yearly": "рік", "quarterly": "кв",
               "weekly": "тиж"}.get(it["cycle"], "?")
        nd = f"  📆 {it['next_due']}" if it["next_due"] else ""
        lines.append(f"• <b>{K.esc(it['vendor'])}</b> — {it['amount']:.2f} "
                     f"{it['currency']}/{cyc}{nd}")
    off = [r for r in subs.values()
           if r.get("confirmed") and (r.get("cancelled") or not r.get("active"))]
    if off:
        lines.append("\n<i>Скасовані/неактивні:</i> "
                     + K.esc(", ".join(str(r.get("vendor")) for r in off[:8])))
    pend = [r for r in subs.values() if not r.get("confirmed")
            and not r.get("skipped")]
    if pend:
        lines.append(f"\n<i>Під наглядом (мало даних): "
                     f"{K.esc(', '.join(str(r.get('vendor')) for r in pend[:6]))}</i>")
    return "\n".join(lines)


# ─── ДІЇ КНОПОК ──────────────────────────────────────────────────────────────

def _get(pid):
    return _store.get(pid)


def do_ok(pid) -> dict:
    p = _get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    key = p.get("key")
    subs = load_subs()
    r = subs.get(key)
    if r:
        r["confirmed"] = True
        r["active"] = True
        r["cancelled"] = False
        K.update_key(SUBS_FILE, key, r)
    _store.drop(pid)
    tot = monthly_total()
    return {"ok": True, "vendor": p.get("vendor"),
            "month": tot["month"], "count": tot["count"]}


def do_reminder(pid) -> dict:
    p = _get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    nd = _valid_date(p.get("next_due"))
    if nd:
        try:
            d = datetime.strptime(nd, "%Y-%m-%d") - timedelta(days=DUE_WARN_DAYS)
            if d.date() < K.now().date():
                d = K.now() + timedelta(days=1)
        except Exception:
            d = K.now() + timedelta(days=1)
    else:
        d = K.now() + timedelta(days=1)
    date_s = d.strftime("%Y-%m-%d")
    start = K.parse_dt(date_s, "09:00")
    title = (f"🔄 Підписка {p.get('vendor')} — "
             f"{_fmt_money(p.get('amount'), p.get('currency'))}")
    res = K.calendar_event(title, start, start + timedelta(minutes=20),
                           description=f"Списання: {nd or 'дата невідома'}\n"
                                       f"Рішення: залишаю чи скасовую?\n\n— AI-асистент")
    if res.get("ok"):
        _store.drop(pid)
        return {"ok": True, "title": title, "date": date_s, "time": "09:00"}
    return {"ok": False, "error": res.get("error", "calendar_error")}


def do_cancel(pid) -> dict:
    """«Хочу скасувати» — ставить задачу в календар ДО списання."""
    p = _get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    nd = _valid_date(p.get("next_due"))
    if nd:
        try:
            d = datetime.strptime(nd, "%Y-%m-%d") - timedelta(days=2)
            if d.date() < K.now().date():
                d = K.now() + timedelta(days=1)
        except Exception:
            d = K.now() + timedelta(days=1)
    else:
        d = K.now() + timedelta(days=1)
    date_s = d.strftime("%Y-%m-%d")
    start = K.parse_dt(date_s, "18:30")
    title = f"🚫 Скасувати підписку {p.get('vendor')}"
    res = K.calendar_event(
        title, start, start + timedelta(minutes=20),
        description=f"Списання буде {nd or 'невідомо коли'} — "
                    f"{_fmt_money(p.get('amount'), p.get('currency'))}.\n"
                    f"Зайти в акаунт і скасувати автопродовження.\n\n— AI-асистент")
    if res.get("ok"):
        return {"ok": True, "title": title, "date": date_s, "time": "18:30",
                "vendor": p.get("vendor"), "due": nd}
    return {"ok": False, "error": res.get("error", "calendar_error")}


def do_stop(pid) -> dict:
    """«Вже не користуюсь» — позначаємо скасованою, рахунок місячних падає."""
    p = _get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    key = p.get("key")
    subs = load_subs()
    r = subs.get(key)
    saved = 0.0
    if r:
        saved = _amount_f(r.get("amount")) * (12.0 / max(_cycle_months(r.get("cycle")), 0.1))
        r["cancelled"] = True
        r["active"] = False
        r["cancelled_at"] = K.today_str()
        K.update_key(SUBS_FILE, key, r)
    _store.drop(pid)
    tot = monthly_total()
    return {"ok": True, "vendor": p.get("vendor"), "saved_year": saved,
            "month": tot["month"], "count": tot["count"]}


def do_skip(pid) -> dict:
    p = _get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    key = p.get("key")
    subs = load_subs()
    r = subs.get(key)
    if r:
        r["skipped"] = True
        r["confirmed"] = False
        r["active"] = False
        K.update_key(SUBS_FILE, key, r)
    _store.drop(pid)
    return {"ok": True, "vendor": p.get("vendor")}


if __name__ == "__main__":
    import sys
    if "--report" in sys.argv:
        print(overview_text())
    elif "--due" in sys.argv:
        print("попереджень:", check_renewals())
    elif "--block" in sys.argv:
        print(report_block())
    else:
        print("карточок:", scan(force=True))
