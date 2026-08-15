// En dev/preview, si VITE_API_URL n'est pas fourni, on déduit l'hôte de l'API à partir
// de celui utilisé pour charger la page plutôt que de coder "localhost" en dur. Sans ça,
// ouvrir l'app depuis un iPhone via l'IP LAN du PC (ex: http://192.168.1.50:5173)
// pointerait les appels API vers "localhost:8000" DE L'IPHONE (donc rien), pas vers le PC.
const API_BASE = import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.hostname}:8000`;

// Clé API optionnelle (miroir de API_KEY côté backend, voir main.py). Vide par défaut :
// si le backend n'a pas non plus de API_KEY configurée, tout fonctionne comme avant.
// Si le backend EXIGE une clé, il faut la fournir ici au moment du build
// (`VITE_API_KEY=... npm run build`) pour que les actions (scan manuel, watchlist,
// test de notifications) fonctionnent.
const API_KEY = import.meta.env.VITE_API_KEY || "";

function authHeaders() {
  return API_KEY ? { "X-API-Key": API_KEY } : {};
}

async function handle(resp) {
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`API error ${resp.status}: ${body}`);
  }
  return resp.json();
}

export async function getLatestScan() {
  const resp = await fetch(`${API_BASE}/api/scan/latest`);
  return handle(resp);
}

export async function getHistory(limit = 30) {
  const resp = await fetch(`${API_BASE}/api/scan/history?limit=${limit}`);
  return handle(resp);
}

export async function getScanDetail(id) {
  const resp = await fetch(`${API_BASE}/api/scan/${id}`);
  return handle(resp);
}

export async function triggerScan() {
  const resp = await fetch(`${API_BASE}/api/scan/run`, { method: "POST", headers: authHeaders() });
  return handle(resp);
}

export async function testNotifications() {
  const resp = await fetch(`${API_BASE}/api/notifications/test`, { method: "POST", headers: authHeaders() });
  return handle(resp);
}

export async function getMarketContext(refresh = false) {
  const resp = await fetch(`${API_BASE}/api/context?refresh=${refresh}`);
  return handle(resp);
}

export async function getWatchlist() {
  const resp = await fetch(`${API_BASE}/api/watchlist`);
  return handle(resp);
}

export async function addToWatchlist(symbol) {
  const resp = await fetch(`${API_BASE}/api/watchlist/${symbol}`, { method: "POST", headers: authHeaders() });
  return handle(resp);
}

export async function removeFromWatchlist(symbol) {
  const resp = await fetch(`${API_BASE}/api/watchlist/${symbol}`, { method: "DELETE", headers: authHeaders() });
  return handle(resp);
}

export async function getBacktestStats(category, period) {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (period) params.set("period", period);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const resp = await fetch(`${API_BASE}/api/backtest/stats${qs}`);
  return handle(resp);
}

export async function getRecentOutcomes(limit = 20, category, period) {
  const params = new URLSearchParams({ limit });
  if (category) params.set("category", category);
  if (period) params.set("period", period);
  const resp = await fetch(`${API_BASE}/api/backtest/recent?${params.toString()}`);
  return handle(resp);
}

export async function getBacktestCategories() {
  const resp = await fetch(`${API_BASE}/api/backtest/categories`);
  return handle(resp);
}

export async function getConfig() {
  const resp = await fetch(`${API_BASE}/api/config`);
  return handle(resp);
}
