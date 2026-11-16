"""Unit tests for DuckDuckWork security features and baseline protections.

Standard library unittest test suite for:
- At-rest field encryption & decryption (encrypt_sensitive, decrypt_sensitive)
- IP rate limiting on admin login (check_rate_limit, record_failed_login)
- Time-bound 24h HMAC admin token verification (generate_hmac_token, verify_hmac_token)
- Anti-CSRF token verification (generate_csrf_token, verify_csrf_token)
- 7-day session inactivity TTL & soft restoration logic (check_and_clean_inactive_session)
"""

import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from applypilot.database import decrypt_sensitive, encrypt_sensitive
from applypilot.server import (
    check_and_clean_inactive_session,
    check_rate_limit,
    generate_csrf_token,
    generate_hmac_token,
    record_failed_login,
    verify_csrf_token,
    verify_hmac_token,
)


class TestDuckDuckWorkSecurity(unittest.TestCase):

    def test_encryption_decryption_at_rest(self):
        """Test Item E: Sensitive data field encryption and decryption at rest."""
        secret_key = "test_secret_key_123"
        plain_note = "Submitted salary expectation: $120,000 / year"

        # Encrypt plain note
        encrypted = encrypt_sensitive(plain_note, key=secret_key)
        self.assertIsNotNone(encrypted)
        self.assertTrue(encrypted.startswith("enc:v1:"))
        self.assertNotIn(plain_note, encrypted)

        # Decrypt encrypted note
        decrypted = decrypt_sensitive(encrypted, key=secret_key)
        self.assertEqual(decrypted, plain_note)

        # Pass plain string or None safely
        self.assertEqual(decrypt_sensitive("Plain text unencrypted"), "Plain text unencrypted")
        self.assertIsNone(encrypt_sensitive(None))

    def test_admin_login_rate_limiting(self):
        """Test Item B: IP rate limiting for failed admin login attempts."""
        test_ip = "192.168.1.99"

        # Initially allowed
        self.assertTrue(check_rate_limit(test_ip, max_attempts=5, window_sec=900))

        # Record 4 failed attempts -> still allowed
        for _ in range(4):
            record_failed_login(test_ip)
        self.assertTrue(check_rate_limit(test_ip, max_attempts=5, window_sec=900))

        # Record 5th failed attempt -> blocked
        record_failed_login(test_ip)
        self.assertFalse(check_rate_limit(test_ip, max_attempts=5, window_sec=900))

    def test_hmac_token_generation_and_verification(self):
        """Test Item C: Time-bound 24h HMAC admin tokens."""
        secret = "admin_password_secret"

        # Valid token
        token = generate_hmac_token(secret)
        self.assertTrue(verify_hmac_token(token, secret=secret, max_age=86400))

        # Invalid secret
        self.assertFalse(verify_hmac_token(token, secret="wrong_secret", max_age=86400))

        # Expired token (simulate past timestamp)
        old_ts = int(time.time()) - 90000  # > 24 hours ago
        expired_token = f"{old_ts}:invalid_signature"
        self.assertFalse(verify_hmac_token(expired_token, secret=secret, max_age=86400))

    def test_anti_csrf_token_verification(self):
        """Test Item F: Anti-CSRF token verification for destructive admin operations."""
        secret = "admin_csrf_secret"

        csrf_token = generate_csrf_token(secret)
        self.assertTrue(verify_csrf_token(csrf_token, secret=secret))
        self.assertFalse(verify_csrf_token("invalid_csrf_token", secret=secret))
        self.assertFalse(verify_csrf_token(None, secret=secret))

    def test_session_lifecycle_5day_10day_and_permanent(self):
        """Test 5-day inactive soft-purge, 10-day hard deletion, and permanent lock exemption."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            os.environ["APPLYPILOT_BASE_DIR"] = str(temp_dir)
            session_dir = temp_dir / "sessions" / "test_session_5d"
            session_dir.mkdir(parents=True, exist_ok=True)

            db_file = session_dir / "applypilot.db"
            conn = sqlite3.connect(str(db_file))
            conn.execute("""
                CREATE TABLE jobs (
                    url TEXT PRIMARY KEY,
                    title TEXT,
                    full_description TEXT,
                    is_saved INTEGER,
                    is_archived INTEGER,
                    apply_status TEXT,
                    discovered_at TEXT
                )
            """)

            # Insert 1 saved job, 1 applied job, 1 archived job, and 1 unclassified new job
            conn.execute("INSERT INTO jobs VALUES ('http://job1', 'Saved Job', 'Full 1', 1, 0, 'unapplied', '2026-09-20')")
            conn.execute("INSERT INTO jobs VALUES ('http://job2', 'Applied Job', 'Full 2', 0, 0, 'applied', '2026-09-20')")
            conn.execute("INSERT INTO jobs VALUES ('http://job3', 'Unclassified New Job', 'Full 3', 0, 0, 'new', '2026-09-20')")
            conn.execute("INSERT INTO jobs VALUES ('http://job4', 'Archived Job', 'Full 4', 0, 1, 'unapplied', '2026-09-20')")
            conn.commit()
            conn.close()

            # 1) Test 6 days inactive -> 5-day inactive soft purge
            six_days_ago = time.time() - (6 * 86400)
            os.utime(session_dir, (six_days_ago, six_days_ago))

            res = check_and_clean_inactive_session(session_dir, inactive_days=5, delete_days=10)
            self.assertEqual(res["status"], "inactive")

            conn = sqlite3.connect(str(db_file))
            urls = [r[0] for r in conn.execute("SELECT url FROM jobs").fetchall()]
            conn.close()

            self.assertIn("http://job1", urls)  # Saved job kept
            self.assertIn("http://job2", urls)  # Applied job kept
            self.assertIn("http://job4", urls)  # Archived job kept
            self.assertNotIn("http://job3", urls)  # Unclassified new job purged
            self.assertTrue((session_dir / ".inactive").exists())

            # 2) Test 11 days inactive -> 10-day deletion
            eleven_days_ago = time.time() - (11 * 86400)
            os.utime(session_dir, (eleven_days_ago, eleven_days_ago))

            res2 = check_and_clean_inactive_session(session_dir, inactive_days=5, delete_days=10)
            self.assertTrue(res2["deleted"])
            self.assertFalse(session_dir.exists())

            # 3) Test Permanent session exemption
            admin_cfg_file = temp_dir / "admin_config.json"
            admin_cfg_file.write_text(json.dumps({"permanent_sessions": ["perm_session_123"]}), encoding="utf-8")
            perm_dir = temp_dir / "sessions" / "perm_session_123"
            perm_dir.mkdir(parents=True, exist_ok=True)
            fifteen_days_ago = time.time() - (15 * 86400)
            os.utime(perm_dir, (fifteen_days_ago, fifteen_days_ago))

            res_perm = check_and_clean_inactive_session(perm_dir, inactive_days=5, delete_days=10)
            self.assertEqual(res_perm["status"], "active")
            self.assertTrue(perm_dir.exists())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
