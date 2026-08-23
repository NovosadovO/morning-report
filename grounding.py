#!/usr/bin/env python3
"""
grounding.py — доступ AI до інтернету (Google Search grounding).

Проблема: AI бачив тільки те, що дають наші API (CoinGecko, Gmail, Calendar,
Strava). На питання «що там з ONDO цього тижня», «які новини по Minebea»,
«чи змінились правила X» він або мовчав, або вигадував зі знань 2024 року.

Тепер обрані типи промптів ідуть у Gemini з інструментом google_search: модель
сама шукає в Google і відповідає за свіжими джерелами.

Обережності (важливі, бо ламають бота якщо їх не мати):
  • JSON-промпти НЕ грунтуємо — grounding домішує текст/цитати і парсер падає.
  • Тільки allowlist тегів. Не всі 30 AI-викликів — це і гроші, і затримка.
  • Якщо API відхилив tools (400) — `strip()` знімає їх і виклик повторюється
    БЕЗ grounding. Тобто нова фіча не може зламати те, що працювало.
  • Джерела показуємо Олегу окремим рядком, щоб було видно звідки факт.
"""
import json
import re

TAG = "grounding"

# Теги AI-викликів, яким реально потрібен свіжий інтернет.
# Свідомо НЕ включені: action_detect / email_ai_item / qwatch_parse тощо —
# це JSON-парсери, там grounding шкідливий і зайвий.
GROUND_TAGS = {
    "crypto_ai",        # аналіз крипти — потрібні свіжі новини ринку
    "defi_digest",      # DeFi/RWA
    "personal_ai",      # головний блок звіту
    "themes_ai",        # теми звіту (фінанси/інвестиції)
    "daily_rec",        # щоденні рекомендації
    "deep_analysis",    # глибокий аналіз
    "briefing", "briefing_v3",
    "proactive_ai",     # проактивні повідомлення
    "context_ask_ai",   # прямі питання Олега боту
    "assistant",
    "weekly_coach", "monthly_coach", "weekly_report",
    # Реальні теги проактивних генераторів у проді (message_generator шле
    # tag=f"MSG_{trigger}", smart_notifications_v3 — свої 4). Без них
    # grounding не спрацьовував на найчастіших повідомленнях.
    "MORNING_AI", "LUNCH_AI", "AFTERNOON_AI", "EVENING_AI",
    "msg_gen",
    # 23.08 — Олег: «перевіряй актуальну інформацію» має діяти й на генераторах,
    # які писали текст без доступу до інтернету (реальні теги з прода).
    "BRIEFING_GEN",       # contextual_briefing_engine — брифінг дня
    "REC_GEN",            # recommendations_engine — рекомендації
    "proactive_actions",  # проактивні дії/пропозиції
    "health_ai",          # здоров'я: свіжі дані по бігу/вазі/сну + контекст
}

# Префікси тегів — MSG_CRYPTO_MOVE, MSG_DEEP_ANALYSIS, MSG_VIP_EMAIL тощо.
GROUND_PREFIXES = ("MSG_",)

_JSON_HINTS = (
    "тільки валідний json", "валідний json", "json-масив", "json масив",
    "поверни json", "відповідь — json", "format: json", "json format",
    "тільки json", "json об'єкт", "respond with json", "у форматі json",
    'поверни {"', "формат відповіді: {",
)


# Бюджет «думання» при пошуку. 0 — ріже пошук, без ліміту — з'їдає весь вивід.
THINK_BUDGET = 512
# Стеля виводу при пошуку: думання + текст мають поміститись разом.
MIN_OUT_TOKENS = 3000


def is_json_prompt(prompt: str) -> bool:
    p = str(prompt or "").lower()
    return any(h in p for h in _JSON_HINTS)


def wanted(tag: str, prompt: str) -> bool:
    """Чи давати цьому виклику інтернет."""
    t = str(tag or "")
    if t not in GROUND_TAGS and not t.startswith(GROUND_PREFIXES):
        return False
    if is_json_prompt(prompt):
        return False
    return True


