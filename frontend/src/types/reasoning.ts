export interface ReasoningTrace {
  trace_id: string;
  workflow_id: string;
  iteration: number;
  phase: string;
  event_type: string;
  content: string;
  confidence: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface StructuredGoal {
  original_prompt: string;
  intent: string;
  constraints: string[];
  risk_level: string;
  expected_output_type: string;
  complexity_estimate: string;
  domain_hint: string | null;
  stop_conditions: string[];
}
