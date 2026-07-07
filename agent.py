"""
GNG Development Studio — Safe Server Agent (agent.py)

A deliberately small, deliberately paranoid local API that lets the Studio UI
inspect approved projects on this server and run a fixed catalog of read-only
checks — so Studio doesn't need VS Code or a terminal to gather real evidence
for AI worker prompts.

This is a SEPARATE process from studio.py, on purpose: studio.py stays
completely subprocess-free (enforced by its test suite). Everything that
touches a shell command lives here, behind three independent gates:

1. APPROVED PROJECTS ONLY — projects come from agent_config.json on the
   server. The browser can only reference a projectId from that file; it can
   never submit a path.
2. ALLOW-LISTED COMMANDS ONLY — /run-check accepts a checkName that must
   exist in CHECK_CATALOG below AND in that project's own "checks" list.
   There is no route that accepts a command string.
3. BLOCKED BY DEFAULT — deploying, restarting services, deleting files,
   pushing to a remote, reading .env/secrets, arbitrary shell: none of these
   have a code path here at all.

Every request — allowed or refused — is logged to state/agent_log.jsonl.

Run: python3 agent.py         (default 127.0.0.1:8894)
Env: GNG_AGENT_PORT, GNG_AGENT_BIND, GNG_AGENT_CONFIG
Config: copy agent_config.example.json -> agent_config.json and edit.
"""
import json
import os
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("GNG_AGENT_CONFIG", os.path.join(ROOT, "agent_config.json"))
STATE_DIR = os.path.join(ROOT, "state")
LOG_PATH = os.path.join(STATE_DIR, "agent_log.jsonl")
PORT = int(os.environ.get("GNG_AGENT_PORT", "8894"))
BIND = os.environ.get("GNG_AGENT_BIND", "127.0.0.1")

AGENT_VERSION = "1.0"

# The complete universe of commands this agent can ever run. Fixed argv lists,
# never joined through a shell, never templated with user input.
CHECK_CATALOG = {
    "npm-test": ("npm", "test", "--silent"),
    "npm-build": ("npm", "run", "build"),
    "pytest": ("python3", "-m", "pytest", "-q"),
    "python-unittest": ("python3", "-m", "unittest", "discover", "-v"),
    "docker-compose-ps": ("docker", "compose", "ps"),
}
CHECK_TIMEOUT_SECONDS = 300
OUTPUT_CAP = 20000          # stdout/stderr are truncated to this many chars
FILE_SIZE_CAP = 200_000     # /read-file refuses larger files
TREE_ENTRY_CAP = 400
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".pytest_cache", ".next", "coverage"}

# /read-file refuses any path whose name matches one of these. Deny-list on
# the filename plus every directory segment, checked case-insensitively.
BLOCKED_NAME_PATTERNS = (".env", "secret", "credential", "token", "password",
                         "id_rsa", "id_ed25519", ".pem", ".key", ".p12",
                         ".pfx", ".keystore", ".ssh", ".aws", ".gnupg")

DEFAULT_ALLOWED_ORIGINS = ["http://127.0.0.1:8893", "http://localhost:8893"]


class Refused(Exception):
    """A request the agent will not serve — always with a stated reason."""


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"projects": [], "allowed_origins": DEFAULT_ALLOWED_ORIGINS,
                "configured": False}
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    cfg.setdefault("projects", [])
    cfg.setdefault("allowed_origins", DEFAULT_ALLOWED_ORIGINS)
    cfg["configured"] = True
    return cfg


def get_project(cfg, project_id):
    for p in cfg["projects"]:
        if p.get("id") == project_id:
            if not os.path.isdir(p.get("path", "")):
                raise Refused(f"approved project '{project_id}' points at a folder "
                              f"that does not exist on this server: {p.get('path')}")
            return p
    raise Refused(f"'{project_id}' is not an approved project on this agent")


def log_action(route, payload, allowed, reason="", duration_ms=0):
    os.makedirs(STATE_DIR, exist_ok=True)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "route": route, "input": json.dumps(payload)[:400],
          "allowed": bool(allowed), "reason": reason,
          "duration_ms": int(duration_ms)}
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _run(argv, cwd, timeout=CHECK_TIMEOUT_SECONDS):
    """Run one fixed argv (never a shell string) inside an approved project."""
    started = time.time()
    try:
        p = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        return {"command": " ".join(argv), "exit_code": p.returncode,
                "stdout": p.stdout[-OUTPUT_CAP:], "stderr": p.stderr[-OUTPUT_CAP:],
                "duration_ms": int((time.time() - started) * 1000)}
    except FileNotFoundError:
        return {"command": " ".join(argv), "exit_code": -1, "stdout": "",
                "stderr": f"{argv[0]}: not installed on this server",
                "duration_ms": int((time.time() - started) * 1000)}
    except subprocess.TimeoutExpired:
        return {"command": " ".join(argv), "exit_code": -1, "stdout": "",
                "stderr": f"timed out after {timeout}s",
                "duration_ms": int((time.time() - started) * 1000)}


