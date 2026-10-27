"""Synthetic frozen runs exercise daily selection and coverage without NBA calls."""
from copy import deepcopy
import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from nba_forecast.schedule import canonical
from nba_forecast.totals import game_totals
from tests.test_store import actual,inputs
from nba_forecast.daily_review import review_day,build_daily_reviews

ASOF='2026-10-23T06:00:00Z'

class NightFixture(unittest.TestCase):
    """One game, two projected players and a frozen final run; tests add to it."""
    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.s,_,self.f=inputs()
        self.g=self.s['games'][0]
        self.g.update(status='scheduled',home_tricode='AAA',away_tricode='BBB')
        self.s['games']=[self.g]
        self.s['as_of']='2026-10-22T11:00:00Z'
        self.f['predictions']=self.f['predictions'][:2]
        self.f['forecast_stage']='final'
        self.f['game_totals']=[dict(game_id='G1',status='partial',points_mean=30,teams=[
            dict(team_id='T1',status='partial',points_mean=20),dict(team_id='T2',status='partial',points_mean=10)])]
        (self.folder/'schedule').mkdir()
        (self.folder/'finals').mkdir()
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',games=[self.g])))
        self.rows=[actual('P1'),actual('P2')]
        self.write_actuals()
        with sqlite3.connect(self.folder/'forecast.sqlite') as c:
            c.execute('CREATE TABLE runs (run_id TEXT,created_at TEXT,as_of TEXT,mode TEXT,scope TEXT,snapshot_json TEXT,forecast_json TEXT,snapshot_sha256 TEXT,forecast_sha256 TEXT)')
        self.save('first')

    def write_actuals(self):
        (self.folder/'finals/G1.json').write_text(json.dumps(self.rows))

    def save(self,rid,stage='final',created='2026-10-22T12:00:00Z',mode='live',points=None):
        s=deepcopy(self.s)
        f=deepcopy(self.f)
        f['forecast_stage']=stage
        s['forecast_stage']=stage
        if points is not None:
            f['predictions'][0]['points_mean']=points
        sr=canonical(s).decode()
        fr=canonical(f).decode()
        with sqlite3.connect(self.folder/'forecast.sqlite') as c:
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',(rid,created,s['as_of'],mode,'league',sr,fr,hashlib.sha256(sr.encode()).hexdigest(),hashlib.sha256(fr.encode()).hexdigest()))

    def report(self):
        return review_day(self.folder,'2026-10-22',ASOF)


