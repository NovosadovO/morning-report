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
ok(V.transcribe(b"abc", "audio/ogg") is None, "без ключа — None (збій), не вигадує")
K.GEMINI_KEY = "k"
def boom(*a, **k): raise RuntimeError("429")
_gp = monitor._gem_post; monitor._gem_post = boom
ok(V.transcribe(b"abc", "audio/ogg") is None, "збій Gemini -> None")
monitor._gem_post = _gp

ok(V._strip_timecodes("00:00 00:01 видали всі нагадування") == "видали всі нагадування", "таймкоди прибрано")
ok(V._strip_timecodes("[00:03] привіт\n00:05 - як справи") == "привіт\nяк справи", "таймкоди по рядках")
ok(V._strip_timecodes("зустріч о 10:30 завтра") == "зустріч о 10:30 завтра", "час у середині речення не чіпає")
print("3) is_long / chunks")
ok(not V.is_long("voice", 20, "коротко"), "коротке голосове — не довге")
ok(V.is_long("audio", 400, "x"), "аудіо 400с — довге")
ok(V.is_long("audio", 0, "x" * 1300), "довгий текст аудіо")
ch = V.chunks("абв\n" * 3000)
ok(all(len(c) <= 3800 for c in ch) and "".join(ch) == "абв\n" * 3000, "chunks без втрат")

print("4) bot.process_update")
import bot, threading
MEM = {}
import durable
durable.load = lambda name, local_path=None: json.loads(json.dumps(MEM.get(name, {})))
durable.save = lambda name, d, local_path=None, keep=400: MEM.__setitem__(name, json.loads(json.dumps(d))) or True
def join_all():
    for t in threading.enumerate():
        if t.name == "voice_flow": t.join(10)
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
bot.process_update({"message": {"chat": {"id": 1}, "voice": {"file_id": "a", "duration": 5, "mime_type": "audio/ogg"}}}); join_all()
ok(not any("🎤" in x for x in sent), "розшифровку в чат НЕ дублює")
ok(handled and "лікаря" in handled[-1], "текст пішов у handle_command")
sent.clear(); handled.clear()
FAKE = "[нерозбірливо]"
bot.process_update({"message": {"chat": {"id": 1}, "voice": {"file_id": "a", "duration": 2, "mime_type": "audio/ogg"}}}); join_all()
ok(not handled and any("Не розібрав" in s for s in sent), "нерозбірливе: чесне повідомлення, команда не виконана")
sent.clear(); handled.clear()
FAKE = "слово " * 400
K.gemini_text = lambda *a, **k: "📝 Суть: тест"
bot.process_update({"message": {"chat": {"id": 1}, "audio": {"file_id": "b", "duration": 600, "mime_type": "audio/ogg"}}}); join_all()
ok(not handled and any("Суть" in s for s in sent) and any("Розшифровка" in s for s in sent), "довгий запис: резюме + розшифровка, без команди")
sent.clear(); handled.clear()
bot.process_update({"message": {"chat": {"id": 2}, "voice": {"file_id": "a", "duration": 5}}})
ok(not handled and any("Немає доступу" in s for s in sent), "чужий чат — відмова")
bot.process_update({"message": {"chat": {"id": 1}, "text": "/status"}})
ok(handled and handled[-1] == "/status", "звичайний текст не зламаний")
print("5) збій AI -> черга -> повтор")
sent.clear(); handled.clear()
monitor._gem_post = boom
bot.process_update({"message": {"chat": {"id": 1}, "voice": {"file_id": "q1", "duration": 5, "mime_type": "audio/ogg"}}}); join_all()
ok(not handled and any("зберіг" in x for x in sent), "повідомив, що запис збережено")
ok(len(MEM["voice_pending.json"]["items"]) == 1, "у черзі 1 запис")
bot.process_update({"message": {"chat": {"id": 1}, "voice": {"file_id": "q1", "duration": 5, "mime_type": "audio/ogg"}}}); join_all()
ok(len(MEM["voice_pending.json"]["items"]) == 1, "дубль не додається")
run, dead = V.due()
ok(not run, "ще рано повторювати")
for i in MEM["voice_pending.json"]["items"]: i["last"] -= 1000
run, dead = V.due()
ok(len(run) == 1, "через 4+ хв — пора повторити")
monitor._gem_post = fake_post; FAKE = "Скільки в мене сьогодні справ?"
sent.clear(); handled.clear()
it = run[0]
bot._audio_flow(it["chat_id"], {}, (it["file_id"], it["mime"], it["kind"], it["dur"]), retry=True)
ok(handled and "справ" in handled[-1], "повтор: текст пішов у конвеєр")
ok(any("відкладений" in x for x in sent), "позначено як відкладений запис")
ok(not MEM["voice_pending.json"]["items"], "черга очищена")
MEM["voice_pending.json"]["items"].append({"chat_id": 1, "file_id": "old", "mime": "audio/ogg", "kind": "voice", "dur": 1, "ts": 1, "tries": 0, "last": 0})
run, dead = V.due()
ok(len(dead) == 1 and not run, "прострочений запис (>24 год) викинуто")

print("6) голосова відповідь")
asked = []
import context
context.ask_ai = lambda t, include_calendar=True: "Сьогодні 2 справи."
bot.handle_command = bot.__dict__["handle_command"] if False else bot.handle_command
V.set_reply(True)
ok(V.reply_enabled(), "/голос увімкнено")
spoke = []
V.speak = lambda t: b"RIFFxxxx"
bot.send_audio_bytes = lambda cid, data, **k: spoke.append(data) or True
bot._VOICE_CTX.on = True; bot._maybe_voice_reply(1, "Сьогодні 2 справи."); bot._VOICE_CTX.on = False
ok(spoke, "голос надіслано для голосового запиту")
spoke.clear(); bot._maybe_voice_reply(1, "текст"); ok(not spoke, "на текстовий запит голосом не відповідаємо")
V.set_reply(False); bot._VOICE_CTX.on = True; bot._maybe_voice_reply(1, "x"); bot._VOICE_CTX.on = False
ok(not spoke, "вимкнено — тільки текст")
ok(V._speakable("<b>Привіт</b> 🎤 *світ*") == "Привіт світ", "_speakable чистить розмітку/емодзі")
print("FAILS:", fails or "none")
