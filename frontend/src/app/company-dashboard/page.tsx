"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Sparkles, Users } from "lucide-react";
import {
  listProblemStatements, getEngine4Matches, runMatchingEngine4,
  getCompanyProfile, upsertCompanyProfile, getCompanyRecommendations,
  logProfessorInteraction, listCompanyProfiles,
  type ProblemStatement, type CompanyProfile, type RecommendedProfessor,
} from "@/lib/api";
import { cn, scoreColor } from "@/lib/utils";
import { SmartMatchCard } from "@/components/smart-match-card";

const LIST_FIELDS = [
  ["products_services", "Products / Services"],
  ["technologies_used", "Technologies Used"],
  ["tech_stack", "Tech Stack"],
  ["research_interests", "Research & Innovation Interests"],
  ["focus_areas", "Focus Areas"],
  ["keywords", "Keywords"],
  ["existing_projects", "Existing Projects"],
  ["preferred_collaboration_areas", "Preferred Collaboration Areas"],
] as const;

const EMPTY_PROFILE: CompanyProfile = {
  company_id: "", company_name: "", description: "", industry: "", business_domain: "",
  products_services: [], technologies_used: [], tech_stack: [], research_interests: [],
  business_objectives: "", focus_areas: [], keywords: [], market_segment: "",
  innovation_challenges: "", strategic_goals: "", existing_projects: [],
  preferred_collaboration_areas: [], company_size: "", category: "",
};

