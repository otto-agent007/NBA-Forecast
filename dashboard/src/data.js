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
export const initialName = (name) =>
  name
    .split(/\s+/)
    .map((x) => x[0])
    .slice(0, 2)
    .join("");
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
  const members = data.players.filter((p) =>
    [game.home_team_id, game.away_team_id].includes(p.team_id),
  );
  const roster = new Map(members.map((p) => [p.player_id, p]));
  for (const p of predictions.values()) roster.set(p.player_id, p);
  return [...roster.values()].map((p) => ({
    ...p,
    ...predictions.get(p.player_id),
    status: predictions.get(p.player_id)?.status || "not_forecast",
    game_id: game.game_id,
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
  if (p.status === "projected") return "Projected";
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
  sort = "name",
) {
  const text = query.trim().toLowerCase();
  return players
    .filter(
      (p) =>
        (!text ||
          `${p.name} ${p.team_code || ""}`.toLowerCase().includes(text)) &&
        (filter === "all" ||
          (filter === "review" &&
            ["needs_review", "not_forecast"].includes(p.status)) ||
          (filter === "out" && p.status === "out") ||
          (filter === "projected" && p.status === "projected")),
    )
    .sort((a, b) =>
      sort === "points"
        ? (b.points_mean ?? -Infinity) - (a.points_mean ?? -Infinity) ||
          a.name.localeCompare(b.name)
        : a.name.localeCompare(b.name),
    );
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
