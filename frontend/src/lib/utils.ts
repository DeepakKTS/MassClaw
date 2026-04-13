import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) { return twMerge(clsx(inputs)); }

export function formatNumber(n: number, decimals = 2): string {
  return n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

export function formatCredits(credits: number): string {
  return `${formatNumber(credits, 1)} credits`;
}

export function truncate(str: string, max: number): string {
  return str.length > max ? str.slice(0, max) + "..." : str;
}

export const STATUS_COLORS: Record<string, string> = {
  active: "text-massclaw-success",
  completed: "text-massclaw-success",
  running: "text-massclaw-accent",
  in_progress: "text-massclaw-accent",
  pending: "text-massclaw-text-muted",
  degraded: "text-massclaw-warning",
  failed: "text-massclaw-danger",
  suspended: "text-massclaw-danger",
  inactive: "text-massclaw-text-muted",
  cancelled: "text-massclaw-text-muted",
  skipped: "text-massclaw-text-muted",
};

export function getStatusColor(status: string): string {
  return STATUS_COLORS[status] || "text-massclaw-text-muted";
}
