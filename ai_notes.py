#!/usr/bin/env python3
"""
AI-нотатки — бот запам'ятовує важливі факти про Олега з розмов
(чат з AI, "📝 Занотувати" під будь-яким AI-повідомленням, email-аналіз тощо)
і використовує їх пізніше як контекст для персоналізації.

Зберігання: storage.py (GitHub data-гілка, persistent, переживає редеплой).
"""
import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NOTES_FILE = "ai_notes.json"
_TZ_OFFSET = timedelta(hours=2)


def _storage():
    import storage
    return storage


def load_notes() -> list:
    """Повертає список нотаток: [{"text","source","ts"}]"""
    s = _storage()
    data = s.load(NOTES_FILE, default={"notes": []})
    if not isinstance(data, dict):
        return []
    return data.get("notes", [])


def add_note(text: str, source: str = "manual"):
    """Додає нотатку. source — звідки взялась (chat/email/qr_note/auto_extract тощо)."""
    if not text or not text.strip():
        return
    s = _storage()
    data = s.load(NOTES_FILE, default={"notes": []})
    if not isinstance(data, dict):
        data = {"notes": []}
    notes = data.get("notes", [])
    notes.append({
        "text": text.strip()[:500],
        "source": source,
        "ts": (datetime.now(timezone.utc) + _TZ_OFFSET).isoformat(),
    })
    # Тримаємо останні 150 нотаток — досить для контексту, не роздуває prompt
    notes = notes[-150:]
    data["notes"] = notes
    s.save(NOTES_FILE, data)
    print(f"[ai_notes] added ({source}): {text[:80]}", flush=True)


# 20.10.2026: скарга Олега — бот пропонував давно неактуальні речі (напр.
# "зустріч з другом, яку ти відкладав 16 жовтня" тижнями після того, як дата
# вже минула) і сам собі заважав, коментуючи у відповіді вголос, що дані
# "застарілі з кешу". Причина: нотатки тут жили у промпті БЕЗ віку — AI не
# міг відрізнити "сказано щойно" від "сказано місяць тому", і дата всередині
# тексту нотатки могла вже бути в минулому. Фікс: показуємо вік кожної
# нотатки явно і дуже старі (>35 днів) взагалі не вставляємо в контекст —
# разова згадка не повинна жити вічно і спливати знову й знову.
_NOTE_MAX_AGE_DAYS = 35


def _note_age_days(n: dict) -> float:
    try:
        ts = datetime.fromisoformat(str(n.get("ts") or "")).replace(tzinfo=None)
        now = (datetime.now(timezone.utc) + _TZ_OFFSET).replace(tzinfo=None)
        return (now - ts).total_seconds() / 86400.0
    except Exception:
        return 0.0


def get_notes_context(max_notes: int = 15) -> str:
    """Повертає останні нотатки у вигляді тексту для вставки в AI-промпт.
    Кожна нотатка — з віком у днях, щоб AI сам бачив, що свіже і що давнє,
    і НЕ пропонував/не нагадував про дати, які вже минули. Нотатки старші
    _NOTE_MAX_AGE_DAYS відсікаються — не захаращують кожен промпт назавжди."""
    notes = load_notes()
    if not notes:
        return ""
    fresh = [n for n in notes if _note_age_days(n) <= _NOTE_MAX_AGE_DAYS]
    if not fresh:
        return ""
    recent = fresh[-max_notes:]
    lines = []
    for n in recent:
        age = _note_age_days(n)
        if age < 1:
            age_label = "сьогодні"
        elif age < 2:
            age_label = "1 день тому"
        else:
            age_label = f"{int(age)} днів тому"
        lines.append(f"- {n['text']} ({age_label})")
    return (
        "\n📝 ЩО АІ ЗНАЄ ПРО ОЛЕГА З ПОПЕРЕДНІХ РОЗМОВ (вік кожної нотатки "
        "вказано поряд — враховуй це, не питай повторно):\n"
        + "\n".join(lines) + "\n"
        "⚠️ Якщо нотатка згадує конкретну дату (напр. «16 жовтня») і ця дата "
        "вже минула відносно сьогодні — вважай це вже неактуальним/виконаним. "
        "НЕ пропонуй це знову і НІКОЛИ не пояснюй вголос Олегу, що дані "
        "«застарілі» чи «з кешу» — просто мовчки пропусти неактуальне й пиши "
        "лише про те, що дійсно свіже й реальне зараз.\n"
    )


def extract_facts_from_conversation(user_message: str, ai_answer: str, gemini_key: str = None) -> list:
    """Викликає Gemini щоб витягти НОВІ важливі факти про Олега з обміну репліками
    (уподобання, плани, стан, рішення тощо). Повертає список коротких фактів або []."""
    import urllib.request as _ur

    api_key = gemini_key or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return []

    combined = f"Олег написав: {user_message}\nАІ відповів: {ai_answer}"
    if len(combined) < 20:
        return []

    prompt = (
        "Проаналізуй цей обмін репліками між Олегом і його AI-асистентом. "
        "Витягни ТІЛЬКИ дійсно важливі НОВІ факти про Олега (уподобання, плани, рішення, "
        "стан здоров'я, робочі зміни, важливі дати, цілі) які варто запам'ятати на майбутнє. "
        "БУДЬ СУВОРИМ — якщо нічого важливого немає, поверни порожній список. "
        "НЕ вигадуй — тільки те що РЕАЛЬНО сказано.\n\n"
        "Відповідь — ТІЛЬКИ валідний JSON-масив рядків, без markdown:\n"
        '["факт 1", "факт 2"] АБО [] якщо нема нічого важливого.\n\n'
        f"{combined[:1500]}"
    )
    # quiet-guard: режим сну — жодних AI-викликів (кредити)
    try:
        import quiet as _q_n
        if _q_n.blocked("ai"):
            print("[quiet] 🌙 сон: ai_notes AI-виклик пропущено", flush=True)
            return []
    except Exception:
        pass

    req_body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 300, "temperature": 0.2, "thinkingConfig": {"thinkingBudget": 0}}
    }).encode()

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        req = _ur.Request(url, data=req_body, headers={"Content-Type": "application/json"})
        with _ur.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
        raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        import re as _re
        raw = _re.sub(r"^```(?:json)?\s*", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"\s*```\s*$", "", raw, flags=_re.MULTILINE)
        facts = json.loads(raw.strip())
        return facts if isinstance(facts, list) else []
    except Exception as e:
        print(f"[ai_notes] extract error: {e}", flush=True)
        return []


def auto_note_from_conversation(user_message: str, ai_answer: str):
    """Викликається після кожної розмови в чаті — автоматично витягає і зберігає факти."""
    try:
        facts = extract_facts_from_conversation(user_message, ai_answer)
        for fact in facts[:3]:  # максимум 3 факти за раз — не засмічувати
            add_note(fact, source="auto_extract")
    except Exception as e:
        print(f"[ai_notes] auto_note error: {e}", flush=True)
