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
ok(gr.wanted("MSG_DEEP_ANALYSIS", "Проаналізуй день Олега"),
   "MSG_* (проактивні у проді) → інтернет")
ok(gr.wanted("MSG_CRYPTO_MOVE", "BTC різко змінився, поясни"),
   "MSG_CRYPTO_MOVE → інтернет")
ok(gr.wanted("MORNING_AI", "Ранкове повідомлення"), "MORNING_AI → інтернет")

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
b2 = gr.inject(body("Аналіз ринку", maxOutputTokens=500, temperature=0.9,
                    thinkingConfig={"thinkingBudget": 0}), "crypto_ai")
_gc2 = json.loads(b2.decode())["generationConfig"]
ok(_gc2.get("thinkingConfig", {}).get("thinkingBudget") == gr.THINK_BUDGET,
   "нульовий thinkingBudget замінено на обмежений (0 ріже пошук)")
ok(_gc2["maxOutputTokens"] >= 3000,
   "низьку стелю виводу піднято (інакше думання з'їдає весь текст)")
ok(_gc2.get("temperature") == 0.9, "решта конфігу не зачеплена")

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
seg = msrc[i:i + 14000]
ok("grounding as _gr" in seg, "grounding імпортується в _gem_post")
ok(seg.find("_gr.inject") < seg.find("for _mi, _model"), "inject ДО циклу моделей")
ok("_gr2.strip(body_bytes)" in seg, "є аварійний відкат при 400/403")
ok("clean_text" in seg, "маркери цитат чистяться у відповіді")
ok(seg.find("ai_brain") < seg.find("_gr.inject"),
   "grounding після ai_brain (пам'ять не втрачається)")

print("\n1b) Пошук не має з'їдати вивід (обрізані повідомлення)")
_b = json.dumps({"contents": [{"parts": [{"text": "Що нового по BTC цього тижня?"}]}],
                 "generationConfig": {"temperature": 0.9, "maxOutputTokens": 1400,
                                      "thinkingConfig": {"thinkingBudget": 0}}}).encode()
_o = json.loads(gr.inject(_b, "MSG_DEEP_ANALYSIS").decode())
_gc = _o.get("generationConfig") or {}
ok(_o.get("tools") == [{"google_search": {}}], "пошук увімкнено")
ok(_gc.get("thinkingConfig", {}).get("thinkingBudget") == gr.THINK_BUDGET,
   f"думання обмежене ({_gc.get('thinkingConfig')})")
ok(_gc.get("maxOutputTokens") >= 3000,
   f"стеля виводу піднята ({_gc.get('maxOutputTokens')})")
_b2 = json.dumps({"contents": [{"parts": [{"text": "Що нового по BTC?"}]}],
                  "generationConfig": {"maxOutputTokens": 8000}}).encode()
ok(json.loads(gr.inject(_b2, "MSG_DEEP_ANALYSIS").decode())["generationConfig"]["maxOutputTokens"] == 8000,
   "більшу стелю не занижуємо")

# ── 2. РЕЖИМ ТИШІ (ТІЛЬКИ ВРУЧНУ) ────────────────────────────────────────────
print("\n2) Тиша тільки після /тиша, авто-вихід о 04:00")
import quiet as q  # noqa: E402

QSTATE = {}
q._load = lambda: dict(QSTATE)


def _qsave(d):
    QSTATE.clear()
    QSTATE.update(d)
    return True


q._save = _qsave


def at(h, d=20):
    now = datetime(2026, 8, d, h, 0)
    aq._now = lambda: now
    q._now = lambda: now
    aq._cache["data"] = None
    aq._cache["ts"] = 0.0


# без команди — жодної тиші, у будь-яку годину
QSTATE.clear()
for h in (2, 4, 9, 13, 23):
    at(h)
    ok(not aq.sleeping(), f"{h:02d}:00 без команди — тиші НЕМА")

print("\n2b) /тиша вмикає тишу до найближчих 04:00")
at(22)
r = q.sleep_on()
ok(r["until"].hour == 4 and r["until"].day == 21, f"until = 21.08 04:00 ({r['until']})")
ok(aq.sleeping(), "22:00 після /тиша — тиша")
at(2, 21)
ok(aq.sleeping(), "02:00 — ще тиша")
st = aq.state()
ok(str(st.get("until"))[11:16] == "04:00", "стан показує пробудження о 04:00")

print("\n2c) О 04:00 бот сам виходить із тиші")
at(4, 21)
ok(not aq.sleeping(), "04:00 — тиша знята автоматично")
ok(not QSTATE.get("until"), "стан очищено (авто-пробудження)")

print("\n2d) /прокинувся — ручний вихід раніше")
at(23)
q.sleep_on()
ok(aq.sleeping(), "тиша увімкнена")
q.sleep_off()
ok(not aq.sleeping(), "після /прокинувся — тиші нема")

print("\n2e) Під час тиші відкладаємо ВСЕ (нічого не губиться)")
at(23)
q.sleep_on()
q._last_user_action["ts"] = 0.0
ok(aq.should_hold("🔮 Астро-прогноз на день"), "астро — у чергу")
ok(aq.should_hold("🔴 ВАЖЛИВО: лист від Michaela"), "навіть важливе — у чергу, не будимо")

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
ok(aq.flush() == 0, "поки тиша — не віддаємо")

at(4, 21)  # авто-пробудження
ok(not aq.sleeping(), "контроль: тиші вже нема")
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
at(23)
q.sleep_on()
q._last_user_action["ts"] = 0.0
MEM.clear()
q.mark_user_thread()
ok(not aq.should_hold("🔮 Астро-прогноз"), "його потік — відповідаємо негайно")
q.clear_user_thread()
ok(aq.should_hold("🔮 Астро-прогноз"), "фоновий потік — знову тиша")

print("\n2i) Під'єднано до відправників")
ak = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_kit.py")).read()
ok("autoquiet as _aq" in ak and "_aq.hold" in ak, "ai_kit.tg тримає чергу")
ok(ak.index("_aq.hold") < ak.index('_q_g.blocked("msg")'),
   "у ai_kit черга ПЕРЕД quiet-guard (нічого не губиться)")
ok("import autoquiet as _aq_c" in msrc, "monitor._send_telegram_chunk")
ok(msrc.index("_aq_c.hold") < msrc.index('_q_g.blocked("msg")'),
   "у monitor черга ПЕРЕД quiet-guard")
ok("_aq_k.hold(text, kind=\"offer\", keyboard=keyboard)" in msrc,
   "monitor keyboard-sender зберігає кнопки")
ml = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor_loop.py")).read()
ok("_aq_w.flush()" in ml, "воркер віддає відкладене після пробудження")
bs = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
ok('"/тиша"' in bs and '"/покажи_відкладене"' in bs, "команди /тиша і /покажи_відкладене")
ok("_q.sleep_on()" in bs.split('elif text in ["/тиша"')[1][:600],
   "/тиша вмикає режим тиші (а не просто показує стан)")
ok('"/тиша_статус"' in bs, "статус переїхав на /тиша_статус")

print(f"\nfails: {FAILS}")
sys.exit(1 if FAILS else 0)
