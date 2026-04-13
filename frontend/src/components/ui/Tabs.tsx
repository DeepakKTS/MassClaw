"use client";
import { cn } from "@/lib/utils";
import { useState, type ReactNode } from "react";

interface Tab { id: string; label: string; icon?: ReactNode; }

export function Tabs({ tabs, defaultTab, children }: { tabs: Tab[]; defaultTab?: string; children: (activeTab: string) => ReactNode }) {
  const [active, setActive] = useState(defaultTab || tabs[0]?.id || "");

  return (
    <div>
      <div className="flex gap-1 border-b border-massclaw-border mb-4">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActive(tab.id)}
            className={cn(
              "flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px",
              active === tab.id
                ? "border-massclaw-accent text-massclaw-accent"
                : "border-transparent text-massclaw-text-muted hover:text-massclaw-text"
            )}
          >
            {tab.icon}
            {tab.label}
          </button>
        ))}
      </div>
      {children(active)}
    </div>
  );
}
