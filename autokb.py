# -*- coding: utf-8 -*-
"""autokb.py — кнопки під КОЖНЕ повідомлення бота, доречні його змісту.

Логіка одна для всього, що бот шле:
1. Це питання/пропозиція («Запланувати?», «Це ще актуально?», «Відбулось?») →
   кнопки = варіанти відповіді САМЕ на це питання, через askme (відповідь
   пам'ятається назавжди, те саме більше не питається).
2. Це сповіщення (лист, рахунок, подія, пробіжка, крипта, здоров'я) → кнопки
   дії саме для цього виду, через react (з текстом сповіщення в payload).
3. Питання, на яке Олег уже відповів, повторно не ставиться взагалі —
   НАВІТЬ якщо AI сформулював його іншими словами (fuzzy-збіг по askme).

02.10 фікс: раніше довгі тексти й повідомлення з тегом report/health/
hcoach/pulse/... взагалі не перевірялись на питання (`_NEVER_BLOCK`,
`_MAX_Q_LEN`) — тому під реальним питанням («…чи потрібна допомога?»)
з'являлись узагальнені кнопки-дії («👌 Прийняв/⏰ Пізніше/🙈 Не нагадуй»),
які не відповідали суті питання. Тепер питання шукається завжди, а
кнопки-відповіді на нього ставляться під будь-яким повідомленням.
"""

TAG = "autokb"

# Тип питання ← за словами самого питання. Порядок важливий: перше влучання.
_Q_RULES = (
    ("plan", ("заплануват", "записати в календар", "додати в календар",
              "поставити в календар", "внести в календар", "забронювати",
              "призначити", "поставити нагадуван", "створити подію")),
    ("relevant", ("ще актуальн", "актуально", "чи потрібно ще",
                  "все ще треба", "лишаємо", "чи в силі")),
    ("happened", ("відбулось", "відбулася", "як пройшло", "вже минул",
                  "було вчора", "чи сталось")),
    ("result", ("вдалося", "досягти", "результат", "потрібна допомога",
                "потрібна поміч", "справився")),
    ("confirm", ("готовий", "будеш", "підеш", "робимо", "варто", "погоджуєш",
                 "підтверджуєш", "ок?", "згоден")),
)

_ASK_MARKS = ("?", "чи варто", "запланувати", "актуально", "підтвердь")


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


def _clean(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def _question_line(text: str) -> str:
    """Останнє питальне РЕЧЕННЯ — саме на нього мають відповідати кнопки.

    Раніше повертався весь рядок цілим, навіть коли перед питанням стояло
    довге вступне речення («Бачу, що сьогодні ти зробив ботаₒ... Чи вдалося
    досягти результатів?») — тоді ключ пам'яті (перші 70 симв.) обрізав
    саме питання, і той самий сенс іншими словами вже не розпізнавався."""
    lines = [l.strip() for l in _clean(text).split("\n") if l.strip()]
    for line in reversed(lines):
        if "?" in line and len(line) > 8:
            qpos = line.rfind("?")
            seg = line[:qpos + 1]
            start = 0
            for i in range(len(seg) - 2, -1, -1):
                if seg[i] in ".!?":
                    start = i + 1
                    break
            sentence = seg[start:].strip()
            return (sentence if len(sentence) > 8 else line)[:300]
    return ""


def _q_kind(q: str) -> str:
    low = q.lower()
    for kind, words in _Q_RULES:
        for w in words:
            if w in low:
                return kind
    return "confirm"


def is_question(text: str) -> bool:
    low = _clean(text).lower()
    return any(m in low for m in _ASK_MARKS)


def _key_for(q: str, dedup_key: str = "") -> str:
    """Ключ пам'яті askme. Якщо викликач дав стабільний dedup_key (тема
    тригера, не буквальний текст) — використовуємо його: тоді питання, яке
    AI щоразу формулює іншими словами про ту саму тему, лишається ОДНИМ
    питанням для пам'яті, а не новим щоразу."""
    if dedup_key:
        return ("d|" + str(dedup_key))[:80]
    norm = "".join(ch for ch in q.lower() if ch.isalnum() or ch == " ")
    return ("q|" + norm[:70]).strip()


def build(text: str, tag: str = "", dedup_key: str = ""):
    """Кнопки під це конкретне повідомлення. None → шле без кнопок."""
    body = _clean(text)
    if not body:
        return None
    q = _question_line(body)
    # 1) Питання → варіанти відповіді саме на нього (пам'ять askme)
    if q:
        try:
            import askme as A
            key = _key_for(q, dedup_key)
            rows = A.buttons(q, kind=_q_kind(q), key=key,
                             meta={"summary": _title(body), "desc": body[:300],
                                   "tag": str(tag or "")})
            if rows:
                _log("питання → кнопки «" + _q_kind(q) + "»: " + q[:60])
                return rows
            # уже відповідав або це реклама/трекер — кнопок питання не даємо
        except Exception as e:
            _log("askme skip: " + str(e))
    # 2) Звичайне сповіщення → дії під його вид
    try:
        import react as R
        kind = R.detect(tag, body)
        rows = R.keyboard(kind, title=_title(body), text=body, tag=str(tag or ""))
        if rows:
            _log("сповіщення → кнопки «" + kind + "»")
        return rows
    except Exception as e:
        _log("react skip: " + str(e))
        return None


def _title(body: str) -> str:
    for line in body.split("\n"):
        s = line.strip()
        if len(s) > 3:
            return s[:110]
    return body[:110]


def should_send(text: str, tag: str = "", dedup_key: str = "") -> bool:
    """False → це питання Олег уже закрив (буквально або по суті — навіть
    якщо AI перефразував), не турбуємо його вдруге."""
    body = _clean(text)
    q = _question_line(body)
    if not q:
        return True
    try:
        import askme as A
        key = _key_for(q, dedup_key)
        r = A.answer_of(key)
        if not r and not dedup_key:
            # буквального збігу нема — можливо, AI спитав те саме іншими
            # словами. Шукаємо по суті серед уже закритих питань.
            sim_key = A.answered_similar(q)
            if sim_key:
                r = A.answer_of(sim_key)
        if r:
            _log("це питання вже закрито («" + str(r.get("label")) +
                 "») — не питаю вдруге: " + q[:60])
            return False
    except Exception:
        return True
    return True
