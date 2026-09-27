"""Wild-Locate Python regression tests."""

import http.client
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

# PyQt6 on macOS ships the native Cocoa platform plugin, not the Linux-style
# offscreen plugin. Configure Qt before importing any Qt widgets so the same
# suite can run on the developer Mac and in headless Linux environments.
if sys.platform == "darwin":
    from wildlocate.cli import _configure_qt_runtime
    _configure_qt_runtime()
    os.environ.setdefault("QT_QPA_PLATFORM", "cocoa")
elif sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
from pyproj import Geod
from sklearn.linear_model import LogisticRegression
from PyQt6.QtWidgets import QApplication
from wildlocate.gui.app import MainWindow


class AreaTests(unittest.TestCase):
    def area_module(self):
        import wildlocate.core.predict as prediction
        return prediction

    def test_grid_stays_within_radius_and_includes_center(self):
        area = self.area_module()
        geod = Geod(ellps="WGS84")
        for lat, lon in [(42.37, -72.28), (0, 179.99), (89.9, 0)]:
            for radius in (10, 25, 50):
                points = area.build_grid(lat, lon, radius)
                self.assertEqual(len(points), 81)
                self.assertTrue(any(abs(p['latitude']-lat) < 1e-8 and abs(p['longitude']-lon) < 1e-8 for p in points))
                for p in points:
                    self.assertLessEqual(geod.inv(lon, lat, p['longitude'], p['latitude'])[2], radius*1000 + 0.001)
                    self.assertTrue(-180 <= p['longitude'] <= 180)

    def test_invalid_radius_and_coordinates_rejected(self):
        area = self.area_module()
        for radius in (0, -10, 11, True, float('nan'), float('inf'), '25'):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                area.build_grid(42, -72, radius)
        for lat, lon in [(float('nan'), 0), (0, float('inf')), (91, 0), (0, -181), (True, 0)]:
            with self.assertRaises(ValueError):
                area.build_grid(lat, lon, 25)

    def setup_prediction(self, extraction):
        area = self.area_module()
        frame = pd.DataFrame({'habitat': [-1., 0., 1.]})
        model = LogisticRegression().fit(frame, [0, 0, 1])
        from types import SimpleNamespace
        record = SimpleNamespace(species='Bobcat')
        for target, value in [
            ('resolve_model', record),
            ('load_model_and_metadata', (model, {'predictor_names': ['habitat'], 'selected_model': 'LogisticRegression', 'presence_count': 25})),
            ('load_comparison_scores', (np.array([0.1, 0.3, 0.6, 0.9]), frame)),
        ]:
            self.enterContext(patch.object(area, target, return_value=value))
        self.enterContext(patch.object(area, 'extract_features', side_effect=extraction))
        return area

    def test_individual_scores_partial_coverage_and_json(self):
        def extract(lat, lon):
            if lat > 42.5:
                raise ValueError('Requested coordinate cannot be evaluated: missing raster')
            return {'habitat': (lat - 42.37) * 10}
        area = self.setup_prediction(extract)
        result = area.predict_area('Bobcat', 42.37, -72.28, 25)
        good = [p for p in result['points'] if p['status'] == 'ok']
        missing = [p for p in result['points'] if p['status'] == 'unavailable']
        self.assertTrue(good and missing)
        self.assertEqual(result['evaluated_points'], len(good))
        self.assertEqual(result['unavailable_points'], len(missing))
        self.assertGreater(len({p['score'] for p in good}), 1)
        self.assertAlmostEqual(result['mean_score'], sum(p['score'] for p in good)/len(good))
        self.assertNotIn('score', missing[0])
        json.dumps(result, allow_nan=False)

    def test_all_unavailable_is_not_zero_suitability(self):
        area = self.setup_prediction(lambda *_: {'habitat': float('nan')})
        result = area.predict_area('Bobcat', 42.37, -72.28, 25)
        self.assertEqual(result['evaluated_points'], 0)
        self.assertIsNone(result['mean_score'])
        self.assertEqual(result['unavailable_points'], 81)

    def test_missing_dataset_fails_instead_of_hiding_as_coverage(self):
        def missing(*_):
            raise FileNotFoundError('Missing environmental dataset')
        area = self.setup_prediction(missing)
        with self.assertRaises(FileNotFoundError):
            area.predict_area('Bobcat', 42.37, -72.28, 25)

    def test_other_states_use_the_selected_states_extractor(self):
        area = self.setup_prediction(lambda *_: self.fail('MA extractor used for another state'))
        model, metrics = area.load_model_and_metadata(None)
        from wildlocate.core.regional import SCHEMA
        for region in ('FL', 'AZ'):
            metadata = dict(metrics, region=region, feature_schema=SCHEMA)
            def extract(lat, lon, selected):
                self.assertEqual(selected, region)
                return {'habitat': lat / 90}
            with patch.object(area, 'load_model_and_metadata', return_value=(model, metadata)), patch('wildlocate.core.regional.extract_regional_features', side_effect=extract):
                result = area.predict_area('Bobcat', 28, -81, 10, region)
                self.assertEqual(result['region'], region)
                self.assertEqual(result['evaluated_points'], 81)

    def test_service_rejects_radius_before_prediction(self):
        from wildlocate.core.predict import assess_habitat, PredictionError
        with patch('wildlocate.core.predict.available_species', return_value=['Bobcat']):
            with self.assertRaises(PredictionError) as error:
                assess_habitat('Bobcat', 42, -72, radius_km=100)
            self.assertEqual(error.exception.code, 'invalid_radius')

    def test_existing_point_request_still_uses_point_prediction(self):
        from wildlocate.core.predict import assess_habitat
        with patch('wildlocate.core.predict.available_species', return_value=['Bobcat']), patch('wildlocate.core.predict.predict_species', return_value={'features': {}, 'score': 0.5}):
            self.assertEqual(assess_habitat('bobcat', 42, -72)['score'], 0.5)


class AreaUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(['test'])

    def setUp(self):
        self.enterContext(patch('wildlocate.gui.app.QWebEngineView', None))
        self.enterContext(patch('wildlocate.gui.app.available_species', return_value=['Bobcat']))
        self.window = MainWindow()
        self.addCleanup(self.window.close)

    def test_mode_radius_preview_and_result_invalidation(self):
        w = self.window
        self.assertTrue(w.radius_controls.isHidden())
        w.analysis_type.setCurrentIndex(1)
        self.assertFalse(w.radius_controls.isHidden())
        self.assertEqual(w.location_map._radius_km, 25)
        w.show_result({'analysis_type': 'regional', 'species': 'Bobcat', 'latitude': 42., 'longitude': -72., 'radius_km': 25, 'grid_spacing_km': 5, 'evaluated_points': 1, 'unavailable_points': 0, 'mean_score': 0.8, 'model': 'Test', 'training_observations': 25, 'points': [{'latitude': 42., 'longitude': -72., 'status': 'ok', 'score': 0.8, 'percentile': 80, 'category': 'Very High'}]})
        self.assertEqual(len(w.location_map._area_points), 1)
        self.assertFalse(w.export_button.isHidden())
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'assessment.json')
            with patch('wildlocate.gui.app.QFileDialog.getSaveFileName', return_value=(path, 'JSON')):
                w.export_result()
            exported = json.loads(Path(path).read_text())
            self.assertEqual(exported['radius_km'], 25)
            self.assertEqual(exported['points'][0]['score'], 0.8)
            self.assertIn('sampled grid', exported['interpretation'])
        w.radius_choice.setCurrentIndex(2)
        self.assertIsNone(w.result)
        self.assertEqual(w.location_map._area_points, [])
        self.assertEqual(w.location_map._radius_km, 50)
        w.analysis_type.setCurrentIndex(0)
        self.assertIsNone(w.location_map._radius_km)

    def test_regional_request_and_cancel_controls(self):
        w = self.window
        w.analysis_type.setCurrentIndex(1)
        # Avoid a real data download while checking the request that the UI sends.
        with patch.object(w.client, 'analyze') as analyze:
            w.analyze()
        self.assertEqual(analyze.call_args.kwargs['radius_km'], 25)
        self.assertFalse(w.analysis_type.isEnabled())
        w.cancelled()
        self.assertTrue(w.analysis_type.isEnabled())

    def test_no_coverage_is_displayed_without_point_metrics(self):
        w = self.window
        w.show_result({'analysis_type': 'regional', 'species': 'Bobcat', 'latitude': 42., 'longitude': -72., 'radius_km': 25, 'grid_spacing_km': 5, 'evaluated_points': 0, 'unavailable_points': 81, 'mean_score': None, 'model': 'Test', 'training_observations': 25, 'points': []})
        self.assertIn('No grid points', w.area_summary.text())
        self.assertTrue(w.environment.isHidden())
        self.assertIs(w.stack.currentWidget(), w.area_page)

