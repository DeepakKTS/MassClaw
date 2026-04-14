import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

const variantStyles = {
  solid: "bg-massclaw-surface border border-massclaw-border",
  glass: "glass",
  glow: "glass border-massclaw-accent/20 shadow-lg shadow-massclaw-accent/5",
};

interface CardProps {
  children: ReactNode;
  className?: string;
  variant?: keyof typeof variantStyles;
}

export function Card({ children, className, variant = "glass" }: CardProps) {
  return (
    <div className={cn("rounded-xl", variantStyles[variant], className)}>
      {children}
    </div>
  );
}

export function CardHeader({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("px-5 py-4 border-b border-white/[0.06]", className)}>{children}</div>;
}

export function CardContent({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("px-5 py-4", className)}>{children}</div>;
}
