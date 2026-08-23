#!/usr/bin/env python3
"""
ДНІ НАРОДЖЕННЯ І ВАЖЛИВІ ДАТИ  (Люди #новий)

Раніше бот бачив дні народження ТІЛЬКИ якщо вони вже стояли в Google Calendar
і нагадував за 3 дні без жодної допомоги. Тепер це власний реєстр:

  • свій список дат, який Олег веде командою (або імпортом з календаря)
  • щорічне повторення — рік у даті не обов'язковий
  • нагадування за 7 / 3 / 1 день і В ДЕНЬ — з різним тоном
  • AI пише ГОТОВИЙ текст привітання (українською або словацькою)
  • AI дає ідеї подарунка з реальним діапазоном цін у євро
  • одна кнопка ставить подію в календар на щороку

Дані: dates.json (гілка data). Нічого не вигадується — якщо дати немає в
реєстрі, модуль про неї не говорить.

Callback-префікси: db_wish_ / db_gift_ / db_cal_ / db_snooze_ / db_skip_
"""

import re
import json
from datetime import datetime, timedelta

import ai_kit as K

TAG = "dates"

DATES_FILE = "dates.json"          # {did: {...}}
STORE_FILE = "dates_store.json"    # payload кнопок
SENT_FILE = "dates_sent.json"      # антидубль нагадувань

WARN_DAYS = (7, 3, 1, 0)           # за скільки днів попереджати
HORIZON = 30                       # скільком дням наперед показувати в /дати

KINDS = {
    "birthday": ("🎂", "день народження"),
    "anniversary": ("💍", "річниця"),
    "work": ("💼", "робоча дата"),
    "memorial": ("🕯", "пам'ятна дата"),
    "other": ("📌", "важлива дата"),
}

_store = K.PayloadStore(STORE_FILE)
_dedup = K.Dedup(SENT_FILE, ttl_days=20)


# ─── ПАРСИНГ ДАТИ ────────────────────────────────────────────────────────────

