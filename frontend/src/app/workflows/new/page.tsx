"use client";

import { useCreateWorkflow } from "@/hooks/useWorkflows";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Send } from "lucide-react";

export default function NewWorkflowPage() {
  const router = useRouter();
  const createWorkflow = useCreateWorkflow();
  const [prompt, setPrompt] = useState("");
  const [budget, setBudget] = useState(500);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;

    try {
      const wf = await createWorkflow.mutateAsync({
        prompt: prompt.trim(),
        budget_limit: budget,
      });
      router.push(`/workflows/${wf.workflow_id}`);
    } catch (err) {
      console.error("Failed to create workflow:", err);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold">New Workflow</h1>
        <p className="text-massclaw-text-muted mt-1">
          Describe what you need and MassClaw will orchestrate specialized agents to deliver it.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        <div>
          <label className="block text-sm font-medium mb-2">Prompt</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Analyze outpatient bottlenecks, identify staffing inefficiencies, propose scheduling improvements, estimate risk areas, and generate an executive operations brief."
            rows={6}
            className="w-full bg-massclaw-surface border border-massclaw-border rounded-lg p-4 text-sm focus:outline-none focus:border-massclaw-accent resize-none"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">
            Budget: {budget} credits
          </label>
          <input
            type="range"
            min={50}
            max={2000}
            step={50}
            value={budget}
            onChange={(e) => setBudget(Number(e.target.value))}
            className="w-full accent-massclaw-accent"
          />
          <div className="flex justify-between text-xs text-massclaw-text-muted mt-1">
            <span>50</span>
            <span>2000</span>
          </div>
        </div>

        <button
          type="submit"
          disabled={!prompt.trim() || createWorkflow.isPending}
          className="flex items-center gap-2 px-6 py-3 bg-massclaw-accent text-white rounded-lg hover:bg-massclaw-accent-light transition disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Send size={16} />
          {createWorkflow.isPending ? "Executing..." : "Execute Workflow"}
        </button>

        {createWorkflow.isError && (
          <p className="text-massclaw-danger text-sm">
            Error: {(createWorkflow.error as Error).message}
          </p>
        )}
      </form>
    </div>
  );
}
