"""Tests for GNG Development Studio. Stdlib unittest only; every test runs against
a temp state directory — the real state/ (if any) is never touched. Studio is
coordination-only: no AI/GitHub/SSH calls, no deployment, no service management —
enforced here by a source scan, not just by convention."""
import contextlib
import functools
import http.server
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
import studio as s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gng-studio-test-")
        s.STATE_DIR = self.tmp
        s.PROJECTS_PATH = os.path.join(self.tmp, "projects.json")
        s.JOBS_PATH = os.path.join(self.tmp, "jobs.jsonl")
        s.REPORTS_PATH = os.path.join(self.tmp, "reports.jsonl")
        s.DECISIONS_PATH = os.path.join(self.tmp, "decisions.jsonl")
        s.NOTES_PATH = os.path.join(self.tmp, "notes.jsonl")
        s.RISKS_PATH = os.path.join(self.tmp, "risks.jsonl")
        s.ROOMS_PATH = os.path.join(self.tmp, "planning_rooms.jsonl")
        s.STUDIO_STATE_PATH = os.path.join(self.tmp, "studio_state.json")
        s.REPORTS_DIR = os.path.join(self.tmp, "reports_out")
        s.PROJECTS_DIR = os.path.join(self.tmp, "projects")
        (self.projects, self.jobs, self.reports, self.decisions, self.risks,
         self.notes, self.rooms) = self._fresh_state()

    def _fresh_state(self):
        projects = s.load_projects()
        s.ensure_all_project_folders(projects)
        jobs = s.JobStore(projects)
        reports = s.ReportStore(projects)
        decisions = s.DecisionStore(projects)
        risks = s.RiskStore(projects)
        notes = s.NoteStore(projects)
        rooms = s.PlanningRoomStore(projects, jobs, notes)
        return projects, jobs, reports, decisions, risks, notes, rooms

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_job(self, project="aubs-os", **over):
        kw = dict(project=project, title="Test job", description="Do a thing",
                  priority="normal", constraints="none", approval_required=True,
                  safety_notes="")
        kw.update(over)
        return self.jobs.create(**kw)


REQUIRED_PROJECTS = ["AUBS OS", "PathBack", "LYLO", "Splendor", "CLASPION", "Handshake",
                    "Veracore", "Knowledge Spine (aubs-knowledge)", "Builder Budget",
                    "GNG Website", "Future Project"]


class TestProjectRegistry(Base):
    def test_all_required_projects_present(self):
        names = [p["name"] for p in self.projects.values()]
        for req in REQUIRED_PROJECTS:
            self.assertIn(req, names)
        self.assertEqual(len(self.projects), 11)

    def test_every_project_has_all_required_fields(self):
        fields = ["name", "type", "repo_path", "github_url", "live_service_name",
                  "port", "status", "hands_off", "notes", "current_goal",
                  "current_branch", "latest_commit", "open_pr", "next_action"]
        for p in self.projects.values():
            for f in fields:
                self.assertIn(f, p, f"{p['id']} missing {f}")
            self.assertIn(p["type"], s.PROJECT_TYPES)

    def test_products_marked_hands_off(self):
        for pid in ("pathback", "lylo", "splendor", "claspion"):
            self.assertTrue(self.projects[pid]["hands_off"], pid)
        self.assertFalse(self.projects["aubs-os"]["hands_off"])

    def test_registry_persists_across_reload(self):
        s.update_project(self.projects, "aubs-os", current_goal="ship the studio")
        reloaded = s.load_projects()
        self.assertEqual(reloaded["aubs-os"]["current_goal"], "ship the studio")


class TestJobCreation(Base):
    def test_job_starts_draft(self):
        job = self.make_job()
        self.assertEqual(job["status"], "Draft")
        self.assertEqual(job["priority"], "normal")
        self.assertTrue(job["approval_required"])

    def test_missing_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.make_job(title="")
        with self.assertRaises(ValueError):
            self.make_job(description="")
        with self.assertRaises(ValueError):
            self.make_job(priority="urgent!")

    def test_jobs_persist_and_reload(self):
        job = self.make_job()
        self.jobs.load()
        self.assertEqual(self.jobs.get(job["id"])["status"], "Draft")


class TestJobLifecycle(Base):
    def test_full_lifecycle_with_approval(self):
        job = self.make_job(approval_required=True)
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing",
                       "Needs Chris Approval", "Approved", "Completed", "Archived"]:
            job = self.jobs.advance(job["id"], status)
            self.assertEqual(job["status"], status)
        self.assertEqual([h["status"] for h in job["history"]], s.JOB_STAGES)

    def test_cannot_skip_needs_chris_approval_when_required(self):
        job = self.make_job(approval_required=True)
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing"]:
            job = self.jobs.advance(job["id"], status)
        self.assertEqual(self.jobs.allowed_next(job), ["Needs Chris Approval"])
        with self.assertRaises(s.IllegalTransition):
            self.jobs.advance(job["id"], "Completed")
        with self.assertRaises(s.IllegalTransition):
            self.jobs.advance(job["id"], "Approved")

    def test_no_approval_path_skips_straight_to_completed(self):
        job = self.make_job(approval_required=False)
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing"]:
            job = self.jobs.advance(job["id"], status)
        self.assertEqual(self.jobs.allowed_next(job), ["Completed"])
        job = self.jobs.advance(job["id"], "Completed")
        self.assertEqual(job["status"], "Completed")

    def test_archived_is_terminal(self):
        job = self.make_job()
        job = self.jobs.advance(job["id"], "Planning")
        # walk a no-approval job all the way to Archived
        job = self.make_job(approval_required=False)
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing",
                       "Completed", "Archived"]:
            job = self.jobs.advance(job["id"], status)
        self.assertEqual(self.jobs.allowed_next(job), [])
        with self.assertRaises(s.IllegalTransition):
            self.jobs.advance(job["id"], "Completed")

    def test_completed_can_archive(self):
        job = self.make_job(approval_required=False)
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing",
                       "Completed"]:
            job = self.jobs.advance(job["id"], status)
        self.assertEqual(self.jobs.allowed_next(job), ["Archived"])

    def test_building_can_escalate_directly_to_needs_chris(self):
        job = self.make_job()
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building"]:
            job = self.jobs.advance(job["id"], status)
        job = self.jobs.advance(job["id"], "Needs Chris Approval")
        self.assertEqual(job["status"], "Needs Chris Approval")

    def test_illegal_jump_refused(self):
        job = self.make_job()
        with self.assertRaises(s.IllegalTransition):
            self.jobs.advance(job["id"], "Building")

    def test_draft_can_be_deleted_only_if_never_sent(self):
        job = self.make_job()
        deleted = self.jobs.delete(job["id"])
        self.assertTrue(deleted["deleted"])
        with self.assertRaises(s.NotFound):
            self.jobs.get(job["id"])

    def test_non_draft_job_cannot_be_deleted(self):
        job = self.make_job()
        job = self.jobs.advance(job["id"], "Planning")
        with self.assertRaises(s.IllegalTransition):
            self.jobs.delete(job["id"])

    def test_all_transitions_written_to_ledger(self):
        job = self.make_job()
        self.jobs.advance(job["id"], "Planning")
        with open(s.JOBS_PATH) as f:
            lines = f.read().strip().splitlines()
        self.assertGreaterEqual(len(lines), 2)


class TestPromptGeneration(Base):
    def test_chatgpt_prompt_includes_required_sections(self):
        job = self.make_job(title="Add search", description="Add search to the site")
        project = self.projects["aubs-os"]
        p = s.build_chatgpt_planning_prompt(job, project)
        for expected in ("aubs-os", "Add search", "Add search to the site", "normal",
                         "SAFETY RAILS", "Prompt for Claude Code"):
            self.assertIn(expected, p)

    def test_claude_prompt_includes_all_safety_rails_and_status_format(self):
        job = self.make_job()
        project = self.projects["aubs-os"]
        p = s.build_claude_code_build_prompt(job, project)
        for rail in s.SAFETY_RAILS:
            self.assertIn(rail, p)
        self.assertIn("FILES ALLOWED", p)
        self.assertIn("FILES NOT ALLOWED", p)
        self.assertIn("STATUS:", p)
        self.assertIn("NEEDS APPROVAL:", p)
        self.assertIn(project["repo_path"], p)

    def test_safety_notes_appended_to_claude_prompt(self):
        job = self.make_job(safety_notes="Never touch the billing table.")
        p = s.build_claude_code_build_prompt(job, self.projects["aubs-os"])
        self.assertIn("Never touch the billing table.", p)

    def test_files_not_allowed_scopes_to_one_project(self):
        job = self.make_job()
        p = s.build_claude_code_build_prompt(job, self.projects["aubs-os"])
        self.assertIn("Do not modify any other project", p)


class TestClaudeReportIngestion(Base):
    GOOD_REPORT = """STATUS: Complete
FILES CHANGED: studio.py, dashboard.html
TESTS: 40/40 passed
COMMIT: abc1234
PR: none
BLOCKERS: none
NEXT ACTION: ship it
NEEDS APPROVAL: no"""

    def test_well_formed_report_parses_fully(self):
        fields, ok = s.parse_claude_report(self.GOOD_REPORT)
        self.assertTrue(ok)
        self.assertEqual(fields["status"], "Complete")
        self.assertEqual(fields["commit"], "abc1234")
        self.assertFalse(fields["needs_approval"])

    def test_malformed_report_stores_raw_without_crashing(self):
        raw = "Hey, I finished the thing, mostly. Some tests broke though."
        fields, ok = s.parse_claude_report(raw)
        self.assertFalse(ok)
        self.assertIsNone(fields["status"])

    def test_ingest_stores_raw_and_parsed_fields(self):
        job = self.make_job()
        rec = self.reports.ingest(job["id"], job["project"], self.GOOD_REPORT)
        self.assertTrue(rec["parsed_ok"])
        self.assertEqual(rec["commit"], "abc1234")
        self.assertEqual(rec["raw"], self.GOOD_REPORT)

    def test_manual_override_fills_gaps_after_imperfect_parse(self):
        job = self.make_job()
        raw = "finished, tests pass, commit deadbeef"
        rec = self.reports.ingest(job["id"], job["project"], raw,
                                  manual={"status": "Complete", "commit": "deadbeef"})
        self.assertFalse(rec["parsed_ok"])   # the auto-parse still failed...
        self.assertEqual(rec["status"], "Complete")   # ...but manual fields win
        self.assertEqual(rec["commit"], "deadbeef")


