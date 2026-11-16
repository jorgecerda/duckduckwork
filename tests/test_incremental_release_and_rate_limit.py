import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from applypilot.database import get_connection, init_db
from applypilot.scoring.scorer import run_scoring, score_job
from applypilot.server import _RUNNER_LOCK, _SEARCH_RUNNER_STATE, _run_search_background_task
from applypilot.view import render_job_card


class TestIncrementalReleaseAndRateLimit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.temp_dir.name)
        (self.app_dir / "resumes").mkdir(parents=True, exist_ok=True)
        (self.app_dir / "resume.txt").write_text("Senior Agile Delivery Lead and Technical Scrum Master", encoding="utf-8")

        import yaml
        cfg = {
            "version": 1,
            "searches": [
                {"id": "search_1", "name": "Scrum Master", "query": "Scrum Master", "locations": ["Remote"], "enabled": True},
                {"id": "search_2", "name": "Project Manager", "query": "Project Manager", "locations": ["Remote"], "enabled": True},
            ],
        }
        (self.app_dir / "searches.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_reasoning_filter_blocks_429_and_llm_errors(self):
        # Even if a job somehow has an error in score_reasoning, render_job_card should block it
        job = {
            "title": "Software Engineer",
            "company": "Tech Corp",
            "location": "Remote",
            "site": "linkedin",
            "url": "https://example.com/job_1",
            "salary": "$100k",
            "posted_date": "2026-11-01",
            "discovered_at": "2026-11-03",
            "apply_status": "new",
            "applied_at": None,
            "is_saved": 0,
            "is_archived": 0,
            "is_expired": 0,
            "repost_count": 1,
            "archived_at": None,
            "fit_score": 0,
            "score_reasoning": "Keywords\nLLM error: Client error '429 Too Many Requests' for url https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429",
            "country": "Worldwide",
            "description": "Short desc",
            "full_description": "Full desc",
            "notes": "",
            "tailored_resume_path": None,
            "tailored_cover_path": None,
            "cover_letter": None,
            "tailored_resume": None,
        }
        card_html = render_job_card(job, 0, {})
        self.assertNotIn("Status/429", card_html)
        self.assertNotIn("developer.mozilla.org", card_html)
        self.assertNotIn("LLM error", card_html)

    @patch("applypilot.scoring.scorer.get_client")
    def test_score_job_error_does_not_return_score_zero(self, mock_get_client):
        mock_client = MagicMock()
        mock_client._is_gemini = False
        mock_client.chat.side_effect = RuntimeError("Client error 429 Too Many Requests")
        mock_get_client.return_value = mock_client

        job = {"title": "PM", "site": "linkedin", "location": "Remote", "description": "Desc"}
        res = score_job("Resume text", job)
        self.assertIsNone(res["score"])
        self.assertIn("429", res["error"])

    @patch("applypilot.scoring.scorer.score_job")
    @patch("applypilot.discovery.liveness.check_posting_liveness")
    def test_run_scoring_leaves_failed_jobs_unscored(self, mock_liveness, mock_score):
        mock_liveness.return_value = {"is_live": True, "reason": "Active listing"}
        # Simulate LLM failure on this job
        mock_score.return_value = {"score": None, "keywords": "", "reasoning": "", "error": "429 Too Many Requests"}

        with patch.dict("os.environ", {"APPLYPILOT_DIR": str(self.app_dir)}):
            init_db()
            conn = get_connection()
            conn.execute(
                """
                INSERT INTO jobs (url, title, company, location, site, description, full_description, search_id, discovered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("https://example.com/failed_job", "PM", "Corp", "Remote", "linkedin", "desc", "full desc", "search_1"),
            )
            conn.commit()

            res = run_scoring(search_id="search_1")
            self.assertEqual(res["errors"], 1)

            # Check database: fit_score must remain NULL, not 0!
            row = conn.execute("SELECT fit_score, score_reasoning FROM jobs WHERE url = ?", ("https://example.com/failed_job",)).fetchone()
            self.assertIsNone(row["fit_score"])
            self.assertIsNone(row["score_reasoning"])

    @patch("applypilot.server.save_session_to_cloud")
    @patch("applypilot.view.generate_dashboard")
    @patch("applypilot.discovery.jobspy.run_discovery")
    def test_sequential_search_updates_completed_profiles_incrementally(self, mock_discovery, mock_gen_dash, mock_cloud):
        mock_discovery.side_effect = [{"new": 2, "total": 5}, {"new": 3, "total": 6}]

        with patch.dict("os.environ", {"APPLYPILOT_DIR": str(self.app_dir)}):
            init_db()
            _run_search_background_task(target_search_id="all", skip_scoring=True)

            with _RUNNER_LOCK:
                self.assertEqual(_SEARCH_RUNNER_STATE["status"], "complete")
                self.assertEqual(_SEARCH_RUNNER_STATE["completed_profiles"], ["search_1", "search_2"])
                self.assertEqual(_SEARCH_RUNNER_STATE["tab_new_counts"], {"search_1": 2, "search_2": 3})

            # Dashboard should have been generated incrementally for each profile plus at the end
            self.assertGreaterEqual(mock_gen_dash.call_count, 2)
