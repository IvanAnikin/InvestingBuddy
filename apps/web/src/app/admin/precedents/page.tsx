import GlassCard from "@/components/ui/GlassCard";
import PrecedentQueue from "./PrecedentQueue";

export const dynamic = "force-dynamic";

export default function PrecedentsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-white">Precedent deals</h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-300">
          Acquisitions proposed from documents the platform already holds. A deal is used in a
          report only after you approve it with its electricity layer and the target&apos;s metric;
          a rejected deal is kept with your reason.
        </p>
      </div>
      <GlassCard className="p-6">
        <PrecedentQueue />
      </GlassCard>
    </div>
  );
}
