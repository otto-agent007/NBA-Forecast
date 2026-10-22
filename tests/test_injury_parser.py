import unittest
from nba_forecast.injury_parser import parse_report, apply_report
from tests.test_schedule import game,payload,NOW
from nba_forecast.schedule import normalize_season

class InjuryParserTests(unittest.TestCase):
    def setUp(self):
        self.schedule=normalize_season(payload([game()]),NOW)
        self.roster={'players':[dict(player_id='1',name='Jayson Tatum',team_id='1610612738',status='unknown'),dict(player_id='2',name='Jaylen Brown',team_id='1610612738',status='unknown')]}
        self.evidence=dict(source_url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-21_07_30PM.pdf',published_at='2026-10-21T23:30:00Z',retrieved_at='2026-10-21T23:31:00Z',sha256='a'*64)
    def parse(self,text):return parse_report(text,self.schedule,self.roster,self.evidence)
    def test_exact_identity_and_continued_page(self):
        p=self.parse('10/21/2026  08:00 (ET)  BOS@NYK  Boston Celtics  Tatum, Jayson  Available  -\n\fInjury Report: 10/21/26 07:30 PM\n Brown, Jaylen  Doubtful  Injury\n New York Knicks  NOT YET SUBMITTED')
        self.assertEqual([r['status'] for r in p['rows']],['available','questionable'])
        self.assertTrue(p['issues'])
        s={'roster':self.roster,'games':[self.schedule['games'][0]],'as_of':'2026-10-21T23:32:00Z'}
        out=apply_report(s,p)
        self.assertEqual(out['roster']['players'][0]['status'],'available')
        self.assertEqual(self.roster['players'][0]['status'],'unknown')
    def test_omission_and_wrong_date_never_available(self):
        p=self.parse('10/22/2026  BOS@NYK  Boston Celtics  Tatum, Jayson  Available  -')
        self.assertEqual(p['rows'],[]);self.assertTrue(p['issues'])
    def test_ambiguous_name_never_joined(self):
        self.roster['players'].append(dict(self.roster['players'][0],player_id='3'))
        p=self.parse('10/21/2026  BOS@NYK  Boston Celtics  Tatum, Jayson  Available  -')
        self.assertEqual(p['rows'],[]);self.assertTrue(p['issues'])
    def test_unrecognized_name_and_conflicting_rows_fail_closed(self):
        p=self.parse('10/21/2026  BOS@NYK  Boston Celtics  Tatum, Jayson  Out  -\n Tatum, Jayson  Available  -\n Unknown, Player  Out  -')
        self.assertEqual(p['rows'],[]);self.assertGreaterEqual(len(p['issues']),2)
    def test_future_report_rejected_on_apply(self):
        p=self.parse('10/21/2026  BOS@NYK  Boston Celtics  Tatum, Jayson  Available  -')
        with self.assertRaises(ValueError):apply_report({'roster':self.roster,'games':[self.schedule['games'][0]],'as_of':NOW},p)

    def test_replacement_omission_clears_stale_availability(self):
        parsed=self.parse('10/21/2026  BOS@NYK  Boston Celtics  Tatum, Jayson  Available  -')
        snapshot={'roster':self.roster,'games':[self.schedule['games'][0]],'as_of':'2026-10-21T23:32:00Z'}
        available=apply_report(snapshot,parsed)
        missing=self.parse('10/21/2026  BOS@NYK  Boston Celtics  NOT YET SUBMITTED')
        self.assertEqual(apply_report(available,missing)['roster']['players'][0]['status'],'unknown')

    def test_missing_opponent_team_is_a_review_item(self):
        parsed=self.parse('10/21/2026  BOS@NYK  Boston Celtics  Tatum, Jayson  Available  -')
        self.assertTrue(any(i['kind']=='missing_team_coverage' and i['team_id']=='1610612752' for i in parsed['issues']))

    @unittest.skipUnless(__import__('shutil').which('pdftotext'),'Poppler not installed')
    def test_archived_official_pdf_exact_matches_and_unmatched_identity(self):
        import json
        from pathlib import Path
        from nba_forecast.injury_parser import pdf_text
        folder=Path(__file__).resolve().parents[1]/'examples/verified'
        fixture=json.loads((folder/'injury-parser-inputs.json').read_text())
        parsed=parse_report(pdf_text(folder/'injury-report-2026-10-21.pdf',fixture['evidence']['sha256']),fixture['schedule'],fixture['roster'],fixture['evidence'])
        self.assertEqual(len(parsed['rows']),91)
        self.assertTrue(any(i.get('name')=='Hinson, Blake' for i in parsed['issues']))
        self.assertEqual(len({(r['game_id'],r['player_id']) for r in parsed['rows']}),91)
