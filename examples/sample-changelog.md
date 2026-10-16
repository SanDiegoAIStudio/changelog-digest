# Changelog

Hobby notes for a seed calendar and a small kitchen timer.

## 2026-03-14 16:10 - [recipe-box] Timers keep the oven honest

**Commit:** b7e31aa
**Session:** Oven timer

### What Changed
The countdown now pauses when the lid opens, and it resumes when the lid shuts. A bump that lasts only a moment no longer freezes the timer.

### Key Features
- **Lid pause:** the countdown waits while the lid sensor says the lid is up.

### Inspiration
A loaf needs a steady window of heat, and a beep that fires while the lid is open is easy to miss.

### Files Modified
- `src/timer.py` - pause when the lid sensor trips

---

## 2026-03-14 09:30 - [garden-planner] Frost dates drive the seed calendar

**Commit:** 4f2a9c1
**Session:** Seed calendar

### What Changed
Sowing dates shift when the last frost date moves. The early peas move with that date, and the later crops keep their spacing behind them.

### Key Features
- **Frost lookup:** the last frost date is stored once and applied to the sowing list.

### Inspiration
A late cold snap last spring wiped out an early pea sowing that the old calendar refused to move.

### Files Modified
- `src/calendar.py` - frost date lookup

---

## 2026-03-12 - [garden-planner] Beds get a simple soil log

**Commit:** 9e8d7c6
**Session:** Soil log

### What Changed
Each bed stores a short note about the soil mix and the last watering day.

### Key Features
- **Bed notes:** one note per bed, shown next to the bed name.

### Inspiration
Paper tags in the shed kept fading after rain, so the mix for each bed was easy to forget.

### Files Modified
- `src/beds.py` - soil notes for each bed
