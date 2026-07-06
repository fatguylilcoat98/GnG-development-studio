# Job Lifecycle

```
Draft → Planning → Ready for ChatGPT → ChatGPT Planned → Ready for Claude →
Sent to Claude → Building → Testing → Needs Chris Approval → Approved →
Completed → Archived
```

## Rules

- **Forward, one step at a time**, with two named exceptions below. Illegal
  jumps are refused (403 at the API).
- **Cannot skip Needs Chris Approval when `approval_required` is true.** At
  `Testing`, the only legal next step is `Needs Chris Approval` — never
  `Completed` directly.
- **No-approval path.** When `approval_required` is false, `Testing` goes
  straight to `Completed` (the approval detour is skipped entirely).
- **Building can escalate directly to Needs Chris Approval.** This is a
  deliberate widening beyond a purely linear ladder: the Needs Chris panel's
  triggers (blocked, tests failed, a merge or deployment decision needed) can
  surface *mid-build*, not only after the formal Testing gate. A job stuck at
  `Building` does not have to fake its way through `Testing` just to reach
  Chris.
- **Needs Chris Approval can send a job back to Building.** `POST
  /api/jobs/<id>/reject {reason}` is the one deliberate exception to
  forward-only progression — the actionable Needs Chris queue's "Send back
  for more work" button. Legal only from `Needs Chris Approval`, and logged
  distinctly (`rejected: true` + the reason) in the job's history so it's
  never mistaken for ordinary forward movement, exactly like
  `PlanningRoomStore.emergency_skip`. A rejected job can walk forward again
  through `Testing → Needs Chris Approval` as many times as it takes.
- **Archived is terminal.** Nothing moves out of it.
- **Completed can archive.** `Completed → Archived` is the only step after
  `Completed`.
- **Draft can be deleted only if never sent.** `DELETE` (via
  `POST /api/jobs/<id>/delete`) only succeeds while status is still `Draft`.
  It is logged as a tombstone in the append-only ledger (so the history is
  honest) but disappears from every normal listing and 404s on direct lookup
  — a real delete from the outside, an honest record on the inside.
- **All transitions are written to the ledger** (`state/jobs.jsonl`), including
  the ledger line for a delete.

## Dashboard button → transition mapping

The center panel's buttons are a friendlier surface over the same state
machine; each one calls the generic advance/generate endpoint underneath, so
the server-side rules above are enforced regardless of which button was
clicked:

| Button | What it does |
|---|---|
| Save Draft | Creates/keeps the job at `Draft` |
| Generate ChatGPT Plan Prompt | Builds the prompt, saves it as a note, auto-walks `Draft → Planning → Ready for ChatGPT` |
| Generate Claude Code Build Prompt | Builds the prompt, saves it as a note, auto-walks forward to `Ready for Claude` if not already there |
| Mark Sent to ChatGPT | `Ready for ChatGPT → ChatGPT Planned` — click once ChatGPT's response has been pasted back |
| Mark Sent to Claude | `Ready for Claude → Sent to Claude` |
| Mark Waiting on Claude | `Sent to Claude → Building` |
| Mark Needs Chris | Advances to `Needs Chris Approval` (from `Testing` when required, or as the `Building` escape hatch) |
| Mark Complete | Advances to `Completed` — the server refuses this if the approval gate hasn't been cleared |

The "auto-walk" behavior only ever moves through the purely linear pre-build
stages (`Draft` .. `Ready for Claude`), which have no branching or gate. It
never forces a job past `Testing`, `Needs Chris Approval`, or `Archived`.
