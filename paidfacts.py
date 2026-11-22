#!/usr/bin/env python3
"""
СПІЛЬНА ПАМ'ЯТЬ «ЦЕ ВЖЕ ОПЛАЧЕНО» для deadlines_watcher і bills_watcher.

Проблема, яку закриває: Олег оплатив страховку, у пошті є лист «Poďakovanie za
úhradu», він сам написав «оплатив» — а бот через 3 год знову читає старий
«Avízo na zaplatenie» і шле нову карточку з іншою датою.

Що тут зберігається (файл paid_facts.json, гілка data — переживає редеплой):
  mail: {uid: {org, ids, ts}}   — листи-підтвердження оплати, які бот бачив
  user: [{org, ts, src}]        — «оплатив …» в чаті / кнопка «Вже оплачено»

Нічого не вигадується: факт з'являється або з реального листа-підтвердження
(регулярка по темі/тексту), або з прямої дії Олега.
"""

import re
import unicodedata

import ai_kit as K

TAG = "paidfacts"
FILE = "paid_facts.json"

# Лист-підтвердження оплати (SK / CZ / EN / UK / DE)
_PAID_MAIL_RE = re.compile(
    r"(poďakovanie za (úhradu|platbu)|ďakujeme za (úhradu|platbu|zaplatenie)"
    r"|úhrada (poistného )?(bola )?(prijat|zaevidovan|zaúčtovan)"
    r"|platba (bola )?(úspešn|prijat|pripísan|zaevidovan)"
    r"|potvrdenie (o )?(úhrad|platb|zaplaten|transakci)"
    r"|(poistné|faktúra|záloha)\s+(bolo|bola)\s+(uhraden|zaplaten)"
    r"|payment (received|successful|confirmation|confirmed)"
    r"|thank you for (your )?payment|receipt for your payment"
    r"|we('| ha)ve received your payment"
    r"|дякуємо за (оплату|сплату)|оплат\w* (отримано|прийнято|успішн)"
    r"|підтвердження (оплати|сплати)"
    r"|vielen dank für ihre zahlung|zahlung (erhalten|eingegangen))",
    re.I)

_NOISE = {"the", "and", "inc", "ltd", "llc", "gmbh", "corp", "corporation",
          "services", "service", "group", "company", "mail", "info", "noreply",
          "notifikacie", "notifikácie", "support", "team", "billing", "no-reply",
          "a.s", "s.r.o", "spol", "com", "www"}


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def strip_html(s) -> str:
    s = re.sub(r"(?is)<(script|style|head|title)[^>]*>.*?</\1>", " ", str(s or ""))
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">"))
    return re.sub(r"\s+", " ", s).strip()


def org_token(s) -> str:
    """Головне слово назви: «KOOPERATIVA poisťovňa» -> kooperativa.
    Для відправника береться відображуване ім'я, а не адреса."""
    s = str(s or "")
    s = re.sub(r"<[^>]*>", " ", s)
    for w in re.findall(r"[a-zа-яіїєґ0-9]+", norm(s)):
        if w not in _NOISE and len(w) >= 4:
            return w
    return ""


def ids_in(text) -> list:
    """Номери поліса / довгі числа (НЕ ЄЧВ авто — він спільний для багатьох справ) — для звʼязку листа з карткою."""
    t = str(text or "").upper()
    out = set()
    for m in re.findall(r"\bPZ\s?(\d{6,})\b", t):
        out.add(m)
    for m in re.findall(r"\b(\d{9,})\b", t):
        out.add(m)
    return sorted(out)


def is_paid_mail(subject, body="") -> bool:
    """Тема або початок ТЕКСТУ (без HTML) — підтвердження оплати?"""
    subj = str(subject or "")
    if _PAID_MAIL_RE.search(subj):
        return True
    return bool(_PAID_MAIL_RE.search(strip_html(body)[:500]))


def _load() -> dict:
    d = K.load(FILE, default={}) or {}
    if not isinstance(d, dict):
        d = {}
    d.setdefault("mail", {})
    d.setdefault("user", [])
    return d


def _uid_int(u):
    try:
        return int(str(u))
    except Exception:
        return None


def learn_mail(uid, sender, subject, body="") -> bool:
    """Запам'ятати лист-підтвердження. True якщо це справді підтвердження."""
    if not is_paid_mail(subject, body):
        return False
    uid = str(uid or "")
    d = _load()
    if uid and uid in d["mail"]:
        return True
    rec = {"org": org_token(sender) or org_token(subject),
           "ids": ids_in(f"{subject} {strip_html(body)[:1500]}"),
           "subject": str(subject or "")[:120],
           "ts": K.today_str()}
    key = uid or f"s{abs(hash(subject)) % 10**8}"
    d["mail"][key] = rec
    # тримаємо останні 200
    if len(d["mail"]) > 200:
        keys = sorted(d["mail"], key=lambda k: str(d["mail"][k].get("ts")), reverse=True)[:200]
        d["mail"] = {k: d["mail"][k] for k in keys}
    K.save(FILE, d)
    K.log(TAG, f"лист-підтвердження оплати: uid={uid} org={rec['org']} ids={rec['ids']}")
    return True


def mail_closes(org, item_uid, item_ids=()) -> bool:
    """Є лист-підтвердження від тієї ж організації (або з тим самим номером
    поліса), який НОВІШИЙ за лист із вимогою (uid більший)?"""
    tok = org_token(org)
    d = _load()
    iu = _uid_int(item_uid)
    ids = {str(i).upper() for i in (item_ids or ()) if i}
    for fu, rec in d["mail"].items():
        fi = _uid_int(fu)
        if iu is not None and fi is not None and fi <= iu:
            continue  # підтвердження старіше за вимогу — це не про неї
        same_org = bool(tok) and tok == (rec.get("org") or "")
        same_id = bool(ids & {str(i).upper() for i in rec.get("ids") or []})
        if same_org or same_id:
            return True
    return False


def record_user(org_text, src="chat") -> str:
    """Олег сказав/натиснув «оплачено» про організацію. Повертає токен."""
    tok = org_token(org_text)
    if not tok:
        return ""
    d = _load()
    d["user"].append({"org": tok, "ts": K.now().isoformat(timespec="minutes"), "src": src})
    d["user"] = d["user"][-100:]
    K.save(FILE, d)
    return tok
