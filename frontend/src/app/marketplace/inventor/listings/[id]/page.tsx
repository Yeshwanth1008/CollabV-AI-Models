"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Save, Send } from "lucide-react";
import {
  getListing,
  getMyListings,
  patchListing,
  transitionListing,
  type PatentListing,
  type PatentListingPatch,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-store";
import { ListingStatusBadge, lifecycleErrorMessage } from "@/components/listing-status-badge";

// Whether the inventor (non-admin) can edit fields on this listing.
// Server-side this is enforced in PATCH /marketplace/listings/{id}; we mirror
// it here so the UI doesn't render disabled-looking inputs that error on save.
function isEditableForInventor(status: string) {
  return status === "draft" || status === "paused";
}

export default function InventorListingDetailPage() {
  const params = useParams<{ id: string }>();
  const listingId = params.id;
  const router = useRouter();
  const { user } = useAuth();
  const qc = useQueryClient();

  // Pull the listing + the owner's profile_type. We get profile_type from the
  // inventor-listings endpoint (which enriches it server-side); falling back
  // to 'faculty' if absent is safe — the server still gates activation.
  const listingQuery = useQuery({
    queryKey: ["listing", listingId],
    queryFn: () => getListing(listingId),
    enabled: !!user && !!listingId,
  });
  const ownerQuery = useQuery({
    queryKey: ["my-listings"],
    queryFn: getMyListings,
    enabled: !!user,
  });

  const listing: PatentListing | undefined = listingQuery.data;
  const ownerProfileType =
    listing?.owner_profile_type ||
    ownerQuery.data?.owner_profile_type ||
    ownerQuery.data?.listings.find((l) => l.listing_id === listingId)?.owner_profile_type ||
    "faculty";
  const isStubOwned = ownerProfileType === "patent_stub";

  // Editable local form state, populated when listing loads.
  const [form, setForm] = useState<PatentListingPatch | null>(null);
  useEffect(() => {
    if (!listing) return;
    setForm({
      title: listing.title || "",
      abstract: listing.abstract || "",
      asking_price_inr: listing.asking_price_inr ?? null,
      inventor_names: listing.inventor_names || [],
      domain_tags: listing.domain_tags || [],
      industry_tags: listing.industry_tags || [],
    });
  }, [listing?.listing_id]);

  const editable = listing ? isEditableForInventor(listing.status) : false;

  const patchMut = useMutation({
    mutationFn: (patch: PatentListingPatch) => patchListing(listingId, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["listing", listingId] });
      qc.invalidateQueries({ queryKey: ["my-listings"] });
    },
  });

  const transitionMut = useMutation({
    mutationFn: (target: "pending_approval" | "draft" | "withdrawn") =>
      transitionListing(listingId, target),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["listing", listingId] });
      qc.invalidateQueries({ queryKey: ["my-listings"] });
    },
  });

  if (!user) {
    return (
      <div className="card">
        Please <Link href="/login" className="text-primary underline">sign in</Link>.
      </div>
    );
  }
  if (listingQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading listing…
      </div>
    );
  }
  if (listingQuery.error || !listing) {
    return (
      <div className="card border-destructive/40 text-destructive">
        {(listingQuery.error as Error)?.message || "Listing not found"}
      </div>
    );
  }

  const updateField = <K extends keyof PatentListingPatch>(
    key: K,
    value: PatentListingPatch[K],
  ) => setForm((f) => (f ? { ...f, [key]: value } : f));

  const tagsToInput = (tags?: string[]) => (tags || []).join(", ");
  const inputToTags = (raw: string) =>
    raw.split(",").map((s) => s.trim()).filter(Boolean);

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-2">
        <button
          onClick={() => router.push("/marketplace/inventor")}
          className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
        >
          <ArrowLeft className="h-3 w-3" /> Back to my listings
        </button>
      </div>

      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold leading-tight">{listing.title}</h1>
          <div className="text-xs text-muted-foreground font-mono">
            {listing.listing_id}
          </div>
        </div>
        <ListingStatusBadge status={listing.status} />
      </div>

      <div className="card space-y-4">
        <div className="text-sm font-medium">Listing metadata</div>

        {!editable && (
          <div className="text-xs rounded border border-amber-500/40 bg-amber-500/5 text-amber-200 px-3 py-2">
            This listing is in <strong>{listing.status}</strong> — editing is
            disabled. Move it back to draft to make changes.
          </div>
        )}

        <div className="space-y-2">
          <label className="text-xs text-muted-foreground">Title</label>
          <input
            className="input"
            disabled={!editable}
            value={form?.title ?? ""}
            onChange={(e) => updateField("title", e.target.value)}
          />
        </div>

        <div className="space-y-2">
          <label className="text-xs text-muted-foreground">
            Abstract{" "}
            <span className="text-muted-foreground/70">
              ({listing.abstract_status || "none"})
            </span>
          </label>
          <textarea
            className="input min-h-[120px]"
            disabled={!editable}
            placeholder="Paste the patent abstract. Helps buyers and improves matching quality."
            value={form?.abstract ?? ""}
            onChange={(e) => updateField("abstract", e.target.value)}
          />
        </div>

        <div className="grid md:grid-cols-2 gap-3">
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">
              Asking price (INR)
            </label>
            <input
              className="input"
              type="number"
              disabled={!editable}
              value={form?.asking_price_inr ?? ""}
              onChange={(e) =>
                updateField(
                  "asking_price_inr",
                  e.target.value === "" ? null : Number(e.target.value),
                )
              }
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">Inventor names</label>
            <input
              className="input"
              disabled={!editable}
              value={tagsToInput(form?.inventor_names)}
              onChange={(e) => updateField("inventor_names", inputToTags(e.target.value))}
              placeholder="comma-separated"
            />
          </div>
        </div>

        <div className="grid md:grid-cols-2 gap-3">
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">Domain tags</label>
            <input
              className="input"
              disabled={!editable}
              value={tagsToInput(form?.domain_tags)}
              onChange={(e) => updateField("domain_tags", inputToTags(e.target.value))}
              placeholder="electronics, materials, ai_ml…"
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">Industry tags</label>
            <input
              className="input"
              disabled={!editable}
              value={tagsToInput(form?.industry_tags)}
              onChange={(e) => updateField("industry_tags", inputToTags(e.target.value))}
              placeholder="semiconductors, biotech…"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 pt-2">
          <button
            className="btn-secondary"
            disabled={!editable || !form || patchMut.isPending}
            onClick={() => form && patchMut.mutate(form)}
          >
            {patchMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Save className="h-4 w-4 mr-2" />
            )}
            Save metadata
          </button>
          {patchMut.isSuccess && (
            <span className="text-xs text-emerald-400">Saved.</span>
          )}
          {patchMut.isError && (
            <span className="text-xs text-destructive">
              {lifecycleErrorMessage(patchMut.error)}
            </span>
          )}
        </div>
      </div>

      {/*
        Submit-for-approval action.

        Hidden when the underlying professor profile is a patent_stub —
        those listings are admin-only by design and the server would 403
        with STUB_REQUIRES_ADMIN_ACTIVATION. The server-side stub gate in
        transition_listing() is the source of truth; this UI suppression is
        just to avoid surfacing an action that would always fail.
      */}
      <div className="card space-y-3">
        <div className="text-sm font-medium">Activation</div>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Submitting moves this listing to <em>pending approval</em>. An admin /
          TTO must then approve it before it goes live (status <em>active</em>).
          This is the consent step — nothing is visible to buyers until that
          approval lands.
        </p>

        {isStubOwned ? (
          <div className="text-xs rounded border border-amber-500/40 bg-amber-500/5 text-amber-200 px-3 py-2">
            This listing belongs to a stub profile. Only admin / TTO can
            activate it. If you believe this patent is yours, contact the TTO to
            re-link it to your faculty record.
          </div>
        ) : listing.status === "draft" ? (
          <div className="space-y-2">
            {(listing.abstract_status || "none") === "none" && (
              <div className="text-xs rounded border border-amber-500/30 bg-amber-500/5 text-amber-200/90 px-3 py-2 leading-relaxed">
                <strong>Tip:</strong> Listings with an abstract match far better
                — paste yours above before submitting. You can still submit
                without one if you prefer.
              </div>
            )}
            <div className="flex items-center gap-2">
              <button
                className="btn-primary"
                disabled={transitionMut.isPending}
                onClick={() => transitionMut.mutate("pending_approval")}
              >
                {transitionMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                ) : (
                  <Send className="h-4 w-4 mr-2" />
                )}
                Submit for approval
              </button>
            </div>
          </div>
        ) : listing.status === "pending_approval" ? (
          <div className="space-y-2">
            <div className="text-xs text-amber-300">
              Waiting on admin / TTO approval.
            </div>
            <button
              className="btn-secondary"
              disabled={transitionMut.isPending}
              onClick={() => transitionMut.mutate("draft")}
            >
              Withdraw back to draft
            </button>
          </div>
        ) : listing.status === "active" ? (
          <div className="text-xs text-emerald-300">
            Live in the marketplace.
          </div>
        ) : (
          <div className="text-xs text-muted-foreground">
            No activation action available from <strong>{listing.status}</strong>.
          </div>
        )}

        {transitionMut.isError && (
          <div className="text-xs text-destructive">
            {lifecycleErrorMessage(transitionMut.error)}
          </div>
        )}
        {transitionMut.isSuccess && (
          <div className="text-xs text-emerald-400">
            Status updated → {transitionMut.data?.new_status}
          </div>
        )}
      </div>
    </div>
  );
}
