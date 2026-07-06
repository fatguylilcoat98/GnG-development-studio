# GNG Development Studio

**GNG Development Studio is the build coordination system for Chris, ChatGPT, and
Claude Code.**

It is not AUBS OS.
It is not PathBack.
It is not LYLO.
It is not Splendor.

**It manages all of them.**

Chris should no longer be the middleman between ChatGPT, Claude Code, VS Code,
GitHub, terminal, server, screenshots, and reports. GNG Development Studio is the
one cockpit where he can:

1. Open GNG Development Studio.
2. Pick a project.
3. Type what he wants built.
4. Generate a ChatGPT planning prompt.
5. Generate a Claude Code execution prompt.
6. Track Claude's work.
7. See reports.
8. Approve only when needed.
9. See what changed, what failed, what passed, and what is next.

It also runs the **multi-AI planning workflow** Chris actually uses before
building anything — idea → ChatGPT direction → Claude critique → optional
council → unified plan → Chris's sign-off — see
[`docs/AI_PLANNING_ROOM.md`](docs/AI_PLANNING_ROOM.md).

Every project gets **persistent, version-controlled memory**
(`projects/<slug>/`, see [`docs/PROJECT_MODEL.md`](docs/PROJECT_MODEL.md)) —
so resuming after weeks away means opening the project, not reconstructing it.
A **Command Center** (Search, AI Inbox, Founder Dashboard) gives a cross-project
view; a **Timeline** gives a chronological one; the **Needs Chris** queue is
actionable (Approve / Send back for more work); and four differently-sized
continuity packets (Where Are We?, Continuity Packet, Start New Chat Packet,
Continue Project) cover everything from a quick status check to a full
Claude-Code resume prompt — see [`docs/WORKFLOW.md`](docs/WORKFLOW.md).

## What this is NOT (Phase 1)

- Not CI. Not GitHub automation. Not an AI API client. Not SSH automation.
- Not a deployment tool. Not a process manager (no pm2/systemd control).
- Not live. `GNG_STUDIO_MODE` is always `dry-run` — there is no live mode.

Everything here is coordination: prompts to copy, reports to paste, status to
track. See [`docs/SAFETY_RAILS.md`](docs/SAFETY_RAILS.md) and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for what's deliberately deferred.

## Run

```bash
python3 studio.py                 # dashboard at http://127.0.0.1:8893/
# or: ./scripts/start.sh
```

Founder report (no server required):

```bash
./scripts/founder-report.sh       # writes reports/FOUNDER_REPORT.md + 4 more
```

| Env var | Default | Meaning |
|---|---|---|
| `GNG_STUDIO_PORT` | `8893` | listen port |
| `GNG_STUDIO_BIND` | `127.0.0.1` | listen address (loopback by default) |
| `GNG_STUDIO_MODE` | `dry-run` | always dry-run; Studio has no live mode |

## Layout

```
studio.py            # stdlib-only server + all core logic (projects, jobs,
                      # prompts, reports, decisions, risks, notes, planning rooms)
dashboard.html        # the four-pane UI + Planning Room + Needs Chris panel
test_studio.py        # full test suite (stdlib unittest)
scripts/
  start.sh            # python3 studio.py
  founder-report.sh   # writes reports/*.md, appends this repo's OWN git status
state/                # gitignored — projects.json, jobs.jsonl, reports.jsonl,
                      # decisions.jsonl, notes.jsonl, risks.jsonl,
                      # planning_rooms.jsonl, studio_state.json
projects/<slug>/      # NOT gitignored — durable, version-controlled per-project
                      # memory (PROJECT_STATE.md, MISSION/ARCHITECTURE/ROADMAP.md,
                      # DECISIONS.md, RISKS.md, NEXT_ACTION.md, CHATGPT/, CLAUDE/,
                      # COUNCIL/, REPORTS/, PRS/, SCREENSHOTS/, FILES/) — see
                      # docs/PROJECT_MODEL.md
reports/              # gitignored generated output — regenerate any time
docs/
  STUDIO_MISSION.md
  WORKFLOW.md
  PROJECT_MODEL.md
  JOB_LIFECYCLE.md
  AI_PLANNING_ROOM.md
  SAFETY_RAILS.md
  ROADMAP.md
```

## Projects Studio coordinates (seed registry)

AUBS OS, PathBack, LYLO, Splendor, CLASPION, Handshake, Veracore, Knowledge
Spine (aubs-knowledge), Builder Budget, GNG Website, and a Future Project
placeholder. See [`docs/PROJECT_MODEL.md`](docs/PROJECT_MODEL.md).

## Test

```bash
python3 test_studio.py
```

**Truth · Safety · We Got Your Back.**
