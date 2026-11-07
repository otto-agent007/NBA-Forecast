import { test, expect } from '@playwright/test';

// Synthetic diagnostics exercise rendering and unavailable states, not accuracy claims.
const fit = feature => ({status: 'estimated', n: 40, clusters: 20,
  coefficients: [{feature, estimate: -.25, se: .1}]});
const overall = {n: 40, clusters: 20, model_mae: 12, close_mae: 11, mae_gap: 1,
  mean_abs_disagreement: 3, cover_n: 38, cover_rate: .5, slope: fit('model_minus_close')};
const diagnostic = {
  overall, excluded_rows: 0, date_range: ['2025-01-01', '2025-04-15'],
  buckets: [{...overall, lower: 0, upper: 2, label: '0–<2'},
    {...overall, n: 3, clusters: 2, lower: 6, upper: 99, label: '6–<99', slope: {status: 'insufficient_games'}}],
  teacher: {feature_names: ['home back-to-back'], fits: {
    close_minus_model: fit('home back-to-back'), actual_minus_model: fit('home back-to-back'),
    actual_minus_close: {status: 'singular_design', coefficients: []}}},
  persistent: {team_seasons: 30, team_season_mean_sd: 1.4, n: 25, standing_variance_share: .37,
    cover_n: 10, cover_rate: .5, fit: {...fit('standing_view'), coefficients: [
      {feature: 'standing_view', estimate: .07, se: .12}, {feature: 'game_day', estimate: .03, se: .1}]}}
};
const payload = {schema_version: 1, season: '2026-27', source_mode: 'saved_operations', games: [], players: [],
  close_teacher: {reference: 'synthetic-reference', diagnostics: diagnostic},
  market: {summary: {close_teacher: {'1': {provider: 'Synthetic book', diagnostics: {...diagnostic, teacher: {status: 'unavailable'}}}}}}
};

test('historical and provider-specific live diagnostics show uncertainty and expandable team split', async ({page}) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/dashboard', route => route.fulfill({json: payload}));
  await page.goto('/');
  await page.getByRole('button', {name: 'Performance', exact: true}).click();
  const section = page.getByRole('region', {name: 'Close-teacher diagnostics'});
  await expect(section.getByRole('heading', {name: 'Historical replay'})).toBeVisible();
  await expect(section.getByRole('table', {name: 'Historical residual coefficients'})).toContainText('-0.250 ± 0.100');
  await expect(section).toContainText('Fixed design cannot be estimated');
  await expect(section).toContainText('Too few games');
  await section.getByText('Persistent team view versus game-day difference', {exact: true}).first().click();
  await expect(section.getByRole('table', {name: 'Persistent and game-day differences'}).first()).toBeVisible();
  await expect(section).toContainText('+0.070 ± 0.120');
  await section.getByText('Synthetic book · 40 matched games', {exact: true}).click();
  await expect(section).toContainText('Providers are evaluated separately');
  await expect(section.getByRole('table', {name: 'Disagreement size diagnostics'})).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test('older exports show missing diagnostics honestly', async ({page}) => {
  await page.route('**/api/dashboard', route => route.fulfill({json: {...payload, close_teacher: null, market: null}}));
  await page.goto('/');
  await page.getByRole('button', {name: 'Performance', exact: true}).click();
  await expect(page.getByText('Historical diagnostics are not available in this export.')).toBeVisible();
  await expect(page.getByText('Awaiting a market report with close-teacher diagnostics.')).toBeVisible();
});
