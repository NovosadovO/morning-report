# -*- coding: utf-8 -*-
"""askme.py — бот ПИТАЄ, а кнопки завжди відповідають саме цьому питанню.

Правила, які тут зашиті:
1. Кнопки будуються з питання. Питання «запланувати?» → «📅 Так, запиши /
   🕒 Інший час / 🚫 Не треба». Питання «це ще актуально?» → «✅ Ще актуально /
   👌 Вже вирішив / 🚫 Не актуально». Ніяких абстрактних «Прийняв».
2. Відповідь запам'ятовується НАЗАВЖДИ і те саме питання більше не ставиться.
3. У календар іде лише справді важливе. Реклама й розсилки — ніколи.
4. Події, які минули, бот розуміє як минулі й акуратно перепитує, чи відбулось.
5. Порожній день — бот пропонує конкретне корисне, а не абстракцію.
"""

from datetime import datetime, timedelta

import ai_kit as K

TAG = "askme"
FILE = "askme.json"          # відповіді назавжди
STORE_FILE = "askme_store.json"
MARK = "⁣ASKME⁣"
REASK_DAYS = 3               # без відповіді — перепитати не раніше
CTX_MAX = 14
MAX_PER_SWEEP = 2            # не більше 2 питань за прохід

_store = K.PayloadStore(STORE_FILE)

# ─── НАБОРИ КНОПОК ПІД ТИП ПИТАННЯ ───────────────────────────────────────────
SETS = {
    "plan": [("plan", "📅 Так, запиши"), ("other", "🕒 Інший час"),
             ("no", "🚫 Не треба")],
    "relevant": [("yes", "✅ Ще актуально"), ("done", "👌 Вже вирішив"),
                 ("drop", "🚫 Не актуально")],
    "happened": [("done", "✅ Відбулось"), ("moved", "🔁 Перенеслось"),
                 ("cancel", "❌ Скасувалось")],
    "confirm": [("yes", "✅ Так"), ("no", "❌ Ні"), ("later", "⏰ Пізніше")],
    # Нагадування: пишеться в reminders.json ЛИШЕ після «🔔 Так, нагадай»
    "remind": [("save", "🔔 Так, нагадай"), ("other", "🕒 Інший час"),
               ("no", "🚫 Не треба")],
    # Будь-який інший запис (нотатка, покупка, задача) — теж лише після «так»
    "write": [("save", "💾 Так, запиши"), ("no", "🚫 Не треба")],
}

MEANS = {
    "plan": "погодився запланувати",
    "other": "хоче інший час",
    "no": "відмовився",
    "yes": "підтвердив",
    "done": "сказав, що вже зроблено",
    "drop": "сказав, що не актуально",
    "moved": "сказав, що перенеслось",
    "cancel": "сказав, що скасувалось",
    "later": "відклав",
    "save": "погодився поставити нагадування",
}

ACK = {
    "other": "🕒 Добре — напиши час, і я запишу саме на нього.",
    "no": "🚫 Записав: не треба. Більше не пропоную.",
    "yes": "✅ Записав.",
    "done": "👌 Записав: уже зроблено. Не питаю більше.",
    "drop": "🚫 Записав: не актуально. Знімаю з радарів.",
    "moved": "🔁 Записав: перенеслось. Скажи новий час — поставлю.",
    "cancel": "❌ Записав: скасувалось.",
    "later": "⏰ Добре, повернусь пізніше.",
    "save": "🔔 Поставив нагадування.",
}

