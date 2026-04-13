"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Agent, AgentSummary } from "@/types/agent";
import type { PaginatedResponse } from "@/types/common";

export function useAgents(page = 1, pageSize = 20) {
  return useQuery({
    queryKey: ["agents", page, pageSize],
    queryFn: () => api.get<PaginatedResponse<AgentSummary>>(`/agents?page=${page}&page_size=${pageSize}&sort_by=trust_score&sort_order=desc`),
  });
}

export function useAgent(id: string) {
  return useQuery({
    queryKey: ["agent", id],
    queryFn: () => api.get<Agent>(`/agents/${id}`),
    enabled: !!id,
  });
}
