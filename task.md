# TASK: Fix duplicate notifications + remove AI spam

## ПРОБЛЕМИ (з скрінів)
1. "Привіт, Олеже! Скоро вже нічна о 17:00..." × 2 о 15:02
   - Причина: proactive.py morning_greet АБО check_calendar_reminders (1г до 16:00 = 15:00)
   - check_shift_reminders вже вимкнений в monitor_loop.py

2. "Погода на дорогу до роботи (17:00)" × 2 о 15:30
   - Причина: check_pre_shift_weather (15:30) шле "Погода"
   - ПЛЮС ще щось шле таке саме — check_calendar_live? (через 15 хв: нічна о 17:00 = шле о 16:45 — не 15:30)
   - Перевірити: двічі одне й те саме, обидва о 15:30 → значить check_pre_shift_weather викликається двічі?
   - АБО є ще одна функція що шле погоду

3. Часті звіти (годинні) — monitor.py кожні 20 хв + report2.py кожні 3г
   - Годинний звіт кожні 3г — це норма, але можна зменшити

## ВИКОНАНІ ЗМІНИ
- [x] check_pre_shift_weather: прибрано нічну (15:30) — залишено тільки ранню (03:30)
- [x] check_pre_shift_weather: shift_hour = 5 (тільки рання)

## TODO
- [ ] Знайти де двічі шлеться "Погода на дорогу" о 15:30
- [ ] Вимкнути proactive morning_greet (дублює morning_brief о 07:00)
- [ ] Перевірити чи check_morning_brief активний (run_morning_brief_watcher DISABLED)
- [ ] Налаштувати частоту годинного звіту (рідше)
- [ ] git commit + push

## НОВІ ПРАВИЛА
- Перед нічною: ОДНЕ повідомлення — pre_night о 16:30 (check_smart_notifications) + 1г calendar reminder о ~16:00
- Погода для нічної — вже в pre_night prompt
- Ранкових повідомлень: тільки check_morning_brief (07:00) АБО check_morning_context (08:30), не обидва
