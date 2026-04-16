"use client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AgentFacts, VerifyResult } from "@/types/agentFacts";

/** Fetch the signed AgentFacts document for a specific agent. */
export function useAgentFacts(agentId: string) {
  return useQuery<AgentFacts>({
    queryKey: ["agent-facts", agentId],
    queryFn: () => api.get<AgentFacts>(`/agents/${agentId}/agent-facts.json`),
    enabled: !!agentId,
  });
}

/** Fetch the MassClaw instance's own AgentFacts document from /.well-known/. */
export function useInstanceAgentFacts(options: { enabled?: boolean } = {}) {
  const { enabled = true } = options;
  return useQuery<AgentFacts>({
    queryKey: ["agent-facts", "instance"],
    queryFn: () => api.getRaw<AgentFacts>("/.well-known/agent-facts.json"),
    enabled,
  });
}

/** Post an AgentFacts document back to the server for signature verification. */
export function useVerifyAgentFacts() {
  return useMutation<VerifyResult, Error, AgentFacts>({
    mutationFn: (facts) => api.post<VerifyResult>("/agents/verify-facts", facts),
  });
}
