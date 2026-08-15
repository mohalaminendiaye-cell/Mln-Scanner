import Sparkline from "./Sparkline.jsx";

const EXCHANGE_COLOR = { Binance: "var(--accent-amber)", Bybit: "var(--accent-cyan)" };

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

function GSBGauge({ score, isFallback }) {
  const color = isFallback
    ? "var(--accent-amber)"
    : score >= 85 ? "var(--accent-emerald)" : score >= 75 ? "var(--accent-cyan)" : "var(--accent-amber)";
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-2 rounded-full overflow-hidden" style={{ background: "var(--glass-bg-strong)" }}>
        <div className="h-full rounded-full" style={{ width: `${score}%`, background: color }} />
      </div>
      <span className="font-mono font-bold text-sm" style={{ color }}>{score}</span>
    </div>
  );
}

function SubScore({ label, value }) {
  return (
    <div className="rounded-lg border p-1.5 text-center" style={{ background: "var(--glass-bg-strong)", borderColor: "var(--glass-border)" }}>
      <p className="text-[9px]" style={{ color: "var(--text-3)" }}>{label}</p>
      <p className="text-xs font-mono font-semibold" style={{ color: "var(--text-1)" }}>{value}</p>
    </div>
  );
}

function GSBCard({ s }) {
  const sparkColor = s.direction === "Short" ? "var(--accent-fuchsia)" : "var(--accent-emerald)";
  const exColor = EXCHANGE_COLOR[s.exchange] || "var(--text-3)";
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
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ color: exColor, background: `${exColor}1a` }}>
            {s.exchange}
          </span>
          <h4 className="font-bold truncate" style={{ color: "var(--text-1)" }}>{s.symbol}</h4>
        </div>
        <DirectionBadge direction={s.direction} />
      </div>

      {s.is_fallback && (
        <p
          className="text-[10px] font-semibold mb-2 px-2 py-1 rounded-lg border"
          style={{ color: "var(--accent-amber)", borderColor: "var(--accent-amber)44", background: "var(--accent-amber)14" }}
        >
          ⚠️ Score sous le seuil GSB ≥ 60 — affiché à titre indicatif (repli)
        </p>
      )}

      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] uppercase tracking-wide" style={{ color: "var(--text-3)" }}>Global Breakout Score</span>
        <GSBGauge score={s.gsb_score} isFallback={s.is_fallback} />
      </div>

      {s.sparkline?.length > 1 && <div className="mb-2"><Sparkline data={s.sparkline} width={220} height={30} /></div>}

      <div className="grid grid-cols-5 gap-1 mb-2">
        <SubScore label="VSI" value={s.vsi_score} />
        <SubScore label="RVOL" value={s.rvol_score} />
        <SubScore label="OIFD" value={s.oifd_score} />
        <SubScore label="MSD" value={s.msd_score} />
        <SubScore label="CORR" value={s.corr_score} />
      </div>

      <p className="text-xs mb-2" style={{ color: "var(--text-2)" }}>{s.trigger_reason}</p>

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

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]" style={{ color: "var(--text-3)" }}>
        <span>Vol: <span style={{ color: s.volume_trend_pct >= 0 ? "var(--accent-emerald)" : "var(--accent-fuchsia)" }}>
          {s.volume_trend_pct > 0 ? "+" : ""}{s.volume_trend_pct}%
        </span></span>
        {s.open_interest_usd != null && (
          <span>OI: ${(s.open_interest_usd / 1e6).toFixed(1)}M{s.oi_change_pct != null ? ` (${s.oi_change_pct > 0 ? "+" : ""}${s.oi_change_pct}%)` : ""}</span>
        )}
        {s.spread_pct != null && <span>Spread: {s.spread_pct}%</span>}
        {s.beta_btc != null && <span>β BTC: {s.beta_btc}</span>}
        {s.key_level_label && <span>Niveau clé: {s.key_level_label} ({s.key_level_distance_pct}%)</span>}
      </div>
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
    </div>
  );
}

export default function Category10({ signals }) {
  const isFallback = signals?.length > 0 && signals.every((s) => s.is_fallback);
  return (
    <div className="rounded-2xl border p-5 mb-4" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)", backdropFilter: "blur(20px)" }}>
      <h3 className="text-lg font-bold mb-1 bg-gradient-to-r from-cyan-400 via-emerald-400 to-amber-400 bg-clip-text text-transparent">
        🎯 Catégorie 10 — Global Breakout Score
      </h3>
      <p className="text-xs mb-3" style={{ color: "var(--text-3)" }}>
        Score composite (VSI + RVOL + OIFD + MSD + CORR) sur Binance Futures & Bybit Futures — seuil GSB ≥ 60, top 5 toutes exchanges confondues.
      </p>
      {isFallback && (
        <p
          className="text-xs mb-3 px-3 py-2 rounded-lg border"
          style={{ color: "var(--accent-amber)", borderColor: "var(--accent-amber)44", background: "var(--accent-amber)14" }}
        >
          ⚠️ Aucune paire n'a atteint le seuil GSB ≥ 60 sur ce scan. Voici les 5 meilleurs scores entre 40 et 60,
          à titre indicatif — ce ne sont pas des setups qualifiés, juste les plus proches du seuil.
        </p>
      )}
      {signals?.length ? (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
          {signals.map((s) => (
            <GSBCard key={`${s.exchange}-${s.symbol}`} s={s} />
          ))}
        </div>
      ) : (
        <p className="text-sm italic" style={{ color: "var(--text-3)" }}>
          Aucune paire n'a franchi le seuil GSB ≥ 60 pour ce scan (ni même le seuil de repli à 40).
        </p>
      )}
    </div>
  );
}
