import Sparkline from "./Sparkline.jsx";

const DIRECTION_STYLES = {
  Long: "text-[var(--accent-emerald)] border-emerald-400/40 bg-emerald-400/10 shadow-[0_0_14px_rgba(52,211,153,0.35)]",
  Short: "text-[var(--accent-fuchsia)] border-fuchsia-400/40 bg-fuchsia-400/10 shadow-[0_0_14px_rgba(232,121,249,0.35)]",
  "Neutre": "text-[var(--accent-amber)] border-amber-400/40 bg-amber-400/10 shadow-[0_0_14px_rgba(251,191,36,0.35)]",
};

const BADGE_COLORS = { orange: "#fb923c", yellow: "#fde047", white: "#ffffff" };

function BadgeIcon({ shape, color, title }) {
  const bg = BADGE_COLORS[color] || "#ffffff";
  if (shape === "triangle") {
    return (
      <span
        title={title}
        style={{
          display: "inline-block", width: 0, height: 0,
          borderLeft: "5px solid transparent", borderRight: "5px solid transparent",
          borderBottom: `8px solid ${bg}`, filter: "drop-shadow(0 0 2px rgba(255,255,255,0.6))",
        }}
      />
    );
  }
  return (
    <span
      title={title}
      className={shape === "circle" ? "rounded-full" : "rounded-[2px]"}
      style={{ display: "inline-block", width: 9, height: 9, background: bg }}
    />
  );
}

export default function AssetCard({ asset, rank, badges }) {
  const dirClasses = DIRECTION_STYLES[asset.direction] || DIRECTION_STYLES["Neutre"];

  return (
    <div
      className="rounded-2xl border p-4 transition duration-300 hover:border-[var(--glass-border-strong)] hover:shadow-[0_0_30px_rgba(124,58,237,0.25)] hover:-translate-y-0.5"
      style={{
        background: "var(--glass-bg)",
        borderColor: asset.is_fallback ? "var(--accent-amber)66" : "var(--glass-border)",
        backdropFilter: "blur(20px)",
      }}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono" style={{ color: "var(--text-3)" }}>#{rank}</span>
          <h3 className="text-lg font-bold tracking-tight">{asset.symbol}</h3>
          {badges?.length > 0 && (
            <div className="flex items-center gap-1 ml-0.5">
              {badges.map((b, i) => (
                <BadgeIcon key={i} shape={b.shape} color={b.color} title={b.title} />
              ))}
            </div>
          )}
        </div>
        <span className={`text-xs font-semibold px-2.5 py-1 rounded-full border ${dirClasses}`}>
          {asset.direction}
        </span>
      </div>

      {asset.is_fallback && (
        <p
          className="text-[10px] font-semibold mb-2 px-2 py-1 rounded-lg border"
          style={{ color: "var(--accent-amber)", borderColor: "var(--accent-amber)44", background: "var(--accent-amber)14" }}
        >
          ⚠️ Score sous le seuil qualifié — affiché à titre indicatif (repli)
        </p>
      )}

      {asset.sparkline?.length > 1 && (
        <div className="mb-3 flex justify-end">
          <Sparkline data={asset.sparkline} />
        </div>
      )}

      <div className="text-sm mb-3" style={{ color: "var(--text-3)" }}>
        Score : <span style={{ color: "var(--text-1)" }} className="font-semibold">{asset.score}/100</span>
        <span className="mx-2 opacity-30">•</span>
        R:R <span style={{ color: "var(--text-1)" }} className="font-semibold">1:{asset.risk_reward}</span>
      </div>

      <div className="mb-3">
        <p className="text-[11px] uppercase tracking-widest mb-1" style={{ color: "var(--text-3)" }}>
          Déclencheur ({asset.trigger_type})
        </p>
        <p className="text-sm leading-relaxed" style={{ color: "var(--text-2)" }}>{asset.trigger_reason}</p>
      </div>

      <div className="grid grid-cols-3 gap-2 text-sm mb-3">
        <div className="rounded-xl border p-2" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)" }}>
          <p className="text-[11px]" style={{ color: "var(--text-3)" }}>Entrée</p>
          <p className="font-mono" style={{ color: "var(--text-1)" }}>{asset.entry}</p>
        </div>
        <div className="rounded-xl border border-fuchsia-400/15 bg-fuchsia-400/5 p-2">
          <p className="text-[11px]" style={{ color: "var(--text-3)" }}>Stop Loss</p>
          <p className="font-mono text-[var(--accent-fuchsia)]">{asset.stop_loss}</p>
        </div>
        <div className="rounded-xl border border-emerald-400/15 bg-emerald-400/5 p-2">
          <p className="text-[11px]" style={{ color: "var(--text-3)" }}>Take Profit</p>
          <p className="font-mono text-[var(--accent-emerald)]">{asset.take_profit}</p>
        </div>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]" style={{ color: "var(--text-3)" }}>
        <span>RSI(H1): {asset.rsi_h1}</span>
        <span>CHOP(H4): {asset.chop_h4}</span>
        <span>ATR%: {asset.atr_pct}</span>
        <span>Vol x{asset.volume_ratio}</span>
        {asset.funding_rate !== null && (
          <span>Funding: {(asset.funding_rate * 100).toFixed(3)}%</span>
        )}
      </div>

      {asset.liquidation_zones?.length > 0 && (
        <div className="mt-3 pt-2 border-t text-[11px]" style={{ borderColor: "var(--glass-border)", color: "var(--text-3)" }}>
          <p className="uppercase tracking-widest mb-1">⚠️ Zones de liquidation estimées (par levier)</p>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {asset.liquidation_zones.map((z) => (
              <span key={z.leverage}>
                {z.leverage}x — L: <span style={{ color: "var(--accent-fuchsia)" }}>{z.long_price}</span>
                {" "}/ S: <span style={{ color: "var(--accent-emerald)" }}>{z.short_price}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
