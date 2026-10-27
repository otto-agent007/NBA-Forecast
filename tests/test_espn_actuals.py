"""ESPN fallback imports only complete, identity-bound final observations."""
import copy
import unittest
from unittest.mock import patch

try:
    from nba_forecast.espn_actuals import fetch_espn_final, normalize_espn_final
except ModuleNotFoundError:
    fetch_espn_final = normalize_espn_final = None
from nba_forecast.reference import BASE
from nba_forecast.validation import validate_history


def fixture():
    game = dict(game_id="0022500001", start_time="2026-01-05T20:00:00Z",
                season_type="Regular Season", home_team_id="H", away_team_id="A",
                home_tricode="GSW", away_tricode="NYK")
    roster = {"players": [dict(player_id="10", name="Home Player Jr.", team_id="H"),
                           dict(player_id="20", name="Away Player", team_id="A"),
                           dict(player_id="30", name="Unused Player", team_id="H")]}
    status = dict(type=dict(completed=True, state="post", name="STATUS_FINAL"), period=4)
    comp = dict(id="401811033", date=game["start_time"], status=status, competitors=[
        dict(homeAway="home", score="9", team=dict(id="9", abbreviation="GS")),
        dict(homeAway="away", score="9", team=dict(id="8", abbreviation="NY"))])
    keys = ["minutes", "points", "fieldGoalsMade-fieldGoalsAttempted",
            "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
            "freeThrowsMade-freeThrowsAttempted", "turnovers", "rebounds",
            "assists", "steals", "blocks", "offensiveRebounds"]
    boxes, teams = [], []
    for tid, name in [("9", "Home Player Jr."), ("8", "Away Player")]:
        entries = [dict(athlete=dict(id="ESPN" + tid, displayName=name),
                        didNotPlay=False, starter=True,
                        stats=["31", "9", "3-7", "1-3", "2-2", "1", "5", "2", "1", "0", "2"])]
        if tid == "9":
            entries.append(dict(athlete=dict(id="ESPN30", fullName="Unused Player"),
                                didNotPlay=True, starter=False, reason="COACH'S DECISION", stats=[]))
        boxes.append(dict(team=dict(id=tid), statistics=[dict(keys=keys, athletes=entries)]))
        teams.append(dict(team=dict(id=tid), statistics=[dict(name=k, displayValue=v)
                      for k, v in zip(keys[2:], ["3-7", "1-3", "2-2", "1", "5", "2", "1", "0", "2"])]))
    summary = dict(header=dict(id="401811033", season=dict(year=2026, type=2), competitions=[comp]),
                   boxscore=dict(players=boxes, teams=teams))
    meta = dict(source_url=BASE + "summary?event=401811033", retrieved_at="2026-01-05T23:00:00Z",
                sha256="a" * 64)
    return summary, game, roster, meta


class ESPNActualTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(normalize_espn_final), "ESPN normalizer must exist")
        self.summary, self.game, self.roster, self.meta = fixture()

    def normalize(self, **options):
        return normalize_espn_final(self.summary, self.game, self.roster, self.meta, **options)

    def test_final_rows_preserve_source_time_identity_and_minute_precision(self):
        before = copy.deepcopy(self.summary)
        rows = self.normalize()
        validate_history(rows)
        by_id = {row["player_id"]: row for row in rows}
        home = by_id["10"]
        self.assertEqual(home["pts"], 9)
        self.assertEqual(home["game_time"], self.game["start_time"])
        self.assertEqual(home["known_at"], self.meta["retrieved_at"])
        self.assertEqual(home["minutes"], 31)
        self.assertEqual(home["minutes_precision"], "whole_minutes")
        self.assertEqual(home["minutes_source_value"], "31")
        self.assertEqual(home["game_period"], 4)
        self.assertEqual(home["reb"], 5)
        self.assertEqual(home["oreb"], 2)
        self.assertEqual(home["source_sha256"], "a" * 64)
        self.assertEqual(by_id["30"]["status"], "dnp")
        self.assertEqual(by_id["30"]["minutes"], 0)
        self.assertEqual(self.summary, before)

    def test_clock_minutes_are_parsed_without_rounding_or_inventing_seconds(self):
        self.summary["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][0] = "31:30"
        row = self.normalize()[0]
        self.assertEqual(row["minutes"], 31.5)
        self.assertEqual(row["minutes_precision"], "seconds")

    def test_full_box_optional_fields_signed_plus_minus_and_derived_percentages(self):
        for box in self.summary['boxscore']['players']:
            group = box['statistics'][0]
            group['keys'] = group['keys'] + ['defensiveRebounds', 'fouls', 'plusMinus']
            group['athletes'][0]['stats'].extend(['3', '4', '-12'])
        row = self.normalize()[0]
        self.assertEqual([row.get(k) for k in ('oreb','dreb','pf','plus_minus')], [2,3,4,-12])
        self.assertAlmostEqual(row.get('fg_pct'), 3/7)
        self.assertAlmostEqual(row.get('fg3_pct'), 1/3)
        self.assertEqual(row.get('ft_pct'), 1)

    def test_missing_full_box_optional_fields_are_null_and_dnp_can_be_structural_zero(self):
        rows = {r['player_id']: r for r in self.normalize()}
        for key in ('dreb','pf','plus_minus'):
            self.assertIn(key, rows['10'])
            self.assertIsNone(rows['10'][key])
            self.assertEqual(rows['30'][key], 0)

    def test_status_matchup_date_season_or_event_mismatch_fails(self):
        for change in ["final", "team", "side", "date", "season", "event"]:
            with self.subTest(change=change):
                self.summary, self.game, self.roster, self.meta = fixture()
                comp = self.summary["header"]["competitions"][0]
                if change == "final":
                    comp["status"]["type"]["completed"] = False
                if change == "team":
                    comp["competitors"][0]["team"]["abbreviation"] = "BOS"
                if change == "side":
                    comp["competitors"][0]["homeAway"] = "away"
                if change == "date":
                    comp["date"] = "2026-01-06T20:00:00Z"
                if change == "season":
                    self.summary["header"]["season"]["type"] = 3
                if change == "event":
                    self.summary["header"]["id"] = "401811034"
                with self.assertRaises(ValueError):
                    self.normalize()

    def test_prestart_or_future_observation_is_rejected(self):
        for known_at in [self.game["start_time"], "2026-01-05T19:00:00Z", "2099-01-01T00:00:00Z"]:
            with self.subTest(known_at=known_at):
                self.meta["retrieved_at"] = known_at
                with self.assertRaises(ValueError):
                    self.normalize()

    def test_unknown_or_ambiguous_identity_is_rejected(self):
        self.roster["players"][0]["name"] = "Home Player"
        with self.assertRaisesRegex(ValueError, "identity"):
            self.normalize()
        self.roster["players"].append(dict(player_id="11", name="Home Player Jr.", team_id="H"))
        self.roster["players"][0]["name"] = "Home Player Jr."
        with self.assertRaisesRegex(ValueError, "identity"):
            self.normalize()

    def test_only_supplied_person_specific_alias_can_match_missing_suffix(self):
        self.summary["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["athlete"]["displayName"] = "Home Player"
        with self.assertRaisesRegex(ValueError, "identity"):
            self.normalize()
        rows = self.normalize(name_aliases=[dict(nba_id="10", nba_name="Home Player Jr.", espn_name="Home Player")])
        self.assertEqual(rows[0]["player_id"], "10")
        self.assertEqual(rows[0]["name"], "Home Player Jr.")
        self.assertIn("identity_alias", rows[0])
        with self.assertRaises(ValueError):
            self.normalize(name_aliases=[dict(nba_id="20", nba_name="Home Player Jr.", espn_name="Home Player")])

    def test_matching_team_historical_identity_can_resolve_roster_departure(self):
        person = self.roster["players"].pop(0)
        rows = self.normalize(history=[person])
        self.assertEqual(rows[0]["player_id"], "10")
        person["team_id"] = "different"
        with self.assertRaisesRegex(ValueError, "identity"):
            self.normalize(history=[person])

    def test_missing_required_stats_bad_shot_arithmetic_and_duplicate_players_fail(self):
        for change in ["missing", "identity", "points", "participation", "duplicate", "team_score"]:
            with self.subTest(change=change):
                self.summary, self.game, self.roster, self.meta = fixture()
                group = self.summary["boxscore"]["players"][0]["statistics"][0]
                entry = group["athletes"][0]
                if change == "missing":
                    entry["stats"][5] = "--"
                if change == "identity":
                    entry["stats"][2] = "8-7"
                if change == "points":
                    entry["stats"][1] = "10"
                if change == "participation":
                    del entry["didNotPlay"]
                if change == "duplicate":
                    group["athletes"].append(copy.deepcopy(entry))
                if change == "team_score":
                    self.summary["header"]["competitions"][0]["competitors"][0]["score"] = "10"
                with self.assertRaises(ValueError):
                    self.normalize()

    def test_optional_missing_or_invalid_categories_remain_null_with_errors(self):
        entry = self.summary["boxscore"]["players"][0]["statistics"][0]["athletes"][0]
        entry["stats"][6] = "--"
        entry["stats"][7] = "garbage"
        rows = self.normalize()
        self.assertIsNone(rows[0]["reb"])
        self.assertIsNone(rows[0]["ast"])
        self.assertIn("reb", rows[0]["optional_stat_errors"])
        self.assertIn("ast", rows[0]["optional_stat_errors"])

    def test_dnp_stats_cannot_contradict_explicit_nonparticipation(self):
        entry = self.summary["boxscore"]["players"][0]["statistics"][0]["athletes"][1]
        entry["stats"] = ["1", "9", "3-7", "1-3", "2-2", "1", "5", "2", "1", "0", "2"]
        with self.assertRaises(ValueError):
            self.normalize()

    def test_fetch_joins_unique_final_board_event_then_archives_summary(self):
        comp = copy.deepcopy(self.summary["header"]["competitions"][0])
        board = dict(season=dict(year=2026), events=[dict(id="401811033", competitions=[comp])])
        board_meta = dict(source_url=BASE + "scoreboard?dates=20260105",
                          retrieved_at="2026-01-05T23:01:00Z", sha256="b" * 64)
        with patch("nba_forecast.espn_actuals.fetch_reference", side_effect=[(board, board_meta), (self.summary, self.meta)]):
            rows = fetch_espn_final(self.game, self.roster, "unused")
        self.assertEqual(rows[0]["player_id"], "10")
        self.assertEqual(rows[0]["known_at"], board_meta["retrieved_at"])
        self.assertEqual(rows[0]["source_provenance"]["scoreboard"], board_meta)

    def test_fetch_ambiguous_or_live_board_does_not_request_a_summary(self):
        comp = copy.deepcopy(self.summary["header"]["competitions"][0])
        for events in [[dict(id="401811033", competitions=[comp])] * 2,
                       [dict(id="401811033", competitions=[{**comp, "status": dict(type=dict(completed=False))}])]]:
            with self.subTest(events=events), patch("nba_forecast.espn_actuals.fetch_reference", return_value=(dict(events=events), self.meta)) as fetch:
                with self.assertRaises(ValueError):
                    fetch_espn_final(self.game, self.roster, "unused")
                self.assertEqual(fetch.call_count, 1)

    def test_scoreboard_period_is_retained_when_summary_omits_it(self):
        comp = copy.deepcopy(self.summary["header"]["competitions"][0])
        comp["status"]["period"] = 5
        del self.summary["header"]["competitions"][0]["status"]["period"]
        board = dict(events=[dict(id="401811033", season=dict(year=2026, type=2), competitions=[comp])])
        board_meta = dict(source_url=BASE + "scoreboard?dates=20260105",
                          retrieved_at="2026-01-05T23:01:00Z", sha256="b" * 64)
        with patch("nba_forecast.espn_actuals.fetch_reference", side_effect=[(board, board_meta), (self.summary, self.meta)]):
            rows = fetch_espn_final(self.game, self.roster, "unused")
        self.assertEqual(rows[0]["game_period"], 5)
        self.assertEqual(rows[0]["game_period_source"], board_meta["source_url"])

    def test_wrong_scoreboard_season_rejects_before_summary_request(self):
        comp = copy.deepcopy(self.summary["header"]["competitions"][0])
        board = dict(events=[dict(id="401811033", season=dict(year=2027, type=2), competitions=[comp])])
        with patch("nba_forecast.espn_actuals.fetch_reference", return_value=(board, self.meta)) as fetch:
            with self.assertRaises(ValueError):
                fetch_espn_final(self.game, self.roster, "unused")
            self.assertEqual(fetch.call_count, 1)

    def test_team_core_stats_and_optional_rebounds_cannot_contradict_players(self):
        self.summary["boxscore"]["teams"][0]["statistics"][0]["displayValue"] = "3-8"
        with self.assertRaises(ValueError):
            self.normalize()
        self.summary, self.game, self.roster, self.meta = fixture()
        self.summary["boxscore"]["players"][0]["statistics"][0]["athletes"][0]["stats"][10] = "6"
        self.summary["boxscore"]["teams"][0]["statistics"][8]["displayValue"] = "6"
        with self.assertRaises(ValueError):
            self.normalize()

    def test_missing_optional_team_category_retains_an_explicit_error(self):
        self.summary["boxscore"]["teams"][0]["statistics"][8]["displayValue"] = "--"
        rows = self.normalize()
        self.assertIsNone(rows[0]["team_boxscore"]["oreb"])
        self.assertIn("oreb", rows[0]["team_optional_stat_errors"])
        self.assertEqual(rows[0]["oreb"], 2)

    def inactive_zero_entry(self):
        entry = self.summary["boxscore"]["players"][0]["statistics"][0]["athletes"][1]
        entry.update(didNotPlay=False, active=False, reason="COACH'S DECISION",
                     stats=["--", "0", "0-0", "0-0", "0-0", "0", "0", "0", "0", "0", "0"])
        return entry

    def test_inactive_coach_decision_with_explicit_zero_counts_resolves_provider_flag(self):
        self.inactive_zero_entry()
        before = copy.deepcopy(self.summary)
        row = {r["player_id"]: r for r in self.normalize()}["30"]
        self.assertEqual(row["status"], "dnp")
        self.assertEqual(row["minutes"], 0)
        self.assertEqual(row["minutes_source_value"], "--")
        self.assertEqual(row["pts"], 0)
        self.assertEqual(row["status_basis"], "inactive_zero_counts_despite_didNotPlay_false")
        self.assertEqual(row["participation_audit"]["didNotPlay"], False)
        self.assertEqual(row["participation_audit"]["active"], False)
        self.assertEqual(row["participation_audit"]["reason"], "COACH'S DECISION")
        self.assertEqual(self.summary, before)

    def test_unavailable_minutes_are_never_a_blanket_dnp_or_zero_count_rule(self):
        for change in ["active", "unknown_reason", "no_active", "positive_points", "positive_attempt",
                       "missing_count", "positive_optional", "invalid_minute_marker"]:
            with self.subTest(change=change):
                self.summary, self.game, self.roster, self.meta = fixture()
                entry = self.inactive_zero_entry()
                if change == "active":
                    entry["active"] = True
                if change == "no_active":
                    del entry["active"]
                if change == "unknown_reason":
                    entry["reason"] = "UNKNOWN"
                if change == "positive_points":
                    entry["stats"][1] = "1"
                if change == "positive_attempt":
                    entry["stats"][2] = "0-1"
                if change == "missing_count":
                    entry["stats"][5] = "--"
                if change == "positive_optional":
                    entry["stats"][6] = "1"
                if change == "invalid_minute_marker":
                    entry["stats"][0] = "garbage"
                with self.assertRaises(ValueError):
                    self.normalize()

    def test_positive_numeric_minutes_and_explicit_played_counts_are_not_relabelled_dnp(self):
        entry = self.summary["boxscore"]["players"][0]["statistics"][0]["athletes"][0]
        entry.update(active=False, reason="COACH'S DECISION")
        row = self.normalize()[0]
        self.assertEqual(row["status"], "played")
        self.assertEqual(row["minutes"], 31)
        self.assertEqual(row["pts"], 9)

    def test_resolved_dnp_keeps_unsupported_optional_counts_null(self):
        entry = self.inactive_zero_entry()
        entry["stats"][6] = "--"
        row = {r["player_id"]: r for r in self.normalize()}["30"]
        self.assertEqual(row["status"], "dnp")
        self.assertIsNone(row["reb"])
        self.assertIn("reb", row["optional_stat_errors"])

    def test_source_totals_conflict_is_explicit_pending_with_source_audit(self):
        from nba_forecast import espn_actuals
        self.assertTrue(hasattr(espn_actuals, "ActualsSourceConflict"), "source disagreement must have an explicit pending type")
        self.summary["boxscore"]["teams"][0]["statistics"][0]["displayValue"] = "3-6"
        with self.assertRaises(espn_actuals.ActualsSourceConflict) as caught:
            self.normalize()
        self.assertIsInstance(caught.exception, espn_actuals.ActualsPending)
        details = caught.exception.details
        self.assertEqual(details["statistic"], "fga")
        self.assertEqual(details["player_total"], 7)
        self.assertEqual(details["team_total"], 6)
        self.assertEqual(details["source_sha256"], self.meta["sha256"])

    def test_fetch_retries_source_conflict_once_without_using_older_cached_finals(self):
        conflict = copy.deepcopy(self.summary)
        conflict["boxscore"]["teams"][0]["statistics"][0]["displayValue"] = "3-6"
        comp = copy.deepcopy(self.summary["header"]["competitions"][0])
        board = dict(events=[dict(id="401811033", competitions=[comp])])
        board_meta = dict(source_url=BASE + "scoreboard?dates=20260105",
                          retrieved_at="2026-01-05T23:01:00Z", sha256="b" * 64)
        with patch("nba_forecast.espn_actuals.fetch_reference", side_effect=[
                (board, board_meta), (conflict, self.meta), (self.summary, self.meta)]) as fetch:
            rows = fetch_espn_final(self.game, self.roster, "unused")
        self.assertEqual(rows[0]["fga"], 7)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(fetch.call_args.kwargs["max_age"], 0)

    def test_persistent_provider_conflict_stays_pending_after_one_fresh_retry(self):
        from nba_forecast import espn_actuals
        self.assertTrue(hasattr(espn_actuals, "ActualsSourceConflict"), "source disagreement must have an explicit pending type")
        self.summary["boxscore"]["teams"][0]["statistics"][0]["displayValue"] = "3-6"
        comp = copy.deepcopy(self.summary["header"]["competitions"][0])
        board = dict(events=[dict(id="401811033", competitions=[comp])])
        board_meta = dict(source_url=BASE + "scoreboard?dates=20260105",
                          retrieved_at="2026-01-05T23:01:00Z", sha256="b" * 64)
        with patch("nba_forecast.espn_actuals.fetch_reference", side_effect=[
                (board, board_meta), (self.summary, self.meta), (self.summary, self.meta)]) as fetch:
            with self.assertRaises(espn_actuals.ActualsSourceConflict):
                fetch_espn_final(self.game, self.roster, "unused")
        self.assertEqual(fetch.call_count, 3)

    def test_source_conflict_is_retained_if_uncached_retry_is_unavailable(self):
        from nba_forecast.espn_actuals import ActualsSourceConflict
        self.summary["boxscore"]["teams"][0]["statistics"][0]["displayValue"] = "3-6"
        comp = copy.deepcopy(self.summary["header"]["competitions"][0])
        board = dict(events=[dict(id="401811033", competitions=[comp])])
        board_meta = dict(source_url=BASE + "scoreboard?dates=20260105",
                          retrieved_at="2026-01-05T23:01:00Z", sha256="b" * 64)
        with patch("nba_forecast.espn_actuals.fetch_reference", side_effect=[
                (board, board_meta), (self.summary, self.meta), OSError("network unavailable")]):
            with self.assertRaises(ActualsSourceConflict) as caught:
                fetch_espn_final(self.game, self.roster, "unused")
        self.assertEqual(caught.exception.details["player_total"], 7)
        self.assertIn("network unavailable", caught.exception.details["retry_error"])

    def test_uncached_retry_retains_the_same_historical_identity_evidence(self):
        conflict = copy.deepcopy(self.summary)
        conflict["boxscore"]["teams"][0]["statistics"][0]["displayValue"] = "3-6"
        comp = copy.deepcopy(self.summary["header"]["competitions"][0])
        board = dict(events=[dict(id="401811033", competitions=[comp])])
        board_meta = dict(source_url=BASE + "scoreboard?dates=20260105",
                          retrieved_at="2026-01-05T23:01:00Z", sha256="b" * 64)
        person = self.roster["players"].pop(0)
        with patch("nba_forecast.espn_actuals.fetch_reference", side_effect=[
                (board, board_meta), (conflict, self.meta), (self.summary, self.meta)]):
            rows = fetch_espn_final(self.game, self.roster, "unused", history=iter([person]))
        self.assertEqual(rows[0]["player_id"], "10")

    def test_explicit_dnp_can_use_unique_exact_sourced_historical_nba_identity(self):
        person = self.roster["players"].pop(2)
        previous = dict(person, team_id="OLD", game_id="0022500002",
                        source_url="https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022500002.json",
                        known_at="2026-01-04T23:00:00Z")
        rows = self.normalize(history=[previous])
        row = {r["player_id"]: r for r in rows}["30"]
        self.assertEqual(row["status"], "dnp")
        self.assertEqual(row["team_id"], "H")
        self.assertEqual(row["identity_resolution"]["source_team_id"], "OLD")
        self.assertEqual(row["identity_resolution"]["source_url"], previous["source_url"])
        self.assertEqual(row["identity_resolution"]["source_known_at"], previous["known_at"])
        self.assertEqual(row["known_at"], self.meta["retrieved_at"])

    def test_historical_dnp_identity_never_bypasses_ambiguity_or_source_cutoff(self):
        person = self.roster["players"].pop(2)
        previous = dict(person, team_id="OLD", game_id="0022500002",
                        source_url="https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022500002.json",
                        known_at="2026-01-04T23:00:00Z")
        for change in ("ambiguous", "future_known", "not_nba", "unobserved"):
            with self.subTest(change=change):
                evidence = copy.deepcopy(previous)
                history = [evidence]
                if change == "ambiguous":
                    history.append(dict(evidence, player_id="31"))
                if change == "future_known":
                    evidence["known_at"] = "2026-01-06T00:00:00Z"
                if change == "not_nba":
                    evidence["source_url"] = "https://example.test/identity"
                if change == "unobserved":
                    del evidence["known_at"]
                with self.assertRaisesRegex(ValueError, "identity"):
                    self.normalize(history=history)

    def test_cross_team_historical_identity_never_admits_an_unsupported_played_row(self):
        person = self.roster["players"].pop(0)
        previous = dict(person, team_id="OLD", game_id="0022500002",
                        source_url="https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022500002.json",
                        known_at="2026-01-04T23:00:00Z")
        with self.assertRaisesRegex(ValueError, "identity"):
            self.normalize(history=[previous])

    def test_exact_historical_dnp_identity_does_not_admit_positive_count_contradiction(self):
        person = self.roster["players"].pop(2)
        previous = dict(person, team_id="OLD", game_id="0022500002",
                        source_url="https://cdn.nba.com/static/json/liveData/boxscore/boxscore_0022500002.json",
                        known_at="2026-01-04T23:00:00Z")
        entry = self.summary["boxscore"]["players"][0]["statistics"][0]["athletes"][1]
        entry["stats"] = ["1", "9", "3-7", "1-3", "2-2", "1", "5", "2", "1", "0", "2"]
        with self.assertRaises(ValueError):
            self.normalize(history=[previous])


if __name__ == "__main__":
    unittest.main()
