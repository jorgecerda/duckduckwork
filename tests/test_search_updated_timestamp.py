"""Unit tests for search profile last updated relative timestamp."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from applypilot.config import (
    get_search_profiles,
    save_search_config,
    update_profile_last_run,
)
from applypilot.database import init_db
from applypilot.view import format_search_updated_text, generate_dashboard


class TestSearchUpdatedTimestamp(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_format_search_updated_text_relative_intervals(self):
        now = time.time()
        self.assertEqual(format_search_updated_text(None), "Search not run yet")
        self.assertEqual(format_search_updated_text(0), "Search not run yet")
        self.assertEqual(format_search_updated_text(""), "Search not run yet")

        # Just now (< 1 min)
        self.assertEqual(format_search_updated_text(now - 10), "Search Updated just now")

        # 1 minute ago
        self.assertEqual(format_search_updated_text(now - 70), "Search Updated 1 minute ago")

        # 15 minutes ago
        self.assertEqual(format_search_updated_text(now - 15 * 60), "Search Updated 15 minutes ago")

        # 30 minutes ago
        self.assertEqual(format_search_updated_text(now - 30 * 60), "Search Updated 30 minutes ago")

        # 1 hour ago
        self.assertEqual(format_search_updated_text(now - 60 * 60), "Search Updated 1 hour ago")

        # 2 hours ago
        self.assertEqual(format_search_updated_text(now - 120 * 60), "Search Updated 2 hours ago")

        # 1 day ago
        self.assertEqual(format_search_updated_text(now - 86400), "Search Updated 1 day ago")

        # 3 days ago
        self.assertEqual(format_search_updated_text(now - 3 * 86400), "Search Updated 3 days ago")

    def test_update_profile_last_run_and_view_rendering(self):
        with patch.dict("os.environ", {"APPLYPILOT_DIR": str(self.app_dir)}):
            init_db()

            now = int(time.time())
            sample_cfg = {
                "searches": [
                    {
                        "id": "search_test_pm",
                        "name": "Product Manager",
                        "queries": ["Product Manager"],
                        "locations": ["Remote"],
                        "last_run": now - (15 * 60),
                    }
                ]
            }
            save_search_config(sample_cfg)

            profiles = get_search_profiles()
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]["last_run"], now - (15 * 60))

            dashboard_path = generate_dashboard()
            html = dashboard_path.read_text(encoding="utf-8")
            self.assertIn('id="search-updated-timestamp"', html)
            self.assertIn("Search Updated 15 minutes ago", html)

            # Update to 2 hours ago and re-check
            update_profile_last_run("search_test_pm", now - (120 * 60))
            dashboard_path_2 = generate_dashboard()
            html_2 = dashboard_path_2.read_text(encoding="utf-8")
            self.assertIn("Search Updated 2 hours ago", html_2)


if __name__ == "__main__":
    unittest.main()
