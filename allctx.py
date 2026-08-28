"""
allctx.py — ПОВНИЙ ДОСТУП AI ДО ВСЬОГО.

Запит Олега (28.08): «Підключи АІ до всього щоб він мав повний доступ».

ПРОБЛЕМА, ЯКА БУЛА (не припущення — це видно в коді):
кожен модуль давав AI тільки свій вузький шматок. astro_ai бачив лише транзити,
hcoach — лише здоров'я, openmind — лише ринок, email-AI — лише листи. Тому AI
не міг зв'язати речі: нічна зміна + поганий сон + падіння BTC + дедлайн у
календарі. Він фізично не бачив усього одночасно.

ЩО РОБИТЬ ЦЕЙ МОДУЛЬ:
збирає ЖИВИЙ зріз ВСІХ підсистем бота в один блок і вкидає його в КОЖЕН
AI-запит (через monitor._gem_post, як nowctx/react/variety). Кожне джерело —
з поміткою свіжості й станом (ok / порожньо / помилка). Джерело недоступне —
так і написано, щоб AI не вигадував.

ДЖЕРЕЛА (17): календар · зміни · здоров'я · вага · сон · оцінка дня · Strava ·
кроки · ліки · звички · пошта · крипто ТОП-20 · портфель · гроші/рахунки ·
дедлайни · дати/люди · нотатки · астро · трафік Кошице · відкриті цикли ·
реакції Олега · фактчек.

API:
    snapshot(force=False) -> str    # весь зріз (кеш 8 хв)
    block()               -> str    # блок для промпту
    inject(body, tag)     -> bytes  # інжект у Gemini-запит (ідемпотентно)
    ask(question)         -> str    # вільне питання по ВСІХ даних
    report()              -> str    # /доступ — що бачить AI і що зламано
"""

import json
import time

TAG = "allctx"
MARK = "⁣ALLCTX⁣"
CACHE_TTL = 480          # 8 хв: щоб не палити API на кожному виклику
MAX_CHARS = 5200         # стеля блоку, щоб не рвати промпт

_CACHE = {"at": 0, "text": "", "status": {}}


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


def _cut(s, n=520):
    s = " ".join(str(s or "").split())
    return s[:n] + ("…" if len(s) > n else "")


# ─── ДЖЕРЕЛА ─────────────────────────────────────────────────────────────────
# Кожне: (ключ, підпис, функція). Функція повертає текст або "" (порожньо).

def _src_calendar():
    import context
    d = context.get_calendar_events(days=7)
    if isinstance(d, dict):
        return _cut(d.get("text") or json.dumps(d, ensure_ascii=False), 900)
    return _cut(d, 900)


def _src_shift():
    import ai_kit as K
    sm = K.shift_map(7)
    if not sm:
        return ""
    return "; ".join(k + ": " + str(v) for k, v in sorted(sm.items()))


def _src_health():
    import healthai
    a = healthai.analytics(14)
    return _cut(healthai.facts_block(a), 900)


def _src_score():
    import hcoach
    h = hcoach.score_history(14)
    if not h:
        return ""
    return "; ".join(d + "=" + str(v) for d, v in h[-10:])


def _src_weight():
    import context
    return _cut(context._get_weight_context(), 300)


def _src_sleep():
    import sleep as _sl
    return _cut(_sl.format_sleep_week_block(), 500)


def _src_strava():
    import strava
    if strava.api_blocked():
        return "Strava API заблоковано: " + _cut(strava.app_inactive_reason(), 160) \
               + " → дані лише з кешу, свіжих НЕМА"
    return _cut(strava.format_strava_block(), 600)


def _src_meds():
    import context
    return _cut(context._get_meds_context(), 300)


def _src_habits():
    import context
    return _cut(context._get_habits_context(), 300)


def _src_emails():
    import context
    return _cut(context._get_recent_emails_context(), 900)


def _src_crypto():
    import openmind
    return _cut(openmind.crypto_block(), 800)


def _src_portfolio():
    import portfolio
    return _cut(portfolio.format_portfolio_block(), 500)


