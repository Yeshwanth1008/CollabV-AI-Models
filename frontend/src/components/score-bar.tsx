"use client";

import { cn, scoreColor } from "@/lib/utils";

export function ScoreBar({
  label, value, max = 100,
}: { label: string; value: number; max?: number }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100));
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span className={cn("font-mono", scoreColor(value))}>{value.toFixed(0)}</span>
      </div>
      <div className="h-1.5 rounded bg-muted overflow-hidden">
        <div
          className="h-full bg-gradient-to-r from-primary to-accent transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
