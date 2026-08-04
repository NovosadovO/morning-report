#!/usr/bin/env python3
"""
feedback_ctx.py — ЄДИНИЙ контекст реакцій Олега для AI.

Проблема, яку вирішує: натискання кнопок зберігались у 4 різні файли
(gx_ack.json, calendar_ack.json, response_log.json, ai_notes.json), але
AI-промпти їх майже не читали. Тобто Олег тиснув «✅ зроблено», «🚫 не цікавить»,
писав нотатки — а наступне AI-повідомлення про це не знало.

Тут усе зводиться в один короткий блок тексту, який підмішується в промпти:
чат, проактивні повідомлення, календарні AI-коментарі, звіти.

Використання:
    import feedback_ctx
    ctx = feedback_ctx.build(days=7)      # готовий текст для промпта
    stats = feedback_ctx.stats(days=7)    # {"done": 3, "muted": 1, ...}

Ніколи не падає: будь-яка помилка джерела → цей блок просто коротший.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TZ = timezone(timedelta(hours=2))
MAX_CHARS = 1400
_CACHE = {"ts": None, "days": None, "text": "", "stats": {}}
_CACHE_TTL = 180  # сек — промпти будуються часто, GitHub-читання дороге


def _now():
    return datetime.now(TZ).replace(tzinfo=None)


def _ts(rec, field="ts"):
    try:
        return datetime.fromisoformat(str(rec.get(field))).replace(tzinfo=None)
    except Exception:
        return None


def _recent(data: dict, cutoff):
    """Записи {key: {..., ts}} новіші за cutoff, найсвіжіші спершу."""
    rows = []
    for rec in (data or {}).values():
        if not isinstance(rec, dict):
            continue
        t = _ts(rec)
        if t and t >= cutoff:
            rows.append((t, rec))
    rows.sort(reverse=True)
    return rows


# ─── ДЖЕРЕЛА ─────────────────────────────────────────────────────────────────

def _buttons(cutoff):
    """gx_ack.json — універсальні кнопки під AI-сповіщеннями."""
    try:
        import ai_kit as K
        import ai_buttons as gx
        return _recent(K.load(gx.ACK_FILE, default={}) or {}, cutoff)
    except Exception as e:
        print(f"[feedback_ctx] buttons: {e}", flush=True)
        return []


def _calendar(cutoff):
    """calendar_ack.json — відповіді під нагадуваннями про події."""
    try:
        import ai_kit as K
        import calendar_watch as cw
        return _recent(K.load(cw.ACK_FILE, default={}) or {}, cutoff)
    except Exception as e:
        print(f"[feedback_ctx] calendar: {e}", flush=True)
        return []


def _responses(days):
    """response_log.json — текстові відповіді й старі кнопки."""
    try:
        import response_log as rl
        return rl.get_responses(days=days) or []
    except Exception as e:
        print(f"[feedback_ctx] responses: {e}", flush=True)
        return []


_JUNK = ("привіт олеже", "привіт олег", "подія: none", "none (none)",
         "відмічено без коментаря", "вітаю, олеже")


def _is_junk(t: str) -> bool:
    """Відсіює нотатки-сміття: тіло AI-повідомлення, "Подія: None" тощо.
    Такі записи не є фактами про Олега і не мають повертатись у промпт."""
    low = " ".join(str(t or "").split()).lower()
    if len(low) < 8:
        return True
    return any(low.startswith(j) or j in low[:40] for j in _JUNK)


def _notes(limit=8):
    """ai_notes.json — нотатки (останні), без сміття."""
    try:
        import ai_notes
        notes = ai_notes.load_notes() or []
        clean = []
        for n in notes:
            t = n.get("text") if isinstance(n, dict) else n
            if not _is_junk(t):
                clean.append(n)
        return clean[-limit:]
    except Exception as e:
        print(f"[feedback_ctx] notes: {e}", flush=True)
        return []


# ─── СТАТИСТИКА ──────────────────────────────────────────────────────────────

def stats(days: int = 7) -> dict:
    cutoff = _now() - timedelta(days=days)
    out = {"done": 0, "missed": 0, "muted": 0, "later": 0, "more": 0,
           "noted": 0, "seen": 0, "total": 0}
    for _, r in _buttons(cutoff) + _calendar(cutoff):
        a = str(r.get("answer") or "")
        out["total"] += 1
        if a == "done":
            out["done"] += 1
        elif a == "missed":
            out["missed"] += 1
        elif a.startswith("muted"):
            out["muted"] += 1
        elif a.startswith("later") or a.startswith("snooze"):
            out["later"] += 1
        elif a in ("more", "ai_prep", "next_steps"):
            out["more"] += 1
        elif a == "noted":
            out["noted"] += 1
        else:
            out["seen"] += 1
    return out


def muted_topics(days: int = 3) -> list:
    """Теми, під якими Олег натиснув «🚫 Не цікавить» — AI не повторює їх."""
    cutoff = _now() - timedelta(days=days)
    topics = []
    for _, r in _buttons(cutoff):
        if str(r.get("answer") or "").startswith("muted") and r.get("topic"):
            if r["topic"] not in topics:
                topics.append(r["topic"])
    return topics


# ─── ТЕКСТ ДЛЯ ПРОМПТА ───────────────────────────────────────────────────────

def _clean(s, n=140):
    return " ".join(str(s or "").split())[:n]


def build(days: int = 7, max_chars: int = MAX_CHARS) -> str:
    """Короткий блок «як Олег реагував» — для вставки в будь-який AI-промпт."""
    now = _now()
    c = _CACHE
    if (c["text"] or c["stats"]) and c["days"] == days and c["ts"] \
            and (now - c["ts"]).total_seconds() < _CACHE_TTL:
        return c["text"]

    cutoff = now - timedelta(days=days)
    st = stats(days=days)
    lines = []

    if st["total"]:
        parts = []
        if st["done"]:
            parts.append(f"виконано {st['done']}")
        if st["missed"]:
            parts.append(f"не відбулось {st['missed']}")
        if st["more"]:
            parts.append(f"просив деталей {st['more']}")
        if st["later"]:
            parts.append(f"відкладав {st['later']}")
        if st["muted"]:
            parts.append(f"приховав {st['muted']}")
        if st["noted"]:
            parts.append(f"нотаток {st['noted']}")
        lines.append(f"РЕАКЦІЇ ОЛЕГА ({days} дн., {st['total']} натискань): "
                     + ", ".join(parts) + ".")

    mt = muted_topics(days=3)
    if mt:
        try:
            import ai_buttons as gx
            labels = [gx.TOPIC_LABEL.get(t, t) for t in mt]
        except Exception:
            labels = mt
        lines.append("НЕ ЦІКАВИТЬ (сам приховав, не піднімай знову): "
                     + ", ".join(labels) + ".")

    btn = _buttons(cutoff)
    if btn:
        rows = []
        for t, r in btn[:6]:
            topic = r.get("topic") or ""
            rows.append(f"{t.strftime('%d.%m %H:%M')} {topic}: {_clean(r.get('answer'), 40)}"
                        + (f" — «{_clean(r.get('note'), 90)}»" if r.get("note") else ""))
        lines.append("ОСТАННІ КНОПКИ: " + " | ".join(rows))

    cal = _calendar(cutoff)
    if cal:
        rows = []
        for t, r in cal[:6]:
            rows.append(f"{t.strftime('%d.%m')} {_clean(r.get('title'), 40)}: "
                        f"{_clean(r.get('answer'), 30)}"
                        + (f" — «{_clean(r.get('note'), 90)}»" if r.get("note") else ""))
        lines.append("ПО ПОДІЯХ: " + " | ".join(rows))

    resp = _responses(days)
    if resp:
        rows = []
        for r in resp[-5:]:
            rows.append(f"{_clean(r.get('category'), 24)}: {_clean(r.get('answer'), 80)}")
        lines.append("ЙОГО ВІДПОВІДІ ТЕКСТОМ: " + " | ".join(rows))

    nts = _notes()
    if nts:
        rows = []
        for n in nts:
            if isinstance(n, dict):
                rows.append(_clean(n.get("text"), 110))
            else:
                rows.append(_clean(n, 110))
        lines.append("ЙОГО НОТАТКИ: " + " | ".join([r for r in rows if r]))

    if not lines:
        text = ""
    else:
        text = ("\n📌 ЗВОРОТНИЙ ЗВ'ЯЗОК ОЛЕГА (реальні дані, спирайся на них, "
                "не вигадуй): \n" + "\n".join(f"• {l}" for l in lines))
        text = text[:max_chars]

    _CACHE.update({"ts": now, "days": days, "text": text, "stats": st})
    return text


def is_muted(topic: str, days: int = 3) -> bool:
    """Чи Олег щойно приховав цю тему — перед відправкою сповіщення."""
    try:
        return topic in muted_topics(days=days)
    except Exception:
        return False


if __name__ == "__main__":
    d = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print("STATS:", stats(d))
    print("MUTED:", muted_topics(3))
    print("---")
    print(build(d) or "(порожньо)")
