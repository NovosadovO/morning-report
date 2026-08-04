"""
ai_buttons — універсальні кнопки під УСІМА AI-сповіщеннями.

Кожне AI-повідомлення (крипто, здоров'я, біг, листи, звички, астро, гроші,
робота, календар) отримує набір живих кнопок:

  🤖 Поясни детальніше   — розгорнутий AI-розбір саме цього повідомлення
  ✍️ Нотатка             — бот питає ТВІЙ текст і зберігає його в /нотатки
  🔔 Нагадай пізніше     — відкладений повтор цього ж повідомлення (+2 год)
  🚫 Не цікавить         — тиша по цій темі на 7 днів
  + 1-2 кнопки під тему (графік, записати вагу, план бігу, рахунки...)

Правила:
  • НЕМА мертвих кнопок: payload у PayloadStore (гілка data), зник payload →
    {"ok": False, "error": "payload_missing"} → UI покаже «застаріло».
  • НЕМА вигадок: AI отримує тільки текст самого повідомлення.
  • Кожне натискання зберігається в gx_ack.json (видно через /кнопки).

Callback-префікси: gx_more_ / gx_note_ / gx_later_ / gx_mute_ / gx_ack_ /
gx_chart_ / gx_weight_ / gx_runplan_ / gx_done_ / gx_astro_ / gx_mail_ /
gx_bills_ / gx_shift_ / gx_agenda_
"""

from datetime import timedelta, datetime

import ai_kit as K

TAG = "ai_buttons"

STORE_FILE = "gx_store.json"
ACK_FILE = "gx_ack.json"
FOLLOW_FILE = "gx_followups.json"
MUTE_FILE = "gx_mute.json"

_store = K.PayloadStore(STORE_FILE)

LATER_MINUTES = 120
MUTE_DAYS = 7

# ─── ТЕМИ ────────────────────────────────────────────────────────────────────

TOPIC_LABEL = {
    "crypto": "крипто",
    "health": "здоров'я і вага",
    "run": "біг",
    "email": "листи",
    "habits": "звички",
    "astro": "астрологія",
    "money": "гроші та інвестиції",
    "work": "робота і зміни",
    "calendar": "календар",
    "general": "загальні повідомлення",
}

# trigger_type -> тема
TRIGGER_TOPIC = {
    "crypto_move": "crypto", "crypto_alert": "crypto", "crypto": "crypto",
    "health_update": "health", "weight": "health", "sleep": "health",
    "habit_checkin": "habits", "habits": "habits",
    "weekly_run_compare": "run", "strava": "run", "new_activity": "run",
    "vip_email": "email", "new_email": "email", "email": "email",
    "astro": "astro", "astro_daily": "astro",
    "bills": "money", "deadline": "money", "finance": "money",
    "event_prep": "calendar", "event_soon": "calendar", "calendar": "calendar",
    "morning": "general", "lunch": "general", "afternoon": "general",
    "evening": "general", "idle": "general",
}

_KEYS = [
    ("crypto", ("btc", "eth", "крипт", "bitcoin", "ethereum", "avax", "ondo",
                "solana", "альткоїн", "$")),
    ("run", ("біг", "пробіж", "strava", "км ", "темп", "run")),
    ("health", ("вага", "кг", "сон ", "кроки", "пульс", "здоров", "калор")),
    ("email", ("лист", "email", "пошт", "gmail", "michaela")),
    ("habits", ("звичк", "стрік", "streak", "дисциплін")),
    ("astro", ("транзит", "астро", "місяц у", "ретрогр", "сатурн", "юпітер",
               "натальн", "асцендент")),
    ("money", ("рахунок", "оплат", "інвест", "дедлайн", "interfin", "портфел",
               "€", "eur")),
    ("work", ("зміна", "нічна", "рання", "minebea", "робот")),
    ("calendar", ("календар", "подія", "зустріч", "нагадув")),
]


