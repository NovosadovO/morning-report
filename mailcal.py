#!/usr/bin/env python3
"""
ЛИСТ → КАЛЕНДАР АВТОМАТИЧНО  (mailcal)

Раніше подія з листа потрапляла в Google Calendar ТІЛЬКИ якщо Олег сам
відкрив лист, натиснув «📅 Календар», а потім ще «✅ Додати всі».
Тепер бот робить це САМ (Олег дав повний доступ до календаря):

  • сам читає нові листи (ті самі, що бачить звіт)
  • AI витягує КОНКРЕТНІ дати: рейси, платежі, зустрічі, скасування, дедлайни
  • подія створюється ОДРАЗУ, без питання
  • Олег отримує картку постфактум: «створив ось це» + 🗑 видалити, якщо зайве

ВИНЯТОК — дати майбутніх ПЛАТЕЖІВ (наступний платіж, продовження, поповнення,
закінчення підписки/поліса/ліміту). Їх бот сам у календар НЕ пише: він
ПРОПОНУЄ картку з кнопками «📅 Поставити нагадування» / «⏭ Не треба»,
і подія з'являється лише після натискання. Це працює для листів на будь-яку
тему, включно з підтвердженнями оплати (напр. страховка).

Нічого не вигадується: якщо в листі немає явної дати — модуль молчить.
Дедуплікація за uid листа + (назва, дата), щоб один лист не створив
подію двічі й щоб повторний скан не дублював.

Callback-префікси: mc_ok_ / mc_del_ / mc_pyes_ / mc_pyes2_ / mc_pno_
"""

import json
import re
import urllib.request
from datetime import datetime, timedelta

import ai_kit as K

TAG = "mailcal"

ITEMS_FILE = "mailcal.json"          # {key: {...}} створені події
STORE_FILE = "mailcal_store.json"    # payload кнопок
SENT_FILE = "mailcal_sent.json"      # антидубль карточок
SCAN_STATE = "mailcal_scan.json"     # rate-limit скану

SCAN_GAP_MIN = 45          # не частіше разу на 45 хв
MAX_EMAILS = 8             # скільком листам за раз дивимось у тіло
MAX_EVENTS_PER_RUN = 4     # скільком подіям за раз дозволяємо створитись
HORIZON_DAYS = 400         # далі — вважаємо що AI помилився з роком

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=60)

# Слова, що майже завжди означають шум — такі листи не аналізуємо
_NOT = (
    "unsubscribe from our newsletter", "black friday", "promo code",
    "промокод", "розсилка новин", "webinar invitation", "newsletter",
    "sale ends", "-50%", "знижка тижня",
)

KINDS = {
    "flight": "✈️",
    "payment": "💳",
    "meeting": "🤝",
    "appointment": "🩺",
    "deadline": "⏳",
    "delivery": "📦",
    "cancel": "🚫",
    "next_payment": "💳",
    "renewal": "🔁",
    "other": "📌",
}
# Ці види — лише пропозиція з кнопкою, без автостворення в календарі.
PROPOSE_KINDS = ("next_payment", "renewal", "payment", "deadline")
MAX_PROPOSALS_PER_RUN = 3


# ─── ЛИСТИ ───────────────────────────────────────────────────────────────────

def _emails(limit=MAX_EMAILS):
    """Нові листи з тілом: [{uid, sender, subject, body}] | None якщо пошта впала."""
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
        if not uid:
            continue
        sender = str(e.get("sender") or e.get("from") or "")
        subject = str(e.get("subject") or "")
        body = str((bodies.get(uid) or {}).get("body") or "")
        blob = f"{sender} {subject} {body[:1500]}".lower()
        if any(n in blob for n in _NOT):
            continue
        out.append({"uid": uid, "sender": sender, "subject": subject,
                    "body": body[:2500]})
        if len(out) >= limit:
            break
    return out


# ─── AI ──────────────────────────────────────────────────────────────────────

