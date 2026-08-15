import { useState } from "react";

const REGION_LABELS = { us: "🇺🇸 États-Unis", europe: "🇪🇺 Europe", asie: "🌏 Asie" };
const IMPACT_DOT = {
  high: "bg-fuchsia-400 shadow-[0_0_8px_rgba(232,121,249,0.8)]",
  medium: "bg-amber-300 shadow-[0_0_8px_rgba(252,211,77,0.8)]",
};
const glass = { background: "var(--glass-bg)", borderColor: "var(--glass-border)", backdropFilter: "blur(20px)" };

function MacroSummary({ summary, updatedAt }) {
  return (
    <div className="rounded-2xl border p-5" style={glass}>
      <h3 className="font-semibold mb-2 flex items-center gap-2 text-fuchsia-400">
        🌍 Situation macro &amp; géopolitique
      </h3>
      <p className="text-sm leading-relaxed" style={{ color: "var(--text-2)" }}>{summary}</p>
      {updatedAt && (
        <p className="text-xs mt-3" style={{ color: "var(--text-3)" }}>
          Mis à jour : {new Date(updatedAt).toLocaleString("fr-FR", { timeZone: "Africa/Dakar" })}
        </p>
      )}
    </div>
  );
}

const TM_CLASS_COLOR = {
  Compression: "var(--accent-cyan)",
  Accumulation: "var(--accent-emerald)",
  "Prise de profit": "var(--accent-fuchsia)",
  "Zone neutre": "var(--text-3)",
  Indisponible: "var(--text-3)",
};
function TraditionalMarketCard({ m }) {
  const color = TM_CLASS_COLOR[m.classification] || "var(--text-3)";
  const changeColor = (m.change_pct_1d ?? 0) >= 0 ? "var(--accent-emerald)" : "var(--accent-fuchsia)";
  return (
    <div className="rounded-xl border p-3" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)" }}>
      <div className="flex items-center justify-between mb-1 gap-2">
        <p className="text-sm font-semibold truncate" style={{ color: "var(--text-1)" }}>{m.name}</p>
        <span
          className="text-xs px-2 py-0.5 rounded-full border whitespace-nowrap flex-shrink-0"
          style={{ color, borderColor: `${color}66`, background: `${color}1a` }}
        >
          {m.classification}
        </span>
      </div>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="text-lg font-mono" style={{ color: "var(--text-1)" }}>{m.price ?? "—"}</span>
        <span className="text-xs font-mono" style={{ color: changeColor }}>
          {m.change_pct_1d > 0 ? "+" : ""}{m.change_pct_1d ?? "—"}%
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1 text-[11px]" style={{ color: "var(--text-3)" }}>
        <span>Support: {m.support ?? "—"}</span>
        <span>Résistance: {m.resistance ?? "—"}</span>
        <span>MA20: {m.ma20 ?? "—"}</span>
        <span>MA50: {m.ma50 ?? "—"}</span>
        <span className="col-span-2">RSI: {m.rsi ?? "—"}</span>
      </div>
    </div>
  );
}

// Regroupement par paires logiques pour permettre le scroll horizontal.
const TRADITIONAL_MARKET_GROUPS = [
  { title: "Dollar & Or", tickers: ["DXY (Dollar Index)", "Or (Gold)"] },
  { title: "Indices actions US", tickers: ["S&P 500", "Nasdaq 100"] },
  { title: "Pétrole & EUR/USD", tickers: ["Pétrole (WTI Crude)", "EUR/USD"] },
];
function TraditionalMarkets({ markets }) {
  if (!markets?.length) return null;
  const byName = Object.fromEntries(markets.map((m) => [m.name, m]));
  return (
    <div className="rounded-2xl border p-5" style={glass}>
      <h3 className="font-semibold mb-3 text-cyan-400">💹 Marché traditionnel</h3>
      <p className="text-xs mb-3 rounded-lg border border-amber-400/30 bg-amber-400/10 text-amber-300 px-3 py-2">
        ⚠️ Source Yahoo Finance (endpoint non-officiel, best-effort).
        <span className="lg:hidden"> Fais glisser horizontalement →</span>
      </p>
      <div className="flex gap-4 overflow-x-auto scrollbar-glass snap-x snap-mandatory pb-2 lg:grid lg:grid-cols-3 lg:overflow-visible lg:pb-0">
        {TRADITIONAL_MARKET_GROUPS.map((g) => (
          <div
            key={g.title}
            className="flex-shrink-0 w-80 lg:w-auto snap-start rounded-xl border p-3 space-y-3"
            style={{ borderColor: "var(--glass-border)", background: "var(--glass-bg-strong)" }}
          >
            <p className="text-xs uppercase tracking-widest" style={{ color: "var(--text-3)" }}>{g.title}</p>
            {g.tickers.map((name) => byName[name] && <TraditionalMarketCard key={name} m={byName[name]} />)}
          </div>
        ))}
      </div>
    </div>
  );
}

