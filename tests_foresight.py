# -*- coding: utf-8 -*-
"""Локальний тест foresight.py + askme remind + calgate.gate_write."""
import sys, types, json
from datetime import datetime, timedelta
sys.path.insert(0, '/home/user/repo')

STORE = {}
CARDS = []
NOW = datetime(2026, 9, 3, 15, 0, 0)

# ─── stub ai_kit ────────────────────────────────────────────────────────────
K = types.ModuleType('ai_kit')
K.datetime = datetime
K.timedelta = timedelta


def now():
    return NOW


K.now = now
K.today_str = lambda: NOW.strftime('%Y-%m-%d')
K.log = lambda t, m: print(f'  [log {t}] {m}')
K.load = lambda f, default=None: STORE.get(f, default)
K.save = lambda f, d: STORE.__setitem__(f, d)


def update_key(f, k, v):
    d = STORE.setdefault(f, {})
    d[k] = v


K.update_key = update_key
K.remove_key = lambda f, k: STORE.get(f, {}).pop(k, None)
K.rate_ok = lambda f, m: True
K.rate_mark = lambda f: None
K.esc = lambda s: str(s)


def send_card(text, keyboard=None, tag='ai_kit', chat_id=None):
    CARDS.append({'text': text, 'kb': keyboard, 'tag': tag})
    print(f'\n─── CARD [{tag}] ───\n{text[:1200]}')
    if keyboard:
        print('  КНОПКИ:', [[b["text"] for b in row] for row in keyboard])
    return True


K.send_card = send_card
K.gemini_text = lambda p, **kw: ''          # порожньо → перевіряємо fallback
K.gemini_json = lambda p, **kw: {}
K.tg = lambda m, b, tag='': {'ok': True}
CAL = []


def calendar_event(summary, start_dt, end_dt=None, description='', force=False):
    CAL.append({'summary': summary, 'start': start_dt, 'force': force})
    print(f'  📅 CALENDAR WRITE: {summary} @ {start_dt} force={force}')
    return {'ok': True}


K.calendar_event = calendar_event
EVENTS = {
    4: [{'summary': '✈️ Виліт у Малагу, Іспанія', 'start': {'dateTime': '2026-09-07T06:20:00'}}],
    5: [{'summary': 'Готель Malaga centro', 'start': {'dateTime': '2026-09-08T14:00:00'}}],
    8: [{'summary': 'Малага — назад до Кошиць', 'start': {'dateTime': '2026-09-11T18:00:00'}}],
    1: [{'summary': '🌙 Нічна зміна', 'start': {'dateTime': '2026-09-04T18:00:00'}}],
    2: [{'summary': '🌙 Нічна зміна', 'start': {'dateTime': '2026-09-05T18:00:00'}}],
    3: [{'summary': '🌙 Нічна зміна', 'start': {'dateTime': '2026-09-06T18:00:00'}}],
}
K.events_for_day = lambda off=0: EVENTS.get(off, [])
_SN = ('нічна', 'night')
_SE = ('рання', 'early')


def classify_shift(evs):
    for e in evs or []:
        s = str(e.get('summary', '')).lower()
        if any(x in s for x in _SN):
            return 'night'
        if any(x in s for x in _SE):
            return 'early'
    return 'free'


K.classify_shift = classify_shift


def shift_map(days=7):
    return {(NOW + timedelta(days=o)).strftime('%Y-%m-%d'):
            classify_shift(EVENTS.get(o, [])) for o in range(days)}


K.shift_map = shift_map
K.parse_dt = lambda d, t='09:00': datetime.strptime(d + ' ' + t, '%Y-%m-%d %H:%M')
K.valid_future_date = lambda d, allow_today=True: d


class PayloadStore:
    def __init__(self, f):
        self.f = f
        self.d = {}
        self.n = 0

    def put(self, p):
        self.n += 1
        pid = str(self.n)
        self.d[pid] = p
        return pid

    def get(self, pid):
        return self.d.get(pid)

    def drop(self, pid):
        self.d.pop(pid, None)

    def gc(self):
        pass


K.PayloadStore = PayloadStore
K.TELEGRAM_CHAT = '1'
class _Dedup:
    def __init__(self, filename, ttl_days=6):
        self.filename = filename
        self.ttl_days = ttl_days

    @staticmethod
    def key(*parts):
        return '|'.join(str(p or '').lower().strip()[:60] for p in parts)

    def seen(self, *parts):
        return bool((STORE.get(self.filename) or {}).get(self.key(*parts)))

    def mark(self, *parts):
        STORE.setdefault(self.filename, {})[self.key(*parts)] = NOW.isoformat()


