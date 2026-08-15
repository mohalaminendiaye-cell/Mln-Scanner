import { useState } from "react";
import Sparkline from "./Sparkline.jsx";

const EXCHANGE_COLOR = { OKX: "var(--accent-cyan)", Hyperliquid: "var(--accent-emerald)", Bybit: "var(--accent-amber)", Binance: "var(--accent-fuchsia)" };

function DirectionBadge({ direction }) {
  const isLong = direction === "Long";
  const color = isLong ? "var(--accent-emerald)" : "var(--accent-fuchsia)";
  return (
    <span
      className="text-xs font-semibold px-2.5 py-1 rounded-full border whitespace-nowrap flex-shrink-0"
      style={{ color, borderColor: `${color}66`, background: `${color}1a` }}
    >
      {isLong ? "LONG 🟢" : "SHORT 🔴"}
    </span>
  );
}

function ExchangeTag({ exchange }) {
  const color = EXCHANGE_COLOR[exchange] || "var(--text-3)";
  return (
    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ color, background: `${color}1a` }}>
      {exchange}
    </span>
  );
}

function TradeLevels({ s }) {
  return (
    <div className="grid grid-cols-3 gap-2 text-sm mb-2">
      <div className="rounded-lg border p-2" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)" }}>
        <p className="text-[10px]" style={{ color: "var(--text-3)" }}>Entrée</p>
        <p className="font-mono text-xs" style={{ color: "var(--text-1)" }}>{s.entry}</p>
      </div>
      <div className="rounded-lg border border-fuchsia-400/15 bg-fuchsia-400/5 p-2">
        <p className="text-[10px]" style={{ color: "var(--text-3)" }}>SL</p>
        <p className="font-mono text-xs" style={{ color: "var(--accent-fuchsia)" }}>{s.stop_loss}</p>
      </div>
      <div className="rounded-lg border border-emerald-400/15 bg-emerald-400/5 p-2">
        <p className="text-[10px]" style={{ color: "var(--text-3)" }}>TP</p>
        <p className="font-mono text-xs" style={{ color: "var(--accent-emerald)" }}>{s.take_profit}</p>
      </div>
    </div>
  );
}

function MarketMetrics({ s }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]" style={{ color: "var(--text-3)" }}>
      <span>Vol: <span style={{ color: s.volume_trend_pct >= 0 ? "var(--accent-emerald)" : "var(--accent-fuchsia)" }}>
        {s.volume_trend_pct > 0 ? "+" : ""}{s.volume_trend_pct}%
      </span></span>
      {s.open_interest_usd != null && (
        <span>OI: ${(s.open_interest_usd / 1e6).toFixed(1)}M
          {s.oi_change_24h_pct != null && ` (${s.oi_change_24h_pct > 0 ? "+" : ""}${s.oi_change_24h_pct}%)`}
        </span>
      )}
      {s.spread_pct != null && <span>Spread: {s.spread_pct}%</span>}
    </div>
  );
}

function LiquidationZones({ s }) {
  if (s.liquidation_long == null && s.liquidation_short == null) return null;
  return (
    <>
      <p className="text-[11px] mt-1" style={{ color: "var(--text-3)" }}>
        ⚠️ Liq. estimées — Long: <span style={{ color: "var(--accent-fuchsia)" }}>{s.liquidation_long}</span>
        {" "}| Short: <span style={{ color: "var(--accent-emerald)" }}>{s.liquidation_short}</span>
      </p>
      {s.liquidation_zones?.length > 0 && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] mt-1" style={{ color: "var(--text-3)" }}>
          {s.liquidation_zones.map((z) => (
            <span key={z.leverage}>
              {z.leverage}x: {z.long_price} / {z.short_price}
            </span>
          ))}
        </div>
      )}
    </>
  );
}

function ScoreLine({ score }) {
  return (
    <div className="text-sm mb-2" style={{ color: "var(--text-3)" }}>
      Score : <span style={{ color: "var(--text-1)" }} className="font-semibold">{score}/100</span>
    </div>
  );
}

