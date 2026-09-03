#!/usr/bin/env python3
"""
selfact.py — AI-ІНІЦІАТОР + ЖУРНАЛ ВЛАСНИХ ДІЙ.

Що робить:
  1. context()  — збирає ПОВНУ картину: календар (7 днів), свіжі листи з тілами,
                  усі реєстри (рахунки, підписки, витрати, здоров'я, нагадування,
                  нотатки, звички, дати, дедлайни, петлі, реакції).
  2. decide()   — AI САМ вирішує, що зробити: нотатка / нагадування / подія /
                  сповіщення / питання. Тільки на основі даних із context().
  3. виконавці  — реально СТВОРЮЮТЬ: нотатку (ai_notes), нагадування
                  (reminders.json), подію (Google Calendar), сповіщення/питання
                  (Telegram з кнопками react).
  4. journal()  — публічний хелпер: будь-який модуль пише сюди свою дію
                  → bot_actions.json.
  5. digest()   — щоденний міні-звіт 21:30: що бот СТВОРИВ, ЗАПИСАВ, ЗАПИТАВ.

Правила:
  - немає даних → жодної дії і жодного слова (нулі не малюємо);
  - максимум 2 дії за прохід, rate-limit 90 хв;
  - закрите через react / заглушене через dismissed — не повторюємо;
  - вигадувати факти заборонено (прямо в промпті).
"""
import os
import sys
import json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K

TAG = "selfact"

SCAN_FILE = "selfact_scan.json"
DEDUP_FILE = "selfact_dedup.json"
JOURNAL_FILE = "bot_actions.json"
DIGEST_FILE = "selfact_digest.json"
REMINDERS_FILE = "reminders.json"

SCAN_GAP_MIN = 15          # ПОВНА ВОЛЯ: дивиться на життя Олега раз на 15 хв (28.08: Олег хоче більше ініціативи)
MAX_ACTIONS = 10           # максимум дій за один прохід (28.08: більше ініціативи)
JOURNAL_KEEP = 400         # скільки записів журналу тримаємо
CAL_DAYS = 7               # горизонт календаря
MAIL_LIMIT = 15            # скільки свіжих листів даємо AI (усі важливі)
BODY_CHARS = 700           # скільки символів тіла листа
DIGEST_HOUR = 21
DIGEST_MIN = 30

_dedup = K.Dedup(DEDUP_FILE, ttl_days=5)

ACTION_TYPES = ("note", "reminder", "event", "notify", "ask")

_KIND_UA = {
    "note": "нотатка",
    "reminder": "нагадування",
    "event": "подія в календарі",
    "notify": "сповіщення",
    "ask": "питання",
}


# ─────────────────────────── допоміжне ───────────────────────────

def _log(msg):
    K.log(TAG, msg)


def _muted(title: str) -> bool:
    """Чи Олег уже закрив/заглушив цю тему."""
    try:
        import dismissed
        if dismissed.is_muted(title=title):
            return True
    except Exception:
        pass
    try:
        import react
        if react.is_closed(title=title):
            return True
    except Exception:
        pass
    return False


def _short(val, limit=260) -> str:
    s = str(val or "").strip()
    s = " ".join(s.split())
    return s[:limit]


def _reg(name: str, limit: int = 1200) -> str:
    """Реєстр із гілки data у вигляді компактного рядка. '' якщо порожній."""
    try:
        data = K.load(name, default=None)
    except Exception as e:
        _log(f"{name}: {e}")
        return ""
    if not data:
        return ""
    try:
        txt = json.dumps(data, ensure_ascii=False)
    except Exception:
        txt = str(data)
    if len(txt) <= limit:
        return txt
    return txt[:limit] + "…(обрізано)"


# ─────────────────────────── 1. КОНТЕКСТ ───────────────────────────

def _calendar_block() -> str:
    lines = []
    for off in range(CAL_DAYS):
        day = K.now() + timedelta(days=off)
        try:
            evs = K.events_for_day(off) or []
        except Exception:
            evs = []
        if not evs:
            continue
        names = []
        for ev in evs[:6]:
            s = _short(ev.get("summary"), 70)
            t = ""
            start = ev.get("start") or {}
            if isinstance(start, dict):
                raw = str(start.get("dateTime") or start.get("date") or "")
                if "T" in raw:
                    t = raw.split("T")[1][:5] + " "
            if s:
                names.append(t + s)
        if names:
            lines.append(day.strftime("%d.%m") + ": " + "; ".join(names))
    if not lines:
        return ""
    return "КАЛЕНДАР НА " + str(CAL_DAYS) + " ДНІВ:\n" + "\n".join(lines)


