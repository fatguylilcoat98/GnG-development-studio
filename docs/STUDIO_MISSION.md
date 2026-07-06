# Studio Mission

GNG Development Studio is the build coordination system for Chris, ChatGPT, and
Claude Code across every GNG project.

It is not AUBS OS. It is not PathBack. It is not LYLO. It is not Splendor.
**It manages all of them.** AUBS OS is one of the projects Studio coordinates
work for — Studio is not built inside it and is not owned by it (it was, in
fact, evolved out of an app that briefly lived inside `aubs-os` — see
`docs/ROADMAP.md` for that history — and has since been extracted to its own
repo, `gng-development-studio`, precisely so it could stand apart from any one
platform or product).

## The problem

Chris has been the middleman between ChatGPT, Claude Code, VS Code, GitHub, a
terminal, a server, screenshots, and reports — for every project, every day.
Context gets lost between long chats. Plans live in one tool, execution in
another, reports nowhere durable. Nothing tracks what needs his attention versus
what's routine.

## The fix

One cockpit. Chris opens Studio, picks a project, types what he wants, generates
a ChatGPT planning prompt and a Claude Code execution prompt, tracks the build
through a real lifecycle, pastes reports back in, and is told plainly — in one
red panel — whenever something needs him and nothing else.

## What Studio is (Phase 1)

- A **project registry**: every GNG project, its type, its repo, its status,
  whether it's hands-off.
- A **build request + job lifecycle**: Draft through Archived, with an
  approval gate that cannot be skipped when required.
- A **prompt generator**: ChatGPT planning prompts and Claude Code build
  prompts, both carrying the same fixed safety rails every time.
- A **planning room**: the multi-AI workflow (ChatGPT → Claude → optional
  council → unified plan → sign-off) that runs *before* a build prompt is
  generated. See `docs/AI_PLANNING_ROOM.md`.
- A **conversation workspace**: every plan, prompt, report, architecture note,
  decision, risk, and next step, persisted and copyable — solving the
  long-chat context-loss problem project by project.
- A **Needs Chris panel**: the one place that says, plainly, what needs him,
  which project, why, and what his choices are.

## What Studio is not (Phase 1)

No AI API calls. No GitHub automation. No SSH automation. No deployment. No
service management. No live execution. Local-only, dry-run, coordination only.
See `docs/SAFETY_RAILS.md`.

## Provenance

Studio began as "GNG Build Control" and later "AUBS Development Workspace"
inside `aubs-os/apps/gng-build-control`. It has been extracted into its own
repo and renamed GNG Development Studio because it coordinates work across
*every* GNG project, not just AUBS OS — it should not live inside, or be seen
as belonging to, any single one of them. The original copy inside `aubs-os` is
left untouched for now; it is not this repo's concern.

**Truth · Safety · We Got Your Back.**
