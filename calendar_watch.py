#!/usr/bin/env python3
"""
КАЛЕНДАРНИЙ ВАРТОВИЙ  (calendar_watch)

Чому взагалі з'явився: старий шлях нагадувань був фактично мертвий —
`monitor._check_event_reminders()` закоментований, а єдиний живий тригер
`event_soon` в intelligent_listener мав дедуп ПО ТИПУ тригера (1 раз на 1.5 год),
а не по кожній події. Тому з календаря приходило 1-2 сповіщення на день,
а частина подій не згадувалась ніколи.

Що робить тут:
  • ⏰ 2 години до події   → «готуйся»
  • 🔔 30 хвилин до події → «виходь / починай»
  • ☀️ ранкова агенда дня  → 1 раз на день: зміна + усі реальні події
  • 🌙 вечірній прев'ю     → 1 раз на день: що чекає завтра
  • ✅ після події         → «як пройшло?» (відповідь зберігається)
  • 🔔 snooze 15 хв        → повторне нагадування точно в час

ВАЖЛИВО ПРО КРЕДИТИ: цей модуль НЕ використовує Gemini взагалі. Усі тексти —
локальні шаблони з ротацією формулювань (щоб не було копірки). Витрата
AI-кредитів = 0. Календар читається з in-process кешем (4 хв), тобто Google
Calendar API не дьоргається щосекунди.

Дедуп — ПО КОЖНІЙ ПОДІЇ ОКРЕМО: ключ `event_id|stage`. Тому 5 подій за день
дадуть 5 наборів нагадувань, а не одне на всіх.

Усі відповіді на кнопки зберігаються в calendar_ack.json (гілка data).

Callback-префікси: cw_ok_ / cw_sn_ / cw_note_ / cw_cancel_ /
                   cw_done_ / cw_miss_ / cw_moved_ / cw_ack_
"""

import re
from datetime import datetime, timedelta, timezone

import ai_kit as K

TAG = "calendar"

STORE_FILE = "calendar_store.json"       # payload кнопок
SENT_FILE = "calendar_sent.json"         # дедуп event_id|stage -> ISO
ACK_FILE = "calendar_ack.json"           # збережені відповіді
SNOOZE_FILE = "calendar_snooze.json"     # відкладені нагадування
DAILY_FILE = "calendar_daily.json"       # агенда/прев'ю: 1 раз на день
WEEKLY_FILE = "calendar_weekly.json"     # огляд тижня: 1 раз на тиждень
AI_CACHE_FILE = "calendar_ai_cache.json"  # AI-коментар: 1 на подію, перевикористовується
AI_BUDGET_FILE = "calendar_ai_budget.json"  # денний ліміт AI-викликів

_store = K.PayloadStore(STORE_FILE)

# Рутина: у нагадуваннях по годинах не потрібна (інакше «вода/чай» спамили б),
# але в агенді дня вона враховується окремим рядком-лічильником.
ROUTINE = [
    "біг", "вода", "чай", "сауна", "armolopid", "армолопід", "ванна", "душ",
    "медитац", "розтяж", "сон", "крок", "вправ", "прокидан", "відбій",
    "навчання інвест", "чек крипто", "пошта", "вітамін", "зарядка",
    "💧", "🍵", "🏃", "🧖", "💊", "📈", "💹", "📬",
    "спрей", "волос", "записати вагу", "apple health", "зважит", "щоденник",
    "💈", "⚖️", "📝",
]
SHIFT_WORDS = ["зміна", "рання", "нічна", "shift", "☀️", "🌙"]

# Скільки хвилин «допуск» на спрацювання (листенер тикає раз на хвилину,
# але буває лаг/рестарт — тому вікно, а не точна секунда).
WINDOW_MIN = 12

_cache = {"ts": None, "events": [], "h": 0}
_CACHE_SEC = 240

# Горизонт за замовчуванням — 8 днів вперед (а не тільки сьогодні):
# бот має бачити тиждень і попереджати заздалегідь.
DEFAULT_HOURS = 192


# ─── КАЛЕНДАР ────────────────────────────────────────────────────────────────

