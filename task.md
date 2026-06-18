# Bot Fix Status [2026-06-17]

## ВИРІШЕНО
- root cause "мовчав": f-string unmatched '(' monitor.py:9006 (вкладені " у f-string, Python 3.11) → Bot crashed loop. ВИПРАВЛЕНО (одинарні лапки).
- Railway mise-баг "secret ID missing for ''": причина = змінна " GEMINI_API_KEY" з ПРОБІЛОМ на початку імені. Новий сервіс resourceful-alignment створено без неї.
- 8 змінних перенесено в новий сервіс через GraphQL API (Project-Access-Token, inline mutation).
- FORCE_LEADER=1 на новому сервісі → відібрав lock у старого (c716e066) → "I am leader: 45ed4aae".
- libsqlite3-0 додано в railpack.json (aptPackages) для kerykeion.

## СЕРВІСИ (project vigilant-bravery, id 1c4de079-85e2-4ba0-ba2a-1fb502b48219)
- resourceful-alignment (ac269393-...) = НОВИЙ РОБОЧИЙ, FORCE_LEADER=1, Railpack, SUCCESS
- morning-report (89de62c3-...) = СТАРИЙ, всі деплої FAILED, треба видалити вручну (project token не може serviceDelete)

## TODO
- [ ] Користувач пише /diag → підтвердити GEMINI_API_KEY ✅ + Gemini API ✅ 200
- [ ] getUpdates timed out часто — перевірити чи команди реально обробляються (HTTP read timeout vs polling timeout)
- [ ] Видалити старий сервіс morning-report (user робить у UI: Settings → Delete Service)
- [ ] Прибрати FORCE_LEADER=1 після підтвердження (щоб не ламало майбутню leader-логіку) — АБО лишити бо сервіс один
- [ ] Перевірити GEMINI ключ: значення AQ.Ab8... (не AIza...) — можливо OAuth, не API key. /diag покаже Gemini API 200 чи ні.

## API доступ
Railway project token: f03f6f22-91f4-4ad6-b19b-89b013652804 (тільки read+variables+deploy, НЕ serviceDelete)
backboard.railway.com/graphql/v2 з header "Project-Access-Token"

## [2026-06-18 онов.] СТАТУС
- ✅ Тематичний AI-аналіз (7 сфер) — функція + контекст + надсилання ДОДАНО, запушено (commit ce0286d).
- ✅ FIX getUpdates timed out: HTTP read timeout тепер = polling+15с (bot.py api()), commit 86a48131. Деплой 77ed775b SUCCESS — "getUpdates timed out" ЗНИК у логах.
- ⏳ Перевіряю чи /звіт тригерить themes_ai+astro_ai (надіслав команду, чекаю логи [themes_ai] OK).
