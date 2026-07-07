# Decision Deck — Manual Test Plan

Serve the app first:

```bash
cd /home/chris/GnG-development-studio/projects/decision-deck/app
python3 -m http.server 8765 --bind 100.90.72.114
```

Open http://100.90.72.114:8765

## Acceptance checklist

| # | Test | Expected | Pass |
|---|------|----------|------|
| 1 | App opens locally | The Welcome card renders (not a directory listing / blank page) | ☐ |
| 2 | Start a decision | Optionally type a title, click **Start** → Situation card appears | ☐ |
| 3 | Progress indicator | Bar + "Step N of 6" update as you move through cards | ☐ |
| 4 | Complete all cards | Situation, Options, Pros/Cons, Risks, Future You all accept input | ☐ |
| 5 | List editor | Adding rows, typing, pressing Enter adds a row, `×` removes a row | ☐ |
| 6 | Auto-save | "Saving…" → "Saved" appears in the top-right after typing | ☐ |
| 7 | Reload preserves data | Refresh the page → your answers and current card content persist | ☐ |
| 8 | Edit earlier answers | Use **Back** to change a prior card; the change sticks | ☐ |
| 9 | Summary displays all info | Summary shows every filled section correctly | ☐ |
| 10 | No recommendation | Summary never says "you should choose X"; a footer states the app doesn't recommend | ☐ |
| 11 | Copy Summary | Click **Copy summary** → toast confirms; paste elsewhere matches | ☐ |
| 12 | Download markdown | Click **Download markdown** → a `.md` file downloads with all sections | ☐ |
| 13 | Start a new decision | Confirm dialog → returns to Welcome with cleared fields | ☐ |
| 14 | Mobile layout | Narrow the window / open on phone → single-column, readable, usable | ☐ |

## Automated checks

`tests/logic.test.js` runs the pure data/summary logic under Node with no
browser or dependencies:

```bash
cd /home/chris/GnG-development-studio/projects/decision-deck/app
node tests/logic.test.js
```

All assertions should print `ok` and the script should exit 0.
