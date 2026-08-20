#!/usr/bin/env python3
"""
dismissed.py — ПОСТІЙНИЙ блок-лист «більше не нагадувати».

Проблема, яку це лікує: Олег натискав «❌ Не треба» → бот перепитував →
Олег підтверджував «✅ Так, не нагадуй» → і бот… лише прибирав клавіатуру.
Ніде не зберігалось, що тема закрита. Тому та сама подія / той самий лист
через годину-добу приходили знову.

Тепер КОЖНЕ підтверджене «не нагадуй» пишеться сюди назавжди (файл у гілці
data, переживає редеплой), і кожен відправник перед надсиланням питає
is_muted(). Заглушено = тиша.

Матчинг подвійний, бо id у пропозицій щоразу нові:
  1) за ключем   — kind|key (email UID, payload id, source_id)
  2) за назвою   — нормалізований заголовок (без емодзі/регістру/«Re:»)
Досить одного збігу — і повідомлення не піде.

API:
    mute(kind, key=None, title=None, note="")   — заглушити
    is_muted(kind=None, key=None, title=None)   — перевірка перед відправкою
    remember_confirm(action, pid, subject, msg) — хук з confirm.yes()
    unmute(kind, key) / unmute_all()            — повернути нагадування
    report()                                    — /вимкнені_нагадування
"""
import hashlib
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K  # noqa: E402

TAG = "dismissed"
FILE = "dismissed.json"

# Дії (з confirm.py), після підтвердження яких тема закривається НАЗАВЖДИ.
# gate_* — універсальний гейт у bot.py, решта — власні обробники.
MUTE_ACTIONS = {
    "gate_cal_skip", "gate_shop_skip", "gate_bill_skip", "gate_dl_skip",
    "gate_dm_skip", "gate_fu_skip", "gate_vr_skip", "gate_wr_skip",
    "gate_rp_skip", "gate_pa_skip", "gate_email_keep",
    "gate_bill_paid", "gate_bill_due_paid", "gate_vr_paid", "gate_dl_due_done",
    "cw_cancel", "cw_miss", "calrem_skip", "gx_mute", "email_delete",
}

_CACHE = {"data": None, "ts": 0.0}
_CACHE_TTL = 20  # с. Достатньо, щоб не бити по storage у циклі, і не проґавити свіже.

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿️‍⬀-⯿]+")
_TAGS = re.compile(r"<[^>]+>")
_JUNK = re.compile(r"^(re|fw|fwd|відповідь|наг)\s*[:\-]\s*", re.I)
_NONWORD = re.compile(r"[^0-9a-zа-яіїєґ ]+", re.I)
_SPACES = re.compile(r"\s+")

# Службові тексти кнопок — це НЕ назва теми, за такими матчити не можна,
# інакше одне «Не треба» заглушило б усе на світі.
_BAD_TITLES = {
    "не треба", "скасувати", "скасовано", "пропусти", "пропустити", "ні",
    "так", "залиш", "залишити", "не нагадуй", "видали", "не надсилай",
    "прибери", "відхили", "оплачено", "зроблено", "готово", "поставити",
    "додати", "додай", "запиши", "постав", "далі", "ок", "",
}


def _norm(title) -> str:
    """Назва теми → стабільний ключ. «🔔 Лист від Michaela — «Re: Faktúra»» і
    «Лист від Michaela — Faktúra» дають один ключ."""
    t = str(title or "")
    t = _TAGS.sub(" ", t)
    t = t.replace("«", " ").replace("»", " ")
    t = _EMOJI.sub(" ", t)
    t = t.strip().lower()
    t = _JUNK.sub("", t)
    t = _NONWORD.sub(" ", t)
    t = _SPACES.sub(" ", t).strip()
    return t[:70]


def _title_ok(norm: str) -> bool:
    return bool(norm) and len(norm) >= 4 and norm not in _BAD_TITLES


def _tkey(norm: str) -> str:
    return "t:" + hashlib.md5(norm.encode("utf-8")).hexdigest()[:12]


def _kkey(kind: str, key: str) -> str:
    return f"k:{kind}|{key}"


def _wkey(norm: str) -> str:
    return "w:" + hashlib.md5(norm.encode("utf-8")).hexdigest()[:12]


