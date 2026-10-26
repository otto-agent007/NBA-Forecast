import { test, expect } from "@playwright/test";

const home = "1610612747", away = "1610612744", gameId = "0022600001";
const distribution = {
  game_id: gameId, home_team_id: home, away_team_id: away, status: "estimated",
  settlement: "final_score_including_overtime", calibration_status: "uncalibrated", sample_count: 200,
  pmf: [{ margin: -4, probability: 0.2 }, { margin: 3, probability: 0.3 }, { margin: 8, probability: 0.5 }],
};

function fixture(margin = distribution) {
  const predictions = [home, away].map((team_id, i) => ({
    player_id: `${i + 1}`, name: `Example Player ${i + 1}`, team_id,
    status: "projected", expected_minutes: 34, points_mean: 24,
    rotation_role: { label: "starter", observations: 2 },
  }));
  return {
    schema_version: 1, source_mode: "saved_operations", season: "2026-27",
    generated_at: "2026-10-24T07:00:00Z", players: predictions,
    games: [{ game_id: gameId, start_time: "2026-10-24T02:00:00Z", status: "final",
      home_team_id: home, away_team_id: away, home_tricode: "LAL", away_tricode: "GSW",
      home_name: "Los Angeles Lakers", away_name: "Golden State Warriors",
      forecast: { stage: "preliminary", predictions, margin_forecast: margin,
        model_version: "synthetic-margin-test", run_id: "synthetic", as_of: "2026-10-24T01:00:00Z" },
    }],
  };
}

async function open(page, margin) {
  await page.route("**/api/dashboard", route => route.fulfill({ json: fixture(margin) }));
  await page.route("https://cdn.nba.com/**", route => route.fulfill({ status: 204 }));
  await page.goto("/");
  await expect(page).toHaveTitle("NBA Forecast · Game day");
  return page.getByRole("region", { name: "Winning margin forecast" });
}

test("line explorer handles equality, away side, invalid input and mobile layout", async ({ page }, testInfo) => {
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const panel = await open(page, distribution);
  await expect(panel).toBeVisible();
  await expect(panel.getByTestId("margin-yes")).toHaveText("80.0%");
  await panel.getByLabel("Offered margin line").fill("3");
  await expect(panel.getByTestId("margin-yes")).toHaveText("50.0%");
  await expect(panel.getByTestId("margin-no")).toHaveText("50.0%");
  await panel.getByRole("combobox", { name: "Team", exact: true }).selectOption(away);
  await panel.getByLabel("Offered margin line").fill("3.5");
  await expect(panel.getByTestId("margin-yes")).toHaveText("20.0%");
  await expect(panel.getByTestId("margin-no")).toHaveText("80.0%");
  await panel.getByLabel("Offered margin line").fill("");
  await expect(panel.getByTestId("margin-yes")).toHaveCount(0);
  await expect(panel.getByRole("status")).toContainText("Enter a number");
  await panel.getByLabel("Offered margin line").fill("7.5");
  await panel.getByText("Forecast evidence", { exact: true }).click();
  await expect(panel).toContainText("not yet been calibrated");
  const box = await panel.boundingBox();
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width + 1);
  await panel.screenshot({ path: testInfo.outputPath("margin-controls.png") });
  expect(errors).toEqual([]);
});

test("partial forecasts and legacy snapshots never invent probabilities", async ({ page }) => {
  const panel = await open(page, { ...distribution, status: "unavailable", pmf: [], reason: "incomplete_team_totals" });
  await expect(panel).toContainText("Both teams need complete projections");
  await expect(panel.getByLabel("Offered margin line")).toHaveCount(0);
  await page.unroute("**/api/dashboard");
  await page.route("**/api/dashboard", route => route.fulfill({ json: fixture(null) }));
  await page.reload();
  await expect(page.getByRole("region", { name: "Winning margin forecast" })).toContainText("not saved for this forecast");
});

test("validated correction is named with its separate training and validation samples", async ({ page }) => {
  const calibrated={...distribution,calibration_status:"prospective_validated",
    calibration_audit:{training_games:64,validation_games:24}};
  const panel=await open(page,calibrated);
  await expect(panel).toContainText("validated correction");
  await panel.getByText("Forecast evidence",{exact:true}).click();
  await expect(panel).toContainText("64 saved pregame games fitted the correction");
  await expect(panel).toContainText("24 later games passed the validation gate");
});

test("schedule cross-check and active correction remain visible after a later failed attempt", async ({ page }) => {
  const data=fixture();
  data.schedule_observed_at="2026-10-21T12:00:00Z";
  data.schedule_crosscheck={status:"verified",observed_at:"2026-10-24T16:00:00Z"};
  data.runner={schedule_source:"espn_verified_cached_nba"};
  data.model_upgrade={model_version:"regular-season-baseline-0.6.0",
    applied_margin_calibration:{status:"ready",training_games:72,validation_games:40},
    prospective_margin:{status:"not_ready",sample_count:112,minimum_training_games:64,minimum_validation_games:32}};
  await page.route("**/api/dashboard", route => route.fulfill({ json: data }));
  await page.route("https://cdn.nba.com/**", route => route.fulfill({ status: 204 }));
  await page.goto("/");
  await expect(page.getByText("ESPN cross-checked")).toBeVisible();
  await expect(page.getByText("NBA calendar captured")).toBeVisible();
  await expect(page.getByText("ESPN cross-check",{exact:true})).toBeVisible();
  data.schedule_crosscheck.status="partial";
  await page.reload();
  await expect(page.getByText("Partial cross-check")).toHaveClass(/warn/);
  delete data.schedule_crosscheck;
  await page.reload();
  await expect(page.getByText("Cross-check unavailable",{exact:true})).toHaveClass(/warn/);
  data.runner.schedule_source="unverified_cached_nba";
  await page.reload();
  await expect(page.getByText("Unverified",{exact:true})).toHaveClass(/warn/);
  await page.getByRole("button",{name:"Performance"}).click();
  await expect(page.getByText("Active margin correction:",{exact:false})).toContainText("72 training and 40 later validation games");
  await expect(page.getByText("Latest margin assessment:",{exact:false})).toContainText("112 complete saved games");
});
