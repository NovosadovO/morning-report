#!/usr/bin/env python3
"""
tests_smart.py — (1) доступ AI до інтернету, (2) розумна тиша за графіком змін.

Головні ризики, які тест мусить закрити:
  • grounding не має ламати JSON-промпти (парсери) і має вміти відкотитись;
  • autoquiet НЕ має вважати, що Олег спить, коли він на нічній зміні
    (це була його пряма скарга: «пише що я сплю, а я на зміні»);
  • термінове мусить проходити попри сон;
  • відкладене не має губитись, а кнопки — виживати.
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")

import ai_kit as K  # noqa: E402
import grounding as gr  # noqa: E402
import autoquiet as aq  # noqa: E402

FAILS = 0


def ok(cond, msg):
    global FAILS
    if cond:
        print(f"✅ {msg}")
    else:
        FAILS += 1
        print(f"❌ {msg}")


def body(prompt, **gc):
    b = {"contents": [{"parts": [{"text": prompt}]}]}
    if gc:
        b["generationConfig"] = gc
    return json.dumps(b).encode()


# ── 1. GROUNDING ─────────────────────────────────────────────────────────────
print("\n1) Інтернет для AI — кому даємо")
ok(gr.wanted("crypto_ai", "Проаналізуй ринок ONDO"), "crypto_ai → інтернет")
ok(gr.wanted("context_ask_ai", "Що там з Minebea?"), "пряме питання Олега → інтернет")
ok(not gr.wanted("action_detect", "Знайди дію"), "action_detect → БЕЗ інтернету")
ok(not gr.wanted("email_ai_item", "Опиши лист"), "email_ai_item → БЕЗ інтернету")

print("\n1b) JSON-промпти не грунтуються (інакше парсер падає)")
ok(not gr.wanted("crypto_ai", "Поверни тільки валідний JSON: {\"a\":1}"),
   "явний JSON-промпт не грунтується")
ok(not gr.wanted("crypto_ai", "Формат відповіді: {\"x\": 1}"),
   "формат-JSON не грунтується")
b = gr.inject(body("Поверни тільки валідний JSON"), "crypto_ai")
ok(not gr.has_tools(b), "inject не додав tools у JSON-промпт")

print("\n1c) inject / strip")
b = gr.inject(body("Проаналізуй ринок ONDO за тиждень"), "crypto_ai")
ok(gr.has_tools(b), "tools додані")
ok(json.loads(b.decode())["tools"] == [{"google_search": {}}], "саме google_search")
ok(gr.inject(b, "crypto_ai") == b, "повторний inject нічого не дублює")
ok(not gr.has_tools(gr.strip(b)), "strip знімає tools (аварійний відкат)")
b2 = gr.inject(body("Аналіз ринку", maxOutputTokens=500,
                    thinkingConfig={"thinkingBudget": 0}), "crypto_ai")
ok("thinkingConfig" not in json.loads(b2.decode()).get("generationConfig", {}),
   "нульовий thinkingBudget знято (він ріже пошук)")
ok(json.loads(b2.decode())["generationConfig"]["maxOutputTokens"] == 500,
   "решта конфігу не зачеплена")

print("\n1d) Чистка маркерів цитат і джерела")
ok(gr.clean_text("TVL зріс [cite: 1, 3] удвічі.") == "TVL зріс удвічі.",
   "маркер [cite: ...] прибрано")
ok(gr.clean_text("Ціна $0.35 [cite:") == "Ціна $0.35",
   "обрізаний маркер прибрано")
resp = {"candidates": [{"groundingMetadata": {"groundingChunks": [
    {"web": {"uri": "https://coinmarketcap.com/x", "title": ""}},
    {"web": {"uri": "https://coinbase.com/y", "title": ""}}]}}]}
ok(gr.sources(resp) == ["coinmarketcap.com", "coinbase.com"], "джерела витягнуто")
ok("Джерела" in gr.footer(resp), "футер з джерелами")
ok(gr.footer({"candidates": [{}]}) == "", "без grounding — футера немає")

print("\n1e) monitor._gem_post під'єднаний правильно")
msrc = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")).read()
i = msrc.find("def _gem_post")
seg = msrc[i:i + 9000]
ok("grounding as _gr" in seg, "grounding імпортується в _gem_post")
ok(seg.find("_gr.inject") < seg.find("for _mi, _model"), "inject ДО циклу моделей")
ok("_gr2.strip(body_bytes)" in seg, "є аварійний відкат при 400/403")
ok("clean_text" in seg, "маркери цитат чистяться у відповіді")
ok(seg.find("ai_brain") < seg.find("_gr.inject"),
   "grounding після ai_brain (пам'ять не втрачається)")

# ── 2. AUTOQUIET ─────────────────────────────────────────────────────────────
print("\n2) Розумна тиша: НЕ спить, коли на зміні")
aq._cache["data"] = None


def fake_shift(today):
    import context as _c
    _c.get_shift_from_calendar = lambda: {"today": today}
    aq._cache["data"] = None
    aq._cache["ts"] = 0.0


def at(h):
    aq._now = lambda: datetime(2026, 8, 20, h, 0)
    aq._cache["data"] = None
    aq._cache["ts"] = 0.0


fake_shift("night")
for h in (19, 23, 2, 5):
    at(h)
    ok(not aq.sleeping(), f"нічна зміна, {h:02d}:00 — НЕ спить (він на роботі)")

print("\n2b) Нічна зміна: спить ДНЕМ після неї")
for h in (8, 11, 13):
    at(h)
    ok(aq.sleeping(), f"після нічної, {h:02d}:00 — спить")
at(15)
ok(not aq.sleeping(), "після нічної, 15:00 — прокинувся")

print("\n2c) Рання зміна")
fake_shift("early")
at(10)
ok(not aq.sleeping(), "рання зміна, 10:00 — на роботі")
at(23)
ok(aq.sleeping(), "рання зміна, 23:00 — спить")
at(4)
ok(aq.sleeping(), "рання зміна, 04:00 — спить")
at(6)
ok(not aq.sleeping(), "рання зміна, 06:00 — вже на зміні")

print("\n2d) Вихідний")
fake_shift("free")
at(2)
ok(aq.sleeping(), "вихідний, 02:00 — спить")
at(12)
ok(not aq.sleeping(), "вихідний, 12:00 — не спить")

print("\n2e) Термінове проходить попри сон")
fake_shift("night")
at(9)  # спить
ok(aq.sleeping(), "контроль: зараз спить")
ok(not aq.should_hold("🔴 ВАЖЛИВО: лист від Michaela"), "VIP-лист шлемо одразу")
ok(not aq.should_hold("🚨 BTC обвалився на 7%"), "крипто-алерт шлемо одразу")
ok(not aq.should_hold("Подія почнеться через годину"), "подія за годину — шлемо")
ok(aq.should_hold("🔮 Астро-прогноз на день"), "астро — відкладаємо")
ok(aq.should_hold("📰 Дайджест новин крипти"), "дайджест — відкладаємо")
ok(aq.should_hold("Мотивація: ти на правильному шляху"), "мотивація — відкладаємо")

print("\n2f) Черга: нічого не губиться, кнопки виживають")
MEM = {}
K.load = lambda f, default=None: MEM.get(f, default if default is not None else {})
K.save = lambda f, d: MEM.__setitem__(f, d)
SENT = []
K.send_card = lambda text, keyboard=None, **kw: SENT.append((text, keyboard)) or True

aq.hold("🔮 Астро-прогноз", kind="astro")
aq.hold("📅 АІ помітив дію: «Оплатити VSE»", kind="offer",
        keyboard={"inline_keyboard": [[{"text": "✅", "callback_data": "cal_add_1"}]]})
ok(aq.pending() == 2, "два повідомлення в черзі")
ok(aq.flush() == 0, "поки спить — не віддаємо")

at(16)  # прокинувся
ok(not aq.sleeping(), "контроль: вже не спить")
n = aq.flush()
ok(n == 2, f"віддано обидва ({n})")
ok(aq.pending() == 0, "черга очищена")
ok(any("Поки ти спав" in t for t, _ in SENT), "є заголовок дайджесту")
ok(any("Астро" in t for t, _ in SENT), "звичайне повідомлення віддано")
kb_sent = [kb for _, kb in SENT if kb]
ok(bool(kb_sent) and kb_sent[0][0][0]["callback_data"] == "cal_add_1",
   "кнопка вціліла і callback правильний")

print("\n2g) Протермінована черга не шлеться")
MEM.clear()
SENT.clear()
MEM["held_msgs.json"] = {"items": [
    {"ts": datetime(2026, 8, 19, 3, 0).isoformat(), "text": "старе", "kind": "x"}]}
ok(aq.flush() == 0, "старше 10 год — не віддаємо")
ok(not SENT, "нічого не надіслано")

print("\n2h) Активність Олега скасовує тишу")
at(9)
fake_shift("night")
MEM.clear()
import quiet as q  # noqa: E402
q.mark_user_thread()
ok(not aq.should_hold("🔮 Астро-прогноз"), "його потік — відповідаємо негайно")
q.clear_user_thread()
ok(aq.should_hold("🔮 Астро-прогноз"), "фоновий потік — знову тиша")

print("\n2i) Під'єднано до відправників")
ak = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_kit.py")).read()
ok("autoquiet as _aq" in ak and "_aq.hold" in ak, "ai_kit.tg тримає чергу")
ok("import autoquiet as _aq_c" in msrc, "monitor._send_telegram_chunk")
ok("_aq_k.hold(text, kind=\"offer\", keyboard=keyboard)" in msrc,
   "monitor keyboard-sender зберігає кнопки")
ml = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_loop.py")).read()
ok("_aq_w.flush()" in ml, "воркер віддає відкладене після пробудження")
bs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
ok('"/тиша"' in bs and '"/покажи_відкладене"' in bs, "команди /тиша і /покажи_відкладене")

print(f"\nfails: {FAILS}")
sys.exit(1 if FAILS else 0)