def _raw_events(hours_ahead: int = DEFAULT_HOURS):
    """Події з УСІХ календарів на N годин вперед (типово 8 днів) + 4 години назад
    (для «як пройшло»). In-process кеш 4 хв — щоб не дьоргати Google API щосекунди.
    Кеш враховує горизонт: запит на більший горизонт не віддає короткий кеш."""
    n = K.now().replace(tzinfo=None)
    if (_cache["ts"] and (n - _cache["ts"]).total_seconds() < _CACHE_SEC
            and _cache.get("h", 0) >= hours_ahead):
        return _cache["events"]
    try:
        import monitor as M
        token = M._calendar_access_token()
        if not token:
            try:
                import context as _ctx
                token = _ctx._get_token()
            except Exception:
                token = None
        if not token:
            K.log(TAG, "календар недоступний (немає токена)")
            return None
        headers = {"Authorization": f"Bearer {token}"}
        t_min = datetime.now(timezone.utc) - timedelta(hours=4)
        t_max = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
        evs = M._fetch_events_all_calendars(headers, t_min, t_max, max_per_cal=40) or []
        if not evs:
            # резервний шлях: прямий календар Олега (той, що працює в context.py)
            try:
                import context as _ctx
                seen = set()
                for _off in range(0, max(2, int(hours_ahead / 24) + 1)):
                    for _e in (_ctx._fetch_events_for_day(token, _off) or []):
                        _u = _e.get("id", "")
                        if _u and _u not in seen:
                            seen.add(_u)
                            evs.append(_e)
            except Exception as e2:
                K.log(TAG, f"fallback fetch error: {e2}")
    except Exception as e:
        K.log(TAG, f"fetch error: {e}")
        return None
    out = []
    for ev in evs:
        item = _norm(ev)
        if item:
            out.append(item)
    out.sort(key=lambda x: x["start"])
    _cache["ts"] = n
    _cache["events"] = out
    _cache["h"] = hours_ahead
    # Діагностика: видно чи «0 реальних подій» — правда, чи наслідок фільтрів
    try:
        _r = sum(1 for e in out if e["routine"])
        _s = sum(1 for e in out if e["shift"])
        _a = sum(1 for e in out if e["allday"])
        _real = [e for e in out if not e["routine"] and not e["shift"] and not e["allday"]]
        K.log(TAG, f"fetch: raw={len(evs)} norm={len(out)} real={len(_real)} "
                   f"routine={_r} shift={_s} allday={_a}"
                   + (" | " + "; ".join(f"{e['start'].strftime('%d.%m %H:%M')} {e['title'][:28]}"
                                        for e in _real[:5]) if _real else ""))
    except Exception:
        pass
    return out


def _norm(ev):
    """Google event -> {id,title,start,end,allday,routine,shift,location}."""
    try:
        title = str(ev.get("summary") or "").strip() or "(без назви)"
        s = (ev.get("start") or {})
        e = (ev.get("end") or {})
        s_raw = s.get("dateTime") or s.get("date")
        if not s_raw:
            return None
        allday = "T" not in s_raw
        start = _parse(s_raw)
        if not start:
            return None
        end = _parse(e.get("dateTime") or e.get("date") or "") or (start + timedelta(hours=1))
        low = title.lower()
        return {
            "id": str(ev.get("id") or "")[:80] or K.Dedup.key(title, s_raw),
            "title": title[:120],
            "start": start,
            "end": end,
            "allday": allday,
            "routine": any(r in low for r in ROUTINE),
            "shift": any(w in low for w in SHIFT_WORDS),
            "location": str(ev.get("location") or "")[:80],
        }
    except Exception:
        return None


def _parse(raw: str):
    """ISO -> naive локальний час (UTC+2)."""
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo:
                dt = (dt.astimezone(timezone.utc) + K.TZ).replace(tzinfo=None)
            return dt
        return datetime.strptime(raw[:10], "%Y-%m-%d")
    except Exception:
        return None


# ─── ДЕДУП ───────────────────────────────────────────────────────────────────

def _sent(evid: str, stage: str) -> bool:
    data = K.load(SENT_FILE, default={}) or {}
    return f"{evid}|{stage}" in data


def _mark(evid: str, stage: str):
    K.update_key(SENT_FILE, f"{evid}|{stage}", K.now().isoformat())


def gc_sent(days: int = 5):
    """Прибирає старі ключі дедупу, щоб файл не ріс."""
    data = K.load(SENT_FILE, default={}) or {}
    cutoff = K.now().replace(tzinfo=None) - timedelta(days=days)
    dead = []
    for k, v in data.items():
        try:
            if datetime.fromisoformat(str(v)).replace(tzinfo=None) < cutoff:
                dead.append(k)
        except Exception:
            dead.append(k)
    for k in dead[:60]:
        K.remove_key(SENT_FILE, k)
    return len(dead[:60])


# ─── ТЕКСТИ (без AI — локальні шаблони з ротацією) ───────────────────────────

_T2H = [
    "⏰ <b>Через 2 години</b>",
    "⏰ <b>За 2 години у тебе</b>",
    "🕑 <b>2 години до</b>",
]
_T30 = [
    "🔔 <b>Через 30 хвилин</b>",
    "🔔 <b>Пора збиратись — 30 хв</b>",
    "🔔 <b>Старт за пів години</b>",
]
_HINT_T2H = [
    "Є час підготуватись спокійно.",
    "Встигаєш зібрати все потрібне.",
    "Заклади 10 хв на дорогу і документи.",
]
_HINT_T30 = [
    "Виходь, щоб не бігти.",
    "Останні хвилини — перевір, чи все взяв.",
    "Час вирушати.",
]
_T24 = [
    "📅 <b>Завтра у тебе</b>",
    "📅 <b>Це вже завтра</b>",
    "🗓 <b>Нагадую про завтра</b>",
]
_HINT_T24 = [
    "Сьогодні ще є час підготуватись — документи, дорога, час виїзду.",
    "Подивись, чи не конфліктує зі зміною, і сплануй сон.",
    "Заплануй, коли виїжджаєш — щоб завтра не поспішати.",
]
_T3D = [
    "🗓 <b>Попереджаю заздалегідь</b>",
    "🗓 <b>На горизонті</b>",
    "📆 <b>Наперед: скоро подія</b>",
]
_HINT_T3D = [
    "Ще є кілька днів — встигаєш підготуватись без нервів.",
    "Якщо потрібні документи або запис — краще зробити зараз.",
    "Внеси в план тижня, щоб не накладалось на зміну.",
]


