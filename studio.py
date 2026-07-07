"""
GNG Development Studio — studio.py

The build coordination cockpit for Chris, ChatGPT, and Claude Code across every
GNG project. This is NOT AUBS OS, NOT PathBack, NOT LYLO, NOT Splendor — it is the
tool that manages all of them. See README.md and docs/STUDIO_MISSION.md.

Stack: Python stdlib only (http.server + json). No database, no external
dependencies, no AI API calls, no deployment authority. Local-only, dry-run by
design — nothing in this module executes a command, deploys anything, restarts a
service, or calls any AI/GitHub/SSH API. (Verified by test_studio.py's
no-automation scan.)

Everything Studio records is a WORKFLOW RECORD, not an action: creating or
advancing a project, job, note, report, decision, risk, or planning room writes
only to the append-only JSONL/JSON state under state/ (gitignored). Prompts are
generated TEXT for Chris to copy — never sent anywhere by this program.

The one deliberate exception: build artifact verification (see
verify_build_live below) makes a single bounded, read-only GET — but only to a
build's own recorded test_url, and only when that URL resolves to localhost, a
private-LAN address, or Tailscale's CGNAT range. Never an arbitrary or
external host; never AI/GitHub/SSH. See docs/SAFETY_RAILS.md.

Port: 8893 (loopback by default). Run: `python3 studio.py` (server) or
`python3 studio.py --founder-report` (write the five reports/ files and exit).
"""
import ipaddress
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
REPORTS_DIR = os.path.join(ROOT, "reports")
PROJECTS_DIR = os.path.join(ROOT, "projects")
DASHBOARD_PATH = os.path.join(ROOT, "dashboard.html")
GUIDED_PATH = os.path.join(ROOT, "guided.html")
OUTCOMES_PATH = os.path.join(ROOT, "outcomes.html")
HANDOFF_PATH = os.path.join(ROOT, "handoff.html")

PROJECTS_PATH = os.path.join(STATE_DIR, "projects.json")
JOBS_PATH = os.path.join(STATE_DIR, "jobs.jsonl")
REPORTS_PATH = os.path.join(STATE_DIR, "reports.jsonl")
DECISIONS_PATH = os.path.join(STATE_DIR, "decisions.jsonl")
NOTES_PATH = os.path.join(STATE_DIR, "notes.jsonl")
RISKS_PATH = os.path.join(STATE_DIR, "risks.jsonl")
ROOMS_PATH = os.path.join(STATE_DIR, "planning_rooms.jsonl")
STUDIO_STATE_PATH = os.path.join(STATE_DIR, "studio_state.json")
BUILDS_PATH = os.path.join(STATE_DIR, "builds.jsonl")

PORT = int(os.environ.get("GNG_STUDIO_PORT", "8893"))
BIND = os.environ.get("GNG_STUDIO_BIND", "127.0.0.1")
MODE = os.environ.get("GNG_STUDIO_MODE", "dry-run")   # dry-run only; Studio has no live execution mode

# ── constants ──────────────────────────────────────────────────────────────────
PROJECT_TYPES = ("platform", "product", "agent", "service", "website", "tool")

JOB_STAGES = ["Draft", "Planning", "Ready for ChatGPT", "ChatGPT Planned",
             "Ready for Claude", "Sent to Claude", "Building", "Testing",
             "Needs Chris Approval", "Approved", "Completed", "Archived"]
PRIORITY_LEVELS = ("low", "normal", "high", "emergency")

PLANNING_STATUSES = ["Idea", "ChatGPT Reviewed", "Claude Reviewed", "Council Needed",
                     "Council Complete", "Unified Plan Ready", "Chris Signed Off",
                     "Ready for Claude Code"]

NOTE_TYPES = ("chatgpt_plan", "chatgpt_plan_response", "claude_code_prompt",
             "architecture_note", "next_steps")

# PROJECT_STATE.md's canonical section headings, in order. This file is the
# per-project persistent memory Chris (or a fresh ChatGPT/Claude chat) can
# read cold — see docs/PROJECT_MODEL.md.
PROJECT_STATE_HEADINGS = [
    "Mission", "Current Status", "Current Goal", "Current Sprint",
    "Current Architecture", "Completed Work", "In Progress", "Blocked",
    "Open Questions", "Latest ChatGPT Plan", "Latest Claude Report",
    "Latest Council Notes", "Active PRs", "Risks", "Needs Chris",
    "Next Action", "Last Updated",
]

DEFAULT_AI_TEAM = {"chatgpt": True, "claude": True, "claude_code": True, "council": False}

STUDIO_WHO_IS_CHRIS = (
    "Chris is the founder/operator directing every GNG project. He works "
    "through GNG Development Studio to coordinate ChatGPT (planning), Claude "
    "(independent critique), and Claude Code (execution) without being the "
    "manual middleman between them."
)

SAFETY_RAILS = [
    "Do not touch unrelated projects.",
    "Do not restart services unless explicitly approved.",
    "Do not deploy unless explicitly approved.",
    "Do not delete originals.",
    "Do not touch secrets.",
    "Do not enable live mode.",
    "Report exactly what changed.",
    "Run tests.",
    "Provide final status.",
]

FINAL_STATUS_FORMAT = """STATUS: <Complete|Blocked|Needs Approval>
FILES CHANGED: <list>
TESTS: <pass/fail summary>
COMMIT: <hash>
PR: <number or none>
BLOCKERS: <none | description>
NEXT ACTION: <text>
NEEDS APPROVAL: <yes|no>
FOLDER: <exact absolute path this was built/run in, or none>
TEST COMMAND: <exact command Chris can run locally to see it working, or none>
TEST URL: <exact local URL Chris can open in a browser, or none>"""

