#!/usr/bin/env python3
"""
astro_view.py — карта транзитів + кнопки під астро-розбором годинного звіту.

Раніше астро в годинному звіті йшло лише текстом на 800-1000 слів. Тепер, коли в
розборі є НОВІ транзити (astro_delta), слідом іде картинка карти (натал+транзити,
astro_chart) з коротким підписом і кнопками, що відповідають на питання
«який із нових аспектів розібрати детальніше?» — по одній кнопці на кожен
реальний новий аспект + «Без карти».

Потік:
  capture(transit_raw)      — monitor викликає ПЕРЕД astro_delta.mark(): запам'ятати,
                              що саме нове (після mark воно вже «старе»).
  send_after_report()       — після відправки астро-розбору: карта + кнопки.
  handle("av_<pid>_<i>")    — кнопка: короткий розбір саме цього аспекту.
Не частіше разу на 12 год; нема нового — мовчимо; тиша/mute поважаються.
Callback-префікс: av_.
"""
import json
import os
import re

import ai_kit as K

TAG = "astro_view"
PENDING = "astro_view_pending.json"
STORE_FILE = "astro_view_store.json"
STATE_FILE = "astro_view_state.json"
MIN_GAP_H = 12
MAX_BTN = 3

_store = K.PayloadStore(STORE_FILE)


def _log(m):
    K.log(TAG, m)


def _why(t):
    try:
        import watch_health
        watch_health.why(TAG, t)
    except Exception:
        pass


def pick_new(new: list) -> list:
    """Найточніші нові аспекти; Місяць (щоденний) — лише якщо інших нема."""
    slow = [a for a in new if a.get("tp") not in ("Місяць",) and a.get("np") not in ("Місяць",)]
    pool = slow or list(new)
    pool.sort(key=lambda a: a.get("orb", 9))
    return pool[:MAX_BTN]


def capture(transit_raw: str) -> None:
    try:
        import astro_delta
        new, _old = astro_delta.classify(transit_raw)
        cur = K.load(PENDING, default={}) or {}
        if not new and cur.get("day") == K.today_str() and cur.get("new"):
            return          # не затираємо ще не відправлене «нове» порожнім
        K.save(PENDING, {"day": K.today_str(), "ts": K.now().isoformat(timespec="seconds"),
                         "new": pick_new(new)})
    except Exception as e:
        _log("capture: " + str(e))


def _label(a: dict) -> str:
    return f"{a['tp']} {a['asp']} {a['np']}"


def _send_photo(png_path: str, caption: str, kb) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat and png_path and os.path.exists(png_path)):
        return False
    try:
        import requests as _rq
        with open(png_path, "rb") as f:
            r = _rq.post("https://api.telegram.org/bot" + token + "/sendPhoto",
                         data={"chat_id": chat, "caption": caption[:1000], "parse_mode": "HTML",
                               "reply_markup": json.dumps({"inline_keyboard": kb})},
                         files={"photo": ("astro.png", f, "image/png")}, timeout=60)
        return getattr(r, "status_code", 0) == 200
    except Exception as e:
        _log("sendPhoto: " + str(e))
        return False


def send_after_report(force: bool = False) -> bool:
    try:
        import quiet
        if not force and quiet.blocked("msg"):
            _why("тиха година / режим сну")
            return False
    except Exception:
        pass
    pend = K.load(PENDING, default={}) or {}
    new = pend.get("new") or []
    if not force:
        if pend.get("day") != K.today_str() or not new:
            _why("нових транзитів у цьому розборі нема — карту не шлю")
            return False
        st = K.load(STATE_FILE, default={}) or {}
        if st.get("pause_until", "") > K.today_str():
            _why("карту вимкнено кнопкою «Без карти» до " + st["pause_until"])
            return False
        if not K.rate_ok("astro_view_gap.json", MIN_GAP_H * 60):
            _why(f"інтервал {MIN_GAP_H} год ще не минув")
            return False
    try:
        import astro_chart
        path = astro_chart.generate_natal_chart()
    except Exception as e:
        _log("chart: " + str(e))
        path = None
    if not path:
        _why("карту не намальовано (kerykeion/matplotlib)")
        return False
    from datetime import datetime
    pid = _store.put({"new": new, "day": K.today_str()})
    cap = "🔮 <b>Карта транзитів</b> · " + K.now().strftime("%d.%m") + "\n" + \
          "Нове: " + "; ".join(K.esc(f"{_label(a)} ({a['orb']}°)") for a in new) + \
          "\n\n❓ <b>Який аспект розібрати детальніше?</b>"
    kb = [[{"text": "🔍 " + _label(a), "callback_data": f"av_{pid}_{i}"}] for i, a in enumerate(new)]
    kb.append([{"text": "🙈 Без карти 3 дні", "callback_data": f"av_skip_{pid}"}])
    ok = _send_photo(path, cap, kb)
    try:
        os.remove(path)
    except Exception:
        pass
    if not ok:
        _store.drop(pid)
        _why("відправка карти не вдалась / відсіяна outbox")
        return False
    if not force:
        K.rate_mark("astro_view_gap.json")
    K.save(PENDING, {"day": pend.get("day"), "new": [], "sent": K.now().isoformat(timespec="seconds")})
    _log("✅ карта + " + str(len(new)) + " кнопок")
    return True


_PROMPT = """Ти астролог Олега (ДН 22.09.1989, Львів; живе в Кошице, нічні/ранні зміни,
біг, ціль 75 кг, фінанси). Розбери ОДИН транзит: {label}, орб {orb}°.
Базовий опис: {desc}
Українською, 4-5 коротких речень HTML (<b> для 1-2 ключових слів), без вступу і кліше:
що це за вплив, у яких сферах життя Олега (здоров'я/біг, гроші, робота) він проявиться
в найближчі дні, і 1 конкретна дія. Не вигадуй дат і чисел, яких нема вище."""


def handle(data: str) -> dict:
    m = re.match(r"^av_skip_(\w+)$", data)
    if m:
        _store.drop(m.group(1))
        from datetime import timedelta
        st = K.load(STATE_FILE, default={}) or {}
        st["pause_until"] = (K.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        K.save(STATE_FILE, st)
        return {"ok": True, "text": "Ок, карту не шлю 3 дні."}
    m = re.match(r"^av_(\w+)_(\d)$", data)
    if not m:
        return {"ok": False, "text": "Невідома дія"}
    p = _store.get(m.group(1))
    if not p:
        return {"ok": False, "text": "Ця карта вже закрита."}
    i = int(m.group(2))
    new = p.get("new") or []
    if i >= len(new):
        return {"ok": False, "text": "Немає такого аспекту."}
    a = new[i]
    desc = ""
    try:
        import astro
        desc = astro.get_aspect_description(a["tp"], a["asp"], a["np"]) or ""
    except Exception:
        pass
    txt = K.gemini_text(_PROMPT.format(label=_label(a), orb=a["orb"], desc=desc or "—"),
                        max_tokens=700, temperature=0.7, tag="astro_view")
    if not txt:
        return {"ok": False, "text": "Не вдалось отримати розбір — спробуй пізніше."}
    return {"ok": True, "html": f"🔮 <b>{K.esc(_label(a))}</b> ({a['orb']}°)\n\n" + K.md_to_html(txt)}
