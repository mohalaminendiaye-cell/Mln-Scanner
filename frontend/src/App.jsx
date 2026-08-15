import { useEffect, useState, useCallback } from "react";
import { getLatestScan, getHistory, getScanDetail, triggerScan, testNotifications, getMarketContext, getConfig } from "./api.js";
import History from "./components/History.jsx";
import MarketContext from "./components/MarketContext.jsx";
import TabNav from "./components/TabNav.jsx";
import SummaryBar from "./components/SummaryBar.jsx";
import Signals from "./components/Signals.jsx";
import TopMovers from "./components/TopMovers.jsx";
import Watchlist from "./components/Watchlist.jsx";
import Performance from "./components/Performance.jsx";
import Skeleton from "./components/Skeleton.jsx";

const SCAN_HOURS = ["09h00", "14h00", "17h00", "21h00", "01h00"];

export default function App() {
  const [scan, setScan] = useState(null);
  const [history, setHistory] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);
  const [notifMsg, setNotifMsg] = useState(null);
  const [context, setContext] = useState(null);
  const [contextLoading, setContextLoading] = useState(true);
  const [activeTab, setActiveTab] = useState("overview");
  const [config, setConfig] = useState(null);

  const loadConfig = useCallback(async () => {
    try {
      const data = await getConfig();
      setConfig(data);
    } catch (e) {
      // silencieux : la bannière ne s'affiche simplement pas si /api/config échoue
    }
  }, []);

  const loadLatest = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getLatestScan();
      setScan(data);
      setSelectedId(data.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const data = await getHistory(30);
      setHistory(data);
    } catch (e) {
      // silencieux : l'historique n'est pas critique
    }
  }, []);

  const loadContext = useCallback(async () => {
    setContextLoading(true);
    try {
      const data = await getMarketContext();
      setContext(data);
    } catch (e) {
      // silencieux
    } finally {
      setContextLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLatest();
    loadHistory();
    loadContext();
    loadConfig();
    const interval = setInterval(loadHistory, 5 * 60 * 1000);
    const contextInterval = setInterval(loadContext, 15 * 60 * 1000);
    return () => {
      clearInterval(interval);
      clearInterval(contextInterval);
    };
  }, [loadLatest, loadHistory, loadContext, loadConfig]);

  const handleSelect = async (id) => {
    setSelectedId(id);
    setLoading(true);
    try {
      const data = await getScanDetail(id);
      setScan(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleManualScan = async () => {
    setScanning(true);
    setError(null);
    try {
      const data = await triggerScan();
      setScan(data);
      setSelectedId(data.id);
      await loadHistory();
    } catch (e) {
      setError(e.message);
    } finally {
      setScanning(false);
    }
  };

  const handleTestNotifications = async () => {
    setNotifMsg(null);
    try {
      const res = await testNotifications();
      const channels = Object.entries(res.sent)
        .filter(([, v]) => v)
        .map(([k]) => k)
        .join(", ");
      setNotifMsg(`✅ Message de test envoyé sur : ${channels}`);
    } catch (e) {
      setNotifMsg(`❌ ${e.message}`);
    }
  };

  return (
    <div className="min-h-screen relative overflow-x-hidden">
      {/* Orbes lumineux d'arrière-plan (glassmorphism) */}
      <div className="fixed inset-0 z-0 pointer-events-none overflow-hidden">
        <div className="orb absolute w-[550px] h-[550px] rounded-full bg-violet-600/30 blur-[110px] -top-40 -left-40" />
        <div
          className="orb absolute w-[500px] h-[500px] rounded-full bg-cyan-500/25 blur-[110px] top-1/3 -right-40"
          style={{ animationDelay: "-4s" }}
        />
        <div
          className="orb absolute w-[500px] h-[500px] rounded-full bg-fuchsia-600/25 blur-[110px] -bottom-40 left-1/4"
          style={{ animationDelay: "-8s" }}
        />
      </div>

      <div className="relative z-10">
        <header
          className="border-b px-6 py-5 flex flex-wrap items-center justify-between gap-3 sticky top-0 z-20"
          style={{ background: "var(--header-bg)", borderColor: "var(--glass-border)", backdropFilter: "blur(20px)" }}
        >
          <div>
            <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight bg-gradient-to-r from-cyan-400 via-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
              📈 MLN Scan
            </h1>
            <p className="text-sm mt-1" style={{ color: "var(--text-3)" }}>
              Scans automatiques (Binance Futures) à {SCAN_HOURS.join(" • ")} (heure de Dakar / GMT)
            </p>
            {notifMsg && <p className="text-xs mt-1" style={{ color: "var(--text-2)" }}>{notifMsg}</p>}
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleTestNotifications}
              className="rounded-xl border px-4 py-2 text-sm font-semibold transition hover:opacity-80"
              style={{ borderColor: "var(--glass-border)", background: "var(--glass-bg)", color: "var(--text-1)" }}
            >
              Tester Telegram
            </button>
            <button
              onClick={handleManualScan}
              disabled={scanning}
              className="rounded-xl border border-cyan-400/30 bg-gradient-to-r from-violet-500/30 to-cyan-500/30 hover:from-violet-500/40 hover:to-cyan-500/40 disabled:opacity-50 text-white px-4 py-2 text-sm font-semibold transition shadow-[0_0_20px_rgba(6,182,212,0.15)]"
            >
              {scanning ? "Scan en cours..." : "Lancer un scan manuel"}
            </button>
          </div>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-6 p-6 max-w-[1600px] mx-auto">
          <aside>
            <History items={history} onSelect={handleSelect} selectedId={selectedId} />
          </aside>

          <main className="min-w-0">
            {error && (
              <div className="rounded-xl border border-red-400/30 bg-red-400/10 backdrop-blur-xl text-red-300 p-4 mb-4 text-sm">
                {error}
              </div>
            )}

            <TabNav active={activeTab} onChange={setActiveTab} />

            {config && !config.ai_features_enabled && (activeTab === "overview" || activeTab === "signals") && (
              <div
                className="rounded-xl border border-amber-400/30 bg-amber-400/10 text-amber-300 text-sm px-4 py-3 mb-4"
              >
                ⚠️ Fonctionnalités IA désactivées — configurez <code>GROK_API_KEY</code> dans{" "}
                <code>backend/.env</code> pour activer le Bonus Trading (pics sociaux, via X/Twitter)
                et les flux ETF. Sans cette clé, ces sections restent vides plutôt que d'afficher
                des données inventées.
              </div>
            )}

            {activeTab === "overview" && (
              <div>
                {loading ? <Skeleton count={3} /> : <SummaryBar scan={scan} />}
                <MarketContext context={context} loading={contextLoading} />
              </div>
            )}

            {activeTab === "signals" && <Signals scan={scan} loading={loading} />}

            {activeTab === "movers" &&
              (loading ? <Skeleton count={6} /> : (
                <TopMovers
                  category2={scan?.category2}
                  category9={scan?.category9}
                />
              ))}

            {activeTab === "watchlist" && <Watchlist />}

            {activeTab === "performance" && <Performance />}
          </main>
        </div>
      </div>
    </div>
  );
}