def _pick(arr, seed: str):
    """Стабільна «випадковість» — щоб текст не стрибав при повторі того ж дня."""
    return arr[sum(ord(c) for c in str(seed)) % len(arr)]


def _fmt_when(ev) -> str:
    if ev["allday"]:
        return "весь день"
    return ev["start"].strftime("%H:%M") + "–" + ev["end"].strftime("%H:%M")


def _shift_line(events) -> str:
    sh = K.classify_shift([{"summary": e["title"]} for e in events])
    return {"early": "☀️ Рання зміна 06:00–18:00",
            "night": "🌙 Нічна зміна 18:00–06:00",
            "free": "🏠 Вільний день"}.get(sh, "")


# ─── AI-КОМЕНТАР (жорсткий бюджет кредитів) ──────────────────────────────────
#
# Логіка економії:
#   1) AI-коментар генерується ОДИН РАЗ на подію і кешується (calendar_ai_cache).
#      Стадії t3d / t24h / t2h / t30 / after переюзають той самий коментар —
#      тобто 5 нагадувань = 1 виклик Gemini, а не 5.
#   2) Денний ліміт AI_MAX_PER_DAY викликів. Вичерпався — тихо працюють
#      локальні шаблони (як раніше), нічого не ламається.
#   3) Рутина і зміни AI не отримують взагалі.
#   4) Немає ключа / Gemini впав / порожня відповідь -> локальний шаблон.

AI_MAX_PER_DAY = 14          # максимум AI-викликів на добу по календарю
AI_MAX_CHARS = 420           # обрізаємо, щоб карточка не роздувалась


def _ai_budget_left() -> int:
    data = K.load(AI_BUDGET_FILE, default={}) or {}
    day = K.today_str()
    used = int(data.get(day) or 0)
    return max(0, AI_MAX_PER_DAY - used)


def _ai_budget_use():
    data = K.load(AI_BUDGET_FILE, default={}) or {}
    day = K.today_str()
    K.update_key(AI_BUDGET_FILE, day, int(data.get(day) or 0) + 1)


def _ai_cache_get(evid: str) -> str:
    data = K.load(AI_CACHE_FILE, default={}) or {}
    rec = data.get(str(evid))
    if isinstance(rec, dict):
        return str(rec.get("text") or "")
    return ""


def _ai_cache_put(evid: str, text: str):
    K.update_key(AI_CACHE_FILE, str(evid),
                 {"text": text[:AI_MAX_CHARS], "ts": K.now().isoformat()})


def _day_ctx(ev, events) -> str:
    """Реальний контекст навколо події — щоб AI не вигадував."""
    if not events:
        return ""
    same = [e for e in events
            if e["start"].date() == ev["start"].date() and e["id"] != ev["id"]
            and not e["routine"]]
    sh = K.classify_shift([{"summary": e["title"]} for e in events
                           if e["start"].date() == ev["start"].date()])
    sh_txt = {"early": "рання зміна 06:00-18:00",
              "night": "нічна зміна 18:00-06:00",
              "free": "зміни немає (вільний день)"}.get(sh, "")
    parts = []
    if sh_txt:
        parts.append(f"того дня: {sh_txt}")
    if same:
        parts.append("інші події того дня: "
                     + "; ".join(f"{e['start'].strftime('%H:%M')} {e['title']}" for e in same[:4]))
    return ". ".join(parts)


def _ai_note(ev, stage: str, events=None) -> str:
    """Короткий персональний коментар до події. '' якщо AI недоступний/бюджет вичерпано."""
    if ev.get("routine") or ev.get("shift"):
        return ""
    cached = _ai_cache_get(ev["id"])
    if cached:
        return cached
    if not K.GEMINI_KEY or _ai_budget_left() <= 0:
        return ""
    n = K.now().replace(tzinfo=None)
    days = (ev["start"].date() - n.date()).days
    when_h = ("сьогодні" if days == 0 else "завтра" if days == 1
              else "післязавтра" if days == 2 else f"через {days} днів")
    ctx = _day_ctx(ev, events)
    prompt = (
        "Ти — особистий асистент і коуч Олега (Кошице, Словаччина; працює на Minebea Mitsumi "
        "у зміни; цілі: фінансова незалежність, схуднення до 78 кг, біг, інвестиції).\n"
        "Напиши КОРОТКИЙ коментар українською до події з його календаря: 2-3 речення, "
        "макс 55 слів, тепло і по-дружньому, з 1-2 емодзі.\n"
        "ПРАВИЛА: використовуй ТІЛЬКИ дані нижче, НЕ вигадуй деталей, часу, людей чи місць. "
        "Якщо даних мало — дай практичну пораду з підготовки. Без вступів і без заголовків, "
        "тільки сам текст.\n\n"
        f"ПОДІЯ: {ev['title']}\n"
        f"КОЛИ: {when_h}, {_fmt_when(ev)}\n"
        f"МІСЦЕ: {ev.get('location') or 'не вказано'}\n"
        f"КОНТЕКСТ: {ctx or 'додаткових даних немає'}\n"
        f"ЕТАП НАГАДУВАННЯ: " + {
            "t3d": "попередження за кілька днів — про підготовку наперед",
            "t24h": "за добу — що зробити сьогодні, щоб завтра пройшло гладко",
            "t2h": "за 2 години — коротка підготовка",
            "t30": "за 30 хвилин — час виходити",
            "after": "подія завершилась — коротке підбадьорення і питання як пройшло",
        }.get(stage, "нагадування")
    )
    try:
        txt = (K.gemini_text(prompt, max_tokens=220, temperature=0.85, tag=TAG) or "").strip()
    except Exception as e:
        K.log(TAG, f"ai_note error: {e}")
        return ""
    if not txt:
        return ""
    txt = re.sub(r"^[*#>\s-]+", "", txt).strip()[:AI_MAX_CHARS]
    _ai_budget_use()
    _ai_cache_put(ev["id"], txt)
    K.log(TAG, f"🤖 AI-коментар: {ev['title'][:30]} (бюджет лишилось {_ai_budget_left()})")
    return txt