class DailyReviewTests(NightFixture):
    def test_verified_reschedule_is_used_for_saved_run_and_final_review(self):
        original=json.loads((self.folder/'schedule/latest.json').read_text())
        original['games'][0]['start_time']='2026-10-23T19:00:00Z'
        (self.folder/'schedule/latest.json').write_text(json.dumps(original))
        overlay=dict(original,games=[self.g])
        report=review_day(self.folder,'2026-10-22',ASOF,schedule=overlay)
        self.assertEqual(report['coverage']['scheduled_games'],1)
        self.assertEqual(report['coverage']['verified_finals'],1)
        self.assertEqual(report['games'][0]['stages']['final']['run_id'],'first')
        rebuilt=build_daily_reviews(self.folder,ASOF,schedule=overlay)
        self.assertIn('2026-10-22',rebuilt['reports'])

    def test_margin_feedback_does_not_grade_partial_player_actuals_as_a_final_score(self):
        from tests.test_margins import small_distribution
        self.f['margin_forecasts']=[dict(small_distribution(),game_id='G1',home_team_id='T1',away_team_id='T2')]
        self.rows[0].update(ftm=3,pts=25)
        self.write_actuals()
        self.save('with-margin',created='2026-10-22T13:00:00Z')
        stage=self.report()['games'][0]['stages']['final']
        self.assertEqual(stage['margin_evaluation']['status'],'pending')
        self.assertEqual(len(stage['players']),2)

    def test_latest_stage_selection_excludes_replays_and_post_tipoff(self):
        self.save('prelim',stage='preliminary',points=18)
        self.save('replay',created='2026-10-22T13:00:00Z',mode='replay',points=24)
        self.save('late',created='2026-10-22T19:00:00Z',points=24)
        r=self.report()
        g=r['games'][0]
        self.assertEqual(g['stages']['final']['run_id'],'first')
        self.assertEqual(g['stages']['preliminary']['run_id'],'prelim')
        self.assertEqual(r['paired_stages']['count'],2)
        self.assertEqual(r['coverage']['verified_finals'],1)

    def test_latest_withholding_does_not_fall_back_to_older_numeric(self):
        self.f['predictions'][0].update(status='needs_review',points_mean=None,expected_minutes=None,expected_fga=None)
        self.save('withheld',created='2026-10-22T13:00:00Z')
        r=self.report()
        p=r['games'][0]['stages']['final']['players'][0]
        self.assertEqual(p['forecast_status'],'needs_review')
        self.assertIsNone(p['feedback']['points_error'])
        self.assertEqual(r['stages']['final']['projected']['count'],1)

    def test_projected_dnp_is_error_and_out_zeros_are_separate(self):
        self.rows[0]=actual('P1','dnp')
        self.write_actuals()
        self.f['predictions'][1].update(status='out',points_mean=0,expected_minutes=0,expected_fga=0)
        self.save('status',created='2026-10-22T13:00:00Z')
        r=self.report()
        m=r['stages']['final']
        self.assertEqual(m['projected']['count'],1)
        self.assertEqual(m['projected']['points_mae'],20)
        self.assertEqual(m['out']['count'],1)
        self.assertEqual(len(m['out_player_played']),1)

    def test_missing_player_is_unscored_and_absence_is_not_dnp(self):
        self.rows=self.rows[:1]
        self.write_actuals()
        r=self.report()
        self.assertEqual(r['coverage']['verified_finals'],0,'one missing team invalidates full game evidence')
        self.assertEqual(r['stages']['final']['projected']['count'],0)

    def test_future_actuals_are_pending_then_accepted_at_real_cutoff(self):
        for row in self.rows:
            row['known_at']='2026-10-23T07:00:00Z'
        self.write_actuals()
        self.assertEqual(self.report()['coverage']['verified_finals'],0)
        self.assertEqual(review_day(self.folder,'2026-10-22','2026-10-23T08:00:00Z')['coverage']['verified_finals'],1)

    def test_saved_partial_status_is_preserved_and_excluded_from_complete_total_mae(self):
        r=self.report()
        total=r['games'][0]['stages']['final']['totals']
        self.assertEqual(total['status'],'partial')
        self.assertEqual(total['points_mean'],30)
        self.assertEqual(r['stages']['final']['complete_game_totals']['count'],0)

    def test_legacy_derived_total_is_not_claimed_as_saved(self):
        self.f.pop('game_totals')
        self.save('legacy',created='2026-10-22T13:00:00Z')
        self.assertEqual(self.report()['games'][0]['stages']['final']['totals_origin'],'derived_from_frozen_players')

    def test_supported_bench_squeezed_to_zero_is_an_explicit_action(self):
        self.f['predictions'][0].update(expected_minutes=0,expected_fga=0,points_mean=0,flags=['team_minutes_scaled'],
            components={'rotation':{'scale':0,'protected_rotation_minutes':245,'protected_rotation_player_ids':[str(i) for i in range(9)]}})
        self.save('squeezed',created='2026-10-22T13:00:00Z')
        q=next(q for q in self.report()['improvement_queue'] if q['code']=='bench_squeezed')
        self.assertEqual(q['evidence'][0]['rotation']['protected_players'],9)

    def test_overtime_total_is_visible_but_excluded_from_regulation_total_metric(self):
        self.f['game_totals'][0]['status']='complete'
        self.save('complete',created='2026-10-22T13:00:00Z')
        for row in self.rows:
            row['game_period']=5
        self.write_actuals()
        r=self.report()
        self.assertTrue(r['games'][0]['stages']['final']['overtime'])
        self.assertIsNotNone(r['games'][0]['stages']['final']['game_total_error'])
        self.assertEqual(r['stages']['final']['complete_game_totals']['count'],0)

    def test_missing_named_player_stays_unscored_without_imputing_zero(self):
        extra=deepcopy(self.f['predictions'][0])
        extra.update(player_id='P3',name='Absent Player')
        self.f['predictions'].append(extra)
        self.save('missing',created='2026-10-22T13:00:00Z')
        r=self.report()
        s=r['stages']['final']
        self.assertEqual(s['missing_actuals'],1)
        self.assertEqual(s['projected']['count'],2)

    def test_hash_tampering_fails_closed(self):
        with sqlite3.connect(self.folder/'forecast.sqlite') as c:
            c.execute("UPDATE runs SET forecast_sha256='bad'")
        with self.assertRaisesRegex(ValueError,'hash'):
            self.report()

    def test_unchanged_review_is_reused_and_actual_correction_creates_revision(self):
        first=build_daily_reviews(self.folder,ASOF)
        path=self.folder/'daily-reviews/2026-10-22.json'
        before=path.read_bytes()
        build_daily_reviews(self.folder,'2026-10-23T06:30:00Z')
        self.assertEqual(before,path.read_bytes())
        self.rows[0].update(pts=26,fgm=11)
        self.write_actuals()
        build_daily_reviews(self.folder,'2026-10-23T07:00:00Z')
        self.assertNotEqual(before,path.read_bytes())
        self.assertEqual(len(list((self.folder/'daily-reviews/revisions/2026-10-22').glob('*.json'))),2)

    def test_late_correction_revisits_an_older_saved_review(self):
        build_daily_reviews(self.folder,ASOF)
        path=self.folder/'daily-reviews/2026-10-22.json'
        before=path.read_bytes()
        self.rows[0].update(pts=26,fgm=11,known_at='2026-10-25T08:00:00Z')
        self.write_actuals()
        result=build_daily_reviews(self.folder,'2026-10-26T06:00:00Z')
        self.assertIn('2026-10-22',result['reports'])
        self.assertNotEqual(before,path.read_bytes())

    def test_rescheduled_older_date_does_not_keep_stale_results(self):
        build_daily_reviews(self.folder,ASOF)
        self.g['status']='postponed'
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',games=[self.g])))
        build_daily_reviews(self.folder,'2026-10-26T06:00:00Z')
        report=json.loads((self.folder/'daily-reviews/2026-10-22.json').read_text())
        self.assertEqual(report['coverage']['scheduled_games'],0)

    def test_pacific_date_includes_utc_next_day_games_and_missing_pbp_does_not_block(self):
        self.g['start_time']='2026-10-23T02:00:00Z'
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',games=[self.g])))
        for row in self.rows:
            row.update(game_time=self.g['start_time'],known_at='2026-10-23T05:00:00Z')
        self.write_actuals()
        r=self.report()
        self.assertEqual(r['coverage']['scheduled_games'],1)
        self.assertEqual(r['coverage']['verified_finals'],1)
        self.assertEqual(r['coverage']['play_by_play_games'],0)
        self.assertEqual(r['coverage']['final_forecasts'],0,'rescheduled tipoff cannot reuse old forecast')