def _mail_block() -> str:
    try:
        import monitor
        raw = monitor.get_emails()
    except Exception as e:
        _log("mail: " + str(e))
        return ""
    # get_emails() віддає dict {"__email_block__","header","items"},
    # або рядок при помилці/відсутності листів.
    if isinstance(raw, dict):
        emails = raw.get("items") or []
    elif isinstance(raw, list):
        emails = raw
    else:
        emails = []
    if not isinstance(emails, list) or not emails:
        return ""
    try:
        cache = K.load("email_body_cache.json", default={}) or {}
    except Exception:
        cache = {}
    lines = []
    for em in emails[:MAIL_LIMIT]:
        if not isinstance(em, dict):
            continue
        subj = _short(em.get("subject") or em.get("тема"), 110)
        frm = _short(em.get("from") or em.get("sender"), 70)
        uid = str(em.get("uid") or em.get("id") or "")
        body = ""
        rec = cache.get(uid) if isinstance(cache, dict) else None
        if isinstance(rec, dict):
            body = _short(rec.get("body"), BODY_CHARS)
        if not body:
            body = _short(em.get("body") or em.get("snippet"), BODY_CHARS)
        piece = "- від " + frm + " | " + subj
        if body:
            piece += "\n  текст: " + body
        lines.append(piece)
    if not lines:
        return ""
    return "СВІЖІ ЛИСТИ:\n" + "\n".join(lines)


_REGISTRIES = (
    ("bills.json", "НЕОПЛАЧЕНІ РАХУНКИ", 900),
    ("subs.json", "ПІДПИСКИ", 700),
    ("money_charges.json", "СПИСАННЯ (факти з листів)", 900),
    ("health.json", "ЗДОРОВ'Я (останні дні)", 900),
    ("habits.json", "ЗВИЧКИ", 600),
    ("dates.json", "ВАЖЛИВІ ДАТИ", 600),
    ("deadlines.json", "ДЕДЛАЙНИ", 700),
    ("openloop.json", "ЗАВИСЛІ ПЕТЛІ", 600),
    ("ai_notes.json", "НОТАТКИ ПРО ОЛЕГА", 900),
    ("reactions.json", "РЕАКЦІЇ ОЛЕГА НА СПОВІЩЕННЯ", 600),
)


def _pending_reminders() -> str:
    """Ще не надіслані нагадування — щоб AI не дублював їх."""
    data = K.load(REMINDERS_FILE, default=[]) or []
    if not isinstance(data, list):
        return ""
    out = []
    for r in data:
        if not isinstance(r, dict) or r.get("sent"):
            continue
        out.append(_short(r.get("datetime_utc"), 20) + " — " + _short(r.get("text"), 90))
    if not out:
        return ""
    return "ЗАПЛАНОВАНІ НАГАДУВАННЯ (не дублюй):\n" + "\n".join(out[-12:])


def _recent_journal(hours: int = 48) -> str:
    """Що бот уже робив — щоб не робив те саме двічі."""
    recs = load_journal()
    if not recs:
        return ""
    cut = K.now().replace(tzinfo=None) - timedelta(hours=hours)
    out = []
    for r in recs[-60:]:
        try:
            ts = datetime.fromisoformat(str(r.get("ts"))).replace(tzinfo=None)
        except Exception:
            continue
        if ts < cut:
            continue
        out.append(_KIND_UA.get(r.get("kind"), str(r.get("kind"))) + ": " + _short(r.get("what"), 90))
    if not out:
        return ""
    return "ЩО БОТ УЖЕ ЗРОБИВ ЗА 48 ГОД (не повторюй):\n" + "\n".join(out[-20:])


def context() -> str:
    """Повна картина для AI. '' якщо взагалі немає даних."""
    blocks = []
    cal = _calendar_block()
    if cal:
        blocks.append(cal)
    mail = _mail_block()
    if mail:
        blocks.append(mail)
    for fname, title, limit in _REGISTRIES:
        val = _reg(fname, limit)
        if val and val not in ("{}", "[]"):
            blocks.append(title + ": " + val)
    rem = _pending_reminders()
    if rem:
        blocks.append(rem)
    jr = _recent_journal()
    if jr:
        blocks.append(jr)
    if not blocks:
        return ""
    head = "СЬОГОДНІ: " + K.now().strftime("%d.%m.%Y %H:%M") + " (Кошице)"
    return head + "\n\n" + "\n\n".join(blocks)


