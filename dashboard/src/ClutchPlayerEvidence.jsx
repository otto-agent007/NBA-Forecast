import React, { useState } from "react";
import { fmt, METRICS } from "./clutchData";
const signed = (n) => (n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}`);
const interval = (v, metric = "efg") =>
  v ? `${fmt(v[0], metric)}–${fmt(v[1], metric)}` : "—";
export default function PlayerEvidence({ player: p, seasons }) {
  const [metric, setMetric] = useState("pts_min");
  return (
    <section
      id="clutch-player-evidence"
      className="clutch-panel clutch-evidence"
      aria-label={`${p.name} evidence`}
    >
      <span className="clutch-eyebrow">03 / SEASON EXPLANATION</span>
      <h2>{p.name}</h2>
      <p className="clutch-muted">
        {p.available_seasons} of 3 archived seasons. Every denominator is
        retained. Missing seasons do not receive synthetic values.
      </p>
      <div className="clutch-trend-heading">
        <h3>Seasonal trend</h3>
        <label>
          Trend metric
          <select
            aria-label="Seasonal trend metric"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          >
            {Object.entries(METRICS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="clutch-trends">
        {seasons.map((s, i) => {
          const r = p.seasons[i];
          return (
            <div key={s}>
              <span>{s}</span>
              <strong>{r ? fmt(r.adjusted[metric], metric) : "—"}</strong>
              <small>
                {r
                  ? `${r.totals.GP} games · ${r.totals.MIN.toFixed(1)} min`
                  : "No archived row"}
              </small>
              <small>
                {r
                  ? r.ratings[metric] == null
                    ? "Limited sample · unranked"
                    : `Adjusted rating: ${r.ratings[metric].toFixed(0)}/100`
                  : "Missing, not zero"}
              </small>
            </div>
          );
        })}
      </div>
      <p className="clutch-muted">
        {p.trends[metric]
          ? `${p.trends[metric].qualified_seasons} qualified seasons; adjusted max–min spread: ${fmt(p.trends[metric].range, metric)}. A three-season spread is descriptive, not an estimate of stable talent.`
          : "Shooting qualification uses the attempts for this shot type, separately from total FGA."}
      </p>
      <details>
        <summary>All counts, shooting samples and uncertainty</summary>
        <div className="table-scroll">
          <table className="clutch-table">
            <thead>
              <tr>
                <th>Season</th>
                <th>GP / MIN</th>
                <th>PTS / AST / TOV</th>
                <th>FGM / FGA</th>
                <th>3PM / 3PA</th>
                <th>FTM / FTA</th>
                <th>MIN/GP</th>
                <th>PTS/GP</th>
                <th>Raw eFG / TS</th>
                <th>3PA share</th>
                <th>AST/TOV</th>
              </tr>
            </thead>
            <tbody>
              {p.seasons.map((r, i) => (
                <tr key={seasons[i]}>
                  <td>{seasons[i]}</td>
                  {r ? (
                    <>
                      <td>
                        {r.totals.GP} / {r.totals.MIN.toFixed(1)}
                      </td>
                      <td>
                        {r.totals.PTS} / {r.totals.AST} / {r.totals.TOV}
                      </td>
                      <td>
                        {r.totals.FGM} / {r.totals.FGA}
                      </td>
                      <td>
                        {r.totals.FG3M} / {r.totals.FG3A}
                      </td>
                      <td>
                        {r.totals.FTM} / {r.totals.FTA}
                      </td>
                      <td>{fmt(r.raw.min_gp)}</td>
                      <td>{fmt(r.raw.pts_gp)}</td>
                      <td>
                        {fmt(r.raw.efg, "efg")} / {fmt(r.raw.ts, "ts")}
                      </td>
                      <td>{fmt(r.raw.three_share, "three_share")}</td>
                      <td>{fmt(r.raw.ast_tov)}</td>
                    </>
                  ) : (
                    <td colSpan="10">No archived row</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="table-scroll">
          <table className="clutch-table">
            <thead>
              <tr>
                <th>Season / shot</th>
                <th>Makes / attempts</th>
                <th>Raw → adjusted</th>
                <th>90% model interval</th>
                <th>Prior share</th>
                <th>Sample</th>
              </tr>
            </thead>
            <tbody>
              {p.seasons.flatMap((r, i) =>
                r
                  ? Object.entries(r.shooting).map(([k, s]) => (
                      <tr key={`${i}-${k}`}>
                        <td>
                          {seasons[i]} · {METRICS[k]}
                        </td>
                        <td>
                          {s.makes} / {s.attempts}
                        </td>
                        <td>
                          {fmt(s.raw, k)} → {fmt(s.adjusted, k)}
                        </td>
                        <td>{interval(s.interval90, k)}</td>
                        <td>{(s.prior_share * 100).toFixed(0)}%</td>
                        <td>
                          {s.qualified
                            ? "Qualified"
                            : s.attempts
                              ? "Limited sample"
                              : "Prior only · no attempts"}
                        </td>
                      </tr>
                    ))
                  : [],
              )}
            </tbody>
          </table>
        </div>
      </details>
      <h3>What changed in points per qualifying game?</h3>
      <p className="clutch-muted">
        Symmetric accounting separates court time, shot/FT attempts per minute,
        and conversion. Effects sum to the observed change; they do not
        establish causes.
      </p>
      <div className="clutch-decompositions">
        {p.transitions.map((t) => (
          <div key={t.start}>
            <h4>
              {t.start} → {t.end}
            </h4>
            {t.decomposition ? (
              <>
                <strong>{signed(t.decomposition.delta_pts_gp)} PTS/GP</strong>
                <dl>
                  {[
                    ["court_time", "Court time"],
                    ["attempt_rates", "Attempt rates & mix"],
                    ["conversion", "Conversion"],
                  ].map(([k, label]) => (
                    <React.Fragment key={k}>
                      <dt>{label}</dt>
                      <dd>{signed(t.decomposition.effects[k])}</dd>
                    </React.Fragment>
                  ))}
                </dl>
              </>
            ) : (
              <p>Unavailable: a season or shot-type denominator is missing.</p>
            )}
          </div>
        ))}
      </div>
      <details>
        <summary>How sensitive is this comparison to shrinkage?</summary>
        <p className="clutch-muted">
          Half / default / double the prior strength. This is an assumption
          check, not a confidence interval. Rate priors: 25 / 50 / 100 minutes.
          Shooting priors: 15 / 30 / 60 attempts per shot type.
        </p>
        <div className="table-scroll">
          <table className="clutch-table">
            <thead>
              <tr>
                <th>Season</th>
                <th>{METRICS[metric]} · half</th>
                <th>Default</th>
                <th>Double</th>
                <th>Rate prior share</th>
              </tr>
            </thead>
            <tbody>
              {p.seasons.map((r, i) => (
                <tr key={seasons[i]}>
                  <td>{seasons[i]}</td>
                  {r ? (
                    <>
                      {["0.5", "1", "2"].map((k) => (
                        <td key={k}>{fmt(r.sensitivity[k][metric], metric)}</td>
                      ))}
                      <td>{(100 * r.rate_prior_share).toFixed(0)}%</td>
                    </>
                  ) : (
                    <td colSpan="4">No archived row</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
