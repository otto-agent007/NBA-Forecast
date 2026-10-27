import React from "react";
import { number, stamp } from "./data";

const signed=value=>value == null ? "—" : `${value > 0 ? "+" : ""}${number(value)}`;
const metric=value=>value == null ? "—" : value.toFixed(2);
const share=value=>value == null ? "—" : `${number(value*100)}%`;
const label=value=>value ? value.replaceAll("_"," ") : "—";
const GROUPS=[["model","Model","What a forecast change could address"],["unexplained","Unexplained","A starter's short night with no report, recap or recorded exit"],["noise","Noise","Exits, blowouts, foul trouble and shooting the game itself explains"]];
const CLASS_GROUP={did_not_play:"model",role_shift:"model",role_shift_repeated:"model",volume:"model",exit_unexplained:"unexplained"};

function Participation({value,raw}) {
  if(!value?.count) return <p>{raw.count} projected players did not play: {number(raw.minutes)} forecast minutes and {number(raw.points)} points.</p>;
  return <p>{raw.count} projected players did not play ({number(raw.minutes)} forecast minutes). Weighted by participation probability that is {number(value.expected_minutes_lost)} expected minutes lost, against {number(value.expected_dnp_minutes)} the forecasts themselves expected to go to players who sit.
    {" "}{value.played} of {value.count} entered ({share(value.played_share)}) at a mean probability of {share(value.mean_probability)}; participation Brier {metric(value.brier)} against {metric(value.expected_brier)} expected.</p>;
}

function MissClasses({value}) {
  if(!value?.count) return null;
  return <details><summary>Where the error came from</summary><div className="table-scroll"><table aria-label="Miss classes"><thead><tr><th>Class</th><th>Players</th><th>Points error share</th><th>Minutes error share</th></tr></thead>
    <tbody>{GROUPS.flatMap(([key,name,detail])=>[<tr key={key} className="group-row"><td><strong>{name}</strong> <small>{detail}</small></td><td>{value.groups[key].count}</td><td>{share(value.groups[key].points_share)}</td><td>{share(value.groups[key].minutes_share)}</td></tr>,
      ...Object.entries(value.classes).filter(([name])=>(CLASS_GROUP[name] || "noise") === key).map(([name,row])=><tr key={name}><td>{label(name)}</td><td>{row.count}</td><td>{share(row.points_share)}</td><td>{share(row.minutes_share)}</td></tr>)])}</tbody>
  </table></div><p className="tiny">Shares of the night's absolute points and minutes error over {value.count} projected players with a final row. One night identifies questions; the repeating table below is what turns them into candidates for a gated change.</p></details>;
}

function Market({report}) {
  const summary=report.market,games=report.games.filter(game=>game.market);
  if(!summary || !games.length) return null;
  return <>
    <h4>Against the market</h4>
    <p>Margin MAE on {summary.games} games: model {metric(summary.model_margin_mae)}, close {metric(summary.close_margin_mae)}{summary.kalshi_games ? `, Kalshi ${metric(summary.kalshi_margin_mae)} (${summary.kalshi_games} games)` : ""}. {summary.lost_to_close.length ? `The model lost to the close by 3 or more in ${summary.lost_to_close.length}.` : "The model did not lose to the close by 3 or more in any game."}</p>
    <div className="table-scroll"><table aria-label="Margins against the market"><thead><tr><th>Game</th><th>Model</th><th>Close</th><th>Kalshi</th><th>Actual</th><th>Model − close error</th></tr></thead>
      <tbody>{games.map(game=>{const m=game.market;return <tr key={game.game_id}><td>{game.matchup}</td><td>{signed(m.margin.model)}</td><td>{signed(m.margin.close)}</td><td>{signed(m.margin.kalshi)}</td><td>{signed(m.margin.actual)}</td>
        <td>{m.margin_abs_error.close == null ? "—" : signed(m.margin_abs_error.model-m.margin_abs_error.close)}</td></tr>;})}</tbody></table></div>
    {summary.lost_to_close.map(game=><details key={game.game_id}><summary>{game.matchup}: lost {number(game.lost_by)} to the close</summary>
      <ul>{game.teams.map(team=><li key={team.team_id}><strong>{team.team}</strong> {signed(team.error)} points against the forecast: model-class misses {signed(team.model_points)}, noise {signed(team.noise_points)}, unprojected players {signed(team.unprojected_points)}.
        {team.drivers.length > 0 && <> Drivers: {team.drivers.map(d=>`${d.name} (${label(d.miss_class)}, ${signed(d.points_error)})`).join(", ")}.</>}</li>)}</ul>
    </details>)}
    <p className="tiny">Home margins, home minus away. The close is the last ESPN line before tip; Kalshi is its last pre-tip price.</p>
  </>;
}

