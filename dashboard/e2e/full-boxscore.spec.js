import { test, expect } from "@playwright/test";

// Synthetic evidence exercises presentation without making NBA requests.
const boxscore = {
  expected_minutes: 32.5, points_mean: 24.7, expected_reb: 7.2,
  expected_ast: 5.4, expected_stl: 0, expected_blk: 1.1,
  expected_fgm: 8.5, expected_fga: 18.1, expected_fg_pct: 0.4696,
  expected_fg3m: 2.1, expected_fg3a: 6.2, expected_fg3_pct: 0.3387,
  expected_ftm: 5.6, expected_fta: 6.4, expected_ft_pct: 0.875,
  expected_oreb: 1.2, expected_dreb: 6, expected_tov: 2.3,
  expected_pf: 2.8, expected_plus_minus: -3.4,
};
const players = ["1610612744", "1610612747"].flatMap((team, t) =>
  Array.from({ length: 6 }, (_, i) => ({
    ...boxscore, player_id: `${t + 1}${i}`, name: `${t ? "Home" : "Away"} Player ${i}`,
    team_id: team, status: i === 5 ? "needs_review" : "projected",
    ...(i === 0 ? { modeled_shot_profile: {
      player_id: `${t + 1}${i}`, team_id: team, observed_attempts: 120, observed_makes: 50,
      games: 8, located_attempts: 110, zones: [], method: "Synthetic observed shot distribution",
    } } : {}),
    expected_minutes: 32.5 - i, points_mean: 24.7 - i,
    rotation_role: { label: "starter", observations: 4, recent_minutes: 32.5 - i },
    observed_stats: { season: "2026-27", games: 4, points_average: 18.5 },
  })),
);
const dashboard = {
  schema_version: 1, source_mode: "saved_operations", season: "2026-27",
  generated_at: "2026-10-22T06:00:00Z", players,
  games: [{
    game_id: "0022600001", start_time: "2026-10-22T02:00:00Z", status: "final",
    home_team_id: "1610612747", away_team_id: "1610612744",
    home_tricode: "LAL", away_tricode: "GSW",
    home_name: "Los Angeles Lakers", away_name: "Golden State Warriors",
    forecast: { stage: "preliminary", predictions: players, model_version: "synthetic-test",
      created_at: "2026-10-22T01:00:00Z", as_of: "2026-10-22T01:00:00Z" },
  }],
};
const actual = {
  status: "played", starter: true, minutes: 31.25, minutes_precision: "seconds",
  pts: 20, reb: 7, ast: 5, stl: 0, blk: 1, fgm: 8, fga: 17, fg_pct: 8 / 17,
  fg3m: 0, fg3a: 0, fg3_pct: null, ftm: 4, fta: 5, ft_pct: 0.8,
  oreb: 1, dreb: 6, tov: 2, pf: 3, plus_minus: -7,
  opponent_id: "1610612747", source_url: "https://www.nba.com/game/synthetic/box-score",
};
const profile = {
  as_of: "2026-10-22T06:00:00Z",
  current_season: { season: "2026-27", gp: 2, dnp: 1, minutes_precision: "mixed_or_rounded",
    averages: { ...actual, minutes: 31.125 },
    percentages: { fg_pct: 47.0588, fg3_pct: null, ft_pct: 80 },
    coverage: {},
  },
  recent_games: [
    { ...actual, game_id: "seconds", game_time: "2026-10-22T02:00:00Z" },
    { ...actual, game_id: "whole", game_time: "2026-10-20T02:00:00Z", minutes: 31, minutes_precision: "whole_minutes", plus_minus: 7 },
    { ...actual, game_id: "dnp", game_time: "2026-10-18T02:00:00Z", status: "dnp", minutes: null },
  ],
  shot_chart: { shots: [], coverage: { games: 2, played_games: 2, missing_games: 0, complete: true, zero_attempt_games: 2, attempts: 0, located_attempts: 0 } },
};
const labels = ["MIN:SS", "PTS", "REB", "AST", "STL", "BLK", "FGM/FGA", "FG%",
  "3PM/3PA", "3P%", "FTM/FTA", "FT%", "OREB", "DREB", "TOV", "PF", "+/-"];

test.beforeEach(async ({ page }) => {
  await page.route("**/api/dashboard", route => route.fulfill({ json: dashboard }));
  await page.route("**/api/players/*", route => route.fulfill({ json: profile }));
  await page.route("https://cdn.nba.com/**", route => route.fulfill({
    contentType: "image/png",
    body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lxkAAAAASUVORK5CYII=", "base64"),
  }));
});

