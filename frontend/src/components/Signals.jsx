import AssetCard from "./AssetCard.jsx";
import Skeleton from "./Skeleton.jsx";
import { Category7 } from "./MultiExchangeMovers.jsx";
import Category10 from "./Category10.jsx";

export function CategoryBlock({ title, gradient, items, loading, emptyText, getBadges }) {
  return (
    <section className="mb-10">
      <h2 className={`text-xl font-bold mb-4 bg-gradient-to-r ${gradient} bg-clip-text text-transparent`}>
        {title}
      </h2>
      {loading ? (
        <Skeleton count={3} />
      ) : items?.length ? (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
          {items.map((a, i) => (
            <AssetCard key={a.symbol} asset={a} rank={i + 1} badges={getBadges ? getBadges(a) : undefined} />
          ))}
        </div>
      ) : (
        <p className="italic" style={{ color: "var(--text-3)" }}>{emptyText}</p>
      )}
    </section>
  );
}

// ---- Catégorie 6 : Stratégies ----
function StrategiesBlock({ category6, loading }) {
  if (loading) {
    return (
      <section className="mb-10">
        <h2 className="text-xl font-bold mb-4 bg-gradient-to-r from-amber-400 via-fuchsia-400 to-cyan-400 bg-clip-text text-transparent">
          🧩 Catégorie 6 — Stratégies
        </h2>
        <Skeleton count={3} />
      </section>
    );
  }
  return (
    <section className="mb-10">
      <h2 className="text-xl font-bold mb-4 bg-gradient-to-r from-amber-400 via-fuchsia-400 to-cyan-400 bg-clip-text text-transparent">
        🧩 Catégorie 6 — Stratégies
      </h2>

      <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-2)" }}>
        Stratégie 1 <span className="font-normal" style={{ color: "var(--text-3)" }}>— Ichimoku + Volume Profile + Order Book (score ≥ 65/100)</span>
      </h3>
      {category6?.strategie1?.length ? (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4 mb-8">
          {category6.strategie1.map((a, i) => (
            <AssetCard key={a.symbol} asset={a} rank={i + 1} />
          ))}
        </div>
      ) : (
        <p className="italic mb-8" style={{ color: "var(--text-3)" }}>
          Marché actuellement en phase de consolidation/faible volatilité, peu de structures valides.
        </p>
      )}

      <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-2)" }}>
        Stratégie 2 <span className="font-normal" style={{ color: "var(--text-3)" }}>— Scalping ICT/VWAP (score ≥ 65/100)</span>
      </h3>
      {category6?.strategie2?.length ? (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
          {category6.strategie2.map((a, i) => (
            <AssetCard key={a.symbol} asset={a} rank={i + 1} />
          ))}
        </div>
      ) : (
        <p className="italic" style={{ color: "var(--text-3)" }}>
          Marché actuellement en phase de consolidation/faible volatilité, peu de structures valides.
        </p>
      )}
    </section>
  );
}