class TestChatGPTPlanIngestion(Base):
    def test_ingestion_stores_all_five_fields_as_a_note(self):
        job = self.make_job()
        note = self.notes.create(project=job["project"], job_id=job["id"],
                                 note_type="chatgpt_plan_response", content="summary",
                                 plan_summary="summary", recommended_prompt="do X",
                                 risks="might break Y", decisions="use approach Z",
                                 next_step="start with the schema")
        self.assertEqual(note["plan_summary"], "summary")
        self.assertEqual(note["next_step"], "start with the schema")

    def test_ingestion_seeds_risks_and_decisions_when_provided(self):
        job = self.make_job()
        self.risks.create(job["project"], "might break Y", job_id=job["id"],
                          source="chatgpt_plan_ingestion")
        self.decisions.create(job["project"], "use approach Z", job_id=job["id"],
                              source="chatgpt_plan_ingestion")
        self.assertEqual(len(self.risks.list(project=job["project"])), 1)
        self.assertEqual(len(self.decisions.list(project=job["project"])), 1)


class TestFounderReport(Base):
    def test_founder_report_generates_all_five_files(self):
        self.make_job()
        studio_state = s.load_studio_state()
        files = s.write_all_reports(self.projects, self.jobs, self.reports,
                                    self.decisions, self.risks, studio_state)
        self.assertEqual(set(files), {"FOUNDER_REPORT.md", "CURRENT_STATUS.md",
                                      "NEXT_ACTION.md", "WAITING_ON_CHRIS.md", "RISKS.md"})
        for name in files:
            self.assertTrue(os.path.exists(os.path.join(s.REPORTS_DIR, name)))

    def test_founder_report_reflects_needs_chris_and_recommends_action(self):
        job = self.make_job()
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building",
                       "Needs Chris Approval"]:
            job = self.jobs.advance(job["id"], status)
        data = s.build_founder_report_data(self.projects, self.jobs, self.reports,
                                           self.decisions, self.risks, {"active_project": None})
        self.assertEqual(len(data["needs_chris"]), 1)
        md = s.render_founder_report_markdown(data)
        self.assertIn("Needs Chris Approval", md)

    def test_founder_report_data_is_pure_no_subprocess(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        # the founder-report DATA functions must never shell out; only the
        # separate scripts/founder-report.sh wrapper touches git, in bash.
        self.assertNotIn("import subprocess", src)


class TestPlanningRoom(Base):
    def test_room_creation_requires_an_idea(self):
        room = self.rooms.create("aubs-os", "Add a public status page")
        self.assertEqual(room["status"], "Idea")
        with self.assertRaises(ValueError):
            self.rooms.create("aubs-os", "   ")

    def test_paste_chatgpt_response_advances_status(self):
        room = self.rooms.create("aubs-os", "idea")
        room = self.rooms.paste_chatgpt_response(room["id"], "ChatGPT's take")
        self.assertEqual(room["status"], "ChatGPT Reviewed")
        self.assertEqual(room["chatgpt_response"], "ChatGPT's take")

    def test_paste_claude_response_advances_status(self):
        room = self.rooms.create("aubs-os", "idea")
        room = self.rooms.paste_chatgpt_response(room["id"], "x")
        room = self.rooms.paste_claude_response(room["id"], "Claude's take")
        self.assertEqual(room["status"], "Claude Reviewed")

    def test_paste_council_response_moves_to_council_needed(self):
        room = self.rooms.create("aubs-os", "idea")
        room = self.rooms.paste_chatgpt_response(room["id"], "x")
        room = self.rooms.paste_claude_response(room["id"], "y")
        room = self.rooms.paste_council_response(room["id"], "Grok", "council take")
        self.assertEqual(room["status"], "Council Needed")
        self.assertEqual(len(room["council_responses"]), 1)

    def test_council_prompt_includes_all_required_elements(self):
        room = self.rooms.create("aubs-os", "Add a public status page")
        room = self.rooms.paste_chatgpt_response(room["id"], "ChatGPT plan")
        room = self.rooms.paste_claude_response(room["id"], "Claude critique")
        self.rooms.set_disagreements_and_risks(room["id"], disagreements="tech stack choice")
        p = self.rooms.build_council_prompt(room["id"], self.projects["aubs-os"])
        for expected in ("Add a public status page", "ChatGPT plan", "Claude critique",
                        "tech stack choice", "Do not agree blindly", "Challenge assumptions"):
            self.assertIn(expected, p, expected)

    def test_unified_plan_storage_and_walk_without_council(self):
        room = self.rooms.create("aubs-os", "idea")
        room = self.rooms.paste_chatgpt_response(room["id"], "x")
        room = self.rooms.paste_claude_response(room["id"], "y")
        room = self.rooms.generate_unified_plan_draft(room["id"], "the unified plan text")
        self.assertEqual(room["status"], "Unified Plan Ready")
        self.assertEqual(room["unified_plan"], "the unified plan text")

    def test_unified_plan_walk_through_council_logs_council_complete(self):
        room = self.rooms.create("aubs-os", "idea")
        room = self.rooms.paste_chatgpt_response(room["id"], "x")
        room = self.rooms.paste_claude_response(room["id"], "y")
        room = self.rooms.paste_council_response(room["id"], "a", "b")
        room = self.rooms.generate_unified_plan_draft(room["id"], "plan")
        self.assertEqual(room["status"], "Unified Plan Ready")
        statuses = [h["status"] for h in room["history"]]
        self.assertIn("Council Complete", statuses)

    def test_build_prompt_blocked_before_signoff(self):
        room = self.rooms.create("aubs-os", "idea")
        room = self.rooms.paste_chatgpt_response(room["id"], "x")
        room = self.rooms.paste_claude_response(room["id"], "y")
        room = self.rooms.generate_unified_plan_draft(room["id"], "plan")
        self.assertFalse(self.rooms.can_generate_build_prompt(room))
        with self.assertRaises(s.IllegalTransition):
            self.rooms.generate_claude_code_build_prompt(room["id"], "aubs-os")

    def test_build_prompt_allowed_after_signoff_and_links_a_job(self):
        room = self.rooms.create("aubs-os", "Add a public status page")
        room = self.rooms.paste_chatgpt_response(room["id"], "x")
        room = self.rooms.paste_claude_response(room["id"], "y")
        room = self.rooms.generate_unified_plan_draft(room["id"], "the unified plan")
        room = self.rooms.chris_approved(room["id"], "go")
        self.assertEqual(room["status"], "Chris Signed Off")
        self.assertTrue(self.rooms.can_generate_build_prompt(room))
        job, room = self.rooms.generate_claude_code_build_prompt(room["id"], "aubs-os")
        self.assertEqual(job["description"], "the unified plan")
        self.assertEqual(room["linked_job_id"], job["id"])
        self.assertEqual(room["status"], "Ready for Claude Code")

    def test_emergency_skip_bypasses_the_whole_ladder(self):
        room = self.rooms.create("aubs-os", "Just ship this now")
        room = self.rooms.emergency_skip(room["id"], "production is down")
        self.assertEqual(room["status"], "Ready for Claude Code")
        self.assertTrue(self.rooms.can_generate_build_prompt(room))
        self.assertTrue(room["emergency_skip"]["engaged"])
        job, room = self.rooms.generate_claude_code_build_prompt(room["id"], "aubs-os")
        self.assertEqual(job["description"], "Just ship this now")   # fell back to the idea text

    def test_illegal_planning_transition_refused(self):
        room = self.rooms.create("aubs-os", "idea")
        with self.assertRaises(s.IllegalTransition):
            self.rooms._advance(room["id"], "Chris Signed Off")

    def test_generate_unified_plan_before_council_complete_still_advances_cleanly(self):
        # a room that got Council Needed but Chris decides to skip straight to
        # drafting the unified plan must still work (Council Complete is a
        # pass-through, not a hard gate).
        room = self.rooms.create("aubs-os", "idea")
        room = self.rooms.paste_chatgpt_response(room["id"], "x")
        room = self.rooms.paste_claude_response(room["id"], "y")
        room = self.rooms.paste_council_response(room["id"], "a", "b")
        self.assertEqual(room["status"], "Council Needed")
        room = self.rooms.generate_unified_plan_draft(room["id"], "final plan")
        self.assertEqual(room["status"], "Unified Plan Ready")


class TestWhereAreWeAndContinuity(Base):
    def test_where_are_we_reflects_state(self):
        room = self.rooms.create("aubs-os", "idea")
        room = self.rooms.paste_chatgpt_response(room["id"], "ChatGPT direction text")
        s.update_project(self.projects, "aubs-os", current_goal="ship v1")
        text = s.build_where_are_we("aubs-os", self.projects, self.jobs, self.reports,
                                    self.decisions, self.rooms)
        self.assertIn("ship v1", text)
        self.assertIn("ChatGPT direction text", text)

    def test_continuity_packet_includes_all_required_sections(self):
        job = self.make_job()
        self.decisions.create("aubs-os", "use approach Z")
        self.risks.create("aubs-os", "might break Y")
        self.reports.ingest(job["id"], "aubs-os", TestClaudeReportIngestion.GOOD_REPORT)
        packet = s.build_continuity_packet("aubs-os", self.projects, self.jobs, self.reports,
                                           self.decisions, self.risks, self.rooms)
        for expected in ("MISSION", "CURRENT STATUS", "RECENT DECISIONS", "use approach Z",
                        "CURRENT PLAN", "OPEN RISKS", "might break Y", "NEXT ACTION",
                        "LATEST CLAUDE CODE REPORT", "COMMIT: abc1234"):
            self.assertIn(expected, packet)


class TestNeedsChris(Base):
    def test_needs_chris_status_flagged(self):
        job = self.make_job()
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building",
                       "Needs Chris Approval"]:
            job = self.jobs.advance(job["id"], status)
        items = s.needs_chris_items(self.jobs, self.reports)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["project"], "aubs-os")

    def test_report_keywords_flag_needs_chris(self):
        job = self.make_job()
        self.reports.ingest(job["id"], job["project"],
                            "STATUS: Blocked\nBLOCKERS: waiting on a merge decision\n"
                            "NEEDS APPROVAL: yes")
        items = s.needs_chris_items(self.jobs, self.reports)
        reasons = [it["reason"] for it in items]
        self.assertTrue(any("approval" in r.lower() for r in reasons))
        self.assertTrue(any("blocker" in r.lower() for r in reasons))

    def test_no_items_when_nothing_needs_chris(self):
        self.make_job()
        self.assertEqual(s.needs_chris_items(self.jobs, self.reports), [])


