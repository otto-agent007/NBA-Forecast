import unittest

from nba_forecast.market_history import closing_line
from nba_forecast.reference import fetch_reference


class ClosingLineTests(unittest.TestCase):
    def test_takes_the_first_pregame_provider_with_a_close(self):
        live = dict(provider=dict(name='ESPN Bet - Live Odds'), homeTeamOdds=dict(open={}), awayTeamOdds={})
        pregame = dict(provider=dict(name='ESPN BET'), close=dict(total=dict(american='237.5')),
                       homeTeamOdds=dict(close=dict(pointSpread=dict(american='-11.5'), moneyLine=dict(american='-700'))),
                       awayTeamOdds=dict(close=dict(pointSpread=dict(american='+11.5'), moneyLine=dict(american='+450'))))
        self.assertEqual(closing_line(dict(items=[live, pregame])),
                         dict(provider='ESPN BET', home_spread=-11.5, total=237.5, home_moneyline=-700, away_moneyline=450))

    def test_no_close_is_none_not_a_guess(self):
        self.assertIsNone(closing_line(dict(items=[dict(provider=dict(name='Live'), homeTeamOdds=dict(current={}))])))
        self.assertIsNone(closing_line({}))

    def test_only_the_event_odds_endpoint_is_allowlisted(self):
        with self.assertRaises(ValueError):
            fetch_reference('https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/401810017/competitions/401810018/odds', '/nonexistent')


if __name__ == '__main__':
    unittest.main()
