"use client";
import { cn } from "@/lib/utils";
import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

export function Modal({ open, onClose, title, children, className }: { open: boolean; onClose: () => void; title: string; children: ReactNode; className?: string }) {
  useEffect(() => {
    if (open) {
      const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
      document.addEventListener("keydown", handler);
      return () => document.removeEventListener("keydown", handler);
    }
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className={cn("relative glass-strong rounded-xl shadow-2xl max-w-lg w-full mx-4 p-6", className)}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button onClick={onClose} className="text-massclaw-text-muted hover:text-massclaw-text transition"><X size={18} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}
