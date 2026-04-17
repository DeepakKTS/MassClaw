export interface PolicyRule {
  rule_id: string;
  name: string;
  description: string;
  action: "allow" | "deny" | "flag";
  enabled: boolean;
}

// ---- New @policy_rule registry (Day 15+) ----

export interface RegistryRule {
  rule_id: string;
  description: string;
  priority: number;
  enabled: boolean;
  tags: string[];
}

export type PolicyDecisionAction = "allow" | "deny" | "escalate_human" | "abstain";

export interface RegistryDecision {
  action: PolicyDecisionAction;
  rule_id: string | null;
  reason: string;
  metadata: Record<string, unknown>;
  contributing_rule_ids: string[];
}

export interface RegistryEvaluateRequest {
  agent_did?: string | null;
  agent_trust_score?: number | null;
  agent_capabilities?: string[];
  agent_id?: string | null;
  action: string;
  action_category?: string | null;
  tool_name?: string | null;
  amount?: number | null;
  target?: string | null;
  estimated_cost?: number | null;
  workflow_id?: string | null;
  task_id?: string | null;
  extra?: Record<string, unknown>;
  rule_ids?: string[] | null;
}

export interface PolicyDecisionAudit {
  content_hash: string;
  workflow_id: string;
  author_did: string | null;
  created_at: string | null;
  content: string;
  decision: {
    action: PolicyDecisionAction;
    rule_id: string | null;
    reason: string;
    metadata: Record<string, unknown>;
    contributing_rule_ids: string[];
    created_at: string;
  };
  context: Record<string, unknown>;
}
