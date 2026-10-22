import unittest
from nba_forecast.profiles import build_profiles
from tests.test_store import inputs

class ProfilesTests(unittest.TestCase):
    def test_full_pool_and_late_facts_excluded(self):
        s,h,f=inputs();p=build_profiles(s,h)
        self.assertEqual(len(p['players']),len(s['roster']['players']))
        late=[dict(r,known_at='2099-01-01T00:00:00Z') for r in h]
        q=build_profiles(s,late)
        self.assertTrue(all(r['expected_minutes_baseline'] is None for r in q['players']))
    def test_out_vacancies_are_not_redistributed(self):
        s,h,f=inputs();player=s['roster']['players'][0];player['status']='out'
        p=build_profiles(s,h)
        self.assertTrue(any(t['out_player_ids'] for t in p['teams']))
        self.assertTrue(all(r['starter_role']=='unknown' for r in p['players']))
