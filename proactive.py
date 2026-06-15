#!/usr/bin/env python3
"""
proactive.py — бот сам ініціює розмову з Олегом.

Логіка:
  - Перевірка кожні 30 хвилин
  - Кожен "слот" дня має свій тригер (вранці, обід, вечір, ніч)
  - Враховує: час, зміну, календар, що Олег відповідав раніше
  - Зберігає user_state: настрій, місцезнаходження, що робить
  - Ніколи не дублює питання якщо вже питав в цей слот
  - Відповіді Олега зберігаються і потрапляють в AI-контекст

USER STATE (data/user_state.json):
  {
    "mood": "хорошо",
    "location": "дома",
    "activity": "відпочиваю",
    "last_updated": "2026-05-09T14:00:00",
    "last_message_from_oleg": "зараз відпочиваю",
    "last_message_time": "2026-05-09T14:01:00"
  }

PROACTIVE SLOTS (data/proactive_sent.json):
  { "2026-05-09": {"morning": true, "midday": true, ...} }
"""

import os
import json
import urllib.request
from datetime import datetime, timezone, timedelta

_DIR = os.path.dirname(__file__)
_DATA = os.path.join(_DIR, "data")
os.makedirs(_DATA, exist_ok=True)

USER_STATE_FILE    = os.path.join(_DATA, "user_state.json")
PROACTIVE_SENT_FILE = os.path.join(_DATA, "proactive_sent.json")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")


# ─── USER STATE ───────────────────────────────────────────────────────────────

