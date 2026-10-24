import PlayerPortrait from "./PlayerPortrait";
import RecentForm from "./RecentForm";
import React, { useRef, useEffect, useState } from "react";
import { Icon, TeamMark } from "./Icons";
import { Status } from "./PlayerTable";
import { dateKey, number, safeSource, stamp } from "./data";
import ShotChart from "./ShotChart";
import BoxScoreTable from "./BoxScoreTable";
import { BOX_SCORE_COLUMNS, averageBoxScore, boxScoreTitle, boxScoreValue } from "./boxscore";
export default function PlayerDetail({ player, onClose }) {
  const ref = useRef(null),
    [profile, setProfile] = useState(null),
    [error, setError] = useState(""),
    [tab, setTab] = useState("Overview"),
    [season, setSeason] = useState("current_season");
  useEffect(() => {
    ref.current.showModal();
    return () => ref.current?.close();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const staticMode =
      new URLSearchParams(location.search).get("snapshot") === "1";
    fetch(
      staticMode
        ? `./players/${player.player_id}.json`
        : `/api/players/${player.player_id}`,
      { signal: controller.signal, cache: "no-store" },
    )
      .then((r) => {
        if (!r.ok)
          throw new Error(
            "Player data is unavailable. Run the player-data sync.",
          );
        return r.json();
      })
      .then(setProfile)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [player.player_id]);
  const source = safeSource(player.status_source_url),
    c = player.components || {},
    shotProfile = player.modeled_shot_profile,
    summary = profile?.[season],
    recent = profile?.recent_games || [];
  return (
    <dialog
      ref={ref}
      className="player-profile"
      aria-labelledby="player-title"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="profile-hero">
        <div className="profile-identity">
          <PlayerPortrait key={player.player_id} player={player} large />
          <div>
            <p className="eyebrow">
              <TeamMark id={player.team_id} code={player.team_code} /> PLAYER
              PROFILE · {player.team_code || "NBA"}
            </p>
            <h2 id="player-title">{player.name}</h2>
            <Status player={player} />
            {profile?.bio && (
              <p className="profile-bio">
                {[
                  profile.bio.position,
                  profile.bio.jersey ? `#${profile.bio.jersey}` : null,
                  profile.bio.height,
                  profile.bio.weight ? `${profile.bio.weight} lb` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            )}
          </div>
        </div>
        <button
          className="icon-button"
          aria-label="Close player details"
          onClick={onClose}
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="profile-tabs" role="tablist" aria-label="Player sections">
        {["Overview", "Game log", "Shot chart", "Forecast history"].map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      <div className="profile-body">
        {error && <p role="alert">{error}</p>}
        {!profile && !error && (
          <p role="status">Loading NBA player evidence…</p>
        )}
        {profile?.missing_reason && (
          <p className="warning">{profile.missing_reason}</p>
        )}
        {tab === "Overview" && (
          <>
            <div className="section-heading">
              <h3>Regular-season averages</h3>
              <select
                aria-label="Statistics season"
                value={season}
                onChange={(e) => setSeason(e.target.value)}
              >
                <option value="current_season">
                  {profile?.current_season?.season || "Current season"}
                </option>
                <option value="prior_season">
                  {profile?.prior_season?.season || "Previous season"}
                </option>
              </select>
            </div>
            <p className="tiny">
              {summary?.gp ?? "—"} captured games played · {summary?.dnp ?? "—"}{" "}
              DNPs excluded from averages. Coverage reflects downloaded final
              boxscores.
            </p>
            <BoxScoreTable row={averageBoxScore(summary)} mode="average" label="Regular-season averages" coverage={summary?.coverage} />
            <p className="tiny">Scroll for the full boxscore. Category sample sizes are in each value’s tooltip.</p>
            <RecentForm profile={profile} />
            <div className="profile-columns">
              <section className="profile-forecast">
                <h3>Next-game projection</h3>
                <p className="projection-matchup">
                  {player.matchup || "No matchup selected"}
                </p>
                <p className="reason">{player.reason}</p>
                <BoxScoreTable row={player} label="Next-game projected boxscore" />
                <p className="tiny">
                  MIN:SS is estimated playing time. {" "}
                  {player.status === "projected"
                    ? `${number(player.points_lower80)}–${number(player.points_upper80)} points: nominal 80% range; ${c.uncertainty?.calibration?.status === 'calibrated' ? c.uncertainty.calibration.audit.label : 'heuristic, not yet calibrated'}.`
                    : "A numeric forecast requires supported availability and role evidence."}
                </p>
                {player.status === "projected" && <p className="tiny" aria-label="Boxscore estimate evidence">
                  Other boxscore estimates are uncalibrated. +/- is a noisy
                  historical on-court point differential, shrunk toward zero;
                  it is not a player impact rating.
                </p>}
                {player.status === "projected" && shotProfile && <p className="tiny" aria-label="Shot distribution evidence">
                  Shot distribution evidence: {number(shotProfile.observed_attempts)} captured attempts
                  {" "}in {number(shotProfile.games)} games; {number(shotProfile.located_attempts)} have verified locations.
                  {" "}Projected attempts use the observed shot mix where available.
                </p>}
                {player.availability === "Not listed" && (
                  <p className="warning">
                    Conditional: omitted from a complete report. Participation
                    is not confirmed.
                  </p>
                )}
                {source && (
                  <a href={source} target="_blank" rel="noreferrer">
                    Availability source ↗
                  </a>
                )}
                {c.historical_prior && <section aria-label="Historical scoring prior">
                  <h3>Historical scoring baseline</h3>
                  <dl className="profile-facts">{c.historical_prior.seasons.map((s) => <React.Fragment key={s.season}>
                    <dt>{s.season}</dt><dd>{number(s.ppg)} PPG · {s.games} games</dd>
                  </React.Fragment>)}</dl>
                  <p className="tiny">Recent completed seasons carry more weight. The game forecast also reflects minutes, availability and matchup.</p>
                  {safeSource(c.historical_prior.source?.source_url) && <a href={c.historical_prior.source.source_url} target="_blank" rel="noreferrer">Historical statistics source ↗</a>}
                </section>}
              </section>
              {c.game_environment && <section aria-label="Game environment">
                <h3>Game environment</h3><dl className="profile-facts">
                  <dt>Expected possessions</dt><dd>{number(c.game_environment.expected_possessions)}</dd>
                  <dt>Pace adjustment</dt><dd>{number((c.game_environment.pace_factor-1)*100)}%</dd>
                  <dt>Defense adjustment</dt><dd>{number((c.game_environment.defense_factor-1)*100)}%</dd>
                  <dt>Rest / travel adjustment</dt><dd>{number((c.game_environment.context_factor-1)*100)}%</dd>
                </dl><p className="tiny">Possessions are estimated from verified team boxscores. Rest and travel effects are fitted from historical outcomes.</p>
              </section>}
              {c.uncertainty?.calibration && <section aria-label="Scoring interval evidence">
                <h3>Scoring interval evidence</h3><p>{c.uncertainty.calibration.status === 'calibrated'
                  ? `${c.uncertainty.calibration.audit.label} · ${c.uncertainty.calibration.audit.sample_count} calibration outcomes`
                  : 'Heuristic fallback: matching calibration evidence is not ready.'}</p>
                <p className="tiny">Nominal coverage is assessed against outcomes; it is not a guarantee for an individual game.</p>
              </section>}
              <section>
                <h3>Role & minutes</h3>
                <p className="role-label">
                  {c.rotation_role?.label || "Role not yet established"}
                </p>
                <dl className="profile-facts">
                  <dt>Baseline minutes</dt>
                  <dd>{number(c.opportunities?.minutes_before_adjustment)}</dd>
                  <dt>Recent minutes</dt>
                  <dd>{number(c.rotation_role?.recent_mean_minutes)}</dd>
                  <dt>Sourced minutes cap</dt>
                  <dd>{number(c.manual_adjustment?.minutes_cap)}</dd>
                  <dt>Opponent factor</dt>
                  <dd>{typeof c.opponent?.factor === 'number' ? c.opponent.factor.toFixed(3) : '—'}</dd>
                </dl>
                <p className="tiny">
                  {c.game_environment ? "Opponent adjustment uses possession-adjusted defensive results." : "Opponent adjustment uses a bounded points-allowed proxy."}
                </p>
                {c.manual_adjustment && (
                  <p className="tiny">
                    Reviewed adjustment:{" "}
                    {c.manual_adjustment.reason ||
                      c.manual_adjustment.rationale ||
                      "See saved source and reviewer evidence."}
                  </p>
                )}
                <ul className="flag-list">
                  {(player.flags || []).map((f) => (
                    <li key={f}>{f.replaceAll("_", " ")}</li>
                  ))}
                </ul>
              </section>
            </div>
            <h3>Recent scoring</h3>
            {recent.length ? (
              <div className="recent-bars">
                {recent
                  .slice(0, 5)
                  .reverse()
                  .map((r) => (
                    <div key={r.game_id}>
                      <strong>
                        {r.status === "dnp" ? "DNP" : number(r.pts)}
                      </strong>
                      <span
                        style={{
                          height: `${r.status === "dnp" ? 0 : Math.max(3, Math.min(90, (r.pts || 0) * 2))}px`,
                        }}
                      />
                      <small>{dateKey(r.game_time).slice(5, 10)}</small>
                    </div>
                  ))}
              </div>
            ) : (
              <p className="muted">
                No current-season finals captured for this player.
              </p>
            )}
          </>
        )}
        {tab === "Game log" && (
          <>
            <h3>Last 10 captured games</h3>
            <div className="table-scroll boxscore-scroll" role="region" aria-label="Recent game boxscores scroll area" tabIndex={0}>
              <table className="boxscore-table game-log-table" aria-label="Recent game boxscores">
                <thead>
                  <tr>
                    <th scope="col" className="game-date-cell">Date</th>
                    <th scope="col">Opponent</th>
                    <th scope="col">Role</th>
                    {BOX_SCORE_COLUMNS.map(column => (
                      <th scope="col" className="numeric" key={column.id} title={column.description}>
                        {column.label}
                      </th>
                    ))}
                    <th scope="col">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((r) => (
                    <tr key={r.game_id}>
                      <td className="game-date-cell">{dateKey(r.game_time)}</td>
                      <td>
                        <TeamMark id={r.opponent_id} />
                      </td>
                      <td>
                        {r.status === "dnp"
                          ? "DNP"
                          : r.starter === true
                            ? "Starter"
                            : r.starter === false
                              ? "Bench"
                              : "—"}
                      </td>
                      {BOX_SCORE_COLUMNS.map(column => (
                        <td className="numeric" key={column.id} data-stat={column.id} title={boxScoreTitle(r, column, "actual")}>
                          {boxScoreValue(r, column, "actual")}
                        </td>
                      ))}
                      <td>
                        {safeSource(r.source_url) && (
                          <a
                            href={r.source_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Boxscore ↗
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!recent.length && (
              <p className="empty">No current-season game log captured.</p>
            )}
            <p className="tiny">
              Final boxscores only. DNP rows remain explicit and do not count as
              games played. Times use MIN:SS only when the source records seconds;
              whole-minute sources show min. Dashes mean missing data or a
              percentage with no attempts.
            </p>
          </>
        )}
        {tab === "Shot chart" && <ShotChart chart={profile?.shot_chart} />}
        {tab === "Forecast history" && (
          <>
            <h3>Pregame forecast → actual result</h3>
            <p className="tiny">
              Latest saved live forecast per game, selected before inspecting
              outcomes. Previous versions remain in the immutable ledger.
            </p>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Game date</th>
                    <th>Status</th>
                    <th>Projected PTS</th>
                    <th>Actual PTS</th>
                    <th>Error</th>
                    <th>Saved</th>
                  </tr>
                </thead>
                <tbody>
                  {(profile?.prediction_history || []).map((r) => (
                    <tr key={r.run_id}>
                      <td>{dateKey(r.prediction.start_time)}</td>
                      <td>
                        <Status player={r.prediction} />
                      </td>
                      <td>{number(r.prediction.points_mean)}</td>
                      <td>{number(r.actual?.actual_points)}</td>
                      <td>{number(r.actual?.points_error)}</td>
                      <td>{stamp(r.saved_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!profile?.prediction_history?.length && (
              <p className="empty">
                No saved pregame predictions for this player yet.
              </p>
            )}
          </>
        )}
        <p className="profile-source tiny">
          Headshots: NBA.com (latest image; uniform may lag roster changes).
          Verified final boxscores · Updated {stamp(profile?.as_of)} · Observed stats and
          projections are separate.
        </p>
      </div>
    </dialog>
  );
}
