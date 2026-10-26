"""Ingestion boundaries use an unmodified, sourced NBA opening-night boxscore."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from nba_forecast import data


FIXTURES = Path(__file__).resolve().parents[1] / "examples" / "verified"
URL = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022600001.json"
KNOWN = "2026-10-22T01:40:00+00:00"


class DataTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((FIXTURES / "boxscore_0022600001.json").read_text())

    def test_real_boxscore_retains_points_minutes_and_explicit_dnp(self):
        rows = data.normalize_boxscore(self.payload, URL, KNOWN)
        self.assertEqual({row["season"] for row in rows}, {"2026-27"})
        self.assertEqual(sum(row["pts"] for row in rows), 234)
        self.assertEqual(len(rows), sum(len(self.payload["game"][side]["players"]) for side in ("homeTeam", "awayTeam")))
        self.assertTrue(any(row["status"] == "dnp" and row["minutes"] == 0 for row in rows))
        self.assertTrue(all(row["known_at"] == KNOWN for row in rows))
        self.assertEqual({row["source_url"] for row in rows}, {URL})

    def test_starter_evidence_is_preserved_when_explicit(self):
        rows=data.normalize_boxscore(self.payload,URL,KNOWN)
        self.assertEqual(sum(r.get('starter',False) for r in rows),10)

    def test_full_box_fields_preserve_seconds_and_derive_percentages(self):
        player = self.payload['game']['homeTeam']['players'][0]
        player['statistics'].update(minutes='PT31M07.00S', reboundsOffensive=2,
                                    reboundsDefensive=5, reboundsTotal=7,
                                    foulsPersonal=3, plusMinusPoints=-8,
                                    fieldGoalsPercentage=0.999)
        rows = data.normalize_boxscore(self.payload, URL, KNOWN)
        result = next(r for r in rows if r['player_id'] == str(player['personId']))
        self.assertEqual(result.get('minutes_precision'), 'seconds')
        self.assertEqual(result.get('minutes_source_value'), 'PT31M07.00S')
        self.assertAlmostEqual(result['minutes'], 31 + 7/60)
        self.assertEqual([result.get(k) for k in ('oreb','dreb','pf','plus_minus')], [2,5,3,-8])
        self.assertAlmostEqual(result.get('fg_pct'), result['fgm']/result['fga'])

    def test_absent_full_box_optional_fields_remain_unknown(self):
        player = self.payload['game']['homeTeam']['players'][0]
        for key in ('reboundsOffensive','reboundsDefensive','foulsPersonal','plusMinusPoints'):
            player['statistics'].pop(key, None)
        rows = data.normalize_boxscore(self.payload, URL, KNOWN)
        result = next(r for r in rows if r['player_id'] == str(player['personId']))
        for key in ('oreb','dreb','pf','plus_minus'):
            self.assertIn(key, result)
            self.assertIsNone(result[key])

    def test_nonregular_and_nonfinal_games_are_rejected(self):
        for game_id, status in [("0012600001", 3), ("0042600001", 3), ("0062600001", 3), ("0022600001", 2)]:
            with self.subTest(game_id=game_id, status=status):
                payload = copy.deepcopy(self.payload)
                payload["game"].update(gameId=game_id, gameStatus=status)
                with self.assertRaises(ValueError):
                    data.normalize_boxscore(payload, URL, KNOWN)

    def test_missing_stats_are_not_zero_filled(self):
        del self.payload["game"]["homeTeam"]["players"][0]["statistics"]["fieldGoalsAttempted"]
        with self.assertRaises(ValueError):
            data.normalize_boxscore(self.payload, URL, KNOWN)

    def test_bad_scoring_identity_and_duplicate_player_are_rejected(self):
        bad = copy.deepcopy(self.payload)
        bad["game"]["homeTeam"]["players"][0]["statistics"]["points"] += 1
        with self.assertRaises(ValueError):
            data.normalize_boxscore(bad, URL, KNOWN)
        self.payload["game"]["homeTeam"]["players"].append(copy.deepcopy(self.payload["game"]["homeTeam"]["players"][0]))
        with self.assertRaises(ValueError):
            data.normalize_boxscore(self.payload, URL, KNOWN)

    def test_absent_player_is_not_invented_as_dnp(self):
        player = self.payload["game"]["homeTeam"]["players"].pop()
        rows = data.normalize_boxscore(self.payload, URL, KNOWN)
        self.assertNotIn(str(player["personId"]), {row["player_id"] for row in rows})

    def test_unsafe_network_targets_fail_before_request(self):
        for url in ["http://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json", "https://example.com/", "https://cdn.nba.com.evil.test/static/json/staticData/scheduleLeagueV2_1.json", "https://cdn.nba.com/static/json/staticData/other.json", URL + "?redirect=x"]:
            with self.subTest(url=url), patch("urllib.request.OpenerDirector.open") as request:
                with self.assertRaises(ValueError):
                    data.fetch_json(url)
                request.assert_not_called()

    def test_archive_preserves_exact_bytes_and_sha256(self):
        raw = (FIXTURES / "boxscore_0022600001.json").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            archive = data.archive_payload(raw, URL, KNOWN, directory)
            self.assertEqual(Path(archive["raw_path"]).read_bytes(), raw)
            self.assertEqual(archive["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(json.loads(Path(archive["provenance_path"]).read_text())["source_url"], URL)

    def test_fetch_json_archives_successful_response(self):
        raw = (FIXTURES / "boxscore_0022600001.json").read_bytes()
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.OpenerDirector.open") as open_request:
            open_request.return_value.__enter__.return_value.read.return_value = raw
            payload = data.fetch_json(URL, cache_dir=directory)
            self.assertEqual(payload["game"]["gameId"], "0022600001")
            metadata = list(Path(directory).glob("*.provenance.json"))
            self.assertEqual(len(metadata), 1)
            self.assertEqual(json.loads(metadata[0].read_text())["sha256"], hashlib.sha256(raw).hexdigest())

    def test_fetch_retries_are_bounded_and_nontransient_errors_fail_immediately(self):
        for status, attempts in [(503, 3), (403, 1)]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory, patch("urllib.request.OpenerDirector.open", side_effect=HTTPError(URL, status, "failed", {}, None)) as request, patch("time.sleep"):
                with self.assertRaises(ValueError):
                    data.fetch_json(URL, cache_dir=directory)
                self.assertEqual(request.call_count, attempts)

    def test_fetch_rejects_an_oversized_response(self):
        with tempfile.TemporaryDirectory() as directory, patch("urllib.request.OpenerDirector.open") as request:
            request.return_value.__enter__.return_value.read.return_value = b"x" * (data.MAX_BYTES + 1)
            with self.assertRaises(ValueError):
                data.fetch_json(URL, cache_dir=directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_roster_membership_does_not_claim_availability(self):
        players = [{"PERSON_ID": i + 1, "PLAYER_FIRST_NAME": "Player", "PLAYER_LAST_NAME": str(i), "TEAM_ID": 1610612737 + i, "ROSTER_STATUS": 1, "TO_YEAR": "2026"} for i in range(30)]
        roster = data.normalize_roster(players, KNOWN, "2026-27")
        self.assertTrue(roster["complete"])
        self.assertEqual(len(roster["team_ids"]), 30)
        self.assertEqual({row["status"] for row in roster["players"]}, {"unknown"})
        with self.assertRaises(ValueError):
            data.normalize_roster(players[:-1], KNOWN, "2026-27")

    def test_fetch_roster_uses_embedded_active_roster_and_original_observation_time(self):
        players = [{"PERSON_ID": i + 1, "PLAYER_FIRST_NAME": "Player", "PLAYER_LAST_NAME": str(i), "TEAM_ID": 1610612737 + i, "ROSTER_STATUS": 1, "TO_YEAR": "2026"} for i in range(30)]
        embedded = json.dumps({"props": {"pageProps": {"players": players}}})
        html = ('<html><script id="__NEXT_DATA__" type="application/json">' + embedded + '</script></html>').encode()
        with patch("nba_forecast.data._fetch", return_value=(html, KNOWN)):
            roster = data.fetch_roster("2026-27")
        self.assertEqual(len(roster["players"]), 30)
        self.assertEqual(roster["observed_at"], KNOWN)
        self.assertEqual({row["status"] for row in roster["players"]}, {"unknown"})

    def test_schedule_excludes_started_preseason_and_cup_championship(self):
        games = []
        for game_id, when, status in [("0022600005", "2026-10-22T02:00:00Z", 1), ("0012600005", "2026-10-22T02:00:00Z", 1), ("0062600005", "2026-10-22T02:00:00Z", 1), ("0022600004", "2026-10-21T23:30:00Z", 2), ("0022600006", "2026-10-24T02:00:00Z", 1)]:
            games.append({"gameId": game_id, "gameStatus": status, "gameDateTimeUTC": when, "homeTeam": {"teamId": 1610612737}, "awayTeam": {"teamId": 1610612738}})
        schedule = {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": [{"games": games}]}}
        future = data.normalize_schedule(schedule, KNOWN)
        self.assertEqual([game["game_id"] for game in future], ["0022600005"])

    def test_build_snapshot_preserves_verified_full_roster_and_enforces_cutoff(self):
        roster = json.loads((FIXTURES / "roster.json").read_text())
        schedule = {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": []}}
        snapshot = data.build_snapshot(roster, schedule, KNOWN)
        self.assertEqual(len(snapshot["roster"]["players"]), 519)
        self.assertEqual(snapshot["games"], [])
        with self.assertRaises(ValueError):
            data.build_snapshot(roster, schedule, "2026-10-21T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