// ---- Bonus Trading ----
const BEHAVIOR_COLOR = {
  Compression: "var(--accent-cyan)",
  Accumulation: "var(--accent-emerald)",
  "Prise de profit": "var(--accent-fuchsia)",
  Indéterminé: "var(--text-3)",
};
function SocialSpikeCard({ s }) {
  const color = BEHAVIOR_COLOR[s.behavior] || "var(--text-3)";
  return (
    <div className="rounded-xl border p-4" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)" }}>
      <div className="flex items-center justify-between mb-2 gap-2">
        <h4 className="font-bold truncate" style={{ color: "var(--text-1)" }}>{s.symbol}</h4>
        <span
          className="text-xs px-2 py-0.5 rounded-full border whitespace-nowrap flex-shrink-0"
          style={{ color, borderColor: `${color}66`, background: `${color}1a` }}
        >
          {s.behavior}
        </span>
      </div>
      <p className="text-sm mb-1" style={{ color: "var(--text-2)" }}>
        <span style={{ color: "var(--text-3)" }}>Cause : </span>{s.cause}
      </p>
      {s.volume_change_24h_pct !== null && s.volume_change_24h_pct !== undefined && (
        <p className="text-xs mb-2" style={{ color: "var(--text-3)" }}>
          Volume 24h : <span style={{ color: "var(--accent-emerald)" }}>+{s.volume_change_24h_pct}%</span>
        </p>
      )}
      <p className="text-sm italic" style={{ color: "var(--text-2)" }}>{s.summary}</p>
    </div>
  );
}
function DerivativesCard({ d }) {
  const color = d.zone_side === "Squeeze longs" ? "var(--accent-fuchsia)" : "var(--accent-emerald)";
  return (
    <div className="rounded-xl border p-4" style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)" }}>
      <div className="flex items-center justify-between mb-2">
        <h4 className="font-bold" style={{ color: "var(--text-1)" }}>{d.symbol}</h4>
        <span
          className="text-xs px-2 py-0.5 rounded-full border"
          style={{ color, borderColor: `${color}66`, background: `${color}1a` }}
        >
          {d.zone_side}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-xs mb-2" style={{ color: "var(--text-3)" }}>
        <span>
          OI 24h:{" "}
          <span style={{ color: d.oi_change_24h_pct >= 0 ? "var(--accent-emerald)" : "var(--accent-fuchsia)" }}>
            {d.oi_change_24h_pct > 0 ? "+" : ""}{d.oi_change_24h_pct}%
          </span>
        </span>
        <span>Funding: {(d.funding_rate * 100).toFixed(3)}%</span>
        <span>Zone: {d.zone_distance_pct}%</span>
      </div>
      <p className="text-sm" style={{ color: "var(--text-2)" }}>{d.reasoning}</p>
    </div>
  );
}
function BonusTradingBlock({ bonus, loading }) {
  if (loading) {
    return (
      <section>
        <h2 className="text-xl font-bold mb-4 bg-gradient-to-r from-fuchsia-400 via-amber-400 to-cyan-400 bg-clip-text text-transparent">
          🔥 Bonus Trading
        </h2>
        <Skeleton count={3} />
      </section>
    );
  }
  return (
    <section>
      <h2 className="text-xl font-bold mb-4 bg-gradient-to-r from-fuchsia-400 via-amber-400 to-cyan-400 bg-clip-text text-transparent">
        🔥 Bonus Trading
      </h2>

      <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--text-2)" }}>
        1. Pics d'activité sociale / recherche (6h)
      </h3>
      <p className="text-xs mb-3 rounded-xl border border-amber-400/30 bg-amber-400/10 text-amber-300 px-4 py-3">
        ⚠️ Recherche X/web via Grok — non déterministe, à vérifier. Vide si GROK_API_KEY absente.
      </p>
      {bonus?.social_spikes?.length ? (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4 mb-8">
          {bonus.social_spikes.map((s) => (
            <SocialSpikeCard key={s.symbol} s={s} />
          ))}
        </div>
      ) : (
        <p className="italic mb-8" style={{ color: "var(--text-3)" }}>
          Aucun pic social recensé (ou GROK_API_KEY non configurée).
        </p>
      )}

      <h3 className="text-sm font-semibold mb-2" style={{ color: "var(--text-2)" }}>
        2. Dérivés — Top 3 OI en forte hausse + funding extrême
      </h3>
      <p className="text-xs mb-3" style={{ color: "var(--text-3)" }}>
        ✅ Données Binance réelles (Open Interest historique + funding rate).
      </p>
      {bonus?.derivatives_top3?.length ? (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
          {bonus.derivatives_top3.map((d) => (
            <DerivativesCard key={d.symbol} d={d} />
          ))}
        </div>
      ) : (
        <p className="italic" style={{ color: "var(--text-3)" }}>Aucun candidat détecté pour ce scan.</p>
      )}
    </section>
  );
}

