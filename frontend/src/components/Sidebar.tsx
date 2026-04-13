"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { clsx } from "clsx";
import {
  LayoutDashboard, Bot, GitBranch, Brain, Shield, Wallet, Trophy, FileText, Settings
} from "lucide-react";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/workflows", label: "Workflows", icon: GitBranch },
  { href: "/memory", label: "Memory", icon: Brain },
  { href: "/trust", label: "Trust", icon: Shield },
  { href: "/wallet", label: "Wallet", icon: Wallet },
  { href: "/evolution", label: "Evolution", icon: Trophy },
  { href: "/audit", label: "Audit", icon: FileText },
  { href: "/policy", label: "Policy", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-60 bg-massclaw-surface border-r border-massclaw-border flex flex-col">
      <div className="p-5 border-b border-massclaw-border">
        <h1 className="text-xl font-bold text-massclaw-accent">MassClaw</h1>
        <p className="text-xs text-massclaw-text-muted mt-0.5">Agent Infrastructure</p>
      </div>
      <nav className="flex-1 py-3">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-3 px-5 py-2.5 text-sm transition-colors",
                active
                  ? "text-massclaw-accent bg-massclaw-accent/10 border-r-2 border-massclaw-accent"
                  : "text-massclaw-text-muted hover:text-massclaw-text hover:bg-massclaw-border/30"
              )}
            >
              <Icon size={18} />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="p-4 border-t border-massclaw-border text-xs text-massclaw-text-muted">
        v0.1.0
      </div>
    </aside>
  );
}