def _kw_ok(norm: str) -> bool:
    """Keyword глушить ВСЕ, що його містить, — тому вимоги суворіші за назву:
    мінімум 4 символи і не службове слово. Інакше одне «так» вимкнуло б усе."""
    return _title_ok(norm) and len(norm) >= 4


def _stem(word: str) -> str:
    """Українські відмінки: «страховка» / «страховку» / «страховки» мають дати
    один корінь, інакше keyword не спрацював би на реальному тексті."""
    w = str(word or "")
    return w[:-2] if len(w) >= 7 else (w[:-1] if len(w) >= 5 else w)


def _kw_hit(kw: str, title_norm: str) -> bool:
    """Чи стосується заглушене ключове слово цієї назви. Кожне слово keyword-а
    мусить знайтись у назві (за коренем) — тоді «страховка авто» ловить
    «Оплатити страховку авто», але не «Страховка квартири»."""
    if not kw or not title_norm:
        return False
    t_words = title_norm.split()
    for kwd in kw.split():
        st = _stem(kwd)
        if not st:
            continue
        if not any(w.startswith(st) for w in t_words):
            return False
    return True


def _load(force: bool = False) -> dict:
    import time
    if not force and _CACHE["data"] is not None and (time.time() - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]
    data = K.load(FILE, default={}) or {}
    if not isinstance(data, dict):
        data = {}
    _CACHE["data"] = data
    _CACHE["ts"] = time.time()
    return data


def _put(store_key: str, rec: dict):
    K.update_key(FILE, store_key, rec)
    d = dict(_CACHE["data"] or {})
    d[store_key] = rec
    _CACHE["data"] = d


# ─── ЗАГЛУШИТИ ───────────────────────────────────────────────────────────────

def mute(kind: str, key=None, title=None, note: str = "", keyword=None) -> dict:
    """Записує «більше не нагадувати». Повертає, що саме заглушено.

    keyword — ширший матч: заглушує все, у назві чого це слово трапляється
    («Корфу» → жодних нагадувань про поїздку, хоч би як вони називались)."""
    kind = str(kind or "other").strip("_") or "other"
    out = {"by_key": None, "by_title": None, "by_keyword": None}
    rec = {"kind": kind, "key": str(key or ""), "title": str(title or "")[:120],
           "note": note[:120], "ts": K.now().isoformat()}
    if key not in (None, ""):
        sk = _kkey(kind, str(key))
        _put(sk, rec)
        out["by_key"] = sk
    n = _norm(title)
    if _title_ok(n):
        tk = _tkey(n)
        _put(tk, dict(rec, norm=n))
        out["by_title"] = n
    kw = _norm(keyword)
    if _kw_ok(kw):
        _put(_wkey(kw), dict(rec, norm=kw, keyword=kw))
        out["by_keyword"] = kw
    if out["by_key"] or out["by_title"] or out["by_keyword"]:
        K.log(TAG, f"🚫 більше не нагадую: {kind} / {str(title or key)[:40]}")
    else:
        K.log(TAG, f"⚠️ нічого не заглушив — ні ключа, ні назви ({kind})")
    return out


def is_muted(kind=None, key=None, title=None) -> bool:
    """True → відправляти НЕ можна. kind=None — шукати в будь-якому типі."""
    try:
        data = _load()
        if not data:
            return False
        if key not in (None, ""):
            key = str(key)
            if kind and _kkey(str(kind).strip("_"), key) in data:
                return True
            if not kind:
                for sk, rec in data.items():
                    if sk.startswith("k:") and str((rec or {}).get("key")) == key:
                        return True
        n = _norm(title)
        if _title_ok(n) and _tkey(n) in data:
            return True
        # keyword-и: заглушено все, що містить це слово
        if n:
            for sk, rec in data.items():
                if not sk.startswith("w:"):
                    continue
                if _kw_hit(str((rec or {}).get("keyword") or ""), n):
                    return True
        return False
    except Exception as e:
        # Блок-лист ніколи не має ламати відправку: не змогли прочитати —
        # поводимось як раніше.
        K.log(TAG, f"is_muted error: {e}")
        return False


