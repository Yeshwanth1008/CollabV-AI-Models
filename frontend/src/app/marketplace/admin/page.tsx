"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, ShieldCheck, XCircle } from "lucide-react";
import {
  getAdminClaimRequests,
  getAdminPendingListings,
  reviewClaimRequest,
  transitionListing,
  type ClaimRow,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-store";
import { ListingStatusBadge, lifecycleErrorMessage } from "@/components/listing-status-badge";

export default function AdminPendingPage() {
  const { user } = useAuth();
  const qc = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-pending"],
    queryFn: getAdminPendingListings,
    enabled: !!user && user.role === "admin",
  });

  const approveMut = useMutation({
    mutationFn: (listingId: string) => transitionListing(listingId, "active"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-pending"] });
    },
  });

  const rejectMut = useMutation({
    mutationFn: (listingId: string) => transitionListing(listingId, "draft"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-pending"] });
    },
  });

  if (!user) {
    return (
      <div className="card">
        Please <Link href="/login" className="text-primary underline">sign in</Link>{" "}
        as an admin to review pending listings.
      </div>
    );
  }
  if (user.role !== "admin") {
    return (
      <div className="card border-destructive/40 text-destructive">
        Admin role required. You are signed in as{" "}
        <span className="font-mono">{user.role}</span>.
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading queue…
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

  const pending = data?.listings ?? [];

  return (
    <div className="space-y-8">
      <ClaimRequestsSection />

      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Pending listing approvals</h1>
        <p className="text-sm text-muted-foreground">
          {pending.length} listing{pending.length === 1 ? "" : "s"} awaiting
          admin/TTO review. Approving moves a listing to <strong>active</strong>{" "}
          and makes it visible to buyers. Sending back to draft returns it to
          the inventor for edits.
        </p>
      </div>

      {pending.length === 0 ? (
        <div className="card text-sm text-muted-foreground">
          Nothing pending. The queue is clear.
        </div>
      ) : (
        <div className="space-y-3">
          {pending.map((l) => (
            <div key={l.listing_id} className="card space-y-3">
              <div className="flex items-start justify-between gap-3">
                <div className="space-y-1">
                  <div className="font-medium leading-snug">{l.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {l.owner_name} · {l.owner_profile_type === "patent_stub"
                      ? "stub profile"
                      : "faculty"}{" "}
                    · {l.indian_patent_number || l.patent_number || "no patent #"}
                  </div>
                </div>
                <ListingStatusBadge status={l.status} />
              </div>

              {l.abstract && (
                <div className="text-xs text-muted-foreground line-clamp-3 whitespace-pre-wrap">
                  {l.abstract}
                </div>
              )}

              <div className="flex flex-wrap gap-1">
                {(l.domain_tags || []).slice(0, 6).map((t) => (
                  <span
                    key={t}
                    className="text-[11px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground"
                  >
                    {t}
                  </span>
                ))}
              </div>

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <button
                  className="btn-primary"
                  disabled={approveMut.isPending || rejectMut.isPending}
                  onClick={() => approveMut.mutate(l.listing_id)}
                >
                  {approveMut.isPending && approveMut.variables === l.listing_id ? (
                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  ) : (
                    <CheckCircle2 className="h-4 w-4 mr-2" />
                  )}
                  Approve → active
                </button>
                <button
                  className="btn-secondary"
                  disabled={approveMut.isPending || rejectMut.isPending}
                  onClick={() => rejectMut.mutate(l.listing_id)}
                >
                  <XCircle className="h-4 w-4 mr-2" />
                  Send back to draft
                </button>
                <Link
                  href={`/marketplace/inventor/listings/${l.listing_id}`}
                  className="text-xs text-muted-foreground hover:text-foreground underline ml-2"
                >
                  View details
                </Link>
              </div>

              {(approveMut.error || rejectMut.error) &&
                (approveMut.variables === l.listing_id ||
                  rejectMut.variables === l.listing_id) && (
                  <div className="text-xs text-destructive">
                    {lifecycleErrorMessage(approveMut.error || rejectMut.error)}
                  </div>
                )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


function ClaimRequestsSection() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-claims"],
    queryFn: getAdminClaimRequests,
  });

  const approveMut = useMutation({
    mutationFn: (claim_id: string) => reviewClaimRequest(claim_id, true),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-claims"] }),
  });
  const rejectMut = useMutation({
    mutationFn: (claim_id: string) => reviewClaimRequest(claim_id, false),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-claims"] }),
  });

  const claims = data?.claims ?? [];

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5 text-amber-300" />
          <h2 className="text-xl font-semibold">Faculty-profile claim requests</h2>
        </div>
        <p className="text-xs text-muted-foreground">
          Inventors submit a claim with their <code>professor_id</code>;
          approving here links their account so they can see and activate that
          faculty's listings. <strong>This is the impersonation gate</strong> —
          don't approve a claim unless you've confirmed the requester is the
          real faculty.
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading claim queue…
        </div>
      ) : error ? (
        <div className="card border-destructive/40 text-destructive">
          {(error as Error).message}
        </div>
      ) : claims.length === 0 ? (
        <div className="card text-sm text-muted-foreground">
          No pending claim requests.
        </div>
      ) : (
        <div className="space-y-2">
          {claims.map((c: ClaimRow) => {
            const isThisClaimPending =
              (approveMut.isPending && approveMut.variables === c.claim_id) ||
              (rejectMut.isPending && rejectMut.variables === c.claim_id);
            const showError =
              (approveMut.error && approveMut.variables === c.claim_id) ||
              (rejectMut.error && rejectMut.variables === c.claim_id);
            return (
              <div key={c.claim_id} className="card space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1 text-sm">
                    <div>
                      <span className="font-medium">{c.requester_name}</span>{" "}
                      <span className="text-muted-foreground">
                        &lt;{c.requester_email}&gt;
                      </span>{" "}
                      requests link to
                    </div>
                    <div className="font-mono text-xs">
                      {c.requested_professor_name} ({c.requested_professor_id})
                      {c.requested_professor_dept && (
                        <span className="text-muted-foreground">
                          {" "}
                          · {c.requested_professor_dept}
                        </span>
                      )}
                      {c.requested_profile_type === "patent_stub" && (
                        <span className="ml-2 text-amber-300">
                          [stub profile — verify carefully]
                        </span>
                      )}
                    </div>
                    {c.note && (
                      <div className="text-xs text-muted-foreground italic">
                        "{c.note}"
                      </div>
                    )}
                  </div>
                  <div className="text-[11px] text-muted-foreground/80 font-mono">
                    {c.claim_id}
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    className="btn-primary"
                    disabled={isThisClaimPending}
                    onClick={() => approveMut.mutate(c.claim_id)}
                  >
                    {isThisClaimPending && approveMut.variables === c.claim_id ? (
                      <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    ) : (
                      <CheckCircle2 className="h-4 w-4 mr-2" />
                    )}
                    Approve — link this account
                  </button>
                  <button
                    className="btn-secondary"
                    disabled={isThisClaimPending}
                    onClick={() => rejectMut.mutate(c.claim_id)}
                  >
                    <XCircle className="h-4 w-4 mr-2" />
                    Reject
                  </button>
                </div>
                {showError && (
                  <div className="text-xs text-destructive">
                    {(approveMut.error as Error)?.message ||
                      (rejectMut.error as Error)?.message ||
                      "Action failed"}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