# ─── ФІЛЬТР РЕКЛАМИ (у календар таке НЕ потрапляє) ───────────────────────────
_PROMO = (
    "акці", "скидк", "знижк", "промокод", "розпродаж", "sale", "promo",
    "offer", "discount", "newsletter", "розсилк", "unsubscribe",
    "відписат", "no-reply", "noreply", "marketing", "вебінар", "webinar",
    "черн", "black friday", "bonus", "бонус", "cashback", "кешбек",
    "тільки сьогодні", "останній шанс", "limited", "%off", "-50%",
    "реклам", "spam", "заробіт", "казино", "ставк",
)
_IMPORTANT = (
    "рахунок", "invoice", "faktur", "оплат", "платіж", "договір", "contract",
    "лікар", "doctor", "termin", "зустріч", "meeting", "співбесід",
    "interview", "дедлайн", "deadline", "суд", "податк", "tax", "school",
    "школ", "сад", "виза", "віза", "паспорт", "техогляд", "стк", "страхов",
)


def _log(m):
    print("[" + TAG + "] " + str(m), flush=True)


def _now():
    return K.now().replace(tzinfo=None)


def _dt(v):
    try:
        return datetime.fromisoformat(str(v)[:19].replace(" ", "T"))
    except Exception:
        return None


def is_promo(text: str) -> bool:
    """True → це реклама/розсилка. У календар не кладемо, питань не ставимо."""
    t = str(text or "").lower()
    if not t:
        return False
    if any(w in t for w in _IMPORTANT):
        return False
    return any(w in t for w in _PROMO)


# ─── ПАМ'ЯТЬ ВІДПОВІДЕЙ ──────────────────────────────────────────────────────

def _load() -> dict:
    return K.load(FILE, default={}) or {}


def answer_of(key: str):
    """Відповідь Олега на це питання або None."""
    if not key:
        return None
    r = (_load() or {}).get(str(key)[:80])
    if r and r.get("action"):
        return r
    return None


def answered(key: str) -> bool:
    return bool(answer_of(key))


def _asked_recently(key: str) -> bool:
    r = (_load() or {}).get(str(key)[:80]) or {}
    ts = _dt(r.get("asked_at"))
    if not ts:
        return False
    return (_now() - ts) < timedelta(days=REASK_DAYS)


def _remember(key, patch: dict) -> None:
    r = ((_load() or {}).get(str(key)[:80]) or {})
    r.update(patch)
    try:
        K.update_key(FILE, str(key)[:80], r)
    except Exception as e:
        _log("save error: " + str(e))


# ─── ПИТАННЯ З ДОРЕЧНИМИ КНОПКАМИ ────────────────────────────────────────────

def ask(question: str, kind: str = "confirm", key: str = "",
        options=None, meta=None, tag: str = "", force: bool = False) -> bool:
    """Поставити питання. Кнопки = варіанти відповіді САМЕ на це питання.

    Не питає, якщо Олег уже відповів (назавжди) або питали <REASK_DAYS тому.
    """
    q = str(question or "").strip()
    if not q:
        return False
    key = str(key or q)[:80]
    if is_promo(q + " " + str((meta or {}).get("summary", ""))):
        _log("реклама — питання не ставлю: " + q[:60])
        return False
    if _is_tracker(str((meta or {}).get("summary", ""))):
        _log("трекер/галочка — питання не ставлю: " +
             str((meta or {}).get("summary", ""))[:60])
        return False
    if not force:
        if answered(key):
            _log("уже відповів — не питаю вдруге: " + key[:50])
            return False
        if _asked_recently(key):
            return False
    opts = list(options or SETS.get(kind) or SETS["confirm"])
    try:
        pid = _store.put({"key": key, "q": q[:400], "kind": kind,
                          "opts": opts, "meta": meta or {},
                          "ts": _now().isoformat(timespec="seconds")})
    except Exception as e:
        _log("store error: " + str(e))
        return False
    btns = [{"text": lbl, "callback_data": "am_" + act + "_" + pid}
            for act, lbl in opts]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    ok = False
    try:
        ok = K.send_card(q, rows, tag=tag or TAG)
    except Exception as e:
        _log("send error: " + str(e))
    if ok:
        _remember(key, {"q": q[:400], "kind": kind,
                        "asked_at": _now().isoformat(timespec="seconds")})
        _log("запитав: " + q[:70])
    return bool(ok)


