# Intelligent Assistant v2.0 — Event-Driven Architecture

## 🎯 Мета

Замість жорсткого scheduler (6am/12pm/3pm/8pm) → **AI САМ вирішує коли писати** на основі:
1. **Тригерів** (VIP-лист, крипто-рух, event, нема активності)
2. **Контексту** (де Олег — дома чи на роботі, час дня)
3. **Інтелекту** (Gemini аналізує чи ДІЙСНО потребує написати)

## 📊 Архітектура

```
┌─────────────────────────────────────────────────────────┐
│ Event Listener (постійно крутиться)                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  1. Gmail polling (кожні 2 хвилини)                    │
│     ├─ Нові листи? ДА → VIP?  ДА → ТРИГЕР 🔴           │
│     └─ Нема → skip                                      │
│                                                         │
│  2. Calendar check (кожні 5 хвилин)                    │
│     ├─ Event за 1-2 години? ДА → ТРИГЕР ⏰             │
│     ├─ Дати зміни (Minebea)? ДА → обновити location   │
│     └─ Нема → skip                                      │
│                                                         │
│  3. Crypto prices (кожні 5 хвилин)                     │
│     ├─ BTC/ETH/AVAX/ONDO ±5% за 1h? ДА → ТРИГЕР 📈    │
│     └─ Нема → skip                                      │
│                                                         │
│  4. Activity timeout (кожні 1 хвилина)                 │
│     ├─ Нема повідомлень від юзера 2+ години?         │
│     │  ДА → Олег неактивний → ТРИГЕР 💪                │
│     └─ Нема → skip                                      │
│                                                         │
│  5. Time-based (кожні хвилину)                         │
│     ├─ Час 6:00-7:00 AM? ДА → ТРИГЕР 🌅 (Ранок)       │
│     ├─ Час 20:00-21:00? ДА → ТРИГЕР 🌙 (Вечір)        │
│     └─ Нема → skip                                      │
│                                                         │
│  6. Health update (коли юзер надсилає дані)            │
│     ├─ Нові steps/weight/sleep? ДА → ТРИГЕР 📊         │
│     └─ Нема → skip                                      │
│                                                         │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
        ┌─────────────────────────────────────┐
        │ Trigger Aggregator                  │
        ├─────────────────────────────────────┤
        │ Збирає ВСІ тригери за останні 5хв   │
        │ (max 1 message per trigger type)     │
        │ Вибирає НАЙВАЖЛИВІШИЙ                │
        └─────────────────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────────┐
        │ AI Decision Engine (Gemini)          │
        ├──────────────────────────────────────┤
        │ "Чи реально потребує цей тригер     │
        │  написати зараз? Чи це spam?"        │
        │                                      │
        │ Input:                               │
        │  - Тригер (VIP-лист / крипто / ...)  │
        │  - Контекст (де Олег, скільки часу)  │
        │  - Історія (коли останній message)   │
        │                                      │
        │ Output: ДА/НІ + text якщо ДА         │
        └──────────────────────────────────────┘
                          │
                          ▼ (if YES)
        ┌──────────────────────────────────────┐
        │ Message Generator (Gemini)           │
        ├──────────────────────────────────────┤
        │ Генерує 300-400 слів message        │
        │ з контекстом (дані, рекомендації)    │
        └──────────────────────────────────────┘
                          │
                          ▼
        ┌──────────────────────────────────────┐
        │ Telegram Send + Dedup                │
        ├──────────────────────────────────────┤
        │ Надсилає message                     │
        │ Записує в data/ai_messages.json      │
        │ (timestamp, trigger_type, text)      │
        └──────────────────────────────────────┘
```

## 🔧 Нові файли / Модифікації