# The seed project registry. This is the CODE-side source of truth; on first run
# it is written into state/projects.json (gitignored) so Chris's live edits
# (current_goal, current_branch, next_action, ...) persist across restarts
# without living in git. GNG Development Studio treats AUBS OS as ONE of the
# projects it coordinates work for — Studio is not built inside it and is not
# owned by it.
DEFAULT_PROJECTS = [
    {"id": "aubs-os", "name": "AUBS OS", "type": "platform",
     "repo_path": "~/aubs-os", "github_url": "https://github.com/fatguylilcoat98/aubs-os",
     "live_service_name": None, "port": None, "status": "active development",
     "hands_off": False,
     "notes": "The constitutional platform repo (kernel, doorway, registries, laboratory, runtime). Studio coordinates work FOR it; it is not the parent of Studio.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "Chris to set the current goal."},
    {"id": "pathback", "name": "PathBack", "type": "product",
     "repo_path": "~/pathback", "github_url": "https://github.com/fatguylilcoat98/The-Guard-Table",
     "live_service_name": "pathback", "port": 8787, "status": "hands-off",
     "hands_off": True,
     "notes": "Ring 3 product, 'The Good Neighbor Guard'. Do not work on PathBack per standing instructions.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "n/a (hands-off)"},
    {"id": "lylo", "name": "LYLO", "type": "product",
     "repo_path": "~/lylo-governed-legacy", "github_url": "https://github.com/fatguylilcoat98/lylo-governed-legacy",
     "live_service_name": "lylo", "port": 5000, "status": "hands-off",
     "hands_off": True,
     "notes": "Ring 3 product. Do not work on LYLO per standing instructions.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "n/a (hands-off)"},
    {"id": "splendor", "name": "Splendor", "type": "agent",
     "repo_path": "~/splendor-theremarkable-AI", "github_url": "https://github.com/fatguylilcoat98/splendor-theremarkable-AI",
     "live_service_name": "splendor", "port": 3000, "status": "frozen",
     "hands_off": True,
     "notes": "First native agent (Ring 4). Frozen tree — do not touch per standing instructions.",
     "current_goal": "", "current_branch": "fix/autonomous-to-groq", "latest_commit": None,
     "open_pr": None, "next_action": "n/a (hands-off, frozen)"},
    {"id": "claspion", "name": "CLASPION", "type": "service",
     "repo_path": "~/claspion-local/engine", "github_url": "https://github.com/fatguylilcoat98/claspion",
     "live_service_name": "claspion", "port": 8000, "status": "stable",
     "hands_off": True,
     "notes": "Ring 0 governance engine. Sole policy author/authority. Hands-off unless Chris explicitly asks for CLASPION work.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "n/a (hands-off)"},
    {"id": "handshake", "name": "Handshake", "type": "service",
     "repo_path": "~/The-Handshake", "github_url": "https://github.com/fatguylilcoat98/The-Handshake",
     "live_service_name": None, "port": None, "status": "early / unclear",
     "hands_off": False,
     "notes": "Identity/delegation. Relationship between this repo and the in-process aubs-core/handshake.py module needs clarifying by Chris.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "Chris to clarify Handshake's scope."},
    {"id": "veracore", "name": "Veracore", "type": "product",
     "repo_path": "~/veracore", "github_url": "https://github.com/fatguylilcoat98/veracore",
     "live_service_name": None, "port": None, "status": "dormant",
     "hands_off": False,
     "notes": "Ring 3 product/app; no live service currently running.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "Chris to decide whether to revive Veracore."},
    {"id": "aubs-knowledge", "name": "Knowledge Spine (aubs-knowledge)", "type": "service",
     "repo_path": "~/aubs-knowledge", "github_url": "https://github.com/fatguylilcoat98/aubs-knowledge",
     "live_service_name": "aubs-knowledge", "port": 7870, "status": "active (cutover pending)",
     "hands_off": False,
     "notes": "Canonical Knowledge/RAG repo. Live process still runs from Splendor's frozen tree; cutover is operator-gated.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "Chris to decide on cutover timing."},
    {"id": "builder-budget", "name": "Builder Budget", "type": "tool",
     "repo_path": "undecided", "github_url": "",
     "live_service_name": "builder-budget", "port": 3002, "status": "blocked",
     "hands_off": True,
     "notes": "Capability synthesis layer. Architecture undecided — 3 sources disagree per AUBS OWNERSHIP_MATRIX.md row 16.",
     "current_goal": "Clarify what Builder is before any build work.",
     "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "Chris to clarify Builder's architecture."},
    {"id": "gng-website", "name": "GNG Website", "type": "website",
     "repo_path": "~/good-neighbor-guard-site", "github_url": "https://github.com/fatguylilcoat98/good-neighbor-guard-site",
     "live_service_name": None, "port": None, "status": "stable (GitHub Pages)",
     "hands_off": False,
     "notes": "Marketing site for The Good Neighbor Guard.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "Chris to set the current goal."},
    {"id": "future-project", "name": "Future Project", "type": "tool",
     "repo_path": "", "github_url": "", "live_service_name": None, "port": None,
     "status": "placeholder", "hands_off": False,
     "notes": "Placeholder slot for a project not yet started.",
     "current_goal": "", "current_branch": None, "latest_commit": None,
     "open_pr": None, "next_action": "Chris to define this project when ready."},
]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _id():
    return uuid.uuid4().hex


_lock = threading.Lock()


# ── generic JSONL helpers ───────────────────────────────────────────────────────
def _jsonl_append(path, record):
    os.makedirs(STATE_DIR, exist_ok=True)
    with _lock:
        with open(path, "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")


def _jsonl_load_index(path):
    """Load a JSONL ledger into an {id: latest_record} index (last write wins),
    tolerating a torn trailing line."""
    index = {}
    if not os.path.exists(path):
        return index
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                index[rec["id"]] = rec
            except (ValueError, KeyError):
                continue
    return index


# ── projects ────────────────────────────────────────────────────────────────────
def load_projects():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(PROJECTS_PATH):
        data = {"projects": {p["id"]: dict(p) for p in DEFAULT_PROJECTS}}
        with open(PROJECTS_PATH, "w") as f:
            json.dump(data, f, indent=2)
        return data["projects"]
    try:
        with open(PROJECTS_PATH) as f:
            data = json.load(f)
    except (ValueError, OSError):
        data = {"projects": {p["id"]: dict(p) for p in DEFAULT_PROJECTS}}
    return data.get("projects", {})


def save_projects(projects):
    os.makedirs(STATE_DIR, exist_ok=True)
    with _lock:
        with open(PROJECTS_PATH, "w") as f:
            json.dump({"projects": projects}, f, indent=2)


def get_project(projects, pid):
    if pid not in projects:
        raise KeyError(f"unknown project: {pid}")
    return projects[pid]


def update_project(projects, pid, **fields):
    p = get_project(projects, pid)
    p.update(fields)
    save_projects(projects)
    return p


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return slug or "project"


def create_project(projects, name, description="", kind="existing", local_folder="",
                   repo_url="", ptype="tool"):
    """The 11 seeded projects are a starting registry, not a ceiling — this is
    how 'Start New Project' adds one. Slugs auto-dedupe (project, project-2, ...)
    rather than colliding with an existing id."""
    name = str(name).strip()
    if not name:
        raise ValueError("project name is required")
    if kind not in ("new", "existing"):
        raise ValueError("kind must be 'new' or 'existing'")
    if ptype not in PROJECT_TYPES:
        raise ValueError(f"type must be one of {PROJECT_TYPES}")
    base = slugify(name)
    pid, n = base, 2
    while pid in projects:
        pid, n = f"{base}-{n}", n + 1
    project = {
        "id": pid, "name": name, "type": ptype,
        "repo_path": str(local_folder or "").strip(),
        "github_url": str(repo_url or "").strip(),
        "live_service_name": None, "port": None,
        "status": "new project" if kind == "new" else "existing project",
        "kind": kind, "hands_off": False, "notes": str(description or "").strip(),
        "current_goal": "", "current_branch": None, "latest_commit": None,
        "open_pr": None, "next_action": "",
    }
    projects[pid] = project
    save_projects(projects)
    ensure_project_folder(pid, project)
    return project


# ── studio state (active project) ──────────────────────────────────────────────
def load_studio_state():
    if not os.path.exists(STUDIO_STATE_PATH):
        return {"active_project": None}
    try:
        with open(STUDIO_STATE_PATH) as f:
            return json.load(f)
    except (ValueError, OSError):
        return {"active_project": None}


def save_studio_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STUDIO_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def set_active_project(pid):
    state = load_studio_state()
    state["active_project"] = pid
    save_studio_state(state)
    return state


class NotFound(KeyError):
    pass


class IllegalTransition(ValueError):
    pass


# ── project folders: persistent, file-based per-project memory ────────────────
# projects/<slug>/ is durable, version-controlled memory — the thing Chris opens
# to reconstruct context instead of scrolling a long chat. It is deliberately
# NOT gitignored (unlike state/, which is a runtime ledger): these files are
# meant to be read, browsed, and hand-edited over time.
def project_dir(pid):
    return os.path.join(PROJECTS_DIR, pid)


def _write_if_absent(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(content)


def _read_project_file(pid, rel, default=""):
    path = os.path.join(project_dir(pid), rel)
    if os.path.exists(path):
        with open(path) as f:
            content = f.read().strip()
        return content or default
    return default


def _write_project_file(pid, rel, content):
    path = os.path.join(project_dir(pid), rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content.rstrip("\n") + "\n")


def _append_project_file(pid, rel, entry_text):
    path = os.path.join(project_dir(pid), rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(f"\n## {_now()}\n{entry_text.strip()}\n")


def _history_filename(suffix=""):
    stamp = _now().replace(":", "-")
    tag = f"_{suffix}" if suffix else ""
    return f"{stamp}{tag}_{_id()[:8]}.md"


def _write_current_and_history(pid, subdir, current_name, content, history_suffix=""):
    """Overwrite subdir/<current_name> with content and ALSO archive a
    timestamped copy into subdir/History/ — 'current' always reflects the
    latest; nothing that was ever pasted in is lost."""
    d = os.path.join(project_dir(pid), subdir)
    hist = os.path.join(d, "History")
    os.makedirs(hist, exist_ok=True)
    with open(os.path.join(d, current_name), "w") as f:
        f.write(content.rstrip("\n") + "\n")
    with open(os.path.join(hist, _history_filename(history_suffix)), "w") as f:
        f.write(content.rstrip("\n") + "\n")


def save_chatgpt_plan_to_folder(pid, content):
    _write_current_and_history(pid, "CHATGPT", "Current_Plan.md", content)


def save_claude_report_to_folder(pid, content):
    _write_current_and_history(pid, "CLAUDE", "Current_Report.md", content)


def save_council_note_to_folder(pid, author, content):
    stamped = f"## {_now()} — {author or 'council'}\n\n{content.strip()}\n"
    _write_current_and_history(pid, "COUNCIL", "Latest.md", stamped, history_suffix=author or "council")


def append_decision_to_folder(pid, text):
    _append_project_file(pid, "DECISIONS.md", text)


def append_risk_to_folder(pid, text):
    _append_project_file(pid, "RISKS.md", text)


def write_next_action_to_folder(pid, text):
    _write_project_file(pid, "NEXT_ACTION.md", text or "")


def ensure_project_folder(pid, project):
    """Idempotent: creates any missing directory/file for one project without
    ever clobbering existing (possibly hand-edited) content. PROJECT_STATE.md
    is the one exception — it is Studio-regenerated (see sync_project_state)
    and only seeded here with a skeleton if entirely absent."""
    d = project_dir(pid)
    for sub in ("CHATGPT/History", "CLAUDE/History", "COUNCIL/History",
               "REPORTS", "PRS", "SCREENSHOTS", "FILES"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
        # git tracks no empty directories — a .gitkeep makes the required
        # subfolder structure actually persist once committed.
        _write_if_absent(os.path.join(d, sub, ".gitkeep"), "")
    _write_if_absent(os.path.join(d, "MISSION.md"),
                     f"# Mission — {project['name']}\n\n{project.get('notes', '')}\n")
    _write_if_absent(os.path.join(d, "ARCHITECTURE.md"),
                     f"# Architecture — {project['name']}\n\n(not yet documented)\n")
    _write_if_absent(os.path.join(d, "DECISIONS.md"), f"# Decisions — {project['name']}\n")
    _write_if_absent(os.path.join(d, "RISKS.md"), f"# Risks — {project['name']}\n")
    _write_if_absent(os.path.join(d, "ROADMAP.md"),
                     f"# Roadmap — {project['name']}\n\n(not yet documented)\n")
    _write_if_absent(os.path.join(d, "NEXT_ACTION.md"), (project.get("next_action") or "") + "\n")
    _write_if_absent(os.path.join(d, "CHATGPT", "Current_Plan.md"), "(no plan yet)\n")
    _write_if_absent(os.path.join(d, "CLAUDE", "Current_Report.md"), "(no report yet)\n")
    _write_if_absent(os.path.join(d, "COUNCIL", "Latest.md"), "(no council notes yet)\n")
    state_path = os.path.join(d, "PROJECT_STATE.md")
    if not os.path.exists(state_path):
        skeleton = "\n\n".join([f"# {project['name']}"] +
                               [f"## {h}\n" for h in PROJECT_STATE_HEADINGS])
        with open(state_path, "w") as f:
            f.write(skeleton + "\n")


def ensure_all_project_folders(projects):
    for pid, project in projects.items():
        ensure_project_folder(pid, project)


def _require_known_project(projects, pid):
    """Hard rule: no job, note, risk, decision, report, or planning room may be
    orphaned. Every one of them must be attached to exactly one REGISTERED
    project id — never empty, never unknown."""
    if not pid or pid not in projects:
        raise ValueError(f"unknown or missing project: {pid!r} — every record must "
                         f"be attached to exactly one registered project")


# ── jobs ────────────────────────────────────────────────────────────────────────
class JobStore:
    def __init__(self, projects):
        self.projects = projects
        self.jobs = {}
        self.load()

    def load(self):
        self.jobs = _jsonl_load_index(JOBS_PATH)

    def _save(self, rec):
        self.jobs[rec["id"]] = rec
        _jsonl_append(JOBS_PATH, rec)
        return rec

    def _get(self, jid):
        j = self.jobs.get(jid)
        if j is None or j.get("deleted"):
            raise NotFound(f"unknown job: {jid}")
        return j

    def get(self, jid):
        return self._get(jid)

    def list(self, project=None, include_deleted=False):
        vals = [j for j in self.jobs.values() if include_deleted or not j.get("deleted")]
        if project:
            vals = [j for j in vals if j.get("project") == project]
        return sorted(vals, key=lambda j: j["created_at"], reverse=True)

    def create(self, project, title, description, priority="normal", constraints="",
              approval_required=True, safety_notes=""):
        _require_known_project(self.projects, project)
        title, description = str(title).strip(), str(description).strip()
        if not title:
            raise ValueError("title is required")
        if not description:
            raise ValueError("description is required")
        if priority not in PRIORITY_LEVELS:
            raise ValueError(f"priority must be one of {PRIORITY_LEVELS}")
        rec = {
            "id": _id(), "project": project, "title": title, "description": description,
            "priority": priority, "constraints": str(constraints).strip(),
            "safety_notes": str(safety_notes).strip(),
            "approval_required": bool(approval_required), "status": "Draft",
            "history": [{"status": "Draft", "at": _now()}], "deleted": False,
            "planning_room_id": None,
            "latest_report_id": None, "created_at": time.time(), "updated_at": time.time(),
        }
        return self._save(rec)

    def set_approval_required(self, jid, value):
        job = dict(self._get(jid), approval_required=bool(value))
        return self._save(job)

    @staticmethod
    def allowed_next(job):
        cur = job["status"]
        if cur == "Archived":
            return []
        if cur == "Completed":
            return ["Archived"]
        if cur == "Approved":
            return ["Completed"]
        if cur == "Needs Chris Approval":
            return ["Approved"]
        if cur == "Testing":
            return ["Needs Chris Approval"] if job.get("approval_required", True) else ["Completed"]
        if cur == "Building":
            # manual escape hatch: a blocker found mid-build can surface to Chris
            # immediately, without waiting for the formal Testing gate.
            return ["Testing", "Needs Chris Approval"]
        idx = JOB_STAGES.index(cur)
        return [JOB_STAGES[idx + 1]] if idx + 1 < len(JOB_STAGES) else []

    def advance(self, jid, to_status):
        job = self._get(jid)
        allowed = self.allowed_next(job)
        if to_status not in allowed:
            raise IllegalTransition(f"cannot move '{job['status']}' -> '{to_status}' "
                                    f"(allowed: {allowed or 'none (terminal)'})")
        job = dict(job)
        job["status"] = to_status
        job["history"] = job["history"] + [{"status": to_status, "at": _now()}]
        job["updated_at"] = time.time()
        return self._save(job)

    def delete(self, jid):
        """Drafts only. Logged as a tombstone line (append-only), then hidden
        from normal listings and 404s on further lookup — a real delete from the
        API's point of view, an honest record in the ledger's."""
        job = self._get(jid)
        if job["status"] != "Draft":
            raise IllegalTransition(f"only Draft jobs can be deleted (status is {job['status']})")
        job = dict(job)
        job["deleted"] = True
        job["updated_at"] = time.time()
        return self._save(job)

    def reject(self, jid, reason=""):
        """The one deliberate exception to forward-only progression: Chris can
        send a job back from Needs Chris Approval to Building for more work.
        Logged distinctly (rejected=True + reason) so it's never mistaken for
        a normal forward step, exactly like PlanningRoomStore.emergency_skip."""
        job = self._get(jid)
        if job["status"] != "Needs Chris Approval":
            raise IllegalTransition(f"can only send back a job that is at Needs Chris "
                                    f"Approval (status is {job['status']})")
        job = dict(job)
        job["status"] = "Building"
        job["history"] = job["history"] + [{"status": "Building", "at": _now(),
                                            "rejected": True, "reason": str(reason)}]
        job["updated_at"] = time.time()
        return self._save(job)


# ── prompts ─────────────────────────────────────────────────────────────────────
def _files_allowed_block(project):
    repo = project.get("repo_path") or "(repo path not set — Chris must fill this in)"
    return (f"Only modify files inside {repo} for {project['name']}.",
           f"Do not modify any other project's repository. GNG Development Studio "
           f"coordinates many projects; this task is scoped to {project['name']} alone.")


def build_chatgpt_planning_prompt(job, project):
    rails = "\n".join(f"- {r}" for r in SAFETY_RAILS)
    return f"""You are the planning partner for {project['name']} inside the GNG fleet.
Turn the request below into a precise, step-by-step plan that can be handed to
Claude Code. Sharpen the goal, call out risks and open questions for Chris, and
finish your reply with a single copy-paste section titled "Prompt for Claude Code".

PROJECT: {project['name']} ({project['id']})
GOAL / TITLE: {job['title']}
WHAT CHRIS WANTS: {job['description']}
CURRENT STATE: {project.get('status', 'unknown')} — {project.get('current_goal') or 'no current goal set'}
CONSTRAINTS: {job['constraints'] or 'none stated'}
PRIORITY: {job['priority']}

SAFETY RAILS (non-negotiable — carry these into your plan)
{rails}

Please produce:
1. A restated goal (one paragraph).
2. A step-by-step plan.
3. Risks and open questions for Chris.
4. A final section titled "Prompt for Claude Code" — self-contained and pasteable."""


def _auto_walk_forward(jobs, jid, target_status):
    """Advance a job forward, one legal step at a time, toward target_status —
    used only for the purely-linear pre-build stages (Draft..Ready for Claude)
    that have no branching or approval gate. Never forces past a gate: if the
    only legal next step diverges from the target (e.g. an approval branch),
    it stops there instead of overshooting."""
    job = jobs.get(jid)
    if target_status not in JOB_STAGES:
        return job
    target_idx = JOB_STAGES.index(target_status)
    while JOB_STAGES.index(job["status"]) < target_idx:
        nxt = JobStore.allowed_next(job)
        if not nxt:
            break
        step = next((s for s in nxt if s in JOB_STAGES and JOB_STAGES.index(s) <= target_idx), None)
        if step is None:
            break
        job = jobs.advance(jid, step)
    return job


def build_claude_code_build_prompt(job, project):
    rails = list(SAFETY_RAILS)
    if job.get("safety_notes"):
        rails.append(job["safety_notes"])
    rails_block = "\n".join(f"- {r}" for r in rails)
    allowed, not_allowed = _files_allowed_block(project)
    return f"""Use Claude Code.

PROJECT: {project['name']} ({project['id']})
REPO PATH: {project.get('repo_path') or '(not set)'}

TASK: {job['title']}
{job['description']}

CONSTRAINTS: {job['constraints'] or 'none stated'}
PRIORITY: {job['priority']}

FILES ALLOWED
- {allowed}

FILES NOT ALLOWED
- {not_allowed}

SAFETY RAILS (non-negotiable)
{rails_block}

TEST EXPECTATIONS
- Run this project's existing test suite (if any) before reporting done.
- Note any tests that fail; do not silently skip them.

OUTPUT REQUIREMENTS
- Reply using EXACTLY this final status format so Studio can ingest it:

{FINAL_STATUS_FORMAT}"""


# ── claude report ingestion ──────────────────────────────────────────────────────
_REPORT_FIELD_PATTERNS = {
    "status": r"^STATUS:\s*(.+)$",
    "files_changed": r"^FILES CHANGED:\s*(.+)$",
    "tests": r"^TESTS:\s*(.+)$",
    "commit": r"^COMMIT:\s*(.+)$",
    "pr": r"^PR:\s*(.+)$",
    "blockers": r"^BLOCKERS:\s*(.+)$",
    "next_action": r"^NEXT ACTION:\s*(.+)$",
    "needs_approval": r"^NEEDS APPROVAL:\s*(.+)$",
}

# Added for the Finished Outcome screen. Optional: not required for parsed_ok,
# since most historical reports (and Claude Code runs that predate this) never
# included them — Chris can always fill these in by hand afterward instead.
_OPTIONAL_REPORT_FIELD_PATTERNS = {
    "folder": r"^FOLDER:\s*(.+)$",
    "test_command": r"^TEST COMMAND:\s*(.+)$",
    "test_url": r"^TEST URL:\s*(.+)$",
}


def parse_claude_report(raw):
    """Best-effort line parser matching FINAL_STATUS_FORMAT labels. Tolerant of
    case and stray whitespace. Returns (fields, parsed_ok) — parsed_ok is True
    only if every REQUIRED labeled field was found; optional fields (folder,
    test_command, test_url) are parsed too but never affect parsed_ok. The raw
    text is always kept so nothing is lost, and Chris can fill in fields
    manually."""
    fields = {k: None for k in _REPORT_FIELD_PATTERNS}
    fields.update({k: None for k in _OPTIONAL_REPORT_FIELD_PATTERNS})
    all_patterns = {**_REPORT_FIELD_PATTERNS, **_OPTIONAL_REPORT_FIELD_PATTERNS}
    for line in raw.splitlines():
        line = line.strip()
        for key, pattern in all_patterns.items():
            m = re.match(pattern, line, re.IGNORECASE)
            if m:
                fields[key] = m.group(1).strip()
    parsed_ok = all(fields[k] is not None for k in _REPORT_FIELD_PATTERNS)
    if fields.get("needs_approval") is not None:
        fields["needs_approval"] = fields["needs_approval"].strip().lower() in ("yes", "true")
    return fields, parsed_ok


class ReportStore:
    def __init__(self, projects):
        self.projects = projects
        self.reports = {}
        self.load()

    def load(self):
        self.reports = _jsonl_load_index(REPORTS_PATH)

    def list(self, project=None, job_id=None):
        vals = list(self.reports.values())
        if project:
            vals = [r for r in vals if r.get("project") == project]
        if job_id:
            vals = [r for r in vals if r.get("job_id") == job_id]
        return sorted(vals, key=lambda r: r["created_at"], reverse=True)

    def ingest(self, job_id, project, raw, manual=None):
        _require_known_project(self.projects, project)
        raw = str(raw or "")
        fields, parsed_ok = parse_claude_report(raw)
        if manual:
            fields.update({k: v for k, v in manual.items() if k in fields})
        rec = {"id": _id(), "job_id": job_id, "project": project, "raw": raw,
              "parsed_ok": parsed_ok, "created_at": time.time(), **fields}
        self.reports[rec["id"]] = rec
        _jsonl_append(REPORTS_PATH, rec)
        save_claude_report_to_folder(project, raw)
        return rec

    def amend(self, report_id, **overrides):
        """Let Chris correct/fill in outcome fields after the fact (folder, test
        command, test URL, blockers/notes) — e.g. when Claude Code's report
        omitted them, or got the folder wrong, as happened with Decision Deck.
        Same last-write-wins JSONL pattern as JobStore.advance()."""
        rec = self.reports.get(report_id)
        if rec is None:
            raise NotFound(f"unknown report: {report_id}")
        allowed = {"folder", "test_command", "test_url", "blockers"}
        updates = {k: str(v).strip() for k, v in overrides.items()
                  if k in allowed and v is not None}
        rec = dict(rec, **updates)
        self.reports[rec["id"]] = rec
        _jsonl_append(REPORTS_PATH, rec)
        return rec


def verify_outcome_files(folder, files_changed):
    """Catch the Decision Deck failure mode on disk, not just trust the report:
    a build was marked Complete, but the recorded folder was empty, missing,
    or was actually Studio's OWN per-project bookkeeping directory
    (projects/<slug>/ itself — ARCHITECTURE.md, CLAUDE/, REPORTS/, ...,
    auto-created for every registered project by ensure_project_folder, never
    application code) rather than wherever the real app was supposed to be
    written. Runs wherever studio.py actually executes, so this checks the
    real filesystem Studio is running on.

    Only the bookkeeping directory ITSELF (a direct child of PROJECTS_DIR) is
    flagged — a subfolder beneath it, e.g. projects/<slug>/app/, is a
    perfectly legitimate place for real application code (exactly the layout
    Studio itself recommends when a project's own folder can't be used
    directly) and must NOT be flagged just for living under PROJECTS_DIR."""
    result = {"folder_exists": False, "is_studio_metadata_folder": False,
             "missing_files": [], "checked_files": [], "warning": None}
    if not folder:
        result["warning"] = "No folder recorded yet — cannot verify the build exists on disk."
        return result
    expanded = os.path.expanduser(str(folder))
    real = os.path.realpath(expanded)
    real_projects_dir = os.path.realpath(PROJECTS_DIR)
    if real == real_projects_dir:
        is_metadata_folder = True
    else:
        rel = os.path.relpath(real, real_projects_dir)
        is_metadata_folder = not rel.startswith(os.pardir) and len(rel.split(os.sep)) == 1
    if is_metadata_folder:
        result["is_studio_metadata_folder"] = True
        result["warning"] = ("This folder is Studio's own project bookkeeping directory "
                             "(ARCHITECTURE.md, CLAUDE/, REPORTS/, ...), not application "
                             "code. The real build folder was never recorded correctly — "
                             "edit this outcome with where the app actually lives.")
        return result
    if not os.path.isdir(expanded):
        result["warning"] = f"Folder does not exist on this server: {folder}"
        return result
    result["folder_exists"] = True
    names = [n.strip() for n in re.split(r"[,\n]", files_changed or "") if n.strip()]
    missing = []
    for name in names:
        if len(name) > 80 or " " in name.strip("."):
            continue   # prose ("several files across the frontend"), not a real filename
        result["checked_files"].append(name)
        if not os.path.exists(os.path.join(expanded, name)):
            missing.append(name)
    result["missing_files"] = missing
    if result["checked_files"] and len(missing) == len(result["checked_files"]):
        result["warning"] = (f"None of the files this report claims changed "
                             f"({', '.join(missing)}) exist in {folder}.")
    elif missing:
        result["warning"] = f"Some claimed files are missing from {folder}: {', '.join(missing)}"
    return result


def build_outcome(job, project, report):
    """The 'Finished Outcome' record for one job: where it lives, how to see it
    running, and what's left. Folder/test command/test URL come from the
    Claude Code report when it declared them; folder falls back to the
    project's registered repo_path otherwise. Chris can always correct any of
    these afterward (ReportStore.amend) — this is exactly the gap Decision
    Deck exposed: the build finished but nothing recorded or showed where it
    actually landed. file_check verifies the claim against the real
    filesystem instead of trusting it blindly."""
    folder = (report.get("folder") if report else None) or project.get("repo_path") or ""
    test_command = (report.get("test_command") if report else None) or ""
    test_url = (report.get("test_url") if report else None) or ""
    files_changed = (report.get("files_changed") if report else None) or ""
    if test_url:
        open_instruction = f"Open {test_url} in your browser."
    elif folder:
        open_instruction = f"Open the project folder: {folder}"
    else:
        open_instruction = "No folder or URL recorded yet — edit this outcome to add one."
    return {
        "job_id": job["id"], "project": job["project"], "project_name": project["name"],
        "job_status": job["status"],
        "status": (report.get("status") if report else None) or job["status"],
        "folder": folder, "test_command": test_command, "test_url": test_url,
        "files_changed": files_changed,
        "notes": (report.get("blockers") if report else None) or "",
        "report_id": report["id"] if report else None,
        "open_instruction": open_instruction,
        "completed_at": report["created_at"] if report else job["updated_at"],
        "file_check": verify_outcome_files(folder, files_changed),
    }


# ── build artifact verification (live) ──────────────────────────────────────────
# The one deliberate, narrow exception to "Studio never calls out anywhere":
# a single bounded GET to a URL Chris/Claude Code themselves already recorded,
# and ONLY when that URL's host is localhost, a private-LAN address, or
# Tailscale's own CGNAT range — never an arbitrary or external host, never an
# AI/GitHub/third-party API. This is artifact verification, not internet
# access. See docs/SAFETY_RAILS.md for the explicit policy this implements.
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_PRIVATE_RANGES = tuple(ipaddress.ip_network(r) for r in
                       ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _is_local_or_tailscale_host(hostname):
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return False   # a real DNS name, not a bare IP — refuse, no external lookups
    return addr in _TAILSCALE_CGNAT or any(addr in r for r in _PRIVATE_RANGES)


def verify_build_live(test_url, project_name=None, timeout=5):
    """Verify a build's recorded test_url actually responds — the direct
    fix for 'the report said Complete but nothing was there.' Refuses
    anything but a localhost/private-LAN/Tailscale host."""
    result = {"reachable": False, "http_ok": None, "is_directory_listing": None,
             "title_matches_project": None, "problems": []}
    if not test_url:
        return result
    host = urlparse(test_url).hostname
    if not _is_local_or_tailscale_host(host):
        result["problems"].append(
            f"Refusing to verify {test_url} — Studio only checks localhost, "
            "private-LAN, or Tailscale addresses, never external hosts.")
        return result
    try:
        req = urllib.request.Request(test_url, headers={"User-Agent": "gng-studio-verify/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.getcode()
            body = r.read(20000).decode("utf-8", errors="replace")
        result["reachable"] = True
        result["http_ok"] = (status == 200)
        if status != 200:
            result["problems"].append(f"{test_url} returned HTTP {status}, not 200.")
        lower = body.lower()
        is_listing = "directory listing for" in lower or "index of /" in lower
        result["is_directory_listing"] = is_listing
        if is_listing:
            result["problems"].append(f"{test_url} returned a directory listing, not the app.")
        if project_name:
            result["title_matches_project"] = project_name.lower() in lower
            # advisory only — a project name not appearing in the page isn't
            # by itself proof of failure, so it never adds to `problems`
    except urllib.error.URLError as e:
        result["problems"].append(f"Could not reach {test_url}: {e.reason}")
    except Exception as e:
        result["problems"].append(f"Could not reach {test_url}: {e}")
    return result


def run_build_verification(outcome, project_name):
    """The full artifact-verification gate: disk checks (outcome['file_check'],
    already computed) plus a live check when a test_url is on file. ok=False
    if either fails — this is what blocks Approve/Continue and produces the
    Human Intervention Required screen."""
    disk = outcome["file_check"]
    live = verify_build_live(outcome["test_url"], project_name)
    problems = [disk["warning"]] if disk["warning"] else []
    problems.extend(live["problems"])
    return {"ok": not problems, "disk": disk, "live": live, "problems": problems}


def build_verification_fix_prompt(job, project, outcome, verification):
    problems = "\n".join(f"- {p}" for p in verification["problems"])
    rails = "\n".join(f"- {r}" for r in SAFETY_RAILS)
    return f"""Use Claude Code.

PROJECT: {project['name']} ({project['id']})
REPO PATH: {project.get('repo_path') or '(not set)'}

TASK: Fix the build verification failures below. Chris tried to confirm this
build actually works, and it does not match what the last report claimed.

RECORDED OUTCOME
FOLDER: {outcome['folder'] or '(not recorded)'}
TEST COMMAND: {outcome['test_command'] or '(not recorded)'}
TEST URL: {outcome['test_url'] or '(not recorded)'}

VERIFICATION FAILURES
{problems}

WHAT TO DO
- Make sure the real application files actually exist in the folder above — or tell Chris the correct folder if this one is wrong.
- Make sure the recorded test command actually starts a server that serves the app at the recorded URL.
- Re-run the test command yourself and confirm the URL returns the real app — not a directory listing, not an error, not a blank page.
- Report back using the standard status format, with FOLDER / TEST COMMAND / TEST URL lines that are actually correct and that you verified yourself before reporting.

SAFETY RAILS (non-negotiable)
{rails}

OUTPUT REQUIREMENTS
Reply using EXACTLY this final status format so Studio can ingest it:

{FINAL_STATUS_FORMAT}"""


# ── decisions / risks / notes ───────────────────────────────────────────────────
class DecisionStore:
    def __init__(self, projects):
        self.projects = projects
        self.decisions = {}
        self.load()

    def load(self):
        self.decisions = _jsonl_load_index(DECISIONS_PATH)

    def list(self, project=None):
        vals = list(self.decisions.values())
        if project:
            vals = [d for d in vals if d.get("project") == project]
        return sorted(vals, key=lambda d: d["created_at"], reverse=True)

    def create(self, project, text, job_id=None, source="manual"):
        _require_known_project(self.projects, project)
        text = str(text).strip()
        if not text:
            raise ValueError("decision text is required")
        rec = {"id": _id(), "project": project, "job_id": job_id, "text": text,
              "source": source, "created_at": time.time()}
        self.decisions[rec["id"]] = rec
        _jsonl_append(DECISIONS_PATH, rec)
        append_decision_to_folder(project, text)
        return rec


class RiskStore:
    def __init__(self, projects):
        self.projects = projects
        self.risks = {}
        self.load()

    def load(self):
        self.risks = _jsonl_load_index(RISKS_PATH)

    def list(self, project=None, resolved=None):
        vals = list(self.risks.values())
        if project:
            vals = [r for r in vals if r.get("project") == project]
        if resolved is not None:
            vals = [r for r in vals if bool(r.get("resolved")) == resolved]
        return sorted(vals, key=lambda r: r["created_at"], reverse=True)

    def create(self, project, description, severity="normal", job_id=None, source="manual"):
        _require_known_project(self.projects, project)
        description = str(description).strip()
        if not description:
            raise ValueError("risk description is required")
        rec = {"id": _id(), "project": project, "job_id": job_id, "description": description,
              "severity": severity, "resolved": False, "source": source,
              "created_at": time.time(), "updated_at": time.time()}
        self.risks[rec["id"]] = rec
        _jsonl_append(RISKS_PATH, rec)
        append_risk_to_folder(project, description)
        return rec

    def resolve(self, rid):
        r = self.risks.get(rid)
        if r is None:
            raise NotFound(f"unknown risk: {rid}")
        r = dict(r, resolved=True, updated_at=time.time())
        self.risks[rid] = r
        _jsonl_append(RISKS_PATH, r)
        return r


class NoteStore:
    def __init__(self, projects):
        self.projects = projects
        self.notes = {}
        self.load()

    def load(self):
        self.notes = _jsonl_load_index(NOTES_PATH)

    def list(self, project=None, job_id=None, note_type=None):
        vals = list(self.notes.values())
        if project:
            vals = [n for n in vals if n.get("project") == project]
        if job_id:
            vals = [n for n in vals if n.get("job_id") == job_id]
        if note_type:
            vals = [n for n in vals if n.get("note_type") == note_type]
        return sorted(vals, key=lambda n: (not n.get("pinned"), -n["created_at"]))

    def create(self, project, note_type, content="", job_id=None, pinned=False,
              author="operator", **extra):
        _require_known_project(self.projects, project)
        if note_type not in NOTE_TYPES:
            raise ValueError(f"note_type must be one of {NOTE_TYPES}")
        rec = {"id": _id(), "project": project, "job_id": job_id, "note_type": note_type,
              "content": str(content), "pinned": bool(pinned), "author": author,
              "created_at": time.time(), "updated_at": time.time()}
        rec.update({k: v for k, v in extra.items() if v is not None})
        self.notes[rec["id"]] = rec
        _jsonl_append(NOTES_PATH, rec)
        return rec

    def pin(self, nid, pinned):
        n = self.notes.get(nid)
        if n is None:
            raise NotFound(f"unknown note: {nid}")
        n = dict(n, pinned=bool(pinned), updated_at=time.time())
        self.notes[nid] = n
        _jsonl_append(NOTES_PATH, n)
        return n


# ── planning room ────────────────────────────────────────────────────────────────
class PlanningRoomStore:
    def __init__(self, projects, jobs, notes):
        self.projects = projects
        self.rooms = {}
        self.jobs = jobs
        self.notes = notes
        self.load()

    def load(self):
        self.rooms = _jsonl_load_index(ROOMS_PATH)

    def _save(self, rec):
        self.rooms[rec["id"]] = rec
        _jsonl_append(ROOMS_PATH, rec)
        return rec

    def _get(self, rid):
        if rid not in self.rooms:
            raise NotFound(f"unknown planning room: {rid}")
        return self.rooms[rid]

    def get(self, rid):
        return self._get(rid)

    def list(self, project=None):
        vals = list(self.rooms.values())
        if project:
            vals = [r for r in vals if r.get("project") == project]
        return sorted(vals, key=lambda r: r["created_at"], reverse=True)

    def create(self, project, chris_idea, ai_team=None, constraints="", safety_notes=""):
        _require_known_project(self.projects, project)
        chris_idea = str(chris_idea).strip()
        if not chris_idea:
            raise ValueError("Chris's original idea is required")
        team = dict(DEFAULT_AI_TEAM)
        team.update(ai_team or {})
        team["claude_code"] = True   # mandatory — there is no build without it
        rec = {
            "id": _id(), "project": project, "status": "Idea", "chris_idea": chris_idea,
            "chatgpt_response": "", "claude_response": "", "council_responses": [],
            "disagreements": "", "risks": "", "unified_plan": "", "ai_team": team,
            "constraints": str(constraints).strip(), "safety_notes": str(safety_notes).strip(),
            "chris_signoff": None, "emergency_skip": None, "linked_job_id": None,
            "history": [{"status": "Idea", "at": _now()}],
            "created_at": time.time(), "updated_at": time.time(),
        }
        return self._save(rec)

    @staticmethod
    def allowed_next(room):
        cur = room["status"]
        table = {
            "Idea": ["ChatGPT Reviewed"],
            "ChatGPT Reviewed": ["Claude Reviewed"],
            "Claude Reviewed": ["Council Needed", "Unified Plan Ready"],
            "Council Needed": ["Council Complete"],
            "Council Complete": ["Unified Plan Ready"],
            "Unified Plan Ready": ["Chris Signed Off"],
            "Chris Signed Off": ["Ready for Claude Code"],
            "Ready for Claude Code": [],
        }
        return table.get(cur, [])

    def _advance(self, rid, to_status, **extra):
        room = self._get(rid)
        allowed = self.allowed_next(room)
        if to_status not in allowed:
            raise IllegalTransition(f"cannot move planning room '{room['status']}' -> "
                                    f"'{to_status}' (allowed: {allowed or 'none (terminal)'})")
        room = dict(room)
        room["status"] = to_status
        room["history"] = room["history"] + [{"status": to_status, "at": _now(), **extra}]
        room["updated_at"] = time.time()
        return self._save(room)

    def paste_chatgpt_response(self, rid, text):
        room = self._get(rid)
        room = dict(room, chatgpt_response=str(text), updated_at=time.time())
        self._save(room)
        save_chatgpt_plan_to_folder(room["project"], str(text))
        if room["status"] == "Idea":
            room = self._advance(rid, "ChatGPT Reviewed")
        return room

    def paste_claude_response(self, rid, text):
        room = self._get(rid)
        room = dict(room, claude_response=str(text), updated_at=time.time())
        self._save(room)
        save_claude_report_to_folder(room["project"], str(text))
        if room["status"] == "ChatGPT Reviewed":
            room = self._advance(rid, "Claude Reviewed")
        return room

    def paste_council_response(self, rid, author, text):
        room = self._get(rid)
        entry = {"author": str(author or "council"), "content": str(text), "at": _now()}
        room = dict(room, council_responses=room["council_responses"] + [entry],
                   updated_at=time.time())
        self._save(room)
        save_council_note_to_folder(room["project"], author, str(text))
        if room["status"] == "Claude Reviewed":
            room = self._advance(rid, "Council Needed")
        return room

    def set_disagreements_and_risks(self, rid, disagreements=None, risks=None):
        room = self._get(rid)
        room = dict(room)
        if disagreements is not None:
            room["disagreements"] = str(disagreements)
        if risks is not None:
            room["risks"] = str(risks)
        room["updated_at"] = time.time()
        return self._save(room)

    def generate_unified_plan_draft(self, rid, plan_text):
        """Stores the unified plan and walks status to Unified Plan Ready,
        passing through Council Complete first (logged distinctly) if a
        council round was used."""
        room = self._get(rid)
        room = dict(room, unified_plan=str(plan_text), updated_at=time.time())
        self._save(room)
        if room["status"] == "Council Needed":
            room = self._advance(rid, "Council Complete")
        if room["status"] in ("Council Complete", "Claude Reviewed"):
            room = self._advance(rid, "Unified Plan Ready")
        return room

    def mark_needs_signoff(self, rid):
        # idempotent nudge: if not already at Unified Plan Ready, this is a no-op
        # error unless the room is already there (Generate Unified Plan Draft is
        # what actually gets it there).
        room = self._get(rid)
        if room["status"] != "Unified Plan Ready":
            raise IllegalTransition("generate the unified plan draft first")
        return room

    def chris_approved(self, rid, note=""):
        room = self._advance(rid, "Chris Signed Off")
        room = dict(room, chris_signoff={"signed": True, "note": str(note), "at": _now()})
        return self._save(room)

    def emergency_skip(self, rid, reason):
        """Bypasses the entire planning ladder. Logged distinctly as an
        emergency bypass, never silently — engaged from ANY non-terminal status."""
        room = self._get(rid)
        if room["status"] == "Ready for Claude Code":
            return room
        room = dict(room)
        room["emergency_skip"] = {"engaged": True, "reason": str(reason), "at": _now()}
        frm = room["status"]
        room["status"] = "Ready for Claude Code"
        room["history"] = room["history"] + [{"status": "Ready for Claude Code", "at": _now(),
                                              "emergency_skip": True, "reason": str(reason),
                                              "from": frm}]
        room["updated_at"] = time.time()
        return self._save(room)

    @staticmethod
    def can_generate_build_prompt(room):
        return room["status"] == "Chris Signed Off" or bool(
            (room.get("emergency_skip") or {}).get("engaged"))

    def generate_claude_code_build_prompt(self, rid, project):
        room = self._get(rid)
        if not self.can_generate_build_prompt(room):
            raise IllegalTransition(
                "Claude Code build prompt cannot be generated until Chris signs off "
                "or an emergency skip is recorded")
        if room["linked_job_id"] is None:
            title = room["chris_idea"][:80]
            description = room["unified_plan"] or room["chris_idea"]
            constraints = "\n".join(x for x in (room.get("constraints", ""),
                                                room.get("disagreements", ""),
                                                room.get("risks", "")) if x)
            job = self.jobs.create(project=room["project"], title=title,
                                   description=description, constraints=constraints,
                                   safety_notes=room.get("safety_notes", ""))
            room = dict(room, linked_job_id=job["id"])
            room = self._save(room)
        if room["status"] != "Ready for Claude Code":
            room = self._advance(rid, "Ready for Claude Code")
        job = self.jobs.get(room["linked_job_id"])
        return job, room

    def build_chatgpt_prompt(self, rid, project_obj):
        room = self._get(rid)
        rails = "\n".join(f"- {r}" for r in SAFETY_RAILS)
        return f"""You are giving architecture / product direction for {project_obj['name']}.
Do not just validate — think independently about the best approach.

PROJECT: {project_obj['name']} ({project_obj['id']})
CHRIS'S IDEA: {room['chris_idea']}

SAFETY RAILS
{rails}

Give your architecture / product direction, tradeoffs, and open questions."""

    def build_claude_prompt(self, rid, project_obj):
        room = self._get(rid)
        chatgpt_block = (f"\nCHATGPT'S RESPONSE (for you to react to, not defer to):\n"
                         f"{room['chatgpt_response']}\n" if room["chatgpt_response"] else "")
        rails = "\n".join(f"- {r}" for r in SAFETY_RAILS)
        return f"""You are giving an INDEPENDENT critique for {project_obj['name']}. Do not
agree just because ChatGPT already weighed in — actively look for what it missed,
got wrong, or oversimplified.

PROJECT: {project_obj['name']} ({project_obj['id']})
CHRIS'S IDEA: {room['chris_idea']}
{chatgpt_block}
SAFETY RAILS
{rails}

Give your independent critique, alternative ideas, and open questions."""

    def build_council_prompt(self, rid, project_obj):
        room = self._get(rid)
        questions = ["What is each side assuming that might not hold?",
                     "Where do ChatGPT and Claude actually disagree, and why?",
                     "What would change the recommendation?",
                     "What is the single riskiest part of this plan?"]
        if room.get("disagreements"):
            questions.insert(0, f"Resolve this specific disagreement: {room['disagreements']}")
        q_block = "\n".join(f"- {q}" for q in questions)
        return f"""COUNCIL SESSION for {project_obj['name']}. Do not agree blindly with either
prior response — challenge assumptions and say plainly where you disagree.

PROJECT CONTEXT: {project_obj['name']} ({project_obj['id']}) — {project_obj.get('notes', '')}
CHRIS'S ORIGINAL IDEA: {room['chris_idea']}

CHATGPT RESPONSE:
{room['chatgpt_response'] or '(none yet)'}

CLAUDE RESPONSE:
{room['claude_response'] or '(none yet)'}

CURRENT DISAGREEMENTS: {room['disagreements'] or 'none recorded yet'}

QUESTIONS TO ANSWER
{q_block}

Do not agree blindly. Challenge assumptions."""


# ── guided build: a thin, stateless orchestration layer over the Planning ─────
# Room + Job machinery. It invents NO new persisted state machine — every
# transition below is the same rule already enforced by PlanningRoomStore /
# JobStore; this layer only decides WHICH screen to show and sequences a few
# calls behind one button, so the guided UI never needs more than one primary
# action. Advanced users can still drive rooms/jobs directly (dashboard.html
# at /advanced); guided.html and this layer are one more front door, not a
# replacement plumbing system.
GUIDED_STAGES = ("not_started", "chatgpt", "claude", "council_choice", "council",
                 "plan_review", "send_build_prompt", "claude_code_report",
                 "test_review", "needs_approval", "verification_failed",
                 "complete", "stalled")


def draft_unified_plan(room):
    """A plain, no-AI-call merge of everything gathered so far — Chris reviews
    and edits it before anything is committed. Not clever synthesis, just a
    legible starting point so the guided flow never stalls waiting on typing."""
    parts = [f"IDEA: {room['chris_idea']}"]
    if room.get("chatgpt_response"):
        parts.append(f"\nCHATGPT DIRECTION:\n{room['chatgpt_response']}")
    if room.get("claude_response"):
        parts.append(f"\nCLAUDE CRITIQUE:\n{room['claude_response']}")
    for c in room.get("council_responses", []):
        parts.append(f"\nCOUNCIL ({c.get('author', '?')}):\n{c.get('content', '')}")
    if room.get("disagreements"):
        parts.append(f"\nDISAGREEMENTS TO RESOLVE:\n{room['disagreements']}")
    parts.append("\n(Auto-drafted from the above — edit before approving if needed.)")
    return "\n".join(parts)


def compute_guided_stage(pid, projects, jobs, rooms, reports=None):
    project = get_project(projects, pid)
    proj_rooms = rooms.list(project=pid)
    room = proj_rooms[0] if proj_rooms else None
    if room is None:
        return {"kind": "not_started", "project": pid}
    job = None
    if room.get("linked_job_id"):
        try:
            job = jobs.get(room["linked_job_id"])
        except NotFound:
            job = None
    if job is not None:
        return _guided_stage_from_job(job, room, project, reports)
    return _guided_stage_from_room(room, project, rooms)


def _guided_stage_from_room(room, project, rooms):
    team = room.get("ai_team") or DEFAULT_AI_TEAM
    status = room["status"]
    base = {"room_id": room["id"], "project": room["project"]}
    if status == "Idea":
        return dict(base, kind="chatgpt", idea=room["chris_idea"],
                   prompt=rooms.build_chatgpt_prompt(room["id"], project))
    if status == "ChatGPT Reviewed":
        return dict(base, kind="claude", prompt=rooms.build_claude_prompt(room["id"], project))
    if status == "Claude Reviewed":
        if team.get("council"):
            return dict(base, kind="council_choice")
        return dict(base, kind="plan_review", plan_draft=draft_unified_plan(room))
    if status == "Council Needed":
        return dict(base, kind="council", prompt=rooms.build_council_prompt(room["id"], project),
                   responses_so_far=room["council_responses"])
    if status in ("Council Complete", "Unified Plan Ready"):
        return dict(base, kind="plan_review",
                   plan_draft=room["unified_plan"] or draft_unified_plan(room))
    if status == "Chris Signed Off":
        return dict(base, kind="plan_review", plan_draft=room["unified_plan"], ready_to_build=True)
    return dict(base, kind="stalled", reason=f"Planning room stuck at unhandled status: {status}")


def _guided_stage_from_job(job, room, project, reports):
    base = {"job_id": job["id"], "room_id": room["id"], "project": job["project"]}
    status = job["status"]
    if status in ("Draft", "Planning", "Ready for ChatGPT", "ChatGPT Planned", "Ready for Claude"):
        return dict(base, kind="send_build_prompt",
                   prompt=build_claude_code_build_prompt(job, project))
    if status in ("Sent to Claude", "Building"):
        return dict(base, kind="claude_code_report")
    latest = None
    if reports is not None:
        recs = reports.list(job_id=job["id"])
        latest = recs[0] if recs else None
    # Testing onward always carries the Finished Outcome data (folder, test
    # command/URL, files changed, notes) so Chris never again reaches the end
    # of a build without a clear "here's what to open" answer. When a test_url
    # is on file, that's an explicit "you can open this in a browser" claim —
    # verify it before showing the normal Continue/Approve screen at all.
    if status in ("Testing", "Needs Chris Approval"):
        kind = "test_review" if status == "Testing" else "needs_approval"
        outcome = build_outcome(job, project, latest)
        if outcome["test_url"]:
            verification = run_build_verification(outcome, project["name"])
            if not verification["ok"]:
                return dict(base, kind="verification_failed", outcome=outcome,
                           verification=verification,
                           fix_prompt=build_verification_fix_prompt(job, project, outcome,
                                                                    verification),
                           resume_action=("continue-after-test" if status == "Testing"
                                         else "approve"))
        return dict(base, kind=kind, latest_report=latest, outcome=outcome)
    if status == "Approved":
        return dict(base, kind="complete", needs_final_click=True, archived=False,
                   outcome=build_outcome(job, project, latest))
    if status in ("Completed", "Archived"):
        return dict(base, kind="complete", needs_final_click=False, archived=status == "Archived",
                   outcome=build_outcome(job, project, latest))
    return dict(base, kind="stalled", reason=f"Job stuck at unhandled status: {status}")


def start_guided_build(pid, jobs, rooms, idea, ai_team=None, constraints="", safety_notes=""):
    """Entry point for 'Start Build'. Auto-skips any AI-team member Chris
    turned off in the wizard by recording a placeholder response for it, so
    the state machine advances exactly as if it had been reviewed."""
    room = rooms.create(project=pid, chris_idea=idea, ai_team=ai_team,
                        constraints=constraints, safety_notes=safety_notes)
    team = room["ai_team"]
    if not team.get("chatgpt"):
        room = rooms.paste_chatgpt_response(room["id"],
                                            "(skipped — ChatGPT not part of this build's AI team)")
    if not team.get("claude"):
        room = rooms.paste_claude_response(room["id"],
                                           "(skipped — Claude not part of this build's AI team)")
    return room


def guided_generate_plan(rooms, room_id):
    """Used by both 'Skip council' (from Claude Reviewed) and 'Done pasting'
    (from Council Needed) — auto-drafts and saves the plan, landing on
    Unified Plan Ready either way."""
    room = rooms.get(room_id)
    return rooms.generate_unified_plan_draft(room_id, draft_unified_plan(room))


def guided_approve_plan(projects, rooms, notes, pid, room_id, plan_text):
    """The one 'commit' button: save Chris's (possibly edited) plan text,
    sign off, generate the Claude Code build prompt, and save it as a note —
    idempotent-safe to call whether or not generate_unified_plan_draft already
    ran (updating unified_plan text never requires a status to already match)."""
    room = rooms.generate_unified_plan_draft(room_id, plan_text)
    if room["status"] == "Unified Plan Ready":
        room = rooms.chris_approved(room_id, note="approved via guided build")
    job, room = rooms.generate_claude_code_build_prompt(room_id, pid)
    project = get_project(projects, pid)
    prompt = build_claude_code_build_prompt(job, project)
    notes.create(project=pid, job_id=job["id"], note_type="claude_code_prompt", content=prompt)
    return job, room, prompt


def guided_sent_to_claude_code(jobs, job_id):
    """Chris clicked 'Sent it' — walk the job through its purely-linear
    pre-build stages straight to Building (all single-legal-step, no gate)."""
    return _auto_walk_forward(jobs, job_id, "Building")


def guided_claude_code_report(jobs, reports, job_id, project_id, raw):
    job = jobs.get(job_id)
    rec = reports.ingest(job_id, project_id, raw)
    if rec.get("needs_approval") is not None:
        job = jobs.set_approval_required(job_id, rec["needs_approval"])
    if job["status"] == "Building":
        job = jobs.advance(job_id, "Testing")
    return job, rec


def require_verified_or_raise(reports, projects, job):
    """The actual enforcement behind 'block approval' — checked here in
    addition to the stage display, so a direct API call can't skip past a
    failed Human Intervention Required screen. Only gates jobs that have a
    test_url on file; jobs with nothing to verify are unaffected."""
    project = get_project(projects, job["project"])
    recs = reports.list(job_id=job["id"])
    latest = recs[0] if recs else None
    outcome = build_outcome(job, project, latest)
    if outcome["test_url"]:
        verification = run_build_verification(outcome, project["name"])
        if not verification["ok"]:
            raise IllegalTransition(
                "Build verification failed: " + "; ".join(verification["problems"]))


def guided_continue_after_test(jobs, reports, projects, job_id):
    job = jobs.get(job_id)
    nxt = JobStore.allowed_next(job)
    if not nxt:
        raise IllegalTransition(f"job {job_id} has no legal next step from {job['status']}")
    require_verified_or_raise(reports, projects, job)
    return jobs.advance(job_id, nxt[0])   # single legal option at Testing — no branching to pick


def guided_complete(jobs, job_id):
    job = jobs.get(job_id)
    if job["status"] == "Approved":
        return jobs.advance(job_id, "Completed")
    if job["status"] == "Completed":
        return jobs.advance(job_id, "Archived")
    return job


# ── Browser Handoff builds (Development Studio v1) ─────────────────────────────
# Studio owns the workflow, state, prompts, responses, and final report; the AI
# websites (claude.ai / chatgpt.com / grok.com) are only external workers Chris
# copies prompts into and pastes responses back from. No API keys, no direct AI
# integration — same copy/paste posture as the rest of Studio, but organized as
# BUILDS: goal in, one next-action at a time, final report out. Optional
# server evidence comes from the separate Safe Server Agent (agent.py), which
# the BROWSER talks to — studio.py itself never runs a command.
WORKERS = {
    "claude": {"name": "Claude", "url": "https://claude.ai"},
    "chatgpt": {"name": "ChatGPT", "url": "https://chatgpt.com"},
    "grok": {"name": "Grok", "url": "https://grok.com"},
    "claude_code": {"name": "Claude Code", "url": None},   # runs in Chris's terminal, no URL
}
BUILD_MODES = ("audit", "fix", "build", "review")
WORKER_SEQUENCES = {
    "builder_only": ("builder",),
    "builder_reviewer": ("builder", "reviewer"),
    "builder_council_reviewer": ("builder", "council", "reviewer"),
}
DEFAULT_ROLE_WORKERS = {"builder": "claude", "council": "grok", "reviewer": "chatgpt"}
BUILD_STATUSES = ("Draft", "Copy Prompt", "Waiting for Response",
                  "Human Decision Needed", "Review Needed", "Complete", "Error")
HANDOFF_RAILS = ("Do not deploy", "Do not delete originals", "Do not touch secrets",
                 "Do not restart services", "Do not change unrelated projects",
                 "Ask before risky actions")
RESPONSE_OUTCOMES = ("COMPLETE", "BLOCKED", "HUMAN_DECISION_REQUIRED",
                     "TESTS_FAILED", "REVIEW_PASS", "REVIEW_FAIL")


def parse_worker_response(raw):
    """v1 parser: don't try to perfectly understand every response. Every
    generated prompt asks the worker to end with a 'RESULT: <TOKEN>' line;
    that line wins. Fallback: exactly one bare token somewhere in the text
    (spaces tolerated in place of underscores). Anything else — zero tokens,
    or several conflicting ones — is (None, False): not confident, so the
    build lands at Human Decision Needed instead of guessing."""
    up = str(raw or "").upper()
    m = None
    for m in re.finditer(r"^\s*RESULT:\s*([A-Z_ ]+?)\s*$", up, re.MULTILINE):
        pass   # last RESULT: line wins — workers sometimes quote the format first
    if m:
        token = m.group(1).strip().replace(" ", "_")
        if token in RESPONSE_OUTCOMES:
            return token, True
    found = []
    for token in RESPONSE_OUTCOMES:
        if re.search(r"\b" + token.replace("_", "[_ ]") + r"\b", up):
            found.append(token)
    if found == ["COMPLETE"] or (len(found) == 1):
        return found[0], True
    if set(found) == {"REVIEW_PASS", "COMPLETE"}:
        return "REVIEW_PASS", True   # "review passed, work is complete" — not a conflict
    return None, False


def _handoff_evidence_block(inspection):
    if not inspection:
        return "(no server inspection on file — inspect the project yourself before changing it)"
    out = []
    for k, v in list(inspection.items())[:14]:
        s = v if isinstance(v, str) else json.dumps(v)
        out.append(f"- {k}: {s[:600]}")
    return "\n".join(out) or "(inspection returned nothing)"


def _handoff_prior_steps_block(build):
    if not build["responses"]:
        return "This is the first worker step; there are no prior responses."
    lines = ["Steps completed so far:"]
    for r in build["responses"]:
        lines.append(f"- {r['role']} ({WORKERS[r['worker']]['name']}): {r['outcome'] or 'unclear'}")
    last = build["responses"][-1]
    lines.append(f"\nMost recent worker output ({last['role']}), for your context:\n"
                f"{last['raw'][:1500]}")
    return "\n".join(lines)


_HANDOFF_ROLE_TASKS = {
    "builder": """YOUR TASK (Builder)
Perform the {mode} task described in the goal above, inside the repo path
given. Inspect before changing. Run the project's tests if any exist. Stay
inside this project only.

REQUIRED OUTPUT FORMAT — reply with exactly these labeled sections:
WHAT WAS INSPECTED: <files/areas you actually looked at>
WHAT WAS CHANGED: <files changed, or none>
TESTS RUN: <what you ran and pass/fail, or none available>
BLOCKERS: <none | description>
HUMAN APPROVALS NEEDED: <none | what Chris must decide>
FINAL STATUS: <one sentence>
RESULT: <COMPLETE|BLOCKED|TESTS_FAILED|HUMAN_DECISION_REQUIRED>""",
    "council": """YOUR TASK (Council)
Do NOT do the build. Challenge it. Attack the plan and product direction the
prior steps imply: what is being assumed, what could go wrong, what should
not be built at all, what a better direction would be.

REQUIRED OUTPUT FORMAT — reply with exactly these labeled sections:
ASSUMPTIONS CHALLENGED: <list>
RISKS: <list>
BETTER DIRECTION: <or "none — current direction is right">
WHAT NOT TO BUILD: <list or none>
RECOMMENDATION: <one paragraph>
RESULT: <COMPLETE|HUMAN_DECISION_REQUIRED>
(Use COMPLETE if the direction is sound enough to proceed; use
HUMAN_DECISION_REQUIRED if Chris must choose between real alternatives.)""",
    "reviewer": """YOUR TASK (Reviewer)
Independently review the Builder's result above. Do not trust its claims —
check them against the goal and the evidence. Look for missed requirements
and regression risk.

REQUIRED OUTPUT FORMAT — reply with exactly these labeled sections:
PASS/FAIL: <pass | fail>
EVIDENCE: <what you verified and how>
RISKS: <list or none>
MISSED REQUIREMENTS: <list or none>
REGRESSION CONCERNS: <list or none>
FINAL RECOMMENDATION: <one paragraph>
RESULT: <REVIEW_PASS|REVIEW_FAIL|HUMAN_DECISION_REQUIRED>""",
}


def build_handoff_prompt(build, role, worker):
    rails = "\n".join(f"- {r}" for r in build["safetyRails"]) or "- (none selected)"
    task = _HANDOFF_ROLE_TASKS[role].format(mode=build["mode"])
    return f"""You are the {role.upper()} in a GNG Development Studio build. You are
{WORKERS[worker]['name']}, working as an external worker: Studio owns the workflow
and state; you do one step and report back in the exact format below.

BUILD: {build['buildName']}
PROJECT: {build['projectName']}
REPO PATH: {build['repoPath'] or '(not set)'}
MODE: {build['mode']}
GOAL: {build['userGoal']}

CURRENT BUILD STATE
{_handoff_prior_steps_block(build)}

EVIDENCE FROM SERVER INSPECTION
{_handoff_evidence_block(build.get('inspection'))}

SAFETY RAILS (non-negotiable)
{rails}

{task}

The final line of your reply MUST be the RESULT: line, alone, exactly as
specified — Studio parses it to decide the next step."""


def _handoff_decision_for(outcome, confident, role):
    base_choices = ["retry_step", "continue_next", "mark_complete", "stop"]
    if not confident:
        return {"reason": "Studio could not confidently determine the result. "
                          "Please choose what happened.",
                "recommended": None,
                "risks": "Guessing wrong here can mark unfinished work complete, "
                         "or redo work that is already done.",
                "choices": base_choices, "outcome": None}
    if outcome == "BLOCKED":
        return {"reason": f"The {role} reported BLOCKED — it could not finish without "
                          "something it doesn't have.",
                "recommended": "retry_step",
                "risks": "Continuing past a blocker usually produces a broken result.",
                "choices": base_choices, "outcome": outcome}
    if outcome == "TESTS_FAILED":
        return {"reason": f"The {role} reported TESTS_FAILED.",
                "recommended": "retry_step",
                "risks": "Marking this complete would ship failing code.",
                "choices": base_choices, "outcome": outcome}
    if outcome == "REVIEW_FAIL":
        return {"reason": "The reviewer failed this build.",
                "recommended": "back_to_builder",
                "risks": "Completing anyway ignores an independent reviewer's fail verdict.",
                "choices": ["back_to_builder"] + base_choices, "outcome": outcome}
    # HUMAN_DECISION_REQUIRED — the worker explicitly asked for Chris
    return {"reason": f"The {role} explicitly asked for a human decision — read its "
                      "response above before choosing.",
            "recommended": None,
            "risks": "The worker flagged something it wasn't willing to decide alone.",
            "choices": base_choices, "outcome": outcome}


class HandoffBuildStore:
    """Builds for Browser Handoff Mode. Same append-only JSONL, last-write-wins
    pattern as every other Studio store."""

    def __init__(self):
        self.builds = _jsonl_load_index(BUILDS_PATH)

    def _save(self, rec):
        rec["updatedAt"] = time.time()
        self.builds[rec["id"]] = rec
        _jsonl_append(BUILDS_PATH, rec)
        return rec

    def get(self, bid):
        b = self.builds.get(bid)
        if b is None:
            raise NotFound(f"unknown build: {bid}")
        return b

    def list(self):
        return sorted(self.builds.values(), key=lambda b: b["createdAt"], reverse=True)

    def _log(self, rec, event, detail=""):
        rec["timeline"] = rec["timeline"] + [{"event": event, "detail": detail, "at": _now()}]

    def create(self, buildName, projectName, userGoal, mode, workerSequence,
              repoPath="", safetyRails=None, serverProjectId=""):
        buildName, userGoal = str(buildName).strip(), str(userGoal).strip()
        if not buildName:
            raise ValueError("build name is required")
        if not userGoal:
            raise ValueError("describe what you want done")
        if mode not in BUILD_MODES:
            raise ValueError(f"mode must be one of {BUILD_MODES}")
        if workerSequence not in WORKER_SEQUENCES:
            raise ValueError(f"worker sequence must be one of {tuple(WORKER_SEQUENCES)}")
        rails = [r for r in (safetyRails or []) if r in HANDOFF_RAILS]
        rec = {"id": _id(), "buildName": buildName,
              "projectName": str(projectName).strip() or buildName,
              "repoPath": str(repoPath).strip(), "userGoal": userGoal,
              "mode": mode, "workerSequence": workerSequence, "safetyRails": rails,
              "serverProjectId": str(serverProjectId).strip(),
              "status": "Draft",
              "currentStep": {"roleIndex": 0, "role": None, "phase": "draft",
                              "worker": None, "decision": None},
              "prompts": [], "responses": [], "timeline": [], "inspection": None,
              "finalReport": None,
              "createdAt": time.time(), "updatedAt": time.time()}
        self._log(rec, "Build created")
        return self._save(rec)

    # ── internal step machinery ────────────────────────────────────────────
    def _roles(self, rec):
        return list(WORKER_SEQUENCES[rec["workerSequence"]])

    def _status_for(self, role, phase):
        if role == "reviewer":
            return "Review Needed"
        return "Copy Prompt" if phase == "copy" else "Waiting for Response"

    def _generate_prompt(self, rec, role, worker=None):
        worker = worker or DEFAULT_ROLE_WORKERS[role]
        text = build_handoff_prompt(rec, role, worker)
        rec["prompts"] = rec["prompts"] + [{"role": role, "worker": worker,
                                            "text": text, "created_at": _now()}]
        rec["currentStep"] = {"roleIndex": self._roles(rec).index(role), "role": role,
                              "phase": "copy", "worker": worker, "decision": None}
        rec["status"] = self._status_for(role, "copy")
        self._log(rec, "Prompt generated", f"{role} ({WORKERS[worker]['name']})")

    def _advance(self, rec):
        roles = self._roles(rec)
        nxt = rec["currentStep"]["roleIndex"] + 1
        if nxt < len(roles):
            self._log(rec, "Next step chosen", roles[nxt])
            self._generate_prompt(rec, roles[nxt])
        else:
            self._complete(rec)

    def _complete(self, rec):
        rec["status"] = "Complete"
        rec["currentStep"] = dict(rec["currentStep"], phase="done", decision=None)
        rec["finalReport"] = build_handoff_final_report(rec)
        self._log(rec, "Build complete")

    def _human(self, rec, outcome, confident):
        role = rec["currentStep"]["role"]
        decision = _handoff_decision_for(outcome, confident, role)
        rec["status"] = "Human Decision Needed"
        rec["currentStep"] = dict(rec["currentStep"], phase="human", decision=decision)
        self._log(rec, "Human decision needed", decision["reason"])

    # ── actions (one per UI button) ────────────────────────────────────────
    def start(self, bid, inspection=None):
        rec = dict(self.get(bid))
        if rec["status"] != "Draft":
            raise IllegalTransition("this build has already been started")
        if inspection:
            rec["inspection"] = inspection
            self._log(rec, "Project inspected",
                     f"{len(inspection)} evidence fields from the Safe Server Agent")
        self._generate_prompt(rec, self._roles(rec)[0])
        return self._save(rec)

    def set_worker(self, bid, worker):
        rec = dict(self.get(bid))
        if worker not in WORKERS:
            raise ValueError(f"worker must be one of {tuple(WORKERS)}")
        if rec["currentStep"]["phase"] != "copy":
            raise IllegalTransition("worker can only be changed while the prompt is being copied")
        self._generate_prompt(rec, rec["currentStep"]["role"], worker)
        return self._save(rec)

    def mark_copied(self, bid):
        rec = dict(self.get(bid))
        self._log(rec, "Prompt copied")
        return self._save(rec)

    def mark_opened(self, bid):
        rec = dict(self.get(bid))
        self._log(rec, "Worker opened", WORKERS[rec["currentStep"]["worker"]]["name"])
        return self._save(rec)

    def mark_pasted(self, bid):
        rec = dict(self.get(bid))
        if rec["currentStep"]["phase"] != "copy":
            raise IllegalTransition("there is no prompt waiting to be pasted")
        rec["currentStep"] = dict(rec["currentStep"], phase="paste")
        rec["status"] = self._status_for(rec["currentStep"]["role"], "paste")
        return self._save(rec)

    def save_response(self, bid, raw):
        rec = dict(self.get(bid))
        if rec["currentStep"]["phase"] not in ("copy", "paste"):
            raise IllegalTransition("this build is not waiting on a worker response")
        raw = str(raw or "").strip()
        if not raw:
            raise ValueError("paste the worker's response first")
        outcome, confident = parse_worker_response(raw)
        role, worker = rec["currentStep"]["role"], rec["currentStep"]["worker"]
        rec["responses"] = rec["responses"] + [{"role": role, "worker": worker,
                                                "raw": raw, "outcome": outcome,
                                                "confident": confident,
                                                "created_at": _now()}]
        self._log(rec, "Response pasted", f"{role}: {outcome or 'unclear'}")
        if confident and outcome in ("COMPLETE", "REVIEW_PASS"):
            if role == "reviewer":
                self._log(rec, "Review completed", outcome)
            self._advance(rec)
        else:
            self._human(rec, outcome, confident)
        return self._save(rec)

    def decide(self, bid, choice):
        rec = dict(self.get(bid))
        decision = rec["currentStep"].get("decision")
        if rec["currentStep"]["phase"] != "human" or not decision:
            raise IllegalTransition("this build is not waiting on a human decision")
        if choice not in decision["choices"]:
            raise ValueError(f"choice must be one of {decision['choices']}")
        role = rec["currentStep"]["role"]
        self._log(rec, "Next step chosen", f"Chris chose: {choice}")
        if choice == "retry_step":
            self._generate_prompt(rec, role, rec["currentStep"]["worker"])
        elif choice == "back_to_builder":
            self._generate_prompt(rec, self._roles(rec)[0])
        elif choice == "continue_next":
            self._advance(rec)
        elif choice == "mark_complete":
            self._complete(rec)
        elif choice == "stop":
            rec["status"] = "Error"
            rec["currentStep"] = dict(rec["currentStep"], phase="done", decision=None)
            rec["finalReport"] = build_handoff_final_report(rec, stopped=True)
            self._log(rec, "Build stopped", "stopped by Chris")
        return self._save(rec)

    def note(self, bid, event, detail=""):
        """Timeline entry from the UI — e.g. a Safe Server Agent check the
        browser ran. Text only; Studio executes nothing."""
        rec = dict(self.get(bid))
        self._log(rec, str(event)[:80] or "Note", str(detail)[:400])
        return self._save(rec)


def build_handoff_final_report(build, stopped=False):
    lines = [f"BUILD REPORT — {build['buildName']}",
             f"Project: {build['projectName']} ({build['repoPath'] or 'no repo path'})",
             f"Goal: {build['userGoal']}",
             f"Mode: {build['mode']}   Sequence: {build['workerSequence']}",
             f"Status: {'Stopped by Chris' if stopped else 'Complete'}", "", "STEPS:"]
    for r in build["responses"]:
        lines.append(f"- {r['role']} ({WORKERS[r['worker']]['name']}): {r['outcome'] or 'unclear'}")
    if not build["responses"]:
        lines.append("- (no worker responses were recorded)")
    lines += ["", f"Timeline: {len(build['timeline'])} events",
              "", "NEXT RECOMMENDED STEP:"]
    if stopped:
        lines.append("Review why this was stopped, then start a fresh build when ready.")
    else:
        lines.append("Open the repo and confirm the result yourself (run the app/tests) "
                     "before treating this as shipped.")
    return "\n".join(lines)


def handoff_build_view(build):
    """The build record plus everything the UI needs to render one screen:
    worker display name/URL for the current step and the latest prompt text."""
    v = dict(build)
    step = dict(build["currentStep"])
    role, worker = step.get("role"), step.get("worker")
    if worker:
        step["workerName"] = WORKERS[worker]["name"]
        step["workerUrl"] = WORKERS[worker]["url"]
    step["prompt"] = next((p["text"] for p in reversed(build["prompts"])
                          if p["role"] == role), None)
    v["currentStep"] = step
    v["workers"] = WORKERS
    return v


def list_outcomes(projects, jobs, reports):
    """Every finished build across all projects, for the 'Built Projects /
    Outcomes' page — anything that has cleared the approval gate (or never
    needed one): Approved, Completed, or Archived. Newest first."""
    out = []
    for job in jobs.list():
        if job["status"] not in ("Approved", "Completed", "Archived"):
            continue
        try:
            project = get_project(projects, job["project"])
        except NotFound:
            continue
        recs = reports.list(job_id=job["id"])
        latest = recs[0] if recs else None
        outcome = build_outcome(job, project, latest)
        outcome["open_available"] = bool(outcome["test_url"])
        out.append(outcome)
    return sorted(out, key=lambda o: o["completed_at"], reverse=True)


# ── cross-cutting: needs-chris, project memory, where-are-we, continuity ───────
def needs_chris_items(jobs, reports):
    """Each item carries the job's CURRENT status (job_status) so a caller (the
    dashboard's actionable queue) knows whether Approve/Send-back are legal
    right now, or whether all it can do is jump to the job/report to review."""
    items = []
    for job in jobs.list():
        if job["status"] == "Needs Chris Approval":
            items.append({"project": job["project"], "job_id": job["id"],
                         "job_status": job["status"],
                         "reason": "Job is waiting at Needs Chris Approval",
                         "choices": ["Approve", "Send back for more work"]})
    for r in reports.list():
        blob = " ".join(str(r.get(k, "")) for k in
                        ("raw", "blockers", "status")).lower()
        reasons = []
        if r.get("needs_approval") is True or "needs approval" in blob:
            reasons.append("Latest Claude report requests approval")
        if "blocked" in blob or (r.get("blockers") and
                                 r["blockers"].strip().lower() not in ("", "none")):
            reasons.append("Latest Claude report reports a blocker")
        if "fail" in blob and "tests" in blob:
            reasons.append("Tests failed per latest report")
        if "merge decision" in blob:
            reasons.append("Merge decision needed")
        if "deployment decision" in blob or "deploy decision" in blob:
            reasons.append("Deployment decision needed")
        if not reasons:
            continue
        try:
            job_status = jobs.get(r["job_id"])["status"]
        except NotFound:
            job_status = None
        choices = (["Approve", "Send back for more work"] if job_status == "Needs Chris Approval"
                  else ["Review the report"])
        for reason in reasons:
            items.append({"project": r["project"], "job_id": r["job_id"],
                         "job_status": job_status, "reason": reason, "choices": choices})
    seen, out = set(), []
    for it in items:
        key = (it["project"], it.get("job_id"), it["reason"])
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def build_project_memory(pid, projects, jobs, reports, decisions, risks, notes):
    project = get_project(projects, pid)
    proj_jobs = jobs.list(project=pid)
    completed = [j for j in proj_jobs if j["status"] in ("Completed", "Archived")]
    blocked = [j for j in proj_jobs if j["status"] == "Needs Chris Approval"]
    active = [j for j in proj_jobs if j not in completed and j not in blocked]
    return {
        "project": project,
        "mission": project.get("notes", ""),
        "current_status": project.get("status", ""),
        "completed_work": completed,
        "active_work": active,
        "blocked_work": blocked,
        "architecture_notes": notes.list(project=pid, note_type="architecture_note"),
        "important_decisions": decisions.list(project=pid)[:20],
        "open_risks": risks.list(project=pid, resolved=False),
        "next_action": project.get("next_action", ""),
        "last_10_jobs": proj_jobs[:10],
        "last_10_reports": reports.list(project=pid)[:10],
    }


def build_where_are_we(pid, projects, jobs, reports, decisions, rooms):
    """Generated from the project's FOLDER FILES (not only JSONL) — see
    docs/PROJECT_MODEL.md. Reading real files means this survives a restart:
    even a freshly-loaded set of stores can reproduce it from disk alone."""
    project = get_project(projects, pid)
    proj_jobs = jobs.list(project=pid)
    latest_job = proj_jobs[0] if proj_jobs else None
    chatgpt_plan = _read_project_file(pid, os.path.join("CHATGPT", "Current_Plan.md"), "(nothing yet)")
    decisions_log = _read_project_file(pid, "DECISIONS.md", "(no decisions logged yet)")
    needs = [it["reason"] for it in needs_chris_items(jobs, reports) if it["project"] == pid]
    next_action = _read_project_file(pid, "NEXT_ACTION.md", project.get("next_action", ""))
    proj_rooms = rooms.list(project=pid)
    latest_room = proj_rooms[0] if proj_rooms else None
    council_needed = bool(latest_room and latest_room["status"] == "Council Needed")
    return f"""WHERE ARE WE — {project['name']}

Project: {project['name']} ({project['id']})
Mission: {project.get('notes', '')}
Current status: {project.get('status', '')}
Current goal: {project.get('current_goal') or '(not set)'}
Latest decisions:
{decisions_log}
Latest ChatGPT plan: {chatgpt_plan}
Latest Claude report: {_read_project_file(pid, os.path.join('CLAUDE', 'Current_Report.md'), '(nothing yet)')}
Risks: {_read_project_file(pid, "RISKS.md", "(none logged)")}
Blockers: {latest_job['status'] if latest_job else '(no jobs yet)'}
Council needed: {'yes' if council_needed else 'no'}
Needs Chris: {'; '.join(needs) if needs else '(nothing right now)'}
Next action: {next_action or '(nothing recorded)'}"""


def build_continuity_packet(pid, projects, jobs, reports, decisions, risks, rooms):
    """Generated from the project's folder files (PROJECT_STATE.md, DECISIONS.md,
    RISKS.md, CHATGPT/Current_Plan.md, CLAUDE/Current_Report.md), not only JSONL."""
    project = get_project(projects, pid)
    proj_rooms = rooms.list(project=pid)
    latest_room = proj_rooms[0] if proj_rooms else None
    proj_jobs = jobs.list(project=pid)
    latest_job = proj_jobs[0] if proj_jobs else None
    current_plan = (latest_room["unified_plan"] if latest_room and latest_room["unified_plan"]
                    else (latest_job["description"] if latest_job else "(no plan yet)"))
    decision_lines = _read_project_file(pid, "DECISIONS.md", "(none logged)")
    risk_lines = _read_project_file(pid, "RISKS.md", "(none open)")
    latest_claude_report = _read_project_file(pid, os.path.join("CLAUDE", "Current_Report.md"),
                                              "(no reports yet)")
    next_action = _read_project_file(pid, "NEXT_ACTION.md", project.get("next_action", ""))
    return f"""PROJECT CONTINUITY PACKET — {project['name']}
(Paste this at the top of a new ChatGPT or Claude chat to restore context.)

MISSION: {project.get('notes', '')}
CURRENT STATUS: {project.get('status', '')}

RECENT DECISIONS:
{decision_lines}

CURRENT PLAN:
{current_plan}

OPEN RISKS:
{risk_lines}

NEXT ACTION: {next_action}

LATEST CLAUDE CODE REPORT:
{latest_claude_report}"""


def build_start_new_chat_packet(pid, projects, jobs, reports, decisions, risks, rooms):
    """The stronger continuity packet: who Chris is, what the project is,
    current state, architecture, completed/active work, decisions, current
    disagreement/risks, what each AI should do next, and what NOT to work on.
    Also folder-file-sourced, for the same restart-survival reason."""
    project = get_project(projects, pid)
    architecture = _read_project_file(pid, "ARCHITECTURE.md", "(not yet documented)")
    proj_jobs = jobs.list(project=pid)
    completed = [j for j in proj_jobs if j["status"] in ("Completed", "Archived")]
    active = [j for j in proj_jobs
             if j["status"] not in ("Completed", "Archived", "Needs Chris Approval")]
    decisions_log = _read_project_file(pid, "DECISIONS.md", "(none logged)")
    risks_log = _read_project_file(pid, "RISKS.md", "(none logged)")
    proj_rooms = rooms.list(project=pid)
    latest_room = proj_rooms[0] if proj_rooms else None
    disagreement = (latest_room.get("disagreements") if latest_room else "") or "(none recorded)"
    next_action = _read_project_file(pid, "NEXT_ACTION.md", project.get("next_action", ""))
    other_hands_off = sorted(p["name"] for p in projects.values()
                             if p.get("hands_off") and p["id"] != pid)
    not_to_work_on = ((f"Do not touch: {', '.join(other_hands_off)}. " if other_hands_off else "")
                      + f"Stay scoped to {project['name']}'s own repo "
                      f"({project.get('repo_path') or 'not set'}); do not deploy, restart "
                      f"services, or enable live mode.")
    completed_lines = "\n".join(f"- {j['title']}" for j in completed) or "(none yet)"
    active_lines = "\n".join(f"- [{j['status']}] {j['title']}" for j in active) or "(none)"
    return f"""START NEW CHAT PACKET — {project['name']}
(Paste this at the very top of a brand-new ChatGPT or Claude chat.)

WHO CHRIS IS: {STUDIO_WHO_IS_CHRIS}

WHAT THE PROJECT IS: {project.get('notes', '')}

CURRENT STATE: {project.get('status', '')} — {project.get('current_goal') or 'no current goal set'}

ARCHITECTURE:
{architecture}

COMPLETED WORK:
{completed_lines}

ACTIVE WORK:
{active_lines}

IMPORTANT DECISIONS:
{decisions_log}

CURRENT DISAGREEMENT / RISKS:
{disagreement}
{risks_log}

WHAT CHATGPT SHOULD DO NEXT: {next_action or '(not set)'}
WHAT CLAUDE SHOULD DO NEXT: {next_action or '(not set)'}

WHAT NOT TO WORK ON: {not_to_work_on}"""


def render_project_state_markdown(pid, projects, jobs, reports, decisions, risks, rooms):
    """Regenerates PROJECT_STATE.md's full content — the canonical, always-fresh
    per-project memory file. Pure/deterministic given current state; call
    sync_project_state() to also write it to disk."""
    project = get_project(projects, pid)
    proj_jobs = jobs.list(project=pid)
    completed = [j for j in proj_jobs if j["status"] in ("Completed", "Archived")]
    blocked = [j for j in proj_jobs if j["status"] == "Needs Chris Approval"]
    active = [j for j in proj_jobs if j not in completed and j not in blocked]
    open_risks = risks.list(project=pid, resolved=False)
    needs = [it["reason"] for it in needs_chris_items(jobs, reports) if it["project"] == pid]
    proj_rooms = rooms.list(project=pid)
    latest_room = proj_rooms[0] if proj_rooms else None
    chatgpt_plan = _read_project_file(pid, os.path.join("CHATGPT", "Current_Plan.md"), "(none yet)")
    claude_report = _read_project_file(pid, os.path.join("CLAUDE", "Current_Report.md"), "(none yet)")
    council_notes = _read_project_file(pid, os.path.join("COUNCIL", "Latest.md"), "(none yet)")
    next_action = _read_project_file(pid, "NEXT_ACTION.md", project.get("next_action", ""))
    open_questions = (latest_room.get("disagreements") if latest_room else "") or "(none recorded)"

    lines = [f"# {project['name']}", "",
            "## Mission", project.get("notes", ""), "",
            "## Current Status", project.get("status", ""), "",
            "## Current Goal", project.get("current_goal") or "(not set)", "",
            "## Current Sprint", "(not tracked yet)", "",
            "## Current Architecture", "(see ARCHITECTURE.md)", "",
            "## Completed Work"]
    lines += [f"- {j['title']}" for j in completed] or ["(none yet)"]
    lines += ["", "## In Progress"]
    lines += [f"- [{j['status']}] {j['title']}" for j in active] or ["(none)"]
    lines += ["", "## Blocked"]
    lines += [f"- {j['title']}" for j in blocked] or ["(none)"]
    lines += ["", "## Open Questions", open_questions, "",
             "## Latest ChatGPT Plan", chatgpt_plan, "",
             "## Latest Claude Report", claude_report, "",
             "## Latest Council Notes", council_notes, "",
             "## Active PRs", project.get("open_pr") or "(none)", "",
             "## Risks"]
    lines += [f"- {r['description']}" for r in open_risks] or ["(none open)"]
    lines += ["", "## Needs Chris"]
    lines += [f"- {r}" for r in needs] or ["(nothing right now)"]
    lines += ["", "## Next Action", next_action or "(not set)", "",
             "## Last Updated", _now()]
    return "\n".join(lines)


def sync_project_state(pid, projects, jobs, reports, decisions, risks, rooms):
    """Regenerate and write PROJECT_STATE.md for one project. Called after any
    mutation that could change the picture (job status, a new plan/report/
    council note, a decision, a risk, a next-action update)."""
    content = render_project_state_markdown(pid, projects, jobs, reports, decisions, risks, rooms)
    _write_project_file(pid, "PROJECT_STATE.md", content)
    return content


# ── search: plain substring search across every artifact ──────────────────────
def _snippet(content, q, window=80):
    idx = content.lower().find(q.lower())
    if idx == -1:
        return content[:window]
    start, end = max(0, idx - window // 2), min(len(content), idx + len(q) + window // 2)
    prefix, suffix = ("…" if start > 0 else ""), ("…" if end < len(content) else "")
    return prefix + content[start:end].strip() + suffix


PROJECT_SEARCHABLE_FILES = ("MISSION.md", "ARCHITECTURE.md", "ROADMAP.md",
                           "DECISIONS.md", "RISKS.md", "NEXT_ACTION.md")


def search_studio(query, projects, jobs, reports, decisions, risks, notes, rooms, project=None):
    """Case-insensitive substring search across jobs, notes, decisions, risks,
    reports, planning rooms, and the hand-edited project files. No indexing —
    this is a coordination tool, not a search engine; a linear scan is plenty."""
    q = str(query or "").strip().lower()
    empty = {"jobs": [], "notes": [], "decisions": [], "risks": [], "reports": [],
            "rooms": [], "files": []}
    if not q:
        return empty

    def hit(*vals):
        return any(q in str(v or "").lower() for v in vals)

    def scoped(items_project):
        return project is None or items_project == project

    job_hits = [j for j in jobs.list() if scoped(j["project"])
               and hit(j["title"], j["description"], j["constraints"], j["safety_notes"])]
    note_hits = [n for n in notes.list() if scoped(n["project"])
                and hit(n.get("content"), n.get("plan_summary"), n.get("recommended_prompt"),
                       n.get("next_step"))]
    decision_hits = [d for d in decisions.list() if scoped(d["project"]) and hit(d["text"])]
    risk_hits = [r for r in risks.list() if scoped(r["project"]) and hit(r["description"])]
    report_hits = [r for r in reports.list() if scoped(r["project"]) and hit(r.get("raw"))]
    room_hits = [rm for rm in rooms.list() if scoped(rm["project"])
                and hit(rm.get("chris_idea"), rm.get("chatgpt_response"), rm.get("claude_response"),
                       rm.get("disagreements"), rm.get("unified_plan"),
                       " ".join(c.get("content", "") for c in rm.get("council_responses", [])))]

    file_hits = []
    search_pids = [project] if project else list(projects.keys())
    for pid in search_pids:
        for fname in PROJECT_SEARCHABLE_FILES:
            content = _read_project_file(pid, fname, "")
            if content and q in content.lower():
                file_hits.append({"project": pid, "file": fname, "snippet": _snippet(content, q)})

    return {"jobs": job_hits, "notes": note_hits, "decisions": decision_hits,
           "risks": risk_hits, "reports": report_hits, "rooms": room_hits, "files": file_hits}


# ── timeline: one chronological feed per project ───────────────────────────────
def _parse_iso(ts):
    import calendar
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def _epoch_to_iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def build_timeline(pid, jobs, reports, decisions, risks, rooms, notes):
    """Merges job/room status history, decisions, risks, reports, and saved
    prompts into one chronological (newest-first) feed for a project — sourced
    from data already logged elsewhere, not a new store of its own."""
    events = []
    for j in jobs.list(project=pid, include_deleted=True):
        for h in j["history"]:
            summary = f"Job “{j['title']}” → {h['status']}"
            if h.get("rejected"):
                summary += f" (sent back: {h.get('reason', '')})" if h.get("reason") else " (sent back)"
            events.append({"at": _parse_iso(h["at"]), "at_iso": h["at"], "kind": "job_status",
                          "summary": summary, "job_id": j["id"]})
    for r in rooms.list(project=pid):
        for h in r["history"]:
            summary = f"Planning room → {h['status']}"
            if h.get("emergency_skip"):
                summary += f" (emergency skip: {h.get('reason', '')})"
            events.append({"at": _parse_iso(h["at"]), "at_iso": h["at"], "kind": "planning_room",
                          "summary": summary, "room_id": r["id"]})
    for d in decisions.list(project=pid):
        events.append({"at": d["created_at"], "at_iso": _epoch_to_iso(d["created_at"]),
                      "kind": "decision", "summary": f"Decision: {d['text']}"})
    for rk in risks.list(project=pid):
        events.append({"at": rk["created_at"], "at_iso": _epoch_to_iso(rk["created_at"]),
                      "kind": "risk", "summary": f"Risk logged: {rk['description']}"})
    for rep in reports.list(project=pid):
        events.append({"at": rep["created_at"], "at_iso": _epoch_to_iso(rep["created_at"]),
                      "kind": "claude_report",
                      "summary": f"Claude report pasted (status: {rep.get('status') or 'unparsed'})",
                      "job_id": rep.get("job_id")})
    for n in notes.list(project=pid):
        if n["note_type"] in ("chatgpt_plan", "chatgpt_plan_response", "claude_code_prompt"):
            label = n["note_type"].replace("_", " ").title()
            events.append({"at": n["created_at"], "at_iso": _epoch_to_iso(n["created_at"]),
                          "kind": n["note_type"], "summary": f"{label} saved",
                          "job_id": n.get("job_id")})
    events.sort(key=lambda e: e["at"], reverse=True)
    return events


# ── AI inbox: cross-project view of everything waiting on an exchange ─────────
_JOB_INBOX_EXPECTATIONS = {
    "Ready for ChatGPT": "Copy the ChatGPT prompt and send it",
    "ChatGPT Planned": "Copy the Claude Code build prompt, or continue planning",
    "Ready for Claude": "Copy the Claude Code build prompt and send it",
    "Sent to Claude": "Waiting on Claude Code — mark Building once it starts",
    "Building": "Waiting on Claude Code to finish and report back",
}
_ROOM_INBOX_EXPECTATIONS = {
    "Idea": "Copy the ChatGPT prompt and paste the response back",
    "ChatGPT Reviewed": "Copy the Claude prompt and paste the response back",
    "Claude Reviewed": "Optional council round, or generate the unified plan",
    "Council Needed": "Paste council/other-AI responses, then generate the unified plan",
}


def build_ai_inbox(projects, jobs, rooms):
    items = []
    for j in jobs.list():
        if j["status"] in _JOB_INBOX_EXPECTATIONS:
            items.append({"kind": "job", "project": j["project"], "id": j["id"],
                         "title": j["title"], "status": j["status"],
                         "expected": _JOB_INBOX_EXPECTATIONS[j["status"]]})
    for r in rooms.list():
        if r["status"] in _ROOM_INBOX_EXPECTATIONS:
            items.append({"kind": "planning_room", "project": r["project"], "id": r["id"],
                         "title": r["chris_idea"][:80], "status": r["status"],
                         "expected": _ROOM_INBOX_EXPECTATIONS[r["status"]]})
    items.sort(key=lambda it: (it["project"], it["status"]))
    return items


# ── "Continue Project" prompt: Claude-Code-flavored resume, not a planning chat ─
def build_continue_project_prompt(pid, projects, jobs, reports, rooms):
    """Distinct from Start New Chat Packet (aimed at ChatGPT/Claude planning
    conversations): this one is aimed squarely at Claude Code, to resume
    unfinished execution after days/weeks away."""
    project = get_project(projects, pid)
    proj_jobs = jobs.list(project=pid)
    latest_job = proj_jobs[0] if proj_jobs else None
    latest_report = next(iter(reports.list(project=pid)), None)
    rails = "\n".join(f"- {r}" for r in SAFETY_RAILS)
    state = _read_project_file(pid, "PROJECT_STATE.md", "(not generated yet)")
    job_block = (f"CURRENT JOB: {latest_job['title']} (status: {latest_job['status']})\n"
                f"{latest_job['description']}" if latest_job
                else "No open job — check PROJECT_STATE.md's Next Action.")
    report_block = latest_report["raw"] if latest_report else "(no prior report)"
    return f"""Use Claude Code. Continue work on {project['name']} from where the last
session left off — do not restart from scratch.

REPO PATH: {project.get('repo_path') or '(not set)'}

PROJECT STATE:
{state}

{job_block}

LAST CLAUDE REPORT:
{report_block}

SAFETY RAILS (non-negotiable)
{rails}

Re-read the project state above before making any changes. Resume from here.
Reply using the same final status format as before when you're done."""


# ── founder report (pure data + markdown; this module never shells out) ───────
def build_founder_report_data(projects, jobs, reports, decisions, risks, studio_state, rooms=None):
    active_pid = studio_state.get("active_project")
    active_folder = project_dir(active_pid) if active_pid else None
    return {
        "inbox": build_ai_inbox(projects, jobs, rooms) if rooms is not None else [],
        "active_project": projects.get(active_pid) if active_pid else None,
        "active_project_folder": active_folder,
        "project_state": (_read_project_file(active_pid, "PROJECT_STATE.md", "(not generated yet)")
                          if active_pid else None),
        "latest_chatgpt_plan_file": (_read_project_file(active_pid, os.path.join("CHATGPT", "Current_Plan.md"), "(none yet)")
                                     if active_pid else None),
        "latest_claude_report_file": (_read_project_file(active_pid, os.path.join("CLAUDE", "Current_Report.md"), "(none yet)")
                                      if active_pid else None),
        "latest_council_notes_file": (_read_project_file(active_pid, os.path.join("COUNCIL", "Latest.md"), "(none yet)")
                                      if active_pid else None),
        "active_jobs": [j for j in jobs.list() if j["status"] not in ("Completed", "Archived")],
        "needs_chris": needs_chris_items(jobs, reports),
        "latest_reports": reports.list()[:5],
        "latest_decisions": decisions.list()[:10],
        "open_risks": risks.list(resolved=False),
        "projects": list(projects.values()),
    }


def _recommend_next_action(data):
    if data["needs_chris"]:
        it = data["needs_chris"][0]
        return f"Review {it['project']} — {it['reason']}."
    testing = [j for j in data["active_jobs"] if j["status"] == "Testing"]
    if testing:
        return f"Waiting on Claude for '{testing[0]['title']}' ({testing[0]['project']})."
    drafts = [j for j in data["active_jobs"] if j["status"] == "Draft"]
    if drafts:
        return f"Generate a ChatGPT plan prompt for '{drafts[0]['title']}' ({drafts[0]['project']})."
    return "No urgent action — pick a project and describe what you want built."


def render_founder_report_markdown(data):
    lines = ["# Founder Report", "", f"_Generated {_now()}_", ""]
    lines += ["## Active Project",
             (f"**{data['active_project']['name']}**" if data["active_project"]
              else "(no active project set)"), ""]
    lines += ["## Active Project Folder",
             f"`{data['active_project_folder']}`" if data["active_project_folder"]
             else "(no active project set)", ""]
    lines += ["## Current Project State"]
    lines += [data["project_state"] or "(no active project set)", ""]
    lines += ["## Active Jobs"]
    lines += [f"- [{j['status']}] {j['title']} ({j['project']})" for j in data["active_jobs"]] or ["(none)"]
    lines += ["", "## AI Inbox"]
    lines += [f"- [{it['kind']}] {it['project']}: {it['title']} ({it['status']}) — {it['expected']}"
             for it in data["inbox"]] or ["(nothing waiting on an AI exchange)"]
    lines += ["", "## Needs Chris Approval"]
    lines += [f"- {it['project']}: {it['reason']}" for it in data["needs_chris"]] or ["(nothing needs Chris right now)"]
    lines += ["", "## Latest ChatGPT Plan"]
    lines += [data["latest_chatgpt_plan_file"] or "(no active project set)"]
    lines += ["", "## Latest Claude Report"]
    lines += [data["latest_claude_report_file"] or (data["latest_reports"][0]["raw"] if data["latest_reports"] else "(none yet)")]
    lines += ["", "## Latest Council Notes"]
    lines += [data["latest_council_notes_file"] or "(no active project set)"]
    lines += ["", "## Latest Decisions"]
    lines += [f"- {d['project']}: {d['text']}" for d in data["latest_decisions"]] or ["(none logged)"]
    lines += ["", "## Open Risks"]
    lines += [f"- [{r['severity']}] {r['project']}: {r['description']}" for r in data["open_risks"]] or ["(none open)"]
    lines += ["", "## Next Recommended Action", _recommend_next_action(data), ""]
    lines += ["## Known Project Repo Paths"]
    lines += [f"- {p['name']}: `{p.get('repo_path') or '(not set)'}`" for p in data["projects"]]
    lines += ["", "_Git status of the Studio repo is appended by scripts/founder-report.sh._"]
    return "\n".join(lines)


def render_current_status_markdown(data):
    ap = data["active_project"]
    lines = ["# Current Status", "",
            f"Active project: {ap['name'] if ap else '(none set)'}", "",
            "## Active Jobs"]
    lines += [f"- [{j['status']}] {j['title']} ({j['project']})" for j in data["active_jobs"]] or ["(none)"]
    return "\n".join(lines)


def render_next_action_markdown(data):
    return "\n".join(["# Next Action", "", _recommend_next_action(data)])


def render_waiting_on_chris_markdown(data):
    lines = ["# Waiting on Chris", ""]
    lines += [f"- {it['project']}: {it['reason']} (choices: {', '.join(it['choices'])})"
             for it in data["needs_chris"]] or ["(nothing waiting on Chris)"]
    return "\n".join(lines)


def render_risks_markdown(data):
    lines = ["# Risks", ""]
    lines += [f"- [{r['severity']}] {r['project']}: {r['description']}"
             for r in data["open_risks"]] or ["(no open risks)"]
    return "\n".join(lines)


def write_all_reports(projects, jobs, reports, decisions, risks, studio_state, rooms=None):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    data = build_founder_report_data(projects, jobs, reports, decisions, risks, studio_state, rooms)
    files = {
        "FOUNDER_REPORT.md": render_founder_report_markdown(data),
        "CURRENT_STATUS.md": render_current_status_markdown(data),
        "NEXT_ACTION.md": render_next_action_markdown(data),
        "WAITING_ON_CHRIS.md": render_waiting_on_chris_markdown(data),
        "RISKS.md": render_risks_markdown(data),
    }
    for name, content in files.items():
        with open(os.path.join(REPORTS_DIR, name), "w") as f:
            f.write(content + "\n")
    return files


# ── HTTP surface ─────────────────────────────────────────────────────────────────
_UUID_RE = r"[a-f0-9]{32}"
_PID_RE = r"[a-z0-9][a-z0-9-]*"


class Handler(BaseHTTPRequestHandler):
    server_version = "GNGDevelopmentStudio/1.0"
    projects = {}
    jobs = None
    reports = None
    decisions = None
    risks = None
    notes = None
    rooms = None

    def _send(self, code, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode() or "{}")
        except ValueError:
            return {}

    def log_message(self, fmt, *args):
        pass

    def _with_next(self, job):
        return dict(job, allowed_next=JobStore.allowed_next(job))

    def _room_with_next(self, room):
        return dict(room, allowed_next=PlanningRoomStore.allowed_next(room),
                   can_generate_build_prompt=PlanningRoomStore.can_generate_build_prompt(room))

    def _sync(self, pid):
        """Regenerate PROJECT_STATE.md for one project after a mutation."""
        sync_project_state(pid, self.projects, self.jobs, self.reports,
                           self.decisions, self.risks, self.rooms)

    def _guided_room(self, pid):
        rooms = self.rooms.list(project=pid)
        if not rooms:
            raise NotFound(f"no guided build has been started for project: {pid}")
        return rooms[0]

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if path == "/health":
                return self._send(200, {"ok": True, "service": "gng-development-studio",
                                        "mode": MODE, "port": PORT,
                                        "projects": len(self.projects)})
            if path == "/api/projects":
                return self._send(200, {"projects": list(self.projects.values()),
                                        "safety_rails": SAFETY_RAILS,
                                        "job_lifecycle": JOB_STAGES,
                                        "planning_statuses": PLANNING_STATUSES})
            if path == "/api/jobs":
                jobs = self.jobs.list(project=qs.get("project"))
                return self._send(200, {"jobs": [self._with_next(j) for j in jobs]})
            m = re.match(rf"^/api/jobs/({_UUID_RE})$", path)
            if m:
                return self._send(200, self._with_next(self.jobs.get(m.group(1))))
            m = re.match(rf"^/api/project/({_PID_RE})/memory$", path)
            if m:
                mem = build_project_memory(m.group(1), self.projects, self.jobs, self.reports,
                                           self.decisions, self.risks, self.notes)
                return self._send(200, mem)
            m = re.match(rf"^/api/project/({_PID_RE})/where-are-we$", path)
            if m:
                text = build_where_are_we(m.group(1), self.projects, self.jobs, self.reports,
                                          self.decisions, self.rooms)
                return self._send(200, {"text": text})
            m = re.match(rf"^/api/project/({_PID_RE})/continuity-packet$", path)
            if m:
                text = build_continuity_packet(m.group(1), self.projects, self.jobs, self.reports,
                                               self.decisions, self.risks, self.rooms)
                return self._send(200, {"text": text})
            m = re.match(rf"^/api/project/({_PID_RE})/start-new-chat-packet$", path)
            if m:
                text = build_start_new_chat_packet(m.group(1), self.projects, self.jobs,
                                                   self.reports, self.decisions, self.risks,
                                                   self.rooms)
                return self._send(200, {"text": text})
            m = re.match(rf"^/api/project/({_PID_RE})/state$", path)
            if m:
                get_project(self.projects, m.group(1))
                content = _read_project_file(m.group(1), "PROJECT_STATE.md", "(not generated yet)")
                return self._send(200, {"content": content})
            m = re.match(rf"^/api/project/({_PID_RE})/file$", path)
            if m:
                get_project(self.projects, m.group(1))
                name = qs.get("name", "")
                if name not in ("MISSION.md", "ARCHITECTURE.md", "ROADMAP.md", "DECISIONS.md",
                                "RISKS.md", "NEXT_ACTION.md", "PROJECT_STATE.md"):
                    return self._send(400, {"error": f"unknown or unreadable file: {name}"})
                content = _read_project_file(m.group(1), name, "")
                return self._send(200, {"name": name, "content": content})
            if path == "/api/founder-report":
                studio_state = load_studio_state()
                data = build_founder_report_data(self.projects, self.jobs, self.reports,
                                                 self.decisions, self.risks, studio_state,
                                                 self.rooms)
                return self._send(200, data)
            if path == "/api/needs-chris":
                return self._send(200, {"items": needs_chris_items(self.jobs, self.reports)})
            if path == "/api/search":
                results = search_studio(qs.get("q", ""), self.projects, self.jobs, self.reports,
                                        self.decisions, self.risks, self.notes, self.rooms,
                                        project=qs.get("project"))
                return self._send(200, results)
            if path == "/api/inbox":
                return self._send(200, {"items": build_ai_inbox(self.projects, self.jobs, self.rooms)})
            m = re.match(rf"^/api/guided/({_PID_RE})/stage$", path)
            if m:
                stage = compute_guided_stage(m.group(1), self.projects, self.jobs, self.rooms,
                                             self.reports)
                return self._send(200, stage)
            if path == "/api/outcomes":
                return self._send(200, {"outcomes": list_outcomes(self.projects, self.jobs,
                                                                   self.reports)})
            if path == "/api/builds":
                return self._send(200, {"builds": [handoff_build_view(b)
                                                   for b in self.builds.list()]})
            m = re.match(rf"^/api/builds/({_UUID_RE})$", path)
            if m:
                return self._send(200, handoff_build_view(self.builds.get(m.group(1))))
            m = re.match(rf"^/api/project/({_PID_RE})/timeline$", path)
            if m:
                get_project(self.projects, m.group(1))
                events = build_timeline(m.group(1), self.jobs, self.reports, self.decisions,
                                        self.risks, self.rooms, self.notes)
                return self._send(200, {"events": events})
            m = re.match(rf"^/api/project/({_PID_RE})/continue-prompt$", path)
            if m:
                text = build_continue_project_prompt(m.group(1), self.projects, self.jobs,
                                                     self.reports, self.rooms)
                return self._send(200, {"text": text})
            if path == "/api/notes":
                notes = self.notes.list(project=qs.get("project"), job_id=qs.get("job"),
                                        note_type=qs.get("type"))
                return self._send(200, {"notes": notes})
            if path == "/api/decisions":
                return self._send(200, {"decisions": self.decisions.list(project=qs.get("project"))})
            if path == "/api/risks":
                resolved = None
                if qs.get("resolved") in ("1", "true"):
                    resolved = True
                elif qs.get("resolved") in ("0", "false"):
                    resolved = False
                return self._send(200, {"risks": self.risks.list(project=qs.get("project"),
                                                                 resolved=resolved)})
            if path == "/api/planning-rooms":
                rooms = self.rooms.list(project=qs.get("project"))
                return self._send(200, {"rooms": [self._room_with_next(r) for r in rooms]})
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})$", path)
            if m:
                return self._send(200, self._room_with_next(self.rooms.get(m.group(1))))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/chatgpt-prompt$", path)
            if m:
                room = self.rooms.get(m.group(1))
                project = get_project(self.projects, room["project"])
                return self._send(200, {"prompt": self.rooms.build_chatgpt_prompt(m.group(1), project)})
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/claude-prompt$", path)
            if m:
                room = self.rooms.get(m.group(1))
                project = get_project(self.projects, room["project"])
                return self._send(200, {"prompt": self.rooms.build_claude_prompt(m.group(1), project)})
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/council-prompt$", path)
            if m:
                room = self.rooms.get(m.group(1))
                project = get_project(self.projects, room["project"])
                return self._send(200, {"prompt": self.rooms.build_council_prompt(m.group(1), project)})
            # The front door is the Builds page — "What would you like to do
            # today?" — per the UX rule that Chris answers WHAT and Studio
            # figures out where/how/who. The guided flow stays at /guided.
            if path in ("/", "/index.html", "/handoff", "/handoff.html", "/builds"):
                try:
                    with open(HANDOFF_PATH, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                except OSError:
                    return self._send(404, {"error": "handoff page missing"})
            if path in ("/guided", "/guided.html"):
                try:
                    with open(GUIDED_PATH, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                except OSError:
                    return self._send(404, {"error": "guided.html missing"})
            if path in ("/advanced", "/advanced.html", "/dashboard", "/dashboard.html"):
                try:
                    with open(DASHBOARD_PATH, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                except OSError:
                    return self._send(404, {"error": "dashboard missing"})
            if path in ("/outcomes", "/outcomes.html"):
                try:
                    with open(OUTCOMES_PATH, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                except OSError:
                    return self._send(404, {"error": "outcomes page missing"})
            return self._send(404, {"error": "not found"})
        except NotFound as e:
            return self._send(404, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e)[:400]})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/projects":
                b = self._body()
                project = create_project(self.projects, name=b.get("name", ""),
                                         description=b.get("description", ""),
                                         kind=b.get("kind", "existing"),
                                         local_folder=b.get("local_folder", ""),
                                         repo_url=b.get("repo_url", ""),
                                         ptype=b.get("type", "tool"))
                return self._send(201, project)
            if path == "/api/jobs":
                b = self._body()
                job = self.jobs.create(project=b.get("project", ""), title=b.get("title", ""),
                                       description=b.get("description", ""),
                                       priority=b.get("priority", "normal"),
                                       constraints=b.get("constraints", ""),
                                       approval_required=bool(b.get("approval_required", True)),
                                       safety_notes=b.get("safety_notes", ""))
                self._sync(job["project"])
                return self._send(201, self._with_next(job))
            m = re.match(rf"^/api/jobs/({_UUID_RE})/status$", path)
            if m:
                job = self.jobs.advance(m.group(1), str(self._body().get("status", "")))
                self._sync(job["project"])
                return self._send(200, self._with_next(job))
            m = re.match(rf"^/api/jobs/({_UUID_RE})/delete$", path)
            if m:
                job = self.jobs.delete(m.group(1))
                return self._send(200, self._with_next(job))
            m = re.match(rf"^/api/jobs/({_UUID_RE})/reject$", path)
            if m:
                job = self.jobs.reject(m.group(1), self._body().get("reason", ""))
                self._sync(job["project"])
                return self._send(200, self._with_next(job))
            m = re.match(rf"^/api/jobs/({_UUID_RE})/generate-chatgpt-prompt$", path)
            if m:
                jid = m.group(1)
                job = _auto_walk_forward(self.jobs, jid, "Ready for ChatGPT")
                project = get_project(self.projects, job["project"])
                prompt = build_chatgpt_planning_prompt(job, project)
                self.notes.create(project=job["project"], job_id=jid,
                                  note_type="chatgpt_plan", content=prompt)
                self._sync(job["project"])
                return self._send(200, {"job": self._with_next(job), "prompt": prompt})
            m = re.match(rf"^/api/jobs/({_UUID_RE})/generate-claude-prompt$", path)
            if m:
                jid = m.group(1)
                job = _auto_walk_forward(self.jobs, jid, "Ready for Claude")
                project = get_project(self.projects, job["project"])
                prompt = build_claude_code_build_prompt(job, project)
                self.notes.create(project=job["project"], job_id=jid,
                                  note_type="claude_code_prompt", content=prompt)
                self._sync(job["project"])
                return self._send(200, {"job": self._with_next(job), "prompt": prompt})
            m = re.match(rf"^/api/jobs/({_UUID_RE})/chatgpt-plan$", path)
            if m:
                jid = m.group(1)
                job = self.jobs.get(jid)
                b = self._body()
                note = self.notes.create(project=job["project"], job_id=jid,
                                         note_type="chatgpt_plan_response",
                                         content=b.get("plan_summary", ""),
                                         plan_summary=b.get("plan_summary", ""),
                                         recommended_prompt=b.get("recommended_prompt", ""),
                                         risks=b.get("risks", ""), decisions=b.get("decisions", ""),
                                         next_step=b.get("next_step", ""))
                formatted = (f"## ChatGPT Plan Response\n\nSummary: {b.get('plan_summary', '')}\n\n"
                            f"Recommended prompt: {b.get('recommended_prompt', '')}\n\n"
                            f"Risks: {b.get('risks', '')}\n\nDecisions: {b.get('decisions', '')}\n\n"
                            f"Next step: {b.get('next_step', '')}")
                save_chatgpt_plan_to_folder(job["project"], formatted)
                if b.get("risks"):
                    self.risks.create(job["project"], b["risks"], job_id=jid,
                                      source="chatgpt_plan_ingestion")
                if b.get("decisions"):
                    self.decisions.create(job["project"], b["decisions"], job_id=jid,
                                          source="chatgpt_plan_ingestion")
                self._sync(job["project"])
                return self._send(201, note)
            m = re.match(rf"^/api/jobs/({_UUID_RE})/claude-report$", path)
            if m:
                jid = m.group(1)
                job = self.jobs.get(jid)
                b = self._body()
                rec = self.reports.ingest(jid, job["project"], b.get("raw", ""),
                                          manual=b.get("manual"))
                self._sync(job["project"])
                return self._send(201, rec)
            if path == "/api/notes":
                b = self._body()
                note = self.notes.create(project=b.get("project", ""),
                                         note_type=b.get("note_type", ""),
                                         content=b.get("content", ""), job_id=b.get("job_id"),
                                         pinned=bool(b.get("pinned", False)))
                return self._send(201, note)
            m = re.match(rf"^/api/notes/({_UUID_RE})/pin$", path)
            if m:
                return self._send(200, self.notes.pin(m.group(1), bool(self._body().get("pinned", True))))
            if path == "/api/decisions":
                b = self._body()
                d = self.decisions.create(project=b.get("project", ""), text=b.get("text", ""),
                                          job_id=b.get("job_id"))
                self._sync(d["project"])
                return self._send(201, d)
            if path == "/api/risks":
                b = self._body()
                r = self.risks.create(project=b.get("project", ""),
                                      description=b.get("description", ""),
                                      severity=b.get("severity", "normal"), job_id=b.get("job_id"))
                self._sync(r["project"])
                return self._send(201, r)
            m = re.match(rf"^/api/risks/({_UUID_RE})/resolve$", path)
            if m:
                r = self.risks.resolve(m.group(1))
                self._sync(r["project"])
                return self._send(200, r)
            m = re.match(rf"^/api/project/({_PID_RE})/active$", path)
            if m:
                return self._send(200, set_active_project(m.group(1)))
            m = re.match(rf"^/api/project/({_PID_RE})/next-action$", path)
            if m:
                pid = m.group(1)
                text = str(self._body().get("text", ""))
                update_project(self.projects, pid, next_action=text)
                write_next_action_to_folder(pid, text)
                self._sync(pid)
                return self._send(200, {"project": pid, "next_action": text})
            m = re.match(rf"^/api/project/({_PID_RE})/file$", path)
            if m:
                pid = m.group(1)
                get_project(self.projects, pid)
                b = self._body()
                name = b.get("name", "")
                if name not in ("MISSION.md", "ARCHITECTURE.md", "ROADMAP.md"):
                    return self._send(400, {"error": f"{name} is not directly writable via this "
                                                     f"endpoint (append-only or auto-generated files "
                                                     f"have their own routes)"})
                _write_project_file(pid, name, str(b.get("content", "")))
                return self._send(200, {"project": pid, "name": name})
            if path == "/api/planning-rooms":
                b = self._body()
                room = self.rooms.create(project=b.get("project", ""),
                                         chris_idea=b.get("chris_idea", ""))
                return self._send(201, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/chatgpt-response$", path)
            if m:
                room = self.rooms.paste_chatgpt_response(m.group(1), self._body().get("text", ""))
                self._sync(room["project"])
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/claude-response$", path)
            if m:
                room = self.rooms.paste_claude_response(m.group(1), self._body().get("text", ""))
                self._sync(room["project"])
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/council-response$", path)
            if m:
                b = self._body()
                room = self.rooms.paste_council_response(m.group(1), b.get("author", ""),
                                                          b.get("text", ""))
                self._sync(room["project"])
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/disagreements$", path)
            if m:
                b = self._body()
                room = self.rooms.set_disagreements_and_risks(m.group(1), b.get("disagreements"),
                                                              b.get("risks"))
                self._sync(room["project"])
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/unified-plan$", path)
            if m:
                room = self.rooms.generate_unified_plan_draft(m.group(1), self._body().get("text", ""))
                self._sync(room["project"])
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/needs-signoff$", path)
            if m:
                room = self.rooms.mark_needs_signoff(m.group(1))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/chris-approved$", path)
            if m:
                room = self.rooms.chris_approved(m.group(1), self._body().get("note", ""))
                self._sync(room["project"])
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/emergency-skip$", path)
            if m:
                room = self.rooms.emergency_skip(m.group(1), self._body().get("reason", ""))
                self._sync(room["project"])
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/build-prompt$", path)
            if m:
                room = self.rooms.get(m.group(1))
                project = get_project(self.projects, room["project"])
                job, room = self.rooms.generate_claude_code_build_prompt(m.group(1), room["project"])
                prompt = build_claude_code_build_prompt(job, project)
                self.notes.create(project=room["project"], job_id=job["id"],
                                  note_type="claude_code_prompt", content=prompt)
                self._sync(room["project"])
                return self._send(200, {"job": self._with_next(job),
                                        "room": self._room_with_next(room), "prompt": prompt})

            # ── guided build: thin routes over the same room/job primitives ──
            m = re.match(rf"^/api/guided/({_PID_RE})/start$", path)
            if m:
                pid = m.group(1)
                b = self._body()
                room = start_guided_build(pid, self.jobs, self.rooms, b.get("idea", ""),
                                          ai_team=b.get("ai_team"),
                                          constraints=b.get("constraints", ""),
                                          safety_notes=b.get("safety_notes", ""))
                self._sync(pid)
                return self._send(201, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/chatgpt-response$", path)
            if m:
                pid = m.group(1)
                room = self._guided_room(pid)
                self.rooms.paste_chatgpt_response(room["id"], self._body().get("text", ""))
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/claude-response$", path)
            if m:
                pid = m.group(1)
                room = self._guided_room(pid)
                self.rooms.paste_claude_response(room["id"], self._body().get("text", ""))
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/council-response$", path)
            if m:
                pid = m.group(1)
                room = self._guided_room(pid)
                b = self._body()
                self.rooms.paste_council_response(room["id"], b.get("author", ""), b.get("text", ""))
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/skip-council$", path)
            if m:
                pid = m.group(1)
                room = self._guided_room(pid)
                guided_generate_plan(self.rooms, room["id"])
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/council-done$", path)
            if m:
                pid = m.group(1)
                room = self._guided_room(pid)
                guided_generate_plan(self.rooms, room["id"])
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/approve-plan$", path)
            if m:
                pid = m.group(1)
                room = self._guided_room(pid)
                plan_text = self._body().get("plan_text", "")
                guided_approve_plan(self.projects, self.rooms, self.notes, pid, room["id"], plan_text)
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/sent-to-claude-code$", path)
            if m:
                pid = m.group(1)
                stage = compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
                if not stage.get("job_id"):
                    return self._send(409, {"error": "no build prompt has been generated yet"})
                guided_sent_to_claude_code(self.jobs, stage["job_id"])
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/claude-code-report$", path)
            if m:
                pid = m.group(1)
                stage = compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
                if not stage.get("job_id"):
                    return self._send(409, {"error": "no job is waiting on a report"})
                guided_claude_code_report(self.jobs, self.reports, stage["job_id"], pid,
                                          self._body().get("raw", ""))
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/continue-after-test$", path)
            if m:
                pid = m.group(1)
                stage = compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
                if not stage.get("job_id"):
                    return self._send(409, {"error": "no job is at Testing"})
                guided_continue_after_test(self.jobs, self.reports, self.projects, stage["job_id"])
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/approve$", path)
            if m:
                pid = m.group(1)
                stage = compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
                if not stage.get("job_id"):
                    return self._send(409, {"error": "no job is waiting on approval"})
                job = self.jobs.get(stage["job_id"])
                require_verified_or_raise(self.reports, self.projects, job)
                self.jobs.advance(stage["job_id"], "Approved")
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/reject$", path)
            if m:
                pid = m.group(1)
                stage = compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
                if not stage.get("job_id"):
                    return self._send(409, {"error": "no job is waiting on approval"})
                self.jobs.reject(stage["job_id"], self._body().get("reason", ""))
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/guided/({_PID_RE})/complete$", path)
            if m:
                pid = m.group(1)
                stage = compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
                if not stage.get("job_id"):
                    return self._send(409, {"error": "no job to complete"})
                guided_complete(self.jobs, stage["job_id"])
                self._sync(pid)
                return self._send(200, compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports))
            m = re.match(rf"^/api/outcomes/({_UUID_RE})/edit$", path)
            if m:
                jid = m.group(1)
                job = self.jobs.get(jid)
                project = get_project(self.projects, job["project"])
                b = self._body()
                overrides = {k: b.get(k) for k in
                            ("folder", "test_command", "test_url", "blockers") if k in b}
                recs = self.reports.list(job_id=jid)
                if recs:
                    rec = self.reports.amend(recs[0]["id"], **overrides)
                else:
                    rec = self.reports.ingest(jid, job["project"],
                                              "(no Claude Code report on file — outcome recorded by hand)",
                                              manual=overrides)
                return self._send(200, build_outcome(job, project, rec))

            # ── Browser Handoff builds ──────────────────────────────────────
            if path == "/api/builds":
                b = self._body()
                rec = self.builds.create(buildName=b.get("buildName", ""),
                                         projectName=b.get("projectName", ""),
                                         repoPath=b.get("repoPath", ""),
                                         userGoal=b.get("userGoal", ""),
                                         mode=b.get("mode", "build"),
                                         workerSequence=b.get("workerSequence", "builder_only"),
                                         safetyRails=b.get("safetyRails"),
                                         serverProjectId=b.get("serverProjectId", ""))
                return self._send(201, handoff_build_view(rec))
            m = re.match(rf"^/api/builds/({_UUID_RE})/start$", path)
            if m:
                b = self._body()
                inspection = b.get("inspection")
                if inspection is not None and not isinstance(inspection, dict):
                    return self._send(400, {"error": "inspection must be an object"})
                rec = self.builds.start(m.group(1), inspection=inspection)
                return self._send(200, handoff_build_view(rec))
            m = re.match(rf"^/api/builds/({_UUID_RE})/worker$", path)
            if m:
                rec = self.builds.set_worker(m.group(1), self._body().get("worker", ""))
                return self._send(200, handoff_build_view(rec))
            m = re.match(rf"^/api/builds/({_UUID_RE})/copied$", path)
            if m:
                return self._send(200, handoff_build_view(self.builds.mark_copied(m.group(1))))
            m = re.match(rf"^/api/builds/({_UUID_RE})/opened$", path)
            if m:
                return self._send(200, handoff_build_view(self.builds.mark_opened(m.group(1))))
            m = re.match(rf"^/api/builds/({_UUID_RE})/pasted$", path)
            if m:
                return self._send(200, handoff_build_view(self.builds.mark_pasted(m.group(1))))
            m = re.match(rf"^/api/builds/({_UUID_RE})/response$", path)
            if m:
                rec = self.builds.save_response(m.group(1), self._body().get("raw", ""))
                return self._send(200, handoff_build_view(rec))
            m = re.match(rf"^/api/builds/({_UUID_RE})/decision$", path)
            if m:
                rec = self.builds.decide(m.group(1), self._body().get("choice", ""))
                return self._send(200, handoff_build_view(rec))
            m = re.match(rf"^/api/builds/({_UUID_RE})/note$", path)
            if m:
                b = self._body()
                rec = self.builds.note(m.group(1), b.get("event", ""), b.get("detail", ""))
                return self._send(200, handoff_build_view(rec))

            return self._send(404, {"error": "not found"})
        except NotFound as e:
            return self._send(404, {"error": str(e)})
        except IllegalTransition as e:
            return self._send(403, {"error": str(e)})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e)[:400]})


def build_app_state():
    projects = load_projects()
    ensure_all_project_folders(projects)
    jobs = JobStore(projects)
    reports = ReportStore(projects)
    decisions = DecisionStore(projects)
    risks = RiskStore(projects)
    notes = NoteStore(projects)
    rooms = PlanningRoomStore(projects, jobs, notes)
    builds = HandoffBuildStore()
    Handler.projects = projects
    Handler.jobs = jobs
    Handler.reports = reports
    Handler.decisions = decisions
    Handler.risks = risks
    Handler.notes = notes
    Handler.rooms = rooms
    Handler.builds = builds
    return projects, jobs, reports, decisions, risks, notes, rooms


def main():
    import sys
    if "--founder-report" in sys.argv:
        projects, jobs, reports, decisions, risks, notes, rooms = build_app_state()
        studio_state = load_studio_state()
        files = write_all_reports(projects, jobs, reports, decisions, risks, studio_state, rooms)
        for name in files:
            print(f"wrote reports/{name}")
        return
    build_app_state()
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"GNG Development Studio on http://{BIND}:{PORT} mode={MODE} "
         f"projects={len(Handler.projects)}")
    server.serve_forever()


if __name__ == "__main__":
    main()