class TestNoAutomation(Base):
    """Coordination-only, test-enforced: no AI/GitHub/SSH/deploy/service-mgmt
    surface anywhere in the core module or the dashboard. One narrow,
    explicitly-approved exception exists — build artifact verification may GET
    a build's own recorded test_url, but ONLY when it resolves to localhost, a
    private-LAN address, or Tailscale's CGNAT range — see docs/SAFETY_RAILS.md."""

    def test_no_subprocess_socket_or_arbitrary_network_in_studio_module(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        for forbidden in ("import subprocess", "os.system", "Popen", "socket.socket",
                          "requests."):
            self.assertNotIn(forbidden, src, f"studio.py contains {forbidden}")
        # urllib.request IS present, but only for the approved verification
        # exception, and only guarded by the local/Tailscale host check —
        # both guards must still exist in source.
        self.assertIn("urllib.request", src)
        self.assertIn("100.64.0.0/10", src)   # Tailscale CGNAT range guard
        self.assertIn("_is_local_or_tailscale_host", src)

    def test_no_ai_or_github_api_endpoints_anywhere(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        html = open(os.path.join(ROOT, "dashboard.html")).read()
        for forbidden in ("api.openai.com", "api.anthropic.com", "api.github.com",
                          "x.ai/api", "generativelanguage.googleapis"):
            self.assertNotIn(forbidden, src)
            self.assertNotIn(forbidden, html)

    def test_no_service_management_or_ssh(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        for forbidden in ("pm2", "systemctl", "systemd", "paramiko", "ssh "):
            self.assertNotIn(forbidden, src)

    def test_no_live_mode_concept_exists(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        self.assertIn('MODE = os.environ.get("GNG_STUDIO_MODE", "dry-run")', src)
        # no branch anywhere in the module ever checks MODE against a "live" value
        self.assertNotIn('MODE ==', src)
        self.assertNotIn('MODE !=', src)

    def test_dashboard_has_no_external_hosts(self):
        html = open(os.path.join(ROOT, "dashboard.html")).read()
        self.assertNotIn("https://", html)


class TestHTTP(Base):
    def setUp(self):
        super().setUp()
        s.build_app_state()
        s.Handler.projects = self.projects
        s.Handler.jobs = self.jobs
        s.Handler.reports = self.reports
        s.Handler.decisions = self.decisions
        s.Handler.risks = self.risks
        s.Handler.notes = self.notes
        s.Handler.rooms = self.rooms
        self.server = s.ThreadingHTTPServer(("127.0.0.1", 0), s.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def _req(self, path, payload=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = (urllib.request.Request(url) if payload is None else
              urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}))
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_health(self):
        code, body = self._req("/health")
        self.assertEqual((code, body["mode"]), (200, "dry-run"))
        self.assertEqual(body["projects"], 11)

    def test_create_job_and_generate_prompts_via_api(self):
        code, job = self._req("/api/jobs", {"project": "aubs-os", "title": "t",
                                            "description": "d"})
        self.assertEqual(code, 201)
        code, r1 = self._req(f"/api/jobs/{job['id']}/generate-chatgpt-prompt", {})
        self.assertEqual(code, 200)
        self.assertIn("Prompt for Claude Code", r1["prompt"])
        self.assertEqual(r1["job"]["status"], "Ready for ChatGPT")
        code, r2 = self._req(f"/api/jobs/{job['id']}/generate-claude-prompt", {})
        self.assertEqual(code, 200)
        self.assertIn("STATUS:", r2["prompt"])
        self.assertEqual(r2["job"]["status"], "Ready for Claude")

    def test_claude_report_ingestion_endpoint(self):
        _, job = self._req("/api/jobs", {"project": "aubs-os", "title": "t", "description": "d"})
        code, rec = self._req(f"/api/jobs/{job['id']}/claude-report",
                              {"raw": TestClaudeReportIngestion.GOOD_REPORT})
        self.assertEqual((code, rec["commit"]), (201, "abc1234"))

    def test_planning_room_full_flow_via_api(self):
        code, room = self._req("/api/planning-rooms", {"project": "aubs-os",
                                                        "chris_idea": "idea"})
        self.assertEqual(code, 201)
        code, room = self._req(f"/api/planning-rooms/{room['id']}/chatgpt-response",
                               {"text": "x"})
        self.assertEqual((code, room["status"]), (200, "ChatGPT Reviewed"))
        code, room = self._req(f"/api/planning-rooms/{room['id']}/claude-response",
                               {"text": "y"})
        self.assertEqual(room["status"], "Claude Reviewed")
        code, prompt = self._req(f"/api/planning-rooms/{room['id']}/council-prompt")
        self.assertEqual(code, 200)
        self.assertIn("Do not agree blindly", prompt["prompt"])
        code, room = self._req(f"/api/planning-rooms/{room['id']}/unified-plan", {"text": "plan"})
        self.assertEqual(room["status"], "Unified Plan Ready")
        code, denied = self._req(f"/api/planning-rooms/{room['id']}/build-prompt", {})
        self.assertEqual(code, 403)
        code, room = self._req(f"/api/planning-rooms/{room['id']}/chris-approved", {})
        self.assertEqual(room["status"], "Chris Signed Off")
        code, out = self._req(f"/api/planning-rooms/{room['id']}/build-prompt", {})
        self.assertEqual(code, 200)
        self.assertIn("STATUS:", out["prompt"])

    def test_founder_report_endpoint(self):
        code, data = self._req("/api/founder-report")
        self.assertEqual(code, 200)
        self.assertIn("projects", data)

    def test_needs_chris_endpoint(self):
        code, data = self._req("/api/needs-chris")
        self.assertEqual((code, data["items"]), (200, []))

    def test_unknown_paths_404(self):
        self.assertEqual(self._req("/api/nope")[0], 404)

    def test_search_endpoint(self):
        self._req("/api/jobs", {"project": "veracore", "title": "Findable qwerty123",
                                "description": "d"})
        code, r = self._req("/api/search?q=qwerty123")
        self.assertEqual((code, len(r["jobs"])), (200, 1))

    def test_inbox_endpoint(self):
        code, r = self._req("/api/jobs", {"project": "veracore", "title": "t", "description": "d"})
        self._req(f"/api/jobs/{r['id']}/generate-chatgpt-prompt", {})
        code, inbox = self._req("/api/inbox")
        self.assertEqual(code, 200)
        self.assertTrue(any(it["id"] == r["id"] for it in inbox["items"]))

    def test_timeline_endpoint(self):
        self._req("/api/jobs", {"project": "veracore", "title": "t", "description": "d"})
        code, t = self._req("/api/project/veracore/timeline")
        self.assertEqual(code, 200)
        self.assertGreater(len(t["events"]), 0)

    def test_continue_prompt_endpoint(self):
        code, r = self._req("/api/project/veracore/continue-prompt")
        self.assertEqual(code, 200)
        self.assertIn("Use Claude Code.", r["text"])

    def test_job_reject_endpoint(self):
        _, job = self._req("/api/jobs", {"project": "veracore", "title": "t", "description": "d"})
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing",
                       "Needs Chris Approval"]:
            self._req(f"/api/jobs/{job['id']}/status", {"status": status})
        code, r = self._req(f"/api/jobs/{job['id']}/reject", {"reason": "redo"})
        self.assertEqual((code, r["status"]), (200, "Building"))

    def test_project_file_get_and_post(self):
        code, r = self._req("/api/project/veracore/file?name=MISSION.md")
        self.assertEqual(code, 200)
        code, r = self._req("/api/project/veracore/file",
                            {"name": "ARCHITECTURE.md", "content": "New architecture text."})
        self.assertEqual(code, 200)
        code, r = self._req("/api/project/veracore/file?name=ARCHITECTURE.md")
        self.assertEqual(r["content"], "New architecture text.")
        code, r = self._req("/api/project/veracore/file",
                            {"name": "DECISIONS.md", "content": "should be refused"})
        self.assertEqual(code, 400)

    def test_next_action_endpoint(self):
        code, r = self._req("/api/project/veracore/next-action", {"text": "ship it"})
        self.assertEqual(code, 200)
        code, r = self._req("/api/project/veracore/file?name=NEXT_ACTION.md")
        self.assertEqual(r["content"], "ship it")


# ─── project folders: persistent, file-based per-project memory ──────────────

REQUIRED_TOP_FILES = ["PROJECT_STATE.md", "MISSION.md", "ARCHITECTURE.md",
                      "DECISIONS.md", "RISKS.md", "ROADMAP.md", "NEXT_ACTION.md"]
REQUIRED_SUBDIRS = ["CHATGPT", "CHATGPT/History", "CLAUDE", "CLAUDE/History",
                   "COUNCIL", "COUNCIL/History", "REPORTS", "PRS", "SCREENSHOTS", "FILES"]


def _history_count(history_dir):
    """Entry count excluding the .gitkeep placeholder that makes the (git-tracked)
    empty History/ directory actually persist once committed."""
    return len([f for f in os.listdir(history_dir) if f != ".gitkeep"])


class TestProjectFolders(Base):
    """1: project folders created for every registered project."""

    def test_folder_created_for_every_registered_project(self):
        for pid in self.projects:
            self.assertTrue(os.path.isdir(s.project_dir(pid)), pid)
        self.assertEqual(len(self.projects), 11)


class TestRequiredFiles(Base):
    """2: required files created."""

    def test_all_seven_top_level_files_exist_for_every_project(self):
        for pid in self.projects:
            for fname in REQUIRED_TOP_FILES:
                path = os.path.join(s.project_dir(pid), fname)
                self.assertTrue(os.path.exists(path), f"{pid}/{fname} missing")

    def test_seed_content_is_not_empty_for_mission_and_next_action(self):
        content = s._read_project_file("aubs-os", "MISSION.md")
        self.assertIn("Mission", content)
        # aubs-os seeds a real next_action string
        self.assertNotEqual(s._read_project_file("aubs-os", "NEXT_ACTION.md"), "")


class TestRequiredSubfolders(Base):
    """3: required subfolders created."""

    def test_all_required_subfolders_exist_for_every_project(self):
        for pid in self.projects:
            for sub in REQUIRED_SUBDIRS:
                path = os.path.join(s.project_dir(pid), sub)
                self.assertTrue(os.path.isdir(path), f"{pid}/{sub} missing")

    def test_ensure_is_idempotent_and_never_clobbers_hand_edits(self):
        s._write_project_file("aubs-os", "MISSION.md", "Chris hand-edited this mission.")
        s.ensure_project_folder("aubs-os", self.projects["aubs-os"])
        s.ensure_all_project_folders(self.projects)
        self.assertEqual(s._read_project_file("aubs-os", "MISSION.md"),
                         "Chris hand-edited this mission.")


class TestProjectStateHeadings(Base):
    """4: PROJECT_STATE.md contains all required headings."""

    def test_skeleton_has_all_headings_on_first_boot(self):
        content = s._read_project_file("aubs-os", "PROJECT_STATE.md")
        for heading in s.PROJECT_STATE_HEADINGS:
            self.assertIn(f"## {heading}", content, heading)

    def test_regenerated_state_still_has_all_headings(self):
        self.make_job(project="aubs-os")
        content = s.sync_project_state("aubs-os", self.projects, self.jobs, self.reports,
                                       self.decisions, self.risks, self.rooms)
        for heading in s.PROJECT_STATE_HEADINGS:
            self.assertIn(f"## {heading}", content, heading)
        self.assertEqual(s._read_project_file("aubs-os", "PROJECT_STATE.md"), content)


class TestNoOrphanJobs(Base):
    """5: no orphan jobs allowed — every job/note/risk/decision/report/room must
    be attached to exactly one registered project."""

    def test_job_requires_a_known_project(self):
        with self.assertRaises(ValueError):
            self.jobs.create(project="", title="t", description="d")
        with self.assertRaises(ValueError):
            self.jobs.create(project="not-a-real-project", title="t", description="d")

    def test_note_risk_decision_report_room_all_require_a_known_project(self):
        with self.assertRaises(ValueError):
            self.notes.create(project="ghost", note_type="architecture_note", content="x")
        with self.assertRaises(ValueError):
            self.risks.create(project="ghost", description="x")
        with self.assertRaises(ValueError):
            self.decisions.create(project="ghost", text="x")
        with self.assertRaises(ValueError):
            self.reports.ingest(job_id="j1", project="ghost", raw="STATUS: Complete")
        with self.assertRaises(ValueError):
            self.rooms.create(project="ghost", chris_idea="x")

    def test_valid_project_succeeds(self):
        job = self.make_job(project="veracore")
        self.assertEqual(job["project"], "veracore")


class TestChatGPTPlanWritesToFolder(Base):
    """6: ChatGPT plan writes to project folder."""

    def test_planning_room_chatgpt_response_writes_current_plan_and_history(self):
        room = self.rooms.create("veracore", "idea")
        self.rooms.paste_chatgpt_response(room["id"], "Use a static dashboard.")
        current = s._read_project_file("veracore", os.path.join("CHATGPT", "Current_Plan.md"))
        self.assertEqual(current, "Use a static dashboard.")
        history_dir = os.path.join(s.project_dir("veracore"), "CHATGPT", "History")
        self.assertEqual(_history_count(history_dir), 1)

    def test_job_level_chatgpt_plan_ingestion_writes_current_plan(self):
        job = self.make_job(project="veracore")
        formatted = "## ChatGPT Plan Response\n\nSummary: do X\n"
        s.save_chatgpt_plan_to_folder(job["project"], formatted)
        self.assertIn("do X", s._read_project_file("veracore", os.path.join("CHATGPT", "Current_Plan.md")))

    def test_multiple_pastes_accumulate_history_but_overwrite_current(self):
        room = self.rooms.create("veracore", "idea")
        self.rooms.paste_chatgpt_response(room["id"], "first")
        self.rooms.paste_chatgpt_response(room["id"], "second")
        self.assertEqual(s._read_project_file("veracore", os.path.join("CHATGPT", "Current_Plan.md")), "second")
        history_dir = os.path.join(s.project_dir("veracore"), "CHATGPT", "History")
        self.assertEqual(_history_count(history_dir), 2)


class TestClaudeReportWritesToFolder(Base):
    """7: Claude report writes to project folder."""

    def test_report_ingestion_writes_current_report_and_history(self):
        job = self.make_job(project="veracore")
        self.reports.ingest(job["id"], job["project"], TestClaudeReportIngestion.GOOD_REPORT)
        current = s._read_project_file("veracore", os.path.join("CLAUDE", "Current_Report.md"))
        self.assertIn("STATUS: Complete", current)
        history_dir = os.path.join(s.project_dir("veracore"), "CLAUDE", "History")
        self.assertEqual(_history_count(history_dir), 1)

    def test_planning_room_claude_response_also_writes_current_report(self):
        room = self.rooms.create("veracore", "idea")
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "Independent critique text.")
        self.assertEqual(s._read_project_file("veracore", os.path.join("CLAUDE", "Current_Report.md")),
                         "Independent critique text.")


class TestCouncilNotesWriteToFolder(Base):
    """8: Council notes write to project folder."""

    def test_council_response_writes_latest_and_history(self):
        room = self.rooms.create("veracore", "idea")
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        self.rooms.paste_council_response(room["id"], "Grok", "council take")
        latest = s._read_project_file("veracore", os.path.join("COUNCIL", "Latest.md"))
        self.assertIn("Grok", latest)
        self.assertIn("council take", latest)
        history_dir = os.path.join(s.project_dir("veracore"), "COUNCIL", "History")
        self.assertEqual(_history_count(history_dir), 1)


class TestDecisionsWriteToFolder(Base):
    """9: decisions write to project folder."""

    def test_decision_appends_to_decisions_md(self):
        self.decisions.create("veracore", "Use approach Z")
        content = s._read_project_file("veracore", "DECISIONS.md")
        self.assertIn("Use approach Z", content)

    def test_multiple_decisions_all_preserved(self):
        self.decisions.create("veracore", "decision one")
        self.decisions.create("veracore", "decision two")
        content = s._read_project_file("veracore", "DECISIONS.md")
        self.assertIn("decision one", content)
        self.assertIn("decision two", content)


class TestRisksWriteToFolder(Base):
    """10: risks write to project folder."""

    def test_risk_appends_to_risks_md(self):
        self.risks.create("veracore", "Might break Y")
        content = s._read_project_file("veracore", "RISKS.md")
        self.assertIn("Might break Y", content)


class TestNextActionWritesToFolder(Base):
    """11: next action writes to project folder."""

    def test_write_next_action_helper(self):
        s.write_next_action_to_folder("veracore", "Ship the dashboard")
        self.assertEqual(s._read_project_file("veracore", "NEXT_ACTION.md"), "Ship the dashboard")

    def test_next_action_overwrites_not_appends(self):
        s.write_next_action_to_folder("veracore", "first")
        s.write_next_action_to_folder("veracore", "second")
        content = s._read_project_file("veracore", "NEXT_ACTION.md")
        self.assertEqual(content, "second")
        self.assertNotIn("first", content)


class TestWhereAreWeUsesFolderData(Base):
    """12: Where Are We uses project folder data."""

    def test_where_are_we_reflects_folder_files_not_just_jsonl(self):
        s.save_chatgpt_plan_to_folder("veracore", "Folder-sourced ChatGPT plan text.")
        text = s.build_where_are_we("veracore", self.projects, self.jobs, self.reports,
                                    self.decisions, self.rooms)
        self.assertIn("Folder-sourced ChatGPT plan text.", text)

    def test_where_are_we_reflects_decisions_file(self):
        self.decisions.create("veracore", "a durable decision")
        text = s.build_where_are_we("veracore", self.projects, self.jobs, self.reports,
                                    self.decisions, self.rooms)
        self.assertIn("a durable decision", text)


class TestStartNewChatPacketUsesFolderData(Base):
    """13: Start New Chat Packet uses project folder data."""

    def test_packet_includes_who_chris_is_and_folder_sourced_fields(self):
        s._write_project_file("veracore", "ARCHITECTURE.md", "Static site, no backend.")
        self.decisions.create("veracore", "keep it static")
        s.write_next_action_to_folder("veracore", "ship v1")
        text = s.build_start_new_chat_packet("veracore", self.projects, self.jobs, self.reports,
                                             self.decisions, self.risks, self.rooms)
        for expected in ("WHO CHRIS IS", "Static site, no backend.", "keep it static",
                        "ship v1", "WHAT NOT TO WORK ON"):
            self.assertIn(expected, text, expected)

    def test_packet_warns_off_other_hands_off_projects(self):
        text = s.build_start_new_chat_packet("veracore", self.projects, self.jobs, self.reports,
                                             self.decisions, self.risks, self.rooms)
        self.assertIn("PathBack", text)
        self.assertIn("LYLO", text)
        self.assertIn("Splendor", text)


class TestContinuityPacketSurvivesRestart(Base):
    """14: continuity packet survives restart — sourced from files on disk, so a
    freshly constructed set of stores (simulating a process restart) reproduces
    it without needing the original in-memory objects."""

    def test_continuity_packet_rebuilds_after_simulated_restart(self):
        job = self.make_job(project="veracore", title="Restart test")
        self.reports.ingest(job["id"], "veracore", TestClaudeReportIngestion.GOOD_REPORT)
        self.decisions.create("veracore", "durable decision")
        self.risks.create("veracore", "durable risk")
        before = s.build_continuity_packet("veracore", self.projects, self.jobs, self.reports,
                                           self.decisions, self.risks, self.rooms)

        # simulate a restart: brand-new store instances reloading from the same files
        projects2 = s.load_projects()
        jobs2 = s.JobStore(projects2)
        reports2 = s.ReportStore(projects2)
        decisions2 = s.DecisionStore(projects2)
        risks2 = s.RiskStore(projects2)
        notes2 = s.NoteStore(projects2)
        rooms2 = s.PlanningRoomStore(projects2, jobs2, notes2)
        after = s.build_continuity_packet("veracore", projects2, jobs2, reports2,
                                          decisions2, risks2, rooms2)
        self.assertEqual(before, after)
        self.assertIn("durable decision", after)
        self.assertIn("COMMIT: abc1234", after)


class TestFounderReportIncludesProjectFolder(Base):
    """15: founder report includes active project folder."""

    def test_founder_report_names_the_active_project_folder_and_state(self):
        s.set_active_project("veracore")
        studio_state = s.load_studio_state()
        s.sync_project_state("veracore", self.projects, self.jobs, self.reports,
                             self.decisions, self.risks, self.rooms)
        data = s.build_founder_report_data(self.projects, self.jobs, self.reports,
                                           self.decisions, self.risks, studio_state)
        self.assertEqual(data["active_project_folder"], s.project_dir("veracore"))
        self.assertIn("Veracore", data["project_state"])
        md = s.render_founder_report_markdown(data)
        self.assertIn(s.project_dir("veracore"), md)
        self.assertIn("## Current Project State", md)
        self.assertIn("## Latest Council Notes", md)

    def test_founder_report_with_no_active_project_is_graceful(self):
        studio_state = {"active_project": None}
        data = s.build_founder_report_data(self.projects, self.jobs, self.reports,
                                           self.decisions, self.risks, studio_state)
        self.assertIsNone(data["active_project_folder"])
        md = s.render_founder_report_markdown(data)
        self.assertIn("no active project set", md)


class TestNoAPIsOrAutomationAdded(Base):
    """16-18: no APIs, no deployment automation, no PM2/systemd/SSH/GitHub
    automation added by this feature. Repo creation/push, when it happens, is
    an operator/session-level git action outside this codebase — never code
    inside studio.py or dashboard.html."""

    def test_no_ai_api_hosts_in_project_folder_code_paths(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        for forbidden in ("api.openai.com", "api.anthropic.com", "x.ai/api",
                          "generativelanguage.googleapis"):
            self.assertNotIn(forbidden, src)

    def test_no_deployment_or_service_management_keywords(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        for forbidden in ("pm2", "systemctl", "systemd", "paramiko", "docker",
                          "kubectl"):
            self.assertNotIn(forbidden, src)

    def test_no_github_api_calls_in_studio_module(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        self.assertNotIn("api.github.com", src)
        self.assertNotIn("import requests", src)

    def test_project_folder_writes_are_plain_file_io_only(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        self.assertNotIn("import subprocess", src)
        self.assertNotIn("Popen", src)


# ─── job reject (send back) ────────────────────────────────────────────────────

class TestJobReject(Base):
    def test_reject_only_legal_from_needs_chris_approval(self):
        job = self.make_job()
        with self.assertRaises(s.IllegalTransition):
            self.jobs.reject(job["id"], "too early")
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing",
                       "Needs Chris Approval"]:
            job = self.jobs.advance(job["id"], status)
        job = self.jobs.reject(job["id"], "needs more tests")
        self.assertEqual(job["status"], "Building")
        self.assertTrue(job["history"][-1]["rejected"])
        self.assertEqual(job["history"][-1]["reason"], "needs more tests")

    def test_rejected_job_can_walk_forward_again(self):
        job = self.make_job()
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing",
                       "Needs Chris Approval"]:
            job = self.jobs.advance(job["id"], status)
        job = self.jobs.reject(job["id"], "redo")
        job = self.jobs.advance(job["id"], "Testing")
        job = self.jobs.advance(job["id"], "Needs Chris Approval")
        job = self.jobs.advance(job["id"], "Approved")
        self.assertEqual(job["status"], "Approved")


# ─── search ─────────────────────────────────────────────────────────────────────

class TestSearch(Base):
    def test_search_finds_jobs_notes_decisions_risks_reports_rooms(self):
        job = self.make_job(project="veracore", title="Findable job title xyzzy")
        self.decisions.create("veracore", "xyzzy decision")
        self.risks.create("veracore", "xyzzy risk")
        self.reports.ingest(job["id"], "veracore", "STATUS: Complete\nxyzzy in the report")
        self.rooms.create("veracore", "xyzzy idea")
        r = s.search_studio("xyzzy", self.projects, self.jobs, self.reports,
                            self.decisions, self.risks, self.notes, self.rooms)
        self.assertEqual(len(r["jobs"]), 1)
        self.assertEqual(len(r["decisions"]), 1)
        self.assertEqual(len(r["risks"]), 1)
        self.assertEqual(len(r["reports"]), 1)
        self.assertEqual(len(r["rooms"]), 1)

    def test_search_finds_hand_edited_project_files(self):
        s._write_project_file("veracore", "ARCHITECTURE.md", "Uses a xyzzy-based cache.")
        r = s.search_studio("xyzzy-based", self.projects, self.jobs, self.reports,
                            self.decisions, self.risks, self.notes, self.rooms)
        self.assertEqual(len(r["files"]), 1)
        self.assertEqual(r["files"][0]["file"], "ARCHITECTURE.md")
        self.assertIn("xyzzy-based", r["files"][0]["snippet"])

    def test_search_is_case_insensitive_and_project_scoped(self):
        self.make_job(project="veracore", title="Findme")
        self.make_job(project="aubs-os", title="findme too")
        r_all = s.search_studio("FINDME", self.projects, self.jobs, self.reports,
                                self.decisions, self.risks, self.notes, self.rooms)
        self.assertEqual(len(r_all["jobs"]), 2)
        r_scoped = s.search_studio("findme", self.projects, self.jobs, self.reports,
                                   self.decisions, self.risks, self.notes, self.rooms,
                                   project="veracore")
        self.assertEqual(len(r_scoped["jobs"]), 1)

    def test_empty_query_returns_nothing(self):
        r = s.search_studio("", self.projects, self.jobs, self.reports,
                            self.decisions, self.risks, self.notes, self.rooms)
        self.assertEqual(r, {"jobs": [], "notes": [], "decisions": [], "risks": [],
                            "reports": [], "rooms": [], "files": []})


# ─── timeline ───────────────────────────────────────────────────────────────────

class TestTimeline(Base):
    def test_timeline_merges_job_and_room_history_newest_first(self):
        job = self.make_job(project="veracore")
        self.jobs.advance(job["id"], "Planning")
        room = self.rooms.create("veracore", "an idea")
        events = s.build_timeline("veracore", self.jobs, self.reports, self.decisions,
                                  self.risks, self.rooms, self.notes)
        kinds = [e["kind"] for e in events]
        self.assertIn("job_status", kinds)
        self.assertIn("planning_room", kinds)
        # newest first
        self.assertTrue(all(events[i]["at"] >= events[i + 1]["at"] for i in range(len(events) - 1)))

    def test_timeline_includes_decisions_risks_and_reports(self):
        job = self.make_job(project="veracore")
        self.decisions.create("veracore", "a decision")
        self.risks.create("veracore", "a risk")
        self.reports.ingest(job["id"], "veracore", "STATUS: Complete")
        events = s.build_timeline("veracore", self.jobs, self.reports, self.decisions,
                                  self.risks, self.rooms, self.notes)
        kinds = {e["kind"] for e in events}
        self.assertTrue({"decision", "risk", "claude_report"} <= kinds)

    def test_timeline_flags_rejected_and_emergency_skip_events(self):
        job = self.make_job(project="veracore")
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing",
                       "Needs Chris Approval"]:
            job = self.jobs.advance(job["id"], status)
        self.jobs.reject(job["id"], "redo this")
        events = s.build_timeline("veracore", self.jobs, self.reports, self.decisions,
                                  self.risks, self.rooms, self.notes)
        self.assertTrue(any("sent back" in e["summary"] for e in events))


# ─── AI inbox ───────────────────────────────────────────────────────────────────

class TestAIInbox(Base):
    def test_inbox_lists_jobs_and_rooms_awaiting_an_exchange(self):
        job = self.make_job(project="veracore")
        self.jobs.advance(job["id"], "Planning")
        self.jobs.advance(job["id"], "Ready for ChatGPT")
        room = self.rooms.create("aubs-os", "idea")
        items = s.build_ai_inbox(self.projects, self.jobs, self.rooms)
        job_items = [it for it in items if it["kind"] == "job"]
        room_items = [it for it in items if it["kind"] == "planning_room"]
        self.assertEqual(len(job_items), 1)
        self.assertEqual(job_items[0]["expected"], s._JOB_INBOX_EXPECTATIONS["Ready for ChatGPT"])
        self.assertEqual(len(room_items), 1)

    def test_inbox_excludes_jobs_not_awaiting_an_exchange(self):
        job = self.make_job(project="veracore", approval_required=False)
        items = s.build_ai_inbox(self.projects, self.jobs, self.rooms)
        self.assertEqual(items, [])  # Draft isn't in the inbox — nothing to copy/paste yet

    def test_inbox_feeds_into_founder_report(self):
        self.make_job(project="veracore")
        job2 = self.jobs.advance(
            self.jobs.create(project="veracore", title="t2", description="d2")["id"], "Planning")
        self.jobs.advance(job2["id"], "Ready for ChatGPT")
        studio_state = {"active_project": None}
        data = s.build_founder_report_data(self.projects, self.jobs, self.reports,
                                           self.decisions, self.risks, studio_state, self.rooms)
        self.assertEqual(len(data["inbox"]), 1)
        md = s.render_founder_report_markdown(data)
        self.assertIn("## AI Inbox", md)


# ─── continue project prompt ───────────────────────────────────────────────────

class TestContinueProjectPrompt(Base):
    def test_prompt_targets_claude_code_and_includes_state_and_last_report(self):
        job = self.make_job(project="veracore", title="Resume this")
        self.reports.ingest(job["id"], "veracore", TestClaudeReportIngestion.GOOD_REPORT)
        s.sync_project_state("veracore", self.projects, self.jobs, self.reports,
                             self.decisions, self.risks, self.rooms)
        text = s.build_continue_project_prompt("veracore", self.projects, self.jobs,
                                               self.reports, self.rooms)
        self.assertIn("Use Claude Code.", text)
        self.assertIn("Resume this", text)
        self.assertIn("COMMIT: abc1234", text)
        self.assertIn("do not restart from scratch", text)
        for rail in s.SAFETY_RAILS:
            self.assertIn(rail, text)

    def test_prompt_handles_no_open_job_gracefully(self):
        text = s.build_continue_project_prompt("veracore", self.projects, self.jobs,
                                               self.reports, self.rooms)
        self.assertIn("No open job", text)


# ─── needs-chris queue is actionable ────────────────────────────────────────────

class TestNeedsChrisActionable(Base):
    def test_job_at_gate_gets_approve_and_send_back_choices(self):
        job = self.make_job(project="veracore")
        for status in ["Planning", "Ready for ChatGPT", "ChatGPT Planned",
                       "Ready for Claude", "Sent to Claude", "Building", "Testing",
                       "Needs Chris Approval"]:
            job = self.jobs.advance(job["id"], status)
        items = s.needs_chris_items(self.jobs, self.reports)
        self.assertEqual(items[0]["job_status"], "Needs Chris Approval")
        self.assertEqual(items[0]["choices"], ["Approve", "Send back for more work"])

    def test_report_triggered_item_for_a_job_not_at_the_gate_only_offers_review(self):
        job = self.make_job(project="veracore")
        self.reports.ingest(job["id"], "veracore",
                            "STATUS: Blocked\nBLOCKERS: waiting on a merge decision\n"
                            "NEEDS APPROVAL: yes")
        items = s.needs_chris_items(self.jobs, self.reports)
        self.assertTrue(all(it["choices"] == ["Review the report"] for it in items))


# ─── project file editor endpoints ─────────────────────────────────────────────

class TestProjectFileEditor(Base):
    def test_get_file_whitelist(self):
        for name in ("MISSION.md", "ARCHITECTURE.md", "ROADMAP.md", "DECISIONS.md",
                    "RISKS.md", "NEXT_ACTION.md", "PROJECT_STATE.md"):
            content = s._read_project_file("veracore", name, "")
            self.assertIsInstance(content, str)


# ─── new-project creation ───────────────────────────────────────────────────────

class TestCreateProject(Base):
    def test_create_new_project_seeds_folder_and_fields(self):
        p = s.create_project(self.projects, "My Test Thing", description="a test",
                             kind="new", local_folder="~/my-test-thing")
        self.assertEqual(p["id"], "my-test-thing")
        self.assertEqual(p["kind"], "new")
        self.assertEqual(p["repo_path"], "~/my-test-thing")
        self.assertTrue(os.path.isdir(s.project_dir("my-test-thing")))
        self.assertIn("my-test-thing", self.projects)

    def test_slug_collisions_get_a_numeric_suffix(self):
        p1 = s.create_project(self.projects, "Widget")
        p2 = s.create_project(self.projects, "Widget")
        self.assertEqual((p1["id"], p2["id"]), ("widget", "widget-2"))

    def test_name_required_and_kind_validated(self):
        with self.assertRaises(ValueError):
            s.create_project(self.projects, "  ")
        with self.assertRaises(ValueError):
            s.create_project(self.projects, "x", kind="sideways")

    def test_slugify_handles_punctuation(self):
        self.assertEqual(s.slugify("Chris's Cool Project 2.0"), "chris-s-cool-project-2-0")
        self.assertEqual(s.slugify("   "), "project")


# ─── guided build orchestration ─────────────────────────────────────────────────

class TestGuidedBuildNoCouncil(Base):
    def test_full_flow_default_ai_team_no_council(self):
        pid = "veracore"
        self.assertEqual(s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)["kind"],
                         "not_started")

        room = s.start_guided_build(pid, self.jobs, self.rooms, "Add a health check")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "chatgpt")
        self.assertIn("Add a health check", stage["prompt"])

        self.rooms.paste_chatgpt_response(room["id"], "Use a /health endpoint.")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "claude")

        self.rooms.paste_claude_response(room["id"], "Agreed, return 200 with a JSON body.")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "plan_review")   # council off by default -> straight here
        self.assertIn("Add a health check", stage["plan_draft"])

        job, room, prompt = s.guided_approve_plan(self.projects, self.rooms, self.notes, pid,
                                                  room["id"], stage["plan_draft"])
        self.assertEqual(room["status"], "Ready for Claude Code")
        self.assertIn("Use Claude Code.", prompt)
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "send_build_prompt")

        s.guided_sent_to_claude_code(self.jobs, stage["job_id"])
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "claude_code_report")

        s.guided_claude_code_report(self.jobs, self.reports, stage["job_id"], pid,
                                    TestClaudeReportIngestion.GOOD_REPORT)
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "test_review")

        s.guided_continue_after_test(self.jobs, self.reports, self.projects, stage["job_id"])
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        # GOOD_REPORT sets NEEDS APPROVAL: no -> Testing goes straight to Completed
        self.assertEqual(stage["kind"], "complete")
        self.assertFalse(stage["needs_final_click"])

    def test_ai_team_skip_chatgpt_and_claude(self):
        pid = "veracore"
        s.start_guided_build(pid, self.jobs, self.rooms, "idea",
                             ai_team={"chatgpt": False, "claude": False})
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "plan_review")   # both skipped -> straight to plan

    def test_claude_code_is_always_mandatory_regardless_of_ai_team(self):
        room = self.rooms.create("veracore", "idea", ai_team={"claude_code": False})
        self.assertTrue(room["ai_team"]["claude_code"])