def load_user_state() -> dict:
    try:
        with open(USER_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_user_state(state: dict):
    try:
        with open(USER_STATE_FILE, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"save_user_state error: {e}")


def update_user_state_from_message(text: str):
    """
    Парсить відповідь Олега і оновлює user_state.
    Викликається з bot.py при кожному вхідному повідомленні.
    """
    state = load_user_state()
    now_str = _now_local().isoformat()

    state["last_message_from_oleg"] = text
    state["last_message_time"] = now_str

    t = text.lower()

    # Локація
    if any(w in t for w in ["вдома", "дома", "додому"]):
        state["location"] = "вдома"
    elif any(w in t for w in ["на роботі", "на заводі", "на зміні", "працюю"]):
        state["location"] = "на роботі"
    elif any(w in t for w in ["в магазині", "магазин", "супермаркет"]):
        state["location"] = "в магазині"
    elif any(w in t for w in ["на вулиці", "надворі", "парк", "вийшов", "гуляю"]):
        state["location"] = "на вулиці"
    elif any(w in t for w in ["в дорозі", "їду", "машина", "авто", "дорога"]):
        state["location"] = "в дорозі"
    elif any(w in t for w in ["в спортзалі", "спортзал", "gym"]):
        state["location"] = "в спортзалі"

    # Активність
    if any(w in t for w in ["сплю", "лягаю", "відпочиваю", "лежу"]):
        state["activity"] = "відпочиваю"
    elif any(w in t for w in ["біжу", "бігу", "пробіжка", "біг"]):
        state["activity"] = "на пробіжці"
    elif any(w in t for w in ["їм", "обідаю", "вечеряю", "снідаю", "їжа", "їдемо"]):
        state["activity"] = "їм"
    elif any(w in t for w in ["читаю", "навчаюсь", "вчуся", "курс"]):
        state["activity"] = "навчаюсь"
    elif any(w in t for w in ["дивлюсь", "серіал", "фільм", "ютуб", "youtube"]):
        state["activity"] = "дивлюсь відео"
    elif any(w in t for w in ["готую", "варю", "кухня"]):
        state["activity"] = "готую їжу"
    elif any(w in t for w in ["працюю", "робота", "робоч"]):
        state["activity"] = "працюю"

    # Настрій
    if any(w in t for w in ["добре", "відмінно", "чудово", "супер", "кайф", "ок", "нормально"]):
        state["mood"] = "добре"
    elif any(w in t for w in ["погано", "жахливо", "втомився", "не дуже", "хворий", "хвора"]):
        state["mood"] = "погано"
    elif any(w in t for w in ["втома", "втомлений", "важко"]):
        state["mood"] = "втомлений"
    elif any(w in t for w in ["настрій хороший", "в настрої"]):
        state["mood"] = "хороший настрій"

    state["last_updated"] = now_str
    save_user_state(state)


# ─── PROACTIVE SLOTS ──────────────────────────────────────────────────────────

def _now_local():
    return datetime.now(timezone.utc) + timedelta(hours=2)


# In-memory lock — захист від race condition в межах одного процесу
_SENT_INMEM = set()

_GH_PROACTIVE_URL = "https://api.github.com/repos/NovosadovO/morning-report/contents/data/proactive_sent.json"
_GH_DATA_BRANCH = "data"  # всі runtime-записи йдуть в data, не main

def _gh_load_sent():
    """Читає proactive_sent.json з GitHub — persistent між restarts."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        # fallback до локального файлу
        try:
            with open(PROACTIVE_SENT_FILE) as f:
                return json.load(f), None
        except Exception:
            return {}, None
    req = urllib.request.Request(
        f"{_GH_PROACTIVE_URL}?ref={_GH_DATA_BRANCH}",
        headers={"Authorization": f"token {gh_token}", "User-Agent": "morning-report-bot"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
            content = json.loads(base64.b64decode(d["content"]).decode())
            return content, d["sha"]
    except Exception:
        return {}, None


def _gh_save_sent(data: dict, sha):
    """Зберігає proactive_sent.json на GitHub."""
    import base64
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    if not gh_token:
        try:
            with open(PROACTIVE_SENT_FILE, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        return
    content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
    body = json.dumps({
        "message": "proactive: mark slot sent",
        "content": content,
        "branch": _GH_DATA_BRANCH,
        **({"sha": sha} if sha else {})
    }).encode()
    req = urllib.request.Request(_GH_PROACTIVE_URL, data=body, headers={
        "Authorization": f"token {gh_token}",
        "Content-Type": "application/json",
        "User-Agent": "morning-report-bot"
    }, method="PUT")
    try:
        urllib.request.urlopen(req, timeout=8)
    except Exception as e:
        print(f"_gh_save_sent error: {e}")


def _load_sent() -> dict:
    data, _ = _gh_load_sent()
    return data


def _mark_sent(slot: str):
    today = _now_local().strftime("%Y-%m-%d")
    key = f"{today}:{slot}"
    _SENT_INMEM.add(key)  # одразу в пам'ять — захист від race
    sent, sha = _gh_load_sent()
    if today not in sent:
        sent[today] = {}
    sent[today][slot] = True
    # Чистимо старіші за 3 дні
    cutoff = (_now_local() - timedelta(days=3)).strftime("%Y-%m-%d")
    sent = {k: v for k, v in sent.items() if k >= cutoff}
    _gh_save_sent(sent, sha)


def _already_sent(slot: str) -> bool:
    today = _now_local().strftime("%Y-%m-%d")
    key = f"{today}:{slot}"
    if key in _SENT_INMEM:  # спочатку в пам'яті — миттєво
        return True
    sent = _load_sent()
    result = sent.get(today, {}).get(slot, False)
    if result:
        _SENT_INMEM.add(key)  # кешуємо
    return result


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def _send_chunk(text: str):
    """Надсилає один шматок тексту (до 4090 символів). Без parse_mode — Gemini може генерувати невалідний HTML."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print(f"[proactive] {text}")
        return
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text,
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            pass
    except Exception as e:
        print(f"_send error: {e}")


def _send(text: str):
    """Надсилає текст, розбиваючи на частини якщо > 4090 символів."""
    MAX = 4090
    if len(text) <= MAX:
        _send_chunk(text)
        return
    # Розбиваємо по рядках
    parts, current = [], ""
    for line in text.split("\n"):
        candidate = current + ("\n" if current else "") + line
        if len(candidate) <= MAX:
            current = candidate
        else:
            if current:
                parts.append(current)
            while len(line) > MAX:
                parts.append(line[:MAX])
                line = line[MAX:]
            current = line
    if current:
        parts.append(current)
    import time as _t
    for i, part in enumerate(parts):
        if i > 0:
            _t.sleep(0.5)
        _send_chunk(part)


# ─── GEMINI ───────────────────────────────────────────────────────────────────

def _ask_gemini(prompt: str, system: str, max_tokens: int = 500) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "AIzaSyDQYOrsPPLZxXdChAG1SlGh1nzPmiJBHSs")
    contents = [
        {"role": "user",  "parts": [{"text": f"[SYSTEM]\n{system}"}]},
        {"role": "model", "parts": [{"text": "Зрозумів, готовий."}]},
        {"role": "user",  "parts": [{"text": prompt}]},
    ]
    payload = json.dumps({
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.85}
    }).encode()
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        return f"⚠️ Gemini error: {e}"


