import React from "react";
import { stamp, timeLabel, safeSource } from "./data";

export default function ForecastStages({ game, selected, onSelect }) {
  const stages = { ...(game.forecasts || {}) };
  if (game.forecast) stages[game.forecast.stage] ||= game.forecast;
  const view = stages[selected];
  const audit = game.preflight;
  const teamName = (id) => id === game.home_team_id ? game.home_name : id === game.away_team_id ? game.away_name : "Inputs";
  const update = game.availability_update;
  const recoveredAfterAttempt = update?.captured_after_tipoff && audit?.checked_at && Date.parse(update.captured_at) > Date.parse(audit.checked_at);
  return (
    <section className="forecast-stages" aria-label="Forecast checkpoints">
      <div className="forecast-stage-buttons">
        {[['preliminary', 'Preliminary forecast'], ['final', 'Final forecast']].map(([key, label]) => (
          <button key={key} aria-label={label} aria-pressed={selected === key} disabled={!stages[key]} onClick={() => onSelect(key)}>
            {label}<small>{stages[key] ? stamp(stages[key].created_at) : key === 'final' && game.start_time
              ? `Due ${timeLabel(new Date(Date.parse(game.start_time) - 1800000).toISOString())}` : 'Awaiting saved run'}</small>
          </button>
        ))}
      </div>
      <p className="forecast-stage-summary">{view
        ? `${selected === 'final' ? 'Final' : 'Preliminary'} forecast · Saved ${stamp(view.created_at)}. Earlier runs remain available.`
        : 'Preliminary forecasts are saved before the final T−30 checkpoint. Unsupported estimates stay blank.'}</p>
      {update && <div className={`availability-update ${update.status === 'superseded' ? 'superseded' : ''}`} aria-label="Latest injury information">
        <strong>Latest injury information</strong>
        {update.teams.map((team) => <p key={team.team_id}>{teamName(team.team_id)}: <strong>No injuries reported</strong> · ESPN game report · Captured {stamp(team.retrieved_at)} {safeSource(team.source_url) && <a href={team.source_url} target="_blank" rel="noreferrer">Source ↗</a>}</p>)}
        {!update.teams.length ? <p>The previous zero-injury listing has been superseded. No current verified zero-injury report is available; check the latest official report.</p> : <small>{update.captured_after_tipoff ? 'Recovered after tipoff. The earlier saved forecast retains its original inputs; this update cannot create a backdated forecast.' : 'Game-specific zero-injury listing. Participation and starting lineups remain unconfirmed; forecasts use their own saved evidence.'}</small>}
      </div>}
      <div className={`preflight-status ${audit?.status || "unverified"}`} role="status">
        <strong>{audit?.status === "blocked" ? recoveredAfterAttempt ? "Earlier forecast attempt was blocked" : "New forecast blocked by cross-check" : audit?.status === "passed" ? "Latest input cross-check passed" : "Awaiting input cross-check"}</strong>
        {audit && <span>Checked {stamp(audit.checked_at)}</span>}
        {audit?.issues?.length > 0 && <ul>{audit.issues.map((issue, i) => <li key={i}>{teamName(issue.team_id)}: {issue.detail}</li>)}</ul>}
        <p>{view?.preflight?.status === "passed" ? "This saved forecast passed its preflight checks. Starting fives are projections, not confirmed lineups." : view ? "This saved forecast predates the cross-check gate and is unverified. New runs require matching schedule, roster and verified injury evidence for both teams." : "No forecast saved. New runs require matching schedule, roster and verified injury evidence for both teams. Starting fives remain unconfirmed."}</p>
        {audit?.sources?.length > 0 && <small>{audit.sources.length} archived independent source captures · ESPN schedule, rosters and any supplemental injury reports, checked against NBA evidence.</small>}
      </div>
    </section>
  );
}
