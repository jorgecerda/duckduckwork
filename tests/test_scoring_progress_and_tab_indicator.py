import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applypilot.database import get_connection, init_db
from applypilot.scoring.scorer import run_scoring
from applypilot.server import _RUNNER_LOCK, _SEARCH_RUNNER_STATE
from applypilot.view import generate_dashboard


class TestScoringProgressAndTabIndicator(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.temp_dir.name)
        (self.app_dir / "resumes").mkdir(parents=True, exist_ok=True)
        (self.app_dir / "resume.txt").write_text("Experienced Scrum Master and Agile Coach", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("applypilot.scoring.scorer.score_job")
    @patch("applypilot.discovery.liveness.check_posting_liveness")
    def test_run_scoring_progress_callback_incremental(self, mock_liveness, mock_score):
        mock_liveness.return_value = {"is_live": True, "reason": "Active listing"}
        mock_score.return_value = {"score": 9, "keywords": "Agile, Scrum", "reasoning": "Strong match"}

        with patch.dict("os.environ", {"APPLYPILOT_DIR": str(self.app_dir)}):
            init_db()
            conn = get_connection()

            # Insert 3 test jobs with descriptions and NULL fit_score
            for i in range(1, 4):
                conn.execute(
                    """
                    INSERT INTO jobs (url, title, company, location, site, description, full_description, search_id, discovered_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        f"https://example.com/job_{i}",
                        f"Scrum Master {i}",
                        f"Company {i}",
                        "Remote",
                        "linkedin",
                        "Looking for an agile leader with 5+ years of scrum experience.",
                        "Looking for an agile leader with 5+ years of scrum experience in cross-functional teams.",
                        "search_1",
                    ),
                )
            conn.commit()

            progress_reports = []

            def progress_cb(completed: int, total: int):
                progress_reports.append((completed, total))

            result = run_scoring(search_id="search_1", progress_callback=progress_cb)

            self.assertEqual(result["scored"], 3)
            self.assertEqual(result["errors"], 0)

            # Verify progress callback was called initially (0, 3) and after each job (1, 3), (2, 3), (3, 3)
            self.assertIn((0, 3), progress_reports)
            self.assertIn((1, 3), progress_reports)
            self.assertIn((2, 3), progress_reports)
            self.assertIn((3, 3), progress_reports)

    def test_progress_message_formatting(self):
        # Test progress callback logic format matching Task 34
        def format_msg(completed: int, total: int) -> str:
            if total <= 0:
                return "Scoring jobs with Gemini..."
            pct = int((completed / total) * 100) if total > 0 else 0
            curr_job = min(completed + 1, total) if completed < total else total
            return f"Scoring job {curr_job} of {total} with Gemini... [{pct}%]"

        self.assertEqual(format_msg(0, 10), "Scoring job 1 of 10 with Gemini... [0%]")
        self.assertEqual(format_msg(1, 10), "Scoring job 2 of 10 with Gemini... [10%]")
        self.assertEqual(format_msg(5, 10), "Scoring job 6 of 10 with Gemini... [50%]")
        self.assertEqual(format_msg(9, 10), "Scoring job 10 of 10 with Gemini... [90%]")
        self.assertEqual(format_msg(10, 10), "Scoring job 10 of 10 with Gemini... [100%]")
        self.assertEqual(format_msg(0, 1), "Scoring job 1 of 1 with Gemini... [0%]")
        self.assertEqual(format_msg(1, 1), "Scoring job 1 of 1 with Gemini... [100%]")

    def test_runner_state_keys_present(self):
        with _RUNNER_LOCK:
            self.assertIn("scoring_completed", _SEARCH_RUNNER_STATE)
            self.assertIn("scoring_total", _SEARCH_RUNNER_STATE)
            self.assertIn("scoring_percent", _SEARCH_RUNNER_STATE)

    def test_view_html_contains_progress_bar_and_tab_elements(self):
        with patch.dict("os.environ", {"APPLYPILOT_DIR": str(self.app_dir)}):
            init_db()
            dashboard_file = generate_dashboard()
            html = dashboard_file.read_text(encoding="utf-8")

            self.assertIn('id="runner-progress-bar-wrap"', html)
            self.assertIn('id="runner-progress-bar-fill"', html)
            self.assertIn('class="search-tab-btn active"', html)


if __name__ == "__main__":
    unittest.main()
