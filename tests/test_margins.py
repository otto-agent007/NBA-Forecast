"""Game-level probability and source-provenance contracts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
import math
import unittest

try:
    margins = importlib.import_module('nba_forecast.margins')
except ModuleNotFoundError:
    margins = None


AS_OF = '2026-02-01T00:00:00Z'
MODEL_KEY = 'new-injury-and-shot-model'


def records(count=100):
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = []
    for i in range(count):
        start = origin + timedelta(hours=i)
        result.append(dict(game_id=f'old-{i}', game_time=start.isoformat(),
            known_at=(start + timedelta(hours=3)).isoformat(),
            home_team_id='H', away_team_id='A', team_minutes={'H': 240., 'A': 240.},
            team_stats={'H': {'pts': 100 + [-20, -10, 10, 20][i % 4]}, 'A': {'pts': 100}}))
    return result


def forecast_inputs(home_points=110., away_points=105.):
    game = dict(game_id='next', home_team_id='H', away_team_id='A',
        start_time='2026-02-02T00:00:00Z', season_type='Regular Season')
    snapshot = dict(as_of=AS_OF, games=[game], model_enhancements={
        'model_key': MODEL_KEY,
        'margin_inputs': margins.prepare_margin_inputs(records(), AS_OF, MODEL_KEY)})
    result = dict(model_key=MODEL_KEY, as_of=AS_OF, predictions=[
        dict(game_id='next', team_id=team, player_id=f'{team}{i}',
             status='projected', points_mean=points / 5, expected_minutes=48.)
        for team, points in [('H', home_points), ('A', away_points)] for i in range(5)])
    return snapshot, result


def small_distribution():
    return dict(game_id='next', home_team_id='H', away_team_id='A', model_key=MODEL_KEY,
        status='estimated', settlement='final_score_including_overtime',
        calibration_status='uncalibrated',
        supported_line_range={'minimum': -9, 'maximum': 9, 'exclusive': True},
        pmf=[{'margin': k, 'probability': p} for k, p in
             [(-8, .1), (-1, .2), (1, .3), (7, .15), (8, .25)]])


class MarginSourceTests(unittest.TestCase):
    def test_validated_prospective_correction_changes_only_future_saved_distribution(self):
        from nba_forecast.prospective_margin_calibration import METHOD as CORRECTION_METHOD, _fit_window
        snapshot,result=forecast_inputs()
        baseline=margins.build_margin_forecasts(snapshot,result)[0]
        rows=[]
        for i in range(112):
            game=datetime(2026,1,1,20,tzinfo=timezone.utc)+timedelta(days=i//8,minutes=15*(i%8))
            rows.append(dict(game_id=f'002260{i:04d}',model_key=MODEL_KEY,home_team_id='H',away_team_id='A',
                game_time=game.isoformat(),forecast_created_at=(game-timedelta(hours=1)).isoformat(),
                actual_known_at=(game+timedelta(hours=3)).isoformat(),base_mean_margin=0.,
                base_standard_deviation=16.,actual_home_margin=15 if i%2 else -7,
                forecast_sha256='a'*64,actuals_sha256='b'*64))
        cutoff='2026-01-25T00:00:00Z'
        correction=dict(schema_version=1,method=CORRECTION_METHOD,model_key=MODEL_KEY,
            window_index=0,training_cutoff=cutoff,evidence_samples=rows,**_fit_window(rows,MODEL_KEY,cutoff))
        self.assertEqual(correction['status'],'ready')
        snapshot['model_enhancements']['margin_calibration']=correction
        calibrated=margins.build_margin_forecasts(snapshot,result)[0]
        self.assertEqual(calibrated['status'],'estimated')
        self.assertEqual(calibrated['calibration_status'],'prospective_validated')
        self.assertAlmostEqual(calibrated['mean_margin'],baseline['mean_margin']+correction['location_bias'])
        self.assertAlmostEqual(calibrated['standard_deviation'],baseline['standard_deviation']*correction['scale_factor'])
        self.assertEqual(calibrated['uncalibrated_mean_margin'],baseline['mean_margin'])
        self.assertEqual(calibrated['uncalibrated_pmf'],baseline['pmf'])
        self.assertEqual(calibrated['uncalibrated_pmf_sha256'],baseline['pmf_sha256'])
        self.assertEqual(margins.probability_above(calibrated,'H',2.5)['calibration_status'],'prospective_validated')
        correction['model_key']='other-model'
        self.assertEqual(margins.build_margin_forecasts(snapshot,result)[0]['reason'],'invalid_margin_calibration')

    def test_prepared_prior_is_source_bound_and_never_residual_calibration(self):
        self.assertIsNotNone(margins, 'The game-margin probability module is missing')
        source = records()
        original = deepcopy(source)
        value = margins.prepare_margin_inputs(source, AS_OF, MODEL_KEY)
        self.assertEqual(value['status'], 'ready')
        self.assertEqual(value['sample_count'], 100)
        self.assertEqual(value['model_key'], MODEL_KEY)
        self.assertEqual(value['calibration_status'], 'uncalibrated')
        self.assertEqual(value['evidence_kind'], 'historical_paired_final_score_outcomes')
        self.assertEqual(len(value['source_sha256']), 64)
        self.assertEqual(value, margins.prepare_margin_inputs(list(reversed(source)), AS_OF, MODEL_KEY))
        self.assertEqual(source, original)
        changed = deepcopy(source)
        changed[0]['team_stats']['H']['pts'] += 1
        self.assertNotEqual(value['source_sha256'], margins.prepare_margin_inputs(changed, AS_OF, MODEL_KEY)['source_sha256'])
        json.dumps(value, allow_nan=False)

    def test_shared_pace_variation_does_not_become_independent_team_noise(self):
        original = records()
        shifted = deepcopy(original)
        for i, row in enumerate(shifted):
            row['team_stats']['H']['pts'] += i * 2
            row['team_stats']['A']['pts'] += i * 2
        baseline = margins.prepare_margin_inputs(original, AS_OF, MODEL_KEY)
        common = margins.prepare_margin_inputs(shifted, AS_OF, MODEL_KEY)
        self.assertAlmostEqual(baseline['standard_deviation'], common['standard_deviation'])
        self.assertGreater(common['paired_score_covariance'], 1000)
        self.assertLess(common['margin_variance'], common['home_score_variance'] + common['away_score_variance'])

    def test_final_overtime_scores_are_not_scaled_to_regulation(self):
        source = records()
        regular = margins.prepare_margin_inputs(source, AS_OF, MODEL_KEY)
        source[0]['team_minutes'] = {'H': 265., 'A': 265.}
        overtime = margins.prepare_margin_inputs(source, AS_OF, MODEL_KEY)
        self.assertAlmostEqual(overtime['standard_deviation'], regular['standard_deviation'])
        self.assertEqual(overtime['overtime_game_count'], 1)

    def test_future_known_and_duplicate_games_cannot_supply_support(self):
        source = records()
        future = deepcopy(source[0]); future['game_id'] = 'future'
        future['known_at'] = '2026-03-01T00:00:00Z'
        value = margins.prepare_margin_inputs(source + [future], AS_OF, MODEL_KEY)
        self.assertEqual(value['sample_count'], 100)
        self.assertEqual(value['exclusions']['future_known'], 1)
        duplicates = margins.prepare_margin_inputs(source + [source[0]], AS_OF, MODEL_KEY)
        self.assertEqual(duplicates['sample_count'], 99)
        self.assertEqual(duplicates['status'], 'unavailable')
        self.assertEqual(duplicates['exclusions']['duplicate_game'], 2)

    def test_invalid_final_identity_score_duration_and_chronology_are_excluded(self):
        alterations = [
            lambda r: r.update(away_team_id='H'),
            lambda r: r['team_stats']['H'].update(pts=100),
            lambda r: r['team_stats']['H'].update(pts=float('nan')),
            lambda r: r['team_stats']['H'].update(pts=True),
            lambda r: r['team_minutes'].update(H=241),
            lambda r: r.update(known_at=r['game_time']),
            lambda r: r.update(known_at='2026-01-02T00:00:00'),
        ]
        for alter in alterations:
            with self.subTest(alter=alter):
                source = records(); alter(source[0])
                value = margins.prepare_margin_inputs(source, AS_OF, MODEL_KEY)
                self.assertEqual(value['sample_count'], 99)
                self.assertEqual(value['status'], 'unavailable')

    def test_missing_source_and_degenerate_outcomes_have_no_invented_dispersion(self):
        self.assertEqual(margins.prepare_margin_inputs([], AS_OF, MODEL_KEY)['status'], 'unavailable')
        source = records()
        for row in source:
            row['team_stats']['H']['pts'] = 101
        value = margins.prepare_margin_inputs(source, AS_OF, MODEL_KEY)
        self.assertEqual(value['status'], 'unavailable')
        for key in ('', None, True):
            with self.assertRaises(ValueError): margins.prepare_margin_inputs(records(), AS_OF, key)


class MarginForecastTests(unittest.TestCase):
    def test_complete_forecast_produces_frozen_nonzero_normalized_pmf(self):
        snapshot, result = forecast_inputs()
        original = deepcopy((snapshot, result))
        value = margins.build_margin_forecasts(snapshot, result)[0]
        self.assertEqual(value['status'], 'estimated')
        # Evenly matched sources with no home edge: half the player sum, half a zero rating.
        self.assertEqual((value['player_sum_margin'], value['team_rating_margin']), (5., 0.))
        self.assertEqual(value['mean_margin'], 2.5)
        self.assertEqual(value['calibration_status'], 'uncalibrated')
        self.assertEqual(value['settlement'], 'final_score_including_overtime')
        self.assertEqual(value['sample_count'], 100)
        self.assertNotIn(0, [p['margin'] for p in value['pmf']])
        self.assertAlmostEqual(math.fsum(p['probability'] for p in value['pmf']), 1.)
        self.assertIsNone(value['joint_probability'])
        self.assertEqual(value, margins.build_margin_forecasts(snapshot, result)[0])
        self.assertEqual((snapshot, result), original)
        json.dumps(value, allow_nan=False)

    def test_reported_out_regulars_lower_their_teams_rating(self):
        snapshot, result = forecast_inputs()
        def out(pid, team, minutes, recent=True):
            return dict(game_id='next', team_id=team, player_id=pid, status='out', points_mean=0., expected_minutes=0.,
                        components=dict(absence=dict(team_games_played=20, minutes_per_game=minutes,
                                                     played_recent_team_game=recent, recent_team_games=10)))
        result['predictions'] += [out('Hstar', 'H', 30.), out('Hbench', 'H', 10.),   # bench: under 15 minutes
                                  out('Along', 'A', 34., recent=False),             # out longer than ten games
                                  dict(out('Aother', 'A', 30.), game_id='other')]   # another game's player
        value = margins.build_margin_forecasts(snapshot, result)[0]
        self.assertEqual(value['absent_regular_minutes'], {'home': 30., 'away': 0.})
        self.assertAlmostEqual(value['absence_rating_adjustment'], -margins.ABSENCE_RATING_PER_MINUTE * 30.)
        self.assertAlmostEqual(value['team_rating_margin'], -3.)
        self.assertAlmostEqual(value['mean_margin'], .5 * 5. + .5 * -3.)

    def test_home_away_means_mirror_the_entire_distribution(self):
        s1, r1 = forecast_inputs(110, 105)
        s2, r2 = forecast_inputs(105, 110)
        a = margins.build_margin_forecasts(s1, r1)[0]
        b = margins.build_margin_forecasts(s2, r2)[0]
        reverse = {p['margin']: p['probability'] for p in b['pmf']}
        for p in a['pmf']:
            self.assertAlmostEqual(p['probability'], reverse[-p['margin']], places=14)
        self.assertAlmostEqual(margins.probability_above(a, 'A', 7)['yes_probability'],
                               margins.probability_above(b, 'H', 7)['yes_probability'])

    def test_partial_or_missing_team_withholds_probabilities(self):
        for missing in ('minutes', 'team', 'unknown'):
            snapshot, result = forecast_inputs()
            if missing == 'minutes': result['predictions'][0]['expected_minutes'] = 47.9
            if missing == 'team': result['predictions'] = result['predictions'][:5]
            if missing == 'unknown': result['predictions'].append(dict(game_id='next', team_id='H',
                player_id='Q', status='needs_review', points_mean=None, expected_minutes=None))
            with self.subTest(missing=missing):
                value = margins.build_margin_forecasts(snapshot, result)[0]
                self.assertEqual(value['status'], 'unavailable')
                self.assertEqual(value['pmf'], [])
                self.assertIsNone(margins.probability_above(value, 'H', 7)['yes_probability'])

    def test_forged_saved_totals_do_not_override_player_coverage(self):
        snapshot, result = forecast_inputs()
        result['predictions'][0]['expected_minutes'] = 40.
        result['game_totals'] = [{'game_id': 'next', 'status': 'complete', 'teams': [
            {'team_id': 'H', 'status': 'complete', 'points_mean': 110.},
            {'team_id': 'A', 'status': 'complete', 'points_mean': 105.}]}]
        self.assertEqual(margins.build_margin_forecasts(snapshot, result)[0]['status'], 'unavailable')

    def test_no_player_sd_independence_or_legacy_interval_label_is_imported(self):
        snapshot, result = forecast_inputs()
        baseline = margins.build_margin_forecasts(snapshot, result)[0]
        for p in result['predictions']:
            p['points_sd'] = 9999.
            p['components'] = {'uncertainty': {'calibration': {'status': 'calibrated'}}}
        snapshot['model_enhancements']['calibration'] = {'status': 'ready', 'quantile': .1}
        value = margins.build_margin_forecasts(snapshot, result)[0]
        self.assertEqual(value['pmf'], baseline['pmf'])
        self.assertEqual(value['calibration_status'], 'uncalibrated')

    def test_missing_mismatched_tampered_and_future_inputs_are_unavailable(self):
        for defect in ('missing', 'model', 'tamper', 'future', 'late_game'):
            snapshot, result = forecast_inputs()
            if defect == 'missing': snapshot['model_enhancements'].pop('margin_inputs')
            if defect == 'model': result['model_key'] = 'different'
            if defect == 'tamper': snapshot['model_enhancements']['margin_inputs']['standard_deviation'] *= .1
            if defect == 'future': snapshot['model_enhancements']['margin_inputs'] = margins.prepare_margin_inputs(records(), '2026-02-03T00:00:00Z', MODEL_KEY)
            if defect == 'late_game': snapshot['games'][0]['start_time'] = '2026-01-31T00:00:00Z'
            with self.subTest(defect=defect):
                self.assertEqual(margins.build_margin_forecasts(snapshot, result)[0]['status'], 'unavailable')

    def test_extreme_location_does_not_collapse_to_a_certain_tail(self):
        snapshot, result = forecast_inputs(600, 5)
        value = margins.build_margin_forecasts(snapshot, result)[0]
        self.assertEqual(value['status'], 'unavailable')
        snapshot, result = forecast_inputs(110, 110)
        value = margins.build_margin_forecasts(snapshot, result)[0]
        for line in (-50, 0, 50):
            p = margins.probability_above(value, 'H', line)
            self.assertGreater(p['yes_probability'], 0)
            self.assertLess(p['yes_probability'], 1)
        endpoints = value['supported_line_range']
        for line in (endpoints['minimum'], endpoints['maximum'], 1000):
            self.assertEqual(margins.probability_above(value, 'H', line)['status'], 'unavailable')


class TeamRatingTests(unittest.TestCase):
    def test_current_margin_is_padded_with_a_regressed_prior_season(self):
        def game(time, home, away, hp, ap):
            return dict(game_time=time, home_team_id=home, away_team_id=away, home_points=hp, away_points=ap)
        samples = [game('2025-12-01T00:00:00Z', 'X', 'Y', 110, 100)] * 4  # last season: X +10, Y -10
        samples += [game('2026-10-22T00:00:00Z', 'Y', 'X', 104, 100)]     # this season: Y +4, X -4
        samples += [game('2023-12-01T00:00:00Z', 'X', 'Y', 150, 100)]     # two seasons ago: ignored
        ratings = margins.team_ratings(samples, '2026-10-24T00:00:00Z')
        prior = margins.RATING_PRIOR_SHRINK * 10
        k = margins.RATING_PRIOR_GAMES
        self.assertAlmostEqual(ratings['X']['rating'], (-4 + k * prior) / (1 + k))
        self.assertAlmostEqual(ratings['Y']['rating'], (4 - k * prior) / (1 + k))
        self.assertEqual((ratings['X']['games'], ratings['X']['prior']), (1, prior))

    def test_location_blends_only_with_both_ratings(self):
        ratings = dict(H=dict(rating=3.), A=dict(rating=-1.))
        location, rating = margins.blended_location(6., 2., ratings, 'H', 'A')
        self.assertEqual(rating, 6.)
        self.assertEqual(location, (1 - margins.RATING_WEIGHT) * 6. + margins.RATING_WEIGHT * 6.)
        self.assertEqual(margins.blended_location(6., 2., ratings, 'H', 'NEW'), (6., None))
        self.assertEqual(margins.blended_location(6., 2., ratings, 'H', 'A', -3.)[1], 3.)  # absences move the rating
        self.assertEqual(margins.blended_location(6., 2., ratings, 'H', 'NEW', -3.), (6., None))


class MarginEventTests(unittest.TestCase):
    def test_strict_integer_halfpoint_and_complement_equality_semantics(self):
        value = small_distribution()
        integer = margins.probability_above(value, 'H', 7)
        halfpoint = margins.probability_above(value, 'H', '7.5')
        self.assertAlmostEqual(integer['yes_probability'], .25)
        self.assertAlmostEqual(integer['no_probability'], .75)
        self.assertAlmostEqual(integer['equal_probability'], .15)
        self.assertAlmostEqual(halfpoint['yes_probability'], .25)
        self.assertEqual(halfpoint['equal_probability'], 0)
        self.assertEqual(integer['equality_result'], 'NO')

    def test_named_away_team_reverses_sign_and_final_win_probabilities_complement(self):
        value = small_distribution()
        home = margins.probability_above(value, 'H', 0)
        away = margins.probability_above(value, 'A', 0)
        self.assertAlmostEqual(home['yes_probability'], .7)
        self.assertAlmostEqual(away['yes_probability'], .3)
        self.assertEqual(home['equal_probability'], 0)
        self.assertAlmostEqual(home['yes_probability'] + away['yes_probability'], 1.)

    def test_probabilities_decrease_with_line_and_decimal_boundaries_are_exact(self):
        value = small_distribution()
        probabilities = [margins.probability_above(value, 'H', line)['yes_probability']
                         for line in (-8, -1, 0, 1, 6.5, 7, 7.5, 8)]
        self.assertEqual(probabilities, sorted(probabilities, reverse=True))
        self.assertAlmostEqual(margins.probability_above(value, 'H', '6.999999999999999999')['yes_probability'], .4)

    def test_invalid_line_identity_settlement_and_corrupt_pmf_fail_closed(self):
        for line in (float('nan'), float('inf'), True, '', 'NaN', None):
            with self.subTest(line=line), self.assertRaises(ValueError):
                margins.probability_above(small_distribution(), 'H', line)
        with self.assertRaises(ValueError): margins.probability_above(small_distribution(), 'X', 7)
        for defect in ('tie', 'duplicate', 'negative', 'sum', 'scope'):
            value = small_distribution()
            if defect == 'tie': value['pmf'][0]['margin'] = 0
            if defect == 'duplicate': value['pmf'][0]['margin'] = -1
            if defect == 'negative': value['pmf'][0]['probability'] = -.1
            if defect == 'sum': value['pmf'][0]['probability'] = .2
            if defect == 'scope': value['settlement'] = 'regulation'
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                margins.probability_above(value, 'H', 7)


if __name__ == '__main__':
    unittest.main()
