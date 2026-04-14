"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Tool, MCPServer } from "@/types/tools";

export function useTools() {
  return useQuery({
    queryKey: ["tools"],
    queryFn: () => api.get<Tool[]>("/tools"),
  });
}

export function useMCPServers() {
  return useQuery({
    queryKey: ["mcp-servers"],
    queryFn: () => api.get<MCPServer[]>("/mcp/servers"),
    refetchInterval: 10000,
  });
}
