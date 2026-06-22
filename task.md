# Задача: Fix /звіт Silent Failure

## Проблема
- `/звіт` команда отправляет "⏳ Збираю звіт..." но никогда не отправляет сам звіт
- Monitor.py запускается (нет ошибок в try-catch), но send_telegram() не работает или молчит

## Root Cause Hypothesis
1. **send_telegram() вызывается успешно в monitor.main()** (р.4897+ основной звіт)
2. **Но** вероятно:
   - send_telegram() возвращает False → ok=False → звіт считается неудачным
   - Или timeout при отправке parts (каждый chunk с timeout 10s)
   - Или HTML/encoding ошибка в _send_telegram_chunk()

## Решение
✅ Добавлены DEBUG логи:
   - bot.py р.1858-1875: логирование load monitor, TELEGRAM_TOKEN доступность, начало/конец main()
   - send_telegram() р.381: логирование количества parts, результат каждого chunk
   - _send_parts_as_one() р.4897+: логирование перед/после отправки part1/part2
   - photo/email отправка р.4907+: явное логирование ok флага

## Проверки выполнены
✅ monitor.py синтаксически OK
✅ bot.py синтаксически OK
✅ send_telegram() определена на р.381 в monitor.py
✅ Все импорты на месте (os, json, urllib)
✅ Добавлено ПОДРОБНОЕ логирование для диагностики

## Следующие шаги
1. Запушить на GitHub (commit: "DEBUG: extensive logging for /звіт diagnosis")
2. Railway redeploy и тест /звіт
3. Проверить логи на Railway с [send_telegram], [tg_chunk], [report] префиксами
4. Если ok=False где-то → диагностировать почему send_telegram() вернула False