function Cat7Card({ s }) {
  const sparkColor = s.direction === "Short" ? "var(--accent-fuchsia)" : "var(--accent-emerald)";
  return (
    <div className="rounded-xl border p-3" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)" }}>
      <div className="flex items-center justify-between mb-2 gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <ExchangeTag exchange={s.exchange} />
          <h4 className="font-bold truncate" style={{ color: "var(--text-1)" }}>{s.symbol}</h4>
        </div>
        <DirectionBadge direction={s.direction} />
      </div>
      <ScoreLine score={s.score} />
      {s.sparkline?.length > 1 && <div className="mb-2"><Sparkline data={s.sparkline} width={180} height={28} /></div>}
      <p className="text-xs mb-2" style={{ color: "var(--text-2)" }}>{s.trigger_reason}</p>
      <TradeLevels s={s} />
      <MarketMetrics s={s} />
      <LiquidationZones s={s} />
    </div>
  );
}

function Cat9Card({ s }) {
  return (
    <div
      className="rounded-xl border p-3"
      style={{
        background: "var(--glass-bg)",
        borderColor: s.is_fallback ? "var(--accent-amber)66" : "var(--glass-border)",
      }}
    >
      <div className="flex items-center justify-between mb-2 gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <ExchangeTag exchange={s.exchange} />
          <h4 className="font-bold truncate" style={{ color: "var(--text-1)" }}>{s.symbol}</h4>
        </div>
        <DirectionBadge direction={s.direction} />
      </div>
      {s.is_fallback && (
        <p
          className="text-[10px] font-semibold mb-2 px-2 py-1 rounded-lg border"
          style={{ color: "var(--accent-amber)", borderColor: "var(--accent-amber)44", background: "var(--accent-amber)14" }}
        >
          ⚠️ Score sous le seuil de 65/100 — affiché à titre indicatif (repli)
        </p>
      )}
      <ScoreLine score={s.score} />
      <p className="text-xs mb-2 font-semibold" style={{ color: "var(--accent-cyan)" }}>{s.fib_level_label} · score ≥ 65/100</p>
      <p className="text-xs mb-2" style={{ color: "var(--text-2)" }}>{s.trigger_reason}</p>
      <TradeLevels s={s} />
      <MarketMetrics s={s} />
      <LiquidationZones s={s} />
    </div>
  );
}

function ExchangeTabs({ exchanges, active, onChange }) {
  return (
    <div className="flex gap-2 mb-3">
      {exchanges.map((ex) => (
        <button
          key={ex}
          onClick={() => onChange(ex)}
          className={`text-xs px-3 py-1.5 rounded-full border transition ${
            active === ex
              ? "bg-gradient-to-r from-violet-500/25 to-cyan-500/25 border-[var(--glass-border-strong)]"
              : "border-[var(--glass-border)] hover:border-[var(--glass-border-strong)]"
          }`}
          style={{ color: active === ex ? "var(--text-1)" : "var(--text-3)" }}
        >
          {ex}
        </button>
      ))}
    </div>
  );
}

function CardGrid({ items, CardComponent, emptyText }) {
  if (!items?.length) return <p className="text-sm italic" style={{ color: "var(--text-3)" }}>{emptyText}</p>;
  return (
    <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
      {items.map((s, i) => (
        <CardComponent key={`${s.exchange}-${s.symbol}-${i}`} s={s} />
      ))}
    </div>
  );
}

