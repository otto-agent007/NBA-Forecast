from copy import deepcopy
import unittest
from tests.test_preflight import evidence


def fixture():
    s,p,r=evidence();g=s['games'][0]
    member=next(person for person in s['roster']['players'] if person['team_id']=='T2')
    for profile in p['players']:
        if profile['player_id']==member['player_id']:profile.update(expected_minutes_baseline=0,rotation_role={})
    # Six team members leave five undisputed eligible starters.
    extra={**member,'player_id':'extra','name':'Sixth Role'};s['roster']['players'].append(extra)
    p['players'].append(dict(player_id='extra',expected_minutes_baseline=30,rotation_role=dict(label='starter',observations=1)))
    r['rosters']['T2']=[x['name'] for x in s['roster']['players'] if x['team_id']=='T2' and x['player_id']!=member['player_id']]
    official=dict(team_id='T2',season=s['season'],players=[{k:x[k] for k in ('player_id','name')} for x in s['roster']['players'] if x['team_id']=='T2'],
        evidence=dict(source_url='https://www.nba.com/team/T2/roster',retrieved_at='2026-10-22T10:59:00Z',sha256='c'*64))
    r['official_rosters']={'T2':official};r['sources'].append(official['evidence'])
    s['roster_reviews']=[dict(game_id=g['game_id'],start_time=g['start_time'],team_id='T2',observed_at='2026-10-22T10:59:00Z',
        expires_at=g['start_time'],reviewer='source-reconciliation',reason='Confirmed official two-way roster; provider discrepancy quarantined.',
        nba_only=[member['name']],espn_only=[],quarantine_player_ids=[member['player_id']])]
    return s,p,r,member['player_id']


class RosterReviewTests(unittest.TestCase):
    def test_evidence_bound_peripheral_mismatch_is_quarantined_with_visible_warning(self):
        from nba_forecast.roster_review import apply_roster_reviews
        from nba_forecast.preflight import check_inputs
        s,p,r,pid=fixture();out=apply_roster_reviews(s,p,r)
        member=next(x for x in out['roster']['players'] if x['player_id']==pid)
        self.assertEqual(member['status'],'unknown');self.assertEqual(member['status_basis'],'roster_membership_review')
        result=check_inputs(out,[],p,r)
        self.assertEqual(result['status'],'passed',result['issues'])
        self.assertTrue(any(w['code']=='roster_membership_review' for w in result['warnings']))
        self.assertEqual(next(x for x in s['roster']['players'] if x['player_id']==pid)['status'],'available')

    def test_stale_primary_changed_discrepancy_or_expired_review_still_blocks(self):
        from nba_forecast.roster_review import apply_roster_reviews
        from nba_forecast.preflight import check_inputs
        for kind in ('stale','extra','expired','official'):
            s,p,r,pid=fixture()
            if kind=='stale':r['official_rosters']['T2']['evidence']['retrieved_at']='2026-10-22T09:00:00Z'
            if kind=='extra':r['rosters']['T2'].append('Unexpected New Player')
            if kind=='expired':s['roster_reviews'][0]['expires_at']='2026-10-22T10:59:00Z'
            if kind=='official':r['official_rosters']['T2']['players'].pop()
            with self.subTest(kind=kind):self.assertEqual(check_inputs(apply_roster_reviews(s,p,r),[],p,r)['status'],'blocked')

    def test_a_projected_starter_cannot_be_disregarded_through_a_roster_review(self):
        from nba_forecast.roster_review import apply_roster_reviews
        from nba_forecast.preflight import check_inputs
        s,p,r,pid=fixture()
        starter=next(x['player_id'] for x in s['roster']['players'] if x['team_id']=='T2' and x['player_id']!=pid)
        s['roster_reviews'][0]['quarantine_player_ids'].append(starter)
        self.assertEqual(check_inputs(apply_roster_reviews(s,p,r),[],p,r)['status'],'blocked')
