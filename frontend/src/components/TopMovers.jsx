import MultiExchangeMovers from "./MultiExchangeMovers.jsx";
import { CategoryBlock } from "./Signals.jsx";

export default function TopMovers({ category2, category9 }) {
  return (
    <section>
      <CategoryBlock
        title="🌀 Catégorie 2 — Choppiness Index élevé (H4 > 60)"
        gradient="from-amber-400 via-fuchsia-400 to-violet-400"
        items={category2}
        loading={false}
        emptyText="Aucun setup qualifié pour ce scan."
      />

      <div className="mt-8">
        <MultiExchangeMovers category9={category9} />
      </div>
    </section>
  );
}
