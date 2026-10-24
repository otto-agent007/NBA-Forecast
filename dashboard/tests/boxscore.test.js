import test from "node:test";
import assert from "node:assert/strict";
import { BOX_SCORE_COLUMNS, averageBoxScore, boxScoreValue, minuteLabel, percentLabel, signedLabel } from "../src/boxscore.js";

test("minute display carries rounded seconds without inventing source precision", () => {
  assert.equal(minuteLabel(31.25, "seconds"), "31:15");
  assert.equal(minuteLabel(31, "whole_minutes"), "31 min");
  assert.equal(minuteLabel(31), "31 min");
  assert.equal(minuteLabel(31.999, null, true), "32:00");
  assert.equal(minuteLabel(0, null, true), "00:00");
  assert.equal(minuteLabel(null, "seconds"), "—");
  assert.equal(minuteLabel(-1, "seconds"), "—");
});

test("percentages distinguish a zero shooting rate from no attempts or missing evidence", () => {
  assert.equal(percentLabel(0.375, 8), "37.5%");
  assert.equal(percentLabel(0, 8), "0%");
  assert.equal(percentLabel(0, 0), "—");
  assert.equal(percentLabel(1, 0), "—");
  assert.equal(percentLabel(null, 8), "—");
  assert.equal(percentLabel(37.5, 8), "—");
  assert.equal(percentLabel(NaN, 8), "—");
});

test("plus minus preserves signs and normalizes rounded negative zero", () => {
  assert.equal(signedLabel(7), "+7");
  assert.equal(signedLabel(-7), "-7");
  assert.equal(signedLabel(2.34), "+2.3");
  assert.equal(signedLabel(-0.01), "0");
  assert.equal(signedLabel(null), "—");
});

test("full boxscore masks unsupported forecasts and DNPs without losing real zeroes", () => {
  const field = id => BOX_SCORE_COLUMNS.find(column => column.id === id);
  assert.equal(boxScoreValue({ status: "needs_review", expected_stl: 0.8 }, field("stl")), "—");
  assert.equal(boxScoreValue({ status: "projected", expected_stl: 0 }, field("stl")), "0");
  assert.equal(boxScoreValue({ status: "projected", expected_fgm: null, expected_fga: 8 }, field("fg")), "—/8");
  assert.equal(boxScoreValue({ status: "projected" }, field("fg")), "—");
  assert.equal(boxScoreValue({ status: "dnp", pts: 20 }, field("pts"), "actual"), "—");
  assert.equal(boxScoreValue({ status: "played", fg3_pct: 0, fg3a: 0 }, field("fg3_pct"), "actual"), "—");
});

test("aggregate percentage points are converted once and missing stats remain unknown", () => {
  const row = averageBoxScore({ averages: { pts: 20, fga: 8 }, percentages: { fg_pct: 37.5, fg3_pct: null }, minute_precision: "whole_minutes" });
  assert.equal(row.fg_pct, 0.375);
  assert.equal(row.fg3_pct, null);
  assert.equal(row.ft_pct, null);
  assert.equal(row.oreb, undefined);
  assert.equal(row.minutes_precision, "whole_minutes");
});

test("boxscore durations use canonical backend precision metadata", () => {
  const minutes = BOX_SCORE_COLUMNS.find(column => column.id === "minutes");
  assert.equal(boxScoreValue({ status: "played", minutes: 31.25, minutes_precision: "seconds" }, minutes, "actual"), "31:15");
  assert.equal(boxScoreValue({ status: "played", minutes: 31, minutes_precision: "whole_minutes" }, minutes, "actual"), "31 min");
  const average = averageBoxScore({ averages: { minutes: 31.25 }, minutes_precision: "seconds" });
  assert.equal(boxScoreValue(average, minutes, "average"), "31:15");
});
