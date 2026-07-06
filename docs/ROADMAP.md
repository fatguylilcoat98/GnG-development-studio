# Roadmap

Documented here for context. **Only Phase 1 is built.** Nothing below it is
implemented — building it is future, operator-approved work, not an implicit
green light.

## Phase 1 — Manual coordination workspace (built)

Projects, jobs, the job lifecycle, prompt generation, manual Claude report and
ChatGPT plan ingestion, project memory, the Founder Report, the Needs Chris
panel, and the AI Planning Room (idea → ChatGPT → Claude → optional council →
unified plan → sign-off → build prompt). Everything is local, dry-run,
copy/paste. See `docs/AI_PLANNING_ROOM.md` for the planning workflow's own
detail.

## Phase 2 — GitHub/PR status, read-only

Read (never write) a project's current branch, latest commit, and open PR
status from GitHub, to auto-fill the project registry fields Chris currently
enters by hand. Still no automation of any action — display only.

## Phase 3 — Server status, read-only

Read (never mutate) whether a project's live service is up, its health
endpoint's response, and its port — informational only, no restart authority.

## Phase 4 — Claude report auto-ingestion

If Claude Code can push its final report somewhere Studio can read
automatically (a file, a webhook), stop requiring Chris to paste it by hand.
Parsing logic already exists (`parse_claude_report`); this phase is about the
delivery mechanism, not the parser.

## Phase 5 — Approval queue

A dedicated queue view across every project's `Needs Chris Approval` jobs,
instead of scanning the Needs Chris panel project by project.

## Phase 6 — Operator-approved deployment

The first phase where Studio could ever *trigger* something outside itself —
and only with an explicit, per-action operator approval step, never a
standing "live mode." Requires its own safety review before a line of it is
written.

## Phase 7 — Rollback manager

If Phase 6 ships, a matching rollback path becomes necessary. Not designed
yet.

## Phase 8 — Multi-AI coordination

Wiring actual API calls to ChatGPT/Claude/other models directly into the
Planning Room, replacing manual copy/paste — the natural end state of the
workflow in `docs/AI_PLANNING_ROOM.md`, but explicitly the *last* phase, after
every read-only and approval-gated step above has shipped and been trusted.

---

Each phase requires its own explicit go-ahead. Nothing here is scheduled.