class TestGuidedBuildConstraintsThreading(Base):
    def test_wizard_constraints_and_safety_notes_reach_the_final_job(self):
        pid = "veracore"
        room = s.start_guided_build(pid, self.jobs, self.rooms, "Add a widget",
                                    constraints="Only touch the widgets/ folder.",
                                    safety_notes="Never touch the payments table.")
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        job, room, prompt = s.guided_approve_plan(self.projects, self.rooms, self.notes, pid,
                                                  room["id"], stage["plan_draft"])
        self.assertIn("Only touch the widgets/ folder.", job["constraints"])
        self.assertEqual(job["safety_notes"], "Never touch the payments table.")
        self.assertIn("Never touch the payments table.", prompt)


class TestGuidedBuildWithCouncil(Base):
    def test_council_choice_appears_when_enabled(self):
        pid = "veracore"
        room = s.start_guided_build(pid, self.jobs, self.rooms, "idea", ai_team={"council": True})
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "council_choice")

    def test_skip_council_from_choice_goes_to_plan_review(self):
        pid = "veracore"
        room = s.start_guided_build(pid, self.jobs, self.rooms, "idea", ai_team={"council": True})
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        s.guided_generate_plan(self.rooms, room["id"])
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "plan_review")

    def test_running_council_then_done_reaches_plan_review_with_council_text(self):
        pid = "veracore"
        room = s.start_guided_build(pid, self.jobs, self.rooms, "idea", ai_team={"council": True})
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "council_choice")
        self.rooms.paste_council_response(room["id"], "Grok", "council insight z")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "council")
        s.guided_generate_plan(self.rooms, room["id"])
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "plan_review")
        self.assertIn("council insight z", stage["plan_draft"])

    def test_approve_plan_uses_edited_text_even_when_already_at_unified_plan_ready(self):
        pid = "veracore"
        room = s.start_guided_build(pid, self.jobs, self.rooms, "idea", ai_team={"council": False})
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        s.guided_generate_plan(self.rooms, room["id"])   # -> Unified Plan Ready
        job, room2, prompt = s.guided_approve_plan(self.projects, self.rooms, self.notes, pid,
                                                    room["id"], "Chris's hand-edited final plan")
        self.assertEqual(room2["unified_plan"], "Chris's hand-edited final plan")
        self.assertIn("Chris's hand-edited final plan", job["description"])