K.Dedup = _Dedup
K.valid_future_date = lambda d, allow_today=False: d
K.parse_dt = lambda d, t='09:00': NOW + timedelta(hours=2)
K.esc = lambda s: str(s)
sys.modules['ai_kit'] = K

# ─── stub httpreq (open-meteo) ──────────────────────────────────────────────
hr = types.ModuleType('httpreq')


class R:
    def __init__(self, j):
        self._j = j
        self.ok = True
        self.status_code = 200
        self.text = json.dumps(j)

    def json(self):
        return self._j


def get(url, params=None, timeout=10):
    if 'geocoding' in url:
        return R({'results': [{'latitude': 36.72, 'longitude': -4.42,
                               'name': 'Málaga', 'country': 'Іспанія'}]})
    return R({'daily': {
        'time': ['2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11'],
        'temperature_2m_max': [29, 30, 28, 27, 31],
        'temperature_2m_min': [21, 22, 20, 20, 22],
        'precipitation_sum': [0, 0, 2.4, 0, 0],
        'weathercode': [0, 1, 61, 2, 0]}})


hr.get = get
hr.post = lambda *a, **kw: R({})
sys.modules['httpreq'] = hr

# ─── stub monitor (листи) ───────────────────────────────────────────────────
mon = types.ModuleType('monitor')
mon.get_emails = lambda: {'items': [
    {'from': 'HR Minebea', 'subject': 'Графік змін на жовтень — підтвердіть', 'starred': True},
    {'from': 'Maroš Sivák InterFin', 'subject': 'Зустріч по портфелю 09.09'},
    {'from': 'Sorare', 'subject': 'Flash sale 50% off — promo code', 'starred': False},
    {'from': 'newsletter@binance', 'subject': 'Unsubscribe? Weekly digest акції'},
]}
sys.modules['monitor'] = mon

dsm = types.ModuleType('dismissed')
dsm.mute = lambda *a, **kw: print(f'  🔇 dismissed.mute {a} {kw}')
dsm.muted = lambda *a, **kw: False
sys.modules['dismissed'] = dsm

st = types.ModuleType('storage')
st.load = K.load
st.save = K.save
sys.modules['storage'] = st

ok_n = fail_n = 0


def ok(cond, label):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f'✅ {label}')
    else:
        fail_n += 1
        print(f'❌ {label}')


import askme as A
import calgate as CG
import foresight as F

print('\n════════ 1. ПОЇЗДКА ════════')
r = F._trip_brief()
ok(r.startswith('trip:'), f'бриф поїздки надіслано ({r})')
trip_card = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_TRIP'), None)
ok(trip_card is not None, 'картка поїздки є')
ok(trip_card and 'Málaga' in trip_card['text'] or 'Malaga' in (trip_card or {}).get('text', ''),
   'у картці місто Málaga')
ok(trip_card and '29' in trip_card['text'] or '31' in (trip_card or {}).get('text', ''),
   'у картці реальна температура з open-meteo')
ok(trip_card and 'ЩО ВЗЯТИ' in trip_card['text'], 'є список речей')
ask_card = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_TRIP_ASK'), None)
ok(ask_card is not None, 'бот СПИТАВ про нагадування зібрати речі')
ok(ask_card and ask_card['kb'] and 'нагадай' in ask_card['kb'][0][0]['text'].lower(),
   'кнопка «🔔 Так, нагадай»')
ok(not CAL, 'у календар ЩЕ нічого не записано (бо не натиснуто)')

print('\n════════ 2. НАТИСКАННЯ «Так, нагадай» → реальний запис ════════')
pid = ask_card['kb'][0][0]['callback_data'].split('_')[-1]
res = A.handle('am_save_' + pid)
print('  →', res['text'])
rem = STORE.get('reminders.json') or []
ok(len(rem) == 1, f'нагадування записано в reminders.json ({len(rem)})')
ok(any('Зібрати речі' in str(r.get('text')) for r in rem), 'текст нагадування правильний')
ok(len(CAL) == 1 and CAL[0]['force'] is False, 'подія в календарі створена (через allow_once)')

print('\n════════ 3. ПОВТОР — те саме не питається вдруге ════════')
CARDS.clear()
r2 = F._trip_brief()
ok(r2 == '', 'повторний бриф тієї ж поїздки НЕ надсилається')

