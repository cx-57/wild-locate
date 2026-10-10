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

    def test_partial_samples_report_coverage_distribution_and_ranked_locations(self):
        points = []
        for percentile, latitude, longitude in (
            (90, 42, -72), (70, 42.02, -72),
            (50, 42, -71.98), (10, 41.98, -72),
        ):
            point = self.point(percentile, .1, -.03)
            point.update(latitude=latitude, longitude=longitude)
            points.append(point)
        points.append({"latitude": 42, "longitude": -72.02, "status": "unavailable"})

        summary = deep_dive.summarize_deep_dive(points, 42, -72)
        overview = summary["overview"]
        self.assertEqual(overview["total_points"], 5)
        self.assertEqual(overview["unavailable_points"], 1)
        self.assertEqual(overview["coverage_percentage"], 80)
        self.assertEqual(overview["median_percentile"], 60)
        self.assertEqual(overview["min_percentile"], 10)
        self.assertEqual(overview["max_percentile"], 90)
        self.assertEqual(overview["high_suitability_percentage"], 50)
        self.assertEqual(summary["distribution"], [
            {"category": "Very Low", "count": 1, "percentage": 25.0},
            {"category": "Low", "count": 0, "percentage": 0.0},
            {"category": "Moderate", "count": 1, "percentage": 25.0},
            {"category": "High", "count": 1, "percentage": 25.0},
            {"category": "Very High", "count": 1, "percentage": 25.0},
        ])
        self.assertEqual(summary["sectors"], [
            {"name": "North", "mean_percentile": 70.0, "points": 1},
            {"name": "East", "mean_percentile": 50.0, "points": 1},
            {"name": "South", "mean_percentile": 10.0, "points": 1},
        ])
        self.assertIsNone(overview["strongest_sector"])
        priorities = summary["survey_priorities"]
        self.assertEqual([p["rank"] for p in priorities], [1, 2, 3])
        self.assertEqual([p["percentile"] for p in priorities], [90, 70, 50])
        self.assertEqual([p["sector"] for p in priorities], ["Center", "North", "East"])
        self.assertEqual(priorities[0]["distance_km"], 0)
        self.assertAlmostEqual(priorities[1]["distance_km"], 2.222, places=2)
        self.assertTrue(summary["warnings"])
        json.dumps(summary, allow_nan=False)

    def test_survey_ranking_is_deterministic_for_tied_scores(self):
        points = [self.point(80, .1, 0) for _ in range(4)]
        for point, latitude, longitude in zip(
            points, (42.03, 42.01, 42.01, 42.02), (-72, -71.99, -72.01, -72)
        ):
            point.update(latitude=latitude, longitude=longitude)
        summary = deep_dive.summarize_deep_dive(points, 42, -72)
        priorities = summary["survey_priorities"]
        self.assertEqual([(p["latitude"], p["longitude"]) for p in priorities], [
            (42.01, -72.01), (42.01, -71.99), (42.02, -72),
        ])

    def test_signal_coverage_excludes_failed_explanations_but_keeps_neutral_samples(self):
        explained = self.point(80, .1, -.04)
        explained["influences"][0].update(current=.8, reference=.5)
        neutral = self.point(50, 0, 0)
        neutral["influences"] = []
        failed = self.point(20, 0, 0)
        failed.update(insight_status="unavailable", influences=[])
        summary = deep_dive.summarize_deep_dive([explained, neutral, failed], 42, -72)

        habitat = summary["habitat"]
        self.assertEqual(habitat["status"], "partial")
        self.assertEqual(habitat["explanation_points"], 2)
        self.assertEqual(habitat["failed_explanations"], 1)
        forest = habitat["strengths"][0]
        self.assertAlmostEqual(forest["mean_current"], .8)
        self.assertAlmostEqual(forest["mean_reference"], .5)
        self.assertEqual(forest["affected_percentage"], 100)
        pressure = summary["pressures"][0]
        self.assertEqual(pressure["points_evaluated"], 2)
        self.assertEqual(pressure["affected_percentage"], 50)

    def test_neutral_explanations_are_available_without_signal_rows(self):
        point = self.point(50, 0, 0)
        point["influences"] = []
        summary = deep_dive.summarize_deep_dive([point], 42, -72)
        self.assertEqual(summary["habitat"]["status"], "available")
        self.assertEqual(summary["habitat"]["explanation_points"], 1)
        self.assertEqual(summary["habitat"]["failed_explanations"], 0)
        self.assertEqual(summary["habitat"]["strengths"], [])
        self.assertEqual(summary["habitat"]["constraints"], [])
        self.assertEqual(summary["pressures"], [])

    def test_numerical_noise_does_not_become_a_pressure_signal(self):
        summary = deep_dive.summarize_deep_dive([
            self.point(50, -1e-12, 1e-12),
        ], 42, -72)
        self.assertEqual(summary["habitat"]["strengths"], [])
        self.assertEqual(summary["habitat"]["constraints"], [])
        self.assertEqual(summary["pressures"], [])

    def test_unavailable_samples_have_null_statistics_and_no_survey_priorities(self):
        for points in ([], [{"latitude": 42, "longitude": -72, "status": "unavailable"}]):
            with self.subTest(points=points):
                summary = deep_dive.summarize_deep_dive(points, 42, -72)
                overview = summary["overview"]
                self.assertEqual(overview["total_points"], len(points))
                self.assertEqual(overview["unavailable_points"], len(points))
                self.assertEqual(overview["coverage_percentage"], 0)
                for statistic in ("mean_percentile", "median_percentile", "min_percentile", "max_percentile"):
                    self.assertIsNone(overview[statistic])
                self.assertEqual(summary["habitat"]["status"], "unavailable")
                self.assertEqual(summary["sectors"], [])
                self.assertEqual(summary["survey_priorities"], [])
                self.assertTrue(summary["warnings"])
                self.assertTrue(all(row["count"] == 0 and row["percentage"] == 0
                                    for row in summary["distribution"]))
                json.dumps(summary, allow_nan=False)