class TestGuidedBuildApprovalGate(Base):
    def test_needs_approval_path_and_reject_then_reapprove(self):
        pid = "veracore"
        room = s.start_guided_build(pid, self.jobs, self.rooms, "idea")
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        job, room, _ = s.guided_approve_plan(self.projects, self.rooms, self.notes, pid,
                                             room["id"], stage["plan_draft"])
        s.guided_sent_to_claude_code(self.jobs, job["id"])
        report = ("STATUS: Blocked\nFILES CHANGED: x\nTESTS: 2/5\nCOMMIT: none\nPR: none\n"
                 "BLOCKERS: needs a decision\nNEXT ACTION: fix\nNEEDS APPROVAL: yes")
        s.guided_claude_code_report(self.jobs, self.reports, job["id"], pid, report)
        s.guided_continue_after_test(self.jobs, self.reports, self.projects, job["id"])
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "needs_approval")

        self.jobs.reject(job["id"], "needs another pass")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "claude_code_report")   # back to Building -> report stage

        s.guided_claude_code_report(self.jobs, self.reports, job["id"], pid,
                                    TestClaudeReportIngestion.GOOD_REPORT)
        s.guided_continue_after_test(self.jobs, self.reports, self.projects, job["id"])
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms)
        self.assertEqual(stage["kind"], "complete")   # GOOD_REPORT needs_approval: no

    def test_guided_complete_walks_approved_to_completed_to_archived(self):
        job = self.make_job(project="veracore", approval_required=True)
        for st in ["Planning", "Ready for ChatGPT", "ChatGPT Planned", "Ready for Claude",
                  "Sent to Claude", "Building", "Testing", "Needs Chris Approval", "Approved"]:
            job = self.jobs.advance(job["id"], st)
        job = s.guided_complete(self.jobs, job["id"])
        self.assertEqual(job["status"], "Completed")
        job = s.guided_complete(self.jobs, job["id"])
        self.assertEqual(job["status"], "Archived")
        job2 = s.guided_complete(self.jobs, job["id"])
        self.assertEqual(job2["status"], "Archived")   # no-op once archived


