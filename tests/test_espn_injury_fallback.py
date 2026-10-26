"""Until the official report covers a team, ESPN's game injury list supplies availability."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from nba_forecast.availability import ESPN_BASIS,apply_espn_injuries,summary_injuries
from nba_forecast.preflight import check_inputs
from tests.test_preflight import evidence

URL='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401909848'


def pending(team='T2'):
    """The official report lacks this team (not yet submitted), so its players are unknown."""
    s,p,r=evidence()
    s['injury_extractions'][0]['complete_teams']=[c for c in s['injury_extractions'][0]['complete_teams'] if c['team_id']!=team]
    for player in s['roster']['players']:
        if player['team_id']==team:
            player['status']='unknown'
            for field in ('status_source_url','status_observed_at','status_published_at'):player.pop(field,None)
    return s,p,r


def espn_list(s,team='T2',players=(),retrieved='2026-10-22T10:59:00Z'):
    g=s['games'][0]
    return dict(game_id=g['game_id'],start_time=g['start_time'],team_id=team,provider_event_id='401909848',
                status='espn_injury_list',players=[dict(name=n,espn_id='1',status=st) for n,st in players],
                source_url=URL,retrieved_at=retrieved,sha256='b'*64)


def team(snapshot,tid='T2'):
    return {p['name']:p for p in snapshot['roster']['players'] if p['team_id']==tid}


class EspnFallbackTests(unittest.TestCase):
    def test_listed_players_take_espn_status_and_others_are_available(self):
        s,p,r=pending();names=sorted(team(s))
        out=apply_espn_injuries(s,[espn_list(s,players=[(names[0],'Out'),(names[1],'Day-To-Day'),(names[2],'Probable'),(names[3],'Unusual')])])
        people=team(out)
        self.assertEqual([people[n]['status'] for n in names],['out','questionable','available','questionable','available'])
        self.assertTrue(all(people[n]['status_basis']==ESPN_BASIS and people[n]['status_source_url']==URL for n in names))
        self.assertEqual(out['espn_injury_fallbacks'][0]['unmatched_players'],[])
        self.assertEqual({p['status'] for p in team(out,'T1').values()},{'available'})
        self.assertNotIn('status_basis',next(iter(team(out,'T1').values())))

    def test_official_coverage_wins_and_stale_or_ambiguous_lists_are_not_used(self):
        s,p,r=evidence()
        self.assertEqual(apply_espn_injuries(s,[espn_list(s,players=[('T2 Person 1','Out')])])['espn_injury_fallbacks'],[])
        s,p,r=pending()
        self.assertEqual(apply_espn_injuries(s,[espn_list(s,retrieved='2026-10-22T10:50:00Z')])['espn_injury_fallbacks'],[])
        self.assertEqual(apply_espn_injuries(s,[espn_list(s),espn_list(s)])['espn_injury_fallbacks'],[])
        out=apply_espn_injuries(s,[espn_list(s,players=[('Not On Roster','Out')])])
        self.assertEqual(out['espn_injury_fallbacks'][0]['unmatched_players'],['Not On Roster'])

    def test_preflight_passes_with_a_visible_pending_report_note(self):
        s,p,r=pending();r['espn_injuries']=[espn_list(s,players=[('T2 Person 1','Out')])]
        result=check_inputs(apply_espn_injuries(s,r['espn_injuries']),[],p,r)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertEqual([n['team_id'] for n in result['notes'] if n['code']=='official_report_pending'],['T2'])

    def test_espn_based_status_must_match_its_source(self):
        s,p,r=pending();r['espn_injuries']=[espn_list(s)]
        out=apply_espn_injuries(s,r['espn_injuries'])
        next(iter(team(out).values()))['status_observed_at']='2026-10-22T10:58:00Z'
        self.assertIn('availability_conflict',[i['code'] for i in check_inputs(out,[],p,r)['issues']])

    def test_run_preflight_applies_the_fallback_to_the_forecast_snapshot(self):
        from nba_forecast.preflight import run_preflight
        from nba_forecast.validation import parse_time
        import tempfile
        s,p,r=pending();r['espn_injuries']=[espn_list(s,players=[('T2 Person 1','Out')])]
        with tempfile.TemporaryDirectory() as folder,patch('nba_forecast.preflight.verify_extractions',return_value=s['injury_extractions']),\
             patch('nba_forecast.preflight.collect_references',return_value=r),patch('nba_forecast.preflight.datetime') as clock:
            clock.now.return_value=parse_time('2026-10-22T11:00:00Z')
            result=run_preflight(s,[],p,folder)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertEqual(team(s)['T2 Person 1']['status'],'out')
        self.assertEqual(s['espn_injury_fallbacks'][0]['team_id'],'T2')

    def test_model_labels_espn_based_availability(self):
        from nba_forecast.model import forecast
        from tests.test_model import row
        s,p,r=pending();out=apply_espn_injuries(s,[espn_list(s)])
        player=next(iter(team(out).values()))
        history=[row(i=i,pid=player['player_id'],team='T2',opponent_id='T1',season='2025-26',
                     game_time=f'2026-04-0{i+1}T20:00:00Z',known_at=f'2026-04-0{i+1}T23:00:00Z') for i in range(3)]
        prediction=next(x for x in forecast(out,history)['predictions'] if x['player_id']==player['player_id'])
        self.assertEqual(prediction['status'],'projected')
        self.assertIn('availability_official_report_pending',prediction['flags'])

    def test_summary_entries_are_read_and_unreadable_lists_skipped(self):
        from tests.test_empty_injuries import fixture
        s,_,_,payload,meta=fixture()
        athlete=dict(id='4433188',fullName='Devin Carter',displayName='Devin Carter')
        payload=deepcopy(payload);payload['injuries'][0]['injuries']=[dict(status='Out',athlete=athlete)]
        reports={x['team_id']:x for x in summary_injuries(payload,s['games'][0],s['season'],meta)}
        self.assertEqual(reports['T1']['players'],[dict(name='Devin Carter',espn_id='4433188',status='Out')])
        self.assertEqual(reports['T2']['players'],[])
        payload['injuries'][0]['injuries'].append(dict(status='Out'))
        self.assertEqual([x['team_id'] for x in summary_injuries(payload,s['games'][0],s['season'],meta)],['T2'])


if __name__=='__main__':
    unittest.main()
