#!/usr/bin/env python3
"""
mailreply — «ЛЮДИНА ПИТАЄ, А ТИ НЕ ВІДПОВІВ».

Чому з'явився (24.11): Jana з Soft-Tech написала «є лише 4 з 5, відправити і
докласти щось за 1,40 €? Дякуємо за відповідь». Алерт про лист потрапив у чергу
режиму тиші й там загубився, а далі бот нагадував лише про вже оплачене
підтвердження. Відповіді на листи людей не було кому «підстрахувати».

Що робить: раз на годину дивиться листи Primary за 3 доби. Якщо лист
  • від ЛЮДИНИ (ім'я+прізвище, не noreply/розсилка),
  • містить питання/прохання відповісти,
  • НЕ є підтвердженням оплати (paidfacts),
  • і в «Надіслані» нема жодного листа цій людині після нього,
— шле одну картку «ще без відповіді» (макс 3 рази, не частіше за 12 год).
Кнопки «✅ Відповів» / «⏭ Не треба» гасять нагадування назавжди.
Нічого не вигадує: показує лише фрагмент самого листа.

Callback-префікси: mr_done_ / mr_no_
"""
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import ai_kit as K

TAG = "mailreply"
STATE_FILE = "mailreply_state.json"     # {uid: {...}}
STORE_FILE = "mailreply_store.json"
SCAN_FILE = "mailreply_scan.json"

SCAN_GAP_MIN = 55
MIN_AGE_H = 2          # перші 2 години Олег і так бачить алерт про лист
REPEAT_GAP_H = 12
MAX_REMINDS = 3
MAX_AGE_DAYS = 3
MAX_CARDS_PER_RUN = 2

_store = K.PayloadStore(STORE_FILE)

_AUTO_ADDR = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|newsletter|notification|mailer|"
    r"marketing|noti?fy|digest|alerts?@|updates?@|news@|bounce|automat|"
    r"billing@|invoice@|support@|info@|hello@|team@)", re.I)
_NAME_WORD = r"[A-Za-zÀ-žА-Яа-яІіЇїЄєҐґ'’\-\.]{2,}"
_PERSON_NAME = re.compile(rf"^{_NAME_WORD}( {_NAME_WORD}){{1,3}}$")

_ASK = re.compile(
    r"\?|odpoveď|odpoved|odpíš|dajte vedieť|dajte mi vedieť|potvrďte|potvrdi|"
    r"prosím o|prosim o|ozvite|napíšte|napiste|please (reply|confirm|let me|advise)|"
    r"let me know|could you|can you|would you|get back to me|"
    r"відповід|підтверд|дай знати|дайте знати|напиши|повідом", re.I)

_QUOTE = re.compile(r"^\s*(>|On .* wrote:|Dňa .* napísal|\d{1,2}\. .* napísal).*$", re.I | re.M)


def _plain(body) -> str:
    try:
        import paidfacts as _pf
        return _pf.strip_html(body)
    except Exception:
        return re.sub(r"<[^>]+>", " ", str(body or ""))


# ─── ЕВРИСТИКИ (чисті, тестуються без IMAP) ─────────────────────────────────

def split_sender(sender: str):
    """'Jana V / Soft-Tech <a@b.sk>' -> (name, addr)"""
    s = str(sender or "")
    m = re.search(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}", s.lower())
    addr = m.group(0) if m else ""
    name = re.sub(r"<[^>]*>", "", s).replace('"', "").strip()
    name = re.split(r"\s*[/|,]\s*", name)[0].strip()
    return name, addr


def is_person(sender: str) -> bool:
    name, addr = split_sender(sender)
    if not addr or _AUTO_ADDR.search(addr):
        return False
    if re.search(r"\d", name) or not _PERSON_NAME.match(name):
        return False
    return True


def asks_reply(subject: str, body: str) -> bool:
    txt = _QUOTE.sub("", _plain(body))
    return bool(_ASK.search(txt[:1500]) or _ASK.search(str(subject or "")))


def needs_reply(sender: str, subject: str, body: str) -> bool:
    if not is_person(sender):
        return False
    try:
        import paidfacts as _pf
        if _pf.is_paid_mail(subject, body):
            return False
    except Exception:
        pass
    return asks_reply(subject, body)


# ─── IMAP ───────────────────────────────────────────────────────────────────

def _fetch():
    """[{uid, sender, subject, body, dt}] — Primary за MAX_AGE_DAYS, нові першими.
    Одночасно повертає mail-конект для перевірки «Надісланих» не потрібен:
    це робить _answered(). None якщо пошта впала."""
    try:
        import email as _email
        import monitor as _m
        mail = _m._imap_connect()
        mail.select("INBOX", readonly=True)
        _, d = mail.uid("search", None,
                        f'X-GM-RAW "category:primary newer_than:{MAX_AGE_DAYS}d"')
        uids = [u.decode() for u in d[0].split()]
        uids = sorted(uids, key=int)[::-1][:25]
        out = []
        for u in uids:
            _, md = mail.uid("fetch", u.encode(), "(BODY.PEEK[])")
            msg = _email.message_from_bytes(md[0][1])
            sender = _m._imap_decode_header(msg.get("From", ""))
            if not is_person(sender):
                continue
            try:
                dt = parsedate_to_datetime(msg.get("Date", ""))
                dt = dt.astimezone(K.now().tzinfo) if K.now().tzinfo else dt.replace(tzinfo=None)
                dt = dt.replace(tzinfo=None)
            except Exception:
                dt = None
            out.append({"uid": u, "sender": sender,
                        "subject": _m._imap_decode_header(msg.get("Subject", "(без теми)")),
                        "body": _m._imap_get_body(msg) or "", "dt": dt})
        sent_to = _sent_recipients(mail)
        try:
            mail.logout()
        except Exception:
            pass
        return out, sent_to
    except Exception as e:
        K.log(TAG, f"fetch error: {e}")
        return None


