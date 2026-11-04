"""Official NBA game-page supplements must reconcile before changing actuals."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nba_forecast import data

try:
    from nba_forecast import full_boxscore_sources as sources
except ImportError:
    sources = None


def fixture():
    game = dict(game_id='0022600008', start_time='2026-10-23T23:00:00Z',
                season_type='Regular Season', home_team_id='1610612738',
                away_team_id='1610612752', home_tricode='BOS', away_tricode='NYK')
    stats = dict(minutes='31:07', fieldGoalsMade=3, fieldGoalsAttempted=7,
                 threePointersMade=1, threePointersAttempted=3, freeThrowsMade=2,
                 freeThrowsAttempted=2, reboundsOffensive=2, reboundsDefensive=3,
                 reboundsTotal=5, assists=2, steals=1, blocks=0, turnovers=1,
                 foulsPersonal=4, points=9, plusMinusPoints=-12,
                 fieldGoalsPercentage=.429)
    official = dict(gameId=game['game_id'], gameTimeUTC=game['start_time'], gameStatus=3, period=4)
    for side, pid in [('home', 10), ('away', 20)]:
        person = dict(personId=pid, firstName=side, familyName='Player',
                      comment='', statistics=copy.deepcopy(stats))
        dnp = dict(personId=pid+1, firstName=side, familyName='Unused',
                   comment="DNP - Coach's Decision", statistics={'minutes': ''})
        official[side+'Team'] = dict(teamId=int(game[side+'_team_id']),
                                    teamTricode=game[side+'_tricode'], score=9,
                                    players=[person,dnp], statistics=copy.deepcopy(stats))
    page = {'props': {'pageProps': {'game': official}}}
    return game, page


def encoded(page):
    return ('<html><script id="__NEXT_DATA__" type="application/json">'+json.dumps(page)+'</script></html>').encode()


class FullBoxscoreSourcesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sources, 'Official NBA full-box source helper must exist')
        self.game, self.page = fixture()

    def normalize(self, page=None, metadata=None):
        raw = encoded(self.page if page is None else page)
        meta = dict(source_url='https://www.nba.com/game/0022600008/box-score',
                    retrieved_at='2026-10-24T04:00:00Z', sha256=hashlib.sha256(raw).hexdigest())
        return sources.normalize_official_boxscore(raw, self.game, metadata or meta)

    def test_official_ids_source_seconds_optional_fields_and_percentages_are_retained(self):
        before = copy.deepcopy(self.page)
        rows = self.normalize()
        row = rows[0]
        self.assertEqual(row['player_id'], '10')
        self.assertEqual(row['team_id'], '1610612738')
        self.assertEqual(row['minutes_precision'], 'seconds')
        self.assertEqual(row['minutes_source_value'], '31:07')
        self.assertAlmostEqual(row['minutes'], 31+7/60)
        self.assertEqual([row[k] for k in ('oreb','dreb','pf','plus_minus')], [2,3,4,-12])
        self.assertAlmostEqual(row['fg_pct'], 3/7)
        self.assertEqual(rows[1]['status'], 'dnp')
        self.assertEqual(rows[1]['pf'], 0)
        self.assertEqual(self.page, before)

    def test_mismatched_game_team_time_score_or_source_hash_fails(self):
        for change in ('game', 'team', 'time', 'final', 'score', 'duplicate', 'rebound'):
            with self.subTest(change=change):
                _, page = fixture()
                g = page['props']['pageProps']['game']
                if change == 'game':
                    g['gameId'] = '0022600009'
                if change == 'team':
                    g['homeTeam']['teamId'] = 1610612740
                if change == 'time':
                    g['gameTimeUTC'] = '2026-10-23T22:00:00Z'
                if change == 'final':
                    g['gameStatus'] = 2
                if change == 'score':
                    g['homeTeam']['score'] = 10
                if change == 'duplicate':
                    g['homeTeam']['players'].append(copy.deepcopy(g['homeTeam']['players'][0]))
                if change == 'rebound':
                    g['homeTeam']['players'][0]['statistics']['reboundsDefensive'] = 4
                with self.assertRaises(ValueError):
                    self.normalize(page)
        with self.assertRaises(ValueError):
            self.normalize(metadata=dict(source_url='https://www.nba.com/game/0022600008/box-score',
                           retrieved_at='2026-10-24T04:00:00Z', sha256='0'*64))

    def test_missing_minutes_do_not_invent_dnp_and_optional_fields_remain_unknown(self):
        p = self.page['props']['pageProps']['game']['homeTeam']['players'][0]
        del p['statistics']['foulsPersonal']
        del p['statistics']['plusMinusPoints']
        rows = self.normalize()
        self.assertIsNone(rows[0]['pf'])
        self.assertIsNone(rows[0]['plus_minus'])
        p['statistics']['minutes'] = ''
        with self.assertRaises(ValueError):
            self.normalize()

    def test_livedata_shape_preserves_explicit_participation_and_iso_clock(self):
        g=self.page['props']['pageProps']['game']
        for side in ('homeTeam','awayTeam'):
            for person in g[side]['players']:
                dnp=bool(person['comment'])
                person['played']='0' if dnp else '1'
                person['name']=person['firstName']+' '+person['familyName']
                person['statistics']['minutes']='PT00M00.00S' if dnp else 'PT31M07.00S'
                del person['comment']
        rows=self.normalize()
        self.assertAlmostEqual(rows[0]['minutes'],31+7/60)
        self.assertEqual(rows[0]['minutes_source_value'],'PT31M07.00S')
        self.assertEqual(rows[1]['status'],'dnp')

    def test_not_with_team_comment_is_a_dnp_not_a_missing_clock(self):
        # 0022600160 (2026-11-02): a suspended player listed "NWT - League Suspension"
        # with an empty clock; the page failed to parse and blocked every forecast.
        unused=self.page['props']['pageProps']['game']['awayTeam']['players'][1]
        unused['comment']='NWT - League Suspension'
        row={r['player_id']:r for r in self.normalize()}['21']
        self.assertEqual((row['status'],row['minutes'],row['participation_reason']),('dnp',0,'NWT - League Suspension'))
        unused['comment']='Suspended'
        with self.assertRaises(ValueError):
            self.normalize()

    def test_positive_clock_overrides_stale_dnp_comment_with_explicit_audit(self):
        person=self.page['props']['pageProps']['game']['homeTeam']['players'][0]
        person['comment']="DNP - Coach's Decision"
        row=self.normalize()[0]
        self.assertEqual(row['status'],'played')
        self.assertEqual(row['participation_audit']['source_comment'],person['comment'])
        self.assertIn('positive',row['participation_audit']['resolution'])

    def test_source_seconds_overflow_is_carried_with_original_value_and_audit(self):
        person=self.page['props']['pageProps']['game']['homeTeam']['players'][0]
        person['statistics']['minutes']='10:60'
        row=self.normalize()[0]
        self.assertEqual(row['minutes'],11)
        self.assertEqual(row['minutes_source_value'],'10:60')
        self.assertIn('60',row['minutes_normalization'])
        person['statistics']['minutes']='10:61'
        with self.assertRaises(ValueError):
            self.normalize()

    def test_livedata_team_rebounds_separate_player_and_team_rebounds(self):
        team=self.page['props']['pageProps']['game']['homeTeam']
        team['statistics'].update(reboundsTotal=8,reboundsPersonal=5,reboundsTeam=3)
        rows=self.normalize()
        self.assertEqual(rows[0]['reb'],5)
        self.assertEqual(rows[0]['team_boxscore']['reb'],5)
        self.assertEqual(rows[0]['team_boxscore']['team_rebounds'],3)
        team['statistics']['reboundsTotal']=9
        with self.assertRaises(ValueError):
            self.normalize()

    def test_explicit_revision_preserves_before_after_counts_and_original_provenance(self):
        self.assertTrue(callable(getattr(sources,'revise_final_rows',None)),
                        'Core count corrections need a separate explicit audited revision API')
        official=self.normalize()
        old=copy.deepcopy(official)
        old[0].update(fga=8,fg_pct=3/8,blk=1,
                      source_url='https://site.api.espn.com/summary?event=401909095',
                      source_sha256='a'*64,known_at='2026-10-24T03:00:00Z')
        before=copy.deepcopy(old)
        revised=sources.revise_final_rows(old,official)
        self.assertEqual(old,before)
        self.assertEqual(revised[0]['fga'],7)
        self.assertEqual(revised[0]['official_final_revision']['changes']['fga'],{'before':8,'after':7})
        self.assertEqual(revised[0]['official_final_revision']['previous_row']['source_sha256'],'a'*64)
        self.assertEqual(revised[0]['source_url'],'https://www.nba.com/game/0022600008/box-score')
        old[0].update(pts=10,ftm=3,fta=3,ft_pct=1)
        with self.assertRaises(ValueError):
            sources.revise_final_rows(old,official)

    def test_material_minutes_correction_requires_explicit_revision_and_records_diff(self):
        official=self.normalize()
        old=copy.deepcopy(official)
        old[0].update(minutes=28,minutes_precision='whole_minutes',minutes_source_value='28')
        with self.assertRaises(ValueError):
            sources.enrich_final_rows(old,official)
        revised=sources.revise_final_rows(old,official)
        change=revised[0]['official_final_revision']['changes']['minutes']
        self.assertEqual(change['before'],28)
        self.assertAlmostEqual(change['after'],31+7/60)

    def test_starters_come_from_position_and_inactive_players_from_either_listing(self):
        page=copy.deepcopy(self.page)
        game=page['props']['pageProps']['game']
        game['homeTeam']['players'][0]['position']='F'
        game['homeTeam']['players'][1]['position']=''
        game['homeTeam']['inactives']=[dict(personId=12,firstName='home',familyName='Inactive',jerseyNum='1'),
                                       dict(personId=11,firstName='home',familyName='Unused',jerseyNum='2')]
        rows=self.normalize(page)
        by_id={r['player_id']:r for r in rows}
        self.assertIs(by_id['10']['starter'],True)
        self.assertIs(by_id['11']['starter'],False)
        self.assertNotIn('starter',by_id['20'])
        self.assertEqual((by_id['12']['status'],by_id['12']['minutes'],by_id['12']['participation_reason'],by_id['12']['name']),
                         ('dnp',0,'Inactive','home Inactive'))
        self.assertEqual([r['player_id'] for r in rows].count('11'),1)
        self.assertEqual((by_id['10']['official_inactives_listed'],by_id['20']['official_inactives_listed']),(True,False))
        game['awayTeam']['players'][1]['comment']='Inactive - Injury/Illness'
        self.assertTrue({r['player_id']:r for r in self.normalize(page)}['20']['official_inactives_listed'])

    def test_an_unmatched_original_dnp_is_retained_only_where_the_official_box_cannot_contradict_it(self):
        official=self.normalize()
        old=copy.deepcopy(official)
        old[1].update(player_id='999',name='Original DNP',participation_reason='Inactive - Injury/Illness')
        rows=sources.enrich_final_rows(old,official)
        row=rows[1]
        self.assertEqual(row['player_id'],'999')
        self.assertEqual(row['full_boxscore_enrichment']['status'],'original_dnp_absent_from_official')
        self.assertNotIn('identity_basis',row['full_boxscore_enrichment'])
        # The official box's own DNP, which the original lacked, is added.
        added=[r for r in rows if r['player_id']=='11']
        self.assertEqual([r['full_boxscore_enrichment']['status'] for r in added],['official_dnp_absent_from_original'])
        self.assertEqual((added[0]['status'],added[0]['team_id']),('dnp','1610612738'))
        # The official box lists every coach's-decision DNP, and everyone once its inactive list is out.
        stale=copy.deepcopy(old)
        stale[1]['participation_reason']="COACH'S DECISION"
        self.assertNotIn('999',[r['player_id'] for r in sources.enrich_final_rows(stale,official)])
        page=copy.deepcopy(self.page)
        page['props']['pageProps']['game']['homeTeam']['inactives']=[dict(personId=12,firstName='home',familyName='Inactive',jerseyNum='1')]
        listed=self.normalize(page)
        self.assertNotIn('999',[r['player_id'] for r in sources.revise_final_rows(old,listed)])

    def test_enrichment_preserves_original_sources_and_updates_knowledge_time(self):
        official = self.normalize()
        old = copy.deepcopy(official)
        for row in old:
            row.update(source_url='https://site.api.espn.com/summary?event=401909095',
                       source_sha256='a'*64, known_at='2026-10-24T03:00:00Z',
                       source_provenance={'summary': {'sha256':'a'*64}},
                       optional_stat_errors={'pf':'missing'})
            if row['status']=='played':
                row.update(minutes=31,minutes_precision='whole_minutes',minutes_source_value='31',
                           pf=None,dreb=None,plus_minus=None)
        before=copy.deepcopy(old)
        enriched=sources.enrich_final_rows(old,official)
        self.assertEqual(old,before)
        self.assertEqual(enriched[0]['source_sha256'],'a'*64)
        self.assertEqual(enriched[0]['minutes_source_value'],'31:07')
        self.assertEqual(enriched[0]['known_at'],'2026-10-24T04:00:00Z')
        self.assertEqual(enriched[0]['full_boxscore_enrichment']['previous_minutes_source_value'],'31')
        self.assertIn('nba_full_boxscore',enriched[0]['source_provenance'])
        self.assertNotIn('pf',enriched[0]['optional_stat_errors'])

    def test_enrichment_refuses_different_player_team_core_count_or_large_minute_disagreement(self):
        official=self.normalize()
        for change in ('player','team','points','missing_played','minutes','optional'):
            with self.subTest(change=change):
                old=copy.deepcopy(official)
                if change=='player':
                    old[0]['player_id']='999'
                if change=='team':
                    old[0]['team_id'],old[0]['opponent_id']=old[0]['opponent_id'],old[0]['team_id']
                if change=='points':
                    old[0].update(pts=10,ftm=3,fta=3,ft_pct=1)
                if change=='missing_played':
                    old=old[1:]
                if change=='minutes':
                    old[0].update(minutes=33,minutes_precision='whole_minutes',minutes_source_value='33')
                if change=='optional':
                    old[0]['pf']=5
                with self.assertRaises(ValueError):
                    sources.enrich_final_rows(old,official)

    def test_fetch_archives_unchanged_html_with_its_real_source(self):
        raw=encoded(self.page)
        with tempfile.TemporaryDirectory() as directory, patch('urllib.request.OpenerDirector.open') as request:
            request.return_value.__enter__.return_value.read.return_value=raw
            rows=sources.fetch_official_boxscore(self.game,cache_dir=directory)
            files=list(Path(directory).glob('*.html'))
            self.assertEqual(len(files),1)
            self.assertEqual(files[0].read_bytes(),raw)
            self.assertEqual(rows[0]['source_sha256'],hashlib.sha256(raw).hexdigest())

    def test_only_exact_regular_season_game_page_urls_are_allowlisted(self):
        for url in ('https://www.nba.com/game/0022600008/box-score?x=1',
                    'https://www.nba.com/game/0042600008/box-score',
                    'https://www.nba.com.evil.test/game/0022600008/box-score'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                data._allowed_url(url)

    def turnover_fixture(self):
        from nba_forecast.shots import normalize_page_shots
        page=copy.deepcopy(self.page['props']['pageProps'])
        actions=[]
        for tid,pid in [(1610612738,10),(1610612752,20)]:
            for points,made in [(3,True),(3,False),(3,False),(2,True),(2,True),(2,False),(2,False)]:
                actions.append(dict(actionNumber=len(actions)+1,period=1,clock='PT10M00.00S',
                    actionType='Made Shot' if made else 'Missed Shot',isFieldGoal=1,
                    shotValue=points,shotResult='Made' if made else 'Missed',
                    teamId=tid,personId=pid,xLegacy=0,yLegacy=20))
            actions.append(dict(actionNumber=200+pid,period=1,clock='PT09M00.00S',
                actionType='Turnover',isFieldGoal=0,teamId=tid,personId=pid))
        actions.extend([
            dict(actionNumber=301,period=2,clock='PT05M00.00S',actionType='turnover',isFieldGoal=0,
                 teamId=1610612738,personId=0),
            dict(actionNumber=302,period=3,clock='PT04M00.00S',actionType='Turnover',isFieldGoal=0,
                 teamId=0,personId=1610612752,location='v',playerName='',playerNameI=''),
            dict(actionNumber=303,period=4,clock='PT03M00.00S',actionType='turnover',isFieldGoal=0,
                 teamId=1610612752,qualifiers=['team']),
        ])
        actions.extend(dict(actionNumber=400+i,period=i,clock='PT00M00.00S',actionType='period',
            subType='end',isFieldGoal=0) for i in range(1,5))
        page['playByPlay']=dict(gameId=self.game['game_id'],actions=actions)
        raw=encoded({'props':{'pageProps':page}})
        meta=dict(source_url='https://www.nba.com/game/0022600008/box-score',
                  retrieved_at='2026-10-24T04:00:00Z',sha256=hashlib.sha256(raw).hexdigest())
        rows=sources.normalize_official_boxscore(raw,self.game,meta)
        verified=normalize_page_shots(page,self.game,meta,final_rows=rows)
        return page,rows,meta,verified

    def test_verified_full_pbp_derives_all_three_explicit_team_turnover_encodings(self):
        self.assertTrue(callable(getattr(sources,'supplement_official_team_turnovers',None)))
        page,rows,meta,verified=self.turnover_fixture()
        before=copy.deepcopy(rows)
        result=sources.supplement_official_team_turnovers(page,rows,meta,verified)
        home,away=result[0],result[2]
        self.assertEqual(home['team_boxscore']['team_turnovers'],1)
        self.assertEqual(home['team_boxscore']['total_turnovers'],2)
        self.assertEqual(away['team_boxscore']['team_turnovers'],2)
        self.assertEqual(away['team_boxscore']['total_turnovers'],3)
        self.assertEqual(len(away['team_turnover_audit']['team_events']),2)
        self.assertEqual(away['team_turnover_audit']['source_sha256'],meta['sha256'])
        self.assertEqual(away['known_at'],meta['retrieved_at'])
        self.assertEqual(rows,before)

    def test_turnover_derivation_rejects_missing_player_events_periods_or_unverified_evidence(self):
        self.assertTrue(callable(getattr(sources,'supplement_official_team_turnovers',None)))
        for change in ('player','period','unknown','side','duplicate','proof','source'):
            with self.subTest(change=change):
                page,rows,meta,verified=self.turnover_fixture()
                actions=page['playByPlay']['actions']
                if change=='player':
                    actions[:]=[a for a in actions if a['actionNumber']!=210]
                if change=='period':
                    actions[:]=[a for a in actions if a['actionNumber']!=404]
                if change=='unknown':
                    next(a for a in actions if a['actionNumber']==303).pop('qualifiers')
                if change=='side':
                    next(a for a in actions if a['actionNumber']==302)['location']='h'
                if change=='duplicate':
                    actions.append(copy.deepcopy(next(a for a in actions if a['actionNumber']==301)))
                if change=='proof':
                    verified['coverage']['reconciled']=False
                if change=='source':
                    verified['provenance'][0]['sha256']='a'*64
                with self.assertRaises(ValueError):
                    sources.supplement_official_team_turnovers(page,rows,meta,verified)

    def test_zero_team_turnovers_require_complete_periods_and_matched_individual_turnovers(self):
        self.assertTrue(callable(getattr(sources,'supplement_official_team_turnovers',None)))
        page,rows,meta,verified=self.turnover_fixture()
        page['playByPlay']['actions']=[a for a in page['playByPlay']['actions'] if a['actionNumber'] not in (301,302,303)]
        result=sources.supplement_official_team_turnovers(page,rows,meta,verified)
        self.assertEqual(result[0]['team_boxscore']['team_turnovers'],0)
        self.assertEqual(result[0]['team_boxscore']['total_turnovers'],1)
        self.assertEqual(result[0]['team_turnover_audit']['completed_periods'],[1,2,3,4])
        self.assertNotIn('team_turnovers',rows[0]['team_boxscore'])

    def test_team_metadata_is_coherent_for_every_row_including_unmatched_dnp(self):
        from nba_forecast.outcome_sync import environment_record
        official=self.normalize()
        for row in official:
            row['team_boxscore'].update(team_turnovers=2,total_turnovers=3)
        old=copy.deepcopy(official)
        old[1].update(player_id='999',name='Retained original DNP',participation_reason='Inactive - Injury/Illness')
        for row in old:
            row['team_boxscore'].update(reb=None,team_turnovers=1,total_turnovers=2)
        for operation in (sources.enrich_final_rows,sources.revise_final_rows):
            with self.subTest(operation=operation.__name__):
                revised=operation(old,official)
                self.assertEqual(revised[0]['team_boxscore'],revised[1]['team_boxscore'])
                self.assertEqual(revised[0]['team_boxscore']['reb'],5)
                self.assertEqual(revised[1]['team_boxscore']['total_turnovers'],3)
                self.assertEqual(revised[1]['full_boxscore_enrichment']['status'],'original_dnp_absent_from_official')
                self.assertIn('team_boxscore_provenance',revised[1])
                self.assertEqual(revised[1]['team_boxscore_update']['previous_team_boxscore']['total_turnovers'],2)
                record=environment_record(self.game,revised)
                self.assertEqual(record['team_stats']['1610612738']['total_turnovers'],3)

    def test_inconsistent_official_team_metadata_is_rejected_and_absent_components_stay_unknown(self):
        official=self.normalize()
        old=copy.deepcopy(official)
        result=sources.revise_final_rows(old,official)
        self.assertIsNone(result[0]['team_boxscore'].get('team_turnovers'))
        official[1]['team_boxscore']['tov']=2
        with self.assertRaisesRegex(ValueError,'team'):
            sources.revise_final_rows(old,official)
