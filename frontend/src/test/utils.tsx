import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { ToastProvider } from "@/components/Toast";
import type { Task, Workflow, WorkflowStatus } from "@/types/workflow";

/** A QueryClient tuned for tests: no retries (so an intentional failure
 *  surfaces immediately instead of after three backoffs) and no cache
 *  carried between cases. */
export function testQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

/** Mirrors src/components/Providers.tsx minus the ErrorBoundary — route
 *  tests want a thrown render error to fail the test, not be swallowed
 *  into a fallback UI. */
export function renderWithProviders(
  ui: ReactElement,
  options: Omit<RenderOptions, "wrapper"> & { client?: QueryClient } = {},
) {
  const { client = testQueryClient(), ...renderOptions } = options;

  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ToastProvider>{children}</ToastProvider>
      </QueryClientProvider>
    );
  }

  return { client, ...render(ui, { wrapper: Wrapper, ...renderOptions }) };
}

const NOW = "2026-08-12T10:00:00Z";

export function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    workflow_id: "wf-00000000-0000-0000-0000-000000000001",
    user_id: "deepak",
    prompt: "Research the ISS crew rotation schedule",
    domain: "research",
    status: "completed",
    budget_limit: 100,
    budget_used: 42.5,
    priority: 5,
    metadata: {},
    execution_mode: "dag",
    dag_snapshot: null,
    result: { summary: "Seven crew aboard." },
    started_at: NOW,
    completed_at: "2026-08-12T10:04:00Z",
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

export function makeWorkflowStatus(overrides: Partial<WorkflowStatus> = {}): WorkflowStatus {
  return {
    workflow_id: "wf-00000000-0000-0000-0000-000000000001",
    status: "completed",
    progress_percent: 100,
    total_tasks: 3,
    completed_tasks: 3,
    running_tasks: 0,
    failed_tasks: 0,
    budget_used: 42.5,
    budget_limit: 100,
    started_at: NOW,
    elapsed_seconds: 240,
    ...overrides,
  };
}

export function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    task_id: "task-1",
    workflow_id: "wf-00000000-0000-0000-0000-000000000001",
    parent_task_id: null,
    assigned_agent_id: "agent-research",
    step_number: 1,
    capability: "web_search",
    description: "Search for the current ISS crew",
    status: "completed",
    input: null,
    output: { text: "Seven crew aboard." },
    confidence: 0.91,
    retry_count: 0,
    max_retries: 2,
    cost_used: 12.25,
    latency_ms: 1840,
    error_message: null,
    completed_at: "2026-08-12T10:02:00Z",
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

/** Shape returned by GET /approvals/pending. The API serves these as loose
 *  dicts, and the page reads them as `any`, so this is a documentation
 *  factory rather than a typed contract. */
export function makeApproval(overrides: Record<string, unknown> = {}) {
  return {
    request_id: "apr-00000000-0000-0000-0000-0000000000aa",
    action: "send_email",
    status: "pending",
    policy_rule: "require_approval_for_external_send",
    requested_at: NOW,
    expires_at: "2026-08-12T10:05:00Z",
    decided_by: null,
    decided_at: null,
    checkpoint_hash: "c0ffee1234567890abcdef",
    context: {
      capability: "email_send",
      agent: "comms-agent",
      confidence: 0.31,
      output_preview: "Dear team, the launch window has moved.",
    },
    ...overrides,
  };
}
