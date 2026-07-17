"use client";

import { cn, scoreColor } from "@/lib/utils";
import type { SmartMatch } from "@/lib/api";

/** Renders one "Patent Smart Matches" result. Used by both the Professor
 * Dashboard (Engine 3: shows a matched problem statement) and the Company
 * Dashboard (Engine 4: shows a matched patent). */
export function SmartMatchCard({
  match,
  href,
  title,
  subtitle,
  tag,
}: {
  match: SmartMatch;
  href?: string;
  title: string;
  subtitle?: string;
  tag?: string;
}) {
  const body = (
    <div className="card hover:border-primary/50 transition flex items-start gap-4">
      <div className="shrink-0 w-14 h-14 rounded-lg bg-primary/10 border border-primary/30 flex flex-col items-center justify-center">
        <div className={cn("text-lg font-bold", scoreColor(match.match_score))}>
          {Math.round(match.match_score)}
        </div>
        <div className="text-[10px] text-muted-foreground">score</div>
      </div>
      <div className="flex-1 space-y-1 min-w-0">
        <div className="flex items-start justify-between gap-2">
          <div className="font-medium leading-snug">{title}</div>
          {tag && (
            <span className="shrink-0 px-1.5 py-0.5 rounded bg-muted text-[11px] text-muted-foreground">
              {tag}
            </span>
          )}
        </div>
        {subtitle && (
          <div className="text-xs text-muted-foreground">{subtitle}</div>
        )}
        {(match.score_breakdown?.matching_domains?.length ||
          match.score_breakdown?.matching_keywords?.length) && (
          <div className="flex flex-wrap gap-1 mt-1 text-[11px]">
            {(match.score_breakdown.matching_domains || []).slice(0, 3).map((d) => (
              <span key={d} className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
                {d}
              </span>
            ))}
          </div>
        )}
        {match.reasons && match.reasons.length > 0 && (
          <ul className="text-xs text-muted-foreground list-disc list-inside mt-2 space-y-0.5">
            {match.reasons.slice(0, 3).map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );

  if (!href) return body;
  return (
    <a href={href} className="block">
      {body}
    </a>
  );
}
