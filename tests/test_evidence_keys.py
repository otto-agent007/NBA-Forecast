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


class TickFixture(unittest.TestCase):
    """A one-game slate whose sources are patched; ``tick`` runs one operations tick at NOW + seconds."""

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


class EvidenceKeyTests(TickFixture):
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



GAME = '0022600001'
HOME, AWAY, ELSEWHERE = '1610612752', '1610612738', '1610612737'  # P16, P2, P1 in the fixture roster


def edition(sha, published='2026-10-21T15:30:00+00:00'):
    from datetime import datetime, timedelta
    retrieved = (datetime.fromisoformat(published) + timedelta(minutes=5)).isoformat()
    report = dict(source_url=f'https://ak-static.cms.nba.com/referee/injury/Injury-Report_{sha[:6]}.pdf', sha256=sha,
                  published_at=published, retrieved_at=retrieved)
    return dict(games=[dict(game_id=GAME, start_time='2026-10-22T00:00:00+00:00', checkpoint_at='2026-10-21T23:30:00+00:00',
                            phase='preliminary', anchor_report=dict(report), latest_report=dict(report),
                            report_status='captured_pending_review', later_report_available=False, action='x')])


def extraction(evidence, statuses, other_rows=()):
    """A parsed edition covering this game's two teams; ``statuses`` maps player_id -> (team, status, reported)."""
    rows = [dict(game_id=GAME, player_id=pid, team_id=team, status=status, reported_status=reported,
                 source_line=f'{pid} {reported} {evidence["sha256"][:4]}', source_name=pid, **evidence)
            for pid, (team, status, reported) in statuses.items()]
    rows += [dict(row, **evidence) for row in other_rows]
    coverage = [dict(date='2026-10-21', matchup='BOS@NYK', team_id=t) for t in (HOME, AWAY)]
    return dict(evidence=evidence, rows=rows, team_coverage=coverage, complete_teams=coverage, issues=[],
                pages_complete=True, heading_matches=True)


