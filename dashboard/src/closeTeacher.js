export const fitStatus = (fit) => ({
  insufficient_games: "Too few games (minimum 20)",
  insufficient_dates: "Too few distinct dates",
  singular_design: "Fixed design cannot be estimated in this sample",
  unavailable: "Unavailable",
}[fit?.status] || "Unavailable");

export function coefficient(fit, feature) {
  if (fit?.status !== "estimated") return null;
  return fit.coefficients?.find(row => row.feature === feature) ?? null;
}

export function estimate(row) {
  if (!Number.isFinite(row?.estimate) || !Number.isFinite(row?.se)) return "—";
  return `${row.estimate >= 0 ? "+" : ""}${row.estimate.toFixed(3)} ± ${row.se.toFixed(3)}`;
}

export const percent = value => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