# ─────────────────────────── 2. РІШЕННЯ ───────────────────────────

_PROMPT = """Ти — АІ-ініціатор персонального бота Олега (Кошице, зміни на заводі).
Нижче — ВСІ реальні дані, які має бот. Твоє завдання: вирішити САМОСТІЙНО, що зробити
ПРЯМО ЗАРАЗ, без запиту Олега. Ти не відповідаєш йому — ти дієш.

ДАНІ:
{ctx}

Дозволені дії (обери СТІЛЬКИ, скільки є реальних причин, до 6; [] якщо причин немає):
- "note"     — записати факт про Олега, який видно з даних і його ще немає в нотатках
- "reminder" — нагадати про конкретну справу в конкретний час (date+time обов'язково)
- "event"    — створити подію в календарі (date+time обов'язково)
- "notify"   — сказати щось важливе, чого Олег міг не побачити
- "ask"      — поставити ОДНЕ пряме питання, відповідь на яке змінить твої дії далі

ЗАБОРОНЕНО:
- вигадувати факти, суми, дати, назви, яких немає в ДАНИХ;
- дублювати те, що вже є в нагадуваннях, нотатках або в журналі дій бота;
- дії "щоб було": порожні мотивації, "не забудь пити воду", загальні поради;
- більше ніж 6 дій.
ТОБІ ДАНА ПОВНА ВОЛЯ: не питай дозволу, не пиши "хочеш, я...". Бачиш причину — дій одразу:
створюй нагадування, події, нотатки, сповіщай, питай. Одна реальна причина = одна дія.
Якщо в даних немає жодної реальної причини діяти — поверни рівно [].

Формат — тільки JSON-масив:
[{{"type":"reminder","title":"коротка назва","text":"що саме сказати Олегу українською",
   "date":"YYYY-MM-DD","time":"HH:MM","why":"на якому саме факті з ДАНИХ це базується"}}]
Для note/notify/ask поля date/time можна не давати. У "text" — живою українською, без води."""


def decide(ctx: str = None) -> list:
    ctx = ctx if ctx is not None else context()
    if not ctx:
        _log("даних немає — рішення не запитую")
        return []
    out = K.gemini_json(_PROMPT.format(ctx=ctx[:14000]), max_tokens=1200,
                        temperature=0.6, tag=TAG, want="list")
    acts = []
    for a in out or []:
        if not isinstance(a, dict):
            continue
        typ = str(a.get("type") or "").strip().lower()
        if typ not in ACTION_TYPES:
            continue
        title = _short(a.get("title"), 120)
        text = _short(a.get("text"), 900)
        if not (title or text):
            continue
        a["type"] = typ
        a["title"] = title or text[:60]
        a["text"] = text or title
        acts.append(a)
    return acts[:MAX_ACTIONS]


# ─────────────────────────── 3. ВИКОНАВЦІ ───────────────────────────

def _kb(kind: str, title: str):
    try:
        import react
        return react.keyboard(kind, title=title, text=title, tag=kind)
    except Exception:
        return None


def _do_note(a: dict) -> bool:
    try:
        import ai_notes
        ai_notes.add_note(a["text"], source=TAG)
        return True
    except Exception as e:
        _log("note: " + str(e))
        return False


def _do_reminder(a: dict) -> bool:
    date = K.valid_future_date(str(a.get("date") or ""), allow_today=True)
    if not date:
        _log("нагадування без достовірної дати — пропускаю")
        return False
    try:
        dt = K.parse_dt(date, str(a.get("time") or "09:00"))
    except Exception as e:
        _log("parse_dt: " + str(e))
        return False
    # Нічого не пишемо без «так» Олега — питаємо кнопками (calgate)
    try:
        import calgate as _cg_r
        _blk = _cg_r.gate_write("reminder", str(a.get("title") or ""), dt,
                                str(a.get("text") or ""), source="selfact")
        if _blk is not None:
            _log("нагадування → спершу питаю Олега: " +
                 str(a.get("title") or "")[:60])
            return False
    except Exception as _e_cg:
        _log("calgate skip: " + str(_e_cg))
    data = K.load(REMINDERS_FILE, default=[]) or []
    if not isinstance(data, list):
        data = []
    rid = TAG + "_" + dt.strftime("%Y%m%d%H%M")
    for r in data:
        if isinstance(r, dict) and r.get("id") == rid:
            return False
    data.append({
        "id": rid,
        "datetime_utc": dt.replace(tzinfo=None).isoformat(timespec="seconds"),
        "text": "🤖 <b>" + K.esc(a["title"]) + "</b>\n\n" + K.esc(a["text"]),
        "sent": False,
    })
    K.save(REMINDERS_FILE, data)
    # Нагадування ЗАВЖДИ дублюємо подією в Google Calendar (вимога Олега)
    try:
        res = K.calendar_event("🔔 " + str(a["title"])[:90], dt, dt + timedelta(minutes=30),
                               str(a.get("text") or "")[:500])
        if not (res and res.get("ok")):
            _log("нагадування: календар відмовив: " + _short(res, 120))
    except Exception as e:
        _log("нагадування: календар помилка: " + str(e))
    return True


