import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from applypilot.server import (
    AUTO_RUN_INTERVAL_MAP,
    _AUTO_RUN_LOCK,
    _AUTO_RUN_STATE,
    calculate_auto_run_next_run,
    get_admin_data,
    load_admin_config,
    save_admin_config,
)


class TestAutoRunScheduler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.temp_dir.name)
        self.orig_state = dict(_AUTO_RUN_STATE)

    def tearDown(self):
        with _AUTO_RUN_LOCK:
            _AUTO_RUN_STATE.clear()
            _AUTO_RUN_STATE.update(self.orig_state)
        self.temp_dir.cleanup()

    def test_interval_map_contains_required_options(self):
        """Ensure 15m, 30m, 1h, 2h are correctly mapped to minutes."""
        self.assertEqual(AUTO_RUN_INTERVAL_MAP.get("15m"), 15)
        self.assertEqual(AUTO_RUN_INTERVAL_MAP.get("30m"), 30)
        self.assertEqual(AUTO_RUN_INTERVAL_MAP.get("1h"), 60)
        self.assertEqual(AUTO_RUN_INTERVAL_MAP.get("2h"), 120)

    def test_admin_config_auto_run_persistence(self):
        """Verify saving and loading auto-run configuration."""
        with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(self.app_dir), "APPLYPILOT_DIR": str(self.app_dir)}):
            cfg = load_admin_config()
            cfg["auto_run_enabled"] = True
            cfg["auto_run_interval"] = "30m"
            save_admin_config(cfg)

            reloaded = load_admin_config()
            self.assertTrue(reloaded.get("auto_run_enabled"))
            self.assertEqual(reloaded.get("auto_run_interval"), "30m")

    def test_get_admin_data_includes_auto_run(self):
        """Verify get_admin_data includes auto_run status block in system."""
        with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(self.app_dir), "APPLYPILOT_DIR": str(self.app_dir)}):
            with _AUTO_RUN_LOCK:
                _AUTO_RUN_STATE["enabled"] = True
                _AUTO_RUN_STATE["interval"] = "2h"
                _AUTO_RUN_STATE["interval_minutes"] = 120
                _AUTO_RUN_STATE["next_run"] = time.time() + 7200

            admin_data = get_admin_data()
            system = admin_data.get("system", {})
            self.assertIn("auto_run", system)
            ar = system["auto_run"]
            self.assertTrue(ar["enabled"])
            self.assertEqual(ar["interval"], "2h")
            self.assertEqual(ar["interval_minutes"], 120)
            self.assertGreater(ar["next_run"], 0)

    def test_calculate_auto_run_next_run_immediate_when_overdue(self):
        """Verify scheduler triggers immediately if profiles are overdue."""
        now = time.time()
        # Mock search profile with last_run 40 minutes ago
        with patch("applypilot.server.get_search_profiles", return_value=[{"id": "p1", "last_run": now - 40 * 60}]):
            next_run = calculate_auto_run_next_run(30)
            self.assertLessEqual(next_run, now + 1)

    def test_calculate_auto_run_next_run_future_when_recent(self):
        """Verify scheduler schedules remaining time if profiles ran recently."""
        now = time.time()
        # Mock search profile with last_run 10 minutes ago
        with patch("applypilot.server.get_search_profiles", return_value=[{"id": "p1", "last_run": now - 10 * 60}]):
            next_run = calculate_auto_run_next_run(30)
            # Expect next run in ~20 minutes (1200 seconds)
            self.assertAlmostEqual(next_run - now, 20 * 60, delta=5)


if __name__ == "__main__":
    unittest.main()