### NEW: `intelligent_listener.py` (600 рядків)
```python
class IntelligentListener:
    """Event listener — постійно перевіряє тригери"""
    
    def __init__(self):
        self.last_email_check = 0
        self.last_calendar_check = 0
        self.last_crypto_check = 0
        self.last_user_activity = time.time()
        self.user_location = "doma"  # or "robota"
        
    def run(self):
        """Main loop: вiкає кожну секунду, перевіряє тригери"""
        while True:
            try:
                triggers = self.check_all_triggers()
                if triggers:
                    self.handle_triggers(triggers)
            except Exception as e:
                log(f"Listener error: {e}")
            time.sleep(1)
    
    def check_all_triggers(self) -> list:
        """Повертає список активних тригерів за останні 5 хвилин"""
        triggers = []
        now = time.time()
        
        # 1. EMAIL
        if now - self.last_email_check > 120:  # Кожні 2 хвилини
            new_vip_emails = self._check_new_vip_emails()
            if new_vip_emails:
                triggers.append(("vip_email", new_vip_emails))
            self.last_email_check = now
        
        # 2. CALENDAR
        if now - self.last_calendar_check > 300:  # Кожні 5 хвилин
            upcoming_events = self._check_upcoming_events()
            if upcoming_events:
                triggers.append(("event_soon", upcoming_events))
            self._update_location_from_calendar()
            self.last_calendar_check = now
        
        # 3. CRYPTO
        if now - self.last_crypto_check > 300:  # Кожні 5 хвилин
            crypto_moves = self._check_crypto_moves()
            if crypto_moves:
                triggers.append(("crypto_move", crypto_moves))
            self.last_crypto_check = now
        
        # 4. ACTIVITY TIMEOUT
        idle_hours = (now - self.last_user_activity) / 3600
        if idle_hours > 2:
            triggers.append(("idle_timeout", idle_hours))
        
        # 5. TIME-BASED
        current_hour = datetime.now(TZ).hour
        if 6 <= current_hour < 7:
            triggers.append(("morning", None))
        elif 20 <= current_hour < 21:
            triggers.append(("evening", None))
        
        # 6. HEALTH
        # (коли юзер надсилає /health дані)
        
        return triggers
    
    def _check_new_vip_emails(self) -> list:
        """Отримати нові листи від VIP за останні 2 хв"""
        # ...IMAP code...
        pass
    
    def _check_upcoming_events(self) -> list:
        """Отримати события за 1-2 години"""
        # ...Google Calendar API...
        pass
    
    def _check_crypto_moves(self) -> dict:
        """Перевірити BTC/ETH/AVAX/ONDO за 1 годину"""
        # ...CoinGecko...
        pass
    
    def _update_location_from_calendar(self):
        """Прочитати Google Calendar, дізнатися де Олег"""
        # Якщо "Minebea" чи "зміна" у события → "robota"
        # Якщо "вихідний" → "doma"
        # Fallback: time-based (06:00-18:00/18:00-06:00)
        pass
    
    def handle_triggers(self, triggers: list):
        """Обробити список тригерів"""
        # Вибрати найважливіший
        # Запитати AI "чи писати?"
        # Якщо ДА → генерувати message + надсилати
        pass

def mark_user_active():
    """Позначити що юзер активний (викликається при /звіт, /листи, etc)"""
    listener.last_user_activity = time.time()
```

### MODIFY: `bot.py`
- Додати `mark_user_active()` виклик при КОЖНІЙ команді юзера
- На startup: запустити `IntelligentListener` в окремому thread (daemon)
- Додати `/location_test` команду для тесту GPS
- Додати `/triggers_status` для перегляду активних тригерів

### MODIFY: `smart_notifications_v3.py`
- Переробити функції щоб приймали `trigger_data` (VIP-листи, крипто-дані, etc)
- Додати `should_send_now(trigger_type, last_message_time)` функцію (AI вирішує чи писати)
- Додати `generate_message_for_trigger(trigger_type, trigger_data, location, current_hour)` (генерує text)

### NEW: `data/ai_messages.json`
```json
{
  "2026-06-26T06:30:15": {
    "trigger": "vip_email",
    "from": "boss@minebea.com",
    "subject": "Important: Project deadline",
    "location": "doma",
    "message_len": 342,
    "gemini_model": "gemini-2.5-flash"
  },
  "2026-06-26T10:45:22": {
    "trigger": "crypto_move",
    "move": "BTC +5.2% за 1h",
    "location": "robota",
    "message_len": 256
  }
}
```

### NEW: `data/location_state.json`
```json
{
  "current_location": "doma",
  "last_update": "2026-06-26T10:00:00",
  "reason": "calendar: вихідний",
  "next_event": {
    "name": "Minebea (ранна зміна)",
    "start": "2026-06-27T06:00:00"
  }
}
```

## 🔐 Dedup Логіка

```
Максимум 1 message per тригер-тип per час:
- vip_email: 1/hour
- crypto_move: 1/hour  
- event_soon: 1/2hours
- idle_timeout: 1/2hours
- morning: 1/day
- evening: 1/day
- health: 1/hour
```

## 🧪 Тестування

### Локально:
```bash
python3 intelligent_listener.py
# Output:
# [06:25] checking emails... found 3 new, 1 VIP from InterFin
# [06:25] trigger: vip_email
# [06:25] asking AI "should send?"
# [06:25] AI: YES, important investment discussion
# [06:25] generating message...
# [06:25] message sent (342 chars)
```

### На Railway:
- Запустити listener на startup
- Перевірити логи: `[LISTENER] Worker started`
- Чекати тригерів у логах
- Якщо тригер спрацював → AI запросили → message надіслалась

## 📝 Статус

- [ ] Написати `intelligent_listener.py` (600 rядків)
- [ ] Modifikувати `bot.py` (додати mark_user_active + startup)
- [ ] Переробити `smart_notifications_v3.py` (should_send + generate)
- [ ] Локальний тест
- [ ] Railway redeploy
- [ ] Тест на живому (вичікування тригерів)

## ⏱️ Очікуваний час

- Кодування: 2-3 години
- Тестування локально: 30 хвилин
- Railway deploy + live test: 30 хвилин

**Всього: ~3-4 години**

---

**Next:** Почнемо з `intelligent_listener.py`?
