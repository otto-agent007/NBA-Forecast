import React from "react";
import { Icon } from "./Icons";
import { stamp, timeLabel, health, safeSource } from "./data";
export default function Sidebar({ game, data, onReview, now }) {
  const forecast = game?.forecast,
    latestCheck = game?.injury_capture?.games?.find((c) => c.game_id === game?.game_id),
    check = latestCheck || forecast?.injury_check;
  const evidence = forecast?.injury_check;
  const report = evidence?.latest_report || evidence?.anchor_report;
  const daily = data.injury_capture;
  const dailySource = safeSource(daily?.capture?.report?.source_url);
  const sourceWarnings = (data.runner?.warnings || []).filter((w) => ['schedule', 'roster'].includes(w.task));
  const h = health(data.runner, now);
  const start = game?.start_time ? Date.parse(game.start_time) : null;
  const before = start && now < start;
  const final =
    (game?.forecasts?.final || forecast?.stage === "final")
      ? "Saved"
      : before && now < start - 1800000
        ? "Waiting for T−30"
        : before
          ? "Checkpoint overdue"
          : "Not saved";
  const unresolved = (forecast?.predictions || []).filter(
    (p) => p.status === "needs_review",
  ).length;
  const source = safeSource(report?.source_url);
  return (
    <aside className="sidebar">
      <h2>Pregame checklist</h2>
      <div className="checklist">
        <Check icon="clock" label="Daily report · 9 AM Pacific" value={daily?.state === 'captured' ? 'Captured' : daily?.state === 'error' ? 'Retrying' : 'Pending'} kind={daily?.state === 'captured' ? 'good' : 'warn'} />
        <Check
          icon="check"
          label="Schedule"
          value={data.schedule_observed_at ? "Captured" : "Missing"}
          kind={data.schedule_observed_at ? "good" : "warn"}
        />
        <Check
          icon="info"
          label="T−30 injury report"
          value={check?.phase === 'final_review' && check?.report_status === 'captured_pending_review' ? "Captured" : report ? "Preliminary" : "Not captured"}
          kind="warn"
        />
        <Check
          icon="clock"
          label="Final forecast"
          value={final}
          kind={final === "Saved" ? "good" : "muted"}
        />
      </div>
      <section>
        <h3>What needs attention</h3>
        <p>
          {unresolved ? `${unresolved} players need review. ` : ""}Confirm
          availability before assigning minutes.
        </p>
        <button className="text-button" onClick={onReview}>
          View review queue <Icon name="arrow" size={18} />
        </button>
      </section>
      {(forecast?.team_roles || [])
        .filter((t) => t.out_player_ids.length > 0)
        .map((t) => (
          <section key={t.team_id}>
            <h3>Rotation opportunity review</h3>
            <p>
              {t.vacated_baseline_minutes.toFixed(1)} baseline minutes and{" "}
              {t.vacated_baseline_fga.toFixed(1)} shot attempts associated with
              out players.
            </p>
            <p>
              Compare:{" "}
              {t.opportunity_ranking
                .map((id) =>
                  forecast.predictions.find((p) => p.player_id === id),
                )
                .filter((p) => p && p.status === "projected")
                .slice(0, 3)
                .map((p) => p.name)
                .join(", ") || "Availability unresolved"}
              . These are review candidates, not assigned beneficiaries.
            </p>
          </section>
        ))}
      <section className="evidence-block">
        <h3>Evidence freshness</h3>
        {sourceWarnings.map((w) => <p className="warning" key={w.task}>
          {w.task === 'schedule' ? 'Calendar refresh unavailable. Using the saved schedule with its original capture time.'
            : 'Roster refresh unavailable. Only saved roster evidence within 48 hours can support a forecast.'}
        </p>)}
        <dl>
          <dt>Schedule</dt>
          <dd>{stamp(data.schedule_observed_at)}</dd>
          <dt>Forecast saved</dt>
          <dd>{stamp(forecast?.created_at)}</dd>
          <dt>Report published</dt>
          <dd>{stamp(report?.published_at)}</dd>
        </dl>
        {source && (
          <a href={source} target="_blank" rel="noreferrer">
            Official injury report <Icon name="arrow" size={14} />
          </a>
        )}
        {dailySource && <p><a href={dailySource} target="_blank" rel="noreferrer">Daily injury report ↗</a><br /><small>Captured {stamp(daily.capture.checked_at)}</small></p>}
        {check?.later_report_available && (
          <p className="warning">
            A later injury report is available. Review changes before relying on
            this run.
          </p>
        )}
        <div className={`runner ${h.kind}`}>
          <span className="status-dot" />
          {h.label}
        </div>
        <p className="tiny">
          {stamp(data.runner?.finished_at)}. Refresh reloads saved evidence; it
          does not start the runner.
        </p>
      </section>
    </aside>
  );
}
function Check({ icon, label, value, kind }) {
  return (
    <div className="check-row">
      <span className={`check-icon ${kind}`}>
        <Icon name={icon} size={16} />
      </span>
      <span>{label}</span>
      <span className={`check-value ${kind}`}>{value}</span>
    </div>
  );
}
