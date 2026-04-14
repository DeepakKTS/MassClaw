"use client";

import { useProjects, useCreateProject } from "@/hooks/useProjects";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/Badge";
import { FolderKanban, Plus } from "lucide-react";
import { useState } from "react";
import Link from "next/link";

export default function PlanningPage() {
  const { data: projectsData, isLoading } = useProjects();
  const createProject = useCreateProject();
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const projects = projectsData?.items || (Array.isArray(projectsData) ? projectsData : []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    await createProject.mutateAsync({ name: newName.trim(), description: newDesc.trim() });
    setNewName("");
    setNewDesc("");
    setShowCreate(false);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-heading">Project Planning</h1>
          <p className="text-massclaw-text-muted mt-1">Manage projects, backlogs, and sprints</p>
        </div>
        <Button onClick={() => setShowCreate(true)}>
          <Plus size={16} /> New Project
        </Button>
      </div>

      {showCreate && (
        <Card>
          <CardContent className="space-y-3 py-4">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Project name"
              className="w-full bg-massclaw-bg border border-massclaw-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-massclaw-accent"
            />
            <textarea
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder="Description"
              rows={2}
              className="w-full bg-massclaw-bg border border-massclaw-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-massclaw-accent resize-none"
            />
            <div className="flex gap-2">
              <Button size="sm" onClick={handleCreate} loading={createProject.isPending}>Create</Button>
              <Button size="sm" variant="ghost" onClick={() => setShowCreate(false)}>Cancel</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <p className="text-massclaw-text-muted">Loading projects...</p>
      ) : projects.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <FolderKanban size={48} className="mx-auto text-massclaw-text-muted mb-3 opacity-50" />
            <p className="text-massclaw-text-muted">No projects yet. Create one to start planning.</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4">
          {projects.map((project: any) => (
            <Card key={project.project_id} className="hover:border-massclaw-accent/30 transition">
              <CardContent className="py-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold">{project.name}</h3>
                    {project.description && <p className="text-sm text-massclaw-text-muted mt-0.5">{project.description}</p>}
                  </div>
                  <StatusBadge status={project.status} />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
