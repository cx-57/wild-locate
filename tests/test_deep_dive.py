"""Conservation Deep Dive analysis tests."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from wildlocate.core import deep_dive


class HabitatModel:
    def predict_proba(self, frame):
        score = (
            0.15
            + 0.65 * frame["forest_fraction_250m"]
            - 0.003 * frame["mean_impervious_250m"]
        )
        score = np.clip(score.to_numpy(dtype=float), 0.01, 0.99)
        return np.column_stack([1 - score, score])


class DeepDiveSummaryTests(unittest.TestCase):
    def point(self, percentile, forest, impervious, forest_effect, impervious_effect):
        return {
            "latitude": 42 + percentile / 10000,
            "longitude": -72,
            "status": "ok",
            "score": percentile / 100,
            "percentile": percentile,
            "category": "Very High" if percentile >= 80 else "High" if percentile >= 60 else "Moderate",
            "features": {
                "forest_fraction_250m": forest,
                "mean_impervious_250m": impervious,
            },
            "influences": [
                {
                    "feature": "forest_fraction_250m",
                    "current": forest,
                    "reference": .4,
                    "effect": forest_effect,
                },
                {
                    "feature": "mean_impervious_250m",
                    "current": impervious,
                    "reference": 20,
                    "effect": impervious_effect,
                },
            ],
            "scenarios": [
                {
                    "title": "Reduce impervious surface by 50%",
                    "score": min(.99, percentile / 100 + .1),
                    "delta": .1,
                    "percentile": min(100, percentile + 10),
                    "changes": [],
                }
            ],
        }

    def test_summary_separates_strengths_pressures_and_scenarios(self):
        points = [
            self.point(90, .8, 5, .12, .02),
            self.point(80, .7, 10, .10, .01),
            self.point(60, .5, 25, .04, -.03),
            self.point(40, .3, 45, -.04, -.08),
            self.point(20, .2, 60, -.08, -.12),
        ]
        summary = deep_dive.summarize_deep_dive(
            points,
            ["forest_fraction_250m", "mean_impervious_250m"],
            42,
            -72,
        )
        self.assertEqual(summary["overview"]["evaluated_points"], 5)
        self.assertEqual(summary["overview"]["strongest_point"]["percentile"], 90)
        self.assertEqual(summary["habitat"]["strengths"][0]["feature"], "forest_fraction_250m")
        self.assertEqual(summary["habitat"]["constraints"][0]["feature"], "mean_impervious_250m")
        self.assertEqual(summary["pressures"][0]["domain"], "Development")
        self.assertEqual(summary["scenarios"][0]["percentile_delta"], 10)
        self.assertTrue(summary["habitat"]["contrasts"])
        json.dumps(summary, allow_nan=False)

    def test_empty_summary_is_explicit(self):
        summary = deep_dive.summarize_deep_dive(
            [{"latitude": 42, "longitude": -72, "status": "unavailable"}],
            ["forest_fraction_250m"],
            42,
            -72,
        )
        self.assertEqual(summary["overview"]["evaluated_points"], 0)
        self.assertIsNone(summary["overview"]["mean_percentile"])
        self.assertEqual(summary["pressures"], [])
        self.assertEqual(summary["scenarios"], [])


class DeepDiveAnalysisTests(unittest.TestCase):
    def test_analysis_runs_separately_from_normal_regional_result(self):
        model = HabitatModel()
        comparison = pd.DataFrame({
            "forest_fraction_250m": [.1, .3, .5, .7, .9],
            "developed_fraction_250m": [.8, .6, .4, .2, .05],
            "mean_impervious_250m": [70., 50., 30., 10., 2.],
        })
        scores = model.predict_proba(comparison[[
            "forest_fraction_250m", "developed_fraction_250m", "mean_impervious_250m"
        ]])[:, 1]
        sample_points = [
            {"latitude": 42.00, "longitude": -72.00},
            {"latitude": 42.01, "longitude": -72.00},
            {"latitude": 42.02, "longitude": -72.00},
            {"latitude": 42.03, "longitude": -72.00},
        ]

        def extract(lat, _lon):
            offset = round((lat - 42) * 100)
            forest = [.2, .4, .6, .8][offset]
            return {
                "forest_fraction_250m": forest,
                "developed_fraction_250m": 1 - forest,
                "mean_impervious_250m": [60., 40., 20., 5.][offset],
            }

        with patch.object(deep_dive, "build_grid", return_value=sample_points), \
             patch.object(deep_dive, "resolve_model", return_value=SimpleNamespace(species="Bobcat")), \
             patch.object(deep_dive, "load_model_and_metadata", return_value=(model, {
                 "predictor_names": list(comparison.columns),
                 "selected_model": "RandomForest",
                 "presence_count": 405,
             })), \
             patch.object(deep_dive, "load_comparison_scores", return_value=(scores, comparison)), \
             patch.object(deep_dive, "extract_features", side_effect=extract), \
             patch.object(deep_dive, "analyze_protection_context", return_value={
                 "status": "available",
                 "source": "USGS PAD-US 4.1",
                 "high_suitability_samples": 2,
                 "checked_samples": 2,
                 "failed_queries": 0,
                 "intersecting_padus": 1,
                 "biodiversity_managed": 1,
                 "not_intersecting_padus": 1,
                 "samples": [],
             }):
            report = deep_dive.analyze_deep_dive("Bobcat", 42, -72, 10)

        self.assertEqual(report["analysis_type"], "deep_dive")
        self.assertEqual(report["species"], "Bobcat")
        self.assertEqual(report["sample_points"], 4)
        self.assertEqual(report["overview"]["evaluated_points"], 4)
        self.assertIn("USGS PAD-US 4.1 protected-area context", report["data_scope"]["connected"])
        self.assertIn("historical land-cover change", report["data_scope"]["not_connected_yet"])
        self.assertEqual(report["protection"]["intersecting_padus"], 1)
        self.assertTrue(report["habitat"]["strengths"])
        self.assertTrue(report["pressures"])
        self.assertEqual(len(report["points"]), 4)
        json.dumps(report, allow_nan=False)

    def test_question_answers_stay_within_report(self):
        report = {
            "overview": {
                "evaluated_points": 10,
                "mean_percentile": 63,
                "strongest_point": {
                    "latitude": 42.1, "longitude": -72.2, "percentile": 94
                },
                "strongest_sector": {
                    "name": "Northwest", "mean_percentile": 78, "points": 4
                },
            },
            "habitat": {
                "strengths": [{"feature": "forest_fraction_1000m"}],
                "constraints": [],
            },
            "pressures": [{
                "domain": "Development",
                "affected_points": 6,
            }],
            "scenarios": [{
                "latitude": 42.1,
                "longitude": -72.2,
                "current_percentile": 55,
                "projected_percentile": 72,
                "description": "Reduce impervious surface by 50%",
            }],
            "protection": {
                "status": "available",
                "checked_samples": 6,
                "intersecting_padus": 4,
                "biodiversity_managed": 2,
            },
        }
        self.assertIn("development", deep_dive.answer_deep_dive_question(report, "What is the biggest threat?").lower())
        self.assertIn("forest fraction 1000m", deep_dive.answer_deep_dive_question(report, "What helps habitat?").lower())
        self.assertIn("55th to 72th", deep_dive.answer_deep_dive_question(report, "Best restoration scenario?"))
        self.assertIn("94th percentile", deep_dive.answer_deep_dive_question(report, "Where is the strongest habitat?"))
        self.assertIn("4 intersect", deep_dive.answer_deep_dive_question(report, "How much strong habitat is protected?"))


if __name__ == "__main__":
    unittest.main()
