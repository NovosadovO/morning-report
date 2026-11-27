import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TELEGRAM_TOKEN", "x"); os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
fails = []
def ok(c, m):
    print(("ok   " if c else "FAIL ") + m)
    if not c: fails.append(m)
import ai_kit as K, voice as V
K.GEMINI_KEY = "k"

print("1) extract")
ok(V.extract({"voice": {"file_id": "a", "duration": 5, "mime_type": "audio/ogg"}}) == ("a", "audio/ogg", "voice", 5), "voice")
ok(V.extract({"audio": {"file_id": "b", "duration": 300, "file_name": "x.m4a"}})[1] == "audio/mp4", "audio m4a")
ok(V.extract({"video_note": {"file_id": "c", "duration": 4}})[2] == "video_note", "video_note")
ok(V.extract({"document": {"file_id": "d", "file_name": "rec.mp3", "mime_type": "audio/mpeg"}})[2] == "audio", "document audio")
ok(V.extract({"document": {"file_id": "d", "file_name": "a.pdf", "mime_type": "application/pdf"}}) is None, "pdf — ні")
ok(V.extract({"text": "hi"}) is None, "текст — ні")

print("2) transcribe через мок Gemini")
import monitor
calls = []
def fake_post(url, body, timeout=90, tag="", max_retries=3):
    d = json.loads(body); calls.append(d)
    part = d["contents"][0]["parts"][0]
    assert part["inline_data"]["mime_type"] == "audio/ogg"
    return {"candidates": [{"content": {"parts": [{"text": FAKE}]}}]}
monitor._gem_post = fake_post
FAKE = "Запиши, що завтра о десятій я їду до лікаря."
ok(V.transcribe(b"abc", "audio/ogg") == FAKE, "текст повернувся")
FAKE = "[нерозбірливо]"
ok(V.transcribe(b"abc", "audio/ogg") == "", "нерозбірливо -> порожньо")
ok(V.transcribe(b"abc", "audio/opus") == "", "mime opus нормалізується")
K.GEMINI_KEY = ""
ok(V.transcribe(b"abc", "audio/ogg") == "", "без ключа — порожньо, не вигадує")
K.GEMINI_KEY = "k"

print("3) is_long / chunks")
ok(not V.is_long("voice", 20, "коротко"), "коротке голосове — не довге")
ok(V.is_long("audio", 400, "x"), "аудіо 400с — довге")
ok(V.is_long("audio", 0, "x" * 1300), "довгий текст аудіо")
ch = V.chunks("абв\n" * 3000)
ok(all(len(c) <= 3800 for c in ch) and "".join(ch) == "абв\n" * 3000, "chunks без втрат")

print("4) bot.process_update")
import bot
sent = []; handled = []
bot.send = lambda cid, t: sent.append(t)
bot.api = lambda m, d=None: {"ok": True}
bot._download_telegram_file = lambda fid: b"OGG"
bot.handle_command = lambda cid, t: handled.append(t)
bot.TELEGRAM_CHAT = "1"
for name in ("handle_interview_answer", "handle_draft_request"):
    setattr(bot, name, lambda *a, **k: False)
bot._handle_note_reply = lambda *a, **k: False
FAKE = "Запиши, що завтра о десятій я їду до лікаря."
bot.process_update({"message": {"chat": {"id": 1}, "voice": {"file_id": "a", "duration": 5, "mime_type": "audio/ogg"}}})
ok(any("🎤" in s and "лікаря" in s for s in sent), "ехо розшифровки")
ok(handled and "лікаря" in handled[-1], "текст пішов у handle_command")
sent.clear(); handled.clear()
FAKE = "[нерозбірливо]"
bot.process_update({"message": {"chat": {"id": 1}, "voice": {"file_id": "a", "duration": 2, "mime_type": "audio/ogg"}}})
ok(not handled and any("Не розібрав" in s for s in sent), "нерозбірливе: чесне повідомлення, команда не виконана")
sent.clear(); handled.clear()
FAKE = "слово " * 400
K.gemini_text = lambda *a, **k: "📝 Суть: тест"
bot.process_update({"message": {"chat": {"id": 1}, "audio": {"file_id": "b", "duration": 600, "mime_type": "audio/ogg"}}})
ok(not handled and any("Суть" in s for s in sent) and any("Розшифровка" in s for s in sent), "довгий запис: резюме + розшифровка, без команди")
sent.clear(); handled.clear()
bot.process_update({"message": {"chat": {"id": 2}, "voice": {"file_id": "a", "duration": 5}}})
ok(not handled and any("Немає доступу" in s for s in sent), "чужий чат — відмова")
bot.process_update({"message": {"chat": {"id": 1}, "text": "/status"}})
ok(handled and handled[-1] == "/status", "звичайний текст не зламаний")
print("FAILS:", fails or "none")
