import React from "react";
import { fmt } from "./clutchData";
const COLORS = [
  "#1966ca",
  "#b35b14",
  "#703cc4",
  "#087f76",
  "#b33257",
  "#506074",
];
export default function Scatter({ rows, selected, onFocus }) {
  const xmax = Math.max(1, ...rows.map((r) => r.adjusted.fga_min)) * 1.05;
  const ymax = Math.max(0.8, ...rows.map((r) => r.adjusted.efg)) * 1.05;
  const x = (v) => 55 + (v / xmax) * 530,
    y = (v) => 280 - (v / ymax) * 245;
  return (
    <div className="clutch-chart">
      <svg
        viewBox="0 0 640 335"
        role="img"
        aria-label="Adjusted clutch field-goal attempts per minute versus effective field-goal percentage"
      >
        <text x="55" y="18" className="clutch-axis-title">
          ADJUSTED eFG%
        </text>
        {[0, 0.25, 0.5, 0.75, 1]
          .filter((v) => v <= ymax)
          .map((v) => (
            <g key={v}>
              <line x1="55" x2="585" y1={y(v)} y2={y(v)} stroke="#dce5ef" />
              <text x="42" y={y(v) + 4} textAnchor="end">
                {Math.round(v * 100)}%
              </text>
            </g>
          ))}
        {[0, 0.25, 0.5, 0.75, 1]
          .filter((v) => v <= xmax)
          .map((v) => (
            <g key={v}>
              <text x={x(v)} y="303" textAnchor="middle">
                {v.toFixed(2)}
              </text>
            </g>
          ))}
        <text x="320" y="329" textAnchor="middle">
          ADJUSTED FGA / MINUTE →
        </text>
        {[...rows]
          .sort(
            (a, b) =>
              Number(selected.includes(a.player_id)) -
              Number(selected.includes(b.player_id)),
          )
          .map((r) => {
            const index = selected.indexOf(r.player_id);
            return (
              <circle
                key={r.player_id}
                cx={x(r.adjusted.fga_min)}
                cy={y(r.adjusted.efg)}
                r={Math.max(3, Math.min(13, Math.sqrt(r.totals.MIN) * 0.65))}
                fill={index < 0 ? "#b9c9dd" : COLORS[index]}
                fillOpacity={index < 0 ? 0.5 : 1}
                stroke={index < 0 ? "#9fb5cf" : "white"}
                strokeWidth="1.5"
                tabIndex="0"
                role="button"
                aria-label={`Inspect ${r.name}`}
                onClick={() => onFocus(r.player_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onFocus(r.player_id);
                  }
                }}
              >
                <title>
                  {r.name}: {fmt(r.adjusted.fga_min)} FGA/min;{" "}
                  {fmt(r.adjusted.efg, "efg")} eFG; {r.totals.MIN.toFixed(1)}{" "}
                  min
                </title>
              </circle>
            );
          })}
      </svg>
    </div>
  );
}
