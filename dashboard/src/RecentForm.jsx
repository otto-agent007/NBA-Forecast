import React from "react";
import { number } from "./data";
export default function RecentForm({ profile }) {
  const games = (profile?.recent_games || [])
    .filter((r) => r.status === "played")
    .slice(0, 5);
  if (!games.length) return null;
  const stats = [
    ["pts", "PTS"],
    ["reb", "REB"],
    ["ast", "AST"],
    ["minutes", "MIN"],
    ["fga", "FGA"],
    ["fg3a", "3PA"],
  ];
  return (
    <section className="recent-form">
      <div className="section-heading">
        <h3>Recent form</h3>
        <span className="count">
          {games.length} captured game{games.length === 1 ? "" : "s"}
        </span>
      </div>
      <p className="tiny">
        Last five appearances within the captured 10-game log · DNPs excluded.
        Differences compare with current-season averages; these are
        observations, not forecast adjustments.
      </p>
      <div className="form-grid">
        {stats.map(([key, label]) => {
          const values = games
            .map((g) => g[key])
            .filter((v) => typeof v === "number" && Number.isFinite(v));
          const mean = values.length
            ? values.reduce((a, b) => a + b, 0) / values.length
            : null;
          const baseline = profile.current_season?.averages?.[key];
          const delta =
            mean != null && baseline != null ? mean - baseline : null;
          return (
            <div key={key}>
              <span>{label}</span>
              <strong>{number(mean)}</strong>
              <small>
                {delta == null
                  ? "Comparison unavailable"
                  : `${delta > 0 ? "+" : ""}${number(delta)} vs season`}
              </small>
              <small>{values.length} games with data</small>
            </div>
          );
        })}
      </div>
      {games.length < 5 && (
        <p className="sample-note">
          Early sample: {games.length} appearance{games.length === 1 ? "" : "s"}
          . An unusual shooting night does not replace the established baseline.
        </p>
      )}
    </section>
  );
}
