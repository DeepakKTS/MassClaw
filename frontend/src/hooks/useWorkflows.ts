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
  });
}

export function useWorkflowStatus(id: string) {
  return useQuery({
    queryKey: ["workflow-status", id],
    queryFn: () => api.get<WorkflowStatus>(`/workflows/${id}/status`),
    enabled: !!id,
    refetchInterval: 2000,
  });
}

export function useWorkflowTasks(id: string) {
  return useQuery({
    queryKey: ["workflow-tasks", id],
    queryFn: () => api.get<Task[]>(`/tasks/workflow/${id}`),
    enabled: !!id,
  });
}

export function useCreateWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { prompt: string; budget_limit: number }) => api.post<Workflow>("/workflows", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflows"] }),
  });
}
