import copy,hashlib,json,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from nba_forecast.outcome_sync import environment_record
from nba_forecast.outcome_sync import sync_finished_games
from nba_forecast.espn_actuals import ActualsPending,ActualsSourceConflict


def complete_official_fixture():
    from tests.test_shots import page_fixture
    page,game,meta,_=page_fixture()
    game['status']='scheduled'
    for side,extra_ids in [('homeTeam',(10,11,12)),('awayTeam',(20,21,22,23))]:
        team=page['game'][side]
        for person in team['players']:
            person['statistics'].update(minutes='48:00',turnovers=0,reboundsTotal=0,
                reboundsOffensive=0,reboundsDefensive=0,assists=0,steals=0,blocks=0,
                foulsPersonal=0,plusMinusPoints=5 if side=='homeTeam' else -5)
        for pid in extra_ids:
            person=copy.deepcopy(team['players'][0])
            person.update(personId=pid,familyName=str(pid))
            person['statistics']={key:('48:00' if key=='minutes' else 0) for key in person['statistics']}
            person['statistics']['plusMinusPoints']=5 if side=='homeTeam' else -5
            team['players'].append(person)
        team['statistics']=dict(fieldGoalsMade=1 if side=='homeTeam' else 0,
            fieldGoalsAttempted=2 if side=='homeTeam' else 0,threePointersMade=0,
            threePointersAttempted=1 if side=='homeTeam' else 0,
            freeThrowsMade=6 if side=='homeTeam' else 3,
            freeThrowsAttempted=8 if side=='homeTeam' else 4,points=8 if side=='homeTeam' else 3,
            turnovers=0,reboundsTotal=0,reboundsOffensive=0,reboundsDefensive=0,
            assists=0,steals=0,blocks=0,foulsPersonal=0)
    page['playByPlay']['actions'].append(dict(actionNumber=20,period=1,clock='PT03M00.00S',
        actionType='Turnover',isFieldGoal=0,teamId=1,personId=0))
    raw=('<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':page}})+'</script>').encode()
    meta['sha256']=hashlib.sha256(raw).hexdigest()
    return page,game,meta


class OutcomeSyncTests(unittest.TestCase):
    def test_unreconciled_final_is_pending_with_evidence_and_never_cached_as_actual(self):
        game=dict(game_id='g',status='final',start_time='2026-10-23T23:00:00Z',home_team_id='H',away_team_id='A')
        conflict=ActualsSourceConflict(game_id='g',team_id='A',provider_event_id='123456',statistic='fga',player_total=81,team_total=80,
            metadata=dict(source_url='https://example.test/summary',sha256='a'*64,retrieved_at='2026-10-24T02:00:00Z'))
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=conflict), \
                patch('nba_forecast.outcome_sync.fetch_official_game_page',side_effect=ValueError('Official source still incomplete'),create=True):
            Path(d,'roster.json').write_text(json.dumps({'players':[]}))
            report=sync_finished_games(d,{'games':[game]},[],'2026-10-24T02:00:00Z')
            self.assertEqual(report['pending'],['g'])
            self.assertFalse(report['errors'])
            self.assertEqual(report['pending_details'][0]['evidence']['player_total'],81)
            self.assertFalse(Path(d,'finals/g.json').exists())
            self.assertIn('Official source still incomplete',report['pending_details'][0]['reason'])

    def test_explicit_overtime_period_determines_duration_not_rounded_player_minutes(self):
        game=dict(game_id='g',start_time='2026-10-23T23:00:00Z',home_team_id='H',away_team_id='A')
        rows=[dict(game_id='g',team_id=t,known_at='2026-10-24T02:00:00Z',season='2026-27',game_period=5,
            team_boxscore=dict(pts=120,fga=90,fta=20,oreb=10,tov=12,team_turnovers=1,total_turnovers=13),
            source_provenance={'summary':{'sha256':'a'*64}},venue_timezone='America/New_York') for t in ('H','A')]
        record=environment_record(game,rows)
        self.assertEqual(record['game_minutes'],53)
        self.assertEqual(record['team_stats']['H']['tov'],13)
        self.assertEqual(record['team_stats']['H']['player_tov'],12)
        self.assertEqual(record['known_at'],'2026-10-24T02:00:00Z')
        rows[0].pop('game_period')
        with self.assertRaises(ValueError):
            environment_record(game,rows)


