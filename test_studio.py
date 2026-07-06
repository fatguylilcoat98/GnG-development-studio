"""Tests for GNG Development Studio. Stdlib unittest only; every test runs against
a temp state directory — the real state/ (if any) is never touched. Studio is
coordination-only: no AI/GitHub/SSH calls, no deployment, no service management —
enforced here by a source scan, not just by convention."""
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
        (self.projects, self.jobs, self.reports, self.decisions, self.risks,
         self.notes, self.rooms) = self._fresh_state()

    def _fresh_state(self):
        projects = s.load_projects()
        jobs = s.JobStore()
        reports = s.ReportStore()
        decisions = s.DecisionStore()
        risks = s.RiskStore()
        notes = s.NoteStore()
        rooms = s.PlanningRoomStore(jobs, notes)
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
    surface anywhere in the core module or the dashboard."""

    def test_no_subprocess_socket_network_in_studio_module(self):
        src = open(os.path.join(ROOT, "studio.py")).read()
        for forbidden in ("import subprocess", "os.system", "Popen", "socket.socket",
                          "urllib.request", "requests."):
            self.assertNotIn(forbidden, src, f"studio.py contains {forbidden}")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
