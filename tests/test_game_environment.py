import copy
import unittest
from nba_forecast.game_environment import build_environment, estimated_possessions


def record(gid='old',home='H',away='A',fga=90,pts=110,minutes=240):
    return dict(game_id=gid,game_time='2025-10-01T00:00:00Z',known_at='2025-10-02T00:00:00Z',home_team_id=home,away_team_id=away,team_minutes={home:minutes,away:minutes},team_stats={t:dict(pts=pts,fga=fga,fta=20,oreb=10,tov=12) for t in (home,away)})


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.game=dict(game_id='target',start_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
    def test_ot_is_normalized_and_missing_oreb_is_rejected(self):
        r=record(minutes=265)
        self.assertAlmostEqual(estimated_possessions(r),100.8*240/265)
        del r['team_stats']['H']['oreb']
        with self.assertRaises(ValueError):
            estimated_possessions(r)
    def test_live_cutoff_checks_known_time_and_game_time(self):
        a=record()
        b=record('future')
        b['known_at']='2025-12-01T00:00:00Z'
        c=record('future_game')
        c['game_time']='2025-12-01T00:00:00Z'
        out=build_environment([a,b,c],self.game,'2025-10-31T00:00:00Z',parameters={'min_games':1})
        self.assertEqual(out['audit']['eligible_games'],1)
        self.assertEqual(out['teams']['H']['pace_factor'],1)
    def test_research_reconstruction_requires_explicit_context(self):
        r=record()
        r['known_at']='2026-01-01T00:00:00Z'
        out=build_environment([r],self.game,'2025-10-31T00:00:00Z',context={'evidence_kind':'historical_reconstruction'},parameters={'min_games':1})
        self.assertEqual(out['audit']['eligible_games'],1)
        self.assertEqual(out['audit']['evidence_kind'],'historical_reconstruction')
    def test_pace_ratio_uses_own_baseline_and_defense_per_possession(self):
        records=[record('h','H','X',fga=110,pts=120),record('a','A','Y',fga=70,pts=100)]
        out=build_environment(records,self.game,'2025-10-31T00:00:00Z',parameters={'min_games':1,'shrinkage_games':0})
        self.assertLess(out['teams']['H']['pace_factor'],1)
        self.assertGreater(out['teams']['A']['pace_factor'],1)
        self.assertAlmostEqual(out['teams']['H']['expected_possessions'],100.8)
        self.assertGreater(out['teams']['H']['defense_factor'],1)
    def test_sparse_and_corrupt_records_are_explicit_neutral_fallbacks(self):
        r=record()
        del r['team_stats']['A']['oreb']
        out=build_environment([r],self.game,'2025-10-31T00:00:00Z')
        self.assertEqual(out['teams']['H']['pace_factor'],1)
        self.assertEqual(out['teams']['H']['defense_factor'],1)
        self.assertTrue(out['teams']['H']['audit']['fallback'])
        self.assertEqual(len(out['audit']['rejected_games']),1)

class EnvironmentIntegrityTests(unittest.TestCase):
    def test_missing_duration_and_duplicates_cannot_silently_bias_pace(self):
        g=dict(game_id='target',game_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
        r=record()
        del r['team_minutes']
        out=build_environment([r],g,'2025-10-31T00:00:00Z')
        self.assertEqual(out['audit']['eligible_games'],0)
        self.assertIsNone(out['teams']['H']['expected_possessions'])
        out=build_environment([record(),record()],g,'2025-10-31T00:00:00Z')
        self.assertEqual(out['audit']['eligible_games'],0)
        self.assertEqual(len(out['audit']['rejected_games']),2)

    def test_own_pace_factor_removes_existing_fast_tempo(self):
        g=dict(game_id='target',start_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
        out=build_environment([record('h','H','X',fga=110),record('a','A','Y',fga=70)],g,'2025-10-31T00:00:00Z',parameters={'min_games':1,'shrinkage_games':0})
        for value in out['teams'].values():
            self.assertAlmostEqual(value['reference_pace']*value['pace_factor'],value['expected_possessions'])

class EnvironmentRecencyTests(unittest.TestCase):
    def test_last_n_and_decay_are_order_independent_and_audited(self):
        g=dict(game_id='target',start_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
        early=record('early',fga=60)
        early['game_time']='2025-10-01T00:00:00Z'
        middle=record('middle',fga=80)
        middle['game_time']='2025-10-02T00:00:00Z'
        late=record('late',fga=100)
        late['game_time']='2025-10-03T00:00:00Z'
        out=build_environment([late,early,middle],g,'2025-10-31T00:00:00Z',parameters={'min_games':1,'max_team_games':2,'decay':.5,'shrinkage_games':0})
        self.assertEqual(out['teams']['H']['audit']['game_ids'],['middle','late'])
        self.assertAlmostEqual(out['teams']['H']['reference_pace'],(90.8*.5+110.8)/1.5)
        self.assertEqual(out['audit']['decay'],.5)
        self.assertEqual(out['audit']['max_team_games'],2)

class EnvironmentOffenseTests(unittest.TestCase):
    def test_own_offense_per100_is_audited_for_context_fitting(self):
        g=dict(game_id='target',game_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
        out=build_environment([record()],g,'2025-10-31T00:00:00Z',parameters={'min_games':1})
        self.assertAlmostEqual(out['teams']['H']['audit']['own_offense_per100'],110/100.8*100)

class EnvironmentMissingIdentityTests(unittest.TestCase):
    def test_missing_game_identity_is_rejected_without_crashing(self):
        g=dict(game_id='target',game_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
        r=record()
        del r['game_id']
        out=build_environment([r],g,'2025-10-31T00:00:00Z')
        self.assertEqual(out['audit']['eligible_games'],0)
        self.assertEqual(len(out['audit']['rejected_games']),1)

class EnvironmentShotVolumeTests(unittest.TestCase):
    def test_team_attempt_volume_uses_own_rate_and_matchup_possessions(self):
        g=dict(game_id='target',game_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
        records=[record('h','H','X',fga=110),record('a','A','Y',fga=70)]
        out=build_environment(records,g,'2025-10-31T00:00:00Z',parameters={'min_games':1,'shrinkage_games':0})
        home=out['teams']['H']
        self.assertAlmostEqual(home['expected_fga'],110*100.8/120.8)
        self.assertAlmostEqual(home['expected_fta'],20*100.8/120.8)
        self.assertAlmostEqual(home['expected_fga'],110*home['pace_factor'])

    def test_overtime_attempts_are_normalized_to_regulation_volume(self):
        g=dict(game_id='target',game_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
        out=build_environment([record(minutes=265)],g,'2025-10-31T00:00:00Z',parameters={'min_games':1})
        self.assertAlmostEqual(out['teams']['H']['expected_fga'],90*240/265)
        self.assertAlmostEqual(out['teams']['H']['expected_fta'],20*240/265)

    def test_no_own_history_has_no_invented_shot_volume(self):
        g=dict(game_id='target',game_time='2025-11-01T00:00:00Z',home_team_id='H',away_team_id='A')
        out=build_environment([],g,'2025-10-31T00:00:00Z')
        self.assertIsNone(out['teams']['H']['expected_fga'])
        self.assertIsNone(out['teams']['H']['expected_fta'])
