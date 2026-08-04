# Кнопки під усіма сповіщеннями (запит Олега 04.08)

## Що робимо
1. Пояснити, що робить 📝 Нотатка (зараз зберігає автотекст, не питає введення).
2. Нотатка → питає ТВІЙ текст (force_reply + state awaiting_note).
3. Нові кнопки під календарними нагадуваннями (cw_*).
4. Універсальні кнопки під УСІМА темами (новий модуль ai_buttons.py, префікс gx_).

## Прогрес
- [x] ai_buttons.py створено (keyboard/detect_topic/do_more/do_note/do_later/do_mute/do_done/tick/pending/report/mute_status)
- [x] calendar_watch: нові кнопки + do_go/do_map/do_focus/do_run/do_next/do_day/do_refresh/do_bills/ask_note_text
- [x] bot.py: хендлери gx_*, cw_*, state awaiting_note, команди /кнопки /відкладені /приховані_теми /увімкни_теми
- [x] message_generator._send_to_telegram(text, topic, trigger_type) + mute-фільтр
- [x] intelligent_listener: ai_buttons.tick() + gc
- [x] тести (tests_ai_buttons.py, 0 помилок) + лінт bad:0 + деплой 9e7b69f2 SUCCESS

## Нотатки
- planner.set_state/get_state/clear_state; невідомі mode планер ігнорує → безпечно.
- charts.plot_crypto_trend / plot_health_2x2_dashboard / plot_habits_heatmap; strava_charts.plot_month_chart
- deadlines_watcher.upcoming(days), bills_watcher.monthly_report()
- лінт: python3 /tmp/lint311.py <files> → bad: 0
