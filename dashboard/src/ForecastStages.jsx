import React from "react";
import { stamp, timeLabel } from "./data";

export default function ForecastStages({ game, selected, onSelect }) {
  const stages = { ...(game.forecasts || {}) };
  if (game.forecast) stages[game.forecast.stage] ||= game.forecast;
  const view = stages[selected];
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
    </section>
  );
}
