import React from "react";
import { fmtNum, fmtPct } from "../utils/formatters";

const ORDER = ["NIFTY 50", "SENSEX", "NIFTY BANK", "NIFTY IT", "NIFTY NEXT 50", "INDIA VIX"];

const IndexPill = ({ data }) => {
  if (!data || data.last == null) return null;
  const up = (data.changePct ?? 0) >= 0;
  const color = up ? "var(--green)" : "var(--red)";
  return (
    <div
      className="flex items-center gap-2 px-3 py-1.5 rounded-lg flex-shrink-0"
      style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
      data-testid={`index-${data.name.replace(/\s+/g, "-")}`}
    >
      <span style={{ fontSize: 10, color: "var(--text-secondary)", letterSpacing: "0.07em", textTransform: "uppercase", fontWeight: 600 }}>
        {data.name}
      </span>
      <span className="mono" style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
        {fmtNum(data.last)}
      </span>
      <span className="mono" style={{ fontSize: 11, color }}>
        {up ? "▲" : "▼"} {fmtPct(data.changePct)}
      </span>
      <span className="mono" style={{ fontSize: 10, color: "var(--text-muted)", whiteSpace: "nowrap" }}>
        O {data.open ? fmtNum(data.open) : "—"} · PC {data.previousClose ? fmtNum(data.previousClose) : "—"}
      </span>
    </div>
  );
};

const IndicesTicker = ({ indices }) => {
  const entries = ORDER.map((k) => indices[k]).filter(Boolean);
  if (entries.length === 0) return null;
  return (
    <div
      className="flex items-center gap-2 px-6 py-2 overflow-x-auto"
      style={{ background: "var(--bg-base)", borderBottom: "1px solid var(--border)" }}
      data-testid="indices-ticker"
    >
      <span style={{ fontSize: 9, color: "var(--text-muted)", letterSpacing: "0.18em", textTransform: "uppercase", fontWeight: 600, whiteSpace: "nowrap", marginRight: 4 }}>
        NSE LIVE
      </span>
      {entries.map((e) => (
        <IndexPill key={e.name} data={e} />
      ))}
    </div>
  );
};

export default IndicesTicker;
