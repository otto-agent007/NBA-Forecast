import React from "react";
import { number } from "./data";
import { coefficient, estimate, fitStatus, percent } from "./closeTeacher";

const targets = [["close_minus_model", "Close − model"], ["actual_minus_model", "Actual − model"], ["actual_minus_close", "Actual − close"]];
function Slope({ fit, feature = "model_minus_close" }) {
  const row = coefficient(fit, feature);
  return row ? estimate(row) : <span className="tiny">{fitStatus(fit)}</span>;
}

function DiagnosticTables({ diagnostic: d }) {
  if (!d?.overall) return <p>Diagnostics have not been generated for this sample.</p>;
  const o = d.overall, p = d.persistent, teacher = d.teacher;
  return <>
    <p>{o.n.toLocaleString()} matched games · {o.clusters} source dates{d.date_range ? ` · ${d.date_range.join(" to ")}` : ""}.
      {d.excluded_rows > 0 ? ` ${d.excluded_rows} incomplete rows excluded.` : ""}
      {d.unmatched_forecast_games > 0 ? ` ${d.unmatched_forecast_games} forecast games lack the required source match.` : ""}</p>
    {d.season_group_mismatches?.length > 0 ? <p className="tiny">{d.season_group_mismatches.length} games have source-season labels that differ from their study group.
      The original study grouping is retained for reproducibility; these are not clean season-only samples.</p> : null}
    <div className="metric-band">
      {[["Model margin MAE", o.model_mae], ["Close margin MAE", o.close_mae],
        ["MAE gap", o.mae_gap], ["Mean |model − close|", o.mean_abs_disagreement]].map(([label, value]) =>
        <div key={label}><span>{label}</span><strong>{number(value)}</strong></div>)}
    </div>
    <p>Disagreement slope: <strong><Slope fit={o.slope} /></strong>. Actual − close regressed on model − close, with an intercept.</p>
    <p className="tiny">All ± values below are one date-clustered standard error using the November 6 study method.
      A slope of zero means no demonstrated information beyond the close; one would mean the model explains the close’s miss. These estimates do not establish a betting edge.</p>
    <details className="teacher-details" open>
      <summary>Disagreement size</summary>
      <div className="table-scroll"><table aria-label="Disagreement size diagnostics">
        <thead><tr><th>|Model − close|</th><th>Games</th><th>Dates</th><th>Model MAE</th><th>Close MAE</th><th>Slope ± SE</th><th>Side covers</th></tr></thead>
        <tbody>{d.buckets.map(b => <tr key={b.lower}><td>{b.label} pts</td><td>{b.n}</td><td>{b.clusters}</td>
          <td>{number(b.model_mae)}</td><td>{number(b.close_mae)}</td><td><Slope fit={b.slope} /></td>
          <td>{percent(b.cover_rate)} ({b.cover_n})</td></tr>)}</tbody>
      </table></div>
      <p className="tiny">Cover-rate denominator excludes pushes and zero disagreements. The study’s final bucket is 6 to &lt;99 points.
        {d.outside_buckets > 0 ? ` ${d.outside_buckets} games lie outside these buckets.` : ""}</p>
    </details>
    {teacher?.fits ? <details className="teacher-details" open>
      <summary>Historical residual coefficients</summary>
      <p>Same fixed features, three targets. Look for a missing adjustment in close − model, then check whether actual − model supports its direction.</p>
      <div className="table-scroll"><table aria-label="Historical residual coefficients">
        <thead><tr><th>Feature</th>{targets.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead>
        <tbody>{teacher.feature_names.map(name => <tr key={name}><td>{name}</td>{targets.map(([key]) =>
          <td key={key}><Slope fit={teacher.fits[key]} feature={name} /></td>)}</tr>)}</tbody>
      </table></div>
      <p className="tiny">{teacher.fits.close_minus_model.n} games · {teacher.fits.close_minus_model.clusters} date clusters.
        Back-to-back: gap &lt;1.5 days; 3+ days: gap ≥2.5 days. March–April standings use prior-date records.
        Season indicators compare with 2023–24. A singular design is shown as unavailable, never as a zero effect.</p>
    </details> : null}
    {p?.fit ? <details className="teacher-details">
      <summary>Persistent team view versus game-day difference</summary>
      <p>Team-season mean disagreement SD: {number(p.team_season_mean_sd)} points across {p.team_seasons} team-seasons (full-sample description).</p>
      <div className="table-scroll"><table aria-label="Persistent and game-day differences">
        <thead><tr><th>Earlier-games component</th><th>Slope ± SE against actual − close</th></tr></thead>
        <tbody><tr><td>Standing team view</td><td><Slope fit={p.fit} feature="standing_view" /></td></tr>
          <tr><td>Game-day remainder</td><td><Slope fit={p.fit} feature="game_day" /></td></tr></tbody>
      </table></div>
      <p>{p.n} eligible games · {p.fit.clusters} date clusters · standing-view variance share {percent(p.standing_variance_share)}.</p>
      <p className="tiny">Each team needs 10+ earlier matched games in the same study season group. Standing view = home prior mean − away prior mean;
        game-day remainder = model − close − standing view. Variance share is Var(view) / [Var(view) + Var(remainder)], not explained variance (R²).
        Standing views of at least 1.5 points covered {percent(p.cover_rate)} ({p.cover_n} non-push games); descriptive only.</p>
    </details> : <p className="tiny">{p?.reason}</p>}
  </>;
}

export default function CloseTeacher({ historical, live }) {
  const providers = Object.entries(live?.summary?.close_teacher || {});
  return <section className="performance-group close-teacher" aria-label="Close-teacher diagnostics">
    <h3>Learn from the closing line</h3>
    <p>Use these diagnostics to choose changes worth testing. Fits are in sample; forecast weights and accuracy gates are unchanged.</p>
    <h4>Historical replay</h4>
    {historical?.diagnostics ? <>
      <p className="tiny">Reference: {historical.reference}. Historical reconstruction, separate from saved live forecasts.</p>
      <DiagnosticTables diagnostic={historical.diagnostics} />
    </> : <p>Historical diagnostics are not available in this export.</p>}
    <h4>Saved live forecasts</h4>
    {providers.length ? providers.map(([id, report]) => <details className="teacher-details" key={id}>
      <summary>{report.provider || id} · {report.diagnostics.overall.n} matched games</summary>
      <p className="tiny">Providers are evaluated separately; multiple lines for one game are not pooled as independent games.</p>
      <DiagnosticTables diagnostic={report.diagnostics} />
    </details>) : <p>Awaiting a market report with close-teacher diagnostics.</p>}
    <p className="tiny">A direction supported by both the close and outcomes is a research candidate. It must still pass the existing paired accuracy gates and prospective evaluation.
      {" "}<a href="https://github.com/otto-agent007/NBA-Forecast/blob/main/docs/beating-the-close-2026-11-06.md" target="_blank" rel="noreferrer">Study definitions and limitations</a></p>
  </section>;
}
