import { useEffect, useState, useCallback } from "react";
import { getBacktestStats, getRecentOutcomes, getBacktestCategories } from "../api.js";

const glass = { background: "var(--glass-bg)", borderColor: "var(--glass-border)", backdropFilter: "blur(20px)" };
const STATUS_STYLE = {
  win: "text-[var(--accent-emerald)] border-emerald-400/40 bg-emerald-400/10",
  loss: "text-[var(--accent-fuchsia)] border-fuchsia-400/40 bg-fuchsia-400/10",
  expired: "text-[var(--accent-amber)] border-amber-400/40 bg-amber-400/10",
};
const STATUS_LABEL = { win: "TP touché", loss: "SL touché", expired: "Expiré" };
const CATEGORY_LABELS = {
  probabilite_mouvement: "Cat.1 Mouvement",
  chop_eleve: "Cat.2 Range/Chop",
  gsb_breakout: "Cat.10 Breakout Global (GSB)",
};
const PERIOD_LABELS = { day: "Jour", week: "Semaine", month: "Mois", all: "Tout" };

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short", timeZone: "Africa/Dakar" });
}

function TradeRow({ o, expanded, onToggle }) {
  const pnlColor = o.status === "win" ? "var(--accent-emerald)" : o.status === "loss" ? "var(--accent-fuchsia)" : "var(--accent-amber)";
  return (
    <div className="rounded-lg overflow-hidden" style={{ background: "var(--glass-bg)" }}>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between text-sm p-2.5 hover:opacity-80 transition text-left"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-medium truncate" style={{ color: "var(--text-1)" }}>{o.symbol}</span>
          <span style={{ color: "var(--text-3)" }} className="text-xs whitespace-nowrap">
            {CATEGORY_LABELS[o.category] || o.category} · {o.direction}
          </span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_STYLE[o.status] || ""}`}>
            {STATUS_LABEL[o.status] || o.status}
          </span>
          <span className="text-xs" style={{ color: "var(--text-3)" }}>{expanded ? "▲" : "▼"}</span>
        </div>
      </button>
      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t" style={{ borderColor: "var(--glass-border)" }}>
          <div className="grid grid-cols-3 gap-2 text-sm my-2">
            <div className="rounded-lg border p-2" style={{ background: "var(--glass-bg-strong)", borderColor: "var(--glass-border)" }}>
              <p className="text-[10px]" style={{ color: "var(--text-3)" }}>Prix d'entrée</p>
              <p className="font-mono" style={{ color: "var(--text-1)" }}>{o.entry}</p>
            </div>
            <div className="rounded-lg border border-fuchsia-400/15 bg-fuchsia-400/5 p-2">
              <p className="text-[10px]" style={{ color: "var(--text-3)" }}>Stop Loss</p>
              <p className="font-mono" style={{ color: "var(--accent-fuchsia)" }}>{o.stop_loss}</p>
            </div>
            <div className="rounded-lg border border-emerald-400/15 bg-emerald-400/5 p-2">
              <p className="text-[10px]" style={{ color: "var(--text-3)" }}>Take Profit</p>
              <p className="font-mono" style={{ color: "var(--accent-emerald)" }}>{o.take_profit}</p>
            </div>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ color: "var(--text-3)" }}>
            <span>Ouvert : {fmtDate(o.opened_at)}</span>
            <span>Clôturé : {fmtDate(o.closed_at)}</span>
            {o.exit_price != null && (
              <span>Prix de sortie : <span style={{ color: pnlColor }} className="font-mono">{o.exit_price}</span></span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Performance() {
  const [stats, setStats] = useState(null);
  const [recent, setRecent] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [categoryFilter, setCategoryFilter] = useState("");
  const [periodFilter, setPeriodFilter] = useState("all");
  const [expandedId, setExpandedId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, r] = await Promise.all([
        getBacktestStats(categoryFilter || undefined, periodFilter),
        getRecentOutcomes(30, categoryFilter || undefined, periodFilter),
      ]);
      setStats(s);
      setRecent(r);
    } catch (e) {
      // silencieux
    } finally {
      setLoading(false);
    }
  }, [categoryFilter, periodFilter]);

  useEffect(() => {
    getBacktestCategories().then(setCategories).catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="space-y-6">
      <h2 className="text-xl font-bold bg-gradient-to-r from-emerald-400 via-cyan-400 to-fuchsia-400 bg-clip-text text-transparent">
        📊 Performance des signaux (backtest)
      </h2>

      {/* Filtres */}
      <div className="rounded-2xl border p-4 flex flex-wrap items-center gap-3" style={glass}>
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: "var(--text-3)" }}>Catégorie :</span>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="text-xs rounded-lg border px-2 py-1.5 outline-none"
            style={{ background: "var(--glass-bg-strong)", borderColor: "var(--glass-border)", color: "var(--text-1)" }}
          >
            <option value="">Toutes</option>
            {categories.map((c) => (
              <option key={c} value={c}>{CATEGORY_LABELS[c] || c}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs" style={{ color: "var(--text-3)" }}>Période :</span>
          <div className="flex gap-1">
            {Object.entries(PERIOD_LABELS).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setPeriodFilter(key)}
                className={`text-xs px-3 py-1.5 rounded-full border transition ${
                  periodFilter === key
                    ? "bg-gradient-to-r from-violet-500/25 to-cyan-500/25 border-[var(--glass-border-strong)]"
                    : "border-[var(--glass-border)] hover:border-[var(--glass-border-strong)]"
                }`}
                style={{ color: periodFilter === key ? "var(--text-1)" : "var(--text-3)" }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {loading ? (
        <p style={{ color: "var(--text-3)" }}>Chargement des statistiques...</p>
      ) : stats && stats.total_closed > 0 ? (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="rounded-2xl border p-4 text-center" style={glass}>
              <p className="text-2xl font-bold" style={{ color: "var(--text-1)" }}>{stats.win_rate_pct}%</p>
              <p className="text-xs mt-1" style={{ color: "var(--text-3)" }}>Taux de réussite</p>
            </div>
            <div className="rounded-2xl border p-4 text-center" style={glass}>
              <p className="text-2xl font-bold" style={{ color: "var(--accent-emerald)" }}>{stats.wins}</p>
              <p className="text-xs mt-1" style={{ color: "var(--text-3)" }}>Gagnants (TP touché)</p>
            </div>
            <div className="rounded-2xl border p-4 text-center" style={glass}>
              <p className="text-2xl font-bold" style={{ color: "var(--accent-fuchsia)" }}>{stats.losses}</p>
              <p className="text-xs mt-1" style={{ color: "var(--text-3)" }}>Perdants (SL touché)</p>
            </div>
            <div className="rounded-2xl border p-4 text-center" style={glass}>
              <p className="text-2xl font-bold" style={{ color: "var(--accent-amber)" }}>{stats.expired}</p>
              <p className="text-xs mt-1" style={{ color: "var(--text-3)" }}>Expirés</p>
            </div>
          </div>

          {Object.keys(stats.by_category).length > 0 && (
            <div className="rounded-2xl border p-5" style={glass}>
              <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-2)" }}>Par catégorie</h3>
              <div className="space-y-2">
                {Object.entries(stats.by_category).map(([cat, data]) => (
                  <div key={cat} className="flex items-center justify-between text-sm">
                    <span style={{ color: "var(--text-2)" }}>{CATEGORY_LABELS[cat] || cat}</span>
                    <span style={{ color: "var(--text-1)" }} className="font-mono">
                      {data.win_rate_pct}% ({data.wins}/{data.total})
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="rounded-2xl border p-5" style={glass}>
            <h3 className="text-sm font-semibold mb-1" style={{ color: "var(--text-2)" }}>Signaux clôturés</h3>
            <p className="text-xs mb-3" style={{ color: "var(--text-3)" }}>Clique sur un trade pour voir l'entrée, le SL et le TP.</p>
            <div className="space-y-1.5">
              {recent.map((o) => (
                <TradeRow
                  key={o.id}
                  o={o}
                  expanded={expandedId === o.id}
                  onToggle={() => setExpandedId(expandedId === o.id ? null : o.id)}
                />
              ))}
            </div>
          </div>
        </>
      ) : (
        <p className="italic" style={{ color: "var(--text-3)" }}>
          Aucun signal clôturé pour ces filtres. Les signaux sont suivis automatiquement pendant 48h
          après chaque scan — reviens dans quelques jours, ou élargis la période.
        </p>
      )}
    </section>
  );
}
