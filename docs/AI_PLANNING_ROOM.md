# AI Planning Room

The workflow Chris actually uses *before* building anything: get ChatGPT's
architecture/product direction, get Claude's independent critique, optionally
run a council round when they disagree, synthesize a unified plan, and only
then — after Chris signs off (or explicitly declares an emergency) — let
Studio generate the Claude Code build prompt.

**Fully manual/copy-paste. No OpenAI, Anthropic, Grok, or any other API call
exists anywhere in this feature.** Every "Copy prompt for X" button produces
text Chris pastes into whatever tool he actually talks to that model in; every
"Paste X Response" box stores text he pastes back.

## Why a room is separate from a job

A **Planning Room** governs *whether Chris has approved the plan* — a gate
that exists before any build work starts. A **Job**'s own
`Needs Chris Approval` stage (see `docs/JOB_LIFECYCLE.md`) governs a different,
later gate: *whether Chris approves the finished result* after Testing. These
are orthogonal. A room can require heavy up-front vetting for a risky idea
whose resulting job still needs no post-build approval, or vice versa.

A project can have many planning rooms over time — one per idea. The most
recent is what the dashboard shows by default; older ones remain queryable via
`GET /api/planning-rooms?project=<id>`.

## Sections

- **Chris Original Idea** — required to start a room.
- **ChatGPT Response** — pasted back after copying the ChatGPT prompt.
- **Claude Response** — pasted back after copying the Claude prompt.
- **Other AI / Council Responses** — a list; each entry has an author label
  and content.
- **Disagreements** — free text, also fed into the Council Prompt.
- **Risks** — free text, also fed into the Council Prompt and the linked job's
  constraints.
- **Unified Plan** — the synthesized plan draft.
- **Chris Sign-Off** — `{signed, note, at}`, set only via the Chris Approved
  action.

## Planning statuses and how they're reached

```
Idea → ChatGPT Reviewed → Claude Reviewed → [Council Needed → Council Complete]
     → Unified Plan Ready → Chris Signed Off → Ready for Claude Code
```

The council branch is optional — `Claude Reviewed` can go straight to
`Unified Plan Ready` if no council round is used.

| Status reached | What causes it |
|---|---|
| `ChatGPT Reviewed` | Pasting a ChatGPT response while at `Idea` |
| `Claude Reviewed` | Pasting a Claude response while at `ChatGPT Reviewed` |
| `Council Needed` | Pasting the *first* council/other-AI response while at `Claude Reviewed` |
| `Council Complete` | Automatically, the moment **Generate Unified Plan Draft** is clicked while at `Council Needed` — logged as its own distinct, real transition in the ledger, even though the click that causes it also immediately continues on to `Unified Plan Ready` |
| `Unified Plan Ready` | **Generate Unified Plan Draft**, from `Claude Reviewed` (no council) or from `Council Complete` |
| `Chris Signed Off` | **Chris Approved** — only legal from `Unified Plan Ready` |
| `Ready for Claude Code` | **Generate Claude Code Build Prompt** — only once signed off, or an emergency skip is recorded |

**Mark Needs Chris Sign-Off** is a no-op confirmation: it only succeeds once
the room is already at `Unified Plan Ready` (i.e., after the draft has been
generated) — it exists as an explicit, deliberate "I want Chris's attention on
this now" click distinct from the drafting step itself, not a new pipeline
stage of its own.

## The gate: build prompt cannot be generated until...

```
can_generate_build_prompt(room) := status == "Chris Signed Off"
                                    OR emergency_skip.engaged == true
```

Enforced server-side (`403` at the API), not just hidden in the UI — calling
`POST /api/planning-rooms/<id>/build-prompt` before either condition holds is
refused with `IllegalTransition`.

**Emergency Build / Skip Planning** bypasses the entire ladder from *any*
non-terminal status, straight to `Ready for Claude Code` — but it is never
silent: the room's `emergency_skip` field records `{engaged, reason, at}`, and
the history ledger logs the bypass with the status it jumped from, so it's
always visible in the room's history and distinguishable from a normal
sign-off.

## Generating the build prompt

The first successful call creates (once — `linked_job_id` stays fixed after
that) a new Job for the room's project: `title` from the idea's first ~80
characters, `description` from the unified plan (or the raw idea, in an
emergency-skip with no plan drafted), `constraints` from the disagreements +
risks text. It then generates that job's Claude Code Build Prompt exactly the
same way a job created directly from the Build Request form would — the
Planning Room is a front door onto the same job pipeline, not a separate one.

## Council Mode

**Copy Council Prompt** builds one clean, self-contained prompt containing:
project context, Chris's original idea, the ChatGPT response, the Claude
response, current disagreements, a list of exact questions to answer (starting
with the recorded disagreement, if any, then a fixed set of challenge
questions), and an explicit instruction: *do not agree blindly — challenge
assumptions.*

## Where are we? / Continuity Packet

Both are plain read-only queries over a project's latest room, latest job,
latest decision, and current registry fields — see `docs/WORKFLOW.md` for what
each includes. Neither writes anything; both exist purely to solve the
long-chat context-loss problem by giving Chris one paste-able block to open a
fresh chat with.

## Safety

No API integration of any kind. No planning-room action ever queues a build,
spawns a process, or reaches the network — the same `TestNoAutomation` scan
that covers the rest of Studio covers this feature; there is no separate
carve-out.
