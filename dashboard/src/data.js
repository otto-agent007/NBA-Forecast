export const ZONE = "America/Los_Angeles";
export const dateKey = (time, zone = ZONE) =>
  time
    ? new Intl.DateTimeFormat("en-CA", {
        timeZone: zone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(new Date(time))
    : null;
export const timeLabel = (time) =>
  time
    ? new Intl.DateTimeFormat("en-US", {
        timeZone: ZONE,
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(new Date(time))
    : "TBD";
export const longDate = (date) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(date + "T12:00:00Z"));
export const stamp = (time) =>
  time
    ? new Intl.DateTimeFormat("en-US", {
        timeZone: ZONE,
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(new Date(time))
    : "Not captured";
export const number = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? Number(value.toFixed(1)).toString()
    : "—";
export const gameDay = (game) =>
  game.start_time
    ? dateKey(game.start_time)
    : dateKey(game.source_start_time, "America/New_York");
export function initialDay(data, now = new Date()) {
  const today = dateKey(now),
    days = [...new Set(data.games.map(gameDay).filter(Boolean))].sort();
  // Start with the next actionable slate, then today or the most recent calendar day.
  const upcoming = data.games
    .filter(
      (g) =>
        g.status === "scheduled" &&
        g.start_time &&
        new Date(g.start_time) > now,
    )
    .sort((a, b) => a.start_time.localeCompare(b.start_time));
  return upcoming.length
    ? gameDay(upcoming[0])
    : days.includes(today)
      ? today
      : days.at(-1) || today;
}
export function gamePlayers(data, game) {
  if (!game) return [];
  const predictions = new Map(
    (game.forecast?.predictions || []).map((p) => [p.player_id, p]),
  );
  const reviewIds = new Set(
    (game.forecast?.review_queue || []).map((p) => p.player_id),
  );
  const members = data.players.filter((p) =>
    p.roster_membership !== "historical_appearance" && [game.home_team_id, game.away_team_id].includes(p.team_id),
  );
  const roster = new Map(members.map((p) => [p.player_id, p]));
  for (const p of predictions.values()) roster.set(p.player_id, { ...roster.get(p.player_id), ...p });
  return [...roster.values()].map((p) => ({
    ...p,
    ...predictions.get(p.player_id),
    status: predictions.get(p.player_id)?.status || "not_forecast",
    requires_review: reviewIds.has(p.player_id),
    rotation_role: predictions.get(p.player_id)?.rotation_role || p.rotation_role,
    game_id: game.game_id,
    team_name: p.team_id === game.home_team_id ? game.home_name : game.away_name,
    team_order: p.team_id === game.away_team_id ? 0 : 1,
    matchup: `${game.away_tricode || "TBD"} @ ${game.home_tricode || "TBD"}`,
    team_code:
      p.team_id === game.home_team_id ? game.home_tricode : game.away_tricode,
    reason:
      predictions.get(p.player_id)?.reason ||
      "No forecast saved for this game. Current roster membership is not availability evidence.",
  }));
}
export function statusLabel(p) {
  if (p.status === "out") return "Out";
  if (p.status === "projected")
    return p.availability === "Not listed" ||
      p.flags?.includes("availability_not_listed")
      ? "Conditional"
      : "Projected";
  if (
    p.availability &&
    ["Questionable", "Doubtful", "Probable", "questionable"].includes(
      p.availability,
    )
  )
    return p.availability[0].toUpperCase() + p.availability.slice(1);
  return p.status === "not_forecast" ? "Not forecast" : "Needs review";
}
export function filterPlayers(
  players,
  query = "",
  filter = "all",
  sort = "rotation",
) {
  const text = query.trim().toLowerCase();
  return players
    .filter(
      (p) =>
        (!text ||
          `${p.name} ${p.team_code || ""}`.toLowerCase().includes(text)) &&
        (filter === "all" ||
          (filter === "review" &&
            (p.requires_review || ["needs_review", "not_forecast"].includes(p.status))) ||
          (filter === "out" && p.status === "out") ||
          (filter === "projected" && p.status === "projected")),
    )
    .sort((a, b) =>
      sort === "points"
        ? (b.points_mean ?? -Infinity) - (a.points_mean ?? -Infinity) ||
          a.name.localeCompare(b.name)
        : (rotationMinutes(b) - rotationMinutes(a)) || surname(a.name).localeCompare(surname(b.name)) || a.name.localeCompare(b.name),
    );
}

const rotationMinutes = (p) => p.expected_minutes ?? p.rotation_role?.recent_minutes ?? -1;
const surname = (name) => name.trim().replace(/\s+(Jr\.?|Sr\.?|II|III|IV)$/i, "").split(/\s+/).at(-1);
export function groupPlayers(players, allPlayers = players) {
  const teams = new Map();
  for (const p of players) {
    if (!teams.has(p.team_id)) teams.set(p.team_id, {
      team_id: p.team_id, team_code: p.team_code || "NBA", team_name: p.team_name,
      team_order: p.team_order ?? 2, players: [],
    });
    teams.get(p.team_id).players.push(p);
  }
  return [...teams.values()].sort((a, b) => a.team_order - b.team_order || a.team_code.localeCompare(b.team_code)).map((team) => {
    const pool = allPlayers.filter((p) => p.team_id === team.team_id);
    const explicit = pool.some((p) => typeof p.projected_starter === "boolean");
    const supported = (p) => p.rotation_role?.observations > 0 || rotationMinutes(p) > 0;
    const eligible = pool.filter((p) => p.status !== "out" && (explicit ? p.projected_starter : supported(p)));
    const latest = Math.max(...eligible.map((p) => Date.parse(p.rotation_role?.last_game_time) || -Infinity));
    const wasStarter = (p) => Number(Boolean(Number.isFinite(latest) && Date.parse(p.rotation_role?.last_game_time) === latest && p.rotation_role?.last_game_started === true));
    const ids = new Set([...eligible].sort((a,b) => wasStarter(b)-wasStarter(a) ||
      Number(b.rotation_role?.label === "starter")-Number(a.rotation_role?.label === "starter") ||
      rotationMinutes(b)-rotationMinutes(a) || a.name.localeCompare(b.name)).slice(0,5).map((p) => p.player_id));
    const role = (p) => ids.has(p.player_id) ? "starter" : supported(p) ? "bench" : "unknown";
    return { ...team, unfilled_slots: 5-ids.size, total: pool.length,
      unavailable: pool.filter((p) => p.availability === "unknown" || (p.status === "needs_review" && !p.availability)).length,
      groups: [["starter", "Starters"], ["bench", "Bench"], ["unknown", "Role unverified"]]
        .map(([key, label]) => ({ key, label, players: team.players.filter((p) => role(p) === key) }))
        .filter((g) => g.players.length),
    };
  });
}
export function health(runner, now = Date.now()) {
  if (!runner) return { label: "No runner evidence", kind: "warn" };
  if (runner.status !== "complete")
    return { label: "Runner needs attention", kind: "bad" };
  if (now - new Date(runner.finished_at).getTime() > 600000)
    return { label: "Runner heartbeat stale", kind: "warn" };
  return { label: "Runner recently completed", kind: "good" };
}
export const safeSource = (url) => {
  try {
    return new URL(url).protocol === "https:" ? url : null;
  } catch {
    return null;
  }
};
// "Final", "Final/OT", "Final/2OT"…, and which side won, from the captured box score.
export function finalResult(score) {
  if (!score || score.home == null || score.away == null) return null;
  const overtimes = Math.max(0, (score.periods || 4) - 4);
  return {
    label: overtimes ? `Final/${overtimes > 1 ? overtimes : ""}OT` : "Final",
    winner: score.home > score.away ? "home" : score.away > score.home ? "away" : null,
  };
}
