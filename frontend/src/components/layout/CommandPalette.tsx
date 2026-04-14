"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search,
  Crosshair,
  Bot,
  Brain,
  Shield,
  Trophy,
  FileText,
  Settings,
  FolderKanban,
  ArrowRight,
} from "lucide-react";
import { useCommandPalette } from "@/hooks/useCommandPalette";

const ROUTES = [
  { label: "Mission Control", href: "/", icon: Crosshair, section: "Navigation" },
  { label: "Mission Log", href: "/missions", icon: Crosshair, section: "Navigation" },
  { label: "Agents", href: "/agents", icon: Bot, section: "Navigation" },
  { label: "Memory Explorer", href: "/memory", icon: Brain, section: "Navigation" },
  { label: "Trust Leaderboard", href: "/trust", icon: Shield, section: "Navigation" },
  { label: "Evolution Rankings", href: "/evolution", icon: Trophy, section: "Navigation" },
  { label: "Audit Trail", href: "/audit", icon: FileText, section: "Navigation" },
  { label: "Policy Rules", href: "/policy", icon: Settings, section: "Navigation" },
  { label: "Planning", href: "/planning", icon: FolderKanban, section: "Navigation" },
];

export function CommandPalette() {
  const { isOpen, query, close, setQuery } = useCommandPalette();
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);

  const filtered = ROUTES.filter(
    (r) =>
      r.label.toLowerCase().includes(query.toLowerCase()) ||
      r.href.toLowerCase().includes(query.toLowerCase())
  );

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 50);
      setSelectedIndex(0);
    }
  }, [isOpen]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  // Global keyboard shortcut
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        useCommandPalette.getState().toggle();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  const navigate = (href: string) => {
    close();
    router.push(href);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && filtered[selectedIndex]) {
      navigate(filtered[selectedIndex].href);
    } else if (e.key === "Escape") {
      close();
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="fixed inset-0 z-[100] flex items-start justify-center pt-[20vh]"
        >
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-sm"
            onClick={close}
          />

          {/* Panel */}
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: -10 }}
            transition={{ type: "spring" as const, stiffness: 400, damping: 30 }}
            className="relative w-full max-w-lg glass-strong rounded-2xl shadow-2xl shadow-black/40 overflow-hidden"
          >
            {/* Search Input */}
            <div className="flex items-center gap-3 px-4 py-3 border-b border-white/[0.06]">
              <Search size={18} className="text-massclaw-text-muted shrink-0" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Search missions, agents, pages..."
                className="flex-1 bg-transparent text-sm text-massclaw-text placeholder:text-massclaw-text-muted/50 focus:outline-none"
              />
              <kbd className="px-1.5 py-0.5 rounded text-[10px] bg-white/[0.06] border border-white/[0.06] text-massclaw-text-muted">
                esc
              </kbd>
            </div>

            {/* Results */}
            <div className="max-h-72 overflow-auto py-2">
              {filtered.length === 0 ? (
                <div className="px-4 py-8 text-center text-massclaw-text-muted text-sm">
                  No results for &ldquo;{query}&rdquo;
                </div>
              ) : (
                filtered.map((route, i) => {
                  const Icon = route.icon;
                  const isSelected = i === selectedIndex;
                  return (
                    <button
                      key={route.href}
                      onClick={() => navigate(route.href)}
                      onMouseEnter={() => setSelectedIndex(i)}
                      className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors ${
                        isSelected
                          ? "bg-massclaw-accent/10 text-massclaw-text"
                          : "text-massclaw-text-muted hover:text-massclaw-text"
                      }`}
                    >
                      <Icon size={16} className={isSelected ? "text-massclaw-accent" : ""} />
                      <span className="flex-1 text-sm">{route.label}</span>
                      {isSelected && (
                        <ArrowRight size={14} className="text-massclaw-accent" />
                      )}
                    </button>
                  );
                })
              )}
            </div>

            {/* Footer */}
            <div className="px-4 py-2 border-t border-white/[0.06] flex items-center gap-4 text-[10px] text-massclaw-text-muted">
              <span><kbd className="px-1 bg-white/[0.06] rounded">↑↓</kbd> navigate</span>
              <span><kbd className="px-1 bg-white/[0.06] rounded">↵</kbd> open</span>
              <span><kbd className="px-1 bg-white/[0.06] rounded">esc</kbd> close</span>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
