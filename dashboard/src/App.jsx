import Postgame from "./Postgame";
import Projections from "./Projections";
import ForecastStages from "./ForecastStages";
import ForecastTotals from "./ForecastTotals";
import MarginForecast from "./MarginForecast";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon, TeamMark } from "./Icons";
import PlayerTable from "./PlayerTable";
import PlayerDetail from "./PlayerDetail";
import Sidebar from "./Sidebar";
import Performance from "./Performance";
import {
  gameDay,
  initialDay,
  gamePlayers,
  timeLabel,
  stamp,
  finalResult,
} from "./data";

// A game-rail logo with the final score under it once the game is over.
function TileTeam({ game, side }) {
  const result = game.status === "final" ? finalResult(game.final_score) : null;
  return (
    <div className="tile-team">
      <TeamMark id={game[side + "_team_id"]} code={game[side + "_tricode"]} />
      {result && (
        <b className={`final-score ${result.winner === side ? "won" : ""}`}>
          {game.final_score[side]}
        </b>
      )}
    </div>
  );
}

export default function App() {
  const [data, setData] = useState(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [tab, setTab] = useState("Games"),
    [date, setDate] = useState(""),
    [selected, setSelected] = useState(""),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [sort, setSort] = useState("rotation"),
    [forecastStage, setForecastStage] = useState("latest"),
    [detail, setDetail] = useState(null),
    [refreshRevision, setRefreshRevision] = useState(0),
    [details, setDetails] = useState({}),
    [reviews, setReviews] = useState({}),
    [now, setNow] = useState(Date.now());
  const refreshInFlight = useRef(false);
  const reload = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    setLoading(true);
    setError("");
    try {
      const staticMode =
        new URLSearchParams(location.search).get("snapshot") === "1";
      // no-cache revalidates with the server's ETag: an unchanged page costs a 304, not 20 MB.
      const response = await fetch(
        staticMode ? "./dashboard.json" : "/api/dashboard",
        { cache: "no-cache" },
      );
      if (!response.ok)
        throw new Error(
          "Saved evidence is unavailable. Check the local dashboard server or open the captured preview.",
        );
      const next = await response.json();
      if (
        next.schema_version !== 1 ||
        !Array.isArray(next.games) ||
        !Array.isArray(next.players)
      )
        throw new Error("Dashboard data format is not supported.");
      // The server names each game's opening forecast by stage instead of repeating it.
      for (const g of next.games)
        g.forecast = g.forecast ?? g.forecasts?.[g.forecast_stage] ?? null;
      setData(next);
      setRefreshRevision((old) => old + 1);
      setDate((old) => old || next.default_day || initialDay(next));
    } catch (e) {
      setError(e.message);
    } finally {
      refreshInFlight.current = false;
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    reload();
    const staticMode = new URLSearchParams(location.search).get("snapshot") === "1";
    const timer = setInterval(() => {
      setNow(Date.now());
      if (!staticMode) reload();
    }, 30000);
    return () => clearInterval(timer);
  }, [reload]);
  // The live server sends one game day's forecasts with the page (detail_days) and
  // any other day's from /api/forecasts; a saved snapshot carries every day's.
  const serverDays = Array.isArray(data?.detail_days) ? data.detail_days : null;
  const dayPending = Boolean(serverDays && date && !serverDays.includes(date) && !details[date]);
  useEffect(() => {
    if (!serverDays || !date || serverDays.includes(date)) return;
    const controller = new AbortController();
    fetch(`/api/forecasts?date=${encodeURIComponent(date)}`, { cache: "no-cache", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Forecasts for this date are unavailable.");
        return response.json();
      })
      .then((next) => setDetails((old) => ({ ...old, [date]: next.games || {} })))
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [data, date]);
  // Daily reviews are listed by date and fetched one night at a time.
  const reviewDates = Array.isArray(data?.daily_review_dates) ? data.daily_review_dates : null;
  useEffect(() => {
    if (!reviewDates || tab !== "Performance" || !reviewDates.includes(date)) return;
    const controller = new AbortController();
    fetch(`/api/daily-reviews/${encodeURIComponent(date)}`, { cache: "no-cache", signal: controller.signal })
      .then((response) => (response.ok ? response.json() : null))
      .then((report) => setReviews((old) => ({ ...old, [date]: report })))
      .catch(() => {});
    return () => controller.abort();
  }, [data, date, tab]);
  const view = useMemo(() => {
    if (!data) return null;
    const games = !serverDays ? data.games : data.games.map((g) => {
      const extra = serverDays.includes(gameDay(g)) ? null : details[gameDay(g)]?.[g.game_id];
      if (!extra) return g;
      const merged = { ...g, ...extra };
      merged.forecast = merged.forecast ?? merged.forecasts?.[merged.forecast_stage] ?? null;
      return merged;
    });
    return {
      ...data,
      games,
      daily_reviews: reviewDates ? reviews : data.daily_reviews,
      daily_review_pending: Boolean(reviewDates?.includes(date) && !(date in reviews)),
    };
  }, [data, details, reviews, date]);
  const games = useMemo(
    () => view?.games.filter((g) => gameDay(g) === date) || [],
    [view, date],
  );
  const selectedGame = games.find((g) => g.game_id === selected) || games[0];
  const game = useMemo(() => {
    if (!selectedGame || forecastStage === "latest") return selectedGame;
    const saved = selectedGame.forecasts?.[forecastStage] ||
      (selectedGame.forecast?.stage === forecastStage ? selectedGame.forecast : null);
    return { ...selectedGame, forecast: saved };
  }, [selectedGame, forecastStage]);
  const players = useMemo(() => {
    if (!view) return [];
    if (tab === "Games") return gamePlayers(view, game);
    const pool = new Map(
      (tab === "Projections" ? [] : view.players).map((p) => [
        p.player_id,
        {
          ...p,
          status: "not_forecast",
          reason: "No saved forecast on the selected date.",
        },
      ]),
    );
    for (const g of games)
      for (const p of gamePlayers(view, g)) pool.set(p.player_id, p);
    return [...pool.values()];
  }, [view, games, game, tab]);
  function chooseTab(value) {
    setTab(value);
    setQuery("");
    setFilter("all");
    setDetail(null);
  }
  function review() {
    setFilter("review");
    setQuery("");
    document
      .getElementById("player-area")
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function download() {
    const report = {
      captured_at: data.generated_at,
      game_id: game?.game_id,
      review_queue: game?.forecast?.review_queue || [],
    };
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `review-${game?.game_id || "slate"}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <>
      <header className="topbar">
        <div className="top-inner">
          <a className="brand" href="/" aria-label="NBA Forecast home">
            NBA <span>FORECAST</span>
          </a>
          <nav aria-label="Main navigation">
            {["Games", "Projections", "Players", "Postgame", "Performance"].map(
              (v) => (
                <button
                  key={v}
                  onClick={() => chooseTab(v)}
                  className={tab === v ? "active" : ""}
                  aria-current={tab === v ? "page" : undefined}
                >
                  {v}
                </button>
              ),
            )}
          </nav>
          <span className="season">{data?.season || "—"}</span>
        </div>
      </header>
      {data && (
        <div className="game-strip">
          <div className="strip-date">
            <span>
              {date
                ? new Intl.DateTimeFormat("en-US", {
                    weekday: "short",
                    timeZone: "UTC",
                  }).format(new Date(date + "T12:00:00Z"))
                : ""}
            </span>
            <strong>
              {date
                ? new Intl.DateTimeFormat("en-US", {
                    month: "short",
                    day: "numeric",
                    timeZone: "UTC",
                  }).format(new Date(date + "T12:00:00Z"))
                : ""}
            </strong>
          </div>
          <div className="game-rail" aria-label="Games on selected date">
            {games.map((g) => (
              <button
                className={`game-tile ${game?.game_id === g.game_id ? "selected" : ""}`}
                key={g.game_id}
                aria-pressed={game?.game_id === g.game_id}
                aria-label={`${g.away_tricode || "TBD"} at ${g.home_tricode || "TBD"}${
                  g.status === "final" && finalResult(g.final_score)
                    ? `, final ${g.final_score.away}–${g.final_score.home}`
                    : ""
                }`}
                onClick={() => {
                  setSelected(g.game_id);
                  setForecastStage("latest");
                  chooseTab(tab === "Postgame" ? "Postgame" : "Games");
                }}
              >
                <div>
                  <TileTeam game={g} side="away" />
                  <span>at</span>
                  <TileTeam game={g} side="home" />
                </div>
                <small>
                  {g.status === "final"
                    ? finalResult(g.final_score)?.label || "Final"
                    : g.status === "live"
                      ? "In progress"
                      : g.status === "postponed"
                        ? "Postponed"
                        : timeLabel(g.start_time)}
                </small>
              </button>
            ))}
            {!games.length && (
              <p className="no-games-strip">No published games on this date.</p>
            )}
          </div>
        </div>
      )}
      <main>
        <div className="page-heading">
          <div>
            <h1>
              {tab === "Games"
                ? "Game day"
                : tab === "Players"
                  ? "The player pool"
                  : tab === "Projections"
                    ? "Player projections"
                    : tab === "Postgame"
                      ? "Postgame review"
                      : "Performance"}
            </h1>
            <p>
              {tab === "Games"
                ? "Every player. Every opportunity."
                : tab === "Players"
                  ? `${data?.players.length || 0} players. One place to see the evidence.`
                  : tab === "Projections"
                    ? "Your daily slate, backed by saved pregame evidence."
                    : "Measure the forecast. Improve the next one."}
            </p>
          </div>
          <div className="page-actions">
            <input
              className="date-input"
              type="date"
              aria-label="Game date"
              value={date}
              onChange={(e) => {
                setDate(e.target.value);
                setSelected("");
                setForecastStage("latest");
                setFilter("all");
              }}
            />
            <button className="primary" onClick={reload} disabled={loading}>
              <Icon name="refresh" />
              {loading ? "Refreshing…" : "Refresh data"}
            </button>
          </div>
        </div>
        {error && (
          <div role="alert" className="error">
            <Icon name="info" />
            {error}
            {data && " Previously loaded data remains visible."}
          </div>
        )}
        {!data && !error && (
          <div className="empty" role="status">
            Loading saved forecasts…
          </div>
        )}
        {data && (
          <>
            <div className="capture-line">
              {data.source_mode === "static_capture"
                ? "Saved snapshot"
                : "Saved operational evidence"}{" "}
              · {stamp(data.generated_at)}{" "}
              {dayPending && <span role="status">Loading this date's forecasts…</span>}{" "}
              <span>Times shown in Pacific Time</span>
            </div>
            {tab === "Postgame" ? (
              <Postgame game={game} refreshRevision={refreshRevision} />
            ) : tab === "Performance" ? (
              <Performance data={view} date={date} onSelect={setDetail} onGame={id=>{setSelected(id);setTab("Games");}} />
            ) : tab === "Projections" ? (
              <Projections
                players={players}
                query={query}
                setQuery={setQuery}
                filter={filter}
                setFilter={setFilter}
                sort={sort}
                setSort={setSort}
                onSelect={setDetail}
              />
            ) : (
              <>
                {tab === "Games" && game && <Matchup game={game} />}
                {tab === "Games" && game && <ForecastStages game={selectedGame} selected={game.forecast?.stage} onSelect={setForecastStage} />}
                {tab === "Games" && game && <ForecastTotals game={game} />}
                {tab === "Games" && game?.forecast && <MarginForecast key={`${game.game_id}:${game.forecast.run_id || game.forecast.stage}`} game={game} />}
                {tab === "Games" && !game ? (
                  <div className="empty">
                    <h2>No games on this date</h2>
                    <p>
                      Choose another date to browse the published season
                      schedule.
                    </p>
                  </div>
                ) : (
                  <div
                    className={`content-grid ${tab === "Players" ? "full-pool" : ""}`}
                  >
                    <div id="player-area">
                      <PlayerTable
                        key={`${tab}-${game?.game_id || "pool"}`}
                        players={players}
                        query={query}
                        setQuery={setQuery}
                        filter={filter}
                        setFilter={setFilter}
                        sort={sort}
                        setSort={setSort}
                        onSelect={setDetail}
                        title={
                          tab === "Players" ? "All players" : "Player outlook"
                        }
                      />
                      {tab === "Games" && game?.forecast && (
                        <div className="run-footer">
                          <span>
                            {game.forecast.stage === "final"
                              ? "Final checkpoint"
                              : "Preliminary forecast"}{" "}
                            · {game.forecast.model_version}
                          </span>
                          <button className="text-button" onClick={download}>
                            <Icon name="download" size={16} /> Export review
                            queue
                          </button>
                        </div>
                      )}
                    </div>
                    {tab === "Games" && (
                      <Sidebar
                        game={game}
                        data={view}
                        now={now}
                        onReview={review}
                      />
                    )}
                  </div>
                )}
              </>
            )}
            <footer>
              <span>Official NBA sources · Forecasts are not guarantees</span>
              <span>Independent analytics · Not affiliated with the NBA</span>
            </footer>
          </>
        )}
      </main>
      {detail && (
        <PlayerDetail player={detail} onClose={() => setDetail(null)} />
      )}
    </>
  );
}
function Matchup({ game }) {
  const splitName = (full, code) => {
    const known = {
      PHI: ["Philadelphia", "76ers"],
      CLE: ["Cleveland", "Cavaliers"],
      DEN: ["Denver", "Nuggets"],
      OKC: ["Oklahoma City", "Thunder"],
      POR: ["Portland", "Trail Blazers"],
    };
    return (
      known[code] || [
        full?.split(" ").slice(0, -1).join(" ") || "",
        full?.split(" ").at(-1) || "TBD",
      ]
    );
  };
  const away = splitName(game.away_name, game.away_tricode),
    home = splitName(game.home_name, game.home_tricode);
  const result = game.status === "final" ? finalResult(game.final_score) : null;
  const logo = (side) => (
    <div className="matchup-logo">
      <TeamMark id={game[side + "_team_id"]} code={game[side + "_tricode"]} large />
      {result && (
        <b className={`final-score ${result.winner === side ? "won" : ""}`}>
          {game.final_score[side]}
        </b>
      )}
    </div>
  );
  return (
    <section className="matchup" aria-label="Selected matchup">
      <div className="matchup-team away">
        {logo("away")}
        <div>
          <span className="city">{away[0]}</span>
          <strong>{away[1]}</strong>
        </div>
      </div>
      <span className="versus">AT</span>
      <div className="matchup-team home">
        {logo("home")}
        <div>
          <span className="city">{home[0]}</span>
          <strong>{home[1]}</strong>
        </div>
      </div>
      <div className="tipoff">
        <strong>
          {game.status === "postponed"
            ? "Postponed"
            : game.status === "final"
              ? result?.label || "Final"
              : timeLabel(game.start_time)}
        </strong>
        <span>
          {game.start_time
            ? `Final check · ${timeLabel(new Date(Date.parse(game.start_time) - 1800000).toISOString())}`
            : "Awaiting confirmed tipoff"}
        </span>
      </div>
    </section>
  );
}
