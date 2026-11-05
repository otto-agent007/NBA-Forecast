"""Injury exits require causal recap evidence, exact identities, and raw replay.

The two positive paragraphs are reduced fixtures from the immutable NBA pages
for 0022600095 (4f77c19c...) and 0022600100 (17eb793f...). Box fixtures are
deliberately small; integration replay of the complete archives is separate.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from nba_forecast.data import archive_payload
from nba_forecast.full_boxscore_sources import normalize_official_page

try:
    from nba_forecast import role_context
except ImportError:
    role_context = None


FLAGG = (
    "Flagg, last season’s NBA Rookie of the Year, did not play in the second half "
    "after he injured his left shoulder when he was fouled by Aaron Nesmith on "
    "what looked like it would be a breakaway dunk."
)
MISSI = (
    "New Orleans starting center Yves Missi injured his left knee in the second "
    "quarter and had to be helped to the locker room. He missed the rest of the "
    "game and will be re-evaluated Saturday."
)
CUTOFF = '2026-10-24T07:00:00Z'
OBSERVED = '2026-10-24T06:29:46.538531+00:00'


def page_fixture(missi=False):
    gid, pid, first, family, away, home, city = (
        ('0022600100', 1642274, 'Yves', 'Missi', 1610612740, 1610612762, 'New Orleans')
        if missi else
        ('0022600095', 1642843, 'Cooper', 'Flagg', 1610612742, 1610612754, 'Dallas'))
    start = '2026-10-24T01:30:00Z' if missi else '2026-10-23T23:00:00Z'
    game = dict(game_id=gid, start_time=start, season_type='Regular Season',
                home_team_id=str(home), away_team_id=str(away))
    stats = dict(minutes='9:17' if missi else '17:00', fieldGoalsMade=3 if missi else 4,
                 fieldGoalsAttempted=3 if missi else 6, threePointersMade=0 if missi else 2,
                 threePointersAttempted=0 if missi else 2, freeThrowsMade=0 if missi else 4,
                 freeThrowsAttempted=0 if missi else 4, reboundsOffensive=0,
                 reboundsDefensive=5, reboundsTotal=5, assists=6, steals=3, blocks=1,
                 turnovers=0 if missi else 3, foulsPersonal=1,
                 points=6 if missi else 14, plusMinusPoints=0)
    source = dict(gameId=gid, gameTimeUTC=start, gameStatus=3, period=4)
    for side, tid, person_id, names in (
            ('home', home, 1630174, ('Aaron', 'Nesmith')),
            ('away', away, pid, (first, family))):
        person = dict(personId=person_id, firstName=names[0], familyName=names[1],
                      comment='', statistics=copy.deepcopy(stats))
        source[side+'Team'] = dict(teamId=tid, teamTricode='NOP' if missi else 'DAL',
            teamCity=city if side=='away' else 'Indiana', teamName='Pelicans' if missi else 'Mavericks',
            score=stats['points'], statistics=copy.deepcopy(stats), players=[person])
    page = dict(game=source, story=dict(storyType='recap', date='2026-10-23T22:20:57',
        header=dict(headline='Final game recap'), content=[MISSI if missi else FLAGG]),
        playByPlay=dict(gameId=gid, actions=[dict(personId=pid, teamId=away, period=2,
            clock='PT06M24.00S' if missi else 'PT00M59.70S', actionNumber=251 if missi else 406,
            actionType='Substitution', description='SUB: Reserve FOR '+family)]))
    return game, page


class RoleContextTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(role_context, 'Source-replayable injury role context must exist')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.snapshot = dict(as_of=CUTOFF, season='2026-27', games=[dict(
            game_id='0022600109', start_time='2026-10-25T00:00:00Z',
            season_type='Regular Season', home_team_id='1610612742', away_team_id='1610612759')])
        self.game, self.page = page_fixture()

    def write_source(self, page, game, observed=OBSERVED, replace=True):
        url = 'https://www.nba.com/game/'+game['game_id']+'/box-score'
        if replace:
            # Alternative subtest scenarios are independent source worlds.
            for path in (self.directory/'raw').glob('*.provenance.json'):
                prior = json.loads(path.read_bytes())
                if prior['source_url']==url:
                    (path.parent/(prior['sha256']+'.html')).unlink()
                    path.unlink()
        raw = ('<script id="__NEXT_DATA__">'+json.dumps({'props': {'pageProps': page}})+'</script>').encode()
        return archive_payload(raw, url, observed, self.directory/'raw')

    def install(self, page=None, game=None, observed=OBSERVED, replace=True):
        page = self.page if page is None else page
        game = self.game if game is None else game
        metadata = self.write_source(page, game, observed, replace)
        history = normalize_official_page(page, game, metadata)
        return history, metadata

    def prepare(self, history):
        return role_context.prepare_role_context(self.directory, self.snapshot, history)

    def test_two_archived_recap_wordings_identify_only_the_injured_players(self):
        history, metadata = self.install()
        missi_game, missi_page = page_fixture(True)
        other, _ = self.install(missi_page, missi_game)
        before = copy.deepcopy(history+other)
        prepared = self.prepare(history+other)
        self.assertEqual({(e['game_id'], e['player_id']) for e in prepared['events']},
                         {('0022600095', '1642843'), ('0022600100', '1642274')})
        flagg = next(e for e in prepared['events'] if e['player_id']=='1642843')
        self.assertEqual(flagg['team_id'], '1610612742')
        self.assertEqual(flagg['opponent_id'], '1610612754')
        self.assertEqual(flagg['source_sha256'], metadata['sha256'])
        self.assertEqual(flagg['source_locator'], 'props.pageProps.story.content[0]')
        self.assertEqual(flagg['known_at'], OBSERVED)
        self.assertEqual(flagg['classification'], 'injury_shortened')
        self.assertEqual(history+other, before)
        self.snapshot['role_context'] = prepared
        role_context.verify_role_context(self.snapshot, history+other, self.directory)
        self.assertEqual(role_context.role_excluded_game_ids(self.snapshot, '1642843', history),
                         {'0022600095'})
        self.assertEqual(role_context.role_excluded_game_ids(self.snapshot, '1630174', history), set())

    def test_short_minutes_without_an_explicit_exit_remain_unclassified(self):
        self.page['story']['content'] = ['Flagg scored 14 points in 17 minutes.']
        history, _ = self.install()
        prepared = self.prepare(history)
        self.assertEqual(prepared['events'], [])
        self.assertEqual(prepared['coverage']['source_games'], 1)
        self.assertEqual(prepared['coverage']['confirmed_events'], 0)

    def test_pregame_historical_temporary_and_unrelated_no_return_prose_is_not_an_exit(self):
        paragraphs = [
            "Flagg was a game-time decision with an ankle injury but started.",
            "Flagg was the only member of Dallas' regular rotation that didn't return.",
            "Flagg asked to be taken out during a timeout and did not return to the game.",
            "Flagg headed to the locker room after hurting his shoulder and was questionable to return.",
            "Flagg injured his shoulder last season and missed the rest of the game.",
            "Flagg did not play because of a shoulder injury.",
            "Nesmith said Flagg did not play in the second half after he injured his left shoulder.",
            "Flagg did not return for the second half because Nesmith injured his shoulder.",
        ]
        for paragraph in paragraphs:
            with self.subTest(paragraph=paragraph):
                self.page['story']['content'] = [paragraph]
                history, _ = self.install()
                self.assertEqual(self.prepare(history)['events'], [])

    def test_ambiguous_family_name_does_not_select_a_player(self):
        self.page['game']['homeTeam']['players'][0].update(firstName='Other', familyName='Flagg')
        history, _ = self.install()
        self.assertEqual(self.prepare(history)['events'], [])

    def test_recap_other_date_other_person_and_conjunction_return_are_not_exits(self):
        cases=(
            'Flagg did not play in the second half after he injured his left shoulder in the season opener on Tuesday.',
            'Cooper Flagg left the game with a teammate who had a left shoulder injury and did not return.',
            "Flagg did not play in the second half after he injured his teammate's left shoulder.",
            "Cooper Flagg injured his teammate's left knee in the second quarter. He missed the rest of the game.",
            'Cooper Flagg left the game with a left shoulder injury and did not return. However, he returned in the third quarter.',
        )
        for paragraph in cases:
            with self.subTest(paragraph=paragraph):
                self.page['story']['content']=[paragraph]
                history,_=self.install()
                self.assertEqual(self.prepare(history)['events'],[])

    def test_unique_full_name_remains_usable_when_family_name_is_ambiguous(self):
        self.page['game']['homeTeam']['players'][0].update(firstName='Other', familyName='Flagg')
        self.page['story']['content']=[FLAGG.replace('Flagg,', 'Cooper Flagg,')]
        history, _ = self.install()
        self.assertEqual([e['player_id'] for e in self.prepare(history)['events']], ['1642843'])

    def test_definite_causal_exit_sentence_generalizes_without_a_player_specific_branch(self):
        self.page['story']['content']=[
            'Cooper Flagg left the game with a left shoulder injury and did not return.']
        history, _ = self.install()
        self.assertEqual([e['player_id'] for e in self.prepare(history)['events']], ['1642843'])

    def test_return_in_second_sentence_and_historical_exit_language_do_not_confirm(self):
        for paragraph in (
                FLAGG+' He returned to the game after halftime.',
                FLAGG.replace('when he was fouled', 'in the previous game when he was fouled'),
                FLAGG.replace('when he was fouled', 'last Monday when he was fouled'),
                'Cooper Flagg left the game with a left shoulder injury and did not return. He later returned.',
                'Cooper Flagg left the game with a left shoulder injury and did not return until halftime.',
                'Cooper Flagg left the game with a left shoulder injury and did not return to Dallas next season.',
                FLAGG+' He came back for the fourth quarter.',
                'Cooper Flagg left the game with a left shoulder injury and did not return in the previous game.'):
            with self.subTest(paragraph=paragraph):
                self.page['story']['content']=[paragraph]
                history, _ = self.install()
                self.assertEqual(self.prepare(history)['events'], [])

    def test_explicit_return_and_second_half_actions_contradict_exit(self):
        for contradiction in ('prose', 'pbp'):
            with self.subTest(contradiction=contradiction):
                game, page = page_fixture()
                if contradiction=='prose':
                    page['story']['content'].append('Flagg returned to the game in the third quarter.')
                else:
                    page['playByPlay']['actions'].append(dict(personId=1642843, teamId=1610612742,
                    period=3, clock='PT10M00.00S', actionNumber=500, actionType='Made Shot'))
                history, _ = self.install(page, game)
                self.assertEqual(self.prepare(history)['events'], [])

    def test_missing_or_malformed_recap_is_explicit_unconfirmed_coverage(self):
        for story in (None, {}, {'storyType':'preview', 'content':[FLAGG]},
                      {'storyType':'recap', 'content':[{'text':FLAGG}]}):
            with self.subTest(story=story):
                self.page['story'] = story
                history, _ = self.install()
                prepared = self.prepare(history)
                self.assertEqual(prepared['events'], [])
                self.assertEqual(prepared['coverage']['confirmed_events'], 0)
                self.assertTrue(prepared['coverage']['unconfirmed_games'])

    def test_future_known_and_forecast_target_history_cannot_supply_events(self):
        for change in ('source', 'history', 'target', 'excluded'):
            with self.subTest(change=change):
                history, _ = self.install()
                snapshot = copy.deepcopy(self.snapshot)
                if change=='source':
                    snapshot['as_of']='2026-10-24T05:00:00Z'
                if change=='history':
                    for row in history:
                        row['known_at']='2026-10-24T08:00:00Z'
                if change=='target':
                    snapshot['games']=[self.game]
                if change=='excluded':
                    snapshot['model_enhancements']={'excluded_history_game_ids':[self.game['game_id']]}
                prepared = role_context.prepare_role_context(self.directory, snapshot, history)
                self.assertEqual(prepared['events'], [])

    def test_source_history_player_and_team_swaps_are_rejected(self):
        for change in ('player', 'team', 'game', 'minutes'):
            with self.subTest(change=change):
                history, _ = self.install()
                injured = next(r for r in history if r['player_id']=='1642843')
                if change=='player':
                    injured['player_id']='999'
                if change=='team':
                    injured['team_id'], injured['opponent_id']=injured['opponent_id'], injured['team_id']
                if change=='game':
                    for row in history:
                        row['game_time']='2026-10-23T22:00:00Z'
                if change=='minutes':
                    injured['minutes']=32
                with self.assertRaises(ValueError):
                    self.prepare(history)

    def test_duplicate_or_malformed_raw_player_identity_is_rejected_by_preparation(self):
        for change in ('duplicate', 'game', 'player', 'names', 'malformed'):
            with self.subTest(change=change):
                game, page = page_fixture()
                history, _ = self.install(page, game)
                if change=='duplicate':
                    page['game']['awayTeam']['players'].append(copy.deepcopy(page['game']['awayTeam']['players'][0]))
                if change=='game':
                    page['game']['gameId']='0022600096'
                if change=='player':
                    page['game']['awayTeam']['players'][0]['personId']=999
                if change=='names':
                    home=page['game']['homeTeam']['players'][0]
                    away=page['game']['awayTeam']['players'][0]
                    for field in ('firstName','familyName'):
                        home[field],away[field]=away[field],home[field]
                if change=='malformed':
                    page['game']['awayTeam']['players']='invalid'
                self.write_source(page, game)
                with self.assertRaises(ValueError):
                    self.prepare(history)

    def test_unplayed_person_is_not_an_injury_shortened_appearance(self):
        person = self.page['game']['awayTeam']['players'][0]
        teammate=copy.deepcopy(person)
        teammate.update(personId=999,firstName='Another',familyName='Player')
        self.page['game']['awayTeam']['players'].append(teammate)
        person['comment']='DNP - Injury/Illness'
        person['statistics']={k:0 for k in person['statistics']}
        person['statistics']['minutes']=''
        history, _ = self.install()
        self.assertEqual(self.prepare(history)['events'], [])

    def test_malformed_provenance_container_raises_value_error(self):
        history, metadata = self.install()
        Path(metadata['provenance_path']).write_text('[]')
        with self.assertRaises(ValueError):
            self.prepare(history)

    def test_same_time_different_raw_versions_are_ambiguous(self):
        history, _ = self.install()
        self.page['story']['content']=['Flagg returned to the game after halftime.']
        self.write_source(self.page, self.game, replace=False)
        with self.assertRaisesRegex(ValueError, 'Ambiguous|ambiguous|same-time'):
            self.prepare(history)

    def tolerant(self, history):
        rejected = []
        prepared = role_context.prepare_role_context(self.directory, self.snapshot, history, rejected=rejected)
        # Whatever was skipped, the context replays from the archives alone.
        role_context.verify_role_context(dict(self.snapshot, role_context=prepared), history, self.directory)
        return prepared, rejected

    def test_an_unusable_newest_page_falls_back_to_the_previous_page(self):
        # A page saved before it was final, or in a layout the parser does not know, costs
        # nothing when an earlier version of the same game's page is good.
        history, metadata = self.install()
        broken = copy.deepcopy(self.page)
        broken['game']['awayTeam']['players'] = 'invalid'
        self.write_source(broken, self.game, observed='2026-10-24T06:40:00+00:00', replace=False)
        with self.assertRaises(ValueError):
            self.prepare(history)
        prepared, rejected = self.tolerant(history)
        self.assertEqual([s['source_sha256'] for s in prepared['sources']], [metadata['sha256']])
        self.assertEqual([e['player_id'] for e in prepared['events']], ['1642843'])
        self.assertEqual([(r['task'], r['game_id'], r['known_at']) for r in rejected],
                         [('role_context_source', '0022600095', '2026-10-24T06:40:00+00:00')])

    def test_one_unusable_game_costs_only_its_own_evidence(self):
        missi_game, missi_page = page_fixture(True)
        for change in ('tampered', 'provenance', 'unparseable', 'ambiguous', 'disagrees'):
            with self.subTest(change=change):
                self.directory = Path(tempfile.mkdtemp(dir=self.tmp.name))
                history, metadata = self.install()
                other, _ = self.install(missi_page, missi_game)
                if change == 'tampered':
                    Path(metadata['raw_path']).write_bytes(Path(metadata['raw_path']).read_bytes()+b' ')
                if change == 'provenance':
                    Path(metadata['provenance_path']).write_text('[]')
                if change == 'unparseable':
                    broken = copy.deepcopy(self.page)
                    broken['game']['awayTeam']['players'][0]['statistics']['minutes'] = '17 min'
                    self.write_source(broken, self.game)
                if change == 'ambiguous':
                    returned = copy.deepcopy(self.page)
                    returned['story']['content'] = ['Flagg returned to the game after halftime.']
                    self.write_source(returned, self.game, replace=False)
                if change == 'disagrees':
                    # A stat correction the cached final has not taken yet.
                    history = [dict(r, minutes=r['minutes']+1) for r in history]
                with self.assertRaises(ValueError):
                    self.prepare(history+other)
                prepared, rejected = self.tolerant(history+other)
                self.assertEqual([e['game_id'] for e in prepared['events']], ['0022600100'])
                self.assertEqual([s['game_id'] for s in prepared['sources']], ['0022600100'])
                self.assertIn(dict(game_id='0022600095', status='source_unavailable'),
                              prepared['coverage']['unconfirmed_games'])
                self.assertTrue(rejected and all(r['task'] == 'role_context_source' for r in rejected))
                self.assertTrue(all(r.get('game_id', '0022600095') == '0022600095' for r in rejected))

    def test_at_most_three_pages_are_tried_per_game(self):
        history, _ = self.install()
        broken = copy.deepcopy(self.page)
        broken['game']['awayTeam']['players'] = 'invalid'
        for minute in range(31, 35):
            broken['story']['content'] = [FLAGG+' '*minute]
            self.write_source(broken, self.game, observed=f'2026-10-24T06:{minute}:00+00:00', replace=False)
        prepared, rejected = self.tolerant(history)
        self.assertEqual(prepared['sources'], [])
        self.assertEqual([r['known_at'][11:16] for r in rejected], ['06:34', '06:33', '06:32'])

    def test_missing_raw_source_remains_unknown_without_rewriting_history(self):
        history, metadata = self.install()
        Path(metadata['raw_path']).unlink()
        Path(metadata['provenance_path']).unlink()
        prepared = self.prepare(history)
        self.assertEqual(prepared['events'], [])
        self.assertEqual(prepared['coverage']['source_games'], 0)
        self.assertEqual(prepared['coverage']['unconfirmed_games'],
                         [{'game_id':'0022600095', 'status':'source_unavailable'}])

    def test_later_source_is_not_admitted_using_earlier_actuals_or_story_date(self):
        history, _ = self.install()
        self.write_source(self.page, self.game, observed='2026-10-24T08:00:00Z')
        prepared = self.prepare(history)
        self.assertEqual(prepared['events'], [])
        self.assertEqual(prepared['coverage']['source_games'], 0)

    def test_malformed_backdated_or_tampered_source_provenance_is_rejected(self):
        for change in ('naive', 'before_game', 'backdated', 'bytes', 'hash'):
            with self.subTest(change=change):
                history, metadata = self.install()
                prepared = self.prepare(history)
                path = Path(metadata['provenance_path'])
                supplied = json.loads(path.read_bytes())
                if change=='naive':
                    supplied['retrieved_at']='2026-10-24T06:00:00'
                if change=='before_game':
                    supplied['retrieved_at']='2026-10-23T22:00:00Z'
                if change=='backdated':
                    supplied['retrieved_at']='2026-10-24T05:00:00Z'
                if change=='bytes':
                    supplied['bytes']+=1
                if change=='hash':
                    supplied['sha256']='0'*64
                path.write_text(json.dumps(supplied))
                if change=='backdated':
                    # Capture pins original observation metadata. A first raw
                    # observation can legitimately precede a later final fetch;
                    # changing it after capture must fail immutable replay.
                    self.snapshot['role_context']=prepared
                    with self.assertRaises(ValueError):
                        role_context.verify_role_context(self.snapshot, history, self.directory)
                else:
                    with self.assertRaises(ValueError):
                        self.prepare(history)
                path.write_text(json.dumps({k:metadata[k] for k in ('source_url','sha256','retrieved_at','bytes')}))

    def test_preparation_is_idempotent_and_replays_after_current_final_cache_disappears(self):
        history, _ = self.install()
        before = copy.deepcopy(history)
        prepared = self.prepare(history)
        self.assertEqual(self.prepare(history), prepared)
        self.assertTrue(list((self.directory/'role-evidence').glob('*.json')))
        self.snapshot['role_context'] = prepared
        role_context.verify_role_context(self.snapshot, history, self.directory)
        self.assertEqual(history, before)

    def test_newer_collection_does_not_change_previously_prepared_evidence(self):
        history, _ = self.install()
        self.snapshot['role_context']=self.prepare(history)
        self.page['story']['content']=['Flagg returned to the game in the second half.']
        self.write_source(self.page, self.game, observed='2026-10-24T06:45:00Z', replace=False)
        role_context.verify_role_context(self.snapshot, history, self.directory)
        self.assertEqual(self.prepare(history)['events'], [])

    def test_equivalent_history_timezones_and_row_order_do_not_change_source_identity(self):
        history, _ = self.install()
        prepared = self.prepare(history)
        history[0]['game_time']='2026-10-23T19:00:00-04:00'
        self.assertEqual(self.prepare(history), prepared)
        self.assertEqual(self.prepare(list(reversed(history))), prepared)
        self.snapshot['role_context']=prepared
        role_context.verify_role_context(self.snapshot, history, self.directory)

    def test_verification_rejects_duplicate_and_tampered_events_or_sources(self):
        history, _ = self.install()
        prepared = self.prepare(history)
        for mutation in ('event_duplicate', 'source_duplicate', 'player', 'team', 'known', 'text', 'hash', 'coverage'):
            with self.subTest(mutation=mutation):
                supplied = copy.deepcopy(prepared)
                if mutation=='event_duplicate':
                    supplied['events'].append(copy.deepcopy(supplied['events'][0]))
                if mutation=='source_duplicate':
                    supplied['sources'].append(copy.deepcopy(supplied['sources'][0]))
                if mutation=='player':
                    supplied['events'][0]['player_id']='1630174'
                if mutation=='team':
                    supplied['events'][0]['team_id']='1610612754'
                if mutation=='known':
                    supplied['events'][0]['known_at']='2026-10-23T22:00:00Z'
                if mutation=='text':
                    supplied['events'][0]['source_text']='invented evidence'
                if mutation=='hash':
                    supplied['events'][0]['source_sha256']='0'*64
                if mutation=='coverage':
                    supplied['coverage']['confirmed_events']=9
                self.snapshot['role_context'] = supplied
                with self.assertRaises(ValueError):
                    role_context.verify_role_context(self.snapshot, history, self.directory)

    def test_raw_and_immutable_evidence_tampering_are_rejected(self):
        history, metadata = self.install()
        self.snapshot['role_context'] = self.prepare(history)
        paths = [Path(metadata['raw_path']), *list((self.directory/'role-evidence').glob('*.json'))]
        for path in paths:
            with self.subTest(path=path.name):
                before = path.read_bytes()
                path.write_bytes(before+b' ')
                try:
                    with self.assertRaisesRegex(ValueError, 'hash|archive'):
                        role_context.verify_role_context(self.snapshot, history, self.directory)
                finally:
                    path.write_bytes(before)

    def test_preflight_uses_prepared_cutoff_for_at_most_five_minutes(self):
        history, _ = self.install()
        self.snapshot['role_context'] = self.prepare(history)
        late = copy.deepcopy(history[-1])
        late.update(known_at='2026-10-24T07:01:00Z', minutes=32)
        self.snapshot['as_of']='2026-10-24T07:05:00Z'
        role_context.verify_role_context(self.snapshot, history+[late], self.directory)
        self.assertEqual(role_context.role_excluded_game_ids(self.snapshot, '1642843', history+[late]),
                         {'0022600095'})
        for cutoff in ('2026-10-24T06:59:59Z', '2026-10-24T07:05:01Z'):
            self.snapshot['as_of']=cutoff
            with self.assertRaises(ValueError):
                role_context.verify_role_context(self.snapshot, history, self.directory)
            with self.assertRaises(ValueError):
                role_context.role_excluded_game_ids(self.snapshot, '1642843', history)

    def test_helper_requires_the_exact_eligible_played_history_identity(self):
        history, _ = self.install()
        self.snapshot['role_context'] = self.prepare(history)
        for change in ('player', 'team', 'time', 'future'):
            with self.subTest(change=change):
                altered = copy.deepcopy(history)
                row = next(r for r in altered if r['player_id']=='1642843')
                if change=='player':
                    row['player_id']='999'
                if change=='team':
                    row['team_id'], row['opponent_id']=row['opponent_id'], row['team_id']
                if change=='time':
                    row['game_time']='2026-10-23T22:00:00Z'
                if change=='future':
                    row['known_at']='2026-10-24T08:00:00Z'
                self.assertEqual(role_context.role_excluded_game_ids(self.snapshot, '1642843', altered), set())

    def test_helper_refuses_duplicate_events_and_same_time_conflicting_history(self):
        history, _ = self.install()
        prepared = self.prepare(history)
        self.snapshot['role_context']=copy.deepcopy(prepared)
        self.snapshot['role_context']['events'].append(copy.deepcopy(prepared['events'][0]))
        with self.assertRaises(ValueError):
            role_context.role_excluded_game_ids(self.snapshot, '1642843', history)
        self.snapshot['role_context']=prepared
        swapped=copy.deepcopy(history[-1])
        swapped['team_id'],swapped['opponent_id']=swapped['opponent_id'],swapped['team_id']
        with self.assertRaises(ValueError):
            role_context.role_excluded_game_ids(self.snapshot, '1642843', history+[swapped])

    def test_helper_honors_candidate_and_snapshot_history_exclusions(self):
        history, _ = self.install()
        prepared = self.prepare(history)
        for target in ('candidate','snapshot'):
            with self.subTest(target=target):
                snapshot=copy.deepcopy(self.snapshot)
                snapshot['role_context']=prepared
                if target=='candidate':
                    snapshot['model_enhancements']={'excluded_history_game_ids':['0022600095']}
                else:
                    snapshot['excluded_history_game_ids']=['0022600095']
                self.assertEqual(role_context.role_excluded_game_ids(snapshot,'1642843',history),set())
                with self.assertRaises(ValueError):
                    role_context.verify_role_context(snapshot,history,self.directory)

    def test_absent_context_keeps_old_snapshots_replayable(self):
        history, _ = self.install()
        role_context.verify_role_context(self.snapshot, history, self.directory)
        self.assertEqual(role_context.role_excluded_game_ids(self.snapshot, '1642843', history), set())