function NewsAccordion({ news }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-2xl border overflow-hidden" style={glass}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-4 text-left hover:opacity-80 transition"
      >
        <span className="font-semibold flex items-center gap-2 text-cyan-400">📰 Dernières news crypto</span>
        <span className="text-sm" style={{ color: "var(--text-3)" }}>{open ? "▲ réduire" : "▼ afficher"}</span>
      </button>
      {open && (
        <div className="px-5 pb-4 border-t pt-3" style={{ borderColor: "var(--glass-border)" }}>
          {news?.length ? (
            <div className="flex gap-3 overflow-x-auto scrollbar-glass snap-x snap-mandatory pb-2">
              {news.map((n, i) => (
                <a
                  key={i}
                  href={n.link}
                  target="_blank"
                  rel="noreferrer"
                  className="flex-shrink-0 w-64 snap-start rounded-xl border p-3 hover:opacity-90 transition block"
                  style={{ borderColor: "var(--glass-border)", background: "var(--glass-bg)" }}
                >
                  <p className="text-sm leading-snug mb-2" style={{ color: "var(--text-1)" }}>{n.title}</p>
                  <p className="text-xs" style={{ color: "var(--accent-cyan)" }}>
                    {n.source} <span style={{ color: "var(--text-3)" }}>{n.published ? `• ${n.published}` : ""}</span>
                  </p>
                </a>
              ))}
            </div>
          ) : (
            <p className="text-sm italic" style={{ color: "var(--text-3)" }}>Aucune actualité disponible pour le moment.</p>
          )}
        </div>
      )}
    </div>
  );
}

