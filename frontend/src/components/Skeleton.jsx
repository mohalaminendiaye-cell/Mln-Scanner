function SkeletonCard() {
  return (
    <div
      className="rounded-2xl border p-4 animate-pulse"
      style={{ background: "var(--glass-bg)", borderColor: "var(--glass-border)" }}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="h-4 w-20 rounded" style={{ background: "var(--glass-bg-strong)" }} />
        <div className="h-5 w-14 rounded-full" style={{ background: "var(--glass-bg-strong)" }} />
      </div>
      <div className="h-3 w-32 rounded mb-3" style={{ background: "var(--glass-bg-strong)" }} />
      <div className="h-10 w-full rounded mb-3" style={{ background: "var(--glass-bg-strong)" }} />
      <div className="grid grid-cols-3 gap-2">
        <div className="h-10 rounded" style={{ background: "var(--glass-bg-strong)" }} />
        <div className="h-10 rounded" style={{ background: "var(--glass-bg-strong)" }} />
        <div className="h-10 rounded" style={{ background: "var(--glass-bg-strong)" }} />
      </div>
    </div>
  );
}

export default function Skeleton({ count = 6 }) {
  return (
    <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  );
}
