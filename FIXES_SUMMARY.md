# Діагностика та рішення: Частові сповіщення

## Проблема
Бот надсилав сповіщення по 2-3 рази на годину замість 1 разу (максимум).

## Root Cause Analysis

### 1. **Проактивні повідомлення (_PROACTIVE_LAST_MESSAGE_TIME)**
- `_PROACTIVE_LAST_MESSAGE_TIME = time.time()` у bot.py (лінія 36) ініціалізувалася при КОЖНОМУ старті бота
- При рестарті бота (що часто на Railway), `last_message_time` обнулювався на `time.now()`
- Функція `should_send_proactive_message()` перевіряла: `if time_since_last > 120*60 → send`
- Тому після рестарту `time_since_last = 0` → не повинна надсилати
- **АЛЕ** потім ще були умови "morning window" (06:00-10:00) → якщо був рестарт о 08:00 — надсилав знову
- Кожен цикл перевірки (кожні 0.1с) міг виконати це

### 2. **Часовий пояс (Race Condition)**
- Bot на Railway працює в UTC (hour=0-23 UTC)
- `should_send_proactive_message()` використовував `datetime.now()` (UTC)
- Dedup перевіряв: `if current_hour == last_sent_hour` де `current_hour = datetime.now().strftime("%Y-%m-%d %H")`
- Олег у Кошіце живе в UTC+2 (год 14:00 локально = 12:00 UTC)
-때문에було розбіжність між локальним часом і сервером
- Деdup не спрацьовував (різні часи!)

### 3. **Частота викликання Phase 1 Alerts**
- `_handle_event_based_alerts()` викликалася ВСЕРЕДИНІ основного polling-цикла бота
- Бот викликає `getUpdates()` кожні ~0.1 секунди (long-polling)
- **Це означає** `_handle_event_based_alerts()` викликалася 10+ разів на секунду!
- Кожен виклик робив:
  - `get_all_urgent_events()` → `get_emails()`, `get_calendar()`, `load_crypto()`
  - Перевірка: деdup за ключем `crypto_{date}` (max 1 per day)
- З dedup це працювало (max 1 alert per day), **але** API-квота швидко вичерпувалась

## Рішення (Commit 827bf81c1b)

### 1. **Файловий Dedup для Проактивних Повідомлень**
```json
// data/proactive_last_send.json
{
  "last_sent_timestamp": 1719379860.123,
  "last_sent_hour": "2026-06-26 14",
  "last_sent_iso": "2026-06-26T14:57:40.123+00:00"
}
```
- **Переважає**: Пережирує рестарти бота (персистентний)
- **UTC+2 фіксовано**: `datetime.now(timezone.utc) + timedelta(hours=2)`
- **Деdup за годиною**: `if current_hour == last_sent_hour → skip`

### 2. **Оновлена should_send_proactive_message()**
```python
# Новий сигнатур — не потребує last_message_time параметра
def should_send_proactive_message():
    # Читає дату/час з файлу (не залежить від перезавантажень)
    # Дозволяє 1 повідомлення на ГОДИНУ
    # Перевіряє timing-вікна: morning (6-10h) або after-work (17-20h)
```

### 3. **Rate-Limit для Phase 1 Alerts**
```python
# bot.py, polling loop
_PHASE1_ALERTS_LAST_CHECK = 0.0
_PHASE1_ALERTS_INTERVAL = 60.0  # Перевіркa max 1x per 60 seconds

if now - _PHASE1_ALERTS_LAST_CHECK > 60:
    _PHASE1_ALERTS_LAST_CHECK = now
    _handle_event_based_alerts()  # Викликаємо алерти раз на хвилину
```

### 4. **Rate-Limit для Phase 2 Recommendations**
```python
_PHASE2_RECS_LAST_CHECK = 0.0
_PHASE2_RECS_INTERVAL = 120.0  # Перевірка max 1x per 120 seconds
```

## Очікуваний результат

### Проактивні повідомлення
- **До**: 2-3 сповіщення на годину (часто дублювалися)
- **Після**: ~1 сповіщення на годину (гарантовано, пережирує рестарти)

### Event-Based Alerts (крипто, email, календар)
- **До**: Перевівалися 10+ разів на секунду (надмірне API використання)
- **Після**: Перевівалися 1 раз на 60 секунд

### Phase 2 Recommendations
- **До**: Обчислювалися часто (велика Gemini API квота)
- **Після**: Обчислювалися 1 раз на 120 секунд

## Деплой на Railway

```bash
git commit -m "FIX: Frequent notifications..."
git push origin main
# Railway автоматично перебудовує сервіс ac269393
```

## Тестування (для Олега)

1. **Проактивні повідомлення**: Повинна отримати повідомлення один раз на годину (максимум)
2. **Крипто-алерти**: Якщо BTC/ETH ±5% — отримаєте ОДИН alert, потім молчатиме 24 години
3. **Email-алерти**: Новий лист → alert, але деdup per day
4. **Логи**: Railway покажуть `[PHASE1] Alerts checked (next check in 60s)`

## Files Changed
- `bot.py`: +rate-limit vars, +global check before Phase 1/2
- `intelligent_assistant_v2.py`: +should_send_proactive_message() переробка, +file-based dedup
- `test_proactive.py`: +test scenarios (можна видалити після тестування)
