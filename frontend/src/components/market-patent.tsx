"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, Send, Sparkles } from "lucide-react";
import {
  getPatentAudienceCategories, sendPatentOffer, logMatchInteraction,
  type MatchResult, type AudienceTargetType,
} from "@/lib/api";
import { cn, scoreColor } from "@/lib/utils";

const CATEGORY_ORDER: AudienceTargetType[] = ["company", "employee", "student", "professor", "institute"];

const CONFIDENCE_STYLE: Record<string, string> = {
  high: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  medium: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  low: "bg-muted text-muted-foreground border-transparent",
};

/** Professor Dashboard: "sell/license this patent" panel. Runs Matching
 * Engine 5 once across all five audience types and shows categorized AI
 * recommendations (Best Matching Companies/Employees/Students/Professors/
 * Academic Institutes), each with a hybrid semantic+keyword score,
 * confidence, shared expertise, and a suggested next action. */
export function MarketPatentPanel({
  patentId,
  patentTitle,
  professorId,
  professorName,
}: {
  patentId: string;
  patentTitle: string;
  professorId: string;
  professorName: string;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["ai-recommendations", patentId],
    queryFn: () => getPatentAudienceCategories(patentId, 5),
  });

  return (
    <div className="card space-y-4">
      <div>
        <h3 className="font-semibold flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> AI Matching Engine — Collaboration & Buyer Recommendations
        </h3>
        <p className="text-xs text-muted-foreground mt-1">
          Semantic + keyword hybrid scoring across every registered audience type for: {patentTitle}
          {data && !data.embeddings_ready && " (semantic model unavailable — keyword-only fallback)"}
        </p>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <Loader2 className="h-4 w-4 animate-spin" /> Scoring companies, employees, students, professors, and institutes…
        </div>
      ) : error ? (
        <div className="text-xs text-destructive">{(error as Error).message}</div>
      ) : (
        <div className="space-y-5">
          {CATEGORY_ORDER.map((type) => {
            const cat = data?.categories?.[type];
            if (!cat) return null;
            return (
              <div key={type} className="space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-sm font-semibold">{cat.label}</h4>
                  <span className="text-[11px] text-muted-foreground">{cat.count} match{cat.count === 1 ? "" : "es"}</span>
                </div>
                {cat.matches.length === 0 ? (
                  <div className="text-xs text-muted-foreground">
                    No matches yet.{type !== "company" && " Profiles need to be registered before they can be matched."}
                  </div>
                ) : (
                  <div className="space-y-2">
                    {cat.matches.map((m) => (
                      <AudienceCandidateRow
                        key={m.target_id}
                        match={m}
                        patentId={patentId}
                        professorId={professorId}
                        professorName={professorName}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AudienceCandidateRow({
  match, patentId, professorId, professorName,
}: {
  match: MatchResult;
  patentId: string;
  professorId: string;
  professorName: string;
}) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const targetType = match.target_kind as AudienceTargetType;

  // Fire-and-forget view logging, once per rendered candidate.
  useEffect(() => {
    logMatchInteraction({
      source_kind: "patent", source_id: patentId, target_kind: targetType, target_id: match.target_id,
      interaction_type: "view", match_score: match.score,
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patentId, targetType, match.target_id]);

  const mutation = useMutation({
    mutationFn: async () => {
      const res = await sendPatentOffer(patentId, {
        professor_id: professorId,
        target_type: targetType,
        target_id: match.target_id,
        target_name: match.target_name,
        message,
        match_score: match.score,
        score_breakdown: { matching_domains: match.matching_domains, matching_keywords: match.matching_keywords },
        reasons: match.reasons,
      });
      await logMatchInteraction({
        source_kind: "patent", source_id: patentId, target_kind: targetType, target_id: match.target_id,
        interaction_type: "offer_sent", match_score: match.score,
      }).catch(() => {});
      return res;
    },
    onSuccess: () => setOpen(false),
  });

  return (
    <div className="border border-border rounded-lg p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium">{match.target_name}</div>
          <div className="text-[11px] text-muted-foreground">{match.tag}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className={cn("text-sm font-mono font-bold", scoreColor(match.score))}>
            {Math.round(match.score)}
          </div>
          <span className={cn("px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wide", CONFIDENCE_STYLE[match.confidence])}>
            {match.confidence}
          </span>
        </div>
      </div>

      <div className="flex flex-wrap gap-1 text-[10px] text-muted-foreground">
        <span className="px-1.5 py-0.5 rounded bg-muted">semantic {Math.round(match.semantic_score)}</span>
        <span className="px-1.5 py-0.5 rounded bg-muted">keyword {Math.round(match.keyword_score)}</span>
      </div>

      {match.shared_expertise.length > 0 && (
        <div className="flex flex-wrap gap-1 text-[11px]">
          {match.shared_expertise.map((s) => (
            <span key={s} className="px-1.5 py-0.5 rounded bg-primary/10 text-primary">{s}</span>
          ))}
        </div>
      )}

      {match.reasons.length > 0 && (
        <ul className="text-xs text-muted-foreground list-disc list-inside space-y-0.5">
          {match.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
        </ul>
      )}

      {match.collaboration_opportunity && (
        <p className="text-xs text-muted-foreground italic">{match.collaboration_opportunity}</p>
      )}

      {mutation.isSuccess ? (
        <div className="text-xs text-emerald-400">Offer sent ✓</div>
      ) : open ? (
        <div className="space-y-2">
          <textarea
            className="input w-full text-sm"
            rows={2}
            placeholder={`Message to ${match.target_name}…`}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              className="btn-primary text-xs"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin mr-1 inline" />
              ) : (
                <Send className="h-3 w-3 mr-1 inline" />
              )}
              {match.next_action}
            </button>
            <button
              className="text-xs text-muted-foreground hover:text-foreground"
              onClick={() => setOpen(false)}
            >
              Cancel
            </button>
          </div>
          {mutation.isError && (
            <div className="text-xs text-destructive">
              {(mutation.error as Error)?.message || "Could not send offer."}
            </div>
          )}
        </div>
      ) : (
        <button className="btn-primary text-xs" onClick={() => setOpen(true)}>
          {match.next_action}
        </button>
      )}
    </div>
  );
}
