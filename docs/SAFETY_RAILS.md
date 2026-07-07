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

## The first exception: reading Studio's own git status

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

## The second exception: build artifact verification

Studio makes exactly one kind of outbound network call: a single, bounded,
read-only GET to a build's own recorded `test_url`, and only to verify the
folder exists, has real application files, and that URL actually responds
(HTTP 200, not a directory listing, not connection-refused) before Chris is
allowed to Approve or Continue past it. This exists because a Claude Code
report claiming `STATUS: Complete` isn't proof the app was ever actually
runnable — see the Decision Deck incident this was built to prevent.

The guardrails on this one exception:

- **Host allow-list, not a blanket allowance.** `_is_local_or_tailscale_host()`
  refuses anything that isn't `localhost`, a loopback/private-LAN address, or
  Tailscale's own CGNAT range (`100.64.0.0/10`). A real DNS name or a public
  IP is refused outright — Studio never resolves or contacts an external
  host, ever.
- **Read-only, single GET, bounded read** (20KB, 5s timeout) — never a POST,
  never follows the app's own links, never crawls.
- **Only the build's own already-recorded URL** — never a URL Studio invents,
  guesses, or discovers; the URL has to already be on file (from a Claude
  Code report or Chris's own manual edit) before Studio will ever touch it.
- **Enforced at the action layer, not just the UI** — `require_verified_or_raise()`
  is called from the actual `continue-after-test`/`approve` handlers, so a
  direct API call can't skip past a failed check either.

`TestNoAutomation` still hard-blocks `subprocess`/`Popen`/`os.system`/
`socket.socket`/`requests.` completely — Studio still never executes a
command, launches a process, or calls a third-party library. Only the stdlib
`urllib.request` GET described above is permitted, and only inside the guard.

## The third exception: the Safe Server Agent (`agent.py`)

Command execution exists in exactly one place — a SEPARATE process
(`agent.py`, port 8894) that the browser talks to directly. `studio.py`
remains subprocess-free and never imports or calls the agent (both facts are
test-enforced). The agent's posture is blocked-by-default:

- **Approved projects only** — projects live in `agent_config.json` on the
  server. The browser can only name a `projectId` from that file; there is no
  route that accepts a path for execution.
- **Allow-listed checks only** — `/run-check` accepts a `checkName` that must
  be in the fixed `CHECK_CATALOG` (npm test/build, pytest, unittest,
  `docker compose ps`) AND in that project's own `checks` list. Commands are
  fixed argv tuples, never shell strings, never templated with user input;
  `shell=True` never appears (test-enforced).
- **Read-only otherwise** — `/inspect` gathers tree/docs/scripts/git evidence;
  `/read-file` refuses `.env`/keys/tokens/credentials by name, path escapes by
  realpath containment, and files over 200KB.
- **No code path at all** for deploying, restarting services, deleting files,
  pushing to a remote, or arbitrary shell.
- **Every request logged** — allowed or refused, with the reason, to
  `state/agent_log.jsonl`.
- **CORS allow-list** — the agent only answers browsers whose Origin is
  listed in `agent_config.json` (`allowed_origins`).

## Hands-off projects

PathBack, LYLO, Splendor, and CLASPION are seeded `hands_off: true`. In Phase
1 this is a **visible signal**, not a code-enforced block: it shows as a badge
in the project list and is meant to stop a human (or an AI being directed by
one) from generating a build prompt against them without noticing. Enforcing
it at the API layer is explicitly deferred — see `docs/ROADMAP.md`.
