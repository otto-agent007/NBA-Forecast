import copy
import tempfile
import unittest
from nba_forecast import research_game_data as data


def fixture():
    rows=[]
    for tid,opp,pts in [('H','A',110),('A','H',100)]:
        rows.append(dict(game_id='nba1',game_time='2025-10-22T01:00:00Z',known_at='2026-10-22T00:00:00Z',season='2025-26',team_id=tid,opponent_id=opp,player_id=tid,minutes=240,pts=pts,fga=90,fta=20,tov=12))
    competitors=[dict(homeAway=s,score=str(p),team=dict(id=t,abbreviation=t)) for s,t,p in [('home','H',110),('away','A',100)]]
    comp=dict(id='123456',date='2025-10-22T01:00Z',competitors=competitors,status={'type':{'completed':True,'state':'post'}})
    teams=[dict(team=dict(id=t,abbreviation=t),statistics=[dict(name=k,displayValue=v) for k,v in [('fieldGoalsMade-fieldGoalsAttempted','40-90'),('freeThrowsMade-freeThrowsAttempted','15-20'),('offensiveRebounds','10'),('turnovers','12'),('teamTurnovers','3'),('totalTurnovers','15')]]) for t in ['H','A']]
    summary=dict(header=dict(id='123456',season={'year':2026,'type':2},competitions=[comp]),boxscore={'teams':teams})
    meta=dict(source_url='summary',retrieved_at='2026-10-23T00:00:00Z',sha256='abc')
    return rows,{'events':[dict(id='123456',competitions=[comp])]},summary,meta


class ResearchGameDataTests(unittest.TestCase):
    def test_exact_join_preserves_observation_and_minutes(self):
        rows,board,summary,meta=fixture()
        result=data.join_summary(rows,summary,{'H':'H','A':'A'},meta)
        self.assertEqual(result['known_at'],meta['retrieved_at'])
        self.assertEqual(result['team_stats']['H']['oreb'],10)
        self.assertEqual(result['team_stats']['H']['player_tov'],12)
        self.assertEqual(result['team_stats']['H']['tov'],15)
        self.assertEqual(result['team_minutes'],{'H':240,'A':240})
        self.assertEqual(result['home_team_id'],'H')
        self.assertEqual(result['provenance']['summary'],meta)

    def test_missing_counts_scores_duplicate_rows_and_wrong_dates_are_rejected(self):
        rows,_,summary,meta=fixture()
        variants=[]
        missing=copy.deepcopy(summary);missing['boxscore']['teams'][0]['statistics'].pop(2);variants.append((rows,missing))
        wrong=copy.deepcopy(summary);wrong['header']['competitions'][0]['competitors'][0]['score']='111';variants.append((rows,wrong))
        wrong=copy.deepcopy(summary);wrong['header']['competitions'][0]['date']='2025-10-23T01:00Z';variants.append((rows,wrong))
        variants.append((rows+[rows[0]],summary))
        for history,payload in variants:
            with self.subTest(payload=payload),self.assertRaises(ValueError):data.join_summary(history,payload,{'H':'H','A':'A'},meta)

    def test_collection_is_bounded_and_manifest_records_unmatched_game(self):
        rows,board,summary,meta=fixture();calls=[]
        def fetch(url,*args,**kwargs):
            calls.append(url);return (board if 'scoreboard?' in url else summary),meta
        with tempfile.TemporaryDirectory() as folder:
            records,manifest=data.collect_team_games(rows,{'H':'H','A':'A'},folder,workers=2,fetch=fetch)
            self.assertEqual(manifest['matched_games'],1)
            self.assertEqual(len(records),1)
            self.assertEqual(len(calls),2)
            board['events'].append(board['events'][0])
            records,manifest=data.collect_team_games(rows,{'H':'H','A':'A'},folder,fetch=fetch)
            self.assertEqual(len(records),0)
            self.assertEqual(len(manifest['failures']),1)
            self.assertIn('unique',manifest['failures'][0]['reason'])

class ResearchIntegrityTests(unittest.TestCase):
    def test_incomplete_minutes_and_nonfinal_summaries_are_rejected(self):
        rows,_,summary,meta=fixture()
        rows[0]['minutes']=230
        with self.assertRaisesRegex(ValueError,'minutes'):data.join_summary(rows,summary,{'H':'H','A':'A'},meta)
        rows,_,summary,meta=fixture();summary['header']['competitions'][0]['status']['type']['completed']=False
        with self.assertRaisesRegex(ValueError,'final'):data.join_summary(rows,summary,{'H':'H','A':'A'},meta)

    def test_same_team_on_both_summary_sides_is_rejected(self):
        rows,_,summary,meta=fixture()
        summary['header']['competitions'][0]['competitors'][1]=copy.deepcopy(summary['header']['competitions'][0]['competitors'][0])
        summary['header']['competitions'][0]['competitors'][1]['homeAway']='away'
        with self.assertRaisesRegex(ValueError,'pair'):data.join_summary(rows,summary,{'H':'H','A':'A'},meta)

    def test_actual_latest_observation_is_never_backdated(self):
        rows,_,summary,meta=fixture();rows[0]['known_at']='2026-12-01T00:00:00Z'
        result=data.join_summary(rows,summary,{'H':'H','A':'A'},meta)
        self.assertEqual(result['known_at'],'2026-12-01T00:00:00Z')

class ResearchVenueTests(unittest.TestCase):
    def test_venue_zone_uses_source_address_and_unknown_stays_unknown(self):
        rows,_,summary,meta=fixture()
        summary['gameInfo']={'venue':{'fullName':'Paycom Center','address':{'city':'Oklahoma City','state':'OK'}}}
        result=data.join_summary(rows,summary,{'H':'H','A':'A'},meta)
        self.assertEqual(result['venue_timezone'],'America/Chicago')
        summary['gameInfo']['venue']['address']={'city':'Unknown'}
        self.assertIsNone(data.join_summary(rows,summary,{'H':'H','A':'A'},meta)['venue_timezone'])

class ResearchTurnoverTests(unittest.TestCase):
    def test_missing_total_turnovers_and_arithmetic_disagreement_are_rejected(self):
        for value in (None,'16'):
            rows,_,summary,meta=fixture()
            stats=summary['boxscore']['teams'][0]['statistics']
            if value is None:summary['boxscore']['teams'][0]['statistics']=[s for s in stats if s['name']!='totalTurnovers']
            else:
                for s in stats:
                    if s['name']=='totalTurnovers':s['displayValue']=value
            with self.assertRaisesRegex(ValueError,'turnover'):data.join_summary(rows,summary,{'H':'H','A':'A'},meta)
