"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { Loader2, Send } from "lucide-react";
import { claimProfessor, getMyListings, type PatentListing } from "@/lib/api";
import { useAuth } from "@/lib/auth-store";
import { ListingStatusBadge } from "@/components/listing-status-badge";

const SECTION_ORDER: Array<PatentListing["status"]> = [
  "draft",
  "pending_approval",
  "active",
  "paused",
  "sold",
  "withdrawn",
];

const SECTION_TITLES: Record<string, string> = {
  draft: "Drafts",
  pending_approval: "Pending approval",
  active: "Active",
  paused: "Paused",
  sold: "Sold",
  withdrawn: "Withdrawn",
};

export default function InventorDashboardPage() {
  const { user } = useAuth();
  const { data, isLoading, error } = useQuery({
    queryKey: ["my-listings"],
    queryFn: getMyListings,
    enabled: !!user,
  });

  if (!user) {
    return (
      <div className="card max-w-xl">
        <h1 className="text-xl font-semibold mb-2">Sign in required</h1>
        <p className="text-sm text-muted-foreground mb-4">
          The inventor dashboard shows only listings linked to your own faculty
          profile.{" "}
          <Link href="/login" className="text-primary underline">
            Sign in
          </Link>{" "}
          to continue.
        </p>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading your listings…
      </div>
    );
  }

  if (error) {
    return (
      <div className="card border-destructive/40 text-destructive">
        {(error as Error).message}
      </div>
    );
  }

  if (!data?.linked_professor_id) {
    return <ClaimGate data={data} />;
  }

  const grouped: Record<string, PatentListing[]> = {};
  for (const l of data.listings) {
    (grouped[l.status] ||= []).push(l);
  }

  const isStub = data.owner_profile_type === "patent_stub";

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">My listings</h1>
        <p className="text-sm text-muted-foreground">
          {data.count ?? data.listings.length} listings linked to your faculty
          profile · activation flow: draft → pending approval → active (admin/TTO
          approval required).
        </p>
        {isStub && (
          <div className="card border-amber-500/40 bg-amber-500/5 text-xs text-amber-200 leading-relaxed">
            Your account is linked to a stub profile (no underlying faculty
            record). Listings under a stub profile can only be activated by
            admin/TTO — the Submit-for-approval action is hidden below.
          </div>
        )}
      </div>

      {SECTION_ORDER.map((s) => {
        const items = grouped[s] || [];
        if (!items.length) return null;
        return (
          <section key={s} className="space-y-3">
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold">{SECTION_TITLES[s]}</h2>
              <span className="text-xs text-muted-foreground">{items.length}</span>
            </div>
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
              {items.map((l) => (
                <Link
                  key={l.listing_id}
                  href={`/marketplace/inventor/listings/${l.listing_id}`}
                  className="card hover:border-primary/50 transition space-y-2"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="font-medium leading-snug line-clamp-2">
                      {l.title}
                    </div>
                    <ListingStatusBadge status={l.status} />
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {l.indian_patent_number
                      ? `Patent ${l.indian_patent_number}`
                      : l.patent_number || "No patent number on file"}
                  </div>
                  <div className="text-[11px] text-muted-foreground/80 flex flex-wrap gap-1">
                    {(l.domain_tags || []).slice(0, 3).map((t) => (
                      <span
                        key={t}
                        className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </Link>
              ))}
            </div>
          </section>
        );
      })}

      {data.listings.length === 0 && (
        <div className="card text-sm text-muted-foreground">
          No listings on file for your profile yet.
        </div>
      )}
    </div>
  );
}


function ClaimGate({ data }: { data: any }) {
  const qc = useQueryClient();
  const [professorId, setProfessorId] = useState("");
  const state = (data?.claim_state || "none") as
    | "none" | "pending" | "approved" | "rejected";
  const claim = data?.claim || null;

  const mutation = useMutation({
    mutationFn: () => claimProfessor(professorId.trim()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["my-listings"] }),
  });

  // Pending: awaiting admin review. Inventor sees nothing else — no listing
  // peek, no early access. Server enforces this too; the gate here is just
  // honest UX.
  if (state === "pending") {
    return (
      <div className="card max-w-2xl space-y-3">
        <h1 className="text-xl font-semibold">Faculty link pending admin verification</h1>
        <p className="text-sm text-muted-foreground">
          Your claim on{" "}
          <span className="font-mono text-foreground">
            {claim?.requested_professor_id}
          </span>{" "}
          is awaiting admin/TTO review. Once approved, your listings will appear
          here automatically.
        </p>
        <div className="text-xs text-muted-foreground/80">
          Submitted{" "}
          {claim?.created_at
            ? new Date(claim.created_at * 1000).toLocaleString()
            : "recently"}.
        </div>
      </div>
    );
  }

  // Rejected: explain + allow another claim. Don't autofill the rejected
  // professor_id — let the inventor enter the right one this time.
  if (state === "rejected") {
    return (
      <div className="space-y-4 max-w-2xl">
        <div className="card border-destructive/40 bg-destructive/5 text-destructive space-y-2">
          <div className="font-medium">Your last claim was rejected</div>
          <p className="text-sm">
            You requested{" "}
            <span className="font-mono">{claim?.requested_professor_id}</span>;
            an admin reviewed and rejected it
            {claim?.review_note ? `: ${claim.review_note}` : "."}
          </p>
          <p className="text-xs">
            If this was in error, contact the TTO. Otherwise submit a new claim
            for the correct profile below.
          </p>
        </div>
        <ClaimForm
          professorId={professorId}
          setProfessorId={setProfessorId}
          mutation={mutation}
        />
      </div>
    );
  }

  // No claim yet — primary onboarding state.
  return (
    <div className="space-y-4 max-w-2xl">
      <div className="card space-y-2">
        <h1 className="text-xl font-semibold">Claim your faculty profile</h1>
        <p className="text-sm text-muted-foreground">
          Your account isn't linked to a faculty profile yet, so there are no
          listings to show. Submit your faculty <code>professor_id</code> below;
          an admin/TTO will verify before approving the link.
        </p>
        <p className="text-xs text-amber-300/90">
          Verification is currently a manual admin review. A self-service path
          (email-domain match, verification email, or Institute SSO) will
          replace it later.
        </p>
      </div>
      <ClaimForm
        professorId={professorId}
        setProfessorId={setProfessorId}
        mutation={mutation}
      />
    </div>
  );
}


function ClaimForm({
  professorId,
  setProfessorId,
  mutation,
}: {
  professorId: string;
  setProfessorId: (s: string) => void;
  mutation: UseMutationResult<unknown, unknown, void, unknown>;
}) {
  return (
    <div className="card space-y-3">
      <div className="text-sm font-medium">Submit a claim</div>
      <input
        className="input"
        placeholder="e.g. IITM-0143"
        value={professorId}
        onChange={(e) => setProfessorId(e.target.value)}
        autoComplete="off"
      />
      <button
        className="btn-primary"
        disabled={!professorId.trim() || mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {mutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin mr-2" />
        ) : (
          <Send className="h-4 w-4 mr-2" />
        )}
        Submit claim for review
      </button>
      {mutation.isError && (
        <div className="text-xs text-destructive">
          {(mutation.error as Error)?.message || "Could not submit claim."}
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Submitting creates a pending request. Re-submitting the same{" "}
        <code>professor_id</code> is idempotent.
      </p>
    </div>
  );
}
