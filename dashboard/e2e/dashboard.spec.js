import { test, expect } from "@playwright/test";

// Synthetic saved evidence: browser tests make no NBA requests or accuracy claims.
const gameId = "0022600001";
const start = "2026-10-22T02:00:00Z";
const players = [
  { player_id: "101", name: "Role Player", team_id: "1610612747", status: "projected" },
  { player_id: "102", name: "Routine Player", team_id: "1610612744", status: "projected" },
];
const game = {
  game_id: gameId, start_time: start, status: "final",
  home_team_id: "1610612747", away_team_id: "1610612744",
  home_tricode: "LAL", away_tricode: "GSW",
  home_name: "Los Angeles Lakers", away_name: "Golden State Warriors",
  forecast: {
    stage: "preliminary", model_version: "synthetic-test",
    created_at: "2026-10-22T01:00:00Z", as_of: "2026-10-22T01:00:00Z",
    predictions: players.map((p) => ({ ...p, game_id: gameId, start_time: start, expected_minutes: 30, points_mean: 20 })),
    review_queue: [{ player_id: "101", game_id: gameId, tier: "reasoning_review", flags: ["minutes_change"] }],
  },
};
const dashboard = {
  schema_version: 1, source_mode: "saved_operations", season: "2026-27",
  generated_at: "2026-10-22T06:00:00Z", games: [game], players,
};
function recap(id = gameId, pattern = "Competitive finish") {
  return {
    game_id: id, pattern, duration: 2880,
    home: { id: "1610612747", code: "LAL", score: 100 },
    away: { id: "1610612744", code: "GSW", score: 98 },
    largest_home_lead: 5, largest_away_lead: 4, low_leverage_start: null,
    timeline: [{ elapsed: 0, margin: 0 }, { elapsed: 2880, margin: 2 }],
    players: [], review_queue: [], team_stats: { "1610612747": {}, "1610612744": {} },
    coverage: { rotation: "Synthetic fixture" }, provenance: [],
  };
}

test.beforeEach(async ({ page }) => {
  await page.route("https://cdn.nba.com/**", (route) => route.fulfill({
    contentType: "image/png",
    body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lxkAAAAASUVORK5CYII=", "base64"),
  }));
});

test("View review queue includes projected role reviews and excludes routine players", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/dashboard", (route) => route.fulfill({ json: dashboard }));
  await page.goto("/");
  await expect(page).toHaveTitle("NBA Forecast · Game day");
  await expect(page.getByRole("heading", { name: "Game day", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "View review queue" }).click();
  await expect(page.getByRole("button", { name: "Role Player", exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Routine Player", exact: false })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("Refresh reloads newly available and corrected postgame evidence for the same game", async ({ page }) => {
  let response = { missing_reason: "Postgame capture is pending." };
  let requests = 0;
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/dashboard", (route) => route.fulfill({ json: dashboard }));
  await page.route(`**/api/postgame/${gameId}`, (route) => {
    requests += 1;
    return route.fulfill({ json: response });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Postgame", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Postgame evidence unavailable" })).toBeVisible();
  expect(requests).toBe(1);
  response = recap();
  await page.getByRole("button", { name: "Refresh data", exact: true }).click();
  await expect(page.getByRole("heading", { name: "What happened", exact: true })).toBeVisible();
  expect(requests).toBe(2);
  response = recap(gameId, "Comeback win");
  await page.getByRole("button", { name: "Refresh data", exact: true }).click();
  await expect(page.locator(".postgame-final")).toContainText("Comeback win");
  expect(requests).toBe(3);
  expect(errors).toEqual([]);
});

test("switching games cancels a pending recap and keeps the newly selected game", async ({ page }) => {
  const secondId = "0022600002";
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  let firstRequested;
  const requested = new Promise((resolve) => { firstRequested = resolve; });
  await page.route("**/api/dashboard", (route) => route.fulfill({ json: {
    ...dashboard,
    games: [game, { ...game, game_id: secondId, home_tricode: "BOS", away_tricode: "NYK", forecast: null }],
  } }));
  await page.route(`**/api/postgame/${gameId}`, async (route) => {
    firstRequested();
    await pending;
    await route.fulfill({ json: recap(gameId, "Old game") });
  });
  await page.route(`**/api/postgame/${secondId}`, (route) => route.fulfill({ json: recap(secondId, "New game") }));
  await page.goto("/");
  await page.getByRole("button", { name: "Postgame", exact: true }).click();
  await requested;
  const failed = page.waitForEvent("requestfailed", (request) => request.url().endsWith(`/api/postgame/${gameId}`));
  await page.getByRole("button", { name: "NYK at BOS", exact: true }).click();
  await expect(page.locator(".postgame-final")).toContainText("New game");
  release();
  // The aborted request cannot overwrite the currently selected game's response.
  await failed;
  await expect(page.locator(".postgame-final")).toContainText("New game");
});

test("player outlook separates teams and places starters before bench by minutes", async ({ page }) => {
  const staged = structuredClone(dashboard);
  staged.runner = { status: 'partial', finished_at: '2026-10-22T06:00:00Z', warnings: [{ task: 'schedule' }] };
  staged.games[0].forecast.predictions = [
    { ...game.forecast.predictions[0], rotation_role: { label: "bench", recent_minutes: 15 }, expected_minutes: 15 },
    { ...game.forecast.predictions[0], player_id: "103", name: "Zach Starter", rotation_role: { label: "starter", recent_minutes: 35 }, expected_minutes: 35 },
    game.forecast.predictions[1],
  ];
  await page.route("**/api/dashboard", (route) => route.fulfill({ json: staged }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Los Angeles Lakers", exact: true })).toBeVisible();
  const lakers = page.locator('tbody[data-team-id="1610612747"]');
  await expect(lakers.locator(".role-heading")).toHaveText(["Starters", "Bench"]);
  await expect(lakers.locator(".player-button").first()).toContainText("Zach Starter");
  await expect(page.getByText('Calendar refresh unavailable.', { exact: false })).toBeVisible();
});

test("preliminary forecast remains selectable alongside the final checkpoint", async ({ page }) => {
  const staged = structuredClone(dashboard);
  const preliminary = staged.games[0].forecast;
  preliminary.injury_check = { latest_report: { source_url: 'https://example.test/preliminary.pdf', published_at: '2026-10-22T00:30:00Z' } };
  const final = { ...structuredClone(preliminary), stage: "final", predictions: preliminary.predictions.map((p) => ({ ...p, points_mean: 30 })) };
  final.injury_check = { anchor_report: { source_url: 'https://example.test/final.pdf', published_at: '2026-10-22T01:30:00Z' } };
  staged.games[0].forecasts = { preliminary, final };
  staged.games[0].forecast = final;
  staged.games[0].injury_capture = { games: [{ game_id: gameId, ...final.injury_check }] };
  await page.route("**/api/dashboard", (route) => route.fulfill({ json: staged }));
  await page.goto("/");
  await page.getByRole("button", { name: "Preliminary forecast", exact: true }).click();
  await expect(page.locator(".forecast-stage-summary")).toContainText("Preliminary");
  await expect(page.getByRole('link', { name: 'Official injury report' })).toHaveAttribute('href', 'https://example.test/preliminary.pdf');
  await expect(page.locator(".player-row .points").first()).toHaveText("20");
  await page.getByRole("button", { name: "Final forecast", exact: true }).click();
  await expect(page.locator(".player-row .points").first()).toHaveText("30");
  await expect(page.getByRole('link', { name: 'Official injury report' })).toHaveAttribute('href', 'https://example.test/final.pdf');
});
