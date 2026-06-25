# Task: Add Steps + Sleep Data to Report + AI Analysis

## OBJECTIVE
Integrate daily steps and sleep hours data into:
1. Main AI prompt context (_ai_real_ctx)
2. Themes AI analysis (_th_ctx dict)
3. Email AI analysis (pass context to function)
4. Astro AI analysis (shift_hint)

## COMPLETED
✅ 1. Extract steps/sleep from load_health() after weight_hint (line ~8915)
   - Created steps_val, sleep_val variables
   - Created steps_hint, sleep_hint formatted strings
   
✅ 2. Add to _ai_real_ctx (line ~8975)
   - Appended steps_hint + sleep_hint to context

✅ 3. Add to themes_ai context (line ~9600)
   - Modified health field to include steps and sleep

✅ 4. Add health context to email_ai function call (line ~9551)
   - Built _health_ctx_email string
   - Passed to _get_email_ai_analysis_for_report()

✅ 5. Modified _get_email_ai_analysis_for_report() signature
   - Added health_context param
   - Passed to _gemini_email_analysis()

✅ 6. Modified _gemini_email_analysis() signature
   - Added health_context param
   - Included in prompt as health note

✅ 7. Update astro_ai context
   - Added health context to shift_hint before astro call
   - Formats as: "Здоров'я: кроки X, сон Yг"

✅ 8. Local syntax tests
   - py_compile OK for monitor.py and bot.py
   - AST parse OK
   - Storage imports OK

## READY FOR DEPLOYMENT
- [ ] 9. Push to GitHub
- [ ] 10. Railway auto-redeploy

## NOTES
- steps_val, sleep_val variables already exist in scope at line ~8920
- _th_ctx["health"] updated with format: "Вага: X. Кроки: Y. Сон: Zг."
- email_ai function is at line 1758, called at line 9543
- astro_ai function is at line 3200+ with shift_hint variable
