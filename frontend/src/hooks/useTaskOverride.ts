"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useTaskOverride(workflowId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, action, reason, forceOutput }: { taskId: string; action: string; reason?: string; forceOutput?: object }) =>
      api.post(`/tasks/${taskId}/override`, { action, reason, force_output: forceOutput }),
    onSuccess: () => {
      if (workflowId) {
        qc.invalidateQueries({ queryKey: ["workflow-tasks", workflowId] });
        qc.invalidateQueries({ queryKey: ["workflow-status", workflowId] });
      }
    },
  });
}

export function useTaskActions(taskId: string) {
  return {
    queryKey: ["task-actions", taskId],
    queryFn: () => api.get<string[]>(`/tasks/${taskId}/actions`),
    enabled: !!taskId,
  };
}
