# Decision Deck

A lightweight, local-first web app that helps someone think through **one
important decision** using structured cards. It guides you through your
situation, options, pros/cons, risks, and a future-self reflection, then
produces a clean one-page summary.

**It never tells you what to choose.** There is no scoring, no
recommendation, and no AI. The clarity is yours; the app just organizes your
thinking.

## Features

- **Card stepper** — Welcome → Situation → Options → Pros/Cons → Risks →
  Future You → Summary, with a progress indicator and Back/Next navigation.
- **Auto-save** — every keystroke is saved to your browser's `localStorage`
  (key: `decision_deck_current`). Reload the page and your work is still there.
- **Edit freely** — go back to any earlier card and change your answers.
- **One-page summary** — Decision, Situation, Options, Pros, Cons, Risks,
  Future-self reflection, What I know now, and What I still need to learn.
- **Copy summary** to the clipboard, or **Download markdown**.
- **Start a new decision** clears the current one (after a confirmation).
- **Private** — nothing leaves your device. No backend, no accounts, no
  network calls.
- **Mobile-first**, calm dark UI, no build step, no dependencies.

## Run it

It's a static site — just serve the folder and open it in a browser.

```bash
cd /home/chris/GnG-development-studio/projects/decision-deck/app
python3 -m http.server 8765 --bind 100.90.72.114
```

Then open **http://100.90.72.114:8765** in your browser.

(You can also open `index.html` directly with a `file://` URL; the clipboard
copy is most reliable when served over http, which is why a local server is
recommended.)

## Data shape

Stored under `localStorage["decision_deck_current"]`:

```json
{
  "title": "",
  "situation": "",
  "options": [],
  "pros": [],
  "cons": [],
  "risks": [],
  "futureYou": "",
  "knowNow": "",
  "needToLearn": "",
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601"
}
```

## Files

| File | Purpose |
|------|---------|
| `index.html` | Markup + card `<template>`s |
| `styles.css` | Calm, mobile-first styling |
| `app.js` | Stepper, list editors, auto-save, summary, copy/download |
| `README.md` | This file |
| `tests/manual-test-plan.md` | Manual acceptance checklist |

## Tech

Static HTML + CSS + vanilla JavaScript. No framework, no build system, no
backend. Intentionally no AI — this project also serves as a clean end-to-end
validation of the GNG Development Studio workflow.

## Not in this MVP (possible later)

- Multiple saved decisions
- Per-option pros/cons and scoring (deliberately omitted — scoring can feel
  like the app is deciding for you)
- PDF export
- Multi-device sync