def _do_event(a: dict) -> bool:
    date = K.valid_future_date(str(a.get("date") or ""), allow_today=True)
    if not date:
        _log("подія без достовірної дати — пропускаю")
        return False
    try:
        start = K.parse_dt(date, str(a.get("time") or "09:00"))
    except Exception as e:
        _log("parse_dt: " + str(e))
        return False
    res = K.calendar_event(a["title"][:100], start, start + timedelta(hours=1),
                           a["text"][:500])
    ok = bool(res and res.get("ok", True) and not res.get("error"))
    if not ok:
        _log("календар відмовив: " + _short(res, 120))
    return ok


def _do_notify(a: dict) -> bool:
    text = "🤖 <b>" + K.esc(a["title"]) + "</b>\n\n" + K.esc(a["text"])
    return K.send_card(text, _kb("generic", a["title"]), tag=TAG)


def _do_ask(a: dict) -> bool:
    text = "❓ <b>" + K.esc(a["title"]) + "</b>\n\n" + K.esc(a["text"])
    return K.send_card(text, _kb("question", a["title"]), tag=TAG)


_DOERS = {
    "note": _do_note,
    "reminder": _do_reminder,
    "event": _do_event,
    "notify": _do_notify,
    "ask": _do_ask,
}


# ─────────────────────────── 4. ЖУРНАЛ ───────────────────────────

def load_journal() -> list:
    data = K.load(JOURNAL_FILE, default={"actions": []}) or {}
    if isinstance(data, list):
        return data
    acts = data.get("actions")
    return acts if isinstance(acts, list) else []


def journal(kind: str, what: str, detail: str = "", module: str = TAG):
    """Публічний хелпер: будь-який модуль пише сюди свою дію."""
    if not what:
        return
    recs = load_journal()
    recs.append({
        "kind": str(kind or "notify"),
        "what": _short(what, 200),
        "detail": _short(detail, 300),
        "module": str(module or TAG),
        "ts": K.now().replace(tzinfo=None).isoformat(timespec="seconds"),
    })
    K.save(JOURNAL_FILE, {"actions": recs[-JOURNAL_KEEP:]})
    _log("журнал: " + str(kind) + " — " + _short(what, 80))


def _today_actions() -> list:
    today = K.today_str()
    return [r for r in load_journal() if str(r.get("ts", "")).startswith(today)]


# ─────────────────────────── 5. МІНІ-ЗВІТ ───────────────────────────

def _counted_today() -> list:
    """Дії інших модулів, які видно з їхніх реєстрів за сьогодні."""
    today = K.today_str()
    out = []

    def _hit(fname, label, field="ts"):
        data = K.load(fname, default=None)
        n = 0
        if isinstance(data, dict):
            vals = data.values()
        elif isinstance(data, list):
            vals = data
        else:
            return
        for v in vals:
            if isinstance(v, str) and v.startswith(today):
                n += 1
            elif isinstance(v, dict):
                for key in (field, "ts", "created", "date", "datetime_utc"):
                    if str(v.get(key, "")).startswith(today):
                        n += 1
                        break
        if n:
            out.append(label + ": " + str(n))

    _hit("mailcal.json", "подій з листів створено")
    _hit("money_charges.json", "списань записано")
    _hit("money_events.json", "платежів у календарі")
    _hit("reactions.json", "твоїх реакцій")
    return out


