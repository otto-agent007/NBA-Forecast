"""Prospective margin correction must use frozen pregame runs and complete finals."""
from datetime import datetime,timedelta,timezone
import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from nba_forecast.store import _SCHEMA
from nba_forecast.margins import _pmf, _sha, METHOD, SETTLEMENT

try:
    from nba_forecast.prospective_margin_calibration import collect_margin_samples,fit_prospective_margin
except ImportError:
    collect_margin_samples=fit_prospective_margin=None

MODEL='margin-model-key'
ASOF='2026-12-31T12:00:00Z'


def distribution(gid,mean=0.,sd=16.):
    pmf=_pmf(mean,sd)
    return dict(schema_version=1,game_id=gid,home_team_id='T1',away_team_id='T2',
        model_key=MODEL,status='estimated',settlement=SETTLEMENT,method=METHOD,
        calibration_status='uncalibrated',mean_margin=mean,standard_deviation=sd,
        supported_line_range={'minimum':-120,'maximum':120,'exclusive':True},
        pmf=pmf,pmf_sha256=_sha(pmf))


def sample(index):
    day=index//8;within=index%8
    game=datetime(2026,11,1,20,tzinfo=timezone.utc)+timedelta(days=day,minutes=within*15)
    actual=15 if index%2 else -7
    return dict(game_id=f'002260{index:04d}',model_key=MODEL,home_team_id='T1',away_team_id='T2',
        game_time=game.isoformat(),forecast_created_at=(game-timedelta(hours=1)).isoformat(),
        actual_known_at=(game+timedelta(hours=3)).isoformat(),base_mean_margin=0.,base_standard_deviation=16.,
        actual_home_margin=actual,run_id=f'run-{index}',forecast_sha256='a'*64,actuals_sha256='b'*64)


class ProspectiveMarginTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(collect_margin_samples))
        self.folder=Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.folder/'finals').mkdir()
        with sqlite3.connect(self.folder/'forecast.sqlite') as db:db.executescript(_SCHEMA)

    def run_row(self,gid='0022600001',run='first',created='2026-11-01T19:00:00Z',
                start='2026-11-01T20:00:00Z',margin=None,model_key=MODEL):
        g=dict(game_id=gid,home_team_id='T1',away_team_id='T2',start_time=start,season_type='Regular Season')
        snapshot=dict(season='2026-27',as_of=created,games=[g])
        forecast=dict(season='2026-27',as_of=created,model_key=model_key,margin_forecasts=[margin] if margin else [])
        x=json.dumps(snapshot,separators=(',',':'));y=json.dumps(forecast,separators=(',',':'))
        with sqlite3.connect(self.folder/'forecast.sqlite') as db:
            db.execute('INSERT INTO runs (run_id,created_at,as_of,mode,scope,snapshot_json,history_json,forecast_json,snapshot_sha256,history_sha256,forecast_sha256,inputs_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(
                run,created,created,'live','league',x,'[]',y,hashlib.sha256(x.encode()).hexdigest(),
                'x',hashlib.sha256(y.encode()).hexdigest(),'x'))

    def final(self,gid='0022600001',start='2026-11-01T20:00:00Z',known='2026-11-01T23:00:00Z'):
        page=dict(props=dict(pageProps=dict(game=dict(gameId=gid,gameStatus=3,
            gameTimeUTC=start,period=4,homeTeam=dict(teamId='T1',score=110),
            awayTeam=dict(teamId='T2',score=100)))))
        raw=('<html><script id="__NEXT_DATA__">'+json.dumps(page)+
             '</script></html>').encode()
        digest=hashlib.sha256(raw).hexdigest();url=f'https://www.nba.com/game/{gid}/box-score'
        (self.folder/'raw').mkdir(exist_ok=True)
        (self.folder/'raw'/(digest+'.html')).write_bytes(raw)
        (self.folder/'raw'/(digest+'.provenance.json')).write_text(json.dumps(dict(
            source_url=url,sha256=digest,retrieved_at=known,bytes=len(raw))))
        rows=[]
        for team,other,fgm in [('T1','T2',9),('T2','T1',8)]:
            points=5*(2*fgm+4)
            for i in range(5):
                rows.append(dict(game_id=gid,season='2026-27',season_type='Regular Season',
                    game_time=start,known_at=known,source_url=url,source_sha256=digest,
                    source_provenance={'nba_full_boxscore':dict(source_url=url,sha256=digest,retrieved_at=known)},
                    team_boxscore={'pts':points},
                    team_boxscore_provenance=dict(game_id=gid,team_id=team,source_url=url,source_sha256=digest),
                    player_id=team+str(i),name=team+str(i),team_id=team,opponent_id=other,
                    status='played',minutes=48,game_period=4,fgm=fgm,fga=20,fg3m=2,fg3a=5,
                    ftm=2,fta=3,tov=2,pts=2*fgm+4))
        (self.folder/'finals'/(gid+'.json')).write_text(json.dumps(rows))

    def test_newest_unavailable_run_blocks_older_probability(self):
        self.run_row(margin=distribution('0022600001'))
        self.run_row(run='newer',created='2026-11-01T19:30:00Z',
                     margin=dict(distribution('0022600001'),status='unavailable',pmf=[]))
        self.final()
        result=collect_margin_samples(self.folder,MODEL,ASOF)
        self.assertEqual(result['samples'],[])
        self.assertEqual(result['counts']['selected_games'],1)
        self.assertEqual(result['exclusions']['latest_margin_unavailable'],1)

    def test_complete_final_yields_one_source_bound_margin(self):
        self.run_row(margin=distribution('0022600001'));self.final()
        result=collect_margin_samples(self.folder,MODEL,ASOF)
        self.assertEqual(len(result['samples']),1)
        self.assertEqual(result['samples'][0]['actual_home_margin'],10)
        self.assertEqual(result['samples'][0]['run_id'],'first')
        self.assertEqual(len(result['samples'][0]['actuals_sha256']),64)
        self.assertEqual(fit_prospective_margin(self.folder,MODEL,ASOF)['status'],'not_ready')

    def test_direct_official_final_without_supplemental_team_provenance_is_eligible(self):
        self.run_row(margin=distribution('0022600001'));self.final()
        path=self.folder/'finals/0022600001.json';rows=json.loads(path.read_text())
        for row in rows:row.pop('team_boxscore_provenance')
        path.write_text(json.dumps(rows))
        self.assertEqual(len(collect_margin_samples(self.folder,MODEL,ASOF)['samples']),1)

    def test_unchanged_official_page_refetched_later_keeps_first_archive_receipt(self):
        self.run_row(margin=distribution('0022600001'));self.final()
        path=self.folder/'finals/0022600001.json';rows=json.loads(path.read_text())
        later='2026-11-01T23:30:00Z'
        for row in rows:
            row['known_at']=later
            row['source_provenance']['nba_full_boxscore']['retrieved_at']=later
        path.write_text(json.dumps(rows))
        self.assertEqual(len(collect_margin_samples(self.folder,MODEL,ASOF)['samples']),1)

    def test_partial_final_or_future_known_result_never_fits(self):
        self.run_row(margin=distribution('0022600001'));self.final()
        path=self.folder/'finals/0022600001.json'
        rows=json.loads(path.read_text());path.write_text(json.dumps(rows[:-1]))
        partial=collect_margin_samples(self.folder,MODEL,ASOF)
        self.assertEqual(partial['samples'],[])
        self.assertEqual(partial['exclusions']['incomplete_final'],1)
        self.assertEqual(partial['errors'],[])
        self.final(known='2027-01-01T00:00:00Z')
        future=collect_margin_samples(self.folder,MODEL,ASOF)
        self.assertEqual(future['samples'],[])
        self.assertEqual(future['exclusions']['future_known_final'],1)
        self.assertEqual(future['errors'],[])

    def test_complete_structure_without_official_archive_is_excluded(self):
        self.run_row(margin=distribution('0022600001'));self.final()
        source=next((self.folder/'raw').glob('*.html'));source.unlink()
        result=collect_margin_samples(self.folder,MODEL,ASOF)
        self.assertEqual(result['samples'],[])
        self.assertEqual(result['exclusions']['unverified_final_source'],1)

    def test_official_page_score_must_match_saved_final(self):
        self.run_row(margin=distribution('0022600001'));self.final()
        path=self.folder/'finals/0022600001.json';rows=json.loads(path.read_text())
        for row in rows:
            if row['team_id']=='T1':row['pts']+=1;row['team_boxscore']['pts']+=5
        path.write_text(json.dumps(rows))
        result=collect_margin_samples(self.folder,MODEL,ASOF)
        self.assertEqual(result['samples'],[])
        self.assertEqual(result['exclusions']['invalid_margin_or_final'],1)

    def test_zero_minute_dnp_from_secondary_source_does_not_erase_official_team_proof(self):
        self.run_row(margin=distribution('0022600001'));self.final()
        path=self.folder/'finals/0022600001.json';rows=json.loads(path.read_text())
        dnp=dict(rows[0],player_id='T1-dnp',name='Bench DNP',status='dnp',minutes=0,
            fgm=0,fga=0,fg3m=0,fg3a=0,ftm=0,fta=0,tov=0,pts=0,
            source_url='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=123',
            source_sha256='c'*64)
        rows.append(dnp);path.write_text(json.dumps(rows))
        self.assertEqual(len(collect_margin_samples(self.folder,MODEL,ASOF)['samples']),1)

    def test_fit_uses_chronological_training_and_later_validation(self):
        from nba_forecast.prospective_margin_calibration import validate_margin_calibration, verify_margin_calibration_sources
        rows=[sample(i) for i in range(112)]
        collected=dict(samples=rows,counts=dict(selected_games=112,matched_games=112),exclusions={},errors=[])
        with patch('nba_forecast.prospective_margin_calibration.collect_margin_samples',return_value=collected):
            result=fit_prospective_margin(self.folder,MODEL,ASOF)
        self.assertEqual(result['status'],'ready')
        self.assertGreater(result['location_bias'],0)
        self.assertLess(result['training_latest_actual_known_at'],result['validation_first_forecast_created_at'])
        self.assertLess(result['validation_metrics']['candidate_ranked_probability_score'],
                        result['validation_metrics']['baseline_ranked_probability_score'])
        self.assertEqual(result['model_key'],MODEL)
        self.assertTrue(validate_margin_calibration(result,MODEL,ASOF))
        with patch('nba_forecast.prospective_margin_calibration.collect_margin_samples',return_value=collected):
            self.assertTrue(verify_margin_calibration_sources(self.folder,result))
        forged=dict(result,location_bias=8.)
        self.assertFalse(validate_margin_calibration(forged,MODEL,ASOF))
        forged=dict(result,evidence_sha256='0'*64)
        self.assertFalse(validate_margin_calibration(forged,MODEL,ASOF))
        altered=dict(rows[0],actual_home_margin=23)
        with patch('nba_forecast.prospective_margin_calibration.collect_margin_samples',
                   return_value=dict(collected,samples=[altered,*rows[1:]])):
            self.assertFalse(verify_margin_calibration_sources(self.folder,result))

    def test_failed_fixed_validation_window_cannot_pass_by_adding_games(self):
        rows=[dict(sample(i),actual_home_margin=15 if i<64 else -15) for i in range(112)]
        def collected(values):
            return dict(samples=values,counts=dict(selected_games=len(values),matched_games=len(values)),
                exclusions={},errors=[])
        with patch('nba_forecast.prospective_margin_calibration.collect_margin_samples',return_value=collected(rows)):
            first=fit_prospective_margin(self.folder,MODEL,ASOF)
        self.assertEqual(first['status'],'not_ready')
        self.assertEqual(first['reason'],'did_not_improve_later_games')
        additional=[dict(sample(i),actual_home_margin=15) for i in range(112,120)]
        with patch('nba_forecast.prospective_margin_calibration.collect_margin_samples',
                   return_value=collected(rows+additional)):
            second=fit_prospective_margin(self.folder,MODEL,ASOF)
        self.assertEqual(second,first)

    def test_training_boundary_moves_to_next_eastern_game_day(self):
        from nba_forecast.prospective_margin_calibration import _fit_window
        for per_day,training in ((9,72),(10,80)):
            with self.subTest(per_day=per_day):
                rows=[]
                for i in range(112):
                    row=sample(i)
                    game=datetime(2026,11,1,20,tzinfo=timezone.utc)+timedelta(
                        days=i//per_day,minutes=(i%per_day)*15)
                    row.update(game_time=game.isoformat(),
                        forecast_created_at=(game-timedelta(hours=1)).isoformat(),
                        actual_known_at=(game+timedelta(hours=3)).isoformat())
                    rows.append(row)
                result=_fit_window(rows,MODEL,ASOF)
                self.assertEqual(result['status'],'ready')
                self.assertEqual(result['training_games'],training)
                self.assertEqual(result['validation_games'],112-training)


if __name__=='__main__':unittest.main()
