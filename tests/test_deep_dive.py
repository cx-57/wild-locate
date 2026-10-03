"""Deep Dive analysis tests."""
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
    def point(self, percentile, forest_effect, impervious_effect):
        return {
            "latitude": 42 + percentile / 10000,
            "longitude": -72,
            "status": "ok",
            "score": percentile / 100,
            "percentile": percentile,
            "category": "High",
            "influences": [
                {"feature": "forest_fraction_250m", "effect": forest_effect},
                {"feature": "mean_impervious_250m", "effect": impervious_effect},
            ],
        }

    def test_summary_keeps_only_core_results(self):
        points = [
            self.point(90, .12, .02),
            self.point(80, .10, .01),
            self.point(60, .04, -.03),
            self.point(40, -.04, -.08),
            self.point(20, -.08, -.12),
        ]
        summary = deep_dive.summarize_deep_dive(points, 42, -72)
        self.assertEqual(summary["overview"]["evaluated_points"], 5)
        self.assertEqual(summary["overview"]["strongest_point"]["percentile"], 90)
        self.assertEqual(summary["habitat"]["strengths"][0]["feature"], "forest_fraction_250m")
        self.assertEqual(summary["habitat"]["constraints"][0]["feature"], "mean_impervious_250m")
        self.assertEqual(summary["pressures"][0]["domain"], "Development")
        self.assertNotIn("scenarios", summary)
        self.assertNotIn("contrasts", summary["habitat"])
        json.dumps(summary, allow_nan=False)

    def test_empty_summary_is_explicit(self):
        summary = deep_dive.summarize_deep_dive(
            [{"latitude": 42, "longitude": -72, "status": "unavailable"}],
            42,
            -72,
        )
        self.assertEqual(summary["overview"]["evaluated_points"], 0)
        self.assertIsNone(summary["overview"]["mean_percentile"])
        self.assertEqual(summary["pressures"], [])


class DeepDiveAnalysisTests(unittest.TestCase):
    def test_analysis_runs_separately_from_normal_result(self):
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
             patch.object(deep_dive, "extract_features", side_effect=extract):
            report = deep_dive.analyze_deep_dive("Bobcat", 42, -72, 10)

        self.assertEqual(report["analysis_type"], "deep_dive")
        self.assertEqual(report["species"], "Bobcat")
        self.assertEqual(report["sample_points"], 4)
        self.assertEqual(report["overview"]["evaluated_points"], 4)
        self.assertIn("strengths", report["habitat"])
        self.assertTrue(report["pressures"])
        self.assertNotIn("protection", report)
        self.assertNotIn("scenarios", report)
        self.assertNotIn("data_scope", report)
        self.assertEqual(len(report["points"]), 4)
        json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
