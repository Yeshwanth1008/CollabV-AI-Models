"use client";

import Link from "next/link";
import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Send } from "lucide-react";
import {
  createInquiry,
  getListing,
  type PatentListing,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-store";
import { ListingStatusBadge } from "@/components/listing-status-badge";
import {
  patentNumberLabel,
  hasAbstract,
} from "@/components/listing-card";

export default function PublicListingDetailPage() {
  const params = useParams<{ id: string }>();
  const listingId = params.id;
  const { user } = useAuth();

  const { data, isLoading, error } = useQuery({
    queryKey: ["public-listing", listingId],
    queryFn: () => getListing(listingId),
    retry: false, // don't retry 404s
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading…
      </div>
    );
  }

  // Backend returns LISTING_NOT_FOUND for both genuinely-missing and
  // not-active-and-not-yours. Render a clean not-found page in both cases
  // (don't leak existence — that's the server's whole reason for the same
  // error code).
  if (error) {
    return (
      <NotFound listingId={listingId} />
    );
  }

  const listing = data as PatentListing;
  return <DetailBody listing={listing} signedIn={!!user} />;
}

function NotFound({ listingId }: { listingId: string }) {
  return (
    <div className="max-w-xl space-y-4">
      <h1 className="text-2xl font-bold">Patent not found</h1>
      <p className="text-sm text-muted-foreground">
        No active patent matches{" "}
        <span className="font-mono text-xs">{listingId}</span>. It may have
        been withdrawn, isn't yet activated, or never existed.
      </p>
      <Link href="/marketplace/browse" className="text-primary text-sm underline">
        ← Back to browse
      </Link>
    </div>
  );
}

function DetailBody({
  listing,
  signedIn,
}: {
  listing: PatentListing;
  signedIn: boolean;
}) {
  const patNum = patentNumberLabel(listing);
  const showAbstract = hasAbstract(listing);

  return (
    <div className="space-y-6 max-w-3xl">
      <Link
        href="/marketplace/browse"
        className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1 w-fit"
      >
        <ArrowLeft className="h-3 w-3" /> Browse all patents
      </Link>

      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold leading-tight">{listing.title}</h1>
          {patNum && (
            <div className="text-xs text-muted-foreground font-mono">{patNum}</div>
          )}
        </div>
        <ListingStatusBadge status={listing.status} />
      </div>

      <div className="card space-y-4">
        <div>
          <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">
            Abstract
          </div>
          {showAbstract ? (
            <p className="text-sm whitespace-pre-wrap">{listing.abstract}</p>
          ) : (
            <p className="text-sm italic text-muted-foreground/60">
              Abstract not yet provided by the inventor. Reach out via the
              inquiry below for details.
            </p>
          )}
        </div>

        {!!(listing.inventor_names || []).length && (
          <div>
            <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">
              Inventors
            </div>
            <p className="text-sm">{(listing.inventor_names || []).join(", ")}</p>
          </div>
        )}

        {!!listing.asking_price_inr && (
          <div>
            <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">
              Asking price
            </div>
            <p className="text-sm">
              ₹{Number(listing.asking_price_inr).toLocaleString("en-IN")}
            </p>
          </div>
        )}

        {(!!(listing.domain_tags || []).length ||
          !!(listing.industry_tags || []).length) && (
          <div>
            <div className="text-xs uppercase tracking-wider text-muted-foreground mb-1">
              Tags
            </div>
            <div className="flex flex-wrap gap-1">
              {(listing.domain_tags || []).map((t) => (
                <span
                  key={`d-${t}`}
                  className="text-[11px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground"
                >
                  {t}
                </span>
              ))}
              {(listing.industry_tags || []).map((t) => (
                <span
                  key={`i-${t}`}
                  className="text-[11px] px-1.5 py-0.5 rounded bg-accent/10 text-accent"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <InquirySection listing={listing} signedIn={signedIn} />
    </div>
  );
}

function InquirySection({
  listing,
  signedIn,
}: {
  listing: PatentListing;
  signedIn: boolean;
}) {
  const [message, setMessage] = useState("");
  const mut = useMutation({
    mutationFn: () => createInquiry(listing.listing_id, message.trim()),
  });

  // The active-only guard is enforced server-side (LISTING_INACTIVE on POST).
  // We mirror it here so the action isn't surfaced on a stale/withdrawn page.
  const canInquire = listing.status === "active";

  if (!canInquire) {
    return (
      <div className="card text-sm text-muted-foreground">
        Inquiries aren't accepted on this listing — it's currently{" "}
        <strong>{listing.status}</strong>.
      </div>
    );
  }

  if (!signedIn) {
    return (
      <div className="card space-y-2">
        <div className="text-sm font-medium">Interested?</div>
        <p className="text-sm text-muted-foreground">
          You need an account to send an inquiry. Sign in or create one (any
          role can inquire — companies, researchers, students).
        </p>
        <div>
          <Link href="/login" className="btn-primary inline-flex">
            Sign in to inquire
          </Link>
        </div>
      </div>
    );
  }

  if (mut.isSuccess) {
    return (
      <div className="card border-emerald-500/40 bg-emerald-500/5 text-emerald-200 text-sm space-y-1">
        <div className="font-medium">Inquiry sent.</div>
        <div className="text-xs text-emerald-300/80">
          Reference{" "}
          <span className="font-mono">{mut.data?.inquiry_id}</span>. The
          inventor / TTO will be in touch.
        </div>
      </div>
    );
  }

  return (
    <div className="card space-y-3">
      <div className="text-sm font-medium">Send an inquiry</div>
      <p className="text-xs text-muted-foreground">
        Briefly describe your interest. The inventor and the technology
        transfer office will see the message.
      </p>
      <textarea
        className="input min-h-[100px]"
        placeholder="e.g. We're evaluating this for low-cost arsenic remediation in rural deployments. Can we discuss licensing terms?"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
      />
      <div className="flex flex-wrap items-center gap-2">
        <button
          className="btn-primary"
          disabled={!message.trim() || mut.isPending}
          onClick={() => mut.mutate()}
        >
          {mut.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
          ) : (
            <Send className="h-4 w-4 mr-2" />
          )}
          Send inquiry
        </button>
        {mut.isError && (
          <span className="text-xs text-destructive">
            {(mut.error as Error)?.message || "Could not send inquiry."}
          </span>
        )}
      </div>
    </div>
  );
}
