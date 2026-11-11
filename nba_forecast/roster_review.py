"""Explicit, short-lived adjudication of peripheral provider disagreements."""
from copy import deepcopy
import re
from .injury_parser import normalized
from .rotation import projected_lineups
from .validation import parse_time


def active_review(snapshot,game,team):
    """The one roster review of this game and team in force at ``snapshot['as_of']``, or None:
    observed within six hours, not yet expired, for this tipoff, with a reason and reviewer."""
    try:
        reviews=[r for r in snapshot.get('roster_reviews',[]) if r['game_id']==game['game_id'] and r['team_id']==team]
        if len(reviews)!=1:
            return None
        review=reviews[0]
        cutoff=parse_time(snapshot['as_of'])
        if (parse_time(review['start_time'])!=parse_time(game['start_time']) or not
            0<=(cutoff-parse_time(review['observed_at'])).total_seconds()<=21600 or not
            cutoff<parse_time(review['expires_at'])<=parse_time(game['start_time']) or not review['reason'] or not review['reviewer']):
            return None
        return review
    except (ValueError,KeyError,TypeError):
        return None


def resolution(snapshot,profiles,reference,game,team):
    """The quarantine a roster review imposes on this team, or None.

    ``profiles`` None leaves out the projected-starter condition; only the
    evidence key does that (starters follow from inputs it already holds).
    """
    try:
        review=active_review(snapshot,game,team)
        if review is None:
            return None
        cutoff=parse_time(snapshot['as_of'])
        official=reference.get('official_rosters',{}).get(team,{})
        meta=official['evidence']
        if (official['team_id']!=team or official['season']!=snapshot['season'] or
            meta['source_url']!=f'https://www.nba.com/team/{team}/roster' or
            not re.fullmatch('[a-f0-9]{64}',meta['sha256']) or
            not 0<=(cutoff-parse_time(meta['retrieved_at'])).total_seconds()<=900):
            return None
        members=[p for p in snapshot['roster']['players'] if p['team_id']==team]
        expected={p['player_id']:normalized(p['name']) for p in members}
        if len(official['players'])!=len(expected) or len(set(expected.values()))!=len(expected):
            return None
        if {p['player_id']:normalized(p['name']) for p in official['players']}!=expected:
            return None
        names=set(expected.values())
        observed={normalized(n) for n in reference['rosters'][team]}
        if len(observed)!=len(reference['rosters'][team]):
            return None
        nba_only=names-observed
        espn_only=observed-names
        if not (nba_only or espn_only) or nba_only!={normalized(n) for n in review['nba_only']} or espn_only!={normalized(n) for n in review['espn_only']}:
            return None
        alias_ids=set()
        for alias in review.get('name_aliases',[]):
            if (alias['player_id'] not in expected or expected[alias['player_id']]!=normalized(alias['nba_name']) or
                not (normalized(alias['nba_name'])==normalized(alias['espn_name']+' Jr.') or normalized(alias['espn_name'])==normalized(alias['nba_name']+' Jr.')) or
                normalized(alias['nba_name']) not in nba_only or normalized(alias['espn_name']) not in espn_only):
                return None
            alias_ids.add(alias['player_id'])
        ids=set(review['quarantine_player_ids'])
        disputed={p['player_id'] for p in members if normalized(p['name']) in nba_only}-alias_ids
        starters=set(projected_lineups(snapshot['roster']['players'],profiles['players'])[team]['player_ids']) if profiles is not None else set()
        if not disputed<=ids or not ids<=expected.keys() or ids & starters:
            return None
        return dict(code='roster_membership_review',team_id=team,quarantine_player_ids=sorted(ids),
            detail=('A researched player-name suffix alias is verified. ' if alias_ids else 'Roster disagreement retained for review; disputed players have no numeric forecast. ')+review['reason'],
            nba_only=review['nba_only'],espn_only=review['espn_only'],evidence=meta,reviewer=review['reviewer'])
    except (ValueError,KeyError,TypeError):
        return None


def apply_roster_reviews(snapshot,profiles,reference):
    out=deepcopy(snapshot)
    out['roster_adjudications']=[]
    for game in out['games']:
        for team in (game['home_team_id'],game['away_team_id']):
            item=resolution(out,profiles,reference,game,team)
            if not item:
                continue
            out['roster_adjudications'].append(item)
            for p in out['roster']['players']:
                if p['player_id'] not in item['quarantine_player_ids']:
                    continue
                p.update(status='unknown',reported_status='Roster review',status_basis='roster_membership_review',
                    status_game_id=game['game_id'],status_source_url=item['evidence']['source_url'],
                    status_observed_at=item['evidence']['retrieved_at'])
                p.pop('status_published_at',None)
    return out
