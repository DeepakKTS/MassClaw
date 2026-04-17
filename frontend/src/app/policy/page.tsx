"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ShieldCheck, ShieldOff, ShieldAlert, Play } from "lucide-react";
import type {
  RegistryRule,
  RegistryDecision,
  RegistryEvaluateRequest,
} from "@/types/policy";
import { useState } from "react";

const SAMPLE_PAYLOAD: RegistryEvaluateRequest = {
  agent_did: "did:key:z6MkExample",
  agent_trust_score: 0.75,
  agent_capabilities: ["research", "writing"],
  action: "execute_tool",
  action_category: "payment",
  tool_name: "pay_vendor",
  amount: 42,
  estimated_cost: 3.2,
  extra: {},
};

function actionBadge(action: string) {
  if (action === "deny")
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wider font-semibold bg-red-500/10 text-red-400">
        deny
      </span>
    );
  if (action === "escalate_human")
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wider font-semibold bg-amber-500/10 text-amber-400">
        escalate
      </span>
    );
  if (action === "allow")
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wider font-semibold bg-emerald-500/10 text-emerald-400">
        allow
      </span>
    );
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full uppercase tracking-wider font-semibold bg-white/5 text-massclaw-text-muted">
      abstain
    </span>
  );
}

function actionIcon(action: string) {
  if (action === "deny") return <ShieldOff size={16} className="text-red-400" />;
  if (action === "escalate_human") return <ShieldAlert size={16} className="text-amber-400" />;
  if (action === "allow") return <ShieldCheck size={16} className="text-emerald-400" />;
  return <ShieldCheck size={16} className="text-massclaw-text-muted" />;
}

export default function PolicyPage() {
  const qc = useQueryClient();
  const rulesQuery = useQuery({
    queryKey: ["policy-registry-rules"],
    queryFn: () => api.get<RegistryRule[]>("/policy/registry/rules"),
    refetchInterval: 10000,
  });

  const toggleMutation = useMutation({
    mutationFn: async ({ rule_id, enabled }: { rule_id: string; enabled: boolean }) =>
      api.post<{ rule_id: string; enabled: boolean }>(
        `/policy/registry/rules/${rule_id}/${enabled ? "enable" : "disable"}`,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["policy-registry-rules"] }),
  });

  const [payload, setPayload] = useState<string>(() => JSON.stringify(SAMPLE_PAYLOAD, null, 2));
  const [decision, setDecision] = useState<RegistryDecision | null>(null);
  const [evalError, setEvalError] = useState<string | null>(null);

  const evalMutation = useMutation({
    mutationFn: async (body: RegistryEvaluateRequest) =>
      api.post<RegistryDecision>("/policy/registry/evaluate", body),
    onSuccess: (data) => {
      setDecision(data);
      setEvalError(null);
    },
    onError: (err: Error) => setEvalError(err.message),
  });

  const onEvaluate = () => {
    try {
      const parsed = JSON.parse(payload) as RegistryEvaluateRequest;
      evalMutation.mutate(parsed);
    } catch (e) {
      setEvalError(`invalid JSON: ${(e as Error).message}`);
    }
  };

  const rules = rulesQuery.data ?? [];

  return (
    <div className="max-w-5xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading">Policy Rules</h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">
          Registered @policy_rule entries. Toggle live — changes take effect on the next task.
        </p>
      </div>

      {rulesQuery.error && (
        <div className="glass rounded-xl p-4 border border-massclaw-danger/20 text-massclaw-danger text-sm">
          Failed to load rules: {rulesQuery.error.message}
        </div>
      )}

      <div className="space-y-2">
        {rulesQuery.isLoading ? (
          <div className="text-massclaw-text-muted text-sm">Loading…</div>
        ) : rules.length === 0 ? (
          <p className="text-massclaw-text-muted text-center py-12 text-sm">
            No rules registered. Built-in rules load on backend startup.
          </p>
        ) : (
          rules.map((rule) => (
            <div
              key={rule.rule_id}
              className="glass rounded-xl p-4 flex items-center justify-between hover:bg-white/[0.03] transition-all"
            >
              <div className="flex items-center gap-3 min-w-0">
                <ShieldCheck
                  size={16}
                  className={rule.enabled ? "text-emerald-400" : "text-massclaw-text-muted/50"}
                />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-mono text-sm truncate">{rule.rule_id}</h3>
                    <span className="text-[10px] text-massclaw-text-muted">p={rule.priority}</span>
                    {rule.tags.map((t) => (
                      <span
                        key={t}
                        className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-massclaw-text-muted"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                  <p className="text-xs text-massclaw-text-muted mt-0.5 truncate">
                    {rule.description || "—"}
                  </p>
                </div>
              </div>
              <button
                onClick={() =>
                  toggleMutation.mutate({ rule_id: rule.rule_id, enabled: !rule.enabled })
                }
                disabled={toggleMutation.isPending}
                className={`text-[11px] px-3 py-1 rounded-full border transition-colors ${
                  rule.enabled
                    ? "border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10"
                    : "border-white/10 text-massclaw-text-muted hover:bg-white/5"
                }`}
              >
                {rule.enabled ? "enabled" : "disabled"}
              </button>
            </div>
          ))
        )}
      </div>

      <div className="glass rounded-xl p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Live Tester</h2>
          <button
            onClick={onEvaluate}
            disabled={evalMutation.isPending}
            className="flex items-center gap-2 text-[11px] px-3 py-1 rounded-full border border-accent-500/30 text-accent-300 hover:bg-accent-500/10 transition-colors"
          >
            <Play size={12} />
            {evalMutation.isPending ? "evaluating…" : "evaluate"}
          </button>
        </div>
        <p className="text-xs text-massclaw-text-muted">
          Paste a PolicyContext JSON. The engine aggregates per-rule verdicts and returns the
          outcome that the scheduler would act on.
        </p>
        <textarea
          value={payload}
          onChange={(e) => setPayload(e.target.value)}
          rows={10}
          className="w-full font-mono text-xs bg-black/30 border border-white/10 rounded-lg p-3 text-massclaw-text focus:outline-none focus:border-accent-500/50"
          spellCheck={false}
        />
        {evalError && (
          <div className="text-[11px] text-red-400 font-mono">{evalError}</div>
        )}
        {decision && (
          <div className="border border-white/10 rounded-lg p-4 space-y-2 bg-black/20">
            <div className="flex items-center gap-2">
              {actionIcon(decision.action)}
              {actionBadge(decision.action)}
              {decision.rule_id && (
                <span className="font-mono text-xs text-massclaw-text">{decision.rule_id}</span>
              )}
            </div>
            {decision.reason && (
              <div className="text-xs text-massclaw-text-muted">{decision.reason}</div>
            )}
            {decision.contributing_rule_ids.length > 0 && (
              <div className="text-[10px] text-massclaw-text-muted">
                contributing: {decision.contributing_rule_ids.join(", ")}
              </div>
            )}
            {Object.keys(decision.metadata).length > 0 && (
              <pre className="text-[10px] text-massclaw-text-muted bg-black/40 p-2 rounded overflow-x-auto">
                {JSON.stringify(decision.metadata, null, 2)}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
