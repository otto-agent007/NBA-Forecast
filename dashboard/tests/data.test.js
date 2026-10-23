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
  groupPlayers,
} from "../src/data.js";
test("unknown projections are not zero", () => {
  assert.equal(number(null), "—");
  assert.equal(number(0), "0");
  assert.equal(number(NaN), "—");
});

test("outlook groups teams, starters before bench, and minutes before first names", () => {
  const rows = [
    { player_id: "1", name: "Adam Bench", team_id: "A", team_code: "AAA", rotation_role: { label: "bench" }, expected_minutes: 20 },
    { player_id: "2", name: "Zach Starter", team_id: "A", team_code: "AAA", rotation_role: { label: "starter" }, expected_minutes: 35 },
    { player_id: "3", name: "Aaron Starter", team_id: "A", team_code: "AAA", rotation_role: { label: "starter" }, expected_minutes: 25 },
    { player_id: "4", name: "Other Team", team_id: "B", team_code: "BBB" },
    ...[5,6,7].map((i) => ({ player_id: String(i), name: `Starter ${i}`, team_id: 'A', team_code: 'AAA', rotation_role: { label: 'starter' }, expected_minutes: 24-i })),
  ];
  const groups = groupPlayers(filterPlayers(rows, "", "all", "rotation"));
  assert.equal(groups.length, 2);
  assert.equal(groups[0].groups[0].label, "Starters");
  assert.deepEqual(groups[0].groups[0].players.map((p) => p.player_id), ["2", "3", "5", "6", "7"]);
  assert.equal(groups[0].groups[1].label, "Bench");
  assert.equal(groups[1].groups[0].label, "Role unverified");
});

test("every full team has only five projected starters, stable across search and sort", () => {
  const rows = ['A', 'B'].flatMap((team) => Array.from({ length: 8 }, (_, i) => ({
    player_id: `${team}${i}`, team_id: team, team_code: team, name: `Player ${team}${i}`,
    status: i === 0 ? 'out' : 'projected', expected_minutes: 40-i,
    rotation_role: { label: 'starter', observations: 5, recent_minutes: 40-i },
  })));
  const all = groupPlayers(filterPlayers(rows), rows);
  assert.deepEqual(all.map((t) => t.groups.find((g) => g.key === 'starter').players.length), [5,5]);
  assert.ok(all.every((t) => !t.groups.find((g) => g.key === 'starter').players.some((p) => p.status === 'out')));
  const searched = groupPlayers(filterPlayers(rows, 'Player A7'), rows);
  assert.ok(!searched[0].groups.some((g) => g.key === 'starter'));
  const sorted = groupPlayers(filterPlayers(rows, '', 'all', 'points'), rows);
  assert.deepEqual(sorted.map((t) => t.groups.find((g) => g.key === 'starter').players.map((p) => p.player_id).sort()),
                   all.map((t) => t.groups.find((g) => g.key === 'starter').players.map((p) => p.player_id).sort()));
});

test("observed player statistics survive forecast row merging when projections are withheld", () => {
  const stats = { points_average: 18.5, games: 2, season: '2026-27' };
  const rows = gamePlayers({ players: [{ player_id: '1', team_id: 'H', name: 'Bucks Player', observed_stats: stats }] }, {
    game_id: 'g', home_team_id: 'H', away_team_id: 'A',
    forecast: { predictions: [{ player_id: '1', team_id: 'H', name: 'Bucks Player', status: 'needs_review', points_mean: null }] },
  });
  assert.deepEqual(rows[0].observed_stats, stats);
  assert.equal(rows[0].points_mean, null);
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

test('starter selection compares instants across mixed timestamp offsets', () => {
  const rows = Array.from({ length:6 }, (_,i) => ({ player_id:String(i),name:String(i),team_id:'A',team_code:'A',expected_minutes:30,
    rotation_role:{label:'starter',observations:2,last_game_started:true,
      last_game_time:i<5 ? (i===0 ? '2026-10-22T03:00:00Z' : '2026-10-21T20:00:00-07:00') : '2026-10-22T02:00:00Z'} }));
  assert.deepEqual(groupPlayers(rows)[0].groups.find(g=>g.key==='starter').players.map(p=>p.player_id).sort(), ['0','1','2','3','4']);
});
