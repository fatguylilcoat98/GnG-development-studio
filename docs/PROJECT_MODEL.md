# Project Model

## Fields

Every project in the registry (`state/projects.json`, seeded from
`DEFAULT_PROJECTS` in `studio.py`) has:

`id · name · type · repo_path · github_url · live_service_name · port · status
· hands_off · notes · current_goal · current_branch · latest_commit · open_pr ·
next_action`

`type` is one of `platform · product · agent · service · website · tool`.

## Seed registry (11 projects)

| Project | Type | Hands-off |
|---|---|---|
| AUBS OS | platform | no |
| PathBack | product | **yes** |
| LYLO | product | **yes** |
| Splendor | agent | **yes** (frozen) |
| CLASPION | service | **yes** (sole governance author) |
| Handshake | service | no |
| Veracore | product | no (dormant) |
| Knowledge Spine (aubs-knowledge) | service | no |
| Builder Budget | tool | **yes** (architecture undecided) |
| GNG Website | website | no |
| Future Project | tool | no (placeholder) |

`hands_off` is a per-project flag, not a hard block enforced by Studio's code
in this phase — it is a clear, visible signal in the UI (badge on the project
list) and in every generated prompt's safety rails ("do not touch unrelated
projects"). Chris (or whoever is directing Claude Code) is the actual
enforcement mechanism in Phase 1: Studio labels the fact plainly so it can't be
missed.

## Project Memory

`GET /api/project/<id>/memory` aggregates everything Studio knows about one
project into a single view: mission (the project's `notes` field), current
status, completed/active/blocked work (bucketed by job status), architecture
notes, important decisions, open risks, next action, and the last 10 jobs and
reports. This is the page to open when a chat has gotten too long — see
`docs/WORKFLOW.md`.

## Seeding vs. live edits

The seed data lives in *code* (`DEFAULT_PROJECTS` in `studio.py`) so it's
version-controlled. The first time Studio runs, it writes that seed out to
`state/projects.json` (gitignored). After that, Chris's live edits — current
goal, branch, commit, PR, next action — persist there across restarts without
ever being committed to git.

## Why branch/commit/PR fields start empty

Phase 1 does not read GitHub or any project's live git state automatically —
that's `docs/ROADMAP.md` Phase 2/3. Reading another project's repo, even
read-only, is out of scope for this build (see the hard-stop rules in the
commit history). Chris fills these fields in manually until then.

## Persistent project folders (`projects/<slug>/`)

Every registered project also gets a durable, **version-controlled** folder —
unlike `state/` (a gitignored runtime ledger), `projects/` is meant to be read,
browsed, and hand-edited over time:

```
projects/<slug>/
  PROJECT_STATE.md   # the canonical, always-fresh memory file (regenerated —
                      #   see below; do not hand-edit, it will be overwritten)
  MISSION.md ARCHITECTURE.md ROADMAP.md   # hand-edited docs; Studio seeds a
                      #   skeleton once and never overwrites them again
  DECISIONS.md RISKS.md   # append-only logs
  NEXT_ACTION.md      # overwritten (not appended) on every update
  CHATGPT/Current_Plan.md + History/
  CLAUDE/Current_Report.md + History/
  COUNCIL/Latest.md + History/
  REPORTS/ PRS/ SCREENSHOTS/ FILES/
```

`PROJECT_STATE.md` is regenerated (`sync_project_state()`) after every job
status change, prompt generation, report/plan/council paste, decision, or
risk — it is a *view*, not something to hand-edit. `Where Are We?`,
`Continuity Packet`, and `Start New Chat Packet` all read these files
directly (not only the JSONL stores), which is what makes them survive a
restart: as long as the files exist on disk, the packets can be rebuilt from
nothing else.

`CHATGPT/Current_Plan.md` and `CLAUDE/Current_Report.md` hold whichever is
most recent of two related-but-distinct things: the Planning Room's
architecture-direction / independent-critique exchange, *and* a job's
generated planning prompt / ingested build report. Both are legitimately "a
ChatGPT plan" or "a Claude report" over a project's life, so both write
through to the same current/History slots rather than inventing separate
folders the task didn't ask for.

### No orphan jobs

Every job, note, risk, decision, report, and planning room **must** be
attached to exactly one registered project id. `JobStore.create()` (and every
other store's `create()`/`ingest()`) raises `ValueError` if the project is
missing or unknown — there is no way to create an orphaned record.
