"""Behavioral checks for scenario honesty, artifact readiness, and evidence."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from wildlocate.core.predict import habitat_insights, model_evidence, validate_lat_lon
from wildlocate.core.registry import ModelRecord, available_models, model_health


class DevelopmentModel:
    def predict_proba(self, frame):
        # An intentionally opposite response verifies that declines aren't hidden.
        score = .1 + frame['developed_fraction_250m'].to_numpy() * .5
        return np.column_stack([1 - score, score])


class HabitatWorkflowTests(unittest.TestCase):
    def test_scenarios_preserve_landcover_mass_and_include_negative_results(self):
        comparison = pd.DataFrame({'forest_fraction_250m': [.1, .3, .5],
                                   'developed_fraction_250m': [.6, .4, .2]})
        frame = comparison.iloc[[0]].copy()
        model = DevelopmentModel()
        scores = model.predict_proba(comparison)[:, 1]
        result = habitat_insights(model, frame, comparison, scores, float(scores[0]))
        self.assertEqual([s['fraction'] for s in result['scenarios']], [.1, .25, .5])
        for scenario in result['scenarios']:
            self.assertLess(scenario['delta'], 0)
            changes = scenario['changes']
            self.assertAlmostEqual(sum(c['before'] for c in changes), sum(c['after'] for c in changes))
        self.assertEqual(habitat_insights(model, frame, comparison, scores, float(scores[0]),
                                        include_scenarios=False)['scenarios'], [])
        json.dumps(result, allow_nan=False)

    def test_finite_coordinates_required(self):
        for latitude, longitude in [(float('nan'), -72), (42, float('inf'))]:
            with self.assertRaises(ValueError):
                validate_lat_lon(latitude, longitude)

    def test_unreadable_artifact_is_excluded_without_hiding_healthy_models(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'model.joblib').write_bytes(b'not a model')
            (root / 'metrics.json').write_text(json.dumps({'predictor_names': ['x']}))
            (root / 'features.csv').write_text('x\n1\n')
            broken = ModelRecord('broken', 'Broken', root / 'model.joblib', root / 'metrics.json', root / 'features.csv')
            self.assertFalse(model_health(broken)['ready'])
            healthy = ModelRecord('healthy', 'Healthy', root / 'other.joblib', root / 'metrics.json', root / 'features.csv')
            with patch('wildlocate.core.registry.list_models', return_value=[broken, healthy]), \
                 patch('wildlocate.core.registry.model_health', side_effect=lambda r: {'ready': r.id == 'healthy'}):
                self.assertEqual(list(available_models()), ['healthy'])

    def test_validation_reference_uses_actual_sampling_balance(self):
        evidence = model_evidence({'presence_count': 25, 'background_count': 75, 'selected_model': 'Test',
                                  'metrics_by_model': [{'model': 'Test', 'mean_pr_auc': .4}]})
        self.assertEqual(evidence['pr_reference'], .25)
        self.assertEqual(evidence['pr_auc'], .4)