def _git(project_path, *args):
    out = _run(("git",) + args, cwd=project_path, timeout=30)
    return (out["stdout"] or out["stderr"]).strip()[:4000]


def safe_join(base, rel):
    """Resolve rel inside base; refuse anything that escapes it."""
    target = os.path.realpath(os.path.join(base, str(rel)))
    real_base = os.path.realpath(base)
    if target != real_base and not target.startswith(real_base + os.sep):
        raise Refused("path escapes the approved project folder")
    return target


def is_blocked_name(path):
    parts = [p.lower() for p in path.replace("\\", "/").split("/") if p]
    for part in parts:
        for pattern in BLOCKED_NAME_PATTERNS:
            if pattern in part:
                return True
    return False


# ── capabilities ────────────────────────────────────────────────────────────────
def inspect_project(project):
    """Read-only evidence gathering: tree summary, docs, scripts, git state,
    detected stack, which catalog checks this project allows, risks."""
    path = project["path"]
    entries, dirs_seen = [], 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, path)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= 3:
            dirnames[:] = []
        dirs_seen += 1
        for name in filenames:
            entries.append(name if rel == "." else f"{rel}/{name}")
            if len(entries) >= TREE_ENTRY_CAP:
                break
        if len(entries) >= TREE_ENTRY_CAP:
            break

    docs = [e for e in entries
            if re.match(r"(readme|docs/).*\.(md|txt|rst)$", e.lower())
            or e.lower().startswith("readme")]
    readme_excerpt = ""
    for candidate in ("README.md", "readme.md", "README.txt", "README"):
        p = os.path.join(path, candidate)
        if os.path.exists(p):
            try:
                with open(p, errors="replace") as f:
                    readme_excerpt = f.read(1500)
            except OSError:
                pass
            break

    scripts = {}
    pkg = os.path.join(path, "package.json")
    if os.path.exists(pkg):
        try:
            with open(pkg) as f:
                scripts = json.load(f).get("scripts", {})
        except (OSError, ValueError):
            scripts = {"error": "package.json could not be parsed"}

    stack = []
    if os.path.exists(pkg):
        stack.append("node")
    if any(e.endswith(".py") for e in entries) or \
       any(os.path.exists(os.path.join(path, f)) for f in
           ("requirements.txt", "pyproject.toml", "setup.py")):
        stack.append("python")
    if any(os.path.exists(os.path.join(path, f)) for f in
           ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yaml")):
        stack.append("docker")

    git_status = _git(path, "status", "--short")
    git_branch = _git(path, "branch", "--show-current")
    git_log = _git(path, "log", "-5", "--oneline")
    git_diff = _git(path, "diff", "--stat")

    allowed_checks = [c for c in project.get("checks", []) if c in CHECK_CATALOG]

    risks = []
    if git_status:
        risks.append(f"uncommitted changes present ({len(git_status.splitlines())} paths)")
    if not docs:
        risks.append("no README/docs found")
    if not allowed_checks:
        risks.append("no checks are approved for this project in agent_config.json")

    return {"project": project["id"], "name": project.get("name", project["id"]),
            "path": path,
            "tree": {"file_count": len(entries), "dir_count": dirs_seen,
                     "truncated": len(entries) >= TREE_ENTRY_CAP,
                     "sample": entries[:60]},
            "docs": docs[:20], "readme_excerpt": readme_excerpt,
            "package_scripts": scripts, "stack": stack,
            "git_status": git_status or "(clean)", "git_branch": git_branch,
            "git_log": git_log, "git_diff_stat": git_diff or "(no diff)",
            "allowed_checks": allowed_checks, "risks": risks}


def run_check(project, check_name):
    allowed = [c for c in project.get("checks", []) if c in CHECK_CATALOG]
    if check_name not in CHECK_CATALOG:
        raise Refused(f"'{check_name}' is not in this agent's fixed check catalog "
                      f"({', '.join(sorted(CHECK_CATALOG))})")
    if check_name not in allowed:
        raise Refused(f"'{check_name}' is not approved for project "
                      f"'{project['id']}' in agent_config.json")
    return _run(CHECK_CATALOG[check_name], cwd=project["path"])


def read_file(project, relative_path):
    if is_blocked_name(str(relative_path)):
        raise Refused("that path matches the agent's secrets deny-list "
                      "(.env, keys, tokens, credentials, ...) and will not be read")
    target = safe_join(project["path"], relative_path)
    if not os.path.isfile(target):
        raise Refused(f"no such file in this project: {relative_path}")
    if os.path.getsize(target) > FILE_SIZE_CAP:
        raise Refused(f"file exceeds the {FILE_SIZE_CAP // 1000}KB read cap")
    with open(target, errors="replace") as f:
        return {"relativePath": relative_path, "content": f.read()}


# ── HTTP layer ──────────────────────────────────────────────────────────────────
class AgentHandler(BaseHTTPRequestHandler):
    server_version = "GNGSafeAgent/" + AGENT_VERSION

    def log_message(self, fmt, *args):   # quiet default request spam; we JSONL-log instead
        pass

    def _cors_origin(self):
        cfg = load_config()
        origin = self.headers.get("Origin", "")
        return origin if origin in cfg.get("allowed_origins", []) else None

    def _send(self, code, payload):
        body = json.dumps(payload, indent=1).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
        self.end_headers()

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode() or "{}")
        except ValueError:
            raise Refused("request body must be JSON")

    def do_GET(self):
        started = time.time()
        cfg = load_config()
        if self.path == "/health":
            payload = {"status": "ok", "agent": "gng-safe-server-agent",
                       "version": AGENT_VERSION, "configured": cfg["configured"],
                       "projects": len(cfg["projects"]),
                       "checks_catalog": sorted(CHECK_CATALOG)}
            log_action("/health", {}, True, duration_ms=(time.time() - started) * 1000)
            return self._send(200, payload)
        if self.path == "/projects":
            projects = [{"id": p.get("id"), "name": p.get("name", p.get("id")),
                         "path": p.get("path"),
                         "checks": [c for c in p.get("checks", []) if c in CHECK_CATALOG],
                         "exists": os.path.isdir(p.get("path", ""))}
                        for p in cfg["projects"]]
            log_action("/projects", {}, True, duration_ms=(time.time() - started) * 1000)
            return self._send(200, {"projects": projects,
                                    "configured": cfg["configured"]})
        log_action(self.path, {}, False, "unknown route")
        return self._send(404, {"error": "this agent only serves its fixed, "
                                          "allow-listed routes"})

    def do_POST(self):
        started = time.time()
        route = self.path
        body = {}
        try:
            cfg = load_config()
            body = self._body()
            if route == "/inspect":
                project = get_project(cfg, body.get("projectId", ""))
                result = inspect_project(project)
                log_action(route, body, True, duration_ms=(time.time() - started) * 1000)
                return self._send(200, result)
            if route == "/run-check":
                project = get_project(cfg, body.get("projectId", ""))
                result = run_check(project, body.get("checkName", ""))
                log_action(route, body, True, f"exit={result['exit_code']}",
                           (time.time() - started) * 1000)
                return self._send(200, result)
            if route == "/read-file":
                project = get_project(cfg, body.get("projectId", ""))
                result = read_file(project, body.get("relativePath", ""))
                log_action(route, body, True, duration_ms=(time.time() - started) * 1000)
                return self._send(200, result)
            log_action(route, body, False, "unknown route")
            return self._send(404, {"error": "this agent only serves its fixed, "
                                              "allow-listed routes"})
        except Refused as e:
            log_action(route, body, False, str(e), (time.time() - started) * 1000)
            return self._send(403, {"error": str(e)})
        except Exception as e:
            log_action(route, body, False, f"error: {e}", (time.time() - started) * 1000)
            return self._send(500, {"error": str(e)[:400]})


def main():
    cfg = load_config()
    server = ThreadingHTTPServer((BIND, PORT), AgentHandler)
    print(f"GNG Safe Server Agent on http://{BIND}:{PORT} "
          f"configured={cfg['configured']} projects={len(cfg['projects'])}")
    if not cfg["configured"]:
        print(f"  no config yet — copy agent_config.example.json to {CONFIG_PATH} and edit")
    server.serve_forever()


if __name__ == "__main__":
    main()
