import React from "react";
export default function Methodology({ data }) {
  return (
    <section className="clutch-panel clutch-method">
      <details>
        <summary>Definitions, qualification, assumptions and sources</summary>
        <h3>What each metric means</h3>
        <p>
          PTS/GP divides points by games with a clutch appearance, not all
          season games. Per-minute rates divide totals by clutch minutes.
          FGA/min measures attempts, not touches or possession share. Assists
          are recorded assists, not potential assists. TOV/min is not adjusted
          for ball-handling workload; AST/TOV is undefined when turnovers are
          zero.
        </p>
        <p>
          2P% = (FGM − 3PM)/(FGA − 3PA); 3P% = 3PM/3PA; FT% = FTM/FTA. eFG% =
          (FGM + 0.5 × 3PM)/FGA. TS% = PTS/[2 × (FGA + 0.44 × FTA)] uses an
          approximate free-throw possession factor; it is descriptive, not an
          exact endgame possession measure. Free throws are included in scoring,
          but intentional fouling cannot be separated in these aggregates.
        </p>
        <h3>Qualification is a display rule</h3>
        <p>
          Exposure rankings require at least 20 clutch games and 50 clutch
          minutes. eFG rankings also require 30 FGA; shot-type rankings require
          30 attempts of that type. The same filters apply to pooled totals.
          These are provisional minimums, not validated reliability cutoffs.
          Small samples remain inspectable and shrink continuously.
        </p>
        <h3>Adjustment and uncertainty</h3>
        <p>
          Rates blend each player’s totals with 50 pseudo-minutes at the other
          players’ exposure-weighted rate in the same period. Each shooting
          probability uses a Beta prior equivalent to 30 attempts, centered on
          the other players’ shot-type conversion with 0.5/1 smoothing.
          Half/double prior strengths are available above; the defaults are not
          optimized for predictive performance.
        </p>
        <p>
          Approximate 90% intervals use{" "}
          {data.priors.monte_carlo_draws.toLocaleString()} deterministic
          posterior draws. eFG combines independent 2P/3P probabilities at the
          observed attempt mix. Priors are treated as fixed; game clustering,
          changing roles, opponent strength and shot difficulty are not modeled.
          Intervals can understate real uncertainty. Rate sensitivity is shown
          separately; rates have no calibrated confidence intervals.
        </p>
        <h3>Pooling and consistency</h3>
        <p>
          Pooled estimates sum counts and exposure, never average percentages,
          with no recency weighting. Different players can cover different
          years. Repeatability correlates within-season percentile ranks for
          overlapping qualified players; it is an exploratory selected sample,
          not an out-of-sample skill test. No overall impact score, non-clutch
          advantage or causal clutch rating is implied.
        </p>
        <h3>Verified archive</h3>
        {data.sources.map((s) => (
          <p key={s.season}>
            <a href={s.source_url} target="_blank" rel="noreferrer">
              Official NBA Stats · {s.season} ↗
            </a>
            <br />
            Retrieved {s.retrieved_at.slice(0, 10)} · original SHA-256{" "}
            <code>{s.original_sha256}</code>
          </p>
        ))}
        <p>
          Method: {data.method_version}. Compressed and original byte hashes are
          checked; normalized records must match replayed official responses.
          The command and evaluation gate are documented in the repository’s
          clutch-ratings guide.
        </p>
        <p>{data.policy}</p>
      </details>
    </section>
  );
}
