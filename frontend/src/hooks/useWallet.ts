"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { WalletBalance, WalletEvent } from "@/types/wallet";
import type { PaginatedResponse } from "@/types/common";

export function useWalletBalance(workflowId: string) {
  return useQuery({
    queryKey: ["wallet-balance", workflowId],
    queryFn: () => api.get<WalletBalance>(`/wallet/workflow/${workflowId}/balance`),
    enabled: !!workflowId,
  });
}

export function useWalletEvents(workflowId: string, page = 1) {
  return useQuery({
    queryKey: ["wallet-events", workflowId, page],
    queryFn: () =>
      api.get<PaginatedResponse<WalletEvent>>(
        `/wallet/workflow/${workflowId}/events?page=${page}&page_size=20`
      ),
    enabled: !!workflowId,
  });
}