def buttons(question: str, kind: str = "confirm", key: str = "",
            options=None, meta=None):
    """Кнопки під це питання БЕЗ відправки (для autokb / інших модулів).

    None → питати не треба: реклама, трекер або Олег уже відповів.
    """
    q = str(question or "").strip()
    if not q:
        return None
    key = str(key or q)[:80]
    meta = meta or {}
    if is_promo(q + " " + str(meta.get("summary", ""))):
        return None
    if _is_tracker(str(meta.get("summary", ""))):
        return None
    if answered(key):
        return None
    opts = list(options or SETS.get(kind) or SETS["confirm"])
    try:
        pid = _store.put({"key": key, "q": q[:400], "kind": kind,
                          "opts": opts, "meta": meta,
                          "ts": _now().isoformat(timespec="seconds")})
    except Exception as e:
        _log("store error: " + str(e))
        return None
    _remember(key, {"q": q[:400], "kind": kind,
                    "asked_at": _now().isoformat(timespec="seconds")})
    btns = [{"text": lbl, "callback_data": "am_" + act + "_" + pid}
            for act, lbl in opts]
    return [btns[i:i + 2] for i in range(0, len(btns), 2)]


# ─── ОБРОБКА НАТИСКАННЯ ──────────────────────────────────────────────────────

def handle(data: str, cb=None) -> dict:
    """am_<action>_<pid>. Виконує дію і запам'ятовує відповідь назавжди."""
    if not str(data or "").startswith("am_"):
        return {"text": "", "alert": False, "keyboard": None}
    action, _, pid = data[3:].rpartition("_")
    if not action or not pid:
        return {"text": "⚠️ Не зрозумів кнопку", "alert": True,
                "keyboard": None}
    p = _store.get(pid) or {}
    key = p.get("key") or ""
    q = p.get("q") or ""
    meta = p.get("meta") or {}
    label = dict(p.get("opts") or []).get(action, action)
    txt = ACK.get(action, "Записав.")

    if action == "plan":
        txt = _do_plan(meta, q)
    elif action == "save":
        txt = _do_save(meta, q)
    elif action in ("drop", "no", "cancel"):
        try:
            import dismissed as D
            D.mute(TAG, key=key or None, title=q[:120] or None,
                   note="відповідь кнопкою: " + str(MEANS.get(action, action)))
        except Exception as e:
            _log("dismissed skip: " + str(e))

    _remember(key, {
        "q": q[:400], "action": action, "label": label,
        "means": str(MEANS.get(action, action)),
        "answered_at": _now().isoformat(timespec="seconds"),
        "meta": {k: str(v)[:80] for k, v in list(meta.items())[:6]},
    })
    _log("відповідь: «" + str(label) + "» на «" + q[:60] + "»")
    kb = [[{"text": "✓ " + str(label), "callback_data": "am_seen_" + pid}]]
    if action == "seen":
        return {"text": "Вже записано.", "alert": False, "keyboard": None}
    try:
        _store.drop(pid)
    except Exception:
        pass
    return {"text": txt, "alert": True, "keyboard": kb}


def _do_plan(meta: dict, q: str) -> str:
    """Створює РЕАЛЬНУ подію в календарі — лише якщо це не реклама."""
    summary = str(meta.get("summary") or q)[:110]
    if is_promo(summary + " " + str(meta.get("desc") or "")):
        return "🚫 Це схоже на рекламу — у календар не кладу."
    start = _dt(meta.get("start"))
    if not start:
        start = (_now() + timedelta(hours=2)).replace(minute=0, second=0,
                                                      microsecond=0)
    mins = int(meta.get("minutes") or 60)
    try:
        import calgate as _cg
        _cg.allow_once()
    except Exception:
        pass
    try:
        res = K.calendar_event(summary, start, start + timedelta(minutes=mins),
                               description=str(meta.get("desc") or
                                               "Створено з питання бота."))
        ok = bool(res) and (not isinstance(res, dict) or
                            res.get("ok", True) is not False)
    except Exception as e:
        _log("calendar error: " + str(e))
        ok = False
    when = start.strftime("%d.%m %H:%M")
    if ok:
        return "📅 Записав у календар: « " + summary + " » на " + when + "."
    return ("⚠️ У календар не вдалось записати (« " + summary + " » на " +
            when + "). Спробую ще раз пізніше.")