class PerGameEvidenceTests(TickFixture):
    """Only what this game's forecast is built from rebuilds it."""

    def setUp(self):
        super().setUp()
        self.parses = {}
        self.enterContext(patch('nba_forecast.operations.pdf_text', return_value='Injury Report:'))
        self.enterContext(patch('nba_forecast.operations.parse_report',
                                side_effect=lambda text, schedule, roster, evidence: self.parses[evidence['sha256']](evidence)))
        self.parses['a' * 64] = lambda e: extraction(e, {'P16': (HOME, 'out', 'Out')})
        self.injuries.return_value = edition('a' * 64)

    def records(self):
        return sorted((self.folder / 'injuries/games' / GAME / 'records').glob('*.json'))

    def test_an_edition_that_leaves_this_game_alone_makes_no_run_and_is_still_archived(self):
        self.assertEqual(self.tick(0)['created_runs'], 1)
        self.assertEqual(len(self.records()), 1)
        other = dict(game_id='0022600002', player_id='P1', team_id=ELSEWHERE, status='out', reported_status='Out')
        self.parses['b' * 64] = lambda e: extraction(e, {'P16': (HOME, 'out', 'Out')}, [other])
        self.injuries.return_value = edition('b' * 64, published='2026-10-21T15:45:00+00:00')
        self.assertEqual(self.tick(900)['created_runs'], 0, 'same lines for this game in a new edition')
        self.assertEqual(len(self.records()), 2, 'the new edition was checked and archived')
        latest = json.loads((self.folder / 'injuries/games' / GAME / 'latest.json').read_text())
        self.assertEqual(latest['games'][0]['latest_report']['sha256'], 'b' * 64)
        self.assertEqual(self.tick(960)['created_runs'], 0)
        self.assertEqual(len(self.records()), 2, 'the same edition is archived once')

    def test_an_edition_that_changes_this_games_lines_rebuilds(self):
        self.assertEqual(self.tick(0)['created_runs'], 1)
        self.parses['c' * 64] = lambda e: extraction(e, {'P16': (HOME, 'questionable', 'Questionable')})
        self.injuries.return_value = edition('c' * 64)
        self.assertEqual(self.tick(900)['created_runs'], 1, 'out -> questionable')
        self.parses['d' * 64] = lambda e: extraction(e, {'P16': (HOME, 'questionable', 'Probable')})
        self.injuries.return_value = edition('d' * 64)
        self.assertEqual(self.tick(1800)['created_runs'], 1, 'the reported status alone is evidence')

    def test_roster_moves_elsewhere_do_not_rebuild_and_on_these_teams_do(self):
        self.assertEqual(self.tick(0)['created_runs'], 1)
        path = self.folder / 'roster.json'
        roster = json.loads(path.read_text())
        for p in roster['players']:
            if p['team_id'] == ELSEWHERE:
                p['status'] = 'out'
        path.write_text(json.dumps(roster))
        self.assertEqual(self.tick(60)['created_runs'], 0, 'another team\'s player')
        attempts = self.preflight.call_count
        roster['players'].append(dict(player_id='P31', name='Player 31', team_id=AWAY, status='unknown'))
        path.write_text(json.dumps(roster))
        self.tick(120)
        self.assertEqual(self.preflight.call_count, attempts + 1, 'a new member of this game\'s team rebuilds it')

    def test_league_inputs_rebuild_and_an_upcoming_games_line_capture_does_not(self):
        import hashlib
        self.assertEqual(self.tick(0)['created_runs'], 1)
        record = json.dumps(dict(game_id='0022600000', game_time='2026-10-20T23:00:00+00:00', known_at='2026-10-21T02:00:00+00:00',
                                 home_team_id=HOME, away_team_id=ELSEWHERE)).encode()
        finals = self.folder / 'environment-finals/records'
        finals.mkdir(parents=True)
        (finals / (hashlib.sha256(record).hexdigest() + '.json')).write_bytes(record)
        self.assertEqual(self.tick(60)['created_runs'], 1, 'an environment final')
        lines = self.folder / 'market/game-lines'
        lines.mkdir(parents=True)
        (lines / (GAME + '.jsonl')).write_text('{}\n')
        self.assertEqual(self.tick(120)['created_runs'], 0, 'a capture of this upcoming game is not a preparation input')
        (lines / '0022600000.jsonl').write_text('{}\n')
        self.assertEqual(self.tick(180)['created_runs'], 1, 'a tipped game\'s close prices the team ratings')
        shots = self.folder / 'shots'
        shots.mkdir(exist_ok=True)
        (shots / '0022600000.json').write_text(json.dumps(dict(missing_reason='Shot evidence queued for collection')))
        self.assertEqual(self.tick(240)['created_runs'], 0, 'a pending shot file is read by no preparation')
        (shots / '0022600000.json').write_text(json.dumps(dict(game_id='0022600000', shots=[])))
        self.assertEqual(self.tick(270)['created_runs'], 1, 'a collected shot file')
        raw = self.folder / 'raw'
        raw.mkdir(exist_ok=True)
        (raw / ('e' * 64 + '.provenance.json')).write_text(json.dumps(dict(source_url='https://official.nba.com/nba-injury-report-2026-27-season/')))
        self.assertEqual(self.tick(300)['created_runs'], 0, 'an archive the preparation does not read')
        (raw / ('f' * 64 + '.provenance.json')).write_text(json.dumps(dict(source_url='https://www.nba.com/game/0022600000/box-score')))
        self.assertEqual(self.tick(360)['created_runs'], 1, 'an NBA box-score page (injury role context)')

    def test_a_new_final_in_the_history_rebuilds(self):
        from tests.test_store import actual
        from datetime import datetime
        def final(gid, day):
            return [dict(actual(player), game_id=gid, game_time=f'2026-10-{day}T23:00:00Z', known_at=f'2026-10-{day + 1}T02:00:00Z')
                    for player in ('P1', 'P2')]
        history = final('0022500001', 18) + final('0022500002', 19)
        def tick(seconds, rows):
            clock = at(NOW, seconds)
            with patch('nba_forecast.operations._now', return_value=clock), \
                 patch('nba_forecast.store._now', return_value=datetime.fromisoformat(clock)):
                return operations.run_tick(self.folder, rows)
        self.assertEqual(tick(0, history[:2])['created_runs'], 1)
        self.assertEqual(tick(60, history[:2])['created_runs'], 0)
        self.assertEqual(tick(120, history)['created_runs'], 1, 'a final joined the history')


