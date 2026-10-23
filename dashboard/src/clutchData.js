export const METRICS = {
  pts_min: "Scoring · PTS/min",
  fga_min: "Opportunity · FGA/min",
  fta_min: "Free throws · FTA/min",
  efg: "Shooting · eFG%",
  ast_min: "Playmaking · AST/min",
  tov_min: "Turnovers · TOV/min ↓",
  two: "2P%",
  three: "3P%",
  ft: "FT%",
};
export const DEFAULT_PLAYERS = [1628973, 1630169, 201142, 203081, 1628983];
export function fmt(value, metric = "") {
  if (!Number.isFinite(value)) return "—";
  return ["efg", "two", "three", "ft", "fg", "ts", "three_share"].includes(
    metric,
  )
    ? `${(100 * value).toFixed(1)}%`
    : value.toFixed(2);
}
export function metricValue(row, metric, adjusted = true) {
  if (["two", "three", "ft"].includes(metric) && !adjusted)
    return row.shooting?.[metric]?.raw;
  return row[adjusted ? "adjusted" : "raw"]?.[metric];
}
export function selectRows(
  rows,
  { metric = "pts_min", query = "", qualified = true, adjusted = true } = {},
) {
  return rows
    .filter(
      (r) =>
        (!qualified || r.ratings[metric] != null) &&
        r.name.toLowerCase().includes(query.trim().toLowerCase()),
    )
    .sort((a, b) => {
      const x = metricValue(a, metric, adjusted),
        y = metricValue(b, metric, adjusted);
      if (x == null) return y == null ? a.player_id - b.player_id : 1;
      if (y == null) return -1;
      return (
        (metric === "tov_min" ? x - y : y - x) || a.player_id - b.player_id
      );
    });
}
export function intervalMessage(a, b) {
  if (!a || !b) return "Comparison unavailable: shooting evidence is missing.";
  return a[0] <= b[1] && b[0] <= a[1]
    ? "Model intervals overlap: the display does not clearly separate these estimates."
    : "Model intervals do not overlap under the stated assumptions. This is not a calibrated significance test.";
}
