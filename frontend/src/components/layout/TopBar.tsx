"use client";

import Link from "next/link";
import Image from "next/image";
import { Search, Sun, Moon, Activity, Bot, Crosshair, Brain, Shield } from "lucide-react";
import { useCommandPalette } from "@/hooks/useCommandPalette";
import { useTheme } from "@/hooks/useTheme";
import { useAgents } from "@/hooks/useAgents";
import { useWorkflows } from "@/hooks/useWorkflows";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

function NavStat({ icon: Icon, value, label }: { icon: any; value: string | number; label: string }) {
  return (
    <div className="flex items-center gap-1.5 px-3 py-1">
      <Icon size={12} className="text-massclaw-text-muted/50" />
      <span className="text-[12px] font-semibold text-massclaw-text tabular-nums">{value}</span>
      <span className="text-[10px] text-massclaw-text-muted/40">{label}</span>
    </div>
  );
}

export function TopBar() {
  const openPalette = useCommandPalette((s) => s.open);
  const { theme, toggle } = useTheme();

  const { data: agents } = useAgents(1, 100);
  const { data: workflows } = useWorkflows();
  const { data: metrics } = useQuery({
    queryKey: ["system-metrics"],
    queryFn: () => api.get<any>("/../../system/metrics"),
    retry: false,
  });

  const activeAgents = agents?.items?.filter((a: any) => a.status === "active").length ?? 0;
  const missionCount = workflows?.total ?? 0;
  const memoryCount = metrics?.memory_records ?? "—";
  const trustCount = metrics?.trust_events ?? "—";

  return (
    <>
      {/* ── Top-left: MassClaw logo button ── */}
      <Link href="/" className="fixed top-4 left-5 z-50 flex items-center gap-2.5 group glass rounded-2xl px-3 py-2 border border-massclaw-border/40 hover:border-massclaw-accent/30 transition-all">
        <Image src="/logo.png" alt="MassClaw" width={26} height={26} className="rounded-lg" priority />
        <span className="text-[14px] font-semibold text-massclaw-text group-hover:text-massclaw-accent transition-colors tracking-tight">
          MassClaw
        </span>
      </Link>

      {/* ── Top-right: Theme + Status ── */}
      <div className="fixed top-4 right-5 z-50 flex items-center gap-2">
        <button
          onClick={toggle}
          className="glass rounded-xl w-9 h-9 flex items-center justify-center border border-massclaw-border/40 text-massclaw-text-muted/50 hover:text-massclaw-text hover:border-massclaw-accent/30 transition-all"
          aria-label="Toggle theme"
        >
          {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
        </button>
        <div className="glass rounded-xl px-3 h-9 flex items-center gap-1.5 border border-massclaw-border/40 text-[11px] text-massclaw-text-muted/50">
          <Activity size={10} className="text-massclaw-success" />
          <span className="hidden sm:inline">Online</span>
        </div>
      </div>

      {/* ── Top-center: Stats pill with search in middle ── */}
      <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 pointer-events-none">
        <div className="pointer-events-auto flex items-center gap-0.5 glass rounded-2xl border border-massclaw-border/40 px-1.5 py-1">
          <NavStat icon={Bot} value={activeAgents} label="Agents" />
          <div className="w-px h-3.5 bg-massclaw-border/40" />
          <NavStat icon={Crosshair} value={missionCount} label="Missions" />
          <div className="w-px h-3.5 bg-massclaw-border/40" />

          {/* Search — between Missions and Memories */}
          <button
            onClick={openPalette}
            className="flex items-center gap-1.5 px-3 py-1.5 mx-1 rounded-xl border border-massclaw-border/30 hover:border-massclaw-accent/30 transition-all text-massclaw-text-muted/50 hover:text-massclaw-text"
          >
            <Search size={12} />
            <span className="text-[10px]">Search</span>
            <kbd className="hidden sm:inline-flex px-1 py-0.5 rounded text-[8px] bg-massclaw-border/30 text-massclaw-text-muted/40 ml-0.5">&#8984;K</kbd>
          </button>

          <div className="w-px h-3.5 bg-massclaw-border/40" />
          <NavStat icon={Brain} value={memoryCount} label="Memories" />
          <div className="w-px h-3.5 bg-massclaw-border/40" />
          <NavStat icon={Shield} value={trustCount} label="Trust" />
        </div>
      </div>
    </>
  );
}
