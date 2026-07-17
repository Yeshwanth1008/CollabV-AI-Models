"use client";

import { useState, useMemo } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
  Loader2,
  Search,
  ChevronDown,
  ChevronUp,
  BookOpen,
  Zap,
  Target,
  Award,
  AlertCircle,
  TrendingUp,
  Users,
  Building2,
  BarChart3,
  CheckCircle2,
  XCircle,
  HelpCircle,
} from "lucide-react";
import {
  getProfessors,
  runProfessorMatch,
  type ProfessorMatchResponse,
  type CompanyMatchResult,
} from "@/lib/api";
import { ScoreBar } from "@/components/score-bar";
import { cn } from "@/lib/utils";

// ─── Colour helpers ───────────────────────────────────────────────────────────

function matchLevelColor(level: string) {
  switch (level) {
    case "Excellent": return "bg-emerald-500/15 text-emerald-400 border-emerald-500/30";
    case "Strong":    return "bg-green-500/15 text-green-400 border-green-500/30";
    case "Moderate":  return "bg-blue-500/15 text-blue-400 border-blue-500/30";
    case "Weak":      return "bg-yellow-500/15 text-yellow-500 border-yellow-500/30";
    default:          return "bg-muted text-muted-foreground border-border";
  }
}

function recColor(rec: string) {
  switch (rec) {
    case "Highly Recommended": return "text-emerald-400";
    case "Recommended":        return "text-green-400";
    case "Consider":           return "text-blue-400";
    default:                   return "text-muted-foreground";
  }
}

function scoreColor(s: number) {
  if (s >= 75) return "text-emerald-400";
  if (s >= 60) return "text-blue-400";
  if (s >= 40) return "text-yellow-500";
  return "text-muted-foreground";
}

function recIcon(rec: string) {
  switch (rec) {
    case "Highly Recommended": return <CheckCircle2 className="h-4 w-4 text-emerald-400" />;
    case "Recommended":        return <CheckCircle2 className="h-4 w-4 text-green-400" />;
    case "Consider":           return <HelpCircle className="h-4 w-4 text-blue-400" />;
    default:                   return <XCircle className="h-4 w-4 text-muted-foreground" />;
  }
}

// ─── Company match card ───────────────────────────────────────────────────────

