# TASK: Fix 5 Broken Features

## User's Demands
1. ✅ Week planner in report
2. ✅ Dashboard image sent after report
3. ❓ Astro AI analysis
4. ❓ Health score charts
5. ❓ All features together

## Current Status

### What's WORKING:
- ✅ `monitor.py` syntax OK (py_compile passes)
- ✅ `dashboard.py` generates 108KB image locally
- ✅ `week_planner.py` returns text block (75 chars, with "календар недоступний" fallback)
- ✅ Week planner added to report (р. 4554-4559)
- ✅ Dashboard sendPhoto code exists (р. 5065-5079)

### What Might Be BROKEN:
- ❌ TELEGRAM_TOKEN on Railway (may be old/invalid)
- ❌ TELEGRAM_CHAT_ID on Railway (may be missing)
- ❌ Astro AI analysis (exists but complex)
- ❌ Health charts (code exists but data missing)

## PLAN

### Phase 1: Verify Railway Vars (CRITICAL)
1. Check if TELEGRAM_TOKEN = `8374312425:AAFcSmsGfPacUVqNvSwrFe6McLeWBbCVWZ0` on Railway
2. Check if TELEGRAM_CHAT_ID = `2100366814` on Railway
3. If not → must update before /звіт works

### Phase 2: Test /звіт Locally (OPTIONAL)
- Mock telegram send to see full flow
- Verify parts_1, parts_2, dashboard, week_planner all present

### Phase 3: Commit & Deploy to Railway
- Verify code is clean
- Push to GitHub
- Railway auto-redeploys

### Phase 4: Test /звіт on Railway
- User runs `/звіт` in bot
- Should see all 5 components:
  1. ✅ Text report (part 1 & 2)
  2. ✅ Week plan (in text)
  3. ✅ Dashboard image
  4. ✅ Astro block (with or without AI)
  5. ✅ Health charts (if available)

## Code Locations
- `monitor.py` р. 4554-4559: week_planner block added
- `monitor.py` р. 5065-5079: dashboard sendPhoto
- `monitor.py` р. 3427-5150: main() function
- `dashboard.py` р. 1-177: get_dashboard_bytes()
- `week_planner.py` р. 1-281: get_week_planner_block()

## NEXT ACTION
1. Get user to verify Railway vars (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
2. If OK → commit and push
3. If not → update Railway vars first, then commit