def _src_money():
    import money
    return _cut(money.line(), 300)


def _src_deadlines():
    import deadlines_watcher as dw
    return _cut(dw.upcoming(), 500)


def _src_dates():
    import dates_book
    return _cut(dates_book.report_block(), 400)


def _src_notes():
    import ai_notes
    return _cut(ai_notes.get_notes_context(), 700)


def _src_astro():
    import astro
    return _cut(astro.ingress_facts(), 700)


def _src_traffic():
    import traffic_kosice
    return _cut(traffic_kosice.format_traffic_report(), 350)


def _src_openloop():
    import openloop
    return _cut(openloop.report(), 400)


def _src_react():
    import react
    return _cut(react.block(), 400)


def _src_truth():
    import truth
    return _cut(truth.stats_text(), 400)


SOURCES = [
    ("calendar", "📅 КАЛЕНДАР (7 днів)", _src_calendar),
    ("shift", "🏭 ЗМІНИ (7 днів)", _src_shift),
    ("health", "🩺 ЗДОРОВ'Я (14 днів)", _src_health),
    ("score", "🏅 ОЦІНКА ДНЯ", _src_score),
    ("weight", "⚖️ ВАГА", _src_weight),
    ("sleep", "😴 СОН", _src_sleep),
    ("strava", "🏃 STRAVA", _src_strava),
    ("meds", "💊 ЛІКИ", _src_meds),
    ("habits", "🔁 ЗВИЧКИ", _src_habits),
    ("emails", "📧 ПОШТА", _src_emails),
    ("crypto", "🪙 КРИПТО (ринок)", _src_crypto),
    ("portfolio", "💼 ПОРТФЕЛЬ", _src_portfolio),
    ("money", "💰 ГРОШІ / РАХУНКИ", _src_money),
    ("deadlines", "⏳ ДЕДЛАЙНИ", _src_deadlines),
    ("dates", "🎂 ДАТИ / ЛЮДИ", _src_dates),
    ("notes", "🧠 НОТАТКИ ПРО ОЛЕГА", _src_notes),
    ("astro", "🔭 АСТРО (фактичні дати)", _src_astro),
    ("traffic", "🚗 ТРАФІК КОШИЦЕ", _src_traffic),
    ("openloop", "🔓 ВІДКРИТІ ЦИКЛИ", _src_openloop),
    ("react", "👍 РЕАКЦІЇ ОЛЕГА", _src_react),
    ("truth", "🔍 ФАКТЧЕК", _src_truth),
]


# ─── ЗБІР ────────────────────────────────────────────────────────────────────

def collect(keys=None):
    """Повертає (текст, статус). Статус: ok / empty / error:<причина>."""
    rows, status = [], {}
    for key, label, fn in SOURCES:
        if keys and key not in keys:
            continue
        t0 = time.time()
        try:
            txt = fn() or ""
        except Exception as e:
            status[key] = "error: " + str(e)[:80]
            rows.append(label + ": ⚠️ джерело недоступне (" + str(e)[:60]
                        + ") — НЕ вигадуй ці дані")
            continue
        ms = int((time.time() - t0) * 1000)
        if not str(txt).strip():
            status[key] = "empty"
            rows.append(label + ": даних немає")
        else:
            status[key] = "ok(" + str(ms) + "ms)"
            rows.append(label + ":\n" + str(txt))
    return ("\n\n".join(rows), status)


def snapshot(force=False):
    now = time.time()
    if not force and _CACHE["text"] and now - _CACHE["at"] < CACHE_TTL:
        return _CACHE["text"]
    text, status = collect()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n…(зріз обрізано по стелі)"
    _CACHE.update({"at": now, "text": text, "status": status})
    ok = sum(1 for v in status.values() if v.startswith("ok"))
    _log("зріз оновлено: " + str(ok) + "/" + str(len(status)) + " джерел, "
         + str(len(text)) + " симв.")
    return text


