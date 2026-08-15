import { useEffect, useState } from "react";
import { getWatchlist, addToWatchlist, removeFromWatchlist } from "../api.js";

const glass = { background: "var(--glass-bg)", borderColor: "var(--glass-border)", backdropFilter: "blur(20px)" };

export default function Watchlist() {
  const [items, setItems] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const data = await getWatchlist();
      setItems(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!input.trim()) return;
    const symbol = input.trim().toUpperCase().endsWith("USDT")
      ? input.trim().toUpperCase()
      : `${input.trim().toUpperCase()}USDT`;
    try {
      await addToWatchlist(symbol);
      setInput("");
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const handleRemove = async (symbol) => {
    try {
      await removeFromWatchlist(symbol);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <section>
      <h2 className="text-xl font-bold mb-4 bg-gradient-to-r from-amber-400 via-cyan-400 to-violet-400 bg-clip-text text-transparent">
        ⭐ Ma Watchlist
      </h2>

      <form onSubmit={handleAdd} className="flex gap-2 mb-4">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ex: BTC ou BTCUSDT"
          className="flex-1 rounded-xl border px-4 py-2.5 text-sm outline-none"
          style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)", color: "var(--text-1)" }}
        />
        <button
          type="submit"
          className="rounded-xl border border-cyan-400/30 bg-gradient-to-r from-violet-500/30 to-cyan-500/30 hover:from-violet-500/40 hover:to-cyan-500/40 text-white px-5 py-2.5 text-sm font-semibold transition"
        >
          Ajouter
        </button>
      </form>

      {error && (
        <div className="rounded-xl border border-red-400/30 bg-red-400/10 text-red-300 p-3 mb-4 text-sm">{error}</div>
      )}

      {loading ? (
        <p style={{ color: "var(--text-3)" }}>Chargement...</p>
      ) : items.length ? (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
          {items.map((item) => (
            <div
              key={item.symbol}
              className="rounded-xl border p-4 flex items-center justify-between"
              style={glass}
            >
              <div>
                <p className="font-bold" style={{ color: "var(--text-1)" }}>{item.symbol}</p>
                <p className="text-sm font-mono" style={{ color: "var(--text-2)" }}>
                  {item.price !== null && item.price !== undefined ? item.price : "—"}
                </p>
              </div>
              <button
                onClick={() => handleRemove(item.symbol)}
                className="text-xs px-3 py-1.5 rounded-full border transition hover:opacity-80"
                style={{ borderColor: "var(--glass-border)", color: "var(--accent-fuchsia)" }}
              >
                Retirer
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="italic" style={{ color: "var(--text-3)" }}>
          Aucune paire suivie. Ajoute un symbole ci-dessus (ex: SOL, INJ, ARB...).
        </p>
      )}
    </section>
  );
}