def _build_system(ctx: dict, state: dict) -> str:
    shift_labels = {"early": "рання (06:00–18:00)", "night": "нічна (18:00–06:00)", "free": "вихідний"}
    lines = [
        "Ти — особистий AI-асистент Олега Новосадова.",
        "Спілкуйся як близький друг: тепло, по-людськи, коротко. Без офіціозу.",
        "Мова: завжди ТІЛЬКИ українська.",
        f"Зараз: {ctx.get('time_str','?')} {ctx.get('weekday','?')} {ctx.get('date_str','')}",
        f"Статус Олега: {ctx.get('status_label','невідомо')}",
        f"Зміна сьогодні: {shift_labels.get(ctx.get('shift_today','free'),'?')}",
        f"Зміна завтра: {shift_labels.get(ctx.get('shift_tomorrow','free'),'?')}",
        "",
        "Що відомо про Олега:",
        f"• Вага: {ctx.get('weight','невідома')}",
        f"• Здоров'я: {ctx.get('health','немає даних')}",
        f"• Звички сьогодні: {ctx.get('habits','не відмічені')}",
        f"• Ліки: {ctx.get('meds','невідомо')}",
    ]
    if ctx.get("calendar"):
        lines.append(f"• Календар сьогодні: {ctx['calendar'][:300]}")
    if ctx.get("crypto"):
        lines.append(f"• Крипто: {ctx.get('crypto','')}"),

    # User state
    if state:
        lines.append("")
        lines.append("Що Олег казав нещодавно:")
        if state.get("location"):
            lines.append(f"• Місцезнаходження: {state['location']}")
        if state.get("activity"):
            lines.append(f"• Зараз займається: {state['activity']}")
        if state.get("mood"):
            lines.append(f"• Настрій/стан: {state['mood']}")
        if state.get("last_message_from_oleg"):
            lines.append(f"• Останнє повідомлення: «{state['last_message_from_oleg'][:100]}»")

    lines += [
        "",
        "Правила:",
        "• Питай тільки ОДНЕ питання за раз — не засипай кількома одразу.",
        "• Будь природнім — якщо щось вже знаєш, не питай знову.",
        "• Якщо Олег на роботі — будь лаконічним.",
        "• Якщо Олег втомлений — підтримай, не грузи.",
        "• Відповідь має бути 2–4 речення MAX, якщо не просять більше.",
        "• ВАЖЛИВО: завжди закінчуй кожне речення повністю. Ніколи не обривай на середині слова чи речення.",
    ]
    return "\n".join(lines)


# ─── КОНТЕКСТ ПОВНИЙ ─────────────────────────────────────────────────────────

def _get_full_ctx():
    try:
        import sys
        sys.path.insert(0, _DIR)
        from context import get_context
        return get_context(include_crypto=True, include_calendar=True)
    except Exception as e:
        print(f"_get_full_ctx error: {e}")
        return {}


# ─── ПРОАКТИВНІ СЛОТИ ────────────────────────────────────────────────────────