export function Category7({ data }) {
  const exchanges = Object.keys(data || {});
  const [active, setActive] = useState(exchanges[0] || "Bybit");
  return (
    <div className="rounded-2xl border p-5 mb-4" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)", backdropFilter: "blur(20px)" }}>
      <h3 className="text-lg font-bold mb-1 bg-gradient-to-r from-emerald-400 via-cyan-400 to-violet-400 bg-clip-text text-transparent">
        🚀 Catégorie 7 — Mouvements imminents à haute probabilité (4h)
      </h3>
      <p className="text-xs mb-3" style={{ color: "var(--text-3)" }}>
        Compression de volatilité + sursaut d'Open Interest + volume, sur un horizon de 4h — Bybit & OKX, en REST (pas de flux WebSocket persistant).
      </p>
      <ExchangeTabs exchanges={exchanges} active={active} onChange={setActive} />
      <CardGrid items={data?.[active]} CardComponent={Cat7Card} emptyText="Aucun signal qualifié sur cet exchange pour ce scan." />
    </div>
  );
}

function Category9({ data }) {
  const exchanges = Object.keys(data || {});
  const [active, setActive] = useState(exchanges[0] || "Binance");
  const current = data?.[active] || { retracement_050: [], golden_pocket: [] };
  const isFallback050 = current.retracement_050?.length > 0 && current.retracement_050.every((s) => s.is_fallback);
  const isFallbackGP = current.golden_pocket?.length > 0 && current.golden_pocket.every((s) => s.is_fallback);
  return (
    <div className="rounded-2xl border p-5" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)", backdropFilter: "blur(20px)" }}>
      <h3 className="text-lg font-bold mb-1 bg-gradient-to-r from-cyan-400 via-violet-400 to-fuchsia-400 bg-clip-text text-transparent">
        📐 Catégorie 9 — Stratégie Fib
      </h3>
      <p className="text-xs mb-3" style={{ color: "var(--text-3)" }}>
        Fibonacci (0.50 / Golden Pocket sur impulsion H4) + Market Structure + Liquidity Sweep + Volume Profile
        + VWAP + Delta/CVD + Footprint (proxy) — score de confluence ≥ 65/100 requis.
      </p>
      <ExchangeTabs exchanges={exchanges} active={active} onChange={setActive} />
      <div className="mb-4">
        <h4 className="text-sm font-semibold mb-2" style={{ color: "var(--text-2)" }}>9.1 — Retracement 0.50</h4>
        {isFallback050 && (
          <p className="text-xs mb-2 px-3 py-2 rounded-lg border" style={{ color: "var(--accent-amber)", borderColor: "var(--accent-amber)44", background: "var(--accent-amber)14" }}>
            ⚠️ Aucun candidat n'a atteint 65/100 — top scores entre 40 et 65 affichés à titre indicatif.
          </p>
        )}
        <CardGrid items={current.retracement_050} CardComponent={Cat9Card} emptyText="Aucun retracement 0.50 qualifié (ni même le seuil de repli à 40)." />
      </div>
      <div>
        <h4 className="text-sm font-semibold mb-2" style={{ color: "var(--text-2)" }}>9.2 — Golden Pocket (0.618 - 0.786)</h4>
        {isFallbackGP && (
          <p className="text-xs mb-2 px-3 py-2 rounded-lg border" style={{ color: "var(--accent-amber)", borderColor: "var(--accent-amber)44", background: "var(--accent-amber)14" }}>
            ⚠️ Aucun candidat n'a atteint 65/100 — top scores entre 40 et 65 affichés à titre indicatif.
          </p>
        )}
        <CardGrid items={current.golden_pocket} CardComponent={Cat9Card} emptyText="Aucune Golden Pocket qualifiée (ni même le seuil de repli à 40)." />
      </div>
    </div>
  );
}

export default function MultiExchangeMovers({ category9 }) {
  return (
    <div>
      <p className="text-xs mb-4 rounded-xl border border-amber-400/30 bg-amber-400/10 text-amber-300 px-4 py-3">
        ⚠️ Intégrations Binance / Bybit : données via REST à chaque scan. Les zones de liquidation
        affichées sont des estimations heuristiques (funding + effet de levier), pas un flux réel.
      </p>
      <Category9 data={category9} />
    </div>
  );
}
