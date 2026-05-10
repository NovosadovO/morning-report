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


def _load_sent() -> dict:
    try:
        with open(PROACTIVE_SENT_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _mark_sent(slot: str):
    sent = _load_sent()
    today = _now_local().strftime("%Y-%m-%d")
    if today not in sent:
        sent[today] = {}
    sent[today][slot] = True
    # Чистимо старіші за 3 дні
    cutoff = (_now_local() - timedelta(days=3)).strftime("%Y-%m-%d")
    sent = {k: v for k, v in sent.items() if k >= cutoff}
    try:
        with open(PROACTIVE_SENT_FILE, "w") as f:
            json.dump(sent, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _already_sent(slot: str) -> bool:
    sent = _load_sent()
    today = _now_local().strftime("%Y-%m-%d")
    return sent.get(today, {}).get(slot, False)


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def _send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print(f"[proactive] {text}")
        return
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": text[:4090],
        "parse_mode": "HTML"
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


# ─── GEMINI ───────────────────────────────────────────────────────────────────

def _ask_gemini(prompt: str, system: str, max_tokens: int = 300) -> str:
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
    if 6 <= h < 8 and not _already_sent("morning_greet"):
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

    # ── Перед зміною (рання: нагадування о 05:15, нічна: о 17:00) ───────────
    elif status == "pre_shift" and not _already_sent("pre_shift_reminder"):
        slot = "pre_shift_reminder"
        shift = ctx.get("shift_today", "free")
        if shift == "early":
            prompt = (
                f"Олег зараз готується до ранньої зміни (починається о 06:00). "
                f"Зараз {now.strftime('%H:%M')}. "
                f"Нагадай коротко: взяти ліки (Armolopid Plus), поїсти/каву, "
                f"і щось підбадьорливе. 2-3 речення максимум."
            )
        else:
            prompt = (
                f"Олег готується до нічної зміни (починається о 18:00). "
                f"Зараз {now.strftime('%H:%M')}. "
                f"Нагадай коротко взяти ліки, поїсти перед зміною, підбадьор. 2-3 речення."
            )

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

    # ── Після зміни check-in (рання: 18:30–19:30, нічна: 06:30–07:30) ───────
    elif status == "post_shift" and not _already_sent("post_shift_checkin"):
        slot = "post_shift_checkin"
        shift = ctx.get("shift_today", "free")
        if shift == "early":
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

    # ── Нагадування про воду (кожен день о 10:00 і 15:00) ────────────────────
    elif h == 10 and 0 <= m < 30 and status in ("home", "post_shift") and not _already_sent("water_reminder_10"):
        slot = "water_reminder_10"
        prompt = (
            f"Зараз {now.strftime('%H:%M')}. Просто коротке нагадування Олегу — "
            f"чи він пив воду сьогодні? Ціль 2л на день. "
            f"Додай щось коротке про здоров'я або самопочуття. 1-2 речення."
        )

    elif h == 15 and 0 <= m < 30 and status in ("home", "post_shift", "working_early", "working_night") and not _already_sent("water_reminder_15"):
        slot = "water_reminder_15"
        prompt = (
            f"Зараз {now.strftime('%H:%M')}. Нагадування про воду — "
            f"2л на день це важливо особливо при схудненні (ціль 78 кг). "
            f"Коротко і з турботою. 1-2 речення."
        )

    # ── Нагадування про ліки (якщо ще не прийняв, о 8:00–10:00) ─────────────
    elif 8 <= h < 10 and not _already_sent("meds_reminder"):
        meds = ctx.get("meds", "")
        if "не відмічено" in meds or "не прийнято" in meds.lower():
            slot = "meds_reminder"
            prompt = (
                f"Зараз {now.strftime('%H:%M')}. Олег ще не відмітив прийом Armolopid Plus. "
                f"Нагадай коротко — 1 речення, по-дружньому."
            )

    # ── Питання про пробіжку (вільний день, 09:00–11:00) ─────────────────────
    elif 9 <= h < 11 and status == "home" and ctx.get("shift_today") == "free" and not _already_sent("run_question"):
        slot = "run_question"
        prompt = (
            f"Зараз {now.strftime('%H:%M')}, у Олега вільний день. "
            f"Він давно не бігав (або бігає нерегулярно). Погода: {ctx.get('weather_hint', 'невідома')}. "
            f"Спитай чи планує сьогодні пробіжку — легко і без тиску. 1-2 речення."
        )

    # ── Пізній вечір (21:30–22:30) — підсумок дня ────────────────────────────
    elif 21 <= h < 23 and not _already_sent("night_summary"):
        slot = "night_summary"
        habits_ctx = ctx.get("habits", "")
        weight_ctx = ctx.get("weight", "")
        mood = state.get("mood", "")
        prompt = (
            f"Зараз {now.strftime('%H:%M')}, пізній вечір. "
            f"Звички сьогодні: {habits_ctx}. Вага: {weight_ctx}. "
            f"{'Настрій Олега: ' + mood + '. ' if mood else ''}"
            f"Зроби короткий підсумок дня — що добре, що можна краще. "
            f"Побажай доброї ночі і скажи щось позитивне про завтра. "
            f"Зміна завтра: {ctx.get('shift_tomorrow', 'невідомо')}."
        )

    # ── Якщо є подія в календарі за 2 год ────────────────────────────────────
    if slot is None:
        slot, prompt = _check_calendar_event_proactive(ctx, state, now)

    if slot and prompt and not _already_sent(slot):
        answer = _ask_gemini(prompt, system, max_tokens=450)
        if answer and not answer.startswith("⚠️"):
            _send(answer)
            _mark_sent(slot)
            print(f"[proactive] sent slot={slot}")
        else:
            print(f"[proactive] gemini error for slot={slot}: {answer}")


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
