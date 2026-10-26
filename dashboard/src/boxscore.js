import { number } from "./data.js";

export const BOX_SCORE_COLUMNS = [
  { id: "minutes", label: "MIN:SS", fields: ["minutes"], forecast: ["expected_minutes"], kind: "minutes", description: "Playing time" },
  { id: "pts", label: "PTS", fields: ["pts"], forecast: ["points_mean"], description: "Points" },
  { id: "reb", label: "REB", fields: ["reb"], forecast: ["expected_reb"], description: "Total rebounds" },
  { id: "ast", label: "AST", fields: ["ast"], forecast: ["expected_ast"], description: "Assists" },
  { id: "stl", label: "STL", fields: ["stl"], forecast: ["expected_stl"], description: "Steals" },
  { id: "blk", label: "BLK", fields: ["blk"], forecast: ["expected_blk"], description: "Blocks" },
  { id: "fg", label: "FGM/FGA", fields: ["fgm", "fga"], forecast: ["expected_fgm", "expected_fga"], kind: "pair", description: "Field goals made / attempted" },
  { id: "fg_pct", label: "FG%", fields: ["fg_pct"], forecast: ["expected_fg_pct"], attempts: "fga", kind: "percent", description: "Field goal percentage" },
  { id: "fg3", label: "3PM/3PA", fields: ["fg3m", "fg3a"], forecast: ["expected_fg3m", "expected_fg3a"], kind: "pair", description: "Three-pointers made / attempted" },
  { id: "fg3_pct", label: "3P%", fields: ["fg3_pct"], forecast: ["expected_fg3_pct"], attempts: "fg3a", kind: "percent", description: "Three-point percentage" },
  { id: "ft", label: "FTM/FTA", fields: ["ftm", "fta"], forecast: ["expected_ftm", "expected_fta"], kind: "pair", description: "Free throws made / attempted" },
  { id: "ft_pct", label: "FT%", fields: ["ft_pct"], forecast: ["expected_ft_pct"], attempts: "fta", kind: "percent", description: "Free throw percentage" },
  { id: "oreb", label: "OREB", fields: ["oreb"], forecast: ["expected_oreb"], description: "Offensive rebounds" },
  { id: "dreb", label: "DREB", fields: ["dreb"], forecast: ["expected_dreb"], description: "Defensive rebounds" },
  { id: "tov", label: "TOV", fields: ["tov"], forecast: ["expected_tov"], description: "Turnovers" },
  { id: "pf", label: "PF", fields: ["pf"], forecast: ["expected_pf"], description: "Personal fouls" },
  { id: "plus_minus", label: "+/-", fields: ["plus_minus"], forecast: ["expected_plus_minus"], kind: "signed", description: "Plus/minus" },
];

const finite = value => typeof value === "number" && Number.isFinite(value);

export function minuteLabel(value, precision, estimated = false) {
  if (!finite(value) || value < 0) return "—";
  if (!estimated && precision !== "seconds") return `${number(value)} min`;
  const seconds = Math.round(value * 60);
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

export function percentLabel(value, attempts) {
  if (!finite(value) || value < 0 || value > 1 || attempts === 0) return "—";
  return `${number(value * 100)}%`;
}

export function signedLabel(value) {
  if (!finite(value)) return "—";
  const rounded = Number(value.toFixed(1));
  return `${rounded > 0 ? "+" : ""}${number(rounded)}`;
}

export function boxScoreValue(row, column, mode = "forecast") {
  if (!row || (mode === "forecast" && !["projected", "out"].includes(row.status)) ||
      (mode === "actual" && row.status === "dnp")) return "—";
  const projected = mode === "forecast";
  const values = (projected ? column.forecast : column.fields).map(field => row[field]);
  if (column.kind === "minutes") return minuteLabel(values[0], row.minutes_precision ?? row.minute_precision, projected);
  if (column.kind === "pair") {
    return values.every(value => !finite(value)) ? "—" : values.map(number).join("/");
  }
  if (column.kind === "percent") return percentLabel(values[0], row[projected ? `expected_${column.attempts}` : column.attempts]);
  if (column.kind === "signed") return signedLabel(values[0]);
  return number(values[0]);
}

export function boxScoreTitle(row, column, mode = "forecast") {
  if (column.kind !== "minutes") return column.description;
  if (mode === "forecast") return "Estimated playing time; displayed seconds are an estimate.";
  const precision = row?.minutes_precision ?? row?.minute_precision;
  if (mode === "average" && precision === "seconds") return "Average playing time from second-precision boxscores.";
  return precision === "seconds"
    ? "Playing time recorded to the second."
    : "Saved source precision: exact seconds unavailable.";
}

export function averageBoxScore(summary) {
  const row = { ...summary?.averages, minutes_precision: summary?.minutes_precision ?? summary?.minute_precision };
  // Existing aggregate percentages are percentage points; row percentages are ratios.
  for (const field of ["fg_pct", "fg3_pct", "ft_pct"]) {
    const value = summary?.percentages?.[field];
    row[field] = finite(value) ? value / 100 : null;
  }
  return row;
}
