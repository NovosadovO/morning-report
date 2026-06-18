# Годинний автозвіт — FIX

## ROOT CAUSE (знайдено 2026-06-18)
Звіт claim-нув слот ПЕРЕД відправкою (last_slot+code_version на GitHub гілку `data`).
Якщо інстанс рестартиться між claim і send → слот "зайнятий" своєю ж version →
наступний інстанс бачить `Already sent ... v>=me → skipping` і НЕ шле звіт.
Лог: `19:00:33 === Already sent this slot (2026-06-18T21:00) by v202606182 >= me v202606182, skipping ===`

ВАЖЛИВО: dedup-файл на гілці `data` (?ref=data), НЕ main.

## FIX (dedup v3)
- `sent_slot` пишеться ТІЛЬКИ ПІСЛЯ успішної відправки (ok=True), після `=== Report sent ===`.
- Перед send — лише `lock_slot`+`lock_at` з TTL 600s (захист від паралелі).
- Якщо ok=False або краш — sent_slot НЕ пишеться → наступний запуск пере-надсилає.
- GitHub-стан скинуто на чисто.
- commit pushed, deploy 5b358826.

## TODO
- [ ] Дочекатись deploy 5b358826 SUCCESS
- [ ] Дочекатись 20:00 UTC (22:00 локал) → перевірити лог: Running INLINE → Locked slot → Report sent → Marked slot SENT
- [ ] Підтвердити юзеру що звіт прийшов

## DONE
- check_shopping_reminder NameError fix (send_message → _send_telegram_text_with_keyboard)

## ENV
- deploy active: чекаю 5b358826
- service ac269393, env 0f1480b0, proj 1c4de079
- TG token 8374312425:AAFcSmsGfPacUVqNvSwrFe6McLeWBbCVWZ0, chat 2100366814
- scripts /tmp/deploy.py /tmp/getdep.py /tmp/logs2.py, source /tmp/railway.env
