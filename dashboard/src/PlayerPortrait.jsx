import React, { useState } from "react";
import { TeamMark } from "./Icons";
export default function PlayerPortrait({ player, large = false }) {
  const [failed, setFailed] = useState(false);
  const id = String(player.player_id || "");
  const valid = /^\d+$/.test(id);
  return (
    <span className={`player-portrait ${large ? "portrait-large" : ""}`}>
      {!failed && valid ? (
        <img
          src={`https://cdn.nba.com/headshots/nba/latest/${large ? "1040x760" : "260x190"}/${id}.png`}
          alt={`${player.name} headshot`}
          loading={large ? "eager" : "lazy"}
          decoding="async"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="portrait-fallback" aria-label="Headshot unavailable">
          <TeamMark id={player.team_id} code={player.team_code} />
          {large && <small>Photo unavailable</small>}
        </span>
      )}
    </span>
  );
}
