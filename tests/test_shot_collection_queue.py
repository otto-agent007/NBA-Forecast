"""Postgame collection advances all games and preserves corrected final evidence."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast.full_boxscore_sources import normalize_official_page
from nba_forecast.player_sync import sync_player_data
from nba_forecast.shots import SHOT_SOURCE_VERSION
from tests.test_shots import page_fixture


class ShotCollectionQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.page,self.game,self.meta,_=page_fixture()
        # A real five-player team must account for all 240 regulation minutes.
        for side,extra_ids in [('homeTeam',(10,11,12)),('awayTeam',(20,21,22,23))]:
            team=self.page['game'][side]
            for person in team['players']:
                person['statistics']['minutes']='48:00'
                person['statistics'].update(reboundsTotal=0,reboundsOffensive=0,reboundsDefensive=0,
                    assists=0,steals=0,blocks=0,foulsPersonal=0,plusMinusPoints=5 if side=='homeTeam' else -5)
            for pid in extra_ids:
                person=copy.deepcopy(team['players'][0])
                person.update(personId=pid,familyName=str(pid))
                person['statistics']={k:('48:00' if k=='minutes' else 0) for k in person['statistics']}
                person['statistics']['plusMinusPoints']=5 if side=='homeTeam' else -5
                team['players'].append(person)
        self.rows=normalize_official_page(self.page,self.game,self.meta)
        self.roster=dict(observed_at='2026-10-22T00:00:00Z',players=[
            dict(player_id=r['player_id'],name=r['name'],team_id=r['team_id'],status='unknown') for r in self.rows])
        (self.folder/'schedule').mkdir()
        self.schedule([])
        self.clock=patch('nba_forecast.player_sync.datetime').start()
        self.addCleanup(patch.stopall)
        self.clock.now.return_value=datetime(2026,10,23,tzinfo=timezone.utc)

    def schedule(self,games):
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',games=games)))

    def run_sync(self,rows=None,limit=1):
        return sync_player_data(self.folder,self.rows if rows is None else rows,roster=self.roster,max_shot_games=limit)

    def failed_retry(self,now,attempt_count):
        """Run one failing attempt for game 0022600001 (tipoff 2026-10-20 00:00 UTC)."""
        self.clock.now.return_value=now
        if attempt_count:
            (self.folder/'shots').mkdir(exist_ok=True)
            (self.folder/'shots/0022600001.json').write_text(json.dumps(dict(game_id='0022600001',season='2026-27',
                game_time=self.game['start_time'],known_at='2026-10-20T03:00:00Z',shots=[],
                missing_reason='Complete final team minutes or played-player count disagree',
                source_version=SHOT_SOURCE_VERSION,attempt_count=attempt_count,
                last_attempt_at='2026-10-20T03:00:00Z',next_retry_at='2026-10-20T03:05:00Z')))
        with patch('nba_forecast.player_sync.fetch_game_shots',side_effect=ValueError('Still inconsistent')):
            out=self.run_sync()
        saved=json.loads((self.folder/'shots/0022600001.json').read_text())
        self.assertEqual(out['shot_games_attempted'],1)
        self.assertEqual(saved['attempt_count'],attempt_count+1)
        return saved['next_retry_at']

    def test_a_game_still_failing_six_hours_after_tipoff_is_retried_hourly(self):
        self.assertEqual(self.failed_retry(datetime(2026,10,23,tzinfo=timezone.utc),12),'2026-10-23T01:00:00+00:00')

    def test_a_failing_game_keeps_five_minute_retries_within_six_hours_of_tipoff(self):
        self.roster['observed_at']='2026-10-19T00:00:00Z'
        for r in self.rows:
            r['known_at']='2026-10-20T02:30:00Z'
        self.assertEqual(self.failed_retry(datetime(2026,10,20,5,tzinfo=timezone.utc),12),'2026-10-20T05:05:00+00:00')

    def test_a_game_first_attempted_late_still_gets_the_quick_retries(self):
        self.assertEqual(self.failed_retry(datetime(2026,10,23,tzinfo=timezone.utc),0),'2026-10-23T00:01:00+00:00')
        self.assertEqual(self.failed_retry(datetime(2026,10,23,0,2,tzinfo=timezone.utc),2),'2026-10-23T00:06:00+00:00')

    def test_new_source_adapter_invalidates_recent_failure_backoff(self):
        (self.folder/'shots').mkdir()
        (self.folder/'shots/0022600001.json').write_text(json.dumps(dict(game_id='0022600001',season='2026-27',
            game_time=self.game['start_time'],known_at='2026-10-23T00:00:00Z',shots=[],
            missing_reason='Old adapter HTTP 403',source_version='old',next_retry_at='2026-10-23T06:00:00Z')))
        with patch('nba_forecast.player_sync.fetch_game_shots',side_effect=ValueError('Fresh bounded attempt')):
            out=self.run_sync()
        saved=json.loads((self.folder/'shots/0022600001.json').read_text())
        self.assertEqual(out['shot_games_attempted'],1)
        self.assertEqual(saved['missing_reason'],'Fresh bounded attempt')
        self.assertNotEqual(saved['source_version'],'old')

    def test_first_attempts_are_newest_first_and_failed_newer_games_do_not_starve_backlog(self):
        rows=[]
        for number in range(1,4):
            for original in self.rows:
                r=copy.deepcopy(original)
                r['game_id']=f'002260000{number}'
                r['game_time']=f'2026-10-20T0{number}:00:00Z'
                rows.append(r)
        calls=[]
        def fail(gid,*args,**kwargs):
            calls.append(gid)
            raise ValueError('Source temporarily blocked')
        with patch('nba_forecast.player_sync.fetch_game_shots',side_effect=fail):
            first=self.run_sync(rows,limit=2)
            second=self.run_sync(rows,limit=2)
        self.assertEqual(calls,['0022600003','0022600002','0022600001'])
        self.assertEqual(first['shot_games_pending'],3)
        self.assertEqual(second['shot_games_attempted'],1)
        self.assertEqual(len(list((self.folder/'shots').glob('*.json'))),3)

    def test_official_correction_archives_original_bytes_then_pairs_shots_with_corrected_final(self):
        original=copy.deepcopy(self.rows)
        original[0]['fga']=3
        original[0]['fg_pct']=1/3
        (self.folder/'finals').mkdir()
        raw=(json.dumps(original,indent=3)+'\n').encode()
        (self.folder/'finals/0022600001.json').write_bytes(raw)
        self.schedule([self.game])
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.page,self.meta)):
            out=self.run_sync(original)
            second=self.run_sync(original)
        current=json.loads((self.folder/'finals/0022600001.json').read_text())
        saved=json.loads((self.folder/'shots/0022600001.json').read_text())
        revision=self.folder/'final-revisions/0022600001'/(hashlib.sha256(raw).hexdigest()+'.json')
        self.assertEqual(revision.read_bytes(),raw)
        self.assertEqual(current[0]['fga'],2)
        self.assertEqual(current[0]['official_final_revision']['changes']['fga'],{'before':3,'after':2})
        self.assertEqual(saved['coverage']['attempts'],2)
        self.assertEqual(out['shot_players_verified'],10)
        self.assertEqual(out['shot_games_verified'],1)
        self.assertEqual(second['shot_games_attempted'],0)

    def test_changed_final_counts_force_reconciliation_of_existing_chart(self):
        self.schedule([self.game])
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.page,self.meta)):
            self.run_sync()
        changed=json.loads((self.folder/'finals/0022600001.json').read_text())
        changed[0]['fga']=3
        changed[0]['fg_pct']=1/3
        (self.folder/'finals/0022600001.json').write_text(json.dumps(changed))
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.page,self.meta)):
            out=self.run_sync(changed)
        self.assertEqual(out['shot_games_attempted'],1)
        self.assertEqual(json.loads((self.folder/'finals/0022600001.json').read_text())[0]['fga'],2)

    def test_unlocated_attempts_remain_in_automatic_retry_queue(self):
        self.schedule([self.game])
        self.page['playByPlay']['actions'][0].pop('xLegacy')
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.page,self.meta)):
            out=self.run_sync()
        self.assertEqual(out['shot_games_verified'],0)
        self.assertEqual(out['shot_games_pending'],1)
        self.assertEqual(out['shot_attempts_verified'],2)
        self.assertEqual(out['shot_locations_verified'],1)
        self.assertTrue(json.loads((self.folder/'shots/0022600001.json').read_text())['next_retry_at'])

    def test_incomplete_official_minutes_cannot_replace_a_verified_final(self):
        self.schedule([self.game])
        (self.folder/'finals').mkdir()
        original=json.dumps(self.rows,indent=2)+'\n'
        (self.folder/'finals/0022600001.json').write_text(original)
        self.page['game']['homeTeam']['players'][0]['statistics']['minutes']='1:00'
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.page,self.meta)):
            out=self.run_sync()
        self.assertEqual(out['shot_games_verified'],0)
        self.assertTrue(out['errors'])
        self.assertEqual((self.folder/'finals/0022600001.json').read_text(),original)

    def test_complete_shots_do_not_stop_bounded_retry_for_missing_full_box_statistic(self):
        self.schedule([self.game])
        self.page['game']['homeTeam']['players'][0]['statistics'].pop('foulsPersonal')
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.page,self.meta)):
            out=self.run_sync()
            second=self.run_sync()
        self.assertEqual(out['shot_games_verified'],1)
        self.assertEqual(out['full_box_games_pending'],1)
        self.assertEqual(second['shot_games_attempted'],0)
        self.assertTrue(json.loads((self.folder/'shots/0022600001.json').read_text())['next_retry_at'])

    def inactive_page(self):
        page=copy.deepcopy(self.page)
        page['game']['homeTeam']['inactives']=[dict(personId=30,firstName='Home',familyName='Inactive',jerseyNum='3')]
        page['game']['awayTeam']['players'].append(dict(personId=31,firstName='Away',familyName='Injured',
            comment='Inactive - Injury/Illness',statistics={'minutes':''}))
        return page

    def test_a_page_without_inactive_players_is_fetched_again_until_it_lists_them(self):
        self.roster['observed_at']='2026-10-19T00:00:00Z'
        self.meta=dict(self.meta,retrieved_at='2026-10-20T02:30:00Z')
        for r in self.rows:
            r['known_at']=self.meta['retrieved_at']
        self.schedule([self.game])
        self.clock.now.return_value=datetime(2026,10,20,3,tzinfo=timezone.utc)
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.page,self.meta)):
            first=self.run_sync()
            second=self.run_sync()
        saved=json.loads((self.folder/'shots/0022600001.json').read_text())
        self.assertEqual(first['full_box_games_pending'],1)
        self.assertEqual(saved['next_retry_at'],'2026-10-20T03:30:00+00:00')
        self.assertEqual(second['shot_games_attempted'],0)
        self.clock.now.return_value=datetime(2026,10,20,3,31,tzinfo=timezone.utc)
        later=dict(self.meta,retrieved_at='2026-10-20T03:31:00Z')
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.inactive_page(),later)):
            third=self.run_sync()
            fourth=self.run_sync()
        final={r['player_id']:r for r in json.loads((self.folder/'finals/0022600001.json').read_text())}
        self.assertEqual(third['shot_games_attempted'],1)
        self.assertEqual((final['30']['status'],final['30']['team_id'],final['31']['participation_reason']),
                         ('dnp','1','Inactive - Injury/Illness'))
        self.assertTrue(all(r['official_inactives_listed'] for r in final.values()))
        self.assertEqual((third['full_box_games_pending'],fourth['shot_games_attempted']),(0,0))

    def test_a_day_after_tip_a_page_without_inactive_players_is_accepted(self):
        self.roster['observed_at']='2026-10-19T00:00:00Z'
        self.schedule([self.game])
        self.clock.now.return_value=datetime(2026,10,21,0,1,tzinfo=timezone.utc)
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.page,self.meta)) as fetch:
            out=self.run_sync()
            self.clock.now.return_value=datetime(2026,10,22,tzinfo=timezone.utc)
            self.run_sync()
        self.assertEqual((out['full_box_games_pending'],fetch.call_count),(0,1))

    def test_a_final_reconciled_before_inactive_lists_gains_them_and_loses_stale_dnps(self):
        dnp_page=copy.deepcopy(self.page)
        dnp_page['game']['homeTeam']['players'].append(dict(personId=99,firstName='Stale',familyName='Espn',
            comment="DNP - Coach's Decision",statistics={'minutes':''}))
        stale=next(r for r in normalize_official_page(dnp_page,self.game,self.meta) if r['player_id']=='99')
        stale.update(source_url='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=1',
                     participation_reason="COACH'S DECISION")
        old=[dict(r) for r in self.rows]+[stale]
        for r in old:
            r.pop('official_inactives_listed')
        (self.folder/'finals').mkdir()
        (self.folder/'finals/0022600001.json').write_text(json.dumps(old))
        self.schedule([self.game])
        with patch('nba_forecast.player_sync.fetch_official_game_page',return_value=(self.inactive_page(),self.meta)) as fetch:
            out=self.run_sync(old)
            self.run_sync(old)
        final={r['player_id']:r for r in json.loads((self.folder/'finals/0022600001.json').read_text())}
        self.assertNotIn('99',final)
        self.assertEqual({'30','31'}&set(final),{'30','31'})
        self.assertEqual(final['30']['full_boxscore_enrichment']['status'],'official_dnp_absent_from_original')
        self.assertEqual((fetch.call_count,len(out['final_revisions'])),(1,1))
