"""Location evidence changes current conversion without duplicating outcomes."""
import copy
import gzip
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from nba_forecast.data import STAT_FIELDS, archive_payload
from nba_forecast.model import _shooting
from nba_forecast.schedule import atomic_json
from nba_forecast.shots import normalize_page_shots
from tests.test_model import row

try:
    from nba_forecast import shot_quality
except ImportError:
    shot_quality = None


class ShotQualityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shot_quality, 'Source-bound shot quality must be implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.snapshot = dict(as_of='2026-10-23T00:00:00Z', season='2026-27',
            games=[dict(game_id='0022600020', season_type='Regular Season',
                start_time='2026-10-23T20:00:00Z', home_team_id='1', away_team_id='2')],
            roster=dict(players=[dict(player_id='7', team_id='1'), dict(player_id='8', team_id='1'),
                dict(player_id='9', team_id='2')]))
        self.history = []
        self.install_target(1)
        self.install_target(2)
        # Both peer teams are outside the forecast slate. Eight of ten near
        # attempts and two of ten midrange attempts are made by each peer.
        peers = [(str(100+i), '3' if i < 5 else '4',
                  [(0, 20, j < 8) for j in range(10)] +
                  [(180, 100, j < 2) for j in range(10)]) for i in range(10)]
        self.install_game(3, peers, home='3', away='4')

    def install_target(self, index, *, location=(0, 20), made=3, missing=False):
        attempts = [(*location, j < made) for j in range(6)]
        if missing:
            attempts = [(None, None, made) for _, _, made in attempts]
        self.install_game(index, [('7', '1', attempts), ('8', '1', []), ('9', '2', [])])

    def install_game(self, index, people, *, home='1', away='2', season='2026-27'):
        year = int(season[:4])
        gid = f'002{year % 100:02d}{index:05d}'
        start = f'{year}-10-{18+index:02d}T00:00:00Z'
        known = f'{year}-10-{18+index:02d}T04:00:00Z'
        game = dict(game_id=gid, start_time=start, season_type='Regular Season',
                    home_team_id=home, away_team_id=away)
        actions, rows, home_players, away_players = [], [], [], []
        for pid, team, attempts in people:
            made = sum(int(a[2]) for a in attempts)
            r = row(pid=pid, team=team, opponent_id=away if team == home else home,
                game_id=gid, game_time=start, known_at=known, season=season,
                fga=len(attempts), fgm=made, fg3a=0, fg3m=0, ftm=0, fta=0)
            rows.append(r)
            stats = {source: r[field] for field, source in STAT_FIELDS.items()}
            stats['minutes'] = '30:00'
            person = dict(personId=int(pid), firstName='Player', familyName=pid, comment='', statistics=stats)
            (home_players if team == home else away_players).append(person)
            for x, y, is_made in attempts:
                actions.append(dict(actionNumber=len(actions)+1, period=1, clock='PT10M00.00S',
                    personId=int(pid), teamId=int(team), actionType='Made Shot' if is_made else 'Missed Shot',
                    isFieldGoal=1, shotValue=2, shotResult='Made' if is_made else 'Missed', xLegacy=x, yLegacy=y))
        actions += [dict(actionNumber=len(actions)+10+i, period=i, clock='PT00M00.00S',
                         actionType='period', subType='end', isFieldGoal=0) for i in range(1, 5)]
        teams = {}
        for side, tid, players in (('homeTeam', home, home_players), ('awayTeam', away, away_players)):
            teams[side] = dict(teamId=int(tid), players=players, statistics={},
                               score=sum(p['statistics']['points'] for p in players))
        page = dict(game=dict(gameId=gid, gameStatus=3, gameTimeUTC=start, period=4, **teams),
                    playByPlay=dict(gameId=gid, actions=actions))
        raw = ('<script id="__NEXT_DATA__">'+json.dumps({'props': {'pageProps': page}})+'</script>').encode()
        meta = archive_payload(raw, f'https://www.nba.com/game/{gid}/box-score', known, self.directory/'raw')
        source = normalize_page_shots(page, game, meta, final_rows=rows)
        atomic_json(self.directory/'shots'/(gid+'.json'), source)
        self.history = [r for r in self.history if r['game_id'] != gid] + rows
        return source

    def prepare(self):
        prepared = shot_quality.prepare_shot_quality(self.directory, self.snapshot, self.history)
        self.snapshot['shot_quality'] = prepared
        return prepared

    def shooting(self):
        current = sorted((r for r in self.history if r['player_id'] == '7' and r['season'] == '2026-27'),
                         key=lambda r: (r['game_time'], r['game_id']))
        prior = [row(fga=100, fgm=60, fg3a=0, fg3m=0, fta=0, ftm=0)]
        return _shooting(prior, current)

    def test_supported_mix_changes_scoring_in_both_directions_and_keeps_prior(self):
        self.prepare()
        original = self.shooting()
        before = copy.deepcopy(original)
        near, audit = shot_quality.adjust_shooting(original, self.snapshot, '7')
        self.assertGreater(near['two_point']['percentage'], original['two_point']['percentage'])
        self.assertEqual(audit['status'], 'applied')
        self.assertEqual(original, before)
        for key in original['two_point']:
            if key != 'percentage':
                self.assertEqual(near['two_point'][key], original['two_point'][key])
        self.assertEqual(near['three_point'], original['three_point'])
        self.assertEqual(near['free_throw'], original['free_throw'])
        self.install_target(1, location=(180, 100))
        self.install_target(2, location=(180, 100))
        self.prepare()
        mid, _ = shot_quality.adjust_shooting(original, self.snapshot, '7')
        self.assertLess(mid['two_point']['percentage'], original['two_point']['percentage'])

    def test_target_hot_outcomes_do_not_change_peer_expectation(self):
        self.prepare()
        _, cold_audit = shot_quality.adjust_shooting(self.shooting(), self.snapshot, '7')
        self.install_target(1, made=6)
        self.install_target(2, made=6)
        self.prepare()
        base = self.shooting()
        adjusted, hot_audit = shot_quality.adjust_shooting(base, self.snapshot, '7')
        self.assertLess(adjusted['two_point']['percentage'], base['two_point']['percentage'])
        self.assertEqual(hot_audit['families']['two_point']['expected_located_makes'],
                         cold_audit['families']['two_point']['expected_located_makes'])
        self.assertAlmostEqual(hot_audit['families']['two_point']['expected_located_makes'], 7.7415)

    def test_missing_and_sparse_profiles_preserve_exact_shooting(self):
        original = self.shooting()
        self.assertEqual(shot_quality.adjust_shooting(original, self.snapshot, '7')[0], original)
        self.install_target(1, missing=True)
        self.install_target(2, missing=True)
        self.prepare()
        changed, audit = shot_quality.adjust_shooting(original, self.snapshot, '7')
        self.assertEqual(changed, original)
        self.assertEqual(audit['status'], 'fallback')
        self.install_target(1)
        self.prepare()
        self.assertEqual(shot_quality.adjust_shooting(original, self.snapshot, '7')[0], original)

    def test_replay_is_immutable_and_rejects_summary_and_raw_tampering(self):
        prepared = self.prepare()
        self.assertEqual(len(prepared['sources']), 3)
        self.assertEqual(prepared['coverage']['located_attempts'], 212)
        shot_quality.verify_shot_quality(self.snapshot, self.history, self.directory)
        for path in (self.directory/'shots').glob('*.json'):
            path.unlink()
        shot_quality.verify_shot_quality(self.snapshot, self.history, self.directory)
        corrupt = copy.deepcopy(prepared)
        corrupt['pool'][0]['makes'] += 1
        self.snapshot['shot_quality'] = corrupt
        with self.assertRaises(ValueError):
            shot_quality.verify_shot_quality(self.snapshot, self.history, self.directory)
        self.snapshot['shot_quality'] = prepared
        source = prepared['sources'][0]
        path = self.directory/source['raw_sources'][0]['raw_path']
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'hash'):
            shot_quality.verify_shot_quality(self.snapshot, self.history, self.directory)

    def test_dnp_game_counts_in_recency_but_prior_season_locations_do_not(self):
        self.history.append(row(pid='7', team='1', opponent_id='2', game_id='0022600010',
            season='2026-27', game_time='2026-10-22T00:00:00Z', known_at='2026-10-22T04:00:00Z',
            status='dnp', minutes=0, fga=0, fgm=0, fg3a=0, fg3m=0, fta=0, ftm=0, tov=0))
        self.install_game(4, [('7', '1', [(0, 20, True)]*20), ('9', '2', [])], season='2025-26')
        prepared = self.prepare()
        self.assertEqual(len(prepared['sources']), 3)
        target = next(p for p in prepared['players'] if p['player_id'] == '7')
        self.assertEqual([g['weight'] for g in target['current_games']], [.985**2, .985, 1.])
        base = self.shooting()
        _, audit = shot_quality.adjust_shooting(base, self.snapshot, '7')
        self.assertAlmostEqual(audit['families']['two_point']['located_attempts'], 11.73135)

    def test_future_sources_and_cutoff_advance_cannot_add_new_evidence(self):
        path = self.directory/'shots/0022600003.json'
        source = json.loads(path.read_bytes())
        source['known_at'] = '2026-10-23T00:01:00Z'
        atomic_json(path, source)
        prepared = self.prepare()
        self.assertEqual(len(prepared['sources']), 2)
        self.snapshot['as_of'] = '2026-10-23T00:05:00Z'
        shot_quality.verify_shot_quality(self.snapshot, self.history, self.directory)
        base = self.shooting()
        self.assertEqual(shot_quality.adjust_shooting(base, self.snapshot, '7')[0], base)
        self.snapshot['as_of'] = '2026-10-23T00:05:01Z'
        with self.assertRaises(ValueError):
            shot_quality.verify_shot_quality(self.snapshot, self.history, self.directory)

    def test_invalid_probability_pool_and_player_identity_are_rejected(self):
        prepared = self.prepare()
        base = self.shooting()
        for bad in ('nan', 'team', 'count', 'duplicate'):
            with self.subTest(bad=bad):
                self.snapshot['shot_quality'] = copy.deepcopy(prepared)
                if bad == 'nan':
                    self.snapshot['shot_quality']['pool'][0]['makes'] = float('nan')
                if bad == 'team':
                    self.snapshot['shot_quality']['players'][0]['team_id'] = '3'
                if bad == 'count':
                    self.snapshot['shot_quality']['pool'][0]['attempts'] = -1
                if bad == 'duplicate':
                    self.snapshot['shot_quality']['players'].append(copy.deepcopy(prepared['players'][0]))
                with self.assertRaises(ValueError):
                    shot_quality.adjust_shooting(base, self.snapshot, '7')

    def test_partial_missing_attempt_remains_in_baseline_exposure(self):
        attempts = [(0, 20, j < 3) for j in range(6)]
        attempts[-1] = (None, None, False)
        self.install_game(2, [('7', '1', attempts), ('8', '1', []), ('9', '2', [])])
        self.prepare()
        original = self.shooting()
        changed, audit = shot_quality.adjust_shooting(original, self.snapshot, '7')
        # 6*.985+5 located attempts; all 6*.985+6 actual attempts stay
        # in the baseline denominator. The unlocated miss is never imputed.
        strength = .35*10.91/60.91
        expected = original['two_point']['percentage']+strength*(10.91*.65-5.955)/151.91
        self.assertAlmostEqual(changed['two_point']['percentage'], expected)
        self.assertAlmostEqual(audit['families']['two_point']['current_attempts'], 11.91)
        self.assertEqual(changed['two_point']['current_weighted_attempts'], original['two_point']['current_weighted_attempts'])

    def test_one_unreplayable_game_is_left_out_and_recorded(self):
        # A collection that no longer replays (here a tampered location) costs only its game.
        path = self.directory/'shots/0022600001.json'
        source = json.loads(path.read_bytes())
        source['shots'][0]['x'] = 200
        atomic_json(path, source)
        (self.directory/'shots/0022600099.json').write_bytes(b'{')
        with self.assertRaises(ValueError):
            self.prepare()
        rejected = []
        prepared = shot_quality.prepare_shot_quality(self.directory, self.snapshot, self.history, rejected=rejected)
        self.assertEqual([s['game_id'] for s in prepared['sources']], ['0022600002', '0022600003'])
        self.assertEqual([(r['task'], r['game_id']) for r in rejected],
                         [('shot_quality_source', '0022600001'), ('shot_quality_source', '0022600099')])
        self.snapshot['shot_quality'] = prepared
        shot_quality.verify_shot_quality(self.snapshot, self.history, self.directory)

    def test_raw_normalized_location_and_source_hash_tampering_fail(self):
        path = self.directory/'shots/0022600001.json'
        source = json.loads(path.read_bytes())
        source['shots'][0]['x'] = 200
        atomic_json(path, source)
        with self.assertRaisesRegex(ValueError, 'replay|source'):
            self.prepare()
        self.install_target(1)
        prepared = self.prepare()
        source = prepared['sources'][0]
        path = self.directory/'shot-quality-evidence'/(source['sha256']+'.json')
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError, 'hash'):
            shot_quality.verify_shot_quality(self.snapshot, self.history, self.directory)

    def test_future_raw_observation_target_game_and_excluded_pool_are_omitted(self):
        path = self.directory/'shots/0022600003.json'
        original = path.read_bytes()
        source = json.loads(original)
        source['provenance'][0]['retrieved_at'] = '2026-10-23T00:01:00Z'
        atomic_json(path, source)
        self.assertEqual(len(self.prepare()['sources']), 2)
        path.write_bytes(original)
        self.snapshot['model_enhancements'] = {'excluded_history_game_ids': ['0022600003']}
        self.assertEqual(len(self.prepare()['sources']), 2)
        del self.snapshot['model_enhancements']
        self.snapshot['games'][0]['game_id'] = '0022600003'
        self.assertEqual(len(self.prepare()['sources']), 2)

    def test_unavailable_peer_actuals_fall_back_without_inventing_reference_evidence(self):
        self.history = [r for r in self.history if r['game_id'] != '0022600003']
        try:
            prepared = self.prepare()
        except ValueError as exc:
            self.fail('Unavailable independent peer actuals must leave that source out: '+str(exc))
        self.assertEqual(len(prepared['sources']), 2)
        base = self.shooting()
        changed, audit = shot_quality.adjust_shooting(base, self.snapshot, '7')
        self.assertEqual(changed, base)
        self.assertEqual(audit['status'], 'fallback')

    def test_pool_requires_a_current_captured_source_and_valid_zone(self):
        prepared = self.prepare()
        base = self.shooting()
        for kind in ('no_source', 'unknown_zone', 'raw_future', 'manifest_backdated'):
            with self.subTest(kind=kind):
                self.snapshot['shot_quality'] = copy.deepcopy(prepared)
                supplied = self.snapshot['shot_quality']
                if kind == 'no_source':
                    supplied['sources'] = []
                    supplied['players'] = []
                if kind == 'unknown_zone':
                    supplied['pool'][0].update(zone='unknown', points=None)
                if kind == 'raw_future':
                    supplied['sources'][0]['raw_sources'][0]['retrieved_at'] = '2027-01-01T00:00:00Z'
                if kind == 'manifest_backdated':
                    supplied['sources'][0]['known_at'] = '2026-10-01T00:00:00Z'
                with self.assertRaises(ValueError):
                    shot_quality.adjust_shooting(base, self.snapshot, '7')


