"""fresh_guard — «тільки свіжі дані» (запит Олега 14.11: «AI написав, що дані про сон
старі!! Завжди тільки свіжі дані у всіх сповіщеннях і звітах»).

Причина збою: AI брав «Сон за сьогодні ще не надходив» з власних попередніх повідомлень
(пам'ять реакцій/питань), хоча в даних сон уже був. Тому:
  1. contradiction(text) — перевірка ГОТОВОГО тексту проти фактів (healthai.analytics):
     якщо текст каже «сон/вага/пульс/… ще не приходив / старі дані», а показник за
     сьогодні Є — повідомлення не йде (outbox.check_request) або перегенеровується.
  2. scrub_prompt(text) — з промпту прибираються рядки-«спогади» про те, що показник
     «ще не приходив» (щоб модель не повторювала власну стару помилку).
Нічого не вигадує: лише порівнює з analytics().
"""
import re

# Фрази про відсутність/застарілість даних (у готовому тексті)
STALE_RE = re.compile(
    r"(ще\s+не\s+(?:приходи\w*|надходи\w*|надійш\w*|з['’]явив\w*|синхронізу\w*)"
    r"|не\s+(?:приходи\w*|надходи\w*|надійш\w*)\b"
    r"|(?:немає|нема)\s+(?:даних\s+)?за\s+сьогодні"
    r"|даних\s+за\s+сьогодні\s+(?:немає|нема)"
    r"|(?:застарі\w+|старі)\s+(?:дані|показник\w*|значення)"
    r"|останн\w+\s+відом\w+)", re.I)

# Вужча форма для чистки промпту — не чіпає службові «СТАРІ ДАНІ» у facts_block
_PROMPT_RE = re.compile(
    r"(ще\s+не\s+(?:приходи\w*|надходи\w*|надійш\w*)|останн\w+\s+відом\w+\s*(?:—|-|значення)?\s*\d{4}-\d{2}-\d{2})", re.I)

METRICS = {
    "sleep": (r"\bсон\b", r"\bсну\b", r"\bсном\b", r"\bспав\b", r"\bсні\b", r"показник\w*\s+сну"),
    "weight": (r"\bваг[аиіуо]\w*",),
    "hr": (r"\bпульс\w*",),
    "hrv": (r"\bhrv\b",),
    "steps": (r"\bкрок\w*",),
    "energy": (r"body\s*battery", r"\bенергі\w*"),
    "stress": (r"\bстрес\w*",),
}
_LABEL = {"sleep": "сон", "weight": "вага", "hr": "пульс", "hrv": "HRV",
          "steps": "кроки", "energy": "Body Battery", "stress": "стрес"}


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?…])\s+|\n+", text or "") if s.strip()]


def _analytics():
    import healthai
    return healthai.analytics(14)


def contradiction(text, a=None):
    """'' якщо ок; інакше рядок-причина: текст стверджує, що показник не приходив, а він є."""
    try:
        if not text or not STALE_RE.search(text):
            return ""
        hits = []
        for s in _sentences(text):
            if not STALE_RE.search(s):
                continue
            low = s.lower()
            for key, pats in METRICS.items():
                if any(re.search(p, low) for p in pats):
                    hits.append(key)
        if not hits:
            return ""
        a = a or _analytics()
        today = a.get("today")
        bad = []
        for key in dict.fromkeys(hits):
            m = a.get(key) or {}
            if m.get("last_day") and m.get("last_day") == today:
                bad.append(f"{_LABEL[key]}: за сьогодні є ({m.get('last')})")
        if bad:
            return "текст каже «не приходив/старі дані», а факти свіжі — " + "; ".join(bad)
    except Exception as e:  # охоронець не має ламати відправку
        try:
            import ai_kit as K
            K.log("fresh_guard", "помилка: " + str(e)[:120])
        except Exception:
            pass
    return ""


_DOWN = re.compile(r"(впа[вл]\w*|знизил\w*|знизив\w*|зменшил\w*|зменшив\w*|просі[вл]\w*|падіння|спад\w*|"
                   r"(?:нижч\w+|менш\w+)\s+(?:за|від|ніж)\s+(?:середн\w+|норм\w+|тижнев\w+))", re.I)
_UP = re.compile(r"(зрі[сл]\w*|вирі[сл]\w*|підвищил\w*|підвищив\w*|збільшил\w*|збільшив\w*|піднялс\w*|піднялась|"
                 r"(?:вищ\w+|більш\w+)\s+(?:за|від|ніж)\s+(?:середн\w+|норм\w+|тижнев\w+))", re.I)
_TOL = 0.03          # менше 3% від середнього — не вважаємо напрямком


def direction_error(text, a=None):
    """'' якщо ок. Ловить «HRV впав до 60 (сер. 53)» — коли 60 > 53, тобто виріс."""
    try:
        if not text:
            return ""
        a = a or _analytics()
        for sent in _sentences(text):
            low = sent.lower()
            down, up = bool(_DOWN.search(sent)), bool(_UP.search(sent))
            if down == up:
                continue                       # нема напрямку або обидва — не чіпаємо
            for key, pats in METRICS.items():
                if not any(re.search(p, low) for p in pats):
                    continue
                m = a.get(key) or {}
                last, avg = m.get("last"), m.get("avg7")
                if last is None or not avg or m.get("last_day") != a.get("today"):
                    continue
                # Перевіряємо лише речення, що називає саме СЬОГОДНІШНЄ значення
                # (тижневі/вчорашні/відсоткові тренди — не чіпаємо: це не про «last»).
                if re.search(r"тижн|вчора|минул|місяц|\d\s*%", low):
                    continue
                nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", sent)]
                tol = max(0.51, abs(float(last)) * 0.005)
                if nums and not any(abs(n - float(last)) <= tol for n in nums):
                    continue
                rel = (float(last) - float(avg)) / abs(float(avg))
                if down and rel > _TOL:
                    return (f"{_LABEL[key]}: текст каже «впав/знизився», а {last} вище за середнє 7 днів {round(avg, 1)}")
                if up and rel < -_TOL:
                    return (f"{_LABEL[key]}: текст каже «зріс/вищий», а {last} нижче за середнє 7 днів {round(avg, 1)}")
    except Exception:
        pass
    return ""


def any_error(text, a=None):
    """Єдина перевірка перед відправкою: застарілість + напрямок."""
    a = a or _safe_analytics()
    return contradiction(text, a) or direction_error(text, a)


def _safe_analytics():
    try:
        return _analytics()
    except Exception:
        return {}


def correction_hint(reason):
    return ("ВИПРАВЛЕННЯ: попередня версія містила неправду про дані: " + reason +
            ". Перепиши, звіряючись із числами в блоці ДАНІ (порівнюй поточне з сер.7д акуратно: "
            "вище середнього = зросло, нижче = впало). Не згадуй відсутність даних.")


def scrub_prompt(text):
    """Прибрати з промпту рядки-спогади «<показник> ще не приходив…» (самозабруднення пам'яті)."""
    try:
        if not text or not _PROMPT_RE.search(text):
            return text
        out, n = [], 0
        for line in text.split("\n"):
            low = line.lower()
            if _PROMPT_RE.search(line) and "!!" not in line and \
               any(re.search(p, low) for pats in METRICS.values() for p in pats):
                n += 1
                continue
            out.append(line)
        return "\n".join(out) if n else text
    except Exception:
        return text
