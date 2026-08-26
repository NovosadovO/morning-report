#!/usr/bin/env python3
"""
recall.py — ПОВНА ПАМ'ЯТЬ: бот пам'ятає ВСЕ, що Олег писав і натискав.

Пише в один журнал `oleh_log.json` (гілка data, переживає редеплой):
  - кожне повідомлення Олега (звичайне І командне);
  - кожне натискання кнопки (callback) з людською назвою;
  - його відповіді на питання бота.

Віддає `block()` — стисла витяжка для будь-якого AI-промпту:
свіже дослівно + старе згруповане. Підключено в `ai_brain.memory_block()`,
тому потрапляє в КОЖЕН не-JSON промпт автоматично.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_kit as K

TAG = "recall"
LOG_FILE = "oleh_log.json"

KEEP = 1200          # скільки записів тримаємо (усе життя переписки в межах розумного)
VERBATIM = 25        # скільки останніх — дослівно в промпт
OLDER_DAYS = 30      # глибина зведеної частини
MAX_CHARS = 2600     # ліміт блоку в промпті


def _log(m):
    K.log(TAG, m)


def _now_iso():
    return K.now().replace(tzinfo=None).isoformat(timespec="seconds")


def _clean(s, n=400) -> str:
    s = " ".join(str(s or "").split())
    return s[:n]


def load() -> list:
    data = K.load(LOG_FILE, default={"items": []}) or {}
    if isinstance(data, list):
        return data
    items = data.get("items")
    return items if isinstance(items, list) else []


def _put(rec: dict):
    items = load()
    items.append(rec)
    K.save(LOG_FILE, {"items": items[-KEEP:]})


def log_message(text: str, kind: str = "msg"):
    """Кожне повідомлення Олега — і команда, і звичайний текст."""
    t = _clean(text, 700)
    if not t:
        return
    _put({"t": "msg", "kind": kind, "text": t, "ts": _now_iso()})
    _log("повідомлення збережено: " + t[:60])


def log_button(data: str, label: str = ""):
    """Кожне натискання кнопки — це теж його відповідь."""
    d = _clean(data, 120)
    if not d:
        return
    _put({"t": "btn", "data": d, "label": _clean(label, 160), "ts": _now_iso()})
    _log("кнопка збережена: " + d[:50])


def log_answer(question: str, answer: str, topic: str = ""):
    """Пряма відповідь на питання бота."""
    a = _clean(answer, 700)
    if not a:
        return
    _put({"t": "ans", "q": _clean(question, 200), "text": a,
          "topic": _clean(topic, 60), "ts": _now_iso()})
    # дублюємо в старий канал пам'яті, щоб нічого не втратити
    try:
        import ai_brain
        ai_brain.remember_answer(question or "питання бота", a,
                                 topic=topic or "чат", source=TAG)
    except Exception as e:
        _log("ai_brain: " + str(e))


def _dt(rec):
    try:
        return datetime.fromisoformat(str(rec.get("ts")))
    except Exception:
        return None


def _line(rec) -> str:
    d = _dt(rec)
    when = d.strftime("%d.%m %H:%M") if d else "?"
    t = rec.get("t")
    if t == "btn":
        return when + " [кнопка] " + (rec.get("label") or rec.get("data", ""))
    if t == "ans":
        q = rec.get("q") or ""
        head = ("на «" + q[:60] + "»: ") if q else ""
        return when + " " + head + rec.get("text", "")
    return when + " " + rec.get("text", "")


def block(max_chars: int = MAX_CHARS) -> str:
    """Витяжка для промпту. '' якщо журнал порожній."""
    items = load()
    if not items:
        return ""
    fresh = items[-VERBATIM:]
    older = items[:-VERBATIM]

    parts = ["ВСЕ, ЩО ОЛЕГ ПИСАВ І НАТИСКАВ (дослівно, найновіше внизу):"]
    for r in fresh:
        parts.append("• " + _line(r))

    if older:
        cut = K.now().replace(tzinfo=None) - timedelta(days=OLDER_DAYS)
        texts = []
        for r in reversed(older):
            d = _dt(r)
            if d and d < cut:
                break
            if r.get("t") in ("msg", "ans"):
                texts.append(_clean(r.get("text"), 90))
            if len(texts) >= 30:
                break
        if texts:
            parts.append("РАНІШЕ ВІН ТАКОЖ КАЗАВ: " + " | ".join(reversed(texts)))

    parts.append("Спирайся на це: посилайся на його слова, не питай те, "
                 "на що він уже відповів, не пропонуй те, що він уже відкинув.")
    return ("\n\n━━━ ПОВНА ПАМ'ЯТЬ ПЕРЕПИСКИ ━━━\n" + "\n".join(parts))[:max_chars]


def report(limit: int = 30) -> str:
    """/пам'ять — що саме бот пам'ятає."""
    items = load()
    if not items:
        return "🧠 <b>Пам'ять</b>\n\nЖурнал порожній — ще нічого не записано."
    msgs = sum(1 for r in items if r.get("t") == "msg")
    btns = sum(1 for r in items if r.get("t") == "btn")
    ans = sum(1 for r in items if r.get("t") == "ans")
    first = _dt(items[0])
    lines = ["🧠 <b>Що я пам'ятаю</b>", ""]
    lines.append("Записів: " + str(len(items)) + " (повідомлень " + str(msgs)
                 + ", кнопок " + str(btns) + ", відповідей " + str(ans) + ")")
    if first:
        lines.append("Найстаріший запис: " + first.strftime("%d.%m.%Y"))
    lines.append("")
    lines.append("<b>Останнє:</b>")
    for r in items[-limit:]:
        lines.append("• " + K.esc(_clean(_line(r), 130)))
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
