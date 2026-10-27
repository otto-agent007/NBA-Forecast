"""Injury allocation conserves evidence and leaves unsupported gaps explicit."""
from copy import deepcopy
import unittest

from tests.test_model import current, player, row

try:
    from nba_forecast.rotation_allocation import redistribute
except ModuleNotFoundError:
    redistribute = None


CUTOFF = '2026-01-10T12:00:00Z'


def inputs(minutes=20, fga=.4, fg3a=.1, fta=.1, tov=.05, **kwargs):
    return dict(minutes=minutes, rates=dict(fga=fga, fg3a=fg3a, fta=fta, tov=tov),
                minute_cap=48, locked_rates=False, **kwargs)


def profile(pid='out', minutes=20, fga=.5, fg3a=.2, fta=.2, tov=.05):
    return dict(player_id=pid, team_id='A', expected_minutes_baseline=minutes,
                role_minutes_evidence=dict(per_minute_rates=dict(fga=fga, fg3a=fg3a, fta=fta, tov=tov)))


def evidence(pid='p', **kwargs):
    defaults=dict(pid=pid, minutes=30, fga=18, fg3a=4, fta=6, tov=3)
    defaults.update(kwargs)
    return [current(i, **defaults) for i in range(3)]


class RedistributionTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(redistribute), 'redistribute must be implemented')
        self.players=[player(), player('out', status='out')]
        self.profiles=[profile()]
        self.history=evidence()+evidence('out', minutes=20, fga=10, fg3a=4, fta=4, tov=1)

    def run_allocation(self, working=None):
        return redistribute(self.players, self.profiles, working or {'p':inputs()}, self.history, CUTOFF)

    def test_a_replay_names_its_season_instead_of_the_clocks(self):
        live, _ = self.run_allocation()
        # A replay runs years later: by the clock these 2025-26 games are two seasons old and unused.
        later = '2028-01-10T12:00:00Z'
        stale, _ = redistribute(self.players, self.profiles, {'p': inputs()}, self.history, later)
        named, _ = redistribute(self.players, self.profiles, {'p': inputs()}, self.history, later, season='2025-26')
        self.assertGreater(live['p']['minutes'], 20)
        self.assertEqual(stale['p']['minutes'], 20)
        self.assertEqual(named, live)

    def test_extra_minutes_attempts_are_subtracted_before_rate_allocation(self):
        adjusted, audit=self.run_allocation()
        self.assertEqual(adjusted['p']['minutes'], 30)
        self.assertAlmostEqual(adjusted['p']['rates']['fga'], .6)
        team=audit['A']
        self.assertAlmostEqual(team['vacated_attempts']['fga'], 10)
        self.assertAlmostEqual(team['extra_minutes_attempts']['fga'], 4)
        self.assertAlmostEqual(team['rate_added_attempts']['fga'], 6)
        self.assertAlmostEqual(team['unallocated_attempts']['fga'], 0)
        self.assertEqual(team['unallocated_vacancy_minutes'], 10)
        self.assertEqual(team['recipients']['p']['history_game_ids'], ['g100', 'g101', 'g102'])
        self.assertTrue(team['out_players'][0]['availability_source']['status_source_url'])
        self.assertTrue(team['recipients']['p']['history_sources'][0]['known_at'])

    def test_attempt_budget_prevents_counting_the_same_vacancy_twice(self):
        self.profiles=[profile(minutes=10, fga=.5, fg3a=.2, fta=.2)]
        adjusted, audit=self.run_allocation({'p':inputs(fga=.5)})
        self.assertEqual(adjusted['p']['minutes'], 30)
        self.assertAlmostEqual(adjusted['p']['rates']['fga'], .5)
        self.assertAlmostEqual(audit['A']['rate_added_attempts']['fga'], 0)

    def test_shot_mix_can_change_without_blocking_supported_minutes(self):
        self.profiles=[profile(minutes=10, fga=.5, fg3a=0, fta=.2, tov=0)]
        adjusted, audit=self.run_allocation({'p':inputs(fga=.5, fg3a=.1)})
        self.assertEqual(adjusted['p']['minutes'], 30)
        self.assertGreater(audit['A']['attempt_mix_change']['fg3a'], 0)
        self.assertLessEqual(adjusted['p']['rates']['fg3a'], adjusted['p']['rates']['fga'])

    def test_multiple_outs_obey_team_and_player_caps_without_reducing_core(self):
        self.players=[player(str(i)) for i in range(5)]+[player('out', status='out'), player('out2', status='out')]
        self.profiles=[profile(), profile('out2')]
        self.history=evidence('out')+evidence('out2')
        working={}
        for i in range(5):
            self.history+=evidence(str(i), minutes=48, fga=12, fg3a=4, fta=6)
            working[str(i)]=inputs(minutes=46, fga=.2, fg3a=.05, fta=.05)
        adjusted, audit=self.run_allocation(working)
        self.assertAlmostEqual(sum(p['minutes'] for p in adjusted.values()), 240)
        self.assertTrue(all(46 <= p['minutes'] <= 48 for p in adjusted.values()))
        self.assertEqual(audit['A']['vacated_minutes'], 40)
        self.assertAlmostEqual(audit['A']['unallocated_vacancy_minutes'], 30)

    def test_manual_caps_and_locks_take_precedence(self):
        first=inputs(locked_minutes=True)
        first['locked_rates']=True
        self.assertEqual(self.run_allocation({'p':first})[0]['p'], first)
        capped=inputs()
        capped['minute_cap']=25
        capped['locked_rates']=True
        adjusted, audit=self.run_allocation({'p':capped})
        self.assertEqual(adjusted['p']['minutes'], 25)
        self.assertEqual(adjusted['p']['rates'], capped['rates'])
        self.assertEqual(audit['A']['recipients']['p']['minute_cap'], 25)

    def test_unknown_questionable_and_cold_start_never_receive_or_create_vacancy(self):
        self.players=[player(status='unknown'), player('q', status='questionable'), player('new'), player('out', status='unknown')]
        working={'p':inputs(), 'q':inputs(), 'new':inputs()}
        adjusted, audit=self.run_allocation(working)
        self.assertEqual(adjusted, working)
        self.assertEqual(audit['A']['vacated_minutes'], 0)
        self.assertEqual(audit['A']['out_player_ids'], [])
        self.assertEqual(set(adjusted), set(working))

    def test_no_confirmed_out_leaves_supported_team_gap_unfilled(self):
        self.players[1]['status']='questionable'
        adjusted, audit=self.run_allocation()
        self.assertEqual(adjusted['p'], inputs())
        self.assertEqual(audit['A']['unfilled_team_minutes'], 220)

    def test_unsourced_or_future_known_out_is_not_a_vacancy(self):
        for change in ({'status_source_url':None}, {'status_observed_at':'2099-01-01T00:00:00Z'},
                       {'status_published_at':'2099-01-01T00:00:00Z'}):
            with self.subTest(change=change):
                self.players[1]=dict(player('out', status='out'), **change)
                adjusted, audit=self.run_allocation()
                self.assertEqual(adjusted['p'], inputs())
                self.assertEqual(audit['A']['vacated_minutes'], 0)
                self.assertEqual(audit['A']['excluded_out_players'][0]['player_id'], 'out')

    def test_late_known_future_and_wrong_season_rows_cannot_expand_headroom(self):
        before=self.run_allocation()[0]
        poison=current(3, pid='p', minutes=48, fga=48, fg3a=48, fta=48, tov=48)
        additions=[dict(poison, game_id='late', known_at='2099-01-01T00:00:00Z'),
                   dict(poison, game_id='future', game_time='2099-01-01T00:00:00Z'),
                   dict(poison, game_id='preseason', season_type='Preseason'),
                   dict(poison, game_id='ancient', season='2023-24'),
                   dict(poison, game_id='next', season='2026-27'),
                   dict(poison, game_id='exact', known_at=CUTOFF)]
        self.history+=additions
        after, audit=self.run_allocation()
        self.assertEqual(before, after)
        self.assertEqual(audit['A']['recipients']['p']['history_game_ids'], ['g100', 'g101', 'g102'])

    def test_current_team_evidence_dominates_a_traded_players_old_role(self):
        self.history=evidence(minutes=24, fga=12, fg3a=3, fta=3)+evidence('out')
        self.history+=evidence(team='OLD', minutes=48, fga=40, fg3a=20, fta=20)
        adjusted, audit=self.run_allocation()
        self.assertEqual(adjusted['p']['minutes'], 24)
        self.assertTrue(all(r['team_id']=='A' for r in audit['A']['recipients']['p']['history_sources']))

    def test_unobserved_new_team_role_does_not_inherit_old_team_headroom(self):
        self.history=evidence(team='OLD', minutes=48)+evidence('out')
        adjusted, audit=self.run_allocation()
        self.assertEqual(adjusted['p'], inputs())
        self.assertEqual(audit['A']['recipients']['p']['support'], 'new_team_role_unobserved')

    def test_established_prior_role_can_support_early_season_allocation(self):
        self.history=[row(i, minutes=30, fga=15, fg3a=3, fta=3) for i in range(3)]+evidence('out')
        adjusted, audit=self.run_allocation()
        self.assertEqual(adjusted['p']['minutes'], 30)
        self.assertEqual(audit['A']['recipients']['p']['support'], 'established_prior_current_team')

    def test_current_team_dnp_cannot_revive_stale_prior_headroom(self):
        self.history=[row(i, minutes=40) for i in range(3)]+evidence('out')
        self.history+=[current(pid='p', minutes=0, status='dnp', fga=0, fg3a=0, fta=0, tov=0)]
        adjusted, _=self.run_allocation()
        self.assertEqual(adjusted['p'], inputs())

    def test_exhausted_headroom_discloses_leftovers_without_forcefill(self):
        adjusted, audit=self.run_allocation({'p':inputs(minutes=30, fga=.6, fg3a=4/30, fta=.2, tov=.1)})
        self.assertEqual(adjusted['p']['minutes'], 30)
        self.assertAlmostEqual(audit['A']['unallocated_vacancy_minutes'], 20)
        self.assertAlmostEqual(audit['A']['unallocated_attempts']['fga'], 10)

    def test_caller_inputs_and_original_source_timestamps_are_preserved(self):
        working={'p':inputs()}
        before=deepcopy((self.players, self.profiles, working, self.history))
        adjusted, _=self.run_allocation(working)
        self.assertEqual((self.players, self.profiles, working, self.history), before)
        self.assertIsNot(adjusted, working)

    def test_explicit_and_regulation_caps_apply_even_to_an_oversized_input(self):
        for minute_cap, expected in ((15, 15), (60, 48)):
            with self.subTest(minute_cap=minute_cap):
                item=inputs(minutes=55, locked_minutes=True)
                item['minute_cap']=minute_cap
                adjusted, audit=self.run_allocation({'p':item})
                self.assertEqual(adjusted['p']['minutes'], expected)
                self.assertEqual(audit['A']['input_cap_corrections'][0]['player_id'], 'p')

    def test_fractional_team_allocation_never_exceeds_exact_240(self):
        self.players=[player(str(i)) for i in range(7)]+[player('out', status='out')]
        self.history=evidence('out')
        working={}
        for i in range(7):
            self.history+=evidence(str(i), minutes=40)
            working[str(i)]=inputs(minutes=33.9, fga=.2, fg3a=.05, fta=.05, tov=.01)
        adjusted, audit=self.run_allocation(working)
        self.assertLessEqual(sum(p['minutes'] for p in adjusted.values()), 240)
        self.assertAlmostEqual(audit['A']['allocated_minutes'], 2.7)

    def test_sparse_prior_role_leaves_vacancy_unresolved(self):
        self.history=[row(minutes=40)]+evidence('out')
        adjusted, audit=self.run_allocation()
        self.assertEqual(adjusted['p'], inputs())
        self.assertEqual(audit['A']['unallocated_vacancy_minutes'], 20)

    def test_no_out_free_throw_attempts_blocks_only_recipients_that_require_them(self):
        self.players+=[player('no_fts')]
        self.history+=evidence('no_fts', fta=0)
        self.profiles=[profile(minutes=10, fga=.5, fta=0)]
        adjusted, audit=self.run_allocation({'p':inputs(), 'no_fts':inputs(fta=0)})
        self.assertEqual(adjusted['p']['minutes'], 20)
        self.assertEqual(adjusted['no_fts']['minutes'], 30)
        self.assertEqual(audit['A']['adjusted_attempts']['fta'], 2)

    def test_a_different_teams_vacancy_cannot_raise_minutes_or_rates(self):
        self.players[1]['team_id']='B'
        self.profiles[0]['team_id']='B'
        self.history=evidence()+evidence('out', team='B')
        adjusted, audit=self.run_allocation()
        self.assertEqual(adjusted['p'], inputs())
        self.assertEqual(audit['A']['vacated_minutes'], 0)
        self.assertEqual(audit['B']['allocated_minutes'], 0)

    def test_future_known_healthy_status_cannot_receive_extra_production(self):
        self.players[0]['status_observed_at']='2099-01-01T00:00:00Z'
        adjusted, audit=self.run_allocation()
        self.assertEqual(adjusted['p'], inputs())
        self.assertEqual(audit['A']['recipients']['p']['support'], 'availability_unsourced_or_future_known')


if __name__ == '__main__':
    unittest.main()
