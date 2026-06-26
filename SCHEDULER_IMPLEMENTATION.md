# Proactive Scheduler v3.0 — Implementation Complete

## What Was Built

### 1. **proactive_scheduler.py** (200 lines)
- Thread-based scheduler with 4 daily schedules: 6am, 12pm, 3pm, 8pm UTC+2
- Singleton worker thread that wakes up every 60 seconds to check schedules
- Dedup via `scheduler_state.json` to prevent duplicate messages
- Exports: `start_scheduler(callbacks)`, `stop_scheduler()`, `is_running()`, `get_scheduler_status()`
- ZoneInfo: Europe/Bratislava (UTC+2 during summer, UTC+1 during winter)

### 2. **smart_notifications_v3.py** (520 lines)
- 4 AI-powered message generators:
  - `_analyze_morning()` — 6am: Greeting, events, crypto prices, health tips (250 words)
  - `_analyze_lunch()` — 12pm: VIP emails summary, crypto updates, lunch break (250 words)
  - `_analyze_afternoon()` — 3pm: Productivity tips, action items, step counter (250 words)
  - `_analyze_evening()` — 8pm: Day reflection, astro insights, motivation (300 words)
- **Data sources:**
  - Gmail IMAP: Last 7 days of emails, VIP filtering
  - CoinGecko API: BTC, ETH, AVAX, ONDO prices + 24h changes
  - Local JSON: Health (steps, sleep), Weight, Calendar events
  - Gemini 2.5-flash: AI text generation (with model-fallback to 2.0-flash & lite)
- **Telegram integration:** `_send_to_telegram()` sends HTML-formatted messages
- Each function has try/catch error handling + fallback text if Gemini fails

### 3. **bot.py modifications**
- Added imports: `proactive_scheduler`, `smart_notifications_v3.CALLBACKS`
- Added startup in `main()`: Scheduler starts after leader election
- Daemon thread: Scheduler runs in background, doesn't block polling loop

## How It Works

```
6:00 AM (UTC+2)
  ├─ Scheduler worker checks hour == 6
  ├─ Calls handle_morning_schedule(...)
  ├─ Fetches: emails, crypto, health, calendar
  ├─ Calls _analyze_morning() → Gemini generates 250-word greeting
  └─ Calls _send_to_telegram() → Message appears in chat

[Same flow repeats at 12pm, 3pm, 8pm]
```

## Files Changed / Created

| File | Change | Lines |
|------|--------|-------|
| `proactive_scheduler.py` | NEW | 200 |
| `smart_notifications_v3.py` | NEW | 520 |
| `bot.py` | MODIFIED | +15 (imports + start scheduler) |

**Total new code: ~735 lines**

## Timezone Configuration

- **Timezone:** `Europe/Bratislava` (UTC+2 summer, UTC+1 winter)
- **Schedules (local time):**
  - 6:00 — Morning report
  - 12:00 — Lunch update
  - 15:00 — Afternoon recommendations
  - 20:00 — Evening summary

## Gemini Model Fallback

If primary model (gemini-2.5-flash) hits rate limit:
1. Try gemini-2.0-flash (different quota pool)
2. Try gemini-2.5-flash-lite (fallback)
3. Use local fallback text if all fail

## Data Sources

### Gmail
- Fetches last 7 days of emails
- Filters VIP: boss, investors (InterFin/Maroš), HR
- Timeout: 10 seconds (IMAP)

### CoinGecko
- Free API, no auth required
- Coins: BTC, ETH, AVAX, ONDO
- Includes 24h price change + market cap
- Timeout: 5 seconds

### Local Files (data/ directory)
- `health.json` — daily steps, sleep hours
- `weight.json` — current weight
- `reminders.json` — events (todo)
- `calendar_events.json` — upcoming events (todo)

### Google Calendar (TODO)
- Requires OAuth2 setup
- Currently returns empty list

## Error Handling

- **Gmail timeout:** Returns empty email list, continues
- **CoinGecko fail:** Returns empty crypto dict, shows "N/A" in message
- **Gemini 429 (rate limit):** Model fallback with backoff (5-15s)
- **Gemini timeout:** Returns pre-written fallback text
- **Telegram send fail:** Logs error, doesn't block scheduler

## Testing

✅ **Locally verified:**
- All 3 modules import successfully
- Scheduler starts/stops without errors
- CoinGecko fetch returns real prices
- Health data loads correctly
- Scheduler correctly calculates next schedule time

## Production Checklist

- [ ] Railway redeploy (git push triggers auto-deploy)
- [ ] Check Railway logs for "[SCHEDULER] Started" message
- [ ] Verify Telegram messages arrive at 6am/12pm/3pm/8pm UTC+2
- [ ] Monitor Gemini API costs (currently pay-as-you-go)
- [ ] Add health data population (currently testing with 0 steps)
- [ ] Integrate Google Calendar (OAuth2)
- [ ] Add graphs (health trend chart, crypto price chart)

## Next Steps

1. **Push to Railway** → Auto-redeploy
2. **Verify 6:00 AM trigger** → Check logs & Telegram message
3. **Monitor costs** → Gemini calls every 6 hours (4x day)
4. **Add graphs** → Chart generation modules
5. **Facebook/YouTube** → Separate project (future)

## Files to Watch

```
/home/user/bot-update/
├── proactive_scheduler.py         [NEW] Scheduler core
├── smart_notifications_v3.py      [NEW] AI message generators
├── bot.py                         [MODIFIED] Startup
├── data/
│   ├── scheduler_state.json       [NEW] Daily dedup state
│   ├── health.json                [EXISTING] Health data
│   └── weight.json                [EXISTING] Weight data
└── SCHEDULER_IMPLEMENTATION.md    [THIS FILE]
```

## Commit

```
commit 8831fff701
Author: Runable
Date:   2026-06-26

    PROACTIVE SCHEDULER v3.0: Thread-based 4x daily AI-generated messages
    
    - proactive_scheduler.py: 4-hour scheduler with dedup
    - smart_notifications_v3.py: Gemini-powered message generators
    - bot.py: Integrate scheduler startup
    - Timezone: Europe/Bratislava (UTC+2)
    - Lookback: 30 days for trends
    - Fallback: Model switching + local fallback text
```

---

**Status:** ✅ READY FOR RAILWAY DEPLOYMENT
