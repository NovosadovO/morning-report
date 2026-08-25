#!/usr/bin/env python3
"""
ГРОШІ ОДНІЄЮ ЛІНІЄЮ  (money.py)

Проблема: рахунки живуть у bills_watcher, підписки — у subs_watcher, реальні
списання з пошти взагалі ніде. Олег не бачить ОДНОЇ картини: скільки вже пішло
цього місяця, скільки ще спишуть, і що з цього фіксоване щомісяця.

Що робить цей модуль:
  1. ЗАПИСУЄ: витягує з пошти підтвердження РЕАЛЬНИХ списань/оплат
     («payment received», «zaplatené», «списано», «receipt») і кладе їх
     у money_charges.json — це факт, а не прогноз.
  2. ЗВОДИТЬ В ОДНУ ЛІНІЮ: витрачено цього місяця (факти) + чекає оплати
     (неоплачені рахунки) + фіксована щомісячна вага підписок + що спишуть
     у найближчі 14 днів. Дані беруться з bills_watcher / subs_watcher,
     нічого не дублюється.
  3. СТВОРЮЄ: якщо на найближчі дні є великий платіж (> BIG_EUR) — сам кладе
     подію в календар на дату платежу і каже про це постфактум.
  4. ЗАПИТУЄ: якщо місяць іде дорожче за попередній на > JUMP_PCT, або є
     прострочені неоплачені рахунки — пише карточку з ПРЯМИМ питанням і
     кнопками реакції (react.py).

Правда важливіша за красу: немає даних — модуль МОВЧИТЬ. Нулі не малюються,
прогноз ніколи не подається як факт, суми в різних валютах не змішуються
(рахуємо EUR, решту показуємо окремо).

Команда: /гроші (/money, /витрати, /лінія)
Callback-префікс: mn_
"""

import re
from datetime import datetime, timedelta

import ai_kit as K

TAG = "money"

CHARGES_FILE = "money_charges.json"     # {cid: {vendor, amount, currency, date, ...}}
STORE_FILE = "money_store.json"         # payload кнопок
SENT_FILE = "money_sent.json"           # антидубль карточок
SCAN_STATE = "money_scan.json"          # rate-limit скану пошти
ALERT_STATE = "money_alert.json"        # антидубль питань про перевитрату
EVENT_STATE = "money_events.json"       # які платежі вже покладені в календар

SCAN_GAP_MIN = 150          # скан пошти не частіше ніж раз на 2.5 години
MAX_CARDS = 2               # максимум карточок за один прохід
LOOKAHEAD_DAYS = 14         # горизонт «що спишуть»
BIG_EUR = 80.0              # від якої суми платіж вартий події в календарі
JUMP_PCT = 25.0             # на скільки % місяць має бути дорожчим, щоб питати
MIN_BASE_EUR = 40.0         # нижче цієї бази відсотки — шум, не питаємо

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=20)
_ev_dedup = K.Dedup(EVENT_STATE, ttl_days=45)

# Маркери ФАКТИЧНОГО списання (не рахунку — рахунки веде bills_watcher)
_PAID_HINTS = (
    "списано", "оплата отримана", "платіж успішн", "квитанц", "чек про оплату",
    "zaplatené", "zaplatene", "platba prijatá", "platba prijata", "úhrada prijatá",
    "potvrdenie o platbe", "doklad o zaplatení", "odúčtovan", "stiahnut z karty",
    "payment received", "payment successful", "payment confirmation",
    "we received your payment", "your receipt", "receipt from", "thanks for your payment",
    "thank you for your payment", "charged", "invoice paid", "paid successfully",
    "transaction complete", "order confirmed",
)
_NOT_PAID = ("unsubscribe from our newsletter", "webinar", "вебінар",
             "black friday", "sale ends", "знижк", "промокод")

_CUR_SIGN = {"EUR": "€", "USD": "$", "CZK": " CZK", "PLN": " PLN", "UAH": " ₴"}


# ─── ПОШТА → ФАКТИ СПИСАНЬ ───────────────────────────────────────────────────

def _email_candidates(limit=10):
    """Листи, які МОЖУТЬ бути підтвердженням оплати. None = пошта недоступна."""
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
        if not any(h in blob for h in _PAID_HINTS):
            continue
        if any(n in blob for n in _NOT_PAID):
            continue
        out.append({"uid": uid, "sender": sender, "subject": subject,
                    "body": body[:2200]})
        if len(out) >= limit:
            break
    return out


