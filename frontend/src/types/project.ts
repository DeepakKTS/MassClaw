export interface Project {
  project_id: string;
  name: string;
  description: string;
  status: string;
  owner_id: string;
  created_at: string;
  updated_at: string;
}

export interface BacklogTask {
  backlog_task_id: string;
  project_id: string;
  title: string;
  description: string;
  priority: number;
  story_points: number | null;
  labels: string[];
  status: string;
  created_at: string;
}

export interface Sprint {
  sprint_id: string;
  project_id: string;
  name: string;
  goal: string | null;
  start_date: string;
  end_date: string;
  status: string;
  created_at: string;
}

export interface SprintTask {
  sprint_task_id: string;
  sprint_id: string;
  backlog_task_id: string;
  workflow_id: string | null;
  status: string;
  assigned_to: string | null;
  position: number;
  created_at: string;
}
