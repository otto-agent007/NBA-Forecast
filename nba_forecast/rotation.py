"""Descriptive recent role evidence, never a confirmed upcoming lineup."""
from statistics import fmean
from .validation import parse_time


def rotation_role(player,rows):
    recent=sorted((r for r in rows if player.get('team_id') and r.get('team_id')==player['team_id'] and r['status']=='played'),
                  key=lambda r:(parse_time(r['game_time']),r['game_id']))[-5:]
    observed=[r for r in recent if type(r.get('starter')) is bool]
    starts=sum(r['starter'] for r in observed)
    return dict(label=('starter' if starts>len(observed)/2 else 'bench') if observed else 'unknown',
                observations=len(observed),starts=starts,game_ids=[r['game_id'] for r in observed],
                recent_minutes=fmean(r['minutes'] for r in recent) if recent else None,
                confirmed_lineup=False,basis='Last five captured appearances on the current team; upcoming lineup unconfirmed.')
