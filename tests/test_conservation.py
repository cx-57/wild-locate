"""Conservation counterfactuals and sampled-location screening."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from wildlocate.core import predict


class HabitatModel:
    """Transparent test model: forest helps, impervious cover lowers the score."""
    def predict_proba(self, frame):
        scores = .2 + .6 * frame['forest_fraction_250m'] - .002 * frame['mean_impervious_250m']
        return np.column_stack([1 - scores, scores])


class RestorationTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame([{'forest_fraction_250m': .2,
                                   'developed_fraction_250m': .6,
                                   'mean_impervious_250m': 50.}])
        self.model = HabitatModel()
        self.score = .22
        self.comparison_scores = np.array([.1, .2, .3, .4, .5])

    def test_shared_search_preserves_existing_point_scenarios(self):
        before = self.frame.copy(deep=True)
        result = predict.restoration_scenarios(self.model, self.frame, self.comparison_scores, self.score)
        self.assertEqual(result['scenarios_tested'], 6)
        self.assertEqual(result['baseline_percentile'], 40)
        forest, impervious = result['scenarios']
        self.assertEqual(forest['title'], 'Replace 50% of developed cover with forest')
        self.assertAlmostEqual(forest['score'], .4)
        self.assertAlmostEqual(forest['delta'], .18)
        self.assertEqual(forest['changes'][0], {'feature': 'developed_fraction_250m', 'before': .6, 'after': .3})
        self.assertEqual(forest['changes'][1], {'feature': 'forest_fraction_250m', 'before': .2, 'after': .5})
        self.assertEqual(impervious['title'], 'Reduce impervious surface by 50%')
        self.assertAlmostEqual(impervious['score'], .27)
        insights = predict.habitat_insights(self.model, self.frame, self.frame, self.comparison_scores, self.score)
        for key in result:
            self.assertEqual(insights[key], result[key])
        pd.testing.assert_frame_equal(before, self.frame)

    def test_unsupported_features_do_not_invent_a_scenario(self):
        result = predict.restoration_scenarios(self.model, pd.DataFrame([{'slope': 3.}]), self.comparison_scores, .2)
        self.assertEqual(result['scenarios'], [])
        self.assertEqual(result['scenarios_tested'], 0)

    def test_no_positive_gain_keeps_existing_threshold(self):
        with patch.object(self.model, 'predict_proba', return_value=np.array([[.78, .2205]])):
            result = predict.restoration_scenarios(self.model, self.frame, self.comparison_scores, self.score)
        self.assertEqual(result['scenarios'], [])
        self.assertEqual(result['scenarios_tested'], 6)

    def test_nonfinite_scenario_is_rejected(self):
        with patch.object(self.model, 'predict_proba', return_value=np.array([[0., np.nan]])):
            with self.assertRaisesRegex(ValueError, 'Non-finite scenario'):
                predict.restoration_scenarios(self.model, self.frame, self.comparison_scores, self.score)


class RegionalRestorationTests(RestorationTests):
    def area(self, extraction=None):
        record = SimpleNamespace(species='Bobcat')
        self.enterContext(patch.object(predict, 'resolve_model', return_value=record))
        self.enterContext(patch.object(predict, 'load_model_and_metadata', return_value=(self.model, {
            'predictor_names': list(self.frame.columns), 'selected_model': 'Test', 'presence_count': 25})))
        self.enterContext(patch.object(predict, 'load_comparison_scores', return_value=(self.comparison_scores, self.frame)))
        extractor = self.enterContext(patch.object(predict, 'extract_features', side_effect=extraction,
                                                   return_value=self.frame.iloc[0].to_dict()))
        return extractor

    def test_area_reuses_extraction_and_includes_counterfactual_and_drivers(self):
        extractor = self.area()
        result = predict.predict_area('Bobcat', 42., -72., 10)
        self.assertEqual(extractor.call_count, 81)
        for point in result['points']:
            scenario = point['restoration']
            self.assertEqual(scenario['current_percentile'], point['percentile'])
            self.assertGreater(scenario['percentile_delta'], 0)
            self.assertEqual(scenario['percentile_delta'], scenario['projected_percentile'] - point['percentile'])
            self.assertIn('forest', scenario['description'])
            self.assertEqual(len(scenario['changes']), 2)
            self.assertTrue(point['insights']['influences'])
            self.assertEqual(point['features'], self.frame.iloc[0].to_dict())
        json.dumps(result, allow_nan=False)

    def test_unavailable_points_have_no_restoration(self):
        def extract(lat, lon):
            if lat > 42:
                raise ValueError('Requested coordinate cannot be evaluated: outside coverage')
            return self.frame.iloc[0].to_dict()
        self.area(extract)
        result = predict.predict_area('Bobcat', 42., -72., 10)
        missing = [p for p in result['points'] if p['status'] == 'unavailable']
        self.assertTrue(missing)
        self.assertTrue(all('restoration' not in p for p in missing))

    def test_failed_insights_preserve_scores_and_distinguish_failure(self):
        self.area()
        with patch.object(predict, 'habitat_insights', side_effect=ValueError('scenario failed')), self.assertLogs(predict.__name__):
            result = predict.predict_area('Bobcat', 42., -72., 10)
        self.assertEqual(result['evaluated_points'], 81)
        self.assertIsNone(result['points'][0]['restoration'])
        self.assertIn('error', result['points'][0]['insights'])

    def test_best_scenario_uses_percentile_gain_then_score(self):
        self.area()
        scenarios = [
            {'title': 'A', 'score': .5, 'delta': .28, 'percentile': 60, 'changes': []},
            {'title': 'B', 'score': .6, 'delta': .38, 'percentile': 80, 'changes': []},
        ]
        with patch.object(predict, 'habitat_insights', return_value={'influences': [], 'scenarios': scenarios}):
            result = predict.predict_area('Bobcat', 42., -72., 10)
        self.assertEqual(result['points'][0]['restoration']['description'], 'B')


class ConservationSummaryTests(unittest.TestCase):
    def point(self, percentile, gain=None):
        return {'latitude': 42., 'longitude': -72., 'status': 'ok',
                'score': percentile / 100, 'percentile': percentile,
                'category': predict.category_for_percentile(percentile),
                'restoration': None if gain is None else {
                    'current_percentile': percentile, 'projected_percentile': percentile + gain,
                    'percentile_delta': gain, 'description': 'Model scenario', 'changes': []}}

    def test_distribution_and_rankings_use_only_available_points(self):
        points = [self.point(80, 2), {'status': 'unavailable'}, self.point(20, 40),
                  self.point(95, 0), self.point(70, 20), self.point(80, 10)]
        summary = predict.summarize_conservation(points)
        self.assertEqual(summary['evaluated_points'], 5)
        self.assertEqual(summary['habitat_distribution']['Very High']['count'], 3)
        self.assertEqual(summary['habitat_distribution']['Very High']['percentage'], 60.)
        self.assertEqual([p['point_index'] for p in summary['protection_candidates']], [3, 0, 5, 4])
        self.assertEqual([p['point_index'] for p in summary['restoration_candidates']], [2, 4, 5, 0])
        self.assertNotIn('conservation_score', summary)
        self.assertNotIn('point_index', points[0])

    def test_rankings_limit_to_five_and_break_ties_by_sample_order(self):
        summary = predict.summarize_conservation([self.point(80, 10) for _ in range(8)])
        for key in ('protection_candidates', 'restoration_candidates'):
            self.assertEqual([p['point_index'] for p in summary[key]], list(range(5)))

    def test_empty_and_unavailable_return_zero_counts_not_fabricated_candidates(self):
        for points in ([], [{'status': 'unavailable'}]):
            summary = predict.summarize_conservation(points)
            self.assertEqual(summary['evaluated_points'], 0)
            self.assertEqual(summary['protection_candidates'], [])
            self.assertEqual(summary['restoration_candidates'], [])
            self.assertTrue(all(v == {'count': 0, 'percentage': 0.} for v in summary['habitat_distribution'].values()))
            json.dumps(summary, allow_nan=False)

    def test_low_suitability_and_no_percentile_gain_are_not_candidates(self):
        summary = predict.summarize_conservation([self.point(10, 0), self.point(30), self.point(40, -10)])
        self.assertEqual(summary['protection_candidates'], [])
        self.assertEqual(summary['restoration_candidates'], [])

    def test_area_result_includes_conservation(self):
        with patch.object(predict, 'resolve_model', return_value=SimpleNamespace(species='Bobcat')), \
             patch.object(predict, 'load_model_and_metadata', return_value=(HabitatModel(), {'predictor_names': ['forest_fraction_250m']})), \
             patch.object(predict, 'load_comparison_scores', return_value=(np.array([.1]), pd.DataFrame())), \
             patch.object(predict, 'extract_features', return_value={'forest_fraction_250m': np.nan}):
            result = predict.predict_area('Bobcat', 42, -72, 10)
        self.assertEqual(result['conservation']['evaluated_points'], 0)
        self.assertEqual(result['conservation']['restoration_candidates'], [])
        limitations = ' '.join(result['limitations'])
        for phrase in ('not presence probability', '81 points', 'not continuous habitat coverage', 'not causal predictions'):
            self.assertIn(phrase, limitations)


if __name__ == '__main__':
    unittest.main()
