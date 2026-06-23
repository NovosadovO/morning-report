# 🔧 TELEGRAM CREDENTIALS SETUP GUIDE

## 🚨 PROBLEM
Bot `/звіт` command returns no response (мовчит). Root cause: **TELEGRAM_TOKEN or TELEGRAM_CHAT_ID incorrect or missing on Railway**.

## 🔍 DIAGNOSIS
Run `/diag` command in Telegram bot chat. You should see:
```
✅ TELEGRAM_TOKEN: 8374312425:AAFcSmsGfPacUVqNvSwrFe6McLeWBbCVWZ0...
✅ TELEGRAM_CHAT_ID: 2100366814
```

If you see:
```
❌ TELEGRAM_TOKEN: НЕМАЄ
❌ TELEGRAM_CHAT_ID: НЕМАЄ
```
Then the env vars are NOT set on Railway.

If they show wrong values, they're incorrect.

## ✅ SOLUTION

### Step 1: Get your correct chat ID
Your chat ID = **2100366814**

### Step 2: Get the correct Telegram token
Your current token (ACTIVE, not expired) = **8374312425:AAFcSmsGfPacUVqNvSwrFe6McLeWBbCVWZ0**
(Issued: 2026-06-18 via /revoke)

### Step 3: Set on Railway
Go to Railway dashboard → Project "vigilant-bravery" → Service "resourceful-alignment" → Variables

Set these two environment variables:
```
TELEGRAM_TOKEN = 8374312425:AAFcSmsGfPacUVqNvSwrFe6McLeWBbCVWZ0
TELEGRAM_CHAT_ID = 2100366814
```

**Important:** Copy exactly, including colons and the full string.

### Step 4: Redeploy
- Save the variables
- Railway will auto-redeploy
- Wait 2-3 minutes for deployment to complete
- Then test: send `/диаг` in Telegram bot chat

### Step 5: Verify
When /diag shows ✅ for both TELEGRAM_TOKEN and TELEGRAM_CHAT_ID, then test:
```
/звіт
```

You should see the report (might take 10-20 seconds).

## 🐛 If still not working

1. **Check Railway logs:**
   - Go to Railway → service → Deployments → latest → Logs
   - Search for: `[/звіт]` or `[tg_chunk]`
   - If you see `HTTP 404` → token/chat_id wrong
   - If you see `HTTP 401` → token expired (need new one from @BotFather /revoke)

2. **Check if bot responds to ANY command:**
   - Send `/ціни` or `/погода`
   - If no response to ANY command → Telegram connection broken (Railway/token issue)
   - If /ціни works but /звіт doesn't → specific issue with monitor.py

3. **Verify token is not expired:**
   - Go to @BotFather → /mybots → select your bot
   - Check "API Token" — should match what's on Railway
   - If needed, click "Edit Bot" → "API Token" → /revoke (old) and get new one

## 📞 Still stuck?
If /diag still shows ❌ after setting variables:
1. Force refresh Railway: go to service → Settings → Restart
2. Wait 5 minutes
3. Try `/diag` again

If /diag shows ✅ but /звіт still silent:
- Check Railway logs for `[/звіт]` → `[send_telegram]` → `[tg_chunk]` messages
- If logs show `404` or `401` errors → token/chat mismatch
