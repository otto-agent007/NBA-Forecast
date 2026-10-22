import React, { useRef, useEffect } from "react";
import { Icon } from "./Icons";
import { Status } from "./PlayerTable";
import { number, safeSource } from "./data";
export default function PlayerDetail({ player, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current.showModal();
    return () => ref.current?.close();
  }, []);
  const source = safeSource(player.status_source_url);
  const c = player.components || {};
  return (
    <dialog
      ref={ref}
      aria-labelledby="player-title"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <div className="dialog-heading">
        <div>
          <p className="muted">{player.team_code || "Player evidence"}</p>
          <h2 id="player-title">{player.name}</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close player details"
          onClick={onClose}
        >
          <Icon name="close" />
        </button>
      </div>
      <Status player={player} />
      <p className="reason">{player.reason}</p>
      <div className="detail-metrics">
        {[
          ["Minutes", player.expected_minutes],
          ["Shot attempts", player.expected_fga],
          ["Points", player.points_mean],
        ].map(([k, v]) => (
          <div key={k}>
            <span>{k}</span>
            <strong>{number(v)}</strong>
          </div>
        ))}
      </div>
      <h3>Uncertainty</h3>
      <p>
        {player.status === "projected"
          ? `${number(player.points_lower80)}–${number(player.points_upper80)} points: heuristic 80% interval, not yet calibrated.`
          : "No scoring interval until a supported projection is available."}
      </p>
      <h3>Evidence & role</h3>
      <p>
        {player.history_games == null
          ? "Regular-season history unavailable."
          : `${player.history_games} eligible regular-season games.`}{" "}
        {c.opportunities
          ? `Baseline minutes before adjustment: ${number(c.opportunities.minutes_before_adjustment)}.`
          : "No opportunity estimate shown for this player."}
      </p>
      {source && (
        <a href={source} target="_blank" rel="noreferrer">
          Open availability source <Icon name="arrow" size={16} />
        </a>
      )}
      <ul className="flag-list">
        {(player.flags || []).map((f) => (
          <li key={f}>{f.replaceAll("_", " ")}</li>
        ))}
      </ul>
      {!player.flags?.length && (
        <p className="muted">No additional flags recorded.</p>
      )}
    </dialog>
  );
}
