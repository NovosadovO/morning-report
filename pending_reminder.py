#!/usr/bin/env python3
"""
НАГАДУВАННЯ ПРО НЕВІДПОВІДЖЕНІ ГОЛОВНІ ПИТАННЯ (pending_reminder)

25.09.2026: Олег попросив — якщо він не відповів на "головне" питання чи
сповіщення бота, той має САМ нагадати коротко "ти ще не відповів на...",
а не мовчки чекати (як зараз робить health_combined-guard в
intelligent_listener._health_combined_awaiting_reply) чи ставити нове
питання поверх старого.

Джерела "головних" pending-питань, які тут перевіряються:
  • monitor_micro_checkin_pending.json (health_combined / micro_checkin) —
    гілка data, переживає редеплой Railway.
  • data/interview_state.json (interview_practice) — локальний файл
    (bot.py сам чистить його, якщо минуло >12г без відповіді).

0 AI-кредитів: шаблонний текст із короткою цитатою суті питання (останнє
речення з "?", інакше початок тексту) — жодного Gemini-виклику.

Нагадує РІВНО ОДИН РАЗ на кожне непідтверджене питання (пишемо
"reminded": True назад у той самий pending-файл, поле саме скидається,
коли задається НОВЕ питання) — не раніше REMIND_AFTER_H годин мовчання і
перестає нагадувати, якщо минуло понад stale_h годин (питання вважається
застарілим — просто чекаємо нового, без нескінченного нагадування).
"""

import os
import re
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import ai_kit as K

TAG = "pending_reminder"
_TZ = ZoneInfo("Europe/Bratislava")

_INTERVIEW_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "interview_state.json"
)

REMIND_AFTER_H = 2.5   # нагадати не раніше, ніж стільки годин мовчання
QUIET_START_H = 7      # не нагадувати вночі — той самий дух, що і в
QUIET_END_H = 23        # micro_checkin/health_combined вікнах


def _interview_load() -> dict:
    try:
        if os.path.exists(_INTERVIEW_STATE_FILE):
            with open(_INTERVIEW_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _interview_save(data: dict):
    try:
        os.makedirs(os.path.dirname(_INTERVIEW_STATE_FILE), exist_ok=True)
        with open(_INTERVIEW_STATE_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        K.log(TAG, f"interview_state save error: {e}")


def _health_load() -> dict:
    return K.load("monitor_micro_checkin_pending.json", default={}) or {}


def _health_save(data: dict):
    K.save("monitor_micro_checkin_pending.json", data)


def _sources():
    """Кожне джерело: pending-файл з awaiting/question/asked_at + власний
    порогом 'застарілості' (узгоджено з тим, де це поле вже читається)."""
    return [
        {
            "name": "health_combined",
            "label": "моє попереднє питання",
            "load": _health_load,
            "save": _health_save,
            # той самий 24г-порог, що і в _health_combined_awaiting_reply —
            # після нього питання вважається застарілим і скоро заміниться новим.
            "stale_h": 24,
        },
        {
            "name": "interview_practice",
            "label": "практику співбесіди",
            "load": _interview_load,
            "save": _interview_save,
            # bot.py сам чистить стан через 12г — нагадуємо з запасом раніше.
            "stale_h": 11,
        },
    ]


def _extract_essence(text: str, limit: int = 220) -> str:
    """Коротка суть питання: останнє речення з '?', інакше початок тексту."""
    plain = re.sub(r"<[^>]+>", " ", text or "").strip()
    plain = re.sub(r"\s+", " ", plain)
    parts = re.split(r"(?<=[.!?])\s+", plain)
    quest = [p.strip() for p in parts if "?" in p]
    core = quest[-1] if quest else plain
    if len(core) > limit:
        core = core[:limit].rsplit(" ", 1)[0] + "…"
    return core


def _hours_ago_str(h: float) -> str:
    if h < 1:
        return f"{max(1, int(round(h * 60)))} хв"
    return f"{h:.1f}".rstrip("0").rstrip(".") + " год"


_TEMPLATES = [
    "Олеже, ти ще не відповів на {label} 👀 (питав {when} тому)\n\n«{core}»",
    "Нагадую — досі чекаю відповідь на {label} (питав {when} тому):\n\n«{core}»",
    "Гей, а відповідь є? 🙂 Це про {label}, питав {when} тому:\n\n«{core}»",
]


def _maybe_remind(source: dict, now_local: datetime) -> bool:
    pending = source["load"]()
    if not pending or not pending.get("awaiting"):
        return False
    if pending.get("reminded"):
        return False
    asked_at = pending.get("asked_at")
    question = pending.get("question", "")
    if not asked_at or not question:
        return False
    try:
        asked_dt = datetime.fromisoformat(asked_at)
    except Exception:
        return False
    if asked_dt.tzinfo is None:
        asked_dt = asked_dt.replace(tzinfo=_TZ)
    age_h = (now_local - asked_dt).total_seconds() / 3600
    if age_h < REMIND_AFTER_H or age_h > source["stale_h"]:
        return False

    core = _extract_essence(question)
    template = _TEMPLATES[now_local.hour % len(_TEMPLATES)]
    text = template.format(label=source["label"], when=_hours_ago_str(age_h), core=core)

    # keyboard=[] свідомо: пропускаємо autokb (він призначений для щойно
    # надісланих сповіщень з відповідним тегом) — тут просто текстове
    # нагадування, відповідь Олег так само пише текстом і її вже ловлять
    # handle_interview_answer/micro_checkin-хендлер у bot.py.
    ok = K.send_card(text, keyboard=[], tag=TAG)
    if ok:
        pending["reminded"] = True
        source["save"](pending)
        K.log(TAG, f"reminded: {source['name']} (age={age_h:.1f}h)")
    return bool(ok)


def tick() -> int:
    """Викликається з intelligent_listener раз на ~15 хв. 0 AI-кредитів."""
    now_local = datetime.now(_TZ)
    if not (QUIET_START_H <= now_local.hour < QUIET_END_H):
        return 0
    sent = 0
    for source in _sources():
        try:
            if _maybe_remind(source, now_local):
                sent += 1
        except Exception as e:
            K.log(TAG, f"{source['name']} error: {e}")
    return sent
