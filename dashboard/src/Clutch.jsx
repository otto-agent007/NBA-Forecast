import Scatter from "./ClutchScatter";
import PlayerEvidence from "./ClutchPlayerEvidence";
import Methodology from "./ClutchMethodology";
import React, { useEffect, useMemo, useState } from "react";
import {
  DEFAULT_PLAYERS,
  METRICS,
  fmt,
  metricValue,
  selectRows,
  intervalMessage,
} from "./clutchData";

const COLORS = [
  "#1966ca",
  "#b35b14",
  "#703cc4",
  "#087f76",
  "#b33257",
  "#506074",
];
const labelPeriod = (s) =>
  s === "pooled" ? "All available seasons · pooled" : s;

export default function Clutch({ refreshKey }) {
  const [data, setData] = useState(null),
    [error, setError] = useState("");
  const [period, setPeriod] = useState("2025-26"),
    [metric, setMetric] = useState("pts_min");
  const [query, setQuery] = useState(""),
    [qualified, setQualified] = useState(true),
    [adjusted, setAdjusted] = useState(true);
  const [selected, setSelected] = useState(DEFAULT_PLAYERS),
    [focus, setFocus] = useState(1628973),
    [page, setPage] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    setData(null);
    const snapshot =
      new URLSearchParams(location.search).get("snapshot") === "1";
    fetch(snapshot ? "./clutch.json" : "/api/clutch", {
      signal: controller.signal,
      cache: "no-store",
    })
      .then((r) => {
        if (!r.ok) throw Error("Historical clutch evidence is unavailable.");
        return r.json();
      })
      .then((d) => {
        if (d.missing_reason) throw Error(d.missing_reason);
        if (
          d.schema_version !== 1 ||
          d.forecast_eligible !== false ||
          !Array.isArray(d.players) ||
          !Array.isArray(d.periods)
        )
          throw Error("Unsupported historical comparison format.");
        setData(d);
      })
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [refreshKey]);
  const current = data?.periods.find((p) => p.period === period);
  const rows = useMemo(
    () =>
      selectRows(current?.rows || [], { metric, query, qualified, adjusted }),
    [current, metric, query, qualified, adjusted],
  );
  const players = useMemo(
    () =>
      selected
        .map((id) => data?.players.find((p) => p.player_id === id))
        .filter(Boolean),
    [data, selected],
  );
  function toggle(id) {
    setSelected((old) =>
      old.includes(id)
        ? old.filter((x) => x !== id)
        : old.length < 6
          ? [...old, id]
          : old,
    );
  }
  function inspect(id) {
    setFocus(id);
    document
      .getElementById("clutch-player-evidence")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  if (error)
    return (
      <section className="clutch-empty" role="alert">
        <h2>Clutch comparisons unavailable</h2>
        <p>{error}</p>
        <p>Game forecasts remain separate from this historical report.</p>
      </section>
    );
  if (!data) return <p role="status">Loading historical clutch comparisons…</p>;
  const detail = data.players.find((p) => p.player_id === focus);
  const pageCount = Math.max(1, Math.ceil(rows.length / 20));
  const activePage = Math.min(page, pageCount - 1);
  const selectedRows = players.map((p) =>
    period === "pooled" ? p.pooled : p.seasons[data.seasons.indexOf(period)],
  );
  const firstPair = selectedRows.slice(0, 2);
  return (
    <div className="clutch">
      <section className="clutch-intro">
        <div>
          <span className="clutch-eyebrow">HISTORICAL LAB · 2023–26</span>
          <h2>
            Who takes over.
            <br />
            What the evidence says.
          </h2>
          <p>
            Scoring responsibility, conversion and playmaking—viewed separately,
            across three regular seasons.
          </p>
        </div>
        <div className="clutch-archive">
          <strong>
            {data.periods
              .filter((p) => p.period !== "pooled")
              .reduce((n, p) => n + p.rows.length, 0)
              .toLocaleString()}
          </strong>
          <span>verified player-seasons</span>
          <b>{data.players.length} players · 3 seasons</b>
          <small>Final 5 minutes of Q4 or OT · margin within 5</small>
        </div>
      </section>
      <div className="clutch-notice">
        <strong>Historical comparisons, not predictions.</strong> No forecast
        weights changed. These totals cannot establish a clutch advantage over a
        player’s normal performance.
      </div>
      <section className="clutch-panel" aria-labelledby="explorer-heading">
        <div className="clutch-section-head">
          <div>
            <span className="clutch-eyebrow">01 / LEAGUE EXPLORER</span>
            <h2 id="explorer-heading">Opportunity meets efficiency</h2>
          </div>
          <label>
            Season
            <select
              aria-label="Clutch season"
              value={period}
              onChange={(e) => {
                setPeriod(e.target.value);
                setPage(0);
              }}
            >
              {data.periods.map((p) => (
                <option key={p.period} value={p.period}>
                  {labelPeriod(p.period)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="clutch-muted">
          {period === "pooled"
            ? "Totals combined over each player’s available seasons; no recency weighting. Unequal career coverage remains visible."
            : "Every player in the archive is eligible for inspection, including players absent from the current roster."}
        </p>
        <div className="clutch-explorer">
          <Scatter
            rows={current.rows.filter((r) => r.efficiency_qualified)}
            selected={selected}
            onFocus={inspect}
          />
          <aside className="clutch-selection">
            <h3>Your comparison</h3>
            <p className="clutch-muted">
              Select up to six players in the table. Select a name here to
              inspect all seasons.
            </p>
            {players.map((p, i) => (
              <div className="clutch-selected" key={p.player_id}>
                <button
                  onClick={() => inspect(p.player_id)}
                  aria-pressed={focus === p.player_id}
                >
                  <span
                    className="clutch-dot"
                    style={{ background: COLORS[i] }}
                  />
                  {p.name}
                </button>
                <button
                  aria-label={`Remove ${p.name}`}
                  onClick={() => toggle(p.player_id)}
                >
                  ×
                </button>
              </div>
            ))}
            {!players.length && <p>No players selected.</p>}
            <p className="clutch-muted">
              Point size reflects clutch minutes. The chart uses adjusted
              estimates and shooting-qualified players only.
            </p>
            <p className="clutch-muted">
              {current.cohorts.efg} shooting-qualified players ·{" "}
              {current.cohorts.pts_min} exposure-qualified players
            </p>
          </aside>
        </div>
        <div className="clutch-controls">
          <label>
            Find a player
            <input
              aria-label="Find clutch player"
              placeholder="Search all historical players"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(0);
              }}
            />
          </label>
          <label>
            Compare dimension
            <select
              aria-label="Clutch dimension"
              value={metric}
              onChange={(e) => {
                setMetric(e.target.value);
                setPage(0);
              }}
            >
              {Object.entries(METRICS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label className="clutch-check">
            <input
              type="checkbox"
              checked={qualified}
              onChange={(e) => {
                setQualified(e.target.checked);
                setPage(0);
              }}
            />{" "}
            Qualified only
          </label>
          <label className="clutch-check">
            <input
              type="checkbox"
              checked={adjusted}
              onChange={(e) => setAdjusted(e.target.checked)}
            />{" "}
            Adjusted estimates
          </label>
        </div>
        <p className="clutch-muted">
          Ratings are adjusted midrank percentiles within this period’s
          qualified cohort ({current.cohorts[metric]} players).{" "}
          {metric === "tov_min"
            ? "Lower turnover rate ranks higher; this is not adjusted for ball-handling workload."
            : "Higher values rank higher."}{" "}
          Unqualified rows have no rating. Filters do not change the rating
          population.
        </p>
        <div className="table-scroll">
          <table className="clutch-table">
            <thead>
              <tr>
                <th>Compare</th>
                <th>Player</th>
                <th>{METRICS[metric]}</th>
                <th>Rating / 100</th>
                <th>GP</th>
                <th>MIN</th>
                <th>FGA</th>
                <th>AST / TOV</th>
                <th>Sample</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(activePage * 20, activePage * 20 + 20).map((r) => (
                <tr key={r.player_id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Compare ${r.name}`}
                      checked={selected.includes(r.player_id)}
                      disabled={
                        !selected.includes(r.player_id) && selected.length >= 6
                      }
                      onChange={() => toggle(r.player_id)}
                    />
                  </td>
                  <td>
                    <button
                      className="text-button"
                      onClick={() => inspect(r.player_id)}
                    >
                      {r.name}
                    </button>
                  </td>
                  <td>
                    <strong>
                      {fmt(metricValue(r, metric, adjusted), metric)}
                    </strong>
                  </td>
                  <td>
                    {r.ratings[metric] == null
                      ? "Unranked"
                      : r.ratings[metric].toFixed(0)}
                  </td>
                  <td>{r.totals.GP}</td>
                  <td>{r.totals.MIN.toFixed(1)}</td>
                  <td>{r.totals.FGA}</td>
                  <td>
                    {r.totals.AST} / {r.totals.TOV}
                  </td>
                  <td>
                    <span
                      className={`clutch-badge ${r.ratings[metric] == null ? "limited" : ""}`}
                    >
                      {r.ratings[metric] == null
                        ? "Limited sample"
                        : "Qualified"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!rows.length && (
          <p className="empty">
            No matching players. Try another season or turn off “Qualified
            only.”
          </p>
        )}
        <div className="clutch-pagination">
          <span>
            {rows.length} matching players · page {activePage + 1} of{" "}
            {pageCount}
          </span>
          <button
            disabled={activePage === 0}
            onClick={() => setPage(activePage - 1)}
          >
            Previous
          </button>
          <button
            disabled={activePage + 1 >= pageCount}
            onClick={() => setPage(activePage + 1)}
          >
            Next
          </button>
        </div>
      </section>
      <section className="clutch-panel">
        <span className="clutch-eyebrow">02 / SIDE BY SIDE</span>
        <h2>Different ways to contribute</h2>
        <p className="clutch-muted">
          {labelPeriod(period)}. Shooting uses fixed observed shot mix.
          Approximate 90% model intervals describe conversion uncertainty; they
          are not game forecasts or uncertainty in causal impact.
        </p>
        <div className="table-scroll">
          <table className="clutch-table">
            <thead>
              <tr>
                <th>Player</th>
                <th>Coverage</th>
                <th>PTS/GP raw</th>
                <th>PTS/min adj</th>
                <th>FGA/min adj</th>
                <th>FTA/min adj</th>
                <th>eFG% raw → adj</th>
                <th>90% eFG interval</th>
                <th>AST/min adj</th>
                <th>TOV/min adj</th>
                <th>GP / MIN</th>
              </tr>
            </thead>
            <tbody>
              {players.map((p, i) => {
                const r = selectedRows[i];
                return (
                  <tr key={p.player_id}>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => inspect(p.player_id)}
                      >
                        {p.name}
                      </button>
                    </td>
                    <td>{p.available_seasons}/3 seasons</td>
                    {r ? (
                      <>
                        <td>{fmt(r.raw.pts_gp)}</td>
                        <td>{fmt(r.adjusted.pts_min)}</td>
                        <td>{fmt(r.adjusted.fga_min)}</td>
                        <td>{fmt(r.adjusted.fta_min)}</td>
                        <td>
                          {fmt(r.raw.efg, "efg")} → {fmt(r.adjusted.efg, "efg")}
                        </td>
                        <td>
                          {r.efg_interval90
                            ? `${fmt(r.efg_interval90[0], "efg")}–${fmt(r.efg_interval90[1], "efg")}`
                            : "—"}
                          {!r.efficiency_qualified && (
                            <small>Limited sample</small>
                          )}
                        </td>
                        <td>{fmt(r.adjusted.ast_min)}</td>
                        <td>{fmt(r.adjusted.tov_min)}</td>
                        <td>
                          {r.totals.GP} / {r.totals.MIN.toFixed(1)}
                        </td>
                      </>
                    ) : (
                      <td colSpan="9">
                        No archived row for this season—not zero.
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {players.length >= 2 && (
          <p className="clutch-muted">
            {players[0].name} / {players[1].name}:{" "}
            {intervalMessage(
              firstPair[0]?.efg_interval90,
              firstPair[1]?.efg_interval90,
            )}{" "}
            Overlap is a visual caution, not a formal test.
          </p>
        )}
        {!players.length && (
          <p>Select players in the league explorer to compare them.</p>
        )}
      </section>
      {detail && (
        <PlayerEvidence
          key={detail.player_id}
          player={detail}
          seasons={data.seasons}
        />
      )}
      <section className="clutch-panel">
        <span className="clutch-eyebrow">04 / REPEATABILITY</span>
        <h2>Does the ordering carry across seasons?</h2>
        <p className="clutch-muted">
          Exploratory correlation of within-season adjusted percentile ranks
          among players qualified in both seasons. Different metrics have
          different samples. This is not a held-out predictive evaluation or
          proof of stable skill.
        </p>
        <div className="table-scroll">
          <table className="clutch-table">
            <thead>
              <tr>
                <th>Dimension</th>
                <th>2023–24 → 2024–25</th>
                <th>Matched players</th>
                <th>2024–25 → 2025–26</th>
                <th>Matched players</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(METRICS)
                .filter((k) => !["two", "three", "ft"].includes(k))
                .map((k) => {
                  const pair = data.consistency.filter((c) => c.metric === k);
                  return (
                    <tr key={k}>
                      <td>{METRICS[k]}</td>
                      {pair.map((c) => (
                        <React.Fragment key={c.start}>
                          <td>{fmt(c.rank_correlation)}</td>
                          <td>{c.pairs}</td>
                        </React.Fragment>
                      ))}
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </section>
      <Methodology data={data} />
    </div>
  );
}