# -----------------------------------------------------------------------------

class SpeciesSearchTests(unittest.TestCase):
    def test_partial_query_returns_state_observed_species_only(self):
        from wildlocate.core import observations

        taxa = {
            "results": [
                {
                    "id": 10, "name": "Alligator", "rank": "genus",
                    "iconic_taxon_name": "Reptilia",
                    "preferred_common_name": "Alligators",
                },
                {
                    "id": 20, "name": "Alligator mississippiensis", "rank": "species",
                    "iconic_taxon_name": "Reptilia",
                    "preferred_common_name": "American Alligator",
                },
                {
                    "id": 30, "name": "Alligator sinensis", "rank": "species",
                    "iconic_taxon_name": "Reptilia",
                    "preferred_common_name": "Chinese Alligator",
                },
                {
                    "id": 40, "name": "Alligator example", "rank": "species",
                    "iconic_taxon_name": "Reptilia",
                    "preferred_common_name": "Example Alligator",
                },
            ]
        }

        def fake_api(endpoint, params=None):
            if endpoint in {"/taxa/autocomplete", "/taxa"}:
                return taxa
            if endpoint == "/observations":
                counts = {20: 1500, 30: 0, 40: 25}
                return {"total_results": counts[int(params["taxon_id"])]}
            self.fail(f"Unexpected endpoint: {endpoint}")

        with patch.object(observations, "find_place_id", return_value=21), patch.object(
            observations, "api_get", side_effect=fake_api
        ):
            results = observations.species_suggestions("alligator", "Florida", limit=3)

        self.assertEqual(
            [item["common_name"] for item in results],
            ["American Alligator", "Example Alligator"],
        )
        self.assertEqual(results[0]["observation_count"], 1500)
        self.assertTrue(all(item["rank"] == "species" for item in results))

    def test_scientific_name_resolution_fetches_full_taxon_when_autocomplete_is_sparse(self):
        from wildlocate.core import observations

        def fake_api(endpoint, params=None):
            if endpoint == "/taxa/autocomplete":
                return {
                    "results": [
                        {
                            "id": 123,
                            "name": "Lampropeltis triangulum",
                            "rank": "species",
                            "iconic_taxon_name": "Reptilia",
                        }
                    ]
                }
            if endpoint == "/taxa/123":
                return {
                    "results": [
                        {
                            "id": 123,
                            "name": "Lampropeltis triangulum",
                            "rank": "species",
                            "iconic_taxon_name": "Reptilia",
                            "preferred_common_name": "Eastern Milksnake",
                        }
                    ]
                }
            self.fail(f"Unexpected endpoint: {endpoint}")

        with patch.object(observations, "api_get", side_effect=fake_api):
            result = observations.resolve_species("Lampropeltis triangulum")

        self.assertEqual(result["common_name"], "Eastern Milksnake")
        self.assertEqual(result["scientific_name"], "Lampropeltis triangulum")
        self.assertEqual(result["taxon_id"], 123)

    def test_generic_mammal_word_finds_species_containing_that_word(self):
        from wildlocate.core import observations

        autocomplete = {
            "results": [
                {
                    "id": 100,
                    "name": "Cervidae",
                    "rank": "family",
                    "iconic_taxon_name": "Mammalia",
                    "preferred_common_name": "Deer",
                }
            ]
        }
        broad = {
            "results": [
                {
                    "id": 101,
                    "name": "Odocoileus virginianus",
                    "rank": "species",
                    "iconic_taxon_name": "Mammalia",
                    "preferred_common_name": "White-tailed Deer",
                },
                {
                    "id": 102,
                    "name": "Odocoileus hemionus",
                    "rank": "species",
                    "iconic_taxon_name": "Mammalia",
                    "preferred_common_name": "Mule Deer",
                },
                {
                    "id": 103,
                    "name": "Cervus canadensis",
                    "rank": "species",
                    "iconic_taxon_name": "Mammalia",
                    "preferred_common_name": "Elk",
                },
            ]
        }

        def fake_api(endpoint, params=None):
            if endpoint == "/taxa/autocomplete":
                return autocomplete
            if endpoint == "/taxa":
                return broad
            if endpoint == "/observations":
                counts = {101: 2000, 102: 0}
                return {"total_results": counts[int(params["taxon_id"])]}
            self.fail(f"Unexpected endpoint: {endpoint}")

        with patch.object(observations, "find_place_id", return_value=1), patch.object(
            observations, "api_get", side_effect=fake_api
        ):
            results = observations.species_suggestions("deer", "Massachusetts", limit=3)

        self.assertEqual(
            [item["common_name"] for item in results],
            ["White-tailed Deer"],
        )

    def test_background_sampling_uses_available_candidates_instead_of_three_to_one(self):
        from wildlocate.core import observations

        with tempfile.TemporaryDirectory() as folder:
            samples = Path(folder)
            species = "Test Deer"
            presence_count = 100
            presence = pd.DataFrame(
                {
                    "latitude": np.linspace(28.0, 28.9, presence_count),
                    "longitude": np.linspace(-81.0, -80.1, presence_count),
                }
            )
            presence.to_csv(
                samples / f"{observations.species_slug(species)}_occurrences.csv",
                index=False,
            )

            candidates = pd.DataFrame(
                {
                    "latitude": np.linspace(27.0, 27.49, 50),
                    "longitude": np.linspace(-82.0, -81.51, 50),
                    "observation_id": range(50),
                    "taxon_id": [999] * 50,
                    "taxon_name": ["Other species"] * 50,
                    "common_name": ["Other animal"] * 50,
                }
            )

            with patch.object(
                observations, "load_or_create_target_group_pool", return_value=candidates
            ), patch.object(
                observations, "filter_target_group_pool", return_value=candidates
            ), patch.object(
                observations, "compute_min_distance_to_presence",
                return_value=np.full(len(candidates), 5000.0),
            ), patch.object(
                observations, "spatial_thin", return_value=candidates
            ):
                result = observations.generate_background(
                    species,
                    samples_dir=samples,
                    taxon_id=123,
                    target_group="Mammalia",
                    place_name="Florida",
                )

        self.assertEqual(len(result), 50)

    def test_cached_background_pool_is_capped_at_8000(self):
        from wildlocate.core import observations

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reptile_pool.csv"
            count = observations.DEFAULT_MAX_BACKGROUND_POOL + 50
            pd.DataFrame(
                {
                    "observation_id": range(count),
                    "taxon_id": [1] * count,
                    "taxon_name": ["Species"] * count,
                    "common_name": ["Animal"] * count,
                    "latitude": [28.0] * count,
                    "longitude": [-81.0] * count,
                    "positional_accuracy": [10] * count,
                    "observed_on": ["2026-01-01"] * count,
                    "coordinates_obscured": [False] * count,
                }
            ).to_csv(path, index=False)

            pool = observations.load_or_create_target_group_pool(
                "Reptilia", pool_file=path, place_name="Florida"
            )

            self.assertEqual(len(pool), 8000)
            self.assertEqual(len(pd.read_csv(path)), 8000)