HEAD = (
    "━━━ ПОВНИЙ ДОСТУП ДО ДАНИХ ОЛЕГА (живий зріз усіх підсистем) ━━━\n"
    "Це РЕАЛЬНІ дані з його календаря, пошти, здоров'я, ринку, фінансів, "
    "нагадувань і нотаток. Використовуй їх ВСІ, а не лише свою тему: "
    "зв'язуй між собою (зміна + сон + гроші + дедлайн + ринок) і давай висновок, "
    "який без цього зв'язку неможливий.\n"
    "Де написано «даних немає» або «джерело недоступне» — так і кажи, "
    "НЕ вигадуй і не бери зі своєї пам'яті.\n"
)


def block():
    s = snapshot()
    if not s:
        return ""
    return "\n\n" + HEAD + s + "\n━━━ КІНЕЦЬ ЗРІЗУ ━━━\n"


# ─── ІНЖЕКТ ──────────────────────────────────────────────────────────────────

def inject(body_bytes, tag="gem"):
    """Вкидає повний зріз у Gemini-запит. Ідемпотентно, JSON-промпти пропускає."""
    try:
        b = json.loads(body_bytes.decode())
        p = b["contents"][0]["parts"][0]["text"]
        if MARK in p:
            return body_bytes
        try:
            import ai_brain
            if ai_brain.is_json_prompt(p):
                return body_bytes
        except Exception:
            pass
        blk = block()
        if not blk:
            return body_bytes
        b["contents"][0]["parts"][0]["text"] = p + blk + MARK
        return json.dumps(b).encode()
    except Exception as e:
        _log("inject skipped for " + str(tag) + ": " + str(e))
        return body_bytes


# ─── ВІЛЬНЕ ПИТАННЯ ПО ВСІХ ДАНИХ ────────────────────────────────────────────

def ask(question):
    """/аі <питання> — відповідь на основі ВСІХ даних (з фактчеком truth.py)."""
    q = (question or "").strip()
    if not q:
        return ("🧠 Напиши питання після команди. Я маю доступ до всього: "
                "календар, зміни, пошта, здоров'я, сон, вага, Strava, крипто, "
                "портфель, гроші, дедлайни, нотатки, астро, трафік.")
    try:
        import ai_kit as K
        prompt = ("Питання Олега: " + q + "\n\n"
                  "Відповідай КОНКРЕТНО, по його реальних даних (вони нижче в "
                  "зрізі). Якщо потрібних даних немає — прямо скажи, чого саме "
                  "немає, і не вигадуй. Якщо з даних видно проблему або "
                  "можливість — скажи про неї, навіть якщо не питали.")
        out = K.gemini_text(prompt, max_tokens=1400, temperature=0.6, tag="allctx_ask")
        return out or "⚠️ AI не відповів. Спробуй ще раз."
    except Exception as e:
        return "⚠️ Помилка: " + str(e)[:200]


# ─── /доступ ─────────────────────────────────────────────────────────────────

def report():
    text, status = collect()
    ok = [k for k, v in status.items() if v.startswith("ok")]
    empty = [k for k, v in status.items() if v == "empty"]
    err = {k: v for k, v in status.items() if v.startswith("error")}
    lines = ["🔌 <b>ДОСТУП AI ДО ДАНИХ</b>", ""]
    lines.append("Джерел підключено: <b>" + str(len(status)) + "</b> — "
                 "живих " + str(len(ok)) + ", порожніх " + str(len(empty))
                 + ", зі збоєм " + str(len(err)))
    lines.append("Обсяг зрізу: " + str(len(text)) + " символів "
                 "(вкидається у КОЖЕН AI-запит, кеш 8 хв)")
    lines.append("")
    lines.append("✅ <b>Живі:</b> " + (", ".join(ok) if ok else "—"))
    if empty:
        lines.append("⚪ <b>Порожні (даних ще немає):</b> " + ", ".join(empty))
    if err:
        lines.append("🔴 <b>Збій:</b>")
        for k, v in err.items():
            lines.append("• " + k + " — " + v)
    lines.append("")
    lines.append("Питай будь-що по всіх даних: <code>/аі твоє питання</code>")
    return "\n".join(lines)