function Repeating({recurrence}) {
  if(!recurrence) return null;
  return <>
    <h4>Repeating</h4>
    {recurrence.queue.length ? <div className="table-scroll"><table aria-label="Repeating misses"><thead><tr><th>Kind</th><th>Detail</th><th>Nights</th></tr></thead>
      <tbody>{recurrence.queue.map(item=><tr key={item.code+(item.player_id || item.team_id)}><td>{label(item.code.replace("recurring_",""))}</td><td>{item.title}</td><td>{item.evidence.map(e=>e.date).join(", ")}</td></tr>)}</tbody></table></div>
      : <p>No miss repeats across the last {recurrence.window.dates.length} saved nights.</p>}
    <p className="tiny">{recurrence.policy}</p>
  </>;
}
function Total({stage}) {
  if(!stage) return <>—</>;
  return <><strong>{number(stage.totals?.points_mean)}</strong><br/>
    <small>{stage.totals?.status}{stage.totals_origin === "derived_from_frozen_players" ? " · derived" : ""}{stage.overtime ? " · OT" : ""}</small></>;
}

function EvidenceTable({rows,onPlayer}) {
  return <div className="table-scroll"><table><thead><tr><th>Player</th><th>Class</th><th>Forecast MIN</th><th>Actual MIN</th><th>Forecast PTS</th><th>Actual PTS</th></tr></thead>
    <tbody>{rows.map(p=><tr key={p.game_id+p.player_id}><td><button className="daily-review-link" onClick={()=>onPlayer?.(p)}>{p.name} · {p.team}</button></td>
      <td title={(p.miss_evidence || []).map(e=>e.detail).join("; ")}>{label(p.miss_class)}</td>
      <td>{number(p.predicted.minutes)}</td><td>{number(p.actual?.minutes)}</td><td>{number(p.predicted.pts)}</td><td>{number(p.actual?.pts)}</td></tr>)}</tbody></table></div>;
}