def _row(minutes=30,actual_minutes=30,fga=10,actual_fga=10,pts=20,actual_pts=20,status='played',starter=True,pf=2,probability=.9,pid='P1'):
    return dict(player_id=pid,team_id='T1',forecast_status='projected',actual_status=status,starter=starter,participation_probability=probability,
        predicted=dict(minutes=minutes,fga=fga,pts=pts),actual=dict(minutes=actual_minutes,fga=actual_fga,pts=actual_pts,pf=pf),
        feedback=dict(points_error=actual_pts-pts,minutes_error=actual_minutes-minutes,fga_error=actual_fga-fga))


def _context(postgame=None,exit=None,margin=5,later=None,previous=None):
    players={'P1':dict(player_id='P1',last_recorded_exit=exit,last_action_elapsed=exit and exit['elapsed'])} if exit else {}
    return dict(postgame=postgame,pbp_players=players,margin=margin,later=later or {},previous=dict(T1=previous or []))


class MissClassTests(unittest.TestCase):
    """Each class, its precedence and its fallbacks, on hand-built rows."""
    def label(self,row,context=None):
        from nba_forecast.daily_review import _classify
        return _classify(row,context or _context())['miss_class']

    def test_each_class_in_order(self):
        pbp=dict(duration=2880,low_leverage_start=None)
        blowout=dict(duration=2880,low_leverage_start=2200)
        exit=dict(elapsed=600,period=1,clock='PT02M00.00S')
        report=dict(P1=[dict(kind='next_report',detail='Listed out for the next game')])
        cases=[
            ('did_not_play',_row(status='dnp',actual_minutes=0,actual_fga=0,actual_pts=0),None),
            ('early_exit',_row(actual_minutes=6,actual_pts=4),_context(later=report)),
            ('early_exit',_row(actual_minutes=6,actual_pts=4),_context(postgame=pbp,exit=exit)),
            ('exit_unexplained',_row(actual_minutes=6,actual_pts=4),None),
            ('role_shift',_row(minutes=20,actual_minutes=4,starter=False),None),
            ('blowout',_row(minutes=34,actual_minutes=26),_context(postgame=blowout)),
            ('blowout',_row(minutes=10,actual_minutes=18,starter=False),_context(postgame=blowout)),
            ('blowout_assumed',_row(minutes=34,actual_minutes=27),_context(margin=24)),
            ('within_noise',_row(minutes=34,actual_minutes=27,actual_fga=14),_context(postgame=pbp,margin=24)),
            ('foul_trouble',_row(minutes=32,actual_minutes=25,pf=5),None),
            ('blowout',_row(minutes=32,actual_minutes=25,pf=5),_context(postgame=blowout)),
            ('role_shift',_row(minutes=12,actual_minutes=24,starter=False),None),
            ('role_shift_repeated',_row(minutes=12,actual_minutes=24,starter=False),_context(previous=[dict(P1=6),dict(P1=5)])),
            ('role_shift',_row(minutes=12,actual_minutes=24,starter=False),_context(previous=[dict(P1=6),dict(P1=-5)])),
            ('role_shift',_row(minutes=12,actual_minutes=24,starter=False),_context(previous=[dict(P1=6)])),
            ('volume',_row(actual_minutes=32,actual_fga=16),None),
            ('shooting_variance',_row(actual_minutes=31,actual_fga=11,actual_pts=8),None),
            ('within_noise',_row(actual_minutes=36.5,actual_fga=14),None),
        ]
        for expected,row,context in cases:
            with self.subTest(expected=expected,row=row['actual']):
                self.assertEqual(self.label(row,context),expected)

    def test_exit_with_a_later_action_or_in_garbage_time_is_not_an_exit(self):
        exit=dict(elapsed=600,period=1,clock='PT02M00.00S')
        context=_context(postgame=dict(duration=2880,low_leverage_start=None),exit=exit)
        context['pbp_players']['P1']['last_action_elapsed']=900
        self.assertEqual(self.label(_row(actual_minutes=6,actual_pts=4),context),'exit_unexplained','a period started with him on the floor')
        late=dict(elapsed=2300,period=4,clock='PT10M20.00S')
        garbage=_context(postgame=dict(duration=2880,low_leverage_start=2200),exit=late)
        self.assertEqual(self.label(_row(actual_minutes=6,actual_pts=4),garbage),'exit_unexplained','left inside the garbage-time window')
        self.assertEqual(self.label(_row(actual_minutes=6,actual_pts=4,pf=6),_context(postgame=dict(duration=2880,low_leverage_start=None),exit=exit)),'exit_unexplained','fouled out')

    def test_small_short_night_is_not_an_exit(self):
        self.assertEqual(self.label(_row(minutes=8,actual_minutes=3,actual_pts=18),_context(later=dict(P1=[dict(kind='next_report')]))),'shooting_variance')

    def test_shares_add_up_and_split_into_groups(self):
        from nba_forecast.daily_review import _classes
        rows=[_row(status='dnp',actual_minutes=0,actual_fga=0,actual_pts=0),_row(actual_minutes=31,actual_fga=11,actual_pts=8),_row(actual_minutes=36.5,actual_fga=14,actual_pts=26)]
        for row,label in zip(rows,('did_not_play','shooting_variance','within_noise')):
            row['miss_class']=label
        out=_classes(rows)
        self.assertEqual((out['points_abs_error'],out['minutes_abs_error']),(38,37.5))
        self.assertAlmostEqual(sum(c['points_share'] for c in out['classes'].values()),1)
        self.assertAlmostEqual(out['groups']['model']['points_share'],20/38)
        self.assertAlmostEqual(out['groups']['noise']['minutes_share'],7.5/37.5)
        self.assertEqual(out['groups']['unexplained']['count'],0)


