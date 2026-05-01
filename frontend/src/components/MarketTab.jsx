import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { fmtINR, fmtNum, fmtPct, fmtVolume } from "../utils/formatters";
import Sparkline from "./Sparkline";
import TradeModal from "./TradeModal";
import ChartModal from "./ChartModal";

const MarketTab = ({ prices, sparks, stocks, portfolio, marketStatus, watchlist, onToggleWatch, onPortfolioChange, onTickListener }) => {
  const [search, setSearch] = useState("");
  const [sector, setSector] = useState(() => localStorage.getItem("scale_market_sector") || "All");
  const [sort, setSort] = useState(() => localStorage.getItem("scale_market_sort") || "symbol");
  const [visible, setVisible] = useState(25);
  const [modal, setModal] = useState(null); // {mode, symbol, stock}
  const [chart, setChart] = useState(null); // {symbol, stock}
  const rowRefs = useRef({});
  const watchSet = useMemo(() => new Set(watchlist || []), [watchlist]);

  useEffect(() => { localStorage.setItem("scale_market_sector", sector); }, [sector]);
  useEffect(() => { localStorage.setItem("scale_market_sort", sort); }, [sort]);

  useEffect(() => {
    if (!onTickListener) return;
    const off = onTickListener((ev) => {
      const el = rowRefs.current[ev.symbol];
      if (!el) return;
      const cls = (ev.change || 0) >= 0 ? "flash-green" : "flash-red";
      el.classList.remove("flash-green", "flash-red");
      void el.offsetWidth;
      el.classList.add(cls);
    });
    return off;
  }, [onTickListener]);

  const sectors = useMemo(() => {
    const s = new Set(stocks.map((x) => x.sector));
    return ["All", ...Array.from(s).sort()];
  }, [stocks]);

  const holdingsMap = useMemo(() => {
    const m = {};
    (portfolio?.holdings || []).forEach((h) => { m[h.symbol] = h; });
    return m;
  }, [portfolio]);

  const filtered = useMemo(() => {
    let rows = stocks;
    if (sector !== "All") rows = rows.filter((r) => r.sector === sector);
    if (search.trim()) {
      const q = search.toLowerCase();
      rows = rows.filter((r) => r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q));
    }
    rows = rows.map((r) => ({ ...r, p: prices[r.symbol] || {} }));
    if (sort === "price") rows.sort((a, b) => (b.p.price || 0) - (a.p.price || 0));
    else if (sort === "change") rows.sort((a, b) => (b.p.changePct || 0) - (a.p.changePct || 0));
    else rows.sort((a, b) => a.symbol.localeCompare(b.symbol));
    return rows;
  }, [stocks, search, sector, sort, prices]);

  const marketClosed = marketStatus?.sessionType === "CLOSED";
  const prevTradedLabel = React.useMemo(() => {
    const td = marketStatus?.tradeDate; // e.g. "30-Apr-2026 15:30"
    if (!td) return "Prev close";
    const datePart = String(td).split(" ")[0]; // "30-Apr-2026"
    const bits = datePart.split("-");
    if (bits.length >= 2) return `Last traded ${bits[0]} ${bits[1]}`;
    return "Prev close";
  }, [marketStatus?.tradeDate]);

  return (
    <div className="px-6 py-5" data-testid="market-tab">
      <div className="flex flex-wrap gap-3 items-center mb-4">
        <input
          data-testid="market-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search symbol or name…"
          className="mono px-3 py-2 rounded-md text-sm outline-none w-64"
          style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
        />
        <div className="flex flex-wrap gap-1.5">
          {sectors.map((s) => (
            <button
              key={s}
              data-testid={`sector-${s}`}
              onClick={() => setSector(s)}
              className="px-3 py-1 rounded-full text-xs transition"
              style={{
                background: sector === s ? "var(--blue-dim)" : "var(--bg-elevated)",
                color: sector === s ? "var(--blue)" : "var(--text-secondary)",
                border: "1px solid",
                borderColor: sector === s ? "var(--blue)" : "var(--border)",
              }}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-1 text-xs">
          <span style={{ color: "var(--text-secondary)", alignSelf: "center", marginRight: 6 }}>Sort:</span>
          {[
            ["symbol", "A→Z"],
            ["price", "Price"],
            ["change", "Change %"],
          ].map(([k, l]) => (
            <button
              key={k}
              onClick={() => setSort(k)}
              className="px-2.5 py-1 rounded-md"
              style={{
                background: sort === k ? "var(--bg-highlight)" : "var(--bg-elevated)",
                color: sort === k ? "var(--text-primary)" : "var(--text-secondary)",
                border: "1px solid var(--border)",
              }}
            >
              {l}
            </button>
          ))}
        </div>
      </div>

      <div className="rounded-xl overflow-hidden surface">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ color: "var(--text-secondary)", fontSize: 11, letterSpacing: "0.07em", textTransform: "uppercase" }}>
                {["", "Symbol", "Name", "Sector", "LTP", "Open", "High", "Low", "Chg", "Chg %", "Vol", "Updated", "Trend", ""].map((h, i) => (
                  <th key={i} className="text-left px-3 py-2.5" style={{ borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.slice(0, visible).map((r) => {
                const p = r.p || {};
                const held = holdingsMap[r.symbol];
                const up = (p.changePct || 0) >= 0;
                return (
                  <tr
                    key={r.symbol}
                    ref={(el) => { if (el) rowRefs.current[r.symbol] = el; }}
                    className={`tr-hover ${held ? "tr-held" : ""}`}
                    style={{ borderTop: "1px solid var(--border-subtle)" }}
                    data-testid={`row-${r.symbol}`}
                  >
                    <td className="px-3 py-2.5">
                      <button
                        data-testid={`watch-toggle-${r.symbol}`}
                        onClick={(e) => { e.stopPropagation(); onToggleWatch?.(r.symbol); }}
                        title={watchSet.has(r.symbol) ? "Remove from watchlist" : "Add to watchlist"}
                        className="text-base"
                        style={{ color: watchSet.has(r.symbol) ? "var(--amber)" : "var(--text-muted)", lineHeight: 1, padding: 2, cursor: "pointer" }}
                      >
                        {watchSet.has(r.symbol) ? "★" : "☆"}
                      </button>
                    </td>
                    <td className="px-3 py-2.5 mono" style={{ color: "var(--blue)", fontWeight: 600, fontSize: 13, cursor: "pointer" }} onClick={() => setChart({ symbol: r.symbol, stock: r })} data-testid={`open-chart-${r.symbol}`}>{r.symbol}</td>
                    <td className="px-3 py-2.5" style={{ color: "var(--text-primary)", maxWidth: 220, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", cursor: "pointer" }} onClick={() => setChart({ symbol: r.symbol, stock: r })}>
                      {r.name}
                    </td>
                    <td className="px-3 py-2.5" style={{ color: "var(--text-secondary)", fontSize: 12 }}>{r.sector}</td>
                    <td className="px-3 py-2.5 mono" style={{ fontWeight: 600 }}>{p.price ? fmtNum(p.price) : "—"}</td>
                    <td className="px-3 py-2.5 mono" style={{ color: "var(--text-secondary)" }}>{p.open ? fmtNum(p.open) : "—"}</td>
                    <td className="px-3 py-2.5 mono" style={{ color: "var(--text-secondary)" }}>{p.high ? fmtNum(p.high) : "—"}</td>
                    <td className="px-3 py-2.5 mono" style={{ color: "var(--text-secondary)" }}>{p.low ? fmtNum(p.low) : "—"}</td>
                    <td className="px-3 py-2.5 mono" style={{ color: up ? "var(--green)" : "var(--red)" }}>
                      {p.change != null ? (up ? "+" : "") + fmtNum(p.change) : "—"}
                    </td>
                    <td className="px-3 py-2.5">
                      <span
                        className="pill mono"
                        style={{
                          background: up ? "var(--green-dim)" : "var(--red-dim)",
                          color: up ? "var(--green)" : "var(--red)",
                        }}
                      >
                        {p.changePct != null ? fmtPct(p.changePct) : "—"}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 mono" style={{ color: "var(--text-secondary)", fontSize: 12 }}>{fmtVolume(p.volume)}</td>
                    <td className="px-3 py-2.5 mono" style={{ color: "var(--text-muted)", fontSize: 11, whiteSpace: "nowrap" }}>
                      {marketClosed ? prevTradedLabel : (p.lastUpdated ? p.lastUpdated.split(" ").slice(-1)[0] : "—")}
                    </td>
                    <td className="px-3 py-2.5"><Sparkline points={sparks[r.symbol] || []} /></td>
                    <td className="px-3 py-2.5">
                      <div className="flex gap-1.5">
                        <button
                          data-testid={`buy-${r.symbol}`}
                          onClick={() => setModal({ mode: "BUY", symbol: r.symbol, stock: r })}
                          className="btn-buy px-2.5 py-1 rounded-md text-xs font-medium"
                          disabled={!p.price || marketClosed}
                          title={marketClosed ? "Market is closed" : ""}
                          style={{ opacity: (!p.price || marketClosed) ? 0.5 : 1, cursor: marketClosed ? "not-allowed" : "pointer" }}
                        >
                          Buy
                        </button>
                        <button
                          data-testid={`sell-${r.symbol}`}
                          onClick={() => setModal({ mode: "SELL", symbol: r.symbol, stock: r })}
                          className="btn-sell px-2.5 py-1 rounded-md text-xs font-medium"
                          disabled={!held || !p.price || marketClosed}
                          title={marketClosed ? "Market is closed" : ""}
                          style={{ opacity: (!held || !p.price || marketClosed) ? 0.35 : 1, cursor: marketClosed ? "not-allowed" : "pointer" }}
                        >
                          Sell
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
              {filtered.length === 0 && (
                <tr><td className="px-3 py-8 text-center" colSpan={14} style={{ color: "var(--text-muted)" }}>No stocks match.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {visible < filtered.length && (
          <div className="p-3 text-center" style={{ borderTop: "1px solid var(--border)" }}>
            <button
              data-testid="load-more"
              onClick={() => setVisible((v) => v + 25)}
              className="btn-ghost px-4 py-1.5 rounded-md text-xs"
            >
              Load more ({filtered.length - visible} remaining)
            </button>
          </div>
        )}
      </div>

      <TradeModal
        open={!!modal}
        onClose={() => setModal(null)}
        mode={modal?.mode}
        symbol={modal?.symbol}
        stock={modal?.stock}
        livePrice={modal ? prices[modal.symbol]?.price : null}
        portfolio={portfolio}
        marketClosed={marketClosed}
        onDone={(p) => onPortfolioChange(p)}
      />

      <ChartModal
        open={!!chart}
        onClose={() => setChart(null)}
        symbol={chart?.symbol}
        stock={chart?.stock}
        livePrice={chart ? prices[chart.symbol]?.price : null}
        marketClosed={marketClosed}
        onTrade={(mode) => { setChart(null); setModal({ mode, symbol: chart.symbol, stock: chart.stock }); }}
      />
    </div>
  );
};

export default MarketTab;
