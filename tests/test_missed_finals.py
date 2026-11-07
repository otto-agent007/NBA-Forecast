import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nba_forecast.operations import MISSED_FINAL_WINDOW_HOURS, _missed_finals

TIP = '2026-11-01T00:00:00+00:00'
FINAL_KEY = f'0022600150:{TIP}:final:model:regular-season-baseline-0.6.0:parser:layout-exact-4'


def schedule(*games):
    return dict(games=[dict(game_id=g, start_time=s, status=st) for g, s, st in games])


def tick(state, sched, folder, now):
    report = dict(started_at=now, missed_final_games=[])
    with patch('nba_forecast.operations._now', return_value=now):
        _missed_finals(state, sched, report, folder)
    return report


class MissedFinalsTests(unittest.TestCase):
    def test_a_recent_miss_marks_the_tick_and_is_recorded_once(self):
        state = dict(jobs={}, tracking_started_at='2026-10-23T00:00:00+00:00')
        sched = schedule(('0022600150', TIP, 'final'))
        with tempfile.TemporaryDirectory() as d:
            report = tick(state, sched, d, '2026-11-01T12:00:00+00:00')
            self.assertEqual(report['missed_final_games'], ['0022600150'])
            self.assertEqual(report['missed_final_record'], dict(total=1, recent=1, recovered=[], older=[]))
            self.assertEqual(state['missed_finals'], {'0022600150': dict(start_time=TIP, first_seen_at='2026-11-01T12:00:00+00:00')})
            saved = json.loads((Path(d) / 'missed-finals.json').read_text())
            self.assertEqual(saved['window_hours'], MISSED_FINAL_WINDOW_HOURS)
            self.assertEqual(saved['games'], [dict(game_id='0022600150', start_time=TIP, first_seen_at='2026-11-01T12:00:00+00:00')])
            # The next tick keeps the first sighting.
            report = tick(state, sched, d, '2026-11-01T13:00:00+00:00')
            self.assertEqual(report['missed_final_games'], ['0022600150'])
            self.assertEqual(state['missed_finals']['0022600150']['first_seen_at'], '2026-11-01T12:00:00+00:00')

    def test_an_old_miss_stays_on_the_record_without_marking_the_tick(self):
        state = dict(jobs={}, tracking_started_at='2026-10-23T00:00:00+00:00')
        sched = schedule(('0022600150', TIP, 'final'), ('0022600160', '2026-11-03T00:30:00+00:00', 'final'))
        with tempfile.TemporaryDirectory() as d:
            tick(state, sched, d, '2026-11-01T12:00:00+00:00')
            report = tick(state, sched, d, '2026-11-03T12:00:00+00:00')  # 60 h after the first tipoff, 11.5 h after the second
            self.assertEqual(report['missed_final_games'], ['0022600160'])
            self.assertEqual(report['missed_final_record'], dict(total=2, recent=1, recovered=[], older=['0022600150']))
            saved = json.loads((Path(d) / 'missed-finals.json').read_text())
            self.assertEqual([g['game_id'] for g in saved['games']], ['0022600150', '0022600160'])

    def test_a_final_run_clears_a_recorded_miss_and_undue_games_are_ignored(self):
        state = dict(jobs={}, tracking_started_at='2026-10-23T00:00:00+00:00')
        sched = schedule(('0022600150', TIP, 'final'),
                         ('0022600151', TIP, 'postponed'),
                         ('0022600152', None, 'scheduled'),
                         ('0022600001', '2026-10-22T23:30:00+00:00', 'final'),  # before tracking began
                         ('0022600170', '2026-11-02T01:00:00+00:00', 'scheduled'))  # not tipped yet
        with tempfile.TemporaryDirectory() as d:
            report = tick(state, sched, d, '2026-11-01T12:00:00+00:00')
            self.assertEqual(report['missed_final_games'], ['0022600150'])
            self.assertEqual(sorted(state['missed_finals']), ['0022600150'])
            # A recovered run saved before a crash carries the final key: the miss leaves the record.
            state['jobs'][FINAL_KEY] = 'run-1'
            report = tick(state, sched, d, '2026-11-01T13:00:00+00:00')
            self.assertEqual(report['missed_final_games'], [])
            self.assertEqual(report['missed_final_record'], dict(total=0, recent=0, recovered=[], older=[]))
            self.assertEqual(json.loads((Path(d) / 'missed-finals.json').read_text())['games'], [])

    def test_a_recovered_miss_stays_on_the_record_without_marking_the_tick(self):
        state = dict(jobs={}, tracking_started_at='2026-10-23T00:00:00+00:00')
        sched = schedule(('0022600150', TIP, 'final'), ('0022600151', TIP, 'final'))
        done = dict(reports='recorded', closing_lines='exists', kalshi='unchanged')
        def recovered(**statuses):
            return dict(start_time=TIP, sources={s: dict(status=v, attempts=1) for s, v in statuses.items()})
        with tempfile.TemporaryDirectory() as d:
            report = tick(state, sched, d, '2026-11-01T12:00:00+00:00')
            self.assertEqual(report['missed_final_games'], ['0022600150', '0022600151'])
            # The derived service recovered every source of the first game; the second still waits on Kalshi.
            (Path(d) / 'recovered-evidence.json').write_text(json.dumps(dict(games={
                '0022600150': recovered(**done), '0022600151': recovered(**dict(done, kalshi='unavailable'))})))
            report = tick(state, sched, d, '2026-11-01T13:00:00+00:00')
            self.assertEqual(report['missed_final_games'], ['0022600151'])
            self.assertEqual(report['missed_final_record'], dict(total=2, recent=1, recovered=['0022600150'], older=[]))
            # The checkpoint itself is still missed: the record keeps both games.
            self.assertEqual([g['game_id'] for g in json.loads((Path(d) / 'missed-finals.json').read_text())['games']],
                             ['0022600150', '0022600151'])
            # A source that was never attempted is not recovered.
            (Path(d) / 'recovered-evidence.json').write_text(json.dumps(dict(games={
                '0022600150': recovered(reports='recorded', closing_lines='recorded')})))
            report = tick(state, sched, d, '2026-11-01T14:00:00+00:00')
            self.assertEqual(report['missed_final_games'], ['0022600150', '0022600151'])
            # After the window both are older misses, recovered or not.
            report = tick(state, sched, d, '2026-11-03T01:00:00+00:00')
            self.assertEqual(report['missed_final_record'], dict(total=2, recent=0, recovered=[], older=['0022600150', '0022600151']))


if __name__ == '__main__':
    unittest.main()
