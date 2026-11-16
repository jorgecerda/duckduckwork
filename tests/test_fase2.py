"""Unit tests for Fase 2: Task #21 (Fact Verification Gate) and Task #25 (Pre-Fill & Tailoring)."""

import unittest

from applypilot.scoring.fact_checker import extract_numbers_and_metrics, verify_facts


class TestFactVerificationGate(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "personal": {
                "full_name": "Alex Morgan",
                "preferred_name": "Alex",
                "email": "alex.morgan@example.com",
                "phone": "+1 555-0199",
                "country": "United States",
            },
            "resume_facts": {
                "preserved_companies": ["Publicis Groupe", "Millicom (Tigo)"],
                "preserved_school": "Keiser University",
                "real_metrics": ["15+ years of experience", "40% performance boost"],
            },
            "skills_boundary": {
                "languages": ["Python", "JavaScript", "SQL"],
                "frameworks": ["FastAPI", "React"],
            },
            "experience": {
                "education_level": "B.S. in Computer Science",
            },
        }

    def test_extract_numbers_and_metrics(self):
        text = "Reduced latency by 45% in 2 weeks, leading to $200k in savings and 3x scale."
        metrics = extract_numbers_and_metrics(text)
        self.assertIn("45%", metrics)
        self.assertIn("$200k", metrics)
        self.assertIn("3x", metrics)

    def test_valid_resume_passes_gate(self):
        text = """
Alex Morgan
SUMMARY
Senior engineering leader with experience at Publicis Groupe and Millicom (Tigo).
EXPERIENCE
Publicis Groupe - Lead Engineer
- Optimized data pipelines achieving 40% performance boost.
Millicom (Tigo) - Product Lead
- Delivered telecom web platforms with Python and FastAPI.
EDUCATION
Keiser University | B.S. in Computer Science
"""
        report = verify_facts(text, self.profile, doc_type="resume")
        self.assertTrue(report["verified"])
        self.assertEqual(report["confidence_level"], "HIGH")
        self.assertEqual(len(report["errors"]), 0)
        self.assertIn("Publicis Groupe", report["verified_entities"]["companies"])
        self.assertIn("Keiser University", report["verified_entities"]["schools"])

    def test_fabricated_employer_caught(self):
        text = """
Alex Morgan
SUMMARY
Engineer at Google and Apple.
EXPERIENCE
Google - VP Engineering
EDUCATION
Keiser University | B.S. in Computer Science
"""
        report = verify_facts(text, self.profile, doc_type="resume")
        self.assertFalse(report["verified"])
        # Preserved real companies were omitted
        self.assertTrue(any("missing from resume" in err for err in report["errors"]))

    def test_fabricated_degree_caught(self):
        text = """
Alex Morgan
SUMMARY
Engineer with Publicis Groupe and Millicom (Tigo).
EDUCATION
Keiser University | Ph.D in Artificial Intelligence
"""
        report = verify_facts(text, self.profile, doc_type="resume")
        self.assertFalse(report["verified"])
        self.assertTrue(any("Unearned degree" in err for err in report["errors"]))

    def test_fabricated_skills_caught(self):
        text = """
Alex Morgan
SUMMARY
Engineer with Publicis Groupe and Millicom (Tigo).
EXPERIENCE
Publicis Groupe
- Built systems using Rust and Django backend.
EDUCATION
Keiser University | B.S. in Computer Science
"""
        report = verify_facts(text, self.profile, doc_type="resume")
        self.assertFalse(report["verified"])
        self.assertTrue(any("Fabricated tool/skill detected: 'rust'" in err for err in report["errors"]))
        self.assertTrue(any("Fabricated tool/skill detected: 'django'" in err for err in report["errors"]))

    def test_cover_letter_signoff_verification(self):
        text = """Dear Hiring Manager,
I am excited to share my work at Publicis Groupe.
Happy to walk through any of this in detail.
Alex Morgan"""
        report = verify_facts(text, self.profile, doc_type="cover_letter")
        self.assertTrue(report["verified"])
        self.assertEqual(report["verified_entities"]["candidate_name"], "Alex Morgan")

    def test_missing_cover_letter_signoff(self):
        text = """Dear Hiring Manager,
Here is my letter.
Best regards,
Anonymous Developer"""
        report = verify_facts(text, self.profile, doc_type="cover_letter")
        self.assertFalse(report["verified"])
        self.assertTrue(any("Sign-off name" in err for err in report["errors"]))


if __name__ == "__main__":
    unittest.main()
