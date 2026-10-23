import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
const path = new URL("../src/clutchData.js", import.meta.url);
test("clutch selection filters by metric eligibility, preserves missing and orders lower turnovers first", async () => {
  assert.ok(existsSync(path), "Clutch comparison helpers must exist");
  const { selectRows } = await import(path);
  const rows = [
    {
      player_id: 1,
      name: "Alpha",
      qualified: true,
      efficiency_qualified: true,
      adjusted: { efg: 0.6, tov_min: 0.1 },
      raw: { efg: 0.7, tov_min: 0.1 },
      ratings: { efg: 80, tov_min: 70 },
    },
    {
      player_id: 2,
      name: "Beta",
      qualified: true,
      efficiency_qualified: false,
      adjusted: { efg: 0.8, tov_min: 0.2 },
      raw: { efg: 0.9, tov_min: 0.2 },
      ratings: { efg: null, tov_min: 30 },
    },
    {
      player_id: 3,
      name: "Gamma",
      qualified: false,
      efficiency_qualified: false,
      adjusted: { efg: null, tov_min: null },
      raw: { efg: null, tov_min: null },
      ratings: { efg: null, tov_min: null },
    },
  ];
  assert.deepEqual(
    selectRows(rows, { metric: "efg", qualified: true }).map(
      (r) => r.player_id,
    ),
    [1],
  );
  assert.deepEqual(
    selectRows(rows, { metric: "tov_min", qualified: false }).map(
      (r) => r.player_id,
    ),
    [1, 2, 3],
  );
  assert.deepEqual(
    selectRows(rows, { metric: "efg", query: "BETA", qualified: false }).map(
      (r) => r.player_id,
    ),
    [2],
  );
  assert.deepEqual(
    rows.map((r) => r.player_id),
    [1, 2, 3],
  );
});
test("percentages and ranges retain missing values and interval overlap is not a significance test", async () => {
  assert.ok(existsSync(path), "Clutch comparison helpers must exist");
  const { fmt, intervalMessage } = await import(path);
  assert.equal(fmt(null), "—");
  assert.equal(fmt(0), "0.00");
  assert.equal(fmt(0.52, "efg"), "52.0%");
  assert.match(intervalMessage([0.4, 0.6], [0.5, 0.7]), /overlap/);
  assert.match(intervalMessage(null, [0.5, 0.7]), /unavailable/);
});