def detect_topic(text: str, trigger_type: str = "") -> str:
    """Визначає тему повідомлення: спочатку по trigger_type, потім по тексту."""
    t = (trigger_type or "").strip().lower()
    if t in TRIGGER_TOPIC:
        return TRIGGER_TOPIC[t]
    low = (text or "").lower()
    best, score = "general", 0
    for topic, keys in _KEYS:
        s = sum(1 for k in keys if k in low)
        if s > score:
            best, score = topic, s
    return best if score else "general"


# ─── MUTE ────────────────────────────────────────────────────────────────────

def is_muted(topic: str) -> bool:
    data = K.load(MUTE_FILE, default={}) or {}
    rec = data.get(topic)
    if not isinstance(rec, dict):
        return False
    try:
        until = datetime.fromisoformat(str(rec.get("until"))).replace(tzinfo=None)
    except Exception:
        return False
    return K.now().replace(tzinfo=None) < until


def mute_status() -> str:
    data = K.load(MUTE_FILE, default={}) or {}
    rows = []
    for topic, rec in (data.items() if isinstance(data, dict) else []):
        if not isinstance(rec, dict):
            continue
        if is_muted(topic):
            rows.append(f"🚫 {TOPIC_LABEL.get(topic, topic)} — до {str(rec.get('until'))[:16].replace('T', ' ')}")
    if not rows:
        return "🔔 Усі теми увімкнені — нічого не приховано."
    return "🔇 <b>ПРИХОВАНІ ТЕМИ</b>\n" + "\n".join(rows) + "\n\n<i>Увімкнути все: /увімкни_теми</i>"


def unmute_all() -> int:
    data = K.load(MUTE_FILE, default={}) or {}
    n = len(data) if isinstance(data, dict) else 0
    K.save(MUTE_FILE, {})
    return n


# ─── КЛАВІАТУРА ──────────────────────────────────────────────────────────────

def _topic_row(topic: str, pid: str):
    if topic == "crypto":
        return [{"text": "📈 Графік крипти", "callback_data": f"gx_chart_{pid}"},
                {"text": "💰 Гроші/дедлайни", "callback_data": f"gx_bills_{pid}"}]
    if topic == "health":
        return [{"text": "⚖️ Записати вагу", "callback_data": f"gx_weight_{pid}"},
                {"text": "📊 Графік здоров'я", "callback_data": f"gx_chart_{pid}"}]
    if topic == "run":
        return [{"text": "🏃 Графік бігу", "callback_data": f"gx_chart_{pid}"},
                {"text": "📅 План бігу", "callback_data": f"gx_runplan_{pid}"}]
    if topic == "habits":
        return [{"text": "✅ Зроблено", "callback_data": f"gx_done_{pid}"},
                {"text": "📊 Графік звичок", "callback_data": f"gx_chart_{pid}"}]
    if topic == "astro":
        return [{"text": "🔮 Транзити детально", "callback_data": f"gx_astro_{pid}"},
                {"text": "📅 План на день", "callback_data": f"gx_agenda_{pid}"}]
    if topic == "email":
        return [{"text": "📬 Показати листи", "callback_data": f"gx_mail_{pid}"},
                {"text": "💰 Гроші/дедлайни", "callback_data": f"gx_bills_{pid}"}]
    if topic == "money":
        return [{"text": "💰 Рахунки і дедлайни", "callback_data": f"gx_bills_{pid}"},
                {"text": "📈 Графік крипти", "callback_data": f"gx_chart_{pid}"}]
    if topic == "work":
        return [{"text": "🗓 Мій графік змін", "callback_data": f"gx_shift_{pid}"},
                {"text": "📅 План на день", "callback_data": f"gx_agenda_{pid}"}]
    if topic == "calendar":
        return [{"text": "📅 План на день", "callback_data": f"gx_agenda_{pid}"},
                {"text": "🗓 Тиждень вперед", "callback_data": f"gx_week_{pid}"}]
    return [{"text": "📅 План на день", "callback_data": f"gx_agenda_{pid}"},
            {"text": "📈 Графік крипти", "callback_data": f"gx_chart_{pid}"}]