function EtfFlowChart({ label, data, color }) {
  if (!data?.length) return <p className="text-sm italic" style={{ color: "var(--text-3)" }}>Indisponible.</p>;
  const max = Math.max(...data.map((d) => Math.abs(d.net_flow_usd_m)), 1);
  const total = data.reduce((s, d) => s + d.net_flow_usd_m, 0);
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <p className="text-xs font-semibold" style={{ color: "var(--text-2)" }}>{label}</p>
        <p className="text-xs font-mono" style={{ color: total >= 0 ? "var(--accent-emerald)" : "var(--accent-fuchsia)" }}>
          {total >= 0 ? "+" : ""}{total.toFixed(0)}M$ (période)
        </p>
      </div>
      <div className="flex items-end gap-1.5">
        {data.map((d, i) => {
          const h = Math.max(4, (Math.abs(d.net_flow_usd_m) / max) * 40);
          const pos = d.net_flow_usd_m >= 0;
          return (
            <div key={i} className="flex flex-col items-center justify-end" style={{ height: 44 }} title={`${d.date}: ${d.net_flow_usd_m}M$`}>
              <div style={{ height: pos ? h : 2, width: 10, background: pos ? color : "transparent", borderRadius: "2px 2px 0 0" }} />
              <div style={{ height: 1, width: "100%", background: "var(--glass-border)" }} />
              <div style={{ height: pos ? 2 : h, width: 10, background: pos ? "transparent" : "var(--accent-fuchsia)", borderRadius: "0 0 2px 2px" }} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
function EtfFlows({ etfFlows }) {
  if (!etfFlows) return null;
  return (
    <div className="rounded-2xl border p-5" style={glass}>
      <h3 className="font-semibold mb-1 text-violet-400">🏦 Flux ETF Bitcoin / ETH / Solana</h3>
      <p className="text-xs mb-3" style={{ color: "var(--text-3)" }}>
        ⚠️ Compilé via recherche web (nécessite GROK_API_KEY) — à vérifier auprès de la source (SoSoValue/Farside).
      </p>
      {etfFlows.available ? (
        <div className="grid md:grid-cols-3 gap-6">
          <EtfFlowChart label="Bitcoin ETF" data={etfFlows.btc} color="var(--accent-amber)" />
          <EtfFlowChart label="Ethereum ETF" data={etfFlows.eth} color="var(--accent-cyan)" />
          <EtfFlowChart label="Solana ETF" data={etfFlows.sol} color="var(--accent-emerald)" />
        </div>
      ) : (
        <p className="text-sm italic" style={{ color: "var(--text-3)" }}>
          Indisponible (GROK_API_KEY non configurée ou recherche infructueuse).
        </p>
      )}
    </div>
  );
}

function EconomicCalendar({ calendar }) {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState("us");
  const events = calendar?.[tab] || [];

  return (
    <div className="rounded-2xl border overflow-hidden" style={glass}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-4 text-left hover:opacity-80 transition"
      >
        <span className="font-semibold flex items-center gap-2 text-violet-400">
          📅 Calendrier économique (7 jours)
        </span>
        <span className="text-sm" style={{ color: "var(--text-3)" }}>{open ? "▲ réduire" : "▼ afficher"}</span>
      </button>
      {open && (
        <div className="px-5 pb-5 border-t pt-3" style={{ borderColor: "var(--glass-border)" }}>
          <div className="flex gap-2 mb-3">
            {Object.entries(REGION_LABELS).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`text-xs px-3 py-1.5 rounded-full border transition ${
                  tab === key
                    ? "bg-gradient-to-r from-violet-500/25 to-cyan-500/25 border-[var(--glass-border-strong)]"
                    : "border-[var(--glass-border)] hover:border-[var(--glass-border-strong)]"
                }`}
                style={{ color: tab === key ? "var(--text-1)" : "var(--text-3)" }}
              >
                {label}
              </button>
            ))}
          </div>
          {events.length ? (
            <div className="space-y-1.5 max-h-64 overflow-y-auto scrollbar-glass">
              {events.map((e, i) => (
                <div
                  key={i}
                  className="flex items-center justify-between text-sm p-2.5 rounded-xl border"
                  style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)" }}
                >
                  <div className="flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${IMPACT_DOT[e.impact] || "bg-gray-400"}`} />
                    <span style={{ color: "var(--text-2)" }}>{e.title}</span>
                  </div>
                  <span className="text-xs" style={{ color: "var(--text-3)" }}>
                    {e.date} {e.time}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm italic" style={{ color: "var(--text-3)" }}>Aucun événement à impact élevé/moyen recensé.</p>
          )}
        </div>
      )}
    </div>
  );
}

export default function MarketContext({ context, loading }) {
  if (loading && !context) {
    return <p className="text-sm mb-6" style={{ color: "var(--text-3)" }}>Chargement du contexte marché...</p>;
  }
  if (!context) return null;

  return (
    <div className="space-y-4 mb-8">
      {/* 1. Situation macro & géopolitique — en premier */}
      <MacroSummary summary={context.macro_summary} updatedAt={context.updated_at} />
      {/* 2. Marché traditionnel — groupé par paires, scroll horizontal */}
      <TraditionalMarkets markets={context.traditional_markets} />
      {/* 3. News */}
      <NewsAccordion news={context.news} />
      {/* 4. Flux ETF */}
      <EtfFlows etfFlows={context.etf_flows} />
      {/* 5. Calendrier économique */}
      <EconomicCalendar calendar={context.calendar} />
    </div>
  );
}