def why(kind=None, key=None, title=None) -> str:
    """Для логів: чому саме промовчали."""
    data = _load()
    if key not in (None, "") and kind and _kkey(str(kind).strip("_"), str(key)) in data:
        return f"ключ {kind}|{key}"
    n = _norm(title)
    if _title_ok(n) and _tkey(n) in data:
        return f"назва «{n}»"
    for sk, rec in data.items():
        kw = str((rec or {}).get("keyword") or "")
        if sk.startswith("w:") and _kw_hit(kw, n):
            return f"ключове слово «{kw}»"
    return "?"


# ─── ХУК З confirm.yes() ─────────────────────────────────────────────────────

def remember_confirm(action: str, pid: str, subject: str = "", msg: str = "") -> bool:
    """Викликається з confirm.yes() після успішної дії.
    Заглушує тему, якщо це була кнопка «не нагадувати / не треба / зроблено».
    """
    action = str(action or "")
    if action not in MUTE_ACTIONS:
        return False
    kind = action[5:] if action.startswith("gate_") else action
    # Для gate_* pid — це сирий callback_data (напр. "cal_skip_12345"),
    # реальний ключ — хвіст після префікса.
    key = str(pid or "")
    if action.startswith("gate_") and key.startswith(kind + "_"):
        key = key[len(kind) + 1:]
    # Назва: спершу subject, але текст кнопки («Не треба») як назву не беремо —
    # тоді пробуємо витягнути тему з тексту самого повідомлення.
    title = str(subject or "")
    if not _title_ok(_norm(title)):
        title = extract_title(msg)
    mute(kind, key=key, title=title, note="confirm")
    return True


_QUOTED = re.compile(r"[«\"](.{3,90}?)[»\"]")


def extract_title(msg: str) -> str:
    """Тема з тексту повідомлення: спершу «в лапках», інакше перший змістовний
    рядок без службового заголовка."""
    t = _TAGS.sub(" ", str(msg or ""))
    m = _QUOTED.search(t)
    if m:
        return m.group(1).strip()
    for line in t.split("\n"):
        line = line.strip()
        if len(_norm(line)) >= 6 and not line.lower().startswith(("⚠️", "точно")):
            return line[:90]
    return ""


# ─── ПОВЕРНУТИ ───────────────────────────────────────────────────────────────

def unmute(kind: str = None, key: str = None, title: str = None) -> int:
    n = 0
    if key not in (None, "") and kind:
        sk = _kkey(str(kind).strip("_"), str(key))
        if sk in _load():
            K.remove_key(FILE, sk)
            n += 1
    nt = _norm(title)
    if _title_ok(nt) and _tkey(nt) in _load():
        K.remove_key(FILE, _tkey(nt))
        n += 1
    if _kw_ok(nt) and _wkey(nt) in _load():
        K.remove_key(FILE, _wkey(nt))
        n += 1
    if n:
        _CACHE["data"] = None
        K.log(TAG, f"🔔 нагадування повернуто ({n})")
    return n


def unmute_all() -> int:
    data = _load(force=True)
    n = len(data)
    K.save(FILE, {})
    _CACHE["data"] = {}
    K.log(TAG, f"🔔 повернуто нагадувань: {n}")
    return n


def count() -> int:
    return len(_load())


def report(limit: int = 25) -> str:
    data = _load(force=True)
    rows = []
    seen = set()
    for sk, rec in data.items():
        if not isinstance(rec, dict):
            continue
        label = (rec.get("title") or rec.get("key") or "").strip()
        n = _norm(label)
        if n in seen:
            continue
        seen.add(n)
        rows.append((str(rec.get("ts") or ""), rec.get("kind") or "", label))
    if not rows:
        return ("🔕 <b>ЗАКРИТІ ТЕМИ</b>\n\nПорожньо — я нагадую про все.")
    rows.sort(reverse=True)
    out = ["🔕 <b>ЗАКРИТІ ТЕМИ</b> (більше не нагадую)", "━━━━━━━━━━━━━━━━━━━━"]
    for ts, kind, label in rows[:limit]:
        try:
            d = datetime.fromisoformat(ts).strftime("%d.%m")
        except Exception:
            d = ""
        out.append(f"🚫 {K.esc(label[:60])} <i>({kind}{', ' + d if d else ''})</i>")
    out.append(f"\n<i>Всього записів: {len(data)}</i>")
    out.append("<i>Повернути все: /увімкни_нагадування</i>")
    return "\n".join(out)[:3900]


if __name__ == "__main__":
    print(report())
