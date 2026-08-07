#!/usr/bin/env python3
"""
confirm.py — двокрокове підтвердження для «незворотних» кнопок.

Навіщо: Олег натискав «🚫 Не цікавить» / «🚫 Скасовано» — і бот МОВЧКИ вимикав
нагадування. Випадковий дотик = втрачене нагадування, і незрозуміло, що зникло.

Тепер такі кнопки спершу питають:
    ⚠️ Точно? Я більше не нагадуватиму про «Тренування».
    [✅ Так, не нагадуй] [↩️ Ні, залиш]
і виконують дію ТІЛЬКИ після «Так».

Як підключити нову дію:
    confirm.register("cw_cancel", handler=lambda pid: cw.do_cancel(pid),
                     question=..., yes_label=..., no_label=...)
Далі bot.py: замість прямого виклику — confirm.ask("cw_cancel", pid, ...).

Callback-префікси: cfm_y_<cid> (так) / cfm_n_<cid> (ні).
Payload живе 24 год; протух → «⚠️ Питання застаріло, нічого не змінив».
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K  # noqa: E402

TAG = "confirm"
STORE_FILE = "confirm_store.json"
LOG_FILE = "confirm_log.json"
TTL_HOURS = 24

_store = K.PayloadStore(STORE_FILE)

# Реєстр дій: name -> {handler, question, detail, yes, no, done}
_ACTIONS = {}


def register(name: str, handler, question: str, yes: str = "✅ Так",
             no: str = "↩️ Ні, залиш", done: str = "Готово."):
    """Реєструє дію, яка вимагає підтвердження."""
    _ACTIONS[name] = {"handler": handler, "question": question,
                      "yes": yes, "no": no, "done": done}


def is_registered(name: str) -> bool:
    return name in _ACTIONS


# ─── КРОК 1: ЗАПИТАТИ ────────────────────────────────────────────────────────

def ask(name: str, pid: str, subject: str = "", extra: dict = None) -> dict:
    """Готує питання. Повертає {ok, text, keyboard} — bot.py показує це Олегу.

    subject — про що саме мова («Тренування», «крипто»), підставляється в текст.
    """
    a = _ACTIONS.get(name)
    if not a:
        return {"ok": False, "error": "unknown_action"}
    cid = _store.put({"action": name, "pid": pid, "subject": subject,
                      "extra": extra or {}, "ts": K.now().isoformat()})
    subj = K.esc(str(subject or "")).strip()
    q = a["question"].replace("{subject}", f"«{subj}»" if subj else "це")
    text = (f"⚠️ <b>Точно?</b>\n━━━━━━━━━━━━━━━━━━━━\n{q}\n\n"
            "<i>Нічого не змінюю, поки не підтвердиш.</i>")
    kb = [[{"text": a["yes"], "callback_data": f"cfm_y_{cid}"},
           {"text": a["no"], "callback_data": f"cfm_n_{cid}"}]]
    K.log(TAG, f"питаю підтвердження: {name} / {subject[:30]}")
    return {"ok": True, "cid": cid, "text": text, "keyboard": kb}


# ─── КРОК 2: ВІДПОВІДЬ ───────────────────────────────────────────────────────

def _expired(p) -> bool:
    try:
        ts = datetime.fromisoformat(str(p.get("ts"))).replace(tzinfo=None)
    except Exception:
        return False
    return (K.now().replace(tzinfo=None) - ts) > timedelta(hours=TTL_HOURS)


def _log(action: str, subject: str, answer: str):
    K.update_key(LOG_FILE, K.Dedup.key(f"{action}|{subject}|{K.now().isoformat()}"),
                 {"action": action, "subject": subject, "answer": answer,
                  "ts": K.now().isoformat()})
    try:
        import response_log
        response_log.log_response("confirm", f"{action}: {subject}", answer)
    except Exception as e:
        K.log(TAG, f"response_log error: {e}")


def yes(cid: str) -> dict:
    """Олег підтвердив — виконуємо реальну дію."""
    p = _store.get(cid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    if _expired(p):
        return {"ok": False, "error": "expired"}
    a = _ACTIONS.get(p.get("action"))
    if not a:
        return {"ok": False, "error": "unknown_action"}
    try:
        r = a["handler"](p.get("pid")) or {}
    except Exception as e:
        K.log(TAG, f"handler error {p.get('action')}: {e}")
        return {"ok": False, "error": f"handler: {str(e)[:80]}"}
    if not isinstance(r, dict):
        r = {"ok": bool(r)}
    _log(str(p.get("action")), str(p.get("subject") or ""), "yes")
    r.setdefault("ok", True)
    r["confirmed"] = True
    r["done_text"] = a["done"]
    r["subject"] = p.get("subject")
    K.log(TAG, f"✅ підтверджено: {p.get('action')} / {str(p.get('subject'))[:30]}")
    return r


def no(cid: str) -> dict:
    """Олег відмовився — нічого не робимо, стан не змінюється."""
    p = _store.get(cid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    _log(str(p.get("action")), str(p.get("subject") or ""), "no")
    K.log(TAG, f"↩️ скасовано користувачем: {p.get('action')}")
    return {"ok": True, "cancelled": True, "subject": p.get("subject")}


# ─── ЗВІТ ────────────────────────────────────────────────────────────────────

def report(days: int = 14) -> str:
    """/підтвердження — що саме ти вимикав і від чого відмовився."""
    data = K.load(LOG_FILE, default={}) or {}
    if not data:
        return ("🛑 <b>ПІДТВЕРДЖЕННЯ</b>\n\nЩе порожньо — сюди потрапляють кнопки, "
                "які вимикають нагадування (я завжди перепитую перед цим).")
    cutoff = K.now().replace(tzinfo=None) - timedelta(days=days)
    rows = []
    for rec in data.values():
        if not isinstance(rec, dict):
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts"))).replace(tzinfo=None)
        except Exception:
            continue
        if ts >= cutoff:
            rows.append((ts, rec))
    if not rows:
        return f"🛑 <b>ПІДТВЕРДЖЕННЯ</b>\n\nЗа {days} дн. нічого не вимикав."
    rows.sort(reverse=True)
    out = [f"🛑 <b>ПІДТВЕРДЖЕННЯ</b> (за {days} дн.)", "━━━━━━━━━━━━━━━━━━━━"]
    for ts, r in rows[:20]:
        mark = "🚫 вимкнув" if r.get("answer") == "yes" else "↩️ передумав"
        out.append(f"{ts.strftime('%d.%m %H:%M')} · {K.esc(str(r.get('subject')))} — {mark}")
    return "\n".join(out)[:3900]


def gc(days: int = 3) -> int:
    return _store.gc(days=days) if hasattr(_store, "gc") else 0


# ─── РЕЄСТРАЦІЯ ДІЙ ──────────────────────────────────────────────────────────

def _h_cw_cancel(pid):
    import calendar_watch as cw
    return cw.do_cancel(pid)


def _h_gx_mute(pid):
    import ai_buttons as gx
    return gx.do_mute(pid)


register(
    "cw_cancel", _h_cw_cancel,
    question=("Я більше <b>не нагадуватиму</b> про {subject} — ні за 3 дні, "
              "ні за добу, ні за 2 години, ні за 30 хвилин."),
    yes="✅ Так, не нагадуй", no="↩️ Ні, залиш нагадування",
    done="🚫 Ок — більше не нагадую.",
)

register(
    "gx_mute", _h_gx_mute,
    question=("Я на <b>7 днів</b> замовкну по темі {subject} — ні сповіщень, "
              "ні аналізу, ні згадок у звітах."),
    yes="✅ Так, приховай тему", no="↩️ Ні, залиш",
    done="🚫 Тему приховано.",
)


if __name__ == "__main__":
    if "--report" in sys.argv:
        print(report())
    else:
        print("Зареєстровані дії:", ", ".join(sorted(_ACTIONS)))
