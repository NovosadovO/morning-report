"""
truth.py — ПЕРЕВІРКА НА ДОСТОВІРНІСТЬ кожної AI-відповіді перед відправкою.

Запит Олега (28.08): «Завжди хай бот чи АІ перевіряє на достовірність все що він
написав чи хоче написати. Мені потрібна завжди реальні дані, реальна інформація,
актуальне все, а не видумки чи стара інформація».

Як працює (без вигадок, детерміновано + один AI-фактчек):
  1. audit(prompt, answer) — механічна перевірка ТЕКСТУ проти ДАНИХ промпту:
     • числа, яких немає в даних (з допуском на округлення й арифметику);
     • часові слова («щойно», «нещодавно», «вперше») без дати поруч;
     • дати з майбутнього, подані як факт;
     • «свіжо» про дані, які в промпті позначені як старі/кеш/stale;
     • вигадані посилання (http) — у даних їх не було.
  2. Якщо проблеми є → recheck(): один додатковий виклик Gemini, який бачить
     і дані, і чернетку, і список претензій, і мусить переписати ТІЛЬКИ неправду.
  3. Повторний audit. Якщо неправда лишилась — sanitize(): проблемні речення
     вирізаються, а не відправляються Олегу.
  4. Статистика — truth_stats.json (щоб було видно, скільько разів AI ловили).

Викликається централізовано з monitor._gem_post → накриває ВСІ модули.
"""

import json
import re
import time

TAG = "truth"
MARK = "<!--TRUTH-->"
STATS_FILE = "truth_stats.json"

# скільки речень максимум вирізаємо, перш ніж відкинути відповідь цілком
_MAX_CUT = 4

_TIME_WORDS = (
    "щойно", "нещодавно", "тільки що", "тільки-но", "щoйно",
    "вперше", "буквально зараз", "цими днями", "днями",
    "свіжа новина", "свіжий", "новина дня", "тільки почалось",
)
_FRESH_WORDS = ("зараз", "актуальн", "свіж", "у реальному часі", "наразі")
_STALE_MARKS = ("stale", "кеш", "cache", "старі дані", "дані від",
                "останні відомі", "недоступн")


def _log(msg):
    print("[" + TAG + "] " + str(msg), flush=True)


def _K():
    import ai_kit
    return ai_kit


# ─── ЧИСЛА ───────────────────────────────────────────────────────────────────

_NUM_RE = re.compile(r"(?<![\w/])(\d{1,3}(?:[  ]\d{3})+|\d+(?:[.,]\d+)?)")


def _norm_num(s):
    s = s.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _numbers(text):
    out = []
    for m in _NUM_RE.finditer(text or ""):
        v = _norm_num(m.group(1))
        if v is not None:
            out.append(v)
    return out


def _num_supported(v, pool):
    """Чи є число v у даних (з допуском на округлення й прості похідні)."""
    if v in pool:
        return True
    for p in pool:
        if p == 0:
            continue
        # округлення: 83.05 → 83, 12194 → 12.2к, 7.17 → 7.2
        if abs(p - v) <= max(0.05, abs(p) * 0.005):
            return True
        # відсоток від числа з даних, різниця, ділення на 60/1000/24
        for k in (60.0, 1000.0, 24.0, 7.0, 30.0):
            if abs(p / k - v) <= max(0.05, abs(p / k) * 0.01):
                return True
        if abs(abs(p - v) - 0) < 1e-9:
            return True
    # різниці й суми пар (AI законно рахує «мінус 2.4 кг», «на 3 дні більше»)
    for a in pool:
        for b in pool:
            for cand in (a - b, a + b, a * b / 100.0):
                if abs(cand - v) <= max(0.05, abs(cand) * 0.01):
                    return True
    return False


def _sentences(text):
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text or "")
    return [p for p in parts if p and p.strip()]


def _has_date(s):
    return bool(re.search(r"\d{1,2}[.\-/]\d{1,2}([.\-/]\d{2,4})?|\d{4}-\d{2}-\d{2}"
                          r"|\d+\s*(дн|дні|днів|тижн|міс|рок|год|хв)", s, re.I))


# ─── ЗМІНА: не брехати про стан поточної зміни ───────────────────────────────
_END_RE = re.compile(r"(завершил|закінчил|скінчил|відпрацював|відробив|позаду)", re.I)
_PAST_RE = re.compile(r"(щойно з|щойно після|після (?:ранкової|ранньої|нічної|цієї) зміни,? (?:ти|тож|час))", re.I)
_HOME_RE = re.compile(r"\bти (?:зараз |вже )?(?:вдома|удома)\b", re.I)
_OLD_RE = re.compile(r"(вчора|учора|минул|вчорашн|позавчора)", re.I)
_KIND_W = {"early": ("ранн", "ранк"), "night": ("нічн", "ніч")}


