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

_store = K.PayloadStore(STORE_FILE)

# Рутина: у нагадуваннях по годинах не потрібна (інакше «вода/чай» спамили б),
# але в агенді дня вона враховується окремим рядком-лічильником.
ROUTINE = [
    "біг", "вода", "чай", "сауна", "armolopid", "армолопід", "ванна", "душ",
    "медитац", "розтяж", "сон", "крок", "вправ", "прокидан", "відбій",
    "навчання інвест", "чек крипто", "пошта", "вітамін", "зарядка",
    "💧", "🍵", "🏃", "🧖", "💊", "📈", "💹", "📬",
]
SHIFT_WORDS = ["зміна", "рання", "нічна", "shift", "☀️", "🌙"]

# Скільки хвилин «допуск» на спрацювання (листенер тикає раз на хвилину,
# але буває лаг/рестарт — тому вікно, а не точна секунда).
WINDOW_MIN = 12

_cache = {"ts": None, "events": []}
_CACHE_SEC = 240


# ─── КАЛЕНДАР ────────────────────────────────────────────────────────────────

def _raw_events(hours_ahead: int = 30):
    """Події з УСІХ календарів на N годин вперед + 3 години назад (для «як пройшло»).
    In-process кеш 4 хв — щоб не дьоргати Google API щохвилини."""
    n = K.now().replace(tzinfo=None)
    if _cache["ts"] and (n - _cache["ts"]).total_seconds() < _CACHE_SEC:
        return _cache["events"]
    try:
        import monitor as M
        token = M._calendar_access_token()
        if not token:
            K.log(TAG, "календар недоступний (немає токена)")
            return None
        headers = {"Authorization": f"Bearer {token}"}
        t_min = datetime.now(timezone.utc) - timedelta(hours=4)
        t_max = datetime.now(timezone.utc) + timedelta(hours=hours_ahead)
        evs = M._fetch_events_all_calendars(headers, t_min, t_max, max_per_cal=40) or []
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
        [{"text": "📝 Нотатка", "callback_data": f"cw_note_{pid}"},
         {"text": "🚫 Скасовано", "callback_data": f"cw_cancel_{pid}"}],
    ]


def _send_event(ev, stage: str) -> bool:
    pid = _store.put({"evid": ev["id"], "title": ev["title"], "stage": stage,
                      "start": ev["start"].isoformat(), "when": _fmt_when(ev),
                      "location": ev["location"]})
    loc = f"\n📍 {K.esc(ev['location'])}" if ev["location"] else ""
    if stage == "t2h":
        head, hint = _pick(_T2H, ev["id"]), _pick(_HINT_T2H, ev["id"])
    elif stage == "t30":
        head, hint = _pick(_T30, ev["id"]), _pick(_HINT_T30, ev["id"])
    else:  # after
        head, hint = "✅ <b>Як пройшло?</b>", "Відповідь збережу — це піде в аналітику тижня."
    text = (f"{head}\n━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>{K.esc(ev['title'])}</b>\n"
            f"🕐 {_fmt_when(ev)}{loc}\n\n"
            f"<i>{hint}</i>")
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

        # 2 години до
        if 120 - WINDOW_MIN <= mins <= 120 + WINDOW_MIN and not _sent(ev["id"], "t2h"):
            if _send_event(ev, "t2h"):
                sent += 1
                continue

        # 30 хвилин до
        if 30 - WINDOW_MIN <= mins <= 30 + WINDOW_MIN and not _sent(ev["id"], "t30"):
            if _send_event(ev, "t30"):
                sent += 1
                continue

        # після завершення (+15..+40 хв) — «як пройшло?»
        after = (n - ev["end"]).total_seconds() / 60
        if 15 <= after <= 45 and not _sent(ev["id"], "after"):
            if _send_event(ev, "after"):
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
    ok = K.send_card(text, kb, tag=TAG)
    if ok and not force:
        _daily_mark("tomorrow")
    if ok:
        K.log(TAG, "✅ прев'ю на завтра")
    return ok


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
        for stage in ("t2h", "t30", "after"):
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

_LABEL = {"remembered": "✅ пам'ятав", "cancelled": "🚫 скасовано",
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
    elif "--report" in sys.argv:
        print(report())
    else:
        evs = _raw_events()
        print("events:", len(evs or []))
        for e in (evs or [])[:20]:
            print(" ", e["start"], "|", e["title"], "| routine" if e["routine"] else "")
