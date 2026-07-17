import Link from "next/link";
import type { PatentListing } from "@/lib/api";

// Two content-gap helpers surfaced while driving the inventor UI:
//
// 1) patentNumberLabel: bare numbers like "1114" read as noise. Prefer
//    "IN <number>" when we have the granted patent number, fall back to
//    "IDF #<idf_code>" when only the IITM-internal IDF code is on file,
//    and return null when neither exists (caller should hide the row).
//
// 2) hasAbstract: title-only patents look broken in a card layout. Callers
//    should render a muted "Abstract not yet provided" placeholder when
//    this returns false, NOT leave the slot blank.

export function patentNumberLabel(l: Partial<PatentListing>): string | null {
  if (l.indian_patent_number) return `IN ${l.indian_patent_number}`;
  if (l.patent_number) return `IDF #${l.patent_number}`;
  return null;
}

export function hasAbstract(l: Partial<PatentListing>): boolean {
  if (!l.abstract) return false;
  if ((l.abstract_status || "none") === "none") return false;
  return l.abstract.trim().length > 0;
}

export function ListingCard({ listing }: { listing: PatentListing }) {
  const patNum = patentNumberLabel(listing);
  const showAbstract = hasAbstract(listing);
  return (
    <Link
      href={`/marketplace/patents/${listing.listing_id}`}
      className="card hover:border-primary/50 transition space-y-3 block"
    >
      <div className="font-medium leading-snug line-clamp-2">{listing.title}</div>

      {/* Abstract slot — placeholder when missing so cards don't look broken */}
      {showAbstract ? (
        <p className="text-xs text-muted-foreground line-clamp-3">
          {listing.abstract}
        </p>
      ) : (
        <p className="text-xs italic text-muted-foreground/60">
          Abstract not yet provided by the inventor.
        </p>
      )}

      {patNum && (
        <div className="text-[11px] text-muted-foreground/80 font-mono">
          {patNum}
        </div>
      )}

      <div className="flex flex-wrap gap-1">
        {(listing.domain_tags || []).slice(0, 4).map((t) => (
          <span
            key={t}
            className="text-[11px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground"
          >
            {t}
          </span>
        ))}
      </div>
    </Link>
  );
}
