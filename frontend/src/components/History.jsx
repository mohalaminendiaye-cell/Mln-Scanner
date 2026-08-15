export default function History({ items, onSelect, selectedId }) {
  return (
    <div>
      <h3 className="text-sm font-semibold mb-3 uppercase tracking-widest" style={{ color: "var(--text-3)" }}>
        Historique des scans
      </h3>
      <div className="space-y-2 max-h-[75vh] overflow-y-auto scrollbar-glass pr-1">
        {items.map((item) => {
          const active = selectedId === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onSelect(item.id)}
              className={`w-full text-left px-4 py-3 rounded-xl text-sm transition duration-300 border ${
                active
                  ? "bg-gradient-to-r from-violet-500/20 to-cyan-500/20 border-[var(--glass-border-strong)] shadow-[0_0_20px_rgba(124,58,237,0.25)]"
                  : "border-[var(--glass-border)] hover:border-[var(--glass-border-strong)] hover:shadow-[0_0_18px_rgba(6,182,212,0.2)]"
              }`}
              style={{
                background: active ? undefined : "var(--glass-bg)",
                color: active ? "var(--text-1)" : "var(--text-2)",
              }}
            >
              <div>{new Date(item.timestamp).toLocaleString("fr-FR", { timeZone: "Africa/Dakar" })}</div>
              <div className="text-xs mt-0.5" style={{ color: "var(--text-3)" }}>{item.symbols_analyzed} paires</div>
            </button>
          );
        })}
        {items.length === 0 && <p className="text-sm" style={{ color: "var(--text-3)" }}>Aucun scan enregistré.</p>}
      </div>
    </div>
  );
}
