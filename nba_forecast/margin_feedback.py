"""Score frozen probabilities on every verified final, without picking winners."""
from math import fsum

from .margins import probability_above
from .full_boxscore_sources import validate_complete_official_final


def score_margin(distribution, home_score, away_score):
    if not distribution or distribution.get('status')!='estimated':
        return dict(status='unavailable',reason='No supported margin distribution was saved.')
    if any(type(v) is not int or v<0 for v in (home_score,away_score)) or home_score==away_score:
        return dict(status='pending',reason='A verified non-tied final score is required.')
    home=distribution['home_team_id'];actual=home_score-away_score
    win=probability_above(distribution,home,0)
    if win['status']!='estimated':return dict(status='unavailable',reason=win['reason'])
    thresholds=[]
    for line in (0,1.5,2.5,3.5,5.5,7.5,10.5):
        value=probability_above(distribution,home,line)
        if value['status']!='estimated':continue
        p=value['yes_probability'];outcome=actual>line
        thresholds.append(dict(team_id=home,line=line,yes_probability=p,actual_yes=outcome,brier=(p-outcome)**2))
    masses={r['margin']:r['probability'] for r in distribution['pmf']}
    low,high=min(masses),max(masses);cdf=0.;errors=[]
    for k in range(low,high):
        cdf+=masses.get(k,0.)
        errors.append((cdf-(actual<=k))**2)
    return dict(status='evaluated',actual_home_margin=actual,
        home_win_probability=win['yes_probability'],home_win_brier=(win['yes_probability']-(actual>0))**2,
        ranked_probability_score=fsum(errors),ranked_probability_score_support=[low,high],
        thresholds=thresholds,threshold_policy='Fixed diagnostic lines; these are not captured market quotes or owner selections.',
        calibration_status=distribution.get('calibration_status','uncalibrated'),settlement=distribution['settlement'])


def score_game_margin(distribution, game, actuals):
    """Partial player evaluation is useful but cannot establish a game margin."""
    if not distribution or distribution.get('status')!='estimated':
        return dict(status='unavailable',reason='No supported margin distribution was saved.')
    try:
        if any(distribution.get(k)!=game[k] for k in ('game_id','home_team_id','away_team_id')):
            raise ValueError('Margin game identity differs')
        validate_complete_official_final(actuals or [])
        teams={game['home_team_id'],game['away_team_id']}
        if {r['team_id'] for r in actuals}!=teams or any(r['game_id']!=game['game_id'] for r in actuals):
            raise ValueError('Final game identity differs')
    except (ValueError,KeyError,TypeError) as exc:
        return dict(status='pending',reason='Complete verified final required: '+str(exc))
    scores={t:sum(r['pts'] for r in actuals if r['team_id']==t) for t in teams}
    return score_margin(distribution,scores[game['home_team_id']],scores[game['away_team_id']])
