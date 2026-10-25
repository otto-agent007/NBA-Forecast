import React, { useEffect, useRef, useState } from "react";

const STEPS = 1000;

// Wide stat tables get a column slider that stays on screen. The native horizontal
// scrollbar sits under the last row, thirty-odd players down, and is an overlay that
// stays hidden in GNOME Firefox and macOS Chrome, so the columns past the viewport
// looked missing. The slider holds a fraction of the live scroll width, so rows,
// filters or fonts that change the table's width never leave it short of the end.
export default function ColumnScroll({ label, children }) {
  const ref = useRef(null);
  const [position, setPosition] = useState({ value: 0, scrollable: false });
  const measure = () => {
    const element = ref.current;
    if (!element) return;
    const max = element.scrollWidth - element.clientWidth;
    const next = { value: max > 0 ? Math.round(element.scrollLeft / max * STEPS) : 0, scrollable: max > 0 };
    setPosition(current => current.value === next.value && current.scrollable === next.scrollable ? current : next);
  };
  useEffect(measure);
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    element.addEventListener("scroll", measure, { passive: true });
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    if (observer) {
      observer.observe(element);
      for (const child of element.children) observer.observe(child);
    }
    return () => {
      element.removeEventListener("scroll", measure);
      observer?.disconnect();
    };
  }, []);
  return (
    <div className="column-scroll">
      <div ref={ref} className="table-scroll boxscore-scroll" role="region" aria-label={`${label} scroll area`} tabIndex={0}>
        {children}
      </div>
      {position.scrollable && (
        <label className="column-slider">
          <span>More stats</span>
          <input
            type="range"
            min="0"
            max={STEPS}
            step="1"
            value={position.value}
            aria-label={`Scroll ${label} stat columns`}
            onChange={(event) => {
              const element = ref.current;
              if (element) element.scrollLeft = Number(event.target.value) / STEPS * (element.scrollWidth - element.clientWidth);
            }}
          />
        </label>
      )}
    </div>
  );
}
