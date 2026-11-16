"""Unit and integration tests for Fase 3:
- Task #26: High-Match LinkedIn Outreach & Contact Strategy Generator
- Task #23: Direct ATS REST API Connectors (Greenhouse, Ashby, Lever)
"""

import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from applypilot.database import init_db
from applypilot.discovery.providers.ats_providers import (
    detect_ats_provider,
    fetch_ashby_jobs,
    fetch_ats_jobs_from_url,
    fetch_greenhouse_jobs,
    fetch_lever_jobs,
)
from applypilot.discovery.providers.latam_providers import fetch_hirewithnear_jobs
from applypilot.scoring.outreach import (
    build_template_messages,
    generate_outreach_strategies,
    get_contact_archetypes,
)


class TestFase3Outreach(unittest.TestCase):
    """Test suite for Task #26: LinkedIn Outreach Strategy."""

    def test_contact_archetypes_product(self):
        archs = get_contact_archetypes("Mercado Libre", "Senior Digital Product Manager")
        self.assertEqual(len(archs), 3)
        roles = [a["archetype_role"] for a in archs]
        self.assertIn("Hiring Manager", roles)
        self.assertIn("Talent Acquisition", roles)
        self.assertIn("Peer Connection", roles)
        for a in archs:
            self.assertTrue(a["search_url"].startswith("https://www.linkedin.com/search/results/people/?keywords="))
            self.assertIn("Mercado%20Libre", a["search_url"])

    def test_contact_archetypes_engineering(self):
        archs = get_contact_archetypes("Stripe", "Senior Backend Engineer")
        self.assertEqual(len(archs), 3)
        self.assertTrue(any("Engineering Manager" in a["query"] for a in archs))

    def test_build_template_messages_strict_bounds(self):
        # Test extreme long names and company names
        long_company = "Super Long International Corporation Global Holdings Ltd"
        long_title = "Principal Solutions Architect and Enterprise Transformation Lead"
        long_name = "Alexander Maximilian Bartholomew-Montgomery"
        long_skill = "Distributed Event-Driven Architecture, High-Throughput Microservices, Kubernetes"

        msgs = build_template_messages(long_company, long_title, long_name, long_skill)
        self.assertIn("connection_note", msgs)
        self.assertIn("recruiter_pitch", msgs)
        self.assertIn("peer_inquiry", msgs)

        # Connection note MUST be <= 300 characters
        cn = msgs["connection_note"]
        self.assertLessEqual(len(cn), 300, f"Connection note exceeded 300 chars: {len(cn)}")
        self.assertGreater(len(cn), 20)

    def test_generate_outreach_strategies_structure(self):
        job = {
            "title": "Lead Software Engineer",
            "company": "Vercel",
            "description": "Looking for Next.js and TypeScript experts.",
            "fit_score": 9.2,
        }
        profile = {
            "personal": {
                "preferred_name": "Alex Morgan",
                "email": "alex.morgan@example.com",
            },
            "skills_boundary": {
                "backend": ["Python", "FastAPI"],
                "frontend": ["React", "TypeScript"],
            },
        }

        res = generate_outreach_strategies(job, profile, use_llm=False)
        self.assertEqual(res["company"], "Vercel")
        self.assertEqual(res["title"], "Lead Software Engineer")
        self.assertEqual(len(res["archetypes"]), 3)

        msgs = res["messages"]
        self.assertIn("connection_note", msgs)
        self.assertIn("recruiter_pitch", msgs)
        self.assertIn("peer_inquiry", msgs)

        cn_data = msgs["connection_note"]
        self.assertLessEqual(cn_data["char_count"], 300)
        self.assertEqual(cn_data["max_chars"], 300)


