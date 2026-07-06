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
   shows up in the red **Needs Chris** panel. Chris reviews and approves.
8. **Completed → Archived.**

## The "Where are we?" / continuity problem

Long chats lose context. Two features exist specifically to fix that:

- **Where are we?** — a short, copyable status block for one project: current
  goal, what was last decided, what ChatGPT said, what Claude said, whether
  council is needed, the next build step, what needs Chris.
- **Project Continuity Packet** — a longer copyable block (mission, status,
  recent decisions, current plan, open risks, next action, latest Claude Code
  report) meant to be pasted at the top of a brand-new ChatGPT or Claude chat
  so nothing has to be re-explained from scratch.

## The Founder Report

`./scripts/founder-report.sh` regenerates five files under `reports/` any
time, without a server running: `FOUNDER_REPORT.md` (the full picture, plus
this Studio repo's own git status), `CURRENT_STATUS.md`, `NEXT_ACTION.md`,
`WAITING_ON_CHRIS.md`, `RISKS.md`.

## Everything is manual/copy-paste (Phase 1)

There is no API integration to ChatGPT, Claude, or any other model. Chris
copies prompts out, runs them wherever he actually talks to those models, and
pastes results back in. See `docs/SAFETY_RAILS.md` and `docs/ROADMAP.md` for
when (if ever) that changes.