export default function CompanyDashboardPage() {
  const [companyIdInput, setCompanyIdInput] = useState("");
  const [companyId, setCompanyId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["problem-statements"],
    queryFn: listProblemStatements,
  });

  const { data: existingProfiles } = useQuery({
    queryKey: ["company-profiles"],
    queryFn: listCompanyProfiles,
  });

  const statements = data?.problem_statements ?? [];
  const selected = statements.find((s) => s.id === selectedId) ?? null;

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Company Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Enter your company ID, optionally build a Company Profile and/or
          pick a research problem statement — the AI Matching Engine
          recommends professors and patents from whichever you have.
        </p>
      </div>

      <div className="card space-y-2">
        <div className="grid md:grid-cols-[1fr_auto] gap-2">
          <input
            className="input"
            placeholder="Company ID (e.g. COMP-ACME, any identifier you choose)"
            value={companyIdInput}
            onChange={(e) => setCompanyIdInput(e.target.value)}
          />
          <button
            className="btn-primary"
            disabled={!companyIdInput.trim()}
            onClick={() => setCompanyId(companyIdInput.trim())}
          >
            Enter
          </button>
        </div>
        {existingProfiles && existingProfiles.count > 0 && (
          <div className="pt-1">
            <div className="text-xs text-muted-foreground mb-1">
              Or pick an existing company to see the AI Matching Engine in action:
            </div>
            <div className="flex flex-wrap gap-2">
              {existingProfiles.profiles.map((p) => (
                <button
                  key={p.company_id}
                  className="px-3 py-1.5 rounded border border-border text-sm hover:border-primary/60 hover:bg-primary/5 transition"
                  onClick={() => {
                    setCompanyIdInput(p.company_id);
                    setCompanyId(p.company_id);
                  }}
                >
                  {p.company_name || p.company_id}
                  <span className="text-muted-foreground text-xs ml-1">· {p.industry}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {companyId && (
        <>
          <CompanyProfileSection companyId={companyId} />

          {isLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading problem statements…
            </div>
          ) : error ? (
            <div className="card border-destructive/40 text-destructive">
              {(error as Error).message}
            </div>
          ) : (
            <div className="space-y-2">
              <h2 className="text-lg font-semibold">Problem Statement (optional)</h2>
              <div className="grid lg:grid-cols-[320px_1fr] gap-6">
                <div className="space-y-2 max-h-[50vh] overflow-y-auto pr-1">
                  {statements.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => setSelectedId(selectedId === s.id ? null : s.id)}
                      className={cn(
                        "w-full text-left card py-2 px-3 transition",
                        selected?.id === s.id
                          ? "border-primary/60 bg-primary/5"
                          : "hover:border-primary/40",
                      )}
                    >
                      <div className="text-[11px] text-muted-foreground">{s.sector}</div>
                      <div className="text-sm font-medium leading-snug">{s.title}</div>
                    </button>
                  ))}
                </div>
                <div>
                  {selected ? (
                    <div className="card space-y-2">
                      <div className="text-xs text-muted-foreground">{selected.sector}</div>
                      <h3 className="text-lg font-semibold">{selected.title}</h3>
                      <p className="text-sm text-muted-foreground">{selected.problem_statement}</p>
                    </div>
                  ) : (
                    <div className="card text-sm text-muted-foreground">
                      No problem statement selected — recommendations will use your
                      Company Profile alone, if you've filled one in above.
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          <CompanyRecommendations companyId={companyId} problemStatementId={selectedId} />

          {selected && <LegacyPatentSmartMatches statement={selected} />}
        </>
      )}
    </div>
  );
}

function CompanyProfileSection({ companyId }: { companyId: string }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<CompanyProfile>({ ...EMPTY_PROFILE, company_id: companyId });

  const { data: profile, isLoading } = useQuery({
    queryKey: ["company-profile", companyId],
    queryFn: () => getCompanyProfile(companyId),
    retry: false,
  });

  useEffect(() => {
    if (profile) setForm(profile);
    else setForm({ ...EMPTY_PROFILE, company_id: companyId });
  }, [profile, companyId]);

  const saveMutation = useMutation({
    mutationFn: () => upsertCompanyProfile(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["company-profile", companyId] });
      setEditing(false);
    },
  });

  const setField = (key: keyof CompanyProfile, value: string) =>
    setForm((f) => ({ ...f, [key]: value }));
  const setListField = (key: keyof CompanyProfile, value: string) =>
    setForm((f) => ({ ...f, [key]: value.split(",").map((s) => s.trim()).filter(Boolean) }));

  if (isLoading) {
    return <div className="card text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin inline mr-2" />Loading company profile…</div>;
  }

  if (!profile && !editing) {
    return (
      <div className="card space-y-2">
        <div className="text-sm text-muted-foreground">No Company Profile on file for {companyId} yet.</div>
        <button className="btn-primary text-sm" onClick={() => setEditing(true)}>Create Company Profile</button>
      </div>
    );
  }

  if (!editing && profile) {
    return (
      <div className="card space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">{profile.company_name || companyId}</h2>
          <button className="text-primary text-sm hover:underline" onClick={() => setEditing(true)}>Edit profile</button>
        </div>
        <div className="text-xs text-muted-foreground">{profile.industry} · {profile.business_domain} · {profile.company_size} {profile.category}</div>
        {profile.description && <p className="text-sm text-muted-foreground">{profile.description}</p>}
        <div className="flex flex-wrap gap-1 text-[11px] pt-1">
          {[...profile.focus_areas, ...profile.keywords].slice(0, 8).map((k) => (
            <span key={k} className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{k}</span>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="card space-y-3">
      <h2 className="text-lg font-semibold">Company Profile</h2>
      <div className="grid md:grid-cols-2 gap-2">
        <input className="input" placeholder="Company name" value={form.company_name} onChange={(e) => setField("company_name", e.target.value)} />
        <input className="input" placeholder="Industry" value={form.industry} onChange={(e) => setField("industry", e.target.value)} />
        <input className="input" placeholder="Business domain" value={form.business_domain} onChange={(e) => setField("business_domain", e.target.value)} />
        <input className="input" placeholder="Market segment" value={form.market_segment} onChange={(e) => setField("market_segment", e.target.value)} />
        <input className="input" placeholder="Company size (e.g. small/medium/large)" value={form.company_size} onChange={(e) => setField("company_size", e.target.value)} />
        <input className="input" placeholder="Category (startup / enterprise)" value={form.category} onChange={(e) => setField("category", e.target.value)} />
      </div>
      <textarea className="input w-full" rows={2} placeholder="Company description" value={form.description} onChange={(e) => setField("description", e.target.value)} />
      <textarea className="input w-full" rows={2} placeholder="Business objectives" value={form.business_objectives} onChange={(e) => setField("business_objectives", e.target.value)} />
      <textarea className="input w-full" rows={2} placeholder="Innovation challenges" value={form.innovation_challenges} onChange={(e) => setField("innovation_challenges", e.target.value)} />
      <textarea className="input w-full" rows={2} placeholder="Strategic goals" value={form.strategic_goals} onChange={(e) => setField("strategic_goals", e.target.value)} />
      <div className="grid md:grid-cols-2 gap-2">
        {LIST_FIELDS.map(([key, label]) => (
          <input
            key={key}
            className="input"
            placeholder={`${label} (comma-separated)`}
            value={(form[key] as string[]).join(", ")}
            onChange={(e) => setListField(key, e.target.value)}
          />
        ))}
      </div>
      <div className="flex gap-2">
        <button className="btn-primary text-sm" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
          {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          Save profile
        </button>
        {profile && <button className="text-sm text-muted-foreground hover:text-foreground" onClick={() => setEditing(false)}>Cancel</button>}
      </div>
      {saveMutation.isError && <div className="text-xs text-destructive">{(saveMutation.error as Error)?.message}</div>}
    </div>
  );
}

function CompanyRecommendations({
  companyId, problemStatementId,
}: {
  companyId: string;
  problemStatementId: string | null;
}) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["company-recommendations", companyId, problemStatementId],
    queryFn: () => getCompanyRecommendations(companyId, {
      problem_statement_id: problemStatementId, top_k_professors: 5, patents_per_professor: 3,
    }),
    retry: false,
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-primary" /> AI Recommendations
        </h2>
        <button className="btn-primary text-sm" disabled={isFetching} onClick={() => refetch()}>
          {isFetching ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          Get AI Recommendations
        </button>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" /> Matching…
        </div>
      ) : error ? (
        <div className="card border-destructive/40 text-destructive text-sm">
          {(error as any)?.response?.data?.message || (error as Error).message}
        </div>
      ) : data ? (
        <>
          <div className="text-xs text-muted-foreground">
            Based on: {data.used_profile && "Company Profile"}
            {data.used_profile && data.used_problem_statement && " + "}
            {data.used_problem_statement && "Problem Statement"}
          </div>

          <section className="space-y-3">
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <Users className="h-4 w-4 text-primary" /> Recommended Professors
            </h3>
            {data.recommended_professors.length === 0 ? (
              <div className="card text-sm text-muted-foreground">No professor matches yet.</div>
            ) : (
              <div className="space-y-3">
                {data.recommended_professors.map((p) => (
                  <RecommendedProfessorCard key={p.professor_id} professor={p} companyId={companyId} />
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}

function RecommendedProfessorCard({ professor, companyId }: { professor: RecommendedProfessor; companyId: string }) {
  useEffect(() => {
    logProfessorInteraction(companyId, {
      professor_id: professor.professor_id, interaction_type: "view", match_score: professor.score,
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId, professor.professor_id]);

  const act = (type: "connect" | "invite_collaborate") =>
    logProfessorInteraction(companyId, {
      professor_id: professor.professor_id, interaction_type: type, match_score: professor.score,
    }).catch(() => {});

  return (
    <div className="card space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-medium">{professor.professor_name}</div>
          <div className="text-[11px] text-muted-foreground">{professor.institution} · {professor.department}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className={cn("text-sm font-mono font-bold", scoreColor(professor.score))}>{Math.round(professor.score)}</div>
          <span className="px-1.5 py-0.5 rounded bg-muted text-[10px] uppercase">{professor.confidence}</span>
        </div>
      </div>
      {professor.research_areas.length > 0 && (
        <div className="flex flex-wrap gap-1 text-[11px]">
          {professor.research_areas.slice(0, 3).map((r) => (
            <span key={r} className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">{r}</span>
          ))}
        </div>
      )}
      {professor.reasons.length > 0 && (
        <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
          {professor.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}
      <div className="text-xs text-muted-foreground italic">Suggested: {professor.suggested_collaboration_type}</div>
      <div className="flex gap-2 pt-1">
        <a href={`/professors/${professor.professor_id}`} className="text-xs text-primary hover:underline">View Profile</a>
        <button className="text-xs text-primary hover:underline" onClick={() => act("connect")}>Connect</button>
        <button className="text-xs text-primary hover:underline" onClick={() => act("invite_collaborate")}>Invite to Collaborate</button>
      </div>

      {professor.matching_patents.length > 0 && (
        <div className="pt-2 mt-1 border-t border-border space-y-2">
          <div className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
            Matching Patents ({professor.matching_patents.length})
          </div>
          <div className="grid sm:grid-cols-2 gap-2">
            {professor.matching_patents.map((patent) => (
              <div key={patent.patent_id} className="rounded border border-border bg-muted/30 p-2 space-y-1">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-xs font-medium leading-snug">{patent.patent_title}</div>
                  <div className={cn("text-xs font-mono font-bold shrink-0", scoreColor(patent.score))}>{Math.round(patent.score)}</div>
                </div>
                {patent.reasons.length > 0 && (
                  <div className="text-[11px] text-muted-foreground">{patent.reasons.slice(0, 2).join(" · ")}</div>
                )}
                <button
                  className="text-[11px] text-primary hover:underline"
                  onClick={() => logProfessorInteraction(companyId, {
                    professor_id: professor.professor_id, interaction_type: "view", match_score: patent.score,
                  }).catch(() => {})}
                >
                  Contact Professor
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function LegacyPatentSmartMatches({ statement }: { statement: ProblemStatement }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["engine4-matches", statement.id],
    queryFn: () => getEngine4Matches(statement.id),
  });

  const runMutation = useMutation({
    mutationFn: () => runMatchingEngine4(statement.id, 10),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["engine4-matches", statement.id] }),
  });

  const matches = data?.matches ?? [];

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-lg font-semibold">Patent Smart Matches (saved, per-problem-statement)</h3>
        <button className="btn-primary text-sm shrink-0" disabled={runMutation.isPending} onClick={() => runMutation.mutate()}>
          {runMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2 inline" /> : null}
          {matches.length > 0 ? "Refresh matches" : "Find smart matches"}
        </button>
      </div>
      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</div>
      ) : matches.length === 0 ? (
        <div className="card text-sm text-muted-foreground">No saved matches yet for this problem statement.</div>
      ) : (
        <div className="grid md:grid-cols-2 gap-3">
          {matches.map((m) => (
            <SmartMatchCard key={m.match_id} match={m} title={m.patent_title || m.patent_number} subtitle={`${m.professor_name}${m.department ? " · " + m.department : ""}`} />
          ))}
        </div>
      )}
    </section>
  );
}
