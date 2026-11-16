"""Unit tests for DuckDuckWork Single User Admin Cockpit and BYOK AI configuration."""

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from applypilot.view import generate_admin_html, generate_dashboard
from applypilot.server import get_admin_data


class TestSingleUserCockpit(unittest.TestCase):
    """Test suite for Single User / Open Source Cockpit."""

    def test_generate_admin_html_structure(self):
        html = generate_admin_html()
        self.assertIn("DuckDuckWork", html)
        self.assertIn("SETTINGS &amp; COCKPIT", html)
        self.assertIn("Back to My Searches", html)
        # Should not have auth overlay blocking
        self.assertNotIn('id="auth-overlay"', html)
        # Should have theme switcher
        self.assertIn('id="theme-segmented-control-admin"', html)
        # Should have the 4 single-user tabs (candidate profile moved to search tabs)
        self.assertIn('id="panel-ai"', html)
        self.assertNotIn('id="panel-candidate"', html)
        self.assertIn('id="panel-sources"', html)
        self.assertIn('id="panel-logs"', html)
        self.assertIn('id="panel-db"', html)
        # BYOK AI Providers options
        self.assertIn("Google Gemini", html)
        self.assertIn("Ollama Local", html)
        self.assertIn("Groq Cloud", html)
        self.assertIn("Anthropic Claude", html)

    def test_get_admin_data_user_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            db_file = tmp_path / "applypilot.db"
            conn = sqlite3.connect(str(db_file))
            conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT, fit_score INTEGER)")
            conn.execute("INSERT INTO jobs (title, fit_score) VALUES ('PM 1', 85)")
            conn.execute("INSERT INTO jobs (title, fit_score) VALUES ('PM 2', 90)")
            conn.execute("INSERT INTO jobs (title, fit_score) VALUES ('PM 3', NULL)")
            conn.commit()
            conn.close()

            searches_file = tmp_path / "searches.yaml"
            searches_file.write_text("searches:\n  - name: Search 1\n  - name: Search 2\n", encoding="utf-8")

            with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(tmp_path), "APPLYPILOT_DIR": str(tmp_path)}):
                data = get_admin_data()
                self.assertEqual(data.get("user_jobs_count"), 3)
                self.assertEqual(data.get("user_scored_jobs"), 2)
                self.assertEqual(data.get("user_searches_count"), 2)

    def test_dashboard_sidebar_settings_button(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            db_file = tmp_path / "applypilot.db"
            conn = sqlite3.connect(str(db_file))
            conn.execute("""
                CREATE TABLE jobs (
                    id INTEGER PRIMARY KEY,
                    url TEXT,
                    title TEXT,
                    company TEXT,
                    location TEXT,
                    site TEXT,
                    search_id TEXT,
                    is_saved INTEGER DEFAULT 0,
                    is_archived INTEGER DEFAULT 0,
                    apply_status TEXT DEFAULT 'new'
                )
            """)
            conn.execute("INSERT INTO jobs (url, title, company, location, site, search_id) VALUES ('https://job.test/1', 'Product Lead', 'Tech Corp', 'Remote', 'LinkedIn', 's1')")
            conn.commit()
            conn.close()

            searches_file = tmp_path / "searches.yaml"
            searches_file.write_text("searches:\n  - id: s1\n    name: Product Lead\n    queries: ['Product Lead']\n", encoding="utf-8")

            with patch.dict("os.environ", {"APPLYPILOT_DIR": str(tmp_path)}):
                out = generate_dashboard()
                self.assertTrue(out.exists())
                content = out.read_text(encoding="utf-8")
                # Settings button moved to sidebar where Save My Searches was
                self.assertIn('id="sidebar-settings-btn"', content)
                self.assertIn('href="/admin"', content)
                self.assertIn('Settings', content)
                self.assertNotIn('id="save-searches-btn"', content)
                # Removed from header
                self.assertNotIn('id="header-admin-cockpit-btn"', content)
                self.assertNotIn('id="header-ai-config-btn"', content)


if __name__ == "__main__":
    unittest.main()