class ReportStateTests(unittest.TestCase):
    def setUp(self):
        self.game = dict(game_id=GAME, home_team_id=HOME, away_team_id=AWAY, start_time='2026-10-22T00:00:00+00:00',
                         home_tricode='NYK', away_tricode='BOS')
        self.now = operations.parse_time('2026-10-21T16:00:00+00:00')
        self.roster = dict(players=[])

    def state(self, check, parse):
        with patch('nba_forecast.operations.pdf_text', return_value='Injury Report:'), \
             patch('nba_forecast.operations.parse_report', side_effect=lambda t, s, r, e: parse(e)):
            return operations._report_state(Path('/nonexistent'), check, self.game, {'games': []}, self.roster, {}, self.now)

    def test_edition_metadata_and_other_games_are_not_this_games_state(self):
        a = self.state(edition('a' * 64), lambda e: extraction(e, {'P16': (HOME, 'out', 'Out')}))
        other = dict(game_id='0022600002', player_id='P1', team_id=ELSEWHERE, status='out', reported_status='Out')
        b = self.state(edition('b' * 64, published='2026-10-21T15:45:00+00:00'),
                       lambda e: dict(extraction(e, {'P16': (HOME, 'out', 'Out')}, [other]),
                                      issues=[dict(date='2026-10-21', kind='team_not_submitted', matchup='MIL@NYC', team_id=ELSEWHERE)]))
        self.assertEqual(a, b)
        c = self.state(edition('c' * 64), lambda e: dict(extraction(e, {'P16': (HOME, 'out', 'Out')}),
                                                        issues=[dict(date='2026-10-21', kind='team_not_submitted', matchup='BOS@NYK', team_id=AWAY)]))
        self.assertNotEqual(a, c, 'an issue for this matchup decides the no-injury supplement')

    def test_a_stale_or_unparsed_edition(self):
        stale = self.state(edition('a' * 64, published='2026-10-20T15:00:00+00:00'), lambda e: extraction(e, {}))
        self.assertFalse(stale['applied'], 'more than 24 hours old: apply_report refuses it')
        def broken(e):
            raise ValueError('unreadable')
        self.assertEqual(self.state(edition('b' * 64), broken), dict(edition='b' * 64))


class LeagueInputTests(unittest.TestCase):
    def test_once_per_tick_digest_ignores_report_and_upcoming_line_archives(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        now = '2026-10-21T16:00:00+00:00'
        first = operations._league_inputs(folder, [], None, now)
        (folder / 'raw').mkdir()
        (folder / 'raw' / ('a' * 64 + '.pdf')).write_bytes(b'%PDF-')
        (folder / 'market/game-lines').mkdir(parents=True)
        (folder / 'market/game-lines' / (GAME + '.jsonl')).write_text('{}\n')
        self.assertEqual(operations._league_inputs(folder, [], None, now), first)
        model = dict(team_games=[dict(game_id=GAME, game_time='2026-10-22T00:00:00+00:00')])
        self.assertEqual(operations._league_inputs(folder, [], model, now), first, 'not tipped yet')
        self.assertNotEqual(operations._league_inputs(folder, [], model, '2026-10-22T03:00:00+00:00'),
                            operations._league_inputs(folder, [], None, '2026-10-22T03:00:00+00:00'), 'tipped: its close is priced')


if __name__ == '__main__':
    unittest.main()
