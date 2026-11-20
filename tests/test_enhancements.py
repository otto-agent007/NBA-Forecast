import json,tempfile,unittest
from pathlib import Path
from nba_forecast.enhancements import install_artifact,load_active,prepare,verify_enhancements,_contemporary_games
from nba_forecast.schedule import canonical
import hashlib
from tests.test_model import snapshot


class EnhancementsTests(unittest.TestCase):
    def artifact(self):
        return dict(schema_version=1,model_version='regular-season-baseline-0.5.0',model_key='test',known_at='2026-01-10T10:00:00Z',
            parameters={},team_games=[],context_model=None,calibration=None,excluded_history_game_ids=[])

    def test_hashed_artifact_is_bound_to_cutoff_and_reproducible_environment(self):
        with tempfile.TemporaryDirectory() as d:
            artifact=self.artifact()
            sha=install_artifact(d,artifact)
            Path(d,'model-upgrade-active.json').write_text(json.dumps({'artifact_sha256':sha,'activated_at':'2026-01-10T11:00:00Z'}))
            loaded,digest=load_active(d,'2026-01-10T12:00:00Z')
            s=snapshot()
            s['model_enhancements']=prepare(loaded,digest,s['games'][0],s['as_of'])
            verify_enhancements(s,d)
            s['model_enhancements']['environment']['teams']['A']['pace_factor']=99
            with self.assertRaises(ValueError):
                verify_enhancements(s,d)
            Path(d,'model-artifacts',sha+'.json').write_text('{}')
            with self.assertRaises(ValueError):
                load_active(d,'2026-01-10T12:00:00Z')

    def test_refined_preparation_reads_no_saved_market_line(self):
        from nba_forecast.market_lines import record_game_lines
        from nba_forecast.refinement_upgrade import build_artifact
        from tests.test_margins import records
        with tempfile.TemporaryDirectory() as d:
            games=[dict(r,game_id=f'00226{i:05d}') for i,r in enumerate(records(8))]  # eight finals on 2026-01-01
            base=dict(self.artifact(),team_games=games,validation_report={})
            parent=install_artifact(d,base)
            refined=build_artifact(base,parent,'2026-01-10T10:00:00Z')
            sha=install_artifact(d,refined)
            Path(d,'model-upgrade-active.json').write_text(json.dumps({'artifact_sha256':sha,'activated_at':'2026-01-10T11:00:00Z'}))
            loaded,digest=load_active(d,'2026-01-10T12:00:00Z')
            s=snapshot()
            line=dict(spread=dict(home=dict(current_line=-4.0)),total=dict(over=dict(current_line=220.0)),
                      moneyline=dict(home=dict(current_odds=-150),away=dict(current_odds=130)))
            # One pregame capture retrieved before as_of; one (a recovered close) retrieved after it.
            for gid,retrieved in (('0022600000','2025-12-31T23:00:00Z'),('0022600001','2026-01-10T11:30:00Z')):
                record_game_lines(d,[dict(game_id=gid,provider_id='100',provider='Draft Kings',retrieved_at=retrieved,lines=line,
                                          **(dict(recovered_close=dict(recovered_at=retrieved)) if gid=='0022600001' else {}))],retrieved)
            without=prepare(loaded,digest,s['games'][0],'2026-01-10T11:00:00Z')
            with_lines=prepare(loaded,digest,s['games'][0],'2026-01-10T11:00:00Z',directory=d)
            self.assertNotIn('market_strength',with_lines['margin_inputs'])
            self.assertEqual(without['margin_inputs'],with_lines['margin_inputs'],'saved lines, even of tipped games, never reach the margin inputs')

    def test_future_active_configuration_cannot_be_used(self):
        with tempfile.TemporaryDirectory() as d:
            sha=install_artifact(d,self.artifact())
            Path(d,'model-upgrade-active.json').write_text(json.dumps({'artifact_sha256':sha,'activated_at':'2026-01-10T11:00:00Z'}))
            with self.assertRaises(ValueError):
                load_active(d,'2026-01-10T09:00:00Z')

    def test_final_corrections_preserve_prior_cutoff_and_archive_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            records=Path(d,'environment-finals/records')
            records.mkdir(parents=True)
            older=dict(game_id='g',game_time='2026-01-09T10:00:00Z',known_at='2026-01-10T10:00:00Z',value=1)
            newer=dict(older,known_at='2026-01-10T12:00:00Z',value=2)
            for record in (older,newer):
                raw=canonical(record)
                (records/(hashlib.sha256(raw).hexdigest()+'.json')).write_bytes(raw)
            self.assertEqual(_contemporary_games(d,'2026-01-10T11:00:00Z')[0],[older])
            self.assertEqual(_contemporary_games(d,'2026-01-10T13:00:00Z')[0],[newer])
            next(records.iterdir()).write_text('{}')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                _contemporary_games(d,'2026-01-10T13:00:00Z')
