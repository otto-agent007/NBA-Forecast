import PlayerPortrait from "./PlayerPortrait";
import React, { useMemo, useState } from "react";
import { filterPlayers, groupPlayers, number, statusLabel } from "./data";
import { Icon, TeamMark } from "./Icons";
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
  const [teamId, setTeamId] = useState("all");
  const allTeams = useMemo(() => groupPlayers(players), [players]);
  const rows = useMemo(
    () => filterPlayers(players, query, filter, sort).filter((p) => teamId === "all" || p.team_id === teamId),
    [players, query, filter, sort, teamId],
  );
  const teams = useMemo(() => groupPlayers(rows, players), [rows, players]);
  const columns = expanded ? 14 : 6;
  return (
    <section className="player-section">
      <div className="section-heading">
        <h2>{title}</h2>
        <span className="count">{rows.length} players</span>
      </div>
      {allTeams.length === 2 && <div className="team-selector" aria-label="Team outlook selector">
        <button aria-pressed={teamId === "all"} onClick={() => setTeamId("all")}>Both teams</button>
        {allTeams.map((team) => <button key={team.team_id} aria-label={`View ${team.team_name || team.team_code}`} aria-pressed={teamId === team.team_id} onClick={() => setTeamId(team.team_id)}>
          <TeamMark id={team.team_id} code={team.team_code} />{team.team_name || team.team_code}<small>{team.total} players</small>
        </button>)}
      </div>}
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
              <th scope="col" className="numeric">Season PPG</th>
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
                  onClick={() => setSort(sort === "points" ? "rotation" : "points")}
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
          {teams.map((team) => (
          <tbody key={team.team_id} data-team-id={team.team_id}>
            <tr className="team-heading"><th colSpan={columns} scope="rowgroup">
              <TeamMark id={team.team_id} code={team.team_code} />
              <h3>{team.team_name || team.team_code}</h3><span>{team.players.length} players · Projected five, unconfirmed</span>
            </th></tr>
            {team.unavailable > 0 && <tr className="coverage-warning"><td colSpan={columns}>Availability unresolved for {team.unavailable} players. Unsupported forecasts are withheld; captured season averages remain visible.</td></tr>}
            {team.unfilled_slots > 0 && !query.trim() && filter === "all" && <tr className="coverage-warning"><td colSpan={columns}>{team.unfilled_slots} of 5 starting slots await sufficient evidence. No lineup has been confirmed.</td></tr>}
            {team.groups.map((group) => (
            <React.Fragment key={group.key}>
            <tr className="role-heading"><th colSpan={columns} scope="rowgroup">{group.label}</th></tr>
            {group.players.map((p) => (
              <tr key={p.player_id} className="player-row" data-role={group.key}>
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
                <td className="numeric observed-ppg" title={p.observed_stats ? `${p.observed_stats.season} · ${p.observed_stats.games} captured games · Observed as of ${p.observed_stats.as_of || "saved capture"}; not a game forecast` : "No captured season statistics"}>{number(p.observed_stats?.points_average)}</td>
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
            </React.Fragment>
            ))}
          </tbody>
          ))}
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
          {" "}Each team has five projected starting slots, selected before filters.
          Upcoming lineups remain unconfirmed. Season PPG is an observed average,
          not a forecast; its tooltip identifies the season and captured games.
        </span>
      </div>
    </section>
  );
}
