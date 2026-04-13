export interface Agent {
  agent_id: string;
  name: string;
  description: string;
  capabilities: string[];
  endpoint: string;
  supported_tools: string[];
  cost_profile: Record<string, number>;
  latency_profile: Record<string, number>;
  trust_score: number;
  version: string;
  safety_level: number;
  input_schema: Record<string, unknown> | null;
  output_schema: Record<string, unknown> | null;
  status: "active" | "inactive" | "suspended" | "degraded";
  health_check_url: string | null;
  last_health_check: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentSummary {
  agent_id: string;
  name: string;
  capabilities: string[];
  trust_score: number;
  status: string;
  version: string;
  safety_level: number;
  created_at: string;
}
