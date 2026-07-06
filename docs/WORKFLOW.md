# Workflow

## The end-to-end loop

1. **Pick a project** in the left panel. Studio sets it as the active project
   and loads its jobs, planning rooms, and conversation history.
2. **Start a Planning Room** (recommended for anything non-trivial) or go
   straight to a **Build Request** for something small/urgent.
3. In the Planning Room: paste Chris's idea, copy the ChatGPT prompt, paste
   ChatGPT's response back, copy the Claude prompt, paste Claude's independent
   critique back. If they disagree, run an optional **Council** round. Draft a
   **Unified Plan**. Chris signs off.
4. Once signed off (or an explicit **Emergency Build / Skip Planning**), Studio
   generates the **Claude Code Build Prompt** and creates a linked Job in
   **Ready for Claude Code**.
5. Chris runs the prompt in Claude Code. Studio's job walks forward — Sent to
   Claude → Building → Testing — as Chris marks progress.
6. Chris **pastes Claude's final report** back into Studio. Studio parses the
   fixed status format (status / files changed / tests / commit / PR /
   blockers / next action / needs approval) — or keeps the raw text if the
   format wasn't followed exactly.
7. If approval is required, the job lands at **Needs Chris Approval** and
   shows up in the red **Needs Chris** panel — which is *actionable*: Approve,
   or **Send back for more work** (returns the job to `Building` with a
   reason, logged distinctly — see `docs/JOB_LIFECYCLE.md`), right from the
   banner, no hunting for the job first.
8. **Completed → Archived.**

## The "Where are we?" / continuity problem

Long chats lose context. Three packets exist specifically to fix that, each a
different size for a different moment:

- **Where are we?** — a short, copyable status block for one project: current
  goal, what was last decided, what ChatGPT said, what Claude said, whether
  council is needed, the next build step, what needs Chris.
- **Project Continuity Packet** — a longer copyable block (mission, status,
  recent decisions, current plan, open risks, next action, latest Claude Code
  report) meant to be pasted at the top of a brand-new ChatGPT or Claude chat.
- **Start New Chat Packet** — the strongest one: who Chris is, what the
  project is, architecture, completed/active work, decisions, current
  disagreement/risks, what ChatGPT and Claude should each do next, and what
  *not* to touch (every other hands-off project, named explicitly).
- **Continue Project (Claude Code)** — a fourth, distinct packet aimed at
  Claude Code specifically (not a planning conversation): repo path,
  `PROJECT_STATE.md`, the current job, and the last Claude report, framed as
  "resume from here, don't restart."

All four are **folder-file-sourced** (`docs/PROJECT_MODEL.md`), which is what
makes them survive a restart — even a freshly-started server with no
in-memory state can reproduce them from the files on disk alone.

## Search, Timeline, and the AI Inbox

Three cross-cutting views live in the **Command Center** at the bottom of the
dashboard:

- **Search** — plain substring search across every job, note, decision, risk,
  report, planning room, and hand-edited project file (`MISSION.md`,
  `ARCHITECTURE.md`, etc.), scoped to one project or across all of them.
- **Timeline** (a tab in the Conversation Workspace) — every job/room status
  change, decision, risk, report, and saved prompt for one project, merged
  into a single newest-first feed, sourced from data already logged elsewhere.
- **AI Inbox** — the cross-project view of everything currently waiting on an
  exchange: jobs sitting at "copy this prompt" or "paste that response," and
  planning rooms mid-conversation. One place to see what's stalled across
  every project at once, instead of clicking into each one to check.

## Founder Dashboard and Founder Report

The dashboard's **Founder Dashboard** panel is a live view over the same data
`./scripts/founder-report.sh` writes to disk — one source of truth, two ways
to read it. The script regenerates five files under `reports/` any time,
without a server running: `FOUNDER_REPORT.md` (the full picture — active
project, its folder path and current `PROJECT_STATE.md`, active jobs, the AI
Inbox, Needs Chris items, latest plan/report/council notes, decisions, risks,
next action, known repo paths, plus this Studio repo's own git status),
`CURRENT_STATUS.md`, `NEXT_ACTION.md`, `WAITING_ON_CHRIS.md`, `RISKS.md`.

## Editing a project's own files

The left panel's **Edit Project Files** section reads and writes
`MISSION.md`, `ARCHITECTURE.md`, `ROADMAP.md`, and `NEXT_ACTION.md` directly —
Chris doesn't need a text editor open on the server to correct the mission
statement or jot down an architecture decision. `PROJECT_STATE.md`,
`DECISIONS.md`, and `RISKS.md` are deliberately not editable here: the first
is auto-regenerated and would just be overwritten, the other two are
append-only logs (see `docs/PROJECT_MODEL.md`).

## Everything is manual/copy-paste (Phase 1)

There is no API integration to ChatGPT, Claude, or any other model. Chris
copies prompts out, runs them wherever he actually talks to those models, and
pastes results back in. See `docs/SAFETY_RAILS.md` and `docs/ROADMAP.md` for
when (if ever) that changes.
