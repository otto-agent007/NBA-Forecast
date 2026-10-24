import React from "react";
import { number, stamp } from "./data";

const signed=value=>value == null ? "—" : `${value > 0 ? "+" : ""}${number(value)}`;
const metric=value=>value == null ? "—" : value.toFixed(2);
function Total({stage}) {
  if(!stage) return <>—</>;
  return <><strong>{number(stage.totals?.points_mean)}</strong><br/>
    <small>{stage.totals?.status}{stage.totals_origin === "derived_from_frozen_players" ? " · derived" : ""}{stage.overtime ? " · OT" : ""}</small></>;
}

export default function DailyReview({report,date,onGame,onPlayer}) {
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
    <p>{final.projected_dnp.count} projected players did not play: {number(final.projected_dnp.minutes)} forecast minutes and {number(final.projected_dnp.points)} points. {final.missing_projected_actuals ?? 0} projected player rows have no matching final row.</p>
    <p className="tiny">Models used: {Object.entries(final.model_versions).map(([version,count])=>`${version} (${count} games)`).join(" · ")}. These are the versions actually saved before tipoff.</p>
    <h4>Every game</h4>
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
    <h4>Priorities for future forecasts</h4>
    {report.improvement_queue.length ? <ol>{report.improvement_queue.map(item=><li key={item.code}>
      <strong>{item.title}.</strong> {item.action}
      {Array.isArray(item.evidence) && item.evidence.length > 0 && <details><summary>Player evidence ({item.evidence.length})</summary>
        <div className="table-scroll"><table><thead><tr><th>Player</th><th>Forecast MIN</th><th>Actual MIN</th><th>Forecast PTS</th><th>Actual PTS</th></tr></thead>
          <tbody>{item.evidence.map(p=><tr key={p.game_id+p.player_id}><td><button className="daily-review-link" onClick={()=>onPlayer?.(p)}>{p.name} · {p.team}</button></td>
            <td>{number(p.predicted.minutes)}</td><td>{number(p.actual.minutes)}</td><td>{number(p.predicted.pts)}</td><td>{number(p.actual.pts)}</td></tr>)}</tbody></table></div>
      </details>}
    </li>)}</ol> : <p>No threshold-based follow-up questions identified.</p>}
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