class TestFase3ATSConnectors(unittest.TestCase):
    """Test suite for Task #23: Direct ATS REST API Connectors."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_ats.db"
        self.conn = init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_detect_ats_provider(self):
        # Greenhouse
        self.assertEqual(detect_ats_provider("https://boards.greenhouse.io/stripe"), ("greenhouse", "stripe"))
        self.assertEqual(detect_ats_provider("https://job-boards.greenhouse.io/airbnb"), ("greenhouse", "airbnb"))
        self.assertEqual(detect_ats_provider("https://boards.greenhouse.io/embed/job_board?for=gusto"), ("greenhouse", "gusto"))
        self.assertEqual(detect_ats_provider("greenhouse:stripe"), ("greenhouse", "stripe"))

        # Ashby
        self.assertEqual(detect_ats_provider("https://jobs.ashbyhq.com/linear"), ("ashby", "linear"))
        self.assertEqual(detect_ats_provider("ashby:linear"), ("ashby", "linear"))

        # Lever
        self.assertEqual(detect_ats_provider("https://jobs.lever.co/netflix"), ("lever", "netflix"))
        self.assertEqual(detect_ats_provider("lever:netflix"), ("lever", "netflix"))

        # Non-ATS
        self.assertIsNone(detect_ats_provider("https://www.linkedin.com/jobs/view/12345"))
        self.assertIsNone(detect_ats_provider("https://getonbrd.com/jobs/123"))

    @patch("urllib.request.urlopen")
    def test_fetch_greenhouse_jobs(self, mock_urlopen):
        sample_response = {
            "jobs": [
                {
                    "id": 101,
                    "title": "Senior Backend Engineer",
                    "absolute_url": "https://boards.greenhouse.io/stripe/jobs/101",
                    "location": {"name": "Remote, Latin America"},
                    "content": "&lt;p&gt;We are seeking a senior engineer to scale payment rails.&lt;/p&gt;",
                },
                {
                    "id": 102,
                    "title": "Closed Role Engineer",
                    "absolute_url": "https://boards.greenhouse.io/stripe/jobs/102",
                    "location": {"name": "San Francisco, CA"},
                    "content": "<p>This position is closed and no longer accepting applications.</p>",
                },
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(sample_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = fetch_greenhouse_jobs("stripe", conn=self.conn, max_jobs=10)
        self.assertEqual(res["provider"], "greenhouse")
        self.assertEqual(res["board"], "stripe")
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["new"], 2)

        # Verify DB insertions
        cur = self.conn.cursor()
        rows = cur.execute("SELECT url, title, is_expired, expired_reason, site, strategy FROM jobs ORDER BY url").fetchall()
        self.assertEqual(len(rows), 2)

        # Job 101 should be live
        row101 = [r for r in rows if "101" in r[0]][0]
        self.assertEqual(row101[1], "Senior Backend Engineer")
        self.assertEqual(row101[2], 0)  # is_expired = 0
        self.assertEqual(row101[4], "Greenhouse")
        self.assertEqual(row101[5], "ats_api")

        # Job 102 should be detected as closed via liveness gate
        row102 = [r for r in rows if "102" in r[0]][0]
        self.assertEqual(row102[2], 1)  # is_expired = 1
        self.assertIn("no longer accepting applications", row102[3])

    @patch("urllib.request.urlopen")
    def test_fetch_ashby_jobs(self, mock_urlopen):
        sample_response = {
            "jobs": [
                {
                    "id": "uuid-1",
                    "title": "Product Designer",
                    "jobUrl": "https://jobs.ashbyhq.com/linear/uuid-1",
                    "applyUrl": "https://jobs.ashbyhq.com/linear/uuid-1/apply",
                    "location": "Americas",
                    "isRemote": True,
                    "descriptionPlain": "Design the next evolution of Linear.",
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(sample_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = fetch_ashby_jobs("linear", conn=self.conn, max_jobs=5)
        self.assertEqual(res["provider"], "ashby")
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["new"], 1)

        cur = self.conn.cursor()
        row = cur.execute("SELECT title, location, site, strategy FROM jobs WHERE url = ?", ("https://jobs.ashbyhq.com/linear/uuid-1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Product Designer")
        self.assertIn("Remote", row[1])
        self.assertEqual(row[2], "Ashby")
        self.assertEqual(row[3], "ats_api")

    @patch("urllib.request.urlopen")
    def test_fetch_lever_jobs(self, mock_urlopen):
        sample_response = [
            {
                "id": "lever-uuid-99",
                "text": "Senior Data Platform Engineer",
                "hostedUrl": "https://jobs.lever.co/netflix/lever-uuid-99",
                "applyUrl": "https://jobs.lever.co/netflix/lever-uuid-99/apply",
                "categories": {
                    "location": "Remote - US / LATAM",
                    "commitment": "Full-time",
                },
                "descriptionPlain": "Build streaming data platforms.",
                "lists": [
                    {"text": "Requirements", "content": "<li>5+ years Python or Scala</li>"}
                ]
            }
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(sample_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = fetch_lever_jobs("netflix", conn=self.conn, max_jobs=5)
        self.assertEqual(res["provider"], "lever")
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["new"], 1)

        cur = self.conn.cursor()
        row = cur.execute("SELECT title, site, full_description FROM jobs WHERE url = ?", ("https://jobs.lever.co/netflix/lever-uuid-99",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Senior Data Platform Engineer")
        self.assertEqual(row[1], "Lever")
        self.assertIn("Requirements", row[2])

    @patch("urllib.request.urlopen")
    def test_fetch_ats_jobs_from_url_dispatch(self, mock_urlopen):
        sample_response = {"jobs": [{"id": 1, "title": "Staff Engineer", "absolute_url": "https://boards.greenhouse.io/gusto/1", "content": "Job desc"}]}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(sample_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = fetch_ats_jobs_from_url("https://boards.greenhouse.io/gusto", conn=self.conn)
        self.assertTrue(res["success"])
        self.assertEqual(res["provider"], "greenhouse")
        self.assertEqual(res["board"], "gusto")


class TestHireWithNearProvider(unittest.TestCase):
    """Test suite for Hire With Near native REST API integration."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_near.db"
        self.conn = init_db(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    @patch("urllib.request.urlopen")
    def test_fetch_hirewithnear_jobs_success_and_dedup(self, mock_urlopen):
        import time
        now_ms = int(time.time() * 1000)

        sample_response = {
            "total": 1,
            "jobs": [
                {
                    "id": "1001",
                    "title": "Senior Full-Stack Engineer",
                    "clientCorporation": "Hire With Near",
                    "description": "<p>Build scalable microservices with Python and React.</p>",
                    "publishedCategory": {"name": "Engineering"},
                    "salaryLow": 5000,
                    "salaryHigh": 7000,
                    "salaryUnit": "monthly",
                    "datePosted": now_ms,
                    "isOpen": True,
                    "publicDescription": "US remote salary: $5,000 - $7,000 USD/month. Fully remote Latin America.",
                    "address": {"city": "Remote", "state": "Latin America"},
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(sample_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = fetch_hirewithnear_jobs("python", conn=self.conn, hours_old=48)
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["new"], 1)
        self.assertEqual(res["existing"], 0)

        cur = self.conn.cursor()
        row = cur.execute(
            "SELECT title, company, site, location, salary, strategy FROM jobs WHERE url = ?",
            ("https://jobs.hirewithnear.com/jobs/1001",),
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Senior Full-Stack Engineer")
        self.assertEqual(row[1], "Hire With Near")
        self.assertEqual(row[2], "Hire With Near")
        self.assertIn("Latin America", row[3])
        self.assertEqual(row[4], "$5,000 - $7,000 USD/monthly")
        self.assertEqual(row[5], "api")

        # Second fetch should deduplicate
        res2 = fetch_hirewithnear_jobs("python", conn=self.conn, hours_old=48)
        self.assertEqual(res2["new"], 0)
        self.assertEqual(res2["existing"], 1)

    @patch("urllib.request.urlopen")
    def test_fetch_hirewithnear_jobs_location_rejection(self, mock_urlopen):
        import time
        now_ms = int(time.time() * 1000)

        sample_response = {
            "total": 1,
            "jobs": [
                {
                    "id": "1002",
                    "title": "Onsite Specialist",
                    "clientCorporation": "Hire With Near",
                    "description": "<p>Requires in-office presence in Brazil.</p>",
                    "datePosted": now_ms,
                    "isOpen": True,
                    "address": {"city": "Sao Paulo", "state": "Brazil"},
                }
            ]
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(sample_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = fetch_hirewithnear_jobs("specialist", conn=self.conn, reject_locations=["brazil"])
        self.assertEqual(res["new"], 0)
        self.assertEqual(res["total"], 1)

    @patch("urllib.request.urlopen")
    def test_fetch_hirewithnear_jobs_api_error(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection timed out")
        res = fetch_hirewithnear_jobs("engineer", conn=self.conn)
        self.assertEqual(res["errors"], 1)
        self.assertEqual(res["new"], 0)


if __name__ == "__main__":
    unittest.main()