_PROMPT = """Ти — асистент Олега (Кошице, Словаччина, працює в Minebea Mitsumi).
Знайди в листах нижче ПОДІЇ З КОНКРЕТНОЮ ДАТОЮ, які варто мати в календарі:
рейси, поїздки, платежі й списання, зустрічі, візити до лікаря, дедлайни,
доставки, скасування чи перенесення.

ОСОБЛИВО УВАЖНО шукай ДАТУ НАСТУПНОГО ПЛАТЕЖУ / продовження / поповнення /
закінчення (страховка, підписка, кредит, оренда, комунальні, телефон, карта,
ліміт, ТО авто). Шукай її навіть у листах-підтвердженнях ВЖЕ здійсненої оплати
("наступний платіж", "splatnosť ďalšej splátky", "next payment", "renews on",
"platnosť do", "expires", "valid until"). Для таких постав kind="next_payment"
(регулярний платіж) або kind="renewal" (продовження/закінчення). Сам факт
минулої оплати подією не є. Дату в минулому не повертай.

СЬОГОДНІ: {today}

ЖОРСТКІ ПРАВИЛА:
- Дату бери ЛИШЕ ту, що явно вказана в листі. Не вгадуй, не округлюй.
- Немає дати — лист пропусти. Порожній список краще за вигадану подію.
- Реклама, розсилки, «до кінця тижня знижка» — це НЕ подія.
- Час указуй лише якщо він у листі. Інакше time = null.
- title — коротко й зрозуміло Олегу, українською, до 60 символів.

ЛИСТИ:
{emails}

Відповідь ТІЛЬКИ JSON-масив, без markdown:
[{{"uid": "uid листа", "title": "...", "date": "YYYY-MM-DD", "time": "HH:MM або null",
   "kind": "flight|payment|meeting|appointment|deadline|delivery|cancel|next_payment|renewal|other",
   "why": "чому це подія — 1 короткий рядок з листа"}}]
Якщо подій немає — [].
"""


def _ask(emails):
    blocks = []
    for e in emails:
        blocks.append(
            f"--- uid={e['uid']}\nВід: {e['sender']}\nТема: {e['subject']}\n"
            f"{e['body'][:2400]}"
        )
    prompt = _PROMPT.format(today=K.today_str(), emails="\n\n".join(blocks))
    out = K.gemini_json(prompt, max_tokens=1200, temperature=0.2,
                        tag="mailcal_extract", want="list")
    return out if isinstance(out, list) else []


# ─── КАЛЕНДАР ────────────────────────────────────────────────────────────────

def _calendar_delete(event_id: str) -> bool:
    """Видаляє подію з календаря Олега. True якщо вдалось."""
    if not event_id:
        return False
    try:
        import context as _ctx
        token = _ctx._get_token()
        if not token:
            return False
        url = (f"https://www.googleapis.com/calendar/v3/calendars/"
               f"{_ctx._CAL_ID}/events/{event_id}")
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        try:
            _ctx._CAL_CACHE.clear()
        except Exception:
            pass
        return True
    except Exception as e:
        K.log(TAG, f"calendar_delete({event_id}) error: {e}")
        return False


# ─── ДОПОМІЖНЕ ───────────────────────────────────────────────────────────────

def _norm_time(t):
    t = str(t or "").strip()
    if not t or t.lower() in ("null", "none", "-"):
        return ""
    m = re.match(r"^(\d{1,2}):(\d{2})", t)
    if not m:
        return ""
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return ""
    return f"{h:02d}:{mi:02d}"


