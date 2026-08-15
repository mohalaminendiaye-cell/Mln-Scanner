const TABS = [
  { id: "overview", label: "🏠 Vue d'ensemble" },
  { id: "signals", label: "🎯 Signaux Techniques" },
  { id: "movers", label: "🏆 Top Movers" },
  { id: "watchlist", label: "⭐ Watchlist" },
  { id: "performance", label: "📊 Performance" },
];

export default function TabNav({ active, onChange }) {
  return (
    <div
      className="flex gap-2 overflow-x-auto scrollbar-glass mb-6 pb-1 border-b"
      style={{ borderColor: "var(--glass-border)" }}
    >
      {TABS.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={`whitespace-nowrap px-4 py-2.5 rounded-t-xl text-sm font-semibold transition border-b-2 ${
            active === tab.id ? "border-cyan-400" : "border-transparent hover:opacity-80"
          }`}
          style={{ color: active === tab.id ? "var(--text-1)" : "var(--text-3)" }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