def _ai_digest(text_block: str, kind: str) -> str:
    """AI-висновок до агенди дня / огляду тижня. 1 виклик на день/тиждень."""
    if not K.GEMINI_KEY or _ai_budget_left() <= 0 or not text_block:
        return ""
    what = ("план на день" if kind == "agenda"
            else "план на завтра" if kind == "tomorrow" else "план на тиждень")
    prompt = (
        "Ти — особистий асистент і коуч Олега (Кошице; змінна робота на Minebea Mitsumi; "
        "цілі: фінансова незалежність, схуднення до 78 кг, біг, інвестиції).\n"
        f"Нижче — його реальний {what} з Google Calendar.\n"
        "Напиши висновок українською: 3-5 речень (макс 90 слів), тепло і мотивуюче, "
        "з 2-3 емодзі. Дай 1-2 КОНКРЕТНІ дії/пріоритети саме з цього списку.\n"
        "ПРАВИЛА: тільки дані зі списку, НЕ вигадуй подій, часу і людей. Якщо список "
        "порожній — скажи, як використати вільний час (біг, інвестиції, відпочинок). "
        "Без заголовків, тільки текст.\n\n"
        + re.sub(r"<[^>]+>", "", text_block)[:1800]
    )
    try:
        txt = (K.gemini_text(prompt, max_tokens=300, temperature=0.85, tag=TAG) or "").strip()
    except Exception as e:
        K.log(TAG, f"ai_digest error: {e}")
        return ""
    if not txt:
        return ""
    _ai_budget_use()
    K.log(TAG, f"🤖 AI-висновок ({kind}), бюджет лишилось {_ai_budget_left()}")
    return re.sub(r"^[*#>\s-]+", "", txt).strip()[:900]


# ─── НАГАДУВАННЯ ПО ГОДИНАХ ──────────────────────────────────────────────────

def _kb_event(pid: str, stage: str):
    if stage == "after":
        return [
            [{"text": "✅ Було", "callback_data": f"cw_done_{pid}"},
             {"text": "❌ Не було", "callback_data": f"cw_miss_{pid}"}],
            [{"text": "⏭ Перенесли", "callback_data": f"cw_moved_{pid}"},
             {"text": "📝 Нотатка", "callback_data": f"cw_note_{pid}"}],
        ]
    return [
        [{"text": "✅ Пам'ятаю", "callback_data": f"cw_ok_{pid}"},
         {"text": "🔔 +15 хв", "callback_data": f"cw_sn_{pid}"}],
        [{"text": "🤖 AI-підготовка", "callback_data": f"cw_ai_{pid}"}],
        [{"text": "📝 Нотатка", "callback_data": f"cw_note_{pid}"},
         {"text": "🚫 Скасовано", "callback_data": f"cw_cancel_{pid}"}],
    ]


def _send_event(ev, stage: str, events=None) -> bool:
    pid = _store.put({"evid": ev["id"], "title": ev["title"], "stage": stage,
                      "start": ev["start"].isoformat(), "when": _fmt_when(ev),
                      "location": ev["location"]})
    loc = f"\n📍 {K.esc(ev['location'])}" if ev["location"] else ""
    if stage == "t3d":
        head, hint = _pick(_T3D, ev["id"]), _pick(_HINT_T3D, ev["id"])
    elif stage == "t24h":
        head, hint = _pick(_T24, ev["id"]), _pick(_HINT_T24, ev["id"])
    elif stage == "t2h":
        head, hint = _pick(_T2H, ev["id"]), _pick(_HINT_T2H, ev["id"])
    elif stage == "t30":
        head, hint = _pick(_T30, ev["id"]), _pick(_HINT_T30, ev["id"])
    else:  # after
        head, hint = "✅ <b>Як пройшло?</b>", "Відповідь збережу — це піде в аналітику тижня."
    dline = ""
    if stage in ("t3d", "t24h"):
        _days = (ev["start"].date() - K.now().replace(tzinfo=None).date()).days
        _dw = ["сьогодні", "завтра", "післязавтра"]
        _human = _dw[_days] if 0 <= _days <= 2 else f"через {_days} дн."
        dline = f"📅 {ev['start'].strftime('%d.%m (%a)')} — {_human}\n"
    ai = _ai_note(ev, stage, events)
    body = f"🤖 {K.esc(ai)}" if ai else f"<i>{hint}</i>"
    text = (f"{head}\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{K.esc(ev['title'])}</b>\n"
            f"{dline}"
            f"🕐 {_fmt_when(ev)}{loc}\n\n"
            f"{body}")
    ok = K.send_card(text, _kb_event(pid, stage), tag=TAG)
    if ok:
        _mark(ev["id"], stage)
        K.log(TAG, f"✅ {stage}: {ev['title']}")
    return ok


