#!/usr/bin/env python3
"""
ai_brain.py — ЄДИНИЙ МОЗОК: пам'ять + свобода для КОЖНОГО AI-виклику бота.

Проблема, яку вирішує:
  feedback_ctx.build() (як Олег реагував) підмішувався лише у 3 промпти з ~24.
  Головний AI звіту, астро, пошта, коуч — не бачили НІЧОГО з того, що Олег
  відповідав. Тому AI повторював те, що Олег уже відхилив, і питав те, на що
  вже отримав відповідь. Тут це виправлено централізовано: інжект робиться
  в monitor._gem_post, через який ходять УСІ модулі.

Дає AI дві речі:
  1) ПАМ'ЯТЬ  — факти про Олега, його текстові відповіді, натиснуті кнопки,
                що він вимкнув/підтвердив, приховані теми, люди, останні теми
                самого AI (щоб не повторювався).
  2) СВОБОДУ  — дозвіл самому вибирати тему/формат/довжину, не тримати шаблон,
                казати незручне, ставити зустрічні питання, визнавати незнання.

Жорсткі рамки, які лишаються (Олег просив саме так):
  • тільки перевірені дані, нічого не вигадувати; немає даних → сказати прямо;
  • українська мова;
  • не піднімати теми, які він приховав.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TAG = "ai_brain"
MARK = "⁣AIBRAIN⁣"        # невидимий маркер: захист від подвійного інжекту
MEM_MAX = 2600                       # символів пам'яті на промпт
TOPICS_FILE = "ai_last_topics.json"  # про що AI писав останнім часом
ANSWERS_FILE = "ai_answers.json"     # відповіді Олега власними словами
_CACHE = {"ts": None, "text": ""}
_CACHE_TTL = 120  # с
_BUF = []          # буфер note_topic (щоб не писати в storage на кожен виклик)
_BUF_TS = [0.0]
_FLUSH_GAP = 240   # с між записами тем у storage


def _log(m):
    print(f"[{TAG}] {m}", flush=True)


def _now():
    try:
        import ai_kit as K
        return K.now().replace(tzinfo=None)
    except Exception:
        return datetime.now()


def _clean(s, n=150):
    s = str(s or "").replace("\n", " ").strip()
    return s[:n]


# ─── ПАМ'ЯТЬ: ВІДПОВІДІ ОЛЕГА ВЛАСНИМИ СЛОВАМИ ───────────────────────────────

def remember_answer(question: str, answer: str, topic: str = "", source: str = "text"):
    """Запам'ятовує відповідь Олега. Викликається з bot.py на КОЖНЕ його
    текстове повідомлення і з confirm.py на кожне підтвердження."""
    try:
        import ai_kit as K
        import storage
        txt = _clean(answer, 400)
        if not txt or len(txt) < 2:
            return False
        data = storage.load(ANSWERS_FILE, default={}) or {}
        key = K.Dedup.key(f"{topic}|{txt}|{_now().isoformat()}")
        data[key] = {"q": _clean(question, 200), "a": txt,
                     "topic": _clean(topic, 40), "src": source,
                     "ts": _now().isoformat()}
        # тримаємо останні 120 записів
        if len(data) > 120:
            items = sorted(data.items(), key=lambda kv: str(kv[1].get("ts")), reverse=True)
            data = dict(items[:120])
        storage.save(ANSWERS_FILE, data)
        _CACHE["ts"] = None  # інвалідуємо, щоб наступний промпт уже бачив
        return True
    except Exception as e:
        _log(f"remember_answer error: {e}")
        return False


def _answers_block(days=14, limit=10):
    try:
        import storage
        data = storage.load(ANSWERS_FILE, default={}) or {}
        cutoff = _now() - timedelta(days=days)
        rows = []
        for r in data.values():
            if not isinstance(r, dict):
                continue
            try:
                ts = datetime.fromisoformat(str(r.get("ts"))).replace(tzinfo=None)
            except Exception:
                continue
            if ts >= cutoff:
                rows.append((ts, r))
        rows.sort(reverse=True)
        out = []
        for ts, r in rows[:limit]:
            t = f"[{r.get('topic')}] " if r.get("topic") else ""
            out.append(f"{ts.strftime('%d.%m')} {t}«{_clean(r.get('a'), 120)}»")
        return out
    except Exception as e:
        _log(f"answers error: {e}")
        return []


# ─── ПАМ'ЯТЬ: ЩО AI ВЖЕ ПИСАВ (щоб не повторювався) ──────────────────────────

def note_topic(topic: str, gist: str = "", force: bool = False):
    """AI написав про X — фіксуємо, щоб наступний раз не жувати те саме.

    Записи буферизуються в пам'яті і скидаються в storage не частіше ніж раз на
    _FLUSH_GAP секунд — інакше один звіт (20+ AI-викликів) дав би 20 комітів.
    """
    try:
        import ai_kit as K
        import storage
        _BUF.append({"topic": _clean(topic, 60), "gist": _clean(gist, 160),
                     "ts": _now().isoformat()})
        import time as _t
        if not force and (_t.time() - _BUF_TS[0]) < _FLUSH_GAP:
            return True
        _BUF_TS[0] = _t.time()
        data = storage.load(TOPICS_FILE, default={}) or {}
        while _BUF:
            _r = _BUF.pop(0)
            data[K.Dedup.key(f"{_r['topic']}|{_r['ts']}")] = _r
        if len(data) > 60:
            items = sorted(data.items(), key=lambda kv: str(kv[1].get("ts")), reverse=True)
            data = dict(items[:60])
        storage.save(TOPICS_FILE, data)
        return True
    except Exception as e:
        _log(f"note_topic error: {e}")
        return False


def _recent_topics(hours=36, limit=8):
    try:
        import storage
        data = storage.load(TOPICS_FILE, default={}) or {}
        cutoff = _now() - timedelta(hours=hours)
        rows = []
        for r in data.values():
            if not isinstance(r, dict):
                continue
            try:
                ts = datetime.fromisoformat(str(r.get("ts"))).replace(tzinfo=None)
            except Exception:
                continue
            if ts >= cutoff:
                rows.append((ts, r))
        rows.sort(reverse=True)
        return [f"{r.get('topic')}" + (f" ({_clean(r.get('gist'), 60)})" if r.get("gist") else "")
                for _, r in rows[:limit]]
    except Exception as e:
        _log(f"recent_topics error: {e}")
        return []


# ─── ПАМ'ЯТЬ: ЩО ОЛЕГ ВИМКНУВ / ПІДТВЕРДИВ ───────────────────────────────────

def _confirm_block(days=14, limit=8):
    try:
        import storage
        data = storage.load("confirm_log.json", default={}) or {}
        cutoff = _now() - timedelta(days=days)
        rows = []
        for r in data.values():
            if not isinstance(r, dict):
                continue
            try:
                ts = datetime.fromisoformat(str(r.get("ts"))).replace(tzinfo=None)
            except Exception:
                continue
            if ts >= cutoff:
                rows.append((ts, r))
        rows.sort(reverse=True)
        out = []
        for ts, r in rows[:limit]:
            verb = "вимкнув" if r.get("answer") == "yes" else "передумав вимикати"
            out.append(f"{ts.strftime('%d.%m')} {verb}: {_clean(r.get('subject'), 60)}")
        return out
    except Exception as e:
        _log(f"confirm error: {e}")
        return []


# ─── ЗБІРКА БЛОКУ ПАМ'ЯТІ ────────────────────────────────────────────────────

def memory_block(max_chars: int = MEM_MAX) -> str:
    """Компактна пам'ять для будь-якого промпту. Кешується на 2 хв."""
    now = _now()
    if _CACHE["ts"] and (now - _CACHE["ts"]).total_seconds() < _CACHE_TTL:
        return _CACHE["text"]

    parts = []

    # 1. факти про Олега (ai_notes)
    try:
        import ai_notes
        n = ai_notes.get_notes_context(max_notes=12)
        if n and str(n).strip():
            parts.append("ФАКТИ ПРО ОЛЕГА (він сам сказав): " + _clean(n, 700))
    except Exception as e:
        _log(f"ai_notes error: {e}")

    # 1.5 ПОВНА ПАМ'ЯТЬ: усе, що Олег писав і натискав (recall.py)
    try:
        import recall
        rc = recall.block(max_chars=2200)
        if rc and rc.strip():
            parts.append(rc.strip())
    except Exception as e:
        _log(f"recall error: {e}")

    # 2. його відповіді власними словами
    a = _answers_block()
    if a:
        parts.append("ЙОГО ВІДПОВІДІ (дослівно, найсвіжіші перші): " + " | ".join(a))

    # 3. реакції на кнопки / приховані теми (уже готовий модуль)
    try:
        import feedback_ctx
        fb = feedback_ctx.build(days=7, max_chars=1100)
        if fb and fb.strip():
            parts.append(fb.replace("\n📌 ЗВОРОТНИЙ ЗВ'ЯЗОК ОЛЕГА (реальні дані, "
                                    "спирайся на них, не вигадуй): \n", "").strip())
    except Exception as e:
        _log(f"feedback_ctx error: {e}")

    # 4. що він вимикав через підтвердження
    c = _confirm_block()
    if c:
        parts.append("ЩО ВІН ВИМИКАВ/ПІДТВЕРДЖУВАВ: " + " | ".join(c))

    # 5. люди з його оточення
    try:
        import people_memory
        for fn in ("get_context_for_ai", "get_people_context", "context_for_ai"):
            f = getattr(people_memory, fn, None)
            if callable(f):
                p = f()
                if p and str(p).strip():
                    parts.append("ЛЮДИ: " + _clean(p, 400))
                break
    except Exception as e:
        _log(f"people_memory error: {e}")

    # 6. про що AI вже писав — не повторюватись
    t = _recent_topics()
    if t:
        parts.append("ТИ ВЖЕ ПИСАВ ПРО ЦЕ (36 год) — НЕ повторюй тими самими "
                     "словами, дай новий кут або іншу тему: " + " | ".join(t))

    if not parts:
        text = ""
    else:
        text = ("\n\n━━━ ПАМ'ЯТЬ ПРО ОЛЕГА (реальні збережені дані) ━━━\n"
                + "\n".join(f"• {p}" for p in parts))[:max_chars]

    _CACHE.update({"ts": now, "text": text})
    return text


