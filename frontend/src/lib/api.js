import axios from "axios";

const resolveBackendUrl = () => {
  const configured = process.env.REACT_APP_BACKEND_URL?.trim();
  if (configured) {
    return configured.replace(/\/$/, "");
  }
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin;
  }
  return "http://localhost:8001";
};

const BACKEND_URL = resolveBackendUrl();
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({ baseURL: API });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("scale_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export const wsUrl = (token) => {
  const u = new URL(BACKEND_URL);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  u.pathname = "/api/ws";
  u.searchParams.set("token", token);
  return u.toString();
};
