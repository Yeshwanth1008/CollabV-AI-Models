"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import {
  recommendPatentsForMe,
  type CandidatePatentDTO,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-store";
import { ListingStatusBadge } from "@/components/listing-status-badge";
import { patentNumberLabel, hasAbstract } from "@/components/listing-card";

export default function BuyerRecommendationsPage() {
  const { user } = useAuth();
  const { data, isLoading, error } = useQuery({
    queryKey: ["buyer-recommendations"],
    queryFn: () => recommendPatentsForMe({ top_k: 20 }),
    enabled: !!user,
    retry: false,
  });

  if (!user) {
    return (
      <div className="card max-w-xl">
        <h1 className="text-xl font-semibold mb-2">Sign in to see recommendations</h1>
        <p className="text-sm text-muted-foreground mb-4">
          Recommendations are personalized to your buyer profile.{" "}
          <Link href="/login" className="text-primary underline">
            Sign in
          </Link>{" "}
          first.
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Finding patents for you…
      </div>
    );
  }

  if (error) {
    // BUYER_NOT_FOUND surfaces here: the user hasn't set up a buyer profile yet.
    // Recoverable — surface a path to the create endpoint via /marketplace/buyer/profile.
    const msg = (error as Error).message || "";
    if (/buyer profile/i.test(msg)) {
      return (
        <div className="card max-w-xl space-y-3">
          <h1 className="text-xl font-semibold">Tell us what you're looking for</h1>
          <p className="text-sm text-muted-foreground">
            Recommendations work off a buyer profile (your industry, technical
            areas, use cases). Create one to start seeing matched patents.
          </p>
          <Link href="/marketplace/buyer/profile" className="btn-primary inline-flex w-fit">
            Create buyer profile
          </Link>
        </div>
      );
    }
    return (
      <div className="card border-destructive/40 text-destructive">
        {msg}
      </div>
    );
  }

  const candidates = data?.candidates ?? [];

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Recommended patents</h1>
        <p className="text-sm text-muted-foreground">
          Ranked active listings matched against your buyer profile. The score
          combines semantic retrieval, domain overlap, and recency.
        </p>
      </div>

      {data?.status === "engine_unavailable" ? (
        <div className="card border-amber-500/40 bg-amber-500/5 text-amber-200 space-y-2">
          <div className="font-medium">Recommendation engine unavailable</div>
          <p className="text-sm">
            {data?.message ||
              "The matching engine couldn't load. This is a service issue, not a data issue — there may be active patents we can't surface right now."}
          </p>
          {data?.operator_hint && (
            <p className="text-xs text-amber-200/70">
              <strong>Operator hint:</strong> {data.operator_hint}
            </p>
          )}
        </div>
      ) : data?.status === "no_active_patents" || candidates.length === 0 ? (
        <div className="card space-y-2">
          <div className="font-medium">No recommendations yet</div>
          <p className="text-sm text-muted-foreground">
            {data?.message ||
              "There are no active patents in the marketplace yet. Check back after inventors submit and admin approves listings."}
          </p>
        </div>
      ) : (
        <>
          <div className="text-xs text-muted-foreground">
            {data?.total_candidates_considered ?? 0} candidates considered ·{" "}
            {data?.total_filtered ?? 0} after rules · showing top{" "}
            {candidates.length}
          </div>
          <div className="space-y-3">
            {candidates.map((c) => (
              <RecommendationCard key={c.listing_id} c={c} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function RecommendationCard({ c }: { c: CandidatePatentDTO }) {
  // Reuse Wave-1 helpers via a synthesized listing object so the abstract
  // placeholder + patent-number prefix render the same way they do on browse.
  // The recommendations response doesn't include abstract/indian_patent_number/
  // patent_number; for the card we lean on title + tags only here and link out
  // to the detail page for the full content.
  return (
    <Link
      href={`/marketplace/patents/${c.listing_id}`}
      className="card hover:border-primary/50 transition flex items-start gap-4 block"
    >
      <ScorePill score={c.score} />
      <div className="flex-1 space-y-1">
        <div className="flex items-start justify-between gap-2">
          <div className="font-medium leading-snug">{c.title}</div>
          <ListingStatusBadge status={c.status} />
        </div>
        <div className="text-[11px] text-muted-foreground font-mono">
          {c.listing_id}
        </div>
        <div className="flex flex-wrap gap-2 mt-2 text-[11px]">
          <ScoreChip label="retrieval" v={c.retrieval_score} />
          <ScoreChip label="domain" v={c.domain_overlap_score} />
          <ScoreChip label="recency" v={c.recency_score} />
          {c.industry_match_score > 0 && (
            <ScoreChip label="industry" v={c.industry_match_score} />
          )}
        </div>
        {c.reasons && c.reasons.length > 0 && (
          <ul className="text-xs text-muted-foreground list-disc list-inside mt-2 space-y-0.5">
            {c.reasons.slice(0, 3).map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
      </div>
    </Link>
  );
}

function ScorePill({ score }: { score: number }) {
  return (
    <div className="shrink-0 w-14 h-14 rounded-lg bg-primary/10 border border-primary/30 flex flex-col items-center justify-center">
      <div className="text-lg font-bold text-primary">{Math.round(score)}</div>
      <div className="text-[10px] text-muted-foreground">score</div>
    </div>
  );
}

function ScoreChip({ label, v }: { label: string; v: number }) {
  return (
    <span className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
      {label}: <span className="text-foreground font-mono">{v.toFixed(0)}</span>
    </span>
  );
}
