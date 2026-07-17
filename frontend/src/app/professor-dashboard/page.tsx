"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Megaphone, Sparkles } from "lucide-react";
import {
  getProfessors, getProfessorPatentsList, type Professor,
  browseAudience, upsertInstituteProfile, type InstituteProfile,
  discoverPatentPool, discoverPatentsGrouped, logMatchInteraction,
  type PatentBuyerType, type MatchResult, type ProfessorPatentGroup,
} from "@/lib/api";
import { cn, scoreColor } from "@/lib/utils";
import { MarketPatentPanel } from "@/components/market-patent";
import { ResearchOpportunityManager } from "@/components/opportunity-matching";

/** Public Professor Dashboard — no sign-in required. Two directions:
 *  - Market My Patents (sell): pick a professor, market one of their patents
 *    via Engine 5, which ranks Companies/Employees/Students/Professors/Institutes.
 *  - Discover Patents (buy): a professor or institute profile discovers the
 *    most relevant patents across the whole platform via Engine 5. Institutes
 *    see results grouped by professor + their affiliated institute, so
 *    patents from professors at other institutes are just as discoverable
 *    as the buyer's own. */
export default function ProfessorDashboardPage() {
  const [buyerMode, setBuyerMode] = useState<"professor" | "institute">("professor");

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Professor Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Pick a professor to market one of their patents (Engine 5), or
          switch to Institute to discover patents from professors across
          every institute on the platform, grouped by professor and their
          affiliated institute (Engine 5).
        </p>
      </div>

      <div className="flex gap-2 border-b border-border">
        {(["professor", "institute"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setBuyerMode(m)}
            className={cn(
              "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition",
              buyerMode === m
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {m === "professor" ? "Professor" : "Institute"}
          </button>
        ))}
      </div>

      {buyerMode === "professor" ? <ProfessorBuyerView /> : <InstituteBuyerView />}
    </div>
  );
}

function ProfessorBuyerView() {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["professors-with-patents"],
    queryFn: () => getProfessors(undefined, 600),
  });

  const withPatents = useMemo(
    () => (data?.professors ?? []).filter((p) => (p.patent_count ?? 0) > 0),
    [data],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return withPatents;
    return withPatents.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.department.toLowerCase().includes(q),
    );
  }, [withPatents, search]);

  const selected =
    filtered.find((p) => p.professor_id === selectedId) ?? filtered[0] ?? null;

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading professors…
      </div>
    );
  }
  if (error) {
    return <div className="card border-destructive/40 text-destructive">{(error as Error).message}</div>;
  }

  return (
    <div className="grid lg:grid-cols-[320px_1fr] gap-6">
      <div className="space-y-2">
        <input
          className="input"
          placeholder="Search by name or department…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="text-xs text-muted-foreground">
          {filtered.length} professors with patents
        </div>
        <div className="space-y-2 max-h-[65vh] overflow-y-auto pr-1">
          {filtered.map((p) => (
            <button
              key={p.professor_id}
              onClick={() => setSelectedId(p.professor_id)}
              className={cn(
                "w-full text-left card py-2 px-3 transition",
                selected?.professor_id === p.professor_id
                  ? "border-primary/60 bg-primary/5"
                  : "hover:border-primary/40",
              )}
            >
              <div className="text-sm font-medium leading-snug">{p.name}</div>
              <div className="text-[11px] text-muted-foreground">
                {p.department} · {p.patent_count} patent
                {p.patent_count === 1 ? "" : "s"}
              </div>
            </button>
          ))}
        </div>
      </div>

      <div>
        {selected ? (
          <ProfessorDetail professor={selected} />
        ) : (
          <div className="card text-sm text-muted-foreground">
            Select a professor to get started.
          </div>
        )}
      </div>
    </div>
  );
}

