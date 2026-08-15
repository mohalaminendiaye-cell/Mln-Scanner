const glass = { background: "var(--glass-bg)", borderColor: "var(--glass-border)", backdropFilter: "blur(20px)" };

export default function SummaryBar({ scan }) {
  if (!scan) return null;

  const bestCat1 = scan.category1?.[0];
  const bestCat2 = scan.category2?.[0];

  const rows = [
    bestCat1 && {
      icon: "🎯",
      text: `Meilleur signal Cat.1 : ${bestCat1.symbol} — ${bestCat1.direction} (score ${bestCat1.score}/100, R:R 1:${bestCat1.risk_reward})`,
    },
    bestCat2 && {
      icon: "🌀",
      text: `Meilleure zone de range Cat.2 : ${bestCat2.symbol} — CHOP(H4) ${bestCat2.chop_h4}`,
    },
  ].filter(Boolean);

  if (rows.length === 0) return null;

  return (
    <div className="rounded-2xl border p-5 mb-6 space-y-2" style={glass}>
      <h3 className="text-xs uppercase tracking-widest mb-2" style={{ color: "var(--text-3)" }}>
        Résumé du scan
      </h3>
      {rows.map((r, i) => (
        <p key={i} className="text-sm flex items-start gap-2" style={{ color: "var(--text-1)" }}>
          <span>{r.icon}</span>
          <span>{r.text}</span>
        </p>
      ))}
    </div>
  );
}
