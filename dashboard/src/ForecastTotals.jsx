import React from "react";

const points = (value) => typeof value === "number" && Number.isFinite(value)
  ? new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value)
  : "—";
const minutes = (value) => typeof value === "number" ? Number(value.toFixed(1)) : "—";

export default function ForecastTotals({ game }) {
  const totals = game.forecast?.totals;
  if (!totals) return null;
  const teamName = (id) => id === game.away_team_id ? game.away_name : game.home_name;
  const label = (status) => status === "partial" ? "Partial" : status === "unavailable" ? "Unavailable" : "Projected";
  return (
    <section className="forecast-totals" aria-label="Forecast point totals">
      <h2>Projected points</h2>
      <div className="point-total-grid">
        {totals.teams.map((team) => (
          <article key={team.team_id} aria-label={`${teamName(team.team_id)} point total`}>
            <span className="total-kind">Team total</span>
            <h3>{teamName(team.team_id)}</h3>
            <strong className="point-total-value" data-testid="point-total">{points(team.points_mean)}</strong>
            <span className={`total-state ${team.status}`}>{label(team.status)}</span>
            <p>{minutes(team.projected_minutes)} / 240 minutes · {team.projected_players} projected</p>
            {!!team.withheld_player_ids.length && <p>{team.withheld_player_ids.length} withheld</p>}
          </article>
        ))}
        <article className="combined-point-total" aria-label="Game point total">
          <span className="total-kind">Combined teams</span>
          <h3>Game total</h3>
          <strong className="point-total-value" data-testid="point-total">{points(totals.points_mean)}</strong>
          <span className={`total-state ${totals.status}`}>{label(totals.status)}</span>
          <p>{game.forecast.stage === "final" ? "Final" : "Preliminary"} forecast · Regulation only</p>
        </article>
      </div>
      <p className="point-total-note">Sum of the selected forecast’s player projections, conditional on participation and the projected rotation. Partial totals exclude withheld players and unallocated minutes.</p>
    </section>
  );
}