def _shift_lie(sentence, w):
    """True, якщо речення суперечить тому, що зміна ТРИВАЄ просто зараз."""
    if not w or w.get("phase") != "in_progress" or w.get("state") != "work":
        return False
    sl = sentence.lower()
    if "змін" not in sl and not _HOME_RE.search(sl):
        return False
    if _HOME_RE.search(sl):
        return True
    if _OLD_RE.search(sl):
        return False
    cur = _KIND_W.get(w.get("shift"), ())
    oth = [x for k, v in _KIND_W.items() if k != w.get("shift") for x in v]
    has_cur = any(x in sl for x in cur)
    has_oth = any(x in sl for x in oth)
    if has_oth and not has_cur:
        return False          # мова про іншу зміну (напр. нічну вчора)
    if re.search(r"(буде|завершиться|закінчиться|коли|до кінця|після того)", sl):
        return False          # майбутнє
    return bool(_END_RE.search(sl) or _PAST_RE.search(sl))


def _where_safe():
    try:
        import nowctx
        return nowctx.where()
    except Exception:
        return None


# ─── АУДИТ ───────────────────────────────────────────────────────────────────

def audit(prompt, answer, tag="gem"):
    """Список претензій до тексту. Порожній список = текст спирається на дані."""
    issues = []
    if not answer or len(answer.strip()) < 20:
        return issues
    p_low = (prompt or "").lower()
    a_low = answer.lower()
    pool = _numbers(prompt)

    # 1. вигадані числа
    bad_nums = []
    for s in _sentences(answer):
        for m in _NUM_RE.finditer(s):
            v = _norm_num(m.group(1))
            if v is None:
                continue
            # дрібні числа (списки, «3 дії», години) не перевіряємо — шум
            if v < 10 and float(v).is_integer():
                continue
            if re.search(r"\d{1,2}[:.]\d{2}", s):
                continue
            if not _num_supported(v, pool):
                bad_nums.append(m.group(1))
    bad_nums = list(dict.fromkeys(bad_nums))[:6]
    if bad_nums:
        issues.append("числа, яких НЕМА в даних: " + ", ".join(bad_nums))

    # 2. «щойно» без дати
    for s in _sentences(answer):
        sl = s.lower()
        for w in _TIME_WORDS:
            if w in sl and not _has_date(s):
                issues.append("часова прив'язка без дати: «" + s.strip()[:90] + "»")
                break
        if len(issues) > 8:
            break

    # 3. видає старі дані за свіжі
    if any(m in p_low for m in _STALE_MARKS) and any(w in a_low for w in _FRESH_WORDS):
        if not any(k in a_low for k in ("стар", "кеш", "недоступ", "останні відомі")):
            issues.append("дані в промпті позначені як старі/кеш, а текст подає їх "
                          "як актуальні — треба прямо сказати, що дані не свіжі")

    # 3b. неправда про стан зміни (рання зміна триває, а текст каже «завершилась»)
    _w = _where_safe()
    for s in _sentences(answer):
        if _shift_lie(s, _w):
            issues.append("зміна ТРИВАЄ зараз (Олег на роботі), а текст каже інакше: «"
                          + s.strip()[:90] + "»")
            break

    # 4. вигадані посилання
    for m in re.finditer(r"https?://[^\s<>\)\"]+", answer):
        if m.group(0)[:40] not in (prompt or ""):
            issues.append("посилання, якого не було в даних: " + m.group(0)[:60])
            break

    return issues[:8]


# ─── ПЕРЕПИТ У МОДЕЛІ ────────────────────────────────────────────────────────

