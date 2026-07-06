# Safety Rails

## The fixed list

Every generated Claude Code build prompt (and every planning-room prompt)
carries this exact list, plus any per-job `safety_notes` appended:

- Do not touch unrelated projects.
- Do not restart services unless explicitly approved.
- Do not deploy unless explicitly approved.
- Do not delete originals.
- Do not touch secrets.
- Do not enable live mode.
- Report exactly what changed.
- Run tests.
- Provide final status.

## What Studio itself will not do (Phase 1, code-level)

- **No AI API calls** — no OpenAI, Anthropic, Grok, or any other model API.
  Every prompt is generated text for Chris to copy; every response is text he
  pastes back.
- **No GitHub API automation** — Studio never opens, comments on, or merges a
  PR, and never reads another project's live git/GitHub state automatically.
- **No SSH automation.**
- **No deployment authority** — nothing here ships code anywhere.
- **No PM2/systemd controls** — Studio never restarts or manages a service.
- **No live execution** — `GNG_STUDIO_MODE` is always `dry-run`; there is no
  live mode to enable.

These are enforced by `test_studio.py`'s `TestNoAutomation` class, which scans
`studio.py` and `dashboard.html` for the actual telltales (`import subprocess`,
`Popen`, `socket.socket`, `paramiko`, `pm2`, `systemctl`, known AI/GitHub API
hosts) rather than trusting a design intention alone.

## The one exception: reading Studio's own git status

`scripts/founder-report.sh` runs plain, read-only `git status` and `git log`
against **this repo** (the Studio's own checkout) to include in
`FOUNDER_REPORT.md`, per the explicit requirement that the founder report show
"git status of Studio repo." This is the one place in the whole project that
touches git, and:

- It lives entirely in the shell script, never in `studio.py` — the core
  module stays subprocess-free, full stop, which is what the no-automation
  test actually verifies.
- It only ever runs `git status` / `git log` — read-only, never push, pull,
  reset, checkout, or clean.
- It only ever operates on the Studio's own working directory — never another
  project's repo.

## Hands-off projects

PathBack, LYLO, Splendor, and CLASPION are seeded `hands_off: true`. In Phase
1 this is a **visible signal**, not a code-enforced block: it shows as a badge
in the project list and is meant to stop a human (or an AI being directed by
one) from generating a build prompt against them without noticing. Enforcing
it at the API layer is explicitly deferred — see `docs/ROADMAP.md`.
