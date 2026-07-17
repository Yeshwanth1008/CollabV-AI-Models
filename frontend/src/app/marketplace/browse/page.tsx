"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";
import { browseListings } from "@/lib/api";
import { ListingCard } from "@/components/listing-card";

export default function BrowseListingsPage() {
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState("");
  const [industry, setIndustry] = useState("");

  const params = useMemo(() => ({
    q: q.trim() || undefined,
    domain: domain.trim() || undefined,
    industry: industry.trim() || undefined,
    limit: 50,
  }), [q, domain, industry]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["browse", params],
    queryFn: () => browseListings(params),
  });

  const listings = data?.listings ?? [];

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Browse patents</h1>
        <p className="text-sm text-muted-foreground">
          Active IITM patents available for licensing or purchase. Listings only
          appear here after the inventor submits and admin/TTO approves.
        </p>
      </div>

      <div className="card flex flex-wrap gap-3">
        <div className="flex items-center gap-2 flex-1 min-w-[220px]">
          <Search className="h-4 w-4 text-muted-foreground" />
          <input
            className="input flex-1"
            placeholder="Search titles…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <input
          className="input max-w-[200px]"
          placeholder="Domain (e.g. electronics)"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
        />
        <input
          className="input max-w-[200px]"
          placeholder="Industry (e.g. semiconductors)"
          value={industry}
          onChange={(e) => setIndustry(e.target.value)}
        />
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading…
        </div>
      ) : error ? (
        <div className="card border-destructive/40 text-destructive">
          {(error as Error).message}
        </div>
      ) : listings.length === 0 ? (
        <div className="card text-sm text-muted-foreground space-y-2">
          <div className="font-medium text-foreground">
            No active patents match your filters.
          </div>
          <p>
            {data?.count === 0 && !q && !domain && !industry
              ? "There are no active patent listings yet. Patents become visible here once an inventor submits one for approval and an admin activates it."
              : "Try clearing the filters."}
          </p>
        </div>
      ) : (
        <>
          <div className="text-xs text-muted-foreground">
            {data?.count} active listing{data?.count === 1 ? "" : "s"}
            {data?.has_more ? ` · showing first ${listings.length}` : ""}
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
            {listings.map((l) => (
              <ListingCard key={l.listing_id} listing={l} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
