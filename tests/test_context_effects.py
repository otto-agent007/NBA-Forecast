import math
import unittest
from nba_forecast.context_effects import fit_context, context_adjustment, context_features


class ContextEffectsTests(unittest.TestCase):
    def samples(self):
        rows=[]
        for i in range(240):
            features=dict(back_to_back=float(i%2),extra_rest=float((i//2)%3),
                three_in_four=float((i//6)%2),eastward_zones=float((i//12)%3),
                westward_zones=float((i//36)%3),away=float((i//108)%2))
            target=-.03*features['back_to_back']+.01*features['extra_rest']-.005*features['eastward_zones']
            rows.append(dict(game_id=f'g{i}',game_time=f'2026-01-{i%28+1:02d}T00:00:00Z',
                known_at='2026-10-23T20:00:00Z',baseline_points=110.,actual_points=110*math.exp(target),features=features))
        return rows

    def test_effects_are_fitted_and_keep_future_known_outcomes_out(self):
        rows=self.samples()
        future=dict(rows[0],game_id='future',known_at='2027-01-01T00:00:00Z',actual_points=1.)
        a=fit_context(rows,as_of='2026-10-23T21:00:00Z',minimum_games=100,ridge=.001)
        b=fit_context(rows+[future],as_of='2026-10-23T21:00:00Z',minimum_games=100,ridge=.001)
        self.assertEqual(a['coefficients'],b['coefficients'])
        self.assertAlmostEqual(a['coefficients']['back_to_back'],-.03,places=4)
        context=dict(back_to_back=True,rest_days=0,three_in_four=False,timezone_shift_hours=0)
        effect=context_adjustment(a,context,home=True,as_of='2026-10-23T22:00:00Z')
        self.assertLess(effect['factor'],1.)
        self.assertGreater(effect['factor'],.95)

    def test_missing_context_and_future_or_sparse_fit_are_neutral(self):
        a=fit_context(self.samples()[:5],as_of='2026-10-23T21:00:00Z')
        self.assertEqual(context_adjustment(a,{},home=True,as_of='2026-10-23T22:00:00Z')['factor'],1.)
        a=fit_context(self.samples(),as_of='2026-10-23T21:00:00Z')
        self.assertEqual(context_adjustment(a,{},home=True,as_of='2026-10-23T20:00:00Z')['factor'],1.)
        features,missing=context_features({},home=True)
        self.assertIn('rest_days',missing)
        self.assertEqual(features['eastward_zones'],0.)

    def test_effect_is_bounded_and_unknown_travel_is_disclosed(self):
        a=fit_context(self.samples(),as_of='2026-10-23T21:00:00Z')
        a['coefficients']['eastward_zones']=100
        effect=context_adjustment(a,{'rest_days':0,'timezone_shift_hours':3},home=True,as_of='2026-10-23T22:00:00Z')
        self.assertLessEqual(effect['factor'],1.1)