def _do_save(meta: dict, q: str) -> str:
    """Реальний запис — лише після «так». Нотатка/покупка/задача → ai_notes,
    усе інше → нагадування в reminders.json."""
    title = str(meta.get("summary") or q)[:110]
    if is_promo(title + " " + str(meta.get("desc") or "")):
        return "🚫 Це схоже на рекламу — не записую."
    if str(meta.get("kind") or "") in ("note", "shopping", "task"):
        return _do_save_note(meta, title)
    start = _dt(meta.get("start"))
    if not start:
        start = (_now() + timedelta(hours=2)).replace(minute=0, second=0,
                                                      microsecond=0)
    body = str(meta.get("desc") or "")[:400]
    try:
        data = K.load("reminders.json", default=[]) or []
        if not isinstance(data, list):
            data = []
        rid = "askme_" + start.strftime("%Y%m%d%H%M")
        if any(isinstance(r, dict) and r.get("id") == rid for r in data):
            return "🔔 Таке нагадування вже стоїть."
        text = "🔔 <b>" + K.esc(title) + "</b>"
        if body:
            text += "\n\n" + K.esc(body)
        data.append({
            "id": rid,
            "datetime_utc": start.replace(tzinfo=None).isoformat(
                timespec="seconds"),
            "text": text,
            "sent": False,
        })
        K.save("reminders.json", data)
    except Exception as e:
        _log("reminder save error: " + str(e))
        return "⚠️ Не вдалось поставити нагадування — спробую ще раз."
    when = start.strftime("%d.%m о %H:%M")
    # Дублюємо подією в календар — дозвіл Олег уже дав цією ж кнопкою
    cal = ""
    if meta.get("calendar") is not False:
        try:
            import calgate as _cg
            _cg.allow_once()
            res = K.calendar_event("🔔 " + title[:90], start,
                                   start + timedelta(minutes=30), body[:300])
            if res and res.get("ok"):
                cal = " + подія в календарі"
        except Exception as e:
            _log("reminder calendar skip: " + str(e))
    return "🔔 Нагадаю: « " + title + " » " + when + "." + cal


def _do_save_note(meta: dict, title: str) -> str:
    """Нотатка/покупка/задача в ai_notes — лише після «💾 Так, запиши»."""
    body = str(meta.get("desc") or "")[:400]
    text = title + ((" — " + body) if body else "")
    try:
        import ai_notes
        ai_notes.add_note(text, source="askme")
    except Exception as e:
        _log("note save error: " + str(e))
        return "⚠️ Не вдалось записати нотатку — спробую ще раз."
    kind = str(meta.get("kind") or "note")
    label = {"note": "📝 Записав у нотатки",
             "shopping": "🛒 Додав у список покупок",
             "task": "✅ Поставив задачу"}.get(kind, "📝 Записав")
    return label + ": « " + title + " »."


# ─── ЩО РОБИТИ САМОМУ: МИНУЛІ ПОДІЇ, ПРОСТРОЧЕНЕ, ПОРОЖНІЙ ДЕНЬ ──────────────

_ROUTINE = ("зміна", "shift", "нічна", "рання", "сон", "будильник", "обід",
            "робота", "work")

# Записи-галочки й трекери звичок — це НЕ події, про них не перепитуємо.
_STATUS_MARKS = ("✅", "❌", "☑", "✔", "🚫", "✓", "[x]", "[ ]")
_HABITISH = ("вода", "душ", "сауна", "чай", "спрей", "вітамін", "крем",
             "зарядк", "розтяжк", "медитац", "щоденник", "трекер", "habit",
             "звичк", "калор", "вага", "крок", "steps", "чекліст", "checklist",
             "план дня", "рутин", "виконано", "done")


