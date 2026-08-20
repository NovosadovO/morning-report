#!/usr/bin/env python3
"""
notrunc.py — повідомлення не мають обриватись посеред слова.

Скарга Олега (20.08, скріншот): повідомлення закінчилось на
«…використовувати його максимально ефективно, ад» — обрив на півслові.

Причина: Gemini впирається в maxOutputTokens і повертає
finishReason = "MAX_TOKENS". Текст приходить рівно до ліміту — де б він не
закінчився. Ліміти розкидані по 40+ місцях коду (від 300 до 8000), тому
правити кожне окремо безглуздо: лікуємо централізовано в monitor._gem_post.

Стратегія (у такому порядку):
  1. bump()  — повторюємо запит з більшою стелею (×2, максимум CAP). Один раз:
               дешевше добрати токенів, ніж показувати огризок.
  2. tidy()  — якщо й після цього обрив, ріжемо до останнього ЗАВЕРШЕНОГО
               речення. Краще коротше, але ціле, ніж «ефективно, ад».

JSON-промпти не чіпаємо через tidy (зламали б структуру) — для них працює
тільки bump.

API:
    truncated(resp)          -> bool
    bump(body_bytes)         -> (нове тіло | None, нова стеля)
    tidy(text)               -> str
    fix_response(resp)       -> bool   # підправляє текст усередині resp
"""
import json
import re

TAG = "notrunc"

CAP = 8000          # вище не піднімаємо: дорого і моделі рідко треба більше
_STOPWORDS = {
    "і", "й", "та", "а", "але", "що", "бо", "як", "на", "в", "у", "з", "із",
    "зі", "до", "для", "по", "за", "про", "від", "при", "над", "під", "без",
    "між", "то", "це", "щоб", "чи", "не", "ні", "так", "the", "and", "to",
    "of", "in", "for", "with", "on", "at",
}
_ENDINGS = ".!?…\"»)"


def truncated(resp) -> bool:
    """True → модель не договорила (впёрлась у стелю токенів)."""
    try:
        for c in (resp.get("candidates") or []):
            if str(c.get("finishReason") or "").upper() == "MAX_TOKENS":
                return True
    except Exception:
        pass
    return False


def text_of(resp) -> str:
    try:
        parts = resp["candidates"][0]["content"]["parts"]
        return "".join(p.get("text") or "" for p in parts)
    except Exception:
        return ""


def is_json_body(body_bytes) -> bool:
    """JSON-промпт? Тоді різати текст не можна — тільки добирати токени."""
    try:
        b = json.loads(body_bytes.decode())
        p = b["contents"][0]["parts"][0]["text"].lower()
        return ("json" in p and ("{" in p or "формат" in p or "format" in p))
    except Exception:
        return False


def bump(body_bytes, factor: int = 2):
    """Повертає (нове тіло, нова стеля) або (None, 0), якщо піднімати нікуди."""
    try:
        b = json.loads(body_bytes.decode())
        gc = b.get("generationConfig") or {}
        cur = int(gc.get("maxOutputTokens") or 0)
        if cur <= 0:
            cur = 1400
        new = min(int(cur * factor), CAP)
        if new <= cur:
            return None, 0
        gc["maxOutputTokens"] = new
        # Думання теж їсть цю стелю. Якщо воно необмежене, ставимо стелю
        # думанню, інакше добрані токени знову підуть не в текст.
        tc = gc.get("thinkingConfig")
        if isinstance(tc, dict) and tc.get("thinkingBudget") not in (0,):
            tc["thinkingBudget"] = min(int(tc.get("thinkingBudget") or 512), 512)
            gc["thinkingConfig"] = tc
        b["generationConfig"] = gc
        return json.dumps(b).encode(), new
    except Exception:
        return None, 0


def _trim_partial(s: str) -> str:
    """Прибирає обірване останнє слово і «висячі» службові слова та коми."""
    words = str(s or "").split()
    if words:
        words = words[:-1]          # останнє слово майже напевно обірване
    while words:
        w = re.sub(r"[^\w'’-]", "", words[-1], flags=re.UNICODE).lower()
        if w in _STOPWORDS or not w:
            words.pop()
            continue
        break
    out = " ".join(words)
    return re.sub(r"[,;:\-–—(«\"]+$", "", out).rstrip()


def tidy(text: str) -> str:
    """Прибирає обрив на півслові: або дорізає хвіст, або ріже до речення."""
    t = str(text or "").rstrip()
    if not t:
        return t
    if t[-1] in _ENDINGS:
        return t
    cut = max(t.rfind(c) for c in ".!?…")
    if cut > 0:
        head = t[:cut + 1].rstrip()
        tail = _trim_partial(t[cut + 1:])
        # Хвіст після останньої крапки ще змістовний — лишаємо його,
        # просто завершуємо думку крапкою. Інакше ріжемо до речення.
        if len(tail.split()) >= 3:
            return (head + " " + tail).rstrip() + "."
        return head
    # Жодного цілого речення — хоча б не ріжемо слово навпіл і чесно
    # позначаємо, що думка не закінчена.
    out = _trim_partial(t)
    if not out:
        return t
    return out + " […]"


def fix_response(resp) -> bool:
    """Підчищає обірваний текст прямо в відповіді. True → щось змінили."""
    try:
        if not truncated(resp):
            return False
        parts = resp["candidates"][0]["content"]["parts"]
        idx = None
        for i in range(len(parts) - 1, -1, -1):
            if isinstance(parts[i].get("text"), str) and parts[i]["text"].strip():
                idx = i
                break
        if idx is None:
            return False
        old = parts[idx]["text"]
        new = tidy(old)
        if new == old:
            return False
        parts[idx]["text"] = new
        print(f"[{TAG}] ✂️ обрив на півслові прибрано ({len(old)} → {len(new)} симв.)",
              flush=True)
        return True
    except Exception as e:
        print(f"[{TAG}] fix error: {e}", flush=True)
        return False


if __name__ == "__main__":
    demo = ("Ти зараз заслужено відпочиваєш. Ніч — це твій час для глибокого "
            "спокою, і важливо використовувати його максимально ефективно, ад")
    print(tidy(demo))
    print("---")
    print(tidy("Без жодної крапки цей текст просто обірвався на сло"))
