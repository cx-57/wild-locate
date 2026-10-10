"""Offline mapped-place retrieval fixtures; no tests contact mapping services."""
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import requests
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform
from pyproj import Transformer

try:
    from wildlocate.core import mapped_places
except ImportError:
    mapped_places = None


def geometry(coordinates):
    return [{"lon": longitude, "lat": latitude} for longitude, latitude in coordinates]


def way(identifier, coordinates, **tags):
    return {"type": "way", "id": identifier, "tags": tags,
            "geometry": geometry(coordinates)}


def response(elements=(), **extra):
    return {"version": 0.6, "generator": "Overpass API",
            "osm3s": {"timestamp_osm_base": "2026-10-09T12:00:00Z"},
            "elements": list(elements), **extra}


class StreamResponse:
    def __init__(self, payload=None, chunks=None, headers=None):
        self.headers = headers or {"content-type": "application/json"}
        self.chunks = chunks if chunks is not None else [json.dumps(payload).encode()]
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield from self.chunks


class MappedPlacesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(mapped_places, "Mapped place retrieval is not implemented")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name) / "mapped-places.json"
        self.cache_patch = patch.object(mapped_places, "_cache_path", return_value=self.cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.areas = [{"latitude": 42.0, "longitude": -72.0, "radius_m": 1000}]

    def discover(self, payload, radius=10, region="XX"):
        with patch.object(mapped_places.requests, "post", return_value=StreamResponse(payload)):
            return mapped_places.discover_places(self.areas, 42.0, -72.0, radius, region)

    def test_actual_ways_have_source_links_geometry_and_metric_sizes(self):
        result = self.discover(response([
            way(1, [(-72.004, 42), (-71.996, 42)], highway="residential", name="Oak Road"),
            way(2, [(-72.001, 42), (-72, 42), (-72, 42.001), (-72.001, 42.001), (-72.001, 42)], building="yes"),
            way(3, [(-72.002, 41.999), (-72.001, 41.999), (-72.001, 42), (-72.002, 42), (-72.002, 41.999)], natural="wetland"),
            {"type": "node", "id": 4, "lat": 42, "lon": -72, "tags": {"building": "yes"}},
        ]))
        self.assertEqual([item["kind"] for item in result["features"]], ["road", "building", "habitat"])
        road, building, habitat = result["features"]
        self.assertEqual(road["name"], "Oak Road")
        self.assertEqual(road["source_url"], "https://www.openstreetmap.org/way/1")
        self.assertGreater(road["length_m"], 600)
        self.assertGreater(building["area_m2"], 8000)
        self.assertEqual(habitat["tags"]["natural"], "wetland")
        self.assertEqual(result["sources"][0]["data_timestamp"], "2026-10-09T12:00:00Z")
        self.assertIn("OpenStreetMap", result["sources"][0]["attribution"])
        self.assertTrue(any("coverage" in value.lower() for value in result["warnings"]))
        json.dumps(result, allow_nan=False)

    def test_geometry_is_clipped_in_meters_to_the_selected_study_circle(self):
        result = self.discover(response([way(1, [(-72.02, 42), (-72, 42), (-71.98, 42)], highway="primary")]), radius=.5)
        feature = result["features"][0]
        project = Transformer.from_crs("EPSG:4326", "+proj=aeqd +lat_0=42 +lon_0=-72 +datum=WGS84 +units=m", always_xy=True).transform
        projected = transform(project, shape(feature["geometry"]))
        self.assertLessEqual(max(abs(value) for value in projected.bounds), 500.01)
        self.assertAlmostEqual(feature["length_m"], 1000, delta=1)
        self.assertTrue(projected.distance(Point(0, 0)) < .01)

    def test_large_way_is_clipped_to_inspected_neighborhoods_as_well(self):
        result = self.discover(response([way(1, [(-72.1, 42), (-71.9, 42)], highway="primary")]))
        self.assertAlmostEqual(result["features"][0]["length_m"], 2000, delta=2)

    def test_paths_and_unclosed_building_ways_do_not_become_footprints(self):
        result = self.discover(response([
            way(1, [(-72.001, 42), (-72, 42)], highway="footway"),
            way(2, [(-72.001, 42), (-72, 42)], highway="path"),
            way(3, [(-72.001, 42), (-72, 42), (-72, 42.001)], building="yes"),
        ]))
        self.assertEqual(result["features"], [])
        self.assertEqual(result["status"], "partial")

    def test_multipolygon_fragments_keep_the_hole_and_do_not_duplicate_members(self):
        outer1 = [(-72.003, 41.997), (-71.997, 41.997), (-71.997, 42.003)]
        outer2 = [(-71.997, 42.003), (-72.003, 42.003), (-72.003, 41.997)]
        inner = [(-72.001, 41.999), (-71.999, 41.999), (-71.999, 42.001), (-72.001, 42.001), (-72.001, 41.999)]
        relation = {"type": "relation", "id": 10, "tags": {"type": "multipolygon", "landuse": "forest", "name": "Test Wood"}, "members": [
            {"type": "way", "ref": 1, "role": "outer", "geometry": geometry(outer1)},
            {"type": "way", "ref": 2, "role": "outer", "geometry": geometry(outer2)},
            {"type": "way", "ref": 3, "role": "inner", "geometry": geometry(inner)},
        ]}
        result = self.discover(response([relation, way(3, inner, landuse="forest")]))
        self.assertEqual(len(result["features"]), 1)
        feature = result["features"][0]
        self.assertEqual(feature["source_url"], "https://www.openstreetmap.org/relation/10")
        polygon = shape(feature["geometry"])
        self.assertFalse(polygon.covers(Point(-72, 42)))
        self.assertTrue(polygon.covers(Point(-72.002, 42)))

    def test_habitat_relation_does_not_hide_a_road_that_also_forms_its_boundary(self):
        ring = [(-72.003, 41.997), (-71.997, 41.997), (-71.997, 42.003), (-72.003, 42.003), (-72.003, 41.997)]
        relation = {"type": "relation", "id": 10, "tags": {"type": "multipolygon", "landuse": "forest"}, "members": [
            {"type": "way", "ref": 1, "role": "outer", "geometry": geometry(ring)},
        ]}
        result = self.discover(response([relation, way(1, ring, highway="service")]))
        self.assertEqual({item["kind"] for item in result["features"]}, {"habitat", "road"})

    def test_forest_relation_does_not_hide_a_separately_tagged_lake_inside_it(self):
        outer = [(-72.003, 41.997), (-71.997, 41.997), (-71.997, 42.003), (-72.003, 42.003), (-72.003, 41.997)]
        lake = [(-72.001, 41.999), (-71.999, 41.999), (-71.999, 42.001), (-72.001, 42.001), (-72.001, 41.999)]
        relation = {"type": "relation", "id": 10, "tags": {"type": "multipolygon", "landuse": "forest"}, "members": [
            {"type": "way", "ref": 1, "role": "outer", "geometry": geometry(outer)},
            {"type": "way", "ref": 2, "role": "inner", "geometry": geometry(lake)},
        ]}
        result = self.discover(response([relation, way(2, lake, natural="water")]))
        self.assertEqual(len(result["features"]), 2)
        self.assertEqual({item["name"] for item in result["features"]}, {"Forest", "Water body"})
        self.assertFalse(shape(result["features"][0]["geometry"]).covers(Point(-72, 42)))
        self.assertTrue(shape(result["features"][1]["geometry"]).covers(Point(-72, 42)))

    def test_nested_multipolygon_preserves_an_island_inside_an_inner_hole(self):
        def square(offset):
            return [(-72-offset, 42-offset), (-72+offset, 42-offset), (-72+offset, 42+offset), (-72-offset, 42+offset), (-72-offset, 42-offset)]
        relation = {"type": "relation", "id": 10, "tags": {"type": "multipolygon", "natural": "water"}, "members": [
            {"type": "way", "ref": 1, "role": "outer", "geometry": geometry(square(.004))},
            {"type": "way", "ref": 2, "role": "inner", "geometry": geometry(square(.003))},
            {"type": "way", "ref": 3, "role": "outer", "geometry": geometry(square(.001))},
        ]}
        result = self.discover(response([relation]))
        self.assertEqual(len(result["features"]), 1)
        polygon = shape(result["features"][0]["geometry"])
        self.assertTrue(polygon.covers(Point(-72, 42)))
        self.assertFalse(polygon.covers(Point(-72.002, 42)))
        self.assertTrue(polygon.covers(Point(-72.0035, 42)))

    def test_duplicate_ids_are_removed_and_nonfinite_geometry_is_rejected(self):
        valid = way(1, [(-72.001, 42), (-72, 42)], highway="service")
        malformed = way(2, [(-72, 42), (float("nan"), 42)], highway="primary")
        result = self.discover(response([valid, copy.deepcopy(valid), malformed]))
        self.assertEqual(len(result["features"]), 1)
        self.assertEqual(result["status"], "partial")
        json.dumps(result, allow_nan=False)

    def test_overpass_remark_rejects_even_otherwise_valid_partial_results(self):
        result = self.discover(response([way(1, [(-72.001, 42), (-72, 42)], highway="primary")], remark="runtime error: Query timed out"))
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["features"], [])
        self.assertFalse(self.cache.exists())

    def test_malformed_top_level_payload_is_unavailable(self):
        for payload in ({"elements": {}}, {"elements": [None]}, []):
            with self.subTest(payload=payload):
                result = self.discover(payload)
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["features"], [])

    def test_more_than_six_thousand_elements_is_rejected_without_truncating(self):
        result = self.discover(response([{"type": "node", "id": value, "lat": 42, "lon": -72} for value in range(6001)]))
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["features"], [])

    def test_streamed_response_larger_than_six_megabytes_is_rejected(self):
        stream = StreamResponse(chunks=[b" " * (6 * 1024 * 1024), b"{}"])
        with patch.object(mapped_places.requests, "post", return_value=stream):
            result = mapped_places.discover_places(self.areas, 42, -72, 10, "XX")
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(stream.closed)

    def test_elapsed_deadline_discards_a_slow_stream(self):
        stream = StreamResponse(response())
        with patch.object(mapped_places.time, "monotonic", side_effect=[0, 31]), patch.object(mapped_places.requests, "post", return_value=stream):
            result = mapped_places.discover_places(self.areas, 42, -72, 10, "XX")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["features"], [])
        self.assertTrue(stream.closed)

    def test_overall_deadline_returns_even_when_the_stream_has_not_yielded_a_chunk(self):
        closed = threading.Event()
        class SlowResponse(StreamResponse):
            def iter_content(self, chunk_size):
                time.sleep(.15)
                yield from super().iter_content(chunk_size)
            def __exit__(self, *args):
                super().__exit__(*args)
                closed.set()
        stream = SlowResponse(response())
        with patch.object(mapped_places, "REQUEST_DEADLINE_SECONDS", .02, create=True), patch.object(mapped_places.requests, "post", return_value=stream):
            started = time.monotonic()
            result = mapped_places.discover_places(self.areas, 42, -72, 10, "XX")
            elapsed = time.monotonic() - started
            self.assertTrue(closed.wait(.4), "aborted request must close its stream once the blocking read returns")
        self.assertEqual(result["status"], "unavailable")
        self.assertLess(elapsed, .1)

    def test_oversized_content_length_is_rejected_before_reading_body(self):
        class UnreadableResponse(StreamResponse):
            def iter_content(self, chunk_size):
                raise AssertionError("oversized body must not be read")
        stream = UnreadableResponse(response(), headers={"content-length": "6291457"})
        with patch.object(mapped_places.requests, "post", return_value=stream):
            result = mapped_places.discover_places(self.areas, 42, -72, 10, "XX")
        self.assertEqual(result["status"], "unavailable")

    def test_request_is_single_bounded_https_query_with_real_geometry(self):
        calls = []
        def request(url, **kwargs):
            calls.append((url, kwargs))
            return StreamResponse(response())
        with patch.object(mapped_places.requests, "post", side_effect=request):
            mapped_places.discover_places(self.areas * 8, 42, -72, 10, "XX")
        self.assertEqual(len(calls), 1)
        url, options = calls[0]
        self.assertEqual(url, "https://overpass-api.de/api/interpreter")
        self.assertEqual(options["timeout"], (5, 25))
        self.assertFalse(options["allow_redirects"])
        self.assertTrue(options["stream"])
        query = options["data"]["data"]
        self.assertIn("[timeout:25]", query)
        self.assertIn("out body geom", query)
        self.assertIn('relation["type"="multipolygon"]["building"]', query)
        self.assertLess(query.count("around:"), 40)

    def test_fresh_cache_survives_a_second_process_and_expired_cache_refetches(self):
        payload = response([way(1, [(-72.001, 42), (-72, 42)], highway="primary")])
        with patch.object(mapped_places.time, "time", return_value=1_790_000_000):
            first = self.discover(payload)
        self.assertTrue(self.cache.exists())
        with patch.object(mapped_places.time, "time", return_value=1_790_000_010), patch.object(mapped_places.requests, "post", side_effect=AssertionError("fresh cache should avoid network")):
            cached = mapped_places.discover_places(self.areas, 42, -72, 10, "XX")
        self.assertEqual(first["features"], cached["features"])
        self.assertEqual(first["retrieved_at"], cached["retrieved_at"])
        with patch.object(mapped_places.time, "time", return_value=1_790_086_401):
            expired = self.discover(response())
        self.assertEqual(expired["features"], [])
        self.assertNotEqual(expired["retrieved_at"], first["retrieved_at"])

    def test_invalid_cache_is_ignored_and_cache_size_stays_bounded(self):
        self.cache.write_text("invalid JSON")
        result = self.discover(response([way(1, [(-72.001, 42), (-72, 42)], highway="primary")], unused="x" * (3 * 1024 * 1024)))
        self.assertEqual(len(result["features"]), 1)
        self.assertLessEqual(self.cache.stat().st_size, 3 * 1024 * 1024)

    def test_candidate_limit_preserves_rare_feature_kinds_and_other_search_neighborhoods(self):
        self.areas.append({"latitude": 42.05, "longitude": -72, "radius_m": 1000})
        elements = [way(value, [(-72.001, 42), (-72, 42)], highway="residential") for value in range(1, 502)]
        elements.extend([
            way(1000, [(-72.001, 42.05), (-72, 42.05)], highway="primary", name="Far Road"),
            way(1001, [(-72.001, 42), (-72, 42), (-72, 42.001), (-72.001, 42.001), (-72.001, 42)], building="yes"),
            way(1002, [(-72.002, 41.999), (-72.001, 41.999), (-72.001, 42), (-72.002, 42), (-72.002, 41.999)], natural="wetland"),
        ])
        result = self.discover(response(elements))
        self.assertLessEqual(len(result["features"]), 500)
        self.assertEqual({item["kind"] for item in result["features"]}, {"road", "building", "habitat"})
        self.assertIn("Far Road", [item["name"] for item in result["features"]])
        self.assertEqual(result["status"], "partial")

    def test_polygon_with_self_crossing_boundary_is_not_repaired_into_a_building(self):
        result = self.discover(response([
            way(1, [(-72.001, 42), (-72, 42.001), (-72.001, 42.001), (-72, 42), (-72.001, 42)], building="yes"),
        ]))
        self.assertEqual(result["features"], [])
        self.assertEqual(result["status"], "partial")

    def test_relation_with_unmatched_inner_ring_is_not_filled_in(self):
        outer = [(-72.003, 41.997), (-71.997, 41.997), (-71.997, 42.003), (-72.003, 42.003), (-72.003, 41.997)]
        outside = [(-72.005, 42.005), (-72.004, 42.005), (-72.004, 42.006), (-72.005, 42.006), (-72.005, 42.005)]
        relation = {"type": "relation", "id": 10, "tags": {"type": "multipolygon", "building": "yes"}, "members": [
            {"type": "way", "ref": 1, "role": "outer", "geometry": geometry(outer)},
            {"type": "way", "ref": 2, "role": "inner", "geometry": geometry(outside)},
        ]}
        result = self.discover(response([relation]))
        self.assertEqual(result["features"], [])
        self.assertEqual(result["status"], "partial")

    def test_cache_write_failure_does_not_discard_online_results(self):
        with patch.object(mapped_places, "_write_cache", side_effect=OSError("disk full")):
            result = self.discover(response([way(1, [(-72.001, 42), (-72, 42)], highway="primary")]))
        self.assertEqual(len(result["features"]), 1)

    def test_massachusetts_uses_real_cached_local_roads_when_mapping_fails(self):
        roads = gpd.GeoDataFrame({"STREETNAME": ["Local Road"], "geometry": [LineString([(-72.002, 42), (-71.998, 42)])]}, crs="EPSG:4326")
        with patch.object(mapped_places.requests, "post", side_effect=requests.Timeout()), patch.object(mapped_places.environment, "get_cached_context", return_value={"roads_gdf": roads}):
            result = mapped_places.discover_places(self.areas, 42, -72, 10, "MA")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["features"][0]["name"], "Local Road")
        self.assertEqual(result["features"][0]["kind"], "road")
        self.assertIn("MassDOT", result["features"][0]["source"])
        self.assertTrue(any("building" in warning.lower() for warning in result["warnings"]))

    def test_local_road_limit_preserves_other_inspected_neighborhoods(self):
        self.areas.append({"latitude": 42.05, "longitude": -72, "radius_m": 1000})
        roads = gpd.GeoDataFrame({
            "STREETNAME": ["Near Road"] * 501 + ["Far Road"],
            "geometry": [LineString([(-72.002, 42), (-71.998, 42)])] * 501 + [LineString([(-72.002, 42.05), (-71.998, 42.05)])],
        }, crs="EPSG:4326")
        with patch.object(mapped_places.requests, "post", side_effect=requests.Timeout()), patch.object(mapped_places.environment, "get_cached_context", return_value={"roads_gdf": roads}):
            result = mapped_places.discover_places(self.areas, 42, -72, 10, "MA")
        self.assertLessEqual(len(result["features"]), 500)
        self.assertIn("Far Road", [feature["name"] for feature in result["features"]])
        self.assertTrue(any("limited" in warning.lower() for warning in result["warnings"]))

    def test_no_local_fallback_is_used_outside_massachusetts(self):
        with patch.object(mapped_places.requests, "post", side_effect=requests.Timeout()), patch.object(mapped_places.environment, "get_cached_context", side_effect=AssertionError("not a Massachusetts analysis")):
            result = mapped_places.discover_places(self.areas, 42, -72, 10, "NY")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["features"], [])

    def test_invalid_search_area_is_not_sent_to_mapping_service(self):
        with patch.object(mapped_places.requests, "post", side_effect=AssertionError("invalid query must not be sent")):
            result = mapped_places.discover_places([{"latitude": float("nan"), "longitude": -72, "radius_m": 1000}], 42, -72, 10, "XX")
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["searched_areas"], [])


if __name__ == "__main__":
    unittest.main()