_PROMPT = """Ти — фінансовий асистент Олега (Кошице, Словаччина).
Витягни з листів нижче ТІЛЬКИ підтвердження ФАКТИЧНО здійснених списань/оплат.

ЗАРАЗ: {now}

ЛИСТИ:
{emails}

ПРАВИЛА:
1. Нічого не вигадуй. Немає суми або дати в тексті — залиш поле "".
2. Це має бути ФАКТ оплати (гроші вже пішли). Рахунок до оплати, реклама,
   нагадування «оплатіть» — ПРОПУСТИ, їх веде інший модуль.
3. date — дата списання YYYY-MM-DD. Якщо в листі лише «today»/«сьогодні» —
   постав сьогоднішню дату. Немає жодної дати — "".
4. amount — тільки число (наприклад 12.90). currency — "EUR"/"USD"/"CZK"/"PLN".
5. vendor — коротка назва отримувача грошей, як у листі (2-4 слова).
6. kind — одне з: "sub" (підписка/сервіс), "bill" (рахунок/комунальні),
   "shop" (покупка), "other".
7. note — 1 коротке речення українською по суті платежу.

Формат — ТІЛЬКИ валідний JSON-масив, без markdown:
[{{"uid":"12345","vendor":"Runable","amount":"10.00","currency":"USD",
   "date":"2026-08-23","kind":"sub","note":"Місячна оплата Runable."}}]
Якщо фактичних списань немає — поверни []."""


def _extract(cands):
    lines = []
    for c in cands:
        body_clean = re.sub(r"[ \t]+", " ", c["body"])[:1200]
        lines.append(
            f"--- uid={c['uid']}\nВІД: {c['sender'][:80]}\nТЕМА: {c['subject'][:120]}\n"
            f"ТЕКСТ: {body_clean}\n"
        )
    prompt = _PROMPT.format(now=K.now().strftime("%Y-%m-%d %H:%M"),
                            emails="\n".join(lines)[:9000])
    return K.gemini_json(prompt, max_tokens=1200, temperature=0.2, tag=TAG)


# ─── РЕЄСТР СПИСАНЬ ──────────────────────────────────────────────────────────

def load_charges() -> dict:
    return K.load(CHARGES_FILE, default={}) or {}


def _amount_f(v):
    try:
        s = str(v).replace(",", ".").strip()
        s = re.sub(r"[^0-9.]", "", s)
        return round(float(s), 2) if s else 0.0
    except Exception:
        return 0.0


def _cid(vendor, amount, date) -> str:
    base = f"{str(vendor).lower().strip()[:24]}|{amount}|{date}"
    return re.sub(r"[^a-z0-9а-яіїєґ.|\-]+", "_", base)


def _fmt(amount, cur="EUR") -> str:
    cur = (cur or "EUR").upper()
    sign = _CUR_SIGN.get(cur, " " + cur)
    return f"{amount:.2f}{sign}"


def _record(item) -> str:
    """Записує факт списання. Повертає cid, або "" якщо даних мало/вже є."""
    vendor = str(item.get("vendor") or "").strip()
    amount = _amount_f(item.get("amount"))
    date = str(item.get("date") or "").strip()[:10]
    if not vendor or not amount:
        return ""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return ""            # немає достовірної дати — факт не пишемо
    cid = _cid(vendor, amount, date)
    if cid in load_charges():
        return ""
    rec = {"vendor": vendor, "amount": amount,
           "currency": (str(item.get("currency") or "EUR")).upper(),
           "date": date, "kind": str(item.get("kind") or "other"),
           "note": str(item.get("note") or "")[:200],
           "uid": str(item.get("uid") or ""), "ts": K.now().isoformat()}
    K.update_key(CHARGES_FILE, cid, rec)
    return cid


# ─── ОДНА ЛІНІЯ ──────────────────────────────────────────────────────────────

def _month_key(dt=None) -> str:
    return (dt or K.now()).strftime("%Y-%m")


def _prev_month_key() -> str:
    first = K.now().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def spent(month: str = None) -> dict:
    """Факти списань за місяць: {'eur': X, 'count': N, 'other': {cur: sum}}."""
    month = month or _month_key()
    eur = 0.0
    other = {}
    rows = []
    for r in load_charges().values():
        if not str(r.get("date", "")).startswith(month):
            continue
        rows.append(r)
        a = _amount_f(r.get("amount"))
        cur = (r.get("currency") or "EUR").upper()
        if cur == "EUR":
            eur += a
        else:
            other[cur] = round(other.get(cur, 0.0) + a, 2)
    rows.sort(key=lambda x: str(x.get("date")), reverse=True)
    return {"eur": round(eur, 2), "count": len(rows), "other": other, "rows": rows}


