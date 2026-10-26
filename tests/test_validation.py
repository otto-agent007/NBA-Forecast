import copy
import unittest

from nba_forecast.validation import validate_history, validate_inputs


def row(**updates):
    result = dict(game_id='0022500001', season='2025-26', season_type='Regular Season',
                  game_time='2025-10-21T23:00:00Z', known_at='2025-10-22T03:00:00Z',
                  source_url='https://www.nba.com/game/test', player_id='p1', name='Test',
                  team_id='a', opponent_id='b', minutes=30, fgm=5, fga=10,
                  fg3m=1, fg3a=3, ftm=2, fta=2, tov=1, pts=13, status='played')
    result.update(updates)
    return result


def snapshot():
    return dict(schema_version=1, season='2026-27', as_of='2026-10-21T18:00:00Z',
                roster=dict(observed_at='2026-10-21T17:00:00Z',
                            source_url='https://www.nba.com/players', complete=True,
                            team_ids=['a', 'b'], players=[
                                dict(player_id='p1', name='Test', team_id='a', status='available',
                                     status_source_url='https://www.nba.com/news/injuries',
                                     status_observed_at='2026-10-21T17:00:00Z'),
                                dict(player_id='p2', name='Cold', team_id='b', status='unknown')]),
                games=[dict(game_id='0022600001', start_time='2026-10-22T01:00:00Z',
                            season_type='Regular Season', home_team_id='a', away_team_id='b')])


class ValidationTests(unittest.TestCase):
    def test_valid_subset_and_history(self):
        validate_inputs(snapshot(), [row()], scope='subset')

    def test_full_league_requires_all_teams(self):
        with self.assertRaises(ValueError):
            validate_inputs(snapshot(), [row()])

    def test_preseason_future_known_duplicate_and_future_season_rejected(self):
        for history in ([row(season_type='Pre Season')], [row(known_at='2026-10-22T02:00:00Z')],
                        [row(), row()], [row(season='2027-28')]):
            with self.subTest(history=history), self.assertRaises(ValueError):
                validate_inputs(snapshot(), history, scope='subset')

    def test_invalid_stats_and_naive_timestamps(self):
        for change in (dict(fgm=11), dict(pts=20), dict(minutes=float('nan')),
                       dict(fta=-1), dict(fga=True), dict(game_time='2025-10-21T23:00:00'),
                       dict(status='dnp'), dict(fg3m=5, fg3a=6, fgm=5, fga=6, pts=13),
                       dict(source_url='file:///secret')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_history([row(**change)])

    def test_zero_minute_dnp_and_zero_minute_appearance_distinct(self):
        zero = dict(minutes=0, fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, tov=0, pts=0)
        validate_history([row(status='dnp', **zero)])
        validate_history([row(status='played', **zero)])

    def test_full_box_optional_counts_signed_plus_minus_and_rebound_identity(self):
        validate_history([row(reb=7, oreb=2, dreb=5, pf=3, plus_minus=-12)])
        validate_history([row(reb=None, oreb=None, dreb=5, pf=None, plus_minus=None)])
        for change in (dict(oreb=-1), dict(dreb=True), dict(pf=1.5),
                       dict(plus_minus=True), dict(plus_minus=1.2),
                       dict(reb=7, oreb=2, dreb=4), dict(reb=2, oreb=3)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_history([row(**change)])

    def test_percentages_must_match_counts_when_present(self):
        validate_history([row(fg_pct=.5, fg3_pct=1/3, ft_pct=1)])
        for change in (dict(fg_pct=50), dict(fg_pct=.51), dict(fg3_pct=float('nan')),
                       dict(fta=0, ftm=0, pts=11, ft_pct=0)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_history([row(**change)])

    def test_nonzero_optional_dnp_fields_are_rejected(self):
        zero = dict(minutes=0, fgm=0, fga=0, fg3m=0, fg3a=0, ftm=0, fta=0, tov=0, pts=0)
        for change in (dict(oreb=1), dict(dreb=1), dict(pf=1), dict(plus_minus=-1)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_history([row(status='dnp', **zero, **change)])

    def test_roster_freshness_duplicates_unknown_teams(self):
        for mutate in (
            lambda s: s['roster'].update(observed_at='2026-10-18T17:00:00Z'),
            lambda s: s['roster'].update(complete=False),
            lambda s: s['roster']['players'].append(copy.deepcopy(s['roster']['players'][0])),
            lambda s: s['roster']['players'][0].update(team_id='unknown'),
            lambda s: s['games'][0].update(start_time=s['as_of']),
        ):
            s = snapshot()
            mutate(s)
            with self.assertRaises(ValueError):
                validate_inputs(s, [row()], scope='subset')

    def test_role_evidence_must_precede_cutoff_and_target_player_game(self):
        s = snapshot()
        adjustment = dict(player_id='p1', game_id='0022600001',
                          observed_at='2026-10-21T17:00:00Z',
                          source_url='https://www.nba.com/news/test',
                          reason='Confirmed rotation change', reviewer='analyst',
                          minutes_delta=3, opportunity_multiplier=1.1)
        s['role_adjustments'] = [adjustment]
        validate_inputs(s, [row()], scope='subset')
        for change in (dict(player_id='missing'), dict(minutes_delta=99),
                       dict(observed_at='2026-10-21T19:00:00Z'), dict(game_id='missing')):
            s['role_adjustments'] = [{**adjustment, **change}]
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_inputs(s, [row()], scope='subset')

    def test_status_requires_fresh_independent_evidence(self):
        for change in (dict(status_source_url=''),
                       dict(status_observed_at='2026-10-19T17:00:00Z'),
                       dict(status_observed_at='2026-10-22T17:00:00Z')):
            s = snapshot()
            s['roster']['players'][0].update(change)
            with self.assertRaises(ValueError):
                validate_inputs(s, [row()], scope='subset')


if __name__ == '__main__':
    unittest.main()
