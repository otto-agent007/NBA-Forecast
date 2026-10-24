"""Synthetic frozen runs exercise daily selection and coverage without NBA calls."""
from copy import deepcopy
import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from nba_forecast.schedule import canonical
from nba_forecast.totals import game_totals
from tests.test_store import actual,inputs
from nba_forecast.daily_review import review_day,build_daily_reviews

ASOF='2026-10-23T06:00:00Z'

class DailyReviewTests(unittest.TestCase):
    def test_margin_feedback_does_not_grade_partial_player_actuals_as_a_final_score(self):
        from tests.test_margins import small_distribution
        self.f['margin_forecasts']=[dict(small_distribution(),game_id='G1',home_team_id='T1',away_team_id='T2')]
        self.rows[0].update(ftm=3,pts=25)
        self.write_actuals();self.save('with-margin',created='2026-10-22T13:00:00Z')
        stage=self.report()['games'][0]['stages']['final']
        self.assertEqual(stage['margin_evaluation']['status'],'pending')
        self.assertEqual(len(stage['players']),2)

    def setUp(self):
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.s,_,self.f=inputs();self.g=self.s['games'][0]
        self.g.update(status='scheduled',home_tricode='AAA',away_tricode='BBB')
        self.s['games']=[self.g];self.s['as_of']='2026-10-22T11:00:00Z'
        self.f['predictions']=self.f['predictions'][:2]
        self.f['forecast_stage']='final'
        self.f['game_totals']=[dict(game_id='G1',status='partial',points_mean=30,teams=[
            dict(team_id='T1',status='partial',points_mean=20),dict(team_id='T2',status='partial',points_mean=10)])]
        (self.folder/'schedule').mkdir();(self.folder/'finals').mkdir()
        (self.folder/'schedule/latest.json').write_text(json.dumps(dict(season='2026-27',games=[self.g])))
        self.rows=[actual('P1'),actual('P2')]
        self.write_actuals()
        with sqlite3.connect(self.folder/'forecast.sqlite') as c:
            c.execute('CREATE TABLE runs (run_id TEXT,created_at TEXT,as_of TEXT,mode TEXT,scope TEXT,snapshot_json TEXT,forecast_json TEXT,snapshot_sha256 TEXT,forecast_sha256 TEXT)')
        self.save('first')

    def write_actuals(self):
        (self.folder/'finals/G1.json').write_text(json.dumps(self.rows))

    def save(self,rid,stage='final',created='2026-10-22T12:00:00Z',mode='live',points=None):
        s=deepcopy(self.s);f=deepcopy(self.f);f['forecast_stage']=stage;s['forecast_stage']=stage
        if points is not None:f['predictions'][0]['points_mean']=points
        sr=canonical(s).decode();fr=canonical(f).decode()
        with sqlite3.connect(self.folder/'forecast.sqlite') as c:
            c.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',(rid,created,s['as_of'],mode,'league',sr,fr,hashlib.sha256(sr.encode()).hexdigest(),hashlib.sha256(fr.encode()).hexdigest()))

    def report(self):return review_day(self.folder,'2026-10-22',ASOF)

    def test_latest_stage_selection_excludes_replays_and_post_tipoff(self):
        self.save('prelim',stage='preliminary',points=18)
        self.save('replay',created='2026-10-22T13:00:00Z',mode='replay',points=24)
        self.save('late',created='2026-10-22T19:00:00Z',points=24)
        r=self.report();g=r['games'][0]
        self.assertEqual(g['stages']['final']['run_id'],'first')
        self.assertEqual(g['stages']['preliminary']['run_id'],'prelim')
        self.assertEqual(r['paired_stages']['count'],2)
        self.assertEqual(r['coverage']['verified_finals'],1)

    def test_latest_withholding_does_not_fall_back_to_older_numeric(self):
        self.f['predictions'][0].update(status='needs_review',points_mean=None,expected_minutes=None,expected_fga=None)
        self.save('withheld',created='2026-10-22T13:00:00Z')
        r=self.report();p=r['games'][0]['stages']['final']['players'][0]
        self.assertEqual(p['forecast_status'],'needs_review');self.assertIsNone(p['feedback']['points_error'])
        self.assertEqual(r['stages']['final']['projected']['count'],1)

    def test_projected_dnp_is_error_and_out_zeros_are_separate(self):
        self.rows[0]=actual('P1','dnp');self.write_actuals()
        self.f['predictions'][1].update(status='out',points_mean=0,expected_minutes=0,expected_fga=0)
        self.save('status',created='2026-10-22T13:00:00Z')
        r=self.report();m=r['stages']['final']
        self.assertEqual(m['projected']['count'],1);self.assertEqual(m['projected']['points_mae'],20)
        self.assertEqual(m['out']['count'],1)
        self.assertEqual(len(m['out_player_played']),1)

    def test_missing_player_is_unscored_and_absence_is_not_dnp(self):
        self.rows=self.rows[:1];self.write_actuals()
        r=self.report()
        self.assertEqual(r['coverage']['verified_finals'],0,'one missing team invalidates full game evidence')
        self.assertEqual(r['stages']['final']['projected']['count'],0)

    def test_future_actuals_are_pending_then_accepted_at_real_cutoff(self):
        for row in self.rows:row['known_at']='2026-10-23T07:00:00Z'
        self.write_actuals()
        self.assertEqual(self.report()['coverage']['verified_finals'],0)
        self.assertEqual(review_day(self.folder,'2026-10-22','2026-10-23T08:00:00Z')['coverage']['verified_finals'],1)

    def test_saved_partial_status_is_preserved_and_excluded_from_complete_total_mae(self):
        r=self.report();total=r['games'][0]['stages']['final']['totals']
        self.assertEqual(total['status'],'partial');self.assertEqual(total['points_mean'],30)
        self.assertEqual(r['stages']['final']['complete_game_totals']['count'],0)

    def test_legacy_derived_total_is_not_claimed_as_saved(self):
        self.f.pop('game_totals');self.save('legacy',created='2026-10-22T13:00:00Z')
        self.assertEqual(self.report()['games'][0]['stages']['final']['totals_origin'],'derived_from_frozen_players')

    def test_supported_bench_squeezed_to_zero_is_an_explicit_action(self):
        self.f['predictions'][0].update(expected_minutes=0,expected_fga=0,points_mean=0,flags=['team_minutes_scaled'],
            components={'rotation':{'scale':0,'protected_rotation_minutes':245,'protected_rotation_player_ids':[str(i) for i in range(9)]}})
        self.save('squeezed',created='2026-10-22T13:00:00Z')
        q=next(q for q in self.report()['improvement_queue'] if q['code']=='bench_squeezed')
        self.assertEqual(q['evidence'][0]['rotation']['protected_players'],9)

    def test_overtime_total_is_visible_but_excluded_from_regulation_total_metric(self):
        self.f['game_totals'][0]['status']='complete';self.save('complete',created='2026-10-22T13:00:00Z')
        for row in self.rows:row['game_period']=5
        self.write_actuals();r=self.report()
        self.assertTrue(r['games'][0]['stages']['final']['overtime'])
        self.assertIsNotNone(r['games'][0]['stages']['final']['game_total_error'])
        self.assertEqual(r['stages']['final']['complete_game_totals']['count'],0)

    def test_missing_named_player_stays_unscored_without_imputing_zero(self):
        extra=deepcopy(self.f['predictions'][0]);extra.update(player_id='P3',name='Absent Player')
        self.f['predictions'].append(extra);self.save('missing',created='2026-10-22T13:00:00Z')
        r=self.report();s=r['stages']['final']
        self.assertEqual(s['missing_actuals'],1);self.assertEqual(s['projected']['count'],2)

    def test_hash_tampering_fails_closed(self):
        with sqlite3.connect(self.folder/'forecast.sqlite') as c:c.execute("UPDATE runs SET forecast_sha256='bad'")
        with self.assertRaisesRegex(ValueError,'hash'):self.report()

    def test_unchanged_review_is_reused_and_actual_correction_creates_revision(self):
        first=build_daily_reviews(self.folder,ASOF)
        path=self.folder/'daily-reviews/2026-10-22.json';before=path.read_bytes()
        build_daily_reviews(self.folder,'2026-10-23T06:30:00Z')
        self.assertEqual(before,path.read_bytes())
        self.rows[0].update(pts=26,fgm=11);self.write_actuals()
        build_daily_reviews(self.folder,'2026-10-23T07:00:00Z')
        self.assertNotEqual(before,path.read_bytes())
        self.assertEqual(len(list((self.folder/'daily-reviews/revisions/2026-10-22').glob('*.json'))),2)

    def test_late_correction_revisits_an_older_saved_review(self):
        build_daily_reviews(self.folder,ASOF)
        path=self.folder/'daily-reviews/2026-10-22.json';before=path.read_bytes()
        self.rows[0].update(pts=26,fgm=11,known_at='2026-10-25T08:00:00Z');self.write_actuals()
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
        for row in self.rows:row.update(game_time=self.g['start_time'],known_at='2026-10-23T05:00:00Z')
        self.write_actuals();r=self.report()
        self.assertEqual(r['coverage']['scheduled_games'],1);self.assertEqual(r['coverage']['verified_finals'],1)
        self.assertEqual(r['coverage']['play_by_play_games'],0)
        self.assertEqual(r['coverage']['final_forecasts'],0,'rescheduled tipoff cannot reuse old forecast')