function ProfessorDetail({ professor }: { professor: Professor }) {
  const [marketingPatentId, setMarketingPatentId] = useState<string | null>(null);

  const { data } = useQuery({
    queryKey: ["professor-patents-list", professor.professor_id],
    queryFn: () => getProfessorPatentsList(professor.professor_id),
  });

  const patents = data?.patents ?? [];
  const marketing = patents.find((p) => p.patent_id === marketingPatentId) ?? null;

  return (
    <div className="space-y-6">
      <div className="card space-y-2">
        <div className="text-xs text-muted-foreground">{professor.department}</div>
        <h2 className="text-xl font-semibold">{professor.name}</h2>
        <div className="text-sm text-muted-foreground">
          {professor.patent_count} patent{professor.patent_count === 1 ? "" : "s"} on file
        </div>
        {patents.length > 0 && (
          <div className="space-y-1 pt-2">
            {patents.map((p) => (
              <div
                key={p.patent_id}
                className={cn(
                  "flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-xs",
                  marketingPatentId === p.patent_id ? "bg-primary/10" : "hover:bg-muted",
                )}
              >
                <div className="min-w-0">
                  <div className="text-foreground truncate">{p.title}</div>
                  <div className="text-muted-foreground">
                    {p.filing_date || "date unknown"} · {p.status}
                  </div>
                </div>
                <button
                  className="shrink-0 flex items-center gap-1 text-primary hover:underline"
                  onClick={() => setMarketingPatentId(p.patent_id)}
                >
                  <Megaphone className="h-3.5 w-3.5" /> Technology Transfer
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {marketing && (
        <MarketPatentPanel
          patentId={marketing.patent_id}
          patentTitle={marketing.title}
          professorId={professor.professor_id}
          professorName={professor.name}
        />
      )}

      <DiscoverPatentsPanel
        buyerType="professor"
        buyerId={professor.professor_id}
        buyerLabel={professor.name}
      />

      <ResearchOpportunityManager
        professorId={professor.professor_id}
        professorName={professor.name}
        department={professor.department}
      />
    </div>
  );
}

const EMPTY_INSTITUTE: InstituteProfile = {
  user_id: "", institute_name: "", focus_areas: [], departments: [], collaboration_types: [], bio: "",
};

function InstituteBuyerView() {
  const [instituteId, setInstituteId] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [form, setForm] = useState<InstituteProfile>(EMPTY_INSTITUTE);
  const [creating, setCreating] = useState(false);
  const qc = useQueryClient();

  const { data: institutes } = useQuery({
    queryKey: ["institute-profiles"],
    queryFn: () => browseAudience("institute"),
  });

  const saveMutation = useMutation({
    mutationFn: () => upsertInstituteProfile({ ...form, user_id: instituteId.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["institute-profiles"] });
      setActiveId(instituteId.trim());
      setCreating(false);
    },
  });

  const setListField = (key: keyof InstituteProfile, value: string) =>
    setForm((f) => ({ ...f, [key]: value.split(",").map((s) => s.trim()).filter(Boolean) }));

  const activeInstitute = (institutes?.candidates ?? []).find((c) => c.user_id === activeId);

  return (
    <div className="space-y-6">
      <div className="card space-y-2">
        <div className="grid md:grid-cols-[1fr_auto] gap-2">
          <input
            className="input"
            placeholder="Institute ID (e.g. INST-IISC, any identifier you choose)"
            value={instituteId}
            onChange={(e) => setInstituteId(e.target.value)}
          />
          <button
            className="btn-primary"
            disabled={!instituteId.trim()}
            onClick={() => {
              const existing = (institutes?.candidates ?? []).find((c) => c.user_id === instituteId.trim());
              if (existing) {
                setActiveId(instituteId.trim());
              } else {
                setForm({ ...EMPTY_INSTITUTE, user_id: instituteId.trim() });
                setCreating(true);
              }
            }}
          >
            Enter
          </button>
        </div>
        {institutes && institutes.candidates.length > 0 && (
          <div className="pt-1">
            <div className="text-xs text-muted-foreground mb-1">Or pick an existing institute:</div>
            <div className="flex flex-wrap gap-2">
              {institutes.candidates.map((c) => (
                <button
                  key={c.user_id}
                  className="px-3 py-1.5 rounded border border-border text-sm hover:border-primary/60 hover:bg-primary/5 transition"
                  onClick={() => {
                    setInstituteId(c.user_id);
                    setActiveId(c.user_id);
                  }}
                >
                  {c.institute_name || c.user_id}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {creating && (
        <div className="card space-y-3">
          <h2 className="text-lg font-semibold">Create Institute Profile</h2>
          <input
            className="input w-full"
            placeholder="Institute name"
            value={form.institute_name}
            onChange={(e) => setForm((f) => ({ ...f, institute_name: e.target.value }))}
          />
          <input
            className="input w-full"
            placeholder="Focus areas (comma-separated)"
            value={form.focus_areas.join(", ")}
            onChange={(e) => setListField("focus_areas", e.target.value)}
          />
          <input
            className="input w-full"
            placeholder="Departments (comma-separated)"
            value={form.departments.join(", ")}
            onChange={(e) => setListField("departments", e.target.value)}
          />
          <input
            className="input w-full"
            placeholder="Collaboration types (comma-separated)"
            value={form.collaboration_types.join(", ")}
            onChange={(e) => setListField("collaboration_types", e.target.value)}
          />
          <textarea
            className="input w-full"
            rows={2}
            placeholder="Bio / research focus"
            value={form.bio}
            onChange={(e) => setForm((f) => ({ ...f, bio: e.target.value }))}
          />
          <button
            className="btn-primary text-sm"
            disabled={!form.institute_name.trim() || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
            Save institute profile
          </button>
        </div>
      )}

      {activeId && (
        <DiscoverPatentsPanel
          buyerType="institute"
          buyerId={activeId}
          buyerLabel={activeInstitute?.institute_name || activeId}
        />
      )}
    </div>
  );
}

function DiscoverPatentsPanel({
  buyerType, buyerId, buyerLabel,
}: {
  buyerType: PatentBuyerType;
  buyerId: string;
  buyerLabel: string;
}) {
  if (buyerType === "institute") {
    return <InstituteDiscoverPanel buyerId={buyerId} buyerLabel={buyerLabel} />;
  }
  return <ProfessorDiscoverPanel buyerId={buyerId} buyerLabel={buyerLabel} />;
}

function ProfessorDiscoverPanel({ buyerId, buyerLabel }: { buyerId: string; buyerLabel: string }) {
  const buyerType: PatentBuyerType = "professor";
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["discover-patents", buyerType, buyerId],
    queryFn: () => discoverPatentPool(buyerType, buyerId, { top_k: 10 }),
    retry: false,
    enabled: false,
  });

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> Discover Patents for {buyerLabel}
        </h3>
        <button className="btn-primary text-sm shrink-0" disabled={isFetching} onClick={() => refetch()}>
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          Find Matching Patents
        </button>
      </div>

      {error ? (
        <div className="text-sm text-destructive">
          {(error as any)?.response?.data?.message || (error as Error).message}
        </div>
      ) : isLoading && isFetching ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" /> Matching against every patent on the platform…
        </div>
      ) : data ? (
        data.matches.length === 0 ? (
          <div className="text-sm text-muted-foreground">No matching patents found.</div>
        ) : (
          <div className="grid md:grid-cols-2 gap-3">
            {data.matches.map((m) => (
              <PatentPoolMatchCard key={m.target_id} match={m} buyerType={buyerType} buyerId={buyerId} />
            ))}
          </div>
        )
      ) : (
        <div className="text-sm text-muted-foreground">
          Click "Find Matching Patents" to rank every patent on the platform against this profile.
        </div>
      )}
    </div>
  );
}

function InstituteDiscoverPanel({ buyerId, buyerLabel }: { buyerId: string; buyerLabel: string }) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["discover-patents-grouped", buyerId],
    queryFn: () => discoverPatentsGrouped("institute", buyerId, { top_k_professors: 8, patents_per_professor: 4 }),
    retry: false,
    enabled: false,
  });

  return (
    <div className="card space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> Cross-Institute Patents for {buyerLabel}
        </h3>
        <button className="btn-primary text-sm shrink-0" disabled={isFetching} onClick={() => refetch()}>
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          Find Matching Patents
        </button>
      </div>

      {error ? (
        <div className="text-sm text-destructive">
          {(error as any)?.response?.data?.message || (error as Error).message}
        </div>
      ) : isLoading && isFetching ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" /> Matching against every patent on the platform, across every institute…
        </div>
      ) : data ? (
        data.groups.length === 0 ? (
          <div className="text-sm text-muted-foreground">No matching patents found.</div>
        ) : (
          <div className="space-y-4">
            {data.groups.map((g) => (
              <ProfessorPatentGroupCard key={g.professor_id} group={g} buyerId={buyerId} />
            ))}
          </div>
        )
      ) : (
        <div className="text-sm text-muted-foreground">
          Click "Find Matching Patents" to rank professors (and their patents) across every institute
          on the platform against this institute's profile.
        </div>
      )}
    </div>
  );
}

function ProfessorPatentGroupCard({ group, buyerId }: { group: ProfessorPatentGroup; buyerId: string }) {
  return (
    <div className="rounded-lg border border-border p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium text-sm">{group.professor_name}</div>
          <div className="text-[11px] text-muted-foreground">
            {group.department} · {group.institute} · {group.patent_count} patent{group.patent_count === 1 ? "" : "s"} available
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <div className={cn("text-sm font-mono font-bold", scoreColor(group.max_score))}>{Math.round(group.max_score)}</div>
          <span className="text-[10px] text-muted-foreground">avg {Math.round(group.average_score)}</span>
        </div>
      </div>

      {group.patents.length > 0 && (
        <div className="pt-2 mt-1 border-t border-border space-y-2">
          <div className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
            Matching Patents ({group.patents.length} of {group.patent_count} available)
          </div>
          <div className="grid md:grid-cols-2 gap-3">
            {group.patents.map((m) => (
              <PatentPoolMatchCard key={m.target_id} match={m} buyerType="institute" buyerId={buyerId} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PatentPoolMatchCard({
  match, buyerType, buyerId,
}: {
  match: MatchResult;
  buyerType: PatentBuyerType;
  buyerId: string;
}) {
  useEffect(() => {
    logMatchInteraction({
      source_kind: buyerType, source_id: buyerId, target_kind: "patent", target_id: match.target_id,
      interaction_type: "view", match_score: match.score,
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buyerType, buyerId, match.target_id]);

  const act = (type: "bookmark" | "licensing_request" | "purchase_request" | "collaboration_proposal" | "technology_transfer_request") =>
    logMatchInteraction({
      source_kind: buyerType, source_id: buyerId, target_kind: "patent", target_id: match.target_id,
      interaction_type: type, match_score: match.score,
    }).catch(() => {});

  return (
    <div className="rounded-lg border border-border p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm font-medium leading-snug">{match.target_name}</div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <div className={cn("text-sm font-mono font-bold", scoreColor(match.score))}>
            {Math.round(match.score)}
          </div>
          <span className="px-1.5 py-0.5 rounded bg-muted text-[10px] uppercase">{match.confidence}</span>
        </div>
      </div>
      <div className="text-[11px] text-muted-foreground">
        {match.professor_name} · {match.institute || "IIT Madras"} · {match.technology_domain}
      </div>
      {match.reasons.length > 0 && (
        <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
          {match.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        <span>Commercialization: {Math.round(match.commercialization_score)}%</span>
        <span>{match.patent_readiness}</span>
      </div>
      <div className="text-xs text-muted-foreground italic">
        Suggested: {match.suggested_action} ({match.collaboration_mode})
      </div>
      <div className="flex flex-wrap gap-2 pt-1">
        <button className="text-xs text-primary hover:underline" onClick={() => act("bookmark")}>Bookmark</button>
        <button className="text-xs text-primary hover:underline" onClick={() => act("licensing_request")}>Request Licensing</button>
        <button className="text-xs text-primary hover:underline" onClick={() => act("purchase_request")}>Request Purchase</button>
        <button className="text-xs text-primary hover:underline" onClick={() => act("collaboration_proposal")}>Propose Collaboration</button>
        <button className="text-xs text-primary hover:underline" onClick={() => act("technology_transfer_request")}>Technology Transfer</button>
      </div>
    </div>
  );
}
