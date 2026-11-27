# -*- coding: utf-8 -*-
"""voice.py — бот слухає голосові/аудіо Олега, розуміє їх і відповідає.

Голосове (voice), кружечок (video_note), аудіофайл (audio / document audio/*)
-> Gemini транскрибує (нічого не вигадує) -> текст іде в ТОЙ САМИЙ конвеєр, що й
набраний вручну (команди, пробіжка, нотатки, планувальник, ask_ai).
Довгі аудіо (запис зустрічі, подкаст) -> коротке резюме + задачі + повна розшифровка.
"""
import base64
import json
import os
import re

import ai_kit as K

TAG = "voice"
MAX_BYTES = 18 * 1024 * 1024        # inline-ліміт Gemini ~20 МБ
LONG_SECONDS = 180                  # довше — резюме, а не «команда»
LONG_CHARS = 1200
MODEL = "gemini-2.5-flash"

_AUDIO_EXT_MIME = {
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
    ".mp3": "audio/mp3", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".flac": "audio/flac", ".mp4": "video/mp4",
    ".mov": "video/quicktime", ".webm": "video/webm", ".amr": "audio/amr",
}


def extract(msg: dict):
    """Повертає (file_id, mime, kind, duration) або None, якщо в повідомленні немає аудіо."""
    if msg.get("voice"):
        v = msg["voice"]
        return v["file_id"], v.get("mime_type") or "audio/ogg", "voice", int(v.get("duration") or 0)
    if msg.get("audio"):
        a = msg["audio"]
        mime = a.get("mime_type") or _AUDIO_EXT_MIME.get(
            os.path.splitext(a.get("file_name") or "")[1].lower(), "audio/mp3")
        return a["file_id"], mime, "audio", int(a.get("duration") or 0)
    if msg.get("video_note"):
        v = msg["video_note"]
        return v["file_id"], "video/mp4", "video_note", int(v.get("duration") or 0)
    d = msg.get("document")
    if d:
        mime = (d.get("mime_type") or "").lower()
        ext = os.path.splitext(d.get("file_name") or "")[1].lower()
        if mime.startswith("audio/") or ext in (".ogg", ".oga", ".opus", ".mp3", ".m4a",
                                                 ".aac", ".wav", ".flac", ".amr"):
            return d["file_id"], mime if mime.startswith("audio/") else _AUDIO_EXT_MIME.get(ext, "audio/mp3"), \
                "audio", 0
    return None


def _norm_mime(mime: str) -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    return {"audio/x-m4a": "audio/mp4", "audio/mpeg": "audio/mp3", "audio/x-wav": "audio/wav",
            "audio/opus": "audio/ogg", "audio/x-opus+ogg": "audio/ogg"}.get(mime, mime or "audio/ogg")


def _gem_audio(audio: bytes, mime: str, prompt: str, max_tokens: int = 8000,
               temperature: float = 0.1) -> str:
    """Один виклик Gemini з аудіо. '' при будь-якому збої (нічого не вигадуємо)."""
    if not K.GEMINI_KEY:
        K.log(TAG, "GEMINI_API_KEY відсутній")
        return ""
    try:
        from monitor import _gem_post
    except Exception as e:
        K.log(TAG, f"no _gem_post: {e}")
        return ""
    body = json.dumps({
        "contents": [{"parts": [
            {"inline_data": {"mime_type": _norm_mime(mime),
                             "data": base64.b64encode(audio).decode()}},
            {"text": prompt},
        ]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{MODEL}:generateContent?key={K.GEMINI_KEY}")
    try:
        resp = _gem_post(url, body, timeout=120, tag=TAG, max_retries=3)
        parts = resp["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except Exception as e:
        K.log(TAG, f"gemini error: {e}")
        return ""


_TRANSCRIBE_PROMPT = (
    "Дослівно розшифруй це аудіо. Мова — українська, російська, словацька або англійська "
    "(може бути суміш): пиши мовою, якою це сказано, НЕ перекладай. "
    "Став розділові знаки. Нічого не додавай від себе, не коментуй, не відповідай на сказане, "
    "не вигадуй слів, яких не чути. Якщо мовлення немає або воно нерозбірливе — "
    "поверни рівно: [нерозбірливо]. Поверни ТІЛЬКИ текст розшифровки."
)

UNCLEAR = "[нерозбірливо]"


def transcribe(audio: bytes, mime: str) -> str:
    t = _gem_audio(audio, mime, _TRANSCRIBE_PROMPT)
    t = re.sub(r"^```\w*\s*|\s*```$", "", t).strip()
    if not t or t.strip().lower().startswith(UNCLEAR) or len(t) < 2:
        return ""
    return t


def summarize(transcript: str) -> str:
    """Резюме довгого запису ТІЛЬКИ за розшифровкою."""
    prompt = (
        "Нижче розшифровка аудіозапису Олега (мова оригіналу). Зроби українською:\n"
        "1) 📝 Суть — 2–4 речення;\n"
        "2) 📌 Ключові пункти — список;\n"
        "3) ✅ Задачі/домовленості/терміни — ТІЛЬКИ ті, що реально прозвучали "
        "(з іменами й датами як у запису). Якщо їх немає — напиши «немає».\n"
        "Нічого не вигадуй і не додавай від себе. Без вступів.\n\n"
        f"РОЗШИФРОВКА:\n{transcript[:60000]}"
    )
    return K.gemini_text(prompt, max_tokens=1500, temperature=0.2, tag=TAG)


def chunks(text: str, size: int = 3800):
    text = text or ""
    out, cur = [], ""
    for para in re.split(r"(?<=\n)", text):
        if len(cur) + len(para) > size and cur:
            out.append(cur)
            cur = ""
        while len(para) > size:
            out.append(para[:size])
            para = para[size:]
        cur += para
    if cur:
        out.append(cur)
    return out


def is_long(kind: str, duration: int, transcript: str) -> bool:
    if kind == "voice" or kind == "video_note":
        return duration >= LONG_SECONDS or len(transcript) >= LONG_CHARS * 2
    return duration >= LONG_SECONDS or len(transcript) >= LONG_CHARS
