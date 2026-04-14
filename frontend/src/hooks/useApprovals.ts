"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function usePendingApprovals() {
  return useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api.get<any[]>("/approvals/pending"),
    refetchInterval: 3000,
  });
}

export function useApproveRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason, decided_by }: { id: string; reason?: string; decided_by?: string }) =>
      api.post(`/approvals/${id}/approve`, { reason: reason || "", decided_by: decided_by || "human" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });
}

export function useDenyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason, decided_by }: { id: string; reason?: string; decided_by?: string }) =>
      api.post(`/approvals/${id}/deny`, { reason: reason || "", decided_by: decided_by || "human" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });
}