def tick() -> int:
    """Головний прохід — викликається листенером раз на хвилину.
    Дивиться ВСІ події і вирішує по кожній окремо. Без AI."""
    sent = 0
    events = _raw_events()
    if events is None:
        return 0
    n = K.now().replace(tzinfo=None)

    for ev in events:
        if ev["allday"] or ev["routine"] or ev["shift"]:
            continue
        mins = (ev["start"] - n).total_seconds() / 60

        # 2-4 дні до події — завчасне попередження (щоб нічого не було раптом)
        if 2880 <= mins <= 5760 and not _sent(ev["id"], "t3d"):
            if _send_event(ev, "t3d", events):
                sent += 1
                continue

        # 24 години до події
        if 1440 - 45 <= mins <= 1440 + 45 and not _sent(ev["id"], "t24h"):
            if _send_event(ev, "t24h", events):
                sent += 1
                continue

        # 2 години до
        if 120 - WINDOW_MIN <= mins <= 120 + WINDOW_MIN and not _sent(ev["id"], "t2h"):
            if _send_event(ev, "t2h", events):
                sent += 1
                continue

        # 30 хвилин до
        if 30 - WINDOW_MIN <= mins <= 30 + WINDOW_MIN and not _sent(ev["id"], "t30"):
            if _send_event(ev, "t30", events):
                sent += 1
                continue

        # після завершення (+15..+40 хв) — «як пройшло?»
        after = (n - ev["end"]).total_seconds() / 60
        if 15 <= after <= 45 and not _sent(ev["id"], "after"):
            if _send_event(ev, "after", events):
                sent += 1

    sent += _fire_snoozed()
    return sent


def _fire_snoozed() -> int:
    """Повторні нагадування після кнопки «+15 хв»."""
    data = K.load(SNOOZE_FILE, default={}) or {}
    if not data:
        return 0
    n = K.now().replace(tzinfo=None)
    fired = 0
    for key, rec in list(data.items()):
        try:
            due = datetime.fromisoformat(str(rec.get("due"))).replace(tzinfo=None)
        except Exception:
            K.remove_key(SNOOZE_FILE, key)
            continue
        if due > n:
            continue
        pid = _store.put({"evid": rec.get("evid"), "title": rec.get("title"),
                          "stage": "snooze", "when": rec.get("when")})
        text = (f"🔔 <b>Нагадую ще раз</b>\n━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 <b>{K.esc(rec.get('title'))}</b>\n"
                f"🕐 {K.esc(rec.get('when'))}\n\n<i>Ти просив нагадати за 15 хвилин.</i>")
        if K.send_card(text, _kb_event(pid, "t30"), tag=TAG):
            fired += 1
        K.remove_key(SNOOZE_FILE, key)
    return fired


# ─── АГЕНДА ДНЯ / ПРЕВ'Ю НА ЗАВТРА ───────────────────────────────────────────

def _daily_done(kind: str) -> bool:
    data = K.load(DAILY_FILE, default={}) or {}
    return data.get(kind) == K.today_str()


def _daily_mark(kind: str):
    K.update_key(DAILY_FILE, kind, K.today_str())


def _day_events(offset: int, events):
    day = (K.now().replace(tzinfo=None) + timedelta(days=offset)).date()
    return [e for e in events if e["start"].date() == day]


def _agenda_text(events, offset: int) -> str:
    day = K.now().replace(tzinfo=None) + timedelta(days=offset)
    head = "☀️ <b>ПЛАН НА СЬОГОДНІ</b>" if offset == 0 else "🌙 <b>ЩО ЧЕКАЄ ЗАВТРА</b>"
    lines = [head, "━━━━━━━━━━━━━━━━━━━━", f"📅 {day.strftime('%d.%m.%Y')}"]
    sh = _shift_line(events)
    if sh:
        lines.append(sh)
    real = [e for e in events if not e["routine"] and not e["shift"]]
    routine_n = sum(1 for e in events if e["routine"])
    lines.append("")
    if real:
        lines.append(f"📌 <b>Реальні події ({len(real)}):</b>")
        for e in real[:10]:
            loc = f" · 📍 {K.esc(e['location'])}" if e["location"] else ""
            lines.append(f"  • <b>{_fmt_when(e)}</b> — {K.esc(e['title'])}{loc}")
    else:
        lines.append("📌 Реальних подій немає — день вільний від зустрічей.")
    if routine_n:
        lines.append(f"\n🔁 Рутини в календарі: {routine_n} (нагадування по них не спамлю)")
    if real:
        first = real[0]
        if offset == 0:
            lines.append(f"\n⏰ Найближче: <b>{K.esc(first['title'])}</b> о "
                         f"{first['start'].strftime('%H:%M')} — нагадаю за 2 год і за 30 хв.")
        else:
            lines.append(f"\n⏰ Перше завтра: <b>{K.esc(first['title'])}</b> о "
                         f"{first['start'].strftime('%H:%M')}.")
    return "\n".join(lines)[:3900]


