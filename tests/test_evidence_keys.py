"""A forecast is rebuilt when its evidence changes, not on a clock."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import operations
from nba_forecast.schedule import normalize_season
from tests.test_operations import NOW
from tests.test_schedule import game, payload
from tests.test_store import inputs
from tests.test_tick_efficiency import at


def check(sha):
    return dict(games=[dict(game_id='0022600001', anchor_report=None,
                            latest_report=dict(source_url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-21_01_30PM.pdf', sha256=sha))])


class EvidenceKeyTests(unittest.TestCase):
    def setUp(self):
        schedule = normalize_season(payload([game()]), NOW)
        schedule['version'] = 'v1'
        roster = inputs()[0]['roster']
        roster['team_ids'] = [str(t) for t in range(1610612737, 1610612767)]
        for p, t in zip(roster['players'], roster['team_ids']):
            p['team_id'] = t
            p['status'] = 'unknown'
        roster['observed_at'] = NOW
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch('nba_forecast.operations.fetch_schedule', return_value=schedule))
        self.roster = self.enterContext(patch('nba_forecast.operations.fetch_roster', return_value=roster))
        self.enterContext(patch('nba_forecast.operations.capture_daily', return_value={'state': 'waiting'}))
        self.enterContext(patch('nba_forecast.operations.archive_daily_references',return_value=dict(sources=[],failures=[])))
        self.enterContext(patch('nba_forecast.operations.capture_game_lines', return_value=dict(evidence={})))
        self.enterContext(patch('nba_forecast.operations.capture_kalshi_lines', return_value=dict(captured=[], unchanged=[], skipped=[], errors=[])))
        self.preflight = self.enterContext(patch('nba_forecast.operations.run_preflight', return_value=dict(status='passed', issues=[])))
        self.injuries = self.enterContext(patch('nba_forecast.operations.fetch_injury_check', return_value=check('a' * 64)))

    def tick(self, seconds):
        from datetime import datetime
        clock = at(NOW, seconds)
        with patch('nba_forecast.operations._now', return_value=clock), \
             patch('nba_forecast.store._now', return_value=datetime.fromisoformat(clock)):
            return operations.run_tick(self.folder, [])

    def test_same_evidence_is_not_reforecast_and_new_report_edition_is(self):
        self.assertEqual(self.tick(0)['created_runs'], 1)
        self.assertEqual(self.tick(1000)['created_runs'], 0, 'same report, same roster: nothing to rebuild')
        self.assertEqual(self.preflight.call_count, 1)
        self.injuries.return_value = check('b' * 64)
        self.assertEqual(self.tick(1100)['created_runs'], 1, 'a new report edition rebuilds the forecast')
        self.assertEqual(self.tick(1200)['created_runs'], 0)
        state = json.loads((self.folder / 'state.json').read_text())
        self.assertEqual(len(state['jobs']), 2)
        self.assertTrue(all(':evidence:' in key for key in state['jobs']))

    def test_a_failed_job_is_retried_after_the_pause_not_every_minute(self):
        self.preflight.return_value = dict(status='blocked', issues=[dict(code='input_contract', detail='x', team_id=None)])
        self.tick(0)
        self.tick(60)
        self.assertEqual(self.preflight.call_count, 1)
        self.tick(900)
        self.assertEqual(self.preflight.call_count, 2)

    def test_unavailable_check_retries_every_fifteen_minutes(self):
        self.injuries.side_effect = ValueError('offline')
        self.tick(0)
        self.tick(600)
        self.assertEqual(self.preflight.call_count, 1)
        self.tick(900)
        self.assertEqual(self.preflight.call_count, 2)

    def test_roster_is_reused_for_preliminary_forecasts(self):
        self.tick(0)
        self.tick(1000)
        self.assertEqual(self.roster.call_count, 1)
        self.tick(3 * 3600 + 1)
        self.assertEqual(self.roster.call_count, 2)

    def test_market_captures_run_after_the_forecasts_within_a_budget(self):
        import time
        seen = []
        def capture(folder, games, season, as_of, deadline=None):
            seen.append((len(list((self.folder / 'runs').glob('*/forecast.json'))), deadline is not None and deadline - time.monotonic() <= operations.CAPTURE_BUDGET_SECONDS + 1))
            return dict(evidence={'0022600001': 'should not key this tick'})
        with patch('nba_forecast.operations.capture_game_lines', side_effect=capture):
            self.assertEqual(self.tick(0)['created_runs'], 1)
        self.assertEqual(seen, [(1, True)], 'the forecast was saved before the capture started, which had a deadline within the budget')
        self.assertEqual(self.tick(60)['created_runs'], 0, 'the capture return value is not evidence; the saved digest is')

    def test_the_saved_espn_digest_keys_the_forecast(self):
        self.assertEqual(self.tick(0)['created_runs'], 1)
        state = self.folder / 'market' / 'capture-state.json'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps(dict(version='x', captured={}, evidence={'0022600001': 'c' * 64})))
        self.assertEqual(self.tick(60)['created_runs'], 1, 'ESPN\'s injury list changed at the last capture')
        self.assertEqual(self.tick(120)['created_runs'], 0)
        state.write_text(json.dumps(dict(version='x', captured={}, evidence={'0022600002': 'd' * 64})))
        self.assertEqual(self.tick(180)['created_runs'], 0, 'another game\'s digest is not this game\'s evidence')


if __name__ == '__main__':
    unittest.main()