def _unpaid_bills() -> list:
    """Неоплачені рахунки з bills_watcher: [{vendor, amount, currency, due, left}]."""
    try:
        import bills_watcher as B
        bills = B.load_bills()
    except Exception as e:
        K.log(TAG, f"bills недоступні: {e}")
        return []
    out = []
    today = K.now().date()
    for b in bills.values():
        if b.get("paid") or b.get("skipped"):
            continue
        a = _amount_f(b.get("amount"))
        due = str(b.get("due") or "")[:10]
        left = None
        if re.match(r"^\d{4}-\d{2}-\d{2}$", due):
            try:
                left = (datetime.strptime(due, "%Y-%m-%d").date() - today).days
            except Exception:
                left = None
        out.append({"vendor": str(b.get("vendor") or "?"), "amount": a,
                    "currency": (b.get("currency") or "EUR").upper(),
                    "due": due, "left": left})
    out.sort(key=lambda x: (x["left"] is None, x["left"] if x["left"] is not None else 0))
    return out


def _subs() -> dict:
    """Підписки: {'month': €/міс, 'count': N, 'items': [...]} або порожньо."""
    try:
        import subs_watcher as S
        return S.monthly_total()
    except Exception as e:
        K.log(TAG, f"subs недоступні: {e}")
        return {"month": 0.0, "year": 0.0, "count": 0, "items": []}