def _sent_recipients(mail):
    """{addr: найновіша дата листа Олега цій адресі} за MAX_AGE_DAYS."""
    res = {}
    try:
        import email as _email
        typ, _ = mail.select('"[Gmail]/Sent Mail"', readonly=True)
        if typ != "OK":
            return res
        _, d = mail.uid("search", None, f"SINCE {(datetime.now() - timedelta(days=MAX_AGE_DAYS + 1)).strftime('%d-%b-%Y')}")
        for u in d[0].split()[-60:]:
            _, md = mail.uid("fetch", u, "(BODY.PEEK[HEADER.FIELDS (TO CC DATE)])")
            hm = _email.message_from_bytes(md[0][1])
            try:
                dt = parsedate_to_datetime(hm.get("Date", ""))
                dt = dt.astimezone(K.now().tzinfo) if K.now().tzinfo else dt.replace(tzinfo=None)
                dt = dt.replace(tzinfo=None)
            except Exception:
                continue
            for a in re.findall(r"[\w.+%-]+@[\w.-]+\.[a-z]{2,}",
                                f"{hm.get('To', '')} {hm.get('Cc', '')}".lower()):
                if a not in res or dt > res[a]:
                    res[a] = dt
    except Exception as e:
        K.log(TAG, f"sent scan error: {e}")
    return res


# ─── СТАН ───────────────────────────────────────────────────────────────────

def _state() -> dict:
    d = K.load(STATE_FILE, default={}) or {}
    return d if isinstance(d, dict) else {}


def _clip(body: str) -> str:
    t = _QUOTE.sub("", _plain(body))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:260] + ("…" if len(t) > 260 else "")


def _card(c, n) -> bool:
    name, addr = split_sender(c["sender"])
    age = ""
    if c.get("dt"):
        h = int((K.now().replace(tzinfo=None) - c["dt"]).total_seconds() // 3600)
        age = f" · {h // 24} д {h % 24} г тому" if h >= 24 else f" · {h} г тому"
    lines = [f"✉️ <b>Лист чекає на твою відповідь</b>{age}", "",
             f"👤 <b>{K.esc(name)}</b> ({K.esc(addr)})",
             f"📋 {K.esc(str(c['subject'])[:80])}", "",
             f"<i>«{K.esc(_clip(c['body']))}»</i>", "",
             "У «Надісланих» відповіді цій людині нема."]
    pid = _store.put({"uid": c["uid"]})
    kb = [[{"text": "✅ Відповів", "callback_data": f"mr_done_{pid}"},
           {"text": "⏭ Не треба", "callback_data": f"mr_no_{pid}"}]]
    if K.send_card("\n".join(lines), kb, tag=TAG):
        return True
    _store.drop(pid)
    return False


# ─── ГОЛОВНИЙ ПРОХІД ────────────────────────────────────────────────────────

def run(force=False, fetched=None) -> int:
    """Скільки карток надіслано. fetched=(emails, sent_to) — для тестів."""
    now = K.now().replace(tzinfo=None)
    if not force:
        try:
            import quiet as _q
            if _q.is_quiet():
                return 0
        except Exception:
            pass
        last = (K.load(SCAN_FILE, default={}) or {}).get("ts")
        try:
            if last and (now - datetime.fromisoformat(last)).total_seconds() < SCAN_GAP_MIN * 60:
                return 0
        except Exception:
            pass
        K.save(SCAN_FILE, {"ts": now.isoformat()})
    got = fetched if fetched is not None else _fetch()
    if not got:
        return 0
    emails, sent_to = got
    st = _state()
    sent = 0
    for c in emails:
        if sent >= MAX_CARDS_PER_RUN:
            break
        uid = str(c["uid"])
        rec = st.get(uid) or {}
        if rec.get("state") in ("done", "declined", "answered"):
            continue
        if not needs_reply(c["sender"], c["subject"], c["body"]):
            continue
        _, addr = split_sender(c["sender"])
        dt = c.get("dt")
        replied = sent_to.get(addr)
        if replied and (not dt or replied >= dt):
            K.update_key(STATE_FILE, uid, {**rec, "state": "answered"})
            continue
        if dt and (now - dt) < timedelta(hours=MIN_AGE_H):
            continue
        if dt and (now - dt) > timedelta(days=MAX_AGE_DAYS):
            continue
        if int(rec.get("n") or 0) >= MAX_REMINDS:
            continue
        try:
            if rec.get("last") and (now - datetime.fromisoformat(rec["last"])) < timedelta(hours=REPEAT_GAP_H):
                continue
        except Exception:
            pass
        if _card(c, int(rec.get("n") or 0) + 1):
            K.update_key(STATE_FILE, uid, {"state": "open", "n": int(rec.get("n") or 0) + 1,
                                           "last": now.isoformat(),
                                           "sender": c["sender"][:80],
                                           "subject": str(c["subject"])[:80]})
            sent += 1
    return sent


# ─── CALLBACK ───────────────────────────────────────────────────────────────

def handle(data: str, cb=None) -> dict:
    if data.startswith("mr_done_"):
        pid, new, txt = data[len("mr_done_"):], "done", "Ок, більше не нагадую про цей лист"
    elif data.startswith("mr_no_"):
        pid, new, txt = data[len("mr_no_"):], "declined", "Ок, відповідь не потрібна"
    else:
        return {"text": "?", "alert": False}
    payload = _store.get(pid) or {}
    uid = payload.get("uid")
    if uid:
        rec = _state().get(str(uid)) or {}
        K.update_key(STATE_FILE, str(uid), {**rec, "state": new})
    return {"text": txt, "alert": False, "keyboard": []}
