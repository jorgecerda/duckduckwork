"""Unit tests for Option C Hybrid Strategy (Cloud Quota Gate, Pro Waitlist, Batch Scoring, OpenRouter)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from applypilot.llm import _detect_provider, get_llm_status
from applypilot.scoring.scorer import _parse_batch_score_response, score_jobs_batch
from applypilot.server import (
    add_waitlist_lead,
    get_session_quota,
    get_waitlist_and_telemetry,
    record_telemetry_event,
    update_session_quota,
)
from applypilot.view import generate_admin_html, render_pro_waitlist_modal


class TestOptionCHybridCloud(unittest.TestCase):
    """Verify Option C hybrid cloud quota, waitlist lead capture, batch scoring, and OpenRouter support."""

    def test_parse_batch_score_response(self):
        raw = """[
          {"index": 0, "score": 88, "keywords": "Python, AWS, FastAPI", "reasoning": "Strong backend fit.", "summary": "Build scalable Python APIs on AWS."},
          {"index": 1, "score": 64, "keywords": "React, TypeScript", "reasoning": "Partial frontend overlap.", "summary": "Develop frontend UI components."}
        ]"""
        parsed = _parse_batch_score_response(raw, 2)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["score"], 88)
        self.assertIn("Python", parsed[0]["keywords"])
        self.assertEqual(parsed[1]["score"], 64)

    @patch("applypilot.scoring.scorer.get_client")
    def test_score_jobs_batch_single_call(self, mock_get_client):
        mock_client = MagicMock()
        mock_client._is_gemini = False
        mock_client.chat.return_value = (
            "JOB_INDEX: 0\nSCORE: 9\nSUMMARY: Lead Python data pipelines.\nKEYWORDS: Python, SQL\nREASONING: Direct match.\n===JOB_END===\n"
            "JOB_INDEX: 1\nSCORE: 7\nSUMMARY: Build cloud APIs.\nKEYWORDS: AWS, Docker\nREASONING: Solid cloud overlap.\n===JOB_END==="
        )
        mock_get_client.return_value = mock_client

        jobs = [
            {"url": "https://example.com/job/1", "title": "Senior Python Engineer", "company": "Acme", "description": "Python and SQL role"},
            {"url": "https://example.com/job/2", "title": "Cloud Engineer", "company": "Beta", "description": "AWS and Docker role"},
        ]
        res = score_jobs_batch("Senior Python Developer with 8 years SQL experience", jobs)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["score"], 9)
        self.assertEqual(res[0]["url"], "https://example.com/job/1")
        self.assertEqual(res[1]["score"], 7)
        mock_client.chat.assert_called_once()

    def test_session_quota_local_vs_cloud(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with patch.dict(os.environ, {"DUCKDUCKWORK_CLOUD_MODE": "0", "VERCEL": ""}, clear=False):
                q_local = get_session_quota(tmp_path)
                self.assertFalse(q_local["cloud_mode"])
                self.assertTrue(q_local["unlimited"])
                self.assertEqual(q_local["searches_remaining"], 999)

            with patch.dict(os.environ, {"DUCKDUCKWORK_CLOUD_MODE": "1"}, clear=False):
                q_cloud = get_session_quota(tmp_path)
                self.assertTrue(q_cloud["cloud_mode"])
                self.assertFalse(q_cloud["unlimited"])
                self.assertEqual(q_cloud["searches_limit"], 2)
                self.assertEqual(q_cloud["searches_remaining"], 2)

                # Use 2 searches -> 0 remaining
                q_used = update_session_quota(tmp_path, searches_used=2)
                self.assertEqual(q_used["searches_remaining"], 0)

                # Grant waitlist bonus -> +1 search unlocked
                q_bonus = update_session_quota(tmp_path, bonus_granted=True)
                self.assertEqual(q_bonus["searches_limit"], 3)
                self.assertEqual(q_bonus["searches_remaining"], 1)

                # Connect BYOK -> unlimited
                q_byok = update_session_quota(tmp_path, byok_active=True)
                self.assertTrue(q_byok["unlimited"])
                self.assertEqual(q_byok["searches_remaining"], 999)

    @patch("applypilot.server.get_postgres_connection", return_value=None)
    def test_waitlist_and_telemetry_persistence(self, _mock_pg):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"APPLYPILOT_BASE_DIR": tmpdir}, clear=False):
                lead = add_waitlist_lead("founder@duckduckwork.com", "Staff Engineer", "sess_123", "pro_modal")
                self.assertEqual(lead["email"], "founder@duckduckwork.com")
                record_telemetry_event("paywall_views")
                record_telemetry_event("github_clicks")

                wt = get_waitlist_and_telemetry(Path(tmpdir))
                self.assertEqual(len(wt["waitlist"]), 1)
                self.assertEqual(wt["waitlist"][0]["target_role"], "Staff Engineer")
                self.assertEqual(wt["telemetry"]["waitlist_signups"], 1)
                self.assertEqual(wt["telemetry"]["paywall_views"], 1)
                self.assertEqual(wt["telemetry"]["github_clicks"], 1)

    def test_openrouter_free_provider_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "APPLYPILOT_BASE_DIR": tmpdir,
                    "OPENROUTER_API_KEY": "sk-or-v1-testkey",
                    "GEMINI_API_KEY": "",
                    "OPENAI_API_KEY": "",
                    "LLM_URL": "",
                    "LLM_MODEL": "",
                },
                clear=False,
            ):
                url, model, key = _detect_provider()
                self.assertEqual(url, "https://openrouter.ai/api/v1")
                self.assertEqual(key, "sk-or-v1-testkey")
                self.assertIn(":free", model)
                status = get_llm_status()
                self.assertEqual(status["provider"], "openrouter")
                self.assertTrue(status["configured"])

    def test_ui_modals_and_admin_growth_panel(self):
        modal_html = render_pro_waitlist_modal()
        self.assertIn('id="pro-waitlist-modal"', modal_html)
        self.assertIn('id="pro-waitlist-email"', modal_html)
        self.assertIn("Join Waitlist (+1 Free Search)", modal_html)
        self.assertIn("Download Repo", modal_html)

        admin_html = generate_admin_html()
        self.assertIn('id="tab-btn-growth"', admin_html)
        self.assertIn('id="panel-growth"', admin_html)
        self.assertIn('id="stat-waitlist-signups"', admin_html)
        self.assertIn('id="waitlist-table-body"', admin_html)


if __name__ == "__main__":
    unittest.main()
