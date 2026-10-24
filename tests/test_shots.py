import copy
import hashlib
import json
import unittest
from unittest.mock import patch
from nba_forecast.shots import normalize_shots, fetch_game_shots, playbyplay_url

try:
    from nba_forecast.shots import normalize_page_shots
except ImportError:
    normalize_page_shots = None


def page_fixture():
    from nba_forecast.data import STAT_FIELDS
    from tests.test_model import row
    gid = '0022600001'
    game = dict(game_id=gid, start_time='2026-10-20T00:00:00Z',
                season_type='Regular Season', home_team_id='1', away_team_id='2')
    known = '2026-10-21T00:00:00Z'
    rows = [row(pid='7', team='1', opponent_id='2', game_id=gid, season='2026-27',
                game_time=game['start_time'], known_at=known, fga=2, fgm=1, fg3a=1, fg3m=0),
            row(pid='8', team='1', opponent_id='2', game_id=gid, season='2026-27',
                game_time=game['start_time'], known_at=known, fga=0, fgm=0, fg3a=0, fg3m=0),
            row(pid='9', team='2', opponent_id='1', game_id=gid, season='2026-27',
                game_time=game['start_time'], known_at=known, fga=0, fgm=0, fg3a=0, fg3m=0)]
    players = []
    for r in rows:
        players.append(dict(personId=int(r['player_id']), firstName='Player', familyName=r['player_id'],
                            comment='', statistics=dict(minutes='1:00',
                            fieldGoalsAttempted=r['fga'], fieldGoalsMade=r['fgm'],
                            threePointersAttempted=r['fg3a'], threePointersMade=r['fg3m'],
                            **{k:r[f] for f,k in STAT_FIELDS.items() if f not in ('fga','fgm','fg3a','fg3m')})))
    source = dict(gameId=gid, gameStatus=3, gameTimeUTC=game['start_time'], period=4,
                  homeTeam=dict(teamId=1, players=players[:2]),
                  awayTeam=dict(teamId=2, players=players[2:]))
    for side in ('homeTeam','awayTeam'):
        team=source[side]
        team['score']=sum(p['statistics']['points'] for p in team['players'])
        team['statistics']={}
    actions = [dict(actionNumber=1, period=1, clock='PT11M00.00S', personId=7, teamId=1,
                    actionType='Made Shot', isFieldGoal=1, shotValue=2, shotResult='Made',
                    xLegacy=-148, yLegacy=224),
               dict(actionNumber=2, period=1, clock='PT10M00.00S', personId=7, teamId=1,
                    actionType='Missed Shot', isFieldGoal=1, shotValue=3, shotResult='Missed',
                    xLegacy=239, yLegacy=-19)]
    actions += [dict(actionNumber=10+i, period=i, clock='PT00M00.00S',
                     actionType='period', subType='end', isFieldGoal=0) for i in range(1,5)]
    page = dict(game=source, playByPlay=dict(gameId=gid, actions=actions))
    meta = dict(source_url=f'https://www.nba.com/game/{gid}/box-score', retrieved_at=known,
                sha256=hashlib.sha256(json.dumps(page).encode()).hexdigest())
    return page, game, meta, rows

class ShotTests(unittest.TestCase):
    def setUp(self):
        self.box={'game':{'gameId':'0022600001','gameStatus':3,'gameTimeUTC':'2026-10-20T00:00:00Z','homeTeam':{'teamId':1,'players':[{'personId':7,'statistics':{'fieldGoalsAttempted':2,'fieldGoalsMade':1,'threePointersAttempted':1,'threePointersMade':0}}]},'awayTeam':{'teamId':2,'players':[]}}}
        self.pbp={'game':{'gameId':'0022600001','actions':[{'actionNumber':1,'teamId':1,'personId':7,'actionType':'2pt','shotResult':'Made','xLegacy':10,'yLegacy':20},{'actionNumber':2,'teamId':1,'personId':7,'actionType':'3pt','shotResult':'Missed'}]}}
        self.time='2026-10-21T00:00:00Z'
    def test_counts_missing_coordinates(self):
        out=normalize_shots(self.pbp,self.box,self.time)
        self.assertEqual(out['coverage'],{'attempts':2,'located_attempts':1,'reconciled':True})
        self.assertIsNone(out['shots'][1]['x'])
    def test_reject_bad_evidence(self):
        for mutate in (lambda p:p['game']['actions'].pop(),lambda p:p['game']['actions'].append(copy.deepcopy(p['game']['actions'][0])),lambda p:p['game']['actions'][0].update(teamId=3),lambda p:p['game'].update(gameId='0022600002')):
            p=copy.deepcopy(self.pbp);mutate(p)
            with self.assertRaises(ValueError):normalize_shots(p,self.box,self.time)
        with self.assertRaises(ValueError):normalize_shots(self.pbp,self.box,'2026-10-19T00:00:00Z')
        self.box['game']['gameStatus']=2
        with self.assertRaises(ValueError):normalize_shots(self.pbp,self.box,self.time)
        with self.assertRaises(ValueError):playbyplay_url('0012600001')
    def test_source_failure_reason_preserved(self):
        with patch('nba_forecast.shots._fetch',side_effect=ValueError('NBA source returned HTTP 403')):
            with self.assertRaisesRegex(ValueError,'HTTP 403'):fetch_game_shots('0022600001','unused')


class OfficialPageShotTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(normalize_page_shots), 'Official page shot normalizer must be implemented')
        self.page, self.game, self.meta, self.rows = page_fixture()

    def normalize(self):
        return normalize_page_shots(self.page, self.game, self.meta, final_rows=self.rows)

    def test_official_coordinates_and_every_played_player_including_zero_attempts(self):
        out = self.normalize()
        self.assertEqual([(s['x'],s['y']) for s in out['shots']], [(-148,224),(239,-19)])
        self.assertEqual(out['coverage']['attempts'], 2)
        self.assertEqual(out['coverage']['played_players'], 3)
        self.assertEqual(out['coverage']['covered_players'], 3)
        self.assertEqual([(p['player_id'],p['attempts']) for p in out['coverage']['players']],
                         [('7',2),('8',0),('9',0)])
        self.assertEqual(out['provenance'][0]['sha256'], self.meta['sha256'])
        self.assertEqual(out['shots'][0]['source_sha256'], self.meta['sha256'])

    def test_live_schema_on_official_page_is_supported_without_guessing_shot_value(self):
        for action, kind in zip(self.page['playByPlay']['actions'][:2], ['2pt','3pt']):
            action['actionType']=kind
            action.pop('shotValue')
        self.assertEqual([s['points'] for s in self.normalize()['shots']], [2,3])

    def test_player_count_conflict_with_normalized_final_rejects_entire_game(self):
        self.rows[0]['fga'] = 3
        with self.assertRaisesRegex(ValueError, 'reconciliation|disagree'):
            self.normalize()

    def test_truncated_duplicate_unknown_identity_and_bad_source_are_rejected(self):
        for change in ('truncated','duplicate','unknown','point_value','source','final','periods','player_missing'):
            with self.subTest(change=change):
                self.page,self.game,self.meta,self.rows=page_fixture()
                actions=self.page['playByPlay']['actions']
                if change=='truncated': actions.pop(0)
                elif change=='duplicate': actions.append(copy.deepcopy(actions[0]))
                elif change=='unknown': actions[0]['personId']=100
                elif change=='point_value': actions[0]['shotValue']=1
                elif change=='source': self.meta['source_url']='https://www.nba.com/game/0022600002/box-score'
                elif change=='final': self.page['game']['gameStatus']=2
                elif change=='periods': actions.pop()
                else: self.rows.pop(1)
                with self.assertRaises(ValueError): self.normalize()

    def test_missing_or_sentinel_coordinates_remain_unlocated(self):
        self.page['playByPlay']['actions'][0].update(xLegacy=-214748340, yLegacy=-214748365)
        self.page['playByPlay']['actions'][1].pop('yLegacy')
        out=self.normalize()
        self.assertEqual(out['coverage']['attempts'],2)
        self.assertEqual(out['coverage']['located_attempts'],0)
        self.assertTrue(all(s['x'] is None and s['coordinate_missing_reason'] for s in out['shots']))

    def test_official_team_heave_retained_separately_without_inventing_player_fga(self):
        self.page['playByPlay']['actions'].insert(2,dict(actionNumber=3,period=1,clock='PT00M00.10S',
            actionType='Heave',subType='Team Field Goal Attempt',isFieldGoal=0,personId=0,teamId=1,
            description='HOME Heave'))
        out=self.normalize()
        self.assertEqual(out['coverage']['attempts'],2)
        self.assertEqual(len(out['team_attempts']),1)
        self.assertEqual(out['team_attempts'][0]['action_number'],3)

    def test_block_and_attempt_can_share_event_number_without_duplicate_shot(self):
        self.page['playByPlay']['actions'].insert(1,dict(actionNumber=1,period=1,
            clock='PT11M00.00S',actionType='',isFieldGoal=0,shotValue=2,
            personId=9,teamId=2,description='Player BLOCK'))
        self.assertEqual(self.normalize()['coverage']['attempts'],2)

    def test_stale_dnp_comment_is_resolved_by_verified_official_participation(self):
        self.page['game']['homeTeam']['players'][0]['comment']="DNP - Coach's Decision"
        out=self.normalize()
        self.assertEqual(out['coverage']['played_players'],3)
        self.assertIn('participation_audit',out['paired_final_rows'][0])

    @patch('nba_forecast.shots.fetch_official_game_page')
    @patch('nba_forecast.shots._fetch',side_effect=ValueError('NBA LiveData HTTP 403'))
    def test_blocked_livedata_uses_archived_official_page(self,fetch,official):
        official.return_value=(self.page,self.meta)
        out=fetch_game_shots(self.game['game_id'],'unused',game=self.game,final_rows=self.rows)
        self.assertEqual(out['coverage']['attempts'],2)
        self.assertIn('403',out['source_failures'][0]['reason'])