def _sane_date(d):
    """Дата в межах розумного горизонту → 'YYYY-MM-DD', інакше ''."""
    d = str(d or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return ""
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
    except Exception:
        return ""
    today = K.now().replace(tzinfo=None)
    if dt.date() < (today - timedelta(days=1)).date():
        return ""      # минуле в календар не пишемо
    if dt.date() > (today + timedelta(days=HORIZON_DAYS)).date():
        return ""      # AI помилився з роком
    return d


def _key(uid, title, date):
    slug = re.sub(r"[^\w]+", "", str(title).lower())[:24]
    return f"{uid}_{date}_{slug}"


def items():
    return K.load(ITEMS_FILE, default={}) or {}


# ─── ГОЛОВНЕ ─────────────────────────────────────────────────────────────────

def run(force=False):
    """Скан листів → створення подій → картка постфактум. Повертає к-ть подій."""
    if not force and not K.rate_ok(SCAN_STATE, SCAN_GAP_MIN):
        return 0

    mails = _emails()
    if mails is None:
        K.log(TAG, "пошта недоступна — пропускаю скан")
        return 0
    if not mails:
        K.rate_mark(SCAN_STATE)
        return 0

    known = items()
    fresh = [m for m in mails
             if not any(k.startswith(f"{m['uid']}_") for k in known)]
    if not fresh:
        K.rate_mark(SCAN_STATE)
        return 0

    K.rate_mark(SCAN_STATE)
    found = _ask(fresh)
    if not found:
        # Позначаємо листи як проглянуті — щоб не питати AI про них знову
        for m in fresh:
            K.update_key(ITEMS_FILE, f"{m['uid']}_none",
                         {"uid": m["uid"], "empty": True, "at": K.today_str()})
        K.log(TAG, f"подій не знайдено у {len(fresh)} листах")
        return 0

    by_uid = {m["uid"]: m for m in fresh}
    created = []
    proposals = []
    covered = _bill_covered_uids()

    for ev in found:
        if len(created) >= MAX_EVENTS_PER_RUN:
            break
        if not isinstance(ev, dict):
            continue
        uid = str(ev.get("uid") or "")
        title = str(ev.get("title") or "").strip()[:80]
        date = _sane_date(ev.get("date"))
        if not (uid and title and date):
            continue
        key = _key(uid, title, date)
        if key in known:
            continue

        tm = _norm_time(ev.get("time"))
        kind = str(ev.get("kind") or "other")
        if kind not in KINDS:
            kind = "other"
        why = str(ev.get("why") or "").strip()[:200]
        src = by_uid.get(uid) or {}

        if kind in PROPOSE_KINDS:
            # Платежі/продовження: не пишемо самі, а ПРОПОНУЄМО.
            if uid in covered or len(proposals) >= MAX_PROPOSALS_PER_RUN:
                continue    # для цього листа вже є картка рахунку з кнопкою календаря
            rec = {
                "uid": uid, "title": title, "date": date, "time": tm,
                "kind": kind, "why": why,
                "subject": src.get("subject", ""), "sender": src.get("sender", ""),
                "created_at": K.now().isoformat(), "state": "proposed",
            }
            K.update_key(ITEMS_FILE, key, rec)
            known[key] = rec
            proposals.append((key, rec))
            continue

        start = K.parse_dt(date, tm or "09:00")
        if not start:
            continue
        desc = (f"Автоматично з листа.\nВід: {src.get('sender','')}\n"
                f"Тема: {src.get('subject','')}\n{why}")
        res = K.calendar_event(f"{KINDS[kind]} {title}", start,
                              end_dt=start + timedelta(hours=1),
                              description=desc)
        if not (isinstance(res, dict) and res.get("ok")):
            err = (res or {}).get("error", "?") if isinstance(res, dict) else "?"
            K.log(TAG, f"календар відмовив: {title} {date} → {err}")
            continue

        rec = {
            "uid": uid, "title": title, "date": date, "time": tm,
            "kind": kind, "why": why,
            "subject": src.get("subject", ""), "sender": src.get("sender", ""),
            "event_id": res.get("event_id", ""),
            "link": res.get("link", ""),
            "created_at": K.now().isoformat(),
            "state": "live",
        }
        K.update_key(ITEMS_FILE, key, rec)
        known[key] = rec
        created.append((key, rec))
        K.log(TAG, f"створено подію: {title} {date} {tm}")

    # Листи, що дали лише пропозиції, теж «проглянуті» (ключі uid_* вже записані).
    for key, rec in proposals:
        _propose_card(key, rec)
    if not created:
        return len(proposals)

    _card(created)
    return len(created) + len(proposals)


def _bill_covered_uids() -> set:
    """uid листів, для яких bills_watcher уже має неоплачений рахунок з дедлайном
    (там своя картка з кнопкою календаря) — щоб не дублювати пропозицію."""
    try:
        bills = K.load("bills.json", default={}) or {}
    except Exception:
        return set()
    return {str(b.get("uid")) for b in bills.values()
            if isinstance(b, dict) and b.get("uid") and b.get("due") and not b.get("paid")}


def _shift(date: str, days: int) -> str:
    try:
        return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _propose_card(key, rec):
    """Картка-пропозиція: Олег сам вирішує, чи ставити нагадування."""
    dkey = "prop_" + key[:80]
    if _dedup.seen(dkey):
        return False
    kind = rec.get("kind")
    head = "💳 <b>Наступний платіж</b>" if kind in ("next_payment", "payment") else \
           "🔁 <b>Дата продовження</b>" if kind == "renewal" else "⏳ <b>Важлива дата</b>"
    when = rec["date"] + (f" о {rec['time']}" if rec.get("time") else "")
    lines = [f"{head} — у листі є дата.", "",
             f"{KINDS.get(kind, '📌')} <b>{K.esc(rec['title'])}</b>",
             f"   🗓 {when}"]
    if rec.get("why"):
        lines.append(f"   <i>{K.esc(rec['why'])}</i>")
    if rec.get("subject"):
        lines.append(f"   📧 {K.esc(rec['subject'][:60])}")
    lines += ["", "Поставити нагадування в календар?"]
    pid = _store.put({"key": key})
    kb = [[{"text": f"📅 Нагадати {rec['date']}", "callback_data": f"mc_pyes_{pid}"}]]
    early = _shift(rec["date"], 2)
    if early and _sane_date(early) and early != rec["date"] and early >= K.today_str():
        kb.append([{"text": f"📅 За 2 дні ({early})", "callback_data": f"mc_pyes2_{pid}"}])
    kb.append([{"text": "⏭ Не треба", "callback_data": f"mc_pno_{pid}"}])
    if K.send_card("\n".join(lines), kb, tag=TAG):
        _dedup.mark(dkey)
        return True
    _store.drop(pid)
    return False


def _accept_proposal(pid: str, days_before: int) -> dict:
    payload = _store.get(pid) or {}
    key = payload.get("key", "")
    rec = items().get(key)
    if not rec:
        _store.drop(pid)
        return {"text": "⚠️ Ця пропозиція вже застаріла.", "alert": True, "keyboard": []}
    if rec.get("state") != "proposed":
        _store.drop(pid)
        return {"text": "ℹ️ Це вже опрацьовано.", "alert": True, "keyboard": []}
    date = _shift(rec["date"], days_before) if days_before else rec["date"]
    if not _sane_date(date):
        return {"text": "⚠️ Ця дата вже минула — не ставлю.", "alert": True, "keyboard": []}
    tm = rec.get("time") or "09:00"
    start = K.parse_dt(date, tm)
    res = K.calendar_event(f"{KINDS.get(rec.get('kind'), '📌')} {rec['title']}", start,
                           end_dt=start + timedelta(hours=1),
                           description=(f"З листа (за твоїм підтвердженням).\n"
                                        f"Дата в листі: {rec['date']}\n"
                                        f"Від: {rec.get('sender','')}\n"
                                        f"Тема: {rec.get('subject','')}\n{rec.get('why','')}"))
    if not (isinstance(res, dict) and res.get("ok")):
        err = (res or {}).get("error", "?") if isinstance(res, dict) else "?"
        return {"text": f"⚠️ Календар відмовив: {err}. Спробуй ще раз.", "alert": True,
                "keyboard": None}
    rec.update({"state": "live", "event_id": res.get("event_id", ""),
                "link": res.get("link", ""), "reminder_date": date,
                "accepted_at": K.now().isoformat()})
    K.update_key(ITEMS_FILE, key, rec)
    _store.drop(pid)
    return {"text": f"✅ Нагадування в календарі: {rec['title']} — {date}",
            "alert": False, "keyboard": []}


def _card(created):
    """Одна картка на всі щойно створені події."""
    lines = ["📅 <b>Додав у твій календар</b> (з листів):", ""]
    kb = []
    for key, rec in created:
        when = rec["date"] + (f" о {rec['time']}" if rec["time"] else " (весь день)")
        lines.append(f"{KINDS[rec['kind']]} <b>{K.esc(rec['title'])}</b>")
        lines.append(f"   🗓 {when}")
        if rec["why"]:
            lines.append(f"   <i>{K.esc(rec['why'])}</i>")
        subj = rec.get("subject", "")
        if subj:
            lines.append(f"   📧 {K.esc(subj[:60])}")
        lines.append("")
        token = _store.put({"key": key})
        kb.append([{"text": f"🗑 Прибрати «{rec['title'][:18]}»",
                    "callback_data": f"mc_del_{token}"}])

    if len(created) == 1:
        first = created[0][0]
        kb.append([{"text": "✅ Все правильно",
                    "callback_data": f"mc_ok_{_store.put({'key': first})}"}])
    else:
        kb.append([{"text": "✅ Все правильно",
                    "callback_data": f"mc_ok_{_store.put({'key': 'all'})}"}])

    dkey = "card_" + "_".join(k for k, _ in created)[:80]
    if _dedup.seen(dkey):
        return
    if K.send_card("\n".join(lines).strip(), kb, tag=TAG):
        _dedup.mark(dkey)


# ─── КНОПКИ ──────────────────────────────────────────────────────────────────

def _label(rec):
    tm = f" о {rec.get('time')}" if rec.get("time") else ""
    return f"{rec.get('title','')} — {rec.get('date','')}{tm}"


def handle(data: str, cb=None) -> dict:
    """Обробка mc_* callback.

    Повертає {"text": попап, "alert": bool, "keyboard": нова клавіатура|None}.
    Видалення — у ДВА кроки: 🗑 лише перепитує (миттєвий попап + кнопки
    Так/Ні), сама подія зникає з календаря тільки після «Так».
    """
    if data.startswith("mc_pyes2_"):
        return _accept_proposal(data[len("mc_pyes2_"):], 2)
    if data.startswith("mc_pyes_"):
        return _accept_proposal(data[len("mc_pyes_"):], 0)
    if data.startswith("mc_pno_"):
        pid = data[len("mc_pno_"):]
        payload = _store.get(pid) or {}
        rec = items().get(payload.get("key", ""))
        if rec and rec.get("state") == "proposed":
            rec["state"] = "declined"
            K.update_key(ITEMS_FILE, payload["key"], rec)
        _store.drop(pid)
        return {"text": "Ок, без нагадування.", "alert": False, "keyboard": []}

    if data.startswith("mc_ok_"):
        _store.drop(data[len("mc_ok_"):])
        return {"text": "✅ Добре, лишаю в календарі.", "alert": False,
                "keyboard": []}

    # ── Крок 1: натиснуто 🗑 → тільки питаємо, нічого не видаляємо ──────────
    if data.startswith("mc_del_"):
        pid = data[len("mc_del_"):]
        payload = _store.get(pid) or {}
        rec = items().get(payload.get("key", ""))
        if not rec:
            _store.drop(pid)
            return {"text": "⚠️ Не знайшов цю подію — можливо, вже прибрана.",
                    "alert": True, "keyboard": []}
        if rec.get("state") != "live":
            _store.drop(pid)
            return {"text": f"ℹ️ Цю подію вже прибрано: {rec.get('title','')}",
                    "alert": True, "keyboard": []}
        return {
            "text": (f"🗑 Прибрати з календаря?\n\n{_label(rec)}\n\n"
                     f"Подія зараз У КАЛЕНДАРІ. Натисни «Так, прибрати» "
                     f"під повідомленням — і я її видалю."),
            "alert": True,
            "keyboard": [[
                {"text": "✅ Так, прибрати", "callback_data": f"mc_yes_{pid}"},
                {"text": "↩️ Ні, лишити", "callback_data": f"mc_no_{pid}"},
            ]],
        }

    # ── Крок 2: підтверджено → видаляємо ───────────────────────────────────
    if data.startswith("mc_yes_"):
        pid = data[len("mc_yes_"):]
        payload = _store.get(pid) or {}
        _store.drop(pid)
        key = payload.get("key", "")
        rec = items().get(key)
        if not rec:
            return {"text": "⚠️ Не знайшов цю подію — можливо, вже прибрана.",
                    "alert": True, "keyboard": []}
        ok = _calendar_delete(rec.get("event_id", ""))
        rec["state"] = "deleted" if ok else "delete_failed"
        rec["deleted_at"] = K.now().isoformat()
        K.update_key(ITEMS_FILE, key, rec)
        if ok:
            return {"text": f"🗑 Прибрав з календаря:\n{_label(rec)}",
                    "alert": True, "keyboard": []}
        return {"text": ("⚠️ Не вдалось видалити з календаря — прибери вручну:\n"
                         f"{_label(rec)}"), "alert": True, "keyboard": []}

    if data.startswith("mc_no_"):
        _store.drop(data[len("mc_no_"):])
        return {"text": "↩️ Ок, лишаю подію в календарі.", "alert": True,
                "keyboard": []}

    return {"text": "", "alert": False, "keyboard": None}


# ─── ЗВІТ ────────────────────────────────────────────────────────────────────

def report() -> str:
    """Що бот сам поставив у календар — для команди /листкал."""
    recs = [r for r in items().values()
            if isinstance(r, dict) and not r.get("empty")]
    if not recs:
        return ("📅 Я ще нічого не створював у календарі з листів.\n"
                "Як тільки в листі буде конкретна дата — поставлю сам і напишу тобі.")

    live = [r for r in recs if r.get("state") == "live"]
    gone = [r for r in recs if r.get("state") in ("deleted", "delete_failed")]
    live.sort(key=lambda r: r.get("date", ""))

    out = ["📅 <b>Події з листів, які я створив сам</b>", ""]
    today = K.today_str()
    future = [r for r in live if r.get("date", "") >= today]
    past = [r for r in live if r.get("date", "") < today]

    if future:
        out.append("<b>Попереду:</b>")
        for r in future[:15]:
            tm = f" о {r['time']}" if r.get("time") else ""
            out.append(f"{KINDS.get(r.get('kind','other'),'📌')} "
                       f"{K.esc(r.get('title',''))} — {r.get('date','')}{tm}")
        out.append("")
    if past:
        out.append(f"Уже минуло: {len(past)}")
    if gone:
        out.append(f"Прибрано тобою: {len(gone)}")
    return "\n".join(out).strip()
