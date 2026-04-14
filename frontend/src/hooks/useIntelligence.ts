"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useRecentWorkflows() {
  return useQuery({
    queryKey: ["workflows", "recent"],
    queryFn: () => api.get<any>("/workflows?page_size=10"),
  });
}