class NightSignalTests(NightFixture):
    """Participation, market and recurrence on synthetic nights built on the daily review fixture."""
    def bench(self,count,probability):
        for i in range(1,count+1):
            extra=deepcopy(self.f['predictions'][0])
            extra.update(player_id=f'B{i}',name=f'Bench {i}',expected_minutes=8,points_mean=3,participation_probability=probability)
            self.f['predictions'].append(extra)
            row=actual('P1','dnp')
            row.update(player_id=f'B{i}',name=f'Bench {i}')
            self.rows.append(row)
        self.write_actuals()

    def test_expected_bench_dnps_are_a_small_weighted_loss_and_no_queue_item(self):
        for p in self.f['predictions'][:2]:
            p['participation_probability']=.95
        self.bench(10,.1)
        self.save('bench',created='2026-10-22T13:00:00Z')
        r=self.report()
        final=r['stages']['final']
        self.assertEqual((final['projected_dnp']['count'],final['projected_dnp']['minutes']),(10,80))
        self.assertAlmostEqual(final['participation']['expected_minutes_lost'],8)
        self.assertNotIn('participation',[q['code'] for q in r['improvement_queue']])
        self.assertEqual({p['miss_class'] for g in r['games'] for p in g['stages']['final']['players'] if p['player_id'].startswith('B')},{'did_not_play'})

    def test_likely_starter_who_sits_out_fires_the_participation_item(self):
        for p in self.f['predictions'][:2]:
            p['participation_probability']=.95
        self.rows[0]=actual('P1','dnp')
        self.write_actuals()
        self.save('starter-out',created='2026-10-22T13:00:00Z')
        item=next(q for q in self.report()['improvement_queue'] if q['code']=='participation')
        self.assertEqual([p['player_id'] for p in item['evidence']],['P1'])
        self.assertGreaterEqual(item['measures']['shortfall_z'],2)

    def test_noise_misses_are_explained_not_queued(self):
        self.rows[0].update(minutes=22,pf=5)
        self.rows[1].update(minutes=31,fga=21,fgm=11,pts=26)
        self.write_actuals()
        self.save('fouls',created='2026-10-22T13:00:00Z')
        r=self.report()
        self.assertEqual([p['miss_class'] for p in r['explained_misses']],['foul_trouble'])
        queued=[p for q in r['improvement_queue'] if isinstance(q['evidence'],list) for p in q['evidence']]
        self.assertTrue(queued)
        self.assertNotIn('P1',[p['player_id'] for p in queued])
        self.assertEqual({p['miss_class'] for p in queued},{'role_shift'})

    def second_game(self):
        g2=dict(self.g,game_id='G2',home_team_id='T3',away_team_id='T4',home_tricode='CCC',away_tricode='DDD')
        self.s['games'].append({k:g2[k] for k in ('game_id','start_time','season_type','home_team_id','away_team_id')})
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',games=[self.g,g2])))
        for p,team in zip(self.f['predictions'][2:4],('T3','T4')):
            p.update({k:v for k,v in self.f['predictions'][0].items() if k not in ('player_id','name','team_id')},team_id=team,game_id='G2')
        rows=[]
        for pid,team,opponent in (('P3','T3','T4'),('P4','T4','T3')):
            row=actual('P1')
            row.update(player_id=pid,name='Player '+pid[1],team_id=team,opponent_id=opponent,game_id='G2')
            rows.append(row)
        (self.folder/'finals/G2.json').write_text(json.dumps(rows))

    def lines(self,gid,spread):
        record=dict(provider_id='100',provider='Draft Kings',game_id=gid,retrieved_at='2026-10-22T17:00:00Z',captured_at='2026-10-22T17:00:00Z',
            lines=dict(spread=dict(home=dict(current_line=spread)),total=dict(over=dict(current_line=40.5)),
                moneyline=dict(home=dict(current_odds=None),away=dict(current_odds=None))))
        path=self.folder/'market/game-lines'/(gid+'.jsonl')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(record)+'\n')

    def test_game_lost_to_the_close_lists_its_drivers(self):
        self.second_game()
        self.f['game_totals']=[dict(game_id='G1',status='complete',points_mean=30,teams=[dict(team_id='T1',status='complete',points_mean=20),dict(team_id='T2',status='complete',points_mean=10)]),
                               dict(game_id='G2',status='complete',points_mean=48,teams=[dict(team_id='T3',status='complete',points_mean=24),dict(team_id='T4',status='complete',points_mean=24)])]
        self.save('two-games',created='2026-10-22T13:00:00Z')
        self.lines('G1',-5)
        self.lines('G2',-6)
        r=self.report()
        markets={g['game_id']:g['market'] for g in r['games']}
        self.assertEqual(markets['G1']['margin'],dict(model=10,close=5,kalshi=None,actual=0))
        self.assertEqual(markets['G2']['margin_abs_error'],dict(model=0,close=6,kalshi=None))
        summary=r['market']
        self.assertEqual((summary['games'],summary['model_margin_mae'],summary['close_margin_mae']),(2,5,5.5))
        self.assertEqual([g['game_id'] for g in summary['lost_to_close']],['G1'])
        lost=summary['lost_to_close'][0]
        self.assertEqual(lost['lost_by'],5)
        home=next(t for t in lost['teams'] if t['team_id']=='T1')
        self.assertEqual((home['error'],home['model_points']),(4,4))
        self.assertEqual([(d['player_id'],d['miss_class']) for d in home['drivers']],[('P1','volume')])

    def test_next_report_and_recap_explain_an_early_exit(self):
        g9=dict(self.g,game_id='G9',start_time='2026-10-24T18:00:00Z',away_team_id='T3')
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',games=[self.g,g9])))
        self.rows[0].update(minutes=6,fga=4,fgm=2,fg3a=0,fg3m=0,ftm=0,fta=0,pts=4)
        self.write_actuals()
        def later(rid,created,status):
            s=deepcopy(self.s)
            f=deepcopy(self.f)
            s.update(as_of=created,games=[{k:g9[k] for k in ('game_id','start_time','season_type','home_team_id','away_team_id')}],
                role_context=dict(events=[dict(game_id='G1',team_id='T1',player_id='P1',source_text='P1 left with a sprained ankle.',source_url='https://example.test/recap')]))
            f.update(as_of=created,forecast_stage='preliminary')
            f['predictions'][0].update(game_id='G9',start_time=g9['start_time'],status='out',components=dict(availability=dict(status=status,status_source_url='https://example.test/report')))
            f['predictions'][1].update(game_id=None,status='no_game')
            sr,fr=canonical(s).decode(),canonical(f).decode()
            with sqlite3.connect(self.folder/'forecast.sqlite') as c:
                c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',(rid,created,created,'live','league',sr,fr,hashlib.sha256(sr.encode()).hexdigest(),hashlib.sha256(fr.encode()).hexdigest()))
        self.assertEqual(self.player()['miss_class'],'exit_unexplained')
        later('next','2026-10-23T05:00:00Z','out')
        later('after-cutoff','2026-10-23T07:00:00Z','available')
        p=self.player()
        self.assertEqual(p['miss_class'],'early_exit')
        self.assertEqual([e['kind'] for e in p['miss_evidence']],['recap_exit','next_report'])
        self.assertEqual(p['miss_evidence'][1]['run_id'],'next')

    def player(self):
        return self.report()['games'][0]['stages']['final']['players'][0]

    def test_market_problems_do_not_block_the_review(self):
        path=self.folder/'market/game-lines/G1.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text('{bad\n{"also": "bad"}\n')
        r=self.report()
        self.assertEqual(r['coverage']['verified_finals'],1)
        self.assertTrue(r['market']['errors'])


class RecurrenceTests(unittest.TestCase):
    """Saved nightly reviews threaded across nights."""
    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.folder/'daily-reviews').mkdir()

    def night(self,date,players,teams):
        rows=[dict(player_id=pid,name='Player '+pid,team_id='T1',team='AAA',forecast_status='projected',actual_status='dnp' if minutes==0 else 'played',
            participation_probability=probability,predicted=dict(minutes=20),actual=dict(minutes=minutes),miss_class=label,
            feedback=dict(minutes_error=minutes-20,points_error=(minutes-20)/2)) for pid,minutes,label,probability in players]
        totals=[dict(team_id=tid,team=tid,status='complete',error=error) for tid,error in teams]
        report=dict(date=date,input_signature=date,games=[dict(game_id='G'+date[-2:],stages=dict(final=dict(players=rows,team_total_errors=totals,
            totals_origin='saved_forecast',overtime=False)))])
        (self.folder/'daily-reviews'/(date+'.json')).write_text(json.dumps(report))

    def test_repeating_player_and_team_are_the_only_items(self):
        from nba_forecast.daily_review import build_recurrence
        self.night('2026-10-20',[('A',30,'role_shift',.9),('B',30,'role_shift',.9)],[('T1',8),('T2',9)])
        self.night('2026-10-21',[('A',29,'role_shift',.9),('B',10,'role_shift',.9)],[('T1',6),('T2',-7)])
        self.night('2026-10-22',[('A',12,'blowout',.9),('B',29,'role_shift',.9)],[('T1',3),('T2',6)])
        self.night('2026-10-23',[('A',28,'role_shift',.9),('B',20,'within_noise',.9),('C',0,'did_not_play',.2)],[('T1',-9),('T2',5)])
        self.night('2026-10-24',[('A',27,'role_shift',.9),('C',0,'did_not_play',.6)],[('T1',-7),('T2',8)])
        result=build_recurrence(self.folder)
        self.assertEqual(sorted((q['code'],q.get('player_id') or q.get('team_id')) for q in result['queue']),
            [('recurring_minutes','A'),('recurring_team_total','T2')])
        a=next(p for p in result['players'] if p['player_id']=='A')
        self.assertEqual(a['minutes_streak'],dict(length=4,direction='over',nights=['2026-10-24','2026-10-23','2026-10-21','2026-10-20']),'the blowout night is skipped')
        self.assertEqual(a['classes'],dict(blowout=1,role_shift=4))
        self.assertEqual(next(t for t in result['teams'] if t['team_id']=='T2')['total_streak']['length'],3)
        self.assertEqual(build_recurrence(self.folder),result)

    def test_likely_player_sitting_twice_in_a_week(self):
        from nba_forecast.daily_review import build_recurrence
        self.night('2026-10-20',[('C',0,'did_not_play',.8)],[])
        self.night('2026-10-24',[('C',0,'did_not_play',.6)],[])
        self.night('2026-10-28',[('C',0,'did_not_play',.7)],[])
        items=[q for q in build_recurrence(self.folder)['queue'] if q['code']=='recurring_dnp']
        self.assertEqual([[e['date'] for e in q['evidence']] for q in items],[['2026-10-24','2026-10-28']])
        team=build_recurrence(self.folder)['teams'][0]
        self.assertAlmostEqual(team['expected_minutes_lost'],(.8+.6+.7)*20)


class RecurrenceRebuildTests(NightFixture):
    def test_changed_night_rebuilds_the_recurrence_file(self):
        build_daily_reviews(self.folder,ASOF)
        path=self.folder/'daily-reviews/recurrence.json'
        before=path.read_bytes()
        build_daily_reviews(self.folder,'2026-10-23T06:30:00Z')
        self.assertEqual(before,path.read_bytes())
        self.rows[0].update(pts=26,fgm=11)
        self.write_actuals()
        build_daily_reviews(self.folder,'2026-10-23T07:00:00Z')
        self.assertNotEqual(before,path.read_bytes())
