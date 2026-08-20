#!/usr/bin/env python3
"""
autoquiet.py — ЧЕРГА ВІДКЛАДЕНИХ ПОВІДОМЛЕНЬ під час режиму тиші.

Олег (20.08): «Режим тиші має працювати ТІЛЬКИ коли я введу /тиша, і бот о
04:00 сам відновиться і вимкне режим тиші».

Тому автоматичного визначення сну за графіком змін тут БІЛЬШЕ НЕМА. Єдине
джерело правди — ручний режим тиші з quiet.py (/тиша → до найближчих 04:00,
о 04:00 авто-пробудження).

Роль цього модуля: поки тиша увімкнена, несрочне не губиться, а ЧЕКАЄ в черзі
і після пробудження приходить дайджестом «поки ти спав». Термінове проходить
одразу (URGENT_HINTS).

API:
    sleeping()          -> bool          # = ручний режим тиші увімкнено
    state()             -> {"sleeping","reason","until","shift"}
    should_hold(text)   -> bool          # True → не шли, поклади в чергу
    hold(text, kind)                     # покласти в чергу
    flush()             -> int           # віддати після пробудження
    status_text()       -> str           # /тиша_статус
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


def state(force: bool = False) -> dict:
    """Спить чи ні. ТІЛЬКИ ручний режим тиші (/тиша) — жодної автоматики.
    force залишено для сумісності з викликами."""
    now = _now()
    try:
        import quiet as _q
        on = bool(_q.is_quiet())
        u = _q.until_dt()
    except Exception as e:
        print(f"[{TAG}] quiet недоступний ({e}) — вважаю що тиші нема", flush=True)
        return {"sleeping": False, "reason": "тиша вимкнена", "until": None,
                "shift": "manual"}
    if not on:
        return {"sleeping": False, "reason": "тиша вимкнена", "until": None,
                "shift": "manual"}
    if u is None:
        u = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if u <= now:
            u += timedelta(days=1)
    return {"sleeping": True, "reason": "режим тиші (/тиша)",
            "until": u.isoformat(), "shift": "manual"}


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
    # Тиша — це тиша: під час /тиша відкладаємо ВСЕ, навіть термінове.
    # Нічого не губиться — о 04:00 прийде дайджестом.
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
    lines = ["\U0001f92b <b>РЕЖИМ ТИШІ</b>", "━" * 20]
    if st.get("sleeping"):
        lines.append("Стан: \U0001f4a4 увімкнено вручну (/тиша)")
        lines.append(f"Прокидаюсь сам: <b>{str(st.get('until'))[11:16]}</b>")
        lines.append("Несрочне складаю в чергу, термінове шлю одразу.")
        lines.append("<i>Вийти раніше: /прокинувся</i>")
    else:
        lines.append("Стан: ✅ вимкнено — пишу як звично")
        lines.append("<i>Увімкнути: /тиша (до 04:00)</i>")
    lines.append(f"У черзі: <b>{n}</b>")
    if n:
        lines.append("<i>Віддати зараз: /покажи_відкладене</i>")
    return "\n".join(lines)


if __name__ == "__main__":
    print(status_text())