def _is_tracker(title: str) -> bool:
    """Галочка звички/трекер, а не подія: «🏃 Біг ❌», «💧 Вода (2л+) ✅»."""
    t = str(title or "")
    if any(m in t for m in _STATUS_MARKS):
        return True
    low = t.lower()
    return any(w in low for w in _HABITISH)


def _ev_title(ev) -> str:
    if isinstance(ev, dict):
        return str(ev.get("summary") or ev.get("title") or "")[:110]
    return str(ev)[:110]


def _ev_start(ev):
    if not isinstance(ev, dict):
        return None
    st = ev.get("start")
    if isinstance(st, dict):
        return _dt(st.get("dateTime") or st.get("date"))
    return _dt(st)


def _is_routine(title: str) -> bool:
    t = str(title or "").lower()
    return any(w in t for w in _ROUTINE)


def sweep() -> int:
    """Раз на годину: перепитати про минуле, запропонувати корисне. Макс 2."""
    asked = 0
    now = _now()

    # 1) Події, які МИНУЛИ — акуратно перепитати, чи відбулось
    for off in (-1, 0):
        if asked >= MAX_PER_SWEEP:
            break
        try:
            evs = K.events_for_day(off) or []
        except Exception as e:
            _log("events error: " + str(e))
            evs = []
        for ev in evs:
            if asked >= MAX_PER_SWEEP:
                break
            title = _ev_title(ev)
            st = _ev_start(ev)
            if not title or not st or _is_routine(title) or is_promo(title):
                continue
            if _is_tracker(title):
                continue
            # Питаємо лише про те, що справді варте питання: зустрічі, люди,
            # платежі, візити, дзвінки, дедлайни — а не про побутові позначки.
            if len(title.strip()) < 5:
                continue
            age_h = (now - st).total_seconds() / 3600.0
            if age_h < 1.5 or age_h > 30:
                continue
            key = "past|" + title.lower()[:50] + "|" + st.strftime("%Y-%m-%d")
            q = ("🕰 « " + title + " » було " + st.strftime("%d.%m о %H:%M") +
                 " — тобто вже минуло.\n\nВідбулось, чи переносимо?")
            if ask(q, kind="happened", key=key, tag="MSG_PAST_EVENT",
                   meta={"summary": title}):
                asked += 1

    # 2) Прострочені нагадування — чи ще актуально
    if asked < MAX_PER_SWEEP:
        try:
            rem = K.load("reminders.json", default=[]) or []
        except Exception:
            rem = []
        if isinstance(rem, dict):
            rem = rem.get("items") or []
        for r in list(rem)[:40]:
            if asked >= MAX_PER_SWEEP:
                break
            if not isinstance(r, dict) or r.get("done"):
                continue
            title = str(r.get("text") or r.get("title") or "")[:110]
            due = _dt(r.get("when") or r.get("due") or r.get("date"))
            if not title or not due or is_promo(title) or _is_tracker(title):
                continue
            over_h = (now - due).total_seconds() / 3600.0
            if over_h < 3 or over_h > 24 * 14:
                continue
            key = "overdue|" + title.lower()[:50]
            q = ("⏳ « " + title + " » мало бути " +
                 due.strftime("%d.%m о %H:%M") + " — строк минув " +
                 str(int(over_h / 24)) + " дн. тому.\n\nЦе ще актуально?")
            if ask(q, kind="relevant", key=key, tag="MSG_OVERDUE",
                   meta={"summary": title}):
                asked += 1

    # 3) Вільний день без планів — запропонувати конкретне корисне
    if asked < MAX_PER_SWEEP and now.hour in (18, 19, 20):
        try:
            evs = K.events_for_day(1) or []
        except Exception:
            evs = []
        real = [e for e in evs if not _is_routine(_ev_title(e))]
        shift = [e for e in evs if _is_routine(_ev_title(e))]
        if not real and not shift:
            tom = (now + timedelta(days=1)).replace(hour=8, minute=0,
                                                    second=0, microsecond=0)
            q = ("🗓 Завтра " + tom.strftime("%d.%m") + " у календарі порожньо "
                 "і зміни немає.\n\nЗапланувати пробіжку на 08:00? Ти казав, "
                 "що ціль 78 кг — регулярний біг це найкоротший шлях.")
            if ask(q, kind="plan",
                   key="freeday|" + tom.strftime("%Y-%m-%d"),
                   tag="MSG_FREE_DAY",
                   meta={"summary": "🏃 Пробіжка", "start": tom.isoformat(),
                         "minutes": 60,
                         "desc": "Запропонував бот: вільний день, ціль 78 кг."}):
                asked += 1
    if asked:
        _log("поставлено питань: " + str(asked))
    return asked


