"""The secondary feed may verify a cached NBA game, never invent its ID."""
import unittest

from nba_forecast.schedule import normalize_season, verify_secondary_schedule
from tests.test_schedule import payload, game

NOW = '2026-10-24T16:00:00Z'


def espn_event(date='2026-10-24T22:00Z', status='STATUS_SCHEDULED',
               home='NY', away='BOS', identity='401909853'):
    return dict(id=identity,date=date,season=dict(year=2027,type=2,slug='regular-season'),
                status=dict(type=dict(name=status,state='pre',completed=False)),
                competitions=[dict(competitors=[
                    dict(homeAway='home',team=dict(abbreviation=home)),
                    dict(homeAway='away',team=dict(abbreviation=away))])])


def observation(events):
    return dict(source_url='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates=20261024',
                observed_at=NOW,source_sha256='a'*64,payload=dict(events=events))


def cached():
    value=normalize_season(payload([game(start='2026-10-24T22:00:00Z')]),'2026-10-22T03:00:00Z')
    value['version']='b'*64
    return value


class SecondaryScheduleTests(unittest.TestCase):
    def test_one_espn_event_cannot_relabel_earlier_same_pair_nba_game(self):
        source=cached()
        later=game('0022600002',start='2026-10-25T22:00:00Z')
        second=normalize_season(payload([later]),'2026-10-22T03:00:00Z')['games'][0]
        source['games'].append(second)
        result=verify_secondary_schedule(source,[observation([espn_event(date='2026-10-25T22:00Z')])],NOW)
        self.assertEqual(result['crosscheck']['verified_game_ids'],['0022600002'])
        self.assertEqual(result['crosscheck']['blocked_game_ids'],['0022600001'])
        self.assertEqual(result['games'][0]['start_time'],'2026-10-24T22:00:00+00:00')

    def test_future_or_postponed_nba_game_moved_into_today_is_discovered(self):
        for status in ('scheduled','postponed'):
            source=cached()
            source['games'][0]['start_time']='2026-10-25T22:00:00Z'
            source['games'][0]['status']=status
            result=verify_secondary_schedule(source,[observation([espn_event()])],NOW)
            self.assertEqual(result['crosscheck']['verified_game_ids'],['0022600001'])
            self.assertEqual(result['games'][0]['status'],'scheduled')
            self.assertEqual(result['games'][0]['start_time'],'2026-10-24T22:00:00+00:00')

    def test_distant_same_pair_game_cannot_supply_a_near_term_nba_id(self):
        for start in ('2026-10-26T22:00:00Z','2026-10-29T22:00:00Z'):
            source=cached()
            source['games'][0]['start_time']=start
            result=verify_secondary_schedule(source,[observation([espn_event()])],NOW)
            self.assertEqual(result['crosscheck']['verified_game_ids'],[])
            self.assertEqual(len(result['crosscheck']['unmatched_secondary_events']),1)
            self.assertEqual(result['games'][0]['start_time'],start)

    def test_past_final_event_cannot_mark_tomorrows_same_pair_game_final(self):
        source=cached()
        source['games'][0].update(start_time='2026-10-24T20:00:00Z',status='final')
        later=game('0022600002',start='2026-10-25T22:00:00Z')
        source['games'].append(normalize_season(payload([later]),'2026-10-22T03:00:00Z')['games'][0])
        record=observation([espn_event(date='2026-10-24T20:00Z',status='STATUS_FINAL')])
        record['observed_at']='2026-10-24T23:00:00Z'
        result=verify_secondary_schedule(source,[record],'2026-10-24T23:00:00Z')
        self.assertEqual(result['games'][1]['status'],'scheduled')
        self.assertEqual(result['crosscheck']['verified_game_ids'],['0022600001'])
        self.assertEqual(result['crosscheck']['blocked_game_ids'],['0022600002'])

    def test_future_final_or_live_event_cannot_suppress_due_forecast(self):
        for state in ('STATUS_FINAL','STATUS_IN_PROGRESS'):
            event=espn_event(status=state)
            if state=='STATUS_IN_PROGRESS':
                event['status']['type']['state']='in'
            result=verify_secondary_schedule(cached(),[observation([event])],NOW)
            self.assertEqual(result['crosscheck']['verified_game_ids'],[])
            self.assertEqual(result['crosscheck']['blocked_game_ids'],['0022600001'])
            self.assertEqual(result['games'][0]['status'],'scheduled')

    def test_unmatched_near_term_secondary_game_is_reported(self):
        result=verify_secondary_schedule(cached(),[observation([espn_event(home='MIA',away='LAL')])],NOW)
        self.assertEqual(result['crosscheck']['blocked_game_ids'],['0022600001'])
        self.assertEqual(len(result['crosscheck']['unmatched_secondary_events']),1)
        self.assertEqual(result['crosscheck']['status'],'partial')

    def test_exact_team_match_verifies_cached_nba_identity(self):
        result=verify_secondary_schedule(cached(),[observation([espn_event()])],NOW)
        self.assertEqual(result['games'][0]['game_id'],'0022600001')
        self.assertEqual(result['crosscheck']['verified_game_ids'],['0022600001'])
        self.assertEqual(result['crosscheck']['blocked_game_ids'],[])
        self.assertEqual(result['crosscheck']['nba_base_version'],'b'*64)
        self.assertEqual(result['games'][0]['start_time'],'2026-10-24T22:00:00+00:00')

    def test_time_change_is_sourced_and_nba_original_retained(self):
        result=verify_secondary_schedule(cached(),[observation([espn_event(date='2026-10-24T22:30Z')])],NOW)
        row=result['games'][0]
        self.assertEqual(row['start_time'],'2026-10-24T22:30:00+00:00')
        self.assertEqual(row['source_start_time'],'2026-10-24T22:00:00Z')
        self.assertEqual(row['local_start_time'],'2026-10-24T18:30:00-04:00')
        self.assertEqual(row['schedule_corroboration']['source_sha256'],'a'*64)

    def test_postponement_blocks_due_job(self):
        result=verify_secondary_schedule(cached(),[observation([espn_event(status='STATUS_POSTPONED')])],NOW)
        self.assertEqual(result['games'][0]['status'],'postponed')

    def test_wrong_team_or_missing_event_blocks_near_term_game(self):
        for events in ([],[espn_event(home='MIA')]):
            result=verify_secondary_schedule(cached(),[observation(events)],NOW)
            self.assertEqual(result['crosscheck']['verified_game_ids'],[])
            self.assertEqual(result['crosscheck']['blocked_game_ids'],['0022600001'])

    def test_duplicate_or_wrong_season_cannot_verify(self):
        for events in ([espn_event(),espn_event(identity='401909854')],
                       [{**espn_event(), 'season':dict(year=2026,type=2,slug='regular-season')}],
                       [espn_event(date='2026-10-27T22:00Z')]):
            result=verify_secondary_schedule(cached(),[observation(events)],NOW)
            self.assertEqual(result['crosscheck']['blocked_game_ids'],['0022600001'])

    def test_unconfirmed_time_and_past_scheduled_tipoff_are_blocked(self):
        untimed=espn_event()
        untimed['competitions'][0]['timeValid']=False
        for events in ([untimed],[espn_event(date='2026-10-24T15:00Z')]):
            result=verify_secondary_schedule(cached(),[observation(events)],NOW)
            self.assertEqual(result['crosscheck']['blocked_game_ids'],['0022600001'])

    def test_espn_utah_alias_matches_nba_uta_without_changing_game_id(self):
        source=cached()
        row=source['games'][0]
        row.update(game_id='0022600112',start_time='2026-10-25T21:00:00Z',
                   source_start_time='2026-10-25T21:00:00Z',
                   home_tricode='UTA',away_tricode='LAL')
        record=observation([espn_event(date='2026-10-25T21:00Z',home='UTAH',away='LAL')])
        record.update(source_url=record['source_url'].replace('20261024','20261025'),
                      observed_at='2026-10-25T16:00:00Z')
        result=verify_secondary_schedule(source,[record],'2026-10-25T16:00:00Z')
        self.assertEqual(result['crosscheck']['verified_game_ids'],['0022600112'])

    def test_future_source_cannot_verify(self):
        record=observation([espn_event()])
        record['observed_at']='2026-10-24T16:01:00Z'
        with self.assertRaises(ValueError):
            verify_secondary_schedule(cached(),[record],NOW)


if __name__=='__main__':
    unittest.main()
