"""Spatial attempt estimates use only reproducible, pre-cutoff shot evidence."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from nba_forecast.data import archive_payload, boxscore_url
from nba_forecast.schedule import atomic_json, canonical
from nba_forecast.shots import normalize_page_shots, normalize_shots, playbyplay_url
from tests.test_shots import page_fixture

try:
    from nba_forecast import shot_profiles
except ImportError:
    shot_profiles = None


class ShotProfilesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(shot_profiles, 'Cutoff-bound spatial shot profiles must exist')
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name)
        self.page,self.game,self.meta,self.history=page_fixture()
        self.page['playByPlay']['actions'][0].update(xLegacy=0,yLegacy=20)
        self.page['playByPlay']['actions'][1].pop('yLegacy')
        self.page['playByPlay']['actions'].insert(2,dict(actionNumber=3,period=1,
            clock='PT09M00.00S',personId=7,teamId=1,actionType='Missed Shot',
            isFieldGoal=1,shotValue=2,shotResult='Missed',xLegacy=180,yLegacy=100))
        self.page['game']['homeTeam']['players'][0]['statistics']['fieldGoalsAttempted']=3
        self.history[0]['fga']=3
        self.snapshot=dict(as_of='2026-10-22T00:00:00Z',season='2026-27',
            games=[dict(game_id='0022600020',season_type='Regular Season',
                        start_time='2026-10-22T20:00:00Z',home_team_id='1',away_team_id='2')],
            roster=dict(players=[dict(player_id='7',team_id='1'),dict(player_id='8',team_id='1'),
                                 dict(player_id='9',team_id='2'),dict(player_id='99',team_id='3')]))
        self.source=self.install_source(self.page,self.game,self.history,self.meta['retrieved_at'])

    def install_source(self,page,game,history,known_at):
        raw=('<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':page}})+'</script>').encode()
        meta=archive_payload(raw,'https://www.nba.com/game/'+game['game_id']+'/box-score',known_at,self.directory/'raw')
        source=normalize_page_shots(page,game,meta,final_rows=history)
        atomic_json(self.directory/'shots'/(game['game_id']+'.json'),source)
        return source

    def prepare(self):
        return shot_profiles.prepare_shot_profiles(self.directory,self.snapshot,self.history)

    def test_preparation_covers_zero_attempt_participants_and_only_slate_roster(self):
        prepared=self.prepare()
        players={p['player_id']:p for p in prepared['players']}
        self.assertEqual(set(players),{'7','8','9'})
        self.assertEqual(players['7']['observed_attempts'],3)
        self.assertEqual(players['7']['observed_makes'],1)
        self.assertEqual(players['7']['located_attempts'],2)
        self.assertEqual(players['7']['games'],1)
        self.assertEqual(players['8']['observed_attempts'],0)
        self.assertEqual(players['8']['games'],1)
        self.assertEqual(players['7']['game_ids'],[self.game['game_id']])
        zones={z['zone']:z for z in players['7']['zones']}
        self.assertEqual(zones['near_basket_2pt']['observed_attempts'],1)
        self.assertEqual(zones['midrange_2pt']['observed_attempts'],1)
        self.assertEqual(zones['unlocated_3pt']['observed_attempts'],1)
        source=prepared['sources'][0]
        archived=self.directory/'shot-evidence'/(source['sha256']+'.json')
        self.assertEqual(hashlib.sha256(archived.read_bytes()).hexdigest(),source['sha256'])
        self.assertEqual(source['raw_sources'][0]['sha256'],self.source['provenance'][0]['sha256'])

    def test_archived_livedata_play_by_play_and_boxscore_can_reproduce_profiles(self):
        gid=self.game['game_id'];box={'game':copy.deepcopy(self.page['game'])}
        pbp={'game':copy.deepcopy(self.page['playByPlay'])}
        for action in pbp['game']['actions']:
            if action.get('isFieldGoal')==1:action['actionType']=str(action['shotValue'])+'pt'
        metadata=[]
        for payload,url in ((pbp,playbyplay_url(gid)),(box,boxscore_url(gid))):
            metadata.append(archive_payload(canonical(payload),url,self.meta['retrieved_at'],self.directory/'raw'))
        source=normalize_shots(pbp,box,self.meta['retrieved_at'],final_rows=self.history)
        source['provenance']=metadata
        atomic_json(self.directory/'shots'/(gid+'.json'),source)
        prepared=self.prepare()
        self.assertEqual(prepared['players'][0]['observed_attempts'],3)
        self.assertEqual(len(prepared['sources'][0]['raw_sources']),2)
        self.snapshot['shot_profiles']=prepared
        shot_profiles.verify_shot_profiles(self.snapshot,self.history,self.directory)

    def test_source_timestamp_cannot_be_backdated_before_the_raw_archive_observation(self):
        source=copy.deepcopy(self.source)
        source['provenance'][0]['retrieved_at']='2026-10-20T12:00:00Z'
        atomic_json(self.directory/'shots'/(self.game['game_id']+'.json'),source)
        with self.assertRaisesRegex(ValueError,'provenance|archive|observation'):
            self.prepare()

    def test_preparation_ignores_later_observations_missing_and_research_excluded_games(self):
        for reason in ('future','missing','excluded'):
            with self.subTest(reason=reason):
                source=copy.deepcopy(self.source)
                snapshot=copy.deepcopy(self.snapshot)
                if reason=='future':source['known_at']='2026-10-23T00:00:00Z'
                if reason=='missing':source.update(shots=[],missing_reason='source unavailable')
                if reason=='excluded':snapshot['model_enhancements']={'excluded_history_game_ids':[self.game['game_id']]}
                atomic_json(self.directory/'shots'/(self.game['game_id']+'.json'),source)
                prepared=shot_profiles.prepare_shot_profiles(self.directory,snapshot,self.history)
                self.assertEqual(prepared['sources'],[])
                self.assertTrue(all(p['games']==0 for p in prepared['players']))

    def test_raw_source_observation_after_cutoff_is_ignored_even_if_summary_time_is_earlier(self):
        source=copy.deepcopy(self.source)
        source['provenance'][0]['retrieved_at']='2026-10-23T00:00:00Z'
        atomic_json(self.directory/'shots'/(self.game['game_id']+'.json'),source)
        self.assertEqual(self.prepare()['sources'],[])

    def test_actual_count_disagreement_rejects_player_profile(self):
        self.history[0]['fga']=4
        with self.assertRaisesRegex(ValueError,'reconcil|disagree|count'):
            self.prepare()

    def test_raw_replay_rejects_tampered_normalized_location_even_with_new_hash(self):
        source=copy.deepcopy(self.source)
        source['shots'][0]['x']=199
        atomic_json(self.directory/'shots'/(self.game['game_id']+'.json'),source)
        with self.assertRaisesRegex(ValueError,'replay|archive|source'):
            self.prepare()

    def test_source_cannot_relabel_current_game_as_prior_season(self):
        source=copy.deepcopy(self.source)
        source['season']='2025-26'
        atomic_json(self.directory/'shots'/(self.game['game_id']+'.json'),source)
        with self.assertRaisesRegex(ValueError,'season'):
            self.prepare()

    def test_verification_rebuilds_snapshot_from_immutable_normalized_and_raw_archives(self):
        self.snapshot['shot_profiles']=self.prepare()
        shot_profiles.verify_shot_profiles(self.snapshot,self.history,self.directory)
        # Current collection can advance without changing this captured input.
        (self.directory/'shots'/(self.game['game_id']+'.json')).unlink()
        shot_profiles.verify_shot_profiles(self.snapshot,self.history,self.directory)
        self.snapshot['shot_profiles']['players'][0]['observed_attempts']+=1
        with self.assertRaises(ValueError):
            shot_profiles.verify_shot_profiles(self.snapshot,self.history,self.directory)

    def test_preflight_clock_can_advance_five_minutes_without_admitting_later_actuals(self):
        self.snapshot['shot_profiles']=self.prepare()
        self.snapshot['as_of']='2026-10-22T00:05:00Z'
        late=copy.deepcopy(self.history[0])
        late.update(fga=4,known_at='2026-10-22T00:01:00Z')
        shot_profiles.verify_shot_profiles(self.snapshot,self.history+[late],self.directory)
        result={'predictions':[dict(player_id='7',team_id='1',game_id='0022600020',status='projected',
            expected_fga=10.,expected_fg3a=4.,points_mean=12.3)]}
        output=shot_profiles.apply_shot_profiles(result,self.snapshot)
        self.assertEqual(output['predictions'][0]['modeled_shot_profile']['observed_attempts'],3)
        for as_of in ('2026-10-21T23:59:59Z','2026-10-22T00:05:01Z'):
            with self.subTest(as_of=as_of):
                self.snapshot['as_of']=as_of
                with self.assertRaises(ValueError):
                    shot_profiles.verify_shot_profiles(self.snapshot,self.history,self.directory)
                with self.assertRaises(ValueError):
                    shot_profiles.apply_shot_profiles(result,self.snapshot)

    def test_normalized_archive_and_raw_archive_tampering_fail_verification(self):
        for target in ('normalized','raw'):
            with self.subTest(target=target):
                self.snapshot['shot_profiles']=self.prepare()
                source=self.snapshot['shot_profiles']['sources'][0]
                path=(self.directory/'shot-evidence'/(source['sha256']+'.json') if target=='normalized'
                      else Path(self.source['provenance'][0]['raw_path']))
                original=path.read_bytes()
                path.write_bytes(original+b' ')
                try:
                    with self.assertRaisesRegex(ValueError,'hash'):
                        shot_profiles.verify_shot_profiles(self.snapshot,self.history,self.directory)
                finally:path.write_bytes(original)

    def test_manifest_future_time_wrong_game_and_newly_excluded_evidence_fail(self):
        prepared=self.prepare()
        for change in ('future','game','excluded'):
            with self.subTest(change=change):
                snapshot=copy.deepcopy(self.snapshot)
                snapshot['shot_profiles']=copy.deepcopy(prepared)
                if change=='future':snapshot['shot_profiles']['sources'][0]['known_at']='2026-10-23T00:00:00Z'
                if change=='game':snapshot['shot_profiles']['sources'][0]['game_id']='0022600020'
                if change=='excluded':snapshot['model_enhancements']={'excluded_history_game_ids':[self.game['game_id']]}
                with self.assertRaises(ValueError):
                    shot_profiles.verify_shot_profiles(snapshot,self.history,self.directory)

    def test_only_latest_cutoff_eligible_actual_revision_is_used(self):
        late=copy.deepcopy(self.history[0])
        late.update(fga=4,known_at='2026-10-23T00:00:00Z')
        prepared=shot_profiles.prepare_shot_profiles(self.directory,self.snapshot,self.history+[late])
        self.assertEqual(prepared['players'][0]['observed_attempts'],3)
        late['known_at']='2026-10-21T12:00:00Z'
        with self.assertRaises(ValueError):
            shot_profiles.prepare_shot_profiles(self.directory,self.snapshot,self.history+[late])

    def test_spatial_allocation_conserves_two_and_three_attempts_without_altering_scoring(self):
        self.snapshot['shot_profiles']=self.prepare()
        result={'predictions':[dict(player_id='7',team_id='1',game_id='0022600020',status='projected',
            expected_fga=10.,expected_fg3a=4.,points_mean=12.3,expected_fg_pct=.4,
            components={'calibration':{'unchanged':True}})]}
        before=copy.deepcopy(result)
        output=shot_profiles.apply_shot_profiles(result,self.snapshot)
        profile=output['predictions'][0]['modeled_shot_profile']
        self.assertEqual(profile['observed_attempts'],3)
        zones={z['zone']:z for z in profile['zones']}
        self.assertEqual(zones['near_basket_2pt']['projected_attempts'],3.)
        self.assertEqual(zones['midrange_2pt']['projected_attempts'],3.)
        self.assertEqual(zones['unlocated_3pt']['projected_attempts'],4.)
        self.assertEqual(sum(z['projected_attempts'] for z in profile['zones']),10.)
        self.assertEqual(output['predictions'][0]['points_mean'],12.3)
        self.assertEqual(output['predictions'][0]['expected_fg_pct'],.4)
        self.assertEqual(output['predictions'][0]['components'],before['predictions'][0]['components'])
        self.assertEqual(result,before)

    def test_unobserved_categories_allocate_only_to_explicit_unresolved_zones(self):
        self.snapshot['shot_profiles']=self.prepare()
        result={'predictions':[dict(player_id='8',team_id='1',game_id='0022600020',status='projected',
            expected_fga=5.,expected_fg3a=1.,points_mean=3.)]}
        profile=shot_profiles.apply_shot_profiles(result,self.snapshot)['predictions'][0]['modeled_shot_profile']
        self.assertEqual(profile['observed_attempts'],0)
        self.assertEqual({z['zone']:z['projected_attempts'] for z in profile['zones']},
                         {'unresolved_2pt':4.,'unresolved_3pt':1.})

    def test_missing_profiles_do_not_change_a_frozen_forecast(self):
        result={'predictions':[{'player_id':'7','points_mean':12.3}]}
        self.assertEqual(shot_profiles.apply_shot_profiles(result,self.snapshot),result)
        shot_profiles.verify_shot_profiles(self.snapshot,self.history,self.directory)
