import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from nba_forecast.reference import BASE, collect_references, fetch_reference


class ReferenceTests(unittest.TestCase):
    def test_unapproved_urls_never_open(self):
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.build_opener') as opener:
            for url in ('https://example.test/roster', BASE+'teams/15/roster?token=x', BASE+'teams/../roster'):
                with self.assertRaises(ValueError):
                    fetch_reference(url,folder)
            opener.assert_not_called()

    def test_fresh_cache_checks_hash_and_retains_original_retrieval_time(self):
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.build_opener') as opener:
            root=Path(folder)
            url=BASE+'scoreboard?dates=20261023'
            raw=b'{"events":[]}'
            meta=dict(source_url=url,sha256=hashlib.sha256(raw).hexdigest(),retrieved_at=datetime.now(timezone.utc).isoformat())
            (root/'raw').mkdir()
            path=root/'raw'/(meta['sha256']+'.json')
            path.write_bytes(raw)
            (root/(hashlib.sha256(url.encode()).hexdigest()+'.json')).write_text(json.dumps(meta))
            payload,saved=fetch_reference(url,root)
            self.assertEqual(payload,{'events':[]})
            self.assertEqual(saved,meta)
            opener.assert_not_called()
            path.write_bytes(b'{"ev')  # a write cut short: fetched again, and the archive repaired
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=raw
            payload,saved=fetch_reference(url,root)
            self.assertEqual(payload,{'events':[]})
            self.assertEqual(path.read_bytes(),raw)
            opener.assert_called_once()

    def test_same_day_board_is_loaded_once_for_multi_game_slate(self):
        games=[]
        events=[]
        for n in range(2):
            start=f'2026-10-23T2{n}:00:00Z'
            home=f'H{n}'
            away=f'A{n}'
            games.append(dict(start_time=start,home_tricode=home,away_tricode=away,home_team_id=home,away_team_id=away))
            events.append(dict(date=start,competitions=[dict(status={'type':{'state':'pre','name':'STATUS_SCHEDULED'}},competitors=[
                dict(homeAway='home',team=dict(id=str(2*n),abbreviation=home)),dict(homeAway='away',team=dict(id=str(2*n+1),abbreviation=away))])]))
        def fetch(url,*args,**kwargs):
            if 'scoreboard?' in url:
                return {'events':events},{'source_url':url}
            return {'team':{'id':url.split('/')[-2]},'season':{'displayName':'2026-27'},'athletes':[{'fullName':'A Player'}]},{'source_url':url}
        with patch('nba_forecast.reference.fetch_reference',side_effect=fetch) as request:
            result=collect_references({'season':'2026-27','games':games},'unused')
        self.assertEqual(len(result['games']),2)
        self.assertEqual(len(result['rosters']),4)
        self.assertEqual(sum('scoreboard?' in c.args[0] for c in request.call_args_list),1)

    def test_wrong_roster_team_or_season_is_rejected(self):
        s={'season':'2026-27','games':[dict(start_time='2026-10-23T20:00:00Z',home_tricode='MIL',away_tricode='CHI',home_team_id='h',away_team_id='a')]}
        board={'events':[{'date':s['games'][0]['start_time'],'competitions':[{'competitors':[
            {'homeAway':'home','team':{'id':'15','abbreviation':'MIL'}},{'homeAway':'away','team':{'id':'4','abbreviation':'CHI'}}]}]}]}
        # A wrong ESPN roster is set aside (recorded), never used and never fatal to the game.
        for payload in ({'team':{'id':'9'}},{'team':{'id':'15'},'season':{'displayName':'2025-26'}}):
            def fetch(url,*args,**kwargs):
                if 'scoreboard?' in url:
                    return board,{}
                if '/teams/15/' in url:
                    return payload,{}
                return {'team':{'id':'4'},'season':{'displayName':'2026-27'},'athletes':[{'fullName':'A Player'}]},{}
            with self.subTest(payload=payload), patch('nba_forecast.reference.fetch_reference',side_effect=fetch):
                result=collect_references(s,'unused')
            self.assertEqual(list(result['rosters']),['a'])
            self.assertEqual([(f['kind'],f['team_id']) for f in result['failures'] if f['kind']=='roster'],[('roster','h')])

    def test_missing_official_team_loads_exact_event_summary(self):
        from tests.test_empty_injuries import fixture
        s,_,_,summary,meta=fixture()
        g=s['games'][0]
        board={'events':[dict(id='401909848',date=g['start_time'],competitions=[dict(id='401909848',competitors=summary['header']['competitions'][0]['competitors'])])]}
        def fetch(url,*args,**kwargs):
            if 'scoreboard?' in url:
                return board,meta
            if 'summary?' in url:
                return summary,meta
            tid='T1' if '/teams/1/' in url else 'T2'
            return {'team':{'id':'1' if tid=='T1' else '2'},'season':{'displayName':'2026-27'},
                'athletes':[{'fullName':p['name']} for p in s['roster']['players'] if p['team_id']==tid]},meta
        with patch('nba_forecast.reference.fetch_reference',side_effect=fetch) as request:
            result=collect_references(s,'unused')
        self.assertEqual(len(result['no_injuries']),1)
        self.assertEqual(result['no_injuries'][0]['team_id'],'T2')
        self.assertEqual(sum('summary?event=401909848' in c.args[0] for c in request.call_args_list),1)

    def test_official_team_roster_html_is_decoded_without_inventing_availability(self):
        from nba_forecast.reference import decode_reference
        team={'info':{'TEAM_ID':1610612752,'SEASON_YEAR':'2026-27'},'roster':[{'TeamID':1610612752,'SEASON':'2026','PLAYER_ID':1,'PLAYER':'A Player'}]}
        raw=('<html><script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':{'team':team}}})+'</script></html>').encode()
        decoded=decode_reference(raw,'https://www.nba.com/team/1610612752/roster')
        self.assertEqual(decoded,team)
        self.assertNotIn('status',decoded['roster'][0])


class DailyArchiveTests(unittest.TestCase):
    def test_daily_archive_captures_injuries_and_depth_charts_once(self):
        import tempfile
        from pathlib import Path
        from nba_forecast.reference import archive_daily_references
        calls=[]
        def fetch(url,directory,max_age=300):
            calls.append(url)
            if url.endswith('/teams/7/depthcharts'):
                raise OSError('offline')
            return {},dict(source_url=url,retrieved_at='2026-10-25T15:00:00Z',sha256='a'*64)
        with tempfile.TemporaryDirectory() as d,patch('nba_forecast.reference.fetch_reference',side_effect=fetch):
            first=archive_daily_references(d,'2026-10-25')
            again=archive_daily_references(d,'2026-10-25')
            self.assertTrue((Path(d)/'daily/2026-10-25.json').exists())
        self.assertEqual(len(calls),31)
        self.assertEqual((len(first['sources']),len(first['failures'])),(30,1))
        self.assertTrue(calls[0].endswith('/injuries'))
        self.assertEqual(again,first)

