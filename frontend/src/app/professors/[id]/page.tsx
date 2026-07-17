"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { getProfessor, getProfessorPatents, getProfessorReadiness } from "@/lib/api";
import { ScoreBar } from "@/components/score-bar";

export default function ProfessorDetail() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const profile = useQuery({ queryKey: ["prof", id], queryFn: () => getProfessor(id) });
  const patents = useQuery({ queryKey: ["patents", id], queryFn: () => getProfessorPatents(id) });
  const readiness = useQuery({ queryKey: ["readiness", id], queryFn: () => getProfessorReadiness(id) });

  if (profile.isLoading) return <div className="text-muted-foreground">Loading…</div>;
  const p = profile.data;
  if (!p) return <div>Not found</div>;

  return (
    <div className="space-y-6">
      <div className="card">
        <h1 className="text-3xl font-bold">{p.name}</h1>
        <p className="text-muted-foreground">{p.department}</p>
        {p.designation && <p className="text-sm text-muted-foreground">{p.designation}</p>}
        {p.biography && <p className="mt-4 text-sm leading-relaxed">{p.biography}</p>}
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        <div className="card">
          <h2 className="font-semibold mb-3">Research areas</h2>
          <div className="flex flex-wrap gap-1.5">
            {(p.research_areas ?? []).map((r: string) => (
              <span key={r} className="text-xs px-2 py-1 rounded-full bg-muted">
                {r}
              </span>
            ))}
          </div>
        </div>

        <div className="card">
          <h2 className="font-semibold mb-3">Collaboration readiness</h2>
          {readiness.isLoading ? (
            <div className="text-muted-foreground text-sm">Loading…</div>
          ) : readiness.data ? (
            <div className="space-y-3">
              <div className="text-3xl font-mono font-bold">
                {Number(readiness.data.overall_score).toFixed(0)}
              </div>
              <div className="text-xs text-muted-foreground">{readiness.data.band} · {readiness.data.confidence} confidence</div>
              <ScoreBar label="Industry" value={readiness.data.breakdown.industry_engagement} />
              <ScoreBar label="Publications" value={readiness.data.breakdown.publication_velocity} />
              <ScoreBar label="Patents" value={readiness.data.breakdown.patent_activity} />
              <ScoreBar label="Bandwidth" value={readiness.data.breakdown.seniority_bandwidth} />
              <ScoreBar label="Infrastructure" value={readiness.data.breakdown.infrastructure} />
              {readiness.data.drivers?.length > 0 && (
                <div className="text-sm">
                  <div className="text-xs text-muted-foreground mb-1">Drivers</div>
                  <ul className="list-disc list-inside text-sm text-success">
                    {readiness.data.drivers.map((d: string, i: number) => <li key={i}>{d}</li>)}
                  </ul>
                </div>
              )}
              {readiness.data.blockers?.length > 0 && (
                <div className="text-sm">
                  <div className="text-xs text-muted-foreground mb-1">Blockers</div>
                  <ul className="list-disc list-inside text-sm text-destructive">
                    {readiness.data.blockers.map((d: string, i: number) => <li key={i}>{d}</li>)}
                  </ul>
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>

      <div className="card">
        <h2 className="font-semibold mb-3">Patent portfolio</h2>
        {patents.isLoading ? (
          <div className="text-muted-foreground text-sm">Loading…</div>
        ) : patents.data?.portfolio.has_patents ? (
          <div className="grid md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <div className="text-3xl font-mono font-bold">
                {Number(patents.data.portfolio.total_score).toFixed(0)}
              </div>
              <div className="text-xs text-muted-foreground">
                {patents.data.portfolio.patent_count} patents · newest {patents.data.portfolio.newest_patent_year ?? "—"}
              </div>
              <ScoreBar label="Count" value={patents.data.portfolio.count_score} />
              <ScoreBar label="Recency" value={patents.data.portfolio.recency_score} />
              <ScoreBar label="Status" value={patents.data.portfolio.status_score} />
              <ScoreBar label="Diversity" value={patents.data.portfolio.diversity_score} />
              <ScoreBar label="Collaboration" value={patents.data.portfolio.collaboration_score} />
            </div>
            <div>
              <div className="text-xs text-muted-foreground mb-2">Recent patents</div>
              <ul className="space-y-2 text-sm">
                {patents.data.recent_patents.map((rp: any, i: number) => (
                  <li key={i} className="border-l-2 border-primary pl-3">
                    <div className="font-medium">{rp.title}</div>
                    <div className="text-xs text-muted-foreground">
                      {rp.filing_date} · {rp.status}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No patents on file</p>
        )}
      </div>

      <div className="card">
        <h2 className="font-semibold mb-3">Publications ({(p.publications ?? []).length})</h2>
        <ul className="text-sm space-y-1 list-disc list-inside text-muted-foreground max-h-80 overflow-auto">
          {(p.publications ?? []).slice(0, 50).map((pub: any, i: number) => (
            <li key={i}>{String(pub)}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
