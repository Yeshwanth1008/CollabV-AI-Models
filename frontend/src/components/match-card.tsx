"use client";

import { useState } from "react";
import { Check, ChevronDown, ChevronUp, FileText, ThumbsDown, ThumbsUp } from "lucide-react";
import type { MatchResult } from "@/lib/api";
import { submitFeedback } from "@/lib/api";
import { ScoreBar } from "./score-bar";
import { bandColor, cn, scoreColor } from "@/lib/utils";

export function MatchCard({
  match, matchId,
}: { match: MatchResult; matchId: string }) {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  async function record(action: "accept" | "reject") {
    if (feedback) return;
    try {
      await submitFeedback({
        match_id: matchId,
        professor_id: match.professor_id,
        action,
      });
      setFeedback(action);
    } catch (e) {
      console.error(e);
    }
  }

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-semibold truncate">{match.professor_name}</h3>
          <p className="text-sm text-muted-foreground">{match.department}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {match.deal_band && (
              <span className={cn("text-xs px-2 py-0.5 rounded-full border", bandColor(match.deal_band))}>
                Deal: {match.deal_band} {match.deal_probability != null && `(${match.deal_probability}%)`}
              </span>
            )}
            <span className={cn("text-xs px-2 py-0.5 rounded-full border",
              match.readiness_score >= 70 ? "bg-success/10 text-success border-success/30" :
              match.readiness_score >= 50 ? "bg-primary/10 text-primary border-primary/30" :
              "bg-muted text-muted-foreground border-border")}>
              Readiness: {match.readiness_score.toFixed(0)}
            </span>
            {(match.patent_score ?? 0) > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full border bg-muted text-muted-foreground border-border">
                Patents: {match.patent_score.toFixed(0)}
              </span>
            )}
            {(match.innovation_score ?? 0) >= 70 && (
              <span
                title={match.innovation_bridges?.slice(0, 3).join(" • ") || "Cross-domain innovator"}
                className="text-xs px-2 py-0.5 rounded-full border bg-accent/10 text-accent border-accent/40 inline-flex items-center gap-1"
              >
                <span aria-hidden>✦</span>
                Innovation: {match.innovation_score!.toFixed(0)}
                {match.innovation_bridges?.[0] && (
                  <span className="text-muted-foreground/80 hidden sm:inline">
                    {" "}· {match.innovation_bridges[0]}
                  </span>
                )}
              </span>
            )}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className={cn("text-4xl font-mono font-bold", scoreColor(match.score))}>
            {match.score.toFixed(0)}
          </div>
          <div className="text-xs text-muted-foreground">/ 100</div>
        </div>
      </div>

      {match.explanation && (
        <p className="mt-4 text-sm text-foreground/90 leading-relaxed">
          {match.explanation.summary}
        </p>
      )}

      <div className="mt-4 grid grid-cols-2 md:grid-cols-3 gap-3">
        <ScoreBar label="Research" value={match.tier1_score} />
        <ScoreBar label="Semantic" value={match.tier2_score} />
        <ScoreBar label="Innovation" value={match.innovation_score ?? 0} />
        <ScoreBar label="Patent" value={match.patent_score} />
        <ScoreBar label="Readiness" value={match.readiness_score} />
        <ScoreBar label="Contextual" value={match.contextual_readiness} />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          onClick={() => record("accept")}
          disabled={!!feedback}
          className={cn(
            "btn-ghost text-xs gap-1",
            feedback === "accept" && "bg-success/10 text-success border-success/30",
          )}
        >
          {feedback === "accept" ? <Check className="h-3 w-3" /> : <ThumbsUp className="h-3 w-3" />}
          Accept
        </button>
        <button
          onClick={() => record("reject")}
          disabled={!!feedback}
          className={cn(
            "btn-ghost text-xs gap-1",
            feedback === "reject" && "bg-destructive/10 text-destructive border-destructive/30",
          )}
        >
          <ThumbsDown className="h-3 w-3" /> Not Interested
        </button>
        <button onClick={() => setExpanded((x) => !x)} className="btn-ghost text-xs gap-1 ml-auto">
          {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          {expanded ? "Hide details" : "Details"}
        </button>
      </div>

      {expanded && (
        <div className="mt-4 space-y-3 border-t border-border pt-4 text-sm">
          {match.reasons?.length > 0 && (
            <div>
              <h4 className="font-medium mb-1">Why this match</h4>
              <ul className="text-muted-foreground space-y-1 list-disc list-inside">
                {match.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          {match.explanation?.key_strengths?.length ? (
            <div>
              <h4 className="font-medium mb-1">Strengths</h4>
              <ul className="text-muted-foreground space-y-1 list-disc list-inside">
                {match.explanation.key_strengths.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          ) : null}
          {match.explanation?.potential_gaps?.length ? (
            <div>
              <h4 className="font-medium mb-1">Gaps</h4>
              <ul className="text-muted-foreground space-y-1 list-disc list-inside">
                {match.explanation.potential_gaps.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          ) : null}
          {match.explanation?.suggested_talking_points?.length ? (
            <div>
              <h4 className="font-medium mb-1">Talking points</h4>
              <ul className="text-muted-foreground space-y-1 list-disc list-inside">
                {match.explanation.suggested_talking_points.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          ) : null}
          {match.deal_assessment?.risk_factors?.length ? (
            <div>
              <h4 className="font-medium mb-1 flex items-center gap-1">
                <FileText className="h-3 w-3" /> Risk factors
              </h4>
              <div className="space-y-2">
                {match.deal_assessment.risk_factors.map((r, i) => (
                  <div key={i} className="rounded border border-border bg-muted/30 p-2">
                    <div className="text-xs text-muted-foreground">{r.category} · {r.severity}</div>
                    <div className="text-sm">{r.description}</div>
                    {r.mitigation && (
                      <div className="text-xs mt-1 text-accent">Mitigation: {r.mitigation}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {match.deal_assessment?.recommended_actions?.length ? (
            <div>
              <h4 className="font-medium mb-1">Recommended actions</h4>
              <ul className="text-muted-foreground space-y-1 list-disc list-inside">
                {match.deal_assessment.recommended_actions.map((a, i) => <li key={i}>{a}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
