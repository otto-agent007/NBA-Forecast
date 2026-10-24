import React from "react";
import { BOX_SCORE_COLUMNS, boxScoreTitle, boxScoreValue } from "./boxscore";

export default function BoxScoreTable({ row, mode = "forecast", label, coverage }) {
  return (
    <div className="table-scroll boxscore-scroll" role="region" aria-label={`${label} scroll area`} tabIndex={0}>
      <table className="boxscore-table single-boxscore" aria-label={label}>
        <thead><tr>
          {BOX_SCORE_COLUMNS.map(column => <th key={column.id} scope="col" className="numeric" title={column.description}>{column.label}</th>)}
        </tr></thead>
        <tbody><tr>
          {BOX_SCORE_COLUMNS.map(column => {
            const samples = coverage ? column.fields.map(field => `${field.toUpperCase()}: ${coverage[field]?.available_games ?? 0} captured games`).join("; ") : "";
            return <td key={column.id} className="numeric" data-stat={column.id}
              title={[boxScoreTitle(row, column, mode), samples].filter(Boolean).join(" ")}>
              {boxScoreValue(row, column, mode)}
            </td>;
          })}
        </tr></tbody>
      </table>
    </div>
  );
}
