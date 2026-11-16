"""Tests for Fase 1: Task #22 (Liveness & Ghost Job Detector) and Task #24 (Voice DNA Filter)."""

import tempfile
import unittest
from pathlib import Path

from applypilot.database import init_db, normalize_job_title, record_scan_history
from applypilot.discovery.liveness import check_posting_liveness
from applypilot.scoring.validator import BANNED_WORDS, PACING_RULES


class TestFase1Features(unittest.TestCase):
    def test_voice_dna_loading(self):
        """Verify Voice DNA rules and banned words load properly."""
        self.assertGreater(len(BANNED_WORDS), 50)
        self.assertGreater(len(PACING_RULES), 3)
        self.assertIn("delve", BANNED_WORDS)
        self.assertIn("leverage", BANNED_WORDS)
        self.assertIn("spearheaded", BANNED_WORDS)

    def test_liveness_detection_english(self):
        """Verify English closure phrases trigger is_live=False."""
        live_res = check_posting_liveness("We are seeking an experienced Backend Engineer to join our team. Apply today!")
        self.assertTrue(live_res["is_live"])

        closed_res = check_posting_liveness("Thank you for your interest. This position has been filled.")
        self.assertFalse(closed_res["is_live"])
        self.assertIn("position has been filled", closed_res["reason"])

        expired_res = check_posting_liveness("Notice: this job posting has expired and is no longer available.")
        self.assertFalse(expired_res["is_live"])

    def test_liveness_detection_spanish(self):
        """Verify Spanish closure phrases trigger is_live=False."""
        closed_es = check_posting_liveness("Lamentamos informarle que esta vacante ya no está disponible para nuevos candidatos.")
        self.assertFalse(closed_es["is_live"])

        expired_es = check_posting_liveness("Esta oferta ya ha expirado.")
        # Very short text (< 50 chars) or pattern match returns is_live=False
        self.assertFalse(expired_es["is_live"])

    def test_title_normalization(self):
        """Verify title identity normalizes location and noise markers."""
        t1 = normalize_job_title("Senior Software Engineer - Remote LATAM (Guatemala)")
        t2 = normalize_job_title("Software Engineer, Senior")
        self.assertEqual(t1, t2)

    def test_scan_history_and_repost_counter(self):
        """Verify SQLite scan_history tracks distinct URLs across 90 days for ghost job detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_repost.db"
            conn = init_db(db_path)

            # First scan of a role
            c1 = record_scan_history(conn, "https://jobs.com/1", "Acme Corp", "Staff Data Engineer", "search_1")
            self.assertEqual(c1, 1)

            # Re-scanning the exact same URL does not inflate repost count
            c1_repeat = record_scan_history(conn, "https://jobs.com/1", "Acme Corp", "Staff Data Engineer", "search_1")
            self.assertEqual(c1_repeat, 1)

            # New posting URL for the same company and normalized title (ghost job repost)
            c2 = record_scan_history(conn, "https://jobs.com/2", "Acme Corp", "Data Engineer, Staff - Remote", "search_1")
            self.assertEqual(c2, 2)

            # 3rd posting URL
            c3 = record_scan_history(conn, "https://jobs.com/3", "Acme Corp", "Staff Data Engineer", "search_1")
            self.assertEqual(c3, 3)

            # Unrelated company role
            c_other = record_scan_history(conn, "https://jobs.com/4", "Beta Inc", "Staff Data Engineer", "search_1")
            self.assertEqual(c_other, 1)


if __name__ == "__main__":
    unittest.main()