class _HTTPBase(Base):
    """Shared boilerplate for tests that need a live Studio server. Not a
    TestCase with its own test_ methods — subclass this, don't subclass a
    sibling test class, or unittest will silently re-run the sibling's tests
    a second time under the new name."""
    def setUp(self):
        super().setUp()
        s.build_app_state()
        s.Handler.projects = self.projects
        s.Handler.jobs = self.jobs
        s.Handler.reports = self.reports
        s.Handler.decisions = self.decisions
        s.Handler.risks = self.risks
        s.Handler.notes = self.notes
        s.Handler.rooms = self.rooms
        self.server = s.ThreadingHTTPServer(("127.0.0.1", 0), s.Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        super().tearDown()

    def _req(self, path, payload=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = (urllib.request.Request(url) if payload is None else
              urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}))
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")


class TestGuidedBuildHTTP(_HTTPBase):
    def test_create_project_endpoint(self):
        code, p = self._req("/api/projects", {"name": "New Thing", "kind": "new"})
        self.assertEqual((code, p["id"]), (201, "new-thing"))

    def test_guided_flow_end_to_end_over_http(self):
        code, stage = self._req("/api/guided/veracore/stage")
        self.assertEqual((code, stage["kind"]), (200, "not_started"))

        code, stage = self._req("/api/guided/veracore/start", {"idea": "Add a widget"})
        self.assertEqual((code, stage["kind"]), (201, "chatgpt"))

        code, stage = self._req("/api/guided/veracore/chatgpt-response", {"text": "x"})
        self.assertEqual(stage["kind"], "claude")

        code, stage = self._req("/api/guided/veracore/claude-response", {"text": "y"})
        self.assertEqual(stage["kind"], "plan_review")

        code, stage = self._req("/api/guided/veracore/approve-plan",
                                {"plan_text": stage["plan_draft"]})
        self.assertEqual(stage["kind"], "send_build_prompt")

        code, stage = self._req("/api/guided/veracore/sent-to-claude-code", {})
        self.assertEqual(stage["kind"], "claude_code_report")

        code, stage = self._req("/api/guided/veracore/claude-code-report",
                                {"raw": TestClaudeReportIngestion.GOOD_REPORT})
        self.assertEqual(stage["kind"], "test_review")

        code, stage = self._req("/api/guided/veracore/continue-after-test", {})
        self.assertEqual(stage["kind"], "complete")

    def test_guided_actions_before_start_are_404(self):
        code, err = self._req("/api/guided/veracore/chatgpt-response", {"text": "x"})
        self.assertEqual(code, 404)

    def test_root_serves_guided_and_advanced_serves_dashboard(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"<html", r.read()[:200].lower())
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/advanced", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"<html", r.read()[:200].lower())


