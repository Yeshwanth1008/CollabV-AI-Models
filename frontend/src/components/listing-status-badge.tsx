import { cn } from "@/lib/utils";

const STYLES: Record<string, string> = {
  draft:            "bg-slate-500/15 text-slate-300 border-slate-500/30",
  pending_approval: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  active:           "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  paused:           "bg-blue-500/15 text-blue-300 border-blue-500/30",
  sold:             "bg-violet-500/15 text-violet-300 border-violet-500/30",
  withdrawn:        "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

const LABELS: Record<string, string> = {
  draft:            "Draft",
  pending_approval: "Pending approval",
  active:           "Active",
  paused:           "Paused",
  sold:             "Sold",
  withdrawn:        "Withdrawn",
};

export function ListingStatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        STYLES[status] ?? "bg-muted text-muted-foreground border-border",
      )}
    >
      {LABELS[status] ?? status}
    </span>
  );
}

// Translate the three lifecycle error codes from the backend into messages
// that a non-engineer inventor will understand.
export function lifecycleErrorMessage(err: any): string {
  const code = err?.code;
  if (code === "LISTING_NOT_ACTIVATABLE") {
    return "This listing can't move to that state from its current one. The activation flow is draft → pending_approval → active; once active, you can pause, withdraw, or mark sold.";
  }
  if (code === "STUB_REQUIRES_ADMIN_ACTIVATION") {
    return "This patent is owned by a stub profile (no claimed faculty account). Only admin/TTO can activate it. Reach out to the TTO if you believe this belongs to you.";
  }
  if (code === "LISTING_INACTIVE") {
    return "This listing is in a locked state and can't be edited right now. Edits are allowed only in draft or paused.";
  }
  return err?.message || "Action failed";
}
