"use client";

import { useState } from "react";
import { usePendingApprovals, useApproveRequest, useDenyRequest } from "@/hooks/useApprovals";
import { UserCheck, Clock, CheckCircle, XCircle, AlertCircle } from "lucide-react";

function statusBadge(status: string) {
  switch (status) {
    case "pending":
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-yellow-500/15 text-yellow-400 border border-yellow-500/20">
          <Clock className="w-3 h-3" /> Pending
        </span>
      );
    case "approved":
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">
          <CheckCircle className="w-3 h-3" /> Approved
        </span>
      );
    case "denied":
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-red-500/15 text-red-400 border border-red-500/20">
          <XCircle className="w-3 h-3" /> Denied
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-white/10 text-massclaw-text-muted border border-white/10">
          <AlertCircle className="w-3 h-3" /> {status}
        </span>
      );
  }
}

function ApprovalCard({ request }: { request: any }) {
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState<"approve" | "deny" | null>(null);
  const approve = useApproveRequest();
  const deny = useDenyRequest();

  const handleApprove = async () => {
    if (confirming !== "approve") {
      setConfirming("approve");
      return;
    }
    await approve.mutateAsync({ id: request.request_id, reason, decided_by: "human" });
    setConfirming(null);
    setReason("");
  };

  const handleDeny = async () => {
    if (confirming !== "deny") {
      setConfirming("deny");
      return;
    }
    await deny.mutateAsync({ id: request.request_id, reason, decided_by: "human" });
    setConfirming(null);
    setReason("");
  };

  const capability = request.context?.capability || request.action;
  const outputPreview = request.context?.output_preview;
  const confidence = request.context?.confidence;
  const agentName = request.context?.agent;
  const isPending = request.status === "pending";

  return (
    <div className="glass rounded-xl p-5 border border-massclaw-border space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {statusBadge(request.status)}
            <span className="text-[10px] text-massclaw-text-muted font-mono">
              {request.request_id.slice(0, 8)}…
            </span>
          </div>
          <h3 className="text-sm font-semibold text-massclaw-text mt-1.5 truncate">
            {request.action.replace(/_/g, " ")}
          </h3>
          {agentName && (
            <p className="text-xs text-massclaw-text-muted mt-0.5">Agent: {agentName}</p>
          )}
        </div>
        <div className="text-right shrink-0">
          <p className="text-[10px] text-massclaw-text-muted">
            {new Date(request.requested_at).toLocaleTimeString()}
          </p>
          <p className="text-[10px] text-massclaw-text-muted">
            {new Date(request.requested_at).toLocaleDateString()}
          </p>
        </div>
      </div>

      {/* Details */}
      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="glass rounded-lg p-3 space-y-1">
          <p className="text-massclaw-text-muted text-[10px] uppercase tracking-wider">Capability</p>
          <p className="text-massclaw-text font-mono">{capability}</p>
        </div>
        <div className="glass rounded-lg p-3 space-y-1">
          <p className="text-massclaw-text-muted text-[10px] uppercase tracking-wider">Policy Rule</p>
          <p className="text-massclaw-text font-mono">{request.policy_rule}</p>
        </div>
        {confidence !== undefined && confidence !== null && (
          <div className="glass rounded-lg p-3 space-y-1">
            <p className="text-massclaw-text-muted text-[10px] uppercase tracking-wider">Confidence</p>
            <p className={`font-mono font-semibold ${confidence < 0.4 ? "text-red-400" : "text-emerald-400"}`}>
              {(confidence * 100).toFixed(1)}%
            </p>
          </div>
        )}
        {request.expires_at && (
          <div className="glass rounded-lg p-3 space-y-1">
            <p className="text-massclaw-text-muted text-[10px] uppercase tracking-wider">Expires</p>
            <p className="text-massclaw-text font-mono">
              {new Date(request.expires_at).toLocaleTimeString()}
            </p>
          </div>
        )}
      </div>

      {/* Checkpoint hash — the primitive that lets any federation peer
          resume this workflow if the originator dies. Click-to-copy
          so operators can use it against POST /workflows/{id}/resume. */}
      {request.checkpoint_hash && (
        <div className="glass rounded-lg p-3 space-y-1">
          <p className="text-massclaw-text-muted text-[10px] uppercase tracking-wider">
            Resume Pointer (checkpoint hash)
          </p>
          <button
            onClick={() => navigator.clipboard?.writeText(request.checkpoint_hash)}
            title="Copy full hash"
            className="w-full text-left font-mono text-[11px] text-accent-300 hover:text-accent-200 truncate"
          >
            {request.checkpoint_hash}
          </button>
        </div>
      )}

      {/* Output preview */}
      {outputPreview && (
        <div className="glass rounded-lg p-3">
          <p className="text-massclaw-text-muted text-[10px] uppercase tracking-wider mb-2">Output Preview</p>
          <p className="text-xs text-massclaw-text font-mono whitespace-pre-wrap line-clamp-4 leading-relaxed">
            {outputPreview}
          </p>
        </div>
      )}

      {/* Decision area — only for pending */}
      {isPending && (
        <div className="space-y-3 pt-1">
          {confirming && (
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={`Reason for ${confirming} (optional)…`}
              className="w-full bg-white/5 border border-massclaw-border rounded-lg px-3 py-2 text-xs text-massclaw-text placeholder-massclaw-text-muted focus:outline-none focus:border-white/20 resize-none"
              rows={2}
            />
          )}
          <div className="flex gap-2">
            <button
              onClick={handleApprove}
              disabled={approve.isPending}
              className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all
                ${confirming === "approve"
                  ? "bg-emerald-500 text-white hover:bg-emerald-400"
                  : "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25"
                }`}
            >
              <CheckCircle className="w-3.5 h-3.5" />
              {confirming === "approve" ? "Confirm Approve" : "Approve"}
            </button>
            <button
              onClick={handleDeny}
              disabled={deny.isPending}
              className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all
                ${confirming === "deny"
                  ? "bg-red-500 text-white hover:bg-red-400"
                  : "bg-red-500/15 text-red-400 border border-red-500/30 hover:bg-red-500/25"
                }`}
            >
              <XCircle className="w-3.5 h-3.5" />
              {confirming === "deny" ? "Confirm Deny" : "Deny"}
            </button>
            {confirming && (
              <button
                onClick={() => { setConfirming(null); setReason(""); }}
                className="px-3 py-2 rounded-lg text-xs font-medium bg-white/5 text-massclaw-text-muted border border-massclaw-border hover:bg-white/10 transition-all"
              >
                Cancel
              </button>
            )}
          </div>
        </div>
      )}

      {/* Decided info */}
      {!isPending && request.decided_by && (
        <div className="pt-1 border-t border-white/[0.06]">
          <p className="text-[10px] text-massclaw-text-muted">
            Decided by <span className="text-massclaw-text">{request.decided_by}</span>
            {request.decided_at && (
              <> at {new Date(request.decided_at).toLocaleString()}</>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

export default function ApprovalsPage() {
  const { data, isLoading, error } = usePendingApprovals();
  const approvals: any[] = data ?? [];

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-yellow-500/15 border border-yellow-500/20 flex items-center justify-center">
          <UserCheck className="w-4 h-4 text-yellow-400" />
        </div>
        <div>
          <h1 className="text-heading">Human Approvals</h1>
          <p className="text-massclaw-text-muted mt-0.5 text-sm">
            Review and approve high-risk agent actions in real time
          </p>
        </div>
        <div className="ml-auto">
          <span className="text-[10px] text-massclaw-text-muted bg-white/5 border border-massclaw-border px-2 py-1 rounded-full">
            Polling every 3s
          </span>
        </div>
      </div>

      {/* Error state */}
      {error && (
        <div className="glass rounded-xl p-4 border border-massclaw-danger/20 text-massclaw-danger text-sm">
          Failed to load approvals: {(error as Error).message}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="glass rounded-xl p-8 text-center">
          <p className="text-massclaw-text-muted text-sm">Loading approvals…</p>
        </div>
      )}

      {/* Empty state */}
      {!isLoading && approvals.length === 0 && !error && (
        <div className="glass rounded-xl p-12 text-center border border-massclaw-border">
          <div className="w-12 h-12 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mx-auto mb-4">
            <CheckCircle className="w-6 h-6 text-emerald-400" />
          </div>
          <h3 className="text-sm font-semibold text-massclaw-text mb-1">All clear</h3>
          <p className="text-xs text-massclaw-text-muted">
            No pending approvals. The system will alert you here when high-risk tasks require review.
          </p>
        </div>
      )}

      {/* Approval cards */}
      {approvals.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-yellow-400 animate-pulse" />
            <span className="text-xs text-massclaw-text-muted">
              {approvals.length} pending approval{approvals.length !== 1 ? "s" : ""}
            </span>
          </div>
          {approvals.map((approval) => (
            <ApprovalCard key={approval.request_id} request={approval} />
          ))}
        </div>
      )}
    </div>
  );
}