# ─── СВОБОДА ─────────────────────────────────────────────────────────────────

FREEDOM = (
    "\n\n━━━ ТВОЯ СВОБОДА ━━━\n"
    "Ти живий розумний співрозмовник Олега, а не генератор шаблонів. Тобі дозволено:\n"
    "• самому вибрати, ПРО ЩО писати — бери те, що зараз реально важливе, "
    "навіть якщо про це не просили; неважливе пропускай, не заповнюй розділи ради структури;\n"
    "• самому вибрати ФОРМАТ і ДОВЖИНУ — коротка репліка на 2 рядки, якщо новин мало, "
    "і розгорнутий розбір, якщо є про що говорити; не тримайся заголовків силою;\n"
    "• мати думку і не погоджуватись — якщо бачиш, що Олег робить собі гірше "
    "(режим сну, вага, витрати, відкладання), скажи прямо, без ватного тону;\n"
    "• ставити ЙОМУ зустрічні питання, коли для доброї поради не хватає даних — "
    "одне конкретне питання краще за абзац здогадів;\n"
    "• казати «не знаю» і «дані застаріли» — це сильніше за вигадану цифру;\n"
    "• пам'ятати попередні відповіді Олега і посилатись на них ("
    "«ти казав, що…», «минулого тижня ти вирішив…»), розвивати ту саму думку далі;\n"
    "• змінювати тон під ситуацію: підтримати, коли важко; штовхнути, коли лінь; "
    "коротко по факту, коли він у зміні.\n"
    "ЧОГО НЕ РОБИТИ: не вигадувати числа, дати й події — тільки з даних вище; "
    "немає даних — так і скажи; не піднімати теми, які він приховав; "
    "не повторювати попереднє повідомлення; писати українською."
)


