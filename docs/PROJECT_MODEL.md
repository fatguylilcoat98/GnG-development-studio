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
