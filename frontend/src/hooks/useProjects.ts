"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<any>("/projects"),
  });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ["project", id],
    queryFn: () => api.get<any>(`/projects/${id}`),
    enabled: !!id,
  });
}

export function useProjectBacklog(projectId: string) {
  return useQuery({
    queryKey: ["project-backlog", projectId],
    queryFn: () => api.get<any>(`/projects/${projectId}/backlog`),
    enabled: !!projectId,
  });
}

export function useProjectSprints(projectId: string) {
  return useQuery({
    queryKey: ["project-sprints", projectId],
    queryFn: () => api.get<any>(`/projects/${projectId}/sprints`),
    enabled: !!projectId,
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; description: string }) => api.post("/projects", data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}