def check_proactive():
    """
    Головна функція — викликається кожні 30 хвилин.
    Перевіряє який слот зараз і чи варто писати.
    """
    now = _now_local()
    h, m = now.hour, now.minute
    today = now.strftime("%Y-%m-%d")

    # Не пишемо вночі (00:00–06:30)
    if h < 6 or (h == 6 and m < 30):
        return

    ctx   = _get_full_ctx()
    state = load_user_state()
    status = ctx.get("status", "home")

    # Не пишемо поки спить
    if status == "sleeping":
        return

    system = _build_system(ctx, state)

    # Визначаємо слот + умову
    slot = None
    prompt = None

    # ── Ранковий привіт (06:30–07:30) ────────────────────────────────────────
    # Пропускаємо якщо morning_context АБО morning_brief вже надіслав брифінг сьогодні
    _morning_ctx_sent = False
    try:
        import storage as _st
        _mc = _st.load("monitor_morning_ctx.json", default={})
        if _mc.get("last") == today:
            _morning_ctx_sent = True
        _mb = _st.load("monitor_morning_brief.json", default={})
        if _mb.get("last") == today:
            _morning_ctx_sent = True
    except Exception:
        pass

    if 6 <= h < 8 and not _already_sent("morning_greet") and not _morning_ctx_sent:
        slot = "morning_greet"
        shift = ctx.get("shift_today", "free")
        shift_str = {"early": "рання зміна о 06:00", "night": "нічна зміна о 18:00", "free": "вільний день"}.get(shift, shift)
        mood = state.get("mood", "")
        last_msg = state.get("last_message_from_oleg", "")

        prompt = (
            f"Зараз {now.strftime('%H:%M')}, {ctx.get('weekday','')}. "
            f"Статус Олега: {status}. Зміна сьогодні: {shift_str}. "
            f"{'Останній настрій: ' + mood + '. ' if mood else ''}"
            f"{'Останнє повідомлення вчора/раніше: «' + last_msg + '». ' if last_msg else ''}"
            f"Напиши короткий ранковий привіт — дізнайся як Олег, нагадай що є сьогодні по зміні/плану. "
            f"Одне коротке питання в кінці. Без зайвого пафосу."
        )

    # ── Перед зміною — відключено (дублює check_smart_notifications)
    elif False and status == "pre_shift" and not _already_sent("pre_shift_reminder"):
        slot = None  # відключено

    # ── Обідній check-in (12:00–13:00, тільки вдома/вільний день) ───────────
    elif 12 <= h < 13 and status in ("home", "post_shift") and not _already_sent("midday_checkin"):
        slot = "midday_checkin"
        activity = state.get("activity", "")
        prompt = (
            f"Зараз обідній час ({now.strftime('%H:%M')}), Олег вдома / після зміни. "
            f"{'Останнє що знаю: він ' + activity + '. ' if activity else ''}"
            f"Спитай як пройшов ранок, чи поїв нормально (ціль 78 кг, дієта 16:8). "
            f"Можливо нагадати про воду або прогулянку. Одне питання."
        )

    # ── Після зміни check-in (рання: 18:30–20:00, нічна: 06:30–08:00) ───────
    elif status == "post_shift" and not _already_sent("post_shift_checkin"):
        slot = "post_shift_checkin"
        shift = ctx.get("shift_today", "free")
        # Перевірка часового вікна щоб не шлялося весь день
        in_window = False
        if shift == "early" and (18 < h < 20 or (h == 18 and m >= 30)):
            in_window = True
        elif shift == "night" and (6 < h < 8 or (h == 6 and m >= 30)):
            in_window = True
        if not in_window:
            slot = None
        elif shift == "early":
            prompt = (
                f"Олег щойно після ранньої зміни (закінчилась о 18:00). "
                f"Зараз {now.strftime('%H:%M')}. "
                f"Спитай як пройшла зміна, чи не забув поїсти, "
                f"і що планує на вечір. Дружньо і коротко."
            )
        else:
            prompt = (
                f"Олег щойно після нічної зміни (закінчилась о 06:00). "
                f"Зараз {now.strftime('%H:%M')}. Він, мабуть, втомлений. "
                f"Привітай з кінцем зміни, нагадай поїсти і відпочити. "
                f"Коротко і з турботою."
            )

    # ── Вечірній check-in (19:00–20:00, вдома) ───────────────────────────────
    elif 19 <= h < 20 and status in ("home",) and not _already_sent("evening_checkin"):
        slot = "evening_checkin"
        cal = ctx.get("calendar", "")
        weight_ctx = ctx.get("weight", "")
        habits_ctx = ctx.get("habits", "")
        activity = state.get("activity", "")
        prompt = (
            f"Зараз вечір ({now.strftime('%H:%M')}), Олег вдома. "
            f"{'Знаю що він ' + activity + '. ' if activity else ''}"
            f"Вага: {weight_ctx}. Звички сьогодні: {habits_ctx}. "
            f"Зроби короткий вечірній check-in: як день, що вдалось, "
            f"чи не забув записати вагу / відмітити звички. "
            f"Питання про плани на завтра. Одне питання, дружньо."
        )

    # ── Нагадування про воду — ВИДАЛЕНО (обробляється check_water_reminder() в monitor.py) ──

    # ── Нагадування про ліки — ВИДАЛЕНО (обробляється check_meds_reminder() в meds.py) ──
    # elif 8 <= h < 10 and not _already_sent("meds_reminder"):
    #     ... (дублювало meds.py, породжувало 4+ повідомлення)

    # ── Питання про пробіжку (вільний день, 09:00–11:00) ─────────────────────
    elif 9 <= h < 11 and status == "home" and ctx.get("shift_today") == "free" and not _already_sent("run_question"):
        slot = "run_question"
        prompt = (
            f"Зараз {now.strftime('%H:%M')}, у Олега вільний день. "
            f"Він давно не бігав (або бігає нерегулярно). Погода: {ctx.get('weather_hint', 'невідома')}. "
            f"Спитай чи планує сьогодні пробіжку — легко і без тиску. 1-2 речення."
        )

    # ── Пізній вечір (22:00–22:30) — коротке побажання ночі
    # НЕ 21:00 щоб не дублювати check_day_summary і check_mood_evening
    elif h == 22 and 0 <= m < 30 and not _already_sent("night_summary"):
        slot = "night_summary"
        mood = state.get("mood", "")
        last_msg = state.get("last_message_from_oleg", "")
        shift_tomorrow = ctx.get("shift_tomorrow", "невідомо")
        prompt = (
            f"Зараз {now.strftime('%H:%M')} {now.strftime('%d.%m.%Y')}, вечір. "
            f"{'Олег казав: «' + last_msg + '». ' if last_msg else ''}"
            f"{'Настрій: ' + mood + '. ' if mood else ''}"
            f"Зміна завтра: {shift_tomorrow}. "
            f"Напиши Олегу КОРОТКЕ (2-3 речення) і ТЕПЛЕ побажання доброї ночі. "
            f"Обов'язково згадай завтрашню зміну і що варто взяти/підготувати. "
            f"Без загальних фраз — конкретно і по-дружньому."
        )

    # ── Anomaly detection (вага/біг/сон) ────────────────────────────────────
    if slot is None:
        slot, prompt = _check_anomalies(ctx, state, now)

    # ── Якщо є подія в календарі за 2 год ────────────────────────────────────
    if slot is None:
        slot, prompt = _check_calendar_event_proactive(ctx, state, now)

    if slot and prompt and not _already_sent(slot):
        answer = _ask_gemini(prompt, system, max_tokens=300)
        if answer and not answer.startswith("⚠️"):
            _send(answer)
            _mark_sent(slot)
            print(f"[proactive] sent slot={slot}")
        else:
            print(f"[proactive] gemini error for slot={slot}: {answer}")


