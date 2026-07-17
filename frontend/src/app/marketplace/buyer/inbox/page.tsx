"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Inbox, Loader2, MessagesSquare, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  getInbox,
  respondToInquiry,
  type InquiryRow,
  type InquiryStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-store";

const STATUS_STYLES: Record<InquiryStatus, string> = {
  new:          "bg-amber-500/15 text-amber-300 border-amber-500/30",
  acknowledged: "bg-blue-500/15 text-blue-300 border-blue-500/30",
  accepted:     "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  declined:     "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

function StatusBadge({ status }: { status: InquiryStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        STATUS_STYLES[status] ?? "bg-muted text-muted-foreground border-border",
      )}
    >
      {status}
    </span>
  );
}

export default function InboxPage() {
  const { user } = useAuth();
  const { data, isLoading, error } = useQuery({
    queryKey: ["inbox"],
    queryFn: getInbox,
    enabled: !!user,
  });

  if (!user) {
    return (
      <div className="card">
        Please <Link href="/login" className="text-primary underline">sign in</Link>{" "}
        to see your inbox.
      </div>
    );
  }
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading inbox…
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

  const sent     = data?.sent ?? [];
  const received = data?.received ?? [];

  return (
    <div className="space-y-8">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Inbox className="h-5 w-5" />
          <h1 className="text-3xl font-bold">Inbox</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          Inquiries you've sent appear at the top. If you're a linked inventor,
          inquiries on your listings appear below.
        </p>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <MessagesSquare className="h-4 w-4" /> Sent ({sent.length})
        </h2>
        {sent.length === 0 ? (
          <div className="card text-sm text-muted-foreground">
            You haven't sent any inquiries yet.{" "}
            <Link href="/marketplace/browse" className="text-primary underline">
              Browse patents
            </Link>{" "}
            to find one.
          </div>
        ) : (
          <div className="space-y-2">
            {sent.map((i) => (
              <SentInquiryCard key={i.inquiry_id} inq={i} />
            ))}
          </div>
        )}
      </section>

      {data?.is_inventor && (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <MessagesSquare className="h-4 w-4" /> Received on your listings
            ({received.length})
          </h2>
          {received.length === 0 ? (
            <div className="card text-sm text-muted-foreground">
              No buyers have inquired on your listings yet.
            </div>
          ) : (
            <div className="space-y-2">
              {received.map((i) => (
                <ReceivedInquiryCard key={i.inquiry_id} inq={i} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function SentInquiryCard({ inq }: { inq: InquiryRow }) {
  return (
    <div className="card space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <Link
            href={`/marketplace/patents/${inq.listing_id}`}
            className="font-medium hover:text-primary"
          >
            {inq.listing_title || inq.listing_id}
          </Link>
          <div className="text-[11px] text-muted-foreground font-mono">
            {inq.inquiry_id} · sent {new Date(inq.created_at * 1000).toLocaleString()}
          </div>
        </div>
        <StatusBadge status={inq.status} />
      </div>
      {inq.message && (
        <div className="text-sm text-muted-foreground italic whitespace-pre-wrap">
          "{inq.message}"
        </div>
      )}
      {inq.status !== "new" && inq.responded_at && (
        <div className="text-xs text-muted-foreground">
          Inventor responded{" "}
          {new Date(inq.responded_at * 1000).toLocaleString()}.
        </div>
      )}
    </div>
  );
}

function ReceivedInquiryCard({ inq }: { inq: InquiryRow }) {
  const qc = useQueryClient();
  const mut = useMutation({
    mutationFn: (status: "acknowledged" | "accepted" | "declined") =>
      respondToInquiry(inq.inquiry_id, status),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["inbox"] }),
  });

  const isFinal = inq.status === "accepted" || inq.status === "declined";
  return (
    <div className="card space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <Link
            href={`/marketplace/patents/${inq.listing_id}`}
            className="font-medium hover:text-primary"
          >
            {inq.listing_title || inq.listing_id}
          </Link>
          <div className="text-xs text-muted-foreground">
            from{" "}
            <span className="font-mono">{inq.requester_email || inq.user_id}</span>
          </div>
          <div className="text-[11px] text-muted-foreground font-mono">
            {inq.inquiry_id} · received{" "}
            {new Date(inq.created_at * 1000).toLocaleString()}
          </div>
        </div>
        <StatusBadge status={inq.status} />
      </div>
      {inq.message && (
        <div className="text-sm whitespace-pre-wrap rounded bg-muted/30 px-3 py-2">
          {inq.message}
        </div>
      )}
      {!isFinal && (
        <div className="flex flex-wrap gap-2 pt-1">
          {inq.status === "new" && (
            <button
              className="btn-secondary"
              disabled={mut.isPending}
              onClick={() => mut.mutate("acknowledged")}
            >
              {mut.isPending && mut.variables === "acknowledged" ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : null}
              Acknowledge
            </button>
          )}
          <button
            className="btn-primary"
            disabled={mut.isPending}
            onClick={() => mut.mutate("accepted")}
          >
            {mut.isPending && mut.variables === "accepted" ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <CheckCircle2 className="h-4 w-4 mr-2" />
            )}
            Accept
          </button>
          <button
            className="btn-secondary"
            disabled={mut.isPending}
            onClick={() => mut.mutate("declined")}
          >
            {mut.isPending && mut.variables === "declined" ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <XCircle className="h-4 w-4 mr-2" />
            )}
            Decline
          </button>
        </div>
      )}
      {mut.isError && (
        <div className="text-xs text-destructive">
          {(mut.error as Error)?.message || "Response failed"}
        </div>
      )}
      {isFinal && inq.responded_at && (
        <div className="text-xs text-muted-foreground">
          You {inq.status} this inquiry on{" "}
          {new Date(inq.responded_at * 1000).toLocaleString()}.
        </div>
      )}
    </div>
  );
}
