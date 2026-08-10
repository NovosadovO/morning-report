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

## feedback_ctx (2026-08-04, деплой 120c4cf7 SUCCESS)
- НОВИЙ feedback_ctx.py: build(days)/stats/muted_topics — агрегує gx_ack + calendar_ack + response_log + ai_notes у блок "📌 ЗВОРОТНИЙ ЗВ'ЯЗОК ОЛЕГА" для промптів. Кеш 180с, ніколи не падає, порожньо → "".
- Підключено: context.py (чат), message_generator.py (проактивні, +правило 8), calendar_watch._ai_note/_ai_digest через _fb_block().
- ai_buttons._ack + calendar_watch._ack тепер пишуть у response_log (категорії ai_button / calendar_button) → єдиний лог.
- bot.py: команда /пам_ять_аі (/ai_memory, /що_знає_аі) — показує сирий блок + статистику. Webhook 200, "Message: /пам_ять_аі" у логах.
- tests_feedback_ctx.py (=/tmp/fb_test.py): 15 перевірок, ❌=0. Решта 4 набори теж ❌=0. lint311 bad:0.

## Верифікація 04.08 (деплой b6d2664a SUCCESS, 0 помилок у логах)
Перевірено НЕ тестами, а реальними callback через webhook. Знайдено і виправлено 4 справжні баги:
1. `_ask_note` → NameError: TELEGRAM_CHAT_ID (константа зветься TELEGRAM_CHAT) — кнопка нотатки НЕ питала текст, тихо падала у fallback. Тепер приймає chat_id.
2. Автонотатка-fallback зберігала все тіло сповіщення ("Привіт Олеже! 👋...") → нове _auto_note(): без вітання, [тема] + 160 симв.
3. calendar_watch.do_note писав "Подія: None (None)" при payload без title.
4. feedback_ctx тягнув це сміття в промпт → _is_junk() + _note_of() + _useful_resp() (сирі callback_data з response_log відсіяно).
Почищено прод: ai_notes 9 → 4 записи (видалено 5 сміттєвих).
E2E підтверджено в логах: [CB] gx_note_ → force-reply без помилки → "перевірка нотатки: тримати BTC до 120k" → [ai_notes] added (gx_email).
Тести: fb_test, note_test, cw_test, cw_week_test, cw_ai_test, cw_month_test — усі ❌=0. lint311 bad:0.
Нове в репо: tests_feedback_ctx.py, tests_notes_regression.py.

## Confirm-шар (двокрокове підтвердження) — 08.08.2026
- `confirm.py`: register/ask/yes/no/report/gc, TTL 24 год, префікси `cfm_y_`/`cfm_n_`, store `confirm_store.json`, лог `confirm_log.json`.
- Деструктивні кнопки `🚫 Скасовано` (`cw_cancel_`) і `🔇 Не нагадувати` (`gx_mute_`) НЕ виконують дію одразу — лише питають.
- Постійний блок нагадувань: `calendar_blocked.json` (НЕ чиститься `gc_sent`), `tick()` і `_fire_snoozed()` його поважають.
- Команди: `/вимкнені_нагадування`, `/увімкни_нагадування`, `/підтвердження`.
- Перевірено в прод (deploy f6476bba, SUCCESS): cw_cancel → питання → cfm_n (нічого) → cfm_y → запис у calendar_blocked; gx_mute → cfm_y → запис у gx_mute; `/увімкни_нагадування` → "повернуто нагадувань: 1". Traceback/NameError у логах немає.
- Тести: tests_confirm.py (34 перевірки, ❌=0) + fb/note/cw/cw_week/cw_ai/cw_month — усі 0. Лінт 3.11 bad: 0.
- ⚠️ Відкрито: Strava API 403 Forbidden на всіх запитах (get_activities / get_last_activity / get_week_stats) — потрібен новий refresh_token або перевірка підписки.

## Оновлення 09.08.2026
- Підтвердження додано ще під 2 кнопки: `❌ Не було` (cw_miss) і `🔔 Нагадай пізніше` (gx_later). Перевірено в проді: питання → cfm_y → запис `missed` у calendar_ack; cfm_n → нічого.
- `confirm.register()` має поле `revert` — після «Так» бот пише, як повернути назад.
- Огляд «місяць вперед» тепер ЩОТИЖНЯ: понеділок 12:00–17:00 (`_monthly_tag()` = рік+ISO-тиждень). Огляд тижня лишився пн 06:00–11:00.
- Strava 403: причина НЕ токен, а `Application/Inactive` (застосунок 228739 деактивовано Strava). Додано `api_blocked()`, `app_inactive_reason()`, `_note_api_error()` — пауза 30 хв замість спаму, зрозумілий рядок у /diag, дані з кешу.

## storage merge-on-conflict — 10.08.2026
- КОРІНЬ «мертвих кнопок»: `_save_github` при 409 (інший потік записав файл між нашим GET і PUT) перезаписував файл своїм знімком → ключі іншого потоку зникали (втрачені відповіді на кнопки, драфти, підтвердження).
- FIX: на повторній спробі беремо свіжий remote як базу і накладаємо свої ключі поверх (`merged = remote | data`). Для не-dict (списки) поведінка без змін. Лог 409-retry притишений (пише лише з 3-ї спроби).
- Тест `tests_storage_merge.py` (фейковий GitHub, 409 на першому PUT): 6 перевірок, ❌=0 — чужий ключ не втрачається, свій записується, список зберігається.
- Деплой `01558676` SUCCESS: 0 Traceback, 0 GAVE UP, 0×409 у логах.
- `email_cancel_` тепер видаляє драфт зі сховища + пише «нічого не надіслано» (перевірено в проді: запис зник).
