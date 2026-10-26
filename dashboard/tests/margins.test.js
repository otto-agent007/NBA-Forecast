import test from "node:test";
import assert from "node:assert/strict";
import { marginProbability, marginPercent } from "../src/margins.js";

const distribution = {
  status: "estimated", home_team_id: "H", away_team_id: "A",
  settlement: "final_score_including_overtime", calibration_status: "uncalibrated",
  pmf: [{ margin: -4, probability: 0.2 }, { margin: 3, probability: 0.3 }, { margin: 8, probability: 0.5 }],
};

test("small nonzero tails are not displayed as certainty", () => {
  assert.equal(marginPercent(0.000001), "<0.1%");
  assert.equal(marginPercent(0.999999), ">99.9%");
  assert.equal(marginPercent(0.5), "50.0%");
});

test("strict YES and complementary NO include equality in NO", () => {
  assert.deepEqual(marginProbability(distribution, "H", "3"), { yes: 0.5, no: 0.5, equal: 0.3 });
  assert.deepEqual(marginProbability(distribution, "H", "2.5"), { yes: 0.8, no: 0.2, equal: 0 });
});

test("away threshold uses the reversed final margin", () => {
  const value = marginProbability(distribution, "A", "3.5");
  assert.equal(value.yes, 0.2);
  assert.equal(value.no, 0.8);
  assert.equal(marginProbability(distribution, "H", "7.5").yes, 0.5);
});

test("unsupported inputs cannot become percentages", () => {
  for (const line of ["", " ", "NaN", "Infinity", "51", "-51", null]) {
    assert.equal(marginProbability(distribution, "H", line), null);
  }
  assert.equal(marginProbability(distribution, "unknown", "3.5"), null);
  assert.equal(marginProbability({ ...distribution, status: "unavailable" }, "H", "3.5"), null);
  assert.equal(marginProbability({ ...distribution, settlement: "regulation" }, "H", "3.5"), null);
  assert.equal(marginProbability({ ...distribution, pmf: [{ margin: 2, probability: 0.4 }] }, "H", "3.5"), null);
  assert.equal(marginProbability({ ...distribution, pmf: [{ margin: 0, probability: 1 }] }, "H", "3.5"), null);
});