# ─── finished outcome screen ─────────────────────────────────────────────────

_OUTCOME_EXTRA_LINES = ("\nFOLDER: /home/user/decision-deck\n"
                       "TEST COMMAND: npm start\nTEST URL: http://localhost:5173")


class TestOutcomeParsing(Base):
    def test_optional_fields_parsed_without_affecting_parsed_ok(self):
        raw = TestClaudeReportIngestion.GOOD_REPORT + _OUTCOME_EXTRA_LINES
        fields, ok = s.parse_claude_report(raw)
        self.assertTrue(ok)
        self.assertEqual(fields["folder"], "/home/user/decision-deck")
        self.assertEqual(fields["test_command"], "npm start")
        self.assertEqual(fields["test_url"], "http://localhost:5173")

    def test_missing_optional_fields_still_parsed_ok(self):
        fields, ok = s.parse_claude_report(TestClaudeReportIngestion.GOOD_REPORT)
        self.assertTrue(ok)
        self.assertIsNone(fields["folder"])
        self.assertIsNone(fields["test_command"])
        self.assertIsNone(fields["test_url"])


class TestReportAmend(Base):
    def test_amend_updates_editable_fields_only(self):
        job = self.make_job(project="veracore")
        rec = self.reports.ingest(job["id"], "veracore", TestClaudeReportIngestion.GOOD_REPORT)
        amended = self.reports.amend(rec["id"], folder="/home/user/decision-deck",
                                     test_command="npm start", test_url="http://localhost:5173",
                                     blockers="none really")
        self.assertEqual(amended["folder"], "/home/user/decision-deck")
        self.assertEqual(amended["test_command"], "npm start")
        self.assertEqual(amended["test_url"], "http://localhost:5173")
        self.assertEqual(amended["blockers"], "none really")
        self.assertEqual(amended["commit"], "abc1234")   # untouched fields survive

    def test_amend_unknown_report_raises_not_found(self):
        with self.assertRaises(s.NotFound):
            self.reports.amend("nope")


class TestBuildOutcome(Base):
    def test_folder_falls_back_to_project_repo_path_when_report_omits_it(self):
        job = self.make_job(project="veracore")
        rec = self.reports.ingest(job["id"], "veracore", TestClaudeReportIngestion.GOOD_REPORT)
        outcome = s.build_outcome(job, self.projects["veracore"], rec)
        self.assertEqual(outcome["folder"], self.projects["veracore"]["repo_path"])
        self.assertIn("Open the project folder", outcome["open_instruction"])

    def test_report_folder_and_test_url_take_priority_and_drive_open_instruction(self):
        job = self.make_job(project="veracore")
        raw = TestClaudeReportIngestion.GOOD_REPORT + _OUTCOME_EXTRA_LINES
        rec = self.reports.ingest(job["id"], "veracore", raw)
        outcome = s.build_outcome(job, self.projects["veracore"], rec)
        self.assertEqual(outcome["folder"], "/home/user/decision-deck")
        self.assertEqual(outcome["test_command"], "npm start")
        self.assertEqual(outcome["test_url"], "http://localhost:5173")
        self.assertIn("http://localhost:5173", outcome["open_instruction"])

    def test_no_report_falls_back_to_job_status_and_project_folder(self):
        job = self.make_job(project="veracore")
        outcome = s.build_outcome(job, self.projects["veracore"], None)
        self.assertEqual(outcome["status"], job["status"])
        self.assertEqual(outcome["folder"], self.projects["veracore"]["repo_path"])
        self.assertEqual(outcome["test_url"], "")


class TestVerifyOutcomeFiles(Base):
    def test_empty_folder_warns(self):
        result = s.verify_outcome_files("", "index.html")
        self.assertIn("No folder recorded", result["warning"])

    def test_studio_metadata_folder_is_flagged(self):
        """The exact Decision Deck failure: the recorded folder resolves to
        Studio's own projects/<slug>/ bookkeeping directory, not app code."""
        folder = os.path.join(s.PROJECTS_DIR, "veracore")
        result = s.verify_outcome_files(folder, "index.html")
        self.assertTrue(result["is_studio_metadata_folder"])
        self.assertIn("bookkeeping directory", result["warning"])

    def test_projects_dir_itself_is_flagged(self):
        result = s.verify_outcome_files(s.PROJECTS_DIR, "index.html")
        self.assertTrue(result["is_studio_metadata_folder"])

    def test_app_subfolder_beneath_bookkeeping_dir_is_NOT_flagged(self):
        """Regression: projects/<slug>/app/ is exactly the layout Studio
        itself recommends for a project whose own folder is bookkeeping-only
        (the real Decision Deck fix) -- it must never be mistaken for the
        bookkeeping directory just because it lives under PROJECTS_DIR."""
        folder = os.path.join(s.PROJECTS_DIR, "veracore", "app")
        os.makedirs(folder, exist_ok=True)
        open(os.path.join(folder, "index.html"), "w").close()
        result = s.verify_outcome_files(folder, "index.html")
        self.assertFalse(result["is_studio_metadata_folder"])
        self.assertIsNone(result["warning"])

    def test_deeply_nested_subfolder_is_also_not_flagged(self):
        folder = os.path.join(s.PROJECTS_DIR, "veracore", "app", "dist")
        os.makedirs(folder, exist_ok=True)
        result = s.verify_outcome_files(folder, "")
        self.assertFalse(result["is_studio_metadata_folder"])

    def test_nonexistent_folder_warns(self):
        result = s.verify_outcome_files("/no/such/path/anywhere-xyz", "index.html")
        self.assertIn("does not exist", result["warning"])
        self.assertFalse(result["folder_exists"])

    def test_existing_folder_with_present_files_has_no_warning(self):
        folder = tempfile.mkdtemp(dir=self.tmp)
        open(os.path.join(folder, "index.html"), "w").close()
        open(os.path.join(folder, "app.js"), "w").close()
        result = s.verify_outcome_files(folder, "index.html, app.js")
        self.assertIsNone(result["warning"])
        self.assertTrue(result["folder_exists"])
        self.assertEqual(result["missing_files"], [])

    def test_existing_folder_missing_claimed_files_warns(self):
        folder = tempfile.mkdtemp(dir=self.tmp)
        result = s.verify_outcome_files(folder, "index.html, app.js")
        self.assertIn("index.html", result["warning"])
        self.assertEqual(set(result["missing_files"]), {"index.html", "app.js"})

    def test_prose_files_changed_is_not_treated_as_filenames(self):
        folder = tempfile.mkdtemp(dir=self.tmp)
        result = s.verify_outcome_files(folder, "several files across the frontend")
        self.assertIsNone(result["warning"])
        self.assertEqual(result["checked_files"], [])


class TestBuildOutcomeFileCheck(Base):
    def test_outcome_flags_studio_metadata_folder(self):
        job = self.make_job(project="veracore")
        raw = (TestClaudeReportIngestion.GOOD_REPORT +
              f"\nFOLDER: {os.path.join(s.PROJECTS_DIR, 'veracore')}")
        rec = self.reports.ingest(job["id"], "veracore", raw)
        outcome = s.build_outcome(job, self.projects["veracore"], rec)
        self.assertTrue(outcome["file_check"]["is_studio_metadata_folder"])

    def test_outcome_clean_when_folder_and_files_are_real(self):
        job = self.make_job(project="veracore")
        real_folder = tempfile.mkdtemp(dir=self.tmp)
        open(os.path.join(real_folder, "index.html"), "w").close()
        raw = (TestClaudeReportIngestion.GOOD_REPORT.replace(
                  "FILES CHANGED: studio.py, dashboard.html", "FILES CHANGED: index.html")
              + f"\nFOLDER: {real_folder}")
        rec = self.reports.ingest(job["id"], "veracore", raw)
        outcome = s.build_outcome(job, self.projects["veracore"], rec)
        self.assertIsNone(outcome["file_check"]["warning"])