class DeepDiveAnalysisTests(unittest.TestCase):
    def analyze_points(self, extractor=None, insight_results=None, places=None):
        model = HabitatModel()
        comparison = pd.DataFrame({
            "forest_fraction_250m": [.2, .5, .8],
            "mean_impervious_250m": [60., 30., 5.],
        })
        metrics = {
            "predictor_names": list(comparison.columns),
            "selected_model": "RandomForest",
            "presence_count": 25,
            "background_count": 75,
            "number_of_folds": 4,
            "metrics_by_model": [{"model": "RandomForest", "mean_pr_auc": .6}],
        }
        points = [{"latitude": 42 + index * .01, "longitude": -72} for index in range(3)]
        with patch.object(deep_dive, "analyze_places", side_effect=places, return_value={"status": "unavailable", "sites": []}), \
             patch.object(deep_dive, "build_grid", return_value=points), \
             patch.object(deep_dive, "resolve_model", return_value=SimpleNamespace(species="Bobcat")), \
             patch.object(deep_dive, "load_model_and_metadata", return_value=(model, metrics)), \
             patch.object(deep_dive, "load_comparison_scores", return_value=(model.predict_proba(comparison)[:, 1], comparison)), \
             patch.object(deep_dive, "extract_features", side_effect=extractor or (
                 lambda _lat, _lon: {"forest_fraction_250m": .6, "mean_impervious_250m": 20.}
             )):
            if insight_results is not None:
                with patch.object(deep_dive, "habitat_insights", side_effect=insight_results):
                    return deep_dive.analyze_deep_dive("Bobcat", 42, -72, 10)
            return deep_dive.analyze_deep_dive("Bobcat", 42, -72, 10)

    def test_specific_places_reuse_the_selected_model_at_actual_coordinates(self):
        calls = []

        def extract(lat, lon):
            calls.append((lat, lon))
            return {"forest_fraction_250m": .6, "mean_impervious_250m": 20.}

        def places(points, lat, lon, radius, region, evaluate):
            assessed = evaluate(42.005, -72.003)
            self.assertAlmostEqual(assessed["score"], .48)
            self.assertEqual(assessed["insight_status"], "available")
            self.assertEqual(len(assessed["influences"]), 2)
            self.assertTrue(all("current" in row and "reference" in row for row in assessed["influences"]))
            return {"status": "available", "sites": [{"name": "Actual road"}]}

        report = self.analyze_points(extractor=extract, places=places)
        self.assertIn((42.005, -72.003), calls)
        self.assertEqual(report["places"]["sites"][0]["name"], "Actual road")
        self.assertEqual(report["overview"]["evaluated_points"], 3)

    def test_report_preserves_scoring_when_some_explanations_fail(self):
        with self.assertLogs("wildlocate.core.deep_dive", level="WARNING"):
            report = self.analyze_points(insight_results=[
                {"influences": []}, ValueError("explanation failed"), {"influences": []},
            ])
        self.assertEqual(report["overview"]["evaluated_points"], 3)
        self.assertEqual(report["habitat"]["status"], "partial")
        self.assertEqual(report["habitat"]["explanation_points"], 2)
        self.assertEqual(report["habitat"]["failed_explanations"], 1)
        self.assertEqual([p["insight_status"] for p in report["points"]], [
            "available", "unavailable", "available",
        ])
        self.assertEqual(report["evidence"]["presence_count"], 25)
        self.assertEqual(report["evidence"]["pr_reference"], .25)
        self.assertEqual(report["evidence"]["pr_auc"], .6)
        self.assertEqual(report["grid_spacing_km"], 2)
        self.assertTrue(report["warnings"])
        json.dumps(report, allow_nan=False)

    def test_all_explanations_failed_is_explicit(self):
        with self.assertLogs("wildlocate.core.deep_dive", level="WARNING"):
            report = self.analyze_points(insight_results=ValueError("explanation failed"))
        self.assertEqual(report["habitat"]["status"], "unavailable")
        self.assertEqual(report["habitat"]["explanation_points"], 0)
        self.assertEqual(report["habitat"]["failed_explanations"], 3)
        self.assertEqual(report["pressures"], [])

    def test_partial_landscape_keeps_unavailable_points_out_of_percentages(self):
        def extract(latitude, _longitude):
            if latitude > 42.01:
                raise ValueError("Requested coordinate cannot be evaluated: outside coverage")
            return {"forest_fraction_250m": .6, "mean_impervious_250m": 20.}

        report = self.analyze_points(extractor=extract)
        self.assertEqual(report["sample_points"], 3)
        self.assertEqual(report["unavailable_points"], 1)
        self.assertEqual(report["overview"]["evaluated_points"], 2)
        self.assertAlmostEqual(report["overview"]["coverage_percentage"], 200 / 3)
        self.assertEqual(sum(row["count"] for row in report["distribution"]), 2)
        self.assertAlmostEqual(sum(row["percentage"] for row in report["distribution"]), 100)
        self.assertEqual(len(report["survey_priorities"]), 2)
        self.assertEqual(report["habitat"]["status"], "available")
        json.dumps(report, allow_nan=False)

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

        with patch.object(deep_dive, "analyze_places", return_value={"status": "unavailable", "sites": []}), \
             patch.object(deep_dive, "build_grid", return_value=sample_points), \
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
