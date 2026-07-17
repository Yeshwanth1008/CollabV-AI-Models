"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";
import { runMatch, type MatchRunResponse } from "@/lib/api";
import { MatchCard } from "@/components/match-card";

export default function MatchPage() {
  const [text, setText] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [sortKey, setSortKey] = useState<"score" | "deal_probability" | "readiness_score">("score");
  const [minScore, setMinScore] = useState(0);
  const [response, setResponse] = useState<MatchRunResponse | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      runMatch({
        raw_text: text,
        company_name: companyName || "Anonymous Company",
        top_k: 12,
        include_deal_score: true,
        include_explanations: true,
        explain_top_k: 5,
      }),
    onSuccess: (data) => setResponse(data),
  });

  const filtered = (response?.results ?? [])
    .filter((m) => m.score >= minScore)
    .sort((a, b) => {
      const av = (a as any)[sortKey] ?? 0;
      const bv = (b as any)[sortKey] ?? 0;
      return bv - av;
    });

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <h1 className="text-3xl font-bold">Find your expert</h1>
        <p className="text-muted-foreground">
          Describe your R&amp;D need below. CollabV AI will parse, search, and rank
          professors with deal-success scoring.
        </p>
      </div>

      <div className="card space-y-3">
        <input
          className="input"
          placeholder="Company name (optional)"
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
        />
        <textarea
          className="input min-h-[180px] font-mono"
          placeholder="e.g. We need an ML model to detect manufacturing defects on a production line in real time. Limited labeled data..."
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="flex gap-3 items-center">
          <button
            className="btn-primary"
            disabled={!text.trim() || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Search className="h-4 w-4 mr-2" />
            )}
            Find matches
          </button>
          <div className="text-xs text-muted-foreground">
            Powered by 6 scoring models · LLM explanations on top 5
          </div>
        </div>
        {mutation.isError && (
          <div className="text-sm text-destructive">
            {(mutation.error as any)?.message || "Match failed"}
          </div>
        )}
      </div>

      {response && (
        <div className="space-y-4">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-sm text-muted-foreground">
              {filtered.length} of {response.results.length} matches
            </span>
            <select
              className="input max-w-xs"
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as any)}
            >
              <option value="score">Sort by Match Score</option>
              <option value="deal_probability">Sort by Deal Probability</option>
              <option value="readiness_score">Sort by Readiness</option>
            </select>
            <label className="text-xs text-muted-foreground flex items-center gap-2">
              Min score:
              <input
                type="range" min={0} max={100} value={minScore}
                onChange={(e) => setMinScore(parseInt(e.target.value))}
              />
              <span className="font-mono">{minScore}</span>
            </label>
          </div>

          <div className="grid gap-4">
            {filtered.map((m) => (
              <MatchCard key={m.professor_id} match={m} matchId={response.match_id} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