def agenda(force: bool = False) -> bool:
    """Ранкова агенда — 1 раз на день (05:00–11:00 або force)."""
    n = K.now().replace(tzinfo=None)
    if not force:
        if _daily_done("agenda") or not (5 <= n.hour < 11):
            return False
    events = _raw_events()
    if events is None:
        K.log(TAG, "агенда: календар недоступний — не вигадую")
        return False
    today = _day_events(0, events)
    text = _agenda_text(today, 0)
    real = [e for e in today if not e["routine"] and not e["shift"]]
    pid = _store.put({"stage": "agenda", "day": K.today_str(),
                      "count": len(real),
                      "titles": [e["title"] for e in real][:10]})
    kb = [[{"text": "👍 Прийняв план", "callback_data": f"cw_ack_{pid}"},
           {"text": "📝 Додати нотатку", "callback_data": f"cw_note_{pid}"}]]
    ai = _ai_digest(text, "agenda")
    if ai:
        text = f"{text}\n\n━━━━━━━━━━━━━━━━━━━━\n🤖 <b>AI-висновок</b>\n{K.esc(ai)}"[:3900]
    ok = K.send_card(text, kb, tag=TAG)
    if ok and not force:
        _daily_mark("agenda")
    if ok:
        K.log(TAG, f"✅ агенда дня ({len(real)} реальних подій)")
    return ok


def tomorrow(force: bool = False) -> bool:
    """Вечірній прев'ю на завтра — 1 раз на день (19:00–23:00 або force)."""
    n = K.now().replace(tzinfo=None)
    if not force:
        if _daily_done("tomorrow") or not (19 <= n.hour < 23):
            return False
    events = _raw_events(hours_ahead=54)
    if events is None:
        return False
    text = _agenda_text(_day_events(1, events), 1)
    pid = _store.put({"stage": "tomorrow", "day": (n + timedelta(days=1)).strftime("%Y-%m-%d")})
    kb = [[{"text": "👍 Готовий", "callback_data": f"cw_ack_{pid}"},
           {"text": "📝 Нотатка", "callback_data": f"cw_note_{pid}"}]]
    ai = _ai_digest(text, "tomorrow")
    if ai:
        text = f"{text}\n\n━━━━━━━━━━━━━━━━━━━━\n🤖 <b>AI-погляд на завтра</b>\n{K.esc(ai)}"[:3900]
    ok = K.send_card(text, kb, tag=TAG)
    if ok and not force:
        _daily_mark("tomorrow")
    if ok:
        K.log(TAG, "✅ прев'ю на завтра")
    return ok


# ─── ТИЖДЕНЬ ВПЕРЕД ──────────────────────────────────────────────────────────

_WD = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def week_text(days: int = 7) -> str:
    """Огляд на N днів вперед: день за днем, зміни + реальні події."""
    events = _raw_events(hours_ahead=max(48, days * 24 + 12))
    if events is None:
        return ""
    n = K.now().replace(tzinfo=None)
    lines = ["🗓 <b>ТИЖДЕНЬ ВПЕРЕД</b>", "━━━━━━━━━━━━━━━━━━━━",
             f"📅 {n.strftime('%d.%m')} – {(n + timedelta(days=days - 1)).strftime('%d.%m.%Y')}"]
    total = 0
    for off in range(days):
        day = n + timedelta(days=off)
        dev = _day_events(off, events)
        real = [e for e in dev if not e["routine"] and not e["shift"]]
        sh = K.classify_shift([{"summary": e["title"]} for e in dev])
        sh_ico = {"early": "☀️", "night": "🌙", "free": "🏠"}.get(sh, "")
        head = ("сьогодні" if off == 0 else "завтра" if off == 1
                else f"{_WD[day.weekday()]} {day.strftime('%d.%m')}")
        lines.append("")
        lines.append(f"<b>{_WD[day.weekday()]} {day.strftime('%d.%m')}</b> {sh_ico} "
                     f"<i>({head})</i>" if off < 2 else
                     f"<b>{_WD[day.weekday()]} {day.strftime('%d.%m')}</b> {sh_ico}")
        if real:
            for e in real[:6]:
                loc = f" · 📍 {K.esc(e['location'])}" if e["location"] else ""
                lines.append(f"  • <b>{_fmt_when(e)}</b> — {K.esc(e['title'])}{loc}")
            total += len(real)
        else:
            lines.append("  — порожньо")
    lines.append("")
    if total:
        lines.append(f"📊 Разом реальних подій: <b>{total}</b>. "
                     f"По кожній нагадаю за 2-4 дні, за добу, за 2 год і за 30 хв.")
    else:
        lines.append("📊 Реальних подій у календарі на цей період немає.")
    return "\n".join(lines)[:3900]


