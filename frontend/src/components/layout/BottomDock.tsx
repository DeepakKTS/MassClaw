"use client";

import { usePathname, useRouter } from "next/navigation";
import { useMemo } from "react";
import {
  Crosshair, Bot, Brain, Shield, Trophy, FileText, Settings, FolderKanban,
} from "lucide-react";
import { LimelightNav } from "@/components/ui/limelight-nav";

const DOCK_ITEMS = [
  { id: "missions", href: "/", icon: <Crosshair />, label: "Missions" },
  { id: "agents", href: "/agents", icon: <Bot />, label: "Agents" },
  { id: "memory", href: "/memory", icon: <Brain />, label: "Memory" },
  { id: "trust", href: "/trust", icon: <Shield />, label: "Trust" },
  { id: "evolution", href: "/evolution", icon: <Trophy />, label: "Evolution" },
  { id: "audit", href: "/audit", icon: <FileText />, label: "Audit" },
  { id: "policy", href: "/policy", icon: <Settings />, label: "Policy" },
  { id: "planning", href: "/planning", icon: <FolderKanban />, label: "Planning" },
];

export function BottomDock() {
  const pathname = usePathname();
  const router = useRouter();

  const activeIndex = useMemo(() => {
    if (pathname === "/" || pathname.startsWith("/missions")) return 0;
    const idx = DOCK_ITEMS.findIndex((item) => item.href !== "/" && pathname.startsWith(item.href));
    return idx >= 0 ? idx : 0;
  }, [pathname]);

  const navItems = DOCK_ITEMS.map((item) => ({
    id: item.id,
    icon: item.icon,
    label: item.label,
    onClick: () => router.push(item.href),
  }));

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 flex justify-center pb-3 px-4 pointer-events-none">
      <div className="pointer-events-auto rounded-[22px] border border-massclaw-border shadow-2xl shadow-black/30 glass-strong"
      >
        <LimelightNav
          items={navItems}
          defaultActiveIndex={activeIndex}
          className="rounded-[22px]"
          iconClassName="text-massclaw-text"
        />
      </div>
    </nav>
  );
}