class ArchivedShotQualityTests(unittest.TestCase):
    def test_archived_nba_games_replay_and_adjust_an_actual_two_game_player(self):
        self.assertIsNotNone(shot_quality, 'Source-bound shot quality must be implemented')
        dataset = Path(__file__).resolve().parents[1]/'datasets/full-boxscore-2026-10-23'
        with gzip.open(dataset/'shots.json.gz', 'rt') as stream:
            shots = json.load(stream)
        with gzip.open(dataset/'finals.json.gz', 'rt') as stream:
            finals = json.load(stream)
        with gzip.open(dataset/'historical-priors.json.gz', 'rt') as stream:
            priors = json.load(stream)
        history = [r for rows in finals.values() for r in rows]
        target = next(r for r in history if r['name'] == 'Anthony Edwards')
        snapshot = dict(as_of='2026-10-24T08:00:00Z', season='2026-27', historical_priors=priors,
            games=[dict(game_id='0022600200', season_type='Regular Season',
                home_team_id=target['team_id'], away_team_id=target['opponent_id'])],
            roster=dict(players=[dict(player_id=target['player_id'], team_id=target['team_id'])]))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            with tarfile.open(dataset/'official-raw.tar.gz', 'r:gz') as archive:
                for source in shots.values():
                    atomic_json(folder/'shots'/(source['game_id']+'.json'), source)
                    for meta in source['provenance']:
                        for suffix in ('.html', '.provenance.json'):
                            name = 'raw/'+meta['sha256']+suffix
                            raw = archive.extractfile(name).read()
                            path = folder/name
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(raw)
            snapshot['shot_quality'] = shot_quality.prepare_shot_quality(folder, snapshot, history)
            self.assertEqual(snapshot['shot_quality']['coverage']['source_games'], 28)
            self.assertEqual(snapshot['shot_quality']['coverage']['located_attempts'], 4942)
            shot_quality.verify_shot_quality(snapshot, history, folder)
            from nba_forecast.historical import season_prior
            current = sorted((r for r in history if r['player_id'] == target['player_id']),
                             key=lambda r: (r['game_time'], r['game_id']))
            baseline = _shooting([], current, season_prior(snapshot, target['player_id']))
            adjusted, audit = shot_quality.adjust_shooting(baseline, snapshot, target['player_id'])
            self.assertEqual(audit['status'], 'applied')
            self.assertTrue(audit['scoring_changed'])
            self.assertEqual(adjusted['two_point']['prior_effective_attempts'], baseline['two_point']['prior_effective_attempts'])
            self.assertEqual(adjusted['free_throw'], baseline['free_throw'])


if __name__ == '__main__':
    unittest.main()
