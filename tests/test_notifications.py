"""Unit tests for Telegram and WhatsApp push notification services and endpoints."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

from applypilot.notifications import (
    dispatch_job_alerts,
    dispatch_telegram_job_alert,
    get_wifi_portal_url,
    send_telegram_message,
    send_test_telegram_notification,
    send_test_whatsapp_notification,
    send_whatsapp_message,
)
from applypilot.server import load_admin_config, save_admin_config


class TestNotifications(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    # --- Telegram Tests ---

    def test_send_telegram_message_validation(self):
        """Ensure empty credentials fail before making network requests."""
        ok, msg = send_telegram_message("", "123456", "Hello")
        self.assertFalse(ok)
        self.assertIn("Bot Token is required", msg)

        ok, msg = send_telegram_message("123:ABC", "", "Hello")
        self.assertFalse(ok)
        self.assertIn("Chat ID is required", msg)

    @patch("urllib.request.urlopen")
    def test_send_telegram_message_success(self, mock_urlopen):
        """Verify successful Telegram Bot API delivery."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "result": {"message_id": 101}}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        ok, msg = send_telegram_message("test_token_123", "987654", "<b>Test Message</b>")
        self.assertTrue(ok)
        self.assertIn("delivered successfully", msg)
        mock_urlopen.assert_called_once()

    @patch("urllib.request.urlopen")
    def test_send_telegram_message_api_error(self, mock_urlopen):
        """Verify handling of Telegram HTTP 401 Unauthorized."""
        err_body = json.dumps({"ok": False, "error_code": 401, "description": "Unauthorized"}).encode("utf-8")
        http_err = urllib.error.HTTPError(
            url="https://api.telegram.org/bot123/sendMessage",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(err_body),
        )
        mock_urlopen.side_effect = http_err

        ok, msg = send_telegram_message("invalid_token", "987654", "Test")
        self.assertFalse(ok)
        self.assertIn("401", msg)
        self.assertIn("Unauthorized", msg)

    @patch("urllib.request.urlopen")
    def test_send_test_telegram_notification(self, mock_urlopen):
        """Verify format and delivery of test notification."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        ok, msg = send_test_telegram_notification("bot_token_abc", "chat_123")
        self.assertTrue(ok)
        req = mock_urlopen.call_args[0][0]
        payload = json.loads(req.data.decode("utf-8"))
        self.assertEqual(payload["chat_id"], "chat_123")
        self.assertIn("DuckDuckWork Notification Test", payload["text"])

    # --- WhatsApp Tests ---

    def test_send_whatsapp_message_validation(self):
        """Ensure empty credentials fail before making CallMeBot network requests."""
        ok, msg = send_whatsapp_message("", "123456", "Hello")
        self.assertFalse(ok)
        self.assertIn("phone number is required", msg)

        ok, msg = send_whatsapp_message("+52 1 999 123 4567", "", "Hello")
        self.assertFalse(ok)
        self.assertIn("API Key is required", msg)

    @patch("urllib.request.urlopen")
    def test_send_whatsapp_message_success(self, mock_urlopen):
        """Verify successful CallMeBot WhatsApp delivery."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"Message queued successfully."
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        ok, msg = send_whatsapp_message("5219991234567", "key_abc", "DuckDuckWork Alert")
        self.assertTrue(ok)
        self.assertIn("queued successfully", msg)
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        self.assertIn("api.callmebot.com/whatsapp.php", req.full_url)
        self.assertIn("phone=5219991234567", req.full_url)
        self.assertIn("apikey=key_abc", req.full_url)

    @patch("urllib.request.urlopen")
    def test_send_whatsapp_message_callmebot_error(self, mock_urlopen):
        """Verify handling of CallMeBot error response text."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"Error: Invalid API Key"
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        ok, msg = send_whatsapp_message("5219991234567", "bad_key", "Test")
        self.assertFalse(ok)
        self.assertIn("Invalid API Key", msg)

    @patch("urllib.request.urlopen")
    def test_send_test_whatsapp_notification(self, mock_urlopen):
        """Verify format and delivery of WhatsApp test alert with local Wi-Fi link."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"Message queued successfully."
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        ok, msg = send_test_whatsapp_notification("14155552671", "key_999")
        self.assertTrue(ok)
        req = mock_urlopen.call_args[0][0]
        self.assertIn("phone=14155552671", req.full_url)
        self.assertIn("apikey=key_999", req.full_url)
        self.assertIn("DuckDuckWork", req.full_url)

    def test_get_wifi_portal_url(self):
        """Verify local Wi-Fi and configured external portal URL generation."""
        with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(self.app_dir)}):
            # 1. Unconfigured -> defaults to local Wi-Fi URL
            cfg = load_admin_config()
            cfg["server_external_url"] = ""
            save_admin_config(cfg)
            url = get_wifi_portal_url()
            self.assertTrue(url.startswith("http://"))
            self.assertIn(":5050", url)

            # 2. Configured external URL (e.g. Cloudflare tunnel)
            cfg["server_external_url"] = "https://my-tunnel.trycloudflare.com"
            save_admin_config(cfg)
            ext_url = get_wifi_portal_url(session_id="search_1")
            self.assertEqual(ext_url, "https://my-tunnel.trycloudflare.com/s/search_1")

    # --- Dual Dispatch & Filter Tests ---

    def test_dispatch_job_alerts_disabled(self):
        """Ensure no alert dispatched when all channels are disabled."""
        with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(self.app_dir)}):
            cfg = load_admin_config()
            cfg["telegram_enabled"] = False
            cfg["whatsapp_enabled"] = False
            save_admin_config(cfg)

            sample_jobs = [{"title": "PM", "company": "Acme", "fit_score": 85}]
            ok, msg = dispatch_job_alerts(sample_jobs)
            self.assertFalse(ok)
            self.assertIn("disabled", msg)

    @patch("urllib.request.urlopen")
    def test_dispatch_dual_channels(self, mock_urlopen):
        """Ensure both Telegram and WhatsApp are dispatched when both are active."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(self.app_dir)}):
            cfg = load_admin_config()
            cfg["telegram_enabled"] = True
            cfg["telegram_bot_token"] = "valid_bot_token"
            cfg["telegram_chat_id"] = "valid_chat_id"
            cfg["whatsapp_enabled"] = True
            cfg["whatsapp_phone"] = "5219991234567"
            cfg["whatsapp_api_key"] = "valid_wa_key"
            cfg["telegram_min_score"] = 0
            save_admin_config(cfg)

            jobs = [
                {"title": "Senior Product Manager", "company": "Acme", "fit_score": 92, "url": "https://example.com/2"},
            ]

            ok, msg = dispatch_job_alerts(jobs, search_name="Product Manager")
            self.assertTrue(ok)
            self.assertIn("Telegram: OK", msg)
            self.assertIn("WhatsApp: OK", msg)
            self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_dispatch_telegram_job_alert_with_fit_score_filter(self, mock_urlopen):
        """Ensure fit score filter excludes low-fit jobs and delivers matching ones."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(self.app_dir)}):
            cfg = load_admin_config()
            cfg["telegram_enabled"] = True
            cfg["telegram_bot_token"] = "valid_bot_token"
            cfg["telegram_chat_id"] = "valid_chat_id"
            cfg["telegram_min_score"] = 70
            save_admin_config(cfg)

            jobs = [
                {"title": "Junior Support", "company": "Co A", "fit_score": 45, "url": "https://example.com/1"},
                {"title": "Senior Product Manager", "company": "Acme", "fit_score": 88, "url": "https://example.com/2"},
            ]

            ok, msg = dispatch_telegram_job_alert(jobs, search_name="Product Manager")
            self.assertTrue(ok)
            req = mock_urlopen.call_args[0][0]
            payload = json.loads(req.data.decode("utf-8"))
            self.assertIn("1 new job", payload["text"])
            self.assertIn("Product Manager", payload["text"])
            self.assertIn("http://", payload["text"])

    @patch("urllib.request.urlopen")
    def test_dispatch_job_alert_with_total_pending_count(self, mock_urlopen):
        """Ensure notification format includes newly found count and total pending count."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(self.app_dir)}):
            cfg = load_admin_config()
            cfg["telegram_enabled"] = True
            cfg["telegram_bot_token"] = "valid_bot_token"
            cfg["telegram_chat_id"] = "valid_chat_id"
            cfg["telegram_min_score"] = 0
            save_admin_config(cfg)

            jobs = [{"title": "PM", "company": "Acme", "fit_score": 85}]
            ok, msg = dispatch_job_alerts(
                jobs,
                search_name="All Searches",
                new_found_count=10,
                total_pending_count=15,
            )
            self.assertTrue(ok)
            payload = json.loads(mock_urlopen.call_args[0][0].data.decode("utf-8"))
            self.assertIn("Found <b>10 new jobs</b> for <b>All Searches</b>.", payload["text"])
            self.assertIn("You have a total of <b>15 new jobs</b>.", payload["text"])

    @patch("urllib.request.urlopen")
    def test_dispatch_job_alert_dual_urls(self, mock_urlopen):
        """Ensure notification includes both Home Wi-Fi and Remote 5G links when external URL is configured."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch.dict("os.environ", {"APPLYPILOT_BASE_DIR": str(self.app_dir)}):
            cfg = load_admin_config()
            cfg["telegram_enabled"] = True
            cfg["telegram_bot_token"] = "valid_bot_token"
            cfg["telegram_chat_id"] = "valid_chat_id"
            cfg["telegram_min_score"] = 0
            cfg["server_external_url"] = "https://dual-test.trycloudflare.com"
            save_admin_config(cfg)

            jobs = [{"title": "PM", "company": "Acme", "fit_score": 85}]
            ok, msg = dispatch_job_alerts(
                jobs,
                search_name="Product Manager",
                session_id="product_manager",
            )
            self.assertTrue(ok)
            payload = json.loads(mock_urlopen.call_args[0][0].data.decode("utf-8"))
            self.assertIn("Home Wi-Fi (Faster):", payload["text"])
            self.assertIn(":5050/s/product_manager", payload["text"])
            self.assertIn("Remote (5G):", payload["text"])
            self.assertIn("https://dual-test.trycloudflare.com/s/product_manager", payload["text"])



if __name__ == "__main__":
    unittest.main()
