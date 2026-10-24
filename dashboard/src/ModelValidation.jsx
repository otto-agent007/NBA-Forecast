import React from "react";
import { number } from "./data";

export default function ModelValidation({ model }) {
  const report=model?.validation, held=report?.holdout;
  if (!held) return model ? <section className="performance-group" aria-label="Model validation status">
    <h3>Model validation · {model.model_version}</h3>
    <p>Collecting results for this model version. Earlier models’ accuracy and calibration do not establish this version’s performance.</p>
    <p>{model.prospective?.game_count ?? 0} matched games · {model.prospective?.sample_count ?? 0} player outcomes.</p>
  </section> : null;
  const prospective=model.prospective;
  const conflicts=model.outcomes?.pending_details?.filter(row=>row.evidence)?.length ?? 0;
  return <section className="performance-group" aria-label="Historical model validation">
    <h3>Historical model validation · {model.model_version}</h3>
    <p>Historical reconstruction. These results are separate from forecasts saved before tipoff.</p>
    <div className="table-scroll"><table><thead><tr><th>Mean absolute error</th><th>Baseline</th><th>Candidate</th></tr></thead>
      <tbody><tr><td>Player points</td><td>{number(held.baseline.mae)}</td><td>{number(held.candidate.mae)}</td></tr>
      <tr><td>Team points</td><td>{number(held.team_baseline.mae)}</td><td>{number(held.team_candidate.mae)}</td></tr></tbody></table></div>
    <p>Nominal 80% intervals: <strong>{number(held.intervals.coverage*100)}%</strong> observed coverage across {held.intervals.sample_count.toLocaleString()} evaluated player outcomes.</p>
    <p className="tiny">{report.scope} Historical source data was collected after those games. Future coverage is assessed from the original saved intervals.</p>
    <h4>Prospective calibration</h4>
    {prospective ? <p>{prospective.game_count ?? "—"} / {prospective.minimum_games ?? "—"} games · {prospective.sample_count ?? "—"} / {prospective.minimum_samples ?? "—"} outcomes. {prospective.status === "ready" ? "New residual evidence is available." : "Collecting matched future results."}</p>
      : <p>Awaiting matched future results.</p>}
    {conflicts > 0 && <p>{conflicts} final {conflicts === 1 ? "boxscore is" : "boxscores are"} awaiting matching team and player statistics before inclusion.</p>}
  </section>;
}
