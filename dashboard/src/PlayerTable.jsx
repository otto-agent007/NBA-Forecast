import PlayerPortrait from "./PlayerPortrait";
import React, { useEffect, useMemo, useState } from "react";
import { filterPlayers, groupPlayers, number, statusLabel } from "./data";
import { BOX_SCORE_COLUMNS, boxScoreTitle, boxScoreValue } from "./boxscore";
import { Icon, TeamMark } from "./Icons";
import ColumnScroll from "./ColumnScroll";
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
  useEffect(() => {
    // The slate changed under the filter: a team that is gone cannot hide every row.
    if (teamId !== "all" && !allTeams.some((t) => t.team_id === teamId)) setTeamId("all");
  }, [allTeams, teamId]);
  const rows = useMemo(
    () => filterPlayers(players, query, filter, sort).filter((p) => teamId === "all" || p.team_id === teamId),
    [players, query, filter, sort, teamId],
  );
  const teams = useMemo(() => groupPlayers(rows, players), [rows, players]);
  const columns = BOX_SCORE_COLUMNS.length + 3 + (expanded ? 2 : 0);
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
      <p className="boxscore-hint">Projected boxscore · Move the More stats slider at the bottom of the screen (or shift-scroll) for shooting splits and all stats; the player column stays put. MIN:SS is an estimate.</p>
      <ColumnScroll label={title}>
        <table className="boxscore-table" aria-label={`${title} full boxscore`}>
          <thead>
            <tr>
              <th scope="col" className="player-cell">Player</th>
              {expanded && <th scope="col">Matchup</th>}
              <th scope="col">Status</th>
              <th scope="col" className="numeric observed-ppg">Season PPG</th>
              {BOX_SCORE_COLUMNS.map(column => <th key={column.id} scope="col" className="numeric" title={column.description} aria-label={column.label}>
                {column.id === "pts" ? <button
                    className="sort"
                    aria-label="Sort by projected points"
                    aria-pressed={sort === "points"}
                    onClick={() => setSort(sort === "points" ? "rotation" : "points")}
                  >PTS {sort === "points" ? "↓" : ""}</button>
                  : column.label}
              </th>)}
              {expanded && (
                <th scope="col" className="numeric">
                  80% range
                </th>
              )}
            </tr>
          </thead>
          {teams.map((team) => (
          <tbody key={team.team_id} data-team-id={team.team_id}>
            <tr className="team-heading"><th colSpan={columns} scope="rowgroup">
              <div className="team-heading-content"><TeamMark id={team.team_id} code={team.team_code} />
              <h3>{team.team_name || team.team_code}</h3><span>{team.players.length} players · Projected five, unconfirmed</span></div>
            </th></tr>
            {team.unavailable > 0 && <tr className="coverage-warning"><td colSpan={columns}><span className="boxscore-row-note">Availability unresolved for {team.unavailable} players in this saved forecast. Unsupported forecasts are withheld; captured season averages remain visible.</span></td></tr>}
            {team.unfilled_slots > 0 && !query.trim() && filter === "all" && <tr className="coverage-warning"><td colSpan={columns}><span className="boxscore-row-note">{team.unfilled_slots} of 5 starting slots await sufficient evidence. No lineup has been confirmed.</span></td></tr>}
            {team.groups.map((group) => (
            <React.Fragment key={group.key}>
            <tr className="role-heading"><th colSpan={columns} scope="rowgroup"><span className="boxscore-row-note">{group.label}</span></th></tr>
            {group.players.map((p) => (
              <tr key={p.player_id} className="player-row" data-role={group.key}>
                <td className="player-cell">
                  <button className="player-button" onClick={() => onSelect(p)}>
                    <PlayerPortrait key={p.player_id} player={p} />
                    <span>
                      {p.name}
                      <small title={p.history_games != null ? `${p.history_games} games of evidence` : undefined}>
                        {p.team_code || "NBA"}
                        {p.history_games != null
                          ? ` · ${p.history_games} games`
                          : ""}
                        {p.status === "projected" && typeof p.participation_probability === "number" && p.participation_probability < 0.95
                          ? ` · ${Math.round(p.participation_probability * 100)}% to play`
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
                {BOX_SCORE_COLUMNS.map(column => <td key={column.id} data-stat={column.id}
                  className={`numeric ${column.id === "pts" ? `points ${p.status === "projected" ? "supported" : ""}` : ""}`}
                  title={boxScoreTitle(p, column)}>
                  {boxScoreValue(p, column)}
                </td>)}
                {expanded && (
                  <td className="numeric range">
                    {p.status === "projected"
                      ? `${number(p.points_lower80)}–${number(p.points_upper80)}`
                      : "—"}
                  </td>
                )}
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
                setTeamId("all");
              }}
            >
              Clear filters
            </button>
          </div>
        )}
      </ColumnScroll>
      <div className="table-note">
        <Icon name="info" />
        <span>
          Dashes mean missing estimates or an undefined shooting percentage.
          Shooting percentages are undefined with no attempts. Conditional projections use a
          verified report not-listed assumption; participation is not confirmed.
          {" "}Each team has five projected starting slots, selected before filters.
          Upcoming lineups remain unconfirmed. Season PPG is an observed average,
          not a forecast; its tooltip identifies the season and captured games.
        </span>
      </div>
    </section>
  );
}
