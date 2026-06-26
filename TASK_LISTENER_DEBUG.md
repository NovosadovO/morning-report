# Task: Debug Intelligent Listener - Why AI Messages Not Sending

## PROBLEM
- Bot deployed successfully with listener + scheduler running
- /звіт command works (sends report)
- But intelligent_listener.py detects triggers but messages don't send
- No proactive AI messages like "Привіт Олег, ось wichtige новини..." appearing

## ROOT CAUSE HYPOTHESIS
1. **Credentials missing on Railway** — GEMINI_API_KEY or TELEGRAM_TOKEN/CHAT_ID not set correctly
2. **Triggers not being detected** — _check_vip_emails(), _check_crypto_moves() returning empty
3. **Gemini API failing** — _should_send_message() hitting 429 or timeout
4. **send_to_telegram() failing silently** — HTTP 401/403 not being logged

## FIXES APPLIED
✅ Added env var checks in process_trigger() 
✅ Added try/except wrapper in listener.run() for process_and_send_trigger()
✅ Added exception logging in intelligent_listener.py

## NEXT STEPS
1. Need to test /schedule_test command — should force 4 AI messages immediately
2. Check Railway logs for "[MESSAGE_GEN" or "[LISTENER]" errors
3. Verify GEMINI_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID on Railway
4. If credentials OK — add more debug logging to trigger detection functions

## FILES MODIFIED
- message_generator.py: process_trigger() now has env var checks + try/except
- intelligent_listener.py: Added exception handling in run() loop

## GIT STATUS
Need to commit and push these changes to Railway
