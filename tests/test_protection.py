"""USGS PAD-US protection context tests."""
import unittest
from unittest.mock import patch

import requests

from wildlocate.core import protection


class ProtectionContextTests(unittest.TestCase):
    def points(self):
        return [
            {"latitude": 42.1, "longitude": -72.1, "status": "ok", "percentile": 90, "category": "Very High"},
            {"latitude": 42.2, "longitude": -72.2, "status": "ok", "percentile": 70, "category": "High"},
            {"latitude": 42.3, "longitude": -72.3, "status": "ok", "percentile": 40, "category": "Moderate"},
            {"latitude": 42.4, "longitude": -72.4, "status": "unavailable"},
        ]

    def test_only_high_suitability_points_are_checked(self):
        def query(point, timeout=8):
            if point["percentile"] == 90:
                return [{
                    "name": "Example Refuge",
                    "manager": "FWS",
                    "designation": "National Wildlife Refuge",
                    "category": "Fee",
                    "gap_status": "1",
                    "public_access": "OA",
                }]
            return []

        with patch.object(protection, "_query_point", side_effect=query) as mocked:
            result = protection.analyze_protection_context(self.points(), max_workers=2)

        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["high_suitability_samples"], 2)
        self.assertEqual(result["checked_samples"], 2)
        self.assertEqual(result["intersecting_padus"], 1)
        self.assertEqual(result["biodiversity_managed"], 1)
        self.assertEqual(result["not_intersecting_padus"], 1)
        self.assertTrue(result["samples"][0]["within_padus"])

    def test_network_failure_is_nonfatal_and_explicit(self):
        with patch.object(
            protection,
            "_query_point",
            side_effect=requests.RequestException("offline"),
        ):
            result = protection.analyze_protection_context(self.points(), max_workers=2)

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["checked_samples"], 0)
        self.assertEqual(result["failed_queries"], 2)
        self.assertIn("could not be retrieved", result["message"])

    def test_gap_status_one_or_two_counts_as_biodiversity_managed(self):
        self.assertTrue(protection._biodiversity_managed([{"gap_status": "1"}]))
        self.assertTrue(protection._biodiversity_managed([{"gap_status": "2 - managed for biodiversity"}]))
        self.assertFalse(protection._biodiversity_managed([{"gap_status": "3"}]))


if __name__ == "__main__":
    unittest.main()
