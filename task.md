# TASK: Зробити бота живішим — більше ініціативи, графіки, емодзі

## АНАЛІЗ ПОТОЧНОГО СТАНУ
- 31 check_ функція в monitor.py
- Більшість повідомлень — прості текстові рядки
- AI tips лише в smart_notifications і morning_context
- Немає ASCII-графіків (вага, крипто, звички)
- Мало emoji-візуалізації прогресу
- Ранковий брифінг простий, без даних

## ПЛАН АПГРЕЙДУ

### 1. check_morning_brief / check_morning_context → MEGA MORNING BRIEF
- ASCII графік ваги (7 днів)
- Крипто зміни з mini-bar chart
- Статус звичок за вчора (✅/❌)
- AI персональна порада
- Емодзі-термометр погоди
- Статус зміни + час виходу

### 2. check_day_summary → RICH DAY SUMMARY
- Статистика дня: звички ✅/❌, вода, ліки
- Графік активності
- Мотиваційний AI підсумок з конкретикою

### 3. check_weekly_habit_stats → WEEKLY DASHBOARD
- Красивий ASCII дашборд звичок
- Streak (серії поспіль)
- Тренд ваги за тиждень

### 4. check_crypto_morning → CRYPTO DASHBOARD
- Красивий header з ринковим настроєм
- Mini bar chart % змін
- Fear & Greed індекс
- AI сигнал (тримати/слідкувати)

### 5. НОВІ ПРОАКТИВНІ ФУНКЦІЇ
- check_mood_evening: 21:00 питання про настрій дня
- check_step_goal: о 18:00 — скільки кроків, чи досяг цілі
- check_friday_recap: п'ятниця 20:00 — підсумок робочого тижня
- check_weight_trend: якщо вага росте 3+ дні — проактивний алерт

### 6. check_smart_notifications → РОЗШИРИТИ
- Більше ситуативних повідомлень
- Графік прогресу до цілі 78кг щоразу в smart tips

## ФАЙЛИ ДЛЯ ЗМІНИ
- monitor.py (основне)
- monitor_loop.py (реєстрація нових watchers)

## СТАТУС
- [ ] Upgrade check_morning_brief
- [ ] Upgrade check_day_summary  
- [ ] Upgrade check_weekly_habit_stats
- [ ] Upgrade check_crypto_morning
- [ ] Нові функції (check_mood_evening, check_step_goal, check_friday_recap)
- [ ] Upgrade smart_notifications AI tips
- [ ] Push + Railway redeploy
