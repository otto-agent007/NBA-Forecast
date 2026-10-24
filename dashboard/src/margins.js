export function marginPercent(value) {
  if (value > 0 && value < 0.001) return "<0.1%";
  if (value < 1 && value > 0.999) return ">99.9%";
  return `${(value * 100).toFixed(1)}%`;
}

export function marginUnavailableReason(reason) {
  if (reason === "incomplete_team_totals") return "Both teams need complete projections. Missing minutes or unresolved player availability are keeping this estimate unavailable.";
  if (["missing_margin_inputs", "insufficient_historical_games", "insufficient_outcome_dispersion"].includes(reason)) return "There is not enough verified historical evidence to estimate the game's uncertainty.";
  if (reason === "not_pregame") return "A margin forecast must be saved before tipoff.";
  if (reason === "margin_location_or_scale_outside_support") return "This game's estimated margin or uncertainty is outside the model's supported range.";
  return "The saved forecast does not have verified inputs for a margin estimate.";
}

// Read the immutable final-score distribution; entering a line never refits it.
export function marginProbability(distribution, teamId, rawLine) {
  if (distribution?.status !== "estimated"
      || distribution.settlement !== "final_score_including_overtime"
      || rawLine === null || rawLine === undefined || String(rawLine).trim() === "") return null;
  const line = Number(rawLine);
  if (!Number.isFinite(line) || Math.abs(line) > 50) return null;
  const range = distribution.supported_line_range;
  if (range && (line <= range.minimum || line >= range.maximum)) return null;
  const { home_team_id: home, away_team_id: away, pmf } = distribution;
  if (!home || !away || home === away || ![home, away].includes(teamId) || !Array.isArray(pmf) || !pmf.length) return null;
  let yes = 0, no = 0, equal = 0, total = 0;
  const sign = teamId === home ? 1 : -1, seen = new Set();
  for (const item of pmf) {
    const { margin, probability } = item;
    if (!Number.isInteger(margin) || margin === 0 || seen.has(margin)
        || !Number.isFinite(probability) || probability < 0 || probability > 1) return null;
    seen.add(margin);
    total += probability;
    const namedMargin = sign * margin;
    if (namedMargin > line) yes += probability;
    else no += probability;
    if (namedMargin === line) equal += probability;
  }
  if (Math.abs(total - 1) > 1e-8) return null;
  return { yes: yes / total, no: no / total, equal: equal / total };
}
