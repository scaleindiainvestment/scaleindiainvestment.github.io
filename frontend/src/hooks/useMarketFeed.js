import { useEffect, useRef, useState, useCallback } from "react";
import { wsUrl } from "../lib/api";

/**
 * Maintains a WebSocket connection, price map, market status and a tick event bus.
 */
export const useMarketFeed = (token) => {
  const [prices, setPrices] = useState({});
  const [marketStatus, setMarketStatus] = useState(null);
  const [indices, setIndices] = useState({});
  const [connected, setConnected] = useState(false);
  const tickListeners = useRef(new Set());
  const wsRef = useRef(null);
  const reconnectRef = useRef(null);

  const onTick = useCallback((fn) => {
    tickListeners.current.add(fn);
    return () => tickListeners.current.delete(fn);
  }, []);

  useEffect(() => {
    if (!token) return;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      const ws = new WebSocket(wsUrl(token));
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!stopped) {
          clearTimeout(reconnectRef.current);
          reconnectRef.current = setTimeout(connect, 3000);
        }
      };
      ws.onerror = () => { try { ws.close(); } catch {} };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === "PRICES") {
            setPrices(msg.data || {});
          } else if (msg.type === "MARKET_STATUS") {
            setMarketStatus(msg.data);
          } else if (msg.type === "INDICES") {
            setIndices(msg.data || {});
          } else if (msg.type === "TICK") {
            setPrices((prev) => {
              const p = prev[msg.symbol];
              if (!p) return prev;
              return {
                ...prev,
                [msg.symbol]: {
                  ...p,
                  price: msg.newPrice,
                  change: msg.change,
                  changePct: msg.changePct,
                  lastUpdated: msg.lastUpdated,
                },
              };
            });
            tickListeners.current.forEach((fn) => fn(msg));
          }
        } catch {}
      };
    };

    connect();
    return () => {
      stopped = true;
      clearTimeout(reconnectRef.current);
      try { wsRef.current?.close(); } catch {}
    };
  }, [token]);

  return { prices, marketStatus, indices, connected, onTick };
};
