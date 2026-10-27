"""Game lines are archived with provenance and compared with saved forecasts and finals."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import market_lines as M

URL = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909848'
META = dict(source_url=URL, retrieved_at='2026-10-23T21:00:00Z', sha256='b' * 64)
GAME = dict(game_id='0022600010', start_time='2026-10-23T23:00:00Z', home_team_id='H', away_team_id='A',
            home_tricode='BOS', away_tricode='NYK', status='scheduled')


def summary(spread_home='-2.5', total='220.5', home_ml='-148', away_ml='+124', provider=('100', 'Draft Kings')):
    entry = dict(provider=dict(id=provider[0], name=provider[1]), details='BOS ' + spread_home, spread=float(spread_home), overUnder=float(total),
                 homeTeamOdds=dict(favorite=True, moneyLine=int(home_ml), spreadOdds=-115.0, teamId='2'),
                 awayTeamOdds=dict(favorite=False, moneyLine=int(away_ml), spreadOdds=-105.0, teamId='18'),
                 pointSpread=dict(home=dict(close=dict(line=spread_home, odds='-115'), open=dict(line='-2', odds='-110')),
                                  away=dict(close=dict(line='+' + spread_home.lstrip('-'), odds='-105'), open=dict(line='+2', odds='-110'))),
                 total=dict(over=dict(close=dict(line='o' + total, odds='-105'), open=dict(line='o223.5', odds='-110')),
                            under=dict(close=dict(line='u' + total, odds='-115'), open=dict(line='u223.5', odds='-110'))),
                 moneyline=dict(home=dict(close=dict(odds=home_ml), open=dict(odds='-135')), away=dict(close=dict(odds=away_ml), open=dict(odds='+114'))))
    return dict(header=dict(id='401909848', season=dict(year=2027, type=2), competitions=[dict(id='401909848', date=GAME['start_time'], competitors=[
        dict(homeAway='home', team=dict(id='2', abbreviation='BOS')), dict(homeAway='away', team=dict(id='18', abbreviation='NY'))])]),
        pickcenter=[entry])


class ExtractionTests(unittest.TestCase):
    def test_numbers_and_sides(self):
        self.assertEqual([M._number(x) for x in ('+124', '-148', '-2.5', 'o220.5', 'u220.5', 'EVEN', 'PK', None, 'n/a')],
                         [124, -148, -2.5, 220.5, 220.5, 100, 0.0, None, None])
        records = M.extract_game_lines(summary(), GAME, '2026-27', META)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual((r['provider_id'], r['provider'], r['event_id'], r['game_id']), ('100', 'Draft Kings', '401909848', '0022600010'))
        self.assertEqual(r['lines']['spread']['home'], dict(current_line=-2.5, current_odds=-115, open_line=-2, open_odds=-110))
        self.assertEqual(r['lines']['spread']['away']['current_line'], 2.5)
        self.assertEqual(r['lines']['total']['over'], dict(current_line=220.5, current_odds=-105, open_line=223.5, open_odds=-110))
        self.assertEqual((r['lines']['moneyline']['home']['current_odds'], r['lines']['moneyline']['away']['open_odds']), (-148, 114))
        self.assertEqual((r['source_url'], r['sha256']), (URL, 'b' * 64))

    def test_legacy_payload_uses_the_favorite_spread(self):
        payload = summary(); entry = payload['pickcenter'][0]
        for key in ('pointSpread', 'total', 'moneyline'):
            entry.pop(key)
        entry['homeTeamOdds']['favorite'] = False; entry['awayTeamOdds']['favorite'] = True; entry['spread'] = -3.0
        r = M.extract_game_lines(payload, GAME, '2026-27', META)[0]
        self.assertEqual((r['lines']['spread']['home']['current_line'], r['lines']['spread']['away']['current_line']), (3.0, -3.0))
        self.assertEqual(r['lines']['total']['over']['current_line'], 220.5)
        self.assertEqual(r['lines']['moneyline']['home']['current_odds'], -148)

    def test_identity_mismatch_is_refused(self):
        with self.assertRaises(ValueError):
            M.extract_game_lines(summary(), dict(GAME, start_time='2026-10-24T23:00:00Z'), '2026-27', META)
        with self.assertRaises(ValueError):
            M.extract_game_lines(summary(), GAME, '2026-27', dict(META, source_url='https://example.test/x'))


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_appends_only_when_lines_change_and_closing_is_the_last_pregame_capture(self):
        first = M.extract_game_lines(summary(), GAME, '2026-27', META)
        self.assertEqual(len(M.record_game_lines(self.folder, first, '2026-10-23T21:00:30Z')), 1)
        self.assertEqual(M.record_game_lines(self.folder, first, '2026-10-23T21:15:30Z'), [])
        moved = M.extract_game_lines(summary(spread_home='-3.5'), GAME, '2026-27', dict(META, retrieved_at='2026-10-23T22:30:00Z', sha256='c' * 64))
        self.assertEqual(len(M.record_game_lines(self.folder, moved, '2026-10-23T22:30:30Z')), 1)
        late = M.extract_game_lines(summary(spread_home='-5.5'), GAME, '2026-27', dict(META, retrieved_at='2026-10-23T23:30:00Z', sha256='d' * 64))
        M.record_game_lines(self.folder, late, '2026-10-23T23:30:30Z')
        records = M.read_game_lines(self.folder, GAME['game_id'])
        self.assertEqual([r['capture_index'] for r in records], [0, 1, 2])
        closing = M.closing_lines(records, GAME['start_time'])
        self.assertEqual(closing['100']['lines']['spread']['home']['current_line'], -3.5)
        index = M.build_index(self.folder)['games'][GAME['game_id']]
        self.assertEqual((index['captures'], index['providers']['100']['first']['spread']['home']['current_line']), (3, -2.5))

    def test_a_cut_off_final_line_is_dropped_and_the_file_repaired(self):
        first = M.extract_game_lines(summary(), GAME, '2026-27', META)
        M.record_game_lines(self.folder, first, '2026-10-23T21:00:30Z')
        path = M._lines_path(self.folder, GAME['game_id'])
        with path.open('ab') as stream:
            stream.write(b'{"game_id": "0022600010", "cut')  # a write interrupted mid-line
        self.assertEqual(len(M.read_game_lines(self.folder, GAME['game_id'])), 1)
        self.assertEqual(M.build_index(self.folder)['games'][GAME['game_id']]['captures'], 1)
        moved = M.extract_game_lines(summary(spread_home='-3.5'), GAME, '2026-27', dict(META, sha256='c' * 64))
        self.assertEqual(len(M.record_game_lines(self.folder, moved, '2026-10-23T22:30:30Z')), 1)
        records = M.read_game_lines(self.folder, GAME['game_id'])
        self.assertEqual([r['capture_index'] for r in records], [0, 1])
        self.assertEqual(len(path.read_bytes().splitlines()), 2)
        self.assertFalse(list(path.parent.glob('.*.tmp')))
        # Damage before the end is a real problem and is reported, never skipped.
        path.write_bytes(b'{"cut\n' + path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'damaged'):
            M.read_game_lines(self.folder, GAME['game_id'])
        index = M.build_index(self.folder)
        self.assertEqual((index['damaged'], index['games']), ([GAME['game_id']], {}))
        report = M.market_report(self.folder, dict(season='2026-27', games=[GAME]), '2026-10-24T05:00:00Z',
                                 selected={(GAME['game_id'], 'final'): dict(run_id='r1')})
        self.assertEqual([e['game_id'] for e in report['errors']], [GAME['game_id']])

    def test_capture_index_counts_each_game_on_its_own(self):
        first = M.extract_game_lines(summary(), GAME, '2026-27', META)
        records = first + [dict(first[0], game_id='0022600011')]
        appended = M.record_game_lines(self.folder, records, '2026-10-23T21:00:30Z')
        self.assertEqual([(e['game_id'], e['capture_index']) for e in appended], [('0022600010', 0), ('0022600011', 0)])

    def test_capture_uses_cadence_slots_and_survives_failures(self):
        games = [GAME, dict(GAME, game_id='0022600011', home_tricode='LAL', away_tricode='DEN')]
        def event(game, directory):
            if game['game_id'] == '0022600011':
                raise OSError('offline')
            return dict(provider_event_id='401909848'), META
        with patch('nba_forecast.reference.scoreboard_event', side_effect=event), \
             patch('nba_forecast.reference.fetch_reference', return_value=(summary(), META)):
            report = M.capture_game_lines(self.folder, games, '2026-27', '2026-10-23T21:00:30Z')
            self.assertEqual([c['game_id'] for c in report['captured']], ['0022600010'])
            self.assertEqual([e['game_id'] for e in report['errors']], ['0022600011'])
            again = M.capture_game_lines(self.folder, games, '2026-27', '2026-10-23T21:05:00Z')
            self.assertEqual(again['skipped'], ['0022600010'])
            later = M.capture_game_lines(self.folder, games, '2026-27', '2026-10-23T22:45:00Z')
            self.assertEqual([c['game_id'] for c in later['unchanged']], ['0022600010'])
            after_tip = M.capture_game_lines(self.folder, games, '2026-27', '2026-10-23T23:30:00Z')
            self.assertEqual({k: after_tip[k] for k in ('captured', 'unchanged', 'skipped', 'errors')}, dict(captured=[], unchanged=[], skipped=[], errors=[]))
            self.assertIn('0022600010', after_tip['evidence'])
        self.assertTrue((self.folder / 'market' / 'index.json').exists())


class ReportTests(unittest.TestCase):
    def test_report_scores_leans_and_head_to_head_error(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d); (folder / 'finals').mkdir()
            M.record_game_lines(folder, M.extract_game_lines(summary(), GAME, '2026-27', META), '2026-10-23T21:00:30Z')
            finals = [dict(game_id=GAME['game_id'], team_id='H', pts=60), dict(game_id=GAME['game_id'], team_id='H', pts=55),
                      dict(game_id=GAME['game_id'], team_id='A', pts=50), dict(game_id=GAME['game_id'], team_id='A', pts=58)]
            (folder / 'finals' / (GAME['game_id'] + '.json')).write_text(json.dumps(finals))
            selected = {(GAME['game_id'], 'final'): dict(run_id='r1', totals=dict(points_mean=224, teams=[
                dict(team_id='H', side='home', points_mean=114), dict(team_id='A', side='away', points_mean=110)]),
                margin_forecast=dict(home_win_probability=.6))}
            report = M.market_report(folder, dict(season='2026-27', games=[GAME]), '2026-10-24T05:00:00Z', selected=selected)
        row = report['games'][0]; s = report['summary']
        # Home won 115-108: margin +7 against a -2.5 spread (home covered), total 223 over 220.5.
        self.assertEqual((row['model_margin'], row['market_margin'], row['spread_edge'], row['home_covered']), (4, 2.5, 1.5, True))
        self.assertEqual((row['total_edge'], row['went_over']), (3.5, True))
        self.assertEqual(row['margin_abs_error'], dict(model=3, market=4.5))
        self.assertEqual(s['spread_leans']['edge>=1'], dict(picks=1, wins=1, losses=0, pushes=0, win_rate=1.0))
        self.assertEqual(s['spread_leans']['edge>=2']['picks'], 0)
        self.assertEqual(s['total_leans']['edge>=3'], dict(picks=1, wins=1, losses=0, pushes=0, win_rate=1.0))
        self.assertAlmostEqual(row['market_home_win'], 0.5721, places=3)
        self.assertEqual(s['winner']['model_pick_accuracy'], 1.0)
        self.assertAlmostEqual(s['winner']['model_brier'], .16)


if __name__ == '__main__':
    unittest.main()