export default function DailyReview({report,recurrence,date,onGame,onPlayer}) {
  if(!report) return <section className="performance-group" aria-label="Daily forecast review"><h3>Daily review</h3><p>No daily review saved for {date || "this date"}.</p></section>;
  const final=report.stages.final,metrics=final.projected,paired=report.paired_stages;
  return <section className="performance-group daily-review" aria-label="Daily forecast review">
    <h3>Daily review · {report.date}</h3>
    <p>{report.coverage.verified_finals} / {report.coverage.scheduled_games} final boxscores reconciled · {report.coverage.final_forecasts} final forecasts saved · {report.status === "complete" ? "All game results captured" : "Awaiting remaining results"}.</p>
    <div className="metric-band">
      <div><span>Player points MAE</span><strong>{metric(metrics.points_mae)}</strong></div>
      <div><span>Minutes MAE</span><strong>{metric(metrics.minutes_mae)}</strong></div>
      <div><span>80% range coverage</span><strong>{metrics.interval80_coverage == null ? "—" : `${number(metrics.interval80_coverage*100)}%`}</strong></div>
      <div><span>Matched projections</span><strong>{metrics.count}</strong></div>
    </div>
    <p>Projected players, including explicit DNPs. Missing actual rows stay unscored; out-player zeros are assessed separately.</p>
    <Participation value={final.participation} raw={final.projected_dnp}/>
    <p>{final.missing_projected_actuals ?? 0} projected player rows have no matching final row.</p>
    <MissClasses value={final.miss_classes}/>
    <p className="tiny">Models used: {Object.entries(final.model_versions).map(([version,count])=>`${version} (${count} games)`).join(" · ")}. These are the versions actually saved before tipoff.</p>
    <h4>Every game</h4>
    {!!final.margin_probabilities?.count && <p>Margin probabilities: {final.margin_probabilities.count} scored games · Winner Brier score {metric(final.margin_probabilities.home_win_brier)}. Lower scores mean smaller probability errors; this is a running evaluation, not a calibration claim.</p>}
    <div className="table-scroll"><table><thead><tr><th>Game</th><th>Final score</th><th>Preliminary total</th><th>Final total</th><th>Actual − final</th></tr></thead>
      <tbody>{report.games.map(game=><tr key={game.game_id}>
        <td><button className="daily-review-link" onClick={()=>onGame?.(game.game_id)}>{game.matchup}</button></td>
        <td>{game.score ? `${game.score[game.away_team_id]} – ${game.score[game.home_team_id]}` : "Pending"}</td>
        <td><Total stage={game.stages.preliminary}/></td><td><Total stage={game.stages.final}/></td>
        <td>{signed(game.stages.final?.game_total_error)}</td>
      </tr>)}</tbody></table></div>
    <p className="tiny">Positive errors mean the actual exceeded the forecast. Partial totals exclude unresolved players. Derived totals sum the saved player forecasts where no aggregate total was saved. Partial totals and overtime are excluded from complete regulation-total accuracy metrics.</p>
    <details><summary>Team totals and errors</summary><div className="table-scroll"><table aria-label="Daily team totals"><thead><tr><th>Game</th><th>Team</th><th>Forecast</th><th>Actual</th><th>Error</th><th>Coverage</th></tr></thead>
      <tbody>{report.games.flatMap(game=>(game.stages.final?.team_total_errors || []).map(team=><tr key={game.game_id+team.team_id}>
        <td>{game.matchup}</td><td>{team.team}</td><td>{number(team.forecast)}</td><td>{number(team.actual)}</td><td>{signed(team.error)}</td>
        <td>{team.status}{game.stages.final.totals_origin === "derived_from_frozen_players" ? " · derived" : ""}</td></tr>))}</tbody>
    </table></div></details>
    {final.complete_game_totals && <p>Saved complete regulation totals: {final.complete_game_totals.count} games, MAE {metric(final.complete_game_totals.mae)}; {final.complete_team_totals.count} teams, MAE {metric(final.complete_team_totals.mae)}. Inspect the sample counts before judging total accuracy.</p>}
    {paired.count > 0 && <p>On the same {paired.count} player outcomes, preliminary scoring MAE was {metric(paired.preliminary_mae)} and final MAE was {metric(paired.final_mae)}. {paired.changed_predictions} point projections changed between stages.</p>}
    <Market report={report}/>
    <h4>Priorities for future forecasts</h4>
    {report.improvement_queue.length ? <ol>{report.improvement_queue.map(item=><li key={item.code}>
      <strong>{item.title}.</strong> {item.action}
      {Array.isArray(item.evidence) && item.evidence.length > 0 && <details><summary>Player evidence ({item.evidence.length})</summary>
        <EvidenceTable rows={item.evidence} onPlayer={onPlayer}/>
      </details>}
    </li>)}</ol> : <p>No threshold-based follow-up questions identified.</p>}
    {report.explained_misses?.length > 0 && <details><summary>Explained by the game ({report.explained_misses.length})</summary>
      <EvidenceTable rows={report.explained_misses} onPlayer={onPlayer}/>
      <p className="tiny">Large minutes or attempt misses that an exit, a blowout, foul trouble or shooting variance accounts for. They stay scored but are not queued.</p>
    </details>}
    <Repeating recurrence={recurrence}/>
    <details><summary>Largest player scoring misses</summary><div className="table-scroll"><table><thead><tr><th>Player</th><th>Forecast</th><th>Actual</th><th>Error</th><th>Minutes effect</th><th>Attempt-volume effect</th><th>Conversion + FT effect</th></tr></thead>
      <tbody>{final.largest_misses.map(p=><tr key={p.game_id+p.player_id}><td><button className="daily-review-link" onClick={()=>onPlayer?.(p)}>{p.name} · {p.team}</button></td>
        <td>{number(p.predicted.pts)}</td><td>{number(p.actual.pts)}</td><td>{signed(p.feedback.points_error)}</td><td>{signed(p.feedback.minutes_effect)}</td><td>{signed(p.feedback.shot_volume_effect)}</td><td>{signed(p.feedback.conversion_and_ft_effect)}</td></tr>)}</tbody></table></div>
      <p className="tiny">Components sum to the point error where denominators are positive. They are accounting terms, not causal explanations; tiny predicted attempt counts can produce unstable components.</p>
    </details>
    <p>Detailed play-by-play available for {report.coverage.play_by_play_games} / {report.coverage.scheduled_games} games. Injury, foul and blowout explanations require supporting evidence.</p>
    {report.source_review?.length > 0 && <p>Roster-source follow-up: {report.source_review.map(row=>row.name).join(", ")} appeared in boxscores for multiple teams on this date. Verify those reported roster entries before inferring a role change.</p>}
    <p className="tiny">{report.interpretation} Updated {stamp(report.generated_at)}.</p>
  </section>;
}
