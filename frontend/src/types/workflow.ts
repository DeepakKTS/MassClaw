export interface Workflow {
  workflow_id: string;
  user_id: string;
  prompt: string;
  domain: string | null;
  status: "pending" | "decomposing" | "running" | "paused" | "completed" | "failed" | "cancelled";
  budget_limit: number;
  budget_used: number;
  priority: number;
  metadata: Record<string, unknown>;
  execution_mode: string | null;
  dag_snapshot: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowStatus {
  workflow_id: string;
  status: string;
  progress_percent: number;
  total_tasks: number;
  completed_tasks: number;
  running_tasks: number;
  failed_tasks: number;
  budget_used: number;
  budget_limit: number;
  started_at: string | null;
  elapsed_seconds: number | null;
}

export interface Task {
  task_id: string;
  workflow_id: string;
  parent_task_id: string | null;
  assigned_agent_id: string | null;
  step_number: number;
  capability: string;
  description: string;
  status: "pending" | "assigned" | "running" | "retrying" | "completed" | "failed" | "skipped";
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  confidence: number | null;
  retry_count: number;
  max_retries: number;
  cost_used: number;
  latency_ms: number | null;
  error_message: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}
