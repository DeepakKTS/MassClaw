"use client";

import { Wallet } from "lucide-react";

export default function WalletPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Wallet & Cost Tracking</h1>
        <p className="text-massclaw-text-muted mt-1">Budget utilization and transaction history across workflows</p>
      </div>
      <div className="bg-massclaw-surface border border-massclaw-border rounded-lg p-8 text-center">
        <Wallet size={48} className="mx-auto text-massclaw-accent mb-4" />
        <p className="text-massclaw-text-muted">Select a workflow to view its wallet details.</p>
        <p className="text-xs text-massclaw-text-muted mt-2">Wallet is per-workflow. View wallet data from the workflow detail page.</p>
      </div>
    </div>
  );
}