def digest(force: bool = False) -> str:
    """Міні-звіт: що бот зробив САМ за день. '' якщо нічого і не час."""
    if not force and not K.rate_ok(DIGEST_FILE, 60 * 20):
        return ""
    acts = _today_actions()
    counted = _counted_today()

    groups = {}
    for r in acts:
        groups.setdefault(r.get("kind", "notify"), []).append(r)

    lines = ["🤖 <b>Що я зробив сам за сьогодні</b>", ""]
    if not acts and not counted:
        lines.append("Нічого не створював і не записував — реальних причин не було.")
        lines.append("Це не збій: без достовірних даних я мовчу.")
    else:
        order = ("event", "reminder", "note", "ask", "notify")
        for kind in order:
            recs = groups.get(kind)
            if not recs:
                continue
            lines.append("<b>" + _KIND_UA.get(kind, kind).capitalize() + " (" + str(len(recs)) + ")</b>")
            for r in recs[:6]:
                lines.append("• " + K.esc(_short(r.get("what"), 110)))
            lines.append("")
        for kind, recs in groups.items():
            if kind in order:
                continue
            lines.append("<b>" + K.esc(str(kind)) + " (" + str(len(recs)) + ")</b>")
            for r in recs[:4]:
                lines.append("• " + K.esc(_short(r.get("what"), 110)))
            lines.append("")
        if counted:
            lines.append("<b>Автоматика за реєстрами</b>")
            for c in counted:
                lines.append("• " + K.esc(c))
    text = "\n".join(lines).strip()
    if not force:
        K.send_card(text, _kb("generic", "звіт бота за день"), tag=TAG + "_digest")
        K.rate_mark(DIGEST_FILE)
    return text


def digest_due() -> bool:
    n = K.now()
    return n.hour == DIGEST_HOUR and n.minute >= DIGEST_MIN


# ─────────────────────────── ЗАПУСК ───────────────────────────

def run(force: bool = False) -> int:
    """Один прохід ініціатора. Повертає кількість виконаних дій."""
    try:
        if digest_due():
            digest()
    except Exception as e:
        _log("digest error: " + str(e))

    if not force and not K.rate_ok(SCAN_FILE, SCAN_GAP_MIN):
        return 0
    K.rate_mark(SCAN_FILE)

    ctx = context()
    if not ctx:
        _log("даних для ініціативи немає — мовчу")
        return 0

    acts = decide(ctx)
    if not acts:
        _log("AI не побачив реальної причини діяти")
        return 0

    done = 0
    for a in acts:
        typ = a["type"]
        title = a["title"]
        if _dedup.seen(typ, title):
            _log("вже робив: " + typ + " / " + title)
            continue
        if _muted(title):
            _log("тема закрита/заглушена: " + title)
            continue
        doer = _DOERS.get(typ)
        if not doer:
            continue
        try:
            ok = doer(a)
        except Exception as e:
            _log(typ + " error: " + str(e))
            ok = False
        if not ok:
            continue
        _dedup.mark(typ, title)
        journal(typ, title, _short(a.get("why") or a.get("text"), 300))
        done += 1
        if typ in ("note", "reminder", "event"):
            # Олег має бачити, що бот зробив це САМ
            msg = ("🤖 <b>Зробив сам: " + _KIND_UA.get(typ, typ) + "</b>\n\n"
                   + K.esc(title) + "\n\n" + K.esc(_short(a.get("text"), 400)))
            why = _short(a.get("why"), 200)
            if why:
                msg += "\n\n<i>Чому: " + K.esc(why) + "</i>"
            K.send_card(msg, _kb("generic", title), tag=TAG)
    _log("виконано дій: " + str(done))
    return done


def report() -> str:
    """Для команди /сам — що бачу і що робив, без побічних дій."""
    ctx = context()
    if not ctx:
        return "🤖 <b>Ініціатива</b>\n\nДаних немає — діяти не на чому."
    acts = decide(ctx)
    lines = ["🤖 <b>Ініціатива: що я бачу зараз</b>", ""]
    lines.append("Джерел даних у контексті: " + str(len(ctx.split("\n\n")) - 1))
    lines.append("")
    if not acts:
        lines.append("Реальної причини щось створювати зараз немає.")
    else:
        lines.append("<b>Що я б зробив:</b>")
        for a in acts:
            lines.append("• " + _KIND_UA.get(a["type"], a["type"]) + ": "
                         + K.esc(_short(a["title"], 100)))
            why = _short(a.get("why"), 180)
            if why:
                lines.append("  <i>" + K.esc(why) + "</i>")
    todays = _today_actions()
    lines.append("")
    lines.append("Дій за сьогодні: " + str(len(todays)))
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
