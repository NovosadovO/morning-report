#!/usr/bin/env python3
"""
autoquiet.py — РОЗУМНА ТИША за графіком змін (без /сон вручну).

Скарга Олега: бот писав «ти зараз спиш» коли він був на нічній зміні, і будив
його вдень, коли він відпочивав після нічної. `/сон` це лікує тільки вручну.

Тепер бот сам знає, коли Олег спить:
  • нічна зміна   → спить приблизно 07:00–14:00 (день після зміни)
  • рання зміна   → спить 22:00–05:00
  • вихідний      → спить 23:00–07:00
Джерело правди — Google Calendar через context.get_shift_from_calendar()
(там уже враховано, що нічна зміна почалась ВЧОРА). Немає календаря →
fallback 23:00–06:00, тобто поводимось як раніше, не гірше.

Головне: несрочне не втрачається, а ЧЕКАЄ. Придушені повідомлення складаються
в чергу і після пробудження приходять одним дайджестом «поки ти спав».

Термінове проходить завжди: VIP-лист, крипто-алерт ±5%, подія за годину,
технічна аварія. Список ознак — URGENT_HINTS.

API:
    sleeping()          -> bool
    state()             -> {"sleeping","reason","until","shift"}
    should_hold(text)   -> bool          # True → не шли, поклади в чергу
    hold(text, kind)                     # покласти в чергу
    flush()             -> int           # віддати дайджест після пробудження
    status_text()       -> str           # /тиша
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TAG = "autoquiet"
QUEUE_FILE = "held_msgs.json"
MAX_QUEUE = 25          # більше — вже не дайджест, а спам
MAX_HOLD_HOURS = 10     # старіше не віддаємо: неактуальне
_CACHE_TTL = 300        # с — визначення сну кешуємо (Calendar API дорогий)
_cache = {"data": None, "ts": 0.0}

# Ознаки, за якими повідомлення вважається терміновим і проходить попри сон.
URGENT_HINTS = (
    "🔴", "🚨", "термінов", "критичн", "аварі",
    "vip", "важлив",              # VIP-лист
    "±5", "обвал", "різко", "різкий",
    "через годину", "за 30 хв", "почин", "зараз почн",
    "не працює", "впав", "помилка", "збій",
)

# Ознаки явно НЕсрочного — навіть якщо вгорі є емодзі.
NEVER_URGENT_HINTS = (
    "астро", "гороскоп", "транзит",
    "мотивац", "звичк", "підсумок тижня", "місячний",
    "дайджест", "новини", "цікавинк",
)


def _now():
    try:
        import ai_kit as K
        return K.now().replace(tzinfo=None)
    except Exception:
        return datetime.now()


def _sleep_window(shift_today: str, h: int):
    """(спить?, підпис, година пробудження) для конкретної зміни й години."""
    if shift_today in ("night",):
        # Нічна: працює 18:00–06:00 → спить після зміни вдень.
        if 7 <= h < 14:
            return True, "відпочиває після нічної зміни", 14
        return False, "", 0
    if shift_today in ("after_night",):
        if h < 14:
            return True, "відпочиває після нічної зміни", 14
        return False, "", 0
    if shift_today == "early":
        # Рання: 06:00–18:00 → лягає раніше.
        if h >= 22 or h < 5:
            return True, "спить перед ранньою зміною", 5
        return False, "", 0
    # вихідний / невідомо
    if h >= 23 or h < 7:
        return True, "нічний сон", 7
    return False, "", 0


def state(force: bool = False) -> dict:
    """Спить чи ні + чому. Кешується на 5 хв."""
    import time as _t
    if not force and _cache["data"] is not None and (_t.time() - _cache["ts"]) < _CACHE_TTL:
        return dict(_cache["data"])

    now = _now()
    h = now.hour
    shift = "unknown"
    try:
        import context as _ctx
        info = _ctx.get_shift_from_calendar() or {}
        shift = str(info.get("today") or "free")
    except Exception as e:
        print(f"[{TAG}] календар недоступний ({e}) — fallback 23:00–07:00", flush=True)

    # На зміні Олег НЕ спить — навіть якщо година «нічна».
    working_night = (shift == "night" and (h >= 18 or h < 6))
    working_early = (shift == "early" and 6 <= h < 18)

    if working_night or working_early:
        res = {"sleeping": False, "reason": "на зміні", "until": None, "shift": shift}
    else:
        slp, why, wake_h = _sleep_window(shift, h)
        until = None
        if slp:
            until = now.replace(hour=wake_h % 24, minute=0, second=0, microsecond=0)
            if until <= now:
                until += timedelta(days=1)
        res = {"sleeping": slp, "reason": why or "активний час",
               "until": until.isoformat() if until else None, "shift": shift}

    _cache["data"] = res
    _cache["ts"] = _t.time()
    return dict(res)


def sleeping() -> bool:
    try:
        return bool(state().get("sleeping"))
    except Exception:
        return False


def is_urgent(text: str) -> bool:
    t = str(text or "").lower()
    if any(h in t for h in NEVER_URGENT_HINTS):
        return False
    return any(h in t for h in URGENT_HINTS)


def should_hold(text: str = "") -> bool:
    """True → зараз не шлемо, кладемо в чергу."""
    try:
        # Олег сам щось написав/натиснув — відповідаємо негайно.
        import quiet as _q
        if _q.is_user_thread() or _q.user_recently_active():
            return False
    except Exception:
        pass
    if not sleeping():
        return False
    if is_urgent(text):
        print(f"[{TAG}] ⚡ термінове — шлю попри сон", flush=True)
        return False
    return True


# ─── ЧЕРГА ───────────────────────────────────────────────────────────────────

def hold(text: str, kind: str = "msg", keyboard=None) -> bool:
    """Відкладає повідомлення. keyboard зберігаємо разом з текстом — інакше
    після пробудження Олег отримав би пропозицію без кнопок і не міг би її
    прийняти."""
    try:
        import ai_kit as K
        q = K.load(QUEUE_FILE, default={"items": []}) or {}
        items = q.get("items") if isinstance(q, dict) else None
        if not isinstance(items, list):
            items = []
        items.append({"ts": _now().isoformat(), "kind": kind,
                      "text": str(text or "")[:1200],
                      "keyboard": keyboard if keyboard else None})
        K.save(QUEUE_FILE, {"items": items[-MAX_QUEUE:]})
        st = state()
        print(f"[{TAG}] 💤 відкладено ({st.get('reason')}, до "
              f"{str(st.get('until'))[11:16]}): {str(text)[:60]}", flush=True)
        return True
    except Exception as e:
        print(f"[{TAG}] hold error: {e}", flush=True)
        return False


def pending() -> int:
    try:
        import ai_kit as K
        q = K.load(QUEUE_FILE, default={"items": []}) or {}
        return len(q.get("items") or [])
    except Exception:
        return 0


def flush(force: bool = False) -> int:
    """Після пробудження віддає відкладене одним дайджестом. Повертає кількість."""
    try:
        import ai_kit as K
        if not force and sleeping():
            return 0
        q = K.load(QUEUE_FILE, default={"items": []}) or {}
        items = [i for i in (q.get("items") or []) if isinstance(i, dict)]
        if not items:
            return 0

        now = _now()
        fresh = []
        for it in items:
            try:
                age = (now - datetime.fromisoformat(str(it.get("ts")))).total_seconds() / 3600
            except Exception:
                age = 0
            if age <= MAX_HOLD_HOURS:
                fresh.append(it)
        K.save(QUEUE_FILE, {"items": []})
        if not fresh:
            print(f"[{TAG}] черга протермінована — нічого не шлю", flush=True)
            return 0

        # З кнопками — окремими картками (інакше кнопки втрачаються).
        with_kb = [i for i in fresh if i.get("keyboard")]
        plain = [i for i in fresh if not i.get("keyboard")]

        head = (f"☀️ <b>Поки ти спав</b> — {len(fresh)} "
                f"{'повідомлення' if len(fresh) == 1 else 'повідомлень'}\n"
                f"<i>Нічого термінового не було, тому не будив.</i>\n"
                "━━━━━━━━━━━━━━━━━━━━")
        if plain:
            parts = [head]
            for it in plain:
                t = str(it.get("ts") or "")[11:16]
                parts.append(f"\n🕐 <b>{t}</b>\n{it.get('text')}")
            K.send_card("\n".join(parts)[:3900])
        elif with_kb:
            K.send_card(head)
        for it in with_kb[:8]:
            t = str(it.get("ts") or "")[11:16]
            kb = it.get("keyboard")
            kb = kb.get("inline_keyboard") if isinstance(kb, dict) else kb
            K.send_card(f"🕐 <i>{t}</i>\n{it.get('text')}", keyboard=kb)
        print(f"[{TAG}] ☀️ віддано відкладених: {len(fresh)} "
              f"(з кнопками: {len(with_kb)})", flush=True)
        return len(fresh)
    except Exception as e:
        print(f"[{TAG}] flush error: {e}", flush=True)
        return 0


def status_text() -> str:
    st = state(force=True)
    n = pending()
    lines = ["🤫 <b>РОЗУМНА ТИША</b>", "━━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"Зміна сьогодні: <b>{st.get('shift')}</b>")
    if st.get("sleeping"):
        lines.append(f"Стан: 💤 {st.get('reason')}")
        lines.append(f"Прокидаюсь: <b>{str(st.get('until'))[11:16]}</b>")
        lines.append("Несрочне складаю в чергу, термінове шлю одразу.")
    else:
        lines.append(f"Стан: ✅ {st.get('reason')} — пишу як звично")
    lines.append(f"У черзі: <b>{n}</b>")
    if n:
        lines.append("<i>Віддати зараз: /покажи_відкладене</i>")
    return "\n".join(lines)


if __name__ == "__main__":
    print(status_text())
