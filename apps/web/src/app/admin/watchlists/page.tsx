import GlassCard from "@/components/ui/GlassCard";
import WatchlistsManager from "./WatchlistsManager";

export const dynamic = "force-dynamic";

export default function WatchlistsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-white">Shortlist and watchlist</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-300">
          The two team lists. A daily monitoring pass looks at every company on them and
          records what observably changed since its last research: a new filing, a dated item
          coming up, a gap the last report could not close. A signal says what changed, never
          what it means. Nothing here starts research by itself: a material signal shows a
          link to re-run it, for you to decide.
        </p>
      </div>
      <GlassCard className="p-6">
        <WatchlistsManager />
      </GlassCard>
    </div>
  );
}
