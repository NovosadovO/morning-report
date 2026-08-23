#!/usr/bin/env python3
"""
openloop.py — ЧОГО ОЛЕГ НЕ ЗРОБИВ + ІНІЦІАТИВА БОТА.

Олег: «слідкувати за тим, чого я НЕ зробив» і «головне щоб бот сам ініціював
щось створити, записати, запитати».

Три джерела — усі реальні, нічого не вигадується:
  1) ЛИСТИ БЕЗ ВІДПОВІДІ — лист старший NO_REPLY_DAYS, а в теці «Надіслані»
     немає нічого з цією темою або до цього адресата. Тобто ти справді не
     відповів, а не «бот так думає».
  2) ПРОСТРОЧЕНІ НАГАДУВАННЯ — reminders.json: час минув, а закриття немає
     (react.py не бачив ні «Зробив», ні «Не нагадуй»).
  3) ОБІЦЯНКИ З ТВОЇХ ЖЕ ЛИСТІВ — Gemini читає ТВОЇ надіслані листи і
     витягує лише явні зобов'язання з датою («надішлю до п'ятниці»).

Що бот РОБИТЬ САМ (без дозволу, як домовились щодо календаря):
  СТВОРЮЄ — обіцянку з датою кладе подією в Google Calendar і каже постфактум.
  ЗАПИСУЄ — кожну відкриту петлю в openloop.json з часом і джерелом.
  ПИТАЄ — надсилає прямий запит із кнопками («Відповів? Зробив? Забудь?»),
           а відповідь лягає в react.py і закриває тему назавжди.

Тиша, коли нема даних: пошта впала → модуль нічого не робить. Немає петель →
жодного повідомлення. Закрите через react/dismissed — не піднімається вдруге.

API: run(force=False) -> кількість надісланих; report(); handle(data, cb)
"""
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K  # noqa: E402

TAG = "openloop"
FILE = "openloop.json"
SENT_FILE = "openloop_sent.json"
SCAN_FILE = "openloop_scan.json"
STORE_FILE = "openloop_store.json"

NO_REPLY_DAYS = 3          # лист старший цього і без відповіді = петля
OVERDUE_HOURS = 6          # нагадування прострочене більше — питаємо
PROMISE_DAYS = 14          # обіцянки шукаємо у надісланих за 2 тижні
SCAN_GAP_MIN = 90          # не частіше, ніж раз на 1.5 год
MAX_ITEMS = 3              # максимум петель за один прохід — не завалюємо
MAX_SENT_SCAN = 40         # скільки надісланих листів читати
REASK_DAYS = 4             # про ту саму петлю не питаємо частіше

_store = K.PayloadStore(STORE_FILE)

_TAGS = re.compile(r"<[^>]+>")
_RE_PREFIX = re.compile(r"^\s*(re|fw|fwd|відповідь)\s*[:\-]\s*", re.I)
_NONWORD = re.compile(r"[^0-9a-zа-яіїєґ ]+", re.I)
_SPACES = re.compile(r"\s+")
_MAIL = re.compile(r"[\w\.\-\+]+@[\w\.\-]+")

# Адресати, від яких відповіді ніхто не чекає.
_NOREPLY = ("noreply", "no-reply", "donotreply", "notification", "notify",
            "mailer", "bounce", "newsletter", "info@", "support@", "billing@",
            "alert", "automated", "postmaster")


def _log(msg):
    print("[" + TAG + "] " + str(msg), flush=True)


def _now():
    return K.now().replace(tzinfo=None)


def _norm(title) -> str:
    t = _TAGS.sub(" ", str(title or ""))
    t = t.replace("«", " ").replace("»", " ").strip().lower()
    t = _RE_PREFIX.sub("", t)
    t = _NONWORD.sub(" ", t)
    return _SPACES.sub(" ", t).strip()[:60]


def _dt(val):
    try:
        return datetime.fromisoformat(str(val)[:19].replace(" ", "T"))
    except Exception:
        return None


def _closed(kind: str, key: str = "", title: str = "") -> bool:
    """Тема вже закрита реакцією або блок-листом → не турбуємо."""
    try:
        import react as R
        if R.is_closed(kind, key=key or None, title=title or None):
            return True
    except Exception:
        pass
    try:
        import dismissed as D
        if D.is_muted(kind, key=key or None, title=title or None):
            return True
    except Exception:
        pass
    return False


