# Task: Multi-functional AI expansion across all topics

User wants: MORE AI notifications/alerts/graphs across ALL 10 topics (health, crypto, running,
email, calendar, astro, finance, work/shifts, mood/habits, weather/traffic).
- Length: varies by topic (short alerts, long reports)
- Charts: 1-2 new (most important topics)
- Frequency: active — as many as there's reason for (within reason)
- AI should proactively track: reminders, emails, calendar, recommendations, email replies, health

## Key files
- smart_notifications_v3.py — 4 daily analyses (morning/lunch/afternoon/evening) — EXPAND these prompts, add more topics per call, more coins
- message_generator.py — event-triggered messages (crypto_move, vip_email, event_soon, idle_timeout, deep_analysis) — ADD new trigger types + widen crypto watchlist
- intelligent_listener.py — triggers checked (crypto/VIP email/idle/time-based/deep_analysis) — ADD new triggers: calendar prep, mood/habit checkin, weather/traffic alert, weekly running comparison
- monitor.py — themes_ai (7 topics) + astro_ai + email_ai — already decent, maybe widen crypto coins
- charts.py / strava_charts.py — ADD 1-2 new charts (crypto trend chart + combined life-score chart)

## Plan
1. Expand crypto watchlist from 4 coins (BTC/ETH/AVAX/ONDO) to include more (SOL, top movers) in message_generator.py _get_live_crypto AND intelligent_listener _check_crypto_moves
2. Add new triggers in intelligent_listener.py: 
   - "event_prep" — meeting/event in 30-60min → prep brief (agenda reminder)
   - "weekly_run_compare" — compare this week vs last week running (1x/week trigger via day-of-week check)
   - "habit_checkin" — evening habit/mood nudge if not logged
3. Expand _generate_message in message_generator.py: add prompt variations per trigger with topic-specific depth (short for crypto_move/vip_email/event_soon, long for deep_analysis/morning/evening)
4. Expand smart_notifications_v3.py 4 daily handlers — add more topics into each (finance/portfolio progress, work shift balance, weather/traffic snippet) so morning/lunch/afternoon/evening cover more ground
5. Add 1-2 new charts: crypto trend line chart (BTC/ETH/AVAX/ONDO over 30d) + already existing combined_dashboard — check if worth extending
6. Compile, commit, push, redeploy

## Status: IN PROGRESS