def keyboard(text: str, topic: str = "", trigger_type: str = "", extra: dict = None):
    """Створює payload + повний набір кнопок під AI-повідомлення.
    Повертає (pid, inline_keyboard)."""
    topic = topic or detect_topic(text, trigger_type)
    payload = {"topic": topic, "trigger": trigger_type or "",
               "text": (text or "")[:2000]}
    if extra:
        payload.update(extra)
    pid = _store.put(payload)
    kb = [
        [{"text": "🤖 Поясни детальніше", "callback_data": f"gx_more_{pid}"},
         {"text": "✍️ Нотатка", "callback_data": f"gx_note_{pid}"}],
        _topic_row(topic, pid),
        [{"text": "🔔 Нагадай пізніше", "callback_data": f"gx_later_{pid}"},
         {"text": "🚫 Не цікавить", "callback_data": f"gx_mute_{pid}"}],
    ]
    return pid, kb


# ─── ACK ─────────────────────────────────────────────────────────────────────

def _ack(pid: str, answer: str, extra: dict = None) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    rec = {"answer": answer, "topic": p.get("topic"), "trigger": p.get("trigger"),
           "preview": (p.get("text") or "")[:160], "ts": K.now().isoformat()}
    if extra:
        rec.update(extra)
    K.update_key(ACK_FILE, K.Dedup.key(f"{pid}|{answer}"), rec)
    try:
        import response_log
        response_log.log_response("ai_button", str(p.get("topic") or "general"), answer,
                                  {"trigger": p.get("trigger"), "note": rec.get("note")})
    except Exception as e:
        K.log(TAG, f"response_log error: {e}")
    return {"ok": True, "topic": p.get("topic"), "answer": answer}


def payload(pid: str):
    return _store.get(pid)


# ─── ДІЇ ─────────────────────────────────────────────────────────────────────

def do_more(pid: str) -> dict:
    """🤖 Поясни детальніше — розгорнутий AI-розбір цього ж повідомлення."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    src = (p.get("text") or "").strip()
    if not src:
        return {"ok": False, "error": "empty"}
    topic = p.get("topic") or "general"
    prompt = (
        "Ти — особистий AI-асистент Олега (Кошице, Словаччина; змінна робота "
        "Minebea Mitsumi; цілі: фінансова незалежність, схуднення до 78 кг, біг, "
        "інвестиції).\n"
        f"ТЕМА: {TOPIC_LABEL.get(topic, topic)}\n"
        "Олег натиснув «Поясни детальніше» під твоїм повідомленням. Дай розгорнутий "
        "розбір українською: 6-9 речень (150-250 слів), більше конкретики, чисел, "
        "причин і наслідків, і 2-3 конкретні наступні кроки. Тепло, з 3-4 емодзі.\n"
        "ПРАВИЛА: опирайся ТІЛЬКИ на дані з повідомлення нижче. НЕ вигадуй цифр, "
        "подій, людей і дат. Якщо даних мало — скажи, яких саме даних не хватає.\n\n"
        "ПОВІДОМЛЕННЯ:\n" + src[:1800]
    )
    try:
        txt = (K.gemini_text(prompt, max_tokens=900, temperature=0.85, tag=TAG) or "").strip()
    except Exception as e:
        K.log(TAG, f"do_more error: {e}")
        return {"ok": False, "error": "ai_failed", "source": src[:3000]}
    if not txt:
        return {"ok": False, "error": "ai_failed", "source": src[:3000]}
    _ack(pid, "more")
    import re
    return {"ok": True, "topic": topic,
            "text": re.sub(r"^[*#>\s-]+", "", txt).strip()[:2500]}


def _auto_note(p: dict) -> str:
    """Автонотатка-fallback, якщо Олег не написав свій текст. Без вітання і без
    усього тіла повідомлення — інакше ai_notes засмічується "Привіт Олеже!" і це
    потім повертається в AI-контекст як нібито факт про Олега."""
    import re as _re
    raw = _re.sub(r"<[^>]+>", " ", str(p.get("text") or ""))
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    lines = [l for l in lines if not _re.match(r"^(привіт|вітаю|доброго|добрий)", l.lower())]
    body = " ".join(lines)
    body = _re.sub(r"\s+", " ", body).strip()[:160]
    topic = TOPIC_LABEL.get(p.get("topic"), p.get("topic") or "сповіщення")
    return f"[{topic}] відмічено без коментаря: {body}" if body else f"[{topic}] відмічено без коментаря"


def do_note(pid: str, note: str = "") -> dict:
    """✍️ Нотатка — зберігає ТВІЙ текст (або сам зміст повідомлення, якщо тексту немає)."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    own = bool((note or "").strip())
    text = (note or "").strip() or _auto_note(p)
    try:
        import ai_notes
        ai_notes.add_note(text, source=f"gx_{p.get('topic') or 'general'}")
    except Exception as e:
        K.log(TAG, f"note save error: {e}")
        return {"ok": False, "error": "save_failed"}
    _ack(pid, "noted", {"note": text[:300]})
    return {"ok": True, "own": own, "note": text[:300], "topic": p.get("topic")}


