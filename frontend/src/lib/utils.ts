import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function scoreColor(score: number): string {
  if (score >= 80) return "text-success";
  if (score >= 60) return "text-primary";
  if (score >= 40) return "text-warning";
  return "text-destructive";
}

export function bandColor(band?: string): string {
  switch ((band || "").toLowerCase()) {
    case "strong":
    case "very high":
      return "bg-success/10 text-success border-success/30";
    case "good":
    case "high":
    case "moderate":
      return "bg-primary/10 text-primary border-primary/30";
    case "exploratory":
      return "bg-warning/10 text-warning border-warning/30";
    case "risky":
    case "low":
      return "bg-destructive/10 text-destructive border-destructive/30";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}