function CompanyMatchCard({ result }: { result: CompanyMatchResult }) {
  const [expanded, setExpanded] = useState(false);
  const bd = result.score_breakdown;

  return (
    <div className="card">
      {/* Header row */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-mono text-muted-foreground">#{result.rank}</span>
            <span className={cn("text-xs px-2 py-0.5 rounded-full border", matchLevelColor(result.match_level))}>
              {result.match_level}
            </span>
            <span className={cn("flex items-center gap-1 text-xs font-medium", recColor(result.recommendation))}>
              {recIcon(result.recommendation)} {result.recommendation}
            </span>
            <span className="text-xs text-muted-foreground">Confidence: {result.confidence_score}%</span>
          </div>

          <h3 className="mt-1 text-base font-semibold leading-tight">{result.company_name}</h3>
          <p className="text-sm text-muted-foreground">{result.project_title}</p>

          <div className="mt-2 flex flex-wrap gap-2">
            <span className="text-xs px-2 py-0.5 rounded-full bg-muted border border-border text-muted-foreground">
              {result.industry_domain}
            </span>
            {result.location && result.location !== "nan" && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-muted border border-border text-muted-foreground">
                {result.location}
              </span>
            )}
            {result.collaboration_type && result.collaboration_type !== "nan" && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                {result.collaboration_type}
              </span>
            )}
            {result.timeline && result.timeline !== "nan" && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-muted border border-border text-muted-foreground">
                {result.timeline}
              </span>
            )}
          </div>
        </div>

        {/* Score dial */}
        <div className="text-right shrink-0">
          <div className={cn("text-4xl font-mono font-bold", scoreColor(result.score))}>
            {result.score.toFixed(0)}
          </div>
          <div className="text-xs text-muted-foreground">/ 100</div>
        </div>
      </div>

      {/* Score bars */}
      <div className="mt-4 grid grid-cols-2 md:grid-cols-5 gap-3">
        <ScoreBar label="Domain (30)" value={(bd.research_domain / 30) * 100} />
        <ScoreBar label="Skills (25)"  value={(bd.technical_skills / 25) * 100} />
        <ScoreBar label="AI/ML (20)"   value={(bd.ai_methods / 20) * 100} />
        <ScoreBar label="Pubs (15)"    value={(bd.publications / 15) * 100} />
        <ScoreBar label="Industry (10)" value={(bd.industry_domain / 10) * 100} />
      </div>

      {/* Top reasons (always visible) */}
      {result.reasons.length > 0 && (
        <div className="mt-3 space-y-1">
          {result.reasons.slice(0, 2).map((r, i) => (
            <p key={i} className="text-xs text-muted-foreground flex gap-1">
              <span className="text-primary mt-0.5">•</span> {r}
            </p>
          ))}
        </div>
      )}

      {/* Expand toggle */}
      <div className="mt-3 flex gap-3">
        <button
          onClick={() => setExpanded((x) => !x)}
          className="btn-ghost text-xs gap-1 ml-auto"
        >
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {expanded ? "Collapse" : "Full analysis"}
        </button>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="mt-4 border-t border-border pt-4 space-y-4 text-sm">
          {/* Score breakdown */}
          <div>
            <h4 className="font-medium mb-2 text-xs uppercase tracking-wide text-muted-foreground">
              Score Breakdown
            </h4>
            <div className="grid grid-cols-2 gap-1 text-xs font-mono">
              <span className="text-muted-foreground">Research Domain (30%)</span>
              <span className="text-right">{bd.research_domain.toFixed(1)} / 30</span>
              <span className="text-muted-foreground">Technical Skills (25%)</span>
              <span className="text-right">{bd.technical_skills.toFixed(1)} / 25</span>
              <span className="text-muted-foreground">AI/ML Methods  (20%)</span>
              <span className="text-right">{bd.ai_methods.toFixed(1)} / 20</span>
              <span className="text-muted-foreground">Publications   (15%)</span>
              <span className="text-right">{bd.publications.toFixed(1)} / 15</span>
              <span className="text-muted-foreground">Industry Domain(10%)</span>
              <span className="text-right">{bd.industry_domain.toFixed(1)} / 10</span>
            </div>
          </div>

          {/* Matching elements grid */}
          <div className="grid md:grid-cols-2 gap-4">
            {result.matching_research_areas.length > 0 && (
              <div>
                <h4 className="font-medium mb-1 flex items-center gap-1 text-xs uppercase tracking-wide text-muted-foreground">
                  <BookOpen className="h-3 w-3" /> Research Areas
                </h4>
                <div className="flex flex-wrap gap-1">
                  {result.matching_research_areas.map((a, i) => (
                    <span key={i} className="text-xs px-2 py-0.5 rounded bg-primary/10 text-primary">{a}</span>
                  ))}
                </div>
              </div>
            )}
            {result.matching_ai_techniques.length > 0 && (
              <div>
                <h4 className="font-medium mb-1 flex items-center gap-1 text-xs uppercase tracking-wide text-muted-foreground">
                  <Zap className="h-3 w-3" /> AI/ML Techniques
                </h4>
                <div className="flex flex-wrap gap-1">
                  {result.matching_ai_techniques.map((t, i) => (
                    <span key={i} className="text-xs px-2 py-0.5 rounded bg-accent/10 text-accent">{t}</span>
                  ))}
                </div>
              </div>
            )}
            {result.matching_skills.length > 0 && (
              <div>
                <h4 className="font-medium mb-1 flex items-center gap-1 text-xs uppercase tracking-wide text-muted-foreground">
                  <Target className="h-3 w-3" /> Skills
                </h4>
                <div className="flex flex-wrap gap-1">
                  {result.matching_skills.map((s, i) => (
                    <span key={i} className="text-xs px-2 py-0.5 rounded bg-muted border border-border">{s}</span>
                  ))}
                </div>
              </div>
            )}
            {result.matching_technologies.length > 0 && (
              <div>
                <h4 className="font-medium mb-1 flex items-center gap-1 text-xs uppercase tracking-wide text-muted-foreground">
                  <TrendingUp className="h-3 w-3" /> Technologies
                </h4>
                <div className="flex flex-wrap gap-1">
                  {result.matching_technologies.map((t, i) => (
                    <span key={i} className="text-xs px-2 py-0.5 rounded bg-muted border border-border uppercase">{t}</span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* All reasons */}
          {result.reasons.length > 0 && (
            <div>
              <h4 className="font-medium mb-1 text-xs uppercase tracking-wide text-muted-foreground">
                Reason for Match
              </h4>
              <ul className="space-y-1">
                {result.reasons.map((r, i) => (
                  <li key={i} className="text-xs text-muted-foreground flex gap-2">
                    <span className="text-primary mt-0.5 shrink-0">•</span>{r}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Missing skills */}
          {result.missing_skills.length > 0 && (
            <div>
              <h4 className="font-medium mb-1 flex items-center gap-1 text-xs uppercase tracking-wide text-destructive/70">
                <AlertCircle className="h-3 w-3" /> Missing Skills
              </h4>
              <div className="flex flex-wrap gap-1">
                {result.missing_skills.map((s, i) => (
                  <span key={i} className="text-xs px-2 py-0.5 rounded bg-destructive/10 text-destructive border border-destructive/20">{s}</span>
                ))}
              </div>
            </div>
          )}

          {/* Contribution / student roles / collab */}
          <div className="grid md:grid-cols-3 gap-4 rounded-lg bg-muted/30 border border-border p-3">
            <div>
              <h4 className="font-medium mb-1 flex items-center gap-1 text-xs text-muted-foreground">
                <Award className="h-3 w-3" /> Professor Contribution
              </h4>
              <p className="text-xs text-foreground/80">{result.professor_contribution}</p>
            </div>
            <div>
              <h4 className="font-medium mb-1 flex items-center gap-1 text-xs text-muted-foreground">
                <Users className="h-3 w-3" /> Student Roles
              </h4>
              <p className="text-xs text-foreground/80">{result.student_roles}</p>
            </div>
            <div>
              <h4 className="font-medium mb-1 flex items-center gap-1 text-xs text-muted-foreground">
                <Building2 className="h-3 w-3" /> Collaboration Potential
              </h4>
              <p className="text-xs text-foreground/80">{result.collaboration_potential}</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Summary stats bar ────────────────────────────────────────────────────────

function SummaryBar({ response }: { response: ProfessorMatchResponse }) {
  const s = response.summary;
  return (
    <div className="card space-y-4">
      <div className="flex items-center gap-2">
        <BarChart3 className="h-4 w-4 text-primary" />
        <h2 className="font-semibold">Match Summary — {response.professor_name}</h2>
      </div>
      <div className="text-sm text-muted-foreground">
        {response.department} · {response.designation}
      </div>
      {response.top_domains.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {response.top_domains.map((d, i) => (
            <span key={i} className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
              {d}
            </span>
          ))}
        </div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: "Avg score",     value: `${s.avg_score}%` },
          { label: "Highest",       value: `${s.highest_score}%` },
          { label: "Recommended",   value: s.recommended_count },
          { label: "Consider",      value: s.consider_count },
        ].map((stat) => (
          <div key={stat.label} className="rounded-lg border border-border bg-card p-3 text-center">
            <div className="text-2xl font-bold text-primary">{stat.value}</div>
            <div className="text-xs text-muted-foreground mt-1">{stat.label}</div>
          </div>
        ))}
      </div>
      {/* Distribution pills */}
      <div className="flex flex-wrap gap-2 text-xs">
        {[
          { label: "Excellent", count: s.distribution.excellent, cls: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" },
          { label: "Strong",    count: s.distribution.strong,    cls: "bg-green-500/15 text-green-400 border-green-500/30" },
          { label: "Moderate",  count: s.distribution.moderate,  cls: "bg-blue-500/15 text-blue-400 border-blue-500/30" },
          { label: "Weak",      count: s.distribution.weak,      cls: "bg-yellow-500/15 text-yellow-500 border-yellow-500/30" },
          { label: "Poor",      count: s.distribution.poor,      cls: "bg-muted text-muted-foreground border-border" },
        ].map((d) => (
          <span key={d.label} className={cn("px-2 py-0.5 rounded-full border", d.cls)}>
            {d.label}: {d.count}
          </span>
        ))}
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function ProfessorMatchPage() {
  const [selectedId, setSelectedId] = useState("");
  const [searchText, setSearchText] = useState("");
  const [sortKey, setSortKey] = useState<"score" | "confidence_score">("score");
  const [filterLevel, setFilterLevel] = useState("all");
  const [response, setResponse] = useState<ProfessorMatchResponse | null>(null);

  // Fetch professor list (up to 200 for selection)
  const { data: profData, isLoading: loadingProfs } = useQuery({
    queryKey: ["professors-list"],
    queryFn: () => getProfessors(undefined, 200),
  });

  const filteredProfs = useMemo(() => {
    if (!profData?.professors) return [];
    const q = searchText.toLowerCase();
    return profData.professors.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.department.toLowerCase().includes(q),
    );
  }, [profData, searchText]);

  const mutation = useMutation({
    mutationFn: () => runProfessorMatch(selectedId),
    onSuccess: (data) => setResponse(data),
  });

  const displayResults = useMemo(() => {
    if (!response) return [];
    let res = [...response.results];
    if (filterLevel !== "all") {
      res = res.filter((r) => r.match_level === filterLevel);
    }
    res.sort((a, b) => {
      const av = (a as any)[sortKey] ?? 0;
      const bv = (b as any)[sortKey] ?? 0;
      return bv - av;
    });
    return res;
  }, [response, sortKey, filterLevel]);

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Professor → Company Match</h1>
        <p className="text-muted-foreground">
          Select a professor and discover which company projects best align with
          their research profile, skills, and publication record.
          <span className="ml-2 text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full border border-primary/20">
            Engine 2 · 5-layer scoring
          </span>
        </p>
      </div>

      {/* Professor selector */}
      <div className="card space-y-3">
        <h2 className="font-semibold flex items-center gap-2">
          <Search className="h-4 w-4 text-primary" /> Select Professor
        </h2>

        <input
          className="input"
          placeholder="Search by name or department…"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
        />

        {loadingProfs ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading professors…
          </div>
        ) : (
          <div className="max-h-52 overflow-y-auto rounded-lg border border-border divide-y divide-border">
            {filteredProfs.slice(0, 80).map((p) => (
              <button
                key={p.professor_id}
                onClick={() => {
                  setSelectedId(p.professor_id);
                  setResponse(null);
                }}
                className={cn(
                  "w-full text-left px-3 py-2 text-sm transition hover:bg-muted",
                  selectedId === p.professor_id && "bg-primary/10 text-primary",
                )}
              >
                <span className="font-medium">{p.name}</span>
                <span className="ml-2 text-xs text-muted-foreground">
                  {p.department.replace("Department of ", "")}
                </span>
              </button>
            ))}
            {filteredProfs.length === 0 && (
              <div className="px-3 py-4 text-sm text-muted-foreground text-center">
                No professors match your search
              </div>
            )}
          </div>
        )}

        <div className="flex gap-3 items-center">
          <button
            className="btn-primary"
            disabled={!selectedId || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Search className="h-4 w-4 mr-2" />
            )}
            Run Match (100 companies)
          </button>
          {selectedId && (
            <span className="text-sm text-muted-foreground">
              Selected:{" "}
              <span className="text-foreground font-medium">
                {profData?.professors.find((p) => p.professor_id === selectedId)?.name}
              </span>
            </span>
          )}
        </div>

        {mutation.isError && (
          <div className="text-sm text-destructive flex items-center gap-2">
            <AlertCircle className="h-4 w-4" />
            {(mutation.error as any)?.message || "Match failed — ensure backend is running on port 8001"}
          </div>
        )}
      </div>

      {/* Results */}
      {response && (
        <div className="space-y-4">
          {/* Summary bar */}
          <SummaryBar response={response} />

          {/* Filter / sort controls */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-sm text-muted-foreground">
              Showing {displayResults.length} of {response.results.length} projects
            </span>
            <select
              className="input max-w-[180px]"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as any)}
            >
              <option value="score">Sort by Score</option>
              <option value="confidence_score">Sort by Confidence</option>
            </select>
            <select
              className="input max-w-[180px]"
              value={filterLevel}
              onChange={(e) => setFilterLevel(e.target.value)}
            >
              <option value="all">All levels</option>
              <option value="Excellent">Excellent only</option>
              <option value="Strong">Strong only</option>
              <option value="Moderate">Moderate only</option>
              <option value="Weak">Weak only</option>
              <option value="Poor">Poor only</option>
            </select>
          </div>

          {/* Cards */}
          <div className="grid gap-4">
            {displayResults.map((r) => (
              <CompanyMatchCard key={`${r.company_name}-${r.rank}`} result={r} />
            ))}
            {displayResults.length === 0 && (
              <div className="card text-center text-muted-foreground py-8">
                No projects match the selected filter.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