def inject(body_bytes: bytes, tag: str) -> bytes:
    """Додає google_search до тіла запиту. Ідемпотентно.
    Повертає оригінал, якщо не треба або щось пішло не так."""
    try:
        b = json.loads(body_bytes.decode())
        if b.get("tools"):
            return body_bytes
        prompt = b["contents"][0]["parts"][0]["text"]
        if not wanted(tag, prompt):
            return body_bytes
        b["tools"] = [{"google_search": {}}]
        # ВАЖЛИВО (перевірено живим викликом 20.08): якщо просто зняти
        # thinkingBudget:0, модель витрачає на «думання» ~1750 токенів із
        # maxOutputTokens=1400 — і на текст лишається 180 токенів (156 симв.).
        # Саме через це проактивні повідомлення приходили обрізаними.
        # Тому: думання дозволяємо, але ОБМЕЖЕНЕ, і піднімаємо стелю виводу.
        gc = b.get("generationConfig") or {}
        gc["thinkingConfig"] = {"thinkingBudget": THINK_BUDGET}
        try:
            cur = int(gc.get("maxOutputTokens") or 0)
        except Exception:
            cur = 0
        gc["maxOutputTokens"] = max(cur, MIN_OUT_TOKENS)
        b["generationConfig"] = gc
        return json.dumps(b).encode()
    except Exception:
        return body_bytes


def strip(body_bytes: bytes) -> bytes:
    """Знімає інструменти — аварійний відкат, коли API їх не прийняв."""
    try:
        b = json.loads(body_bytes.decode())
        if "tools" not in b:
            return body_bytes
        b.pop("tools", None)
        b.pop("toolConfig", None)
        return json.dumps(b).encode()
    except Exception:
        return body_bytes


def has_tools(body_bytes: bytes) -> bool:
    try:
        return bool(json.loads(body_bytes.decode()).get("tools"))
    except Exception:
        return False


_HOST = re.compile(r"https?://(?:www\.)?([^/\s]+)")


def sources(resp: dict, limit: int = 4) -> list:
    """Домени, на які модель реально спиралась. Порожньо — grounding не спрацював."""
    out = []
    try:
        gm = (resp.get("candidates") or [{}])[0].get("groundingMetadata") or {}
        for ch in (gm.get("groundingChunks") or []):
            uri = ((ch or {}).get("web") or {}).get("uri") or ""
            title = ((ch or {}).get("web") or {}).get("title") or ""
            name = title or ""
            if not name:
                m = _HOST.search(uri)
                name = m.group(1) if m else ""
            name = str(name).strip()[:40]
            if name and name not in out:
                out.append(name)
            if len(out) >= limit:
                break
        if not out:
            for q in (gm.get("webSearchQueries") or [])[:limit]:
                out.append(f"пошук: {q}"[:40])
    except Exception:
        pass
    return out


def footer(resp: dict) -> str:
    """Рядок «🌐 Джерела: ...» під повідомлення. '' якщо джерел не було."""
    s = sources(resp)
    return ("\n\n🌐 <i>Джерела: " + ", ".join(s) + "</i>") if s else ""


_CITE = re.compile(r"\[cite[^\]]*\]|\[\d+(?:,\s*\d+)*\]\s*(?=[\s.,;)]|$)", re.I)
_CITE_OPEN = re.compile(r"\[cite[^\]]*$", re.I)  # обрізаний по лімиту токенів


def clean_text(txt: str) -> str:
    """Прибирає технічні маркери цитат, які модель вставляє при grounding
    («...TVL [cite: 1, 3]»). Олегу вони ні до чого — джерела показуємо окремо."""
    t = str(txt or "")
    t = _CITE.sub("", t)
    t = _CITE_OPEN.sub("", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    return t.strip()
