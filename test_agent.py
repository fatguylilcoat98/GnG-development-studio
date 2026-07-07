"""Tests for the Safe Server Agent (agent.py). Run: python3 test_agent.py

Every test builds its own throwaway config + fake project under a temp dir —
the real agent_config.json (if any) is never read, and nothing outside the
temp dir is touched. The dangerous-by-default posture is what's under test:
unapproved projects, unapproved checks, escaping paths, and secret-looking
files must all be refused with a reason, and every decision must be logged.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import agent as a


def _make_fake_project(base, with_git=True):
    proj = os.path.join(base, "fake-app")
    os.makedirs(os.path.join(proj, "docs"), exist_ok=True)
    with open(os.path.join(proj, "README.md"), "w") as f:
        f.write("# Fake App\nA test project for agent tests.\n")
    with open(os.path.join(proj, "docs", "GUIDE.md"), "w") as f:
        f.write("guide\n")
    with open(os.path.join(proj, "app.py"), "w") as f:
        f.write("print('hi')\n")
    with open(os.path.join(proj, ".env"), "w") as f:
        f.write("SECRET=do-not-read\n")
    with open(os.path.join(proj, "test_ok.py"), "w") as f:
        f.write("import unittest\n"
                "class T(unittest.TestCase):\n"
                "    def test_pass(self):\n"
                "        self.assertTrue(True)\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n")
    if with_git:
        os.system(f"cd {proj} && git init -q && git add -A "
                  f"&& git -c user.email=t@t -c user.name=t commit -qm init")
    return proj


class AgentBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gng-agent-test-")
        self.proj = _make_fake_project(self.tmp)
        self.cfg_path = os.path.join(self.tmp, "agent_config.json")
        with open(self.cfg_path, "w") as f:
            json.dump({"projects": [{"id": "fake-app", "name": "Fake App",
                                      "path": self.proj,
                                      "checks": ["python-unittest"]}],
                       "allowed_origins": ["http://127.0.0.1:8893"]}, f)
        self._old_config, self._old_log = a.CONFIG_PATH, a.LOG_PATH
        self._old_state = a.STATE_DIR
        a.CONFIG_PATH = self.cfg_path
        a.STATE_DIR = os.path.join(self.tmp, "state")
        a.LOG_PATH = os.path.join(a.STATE_DIR, "agent_log.jsonl")

    def tearDown(self):
        a.CONFIG_PATH, a.LOG_PATH, a.STATE_DIR = (self._old_config, self._old_log,
                                                   self._old_state)
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestConfigAndProjects(AgentBase):
    def test_missing_config_reports_unconfigured_with_no_projects(self):
        a.CONFIG_PATH = os.path.join(self.tmp, "nope.json")
        cfg = a.load_config()
        self.assertFalse(cfg["configured"])
        self.assertEqual(cfg["projects"], [])

    def test_unapproved_project_is_refused(self):
        cfg = a.load_config()
        with self.assertRaises(a.Refused):
            a.get_project(cfg, "not-in-config")

    def test_approved_project_with_missing_folder_is_refused(self):
        with open(self.cfg_path, "w") as f:
            json.dump({"projects": [{"id": "ghost", "path": "/no/such/dir",
                                      "checks": []}]}, f)
        with self.assertRaises(a.Refused):
            a.get_project(a.load_config(), "ghost")


class TestInspect(AgentBase):
    def test_inspect_returns_all_evidence_fields(self):
        result = a.inspect_project(a.get_project(a.load_config(), "fake-app"))
        for key in ("tree", "docs", "readme_excerpt", "package_scripts", "stack",
                    "git_status", "git_branch", "git_log", "allowed_checks", "risks"):
            self.assertIn(key, result)
        self.assertIn("python", result["stack"])
        self.assertTrue(any("README" in d for d in result["docs"]))
        self.assertEqual(result["allowed_checks"], ["python-unittest"])
        self.assertIn("Fake App", result["readme_excerpt"])

    def test_inspect_flags_dirty_git_tree_as_risk(self):
        with open(os.path.join(self.proj, "new.txt"), "w") as f:
            f.write("dirty\n")
        result = a.inspect_project(a.get_project(a.load_config(), "fake-app"))
        self.assertTrue(any("uncommitted" in r for r in result["risks"]))


class TestRunCheck(AgentBase):
    def test_approved_catalog_check_runs_and_reports(self):
        result = a.run_check(a.get_project(a.load_config(), "fake-app"),
                             "python-unittest")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("python3 -m unittest", result["command"])
        self.assertGreaterEqual(result["duration_ms"], 0)

    def test_check_not_in_catalog_is_refused(self):
        with self.assertRaises(a.Refused):
            a.run_check(a.get_project(a.load_config(), "fake-app"), "rm-rf-everything")

    def test_catalog_check_not_approved_for_project_is_refused(self):
        with self.assertRaises(a.Refused):
            a.run_check(a.get_project(a.load_config(), "fake-app"), "npm-test")

    def test_catalog_contains_no_shell_strings(self):
        for name, argv in a.CHECK_CATALOG.items():
            self.assertIsInstance(argv, tuple, name)
            for part in argv:
                self.assertNotIn(";", part)
                self.assertNotIn("|", part)
                self.assertNotIn("&", part)


class TestReadFile(AgentBase):
    def test_reads_ordinary_project_file(self):
        result = a.read_file(a.get_project(a.load_config(), "fake-app"), "README.md")
        self.assertIn("Fake App", result["content"])

    def test_env_file_is_refused(self):
        with self.assertRaises(a.Refused):
            a.read_file(a.get_project(a.load_config(), "fake-app"), ".env")

    def test_secret_looking_names_are_refused(self):
        proj = a.get_project(a.load_config(), "fake-app")
        for name in ("config/secrets.yaml", "id_rsa", "server.key", "api_token.txt",
                     ".ssh/known_hosts", "passwords.csv"):
            with self.assertRaises(a.Refused):
                a.read_file(proj, name)

    def test_path_traversal_is_refused(self):
        proj = a.get_project(a.load_config(), "fake-app")
        outside = os.path.join(self.tmp, "outside.txt")
        with open(outside, "w") as f:
            f.write("outside\n")
        for attempt in ("../outside.txt", "../../etc/hostname", "/etc/hostname",
                        "docs/../../outside.txt"):
            with self.assertRaises(a.Refused):
                a.read_file(proj, attempt)

    def test_every_action_is_logged(self):
        proj = a.get_project(a.load_config(), "fake-app")
        a.log_action("/read-file", {"relativePath": "README.md"}, True)
        a.log_action("/read-file", {"relativePath": ".env"}, False, "secrets deny-list")
        with open(a.LOG_PATH) as f:
            lines = [json.loads(line) for line in f]
        self.assertEqual([e["allowed"] for e in lines], [True, False])
        self.assertIn("deny-list", lines[1]["reason"])


class TestAgentHTTP(AgentBase):
    def setUp(self):
        super().setUp()
        self.server = a.ThreadingHTTPServer(("127.0.0.1", 0), a.AgentHandler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def _req(self, path, payload=None, origin=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        req = (urllib.request.Request(url, headers=headers) if payload is None else
              urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers=headers))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode()), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}"), dict(e.headers)

    def test_health_and_projects(self):
        code, h, _ = self._req("/health")
        self.assertEqual((code, h["status"], h["projects"]), (200, "ok", 1))
        code, p, _ = self._req("/projects")
        self.assertEqual(p["projects"][0]["id"], "fake-app")
        self.assertTrue(p["projects"][0]["exists"])

    def test_inspect_and_run_check_over_http(self):
        code, ins, _ = self._req("/inspect", {"projectId": "fake-app", "mode": "audit"})
        self.assertEqual(code, 200)
        self.assertIn("git_status", ins)
        code, chk, _ = self._req("/run-check", {"projectId": "fake-app",
                                                "checkName": "python-unittest"})
        self.assertEqual((code, chk["exit_code"]), (200, 0))

    def test_refusals_are_403_with_reasons(self):
        code, err, _ = self._req("/run-check", {"projectId": "fake-app",
                                                "checkName": "arbitrary; rm -rf /"})
        self.assertEqual(code, 403)
        self.assertIn("catalog", err["error"])
        code, err, _ = self._req("/read-file", {"projectId": "fake-app",
                                                "relativePath": ".env"})
        self.assertEqual(code, 403)
        code, err, _ = self._req("/inspect", {"projectId": "not-approved"})
        self.assertEqual(code, 403)

    def test_unknown_routes_are_404(self):
        code, err, _ = self._req("/exec", {"cmd": "ls"})
        self.assertEqual(code, 404)
        code, err, _ = self._req("/anything")
        self.assertEqual(code, 404)

    def test_cors_echoes_only_allow_listed_origins(self):
        _, _, headers = self._req("/health", origin="http://127.0.0.1:8893")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"),
                         "http://127.0.0.1:8893")
        _, _, headers = self._req("/health", origin="https://evil.example.com")
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))


class TestAgentSourceRails(unittest.TestCase):
    """The dangerous operations must have NO code path at all."""

    def test_no_shell_true_and_no_forbidden_operations(self):
        src = open(os.path.join(ROOT, "agent.py")).read()
        for forbidden in ("shell=True", "os.system", "rm -rf", "git push",
                          "systemctl", "pm2 ", "service restart"):
            self.assertNotIn(forbidden, src, f"agent.py contains {forbidden}")

    def test_studio_module_still_never_imports_subprocess_or_agent(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("import agent", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
