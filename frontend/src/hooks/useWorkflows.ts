"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Workflow, WorkflowStatus, Task } from "@/types/workflow";
import type { PaginatedResponse } from "@/types/common";

export function useWorkflows(page = 1) {
  return useQuery({
    queryKey: ["workflows", page],
    queryFn: () => api.get<PaginatedResponse<Workflow>>(`/workflows?page=${page}`),
  });
}

export function useWorkflow(id: string) {
  return useQuery({
    queryKey: ["workflow", id],
    queryFn: () => api.get<Workflow>(`/workflows/${id}`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data as Workflow | undefined;
      if (!data) return 2000;
      // Stop polling once terminal
      if (["completed", "failed", "cancelled"].includes(data.status)) return false;
      return 2000;
    },
  });
}

export function useWorkflowStatus(id: string) {
  return useQuery({
    queryKey: ["workflow-status", id],
    queryFn: () => api.get<WorkflowStatus>(`/workflows/${id}/status`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data as WorkflowStatus | undefined;
      if (data && data.progress_percent >= 100) return false;
      return 1500; // Fast polling during execution
    },
  });
}

export function useWorkflowTasks(id: string) {
  return useQuery({
    queryKey: ["workflow-tasks", id],
    queryFn: () => api.get<Task[]>(`/tasks/workflow/${id}`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data as Task[] | undefined;
      if (!data || data.length === 0) return 2000;
      const allDone = data.every((t) =>
        ["completed", "failed", "skipped"].includes(t.status)
      );
      if (allDone) return false;
      return 1500; // Fast polling while tasks are running
    },
  });
}

export function useCreateWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { prompt: string; budget_limit: number }) =>
      api.post<Workflow>("/workflows", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflows"] }),
  });
}