test("full box forecasts preserve team starters, signed values and unsupported cells", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto("/");
  await expect(page).toHaveTitle("NBA Forecast · Game day");
  const table = page.locator(".player-section table");
  for (const label of labels) {
    await expect(table.getByRole("columnheader", { name: label, exact: true })).toHaveCount(1);
  }
  const away = table.locator('tbody[data-team-id="1610612744"]');
  await expect(away.locator('.player-row[data-role="starter"]')).toHaveCount(5);
  await expect(table.locator('tbody[data-team-id="1610612747"] .player-row[data-role="starter"]')).toHaveCount(5);
  const row = away.locator(".player-row").first();
  await expect(row.locator('[data-stat="minutes"]')).toHaveText("32:30");
  await expect(row.locator('[data-stat="minutes"]')).toHaveAttribute("title", /Estimated/);
  await expect(row.locator('[data-stat="fg"]')).toHaveText("8.5/18.1");
  await expect(row.locator('[data-stat="fg_pct"]')).toHaveText("47%");
  await expect(row.locator('[data-stat="stl"]')).toHaveText("0");
  await expect(row.locator('[data-stat="plus_minus"]')).toHaveText("-3.4");
  const withheld = away.locator('.player-row[data-role="bench"]');
  await expect(withheld.locator('[data-stat="pts"]')).toHaveText("—");
  await expect(withheld.locator('[data-stat="plus_minus"]')).toHaveText("—");
  await expect(withheld.locator(".observed-ppg")).toHaveText("18.5");
  await page.getByRole("button", { name: "Sort by projected points" }).click();
  await expect(away.locator('.player-row[data-role="starter"]')).toHaveCount(5);
  await page.getByRole("button", { name: "Projections", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Player projections", exact: true, level: 2 })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "FTM/FTA", exact: true })).toHaveCount(1);
  await page.screenshot({ path: testInfo.outputPath("projections-first-viewport.png"), fullPage: false });
  const scroll = page.locator(".player-section .table-scroll");
  await scroll.evaluate(element => element.scrollIntoView({ block: "start" }));
  // The column slider stays on screen while the table is in view; the native bar sits under the last row.
  const slider = page.locator(".player-section").getByRole("slider", { name: /stat columns/ });
  if (await scroll.evaluate(element => element.scrollWidth > element.clientWidth)) {
    await expect(slider).toBeInViewport();
    await slider.focus();
    await page.keyboard.press("End");
    await expect.poll(() => scroll.evaluate(element => element.scrollWidth - element.clientWidth - element.scrollLeft)).toBeLessThanOrEqual(1);
  } else {
    await expect(slider).toHaveCount(0);
  }
  await expect(table.locator(".player-button").first()).toBeInViewport();
  await expect(table.getByRole("columnheader", { name: "+/-", exact: true })).toBeInViewport();
  const widths = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, viewport: innerWidth }));
  expect(widths.page).toBeLessThanOrEqual(widths.viewport);
  await page.screenshot({ path: testInfo.outputPath("full-boxscore.png"), fullPage: false });
  expect(errors).toEqual([]);
});

test("player forecast and game log show full stats with honest minute precision", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Away Player 0", exact: false }).click();
  const dialog = page.getByRole("dialog");
  const forecast = dialog.getByRole("table", { name: "Next-game projected boxscore" });
  await expect(forecast.getByRole("columnheader", { name: "OREB", exact: true })).toBeVisible();
  await expect(forecast.locator('[data-stat="plus_minus"]')).toHaveText("-3.4");
  const shotEvidence = dialog.getByLabel("Shot distribution evidence");
  await expect(shotEvidence).toContainText("120 captured attempts");
  await expect(shotEvidence).toContainText("8 games");
  await expect(shotEvidence).toContainText("110 have verified locations");
  const averages = dialog.getByRole("table", { name: "Regular-season averages" });
  await expect(averages.locator('[data-stat="fg_pct"]')).toHaveText("47.1%");
  await dialog.locator(".profile-forecast").scrollIntoViewIfNeeded();
  await expect(forecast.locator('[data-stat="minutes"]')).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath("player-forecast-evidence.png"), fullPage: false });
  await dialog.getByRole("tab", { name: "Game log", exact: true }).click();
  const rows = dialog.getByRole("table", { name: "Recent game boxscores" }).locator("tbody tr");
  await expect(rows.nth(0).locator('[data-stat="minutes"]')).toHaveText("31:15");
  await expect(rows.nth(1).locator('[data-stat="minutes"]')).toHaveText("31 min");
  await expect(rows.nth(1).locator('[data-stat="minutes"]')).toHaveAttribute("title", /seconds unavailable/i);
  await expect(rows.nth(0).locator('[data-stat="fg3_pct"]')).toHaveText("—");
  await expect(rows.nth(0).locator('[data-stat="stl"]')).toHaveText("0");
  await expect(rows.nth(0).locator('[data-stat="plus_minus"]')).toHaveText("-7");
  await expect(rows.nth(1).locator('[data-stat="plus_minus"]')).toHaveText("+7");
  await expect(rows.nth(2).locator('[data-stat="pts"]')).toHaveText("—");
  await expect(rows.nth(2)).toContainText("DNP");
  await page.screenshot({ path: testInfo.outputPath("player-game-log.png"), fullPage: false });
  await dialog.getByRole("button", { name: "Close player details" }).click();
  await expect(dialog).toHaveCount(0);
});

test("shot chart distinguishes verified zero attempts from missing played games", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Away Player 0", exact: false }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("tab", { name: "Shot chart", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "No field-goal attempts", exact: true })).toBeVisible();
  await expect(dialog).toContainText("2/2 played games captured");
  await expect(dialog.getByRole("heading", { name: "Shot locations unavailable", exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Close player details" }).click();
  const partial = structuredClone(profile);
  partial.shot_chart = { shots: [
    { game_id: "one", action_number: 1, player_id: "10", x: 0, y: 0, made: true, points: 2, period: 1 },
    { game_id: "one", action_number: 2, player_id: "10", x: 220, y: 0, made: false, points: 3, period: 1 },
  ], coverage: { attempts: 2, located_attempts: 2, games: 1, played_games: 2, missing_games: 1, complete: false, zero_attempt_games: 0 } };
  await page.route("**/api/players/*", route => route.fulfill({ json: partial }));
  await page.getByRole("button", { name: "Away Player 0", exact: false }).click();
  await dialog.getByRole("tab", { name: "Shot chart", exact: true }).click();
  await expect(dialog).toContainText("1/2 played games captured");
  await expect(dialog.getByLabel("Shot coverage")).toContainText("1 played game");
  await expect(dialog.getByRole("img", { name: "2 observed shot locations", exact: false })).toBeVisible();
  await dialog.getByLabel("Shot result").selectOption("made");
  await expect(dialog.getByRole("img", { name: "1 observed shot locations", exact: false })).toBeVisible();
});
