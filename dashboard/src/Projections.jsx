import React from "react";
import PlayerTable from "./PlayerTable";
export default function Projections(props) {
  const supported = props.players.filter((p) => p.status === "projected");
  const review = props.players.filter(
    (p) => p.status === "needs_review" || p.status === "not_forecast",
  );
  return (
    <section className="projections">
      <div className="projection-intro">
        <div>
          <span className="eyebrow">YOUR DAILY PROJECTION BOARD</span>
          <h2>The whole slate. Every player.</h2>
          <p>
            Compare full projected boxscores and open a player for the evidence
            behind their outlook.
          </p>
        </div>
        <span className="scoring-tag">Full NBA boxscore</span>
      </div>
      <div className="projection-summary">
        <div>
          <span>Players on slate</span>
          <strong>{props.players.length}</strong>
        </div>
        <div>
          <span>With projections</span>
          <strong>{supported.length}</strong>
        </div>
        <div>
          <span>Awaiting evidence</span>
          <strong>{review.length}</strong>
        </div>
        <div>
          <span>Projection window</span>
          <strong className="text-value">Pregame</strong>
        </div>
      </div>
      <PlayerTable {...props} title="Player projections" expanded />
      <p className="projection-disclaimer">
        Boxscore projections use saved regular-season evidence. Unsupported
        categories stay blank. MIN:SS represents estimated playing time.
        Nominal 80% scoring intervals carry their calibration or heuristic
        evidence in player details.
      </p>
    </section>
  );
}
