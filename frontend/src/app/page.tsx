import { Suspense } from "react";
import { DashboardStats } from "@/components/dashboard/DashboardStats";
import { RecentWorkflows } from "@/components/dashboard/RecentWorkflows";
import { AgentHealthSummary } from "@/components/dashboard/AgentHealthSummary";
import Link from "next/link";

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-massclaw-text-muted mt-1">MassClaw System Overview</p>
        </div>
        <Link
          href="/workflows/new"
          className="px-4 py-2 bg-massclaw-accent text-white text-sm rounded-lg hover:bg-massclaw-accent-light transition"
        >
          New Workflow
        </Link>
      </div>

      <Suspense fallback={<div className="h-24 bg-massclaw-surface animate-pulse rounded-xl" />}>
        <DashboardStats />
      </Suspense>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Suspense fallback={<div className="h-64 bg-massclaw-surface animate-pulse rounded-xl" />}>
          <RecentWorkflows />
        </Suspense>
        <Suspense fallback={<div className="h-64 bg-massclaw-surface animate-pulse rounded-xl" />}>
          <AgentHealthSummary />
        </Suspense>
      </div>
    </div>
  );
}
