"use client";

import { useState } from "react";
import { Wrench, ChevronDown, ChevronRight, Shield, Cpu, Globe } from "lucide-react";
import { cn } from "@/lib/utils";
import { useTools } from "@/hooks/useTools";
import type { Tool } from "@/types/tools";

const MODE_STYLES: Record<string, { label: string; color: string; icon: typeof Cpu }> = {
  in_process: { label: "In-Process", color: "text-massclaw-success bg-massclaw-success/10", icon: Cpu },
  sandboxed: { label: "Sandboxed", color: "text-massclaw-warning bg-massclaw-warning/10", icon: Shield },
};

function ToolCard({ tool }: { tool: Tool }) {
  const [expanded, setExpanded] = useState(false);
  const mode = MODE_STYLES[tool.execution_mode] || MODE_STYLES.in_process;
  const ModeIcon = mode.icon;

  return (
    <div className="glass rounded-xl p-5 space-y-3">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-massclaw-accent/10 flex items-center justify-center">
            {tool.execution_mode === "sandboxed" ? (
              <Shield size={18} className="text-massclaw-warning" />
            ) : (
              <Globe size={18} className="text-massclaw-accent" />
            )}
          </div>
          <div>
            <h3 className="font-semibold font-mono text-sm">{tool.name}</h3>
            <p className="text-xs text-massclaw-text-muted mt-0.5">{tool.description}</p>
          </div>
        </div>
        <span className={cn("text-[10px] font-medium px-2 py-0.5 rounded-full", mode.color)}>
          <ModeIcon size={10} className="inline mr-1" />
          {mode.label}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {tool.required_capabilities.map((cap) => (
          <span
            key={cap}
            className="text-[10px] px-2 py-0.5 rounded-full bg-white/[0.06] text-massclaw-text-muted border border-white/[0.06]"
          >
            {cap}
          </span>
        ))}
      </div>

      <div className="flex items-center justify-between text-xs text-massclaw-text-muted pt-1 border-t border-white/[0.04]">
        <span>Cost: {tool.estimated_cost_credits} credits/call</span>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 hover:text-massclaw-accent transition-colors"
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          Schema
        </button>
      </div>

      {expanded && (
        <pre className="text-[11px] font-mono bg-massclaw-bg/60 rounded-lg p-3 overflow-x-auto text-massclaw-text-muted border border-white/[0.04]">
          {JSON.stringify(tool.parameters, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function ToolsPage() {
  const { data: tools, isLoading, error } = useTools();

  const inProcess = tools?.filter((t) => t.execution_mode === "in_process") || [];
  const sandboxed = tools?.filter((t) => t.execution_mode === "sandboxed") || [];

  return (
    <div className="max-w-4xl mx-auto space-y-6 py-6">
      <div>
        <h1 className="text-heading flex items-center gap-2">
          <Wrench size={24} className="text-massclaw-accent" />
          Tool Registry
        </h1>
        <p className="text-massclaw-text-muted mt-1 text-sm">
          Tools available to agents during workflow execution. Agents access tools based on their capabilities.
        </p>
      </div>

      {error && (
        <div className="glass rounded-xl p-4 border border-massclaw-danger/20 text-massclaw-danger text-sm">
          Failed to load tools: {error.message}
        </div>
      )}

      {isLoading && (
        <div className="glass rounded-xl p-8 text-center text-massclaw-text-muted">Loading tools...</div>
      )}

      {tools && (
        <div className="glass rounded-xl p-4">
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <div className="text-2xl font-bold text-massclaw-accent">{tools.length}</div>
              <div className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">Total Tools</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-massclaw-success">{inProcess.length}</div>
              <div className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">In-Process</div>
            </div>
            <div>
              <div className="text-2xl font-bold text-massclaw-warning">{sandboxed.length}</div>
              <div className="text-[10px] text-massclaw-text-muted uppercase tracking-wider">Sandboxed</div>
            </div>
          </div>
        </div>
      )}

      {sandboxed.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-massclaw-text-muted uppercase tracking-wider">
            Sandboxed Tools (Docker)
          </h2>
          {sandboxed.map((tool) => (
            <ToolCard key={tool.name} tool={tool} />
          ))}
        </div>
      )}

      {inProcess.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-massclaw-text-muted uppercase tracking-wider">
            In-Process Tools
          </h2>
          {inProcess.map((tool) => (
            <ToolCard key={tool.name} tool={tool} />
          ))}
        </div>
      )}
    </div>
  );
}
