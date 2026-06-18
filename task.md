# Bot 2.0 — задача активна

## DONE
- timedelta UnboundLocalError у get_summary — fixed (commit a21c65930d)
- email_text dict→str краш — fixed (17dcee8cce)
- 429 model-fallback у _gem_post: 2.5-flash→2.0-flash→2.5-flash-lite (96580d44b1)
- 429 миттєвий switch при retryDelay>25s, макс 18s/модель (88fe2cf45f) deploy 0c184d7e
- _GEM_MIN_GAP 7→9s

## ПЕРЕВІРИТИ
- deploy 0c184d7e SUCCESS?
- /звіт у логах: всі AI-блоки OK (можливо via FALLBACK), без "skipping block"
- get_summary без error

## ВІДКЛАДЕНО
- get_summary _gemini_summarize (email) НЕ юзає _gem_post fallback — окремий 429 шлях (некритично, email-summary)
- ГРАФІКИ: збільшити, дашборд звичок, емодзі→текст, налазящі підписи
- Старий інстанс десь в ІНШОМУ Railway-проєкті (project-token не бачить). Юзер каже "тільки Railway" → треба щоб ВІН зайшов у railway.app і видалив старий проєкт/сервіс. Поки fallback робить це безпечним.

## ENV
- dir /home/user/morning-report, service ac269393, env 0f1480b0, proj 1c4de079
- /tmp/deploy.py /tmp/getdep.py /tmp/logs2.py <id> <n> /tmp/railway.env
- TG token 8374312425:AAH..., chat 2100366814
- GEMINI key AQ.Ab8... (rate-limited бо старий інстанс палить)