export default function Signals({ scan, loading }) {
  // Index croisés pour les badges de confluence sur la Catégorie 1 (calculés une
  // seule fois par rendu, pas par carte).
  const cat4Symbols = new Set((scan?.category4 || []).map((a) => a.symbol));
  const oiSymbols = new Set((scan?.bonus_trading?.derivatives_top3 || []).map((d) => d.symbol));
  const cat7List = Object.values(scan?.category7 || {}).flat();
  const cat7Symbols = new Set(cat7List.map((s) => s.symbol));
  const cat7DirectionBySymbol = new Map();
  cat7List.forEach((s) => {
    if (!cat7DirectionBySymbol.has(s.symbol)) cat7DirectionBySymbol.set(s.symbol, s.direction);
  });
  const cat10Symbols = new Set((scan?.category10 || []).map((s) => s.symbol));

  function getCategory1Badges(asset) {
    const badges = [];
    if (cat4Symbols.has(asset.symbol)) {
      badges.push({ shape: "square", color: "orange", title: "Aussi présente en Catégorie 4 (corrélation BTC)" });
    }
    if (oiSymbols.has(asset.symbol)) {
      badges.push({ shape: "square", color: "yellow", title: "Aussi présente dans la section OI (Bonus Trading — dérivés)" });
    }
    if (cat7Symbols.has(asset.symbol)) {
      badges.push({ shape: "circle", color: "orange", title: "Aussi présente en Catégorie 7 (mouvements imminents)" });
    }
    if (cat10Symbols.has(asset.symbol)) {
      badges.push({ shape: "circle", color: "yellow", title: "Aussi présente en Catégorie 10 (Global Breakout Score)" });
    }
    const cat7Direction = cat7DirectionBySymbol.get(asset.symbol);
    if (cat7Direction && cat7Direction !== asset.direction && (cat7Direction === "Long" || cat7Direction === "Short") && (asset.direction === "Long" || asset.direction === "Short")) {
      badges.push({
        shape: "triangle", color: "white",
        title: `⚠️ Directions opposées : ${asset.direction} en Cat.1 vs ${cat7Direction} en Cat.7`,
      });
    }
    return badges;
  }

  return (
    <div>
      <p className="text-[11px] mb-2 flex flex-wrap items-center gap-x-4 gap-y-1" style={{ color: "var(--text-3)" }}>
        <span className="flex items-center gap-1"><span style={{ display: "inline-block", width: 9, height: 9, background: "#fb923c", borderRadius: 2 }} /> aussi en Cat.4</span>
        <span className="flex items-center gap-1"><span style={{ display: "inline-block", width: 9, height: 9, background: "#fde047", borderRadius: 2 }} /> aussi en section OI</span>
        <span className="flex items-center gap-1"><span style={{ display: "inline-block", width: 9, height: 9, background: "#fb923c", borderRadius: "50%" }} /> aussi en Cat.7</span>
        <span className="flex items-center gap-1"><span style={{ display: "inline-block", width: 9, height: 9, background: "#fde047", borderRadius: "50%" }} /> aussi en Cat.10</span>
        <span className="flex items-center gap-1"><span style={{ display: "inline-block", width: 0, height: 0, borderLeft: "5px solid transparent", borderRight: "5px solid transparent", borderBottom: "8px solid #fff" }} /> direction opposée vs Cat.7</span>
      </p>
      <CategoryBlock
        title="🎯 Catégorie 1 — Probabilité de mouvement significatif (±5%/24h)"
        gradient="from-cyan-400 via-violet-400 to-fuchsia-400"
        items={scan?.category1}
        loading={loading}
        emptyText="Aucun setup qualifié pour ce scan."
        getBadges={getCategory1Badges}
      />
      <CategoryBlock
        title="🔗 Catégorie 4 — Divergence de corrélation BTC"
        gradient="from-violet-400 via-cyan-400 to-emerald-400"
        items={scan?.category4}
        loading={loading}
        emptyText="Aucune divergence significative détectée."
      />

      <StrategiesBlock category6={scan?.category6} loading={loading} />

      <CategoryBlock
        title="🤖 Catégorie 11 — Scalping IA (Grok) · score ≥ 65/100"
        gradient="from-fuchsia-400 via-cyan-400 to-amber-400"
        items={scan?.category11}
        loading={loading}
        emptyText="Marché actuellement en phase de consolidation/faible volatilité, peu de structures valides."
      />

      {loading ? (
        <Skeleton count={3} />
      ) : (
        <>
          <Category7 data={scan?.category7} />
          <Category10 signals={scan?.category10} />
        </>
      )}

      <BonusTradingBlock bonus={scan?.bonus_trading} loading={loading} />

      {scan?.errors?.length > 0 && (
        <details className="text-xs mt-10" style={{ color: "var(--text-3)" }}>
          <summary className="cursor-pointer">
            {scan.errors.length} erreur(s) ignorée(s) pendant le scan
          </summary>
          <ul className="mt-2 space-y-1">
            {scan.errors.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
