# BOT CALLBACK SAVE DEBUG — Session 2026-06-23

## ISSUE
Олег каже: "При натискані Ні/Так бот не записує" — callback buttons (Так/Ні) не зберігають дані

## ROOT CAUSE FOUND
1. `handle_meds_callback()` в bot.py (лінія 243-248): 
   - Зберігає через `storage.save_meds()` 
   - Якщо 3 спроби в `_save_github()` впаду → функція просто логує "gave up" і ігнорує
   - Юзер не бачить помилку → здається що дані не записались

2. `handle_habit_callback()` в bot.py:
   - Зберігає через `habits.save_data()` → `storage.save_habits()` → `_save_github()`
   - Та ж сама проблема

## FIXES APPLIED

### 1. ✅ bot.py (handle_meds_callback) 
- Додав детальне логування: ✅ SAVED, ❌ FAILED
- Якщо save повертає False → пробуємо /tmp fallback
- Якщо обидва впаду → відправляємо помилку юзеру в Telegram

### 2. ✅ habits.py (save_data)
- Додав check повертає ли save_habits() True
- Якщо False → логуємо & зберігаємо в /tmp fallback
- Більш інформативні логи: ✅ [habits] SAVED

### 3. 🔄 storage.py (_save_github)
- ПОТРЕБУЄ: додати retry на network errors (не тільки 409)
- ПОТРЕБУЄ: логувати точну помилку від GitHub API (_gh_request)

## NEXT STEPS
1. Прочитати `_gh_request()` — яку помилку вона повертає при network error
2. Додати retry logic на network failures
3. Коміт & push
4. Тест: натиснути Так/Ні → перевірити Railway логи на ✅/❌ tags

## FILES MODIFIED
- bot.py: handle_meds_callback (строка ~240)
- habits.py: save_data (строка ~200)
- storage.py: PLANNING

## COMMITS
- 29186df480: Media group (all charts in one)
- NEXT: "FIX: Improve callback save error handling + GitHub API retry logic"