print('\n════════ 4. БЛОК НІЧНИХ ЗМІН ════════')
STORE['reminders.json'] = [
    {'id': 'x1', 'datetime_utc': '2026-09-04T17:00:00',
     'text': '🔔 <b>Забрати взуття</b>', 'sent': False}]
CARDS.clear()
r3 = F._shift_brief()
ok(r3.startswith('nights:'), f'бриф нічних надіслано ({r3})')
nc = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_SHIFTS'), None)
ok(nc is not None, 'картка нічних є')
ok(nc and 'взуття' in nc['text'], 'у картці — незакрита справа «взуття»')
nask = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_SHIFTS_ASK'), None)
ok(nask is not None, 'бот спитав про нагадування під вікно перед нічними')

print('\n════════ 5. ЛИСТИ: ВАЖЛИВІ СПЕРШУ ════════')
CARDS.clear()
r4 = F._mail_brief()
ok(r4.startswith('mail:'), f'бриф листів надіслано ({r4})')
mc = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_MAIL'), None)
ok(mc and 'HR Minebea' in mc['text'], 'важливий лист від HR у списку')
ok(mc and 'Maroš' in mc['text'], 'важливий лист від InterFin у списку')
ok(mc and 'Flash sale' not in mc['text'], 'рекламний Flash sale НЕ в списку')
ok(mc and 'Реклама: 2' in mc['text'], 'реклама згорнута одним рядком (2 шт.)')

print('\n════════ 6. calgate.gate_write — нічого без «так» ════════')
CARDS.clear()
CG._take_allow()  # знімаємо дозвіл, що лишився від кроку 2 (заглушка календаря його не їсть)
blk = CG.gate_write('reminder', 'Забрати взуття з ремонту',
                    NOW + timedelta(days=2), 'Пʼятниця, до 18:00')
ok(blk is not None and blk.get('pending'), 'запис заблоковано, питання надіслано')
wc = next((c for c in CARDS if str(c['tag']).startswith('MSG_WRITE_ASK')), None)
ok(wc is not None and 'Поставити?' in wc['text'], 'питання «Поставити?» з кнопками')
blk2 = CG.gate_write('reminder', '💧 Вода 2л ✅', NOW)
ok(blk2 is not None and not blk2.get('pending'), 'трекер відкинуто тихо, без питання')
blk3 = CG.gate_write('reminder', 'Знижка 50% промокод SALE', NOW)
ok(blk3 is not None and not blk3.get('pending'), 'реклама відкинута тихо')
blk4 = CG.gate_write('note', 'Купити молоко', NOW)
ok(blk4 is not None and blk4.get('pending'), 'навіть дрібна нотатка — ПИТАЄ (нове правило)')
blk5 = CG.gate_write('reminder', 'Що завгодно', NOW, force=True)
ok(blk5 is None, 'force=True — пише без питання (Олег сам попросив)')

print('\n════════ 7. /вперед ════════')
rep = F.report()
print(rep[:700])
ok('Málaga' in rep or 'Malaga' in rep, 'у звіті видно поїздку')
ok('нічних поспіль' in rep, 'у звіті видно блок нічних')

print('\n════════ 8. selfact._do_note — не пише без «так» ════════')
NOTES = []
import types as _t
_an = _t.ModuleType('ai_notes')
_an.add_note = lambda text, source='manual': NOTES.append({'text': text, 'source': source})
_an.load_notes = lambda: list(NOTES)
_an.get_notes_context = lambda max_notes=15: ''
sys.modules['ai_notes'] = _an
CARDS.clear()
NOTES.clear()
try:
    import selfact as SA
    CG._take_allow()
    r1 = SA._do_note({'title': 'Оплатити рахунок за інтернет', 'text': 'Оплатити рахунок за інтернет до 10.09'})
    ok(r1 is False and not NOTES, 'нотатка НЕ записана без дозволу')
    nc = next((c for c in CARDS if str(c['tag']).startswith('MSG_WRITE_ASK')), None)
    ok(nc is not None and 'нотатку' in nc['text'], 'бот спитав дозвіл на нотатку')
    CG.allow_once()
    r2 = SA._do_note({'title': 'Оплатити рахунок за інтернет', 'text': 'Оплатити рахунок за інтернет до 10.09'})
    ok(r2 is True and len(NOTES) == 1, 'після дозволу нотатка записана')
except Exception as _e:
    ok(False, 'selfact import/note: ' + str(_e))

