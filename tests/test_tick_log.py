"""The tick log keeps a player sync's counts; its per-player blocks live in players/index.json."""
import json
from pathlib import Path
import tempfile
import unittest

from nba_forecast import operations


def synced(players=3):
    return dict(as_of='2026-11-06T18:42:42Z', players=players, history_rows=47533, errors=[],
                shot_games_pending=0, shot_games_verified=123,
                rotation_roles={str(i): dict(role='starter', basis='x' * 400) for i in range(players)},
                player_summaries={str(i): dict(name=f'Player {i}', points_average=12.5) for i in range(players)},
                shot_queue=[dict(game_id='0022600188', status='complete'), dict(game_id='0022600189', status='pending')])


class PlayerSyncSummaryTests(unittest.TestCase):
    def test_counts_stay_and_per_player_blocks_go(self):
        summary = operations.player_sync_summary(synced())
        self.assertEqual((summary['players'], summary['history_rows'], summary['errors']), (3, 47533, []))
        self.assertEqual((summary['shot_queue_games'], summary['shot_queue_pending'], summary['detail']), (2, 1, 'players/index.json'))
        for key in operations.PLAYER_SYNC_DETAIL_KEYS:
            self.assertNotIn(key, summary)
        self.assertLess(len(json.dumps(summary)), len(json.dumps(synced())) / 4)

    def test_a_sync_without_a_shot_queue_summarises_too(self):
        self.assertEqual(operations.player_sync_summary({'errors': []}), {'errors': [], 'detail': 'players/index.json'})


class CompactTicksTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.folder / 'ticks.jsonl'

    def rows(self):
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def test_old_rows_are_reduced_and_a_torn_line_is_kept(self):
        full = dict(started_at='2026-11-06T18:22:00+00:00', status='partial', player_sync=synced(200))
        slim = dict(started_at='2026-11-06T18:37:31+00:00', status='complete', player_sync=operations.player_sync_summary(synced()))
        with self.path.open('w') as f:
            f.write(json.dumps(full, sort_keys=True) + '\n')
            f.write(json.dumps(slim, sort_keys=True) + '\n')
            f.write('{"started_at": "2026-11-05T20:1')  # a write a power loss cut short
        before = self.path.stat().st_size
        result = operations.compact_ticks(self.folder)
        self.assertEqual((result['rows'], result['kept_lines'], result['bytes_before']), (2, 1, before))
        self.assertLess(result['bytes_after'], before / 4)
        self.assertEqual(result['bytes_after'], self.path.stat().st_size)
        lines = self.path.read_text().splitlines()
        self.assertEqual(len(lines), 3)
        first, second = json.loads(lines[0]), json.loads(lines[1])
        self.assertEqual(first['player_sync'], operations.player_sync_summary(synced(200)))
        self.assertEqual(first['status'], 'partial')
        self.assertEqual(second, slim, 'a row already reduced is unchanged')
        self.assertEqual(lines[2], '{"started_at": "2026-11-05T20:1')
        self.assertFalse(self.path.with_name('ticks.jsonl.compact').exists())

    def test_runs_under_the_tick_lock_and_tolerates_a_missing_log(self):
        self.assertEqual(operations.compact_ticks(self.folder), dict(rows=0, kept_lines=0, bytes_before=0, bytes_after=0))
        self.path.write_text(json.dumps(dict(status='complete')) + '\n')
        import fcntl
        with (self.folder / 'tick.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ValueError):
                operations.compact_ticks(self.folder)
        self.assertEqual(operations.compact_ticks(self.folder)['rows'], 1)
        self.assertEqual(self.rows(), [dict(status='complete')])


if __name__ == '__main__':
    unittest.main()