_FIX_TMPL = (
    "Нижче ДАНІ, потім ЧЕРНЕТКА твоєї відповіді, потім ПРЕТЕНЗІЇ фактчекера.\n"
    "Твоє завдання: віддати ту саму відповідь, але БЕЗ жодного твердження, "
    "яке не підтверджується ДАНИМИ.\n"
    "ПРАВИЛА:\n"
    "• Числа, дати, суми, назви — тільки ті, що є в ДАНИХ. Немає — прибери "
    "твердження або напиши «даних немає»;\n"
    "• «щойно/нещодавно/вперше/новий» — тільки якщо в ДАНИХ є дата, з якої це "
    "видно, і ти цю дату називаєш;\n"
    "• якщо дані старі або з кешу — так і скажи, скільки їм днів;\n"
    "• не додавай нових фактів, не вигадуй джерел і посилань;\n"
    "• стиль, мову, структуру і довжину збережи. Віддай ЛИШЕ готовий текст, "
    "без пояснень, що ти виправив.\n\n"
    "━━━ ДАНІ ━━━\n{data}\n\n"
    "━━━ ЧЕРНЕТКА ━━━\n{draft}\n\n"
    "━━━ ПРЕТЕНЗІЇ ━━━\n{issues}\n"
)


_META_RE = re.compile(
    r"^\s*(?:\W{0,3}\s*)?(?:нижче|ось|далі|наведено|надаю|виправлен\w+|оновлен\w+|"
    r"перероблен\w+|скоригован\w+|here\s+is|below)\b[^\n]*"
    r"(?:відповід|текст|версі|варіант|чернетк|answer|text|version|draft)[^\n]*\n+",
    re.I)


def strip_meta(t):
    """14.11: у чат пішло «Нижче надано оновлену відповідь, яка відповідає наданим ДАНИМ…» —
    модель-коректор додала пояснення, хоча просили лише текст. Зрізаємо такі преамбули."""
    t = (t or "").strip()
    for _ in range(2):
        m = _META_RE.match(t + "\n")
        if not m:
            break
        t = t[m.end():].strip()
    return t


def recheck(prompt, answer, issues, tag="gem"):
    """Один AI-прохід: модель сама виправляє те, що не тримається на даних."""
    try:
        import monitor as _m
        import os
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return ""
        p = _FIX_TMPL.format(
            data=(prompt or "")[-6000:],
            draft=(answer or "")[:6000],
            issues="\n".join("• " + i for i in issues),
        ) + MARK
        body = json.dumps({
            "contents": [{"parts": [{"text": p}]}],
            "generationConfig": {"maxOutputTokens": 2600, "temperature": 0.2,
                                 "thinkingConfig": {"thinkingBudget": 0}},
        }).encode()
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               "gemini-2.5-flash:generateContent?key=" + key)
        resp = _m._gem_post(url, body, timeout=60, tag=tag + "/truthfix",
                            max_retries=2)
        return strip_meta(resp["candidates"][0]["content"]["parts"][0]["text"] or "")
    except Exception as e:
        _log("recheck error: " + str(e))
        return ""


# ─── ВИРІЗАННЯ НЕПРАВДИ ──────────────────────────────────────────────────────

def sanitize(prompt, answer):
    """Ріже речення, які не тримаються на даних. Повертає (текст, скільки зрізано)."""
    pool = _numbers(prompt)
    keep, cut = [], 0
    _w = _where_safe()
    for s in _sentences(answer):
        bad = False
        if _shift_lie(s, _w):
            cut += 1          # брехня про зміну ріжеться без ліміту
            continue
        for m in _NUM_RE.finditer(s):
            v = _norm_num(m.group(1))
            if v is None or (v < 10 and float(v).is_integer()):
                continue
            if re.search(r"\d{1,2}[:.]\d{2}", s):
                continue
            if not _num_supported(v, pool):
                bad = True
                break
        if not bad:
            sl = s.lower()
            if any(w in sl for w in _TIME_WORDS) and not _has_date(s):
                bad = True
        if bad and cut < _MAX_CUT:
            cut += 1
            continue
        keep.append(s)
    return (" ".join(keep).strip(), cut)


# ─── СТАТИСТИКА ──────────────────────────────────────────────────────────────

def _bump(tag, kind):
    try:
        K = _K()
        st = K.load(STATS_FILE, default={}) or {}
        day = K.today_str()
        d = st.setdefault(day, {})
        d[kind] = int(d.get(kind, 0)) + 1
        t = st.setdefault("by_tag", {}).setdefault(str(tag), {})
        t[kind] = int(t.get(kind, 0)) + 1
        last = st.setdefault("last", [])
        st["last"] = last[-19:]
        for k in list(st.keys()):
            if re.match(r"^\d{4}-\d{2}-\d{2}$", k) and k < day[:8] + "01":
                pass
        K.save(STATS_FILE, st)
    except Exception as e:
        _log("stats error: " + str(e))


def _note_case(tag, issues, action):
    try:
        K = _K()
        st = K.load(STATS_FILE, default={}) or {}
        last = st.setdefault("last", [])
        last.append({"at": time.strftime("%Y-%m-%d %H:%M"), "tag": str(tag),
                     "action": action, "issues": issues[:4]})
        st["last"] = last[-20:]
        K.save(STATS_FILE, st)
    except Exception:
        pass


