"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface AnimatedBorderProps {
  children: ReactNode;
  className?: string;
  active?: boolean;
}

export function AnimatedBorder({
  children,
  className,
  active = true,
}: AnimatedBorderProps) {
  return (
    <div
      className={cn(
        "animated-border",
        !active && "[&::before]:opacity-0",
        className
      )}
    >
      {children}
    </div>
  );
}