# ─── AI: НЕ ПИТАТИ ТЕ, НА ЩО ВЖЕ Є ВІДПОВІДЬ ─────────────────────────────────

def block() -> str:
    data = _load()
    if not data:
        return ""
    done, open_q = [], []
    for k, r in data.items():
        if not isinstance(r, dict):
            continue
        ts = _dt(r.get("answered_at")) or _dt(r.get("asked_at"))
        if r.get("action"):
            done.append((ts or _now(), r))
        elif r.get("asked_at"):
            open_q.append((ts or _now(), r))
    if not done and not open_q:
        return ""
    out = ["", "ВІДПОВІДІ ОЛЕГА НА ПИТАННЯ БОТА (реальні натискання):"]
    done.sort(key=lambda x: x[0], reverse=True)
    for ts, r in done[:CTX_MAX]:
        out.append("• «" + str(r.get("q", ""))[:90] + "» → натиснув «" +
                   str(r.get("label") or r.get("action")) + "» = " +
                   str(r.get("means") or "") + " (" +
                   ts.strftime("%d.%m %H:%M") + ")")
    out.append("Ці питання ВЖЕ закриті — не ставити їх удруге, не пропонувати "
               "те, від чого він відмовився, і не питати про те, що він "
               "назвав зробленим.")
    if open_q:
        open_q.sort(key=lambda x: x[0], reverse=True)
        out.append("ПИТАННЯ БЕЗ ВІДПОВІДІ (уже задані, не дублюй): " +
                   "; ".join(str(r.get("q", ""))[:70] for _, r in open_q[:6]))
    return "\n".join(out)


def inject(body_bytes, tag=""):
    try:
        import json as _js
        b = _js.loads(body_bytes.decode())
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
        b["contents"][0]["parts"][0]["text"] = p + "\n" + blk + "\n" + MARK
        _log("пам'ять відповідей додана → " + str(tag))
        return _js.dumps(b).encode()
    except Exception as e:
        _log("inject error: " + str(e))
        return body_bytes


def report(limit: int = 20) -> str:
    data = _load()
    if not data:
        return ("❓ <b>Питання бота</b>\n\nЩе жодного питання не задано.")
    rows = sorted(
        [r for r in data.values() if isinstance(r, dict)],
        key=lambda r: str(r.get("answered_at") or r.get("asked_at") or ""),
        reverse=True)[:limit]
    out = ["❓ <b>Питання і твої відповіді</b>", ""]
    for r in rows:
        when = str(r.get("answered_at") or r.get("asked_at") or "")[:16]
        when = when.replace("T", " ")
        if r.get("action"):
            out.append("✅ " + when + " — « " + str(r.get("q", ""))[:70] +
                       " »\n    → " + str(r.get("label")) + " (" +
                       str(r.get("means")) + ")")
        else:
            out.append("⏳ " + when + " — « " + str(r.get("q", ""))[:70] +
                       " » — чекаю відповіді")
    out.append("")
    out.append("Відповіді пам'ятаю назавжди — те саме більше не питаю.")
    return "\n".join(out)
