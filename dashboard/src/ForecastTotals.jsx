import React from "react";

const points = (value) => typeof value === "number" && Number.isFinite(value)
  ? new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value)
  : "—";
const minutes = (value) => typeof value === "number" ? Number(value.toFixed(1)) : "—";
const unresolved = (team) => team.unresolved_player_ids?.length ?? team.withheld_player_ids.length;
const signed = (value, digits = 1) => typeof value === "number" && Number.isFinite(value) ? `${value > 0 ? "+" : ""}${value.toFixed(digits)}` : "—";
const odds = (value) => typeof value === "number" ? (value > 0 ? `+${value}` : `${value}`) : "—";

export function MarketLines({ game }) {
  const providers = game.market?.providers;
  if (!providers) return null;
  const totals = game.forecast?.totals;
  const home = totals?.teams?.find((t) => t.team_id === game.home_team_id);
  const away = totals?.teams?.find((t) => t.team_id === game.away_team_id);
  const modelMargin = home?.points_mean != null && away?.points_mean != null ? home.points_mean - away.points_mean : null;
  const modelTotal = totals?.status === "complete" ? totals.points_mean : null;
  return (
    <div className="market-lines" aria-label="Market lines">
      <h3>Market lines</h3>
      {Object.values(providers).map((item) => {
        const spread = item.latest?.spread?.home?.current_line;
        const total = item.latest?.total?.over?.current_line;
        return (
          <p key={item.provider}>
            <strong>{item.provider}</strong>: {game.home_tricode} {signed(spread)} · total {typeof total === "number" ? total : "—"} ·
            ML {odds(item.latest?.moneyline?.home?.current_odds)} / {odds(item.latest?.moneyline?.away?.current_odds)}
            {modelMargin != null && typeof spread === "number" && <> · model margin {signed(modelMargin)} ({game.home_tricode}), edge {signed(modelMargin + spread)}</>}
            {modelTotal != null && typeof total === "number" && <> · model total {modelTotal.toFixed(1)}, edge {signed(modelTotal - total)}</>}
            <small> captured {new Date(item.latest_captured_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</small>
          </p>
        );
      })}
      <small>Archived from ESPN’s game summary at the forecast cadence; the last capture before tipoff is the closing line. An edge is the model minus the market, not a recommendation.</small>
    </div>
  );
}

export default function ForecastTotals({ game }) {
  const totals = game.forecast?.totals;
  if (!totals) return game.market ? <section className="forecast-totals" aria-label="Forecast point totals"><MarketLines game={game} /></section> : null;
  const teamName = (id) => id === game.away_team_id ? game.away_name : game.home_name;
  const label = (status) => status === "partial" ? "Partial" : status === "unavailable" ? "Unavailable" : "Projected";
  return (
    <section className="forecast-totals" aria-label="Forecast point totals">
      <h2>Projected points</h2>
      <MarketLines game={game} />
      <div className="point-total-grid">
        {totals.teams.map((team) => (
          <article key={team.team_id} aria-label={`${teamName(team.team_id)} point total`}>
            <span className="total-kind">Team total</span>
            <h3>{teamName(team.team_id)}</h3>
            <strong className="point-total-value" data-testid="point-total">{points(team.points_mean)}</strong>
            <span className={`total-state ${team.status}`}>{label(team.status)}</span>
            <p>{minutes(team.projected_minutes)} / 240 minutes · {team.projected_players} projected</p>
            {team.residual_minutes > 0 && <p>{minutes(team.residual_minutes)} unallocated minutes at the replacement rate · {points(team.residual_points)} pts</p>}
            {!!team.cold_start_player_ids?.length && <p>{team.cold_start_player_ids.length} roster {team.cold_start_player_ids.length === 1 ? "player" : "players"} without playing or scoring evidence{team.residual_minutes > 0 ? " · covered by the replacement minutes" : ""}</p>}
            {!!unresolved(team) && <p>{unresolved(team)} withheld · Availability or role review</p>}
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
      <p className="point-total-note">Sum of the selected forecast’s player projections weighted by each player’s probability of playing, plus any regulation minutes the rotation leaves unallocated at a replacement scoring rate; individual projections are conditional on playing. Players without playing or scoring evidence stay unprojected and are covered by those minutes. Unresolved availability and roles are marked Partial.</p>
    </section>
  );
}