print('\n════════ 9. GAP-BRIEF: вільне вікно ════════')
CARDS.clear()
STORE.pop('foresight_state.json', None)
EVENTS[0] = []  # день 0 (2026-09-03) вільний і без подій
r9 = F._gap_brief()
ok(r9.startswith('gap:'), f'бриф вільного вікна надіслано ({r9})')
gc = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_GAP'), None)
ok(gc is not None, 'картка вільного вікна є')
gask = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_GAP_ASK'), None)
ok(gask is not None, 'бот спитав про запис у вільне вікно')
CARDS.clear()
r9b = F._gap_brief()
ok(r9b == '', 'повторний бриф того ж вікна НЕ надсилається')

print('\n════════ 10. GAP-BRIEF: немає вільних днів → мовчить ════════')
STORE.pop('foresight_state.json', None)
EVENTS[0] = [{'summary': '🌙 Нічна зміна', 'start': {'dateTime': '2026-09-03T18:00:00'}}]
EVENTS[1] = [{'summary': '🌙 Нічна зміна', 'start': {'dateTime': '2026-09-04T18:00:00'}}]
CARDS.clear()
r10 = F._gap_brief()
ok(r10 == '', 'немає вільних днів (0,1) — бриф мовчить')
EVENTS[0] = []  # повертаємо як було

print('\n════════ 11. TRY-NEW: ротація раз на N днів ════════')
STORE.pop('foresight_state.json', None)
CARDS.clear()
r11 = F._try_new_brief()
ok(r11.startswith('trynew:'), f'бриф «спробуй нове» надіслано ({r11})')
tnc = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_TRYNEW'), None)
ok(tnc is not None, 'картка «спробуй нове» є')
tnask = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_TRYNEW_ASK'), None)
ok(tnask is not None, 'бот спитав про нагадування спробувати')
CARDS.clear()
r11b = F._try_new_brief()
ok(r11b == '', 'той самий день — повторно НЕ брифить (gap < 3 дні)')

print('\n════════ 12. CRYPTO-WATCH: рух ≥5% через DefiLlama-стаб ════════')
lp = types.ModuleType('llama_prices')
lp.change_pct = lambda hours, symbols=None: {
    'BTC': {'now': 61000.0, 'then': 57000.0, 'pct': 7.02},
    'ETH': {'now': 2500.0, 'then': 2490.0, 'pct': 0.4},
}
sys.modules['llama_prices'] = lp
STORE.pop('foresight_state.json', None)
CARDS.clear()
r12 = F._crypto_watch_brief()
ok(r12.startswith('crypto:BTC'), f'бриф руху BTC надіслано ({r12})')
cc = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_CRYPTO'), None)
ok(cc is not None, 'картка крипто-руху є')
ok(cc and 'DefiLlama' in cc['text'], 'у картці явно вказано джерело DefiLlama')
ok(cc and 'ETH' not in cc['text'], 'ETH (рух <5%) НЕ згадано — тільки BTC')
CARDS.clear()
r12b = F._crypto_watch_brief()
ok(r12b == '', 'той самий рух BTC того ж дня — повторно НЕ брифить')

print('\n════════ 13. CRYPTO-WATCH: рух ≥10% → додатково питає нотатку ════════')
STORE.pop('foresight_state.json', None)
lp.change_pct = lambda hours, symbols=None: {
    'SOL': {'now': 300.0, 'then': 250.0, 'pct': 20.0},
}
CARDS.clear()
r13 = F._crypto_watch_brief()
ok(r13.startswith('crypto:SOL'), f'бриф сильного руху SOL надіслано ({r13})')
snote = next((c for c in CARDS if c['tag'] == 'MSG_FORESIGHT_CRYPTO_ASK'), None)
ok(snote is not None, 'при русі ≥10% бот ДОДАТКОВО питає про нотатку')

print('\n════════ 14. CRYPTO-WATCH: DefiLlama недоступний → тихо мовчить ════════')
STORE.pop('foresight_state.json', None)


def _boom(hours, symbols=None):
    raise RuntimeError('no network')


lp.change_pct = _boom
CARDS.clear()
r14 = F._crypto_watch_brief()
ok(r14 == '', 'DefiLlama впав — бриф тихо мовчить, без падіння')
ok(not CARDS, 'жодної картки не надіслано при збою DefiLlama')

print(f'\n══════ РЕЗУЛЬТАТ: {ok_n} ok, {fail_n} fail ══════')
sys.exit(1 if fail_n else 0)
