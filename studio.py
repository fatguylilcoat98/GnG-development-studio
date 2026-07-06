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

Port: 8893 (loopback by default). Run: `python3 studio.py` (server) or
`python3 studio.py --founder-report` (write the five reports/ files and exit).
"""
import json
import os
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
REPORTS_DIR = os.path.join(ROOT, "reports")
DASHBOARD_PATH = os.path.join(ROOT, "dashboard.html")

PROJECTS_PATH = os.path.join(STATE_DIR, "projects.json")
JOBS_PATH = os.path.join(STATE_DIR, "jobs.jsonl")
REPORTS_PATH = os.path.join(STATE_DIR, "reports.jsonl")
DECISIONS_PATH = os.path.join(STATE_DIR, "decisions.jsonl")
NOTES_PATH = os.path.join(STATE_DIR, "notes.jsonl")
RISKS_PATH = os.path.join(STATE_DIR, "risks.jsonl")
ROOMS_PATH = os.path.join(STATE_DIR, "planning_rooms.jsonl")
STUDIO_STATE_PATH = os.path.join(STATE_DIR, "studio_state.json")

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
NEEDS APPROVAL: <yes|no>"""

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


# ── jobs ────────────────────────────────────────────────────────────────────────
class JobStore:
    def __init__(self):
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


def parse_claude_report(raw):
    """Best-effort line parser matching FINAL_STATUS_FORMAT labels. Tolerant of
    case and stray whitespace. Returns (fields, parsed_ok) — parsed_ok is True
    only if every labeled field was found; otherwise the raw text is always kept
    so nothing is lost, and Chris can fill in fields manually."""
    fields = {k: None for k in _REPORT_FIELD_PATTERNS}
    for line in raw.splitlines():
        line = line.strip()
        for key, pattern in _REPORT_FIELD_PATTERNS.items():
            m = re.match(pattern, line, re.IGNORECASE)
            if m:
                fields[key] = m.group(1).strip()
    parsed_ok = all(v is not None for v in fields.values())
    if fields.get("needs_approval") is not None:
        fields["needs_approval"] = fields["needs_approval"].strip().lower() in ("yes", "true")
    return fields, parsed_ok


class ReportStore:
    def __init__(self):
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
        raw = str(raw or "")
        fields, parsed_ok = parse_claude_report(raw)
        if manual:
            fields.update({k: v for k, v in manual.items() if k in _REPORT_FIELD_PATTERNS})
        rec = {"id": _id(), "job_id": job_id, "project": project, "raw": raw,
              "parsed_ok": parsed_ok, "created_at": time.time(), **fields}
        self.reports[rec["id"]] = rec
        _jsonl_append(REPORTS_PATH, rec)
        return rec


# ── decisions / risks / notes ───────────────────────────────────────────────────
class DecisionStore:
    def __init__(self):
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
        text = str(text).strip()
        if not text:
            raise ValueError("decision text is required")
        rec = {"id": _id(), "project": project, "job_id": job_id, "text": text,
              "source": source, "created_at": time.time()}
        self.decisions[rec["id"]] = rec
        _jsonl_append(DECISIONS_PATH, rec)
        return rec