# ─── 1. НАДІСЛАНІ ЛИСТИ: НА ЩО ТИ ВЖЕ ВІДПОВІВ ───────────────────────────────

def _sent_mail(days=PROMISE_DAYS, limit=MAX_SENT_SCAN):
    """Твої надіслані листи: [{to, subject, body, date}] | None якщо IMAP впав."""
    try:
        import email as _em
        import monitor as _m
        mail = _m._imap_connect()
    except Exception as e:
        _log("IMAP недоступний: " + str(e))
        return None
    out = []
    try:
        # Теку «Надіслані» шукаємо за IMAP-флагом \Sent, а не за назвою:
        # у Gmail вона локалізована («[Gmail]/Надіслані»), тому жорсткі
        # назви не працювали — прод це й показав.
        boxes = []
        try:
            typ, lst = mail.list()
            if typ == "OK":
                for raw in (lst or []):
                    line = raw.decode(errors="replace") if isinstance(
                        raw, bytes) else str(raw)
                    if "\\Sent" in line:
                        name = line.split(' "/" ')[-1].strip()
                        if not name.startswith('"'):
                            name = '"' + name + '"'
                        boxes.append(name)
        except Exception as e:
            _log("IMAP LIST: " + str(e))
        boxes += ['"[Gmail]/Sent Mail"', '"[Gmail]/Надіслані"', "Sent"]
        ok = False
        for box in boxes:
            try:
                typ, _ = mail.select(box, readonly=True)
                if typ == "OK":
                    _log("тека надісланих: " + box)
                    ok = True
                    break
            except Exception:
                continue
        if not ok:
            _log("теку «Надіслані» не знайдено — пропускаю")
            return None
        since = (_now() - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, res = mail.uid("search", None, "SINCE " + since)
        uids = res[0].split() if (typ == "OK" and res and res[0]) else []
        for uid in reversed(uids[-limit:]):
            try:
                typ, data = mail.uid("fetch", uid, "(RFC822)")
                if typ != "OK" or not data or not data[0]:
                    continue
                msg = _em.message_from_bytes(data[0][1])
                subj = _m._imap_decode_header(msg.get("Subject", ""))
                to = _m._imap_decode_header(msg.get("To", ""))
                body = ""
                try:
                    body = _m._imap_get_body(msg) or ""
                except Exception:
                    body = ""
                out.append({"to": to, "subject": subj,
                            "body": body[:1500],
                            "date": str(msg.get("Date", ""))})
            except Exception:
                continue
    except Exception as e:
        _log("читання надісланих: " + str(e))
        return None
    finally:
        try:
            mail.logout()
        except Exception:
            pass
    return out


def _answered_sets(sent):
    """З надісланих: множини нормалізованих тем і адресатів."""
    subs, addrs = set(), set()
    for s in sent or []:
        n = _norm(s.get("subject"))
        if n:
            subs.add(n)
        for a in _MAIL.findall(str(s.get("to") or "")):
            addrs.add(a.lower())
    return subs, addrs


# ─── 2. ЛИСТИ БЕЗ ВІДПОВІДІ ──────────────────────────────────────────────────

def _waiting(sent):
    """Листи, що справді чекають на відповідь."""
    try:
        import mailcal as MC
        items = MC._emails(limit=25)
    except Exception as e:
        _log("листи недоступні: " + str(e))
        return None
    if items is None:
        return None
    subs, addrs = _answered_sets(sent)
    bodies = K.load("email_body_cache.json", default={}) or {}
    out = []
    for e in items:
        uid = str(e.get("uid") or "")
        sender = str(e.get("sender") or "")
        subject = str(e.get("subject") or "")
        low = (sender + " " + subject).lower()
        if any(w in low for w in _NOREPLY):
            continue
        n = _norm(subject)
        if n and n in subs:
            continue                      # тему вже згадував у надісланих
        addr = ""
        m = _MAIL.findall(sender)
        if m:
            addr = m[0].lower()
        if addr and addr in addrs:
            continue                      # цій людині вже писав
        got = (bodies.get(uid) or {}).get("date") or \
            (bodies.get(uid) or {}).get("ts")
        d = _dt(got)
        if d is None:
            continue                      # без дати не вигадуємо давність
        age = (_now() - d).days
        if age < NO_REPLY_DAYS:
            continue
        if _closed("email", uid, subject):
            continue
        out.append({"src": "email", "key": uid, "title": subject,
                    "who": sender, "age": age})
    return out


# ─── 3. ПРОСТРОЧЕНІ НАГАДУВАННЯ ──────────────────────────────────────────────

def _overdue():
    """reminders.json: час минув, закриття немає."""
    rem = K.load("reminders.json", default=[]) or []
    if isinstance(rem, dict):
        rem = list(rem.values())
    out = []
    for r in rem:
        if not isinstance(r, dict):
            continue
        if r.get("repeat"):
            continue                       # щоденні не рахуємо простроченими
        d = _dt(r.get("datetime_utc") or r.get("when"))
        if d is None:
            continue
        hours = (_now() - d).total_seconds() / 3600.0
        if hours < OVERDUE_HOURS or hours > 24 * 30:
            continue
        rid = str(r.get("id") or "")
        txt = _TAGS.sub(" ", str(r.get("text") or "")).strip()
        txt = _SPACES.sub(" ", txt)[:90]
        if not txt:
            continue
        if _closed("task", rid, txt):
            continue
        out.append({"src": "reminder", "key": rid, "title": txt,
                    "who": "", "age": int(hours // 24)})
    return out


# ─── 4. ОБІЦЯНКИ З ТВОЇХ ЛИСТІВ ──────────────────────────────────────────────

_PROMPT = """Ти читаєш листи, які Олег НАПИСАВ САМ.
Знайди його ЯВНІ обіцянки зробити щось — тільки ті, де він прямо пише, що
щось надішле, зробить, сплатить, підготує, зателефонує.

СУВОРО:
- лише явні зобов'язання Олега, не чужі і не припущення;
- date — тільки якщо в листі є конкретний день; інакше null;
- нічого не вигадувати; сумнівне — пропускати.

Формат — JSON-масив (максимум 4):
[{"what":"надіслати документи Янці","to":"кому","date":"2026-08-27"}]
Немає обіцянок — поверни [].

ЛИСТИ:
"""


def _promises(sent):
    if not sent:
        return []
    blob = []
    for s in sent[:12]:
        blob.append("— Кому: " + str(s.get("to") or "")[:60] +
                    " | Тема: " + str(s.get("subject") or "")[:80] +
                    "\n" + str(s.get("body") or "")[:700])
    try:
        res = K.gemini_json(_PROMPT + "\n\n".join(blob),
                            tag="openloop_promises", want="list")
    except Exception as e:
        _log("Gemini: " + str(e))
        return []
    out = []
    for p in (res or [])[:4]:
        if not isinstance(p, dict):
            continue
        what = str(p.get("what") or "").strip()
        if len(what) < 5:
            continue
        date = str(p.get("date") or "").strip()
        if date and not K.valid_future_date(date):
            date = ""
        if _closed("task", "", what):
            continue
        out.append({"src": "promise", "key": "", "title": what[:110],
                    "who": str(p.get("to") or "")[:60], "date": date,
                    "age": 0})
    return out


# ─── ІНІЦІАТИВА: СТВОРИТИ / ЗАПИСАТИ / ЗАПИТАТИ ──────────────────────────────

_HEAD = {
    "email": "📬 Лист без відповіді",
    "reminder": "⏳ Нагадування висить",
    "promise": "🤝 Ти обіцяв",
}
_KIND = {"email": "email", "reminder": "task", "promise": "task"}


def _text(item) -> str:
    src = item.get("src")
    head = _HEAD.get(src, "🔎 Незакрита справа")
    lines = ["<b>" + head + "</b>", ""]
    lines.append("« " + K.esc(str(item.get("title"))[:110]) + " »")
    if item.get("who"):
        lines.append("👤 " + K.esc(str(item["who"])[:60]))
    if src == "email":
        lines.append("⏱ лежить " + str(item.get("age")) + " дн. — відповіді "
                     "від тебе в надісланих немає")
        lines.append("")
        lines.append("Відповів десь інде — скажи, і я закрию тему.")
    elif src == "reminder":
        n = item.get("age") or 0
        lines.append("⏱ час минув" + (" " + str(n) + " дн. тому" if n else
                                      " сьогодні") + ", закриття не бачив")
        lines.append("")
        lines.append("Зробив — тисни, і я перестану питати.")
    else:
        if item.get("date"):
            lines.append("📅 обіцяна дата: " + str(item["date"]))
        lines.append("")
        lines.append("Взяв це з твого ж листа. Не актуально — закрий.")
    return "\n".join(lines)


def _create_event(item) -> str:
    """Ініціатива: обіцянка з датою → подія в календарі. Повертає опис або ''."""
    date = str(item.get("date") or "")
    if not date:
        return ""
    title = "🤝 " + str(item.get("title"))[:80]
    desc = ("Твоя обіцянка з листа" +
            (" до " + str(item.get("who")) if item.get("who") else "") +
            ". Створено ботом автоматично.")
    try:
        start = datetime.strptime(date, "%Y-%m-%d").replace(hour=18)
    except Exception:
        return ""
    try:
        res = K.calendar_event(title, start,
                              end_dt=start + timedelta(hours=1),
                              description=desc)
    except Exception as e:
        _log("подія не створена: " + str(e))
        return ""
    if not (isinstance(res, dict) and res.get("ok")):
        err = (res or {}).get("error", "?") if isinstance(res, dict) else "?"
        _log("календар відмовив: " + title + " → " + str(err))
        return ""
    _log("створено подію: " + title + " " + date)
    return title + " → " + date


def run(force: bool = False) -> int:
    """Шукає незакриті справи, записує їх і питає Олега. Повертає надіслане."""
    if not force and not K.rate_ok(SCAN_FILE, SCAN_GAP_MIN):
        return 0
    K.rate_mark(SCAN_FILE)

    sent = _sent_mail()
    if sent is None:
        _log("надіслані недоступні — про листи без відповіді мовчу")
    items = []
    if sent is not None:
        w = _waiting(sent)
        if w:
            items += w
        items += _promises(sent)
    items += _overdue()

    if not items:
        _log("незакритих справ не знайдено")
        return 0

    log = K.load(FILE, default={}) or {}
    dedup = K.load(SENT_FILE, default={}) or {}
    n = 0
    for item in items:
        if n >= MAX_ITEMS:
            break
        rk = str(item.get("src")) + "|" + (str(item.get("key")) or
                                           _norm(item.get("title")))
        prev = _dt((dedup.get(rk) or {}).get("ts"))
        if prev and (_now() - prev).days < REASK_DAYS:
            continue

        # ЗАПИСУЄ
        rec = dict(item)
        rec["ts"] = _now().isoformat(timespec="seconds")
        log[rk] = rec
        K.update_key(FILE, rk, rec)

        # СТВОРЮЄ (лише обіцянки з датою)
        created = _create_event(item) if item.get("src") == "promise" else ""

        # ПИТАЄ
        txt = _text(item)
        if created:
            txt += "\n\n📅 Я вже поставив це в календар: " + K.esc(created)
        kind = _KIND.get(item.get("src"), "task")
        try:
            import react as R
            kb = R.keyboard(kind, key=str(item.get("key") or ""),
                            title=str(item.get("title") or ""))
        except Exception:
            kb = None
        if K.send_card(txt, kb, tag="openloop"):
            dedup[rk] = {"ts": _now().isoformat(timespec="seconds")}
            K.update_key(SENT_FILE, rk, dedup[rk])
            n += 1
    _log("надіслано петель: " + str(n))
    return n


def report() -> str:
    """Для /зависло."""
    data = K.load(FILE, default={}) or {}
    if not data:
        return ("🔎 <b>Незакриті справи</b>\n\nЖодної не знайшов. Якщо пошта "
                "чи надіслані недоступні — я про них просто мовчу, а не "
                "вигадую.")
    rows = []
    for rk, r in data.items():
        ts = _dt(r.get("ts"))
        if ts:
            rows.append((ts, rk, r))
    rows.sort(key=lambda x: x[0], reverse=True)
    out = ["🔎 <b>Незакриті справи</b> — що я побачив і про що питав", ""]
    live = 0
    for ts, rk, r in rows[:20]:
        kind = _KIND.get(r.get("src"), "task")
        done = _closed(kind, str(r.get("key") or ""), str(r.get("title") or ""))
        if not done:
            live += 1
        out.append(("✅ " if done else "🔸 ") +
                   _HEAD.get(r.get("src"), "справа").split(" ", 1)[-1] +
                   ": " + K.esc(str(r.get("title"))[:60]))
    out.append("")
    out.append("Досі відкритих: " + str(live) + " із " + str(len(rows)) +
               ". ✅ = ти вже закрив кнопкою.")
    return "\n".join(out)