# ─── ЄДИНА ТОЧКА ВХОДУ ───────────────────────────────────────────────────────

def wrap(prompt: str, freedom: bool = True, memory: bool = True) -> str:
    """Додає пам'ять і свободу до промпту. Ідемпотентно (маркер MARK)."""
    try:
        p = str(prompt or "")
        if MARK in p:
            return p
        add = ""
        if memory:
            add += memory_block()
        if freedom:
            add += FREEDOM
        if not add:
            return p
        return p + add + MARK
    except Exception as e:
        _log(f"wrap error: {e}")
        return prompt


def is_json_prompt(prompt: str) -> bool:
    """Промпти, що чекають СТРОГИЙ JSON, не отримують блок свободи —
    інакше AI почне писати прозу і парсер зламається."""
    p = str(prompt or "").lower()
    keys = ["тільки валідний json", "валідний json", "json-масив", "json масив",
            "поверни json", "відповідь — json", 'format: json', "json format",
            "тільки json", "json об'єкт", "respond with json"]
    return any(k in p for k in keys)


def report() -> str:
    """/мозок — що саме AI про нього пам'ятає."""
    m = memory_block()
    if not m.strip():
        return ("🧠 <b>ПАМ'ЯТЬ AI</b>\n\nПоки порожньо. Вона наповнюється сама: "
                "твої відповіді, натиснуті кнопки, підтвердження, нотатки.")
    body = m.replace("━━━ ПАМ'ЯТЬ ПРО ОЛЕГА (реальні збережені дані) ━━━", "").strip()
    try:
        import ai_kit as K
        body = K.esc(body)
    except Exception:
        pass
    return ("🧠 <b>ПАМ'ЯТЬ AI — ЩО Я ПРО ТЕБЕ ЗНАЮ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n" + body[:3500] +
            "\n\n<i>Це підмішується в КОЖЕН мій AI-запит.</i>")


if __name__ == "__main__":
    print(report())