@contextlib.contextmanager
def _local_static_server(folder):
    """A real, local, ephemeral-port static file server for exercising
    verify_build_live against actual HTTP responses (200s, directory
    listings) instead of mocking urllib."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=folder)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestLocalTailscaleHostGuard(unittest.TestCase):
    def test_allows_localhost_loopback_private_and_tailscale(self):
        for host in ("localhost", "127.0.0.1", "10.1.2.3", "192.168.1.5",
                    "172.16.0.1", "100.90.72.114"):
            self.assertTrue(s._is_local_or_tailscale_host(host), host)

    def test_refuses_public_ip_and_dns_names_and_empty(self):
        for host in ("8.8.8.8", "example.com", "api.github.com", "", None):
            self.assertFalse(s._is_local_or_tailscale_host(host), host)


class TestVerifyBuildLive(unittest.TestCase):
    def test_no_url_is_a_no_op(self):
        result = s.verify_build_live("")
        self.assertEqual(result["problems"], [])
        self.assertFalse(result["reachable"])

    def test_refuses_external_host_without_making_a_request(self):
        result = s.verify_build_live("https://example.com/")
        self.assertFalse(result["reachable"])
        self.assertIn("Refusing to verify", result["problems"][0])

    def test_unreachable_local_port_reports_a_clear_problem(self):
        result = s.verify_build_live("http://127.0.0.1:1/", timeout=2)
        self.assertFalse(result["reachable"])
        self.assertTrue(any("Could not reach" in p for p in result["problems"]))

    def test_real_app_returns_ok_with_no_problems(self):
        folder = tempfile.mkdtemp()
        try:
            with open(os.path.join(folder, "index.html"), "w") as f:
                f.write("<html><title>Decision Deck</title><body>Decision Deck</body></html>")
            with _local_static_server(folder) as url:
                result = s.verify_build_live(url, project_name="Decision Deck")
                self.assertEqual(result["problems"], [])
                self.assertTrue(result["reachable"])
                self.assertTrue(result["http_ok"])
                self.assertFalse(result["is_directory_listing"])
                self.assertTrue(result["title_matches_project"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_directory_listing_is_detected_as_a_problem(self):
        folder = tempfile.mkdtemp()   # no index.html -> http.server serves a listing
        try:
            with _local_static_server(folder) as url:
                result = s.verify_build_live(url)
                self.assertTrue(result["is_directory_listing"])
                self.assertTrue(any("directory listing" in p for p in result["problems"]))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TestRunBuildVerification(Base):
    def test_ok_when_folder_and_url_both_check_out(self):
        folder = tempfile.mkdtemp(dir=self.tmp)
        with open(os.path.join(folder, "index.html"), "w") as f:
            f.write("<html><title>Veracore</title></html>")
        with _local_static_server(folder) as url:
            job = self.make_job(project="veracore")
            raw = (TestClaudeReportIngestion.GOOD_REPORT.replace(
                      "FILES CHANGED: studio.py, dashboard.html", "FILES CHANGED: index.html")
                  + f"\nFOLDER: {folder}\nTEST URL: {url}")
            rec = self.reports.ingest(job["id"], "veracore", raw)
            outcome = s.build_outcome(job, self.projects["veracore"], rec)
            verification = s.run_build_verification(outcome, "Veracore")
            self.assertTrue(verification["ok"], verification["problems"])

    def test_not_ok_when_url_is_unreachable(self):
        job = self.make_job(project="veracore")
        raw = TestClaudeReportIngestion.GOOD_REPORT + "\nTEST URL: http://127.0.0.1:1/"
        rec = self.reports.ingest(job["id"], "veracore", raw)
        outcome = s.build_outcome(job, self.projects["veracore"], rec)
        verification = s.run_build_verification(outcome, "Veracore")
        self.assertFalse(verification["ok"])
        self.assertTrue(verification["problems"])


class TestVerificationBlocksGuidedFlow(Base):
    """The actual 'block approval' + 'Human Intervention Required' behavior:
    when a build's recorded test_url doesn't check out, the guided flow must
    not silently advance, at either the stage-display layer or the action
    layer (a direct API call can't skip past it either)."""

    def _job_at_testing_with_bad_url(self):
        pid = "veracore"
        room = s.start_guided_build(pid, self.jobs, self.rooms, "Decision Deck test")
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
        job, room, _ = s.guided_approve_plan(self.projects, self.rooms, self.notes, pid,
                                             room["id"], stage["plan_draft"])
        s.guided_sent_to_claude_code(self.jobs, job["id"])
        raw = (TestClaudeReportIngestion.GOOD_REPORT +
              f"\nFOLDER: {os.path.join(s.PROJECTS_DIR, 'veracore')}"
              "\nTEST COMMAND: npm run dev\nTEST URL: http://127.0.0.1:1/")
        s.guided_claude_code_report(self.jobs, self.reports, job["id"], pid, raw)
        return pid, job["id"]

    def test_stage_shows_verification_failed_instead_of_test_review(self):
        pid, job_id = self._job_at_testing_with_bad_url()
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
        self.assertEqual(stage["kind"], "verification_failed")
        # port 1 on loopback is a valid local host, so this fails at connect
        # time ("Could not reach"), not the external-host refusal path.
        self.assertTrue(any("Could not reach" in p for p in stage["verification"]["problems"]))
        self.assertIn("Use Claude Code.", stage["fix_prompt"])
        self.assertEqual(stage["resume_action"], "continue-after-test")

    def test_guided_continue_after_test_raises_when_verification_fails(self):
        pid, job_id = self._job_at_testing_with_bad_url()
        with self.assertRaises(s.IllegalTransition):
            s.guided_continue_after_test(self.jobs, self.reports, self.projects, job_id)
        # job must still be at Testing -- nothing advanced
        self.assertEqual(self.jobs.get(job_id)["status"], "Testing")

    def test_fixing_the_url_unblocks_the_same_job(self):
        pid, job_id = self._job_at_testing_with_bad_url()
        folder = tempfile.mkdtemp(dir=self.tmp)
        with open(os.path.join(folder, "index.html"), "w") as f:
            f.write("<html>ok</html>")
        # GOOD_REPORT's FILES CHANGED ("studio.py, dashboard.html") is still
        # on the report -- amend() only overrides folder/test_command/
        # test_url/blockers -- so satisfy the disk file-existence check too.
        open(os.path.join(folder, "studio.py"), "w").close()
        open(os.path.join(folder, "dashboard.html"), "w").close()
        with _local_static_server(folder) as url:
            self.reports.amend(self.reports.list(job_id=job_id)[0]["id"],
                              folder=folder, test_url=url)
            stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
            self.assertEqual(stage["kind"], "test_review")
            s.guided_continue_after_test(self.jobs, self.reports, self.projects, job_id)
            self.assertEqual(self.jobs.get(job_id)["status"], "Completed")


class TestListOutcomes(Base):
    def test_no_finished_jobs_yields_empty_list(self):
        self.make_job(project="veracore")   # stays Draft — not finished
        self.assertEqual(s.list_outcomes(self.projects, self.jobs, self.reports), [])

    def test_completed_job_appears_with_outcome_fields(self):
        pid = "veracore"
        room = s.start_guided_build(pid, self.jobs, self.rooms, "Add a widget")
        self.rooms.paste_chatgpt_response(room["id"], "x")
        self.rooms.paste_claude_response(room["id"], "y")
        stage = s.compute_guided_stage(pid, self.projects, self.jobs, self.rooms, self.reports)
        job, room, _ = s.guided_approve_plan(self.projects, self.rooms, self.notes, pid,
                                             room["id"], stage["plan_draft"])
        s.guided_sent_to_claude_code(self.jobs, job["id"])
        s.guided_claude_code_report(self.jobs, self.reports, job["id"], pid,
                                    TestClaudeReportIngestion.GOOD_REPORT)
        s.guided_continue_after_test(self.jobs, self.reports, self.projects, job["id"])   # GOOD_REPORT: needs_approval no -> Completed
        outcomes = s.list_outcomes(self.projects, self.jobs, self.reports)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["project"], "veracore")
        self.assertEqual(outcomes[0]["job_status"], "Completed")
        self.assertFalse(outcomes[0]["open_available"])   # GOOD_REPORT has no TEST URL


class TestOutcomesHTTP(_HTTPBase):
    def test_outcomes_endpoint_lists_and_edit_updates(self):
        pid = "veracore"
        self._req(f"/api/guided/{pid}/start", {"idea": "Add a widget"})
        self._req(f"/api/guided/{pid}/chatgpt-response", {"text": "x"})
        code, stage = self._req(f"/api/guided/{pid}/claude-response", {"text": "y"})
        self._req(f"/api/guided/{pid}/approve-plan", {"plan_text": stage["plan_draft"]})
        self._req(f"/api/guided/{pid}/sent-to-claude-code", {})
        code, stage = self._req(f"/api/guided/{pid}/claude-code-report",
                                {"raw": TestClaudeReportIngestion.GOOD_REPORT})
        job_id = stage["job_id"]
        self._req(f"/api/guided/{pid}/continue-after-test", {})

        code, data = self._req("/api/outcomes")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["outcomes"]), 1)
        self.assertEqual(data["outcomes"][0]["job_id"], job_id)

        code, updated = self._req(f"/api/outcomes/{job_id}/edit",
                                  {"folder": "/home/user/decision-deck",
                                   "test_command": "npm start",
                                   "test_url": "http://localhost:5173"})
        self.assertEqual(code, 200)
        self.assertEqual(updated["folder"], "/home/user/decision-deck")
        self.assertEqual(updated["test_url"], "http://localhost:5173")

        code, data = self._req("/api/outcomes")
        self.assertEqual(data["outcomes"][0]["test_url"], "http://localhost:5173")

    def test_edit_with_no_prior_report_creates_a_manual_one(self):
        job = self.make_job(project="veracore", approval_required=False)
        for status in ("Planning", "Ready for ChatGPT", "ChatGPT Planned", "Ready for Claude",
                      "Sent to Claude", "Building", "Testing", "Completed"):
            self.jobs.advance(job["id"], status)
        code, updated = self._req(f"/api/outcomes/{job['id']}/edit",
                                  {"folder": "/home/user/manually-found-folder"})
        self.assertEqual(code, 200)
        self.assertEqual(updated["folder"], "/home/user/manually-found-folder")

    def test_outcomes_page_serves_html(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/outcomes", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertIn(b"<html", r.read()[:200].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
