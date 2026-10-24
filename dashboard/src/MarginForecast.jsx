import React, { useId, useState } from "react";
import { marginProbability, marginUnavailableReason, marginPercent } from "./margins.js";

export default function MarginForecast({ game }) {
  const [team, setTeam] = useState(game.home_team_id);
  const [line, setLine] = useState("2.5");
  const id = useId();
  const distribution = game.forecast?.margin_forecast;
  const matches = distribution?.game_id === game.game_id
    && distribution.home_team_id === game.home_team_id && distribution.away_team_id === game.away_team_id;
  const ready = matches && distribution.status === "estimated";
  const probability = ready ? marginProbability(distribution, team, line) : null;
  const teamName = team === game.home_team_id ? game.home_name : game.away_name;
  return <section className="margin-forecast" aria-label="Winning margin forecast">
    <div className="margin-heading"><h2>Winning margin</h2><span className="total-state">{ready ? "Estimate · calibration pending" : "Unavailable"}</span></div>
    {!ready ? <p>{matches ? marginUnavailableReason(distribution.reason)
      : "Margin probabilities were not saved for this forecast."}</p> : <>
      <p>Enter the offered line to explore the selected forecast. Final score includes overtime.</p>
      <div className="margin-controls">
        <label htmlFor={`${id}-team`}>Team<select id={`${id}-team`} value={team} onChange={event => setTeam(event.target.value)}>
          <option value={game.away_team_id}>{game.away_name}</option><option value={game.home_team_id}>{game.home_name}</option>
        </select></label>
        <label htmlFor={`${id}-line`}>Offered margin line<input id={`${id}-line`} type="number" min="-50" max="50" step="0.5" value={line}
          onChange={event => setLine(event.target.value)} inputMode="decimal" /></label>
      </div>
      {probability ? <div className="margin-result" aria-live="polite">
        <p>{teamName} finishes with a score margin <strong>greater than {Number(line)}</strong> points:</p>
        <div className="margin-probabilities"><div><span>YES</span><strong data-testid="margin-yes">{marginPercent(probability.yes)}</strong></div>
          <div><span>NO</span><strong data-testid="margin-no">{marginPercent(probability.no)}</strong></div></div>
        <div className="margin-bar" aria-hidden="true"><span style={{ width: `${probability.yes * 100}%` }} /></div>
        <p className="tiny">NO includes a margin equal to the line. A negative margin means the selected team loses.</p>
      </div> : <p role="status">Enter a number from −50 to 50. A valid saved distribution is required.</p>}
      <details><summary>Forecast evidence</summary>
        <p>{distribution.sample_count ?? 0} historical games inform the uncertainty estimate. These probabilities have not yet been calibrated against this model’s future results.</p>
        <p className="tiny">{game.forecast.stage === "final" ? "Final" : "Preliminary"} forecast · The entered line is a comparison, not a captured market quote.</p>
      </details>
    </>}
  </section>;
}
