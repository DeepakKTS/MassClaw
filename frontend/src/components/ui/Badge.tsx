import { cn } from "@/lib/utils";

const variants: Record<string, string> = {
  default: "bg-massclaw-border text-massclaw-text-muted",
  success: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  warning: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  danger: "bg-red-500/10 text-red-400 border-red-500/20",
  info: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20",
  // Status-specific
  completed: "bg-emerald-500/15 text-emerald-400",
  running: "bg-indigo-500/15 text-indigo-400",
  failed: "bg-red-500/15 text-red-400",
  pending: "bg-gray-500/15 text-gray-400",
  blocked: "bg-amber-500/15 text-amber-400",
  skipped: "bg-gray-500/10 text-gray-500",
  todo: "bg-blue-500/10 text-blue-400",
  active: "bg-emerald-500/15 text-emerald-400",
  degraded: "bg-amber-500/15 text-amber-400",
  suspended: "bg-red-500/15 text-red-400",
  inactive: "bg-gray-500/10 text-gray-500",
};

export function Badge({ children, variant = "default", className }: { children: React.ReactNode; variant?: string; className?: string }) {
  return (
    <span className={cn("inline-flex items-center px-2 py-0.5 text-[11px] font-semibold rounded-full uppercase tracking-wide border border-transparent", variants[variant] || variants.default, className)}>
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge variant={status}>{status.replace(/_/g, " ")}</Badge>;
}