def ask_note_text(pid: str) -> dict:
    """Перевіряє, що payload живий, перед тим як питати текст нотатки."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    return {"ok": True, "topic": p.get("topic"),
            "preview": (p.get("text") or "")[:160]}


def do_later(pid: str, minutes: int = LATER_MINUTES) -> dict:
    """🔔 Нагадай пізніше — повторить це саме повідомлення через N хвилин."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    due = K.now().replace(tzinfo=None) + timedelta(minutes=minutes)
    K.update_key(FOLLOW_FILE, K.Dedup.key(f"{pid}|{due.strftime('%H%M')}"), {
        "pid": pid, "topic": p.get("topic"), "trigger": p.get("trigger"),
        "text": (p.get("text") or "")[:2000], "due": due.isoformat(),
        "sent": False,
    })
    _ack(pid, f"later_{minutes}")
    return {"ok": True, "at": due.strftime("%H:%M"), "minutes": minutes,
            "topic": p.get("topic")}


def do_mute(pid: str, days: int = MUTE_DAYS) -> dict:
    """🚫 Не цікавить — тиша по темі на N днів."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    topic = p.get("topic") or "general"
    until = K.now().replace(tzinfo=None) + timedelta(days=days)
    K.update_key(MUTE_FILE, topic, {"until": until.isoformat(),
                                    "ts": K.now().isoformat()})
    _ack(pid, f"muted_{days}d")
    return {"ok": True, "topic": topic, "label": TOPIC_LABEL.get(topic, topic),
            "until": until.strftime("%d.%m")}


def do_done(pid: str) -> dict:
    """✅ Зроблено — фіксує виконання того, про що йшла мова."""
    r = _ack(pid, "done")
    if not r.get("ok"):
        return r
    return r


def do_ack(pid: str, answer: str = "seen") -> dict:
    return _ack(pid, answer)


# ─── ВІДКЛАДЕНІ ПОВТОРИ ──────────────────────────────────────────────────────

def tick() -> int:
    """Надсилає повідомлення, які Олег відклав кнопкою «🔔 Нагадай пізніше»."""
    data = K.load(FOLLOW_FILE, default={}) or {}
    if not isinstance(data, dict) or not data:
        return 0
    n = K.now().replace(tzinfo=None)
    sent = 0
    for key, rec in list(data.items()):
        if not isinstance(rec, dict) or rec.get("sent"):
            continue
        try:
            due = datetime.fromisoformat(str(rec.get("due"))).replace(tzinfo=None)
        except Exception:
            continue
        if due > n:
            continue
        text = (rec.get("text") or "").strip()
        if not text:
            rec["sent"] = True
            K.update_key(FOLLOW_FILE, key, rec)
            continue
        _pid, kb = keyboard(text, topic=rec.get("topic") or "",
                            trigger_type=rec.get("trigger") or "")
        body = ("🔔 <b>ТИ ПРОСИВ НАГАДАТИ</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n" + text)[:3900]
        if K.send_card(body, kb, tag=TAG):
            sent += 1
            rec["sent"] = True
            rec["sent_at"] = K.now().isoformat()
            K.update_key(FOLLOW_FILE, key, rec)
            K.log(TAG, f"🔔 відкладене повідомлення надіслано ({rec.get('topic')})")
    return sent


def pending() -> str:
    """/відкладені — що бот ще нагадає."""
    data = K.load(FOLLOW_FILE, default={}) or {}
    rows = []
    for rec in (data.values() if isinstance(data, dict) else []):
        if not isinstance(rec, dict) or rec.get("sent"):
            continue
        due = str(rec.get("due") or "")[:16].replace("T", " ")
        prev = (rec.get("text") or "")[:90].replace("\n", " ")
        rows.append(f"🔔 {due} · {K.esc(prev)}...")
    if not rows:
        return "🔔 <b>ВІДКЛАДЕНІ</b>\n\nНічого не відкладено."
    return "🔔 <b>ВІДКЛАДЕНІ</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(rows[:15])


# ─── ЗВІТ ────────────────────────────────────────────────────────────────────

_LABEL = {"more": "🤖 просив детальніше", "noted": "✍️ нотатка",
          "done": "✅ зроблено", "seen": "👀 бачив"}


def report(days: int = 7) -> str:
    """/кнопки — що саме ти натискав під сповіщеннями."""
    data = K.load(ACK_FILE, default={}) or {}
    if not data:
        return ("🎛 <b>НАТИСКАННЯ КНОПОК</b>\n\nЩе порожньо — натисни будь-яку кнопку "
                "під сповіщенням, і вона тут з'явиться.")
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
        return f"🎛 <b>НАТИСКАННЯ КНОПОК</b>\n\nЗа {days} дн. натискань немає."
    rows.sort(reverse=True)
    out = [f"🎛 <b>НАТИСКАННЯ КНОПОК</b> (за {days} дн.)", "━━━━━━━━━━━━━━━━━━━━"]
    for ts, r in rows[:20]:
        ans = str(r.get("answer") or "")
        lab = _LABEL.get(ans, ans)
        if ans.startswith("later_"):
            lab = f"🔔 відкладено на {ans.split('_')[1]} хв"
        elif ans.startswith("muted_"):
            lab = f"🚫 приховано на {ans.split('_')[1]}"
        topic = TOPIC_LABEL.get(r.get("topic"), r.get("topic") or "")
        out.append(f"{ts.strftime('%d.%m %H:%M')} · <b>{K.esc(topic)}</b> — {lab}")
        if r.get("note"):
            out.append(f"    ✍️ {K.esc(r['note'])[:120]}")
    return "\n".join(out)[:3900]


def gc(days: int = 14) -> int:
    return _store.gc(days)


if __name__ == "__main__":
    import sys
    if "--tick" in sys.argv:
        print("sent:", tick())
    elif "--pending" in sys.argv:
        print(pending())
    elif "--report" in sys.argv:
        print(report())
    elif "--mute" in sys.argv:
        print(mute_status())
    else:
        pid, kb = keyboard("BTC $118 000, +5.2% за добу. Вага 83.4 кг.")
        print("pid:", pid)
        for row in kb:
            print([b["text"] for b in row])