class OfficialOutcomeDiscoveryTests(unittest.TestCase):
    def test_unmapped_espn_rookie_cannot_block_exact_nba_final_and_every_player_shots(self):
        page,game,meta=complete_official_fixture()
        with tempfile.TemporaryDirectory() as d, \
                patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=ValueError('unknown ESPN player identity: New Rookie')), \
                patch('nba_forecast.outcome_sync.fetch_official_game_page',return_value=(page,meta),create=True):
            Path(d,'roster.json').write_text(json.dumps({'players':[]}))
            report=sync_finished_games(d,{'games':[game]},[],'2026-10-21T00:01:00Z')
            self.assertEqual(report['imported'],['0022600001'])
            self.assertFalse(report['pending'])
            self.assertFalse(report['errors'])
            rows=json.loads(Path(d,'finals/0022600001.json').read_text())
            shots=json.loads(Path(d,'shots/0022600001.json').read_text())
            self.assertEqual(len(rows),10)
            self.assertEqual(shots['coverage']['played_players'],10)
            self.assertEqual(shots['coverage']['attempts'],2)
            self.assertEqual(shots['coverage']['located_attempts'],2)
            self.assertEqual(rows[0]['source_sha256'],meta['sha256'])
            self.assertIn('New Rookie',rows[0]['outcome_source_fallback']['primary_failure']['reason'])
            self.assertEqual(report['fallbacks'][0]['source_url'],meta['source_url'])
            self.assertEqual(rows[0]['team_boxscore']['team_turnovers'],1)
            self.assertEqual(rows[-1]['team_boxscore']['team_turnovers'],0)
            self.assertEqual(len(report['environment_updates']),1)

    def test_pending_or_inaccessible_espn_and_absent_roster_can_use_official_ids(self):
        for failure,has_roster in [(ActualsPending('ESPN has no explicit final'),True),
                                   (OSError('ESPN returned HTTP 403'),True),
                                   (ValueError('No roster'),False)]:
            with self.subTest(failure=str(failure)),tempfile.TemporaryDirectory() as d:
                page,game,meta=complete_official_fixture()
                if has_roster:
                    Path(d,'roster.json').write_text(json.dumps({'players':[]}))
                with patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=failure), \
                        patch('nba_forecast.outcome_sync.fetch_official_game_page',return_value=(page,meta),create=True):
                    report=sync_finished_games(d,{'games':[game]},[],'2026-10-21T00:01:00Z')
                self.assertEqual(report['imported'],['0022600001'])
                self.assertFalse(report['pending'])
                self.assertEqual(len(json.loads(Path(d,'finals/0022600001.json').read_text())),10)

    def test_ongoing_truncated_or_inconsistent_official_evidence_stays_pending_without_zero_actuals(self):
        for change in ('ongoing','missing_period','shot_count','minutes','turnovers'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                page,game,meta=complete_official_fixture()
                if change=='ongoing':
                    page['game']['gameStatus']=2
                elif change=='missing_period':
                    page['playByPlay']['actions']=[a for a in page['playByPlay']['actions'] if a['period']!=4]
                elif change=='shot_count':
                    page['playByPlay']['actions'].pop(0)
                elif change=='minutes':
                    page['game']['homeTeam']['players'][0]['statistics']['minutes']='1:00'
                else:
                    page['game']['homeTeam']['players'][0]['statistics']['turnovers']=1
                Path(d,'roster.json').write_text(json.dumps({'players':[]}))
                with patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=ValueError('ESPN athlete ID unavailable')), \
                        patch('nba_forecast.outcome_sync.fetch_official_game_page',return_value=(page,meta),create=True):
                    report=sync_finished_games(d,{'games':[game]},[],'2026-10-21T00:01:00Z')
                self.assertEqual(report['pending'],['0022600001'])
                self.assertFalse(report['errors'])
                self.assertFalse(Path(d,'finals/0022600001.json').exists())
                self.assertFalse(Path(d,'shots/0022600001.json').exists())
                failures=report['pending_details'][0]['source_failures']
                self.assertIn('ESPN athlete ID unavailable',failures[0]['reason'])
                self.assertTrue(failures[1]['reason'])

    def test_uncached_current_season_final_older_than_48_hours_is_discovered(self):
        page,game,meta=complete_official_fixture()
        with tempfile.TemporaryDirectory() as d, \
                patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=ValueError('ESPN unavailable')), \
                patch('nba_forecast.outcome_sync.fetch_official_game_page',return_value=(page,meta),create=True):
            Path(d,'roster.json').write_text(json.dumps({'players':[]}))
            report=sync_finished_games(d,{'season':'2026-27','games':[game]},[],'2026-10-24T00:00:00Z')
            self.assertEqual(report['imported'],['0022600001'])
            self.assertEqual(report['source_games_attempted'],1)
            self.assertTrue(Path(d,'shots/0022600001.json').exists())

    def test_bounded_persistent_discovery_prioritizes_new_games_without_starving_old_games(self):
        _,older,_=complete_official_fixture()
        newer=dict(older,game_id='0022600002',start_time='2026-10-22T00:00:00Z')
        prior=dict(older,game_id='0022500001',start_time='2025-10-20T00:00:00Z')
        calls=[]
        def blocked(game,*args,**kwargs):
            calls.append(game['game_id'])
            raise ValueError('ESPN source temporarily unavailable')
        with tempfile.TemporaryDirectory() as d, \
                patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=blocked), \
                patch('nba_forecast.outcome_sync.fetch_official_game_page',side_effect=ValueError('NBA source temporarily unavailable'),create=True):
            Path(d,'roster.json').write_text(json.dumps({'players':[]}))
            schedule={'season':'2026-27','games':[prior,older,newer]}
            first=sync_finished_games(d,schedule,[],'2026-10-24T00:00:00Z',max_source_games=1)
            state=json.loads(Path(d,'outcome-discovery/queue.json').read_text())
            self.assertEqual(state['games']['0022600002']['attempt_count'],1)
            self.assertTrue(state['games']['0022600002']['next_retry_at'])
            second=sync_finished_games(d,schedule,[],'2026-10-24T00:01:01Z',max_source_games=1)
            self.assertEqual(calls,['0022600002','0022600001'])
            self.assertEqual(first['source_games_attempted'],1)
            self.assertEqual(second['source_games_attempted'],1)
            self.assertEqual(set(second['pending']),{'0022600001','0022600002'})
            self.assertNotIn('0022500001',json.loads(Path(d,'outcome-discovery/queue.json').read_text())['games'])

    def test_old_cached_final_environment_is_rebuilt_locally_without_source_requests(self):
        page,game,meta=complete_official_fixture()
        with tempfile.TemporaryDirectory() as d:
            Path(d,'roster.json').write_text(json.dumps({'players':[]}))
            schedule={'season':'2026-27','games':[game]}
            with patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=ValueError('ESPN unavailable')), \
                    patch('nba_forecast.outcome_sync.fetch_official_game_page',return_value=(page,meta),create=True):
                initial=sync_finished_games(d,schedule,[],'2026-10-21T00:01:00Z')
            self.assertEqual(initial['imported'],['0022600001'])
            for path in Path(d,'environment-finals/records').glob('*.json'):
                path.unlink()
            with patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=AssertionError('No network expected')), \
                    patch('nba_forecast.outcome_sync.fetch_official_game_page',side_effect=AssertionError('No network expected'),create=True):
                report=sync_finished_games(d,schedule,[],'2026-10-24T00:00:00Z')
            self.assertEqual(report['source_games_attempted'],0)
            self.assertEqual(report['environment_updates'],['0022600001'])

    def test_default_tick_attempts_at_most_four_source_games_and_honors_retry_times(self):
        _,base,_=complete_official_fixture()
        games=[dict(base,game_id=f'002260000{i+1}',start_time=f'2026-10-{18+i:02d}T00:00:00Z') for i in range(6)]
        calls=[]
        def blocked(game,*args,**kwargs):
            calls.append(game['game_id'])
            raise ValueError('ESPN unavailable')
        with tempfile.TemporaryDirectory() as d, \
                patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=blocked), \
                patch('nba_forecast.outcome_sync.fetch_official_game_page',side_effect=ValueError('NBA unavailable')):
            Path(d,'roster.json').write_text(json.dumps({'players':[]}))
            first=sync_finished_games(d,{'season':'2026-27','games':games},[],'2026-10-24T00:00:00Z')
            second=sync_finished_games(d,{'season':'2026-27','games':games},[],'2026-10-24T00:00:01Z')
            third=sync_finished_games(d,{'season':'2026-27','games':games},[],'2026-10-24T00:00:02Z')
            self.assertEqual(first['source_games_attempted'],4)
            self.assertEqual(second['source_games_attempted'],2)
            self.assertEqual(third['source_games_attempted'],0)
            self.assertEqual(calls,['0022600006','0022600005','0022600004','0022600003','0022600002','0022600001'])

    def test_cancelled_and_canceled_games_never_enter_discovery_queue(self):
        _,base,_=complete_official_fixture()
        games=[dict(base,game_id=f'002260000{i+1}',status=status) for i,status in enumerate(('cancelled','canceled'))]
        with tempfile.TemporaryDirectory() as d, \
                patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=AssertionError('Cancelled game must not fetch')), \
                patch('nba_forecast.outcome_sync.fetch_official_game_page',side_effect=AssertionError('Cancelled game must not fetch')):
            report=sync_finished_games(d,{'season':'2026-27','games':games},[],'2026-10-24T00:00:00Z')
            self.assertEqual(report['source_games_attempted'],0)
            self.assertFalse(report['pending'])
            self.assertFalse(json.loads(Path(d,'outcome-discovery/queue.json').read_text())['games'])

    def test_cached_final_identity_must_match_schedule_before_environment_use(self):
        from nba_forecast.full_boxscore_sources import normalize_official_page
        for change in ('game','time','teams'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                page,game,meta=complete_official_fixture()
                rows=normalize_official_page(page,game,meta)
                for row in rows:
                    if change=='game':
                        row['game_id']='0022600002'
                    elif change=='time':
                        row['game_time']='2026-10-20T00:01:00Z'
                    else:
                        row['team_id']='3' if row['team_id']=='1' else '4'
                        row['opponent_id']='4' if row['team_id']=='3' else '3'
                Path(d,'finals').mkdir()
                path=Path(d,'finals/0022600001.json')
                original=json.dumps(rows,indent=2)
                path.write_text(original)
                with patch('nba_forecast.outcome_sync.fetch_espn_final',side_effect=AssertionError('No replacement fetch expected')), \
                        patch('nba_forecast.outcome_sync.fetch_official_game_page',side_effect=AssertionError('No replacement fetch expected')):
                    report=sync_finished_games(d,{'season':'2026-27','games':[game]},[],'2026-10-24T00:00:00Z')
                self.assertEqual(len(report['errors']),1)
                self.assertFalse(report['environment_updates'])
                self.assertEqual(report['source_games_attempted'],0)
                self.assertEqual(path.read_text(),original)
