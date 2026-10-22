import React from "react";
export function Icon({ name, size = 20, ...props }) {
  const paths = {
    refresh: (
      <>
        <path d="M20 7v5h-5M4 17v-5h5" />
        <path d="M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 18 17" />
      </>
    ),
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 5 5" />
      </>
    ),
    close: <path d="m6 6 12 12M18 6 6 18" />,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 6v6l4 3" />
      </>
    ),
    info: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v6M12 7v1" />
      </>
    ),
    download: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name] || paths.info}
    </svg>
  );
}
const colors = {
  CLE: "#a52243",
  PHI: "#006cff",
  DEN: "#e2a914",
  OKC: "#ef5529",
  BOS: "#008248",
  NYK: "#ef6c23",
  LAL: "#b99521",
  GSW: "#1476df",
  MIA: "#be2345",
};
export function TeamMark({ id, code, large = false }) {
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [id]);
  return id && id !== "0" && !failed ? (
    <img
      className={`team-logo ${large ? "large" : ""}`}
      src={`/logos/${id}.svg`}
      alt=""
      onError={() => setFailed(true)}
    />
  ) : (
    <span
      className={`team-mark ${large ? "large" : ""}`}
      style={{ "--team": colors[code] || "#4c6b92" }}
      aria-hidden="true"
    >
      {code || "TBD"}
    </span>
  );
}