def _weekly_done() -> bool:
    data = K.load(WEEKLY_FILE, default={}) or {}
    n = K.now().replace(tzinfo=None)
    return data.get("week") == f"{n.isocalendar()[0]}-{n.isocalendar()[1]}"


def _weekly_mark():
    n = K.now().replace(tzinfo=None)
    K.update_key(WEEKLY_FILE, "week", f"{n.isocalendar()[0]}-{n.isocalendar()[1]}")


def week(force: bool = False) -> bool:
    """Огляд тижня — 1 раз на тиждень: нд 18:00-22:00 або пн 06:00-11:00."""
    n = K.now().replace(tzinfo=None)
    if not force:
        ok_time = (n.weekday() == 6 and 18 <= n.hour < 23) or (n.weekday() == 0 and 6 <= n.hour < 11)
        if _weekly_done() or not ok_time:
            return False
    text = week_text(7)
    if not text:
        K.log(TAG, "тиждень: календар недоступний — не вигадую")
        return False
    pid = _store.put({"stage": "week", "day": K.today_str()})
    kb = [[{"text": "👍 Бачу тиждень", "callback_data": f"cw_ack_{pid}"},
           {"text": "📝 Нотатка", "callback_data": f"cw_note_{pid}"}]]
    ai = _ai_digest(text, "week")
    if ai:
        text = f"{text}\n\n━━━━━━━━━━━━━━━━━━━━\n🤖 <b>AI-стратегія тижня</b>\n{K.esc(ai)}"[:3900]
    ok = K.send_card(text, kb, tag=TAG)
    if ok and not force:
        _weekly_mark()
    if ok:
        K.log(TAG, "✅ огляд тижня надіслано")
    return ok


def upcoming_text(days: int = 7, limit: int = 14) -> str:
    """Компактний рядок для AI-промптів: що заплановано на найближчі дні.
    Формат: «05.08 17:00 Тренування; 07.08 09:30 Лікар». Без AI-викликів."""
    events = _raw_events(hours_ahead=max(48, days * 24 + 12))
    if not events:
        return ""
    n = K.now().replace(tzinfo=None)
    limit_dt = n + timedelta(days=days)
    out = []
    for e in events:
        if e["routine"] or e["shift"] or e["start"] < n or e["start"] > limit_dt:
            continue
        when = "весь день" if e["allday"] else e["start"].strftime("%H:%M")
        out.append(f"{e['start'].strftime('%d.%m')} {when} {e['title']}")
        if len(out) >= limit:
            break
    return "; ".join(out)


# ─── КНОПКИ ──────────────────────────────────────────────────────────────────

