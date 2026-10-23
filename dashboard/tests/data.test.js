import test from "node:test";
import assert from "node:assert/strict";
import {
  number,
  filterPlayers,
  gamePlayers,
  health,
  dateKey,
  initialDay,
  safeSource,
} from "../src/data.js";
test("unknown projections are not zero", () => {
  assert.equal(number(null), "—");
  assert.equal(number(0), "0");
  assert.equal(number(NaN), "—");
});
test("filter and numeric sort preserve unsupported rows", () => {
  let rows = [
    { name: "Alpha", status: "needs_review" },
    { name: "Beta", status: "projected", points_mean: 20 },
    { name: "Gamma", status: "out", points_mean: 0 },
  ];
  assert.equal(filterPlayers(rows, "", "review").length, 1);
  assert.equal(filterPlayers(rows, "", "all", "points")[0].name, "Beta");
  assert.equal(filterPlayers(rows, "none").length, 0);
});
test("roster membership cannot manufacture a forecast", () => {
  let rows = gamePlayers(
    { players: [{ name: "A", player_id: "1", team_id: "H" }] },
    { game_id: "g", home_team_id: "H", away_team_id: "A" },
  );
  assert.equal(rows[0].status, "not_forecast");
  assert.equal(rows[0].points_mean, undefined);
});
test("stale successful ticks are not live", () => {
  assert.equal(
    health(
      { status: "complete", finished_at: "2026-01-01" },
      Date.parse("2026-10-21"),
    ).kind,
    "warn",
  );
});
test("Pacific dates and upcoming slate selection", () => {
  assert.equal(dateKey("2026-10-22T02:00:00Z"), "2026-10-21");
  assert.equal(
    initialDay(
      { games: [{ start_time: "2026-10-23T02:00:00Z", status: "scheduled" }] },
      new Date("2026-10-22T03:00:00Z"),
    ),
    "2026-10-22",
  );
});
test("unsafe source schemes are rejected", () => {
  assert.equal(safeSource("javascript:alert(1)"), null);
  assert.equal(safeSource("https://nba.com"), "https://nba.com");
});

test("review filtering includes projected members of the saved role queue", () => {
  const game = {
    game_id: "g",
    home_team_id: "H",
    away_team_id: "A",
    forecast: {
      predictions: [
        { player_id: "1", name: "Role review", team_id: "H", status: "projected" },
        { player_id: "2", name: "Routine", team_id: "A", status: "projected" },
      ],
      review_queue: [{ player_id: "1", tier: "reasoning_review" }],
    },
  };
  const rows = gamePlayers({ players: [] }, game);
  assert.deepEqual(filterPlayers(rows, "", "review").map((p) => p.player_id), ["1"]);
  assert.equal(rows.find((p) => p.player_id === "1").status, "projected");
});

test("missing or empty saved queues preserve availability review filtering", () => {
  const game = {
    game_id: "g", home_team_id: "H", away_team_id: "A",
    forecast: { predictions: [{ player_id: "1", name: "Unknown", team_id: "H", status: "needs_review" }] },
  };
  assert.equal(filterPlayers(gamePlayers({ players: [] }, game), "", "review").length, 1);
  game.forecast.review_queue = [];
  assert.equal(filterPlayers(gamePlayers({ players: [] }, game), "", "review").length, 1);
});
