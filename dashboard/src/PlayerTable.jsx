import PlayerPortrait from "./PlayerPortrait";
import React, { useMemo } from "react";
import { filterPlayers, number, statusLabel } from "./data";
import { Icon } from "./Icons";
export function Status({ player }) {
  return (
    <span
      className={`status ${player.status === "out" ? "out" : player.status === "projected" ? "projected" : statusLabel(player) === "Needs review" || player.status === "not_forecast" ? "review" : "uncertain"}`}
    >
      <span className="status-dot" />
      {statusLabel(player)}
    </span>
  );
}
export default function PlayerTable({
  players,
  query,
  setQuery,
  filter,
  setFilter,
  sort,
  setSort,
  onSelect,
  title = "Player outlook",
  expanded = false,
}) {
  const rows = useMemo(
    () => filterPlayers(players, query, filter, sort),
    [players, query, filter, sort],
  );
  return (
    <section className="player-section">
      <div className="section-heading">
        <h2>{title}</h2>
        <span className="count">{rows.length} players</span>
      </div>
      <div className="table-toolbar">
        <div className="filters" aria-label="Player status filter">
          {[
            ["all", "All players"],
            ["review", "Needs review"],
            ["out", "Out"],
            ["projected", "Projected"],
          ].map(([v, t]) => (
            <button
              key={v}
              className={filter === v ? "active" : ""}
              aria-pressed={filter === v}
              onClick={() => setFilter(v)}
            >
              {t}
            </button>
          ))}
        </div>
        <label className="search">
          <Icon name="search" />
          <input
            aria-label="Search players"
            placeholder="Search players"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Player</th>
              {expanded && <th scope="col">Matchup</th>}
              <th scope="col">Status</th>
              <th scope="col" className="numeric">
                Min
              </th>
              <th scope="col" className="numeric">
                FGA
              </th>
              <th scope="col" className="numeric">
                <button
                  className="sort"
                  aria-label="Sort by projected points"
                  aria-pressed={sort === "points"}
                  onClick={() => setSort(sort === "points" ? "name" : "points")}
                >
                  Points {sort === "points" ? "↓" : ""}
                </button>
              </th>
              {expanded && (
                <th scope="col" className="numeric">
                  80% range
                </th>
              )}
              {expanded &&
                ["REB", "AST", "3PM", "TO", "STL", "BLK"].map((k) => (
                  <th key={k} scope="col" className="numeric">
                    {k}
                  </th>
                ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.player_id}>
                <td>
                  <button className="player-button" onClick={() => onSelect(p)}>
                    <PlayerPortrait key={p.player_id} player={p} />
                    <span>
                      {p.name}
                      <small>
                        {p.team_code || "NBA"}
                        {p.history_games != null
                          ? ` · ${p.history_games} games of evidence`
                          : ""}
                      </small>
                    </span>
                  </button>
                </td>
                {expanded && (
                  <td className="matchup-cell">{p.matchup || "—"}</td>
                )}
                <td>
                  <Status player={p} />
                </td>
                <td className="numeric">{number(p.expected_minutes)}</td>
                <td className="numeric">{number(p.expected_fga)}</td>
                <td
                  className={`numeric points ${p.status === "projected" ? "supported" : ""}`}
                >
                  {number(p.points_mean)}
                </td>
                {expanded && (
                  <td className="numeric range">
                    {p.status === "projected"
                      ? `${number(p.points_lower80)}–${number(p.points_upper80)}`
                      : "—"}
                  </td>
                )}
                {expanded &&
                  [
                    "expected_reb",
                    "expected_ast",
                    "expected_fg3m",
                    "expected_tov",
                    "expected_stl",
                    "expected_blk",
                  ].map((k) => (
                    <td key={k} className="numeric">
                      {number(p[k])}
                    </td>
                  ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <div className="empty small">
            <h3>No players match</h3>
            <p>Try a different name or status filter.</p>
            <button
              className="text-button"
              onClick={() => {
                setQuery("");
                setFilter("all");
              }}
            >
              Clear filters
            </button>
          </div>
        )}
      </div>
      <div className="table-note">
        <Icon name="info" />
        <span>
          Dashes mean unsupported estimates. Conditional projections use a
          complete-report not-listed assumption; participation is not confirmed.
        </span>
      </div>
    </section>
  );
}
