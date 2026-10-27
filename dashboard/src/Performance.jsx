import React from "react";
import { number, stamp } from "./data";
import ModelValidation from "./ModelValidation";
import DailyReview from "./DailyReview";
function MarketReport({ report }) {
  const s = report?.summary;
  if (!s?.rows) return null;
  const record = (r) => r ? `${r.wins}-${r.losses}${r.pushes ? `-${r.pushes}` : ""}${r.win_rate != null ? ` (${Math.round(r.win_rate * 100)}%)` : ""}` : "—";
  return (
    <section className="performance-group market-report" aria-label="Model against the market">
      <h3>Against the closing line · {s.games} games</h3>
      <div className="metric-band">
        {[["Margin error: model", s.margin_abs_error?.model], ["Margin error: market", s.margin_abs_error?.market],
          ...(s.margin_abs_error?.kalshi != null ? [[`Margin error: Kalshi at tip (${s.margin_abs_error.kalshi_n})`, s.margin_abs_error.kalshi]] : []),
          ["Total error: model", s.total_abs_error?.model], ["Total error: market", s.total_abs_error?.market]].map(([label, value]) => (
          <div key={label}><span>{label}</span><strong>{number(value)}</strong></div>
        ))}
      </div>
      <div className="table-scroll"><table>
        <thead><tr><th>Model lean of at least</th><th>Against the spread</th><th>Over/under</th></tr></thead>
        <tbody>{Object.keys(s.spread_leans || {}).map((key) => (
          <tr key={key}><td>{key.replace("edge>=", "")} pts</td><td>{record(s.spread_leans[key])}</td><td>{record(s.total_leans?.[key])}</td></tr>
        ))}</tbody>
      </table></div>
      {s.winner && <p>Winner picks: model {Math.round(s.winner.model_pick_accuracy * 100)}% · market favorite {Math.round(s.winner.market_pick_accuracy * 100)}% ·
        Brier market {number(s.winner.market_brier)}{s.winner.model_brier != null ? ` · model ${number(s.winner.model_brier)}` : ""} · {s.winner.n} games</p>}
      <p>Closing line is the last capture before tipoff (ESPN game summary, DraftKings). Lean records on a small sample are not an edge; the error rows compare model and market against the same final scores.</p>
    </section>
  );
}

export default function Performance({ data,date,onGame,onSelect }) {
  const report = data.performance,
    groups = report?.groups || [],
    count = report?.sample_counts?.evaluated || 0;
  return (
    <div className="performance">
      <div className="performance-intro">
        <h2>How the model is doing</h2>
        <p>Prospective results from predictions saved before tipoff.</p>
      </div>
      <DailyReview report={data.daily_reviews?.[date]} date={date} onGame={onGame} onPlayer={row=>{
        const game=data.games.find(g=>g.game_id===row.game_id);
        const forecast=game?.forecasts?.final || game?.forecast;
        const player=forecast?.predictions.find(p=>p.player_id===row.player_id);
        if(player) onSelect?.(player);
      }}/>
      <ModelValidation model={data.model_upgrade} />
      <MarketReport report={data.market} />
      {!count ? (
        <div className="empty performance-empty">
          <span className="empty-score">—</span>
          <h3>Evidence before a scorecard.</h3>
          <p>
            No evaluated player-games yet. Accuracy and calibration will appear
            here after saved forecasts are matched to final boxscores.
          </p>
        </div>
      ) : (
        groups.map((g) => (
          <section
            className="performance-group"
            key={g.season + g.model_version}
          >
            <h3>
              {g.season} · {g.model_version}
            </h3>
            <div className="metric-band">
              {[
                ["Evaluated", g.sample_counts.evaluated],
                ["Points MAE", g.metrics.points_mae],
                ["Minutes MAE", g.metrics.minutes_mae],
                [
                  "80% coverage",
                  g.metrics.interval80_coverage == null
                    ? null
                    : g.metrics.interval80_coverage * 100,
                ],
              ].map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <strong>
                    {number(value)}
                    {label === "80% coverage" && value != null ? "%" : ""}
                  </strong>
                </div>
              ))}
            </div>
            <p>
              Includes out predictions; inspect status groups before
              interpreting aggregate accuracy. Interval methods and evidence differ by model version.
            </p>
            {g.metrics.per_stat && (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Statistic</th>
                      <th>Outcomes</th>
                      <th>MAE</th>
                      <th>Bias</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(g.metrics.per_stat).map(([k, v]) => (
                      <tr key={k}>
                        <td>{{fg_pct:"FG%",fg3_pct:"3P%",ft_pct:"FT%",plus_minus:"+/-"}[k] || k.toUpperCase()}</td>
                        <td>{v.count}</td>
                        <td>{number(k.endsWith("_pct") && v.mae != null ? v.mae * 100 : v.mae)}{k.endsWith("_pct") && v.mae != null ? " pp" : ""}</td>
                        <td>{number(k.endsWith("_pct") && v.mean_signed_error != null ? v.mean_signed_error * 100 : v.mean_signed_error)}{k.endsWith("_pct") && v.mean_signed_error != null ? " pp" : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <details>
              <summary>Error breakdown and status groups</summary>
              <pre>
                {JSON.stringify(
                  {
                    status_groups: g.status_groups,
                    diagnostics: g.diagnostics,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
          </section>
        ))
      )}
      <div className="performance-bottom">
        <section>
          <h3>Coverage comes first</h3>
          <dl>
            <dt>Selected predictions</dt>
            <dd>{report?.sample_counts?.predictions ?? "—"}</dd>
            <dt>Awaiting results</dt>
            <dd>{report?.sample_counts?.pending ?? "—"}</dd>
            <dt>Withheld / excluded</dt>
            <dd>{report?.sample_counts?.excluded ?? "—"}</dd>
          </dl>
        </section>
        <section>
          <h3>Compare against the basics</h3>
          <p>
            Player average and minutes × scoring rate, compared on the same
            supported player-games.
          </p>
          {data.benchmarks?.groups?.length ? (
            data.benchmarks.groups.map((g) => (
              <div key={g.season + g.model_version}>
                <p>
                  {g.season} · {g.metrics.paired_n} paired outcomes
                </p>
                <table>
                  <thead>
                    <tr>
                      <th>Method</th>
                      <th>Points MAE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ["model", "Model"],
                      ["player_average", "Player average"],
                      ["minutes_rate", "Minutes × rate"],
                    ].map(([k, label]) => (
                      <tr key={k}>
                        <td>{label}</td>
                        <td>{number(g.metrics[k]?.mae)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))
          ) : (
            <p className="muted">Waiting for paired outcomes.</p>
          )}
        </section>
      </div>
      <p className="tiny">
        Report generated {stamp(report?.generated_at)}. No automatic tuning or
        accuracy claim.
      </p>
    </div>
  );
}
