"""Specific-place priorities must remain local model comparisons, not damage claims."""
import json
import unittest
from unittest.mock import patch

from wildlocate.core import place_analysis


def influence(feature, effect, current=40., reference=20.):
    return {"feature": feature, "effect": effect,
            "current": current, "reference": reference}


def point(latitude=42., longitude=-72., influences=None, percentile=50):
    return {"latitude": latitude, "longitude": longitude, "status": "ok",
            "score": .5, "percentile": percentile, "category": "Moderate",
            "insight_status": "available", "influences": influences or []}


def feature(identifier, kind, latitude=42., longitude=-72.):
    return {"id": identifier, "kind": kind, "name": identifier,
            "latitude": latitude, "longitude": longitude,
            "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
            "source": "OpenStreetMap", "source_url": "https://www.openstreetmap.org/way/1",
            "tags": {}}


class PlaceAnalysisTests(unittest.TestCase):
    def analyze(self, features, evaluate, points=None, mapping_status="available"):
        mapping = {"status": mapping_status, "features": features,
                   "sources": [{"name": "OpenStreetMap", "url": "https://www.openstreetmap.org/copyright"}],
                   "warnings": [], "retrieved_at": "2026-10-10T00:00:00Z"}
        with patch.object(place_analysis, "discover_places", return_value=mapping):
            return place_analysis.analyze_places(points or [point()], 42, -72, 10, "MA", evaluate)

    def test_rank_uses_strongest_effect_without_summing_correlated_conditions(self):
        features = [feature("one", "building"), feature("two", "road", 42.01)]

        def evaluate(latitude, longitude):
            signals = ([influence("developed_fraction_250m", -.06, .6, .3),
                        influence("mean_impervious_250m", -.05)] if latitude == 42
                       else [influence("distance_to_road_m", -.08, 10, 300)])
            return point(latitude, longitude, signals)

        report = self.analyze(features, evaluate)
        self.assertEqual([site["id"] for site in report["sites"]], ["two", "one"])
        self.assertAlmostEqual(report["sites"][0]["strongest_signal_points"], 8)
        self.assertAlmostEqual(report["sites"][1]["strongest_signal_points"], 6)
        self.assertIn("surrounding", report["limitations"].lower())
        json.dumps(report, allow_nan=False)

    def test_unrelated_negative_features_cannot_indict_roads_or_buildings(self):
        report = self.analyze([feature("road", "road"), feature("building", "building", 42.01)],
            lambda lat, lon: point(lat, lon, [influence("forest_fraction_250m", -.2),
                influence("mean_slope_250m", -.1), influence("distance_to_road_m", .05)]))
        self.assertEqual(report["status"], "available")
        self.assertEqual(len(report["sites"]), 2)
        self.assertTrue(all(not site["signals"] for site in report["sites"]))
        self.assertEqual(report["assessed_features"], 2)

    def test_neutral_and_rounding_noise_do_not_create_a_priority(self):
        report = self.analyze([feature("building", "building")],
            lambda lat, lon: point(lat, lon, [influence("mean_impervious_250m", -1e-9)]))
        self.assertEqual(report["status"], "available")
        self.assertEqual(len(report["sites"]), 1)
        self.assertEqual(report["sites"][0]["signals"], [])

    def test_partial_evaluation_failure_keeps_good_sites_and_honest_counts(self):
        def evaluate(lat, lon):
            if lat > 42:
                raise ValueError("Outside model coverage")
            return point(lat, lon, [influence("mean_impervious_250m", -.05)])

        report = self.analyze([feature("good", "building"), feature("bad", "building", 42.01)], evaluate)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["assessed_features"], 1)
        self.assertEqual(report["failed_evaluations"], 1)
        self.assertEqual(len(report["sites"]), 2)
        self.assertEqual({site["assessment_status"] for site in report["sites"]}, {"available", "unavailable"})

    def test_nonfinite_scores_and_explanations_are_not_valid_assessments(self):
        for result in (dict(point(), score=float("nan")),
                       dict(point(), insight_status="unavailable"),
                       point(influences=[influence("mean_impervious_250m", float("nan"))])):
            with self.subTest(result=result):
                report = self.analyze([feature("building", "building")], lambda lat, lon: result)
                self.assertEqual(len(report["sites"]), 1)
                self.assertEqual(report["sites"][0]["assessment_status"], "unavailable")
                self.assertEqual(report["failed_evaluations"], 1)
                json.dumps(report, allow_nan=False)

    def test_offline_mapping_still_shows_real_raster_terrain_samples(self):
        terrain = point(influences=[influence("mean_slope_250m", -.04, 18, 4)])
        with patch.object(place_analysis, "discover_places", side_effect=ConnectionError("offline")):
            report = place_analysis.analyze_places([terrain], 42, -72, 10, "MA",
                lambda lat, lon: self.fail("Existing terrain sample must not be recomputed"))
        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(report["sites"]), 1)
        site = report["sites"][0]
        self.assertEqual(site["kind"], "habitat")
        self.assertTrue(site["terrain_sample"])
        self.assertEqual(site["geometry"]["type"], "Point")
        self.assertEqual(site["source"], "USGS 3DEP terrain model")
        self.assertIn("Terrain sample at", site["name"])
        self.assertEqual(report["mapped_features"], 0)
        self.assertEqual(report["terrain_samples"], 1)
        self.assertIn("unavailable", " ".join(report["warnings"]).lower())

    def test_terrain_samples_respect_habitat_caps_alongside_mapped_polygon(self):
        terrains = [
            point(42 + i * .01, -72, [influence("mean_slope_250m", -.1 - i * .01)])
            for i in range(3)
        ]
        mapped = [feature("wetland", "habitat", 42.025)]
        report = self.analyze(mapped, lambda lat, lon: point(lat, lon, [
            influence("wetland_fraction_250m", -.05)]), points=terrains)
        sites = report["sites"]
        self.assertEqual(sum(site["terrain_sample"] for site in sites), 2)
        self.assertEqual(sum(not site["terrain_sample"] for site in sites), 1)
        self.assertEqual(len(sites), 3)

    def test_missing_mapping_does_not_claim_no_buildings_exist(self):
        report = self.analyze([], lambda lat, lon: point(), mapping_status="unavailable")
        self.assertEqual(report["status"], "unavailable")
        self.assertIn("unavailable", report["summary"].lower())
        self.assertNotIn("no buildings", report["summary"].lower())

    def test_selection_is_bounded_diverse_and_excludes_outside_features(self):
        features = [feature(f"building{i}", "building", 42 + i * .006) for i in range(30)]
        features += [feature("outside", "building", 44)]
        calls = []

        def evaluate(lat, lon):
            calls.append((lat, lon))
            return point(lat, lon, [influence("mean_impervious_250m", -.03)])

        report = self.analyze(features, evaluate)
        self.assertLessEqual(len(calls), 6)
        self.assertLessEqual(len(report["sites"]), 3)
        self.assertFalse(any(lat == 44 for lat, lon in calls))

    def test_center_always_included_even_without_valid_samples(self):
        areas = place_analysis.select_search_areas(
            [{"latitude": 42, "longitude": -72, "status": "unavailable"}], 42, -72, 10)
        self.assertEqual(areas, [{"latitude": 42.0, "longitude": -72.0, "radius_m": 1000}])

    def test_supporting_factors_do_not_disappear_from_specific_places(self):
        report = self.analyze([feature("road", "road")], lambda lat, lon:
            point(lat, lon, [influence("distance_to_road_m", .04, 300, 40)]))
        self.assertEqual(len(report["sites"]), 1)
        self.assertEqual(report["sites"][0]["signals"], [])
        self.assertEqual(report["sites"][0]["supporting_signals"][0]["effect"], .04)

    def test_search_neighborhoods_are_separated_bounded_and_inside_the_radius(self):
        points = [point(42 + i * .004, influences=[influence("mean_impervious_250m", -.1)])
                  for i in range(30)] + [point(44)]
        areas = place_analysis.select_search_areas(points, 42, -72, 10)
        self.assertLessEqual(len(areas), 6)
        self.assertTrue(all(area["radius_m"] == 1000 for area in areas))
        self.assertFalse(any(area["latitude"] == 44 for area in areas))

    def test_search_keeps_built_pressures_when_natural_terrain_effects_are_stronger(self):
        built = point(42, -72, [influence("mean_impervious_250m", -.05)])
        road = point(42.018, -72, [influence("distance_to_road_m", -.04)])
        terrain = [point(42 + i * .018, -72.05, [influence("elevation_m", -.4)])
                   for i in range(6)]
        areas = place_analysis.select_search_areas(terrain + [built, road], 42, -72, 10)
        locations = {(area["latitude"], area["longitude"]) for area in areas}
        self.assertIn((42, -72), locations)
        self.assertIn((42.018, -72), locations)

    def test_no_usable_habitat_samples_still_searches_requested_center(self):
        mapping = {"status": "available", "features": [feature("road", "road")],
                   "sources": [], "warnings": []}
        with patch.object(place_analysis, "discover_places", return_value=mapping) as discover:
            report = place_analysis.analyze_places([
                {"latitude": 42, "longitude": -72, "status": "unavailable"}],
                42, -72, 10, "MA", lambda lat, lon: point(lat, lon))
        discover.assert_called_once()
        self.assertEqual(discover.call_args.args[0][0]["latitude"], 42)
        self.assertEqual(report["sites"][0]["id"], "road")

    def test_malformed_mapping_keeps_an_unavailable_report(self):
        for mapping in (None, {"features": None}):
            with self.subTest(mapping=mapping), patch.object(place_analysis, "discover_places", return_value=mapping):
                report = place_analysis.analyze_places([point()], 42, -72, 10, "MA", lambda lat, lon: point())
                self.assertEqual(report["status"], "unavailable")

    def test_malformed_sample_explanations_do_not_break_search_selection(self):
        for influences in (None, [None, {"feature": None, "effect": -.2}]):
            with self.subTest(influences=influences):
                sample = point()
                sample["influences"] = influences
                report = self.analyze([], lambda lat, lon: point(), points=[sample])
                self.assertEqual(report["sites"], [])

    def test_unavailable_assessments_with_stale_numbers_are_excluded(self):
        report = self.analyze([feature("building", "building")],
            lambda lat, lon: dict(point(influences=[influence("mean_impervious_250m", -.1)]), status="unavailable"))
        self.assertEqual(len(report["sites"]), 1)
        self.assertEqual(report["sites"][0]["assessment_status"], "unavailable")
        self.assertEqual(report["failed_evaluations"], 1)

    def test_mapped_wetland_checks_do_not_assume_a_terrain_slope(self):
        report = self.analyze([feature("Mapped wetland", "habitat")],
            lambda lat, lon: point(lat, lon, [influence("wetland_fraction_250m", -.05, .2, .5)]))
        site = report["sites"][0]
        self.assertFalse(site["terrain_sample"])
        self.assertNotIn("slope", " ".join(site["field_checks"]).lower())


if __name__ == "__main__":
    unittest.main()