def upcoming(days: int = LOOKAHEAD_DAYS) -> list:
    """Що спишуть/треба заплатити найближчі N днів — рахунки + підписки."""
    today = K.now().date()
    out = []
    for b in _unpaid_bills():
        if b["left"] is None or b["left"] < 0 or b["left"] > days:
            continue
        out.append({"what": b["vendor"], "amount": b["amount"],
                    "currency": b["currency"], "date": b["due"],
                    "left": b["left"], "src": "bill"})
    for it in _subs().get("items") or []:
        nd = str(it.get("next_due") or "")[:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", nd):
            continue
        try:
            left = (datetime.strptime(nd, "%Y-%m-%d").date() - today).days
        except Exception:
            continue
        if left < 0 or left > days:
            continue
        out.append({"what": it.get("vendor") or "?",
                    "amount": _amount_f(it.get("amount")),
                    "currency": (it.get("currency") or "EUR").upper(),
                    "date": nd, "left": left, "src": "sub"})
    out.sort(key=lambda x: x["left"])
    return out


def overdue() -> list:
    return [b for b in _unpaid_bills()
            if b["left"] is not None and b["left"] < 0]


def picture() -> dict:
    """Уся картина в одному місці. Нічого не вигадує."""
    cur = spent()
    prev = spent(_prev_month_key())
    subs = _subs()
    ups = upcoming()
    ovd = overdue()
    unpaid = _unpaid_bills()
    unpaid_eur = round(sum(b["amount"] for b in unpaid
                           if b["currency"] == "EUR"), 2)
    diff_pct = None
    if prev["eur"] >= MIN_BASE_EUR and cur["eur"]:
        diff_pct = round((cur["eur"] - prev["eur"]) / prev["eur"] * 100, 1)
    return {"month": _month_key(), "spent": cur, "prev": prev,
            "subs": subs, "upcoming": ups, "overdue": ovd,
            "unpaid": unpaid, "unpaid_eur": unpaid_eur, "diff_pct": diff_pct,
            "has_data": bool(cur["count"] or unpaid or subs.get("count"))}


def line() -> str:
    """Один рядок для звіту. Порожній рядок = немає про що казати."""
    p = picture()
    if not p["has_data"]:
        return ""
    bits = []
    if p["spent"]["count"]:
        bits.append(f"витрачено <b>{_fmt(p['spent']['eur'])}</b>")
    if p["unpaid_eur"]:
        bits.append(f"чекає оплати {_fmt(p['unpaid_eur'])}")
    if p["subs"].get("count"):
        bits.append(f"підписки {_fmt(p['subs']['month'])}/міс")
    if not bits:
        return ""
    head = f"💰 <b>{p['month']}</b>: " + " · ".join(bits)
    tail = ""
    if p["overdue"]:
        tail = f"\n   ⚠️ прострочено: {len(p['overdue'])} рахунк(и)"
    elif p["upcoming"]:
        u = p["upcoming"][0]
        tail = (f"\n   ⏳ найближче: {K.esc(u['what'])} "
                f"{_fmt(u['amount'], u['currency'])} через {u['left']} дн.")
    return head + tail


def report() -> str:
    """/гроші — повна картина."""
    p = picture()
    if not p["has_data"]:
        return ("💰 <b>ГРОШІ</b>\n\nПоки нічого достовірного немає: ні списань у "
                "пошті, ні неоплачених рахунків, ні активних підписок. "
                "Як тільки прийде лист про оплату — запишу сам.")
    out = [f"💰 <b>ГРОШІ — {p['month']}</b>", "━━━━━━━━━━━━━━━━━━━━"]

    s = p["spent"]
    if s["count"]:
        extra = ""
        if s["other"]:
            extra = " + " + ", ".join(_fmt(v, c) for c, v in s["other"].items())
        out.append(f"✅ Фактично списано: <b>{_fmt(s['eur'])}</b>{extra} "
                   f"({s['count']} платеж(і))")
        if p["diff_pct"] is not None:
            arrow = "📈" if p["diff_pct"] > 0 else "📉"
            out.append(f"   {arrow} проти {_prev_month_key()} "
                       f"({_fmt(p['prev']['eur'])}): {p['diff_pct']:+.1f}%")
    else:
        out.append("✅ Фактичних списань цього місяця в пошті не знайшов.")

    if p["unpaid"]:
        out.append("")
        out.append(f"⏳ <b>Чекає оплати</b>: {len(p['unpaid'])} шт"
                   + (f" — {_fmt(p['unpaid_eur'])}" if p["unpaid_eur"] else ""))
        for b in p["unpaid"][:6]:
            when = f"  📆 {b['due']}" if b["due"] else "  📆 дати немає"
            mark = "🔴" if (b["left"] is not None and b["left"] < 0) else "•"
            out.append(f"{mark} {K.esc(b['vendor'])} — "
                       f"{_fmt(b['amount'], b['currency'])}{when}")

    if p["subs"].get("count"):
        out.append("")
        out.append(f"🔄 <b>Фіксовано щомісяця</b>: {p['subs']['count']} підписок — "
                   f"{_fmt(p['subs']['month'])}/міс ({p['subs']['year']:.0f}€/рік)")

    if p["upcoming"]:
        out.append("")
        out.append(f"📅 <b>Наступні {LOOKAHEAD_DAYS} днів</b>:")
        for u in p["upcoming"][:7]:
            icon = "🧾" if u["src"] == "bill" else "🔄"
            out.append(f"{icon} {u['date']} — {K.esc(u['what'])} "
                       f"{_fmt(u['amount'], u['currency'])} (через {u['left']} дн.)")
        tot = round(sum(u["amount"] for u in p["upcoming"]
                        if u["currency"] == "EUR"), 2)
        if tot:
            out.append(f"   Разом до списання: <b>{_fmt(tot)}</b>")

    if s["rows"]:
        out.append("")
        out.append("<i>Останні платежі:</i>")
        for r in s["rows"][:5]:
            out.append(f"   {r['date']} · {K.esc(r['vendor'])} — "
                       f"{_fmt(_amount_f(r['amount']), r.get('currency'))}")
    return "\n".join(out)[:3900]


# ─── ІНІЦІАТИВА: СТВОРИТИ ────────────────────────────────────────────────────

def _put_in_calendar(u) -> bool:
    """Кладе великий майбутній платіж у календар. True = створено."""
    if _ev_dedup.seen("mn_ev", u["what"], u["date"]):
        return False
    try:
        start = datetime.strptime(u["date"], "%Y-%m-%d").replace(hour=9, minute=0)
    except Exception:
        return False
    title = f"💸 Оплата: {u['what']} — {_fmt(u['amount'], u['currency'])}"
    try:
        res = K.calendar_event(title, start, start + timedelta(hours=1),
                              description="Створено ботом із листа/підписки.")
    except Exception as e:
        K.log(TAG, f"calendar error: {e}")
        return False
    if not res:
        return False
    _ev_dedup.mark("mn_ev", u["what"], u["date"])
    return True


# ─── ІНІЦІАТИВА: ЗАПИТАТИ ────────────────────────────────────────────────────

def _kb(pid, kind):
    try:
        import react as R
        return R.keyboard(kind)
    except Exception:
        return [[{"text": "👌 Прийняв", "callback_data": f"mn_ok_{pid}"}]]


def _ask(text, kind, dkey) -> bool:
    if _dedup.seen("mn_ask", dkey):
        return False
    pid = _store.put({"key": dkey, "kind": kind})
    if K.send_card(text, _kb(pid, kind), tag=TAG):
        _dedup.mark("mn_ask", dkey)
        return True
    return False


def _muted(key) -> bool:
    try:
        import dismissed as D
        if D.is_muted("money", key):
            return True
    except Exception:
        pass
    try:
        import react as R
        if R.is_closed("money", key):
            return True
    except Exception:
        pass
    return False


# ─── ГОЛОВНИЙ ПРОХІД ─────────────────────────────────────────────────────────

def run(force: bool = False) -> int:
    """Скан пошти → запис фактів → календар для великих платежів → питання."""
    if not force and not K.rate_ok(SCAN_STATE, SCAN_GAP_MIN):
        return 0
    K.rate_mark(SCAN_STATE)

    # 1. ЗАПИСАТИ факти списань
    written = 0
    cands = _email_candidates()
    if cands is None:
        K.log(TAG, "пошта недоступна — фактів не пишу")
    elif cands:
        items = _extract(cands)
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict) and _record(it):
                    written += 1
        K.log(TAG, f"листів-кандидатів {len(cands)}, нових списань {written}")

    p = picture()
    if not p["has_data"]:
        return written

    sent = 0

    # 2. СТВОРИТИ подію на великий майбутній платіж
    created = []
    for u in p["upcoming"]:
        if u["currency"] == "EUR" and u["amount"] >= BIG_EUR and u["left"] <= 7:
            if _put_in_calendar(u):
                created.append(u)
    if created:
        lines = ["📅 <b>Поклав у календар великі платежі</b>:"]
        for u in created:
            lines.append(f"• {u['date']} — {K.esc(u['what'])} "
                         f"{_fmt(u['amount'], u['currency'])}")
        lines.append("\nЯкщо щось із цього вже оплачено — скажи, приберу.")
        if _ask("\n".join(lines), "money", "big_" + created[0]["date"]):
            sent += 1

    # 3. ЗАПИТАТИ про прострочене
    ovd = p["overdue"]
    if ovd and sent < MAX_CARDS and not _muted("overdue"):
        key = "ovd_" + K.now().strftime("%Y-%W")
        names = ", ".join(f"{b['vendor']} ({_fmt(b['amount'], b['currency'])})"
                          for b in ovd[:4])
        txt = (f"🔴 <b>Прострочені рахунки: {len(ovd)}</b>\n{K.esc(names)}\n\n"
               f"Дедлайн уже минув. Ти їх оплатив і я просто не бачив "
               f"підтвердження, чи вони справді висять?")
        if _ask(txt, "bill", key):
            sent += 1

    # 4. ЗАПИТАТИ про стрибок витрат
    if (sent < MAX_CARDS and p["diff_pct"] is not None
            and p["diff_pct"] >= JUMP_PCT and not _muted("jump")):
        key = "jump_" + p["month"]
        top = p["spent"]["rows"][:3]
        det = "\n".join(f"   • {r['date']} · {K.esc(r['vendor'])} — "
                        f"{_fmt(_amount_f(r['amount']), r.get('currency'))}"
                        for r in top)
        txt = (f"📈 <b>Місяць іде дорожче</b>\n"
               f"{p['month']}: {_fmt(p['spent']['eur'])} проти "
               f"{_fmt(p['prev']['eur'])} у {_prev_month_key()} "
               f"({p['diff_pct']:+.1f}%).\n\nОстанні платежі:\n{det}\n\n"
               f"Це разові витрати чи нова постійна вага? Від відповіді залежить, "
               f"чи рахувати це в щомісячний мінімум.")
        if _ask(txt, "money", key):
            sent += 1

    if sent:
        K.log(TAG, f"карточок надіслано: {sent}")
    return written + sent


# ─── КНОПКИ ──────────────────────────────────────────────────────────────────

def handle(pid: str) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "text": "Дані кнопки застаріли."}
    _store.drop(pid)
    return {"ok": True, "text": "👌 Записав."}


if __name__ == "__main__":
    print(report())