def _check_anomalies(ctx, state, now):
    """
    Anomaly detection:
    - вага зростає 3+ дні поспіль → попередження
    - не бігав 5+ днів → мотивація
    - поганий сон 2+ ночі → порада
    Повертає (slot, prompt) або (None, None).
    """
    import sys as _sys_an; _sys_an.path.insert(0, _DIR)
    h = now.hour

    # Тільки між 09:00–20:00 щоб не заважати вночі
    if not (9 <= h < 20):
        return None, None

    # ── Аномалія ваги (3+ дні зростання) ─────────────────────────────────────
    if not _already_sent("anomaly_weight_rising"):
        try:
            from storage import load as _st_an
            wdata = _st_an("weight_data.json") or {}
            sorted_w = sorted(wdata.keys())[-5:]
            vals = [wdata[d] for d in sorted_w if wdata.get(d)]
            if len(vals) >= 3:
                # Перевіряємо 3 останні дні поспіль зростання
                rising = all(vals[i] < vals[i+1] for i in range(len(vals)-3, len(vals)-1))
                if rising:
                    delta = round(vals[-1] - vals[-3], 1)
                    return "anomaly_weight_rising", (
                        f"Вага Олега зростала 3 дні поспіль: {vals[-3]}→{vals[-2]}→{vals[-1]} кг (+{delta} кг). "
                        f"Зараз {now.strftime('%H:%M')}. "
                        f"Як коуч і друг — скажи про це конкретно, але без паніки. "
                        f"Нагадай про дієту 16:8, воду, біг. 2-3 речення, практичний тон."
                    )
        except Exception as _e_an_w:
            print(f"anomaly weight check: {_e_an_w}")

    # ── Аномалія бігу (5+ днів без пробіжки) ─────────────────────────────────
    if not _already_sent("anomaly_no_run"):
        try:
            from storage import load_habits as _lh_an
            hab_db = _lh_an()
            today = now.strftime("%Y-%m-%d")
            days_no_run = 0
            from datetime import timedelta
            for i in range(1, 10):
                d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                if hab_db.get(d, {}).get("run") is True:
                    break
                days_no_run += 1
            if days_no_run >= 5:
                return "anomaly_no_run", (
                    f"Олег не бігав вже {days_no_run} днів. Зараз {now.strftime('%H:%M')}. "
                    f"Погода: {ctx.get('weather_hint', 'невідома')}. "
                    f"Як коуч — спонукай вийти на пробіжку, навіть коротку 20 хв. "
                    f"Без занудства, з конкретним закликом. 2 речення."
                )
        except Exception as _e_an_r:
            print(f"anomaly no_run check: {_e_an_r}")

    # ── Аномалія сну (2+ ночі поганий сон < 6г) ──────────────────────────────
    if not _already_sent("anomaly_bad_sleep"):
        try:
            from storage import load_habits as _lh_sl
            hab_db = _lh_sl()
            from datetime import timedelta
            bad_nights = 0
            for i in range(1, 4):
                d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
                sl = hab_db.get(d, {}).get("sleep")
                if sl and sl < 6:
                    bad_nights += 1
            if bad_nights >= 2:
                return "anomaly_bad_sleep", (
                    f"Олег спав менше 6 годин {bad_nights} ночі поспіль. "
                    f"Зараз {now.strftime('%H:%M')}. "
                    f"Як коуч і друг — напиши з турботою про важливість сну, "
                    f"одну конкретну пораду (напр. відкласти телефон о 22:00). 2 речення."
                )
        except Exception as _e_an_sl:
            print(f"anomaly bad_sleep check: {_e_an_sl}")

    return None, None


