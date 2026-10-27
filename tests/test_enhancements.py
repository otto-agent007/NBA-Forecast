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
