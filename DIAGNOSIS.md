# /звіт Silent Failure — Diagnosis Guide

## Problem
User sends `/звіт` command → bot sends "⏳ Збираю звіт..." → but no actual report arrives.

## What We Fixed
Added **extensive logging** to track exactly where report sending fails:

### 1. **bot.py** (handler for /звіт)
```
[/звіт] Loading monitor from ...
[/звіт] Monitor loaded, TELEGRAM_TOKEN available: True
[/звіт] Monitor loaded, TELEGRAM_CHAT available: True
[/звіт] Starting mod.main() with _FORCE_REPORT=True...
[/звіт] mod.main() completed
```

### 2. **monitor.py** → send_telegram()
```
[send_telegram] single chunk returned True  (if text <= 4090 chars)
[send_telegram] split into 3 parts           (if text > 4090 chars)
[send_telegram] part 1/3 returned True
[send_telegram] part 2/3 returned False      ← PROBLEM HERE if False
[send_telegram] part 3/3 returned True
[send_telegram] final result: False
```

### 3. **monitor.py** → _send_telegram_chunk()
```
[tg_chunk] len=2500 preview="ваш текст..."
[tg_chunk] HTML error: 400 Bad Request      (if Telegram rejects)
[tg_chunk] plain fallback OK                (recovery attempt)
```

### 4. **monitor.py** → main() report assembly
```
[report] ========== STARTING REPORT SEND ==========
[report] sending part 1 (12 sections)
[report] part 1 done, ok=True
[report] sending part 2 (8 sections)
[report] part 2 done, ok=True
[report] sending album (3 photos)
[report] album done, ok=True
[report] no email parts to send
=== Report sent ===
```

## How to Get Logs on Railway

1. **Go to Railway dashboard**: https://railway.app
2. **Select project**: vigilant-bravery
3. **Select service**: resourceful-alignment (ac269393)
4. **Go to Deployments tab**
5. **Latest deployment** → click to see logs
6. **Search for**: [send_telegram], [tg_chunk], [report], [/звіт]

## What to Look For

### Success Case
```
[/звіт] Loading monitor...
[/звіт] Monitor loaded, TELEGRAM_TOKEN available: True
[send_telegram] single chunk returned True
[report] sending part 1...
[send_telegram] final result: True
[/звіт] mod.main() completed
```

### Failure Cases

**Case A: send_telegram returns False**
```
[send_telegram] part 2/3 returned False
[send_telegram] final result: False
=== Report FAILED ===
```
→ Problem: Telegram API error, timeout, or network issue

**Case B: Timeout**
```
[report] sending part 1 (12 sections)
[tg_chunk] timeout: timed out
```
→ Problem: Telegram API slow, network latency

**Case C: HTML error**
```
[tg_chunk] HTML error: 400 Bad Request
[tg_chunk] TEXT START: "<b>Звіт за ...
```
→ Problem: Invalid HTML in report text, fallback should help

**Case D: Command never completes**
```
[/звіт] Loading monitor...
[/звіт] Monitor loaded, TELEGRAM_TOKEN available: True
[/звіт] Starting mod.main() with _FORCE_REPORT=True...
(no more logs)
```
→ Problem: main() hung somewhere, need to check what AI block or data fetch is timing out

## Report This Back

When you test, provide:
1. **Time** when you sent /звіт (in your timezone)
2. **Full log** from Railway with all [send_telegram], [tg_chunk], [report], [/звіт] lines
3. **What you saw in Telegram** (just "⏳..." or nothing?)

---

**Deployment updated**: commit c1801aae43  
**Status**: Awaiting Railway redeploy and user test
