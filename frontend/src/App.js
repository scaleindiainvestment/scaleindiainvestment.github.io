import React, { useCallback, useEffect, useState } from "react";
import "@/App.css";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import AuthPage from "@/pages/AuthPage";
import Navbar from "@/components/Navbar";
import MarketTab from "@/components/MarketTab";
import PortfolioTab from "@/components/PortfolioTab";
import HistoryTab from "@/components/HistoryTab";
import LeaderboardTab from "@/components/LeaderboardTab";
import AdminTab from "@/components/AdminTab";
import WatchlistTab from "@/components/WatchlistTab";
import IndicesTicker from "@/components/IndicesTicker";
import { api } from "@/lib/api";
import { useMarketFeed } from "@/hooks/useMarketFeed";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";

const Shell = () => {
  const { user, token, loading, logout } = useAuth();
  const [tab, setTab] = useState("Market");
  const [stocks, setStocks] = useState([]);
  const [sparks, setSparks] = useState({});
  const [portfolio, setPortfolio] = useState(null);
  const [watchlist, setWatchlist] = useState([]);
  const { prices, marketStatus, indices, connected, onTick } = useMarketFeed(token);

  const refreshPortfolio = useCallback(async () => {
    try {
      const { data } = await api.get("/portfolio");
      setPortfolio(data);
    } catch (e) {
      if (e?.response?.status === 401) logout();
    }
  }, [logout]);

  const refreshWatchlist = useCallback(async () => {
    try {
      const { data } = await api.get("/watchlist");
      setWatchlist(data.symbols || []);
    } catch {}
  }, []);

  const toggleWatch = useCallback(async (symbol) => {
    const isWatched = watchlist.includes(symbol);
    // optimistic update
    setWatchlist((prev) => isWatched ? prev.filter((s) => s !== symbol) : [...prev, symbol]);
    try {
      if (isWatched) {
        await api.delete(`/watchlist/${symbol}`);
        toast.success(`Removed ${symbol} from watchlist`);
      } else {
        await api.post("/watchlist", { symbol });
        toast.success(`Added ${symbol} to watchlist`);
      }
    } catch (e) {
      // revert on failure
      setWatchlist((prev) => isWatched ? [...prev, symbol] : prev.filter((s) => s !== symbol));
      toast.error(e?.response?.data?.detail || "Watchlist update failed");
    }
  }, [watchlist]);

  useEffect(() => {
    if (!user) return;
    (async () => {
      try {
        const [s, p] = await Promise.all([
          api.get("/stocks"),
          api.get("/prices"),
        ]);
        setStocks(s.data.stocks || []);
        setSparks(p.data.sparks || {});
      } catch {}
    })();
    refreshPortfolio();
    refreshWatchlist();
    const id = setInterval(refreshPortfolio, 30000);
    return () => clearInterval(id);
  }, [user, refreshPortfolio, refreshWatchlist]);

  // Refresh sparks occasionally from REST (WebSocket only updates prices)
  useEffect(() => {
    if (!user) return;
    const id = setInterval(async () => {
      try {
        const { data } = await api.get("/prices");
        setSparks(data.sparks || {});
      } catch {}
    }, 30000);
    return () => clearInterval(id);
  }, [user]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ color: "var(--text-secondary)" }}>
        Loading…
      </div>
    );
  }
  if (!user) return <AuthPage />;

  return (
    <div className="min-h-screen pb-8">
      <Navbar portfolio={portfolio} marketStatus={marketStatus} tab={tab} setTab={setTab} />
      <IndicesTicker indices={indices} />
      {tab === "Market" && (
        <MarketTab
          prices={prices}
          sparks={sparks}
          stocks={stocks}
          portfolio={portfolio}
          marketStatus={marketStatus}
          watchlist={watchlist}
          onToggleWatch={toggleWatch}
          onPortfolioChange={(p) => setPortfolio(p)}
          onTickListener={onTick}
        />
      )}
      {tab === "Watchlist" && (
        <WatchlistTab
          prices={prices}
          sparks={sparks}
          stocks={stocks}
          portfolio={portfolio}
          marketStatus={marketStatus}
          watchlist={watchlist}
          onToggleWatch={toggleWatch}
          onPortfolioChange={(p) => setPortfolio(p)}
          onTickListener={onTick}
        />
      )}
      {tab === "Portfolio" && (
        <PortfolioTab
          portfolio={portfolio}
          prices={prices}
          stocks={stocks}
          marketStatus={marketStatus}
          onPortfolioChange={(p) => setPortfolio(p)}
        />
      )}
      {tab === "History" && <HistoryTab />}
      {tab === "Leaderboard" && <LeaderboardTab />}
      {tab === "Admin" && user?.isAdmin && <AdminTab />}

      {!connected && (
        <div
          className="fixed left-4 bottom-10 text-xs px-3 py-1.5 rounded-md"
          style={{ background: "var(--amber-dim)", color: "var(--amber)", border: "1px solid var(--amber)" }}
          data-testid="ws-disconnected"
        >
          Live feed reconnecting…
        </div>
      )}

      <div className="disclaimer">
        SCALE India Investment is a paper trading simulator for educational purposes only.
        No real money is involved. Prices sourced from NSE India.
      </div>
      <Toaster />
    </div>
  );
};

function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}

export default App;