# Web server tests
from wildlocate.web.server import JobManager, create_server

PAYLOAD = {'species': 'Bobcat', 'latitude': 42.37, 'longitude': -72.28, 'region': 'MA', 'radius_km': 25}


class JobTests(unittest.TestCase):
    def manager(self, source):
        manager = JobManager(command=[sys.executable, '-c', source])
        self.addCleanup(manager.close)
        return manager

    def wait(self, manager, identifier):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = manager.status(identifier)
            if result['status'] != 'running':
                return result
            time.sleep(.01)
        self.fail('Worker did not complete')

    def test_success_and_error_protocol(self):
        for reply, expected in [({'result': {'score': .5}}, 'complete'), ({'error': 'Missing data', 'code': 'data_unavailable'}, 'error')]:
            m = self.manager(f'import json,sys; json.loads(sys.stdin.readline()); print({json.dumps(reply)!r})')
            job = m.start(PAYLOAD)
            result = self.wait(m, job['id'])
            self.assertEqual(result['status'], expected)
            self.assertEqual(result.get('result', {}).get('score') if expected == 'complete' else result['error'], .5 if expected == 'complete' else 'Missing data')

    def test_cancel_busy_restart_and_shutdown(self):
        m = self.manager('import json,sys,time; r=json.loads(sys.stdin.readline()); time.sleep(r.get("delay",0)); print(json.dumps({"result":{"score":.6}}))')
        first = m.start({'delay': 30})
        with self.assertRaises(ValueError):
            m.start({})
        self.assertEqual(m.cancel(first['id'])['status'], 'cancelled')
        second = m.start({})
        self.assertEqual(self.wait(m, second['id'])['result']['score'], .6)
        with self.assertRaises(KeyError):
            m.status(first['id'])
        m.close()
        with self.assertRaises(ValueError):
            m.start({})

    def test_bad_worker_output_is_error(self):
        m = self.manager('print("not JSON")')
        job = m.start({})
        self.assertEqual(self.wait(m, job['id'])['status'], 'error')


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = create_server(0)
        # Most server tests exercise the signed-in application. Authentication
        # itself is covered separately without touching a real local account DB.
        self.server.username = 'testuser'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        body = json.dumps(payload) if payload is not None else None
        h = {'Content-Type': 'application/json', 'X-Wildlocate-Token': self.server.token}
        h.update(headers or {})
        connection.request(method, path, body, h)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        return status, data

    def test_config_and_empty_states(self):
        status, data = self.request('GET', '/api/config')
        self.assertEqual(status, 200)
        config = json.loads(data)
        ma = next(r for r in config['regions'] if r['code'] == 'MA')
        self.assertTrue(config['authenticated'])
        self.assertEqual(config['username'], 'testuser')
        self.assertIn('Bobcat', ma['species'])
        self.assertEqual(config['token'], self.server.token)

    def test_species_suggestions_endpoint_uses_selected_state(self):
        suggestions = [
            {
                "taxon_id": 20,
                "common_name": "American Alligator",
                "scientific_name": "Alligator mississippiensis",
                "rank": "species",
                "iconic_taxon_name": "Reptilia",
                "observation_count": 1500,
            }
        ]
        with patch("wildlocate.web.server.species_suggestions", return_value=suggestions) as search:
            status, data = self.request(
                "POST",
                "/api/species/suggestions",
                {"region": "FL", "query": "alligator"},
            )
        self.assertEqual(status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["region"], "FL")
        self.assertEqual(payload["suggestions"][0]["common_name"], "American Alligator")
        search.assert_called_once_with("alligator", "Florida", limit=3)

    def test_map_responses_allow_cross_origin_referrer(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{self.server.server_port}"})
        response = connection.getresponse()
        response.read()
        self.assertEqual(
            response.getheader("Referrer-Policy"),
            "strict-origin-when-cross-origin",
        )
        connection.close()

    def test_login_logout_and_auth_gate(self):
        self.server.username = None
        self.assertEqual(self.request('POST', '/api/jobs', PAYLOAD)[0], 401)
        with patch('wildlocate.web.server.authenticate', return_value='alice') as authenticate:
            status, data = self.request(
                'POST', '/api/auth/login',
                {'username': 'Alice', 'password': 'password1', 'create': False},
            )
        self.assertEqual(status, 200)
        authenticate.assert_called_once_with('Alice', 'password1', create=False)
        config = json.loads(data)
        self.assertTrue(config['authenticated'])
        self.assertEqual(config['username'], 'alice')
        status, data = self.request('POST', '/api/auth/logout', {})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(data)['authenticated'])

    def test_forbidden_requests_and_paths(self):
        self.assertEqual(self.request('POST', '/api/jobs', PAYLOAD, {'X-Wildlocate-Token': ''})[0], 403)
        self.assertEqual(self.request('POST', '/api/jobs', PAYLOAD, {'Origin': 'https://example.com'})[0], 403)
        self.assertEqual(self.request('GET', '/api/config', headers={'Host': 'evil.example'})[0], 403)
        for path in ('/../../pyproject.toml', '/%2e%2e/pyproject.toml', '/vendor/../../cli.py'):
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_invalid_inputs_do_not_launch_jobs(self):
        with patch.object(self.server.jobs, 'start', side_effect=AssertionError('Invalid request reached worker')):
            for change in ({'latitude': True}, {'latitude': float('nan')}, {'longitude': 181}, {'radius_km': 11}, {'species': 'Unknown'}, {'region': 'NY'}, {'extra': 1}):
                self.assertEqual(self.request('POST', '/api/jobs', dict(PAYLOAD, **change))[0], 422)
            self.assertEqual(self.request('POST', '/api/jobs', ['wrong shape'])[0], 422)
            self.assertEqual(self.request('POST', '/api/jobs', {'padding': 'a' * 9000})[0], 413)

    def test_job_http_round_trip(self):
        self.server.jobs.close()
        self.server.jobs = JobManager(command=[sys.executable, '-c', 'import json,sys; r=json.loads(sys.stdin.readline()); print(json.dumps({"result":{"radius_km":r["radius_km"]}}))'])
        status, body = self.request('POST', '/api/jobs', PAYLOAD)
        self.assertEqual(status, 202)
        job = json.loads(body)
        for _ in range(100):
            status, body = self.request('GET', '/api/jobs/' + job['id'])
            result = json.loads(body)
            if result['status'] != 'running':
                break
            time.sleep(.01)
        self.assertEqual(result['result']['radius_km'], 25)

    def test_cli_preserves_desktop_and_dispatches_web(self):
        from wildlocate.cli import main
        with patch('sys.argv', ['wildlocate']), patch('wildlocate.cli.cmd_gui') as gui:
            main()
            gui.assert_called_once()
        with patch('sys.argv', ['wildlocate', 'web', '--port', '8766', '--no-browser']), patch('wildlocate.web.server.serve') as serve:
            main()
            serve.assert_called_once_with(port=8766, open_browser=False)

# -----------------------------------------------------------------------------

# macOS launch regression
from wildlocate.cli import _configure_qt_runtime


@unittest.skipUnless(sys.platform == 'darwin', 'macOS plugin discovery')
class QtLaunchTests(unittest.TestCase):
    def test_hidden_plugin_is_made_discoverable(self):
        import PyQt6
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()
            platforms=root/'Qt6/plugins/platforms'
            platforms.mkdir(parents=True)
            plugin=platforms/'libqcocoa.dylib'
            plugin.write_bytes(b'test')
            for path in (root/'Qt6/plugins', platforms, plugin):
                os.chflags(path, path.stat().st_flags | stat.UF_HIDDEN)
            with patch.object(PyQt6, '__file__', str(root/'__init__.py')), patch.dict(os.environ):
                _configure_qt_runtime()
                self.assertEqual(os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'],str(platforms))
                for path in (root/'Qt6/plugins', platforms, plugin):
                    self.assertFalse(path.stat().st_flags & stat.UF_HIDDEN)
