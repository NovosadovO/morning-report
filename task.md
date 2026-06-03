# TASK: monitor.py fixes

## Fixes needed

1. **Period/vibe fix** (~2502)
   - `4<=h<9` → split: `4<=h<7` = early_morning, `7<=h<11` = morning, `11<=h<13` = midday
   - Add `_early_morning_vibes` (4-7am) and proper `_morning_vibes` (7-11am)
   - 9am should NOT get "Обідній спринт"

2. **Header redesign** (~2545)
   - One fixed beautiful style (no 12 rotations)
   - Show: period icon + time + date/weekday + location context + vibe
   - Format:
     ```
     🌅 <b>09:00  ·  Ср 03.06</b>
     🏠 Вдома  ·  Вихідний
     <i>Ранок вирішує день! 🌅</i>
     ```
   - Location: if work day + shift active → "🏭 На роботі", else "🏠 Вдома"
   - Weekend: 🏖 Вихідний

3. **Armolopid dedup** (~3676)
   - The duplication was analyzed: line 3676 is in `build_*` fn inside main()
   - Line 5509 is in check_day_summary() (21:00) — separate, OK
   - The "second" appearance is in morning_context (04:30/05:00 pre-shift message at ~6468)
   - These are different messages sent at different times → NOT a bug actually
   - User sees both because morning pre-shift AND main report both mention Armolopid
   - FIX: In the main report health block (3676), only show Armolopid if h >= 7 
     (not in pre-shift messages which are 04:30/05:00)
   - Actually user said "ліки Armolopid з'являються двічі в одному повідомленні"
   - Need to check if within single report text Armolopid appears twice

4. **Context check before report** — location/shift context in header
   Already handled in #2

## Status
- [ ] Period split
- [ ] Header
- [ ] Armolopid dedup (need to verify actual duplication source)
- [ ] Push to git
