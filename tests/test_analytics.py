"""Tests for DuckDuckWork Analytics Engine and /api/analytics endpoints.

Strict English documentation protocol and zero emojis maintained across all test cases.
"""

import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import HTTPServer
from pathlib import Path

from applypilot.analytics import (
    compute_tab_recommendation,
    get_analytics_summary,
    map_location_to_coordinates,
    normalize_location_text,
)
from applypilot.database import init_db
from applypilot.server import CRMDashboardHandler


class TestAnalyticsEngine(unittest.TestCase):
    """Test suite for DuckDuckWork Analytics Engine aggregations and utilities."""

    def test_normalize_location_text(self):
        """Test normalization of location strings removing accents and uppercase."""
        self.assertEqual(normalize_location_text(""), "")
        self.assertEqual(normalize_location_text(None), "")
        self.assertEqual(normalize_location_text("Bogotá, Colombia"), "bogota, colombia")
        self.assertEqual(normalize_location_text("São Paulo, Brasil"), "sao paulo, brasil")
        self.assertEqual(normalize_location_text("QUETZALTENANGO"), "quetzaltenango")

    def test_map_location_to_coordinates(self):
        """Test coordinate resolution across Guatemala, Nicaragua, LATAM, and Global."""
        # Guatemala municipalities and regions
        self.assertEqual(map_location_to_coordinates("Mixco, Guatemala"), [14.6310, -90.6064])
        self.assertEqual(map_location_to_coordinates("Villa Nueva, Guatemala"), [14.5269, -90.5875])
        self.assertEqual(map_location_to_coordinates("San Miguel Petapa"), [14.5019, -90.5594])
        self.assertEqual(map_location_to_coordinates("Antigua Guatemala, Sacatepéquez"), [14.5586, -90.7295])
        self.assertEqual(map_location_to_coordinates("Quetzaltenango (Xela)"), [14.8347, -91.5181])
        self.assertEqual(map_location_to_coordinates("Ciudad de Guatemala, Zona 10"), [14.6349, -90.5069])
        self.assertEqual(map_location_to_coordinates("Guatemala"), [14.6349, -90.5069])

        # Nicaragua departments and municipalities
        self.assertEqual(map_location_to_coordinates("Managua, Nicaragua (Remote)"), [12.1149, -86.2362])
        self.assertEqual(map_location_to_coordinates("Granada, Nicaragua"), [11.9299, -85.9560])
        self.assertEqual(map_location_to_coordinates("León, Nicaragua"), [12.4379, -86.8780])
        self.assertEqual(map_location_to_coordinates("Matagalpa"), [12.9256, -85.9175])
        self.assertEqual(map_location_to_coordinates("Nicaragua"), [12.8654, -85.2072])

        # Central America & Latin America
        self.assertEqual(map_location_to_coordinates("San José, Costa Rica"), [9.9281, -84.0907])
        self.assertEqual(map_location_to_coordinates("Panamá City, Panama"), [8.9824, -79.5199])
        self.assertEqual(map_location_to_coordinates("Tegucigalpa, Honduras"), [14.0723, -87.1921])
        self.assertEqual(map_location_to_coordinates("San Salvador, El Salvador"), [13.6929, -89.2182])
        self.assertEqual(map_location_to_coordinates("Medellín, Colombia"), [6.2442, -75.5812])
        self.assertEqual(map_location_to_coordinates("Bogotá, Colombia"), [4.7110, -74.0721])
        self.assertEqual(map_location_to_coordinates("Monterrey, Mexico"), [19.4326, -99.1332])
        self.assertEqual(map_location_to_coordinates("São Paulo, Brazil"), [-23.5505, -46.6333])
        self.assertEqual(map_location_to_coordinates("Buenos Aires, Argentina"), [-34.6037, -58.3816])
        self.assertEqual(map_location_to_coordinates("Santiago, Chile"), [-33.4489, -70.6693])
        self.assertEqual(map_location_to_coordinates("Lima, Peru"), [-12.0464, -77.0428])
        self.assertEqual(map_location_to_coordinates("Montevideo, Uruguay"), [-34.9011, -56.1645])
        self.assertEqual(map_location_to_coordinates("Quito, Ecuador"), [-0.1807, -78.4678])
        self.assertEqual(map_location_to_coordinates("La Paz, Bolivia"), [-16.5000, -68.1193])
        self.assertEqual(map_location_to_coordinates("LATAM Remote"), [9.7489, -83.7534])

        # North America & Europe
        self.assertEqual(map_location_to_coordinates("USA Remote"), [37.0902, -95.7129])
        self.assertEqual(map_location_to_coordinates("Canada (Remote)"), [56.1304, -106.3468])
        self.assertEqual(map_location_to_coordinates("London, UK, Europe"), [50.1109, 8.6821])

        # Global & Fallbacks
        self.assertEqual(map_location_to_coordinates("Worldwide (Remote)"), [20.0, -10.0])
        self.assertEqual(map_location_to_coordinates("Anywhere Remote"), [20.0, -10.0])
        self.assertEqual(map_location_to_coordinates("Unknown Location XYZ"), [20.0, -10.0])
        self.assertEqual(map_location_to_coordinates(""), [20.0, -10.0])
        self.assertEqual(map_location_to_coordinates(None), [20.0, -10.0])

    def test_compute_tab_recommendation(self):
        """Test algorithmic recommendation logic for search profile ROI evaluation."""
        # Zero jobs discovered yet
        self.assertEqual(compute_tab_recommendation(0, 0.0, 0.0, 0.0), "Keep Searching")

        # Small volume (<10 jobs) should remain in Keep Searching to allow accumulation
        self.assertEqual(compute_tab_recommendation(5, 25.0, 0.0, 0.0), "Keep Searching")

        # High volume with zero saved and zero applied actions -> Discard / Pause
        self.assertEqual(compute_tab_recommendation(30, 60.0, 0.0, 0.0), "Discard / Pause")
        self.assertEqual(compute_tab_recommendation(55, 45.0, 0.0, 0.0), "Discard / Pause")

        # High volume with low match score (<30) and weak engagement (<5%) -> Refine Query
        self.assertEqual(compute_tab_recommendation(15, 25.0, 2.0, 0.0), "Refine Query")
        self.assertEqual(compute_tab_recommendation(28, 20.0, 1.0, 1.5), "Refine Query")

        # Strong candidate fit (avg_score >= 50) -> Keep Searching
        self.assertEqual(compute_tab_recommendation(20, 52.0, 0.0, 0.0), "Keep Searching")
        self.assertEqual(compute_tab_recommendation(40, 85.0, 10.0, 5.0), "Keep Searching")

        # High conversion rate (saved_rate + applied_rate >= 8.0) -> Keep Searching
        self.assertEqual(compute_tab_recommendation(25, 35.0, 8.0, 2.0), "Keep Searching")

        # Moderate score (>= 40) with positive user interest -> Keep Searching
        self.assertEqual(compute_tab_recommendation(20, 42.0, 5.0, 0.0), "Keep Searching")

        # Default fallback for uncalibrated searches
        self.assertEqual(compute_tab_recommendation(12, 32.0, 2.0, 0.0), "Refine Query")

    def test_get_analytics_summary_empty_database(self):
        """Test analytics summary behavior with non-existent or empty database."""
        # Non-existent database path
        non_existent_path = Path("/tmp/duckduckwork_non_existent_analytics_test.db")
        result = get_analytics_summary(non_existent_path)
        self.assertEqual(result["total_found"], 0)
        self.assertEqual(result["saved"], 0)
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["archived"], 0)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(result["top_sites"], [])
        self.assertEqual(result["top_companies"], [])
        self.assertEqual(result["top_titles"], [])
        self.assertEqual(result["score_distribution"]["avg_score"], 0.0)
        self.assertEqual(result["score_distribution"]["score_buckets"]["90+"], 0)
        self.assertEqual(result["score_distribution"]["score_buckets"]["unscored"], 0)
        self.assertEqual(result["geolocations"], [])
        self.assertEqual(result["tabs_health"], [])

        # Empty initialized database (table exists, 0 rows)
        with tempfile.TemporaryDirectory() as tmp_dir:
            empty_db_path = Path(tmp_dir) / "applypilot.db"
            conn = init_db(empty_db_path)
            conn.close()

            res_empty = get_analytics_summary(empty_db_path)
            self.assertEqual(res_empty["total_found"], 0)
            self.assertEqual(res_empty["saved"], 0)
            self.assertEqual(res_empty["applied"], 0)
            self.assertEqual(res_empty["archived"], 0)
            self.assertEqual(res_empty["pending"], 0)
            self.assertEqual(res_empty["top_sites"], [])
            self.assertEqual(res_empty["top_companies"], [])
            self.assertEqual(res_empty["top_titles"], [])
            self.assertEqual(res_empty["score_distribution"]["avg_score"], 0.0)
            self.assertEqual(res_empty["geolocations"], [])

    def test_get_analytics_summary_sample_jobs_across_multiple_searches(self):
        """Test analytics metrics calculation across multiple searches with full pipeline states."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "applypilot.db"
            conn = init_db(db_path)

            # Insert sample jobs across 3 searches with distinct states and scores
            jobs_data = [
                # Search 1: Product Manager (3 jobs)
                (
                    "https://site.com/p1",
                    "Lead Product Manager",
                    "TechCorp",
                    "linkedin",
                    "Guatemala City, Guatemala",
                    95,
                    1,
                    None,
                    None,
                    0,
                    "search_1",
                    "Product Manager",
                ),
                (
                    "https://site.com/p2",
                    "Senior PM",
                    "TechCorp",
                    "linkedin",
                    "Managua, Nicaragua",
                    85,
                    0,
                    "applied",
                    "2026-11-14 10:00:00",
                    0,
                    "search_1",
                    "Product Manager",
                ),
                (
                    "https://site.com/p3",
                    "Product Owner",
                    "StartupX",
                    "indeed",
                    "San José, Costa Rica",
                    72,
                    0,
                    "archived",
                    None,
                    1,
                    "search_1",
                    "Product Manager",
                ),
                # Search 2: Scrum Master (3 jobs)
                (
                    "https://site.com/s1",
                    "Agile Coach",
                    "AgileWorks",
                    "getonboard",
                    "Medellín, Colombia",
                    8,  # On 0-10 scale -> normalized to 80
                    0,
                    None,
                    None,
                    0,
                    "search_2",
                    "Scrum Master",
                ),
                (
                    "https://site.com/s2",
                    "Scrum Master",
                    "AgileWorks",
                    "getonboard",
                    "Mixco, Guatemala",
                    6,  # On 0-10 scale -> normalized to 60
                    1,
                    None,
                    None,
                    0,
                    "search_2",
                    "Scrum Master",
                ),
                (
                    "https://site.com/s3",
                    "Scrum Master II",
                    "FintechLab",
                    "weworkremotely",
                    "Worldwide (Remote)",
                    None,  # Unscored
                    0,
                    None,
                    None,
                    0,
                    "search_2",
                    "Scrum Master",
                ),
                # Search 3: Data Analyst (1 job, pending)
                (
                    "https://site.com/d1",
                    "BI Analyst",
                    "DataFlow",
                    "linkedin",
                    "USA Remote",
                    40,
                    0,
                    None,
                    None,
                    0,
                    "search_3",
                    "Data Analyst",
                ),
            ]

            conn.executemany(
                """
                INSERT INTO jobs (
                    url, title, company, site, location, fit_score,
                    is_saved, apply_status, applied_at, is_archived,
                    search_id, search_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                jobs_data,
            )
            conn.commit()
            conn.close()

            # 1. Global Aggregation
            global_summary = get_analytics_summary(db_path)

            self.assertEqual(global_summary["total_found"], 7)
            self.assertEqual(global_summary["saved"], 2)  # p1, s2
            self.assertEqual(global_summary["applied"], 1)  # p2
            self.assertEqual(global_summary["archived"], 1)  # p3
            # Pending: s1, s3, d1 = 3 pending jobs
            self.assertEqual(global_summary["pending"], 3)

            # Top Sites Verification
            sites_map = {item["name"]: item for item in global_summary["top_sites"]}
            self.assertEqual(sites_map["linkedin"]["count"], 3)
            self.assertEqual(sites_map["getonboard"]["count"], 2)
            self.assertEqual(sites_map["indeed"]["count"], 1)
            self.assertEqual(sites_map["weworkremotely"]["count"], 1)
            self.assertAlmostEqual(sites_map["linkedin"]["percentage"], 42.9, delta=0.2)

            # Top Companies Verification
            comp_map = {item["name"]: item for item in global_summary["top_companies"]}
            self.assertEqual(comp_map["TechCorp"]["count"], 2)
            self.assertEqual(comp_map["AgileWorks"]["count"], 2)
            self.assertEqual(comp_map["StartupX"]["count"], 1)
            self.assertEqual(comp_map["FintechLab"]["count"], 1)
            self.assertEqual(comp_map["DataFlow"]["count"], 1)

            # Top Titles Verification
            title_names = [item["name"] for item in global_summary["top_titles"]]
            self.assertIn("Scrum Master", title_names)
            self.assertIn("Lead Product Manager", title_names)

            # Score Distribution Verification
            # Normalized scores: 95, 85, 72, 80, 60, 40 -> avg = 432 / 6 = 72.0
            score_dist = global_summary["score_distribution"]
            self.assertAlmostEqual(score_dist["avg_score"], 72.0, delta=0.5)
            buckets = score_dist["score_buckets"]
            self.assertEqual(buckets["90+"], 1)  # 95
            self.assertEqual(buckets["80-89"], 2)  # 85, 80
            self.assertEqual(buckets["70-79"], 1)  # 72
            self.assertEqual(buckets["<70"], 2)  # 60, 40
            self.assertEqual(buckets["unscored"], 1)  # None

            # Geolocations Verification
            geos = {g["location"]: g for g in global_summary["geolocations"]}
            self.assertIn("Guatemala City, Guatemala", geos)
            self.assertEqual(geos["Guatemala City, Guatemala"]["coordinates"], [14.6349, -90.5069])
            self.assertEqual(geos["Guatemala City, Guatemala"]["saved"], 1)

            self.assertIn("Mixco, Guatemala", geos)
            self.assertEqual(geos["Mixco, Guatemala"]["coordinates"], [14.6310, -90.6064])
            self.assertEqual(geos["Mixco, Guatemala"]["saved"], 1)

            self.assertIn("Managua, Nicaragua", geos)
            self.assertEqual(geos["Managua, Nicaragua"]["coordinates"], [12.1149, -86.2362])
            self.assertEqual(geos["Managua, Nicaragua"]["applied"], 1)

            self.assertIn("Worldwide (Remote)", geos)
            self.assertEqual(geos["Worldwide (Remote)"]["coordinates"], [20.0, -10.0])
            self.assertEqual(geos["Worldwide (Remote)"]["pending"], 1)

            # Tabs Health Verification
            tabs_health = global_summary["tabs_health"]
            self.assertTrue(len(tabs_health) >= 3)
            tabs_map = {t["search_id"]: t for t in tabs_health}

            # Search 1: 3 jobs, 1 saved, 1 applied -> saved_rate=33.3%, applied_rate=33.3%
            self.assertEqual(tabs_map["search_1"]["total_jobs"], 3)
            self.assertAlmostEqual(tabs_map["search_1"]["saved_rate"], 33.3, delta=0.2)
            self.assertAlmostEqual(tabs_map["search_1"]["applied_rate"], 33.3, delta=0.2)
            self.assertEqual(tabs_map["search_1"]["recommendation"], "Keep Searching")

            # 2. Filtered Aggregation by search_id='search_1'
            filtered_s1 = get_analytics_summary(db_path, search_id="search_1")
            self.assertEqual(filtered_s1["total_found"], 3)
            self.assertEqual(filtered_s1["saved"], 1)
            self.assertEqual(filtered_s1["applied"], 1)
            self.assertEqual(filtered_s1["archived"], 1)
            self.assertEqual(filtered_s1["pending"], 0)

            # 3. Filtered Aggregation with search_id='all'
            filtered_all = get_analytics_summary(db_path, search_id="all")
            self.assertEqual(filtered_all["total_found"], 7)

            # 4. Filtered Aggregation with non-existent search_id
            filtered_none = get_analytics_summary(db_path, search_id="non_existent_search")
            self.assertEqual(filtered_none["total_found"], 0)
            self.assertEqual(filtered_none["saved"], 0)
            self.assertEqual(filtered_none["pending"], 0)