def _check_calendar_event_proactive(ctx, state, now):
    """
    Якщо є подія в календарі за ~2 год — нагадай і спитай чи готовий.
    """
    import sys
    sys.path.insert(0, _DIR)
    try:
        cal_text = ctx.get("calendar", "")
        if not cal_text or "нічого" in cal_text.lower():
            return None, None

        import re
        # Знаходимо всі часи в форматі HH:MM
        times = re.findall(r"(\d{1,2}):(\d{2})", cal_text)
        h_now, m_now = now.hour, now.minute

        for (hh, mm) in times:
            h_ev, m_ev = int(hh), int(mm)
            diff_min = (h_ev * 60 + m_ev) - (h_now * 60 + m_now)
            if 100 <= diff_min <= 130:  # 1.5–2.5 год до події
                slot = f"cal_reminder_{hh}{mm}"
                if _already_sent(slot):
                    return None, None
                # Знайти назву події
                ev_name_m = re.search(rf"{hh}:{mm}[^\n]*?<b>([^<]+)</b>", cal_text)
                ev_name = ev_name_m.group(1) if ev_name_m else "подія"
                prompt = (
                    f"У Олега сьогодні о {hh}:{mm} — {ev_name}. "
                    f"Зараз {now.strftime('%H:%M')}, тобто ще ~{diff_min//60}г {diff_min%60}хв. "
                    f"Нагадай про це дружньо — спитай чи готовий, чи є що треба взяти/зробити. "
                    f"Коротко, 2 речення."
                )
                return slot, prompt
    except Exception as e:
        print(f"_check_calendar_event_proactive error: {e}")
    return None, None