def _ack(pid: str, answer: str, extra: dict = None) -> dict:
    """Зберігає відповідь на кнопку — саме те, що Олег просив «зберігай їх»."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    key = f"{p.get('evid') or p.get('stage')}|{p.get('stage')}|{K.today_str()}"
    rec = {"answer": answer, "title": p.get("title") or p.get("stage"),
           "when": p.get("when") or "", "stage": p.get("stage"),
           "ts": K.now().isoformat()}
    if extra:
        rec.update(extra)
    K.update_key(ACK_FILE, K.Dedup.key(key), rec)
    return {"ok": True, "title": rec["title"], "when": rec["when"], "answer": answer}


def do_ok(pid):
    return _ack(pid, "remembered")


def do_cancel(pid):
    p = _store.get(pid)
    if p and p.get("evid"):
        # більше не нагадуємо по цій події
        for stage in ("t3d", "t24h", "t2h", "t30", "after"):
            _mark(p["evid"], stage)
    return _ack(pid, "cancelled")


def do_done(pid):
    return _ack(pid, "done")


def do_miss(pid):
    return _ack(pid, "missed")


def do_moved(pid):
    return _ack(pid, "moved")


def do_agenda_ack(pid):
    return _ack(pid, "accepted")


def do_snooze(pid, minutes: int = 15) -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    due = K.now().replace(tzinfo=None) + timedelta(minutes=minutes)
    K.update_key(SNOOZE_FILE, K.Dedup.key(p.get("evid"), due.strftime("%H%M")),
                 {"evid": p.get("evid"), "title": p.get("title"),
                  "when": p.get("when"), "due": due.isoformat()})
    _ack(pid, f"snooze_{minutes}")
    return {"ok": True, "title": p.get("title"), "minutes": minutes,
            "at": due.strftime("%H:%M")}


def do_ai(pid) -> dict:
    """Кнопка «🤖 AI-підготовка» — розгорнутий план підготовки до конкретної події.
    Один виклик Gemini на клік, у межах денного бюджету."""
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    if not K.GEMINI_KEY:
        return {"ok": False, "error": "no_ai"}
    if _ai_budget_left() <= 0:
        return {"ok": False, "error": "budget"}
    events = _raw_events() or []
    ev = next((e for e in events if e["id"] == p.get("evid")), None)
    ctx = _day_ctx(ev, events) if ev else ""
    prompt = (
        "Ти — особистий асистент Олега (Кошице, Словаччина; змінна робота на Minebea Mitsumi; "
        "цілі: фінансова незалежність, схуднення до 78 кг, біг, інвестиції).\n"
        "Зроби КОНКРЕТНИЙ план підготовки до події з календаря, українською.\n"
        "Формат: 4-6 пунктів списком з емодзі, кожен — коротка дія. В кінці 1 речення "
        "про головний ризик або що не забути.\n"
        "ПРАВИЛА: тільки дані нижче, НЕ вигадуй часу, людей, адрес і деталей.\n\n"
        f"ПОДІЯ: {p.get('title')}\n"
        f"КОЛИ: {p.get('when')}\n"
        f"МІСЦЕ: {p.get('location') or 'не вказано'}\n"
        f"КОНТЕКСТ ДНЯ: {ctx or 'додаткових даних немає'}"
    )
    try:
        txt = (K.gemini_text(prompt, max_tokens=520, temperature=0.8, tag=TAG) or "").strip()
    except Exception as e:
        K.log(TAG, f"do_ai error: {e}")
        return {"ok": False, "error": "ai_failed"}
    if not txt:
        return {"ok": False, "error": "ai_failed"}
    _ai_budget_use()
    _ack(pid, "ai_prep", {"note": txt[:300]})
    return {"ok": True, "title": p.get("title"), "when": p.get("when"),
            "text": re.sub(r"^[*#>\s-]+", "", txt).strip()[:2500]}


def do_note(pid, note: str = "") -> dict:
    p = _store.get(pid)
    if not p:
        return {"ok": False, "error": "payload_missing"}
    text = note or f"Подія: {p.get('title')} ({p.get('when')})"
    try:
        import ai_notes
        ai_notes.add_note(text, source="calendar_watch")
    except Exception:
        pass
    _ack(pid, "noted", {"note": text[:300]})
    return {"ok": True, "title": p.get("title")}


# ─── ЗВІТ ────────────────────────────────────────────────────────────────────

_LABEL = {"remembered": "✅ пам'ятав", "week": "🗓 огляд тижня",
          "ai_prep": "🤖 AI-підготовка", "cancelled": "🚫 скасовано",
          "done": "✅ було", "missed": "❌ не було", "moved": "⏭ перенесено",
          "accepted": "👍 прийняв план", "noted": "📝 нотатка"}


def report(days: int = 7) -> str:
    """/події_відповіді — що саме ти відповідав на нагадування."""
    data = K.load(ACK_FILE, default={}) or {}
    if not data:
        return ("📋 <b>ВІДПОВІДІ НА НАГАДУВАННЯ</b>\n\nЩе порожньо — щойно натиснеш "
                "кнопку під нагадуванням, вона тут з'явиться.")
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
        return f"📋 <b>ВІДПОВІДІ НА НАГАДУВАННЯ</b>\n\nЗа {days} днів відповідей немає."
    rows.sort(reverse=True)
    out = [f"📋 <b>ВІДПОВІДІ НА НАГАДУВАННЯ</b> (за {days} дн.)", "━━━━━━━━━━━━━━━━━━━━"]
    done = sum(1 for _, r in rows if r.get("answer") == "done")
    missed = sum(1 for _, r in rows if r.get("answer") == "missed")
    for ts, r in rows[:20]:
        lab = _LABEL.get(r.get("answer"), str(r.get("answer")))
        out.append(f"{ts.strftime('%d.%m %H:%M')} · <b>{K.esc(r.get('title'))}</b> — {lab}")
        if r.get("note"):
            out.append(f"    📝 {K.esc(r['note'])[:120]}")
    if done or missed:
        out.append(f"\n📊 Виконано: {done} · Не відбулось: {missed}")
    return "\n".join(out)[:3900]


def demo(stage: str = "t30") -> bool:
    """Демо-нагадування (без Google Calendar і без AI) — щоб перевірити,
    що карточка і всі кнопки живі. Подія синтетична, календар не змінюється."""
    n = K.now().replace(tzinfo=None)
    ev = {
        "id": "demo_" + n.strftime("%Y%m%d%H%M"),
        "title": "Демо-подія (перевірка кнопок)",
        "start": n + timedelta(minutes=30),
        "end": n + timedelta(minutes=90),
        "allday": False, "routine": False, "shift": False,
        "location": "Košice",
    }
    return _send_event(ev, stage)


def today_cards(force: bool = True) -> bool:
    """/події — агенда сьогодні + завтра одразу (ручний виклик)."""
    a = agenda(force=True)
    b = tomorrow(force=True)
    return bool(a or b)


if __name__ == "__main__":
    import sys
    if "--agenda" in sys.argv:
        print(_agenda_text(_day_events(0, _raw_events() or []), 0))
    elif "--tick" in sys.argv:
        print("sent:", tick())
    elif "--budget" in sys.argv:
        print("AI-бюджет на сьогодні лишилось:", _ai_budget_left(), "/", AI_MAX_PER_DAY)
    elif "--week" in sys.argv:
        print(week_text(7))
    elif "--upcoming" in sys.argv:
        print(upcoming_text(7))
    elif "--report" in sys.argv:
        print(report())
    else:
        evs = _raw_events()
        print("events:", len(evs or []))
        for e in (evs or [])[:20]:
            print(" ", e["start"], "|", e["title"], "| routine" if e["routine"] else "")
