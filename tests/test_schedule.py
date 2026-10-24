import copy
import tempfile
import unittest
from nba_forecast import schedule

NOW='2026-10-21T12:00:00Z'
def game(gid='0022600001',start='2026-10-22T00:00:00Z'):
    return dict(gameId=gid,gameDateTimeUTC=start,gameStatus=1,gameStatusText='8:00 pm ET',postponedStatus='N',arenaCity='New York',arenaState='NY',arenaName='MSG',homeTeam=dict(teamId=1610612752,teamCity='New York',teamName='Knicks',teamTricode='NYK'),awayTeam=dict(teamId=1610612738,teamCity='Boston',teamName='Celtics',teamTricode='BOS'))
def payload(games):return {'leagueSchedule':{'seasonYear':'2026-27','gameDates':[{'games':games}]}}

class ScheduleTests(unittest.TestCase):
    def test_preliminaries_open_at_nine_pacific_not_the_previous_afternoon(self):
        s=schedule.normalize_season(payload([game(start='2026-10-22T02:00:00Z')]),NOW)
        self.assertEqual(schedule.due_jobs(s,'2026-10-20T23:30:00Z'),[])
        self.assertEqual(schedule.due_jobs(s,'2026-10-21T15:59:59Z'),[])
        self.assertEqual(schedule.due_jobs(s,'2026-10-21T16:00:00Z')[0]['stage'],'preliminary')
        waiting=schedule.due_jobs(s,'2026-10-21T15:59:59Z',include_waiting=True)
        self.assertTrue(waiting[0]['waiting'])
        self.assertEqual(waiting[0]['ready_at'],'2026-10-21T09:00:00-07:00')

    def test_early_game_final_checkpoint_and_winter_nine_am_are_not_delayed(self):
        s=schedule.normalize_season(payload([game(start='2026-10-21T15:00:00Z')]),NOW)
        self.assertEqual(schedule.due_jobs(s,'2026-10-21T14:30:00Z')[0]['stage'],'final')
        s=schedule.normalize_season(payload([game(start='2026-12-22T02:00:00Z')]),NOW)
        self.assertEqual(schedule.due_jobs(s,'2026-12-21T16:59:59Z'),[])
        self.assertEqual(schedule.due_jobs(s,'2026-12-21T17:00:00Z')[0]['stage'],'preliminary')
    def test_retains_tbd_and_excludes_preseason(self):
        t=game('0022600002');t['gameStatusText']='TBD';t['homeTeam']['teamId']=0
        s=schedule.normalize_season(payload([game(),t,game('0012600001')]),NOW)
        self.assertEqual(len(s['games']),2)
        self.assertIsNone(s['games'][1]['start_time'])
        self.assertEqual(s['games'][0]['local_start_time'],'2026-10-21T20:00:00-04:00')
        self.assertFalse(s['coverage']['complete_1230'])
    def test_postponed_is_not_due_and_t30_per_game(self):
        p=game('0022600002');p['postponedStatus']='Y'
        s=schedule.normalize_season(payload([game(),p]),NOW)
        jobs=schedule.due_jobs(s,'2026-10-21T23:31:00Z')
        self.assertEqual([(j['game_id'],j['stage']) for j in jobs],[('0022600001','final')])
        self.assertEqual(schedule.due_jobs(s,'2026-10-22T00:00:00Z'),[])
    def test_versions_preserve_changed_tipoff_and_removed_game(self):
        with tempfile.TemporaryDirectory() as d:
            a=schedule.save_schedule(payload([game()]),NOW,d)
            b=schedule.save_schedule(payload([]),'2026-10-21T13:00:00Z',d)
            self.assertNotEqual(a['version'],b['version'])
            self.assertEqual(b['changes']['removed'],['0022600001'])
            self.assertEqual(len(list(__import__('pathlib').Path(d).glob('versions/*.json'))),2)
    def test_rest_and_dst_use_eastern_game_dates(self):
        a=game(start='2026-10-21T00:00:00Z');a['gameStatus']=3
        b=game('0022600002','2026-10-22T00:00:00Z')
        s=schedule.normalize_season(payload([a,b]),NOW)
        ctx=schedule.game_context(s,'0022600002')['1610612752']
        self.assertTrue(ctx['back_to_back']);self.assertEqual(ctx['rest_days'],0)
        c=game(start='2026-11-03T01:00:00Z')
        self.assertTrue(schedule.normalize_season(payload([c]),NOW)['games'][0]['local_start_time'].endswith('-05:00'))
    def test_duplicate_or_wrong_season_fails(self):
        for gs in ([game(),game()],[game('0022500001')]):
            with self.assertRaises(ValueError):schedule.normalize_season(payload(gs),NOW)
