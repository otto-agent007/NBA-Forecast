import React, { useEffect, useState, useRef } from "react";
import { TeamMark } from "./Icons";
import { number, stamp, safeSource } from "./data";
const moment = (t) => {
  const q =
    t <= 2880
      ? Math.min(4, Math.floor(t / 720) + 1)
      : 5 + Math.floor((t - 2880) / 300);
  const remaining =
    t === 2880 ? 0 : q <= 4 ? q * 720 - t : 2880 + (q - 4) * 300 - t;
  return `${q <= 4 ? "Q" + q : "OT" + (q - 4)} ${Math.floor(remaining / 60)}:${String(Math.floor(remaining % 60)).padStart(2, "0")}`;
};
export default function Postgame({ game }) {
  const playerPanel = useRef(null);
  const [data, setData] = useState(null),
    [error, setError] = useState(""),
    [team, setTeam] = useState("all"),
    [selected, setSelected] = useState("");
  useEffect(() => {
    setData(null);
    setError("");
    setTeam("all");
    setSelected("");
    if (!game) return;
    const abort = new AbortController();
    const snapshot =
      new URLSearchParams(location.search).get("snapshot") === "1";
    fetch(
      snapshot
        ? `./postgame/${game.game_id}.json`
        : `/api/postgame/${game.game_id}`,
      { signal: abort.signal, cache: "no-store" },
    )
      .then((r) => {
        if (!r.ok) throw Error("Postgame evidence unavailable.");
        return r.json();
      })
      .then(setData)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => abort.abort();
  }, [game?.game_id]);
  if (!game)
    return <div className="empty">Choose a date with a completed game.</div>;
  if (error || data?.missing_reason)
    return (
      <div className="empty">
        <h2>Postgame evidence unavailable</h2>
        <p>{error || data.missing_reason}</p>
      </div>
    );
  if (!data) return <p role="status">Loading postgame evidence…</p>;
  const scale =
    Math.max(20, ...data.timeline.map((p) => Math.abs(p.margin))) + 5;
  const path = data.timeline
    .map(
      (p, i) =>
        `${i ? "L" : "M"}${45 + (p.elapsed / data.duration) * 850},${140 - (p.margin / scale) * 110}`,
    )
    .join(" ");
  const rows = data.players.filter((p) => team === "all" || p.team_id === team);
  const player = data.players.find((p) => p.player_id === selected) || rows[0];
  const opening = data.opening_large_lead;
  return (
    <section className="postgame">
      <div className="postgame-score">
        <div>
          <TeamMark id={data.away.id} code={data.away.code} large />
          <span>{data.away.code}</span>
          <strong>{data.away.score}</strong>
        </div>
        <span className="postgame-final">
          FINAL
          <br />
          {data.pattern}
        </span>
        <div>
          <TeamMark id={data.home.id} code={data.home.code} large />
          <span>{data.home.code}</span>
          <strong>{data.home.score}</strong>
        </div>
      </div>
      <div className="postgame-summary">
        <section>
          <h3>What happened</h3>
          <p>
            {opening
              ? `${data.home.code} ${opening.home}–${opening.away} ${data.away.code} at ${moment(opening.elapsed)}: the first captured 20-point first-quarter lead.`
              : `Largest leads: ${data.home.code} +${data.largest_home_lead}; ${data.away.code} +${data.largest_away_lead}.`}
          </p>
          <p>
            {data.low_leverage_start != null
              ? `Sustained large-lead fourth-quarter window begins ${moment(data.low_leverage_start)}. This is a descriptive threshold, not proof the game was decided.`
              : "No sustained large-lead fourth-quarter window met the rule."}
          </p>
        </section>
        <section>
          <h3>What we learned</h3>
          <p>
            Compare minutes, attempts and conversion separately. A shortened
            night or late bench appearance does not establish a new role.
          </p>
          <h3>What changes next game</h3>
          <p>
            {data.review_queue.length} player reviews flagged. No automatic
            baseline changes. Coaching, injury and matchup explanations need
            sourced review.
          </p>
        </section>
      </div>
      <h3>How the teams produced</h3>
      <p className="tiny">
        Full-game boxscore totals. These describe production; they do not
        establish why the opening run occurred.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Team</th>
              <th>FG</th>
              <th>3PT</th>
              <th>FT</th>
              <th>Turnovers</th>
              <th>Off. rebounds</th>
              <th>Fast-break PTS</th>
              <th>PTS off turnovers</th>
              <th>Bench PTS</th>
            </tr>
          </thead>
          <tbody>
            {[data.away, data.home].map((t) => {
              const s = data.team_stats[t.id];
              return (
                <tr key={t.id}>
                  <td>{t.code}</td>
                  <td>
                    {number(s.fieldGoalsMade)}/{number(s.fieldGoalsAttempted)}
                  </td>
                  <td>
                    {number(s.threePointersMade)}/
                    {number(s.threePointersAttempted)}
                  </td>
                  <td>
                    {number(s.freeThrowsMade)}/{number(s.freeThrowsAttempted)}
                  </td>
                  <td>{number(s.turnoversTotal)}</td>
                  <td>{number(s.reboundsOffensive)}</td>
                  <td>{number(s.pointsFastBreak)}</td>
                  <td>{number(s.pointsFromTurnovers)}</td>
                  <td>{number(s.benchPoints)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <h3>Score-margin timeline</h3>
      <p className="tiny">
        Positive = {data.home.code} leads · Negative = {data.away.code} leads ·
        Shading marks the descriptive large-lead window.
      </p>
      <svg
        className="margin-chart"
        viewBox="0 0 940 290"
        role="img"
        aria-label={`${data.away.code} at ${data.home.code} score margin over time`}
      >
        {data.low_leverage_start != null && (
          <rect
            x={45 + (data.low_leverage_start / data.duration) * 850}
            y="20"
            width={
              ((data.duration - data.low_leverage_start) / data.duration) * 850
            }
            height="240"
            fill="#edf3fc"
          />
        )}
        {[-20, 0, 20].map((n) => (
          <g key={n}>
            <line
              x1="45"
              x2="895"
              y1={140 - (n / scale) * 110}
              y2={140 - (n / scale) * 110}
              stroke="#d9e2ef"
            />
            <text x="5" y={144 - (n / scale) * 110}>
              {n > 0 ? "+" : ""}
              {n}
            </text>
          </g>
        ))}
        {[0, 720, 1440, 2160, data.duration].map((t) => (
          <g key={t}>
            <line
              x1={45 + (t / data.duration) * 850}
              x2={45 + (t / data.duration) * 850}
              y1="20"
              y2="250"
              stroke="#e6ebf2"
            />
            <text
              x={45 + (t / data.duration) * 850}
              y="278"
              textAnchor="middle"
            >
              {t === data.duration ? "Final" : moment(t)}
            </text>
          </g>
        ))}
        <path d={path} fill="none" stroke="#2472d5" strokeWidth="3" />
      </svg>
      <div className="section-heading">
        <h3>Every player · projection vs actual</h3>
        <select
          aria-label="Postgame team"
          value={team}
          onChange={(e) => {
            setTeam(e.target.value);
            setSelected("");
          }}
        >
          <option value="all">Both teams</option>
          <option value={data.away.id}>{data.away.code}</option>
          <option value={data.home.id}>{data.home.code}</option>
        </select>
      </div>
      <p className="tiny">
        {data.forecast_run_id
          ? `Saved pregame run: ${data.forecast_run_id}`
          : "No eligible saved pregame forecast. Actuals remain visible; retrospective predictions are not manufactured."}{" "}
        Error = actual minus projected. All outcomes count.
      </p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Player</th>
              <th>Role</th>
              <th>MIN proj / actual</th>
              <th>FGA proj / actual</th>
              <th>PTS proj / actual</th>
              <th>PTS error</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.player_id}>
                <td>
                  <button
                    className="text-button"
                    onClick={() => {
                      setSelected(p.player_id);
                      playerPanel.current?.scrollIntoView({
                        behavior: "smooth",
                        block: "start",
                      });
                    }}
                    aria-pressed={player?.player_id === p.player_id}
                  >
                    {p.name}
                  </button>
                </td>
                <td>
                  {p.status === "dnp" ? "DNP" : p.starter ? "Starter" : "Bench"}
                </td>
                <td>
                  {number(p.prediction?.expected_minutes)} / {number(p.minutes)}
                </td>
                <td>
                  {number(p.prediction?.expected_fga)} / {number(p.fga)}
                </td>
                <td>
                  {number(p.prediction?.points_mean)} / {number(p.pts)}
                </td>
                <td>{number(p.feedback.points_error)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {player && (
        <section className="postgame-player" ref={playerPanel}>
          <h3>{player.name} · rotation and takeaways</h3>
          <ul>
            {player.takeaways.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
          <div className="form-grid">
            {[
              ["Minutes effect", "minutes_effect"],
              ["Shot-volume effect", "shot_volume_effect"],
              ["Conversion + FT effect", "conversion_and_ft_effect"],
            ].map(([label, k]) => (
              <div key={k}>
                <span>{label}</span>
                <strong>{number(player.feedback[k])}</strong>
                <small>points of forecast error</small>
              </div>
            ))}
          </div>
          <p className="tiny">
            Arithmetic attribution, not a causal diagnosis. Conversion includes
            free throws, shot mix and opponent adjustment; it is not pure
            shooting luck.
          </p>
          <h4>Recorded substitutions</h4>
          <div className="rotation-events">
            {player.substitutions.map((e) => (
              <div key={e.action_number}>
                <strong>{moment(e.elapsed)}</strong>
                <span className={e.direction === "in" ? "rotation-in" : ""}>
                  {e.direction.toUpperCase()}
                </span>
                <small>
                  {data.home.code} {e.margin >= 0 ? "+" : ""}
                  {e.margin}
                </small>
              </div>
            ))}
          </div>
          {!player.substitutions.length && (
            <p>No substitution events captured for this player.</p>
          )}
          <p className="tiny">{data.coverage.rotation}</p>
        </section>
      )}
      <details>
        <summary>Evidence and classification rules</summary>
        <p>
          Early runaway: eventual winner leads by 20 in Q1 and never falls below
          10 afterward. Comeback: winner previously trailed by 15. Late blowout:
          final margin at least 20 with a sustained second-half lead of at least
          15 after reaching 20. Competitive finish: final margin within eight.
          Categories are descriptive and prioritize comeback, then early
          runaway.
        </p>
        <p>
          Large-lead window: Q4 lead reaches 20 and never drops below 15
          afterward. It is not a calibrated garbage-time detector. Shooting and
          free-throw totals and final score reconcile with the final boxscore.
        </p>
        {data.provenance.map((p) => (
          <p key={p.source_url}>
            <a href={safeSource(p.source_url)} target="_blank" rel="noreferrer">
              NBA{" "}
              {p.source_url.includes("playbyplay")
                ? "play-by-play"
                : "boxscore"}{" "}
              ↗
            </a>{" "}
            · {stamp(p.retrieved_at)}
          </p>
        ))}
        <p>{data.policy}</p>
      </details>
    </section>
  );
}