# ─── AI ВЛАСНІ СПОСТЕРЕЖЕННЯ ─────────────────────────────────────────────────

def check_ai_observations():
    """
    Раз на день (о 15:00 або 16:00 якщо на роботі) AI сам аналізує
    всі дані за останні 7-14 днів, знаходить патерни і надсилає
    власні спостереження + задає питання.
    """
    now = _now_local()
    h, m = now.hour, now.minute
    today = now.strftime("%Y-%m-%d")

    # Вікно: 15:00–15:05 або 16:00–16:05 (якщо о 15:00 на ранній зміні)
    if not ((h == 15 and 0 <= m < 6) or (h == 16 and 0 <= m < 6)):
        return

    if _already_sent("ai_observations"):
        return

    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not gemini_key:
        return

    # ── Збираємо всі доступні дані ───────────────────────────────────────────
    import sys
    sys.path.insert(0, _DIR)

    data_parts = []

    # Вага — 14 днів
    try:
        import storage as _st
        wd = _st.load("weight_data.json") or {}
        cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d")
        w14 = {k: v for k, v in sorted(wd.items()) if k >= cutoff}
        if w14:
            entries = [f"{k}: {v} кг" for k, v in list(w14.items())[-14:]]
            data_parts.append("ВАГА (14 днів):\n" + "\n".join(entries))
    except Exception as e:
        print(f"[ai_obs] weight error: {e}")

    # Звички — 14 днів
    try:
        from habits import load_data as _lhab
        hab_db = _lhab()
        hab_lines = []
        for i in range(13, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day_data = hab_db.get(d, {})
            if day_data:
                done = [k for k, v in day_data.items() if v is True]
                missed = [k for k, v in day_data.items() if v is False]
                hab_lines.append(f"{d}: виконано={','.join(done) or '-'} | пропущено={','.join(missed) or '-'}")
        if hab_lines:
            data_parts.append("ЗВИЧКИ (14 днів):\n" + "\n".join(hab_lines))
    except Exception as e:
        print(f"[ai_obs] habits error: {e}")

    # Біг — Strava
    try:
        from strava import get_month_stats as _gms, get_last_activity as _gla
        ms = _gms(now.year, now.month)
        la = _gla()
        run_str = f"Цей місяць: {ms.get('runs',0)} пробіжок, {ms.get('km',0):.1f} км (ціль 40 км)"
        if la:
            run_str += f"\nОстання: {la.get('distance_km',0):.1f} км, темп {la.get('pace','?')}, {la.get('date','?')}"
        data_parts.append("БІГ:\n" + run_str)
    except Exception as e:
        print(f"[ai_obs] strava error: {e}")

    # QWatch (здоров'я)
    try:
        from storage import load_health as _lhh
        hd = _lhh()
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        h7 = {k: v for k, v in sorted(hd.items()) if k >= cutoff}
        if h7:
            h_lines = []
            for d, vals in h7.items():
                parts = []
                if vals.get("steps"): parts.append(f"кроки {vals['steps']}")
                if vals.get("sleep_hours"): parts.append(f"сон {vals['sleep_hours']}г")
                if vals.get("hrv"): parts.append(f"HRV {vals['hrv']}")
                if vals.get("hr_avg"): parts.append(f"ЧСС {vals['hr_avg']}")
                if parts:
                    h_lines.append(f"{d}: {', '.join(parts)}")
            if h_lines:
                data_parts.append("ЗДОРОВ'Я/QWATCH (7 днів):\n" + "\n".join(h_lines))
    except Exception as e:
        print(f"[ai_obs] health error: {e}")

    # Крипто — поточні ціни + портфель
    try:
        from context import get_context as _gctx
        ctx = _gctx(include_crypto=True, include_calendar=False)
        crypto_str = ctx.get("crypto", "")
        portfolio_str = ctx.get("portfolio", "")
        if crypto_str:
            data_parts.append("КРИПТО ЗАРАЗ:\n" + crypto_str)
        if portfolio_str:
            data_parts.append("ПОРТФЕЛЬ:\n" + portfolio_str)
    except Exception as e:
        print(f"[ai_obs] crypto error: {e}")

    # Попередні підсумки (для розуміння трендів)
    try:
        prev = _st.load("summaries_history.json", default=[])
        if prev and isinstance(prev, list):
            recent7 = [p for p in prev if p.get("date", "") >= (now - timedelta(days=7)).strftime("%Y-%m-%d")]
            if recent7:
                prev_lines = [f"[{p['date']} {p.get('time','')}]: {p['text'][:300]}" for p in recent7[-5:]]
                data_parts.append("ПОПЕРЕДНІ ПІДСУМКИ (останній тиждень):\n" + "\n---\n".join(prev_lines))
    except Exception as e:
        print(f"[ai_obs] summaries error: {e}")

    if not data_parts:
        print("[ai_obs] no data available, skipping")
        return

    all_data = "\n\n".join(data_parts)

    # ── Промпт — AI аналізує як незалежний аналітик ─────────────────────────
    import uuid as _uuid
    seed = str(_uuid.uuid4())[:8]

    prompt = f"""Ти — персональний AI-аналітик і коуч Олега Новосадова (Кошіце, Словаччина, {now.strftime('%d.%m.%Y')}).
Профіль: заводський робітник Minebea Mitsumi (змінний графік), цілі — схуднути до 78 кг, бігати 40 км/міс, фінансова незалежність через крипто+ETF.

Ось всі дані за останні 7-14 днів:

{all_data}

Твоє завдання — САМОСТІЙНО проаналізувати ці дані і написати повідомлення у такому форматі:

🔍 <b>Що я помітив</b>
[3-5 конкретних спостережень на основі реальних чисел і патернів в даних.
Приклади: "Вага зростає щопонеділка після вихідних", "Ти не бігав вже 5 днів — це найдовша пауза за місяць",
"HRV падає в дні після нічних змін", "ONDO впав на 12% — найбільше в портфелі"]

📈 <b>Тренди</b>
[2-3 тренди — що покращується, що погіршується. Тільки якщо дані підтверджують.]

❓ <b>Питання до тебе</b>
[2-3 питання які AI реально хоче знати щоб краще допомагати.
НЕ загальні ("як ти?") — а конкретні виходячи з даних:
"Чому ти пропустив біг в четвер — зайнятість чи погода?",
"Ти відмітив душ як не виконаний — це через зміну чи щось інше?"]

Правила:
- Тільки реальні числа і факти з даних вище — нічого не вигадувати
- Кожне спостереження = конкретний факт або число
- Питання мають бути такими щоб Олег захотів відповісти
- Мова: українська, неформально як близький друг
- Seed: {seed}"""

    try:
        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.85},
        }).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
        text = resp["candidates"][0]["content"]["parts"][0]["text"].strip()

        if text:
            _mark_sent("ai_observations")
            _send(text)
            print(f"[ai_obs] sent observations for {today}")
    except Exception as e:
        print(f"[ai_obs] gemini error: {e}")
