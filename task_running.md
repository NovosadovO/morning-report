# Біг-модуль — план

## Що є зараз
- strava.py: get_last_activity, get_week_stats, format_strava_block
- monitor.py: check_strava_new_activity (сповіщення після пробіжки з AI + тиждень)
- monitor_loop.py: run_strava_watcher (кожні 10 хв)

## Що треба додати

### strava.py — нові функції
1. get_activities(days=30) — список активностей за N днів
2. get_month_stats(year, month) — повна статистика за місяць
3. get_year_stats(year) — річна статистика
4. get_all_runs_for_chart(days=365) — дані для графіку (date, km, pace_sec)
5. compare_weeks() — цей тиждень vs минулий (км, темп, кількість)
6. compare_months() — цей місяць vs попередній

### strava_charts.py — новий файл
1. plot_month_chart(year, month) → bytes PNG
   - Bar chart: км по днях місяця
   - Line: темп
2. plot_year_chart(year) → bytes PNG  
   - Bar: км по тижнях/місяцях
   - Cumulative line

### monitor.py — доповнення
1. check_strava_new_activity — розширити:
   - додати порівняння з попередньою пробіжкою (краще/гірше)
   - каденс, потужність якщо є
   - тижневий прогрес vs минулий тиждень
2. Щоденний звіт — вже є format_strava_block, розширити:
   - тиждень vs минулий тиждень
   - топ-3 пробіжки місяця
3. check_strava_weekly_report() — нова функція, по неділях
   - повний аналіз тижня + графік
4. check_strava_monthly_report() — нова функція, 1-го числа
   - повний аналіз місяця + графік

### bot.py — нові команди
- /біг — детальна статистика + графік за місяць
- /біг тиждень — порівняння тижнів
- /біг місяць — місячний звіт + графік
- /біг рік — річний звіт + графік

### monitor_loop.py
- run_strava_charts_loop() — кожні 12 год відправляє графік

## Порядок виконання
1. strava.py — нові функції
2. strava_charts.py — графіки
3. monitor.py — розширити сповіщення + weekly/monthly
4. bot.py — команди
5. monitor_loop.py — charts loop
6. Тест + push
