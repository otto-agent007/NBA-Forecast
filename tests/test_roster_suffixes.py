"""Providers' generational-suffix spellings name the same person; real disagreements still block."""
import unittest

from nba_forecast.injury_parser import person_key as _person_key
from nba_forecast.preflight import check_inputs,_same_people
from tests.test_preflight import evidence


def rename(snapshot,reference,team,nba,espn,index=0):
    person=[p for p in snapshot['roster']['players'] if p['team_id']==team][index]
    old=person['name']
    person['name']=nba
    names=reference['rosters'][team]
    names[names.index(old)]=espn


class RosterSuffixTests(unittest.TestCase):
    def test_suffix_spellings_confirm_the_roster(self):
        s,p,r=evidence()
        rename(s,r,'T1','Bobby Portis Jr.','Bobby Portis')
        rename(s,r,'T2','Ernest Udeh','Ernest Udeh, Jr.')
        result=check_inputs(s,[],p,r)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertEqual(result['name_suffix_matches'],[dict(team_id='T1',nba=['Bobby Portis Jr.'],espn=['Bobby Portis']),
                                                        dict(team_id='T2',nba=['Ernest Udeh'],espn=['Ernest Udeh, Jr.'])])

    def test_a_real_difference_beside_a_suffix_is_noted_without_the_suffix_pair(self):
        s,p,r=evidence()
        rename(s,r,'T1','Bobby Portis Jr.','Bobby Portis')
        dropped=r['rosters']['T1'].pop()
        result=check_inputs(s,[],p,r)
        notes=[n for n in result['notes'] if n['code']=='roster_difference']
        self.assertEqual(result['status'],'passed')
        self.assertEqual([(n['team_id'],n['nba_only'],n['espn_only']) for n in notes],[('T1',[dropped],[])])

    def test_names_that_collide_without_suffixes_are_not_treated_as_matches(self):
        s,p,r=evidence()
        rename(s,r,'T1','Gary Payton II','Gary Payton',index=0)
        rename(s,r,'T1','Gary Payton','Gary Payton III',index=1)
        result=check_inputs(s,[],p,r)
        self.assertEqual(result['name_suffix_matches'],[])
        self.assertTrue(any(n['code']=='roster_difference' and n['team_id']=='T1' for n in result['notes']))

    def test_person_key(self):
        self.assertEqual(_person_key('Ernest Udeh, Jr.'),_person_key('Ernest Udeh'))
        self.assertEqual(_person_key('Gary Payton II'),'garypayton')
        self.assertEqual(_person_key('J.R. Smith'),'jrsmith')
        self.assertEqual(_person_key('Jr Smith'),'jrsmith')
        self.assertNotEqual(_person_key('Nikola Jokić'),_person_key('Nikola Jovic'))
        self.assertFalse(_same_people(['A B'],['A B','A B Jr.']))


if __name__=='__main__':
    unittest.main()


class ReportSuffixTests(unittest.TestCase):
    def setUp(self):
        from nba_forecast.schedule import normalize_season
        from tests.test_schedule import game,payload,NOW
        self.schedule=normalize_season(payload([game()]),NOW)
        self.evidence=dict(source_url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-10-21_07_30PM.pdf',
                           published_at='2026-10-21T23:30:00Z',retrieved_at='2026-10-21T23:31:00Z',sha256='a'*64)

    def rows(self,roster_names,report_name):
        from nba_forecast.injury_parser import parse_report
        roster={'players':[dict(player_id=str(i),name=n,team_id='1610612738',status='unknown') for i,n in enumerate(roster_names,1)]}
        text=f'10/21/2026  08:00 (ET)  BOS@NYK  Boston Celtics  {report_name}  Out  Injury'
        return [r['player_id'] for r in parse_report(text,self.schedule,roster,self.evidence)['rows']]

    def test_suffix_written_between_surname_and_given_name(self):
        self.assertEqual(self.rows(['Ernest Udeh, Jr.'],'Udeh, Jr., Ernest'),['1'])
        self.assertEqual(self.rows(['Bobby Portis Jr.'],'Portis Jr., Bobby'),['1'])

    def test_suffix_present_on_only_one_side(self):
        self.assertEqual(self.rows(['Bobby Portis'],'Portis Jr., Bobby'),['1'])
        self.assertEqual(self.rows(['Ernest Udeh, Jr.'],'Udeh, Ernest'),['1'])

    def test_a_suffix_never_picks_between_two_teammates(self):
        self.assertEqual(self.rows(['Gary Payton','Gary Payton II'],'Payton III, Gary'),[])
        self.assertEqual(self.rows(['Gary Payton','Gary Payton II'],'Payton II, Gary'),['2'])