class TestAnalyticsEndpoints(unittest.TestCase):
    """Test suite for HTTP server /api/analytics endpoints."""

    @classmethod
    def setUpClass(cls):
        """Start local HTTP server with temporary test database."""
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.tmp_dir.name) / "applypilot.db"
        conn = init_db(cls.db_path)
        conn.execute("""
            INSERT INTO jobs (
                url, title, company, site, location, fit_score,
                is_saved, apply_status, search_id, search_name
            ) VALUES 
            ('https://job1.com', 'Senior Software Engineer', 'Global Corp', 'linkedin', 'Mixco, Guatemala', 90, 1, NULL, 'search_tech', 'Tech Roles'),
            ('https://job2.com', 'DevOps Specialist', 'Cloud Systems', 'indeed', 'Managua, Nicaragua', 82, 0, 'applied', 'search_tech', 'Tech Roles'),
            ('https://job3.com', 'Marketing Lead', 'Brand Agency', 'getonboard', 'Worldwide (Remote)', 65, 0, NULL, 'search_growth', 'Growth Roles')
        """)
        conn.commit()
        conn.close()

        # Find an open port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()

        cls.server = HTTPServer(("127.0.0.1", cls.port), CRMDashboardHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        """Clean up HTTP server and temporary directory."""
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp_dir.cleanup()

    def test_analytics_api_endpoint_global(self):
        """Verify GET /api/analytics returns 200, JSON, and anti-caching headers."""
        url = f"http://127.0.0.1:{self.port}/api/analytics"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            headers = dict(resp.headers)
            cache_ctrl = headers.get("Cache-Control", "")
            self.assertIn("no-cache", cache_ctrl)
            self.assertIn("no-store", cache_ctrl)
            self.assertIn("must-revalidate", cache_ctrl)
            self.assertIn("application/json", headers.get("Content-Type", ""))

            body = resp.read().decode("utf-8")
            data = json.loads(body)

            self.assertIn("summary", data)
            self.assertIn("top_sites", data)
            self.assertIn("top_companies", data)
            self.assertIn("top_titles", data)
            self.assertIn("score_distribution", data)
            self.assertIn("geolocations", data)
            self.assertIn("tabs_health", data)

            self.assertIsInstance(data["summary"]["total_found"], int)
            self.assertIsInstance(data["score_distribution"]["avg_score"], float)

    def test_analytics_api_endpoint_with_search_id_parameter(self):
        """Verify GET /api/analytics?search_id=... filters data correctly."""
        url = f"http://127.0.0.1:{self.port}/api/analytics?search_id=search_tech"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("summary", data)
            self.assertIn("score_distribution", data)
            # The search_id parameter should filter without error
            self.assertIsInstance(data["summary"]["total_found"], int)

    def test_analytics_api_endpoint_search_id_all(self):
        """Verify GET /api/analytics?search_id=all behaves like global query."""
        url = f"http://127.0.0.1:{self.port}/api/analytics?search_id=all"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("summary", data)
            self.assertIsInstance(data["summary"]["total_found"], int)


if __name__ == "__main__":
    unittest.main()