class RiskStore:
    def __init__(self):
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
        description = str(description).strip()
        if not description:
            raise ValueError("risk description is required")
        rec = {"id": _id(), "project": project, "job_id": job_id, "description": description,
              "severity": severity, "resolved": False, "source": source,
              "created_at": time.time(), "updated_at": time.time()}
        self.risks[rec["id"]] = rec
        _jsonl_append(RISKS_PATH, rec)
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
    def __init__(self):
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
    def __init__(self, jobs, notes):
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

    def create(self, project, chris_idea):
        chris_idea = str(chris_idea).strip()
        if not chris_idea:
            raise ValueError("Chris's original idea is required")
        rec = {
            "id": _id(), "project": project, "status": "Idea", "chris_idea": chris_idea,
            "chatgpt_response": "", "claude_response": "", "council_responses": [],
            "disagreements": "", "risks": "", "unified_plan": "",
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
        if room["status"] == "Idea":
            room = self._advance(rid, "ChatGPT Reviewed")
        return room

    def paste_claude_response(self, rid, text):
        room = self._get(rid)
        room = dict(room, claude_response=str(text), updated_at=time.time())
        self._save(room)
        if room["status"] == "ChatGPT Reviewed":
            room = self._advance(rid, "Claude Reviewed")
        return room

    def paste_council_response(self, rid, author, text):
        room = self._get(rid)
        entry = {"author": str(author or "council"), "content": str(text), "at": _now()}
        room = dict(room, council_responses=room["council_responses"] + [entry],
                   updated_at=time.time())
        self._save(room)
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
            constraints = "\n".join(x for x in (room.get("disagreements", ""),
                                                room.get("risks", "")) if x)
            job = self.jobs.create(project=room["project"], title=title,
                                   description=description, constraints=constraints)
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


# ── cross-cutting: needs-chris, project memory, where-are-we, continuity ───────
def needs_chris_items(jobs, reports):
    items = []
    for job in jobs.list():
        if job["status"] == "Needs Chris Approval":
            items.append({"project": job["project"], "job_id": job["id"],
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
        for reason in reasons:
            items.append({"project": r["project"], "job_id": r["job_id"], "reason": reason,
                         "choices": ["Review the report", "Approve", "Reject / send back"]})
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
    project = get_project(projects, pid)
    proj_jobs = jobs.list(project=pid)
    latest_job = proj_jobs[0] if proj_jobs else None
    proj_rooms = rooms.list(project=pid)
    latest_room = proj_rooms[0] if proj_rooms else None
    latest_decision = next(iter(decisions.list(project=pid)), None)
    council_needed = bool(latest_room and latest_room["status"] == "Council Needed")
    return f"""WHERE ARE WE — {project['name']}

Current goal: {project.get('current_goal') or '(not set)'}
Last decided: {latest_decision['text'] if latest_decision else '(no decisions logged yet)'}
What ChatGPT said: {(latest_room['chatgpt_response'][:400] + '...') if latest_room and latest_room['chatgpt_response'] else '(nothing yet)'}
What Claude said: {(latest_room['claude_response'][:400] + '...') if latest_room and latest_room['claude_response'] else '(nothing yet)'}
Council needed: {'yes' if council_needed else 'no'}
Next build step: {latest_job['status'] if latest_job else '(no jobs yet)'}
What needs Chris: {project.get('next_action') or '(nothing recorded)'}"""


def build_continuity_packet(pid, projects, jobs, reports, decisions, risks, rooms):
    project = get_project(projects, pid)
    proj_rooms = rooms.list(project=pid)
    latest_room = proj_rooms[0] if proj_rooms else None
    proj_jobs = jobs.list(project=pid)
    latest_job = proj_jobs[0] if proj_jobs else None
    latest_report = next(iter(reports.list(project=pid)), None)
    open_risks = risks.list(project=pid, resolved=False)
    recent_decisions = decisions.list(project=pid)[:5]
    current_plan = (latest_room["unified_plan"] if latest_room and latest_room["unified_plan"]
                    else (latest_job["description"] if latest_job else "(no plan yet)"))
    risk_lines = "\n".join(f"- {r['description']}" for r in open_risks) or "(none open)"
    decision_lines = "\n".join(f"- {d['text']}" for d in recent_decisions) or "(none logged)"
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

NEXT ACTION: {project.get('next_action', '')}

LATEST CLAUDE CODE REPORT:
{latest_report['raw'] if latest_report else '(no reports yet)'}"""


# ── founder report (pure data + markdown; this module never shells out) ───────
def build_founder_report_data(projects, jobs, reports, decisions, risks, studio_state):
    active_pid = studio_state.get("active_project")
    return {
        "active_project": projects.get(active_pid) if active_pid else None,
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
    lines += ["## Active Jobs"]
    lines += [f"- [{j['status']}] {j['title']} ({j['project']})" for j in data["active_jobs"]] or ["(none)"]
    lines += ["", "## Needs Chris Approval"]
    lines += [f"- {it['project']}: {it['reason']}" for it in data["needs_chris"]] or ["(nothing needs Chris right now)"]
    lines += ["", "## Latest Claude Report"]
    lines += [data["latest_reports"][0]["raw"] if data["latest_reports"] else "(none yet)"]
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


def write_all_reports(projects, jobs, reports, decisions, risks, studio_state):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    data = build_founder_report_data(projects, jobs, reports, decisions, risks, studio_state)
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
            if path == "/api/founder-report":
                studio_state = load_studio_state()
                data = build_founder_report_data(self.projects, self.jobs, self.reports,
                                                 self.decisions, self.risks, studio_state)
                return self._send(200, data)
            if path == "/api/needs-chris":
                return self._send(200, {"items": needs_chris_items(self.jobs, self.reports)})
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
            if path in ("/", "/index.html"):
                try:
                    with open(DASHBOARD_PATH, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                except OSError:
                    return self._send(404, {"error": "dashboard missing"})
            return self._send(404, {"error": "not found"})
        except NotFound as e:
            return self._send(404, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e)[:400]})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/jobs":
                b = self._body()
                job = self.jobs.create(project=b.get("project", ""), title=b.get("title", ""),
                                       description=b.get("description", ""),
                                       priority=b.get("priority", "normal"),
                                       constraints=b.get("constraints", ""),
                                       approval_required=bool(b.get("approval_required", True)),
                                       safety_notes=b.get("safety_notes", ""))
                return self._send(201, self._with_next(job))
            m = re.match(rf"^/api/jobs/({_UUID_RE})/status$", path)
            if m:
                job = self.jobs.advance(m.group(1), str(self._body().get("status", "")))
                return self._send(200, self._with_next(job))
            m = re.match(rf"^/api/jobs/({_UUID_RE})/delete$", path)
            if m:
                job = self.jobs.delete(m.group(1))
                return self._send(200, self._with_next(job))
            m = re.match(rf"^/api/jobs/({_UUID_RE})/generate-chatgpt-prompt$", path)
            if m:
                jid = m.group(1)
                job = _auto_walk_forward(self.jobs, jid, "Ready for ChatGPT")
                project = get_project(self.projects, job["project"])
                prompt = build_chatgpt_planning_prompt(job, project)
                self.notes.create(project=job["project"], job_id=jid,
                                  note_type="chatgpt_plan", content=prompt)
                return self._send(200, {"job": self._with_next(job), "prompt": prompt})
            m = re.match(rf"^/api/jobs/({_UUID_RE})/generate-claude-prompt$", path)
            if m:
                jid = m.group(1)
                job = _auto_walk_forward(self.jobs, jid, "Ready for Claude")
                project = get_project(self.projects, job["project"])
                prompt = build_claude_code_build_prompt(job, project)
                self.notes.create(project=job["project"], job_id=jid,
                                  note_type="claude_code_prompt", content=prompt)
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
                if b.get("risks"):
                    self.risks.create(job["project"], b["risks"], job_id=jid,
                                      source="chatgpt_plan_ingestion")
                if b.get("decisions"):
                    self.decisions.create(job["project"], b["decisions"], job_id=jid,
                                          source="chatgpt_plan_ingestion")
                return self._send(201, note)
            m = re.match(rf"^/api/jobs/({_UUID_RE})/claude-report$", path)
            if m:
                jid = m.group(1)
                job = self.jobs.get(jid)
                b = self._body()
                rec = self.reports.ingest(jid, job["project"], b.get("raw", ""),
                                          manual=b.get("manual"))
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
                return self._send(201, d)
            if path == "/api/risks":
                b = self._body()
                r = self.risks.create(project=b.get("project", ""),
                                      description=b.get("description", ""),
                                      severity=b.get("severity", "normal"), job_id=b.get("job_id"))
                return self._send(201, r)
            m = re.match(rf"^/api/risks/({_UUID_RE})/resolve$", path)
            if m:
                return self._send(200, self.risks.resolve(m.group(1)))
            m = re.match(rf"^/api/project/({_PID_RE})/active$", path)
            if m:
                return self._send(200, set_active_project(m.group(1)))
            if path == "/api/planning-rooms":
                b = self._body()
                room = self.rooms.create(project=b.get("project", ""),
                                         chris_idea=b.get("chris_idea", ""))
                return self._send(201, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/chatgpt-response$", path)
            if m:
                room = self.rooms.paste_chatgpt_response(m.group(1), self._body().get("text", ""))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/claude-response$", path)
            if m:
                room = self.rooms.paste_claude_response(m.group(1), self._body().get("text", ""))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/council-response$", path)
            if m:
                b = self._body()
                room = self.rooms.paste_council_response(m.group(1), b.get("author", ""),
                                                          b.get("text", ""))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/disagreements$", path)
            if m:
                b = self._body()
                room = self.rooms.set_disagreements_and_risks(m.group(1), b.get("disagreements"),
                                                              b.get("risks"))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/unified-plan$", path)
            if m:
                room = self.rooms.generate_unified_plan_draft(m.group(1), self._body().get("text", ""))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/needs-signoff$", path)
            if m:
                room = self.rooms.mark_needs_signoff(m.group(1))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/chris-approved$", path)
            if m:
                room = self.rooms.chris_approved(m.group(1), self._body().get("note", ""))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/emergency-skip$", path)
            if m:
                room = self.rooms.emergency_skip(m.group(1), self._body().get("reason", ""))
                return self._send(200, self._room_with_next(room))
            m = re.match(rf"^/api/planning-rooms/({_UUID_RE})/build-prompt$", path)
            if m:
                room = self.rooms.get(m.group(1))
                project = get_project(self.projects, room["project"])
                job, room = self.rooms.generate_claude_code_build_prompt(m.group(1), room["project"])
                prompt = build_claude_code_build_prompt(job, project)
                self.notes.create(project=room["project"], job_id=job["id"],
                                  note_type="claude_code_prompt", content=prompt)
                return self._send(200, {"job": self._with_next(job),
                                        "room": self._room_with_next(room), "prompt": prompt})
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
    jobs = JobStore()
    reports = ReportStore()
    decisions = DecisionStore()
    risks = RiskStore()
    notes = NoteStore()
    rooms = PlanningRoomStore(jobs, notes)
    Handler.projects = projects
    Handler.jobs = jobs
    Handler.reports = reports
    Handler.decisions = decisions
    Handler.risks = risks
    Handler.notes = notes
    Handler.rooms = rooms
    return projects, jobs, reports, decisions, risks, notes, rooms


def main():
    import sys
    if "--founder-report" in sys.argv:
        projects, jobs, reports, decisions, risks, notes, rooms = build_app_state()
        studio_state = load_studio_state()
        files = write_all_reports(projects, jobs, reports, decisions, risks, studio_state)
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
