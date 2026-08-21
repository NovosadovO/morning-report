#!/usr/bin/env python3
"""
Офлайн-тести трьох нових трекерів (без мережі, без Telegram, без Gemini):
  1) dates_book   — дні народження і важливі дати
  2) subs_watcher — регулярні платежі та підписки
  3) rwa_radar    — RWA топ-10 (пороги сповіщень, фонди vs монети)
  4) під'єднання до bot.py / intelligent_listener.py / monitor.py

Запуск:  TELEGRAM_TOKEN=x TELEGRAM_CHAT_ID=1 python3 -u tests_new_trackers.py
"""

import os
import sys
import json
from datetime import datetime, timedelta

os.environ.setdefault("TELEGRAM_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILS = 0
NOW = datetime(2026, 8, 21, 10, 0, 0)   # фіксований «зараз» для детермінізму


def ok(cond, msg):
    global FAILS
    if cond:
        print(f"✅ {msg}")
    else:
        FAILS += 1
        print(f"❌ {msg}")


def head(t):
    print(f"\n{t}")


# ─── ЗАГЛУШКИ storage (нічого не летить у GitHub) ─────────────────────────────
import ai_kit as K

_MEM = {}


def _load(fn, default=None):
    return json.loads(json.dumps(_MEM.get(fn, default if default is not None else {})))


def _save(fn, data):
    _MEM[fn] = json.loads(json.dumps(data))
    return True


def _update_key(fn, key, value):
    d = _MEM.setdefault(fn, {})
    d[key] = json.loads(json.dumps(value))
    return True


def _remove_key(fn, key):
    _MEM.get(fn, {}).pop(key, None)
    return True


_SENT = []


def _send_card(text, keyboard=None, tag="t", chat_id=None):
    _SENT.append({"text": text, "kb": keyboard, "tag": tag})
    return True


K.load = _load
K.save = _save
K.update_key = _update_key
K.remove_key = _remove_key
K.send_card = _send_card
K.now = lambda: NOW
K.today_str = lambda: NOW.strftime("%Y-%m-%d")
K.gemini_text = lambda *a, **kw: "AI-текст (заглушка)"
K.gemini_json = lambda *a, **kw: []
K.calendar_event = lambda *a, **kw: {"ok": True, "id": "ev1"}
K.events_for_day = lambda off=0: []

import dates_book as DB
import subs_watcher as SB
import rwa_radar as RW

# модулі тримають власні store/dedup, створені ДО підміни — перестворюємо
for M in (DB, SB, RW):
    if hasattr(M, "_store"):
        M._store = K.PayloadStore(M.STORE_FILE)
    if hasattr(M, "_dedup"):
        M._dedup = K.Dedup(M.SENT_FILE, ttl_days=20)
SB._due_dedup = K.Dedup(SB.DUE_STATE, ttl_days=2)


# ═══════════════════════════════════════════════════════════════════════════════
head("1) dates_book: розбір дати в усіх формах, які реально напише Олег")
ok(DB.parse_date("14.03") == ("03-14", ""), "14.03 → 03-14 без року")
ok(DB.parse_date("12.03.1990") == ("03-12", "1990"), "12.03.1990 → рік окремо")
ok(DB.parse_date("1990-03-12") == ("03-12", "1990"), "ISO-формат")
ok(DB.parse_date("2 листопада")[0] == "11-02", "'2 листопада' словами")
ok(DB.parse_date("2 november")[0] == "11-02", "англійський місяць")
ok(DB.parse_date("14.03.90") == ("03-14", "1990"), "дворічний рік → 1990")
ok(DB.parse_date("сміття") == ("", ""), "сміття не стає датою")
ok(DB.parse_date("45.99") == ("", ""), "неможлива дата відкинута")

head("2) dates_book: рік не потрібен — дата повторюється щороку")
nxt = DB._next_occurrence("12-31")
ok(nxt is not None and nxt.year == 2026 and nxt.month == 12, "31.12 цього року")
past = DB._next_occurrence("01-05")
ok(past is not None and past.year == 2027, "дата, що вже минула → наступний рік")
ok(DB._next_occurrence("02-29") is not None, "29 лютого не ламає (→ 28.02)")
ok(DB._next_occurrence("xx") is None, "битий формат → None")

head("3) dates_book: додавання, вік, нагадування за 7/3/1/0")
_MEM.clear()
_SENT.clear()
r = DB.add("Міхаела", "24.08", note="любить каву")
ok(r.get("ok") and r["days"] == 3, f"додано, до дати 3 дні (отримано {r.get('days')})")
r2 = DB.add("Мама", "21.08.1965")
ok(r2.get("ok") and r2["days"] == 0, "дата сьогодні → 0 днів")
up = DB.upcoming(30)
ok(len(up) == 2, f"в горизонті 30 днів дві дати (маємо {len(up)})")
mama = [u for u in up if u["rec"]["name"] == "Мама"][0]
ok(mama["age"] == 61, f"вік порахований: 61 (маємо {mama['age']})")
ok(up[0]["days_left"] <= up[1]["days_left"], "сортування за близькістю")

n = DB.check_upcoming()
ok(n == 2, f"надіслано 2 нагадування (маємо {n})")
ok(any("СЬОГОДНІ" in s["text"] for s in _SENT), "для сьогоднішньої дати тон 'СЬОГОДНІ'")
ok(any("ЧЕРЕЗ 3 ДН" in s["text"] for s in _SENT), "для дати за 3 дні свій заголовок")
ok(all(s["kb"] for s in _SENT), "під кожним нагадуванням є кнопки")
ok(any("db_wish_" in json.dumps(s["kb"]) for s in _SENT), "є кнопка привітання")

before = len(_SENT)
ok(DB.check_upcoming() == 0, "повторний прогін не дублює нагадування")
ok(len(_SENT) == before, "нічого зайвого не надіслано")

head("4) dates_book: кнопка «не нагадувати» справді глушить")
pid = list(_MEM.get(DB.STORE_FILE, {}).keys())[0]
res = DB.do_skip(pid)
ok(res.get("ok"), "do_skip відпрацював")
muted = [r for r in _MEM[DB.DATES_FILE].values() if r.get("muted")]
ok(len(muted) == 1, "запис позначено muted")
ok(all(not u["rec"].get("muted") for u in DB.upcoming(30)), "muted зникає зі списку")

head("5) dates_book: /дата з вільного тексту")
_MEM.clear()
r = DB.add_from_text("/дата Марош Сівак 03.09 інвестиції, партнер")
ok(r.get("ok"), "розібрав команду з іменем, датою і нотаткою")
ok(r["rec"]["name"] == "Марош Сівак", f"ім'я з двох слів (маємо {r['rec']['name']})")
ok("інвестиції" in r["rec"]["note"], "нотатка збережена")
ok(DB.add_from_text("/дата Хтось").get("error") == "bad_date", "без дати — чесна помилка")
r3 = DB.add_from_text("/дата Оля і Петро 10.10 річниця весілля")
ok(r3.get("ok") and r3["rec"]["kind"] == "anniversary", "слово 'річниця' → тип anniversary")

head("6) dates_book: календар ставиться на 3 роки вперед")
_MEM.clear()
_SENT.clear()
DB.add("Тест", "24.08")
DB.check_upcoming()
pid = list(_MEM[DB.STORE_FILE].keys())[0]
res = DB.do_calendar(pid)
ok(res.get("ok") and len(res.get("dates", [])) == 3,
   f"створено 3 події (маємо {len(res.get('dates', []))})")

# ═══════════════════════════════════════════════════════════════════════════════
head("7) subs_watcher: цикл, суми, наступне списання")
ok(SB._cycle_months("monthly") == 1.0 and SB._cycle_months("yearly") == 12.0,
   "місячний і річний цикл")
ok(SB._cycle_months("шось") == 1.0, "невідомий цикл → трактуємо як місячний")
ok(SB._add_months(datetime(2026, 8, 14), 1).strftime("%Y-%m-%d") == "2026-09-14",
   "14.08 + 1 міс = 14.09 (а не +30 днів)")
ok(SB._add_months(datetime(2026, 1, 31), 1).strftime("%Y-%m-%d") == "2026-02-28",
   "31.01 + 1 міс = 28.02 (без вилітання за межі місяця)")
rec = {"cycle": "monthly", "charges": [{"date": "2026-07-14", "amount": "13.49"},
                                       {"date": "2026-08-14", "amount": "13.49"}]}
ok(SB._calc_next(rec) == "2026-09-14", f"наступне списання (маємо {SB._calc_next(rec)})")
old = {"cycle": "monthly", "charges": [{"date": "2026-02-03", "amount": "9.99"}]}
nxt = SB._calc_next(old)
ok(nxt >= NOW.strftime("%Y-%m-%d"), f"стара дата прокручується в майбутнє ({nxt})")

head("8) subs_watcher: підписка підтверджується двома місяцями або словом у листі")
_MEM.clear()
_SENT.clear()
k, r, ev = SB._upsert({"vendor": "Netflix", "amount": "13.49", "currency": "EUR",
                       "cycle": "monthly", "charge_date": "2026-07-14",
                       "recurring": False, "uid": "1"}, {})
ok(not r["confirmed"] and ev == "charge", "одне списання — ще не підписка")
k, r, ev = SB._upsert({"vendor": "Netflix", "amount": "13.49", "currency": "EUR",
                       "cycle": "monthly", "charge_date": "2026-08-14",
                       "recurring": False, "uid": "2"}, {})
ok(r["confirmed"] and ev == "new_confirmed", "два різні місяці — підписка підтверджена")
k2, r2, ev2 = SB._upsert({"vendor": "iCloud", "amount": "2.99", "currency": "EUR",
                          "cycle": "monthly", "charge_date": "2026-08-01",
                          "recurring": True, "uid": "3"}, {})
ok(r2["confirmed"] and ev2 == "new_confirmed", "AI побачив регулярність — одразу підписка")

head("9) subs_watcher: гроші рахуються правильно")
SB._upsert({"vendor": "Hosting", "amount": "60.00", "currency": "EUR",
            "cycle": "yearly", "charge_date": "2026-03-01",
            "recurring": True, "uid": "4"}, {})
tot = SB.monthly_total()
ok(tot["count"] == 3, f"3 активні підписки (маємо {tot['count']})")
expect = 13.49 + 2.99 + 60.0 / 12
ok(abs(tot["month"] - expect) < 0.01,
   f"місячна сума {tot['month']:.2f}€ = {expect:.2f}€ (річна поділена на 12)")
ok(abs(tot["year"] - expect * 12) < 0.1, "річна сума = місячна × 12")
ok(tot["items"][0]["vendor"] == "Netflix", "найдорожча підписка перша")

head("10) subs_watcher: подорожчання ловиться")
_SENT.clear()
k, r, ev = SB._upsert({"vendor": "Netflix", "amount": "15.49", "currency": "EUR",
                       "cycle": "monthly", "charge_date": "2026-09-14",
                       "recurring": True, "uid": "5"}, {})
ok(ev == "hike", f"подорожчання розпізнано (event={ev})")
ok(r.get("hike_from") == "13.49" and r.get("hike_to") == "15.49",
   "стара і нова ціна збережені")
ok(SB._offer_hike(k, r), "карточка про подорожчання надіслана")
ok("ПОДОРОЖЧАЛА" in _SENT[-1]["text"], "у тексті прямо сказано про подорожчання")
ok("на рік" in _SENT[-1]["text"], "показано, скільки це коштує на рік")
k, r, ev = SB._upsert({"vendor": "Netflix", "amount": "15.49", "currency": "EUR",
                       "cycle": "monthly", "charge_date": "2026-09-14",
                       "recurring": True, "uid": "5"}, {})
ok(ev != "hike", "той самий лист двічі не рахується за нове списання")

head("11) subs_watcher: попередження ДО списання, а не після")
_SENT.clear()
SB._due_dedup = K.Dedup(SB.DUE_STATE, ttl_days=2)
subs = _MEM[SB.SUBS_FILE]
for key in subs:
    subs[key]["next_due"] = (NOW + timedelta(days=2)).strftime("%Y-%m-%d")
n = SB.check_renewals()
ok(n == 3, f"попереджено про всі 3 підписки за 2 дні (маємо {n})")
ok(all("СКОРО СПИСАННЯ" in s["text"] for s in _SENT), "правильний заголовок")
ok(all("sb_cancel_" in json.dumps(s["kb"]) for s in _SENT),
   "під кожним є кнопка «хочу скасувати»")
ok(SB.check_renewals() == 0, "повторно не спамить")
_SENT.clear()
for key in subs:
    subs[key]["next_due"] = (NOW + timedelta(days=20)).strftime("%Y-%m-%d")
ok(SB.check_renewals() == 0, "за 20 днів ще не турбує")

head("12) subs_watcher: «вже не користуюсь» рахує економію")
_SENT.clear()
pid = SB._store.put({"key": "netflix", "vendor": "Netflix", "amount": "15.49",
                     "currency": "EUR", "cycle": "monthly"})
res = SB.do_stop(pid)
ok(res.get("ok"), "do_stop відпрацював")
ok(abs(res["saved_year"] - 15.49 * 12) < 0.1,
   f"економія за рік {res['saved_year']:.0f}€")
ok(res["count"] == 2, f"активних лишилось 2 (маємо {res['count']})")
ok("Netflix" not in json.dumps(SB.report_block()), "скасована зникла зі звіту")

head("13) subs_watcher: «не підписка» більше не пропонується")
pid = SB._store.put({"key": "icloud", "vendor": "iCloud"})
SB.do_skip(pid)
k, r, ev = SB._upsert({"vendor": "iCloud", "amount": "2.99", "cycle": "monthly",
                       "charge_date": "2026-10-01", "recurring": True, "uid": "9"}, {})
ok(ev == "", "після skip модуль про цей сервіс молчить")
ok(SB.monthly_total()["count"] == 1, "у грошах його теж більше немає")

head("14) subs_watcher: блок у звіт і /підписки")
blk = SB.report_block()
ok("ПІДПИСКИ" in blk and "€/міс" in blk, "у звіті є сума за місяць")
txt = SB.overview_text()
ok("Hosting" in txt, "у /підписки видно активні")
ok("Скасовані" in txt or "неактивні" in txt, "скасовані показані окремо")

# ═══════════════════════════════════════════════════════════════════════════════
head("15) rwa_radar: токенізований фонд відрізняється від монети")
fund = {"sym": "USDY", "name": "Ondo US Dollar Yield", "price": 1.14,
        "ch24": 0.0, "ch7d": 0.1}
coin = {"sym": "ONDO", "name": "Ondo", "price": 0.36, "ch24": 3.9, "ch7d": 8.6}
gold = {"sym": "PAXG", "name": "PAX Gold", "price": 4507.0, "ch24": 0.7, "ch7d": 4.2}
ok(RW._pegged(fund), "USDY = фонд (стоїть на місці, ціна біля 1)")
ok(not RW._pegged(coin), "ONDO = монета, не фонд")
ok(not RW._pegged(gold), "золото не плутаємо з фондом")
ok(RW._is_mine(coin), "ONDO позначений як позиція Олега")
ok(not RW._is_mine(gold), "PAXG не моя позиція")

head("16) rwa_radar: пороги сповіщень")
snap = {"ids": ["ondo", "paxgold", "usdy"]}
r = RW._reasons([dict(coin, id="ondo", mcap=1e9, ch24=5.5)], {}, snap)
ok(len(r) == 1 and "ONDO" in r[0]["text"], "ONDO ±5% → причина написати")
r = RW._reasons([dict(coin, id="ondo", mcap=1e9, ch24=3.0)], {}, snap)
ok(not r, "ONDO 3% — це шум, молчимо")
r = RW._reasons([dict(gold, id="paxgold", mcap=1e9, ch24=4.0)], {}, snap)
ok(not r, "чужа монета 4% — молчимо (порог 8%)")
r = RW._reasons([dict(gold, id="paxgold", mcap=1e9, ch24=9.0)], {}, snap)
ok(len(r) == 1, "чужа монета 9% → причина є")
r = RW._reasons([dict(fund, id="usdy", mcap=1e9, ch24=-1.5)], {}, snap)
ok(len(r) == 1 and "АНОМАЛІЯ" in r[0]["text"], "фонд впав 1.5% → аномалія/депег")
r = RW._reasons([dict(fund, id="usdy", mcap=1e9, ch24=0.1)], {}, snap)
ok(not r, "фонд стоїть на місці — нормально, молчимо")
r = RW._reasons([], {"mcap": 6.9e10, "ch24": -6.0}, snap)
ok(len(r) == 1 and "сектор" in r[0]["text"], "сектор -6% → причина")
r = RW._reasons([], {"mcap": 6.9e10, "ch24": 1.0}, snap)
ok(not r, "сектор +1% — молчимо")
r = RW._reasons([dict(coin, id="newcoin", sym="NEW", mcap=1e9, ch24=1.0)], {}, snap)
ok(len(r) == 1 and "нова монета" in r[0]["text"], "нова монета в топ-10 — новина")
r = RW._reasons([dict(coin, id="tiny", sym="TINY", mcap=1e6, ch24=1.0)], {}, snap)
ok(not r, "дрібнота (<300M) в топ-10 не рахується новиною")

head("17) rwa_radar: сповіщення не дублюється, «не турбувати» працює")
_MEM.clear()
_SENT.clear()
RW._dedup = K.Dedup(RW.SENT_FILE, ttl_days=2)
RW.top_coins = lambda n=10: [dict(coin, id="ondo", mcap=1e9, ch24=6.0)]
RW.sector = lambda: {"mcap": 6.9e10, "ch24": 0.5}
RW.tvl_top = lambda n=5: ([], 0.0)
ok(RW.check_moves(force=True) == 1, "перше сповіщення пішло")
ok("RWA-РАДАР" in _SENT[-1]["text"], "заголовок на місці")
ok("rw_mute_" in json.dumps(_SENT[-1]["kb"]), "є кнопка «не турбувати»")
ok(RW.check_moves(force=True) == 0, "та сама причина двічі не летить")
RW.mute_today()
ok(RW._muted(), "mute_today вмикає тишу по RWA на сьогодні")
ok(RW.check_moves() == 0, "під час mute нічого не летить")

head("18) rwa_radar: форматування чисел")
ok(RW._money(22197369759) == "$22.20B", f"мільярди (маємо {RW._money(22197369759)})")
ok(RW._money(2.5e6) == "$2.5M", "мільйони")
ok(RW._price(4507.58) == "$4 508", f"велика ціна без копійок ({RW._price(4507.58)})")
ok(RW._price(0.3624) == "$0.36240", f"дрібна ціна з 5 знаками ({RW._price(0.3624)})")
ok("🔴" in RW._pct(-3.2) and "🟢" in RW._pct(1.1), "колір за напрямком")
ok(RW._pct(None) == "—", "немає даних — риска, а не нуль")

head("19) rwa_radar: коли API мовчить — не вигадуємо цифри")
RW.top_coins = lambda n=10: []
blk = RW.report_block()
ok("не відповів" in blk or "немає" in blk, "у звіті чесно сказано, що даних немає")
ok(RW.check_moves(force=True) == 0, "без даних сповіщень немає")

# ═══════════════════════════════════════════════════════════════════════════════
head("20) Під'єднання до бота (файли, а не припущення)")
bot_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")).read()
ok('data.startswith("sb_")' in bot_src, "sb_ у диспетчері callback-ів")
ok('data.startswith("db_")' in bot_src, "db_ у диспетчері callback-ів")
ok('data.startswith("rw_")' in bot_src, "rw_ у диспетчері callback-ів")
ok(bot_src.count('elif data.startswith("sb_"):') == 1, "обробник sb_ існує один раз")
ok(bot_src.count('elif data.startswith("db_"):') == 1, "обробник db_ існує")
ok(bot_src.count('elif data.startswith("rw_"):') == 1, "обробник rw_ існує")
for cbd in ("sb_ok_", "sb_rem_", "sb_cancel_", "sb_stop_", "sb_skip_",
            "db_wish_", "db_gift_", "db_cal_", "db_snooze_", "db_skip_",
            "rw_top_", "rw_note_", "rw_mute_"):
    ok(f'"{cbd}"' in bot_src or f"'{cbd}'" in bot_src, f"кнопка {cbd} має обробник")
for cmd in ("/підписки", "/дати", "/дата", "/дати_імпорт", "/rwa"):
    ok(cmd in bot_src, f"команда {cmd} додана")

lis_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "intelligent_listener.py")).read()
ok("import subs_watcher as _sb_l" in lis_src, "subs_watcher крутиться у слухачі")
ok("import dates_book as _db_l" in lis_src, "dates_book крутиться у слухачі")
ok("import rwa_radar as _rw_l" in lis_src, "rwa_radar крутиться у слухачі")
ok("_sb_l.check_renewals()" in lis_src, "попередження про списання викликаються")
ok("_db_l.check_upcoming()" in lis_src, "нагадування про дати викликаються")
ok("_rw_l.check_moves()" in lis_src, "перевірка рухів RWA викликається")

mon_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "monitor.py")).read()
ok("_rw_rep.report_block()" in mon_src, "RWA-блок у звіті")
ok("_sb_rep.report_block()" in mon_src, "блок підписок у звіті")
ok("_db_rep.report_block()" in mon_src, "блок важливих дат у звіті")
ok("Сектор RWA за 24г" in mon_src, "RWA потрапляє в AI-контекст")
ok("Підписки:" in mon_src, "підписки потрапляють в AI-контекст")
ok("Важливі дати:" in mon_src, "дати потрапляють в AI-контекст")

print(f"\nfails: {FAILS}")
sys.exit(0)