def stats_text():
    """Звіт для Олега: скільки разів AI ловили на неправді."""
    try:
        K = _K()
        st = K.load(STATS_FILE, default={}) or {}
    except Exception as e:
        return "⚠️ Статистика недоступна: " + str(e)
    if not st:
        return "🔍 <b>ФАКТЧЕК</b>\n\nЩе жодної перевірки не записано."
    days = sorted([k for k in st if re.match(r"^\d{4}-\d{2}-\d{2}$", k)])[-7:]
    lines = ["🔍 <b>ФАКТЧЕК AI — останні дні</b>", ""]
    tot = {"checked": 0, "issues": 0, "fixed": 0, "cut": 0}
    for d in days:
        r = st[d]
        for k in tot:
            tot[k] += int(r.get(k, 0) or 0)
        lines.append(d + ": перевірено " + str(r.get("checked", 0))
                     + ", з проблемами " + str(r.get("issues", 0))
                     + ", виправлено " + str(r.get("fixed", 0))
                     + ", зрізано " + str(r.get("cut", 0)))
    lines.append("")
    lines.append("<b>Разом за " + str(len(days)) + " дн.:</b> перевірено "
                 + str(tot["checked"]) + ", зловлено неправди "
                 + str(tot["issues"]) + ", виправлено " + str(tot["fixed"])
                 + ", вирізано " + str(tot["cut"]))
    last = st.get("last") or []
    if last:
        lines.append("")
        lines.append("<b>Останні випадки:</b>")
        for c in last[-5:]:
            lines.append("• " + c.get("at", "") + " " + c.get("tag", "")
                         + " → " + c.get("action", "")
                         + ": " + "; ".join(c.get("issues", []))[:160])
    lines.append("")
    lines.append("Перевіряється КОЖНА AI-відповідь: числа проти даних, "
                 "часові прив'язки, свіжість, посилання.")
    return "\n".join(lines)


# ─── ГОЛОВНА ТОЧКА ВХОДУ ─────────────────────────────────────────────────────

def verify(prompt, answer, tag="gem"):
    """
    Повертає перевірений текст. Ніколи не кидає виняток — при будь-якій
    проблемі віддає оригінал (краще текст, ніж тиша).
    """
    try:
        if not answer or MARK in (prompt or ""):
            return answer
        issues = audit(prompt, answer, tag)
        _bump(tag, "checked")
        if not issues:
            return answer
        _bump(tag, "issues")
        _log(str(tag) + ": " + str(len(issues)) + " претензій → " + "; ".join(issues)[:300])

        # ЕКОНОМІЯ КРЕДИТІВ: спершу безкоштовний шлях — вирізати 1-2 проблемні
        # речення. Якщо після цього аудит чистий і текст лишився змістовним,
        # другий виклик Gemini (recheck, ~12k токенів) не потрібен.
        try:
            _cl0, _cut0 = sanitize(prompt, answer)
            if _cut0 and _cut0 <= 2 and len(_cl0) > 160 and not audit(prompt, _cl0, tag):
                _bump(tag, "cut")
                _note_case(tag, issues, "зрізано " + str(_cut0) + " реч. без AI")
                _log(str(tag) + ": ✂️ безкоштовне зрізання " + str(_cut0) + " реч.")
                return _cl0
        except Exception as _e0:
            _log("free-cut skip: " + str(_e0))

        fixed = recheck(prompt, answer, issues, tag)
        if fixed and len(fixed) > 60:
            left = audit(prompt, fixed, tag)
            if not left:
                _bump(tag, "fixed")
                _note_case(tag, issues, "виправлено моделлю")
                _log(str(tag) + ": ✅ виправлено після фактчеку")
                return fixed
            if len(left) < len(issues):
                answer = fixed
                issues = left

        clean, cut = sanitize(prompt, answer)
        if cut and len(clean) > 120:
            _bump(tag, "cut")
            _note_case(tag, issues, "зрізано " + str(cut) + " реч.")
            _log(str(tag) + ": ✂️ зрізано " + str(cut) + " непідтверджених речень")
            return clean
        _note_case(tag, issues, "залишено з попередженням")
        _log(str(tag) + ": ⚠️ неправду не вдалось прибрати — лишаю з поміткою")
        return answer
    except Exception as e:
        _log("verify error: " + str(e))
        return answer
