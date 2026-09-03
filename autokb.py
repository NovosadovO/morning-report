# -*- coding: utf-8 -*-
"""autokb.py — кнопки під КОЖНЕ повідомлення бота, доречні його змісту.

Логіка одна для всього, що бот шле:
1. Це питання/пропозиція («Запланувати?», «Це ще актуально?», «Відбулось?») →
   кнопки = варіанти відповіді САМЕ на це питання, через askme (відповідь
   пам'ятається назавжди, те саме більше не питається).
2. Це сповіщення (лист, рахунок, подія, пробіжка, крипта, здоров'я) → кнопки
   дії саме для цього виду, через react (з текстом сповіщення в payload).
3. Питання, на яке Олег уже відповів, повторно не ставиться взагалі.
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
    """Останній рядок-питання — саме на нього мають відповідати кнопки."""
    lines = [l.strip() for l in _clean(text).split("\n") if l.strip()]
    for line in reversed(lines):
        if "?" in line and len(line) > 8:
            return line[:300]
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


def build(text: str, tag: str = ""):
    """Кнопки під це конкретне повідомлення. None → шле без кнопок."""
    body = _clean(text)
    if not body:
        return None
    low_tag = str(tag or "").lower()
    q = "" if (len(body) > _MAX_Q_LEN or
               any(w in low_tag for w in _NEVER_BLOCK)) \
        else _question_line(body)
    # 1) Питання → варіанти відповіді саме на нього (пам'ять askme)
    if q:
        try:
            import askme as A
            key = "q|" + "".join(ch for ch in q.lower() if ch.isalnum() or
                                 ch == " ")[:70].strip()
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


# Довгі тексти й звіти НІКОЛИ не блокуються: там «?» — частина розповіді,
# а не питання до Олега.
_NEVER_BLOCK = ("report", "звіт", "briefing", "брифінг", "digest", "themes",
                "astro", "deep", "pulse", "health", "hcoach", "openmind")
_MAX_Q_LEN = 900


def should_send(text: str, tag: str = "") -> bool:
    """False → це питання Олег уже закрив, не турбуємо його вдруге."""
    body = _clean(text)
    if len(body) > _MAX_Q_LEN:
        return True
    low = str(tag or "").lower()
    if any(w in low for w in _NEVER_BLOCK):
        return True
    q = _question_line(body)
    if not q:
        return True
    try:
        import askme as A
        key = "q|" + "".join(ch for ch in q.lower() if ch.isalnum() or
                             ch == " ")[:70].strip()
        r = A.answer_of(key)
        if r:
            _log("це питання вже закрито («" + str(r.get("label")) +
                 "») — не питаю вдруге: " + q[:60])
            return False
    except Exception:
        return True
    return True