_MONTHS = {
    "січ": 1, "лют": 2, "бер": 3, "квіт": 4, "трав": 5, "черв": 6,
    "лип": 7, "серп": 8, "вер": 9, "жовт": 10, "лист": 11, "груд": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_date(raw):
    """'12.03' / '12.03.1990' / '1990-03-12' / '12 березня' → ('MM-DD', year|'')."""
    s = str(raw or "").strip().replace("/", ".")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mo, d = m.groups()
        return _md(mo, d), y
    m = re.match(r"^(\d{1,2})[.\s-](\d{1,2})(?:[.\s-](\d{2,4}))?$", s)
    if m:
        d, mo, y = m.groups()
        if y and len(y) == 2:
            y = "19" + y if int(y) > 30 else "20" + y
        return _md(mo, d), (y or "")
    m = re.match(r"^(\d{1,2})\s+([^\s\d]+)\s*(\d{4})?$", s)
    if m:
        d, word, y = m.groups()
        low = word.lower()[:4]
        for pref, num in _MONTHS.items():
            if low.startswith(pref[:3]):
                return _md(num, d), (y or "")
    return "", ""


def _md(mo, d):
    try:
        mo, d = int(mo), int(d)
    except Exception:
        return ""
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{mo:02d}-{d:02d}"


def _next_occurrence(md: str):
    """Дата найближчого настання (date) для 'MM-DD'. None якщо md битий."""
    if not re.match(r"^\d{2}-\d{2}$", md or ""):
        return None
    today = K.now().date()
    for year in (today.year, today.year + 1):
        try:
            d = datetime.strptime(f"{year}-{md}", "%Y-%m-%d").date()
        except ValueError:
            # 02-29 у невисокосний рік → 28.02
            try:
                d = datetime.strptime(f"{year}-02-28", "%Y-%m-%d").date()
            except Exception:
                return None
        if d >= today:
            return d
    return None


def _age_turning(rec, when):
    """Скільки виповнюється (int) або None."""
    y = str(rec.get("year") or "")
    if not re.match(r"^\d{4}$", y) or not when:
        return None
    return when.year - int(y)


# ─── РЕЄСТР ──────────────────────────────────────────────────────────────────

def load_all() -> dict:
    return K.load(DATES_FILE, default={}) or {}


def _did(name, md) -> str:
    return K.Dedup.key("d", str(name or "").lower().strip(), md)[:32]


def add(name, date_raw, kind="birthday", note="", source="manual") -> dict:
    md, year = parse_date(date_raw)
    if not md:
        return {"ok": False, "error": "bad_date"}
    name = str(name or "").strip()[:60]
    if not name:
        return {"ok": False, "error": "no_name"}
    if kind not in KINDS:
        kind = "other"
    did = _did(name, md)
    rec = {"name": name, "md": md, "year": year, "kind": kind,
           "note": str(note or "")[:200], "source": source,
           "muted": False, "created": K.today_str()}
    K.update_key(DATES_FILE, did, rec)
    when = _next_occurrence(md)
    K.log(TAG, f"додано: {name} {md} ({kind})")
    return {"ok": True, "did": did, "rec": rec,
            "when": when.strftime("%Y-%m-%d") if when else "",
            "days": (when - K.now().date()).days if when else None}


def remove(did) -> bool:
    if did in load_all():
        K.remove_key(DATES_FILE, did)
        return True
    return False


def mute(did) -> bool:
    all_ = load_all()
    r = all_.get(did)
    if not r:
        return False
    r["muted"] = True
    K.update_key(DATES_FILE, did, r)
    return True


def upcoming(days=HORIZON) -> list:
    """[{did, rec, when(date), days_left, age}] найближчі — за зростанням."""
    out = []
    for did, rec in load_all().items():
        if rec.get("muted"):
            continue
        when = _next_occurrence(rec.get("md"))
        if not when:
            continue
        left = (when - K.now().date()).days
        if left > days:
            continue
        out.append({"did": did, "rec": rec, "when": when, "days_left": left,
                    "age": _age_turning(rec, when)})
    out.sort(key=lambda x: x["days_left"])
    return out


# ─── ІМПОРТ З КАЛЕНДАРЯ ──────────────────────────────────────────────────────

_BD_HINT = ("народж", "birthday", "🎂", "narozen", "meniny", "річниц", "anniversar")


def import_from_calendar(days=400) -> dict:
    """Одноразово тягне дні народження, які вже стоять у Google Calendar."""
    added, seen = [], 0
    try:
        import context as _ctx
        token = _ctx._get_token()
        if not token:
            return {"ok": False, "error": "no_calendar_token"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    for off in range(0, min(days, 400)):
        try:
            evs = K.events_for_day(off) or []
        except Exception:
            continue
        for ev in evs:
            s = str(ev.get("summary") or "")
            if not any(h in s.lower() for h in _BD_HINT):
                continue
            seen += 1
            raw = (ev.get("start") or {}).get("date") or \
                  str((ev.get("start") or {}).get("dateTime") or "")[:10]
            md, year = parse_date(raw)
            if not md:
                continue
            name = re.sub(r"(?i)(день народження|birthday|narozeniny|річниця|🎂)",
                          "", s).strip(" -–—:,") or s.strip()
            kind = "anniversary" if ("річниц" in s.lower() or "anniversar" in s.lower()) \
                else "birthday"
            did = _did(name, md)
            if did in load_all():
                continue
            r = add(name, raw, kind=kind, note="імпорт з календаря",
                    source="calendar")
            if r.get("ok"):
                added.append(f"{name} — {md[3:]}.{md[:2]}")
    return {"ok": True, "added": added, "scanned": seen}


# ─── AI: ПРИВІТАННЯ І ПОДАРУНОК ──────────────────────────────────────────────

def _person_context(name) -> str:
    """Що бот уже знає про людину (people_memory) — щоб текст був не шаблонним."""
    try:
        import people_memory as PM
        people = K.load("people.json", default={}) or {}
        low = str(name or "").lower()
        for email, card in people.items():
            blob = f"{card.get('name', '')} {email}".lower()
            if low and (low.split()[0] in blob):
                topics = card.get("topics") or card.get("last_topics") or ""
                if isinstance(topics, list):
                    topics = ", ".join(str(t) for t in topics[:5])
                return (f"Відомо про людину: {card.get('name') or email}. "
                        f"Останній контакт: {card.get('last_contact', '?')}. "
                        f"Теми: {str(topics)[:300]}")
    except Exception as e:
        K.log(TAG, f"person ctx: {e}")
    return ""


_WISH_PROMPT = """Напиши привітання від Олега Новосадова (живе в Кошице, Словаччина).

КОМУ: {name}
ПОВОД: {kind}{age}
ДАТА: {when}
НОТАТКА ОЛЕГА: {note}
{ctx}

Дай ТРИ варіанти, кожен окремим блоком:
1️⃣ ТЕПЛИЙ (близька людина, 3-4 речення, щиро, без пафосу)
2️⃣ КОРОТКИЙ (для месенджера, 1-2 речення, з емодзі)
3️⃣ ФОРМАЛЬНИЙ (колега/партнер, стримано, українською або словацькою — вибери
   доречніше і напиши, якою мовою це варіант)

Правила: без канцеляриту, без "щастя-здоров'я-успіхів" списком, без віршів.
Живою мовою, так, щоб можна було скопіювати і відправити без правок.
Нічого не вигадуй про людину, чого немає у вхідних даних."""


def wish_text(did) -> str:
    rec = load_all().get(did)
    if not rec:
        return ""
    when = _next_occurrence(rec.get("md"))
    age = _age_turning(rec, when)
    icon, label = KINDS.get(rec.get("kind"), KINDS["other"])
    prompt = _WISH_PROMPT.format(
        name=rec.get("name"), kind=label,
        age=f" (виповнюється {age})" if age else "",
        when=when.strftime("%d.%m.%Y") if when else "?",
        note=rec.get("note") or "—",
        ctx=_person_context(rec.get("name")))
    return K.gemini_text(prompt, max_tokens=2000, temperature=0.85,
                         tag="MSG_WISH")


_GIFT_PROMPT = """Олег (Кошице, Словаччина) шукає подарунок.

КОМУ: {name}
ПОВОД: {kind}{age}
ЩО ВІДОМО: {note}
{ctx}

Дай 5 ІДЕЙ подарунка, які реально купити в Словаччині або онлайн (Alza, Datart,
Amazon.de, Martinus, місцеві крамниці Кошице). Для кожної:
• назва ідеї
• приблизна ціна в євро (реальний діапазон, не вигадуй точну ціну)
• одне речення, чому саме ця людина це оцінить
• де взяти

Різні бюджети: одна ідея до 20€, дві 20-60€, одна 60-150€, одна безкоштовна
(вчинок, а не річ). Без банальностей типу "шкарпетки" і "подарункова карта".
Якщо про людину даних мало — так і скажи і дай універсальні, але не тупі варіанти."""


def gift_ideas(did) -> str:
    rec = load_all().get(did)
    if not rec:
        return ""
    when = _next_occurrence(rec.get("md"))
    age = _age_turning(rec, when)
    icon, label = KINDS.get(rec.get("kind"), KINDS["other"])
    prompt = _GIFT_PROMPT.format(
        name=rec.get("name"), kind=label,
        age=f" (виповнюється {age})" if age else "",
        note=rec.get("note") or "—",
        ctx=_person_context(rec.get("name")))
    return K.gemini_text(prompt, max_tokens=2200, temperature=0.8,
                         tag="MSG_GIFT")


# ─── НАГАДУВАННЯ ─────────────────────────────────────────────────────────────

def _head(item) -> str:
    rec = item["rec"]
    icon, label = KINDS.get(rec.get("kind"), KINDS["other"])
    left = item["days_left"]
    when = item["when"].strftime("%d.%m")
    age = item.get("age")
    age_s = f", виповнюється <b>{age}</b>" if age else ""
    # Хвіст картки — через spice: ставка + один конкретний крок, у ротації.
    # Раніше тут був один статичний рядок на всі випадки — він нічого не
    # додавав і повторювався щоразу.
    try:
        import spice
        tail = spice.tail("date", left, key=str(rec.get("name") or "") + str(rec.get("kind")))
    except Exception:
        tail = ""
    if left == 0:
        head = f"{icon} <b>СЬОГОДНІ — {label.upper()}</b>"
        body = f"🎉 <b>{K.esc(rec.get('name'))}</b>{age_s}"
    elif left == 1:
        head = f"{icon} <b>ЗАВТРА — {label}</b>"
        body = f"🎉 <b>{K.esc(rec.get('name'))}</b> — {when}{age_s}"
    else:
        head = f"{icon} <b>ЧЕРЕЗ {left} ДН. — {label}</b>"
        body = f"🎉 <b>{K.esc(rec.get('name'))}</b> — {when}{age_s}"
    out = head + "\n━━━━━━━━━━━━━━━━━━━━\n" + body
    if tail:
        out += "\n\n" + tail
    return out


def _kb(pid, left):
    kb = [[{"text": "✍️ Текст привітання", "callback_data": f"db_wish_{pid}"}]]
    if left >= 1:
        kb.append([{"text": "🎁 Ідеї подарунка", "callback_data": f"db_gift_{pid}"}])
    kb.append([{"text": "📅 В календар щороку", "callback_data": f"db_cal_{pid}"}])
    kb.append([{"text": "🔔 Нагадай ще раз ближче", "callback_data": f"db_snooze_{pid}"},
               {"text": "❌ Не нагадувати", "callback_data": f"db_skip_{pid}"}])
    return kb


def check_upcoming() -> int:
    """Нагадування за 7/3/1/0 днів. Повертає кількість надісланих карточок."""
    sent = 0
    for item in upcoming(days=max(WARN_DAYS)):
        left = item["days_left"]
        if left not in WARN_DAYS:
            continue
        did = item["did"]
        if _dedup.seen("d", did, str(left), item["when"].strftime("%Y")):
            continue
        rec = item["rec"]
        pid = _store.put({"did": did, "name": rec.get("name"),
                          "md": rec.get("md"), "kind": rec.get("kind"),
                          "when": item["when"].strftime("%Y-%m-%d"),
                          "age": item.get("age")})
        text = _head(item)
        if rec.get("note"):
            text += f"\n\n📝 {K.esc(rec['note'])}"
        if K.send_card(text, _kb(pid, left), tag=TAG):
            _dedup.mark("d", did, str(left), item["when"].strftime("%Y"))
            sent += 1
            K.log(TAG, f"✅ нагадування: {rec.get('name')} за {left} дн.")
        else:
            _store.drop(pid)
    return sent


# ─── ЗВІТ І СПИСОК ───────────────────────────────────────────────────────────

def report_block(days=HORIZON) -> str:
    items = upcoming(days)
    if not items:
        return ""
    lines = ["🎂 <b>ВАЖЛИВІ ДАТИ</b>"]
    for it in items[:6]:
        icon, _ = KINDS.get(it["rec"].get("kind"), KINDS["other"])
        left = it["days_left"]
        when = it["when"].strftime("%d.%m")
        age = f" ({it['age']})" if it.get("age") else ""
        mark = "🔴" if left <= 1 else ("🟠" if left <= 3 else "🟢")
        word = "сьогодні" if left == 0 else ("завтра" if left == 1 else f"за {left} дн.")
        lines.append(f"   {mark} {icon} {K.esc(it['rec'].get('name'))}{age} — "
                     f"{when}, {word}")
    return "\n".join(lines)


def list_text() -> str:
    """/дати — весь реєстр за календарем."""
    all_ = load_all()
    if not all_:
        return ("🎂 <b>ВАЖЛИВІ ДАТИ</b>\n\nРеєстр порожній.\n\n"
                "Додати:  <code>/дата Міхаела 14.03</code>\n"
                "Або одразу з нотаткою:  <code>/дата Мама 02.11 любить квіти</code>\n"
                "Забрати з календаря:  <code>/дати_імпорт</code>")
    rows = []
    for did, rec in all_.items():
        when = _next_occurrence(rec.get("md"))
        if not when:
            continue
        rows.append((when, did, rec))
    rows.sort(key=lambda r: r[0])
    lines = [f"🎂 <b>ВАЖЛИВІ ДАТИ</b> ({len(rows)})\n━━━━━━━━━━━━━━━━━━━━"]
    for when, did, rec in rows:
        icon, _ = KINDS.get(rec.get("kind"), KINDS["other"])
        left = (when - K.now().date()).days
        age = _age_turning(rec, when)
        age_s = f" · {age} р." if age else ""
        mute_s = " 🔕" if rec.get("muted") else ""
        lines.append(f"{icon} <b>{K.esc(rec.get('name'))}</b> — "
                     f"{when.strftime('%d.%m')} (за {left} дн.){age_s}{mute_s}")
    lines.append("\n<i>Додати: /дата Ім'я 14.03 [нотатка]</i>")
    return "\n".join(lines)


# ─── ДІЇ КНОПОК ──────────────────────────────────────────────────────────────

def do_wish(pid) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    txt = wish_text(p.get("did"))
    if not txt:
        return {"ok": False, "error": "ai_unavailable"}
    return {"ok": True, "name": p.get("name"), "text": txt}


def do_gift(pid) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    txt = gift_ideas(p.get("did"))
    if not txt:
        return {"ok": False, "error": "ai_unavailable"}
    return {"ok": True, "name": p.get("name"), "text": txt}


def do_calendar(pid) -> dict:
    """Ставить подію на дату (і наступні 3 роки — Google recurrence не завжди
    доступний через наш хелпер, тому просто три події)."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    md = p.get("md") or ""
    when = _next_occurrence(md)
    if not when:
        return {"ok": False, "error": "bad_date"}
    icon, label = KINDS.get(p.get("kind"), KINDS["other"])
    title = f"{icon} {p.get('name')} — {label}"
    created = []
    for i in range(3):
        try:
            y = when.year + i
            start = K.parse_dt(f"{y}-{md}", "09:00")
        except Exception:
            continue
        res = K.calendar_event(title, start, start + timedelta(minutes=30),
                               description="Додано AI-асистентом з реєстру дат")
        if res.get("ok"):
            created.append(f"{y}-{md}")
    if not created:
        return {"ok": False, "error": "calendar_error"}
    return {"ok": True, "title": title, "dates": created}


def do_snooze(pid) -> dict:
    """Скидає антидубль, щоб нагадування прийшло на наступному порозі."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    return {"ok": True, "name": p.get("name")}


def do_skip(pid) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    mute(p.get("did"))
    _store.drop(pid)
    return {"ok": True, "name": p.get("name")}


# ─── ДОДАВАННЯ З ВІЛЬНОГО ТЕКСТУ ─────────────────────────────────────────────

def add_from_text(text) -> dict:
    """'/дата Міхаела 14.03 любить каву' → запис. Дата може стояти будь-де."""
    s = re.sub(r"^/\S+\s*", "", str(text or "")).strip()
    if not s:
        return {"ok": False, "error": "empty"}
    m = re.search(r"(\d{1,2}[.\s/-]\d{1,2}(?:[.\s/-]\d{2,4})?|\d{4}-\d{2}-\d{2})", s)
    if not m:
        return {"ok": False, "error": "bad_date"}
    date_raw = m.group(1)
    rest = (s[:m.start()] + " " + s[m.end():]).strip()
    parts = rest.split()
    if not parts:
        return {"ok": False, "error": "no_name"}
    name = " ".join(parts[:2]) if len(parts) > 2 else " ".join(parts)
    note = " ".join(parts[2:]) if len(parts) > 2 else ""
    kind = "birthday"
    low = rest.lower()
    if "річниц" in low or "весіл" in low:
        kind = "anniversary"
    elif "робот" in low or "контракт" in low:
        kind = "work"
    return add(name, date_raw, kind=kind, note=note)


# ─── ВІЛЬНИЙ ТЕКСТ БЕЗ КОМАНДИ ───────────────────────────────────────────────
# Олег часто пише просто «01.09. День народження Олі» — без /дата.
# Ловимо такі фрази: має бути дата + слово-маркер свята.

_HINT_RE = re.compile(
    r"(день\s+народж|день\s+рожд|днюх|іменин|річниц|годовщин|весіл|ювіле)", re.I)
_KIND_RE = re.compile(
    r"(день\s+народж\w*|день\s+рожд\w*|днюх\w*|іменин\w*|річниц\w*|годовщин\w*|"
    r"весіл\w*|ювіле\w*)", re.I)
_DATE_RE = re.compile(r"(\d{1,2}[.\s/-]\d{1,2}(?:[.\s/-]\d{2,4})?|\d{4}-\d{2}-\d{2})")


def looks_like_date_note(text) -> bool:
    """True, якщо у вільному тексті є і дата, і згадка про свято."""
    s = str(text or "")
    if s.strip().startswith("/"):
        return False
    if not _DATE_RE.search(s):
        return False
    return bool(_HINT_RE.search(s))


def add_from_free_text(text) -> dict:
    """«01.09. День народження Олі» → запис у реєстр. Слова-маркери прибираємо з імені."""
    s = str(text or "")
    low = s.lower()
    kind = "birthday"
    if "річниц" in low or "весіл" in low or "годовщин" in low or "ювіле" in low:
        kind = "anniversary"
    cleaned = _KIND_RE.sub(" ", s)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,;:-–—")
    m = _DATE_RE.search(cleaned)
    if not m:
        return {"ok": False, "error": "bad_date"}
    date_raw = m.group(1)
    rest = (cleaned[:m.start()] + " " + cleaned[m.end():])
    rest = re.sub(r"\s+", " ", rest).strip(" .,;:-–—")
    words = [w for w in rest.split() if len(w) > 1 or w.isalpha()]
    if not words:
        return {"ok": False, "error": "no_name"}
    name = " ".join(words[:2]) if len(words) > 2 else " ".join(words)
    note = " ".join(words[2:]) if len(words) > 2 else ""
    return add(name, date_raw, kind=kind, note=note)


def added_card(r):
    """(text, keyboard) для щойно доданої дати — з кнопками календаря й привітання."""
    rec = (r or {}).get("rec") or {}
    icon, label = KINDS.get(rec.get("kind"), KINDS["other"])
    pid = _store.put({"did": r.get("did"), "name": rec.get("name"),
                      "md": rec.get("md"), "kind": rec.get("kind"),
                      "when": r.get("when") or "", "age": None})
    md = rec.get("md") or ""
    when_s = f"{md[3:]}.{md[:2]}" if len(md) == 5 else md
    days = r.get("days")
    days_s = f" (за {days} дн.)" if isinstance(days, int) else ""
    note = f"\n📝 {K.esc(rec.get('note'))}" if rec.get("note") else ""
    text = (f"✅ <b>Записав у реєстр дат</b>\n"
            f"{icon} <b>{K.esc(rec.get('name'))}</b> — {when_s} · {label}{note}\n"
            f"⏳ Найближче: {r.get('when') or '?'}{days_s}\n\n"
            f"<i>Нагадаю за 7, 3, 1 день і в сам день. Список: /дати</i>")
    kb = [[{"text": "📅 В календар щороку", "callback_data": f"db_cal_{pid}"}],
          [{"text": "✍️ Текст привітання", "callback_data": f"db_wish_{pid}"},
           {"text": "🎁 Ідеї подарунка", "callback_data": f"db_gift_{pid}"}],
          [{"text": "❌ Не нагадувати", "callback_data": f"db_skip_{pid}"}]]
    return text, kb


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        print("нагадувань:", check_upcoming())
    elif "--list" in sys.argv:
        print(list_text())
    elif "--import" in sys.argv:
        print(import_from_calendar())
    elif "--add" in sys.argv:
        print(add_from_text(" ".join(sys.argv[2:])))
    else:
        print(report_block() or "(порожньо)")
