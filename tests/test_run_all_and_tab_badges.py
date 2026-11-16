import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applypilot.database import init_db
from applypilot.server import _RUNNER_LOCK, _SEARCH_RUNNER_STATE, _run_search_background_task
from applypilot.view import generate_dashboard


class TestRunAllAndTabBadges(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.temp_dir.name)
        (self.app_dir / "resumes").mkdir(parents=True, exist_ok=True)
        (self.app_dir / "resume.txt").write_text("Experienced Agile Coach and Engineering Lead", encoding="utf-8")

        # Create multi-profile search config
        cfg = {
            "version": 1,
            "searches": [
                {
                    "id": "search_1",
                    "name": "Scrum Master",
                    "query": "Scrum Master",
                    "locations": ["Remote"],
                    "enabled": True,
                },
                {
                    "id": "search_2",
                    "name": "Project Manager",
                    "query": "Project Manager",
                    "locations": ["Remote"],
                    "enabled": True,
                },
            ],
        }
        import yaml
        (self.app_dir / "searches.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_view_contains_run_all_button(self):
        with patch.dict("os.environ", {"APPLYPILOT_DIR": str(self.app_dir)}):
            init_db()
            dashboard_file = generate_dashboard()
            html = dashboard_file.read_text(encoding="utf-8")

            self.assertIn('id="sidebar-run-all-btn"', html)
            self.assertIn('onclick="triggerRunAllSearches()"', html)
            self.assertIn('Run All', html)

            # Ensure Run All button is placed before Settings button
            run_all_pos = html.find('id="sidebar-run-all-btn"')
            settings_pos = html.find('id="sidebar-settings-btn"')
            self.assertGreater(settings_pos, run_all_pos)

    def test_css_contains_run_all_and_tab_badge_rules(self):
        css_path = Path(__file__).parent.parent / "src" / "applypilot" / "static" / "css" / "dashboard.css"
        css_content = css_path.read_text(encoding="utf-8")

        self.assertIn(".tab-new-badge", css_content)
        self.assertIn(".tab-new-badge.has-new", css_content)
        self.assertIn(".tab-new-badge.zero", css_content)
        self.assertIn(".btn-run-all", css_content)

    def test_js_contains_run_all_and_tab_badge_logic(self):
        js_path = Path(__file__).parent.parent / "src" / "applypilot" / "static" / "js" / "dashboard.js"
        js_content = js_path.read_text(encoding="utf-8")

        self.assertIn("function triggerRunAllSearches()", js_content)
        self.assertIn("function restoreRunAllButton()", js_content)
        self.assertIn("function setRunAllButtonSearching()", js_content)
        self.assertIn("function updateSearchTabBadges()", js_content)
        self.assertIn("window.triggerRunAllSearches = triggerRunAllSearches;", js_content)
        self.assertIn("window.updateSearchTabBadges = updateSearchTabBadges;", js_content)

    def test_server_runner_state_has_tab_new_counts(self):
        with _RUNNER_LOCK:
            self.assertIn("tab_new_counts", _SEARCH_RUNNER_STATE)

    @patch("applypilot.server.save_session_to_cloud")
    @patch("applypilot.view.generate_dashboard")
    @patch("applypilot.discovery.jobspy.run_discovery")
    def test_run_search_background_task_sequential_all(self, mock_discovery, mock_gen_dash, mock_cloud):
        # First call discovers 4 new jobs, second call discovers 2 new jobs
        mock_discovery.side_effect = [{"new": 4, "total": 10}, {"new": 2, "total": 8}]

        with patch.dict("os.environ", {"APPLYPILOT_DIR": str(self.app_dir)}):
            init_db()
            _run_search_background_task(target_search_id="all", skip_scoring=True)

            self.assertEqual(mock_discovery.call_count, 2)
            with _RUNNER_LOCK:
                self.assertEqual(_SEARCH_RUNNER_STATE["status"], "complete")
                self.assertEqual(_SEARCH_RUNNER_STATE["new_jobs"], 6)
                self.assertEqual(_SEARCH_RUNNER_STATE["tab_new_counts"], {"search_1": 4, "search_2": 2})

    @patch("applypilot.server.save_session_to_cloud")
    @patch("applypilot.view.generate_dashboard")
    @patch("applypilot.discovery.jobspy.run_discovery")
    def test_run_search_background_task_single_profile(self, mock_discovery, mock_gen_dash, mock_cloud):
        mock_discovery.return_value = {"new": 3, "total": 7}

        with patch.dict("os.environ", {"APPLYPILOT_DIR": str(self.app_dir)}):
            init_db()
            _run_search_background_task(target_search_id="search_2", skip_scoring=True)

            self.assertEqual(mock_discovery.call_count, 1)
            with _RUNNER_LOCK:
                self.assertEqual(_SEARCH_RUNNER_STATE["status"], "complete")
                self.assertEqual(_SEARCH_RUNNER_STATE["new_jobs"], 3)
                self.assertEqual(_SEARCH_RUNNER_STATE["tab_new_counts"], {"search_2": 3})
