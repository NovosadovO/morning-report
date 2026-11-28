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
    """Один виклик Gemini з аудіо. None при збої (нічого не вигадуємо)."""
    if not K.GEMINI_KEY:
        K.log(TAG, "GEMINI_API_KEY відсутній")
        return None
    try:
        from monitor import _gem_post
    except Exception as e:
        K.log(TAG, f"no _gem_post: {e}")
        return None
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
        return None


_TRANSCRIBE_PROMPT = (
    "Дослівно розшифруй це аудіо. Мова — українська, російська, словацька або англійська "
    "(може бути суміш): пиши мовою, якою це сказано, НЕ перекладай. "
    "Став розділові знаки. БЕЗ таймкодів, БЕЗ міток спікерів, БЕЗ лапок навколо. Нічого не додавай від себе, не коментуй, не відповідай на сказане, "
    "не вигадуй слів, яких не чути. Якщо мовлення немає або воно нерозбірливе — "
    "поверни рівно: [нерозбірливо]. Поверни ТІЛЬКИ текст розшифровки."
)

UNCLEAR = "[нерозбірливо]"


_TC = re.compile(r"(?m)^[ \t]*(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?[ \t]*[-–:]?[ \t]*)+")


def _strip_timecodes(t: str) -> str:
    """Gemini інколи додає «00:00 00:01» на початку рядків — це не мовлення."""
    return _TC.sub("", t or "").strip()


def transcribe(audio: bytes, mime: str):
    """Текст розшифровки; "" — мовлення нерозбірливе/немає; None — ЗБІЙ AI (треба повторити)."""
    t = _gem_audio(audio, mime, _TRANSCRIBE_PROMPT)
    if t is None:
        return None
    t = re.sub(r"^```\w*\s*|\s*```$", "", t).strip()
    t = _strip_timecodes(t)
    if not t:
        return None            # порожня відповідь моделі = збій, не «тиша»
    if t.strip().lower().startswith(UNCLEAR) or len(t) < 2:
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


# ─── ЧЕРГА: аудіо, яке не вдалось розшифрувати (AI впав) — не губимо ──────────
import time as _time

PENDING_FILE = "voice_pending.json"
RETRY_EVERY = 240            # c між спробами
MAX_TRIES = 12
MAX_AGE_H = 24


def _pload():
    try:
        import durable
        d = durable.load(PENDING_FILE) or {}
    except Exception:
        d = {}
    return d if isinstance(d.get("items"), list) else {"items": []}


def _psave(d):
    try:
        import durable
        durable.save(PENDING_FILE, d)
    except Exception as e:
        K.log(TAG, f"pending save: {e}")


def enqueue(chat_id, aud):
    file_id, mime, kind, dur = aud
    d = _pload()
    if any(i.get("file_id") == file_id for i in d["items"]):
        return False
    d["items"].append({"chat_id": chat_id, "file_id": file_id, "mime": mime, "kind": kind,
                       "dur": dur, "ts": _time.time(), "tries": 0, "last": _time.time()})
    _psave(d)
    return True


def due(now=None):
    """Елементи черги, які пора повторити; прострочені викидає і повертає окремо."""
    now = now or _time.time()
    d = _pload()
    keep, run, dead = [], [], []
    for i in d["items"]:
        if i.get("tries", 0) >= MAX_TRIES or now - i.get("ts", now) > MAX_AGE_H * 3600:
            dead.append(i)
            continue
        keep.append(i)
        if now - i.get("last", 0) >= RETRY_EVERY:
            run.append(i)
    if dead:
        d["items"] = keep
        _psave(d)
    return run, dead


def done(file_id):
    d = _pload()
    d["items"] = [i for i in d["items"] if i.get("file_id") != file_id]
    _psave(d)


def bump(file_id):
    d = _pload()
    for i in d["items"]:
        if i.get("file_id") == file_id:
            i["tries"] = i.get("tries", 0) + 1
            i["last"] = _time.time()
    _psave(d)


# ─── ГОЛОСОВА ВІДПОВІДЬ (/голос) ─────────────────────────────────────────────
SETTINGS_FILE = "voice_settings.json"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"


def reply_enabled() -> bool:
    try:
        import durable
        return bool((durable.load(SETTINGS_FILE) or {}).get("reply_voice"))
    except Exception:
        return False


def set_reply(on: bool) -> bool:
    try:
        import durable
        return durable.save(SETTINGS_FILE, {"reply_voice": bool(on)})
    except Exception:
        return False


def _speakable(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = re.sub(r"[*_`#>|]+", " ", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:1200]


def speak(text: str):
    """(wav_bytes) або None. Gemini TTS -> PCM 24 кГц -> WAV (без ffmpeg)."""
    t = _speakable(text)
    if not K.GEMINI_KEY or len(t) < 2:
        return None
    import urllib.request, io, wave
    body = json.dumps({
        "contents": [{"parts": [{"text": "Прочитай природно, тепло і спокійно українською: " + t}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": TTS_VOICE}}},
        },
    }).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{TTS_MODEL}:generateContent?key={K.GEMINI_KEY}")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        part = resp["candidates"][0]["content"]["parts"][0]
        inl = part.get("inlineData") or part.get("inline_data")
        pcm = base64.b64decode(inl["data"])
        m = re.search(r"rate=(\d+)", inl.get("mimeType") or inl.get("mime_type") or "")
        rate = int(m.group(1)) if m else 24000
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
        return buf.getvalue()
    except Exception as e:
        K.log(TAG, f"tts error: {e}")
        return None
