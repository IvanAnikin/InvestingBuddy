import GlassCard from "@/components/ui/GlassCard";
import PortfolioManager from "./PortfolioManager";

export const dynamic = "force-dynamic";

export default function PortfolioPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-white">My portfolio</h1>
        <p className="mt-1 max-w-2xl text-sm text-slate-300">
          A private list you type in by hand: the issuer, the date you entered it and, if you
          like, the price and weight. Only you can see it. It is never imported from a broker,
          never turned into an order, and never written into the public research.
        </p>
      </div>
      <GlassCard className="p-6">
        <PortfolioManager />
      </GlassCard>
    </div>
  );
}
