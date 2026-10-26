import React, { useState } from "react";
export default function ShotChart({ chart }) {
  const [result, setResult] = useState("all");
  const shots = chart?.shots || [],
    located = shots.filter((s) => Number.isFinite(s.x) && Number.isFinite(s.y));
  const half = located.filter(
    (s) => Math.abs(s.x) <= 250 && s.y >= -52.5 && s.y <= 417.5,
  );
  const shown = half.filter(
    (s) => result === "all" || s.made === (result === "made"),
  );
  const made = shots.filter((s) => s.made).length;
  const coverage = chart?.coverage || {};
  const verifiedZero = coverage.complete === true && coverage.games > 0 && coverage.attempts === 0;
  return (
    <section className="shot-panel">
      <div className="section-heading">
        <h3>Where the shots came from</h3>
        <select
          aria-label="Shot result"
          value={result}
          onChange={(e) => setResult(e.target.value)}
        >
          <option value="all">All attempts</option>
          <option value="made">Made shots</option>
          <option value="missed">Missed shots</option>
        </select>
      </div>
      <p className="muted">
        Actual regular-season shots · {Number.isFinite(coverage.played_games)
          ? `${coverage.games || 0}/${coverage.played_games} played games captured`
          : `${coverage.games || 0} captured games`}
        {coverage.games > 0 ? ` · ${made}/${shots.length} FG` : ""}
      </p>
      {coverage.missing_games > 0 && <p className="warning" aria-label="Shot coverage">
        Shot coverage is incomplete: {coverage.missing_games} played {coverage.missing_games === 1 ? "game awaits" : "games await"} verified shot data.
      </p>}
      {verifiedZero ? <div className="empty">
        <h3>No field-goal attempts</h3>
        <p>{coverage.games} played {coverage.games === 1 ? "game verified" : "games verified"} with zero attempts.</p>
      </div> : !located.length ? (
        <div className="empty">
          <h3>Shot locations unavailable</h3>
          <p>
            {chart?.missing_reason ||
              "No verified coordinates have been captured for this player."}
          </p>
          <p>Game logs and season averages remain available independently.</p>
        </div>
      ) : (
        <>
          <svg
            viewBox="0 0 500 475"
            className="shot-court"
            role="img"
            aria-label={`${shown.length} observed shot locations, green circles made, orange crosses missed`}
          >
            <rect
              x="1"
              y="1"
              width="498"
              height="472"
              fill="#f7f9fc"
              stroke="#a6b6cc"
            />
            <g fill="none" stroke="#b3c0d1" strokeWidth="1.5">
              <rect x="170" y="282.5" width="160" height="190" />
              <circle cx="250" cy="282.5" r="60" />
              <path d="M30 472.5V330.5 A237.5 237.5 0 0 1 470 330.5V472.5" />
              <path d="M210 420 A40 40 0 0 1 290 420" />
              <path d="M220 432.5H280" />
              <circle cx="250" cy="420" r="7.5" />
              <path d="M190 1 A60 60 0 0 0 310 1" />
            </g>
            {shown.map((s) => (
              <g
                key={`${s.game_id}-${s.action_number}`}
                transform={`translate(${s.x + 250} ${420 - s.y})`}
              >
                <title>
                  {s.made ? "Made" : "Missed"} {s.points}PT · period{" "}
                  {s.period ?? "—"} · {s.game_id}
                </title>
                {s.made ? (
                  <circle r="4" fill="#16856c" opacity=".8" />
                ) : (
                  <path
                    d="M-3-3 3 3M-3 3 3-3"
                    stroke="#d57a36"
                    strokeWidth="2"
                  />
                )}
              </g>
            ))}
          </svg>
          <p className="tiny">
            Green circles: made · Orange crosses: missed.{" "}
            {shots.length - located.length} attempts lack coordinates.{" "}
            {located.length - half.length} shots outside this half-court are
            excluded from the plot. {chart?.coverage?.located_attempts ?? 0}/
            {chart?.coverage?.attempts ?? 0} captured attempts have locations.
          </p>
        </>
      )}
    </section>
  );
}
