import unittest
from nba_forecast.injury_parser import parse_report,apply_report
from tests import test_injury_parser

class AvailabilityCoverageTests(unittest.TestCase):
    setUp=test_injury_parser.InjuryParserTests.setUp
    parse=test_injury_parser.InjuryParserTests.parse
    def complete(self,extra=''):
        return self.parse('Injury Report: 10/21/26 07:30 PM\n10/21/2026  BOS@NYK  Boston Celtics  Tatum, Jayson  Available  -\nNew York Knicks  NOT YET SUBMITTED\n'+extra+'\nPage 1 of 1')
    def test_complete_team_omission_is_conditional_and_opt_in(self):
        snapshot={'roster':self.roster,'games':[self.schedule['games'][0]],'as_of':'2026-10-21T23:32:00Z'}
        p=self.complete()
        self.assertEqual(apply_report(snapshot,p)['roster']['players'][1]['status'],'unknown')
        row=apply_report(snapshot,p,allow_not_listed=True)['roster']['players'][1]
        self.assertEqual(row['status'],'available')
        self.assertEqual(row['status_basis'],'not_listed_complete_report')
        self.assertEqual(row['reported_status'],'Not listed')
    def test_missing_pages_or_unmatched_row_prevents_inference(self):
        snapshot={'roster':self.roster,'games':[self.schedule['games'][0]],'as_of':'2026-10-21T23:32:00Z'}
        for parsed in [self.complete('Boston Celtics  Brown, Jaylen  Unavailable  Injury'),self.complete('Boston Celtics  Brown, Jaylen'),self.complete('Boston Celtics  Unknown, Person  Out  -'),self.parse('10/21/2026  BOS@NYK  Boston Celtics  Tatum, Jayson  Available  -')]:
            self.assertEqual(apply_report(snapshot,parsed,allow_not_listed=True)['roster']['players'][1]['status'],'unknown')
